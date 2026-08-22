"""MTF 历史回放管道 —— 纯函数（2026-08-16）。

职责（领导拍板，全部纯计算、无网络 / 无 DB / 无时钟读取）：
  1. parse_binance_kline_csv：Binance kline CSV → time秒/OHLCV/is_closed；
     首列恰为 open_time 才跳过表头；无表头首根不丢。
  2. validate_closed_4h：唯一 time、严格 14400 连续、合法 OHLC、volume 有限且 >= 0、
     全部已收盘；bool/非法 time 拒绝。
  3. aggregate_utc：4h → 1day（UTC 00 边界 6 根）/ 1week（周一 00 边界 42 根）；
     OHLC 首高低末 + volume 和；头尾不完整桶丢弃；不跨缺口。
  4. classify_policies：A/B 策略门控与 week_relation（agree/disagree/sideways）；
     A/B 都要求日线明确 up/down 且严格等于事件方向（sideways/unknown 均 false），
     A 另要求周线明确同向，B 周线只须 ready+closed。
  5. replay_monitor_events：逐根把最近最多 360 根前缀传给现有
     run_monitor_detectors，按 (strategy,event_type,direction,signal_time) 留首个；
     从第一个 is_closed=False 起立即 cutoff（不读其 OHLC、不调 detector），
     已收盘前缀事件逐字不变。
  6. compute_outcomes：entry=signal close（reference_price 给出时须有限正数且
     与事件 close 在 1e-6 相对容差内，否则拒绝；缺失才用 close），ATR14 1R；
     后 5/10/20/40 根不含事件；long/short MFE/MAE、first、fake；
     return_r 除事件 ATR14，return_pct 分母为 next_open。事件方向统一归一
     long/short（buy→long、sell→short）。
  7. wilson_interval；verify_manifest（sha256 摘要校验）。
"""
from __future__ import annotations

import bisect
import csv
import datetime
import gzip
import hashlib
import io
import json
import math
import ssl
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from app.core.indicators import _atr
from app.core.market_state import (
    DEFAULT_MARKET_STATE_CONFIG,
    precompute_market_states,
)
from app.core.outcome_labeling import (
    TOUCH_AMBIGUOUS,
    TOUCH_NEGATIVE,
    TOUCH_NEITHER,
    TOUCH_POSITIVE,
)
from app.core.signals import monitor_signals as _monitor_signals

STEP4 = 14400
DAY = 86400
WEEK = 604800
# 周一 00:00 UTC 相对 epoch 的偏移：1969-12-29T00:00:00Z = -259200 秒
_WEEK_OFFSET = 259200
_CSV_HEADER_FIRST_COL = "open_time"
OUTCOME_WINDOWS = (5, 10, 20, 40)


# ═══════════════════════════════════════════════════════════════════════════════
#  1. parse_binance_kline_csv
# ═══════════════════════════════════════════════════════════════════════════════

def parse_binance_kline_csv(data: bytes) -> list[dict[str, Any]]:
    """解析 Binance kline CSV（UTF-8）。

    - 第一行首列恰为 ``open_time`` 才视为表头并跳过；否则首行按数据保留。
    - open_time 为毫秒（>= 1e12）时折算为秒；已是秒则原样保留。
    - 每行至少 6 列：open_time, open, high, low, close, volume；
      其余列（close_time / quote_volume / ...）忽略。
    - 返回 ``{time, open, high, low, close, volume, is_closed=True}``。
    - 空输入返回 []；列数不足 / 数值非法 → ValueError。
    """
    text = data.decode("utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    out: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        parts = [p.strip() for p in line.split(",")]
        if idx == 0 and parts and parts[0] == _CSV_HEADER_FIRST_COL:
            continue
        if len(parts) < 6:
            raise ValueError(f"CSV 行 {idx + 1} 列数不足（至少 6 列）: {line!r}")
        try:
            raw_t = int(float(parts[0]))
            open_, high, low, close, volume = (float(parts[i]) for i in range(1, 6))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CSV 行 {idx + 1} 数值非法: {line!r}") from exc
        t = raw_t // 1000 if raw_t >= 1_000_000_000_000 else raw_t
        out.append({
            "time": t,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "is_closed": True,
        })
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  2. validate_closed_4h
# ═══════════════════════════════════════════════════════════════════════════════

def _bar_time(candle: dict[str, Any]) -> int:
    raw = candle.get("time")
    if isinstance(raw, bool):
        raise ValueError(f"bar time 非法（bool）: {raw!r}")
    try:
        f = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bar time 非法: {raw!r}") from exc
    if not math.isfinite(f):
        raise ValueError(f"bar time 非法（非有限）: {raw!r}")
    if f < 0 or f != int(f):
        raise ValueError(f"bar time 非法（负数或非整数秒）: {raw!r}")
    return int(f)


def _validate_volume(candle: dict[str, Any], t: int) -> None:
    raw = candle.get("volume")
    if isinstance(raw, bool):
        raise ValueError(f"bar time={t} volume 非法（bool）: {raw!r}")
    try:
        f = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bar time={t} volume 非数值: {raw!r}") from exc
    if not math.isfinite(f):
        raise ValueError(f"bar time={t} volume 非有限: {raw!r}")
    if f < 0:
        raise ValueError(f"bar time={t} volume 必须 >= 0: {raw!r}")


def _validate_ohlc(candle: dict[str, Any]) -> None:
    t = _bar_time(candle)
    vals: dict[str, float] = {}
    for name in ("open", "high", "low", "close"):
        raw = candle.get(name)
        if raw is None:
            raise ValueError(f"bar time={t} 缺 {name}（已收盘 bar 必须含 OHLC）")
        try:
            f = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bar time={t} {name} 非数值: {raw!r}") from exc
        if not math.isfinite(f):
            raise ValueError(f"bar time={t} {name} 非有限: {raw!r}")
        if f <= 0:
            raise ValueError(f"bar time={t} {name} 必须为正数: {raw!r}")
        vals[name] = f
    h, o, c, l = vals["high"], vals["open"], vals["close"], vals["low"]
    if not (h >= max(o, c) >= min(o, c) >= l):
        raise ValueError(
            f"bar time={t} OHLC 关系非法: high={h} open={o} close={c} low={l}"
        )


def validate_closed_4h(candles: list[dict[str, Any]]) -> None:
    """4H 序列契约：唯一 time、严格 14400 连续、合法 OHLC、全部已收盘。

    任何违反 → ValueError（不返回部分结果）。
    """
    if not candles:
        return
    prev: int | None = None
    for candle in candles:
        t = _bar_time(candle)
        if candle.get("is_closed") is False:
            raise ValueError(f"bar time={t} 未收盘（is_closed=False）")
        _validate_ohlc(candle)
        _validate_volume(candle, t)
        if prev is not None:
            if t == prev:
                raise ValueError(f"bar time 重复: {t}")
            if t - prev != STEP4:
                raise ValueError(
                    f"bar time 不连续（非严格 14400）: {prev} -> {t}"
                )
        prev = t


# ═══════════════════════════════════════════════════════════════════════════════
#  3. aggregate_utc
# ═══════════════════════════════════════════════════════════════════════════════

def _bucket_key(time: int, timeframe: str) -> int:
    if timeframe == "1day":
        return time // DAY
    if timeframe == "1week":
        return (time + _WEEK_OFFSET) // WEEK
    raise ValueError(f"不支持的聚合周期: {timeframe!r}（仅支持 1day/1week）")


def _bucket_start(time: int, timeframe: str) -> int:
    if timeframe == "1day":
        return time // DAY * DAY
    if timeframe == "1week":
        return (time + _WEEK_OFFSET) // WEEK * WEEK - _WEEK_OFFSET
    raise ValueError(f"不支持的聚合周期: {timeframe!r}（仅支持 1day/1week）")


def aggregate_utc(candles: list[dict[str, Any]], timeframe: str) -> list[dict[str, Any]]:
    """把 4H bars 按 UTC 日 / 周（周一 00:00）聚合。

    - 1day：每桶恰 6 根；1week：每桶恰 42 根（不足即不完整 → 丢弃）。
    - 桶内 4H 步长必须严格 14400（不跨缺口；桶内任何缺口 → 整桶丢弃）。
    - OHLC = 首 open / 最高 high / 最低 low / 末 close；volume 求和。
    - 返回桶 time 为边界开盘秒；与输入同构（time/OHLCV/is_closed）。
    """
    if timeframe not in ("1day", "1week"):
        raise ValueError(f"不支持的聚合周期: {timeframe!r}（仅支持 1day/1week）")
    per = 6 if timeframe == "1day" else 42
    buckets: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    for candle in candles:
        t = _bar_time(candle)
        key = _bucket_key(t, timeframe)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(candle)

    out: list[dict[str, Any]] = []
    for key in order:
        bucket = buckets[key]
        if len(bucket) != per:
            continue  # 头尾/缺口不完整桶丢弃
        ts = [_bar_time(b) for b in bucket]
        if any(ts[i + 1] - ts[i] != STEP4 for i in range(len(ts) - 1)):
            continue  # 桶内缺口：不跨缺口聚合
        out.append({
            "time": _bucket_start(ts[0], timeframe),
            "open": float(bucket[0]["open"]),
            "high": max(float(b["high"]) for b in bucket),
            "low": min(float(b["low"]) for b in bucket),
            "close": float(bucket[-1]["close"]),
            "volume": sum(float(b.get("volume") or 0.0) for b in bucket),
            "is_closed": all(bool(b.get("is_closed", True)) for b in bucket),
        })
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  4. classify_policies
# ═══════════════════════════════════════════════════════════════════════════════

def _trade_direction(trend: str) -> str | None:
    return {"up": "long", "down": "short"}.get(trend)


def _normalize_direction(direction: str) -> str:
    """事件方向归一化：buy→long、sell→short；其余必须为 long/short，否则 ValueError。
    classify_policies 与 compute_outcomes 同口径（返回/计算都用归一方向）。"""
    normalized = {"buy": "long", "sell": "short"}.get(direction, direction)
    if normalized not in ("long", "short"):
        raise ValueError(
            f"direction 必须为 long/short/buy/sell，收到 {direction!r}"
        )
    return normalized


def classify_policies(
    week_state: Any,
    day_state: Any,
    trigger_state: Any,
    event_direction: str,
) -> dict[str, Any]:
    """A/B 策略门控 + 周线关系。

    - week_relation：周线趋势映射 == event → agree；反向 → disagree；
      无法映射（sideways/unknown）→ sideways。
    - A：周日线均 ready 且 closed、trigger ready、日线明确 up/down 且严格等于
      event 方向、周线明确同向。
    - B：周日线均 ready 且 closed、trigger ready、日线明确 up/down 且严格等于
      event 方向（周线方向不限，只须 ready+closed）。
    - 日线 sideways/unknown 或与 event 反向 → A/B 都 false。
    - event_direction 支持 long/short/buy/sell，统一归一为 long/short。
    """
    event_direction = _normalize_direction(event_direction)
    week_ready = bool(week_state.ready and week_state.is_closed)
    day_ready = bool(day_state.ready and day_state.is_closed)
    trig_ready = bool(trigger_state.ready and trigger_state.is_closed)
    week_trade = _trade_direction(getattr(week_state, "trend_direction", "unknown"))
    day_trade = _trade_direction(getattr(day_state, "trend_direction", "unknown"))

    if week_trade == event_direction:
        week_relation = "agree"
    elif week_trade is not None:
        week_relation = "disagree"
    else:
        week_relation = "sideways"

    base = week_ready and day_ready and trig_ready
    day_match = day_trade == event_direction  # 日线须明确 up/down 且严格同向
    a = base and day_match and week_trade == event_direction
    b = base and day_match
    return {"A": a, "B": b, "week_relation": week_relation}


# ═══════════════════════════════════════════════════════════════════════════════
#  5. replay_monitor_events
# ═══════════════════════════════════════════════════════════════════════════════

def replay_monitor_events(
    candles_4h: list[dict[str, Any]],
    strategies: list[str],
    parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """逐根回放：每根把「最近最多 360 根」前缀交给现有 run_monitor_detectors。

    - 事件按 (strategy, event_type, direction, signal_time) 只保留首个；
    - 从第一个 is_closed=False 起立即 cutoff：不读取该 bar 的 OHLC、不调用
      detector；之后即使标 closed 也不可见。未收盘 bar 允许只有 time/is_closed。
    - 已收盘前缀上的事件逐字不变（追加尾巴不改变旧结果）。
    """
    cutoff = len(candles_4h)
    for i, candle in enumerate(candles_4h):
        if not bool(candle.get("is_closed", True)):
            cutoff = i
            break
    seen: set[tuple[str, str, str, int]] = set()
    events: list[dict[str, Any]] = []
    for i in range(cutoff):
        prefix = candles_4h[max(0, i - 359): i + 1]
        for ev in _monitor_signals.run_monitor_detectors(
            prefix, "4h", strategies, parameters or {}
        ):
            signal_time = int(ev["signal_time"])
            key = (str(ev["strategy"]), str(ev["event_type"]),
                   str(ev["direction"]), signal_time)
            if key in seen:
                continue
            seen.add(key)
            events.append(ev)
    return events


# ═══════════════════════════════════════════════════════════════════════════════
#  6. compute_outcomes
# ═══════════════════════════════════════════════════════════════════════════════

def compute_outcomes(
    candles_4h: list[dict[str, Any]],
    event: dict[str, Any],
    *,
    atr_period: int = 14,
    windows: tuple[int, ...] = OUTCOME_WINDOWS,
) -> dict[str, Any]:
    """单事件结果计算（契约与 M3A 同口径）。

    - 输入须通过 validate_closed_4h（唯一/14400 连续/合法 OHLC/全收盘/volume 合法）；
    - direction 支持 long/short/buy/sell，统一归一为 long/short；非法值受控 ValueError；
    - entry = signal close；reference_price 若给出必须有限正数且与事件 bar close
      在 1e-6 相对容差内，否则拒绝（缺失才用 close）；
    - 1R = 事件 bar 处 Wilder ATR(atr_period)；
    - 窗口 = 事件 bar 后 5/10/20/40 根，**不含事件 bar**；
    - first：positive_first / negative_first / neither / ambiguous_same_bar；
      fake 仅 negative_first=True，ambiguous=None；
    - MFE/MAE 从 0 起取非负极值；long/short 镜像；
    - return = next_open（窗口第 1 根 open，入场价）→ 窗口末 close 的方向收益
      （long: (end-close - next-open)；short 取反）；return_r 仍除事件 ATR14，
      return_pct 分母是 next_open。
    """
    validate_closed_4h(candles_4h)
    signal_time = int(event["signal_time"])
    direction = _normalize_direction(str(event["direction"]))
    idx = next((i for i, c in enumerate(candles_4h)
                if _bar_time(c) == signal_time), None)
    if idx is None:
        raise ValueError(f"事件 bar 不存在: signal_time={signal_time}")

    event_close = float(candles_4h[idx]["close"])
    entry_raw = event.get("reference_price")
    if entry_raw is None:
        entry = event_close  # 缺失才用 close
    else:
        if isinstance(entry_raw, bool):
            raise ValueError(f"reference_price 非法（bool）: {entry_raw!r}")
        try:
            entry = float(entry_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"reference_price 非数值: {entry_raw!r}"
            ) from exc
        if not math.isfinite(entry) or entry <= 0:
            raise ValueError(
                f"reference_price 必须为有限正数，收到 {entry_raw!r}"
            )
        if abs(entry - event_close) / event_close > 1e-6:
            raise ValueError(
                f"reference_price({entry!r}) 与事件 bar close({event_close!r})"
                " 偏差超出 1e-6 相对容差"
            )
    highs = [float(c["high"]) for c in candles_4h]
    lows = [float(c["low"]) for c in candles_4h]
    closes = [float(c["close"]) for c in candles_4h]
    atr_list = _atr(highs, lows, closes, int(atr_period))
    atr_abs = atr_list[idx]
    if atr_abs is None or atr_abs <= 0:
        raise ValueError(
            f"事件 bar time={signal_time} 处 ATR({atr_period}) 不可用"
        )
    up_level = entry + atr_abs
    dn_level = entry - atr_abs

    result_windows: list[dict[str, Any]] = []
    for w in windows:
        end = idx + w
        if end >= len(candles_4h):
            result_windows.append({
                "window": w, "matured": False,
                "mfe_r": None, "mfe_pct": None, "mae_r": None, "mae_pct": None,
                "first_touch": None, "fake_breakout": None,
                "return_r": None, "return_pct": None,
                "next_open": None, "end_close": None, "end_time": None,
            })
            continue
        mfe_abs = 0.0
        mae_abs = 0.0
        first: str | None = None
        fake: bool | None = None
        for j in range(idx + 1, end + 1):
            h = float(candles_4h[j]["high"])
            l = float(candles_4h[j]["low"])
            if direction == "long":
                fav, unfav = h - entry, entry - l
                touch_plus = h >= up_level
                touch_minus = l <= dn_level
            else:
                fav, unfav = entry - l, h - entry
                touch_plus = l <= dn_level
                touch_minus = h >= up_level
            mfe_abs = max(mfe_abs, fav)
            mae_abs = max(mae_abs, unfav)
            if first is None:
                if touch_plus and touch_minus:
                    first = TOUCH_AMBIGUOUS
                    fake = None
                elif touch_plus:
                    first = TOUCH_POSITIVE
                    fake = False
                elif touch_minus:
                    first = TOUCH_NEGATIVE
                    fake = True
        if first is None:
            first = TOUCH_NEITHER
            fake = False
        next_open = float(candles_4h[idx + 1]["open"])
        end_close = float(candles_4h[end]["close"])
        signed = (end_close - next_open) if direction == "long" else (next_open - end_close)
        result_windows.append({
            "window": w, "matured": True,
            "mfe_r": mfe_abs / atr_abs,
            "mfe_pct": mfe_abs / entry,
            "mae_r": mae_abs / atr_abs,
            "mae_pct": mae_abs / entry,
            "first_touch": first,
            "fake_breakout": fake,
            "return_r": signed / atr_abs,
            "return_pct": signed / next_open,
            "next_open": next_open,
            "end_close": end_close,
            "end_time": _bar_time(candles_4h[end]),
        })

    return {
        "signal_time": signal_time,
        "direction": direction,
        "entry": entry,
        "atr_abs": atr_abs,
        "windows": result_windows,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  7. wilson_interval / verify_manifest
# ═══════════════════════════════════════════════════════════════════════════════

def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval（双侧，默认 95%）。

    trials 必须 > 0，0 <= successes <= trials，否则 ValueError。
    返回 (lower, upper)，边界裁剪到 [0, 1]。
    """
    if isinstance(trials, bool) or not isinstance(trials, int) or trials <= 0:
        raise ValueError(f"trials 必须为正整数，收到 {trials!r}")
    if isinstance(successes, bool) or not isinstance(successes, int) \
            or not (0 <= successes <= trials):
        raise ValueError(
            f"successes 必须为 [0, trials] 内整数，收到 {successes!r}"
        )
    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denom
    margin = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def verify_manifest(manifest_path: Path, data_dir: Path) -> dict[str, Any]:
    """验证回测 manifest 与其符号文件的一致性。

    规则：
      - manifest 必须是 dict 且含非空 symbols 列表；
      - 每项必须是 dict 且含非空 code / name 与 64 位十六进制 sha256 摘要；
      - 每个 code 必须在 data_dir 下有 ``{code}.json``、可解析为 dict、
        文件 symbol 字段 == code、bar_count（若存在）为正整数；
      - 重算文件内容 sha256 必须与 manifest 摘要一致（防篡改）。
    返回 {"valid": bool, "errors": [str, ...]}。
    """
    errors: list[str] = []
    try:
        with Path(manifest_path).open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False,
                "errors": [f"manifest JSON 损坏或不可读: {exc}"]}
    if not isinstance(manifest, dict):
        return {"valid": False, "errors": ["manifest 必须是 JSON 对象"]}

    symbols = manifest.get("symbols")
    if not isinstance(symbols, list):
        errors.append("manifest 缺少非空 symbols 列表")
    else:
        for i, item in enumerate(symbols):
            if not isinstance(item, dict):
                errors.append(f"symbols[{i}] 必须是对象，收到 {type(item).__name__}")
                continue
            code = item.get("code")
            name = item.get("name")
            if not code or not isinstance(code, str):
                errors.append(f"symbols[{i}] code 缺失或非法: {code!r}")
            if not name or not isinstance(name, str):
                errors.append(f"symbols[{i}] name 缺失或非法: {name!r}")
            expected_sha = item.get("sha256")
            sha_ok = isinstance(expected_sha, str) and len(expected_sha) == 64
            if expected_sha is not None and not sha_ok:
                errors.append(f"symbols[{i}] sha256 摘要非法: {expected_sha!r}")
            if not code or not isinstance(code, str):
                continue
            if not sha_ok:
                errors.append(f"{code}.json 缺少 sha256 摘要")
            fpath = Path(data_dir) / f"{code}.json"
            if not fpath.exists():
                errors.append(f"缺少符号文件 {code}.json")
                continue
            try:
                raw = fpath.read_bytes()
                payload = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                errors.append(f"{code}.json JSON 损坏或不可读: {exc}")
                continue
            if not isinstance(payload, dict):
                errors.append(f"{code}.json 内容必须是 JSON 对象")
                continue
            if payload.get("symbol") != code:
                errors.append(
                    f"{code}.json symbol 字段({payload.get('symbol')!r})"
                    f" 与 manifest code({code!r}) 不一致"
                )
            bar_count = payload.get("bar_count")
            if bar_count is not None and (
                isinstance(bar_count, bool) or not isinstance(bar_count, int)
                or bar_count < 1
            ):
                errors.append(f"{code}.json bar_count 非法: {bar_count!r}")
            if sha_ok:
                digest = hashlib.sha256(raw).hexdigest()
                if digest != expected_sha:
                    errors.append(
                        f"{code}.json sha256 摘要不一致"
                        f"（期望 {expected_sha[:12]}…，实际 {digest[:12]}…）"
                    )
    return {"valid": not errors, "errors": errors}


# ═══════════════════════════════════════════════════════════════════════════════
#  8. MTF-BTC-V1 流水线 helper（冻结/合并/对齐/汇总；纯计算 + HTTP 只读）
# ═══════════════════════════════════════════════════════════════════════════════
#  仅服务于 mtf_backtest_cli 的 fetch/run/verify，不改动上方 1-7 节契约。

class FetchError(Exception):
    """网络级错误（非 HTTP 状态码可表达的错误）。"""


def _https_context() -> ssl.SSLContext:
    """优先 certifi CA 包，保证 HTTPS 校验（无证书回退到默认上下文）。"""
    try:
        import certifi  # type: ignore[import-not-found]
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def http_get(url: str, timeout: float = 60.0) -> tuple[int, bytes]:
    """GET 返回 (http_status, body)。HTTP 非 200 不抛（由调用方记录）；
    网络错误（连接/超时/证书）→ FetchError。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": "mtf-backtest-cli/1.0 (research only)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_https_context()) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), b""
    except OSError as exc:
        raise FetchError(f"网络错误 {url}: {exc}") from exc


def parse_zip_csv(body: bytes) -> list[dict[str, Any]]:
    """解压单文件 zip → parse_binance_kline_csv（有/无表头均正确）。zip 损坏 → ValueError。"""
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            names = zf.namelist()
            if not names:
                raise ValueError("zip 为空（无文件）")
            return parse_binance_kline_csv(zf.read(names[0]))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"zip 损坏: {exc}") from exc


def fetch_binance_zip(url: str, timeout: float = 60.0) -> dict[str, Any]:
    """下载官方 zip：HTTP 非 200 → 只记录（绝不读作 zip）；200 → 解析并记
    content_sha256（原始 zip 字节摘要）。返回记录 dict（确定性字段）。"""
    status, body = http_get(url, timeout=timeout)
    if status != 200:
        return {
            "url": url, "http_status": status, "ok": False,
            "error": f"HTTP {status}",
        }
    sha = hashlib.sha256(body).hexdigest()
    try:
        rows = parse_zip_csv(body)
    except ValueError as exc:
        return {
            "url": url, "http_status": status, "ok": False,
            "error": f"zip/csv 解析失败: {exc}",
        }
    return {
        "url": url, "http_status": status, "ok": True,
        "content_sha256": sha, "row_count": len(rows), "rows": rows,
    }


def write_gzip_json_deterministic(path: Path | str, payload: Any) -> str:
    """确定性 gzip JSON：sort_keys、紧凑分隔符、gzip 头 mtime=0。
    返回压缩文件字节的 sha256（防篡改）。"""
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(data)
    raw = buf.getvalue()
    Path(path).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def read_gzip_json(path: Path | str) -> Any:
    with gzip.GzipFile(filename="", mode="rb",
                       fileobj=io.BytesIO(Path(path).read_bytes())) as gz:
        return json.loads(gz.read().decode("utf-8"))


def _bar_fields_eq(a: dict[str, Any], b: dict[str, Any]) -> bool:
    for k in ("time", "open", "high", "low", "close", "volume"):
        if float(a.get(k)) != float(b.get(k)):
            return False
    return True


def merge_4h_batches(batches: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """多数据源 4H 合并：按 time 去重（相同内容幂等；内容冲突 → ValueError）、
    排序、validate_closed_4h（必须 0 缺口）。返回已排序列表。"""
    by_time: dict[int, dict[str, Any]] = {}
    for batch in batches:
        for candle in batch:
            t = _bar_time(candle)
            if t in by_time:
                if not _bar_fields_eq(by_time[t], candle):
                    raise ValueError(
                        f"bar time={t} 内容冲突（不同数据源 OHLCV 不一致）"
                    )
                continue
            by_time[t] = candle
    merged = sorted(by_time.values(), key=_bar_time)
    validate_closed_4h(merged)
    return merged


def bisect_closed_index(close_times: list[int], as_of: int) -> int:
    """最后一个 close_time <= as_of 的索引；无 → -1。绝不返回未到期的未来 bar。"""
    return bisect.bisect_right(close_times, as_of) - 1


def closed_state_at(
    states: list[Any], bars: list[dict[str, Any]], duration: int, as_of: int
) -> tuple[Any | None, str | None]:
    """as_of 时「最新已收盘」MarketState：按 bar close_time（time+duration）
    bisect。返回 (state|None, reason|None)：找不到 → (None, 原因)；
    找到但未 ready → (state, 原因)。states[i] 与 bars[i] 必须对齐。"""
    if not bars:
        return None, "无该周期 bar"
    close_times = [int(b["time"]) + int(duration) for b in bars]
    i = bisect_closed_index(close_times, as_of)
    if i < 0:
        return None, "as_of 之前无已收盘 bar"
    st = states[i]
    if not st.ready:
        return st, st.missing_reason or "历史不足（未 ready）"
    return st, None


def _trade_direction_of(state: Any) -> str | None:
    return {"up": "long", "down": "short"}.get(
        getattr(state, "trend_direction", "unknown")
    )


def classify_with_reasons(
    week_state: Any, day_state: Any, trigger_state: Any, event_direction: str
) -> dict[str, Any]:
    """A/B 门控 + week_relation；找不到 / 未 ready → A/B false 且 reason 明示。
    与 classify_policies 同口径：A=周日同向，B=仅日同向且周 ready+closed。"""
    nd = _normalize_direction(event_direction)
    reasons: list[str] = []

    def _check(tf: str, st: Any) -> bool:
        if st is None:
            reasons.append(f"{tf} 状态不可用（as_of 之前无已收盘 bar）")
            return False
        if not st.is_closed:
            reasons.append(f"{tf} 未收盘")
            return False
        if not st.ready:
            reasons.append(f"{tf} 未 ready（{st.missing_reason or '历史不足'}）")
            return False
        return True

    wk_ok = _check("week", week_state)
    dy_ok = _check("day", day_state)
    tr_ok = _check("trigger", trigger_state)

    week_relation = "unknown"
    if week_state is not None:
        wt = _trade_direction_of(week_state)
        week_relation = "agree" if wt == nd else ("disagree" if wt is not None else "sideways")
    if not (wk_ok and dy_ok and tr_ok):
        return {
            "A": False, "B": False,
            "week_relation": week_relation,
            "reason": "; ".join(reasons),
        }
    cls = classify_policies(week_state, day_state, trigger_state, nd)
    return {"A": cls["A"], "B": cls["B"],
            "week_relation": cls["week_relation"], "reason": ""}


# ── events.csv（long format：每事件 × 窗口）─────────────────────────────────────

EVENT_CSV_COLUMNS: tuple[str, ...] = (
    "event_key", "time", "year", "raw_direction", "direction", "event_type",
    "strategy", "reference", "A", "B", "B_minus_A", "week_relation",
    "classify_reason", "week_trend", "day_trend", "trigger_trend",
    "setup_vol_phase", "trigger_vol_phase", "trigger_candle_strength",
    "trigger_volume_state", "window", "matured", "mfe_r", "mfe_pct", "mae_r",
    "mae_pct", "first_touch", "fake_breakout", "next_open", "return_r",
    "return_pct",
)
DECISION_CSV_COLUMNS: tuple[str, ...] = EVENT_CSV_COLUMNS + ("n_signals",)


def _utc_year(ts: int) -> int:
    return datetime.datetime.fromtimestamp(
        int(ts), tz=datetime.timezone.utc
    ).year


def _attr(state: Any, name: str) -> str:
    return str(getattr(state, name, "")) if state is not None else ""


def build_event_rows(
    events: list[dict[str, Any]],
    candles_4h: list[dict[str, Any]],
    states_4h: list[Any],
    states_1d: list[Any],
    states_1w: list[Any],
    candles_1d: list[dict[str, Any]],
    candles_1w: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """逐事件扩展为 long-format 行（每窗口一行）。事件 as_of=signal_time+14400，
    用 bar close 时间 bisect 找当时最新已收盘状态；找不到/未 ready 明示原因。"""
    rows: list[dict[str, Any]] = []
    for ev in events:
        signal_time = int(ev["signal_time"])
        as_of = signal_time + STEP4
        week_state, _ = closed_state_at(states_1w, candles_1w, WEEK, as_of)
        day_state, _ = closed_state_at(states_1d, candles_1d, DAY, as_of)
        trig_state, _ = closed_state_at(states_4h, candles_4h, STEP4, as_of)
        cls = classify_with_reasons(
            week_state, day_state, trig_state, str(ev["direction"])
        )
        out = compute_outcomes(candles_4h, ev)
        nd = out["direction"]
        base: dict[str, Any] = {
            "event_key": f"{ev['strategy']}|{ev['event_type']}|{ev['direction']}|{signal_time}",
            "time": signal_time,
            "year": _utc_year(signal_time),
            "raw_direction": str(ev["direction"]),
            "direction": nd,
            "event_type": str(ev["event_type"]),
            "strategy": str(ev["strategy"]),
            "reference": float(ev["reference_price"]),
            "A": 1 if cls["A"] else 0,
            "B": 1 if cls["B"] else 0,
            "B_minus_A": 1 if (cls["B"] and not cls["A"]) else 0,
            "week_relation": cls["week_relation"],
            "classify_reason": cls["reason"],
            "week_trend": _attr(week_state, "trend_direction"),
            "day_trend": _attr(day_state, "trend_direction"),
            "trigger_trend": _attr(trig_state, "trend_direction"),
            "setup_vol_phase": _attr(day_state, "vol_phase"),
            "trigger_vol_phase": _attr(trig_state, "vol_phase"),
            "trigger_candle_strength": _attr(trig_state, "candle_strength"),
            "trigger_volume_state": _attr(trig_state, "volume_state"),
        }
        for w in out["windows"]:
            row = dict(base)
            row.update({
                "window": w["window"],
                "matured": 1 if w["matured"] else 0,
                "mfe_r": w["mfe_r"],
                "mfe_pct": w["mfe_pct"],
                "mae_r": w["mae_r"],
                "mae_pct": w["mae_pct"],
                "first_touch": w["first_touch"] if w["first_touch"] is not None else "",
                "fake_breakout": w["fake_breakout"] if w["fake_breakout"] is not None else "",
                "next_open": w["next_open"],
                "return_r": w["return_r"],
                "return_pct": w["return_pct"],
            })
            rows.append(row)
    return rows


def sort_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (r["time"], r["strategy"], r["event_type"],
                       r["direction"], r["window"]),
    )


def render_events_csv(
    rows: list[dict[str, Any]], columns: tuple[str, ...] = EVENT_CSV_COLUMNS
) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (r.get(k) if r.get(k) is not None else "")
                         for k in columns})
    return buf.getvalue()


def write_events_csv(
    rows: list[dict[str, Any]], path: Path | str,
    columns: tuple[str, ...] = EVENT_CSV_COLUMNS,
) -> None:
    Path(path).write_bytes(render_events_csv(rows, columns).encode("utf-8"))


def _read_events_csv(path: Path | str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        out: list[dict[str, Any]] = []
        for raw in reader:
            row: dict[str, Any] = {}
            for k, v in raw.items():
                if k in ("time", "year", "window", "A", "B", "B_minus_A", "matured"):
                    row[k] = int(v)
                elif k in ("reference", "mfe_r", "mfe_pct", "mae_r", "mae_pct",
                           "next_open", "return_r", "return_pct"):
                    row[k] = float(v) if v != "" else None
                elif k == "fake_breakout":
                    row[k] = None if v == "" else (v == "True")
                else:
                    row[k] = v
            out.append(row)
    return out


def dedupe_decisions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (time, direction, window) 去重：每窗口保留排序后第一个
    （最小 strategy/event_type 的稳定首信号），避免多策略同根同窗口信号
    被重复计数。返回排序后的行。"""
    sorted_rows = sorted(
        rows,
        key=lambda r: (r["time"], r["direction"], r["strategy"],
                       r["event_type"], r["window"]),
    )
    seen: set[tuple[int, str, int]] = set()
    out: list[dict[str, Any]] = []
    for r in sorted_rows:
        key = (int(r["time"]), str(r["direction"]), int(r["window"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def decision_n_signals(rows: list[dict[str, Any]]) -> dict[tuple[int, str], int]:
    """同 (time, direction) 的唯一 event_key 数量：long-format 的 4 个窗口
    不重复计（即多策略同根信号数）。"""
    keys: dict[tuple[int, str], set[str]] = {}
    for r in rows:
        key = (int(r["time"]), str(r["direction"]))
        keys.setdefault(key, set()).add(str(r["event_key"]))
    return {k: len(v) for k, v in keys.items()}


# ── summary.json（只从 events.csv 重新读取聚合）────────────────────────────────

COHORT_KEYS: tuple[str, ...] = (
    "raw", "A", "B", "B_minus_A", "B_week_agree", "B_week_disagree",
    "B_week_sideways",
)
GROUP_DIMS: tuple[str, ...] = (
    "direction", "event_type", "year", "setup_vol_phase", "trigger_vol_phase",
    "trigger_candle_strength",
)
EXPLORATORY_N = 30


def _fmt(x: float | None, digits: int = 6) -> float | None:
    if x is None:
        return None
    v = round(float(x), digits)
    return 0.0 if v == 0 else v


def _cohort_of(row: dict[str, Any]) -> set[str]:
    """行所属的全部 cohort（成员关系，非互斥）：A / B / B_minus_A / B_week_*。
    返回空集表示不属于任何专属 cohort（raw 恒含全部行）。"""
    cohorts: set[str] = set()
    a = row.get("A") == 1
    b = row.get("B") == 1
    if a:
        cohorts.add("A")
    if b:
        cohorts.add("B")
        wr = row.get("week_relation")
        if wr == "agree":
            cohorts.add("B_week_agree")
        elif wr == "disagree":
            cohorts.add("B_week_disagree")
        elif wr == "sideways":
            cohorts.add("B_week_sideways")
        if not a:
            cohorts.add("B_minus_A")
    return cohorts


def _window_stats(rows: list[dict[str, Any]], w: int) -> dict[str, Any]:
    matured = [r for r in rows
               if r.get("window") == w and r.get("matured") == 1]
    n = len(matured)
    empty = {
        "window": w, "n": 0, "unique_entry_points": 0, "exploratory": True,
        "next_open_win": {"count": 0, "rate": None, "wilson95": None},
        "median_return_pct": None, "mean_mfe_r": None, "mean_mae_r": None,
        "first": {"positive_first": 0, "negative_first": 0, "neither": 0,
                  "ambiguous_same_bar": 0},
        "fake": {"known": 0, "count": 0, "rate": None, "wilson95": None},
    }
    if n == 0:
        return empty
    win_count = sum(1 for r in matured if r["return_r"] > 0)
    lo, hi = wilson_interval(win_count, n)
    returns = sorted(r["return_pct"] for r in matured)
    med = returns[n // 2] if n % 2 else (returns[n // 2 - 1] + returns[n // 2]) / 2
    known = [r for r in matured if r["fake_breakout"] is not None]
    fake_count = sum(1 for r in known if r["fake_breakout"] is True)
    fake_lo, fake_hi = wilson_interval(fake_count, len(known)) if known else (None, None)
    first: dict[str, int] = {"positive_first": 0, "negative_first": 0,
                             "neither": 0, "ambiguous_same_bar": 0}
    for r in matured:
        ft = r["first_touch"]
        first[ft] = first.get(ft, 0) + 1
    return {
        "window": w, "n": n,
        "unique_entry_points": len(
            {(int(r["time"]), str(r["direction"])) for r in matured}
        ),
        "exploratory": n < EXPLORATORY_N,
        "next_open_win": {
            "count": win_count,
            "rate": _fmt(win_count / n),
            "wilson95": [_fmt(lo), _fmt(hi)],
        },
        "median_return_pct": _fmt(med),
        "mean_mfe_r": _fmt(sum(r["mfe_r"] for r in matured) / n),
        "mean_mae_r": _fmt(sum(r["mae_r"] for r in matured) / n),
        "first": first,
        "fake": {
            "known": len(known),
            "count": fake_count,
            "rate": _fmt(fake_count / len(known)) if known else None,
            "wilson95": [_fmt(fake_lo), _fmt(fake_hi)] if known else None,
        },
    }


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(w): _window_stats(rows, w) for w in OUTCOME_WINDOWS}


def _aggregate_groups(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"overall": _group_stats(rows)}
    for dim in GROUP_DIMS:
        values = sorted({r.get(dim) for r in rows})
        out[f"by_{dim}"] = {
            str(v): _group_stats([r for r in rows if r.get(dim) == v])
            for v in values
        }
    return out


def summarize_events_csv(csv_path: Path | str) -> dict[str, Any]:
    """summary.json 唯一入口：只从 events.csv 重新读取聚合。
    cohorts=raw/A/B/B_minus_A/B_week_agree/B_week_disagree/B_week_sideways；
    decision_level 由 (time,direction) 去重后同口径聚合。"""
    rows = _read_events_csv(csv_path)
    cohorts: dict[str, Any] = {}
    for cohort in COHORT_KEYS:
        cohort_rows = (rows if cohort == "raw"
                       else [r for r in rows if cohort in _cohort_of(r)])
        cohorts[cohort] = _aggregate_groups(cohort_rows)
    decisions = dedupe_decisions(rows)
    return {
        "schema": "mtf-btc-backtest-v1.summary.v1",
        "generated_from": "events.csv",
        "events_rows": len(rows),
        "decision_rows": len(decisions),
        "cohorts": cohorts,
        "decision_level": _aggregate_groups(decisions),
    }


# ── 端到端确定性流水线（同冻结输入 → events.csv / decisions.csv / summary.json）──

def run_backtest_pipeline(
    candles_4h: list[dict[str, Any]],
    strategies: list[str],
    parameters: dict[str, Any] | None,
    out_events: Path | str,
    out_summary: Path | str,
    out_decisions: Path | str,
    config: Any = DEFAULT_MARKET_STATE_CONFIG,
) -> dict[str, Any]:
    """冻结 4H 输入 → 聚合 1d/1w、M1 各预计算一次、逐根 360 前缀回放、
    events.csv（long format 稳定排序）→ 只从 events.csv 复算 decisions.csv 与
    summary.json。全部输出确定性（无时钟/无随机）。返回确定性摘要。

    - 从第一个 is_closed=False 起立即 cutoff：不读其 OHLC、后续不可见；
      校验/聚合/状态/结果只作用于 closed 前缀（尾巴可只有 time/is_closed）。
    - 任何输出写入前先 mkdir(parents=True, exist_ok=True)。
    """
    cutoff = len(candles_4h)
    for i, candle in enumerate(candles_4h):
        if not bool(candle.get("is_closed", True)):
            cutoff = i
            break
    closed = candles_4h[:cutoff]
    validate_closed_4h(closed)
    cfg = config or DEFAULT_MARKET_STATE_CONFIG
    candles_1d = aggregate_utc(closed, "1day")
    candles_1w = aggregate_utc(closed, "1week")
    states_4h = precompute_market_states(closed, cfg)
    states_1d = precompute_market_states(candles_1d, cfg)
    states_1w = precompute_market_states(candles_1w, cfg)
    events = replay_monitor_events(closed, strategies, parameters or {})
    rows = sort_event_rows(build_event_rows(
        events, closed, states_4h, states_1d, states_1w,
        candles_1d, candles_1w,
    ))
    csv_text = render_events_csv(rows)
    Path(out_events).parent.mkdir(parents=True, exist_ok=True)
    Path(out_events).write_bytes(csv_text.encode("utf-8"))

    decisions = dedupe_decisions(rows)
    n_sig = decision_n_signals(rows)
    dec_rows: list[dict[str, Any]] = []
    for r in decisions:
        dr = dict(r)
        dr["n_signals"] = n_sig.get((int(r["time"]), str(r["direction"])), 1)
        dec_rows.append(dr)
    Path(out_decisions).parent.mkdir(parents=True, exist_ok=True)
    Path(out_decisions).write_bytes(
        render_events_csv(dec_rows, DECISION_CSV_COLUMNS).encode("utf-8")
    )

    summary = summarize_events_csv(out_events)
    summary_bytes = (
        json.dumps(summary, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False) + "\n"
    ).encode("utf-8")
    Path(out_summary).parent.mkdir(parents=True, exist_ok=True)
    Path(out_summary).write_bytes(summary_bytes)

    def _sha(p: Path | str) -> str:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    return {
        "events_rows": len(rows),
        "event_count": len(events),
        "decision_count": len(decisions),
        "events_sha256": _sha(out_events),
        "summary_sha256": _sha(out_summary),
        "decisions_sha256": _sha(out_decisions),
        "first_event_time": min(int(e["signal_time"]) for e in events) if events else None,
        "last_event_time": max(int(e["signal_time"]) for e in events) if events else None,
    }
