"""M3A 历史结果标注 —— 纯计算、因果、可回放（2026-08-13）。

任务书要点（领导拍板）：
  - 1R = 事件 4H MarketState 的 Wilder ATR(14)，必须来自 M2 决策快照
    （trigger.market_state.features.atr），且传入 MarketStateConfig 的
    config_hash 必须等于决策 config_hash.market_state；同时输出相对参考价
    （reference_price）的百分比（atr_pct / mfe_pct / mae_pct，小数比例）。
  - 窗口 = 触发 bar 后 5/10/20/40 根完整连续已收盘 4H bar，**不含触发 bar**；
    缺根 / 未收盘 / label_as_of 未到 → censored，不拿短窗口冒充。
  - 同 bar 首次同时触及 ±1R → ambiguous_same_bar（fake_breakout=null），
    不得假设 OHLC 内路径；其余 positive_first / negative_first / neither；
    fake_breakout 仅 negative_first=true。
  - MFE/MAE 从 0 起取非负极值；long/short 镜像；事件 bar 极值不计。
  - 未来 K 线只可生成标签，不得改变事件时点 decision/risk/condition 快照。

as-of 契约（继承 M0/M1/M2）：
  - 输入 4H 序列按 time 稳定排序；只消费「close_time <= label_as_of 且未显式
    is_closed=False」的连续前缀；首个未收盘或 close_time>label_as_of 的 bar 即
    cutoff（其 OHLC 可缺，**不得读取**），之后全部不可见。
  - 追加任何未来 K 线不得改变已成熟窗口结果与 decision digest。

纯函数：无网络 / 无数据库 / 无时钟读取。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from app.core.market_state import (
    DEFAULT_MARKET_STATE_CONFIG,
    MarketStateConfig,
    precompute_market_states,
)
from app.core.multi_timeframe import FusionDecision, REFERENCE_TOLERANCE
from app.schemas.market_fusion import validate_decision

SCHEMA_VERSION = "m3a-v1"
WINDOWS = (5, 10, 20, 40)

# 首触及分类 / 假突破（fake_breakout 仅 negative_first=true）
TOUCH_POSITIVE = "positive_first"
TOUCH_NEGATIVE = "negative_first"
TOUCH_NEITHER = "neither"
TOUCH_AMBIGUOUS = "ambiguous_same_bar"


# ═══════════════════════════════════════════════════════════════════════════════
#  基础工具
# ═══════════════════════════════════════════════════════════════════════════════

def _bar_time(candle: dict) -> int:
    """bar 开盘秒（int 校验）：缺 time / 非法 / NaN / Inf / 负数 / 非整数 →
    明确 ValueError；cutoff bar 可缺 OHLC 但必须有合法 time。"""
    raw = candle.get("time")
    if raw is None:
        raise ValueError("bar 缺少 time 字段（bar.time 必须是整数秒）")
    try:
        f = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"bar time 非法: {raw!r}（必须是整数秒）")
    if not math.isfinite(f) or f < 0 or f != int(f):
        raise ValueError(f"bar time 非法: {raw!r}（必须是整数秒）")
    return int(f)


def _validate_label_as_of(label_as_of: Any) -> None:
    """label_as_of 必须为非负整数秒（bool/负数/非整数/缺失 → ValueError）。"""
    if isinstance(label_as_of, bool) or not isinstance(label_as_of, int) or label_as_of < 0:
        raise ValueError(f"label_as_of 必须为非负整数秒，收到 {label_as_of!r}")


def _validate_ohlc(bar: dict) -> None:
    """消费前缀内 bar 的 OHLC 必须有限正数且满足
    high >= max(open, close) >= min(open, close) >= low。"""
    t = _bar_time(bar)
    vals: dict[str, float] = {}
    for name in ("open", "high", "low", "close"):
        raw = bar.get(name)
        if raw is None:
            raise ValueError(f"bar time={t} 缺 {name}（已收盘 bar 必须含 OHLC）")
        try:
            f = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"bar time={t} {name} 非数值: {raw!r}")
        if not math.isfinite(f):
            raise ValueError(f"bar time={t} {name} 非有限: {raw!r}")
        if f <= 0:
            raise ValueError(f"bar time={t} {name} 必须为正数: {raw!r}")
        vals[name] = f
    h, o, c, l = vals["high"], vals["open"], vals["close"], vals["low"]
    if not (h >= max(o, c) >= min(o, c) >= l):
        raise ValueError(
            f"bar time={t} OHLC 关系非法: high={h} open={o} close={c} low={l}"
            "（必须 high >= max(open,close) >= min(open,close) >= low）"
        )


def _freeze(value: Any) -> Any:
    """递归深冻结：dict → MappingProxyType、list/tuple → tuple（只读）。"""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    """_freeze 的逆操作（to_dict 时生成全新普通 dict/list，脱钩）。"""
    if isinstance(value, MappingProxyType):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


# ═══════════════════════════════════════════════════════════════════════════════
#  决策入口校验
# ═══════════════════════════════════════════════════════════════════════════════

def decision_digest(decision_dict: dict[str, Any]) -> str:
    """决策快照的严格 canonical digest（第一轮整改）。

    序列化：sort_keys=True + allow_nan=False，**不使用 default=str**（M2 决策
    必须本身就是 JSON 可序列化；遇到不可序列化/NaN → 受控 ValueError）。
    sha256 前 16 位。用于回放校验与批量去重。
    """
    try:
        payload = json.dumps(decision_dict, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"decision 无法 canonical 序列化（含 NaN/不可序列化）: {e}") from e
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_decision(decision: FusionDecision | dict[str, Any]) -> dict[str, Any]:
    """统一决策入口：FusionDecision 或 m2-v1 dict → 规范化 dict。

    - dict 必须通过 validate_decision（M2 严格 schema：伪造 / NaN / 语义矛盾
      一律拒绝）；
    - 必须 eligible（拒绝态不产生标签）。
    """
    if isinstance(decision, FusionDecision):
        d = decision.to_dict()
    elif isinstance(decision, dict):
        d = decision
    else:
        raise ValueError(
            f"decision 必须为 FusionDecision 或 m2-v1 dict，收到 "
            f"{type(decision).__name__}"
        )
    try:
        validated = validate_decision(d)
    except ValidationError as e:
        raise ValueError(f"decision 未通过 m2-v1 schema 校验（伪造/不完整）: {e}") from e
    if not validated.eligible:
        raise ValueError("decision 必须 eligible（拒绝态不产生结果标签）")
    return validated.model_dump()


def _prepare_4h(
    candles_4h: list[dict],
    step: int,
    label_as_of: int,
) -> tuple[list[dict], list[dict], str | None]:
    """预处理 4H 序列（cutoff 先于任何 OHLC 读取）。

    返回 (全序列已排序, 已收盘连续前缀, cutoff 触发原因)。
    cutoff 原因：'unclosed'（显式未收盘）/ 'as_of'（close_time > label_as_of）
    / None（无 cutoff，全部可见）。
    """
    _validate_label_as_of(label_as_of)
    if not isinstance(step, int) or step <= 0:
        raise ValueError(f"4H 步长必须为正整数秒，收到 {step!r}")
    bars = sorted(candles_4h, key=_bar_time)
    if not bars:
        raise ValueError("4H 序列为空（无法定位事件 bar）")
    seen: set[int] = set()
    cutoff = len(bars)
    cutoff_reason: str | None = None
    for i, c in enumerate(bars):
        t = _bar_time(c)
        if t in seen:
            raise ValueError(f"4H bar time 重复: {t}（唯一 time 是回放前提）")
        seen.add(t)
        # as_of cutoff 判定优先于读取 is_closed（主审反例）：截止之外的 bar
        # 除 time 用于 cutoff 外，其 is_closed/OHLC/volume/数量不得影响任何
        # label 字段——先判 close_time > label_as_of，再判显式未收盘。
        if t + step > label_as_of:
            cutoff = i
            cutoff_reason = "as_of"
            break
        if c.get("is_closed", True) is False:
            cutoff = i
            cutoff_reason = "unclosed"
            break
    closed = bars[:cutoff]
    # 4H 步长校验（消费前缀内相邻差 == step；cutoff 之后不可见不校验）
    for i in range(1, len(closed)):
        prev_t, cur_t = _bar_time(closed[i - 1]), _bar_time(closed[i])
        if cur_t - prev_t != step:
            raise ValueError(
                f"4H 步长异常: bar time {prev_t} → {cur_t}（期望 {step}，"
                "消费前缀内必须连续）"
            )
    # OHLC 校验（只读消费前缀）
    for b in closed:
        _validate_ohlc(b)
    return bars, closed, cutoff_reason


# ═══════════════════════════════════════════════════════════════════════════════
#  结果模型（frozen + 深冻结：深不可变，to_dict 每次脱钩）
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PathStep:
    """窗口内单根 bar 的完整快照（含 OHLC，供 schema 复算审计）。

    第二轮整改：增加 open/high/low/close（均有限正数），使 schema 能基于
    逐 bar OHLC 独立复算 MFE/MAE 与首触及分类（体积取舍见文档）。
    """

    bar_time: int
    vol_phase: str
    trend_strength: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "bar_time": self.bar_time,
            "vol_phase": self.vol_phase,
            "trend_strength": self.trend_strength,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class WindowOutcome:
    """单窗口（5/10/20/40）结果：成熟或删失。

    第二轮整改：删失原因结构化——censor_code ∈
    {label_as_of_not_reached, unclosed, missing_bars}（成熟为 None）+
    人话 censor_reason。判定先看 expected end close_time 是否超过
    label_as_of（同一 as_of 下追加不可见未来 bar 不改变完整 label）。
    """

    window: int
    matured: bool
    censor_code: str | None = None
    censor_reason: str | None = None
    mfe_r: float | None = None
    mfe_pct: float | None = None
    mae_r: float | None = None
    mae_pct: float | None = None
    first_touch: str | None = None
    fake_breakout: bool | None = None
    end_vol_phase: str | None = None
    end_trend_strength: str | None = None
    end_close: float | None = None
    end_time: int | None = None
    path: tuple[PathStep, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "matured": self.matured,
            "censor_code": self.censor_code,
            "censor_reason": self.censor_reason,
            "mfe_r": self.mfe_r,
            "mfe_pct": self.mfe_pct,
            "mae_r": self.mae_r,
            "mae_pct": self.mae_pct,
            "first_touch": self.first_touch,
            "fake_breakout": self.fake_breakout,
            "end_vol_phase": self.end_vol_phase,
            "end_trend_strength": self.end_trend_strength,
            "end_close": self.end_close,
            "end_time": self.end_time,
            "path": [p.to_dict() for p in self.path],
        }


@dataclass(frozen=True)
class OutcomeLabel:
    """单事件完整结果标签（frozen + 深冻结）。

    第一轮整改：保存深冻结的完整 normalized m2 decision_snapshot（可验证
    审计：schema 用 MarketFusionOut 重验证并重算 digest）。
    """

    schema_version: str
    decision_digest: str
    symbol: str
    as_of: int
    label_as_of: int
    signal_time: int
    direction: str
    reference_price: float
    risk: MappingProxyType
    config_hash: MappingProxyType
    event_state: MappingProxyType
    snapshots: MappingProxyType
    decision_snapshot: MappingProxyType
    market_state_config: MappingProxyType
    replay_context: tuple[dict[str, Any], ...]
    windows: tuple[WindowOutcome, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "risk", _freeze(dict(self.risk)))
        object.__setattr__(self, "config_hash", _freeze(dict(self.config_hash)))
        object.__setattr__(self, "event_state", _freeze(dict(self.event_state)))
        object.__setattr__(self, "snapshots", _freeze(dict(self.snapshots)))
        object.__setattr__(self, "decision_snapshot", _freeze(dict(self.decision_snapshot)))
        object.__setattr__(self, "market_state_config", _freeze(dict(self.market_state_config)))
        object.__setattr__(self, "replay_context", tuple(
            _freeze(dict(b)) for b in self.replay_context))
        object.__setattr__(self, "windows", tuple(self.windows))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_digest": self.decision_digest,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "label_as_of": self.label_as_of,
            "signal_time": self.signal_time,
            "direction": self.direction,
            "reference_price": self.reference_price,
            "risk": _thaw(self.risk),
            "config_hash": _thaw(self.config_hash),
            "event_state": _thaw(self.event_state),
            "snapshots": _thaw(self.snapshots),
            "decision_snapshot": _thaw(self.decision_snapshot),
            "market_state_config": _thaw(self.market_state_config),
            "replay_context": [_thaw(b) for b in self.replay_context],
            "windows": [w.to_dict() for w in self.windows],
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  单窗口计算
# ═══════════════════════════════════════════════════════════════════════════════

def _window_outcome(
    w: int,
    event_idx: int,
    replay_start_idx: int,
    closed: list[dict],
    cutoff_reason: str | None,
    config: MarketStateConfig,
    direction: str,
    entry: float,
    atr_abs: float,
    signal_time: int,
    step: int,
    label_as_of: int,
) -> WindowOutcome:
    """计算窗口 w 的结果。事件 bar 极值**不计**：只统计其后 w 根 bar。

    第一轮整改（同源重放）：窗口 bar 的 MarketState 从 replay slice 起点
    （replay_start_idx）起计算，与事件时点重放使用同一历史窗口。

    第二轮整改（删失结构化稳定码）：判定**先看** expected end close_time
    （= signal_time + (w+1)*step，第 w 根未来 bar 收盘）是否超过 label_as_of
    ——同一 as_of 下追加任何不可见未来 bar 不改变完整 label（输入形态不改变
    输出）；截止已到但 bar 不存在才 missing_bars；显式未收盘且窗口所需 bar
    落在 cutoff 则 unclosed。
    """
    end = event_idx + w  # 窗口末 bar 索引（闭区间）
    if end >= len(closed):
        # 第 w 根未来 bar：开盘 = signal_time + w*step，收盘 = + (w+1)*step
        expected_end_close = signal_time + (w + 1) * step
        if expected_end_close > label_as_of:
            code = "label_as_of_not_reached"
            reason = (f"label_as_of 未到（窗口第 {w} 根 close_time="
                      f"{expected_end_close} > label_as_of={label_as_of}）")
        elif cutoff_reason == "unclosed":
            code = "unclosed"
            reason = "未收盘（窗口内存在显式未收盘 bar，数据未确认）"
        else:
            # missing 数量只基于可见已收盘前缀（主审反例：不得读取全 bars）
            visible_after = len(closed) - 1 - event_idx
            code = "missing_bars"
            reason = (f"缺根（事件 bar 后仅有 {visible_after} 根已收盘可见 "
                      f"bar，需要 {w}）")
        return WindowOutcome(window=w, matured=False,
                             censor_code=code, censor_reason=reason)

    # 因果计算窗口 bar 的 MarketState：从 replay slice 起点到窗口末 bar
    prefix = closed[replay_start_idx:end + 1]
    states = precompute_market_states(prefix, config)
    # 窗口 bar 在 prefix 中的索引：事件 bar 后第 1 根起共 w 根
    win_start = event_idx + 1 - replay_start_idx
    win_states = states[win_start:win_start + w]

    up_level = entry + atr_abs
    dn_level = entry - atr_abs
    mfe_abs = 0.0
    mae_abs = 0.0
    first: str | None = None
    fake: bool | None = None
    path: list[PathStep] = []
    for st in win_states:
        i = st.bar_index
        b = prefix[i]
        o, h, l, c = (float(b["open"]), float(b["high"]),
                      float(b["low"]), float(b["close"]))
        if direction == "long":
            fav, unfav = h - entry, entry - l
            # +1R（有利）= 上涨；-1R（不利）= 下跌
            touch_plus = h >= up_level
            touch_minus = l <= dn_level
        else:  # short：镜像
            fav, unfav = entry - l, h - entry
            # +1R（有利）= 下跌；-1R（不利）= 上涨
            touch_plus = l <= dn_level
            touch_minus = h >= up_level
        mfe_abs = max(mfe_abs, fav)
        mae_abs = max(mae_abs, unfav)
        if first is None:
            if touch_plus and touch_minus:
                # 同 bar 首次同时触及 ±1R：不得假设 OHLC 内路径 → ambiguous
                first = TOUCH_AMBIGUOUS
                fake = None
            elif touch_plus:
                first = TOUCH_POSITIVE
                fake = False
            elif touch_minus:
                first = TOUCH_NEGATIVE
                fake = True
        path.append(PathStep(
            bar_time=st.bar_time,
            vol_phase=st.vol_phase,
            trend_strength=st.trend_strength,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=float(b.get("volume") or 0.0),
        ))
    # 从 0 起取非负极值
    mfe_abs = max(0.0, mfe_abs)
    mae_abs = max(0.0, mae_abs)
    if first is None:
        first = TOUCH_NEITHER
        fake = False
    last = win_states[-1]
    return WindowOutcome(
        window=w,
        matured=True,
        censor_code=None,
        censor_reason=None,
        mfe_r=mfe_abs / atr_abs,
        mfe_pct=mfe_abs / entry,
        mae_r=mae_abs / atr_abs,
        mae_pct=mae_abs / entry,
        first_touch=first,
        fake_breakout=fake,
        end_vol_phase=last.vol_phase,
        end_trend_strength=last.trend_strength,
        end_close=last.close,
        end_time=last.bar_time,
        path=tuple(path),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  公开入口
# ═══════════════════════════════════════════════════════════════════════════════

def label_decision(
    decision: FusionDecision | dict[str, Any],
    candles_4h: list[dict],
    label_as_of: int,
    config: MarketStateConfig = DEFAULT_MARKET_STATE_CONFIG,
) -> OutcomeLabel:
    """单事件结果标签（纯计算）。

    硬约束（第一轮整改加固）：
      - decision 必须经 validate_decision 通过且 eligible（拒绝伪造/拒绝态/NaN）；
      - 传入 config 的 hash 必须等于决策 config_hash.market_state；
      - **风险口径**：config.atr_period 必须 == 14（Wilder ATR(14) 冻结）；
        risk = {method: 'wilder_atr', period: 14, atr_abs, atr_pct}；
        atr_abs 来自决策 trigger.market_state.features.atr（有限正数），
        atr_pct = atr_abs / reference；
      - **同源重放**：用决策 trigger.market_state.bar_index 定位事件时点
        历史起点（time = signal_time - bar_index*step），在该 slice 上重算
        事件时点 MarketState；其完整语义快照必须与决策 trigger market_state
        逐字段一致后才标注（事件前 bar 篡改 → 受控拒绝）；窗口路径也从同一
        replay slice 逐窗口计算；
      - 事件 bar（signal_time）必须存在于已收盘前缀且 close≈reference；
      - 4H 序列唯一 time、消费前缀内步长连续、OHLC 有限正数且关系合法。
    未来 K 线只可生成标签，不得改变事件时点 decision/risk/condition 快照。
    """
    d = _normalize_decision(decision)
    if config.config_hash() != d["config_hash"]["market_state"]:
        raise ValueError(
            "MarketStateConfig hash 与决策 config_hash.market_state 不匹配"
            "（必须用与决策相同的 M1 配置重放）"
        )
    if int(config.atr_period) != 14:
        raise ValueError(
            f"M3A 风险口径冻结为 Wilder ATR(14)：config.atr_period 必须为 14，"
            f"收到 {config.atr_period!r}"
        )
    symbol = d["symbol"]
    as_of = int(d["as_of"])
    direction = d["direction"]
    te = d["trigger_event"]
    signal_time = te["signal_time"]
    reference = te["reference_price"]
    trig = d["trigger"]
    atr_abs = trig["market_state"]["features"]["atr"]
    if atr_abs is None or not math.isfinite(float(atr_abs)) or float(atr_abs) <= 0:
        raise ValueError(f"风险 ATR 非法（必须有限正数）: {atr_abs!r}")
    atr_abs = float(atr_abs)
    if reference is None or not math.isfinite(float(reference)) or float(reference) <= 0:
        raise ValueError(f"reference_price 非法（必须有限正数）: {reference!r}")
    reference = float(reference)
    step = int(trig["bar_close_time"]) - int(trig["bar_time"])
    if step <= 0:
        raise ValueError(f"决策 4H 步长非法: {step}")

    bars, closed, cutoff_reason = _prepare_4h(candles_4h, step, label_as_of)

    # 事件 bar 定位（必须在已收盘前缀内）
    event_idx: int | None = None
    for i, b in enumerate(closed):
        if _bar_time(b) == signal_time:
            event_idx = i
            break
    if event_idx is None:
        raise ValueError(
            f"事件 bar 不存在（signal_time={signal_time} 不在已收盘前缀中，"
            "label_as_of 太早或序列缺该 bar）"
        )
    eb_close = float(closed[event_idx]["close"])
    if abs(eb_close - reference) > REFERENCE_TOLERANCE * abs(reference):
        raise ValueError(
            f"事件 bar close({eb_close}) 与 reference({reference}) 不一致"
            "（reference 必须来自决策快照对应 4H bar）"
        )

    # ── 同源重放（第一轮整改）────────────────────────────────────────────
    trigger_bar_index = int(trig["market_state"]["bar_index"])
    replay_start_time = signal_time - trigger_bar_index * step
    replay_start_idx: int | None = None
    for i, b in enumerate(closed):
        if _bar_time(b) == replay_start_time:
            replay_start_idx = i
            break
    if replay_start_idx is None or replay_start_idx < 0:
        raise ValueError(
            f"无法同源重放：缺少事件 bar 前 {trigger_bar_index} 根历史"
            f"（replay 起点 time={replay_start_time} 不在已收盘前缀中）"
        )
    replay_prefix = closed[replay_start_idx:event_idx + 1]
    replayed_states = precompute_market_states(replay_prefix, config)
    replayed = replayed_states[-1]
    expected_trigger = trig["market_state"]
    if replayed.to_dict() != expected_trigger:
        raise ValueError(
            "同源重放不一致：事件时点 MarketState 与决策 trigger.market_state "
            "不符（输入序列在事件 bar 前被篡改或历史不一致，拒绝标注）"
        )

    windows = tuple(
        _window_outcome(w, event_idx, replay_start_idx, closed,
                        cutoff_reason, config, direction, reference, atr_abs,
                        signal_time, step, label_as_of)
        for w in WINDOWS
    )
    event_state = {
        "vol_phase": trig["market_state"]["volatility"]["phase"],
        "trend_strength": trig["market_state"]["trend"]["strength"],
    }
    # 第三轮收口：自包含可重放——完整 M1 配置字段快照 + 事件时点 replay
    # context（原始起点到事件 bar 的 time/OHLC/volume）
    import dataclasses
    market_state_config = dataclasses.asdict(config)
    replay_context = tuple(
        {
            "time": _bar_time(b),
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "volume": float(b.get("volume") or 0.0),
        }
        for b in closed[replay_start_idx:event_idx + 1]
    )
    return OutcomeLabel(
        schema_version=SCHEMA_VERSION,
        decision_digest=decision_digest(d),
        symbol=symbol,
        as_of=as_of,
        label_as_of=label_as_of,
        signal_time=signal_time,
        direction=direction,
        reference_price=reference,
        risk={
            "method": "wilder_atr",
            "period": 14,
            "atr_abs": atr_abs,
            "atr_pct": atr_abs / reference,
        },
        config_hash={
            "market_state": d["config_hash"]["market_state"],
            "fusion": d["config_hash"]["fusion"],
        },
        event_state=event_state,
        snapshots={"context": d["context"], "setup": d["setup"], "trigger": d["trigger"]},
        decision_snapshot=d,
        market_state_config=market_state_config,
        replay_context=replay_context,
        windows=windows,
    )


def label_batch(
    decisions: list[FusionDecision | dict[str, Any]],
    candles_4h: list[dict],
    label_as_of: int,
    config: MarketStateConfig = DEFAULT_MARKET_STATE_CONFIG,
) -> list[OutcomeLabel]:
    """批量标签：同一 4H 序列与 label_as_of 下逐事件标注。

    受控规则（第一轮整改）：同一 candles 序列 → 拒绝混合 symbol；
    重复 decision（相同 decision_digest）→ ValueError；
    标签按输入顺序返回（每标签独立，结果确定性）。
    """
    labels: list[OutcomeLabel] = []
    seen: set[str] = set()
    symbols: set[str] = set()
    for dec in decisions:
        lab = label_decision(dec, candles_4h, label_as_of, config)
        symbols.add(lab.symbol)
        if len(symbols) > 1:
            raise ValueError(
                f"label_batch 拒绝混合 symbol（同一 candles 序列必须同 symbol）: "
                f"{sorted(symbols)}"
            )
        if lab.decision_digest in seen:
            raise ValueError(f"重复 decision（decision_digest={lab.decision_digest}）")
        seen.add(lab.decision_digest)
        labels.append(lab)
    return labels


def summarize(labels: list[OutcomeLabel | dict[str, Any]]) -> dict[str, Any]:
    """确定性汇总（只聚合成熟样本）。

    受控规则（第一轮整改）：
      - 空样本 → 返回空汇总结构（不抛异常）；
      - 混合 symbol → ValueError；重复 decision_digest → ValueError；
        混合 config_hash → ValueError；
      - 每窗口输出：样本/删失数、MFE/MAE 均值（R 与 pct）、四类首触及计数、
        已知样本 fake rate（= negative_first / (samples - ambiguous)，分母 0 → null）；
      - 分组：按 (window, direction, event_type, Context 方向+强度, Setup 方向+
        强度+波动 phase, Trigger 波动 phase+蜡烛强度+成交量) 与 (window, 状态
        迁移) 稳定排序输出；每组 samples 总和 == 窗口 samples。
    """
    from app.schemas.outcome_labeling import validate_outcome_label

    def _as_dict(l: OutcomeLabel | dict[str, Any]) -> dict[str, Any]:
        if isinstance(l, OutcomeLabel):
            d = l.to_dict()
            validate_outcome_label(d)  # 第二轮整改：绝不信任 dataclass 内存对象
            return d
        if isinstance(l, dict):
            validate_outcome_label(l)  # 严格校验（伪造/NaN → ValidationError）
            return l
        raise ValueError(
            f"标签必须为 OutcomeLabel 或 m3a-v1 dict，收到 {type(l).__name__}"
        )

    def _win(label_d: dict[str, Any], w: int) -> dict[str, Any]:
        for x in label_d["windows"]:
            if x["window"] == w:
                return x
        raise ValueError(f"标签缺少窗口 {w}")  # schema 已保证不会发生

    def _group_key(label_d: dict[str, Any]) -> tuple[str, ...]:
        """三层条件分组维度（第一轮整改补齐用户关心维度）。"""
        snap = label_d["snapshots"]
        ds = label_d["decision_snapshot"]
        ctx = snap["context"]["market_state"]
        setup = snap["setup"]["market_state"]
        trig = snap["trigger"]["market_state"]
        return (
            label_d["direction"],
            ds["trigger_event"]["event_type"],
            ctx["trend"]["direction"],
            ctx["trend"]["strength"],
            setup["trend"]["direction"],
            setup["trend"]["strength"],
            setup["volatility"]["phase"],
            trig["volatility"]["phase"],
            trig["candle"]["strength"],
            trig["candle"]["volume_state"],
        )

    objs = [_as_dict(l) for l in labels]
    if not objs:
        return {
            "schema_version": SCHEMA_VERSION,
            "symbol": "",
            "label_as_of": 0,
            "config_hash": None,
            "decision_digests": [],
            "n_decisions": 0,
            "windows": [
                {
                    "window": w, "samples": 0, "censored": 0,
                    "mean_mfe_r": None, "mean_mae_r": None,
                    "mean_mfe_pct": None, "mean_mae_pct": None,
                    "touches": {"positive_first": 0, "negative_first": 0,
                                "neither": 0, "ambiguous_same_bar": 0},
                    "fake_rate": None,
                }
                for w in WINDOWS
            ],
            "groups": [],
            "transitions": [],
        }
    symbols = {l["symbol"] for l in objs}
    if len(symbols) > 1:
        raise ValueError(f"汇总拒绝混合 symbol: {sorted(symbols)}")
    las_set = {l["label_as_of"] for l in objs}
    if len(las_set) > 1:
        raise ValueError(
            f"汇总拒绝混合 label_as_of（报告只能同一观察截止，不得取 max 冒充）: "
            f"{sorted(las_set)}"
        )
    digests = [l["decision_digest"] for l in objs]
    if len(set(digests)) != len(digests):
        raise ValueError("汇总拒绝重复 decision（相同 decision_digest 重复计入）")
    hashes = {(l["config_hash"]["market_state"], l["config_hash"]["fusion"])
              for l in objs}
    if len(hashes) > 1:
        raise ValueError("汇总拒绝混合 config_hash（必须同一 M1/M2 配置重放）")
    symbol = objs[0]["symbol"]
    label_as_of = objs[0]["label_as_of"]
    config_hash = objs[0]["config_hash"]
    n = len(objs)

    # ── 每窗口汇总 ─────────────────────────────────────────────────────────
    windows_out: list[dict[str, Any]] = []
    for w in WINDOWS:
        matured = [l for l in objs if _win(l, w)["matured"]]
        censored = n - len(matured)
        if matured:
            mfe_r = [float(_win(l, w)["mfe_r"]) for l in matured]
            mae_r = [float(_win(l, w)["mae_r"]) for l in matured]
            mfe_pct = [float(_win(l, w)["mfe_pct"]) for l in matured]
            mae_pct = [float(_win(l, w)["mae_pct"]) for l in matured]
            known = [l for l in matured if _win(l, w)["fake_breakout"] is not None]
            fake_rate = (
                sum(1 for l in known if _win(l, w)["fake_breakout"]) / len(known)
                if known else None
            )
            touches = {
                "positive_first": sum(1 for l in matured
                                      if _win(l, w)["first_touch"] == TOUCH_POSITIVE),
                "negative_first": sum(1 for l in matured
                                      if _win(l, w)["first_touch"] == TOUCH_NEGATIVE),
                "neither": sum(1 for l in matured
                               if _win(l, w)["first_touch"] == TOUCH_NEITHER),
                "ambiguous_same_bar": sum(1 for l in matured
                                          if _win(l, w)["first_touch"] == TOUCH_AMBIGUOUS),
            }
        else:
            mfe_r = mae_r = mfe_pct = mae_pct = []
            fake_rate = None
            touches = {"positive_first": 0, "negative_first": 0,
                       "neither": 0, "ambiguous_same_bar": 0}
        windows_out.append({
            "window": w,
            "samples": len(matured),
            "censored": censored,
            "mean_mfe_r": (sum(mfe_r) / len(mfe_r)) if mfe_r else None,
            "mean_mae_r": (sum(mae_r) / len(mae_r)) if mae_r else None,
            "mean_mfe_pct": (sum(mfe_pct) / len(mfe_pct)) if mfe_pct else None,
            "mean_mae_pct": (sum(mae_pct) / len(mae_pct)) if mae_pct else None,
            "touches": touches,
            "fake_rate": fake_rate,
        })

    # ── 三层条件分组（只聚合成熟样本，第一轮整改补齐维度）───────────────
    group_rows: list[dict[str, Any]] = []
    for w in WINDOWS:
        acc: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for l in objs:
            wo = _win(l, w)
            if not wo["matured"]:
                continue
            key = _group_key(l)
            acc.setdefault(key, []).append(wo)
        for key in sorted(acc):
            rows = acc[key]
            pos = sum(1 for r in rows if r["first_touch"] == TOUCH_POSITIVE)
            group_rows.append({
                "window": w,
                "direction": key[0],
                "event_type": key[1],
                "context_direction": key[2],
                "context_strength": key[3],
                "setup_direction": key[4],
                "setup_strength": key[5],
                "setup_vol_phase": key[6],
                "trigger_vol_phase": key[7],
                "trigger_candle_strength": key[8],
                "trigger_volume_state": key[9],
                "samples": len(rows),
                "positive_first": pos,
                "positive_first_rate": pos / len(rows),
                "mean_mfe_r": sum(float(r["mfe_r"]) for r in rows) / len(rows),
                "mean_mae_r": sum(float(r["mae_r"]) for r in rows) / len(rows),
            })

    # ── 状态迁移分组（事件态 → 终点态，逐窗口）────────────────────────────
    trans_rows: list[dict[str, Any]] = []
    for w in WINDOWS:
        acc: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for l in objs:
            wo = _win(l, w)
            if not wo["matured"]:
                continue
            es = l["event_state"]
            key = (es["vol_phase"], wo["end_vol_phase"],
                   es["trend_strength"], wo["end_trend_strength"])
            acc.setdefault(key, []).append(wo)
        for key in sorted(acc):
            rows = acc[key]
            pos = sum(1 for r in rows if r["first_touch"] == TOUCH_POSITIVE)
            trans_rows.append({
                "window": w,
                "event_vol_phase": key[0],
                "end_vol_phase": key[1],
                "event_trend_strength": key[2],
                "end_trend_strength": key[3],
                "samples": len(rows),
                "positive_first": pos,
                "positive_first_rate": pos / len(rows),
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "label_as_of": label_as_of,
        "config_hash": config_hash,
        "decision_digests": sorted(digests),
        "n_decisions": n,
        "windows": windows_out,
        "groups": group_rows,
        "transitions": trans_rows,
    }
