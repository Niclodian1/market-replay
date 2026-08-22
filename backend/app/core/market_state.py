"""单周期 MarketState —— 统一、因果、可解释的市场状态模型（M1）。

目标：让回测、日报与后续多周期融合只认同一份市场状态。本模块是**纯计算**：
不读数据库、不取网络、不看未收盘或未来 K 线。

as-of 契约（继承 M0）：
  - 时刻 t 的状态只依赖 candles[:t+1]；
  - 中心摆动点只有经过右侧 pivot 根（idx + pivot <= t）后才确认可用；
  - `market_state_at(candles, index, config)` 等于
    `precompute_market_states(candles[:index+1], config)[-1]`。

构成：
  - trend：方向（已确认摆动结构 + MA20/MA60 方向）、强度（ADX 独立判定）；
  - volatility：ATR/price、历史百分位、5 根变化、level、phase
    （contraction/transition/expansion，同时看历史位置与变化方向）；
  - candle：方向、质量（body_atr/range_atr/body_range/双向 close_location）、
    成交量状态（复用 signals/evidence.py 口径）；
  - features/audit：实际特征值、阈值与判定原因，供兼容适配器与排查使用。

所有输出经 `to_dict()` 稳定、JSON 可序列化，不含 NaN/Infinity。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from app.core.indicators import _adx, _atr, _atr_percentile, _sma
from app.core.signals.evidence import volume_evidence


# ═══════════════════════════════════════════════════════════════════════════════
#  配置
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MarketStateConfig:
    """MarketState 阈值配置（M1 初始工程值，未经参数寻优）。"""

    schema_version: str = "m1-v1"
    # 趋势强度：ADX 阈值
    adx_ranging: float = 20.0
    adx_strong: float = 35.0
    # 波动：ATR/price 历史百分位阈值（level）与窗口
    vol_percentile_low: float = 0.30
    vol_percentile_high: float = 0.70
    vol_percentile_lookback: int = 100
    vol_change_window: int = 5
    # K 线质量四项阈值
    body_atr_threshold: float = 0.8
    range_atr_threshold: float = 1.0
    body_range_threshold: float = 0.6
    close_location_threshold: float = 0.75
    # 摆动结构窗口（趋势方向）
    pivot: int = 10
    # 均线
    ma_fast: int = 20
    ma_slow: int = 60
    ma_tertiary: int = 120
    # 指标周期
    atr_period: int = 14
    adx_period: int = 14

    def config_hash(self) -> str:
        """稳定序列化全部字段 → sha256 前 16 位（用于审计/回放校验）。"""
        payload = json.dumps(
            {k: v for k, v in self.__dict__.items() if not k.startswith("_")},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


DEFAULT_MARKET_STATE_CONFIG = MarketStateConfig()


# ═══════════════════════════════════════════════════════════════════════════════
#  结果模型
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MarketState:
    """单根已完成 bar 的市场状态快照（frozen，纯计算结果）。"""

    schema_version: str
    config_hash: str
    bar_index: int
    bar_time: int
    ready: bool
    missing_reason: str | None = None
    is_closed: bool = True  # False = 未收盘 bar（数据未确认，占位状态）

    # ── trend ──────────────────────────────────────────────────────────────
    trend_direction: str = "unknown"      # up / down / sideways / unknown
    trend_strength: str = "unknown"       # ranging / weak / strong / unknown
    swing_score: int = 0                  # 因果摆动结构分 -2/0/2
    ma_direction_score: int = 0           # MA20 vs MA60：+1/-1/0
    price_position_score: int = 0         # 近5根收盘 vs MA60：+1/-1/0
    adx: float | None = None
    trend_reason: str = ""

    # ── volatility ─────────────────────────────────────────────────────────
    atr_price: float | None = None        # ATR/close
    vol_percentile: float | None = None   # 历史百分位 0..1
    vol_change_5: float | None = None     # 5 根相对变化
    vol_level: str = "unknown"            # low / normal / high / unknown
    vol_phase: str = "unknown"            # contraction / transition / expansion / unknown
    vol_previous_phase: str = "unknown"
    vol_phase_age: int = 0
    vol_reason: str = ""

    # ── candle ─────────────────────────────────────────────────────────────
    candle_direction: str = "neutral"     # bullish / bearish / neutral
    candle_strength: str = "unknown"      # weak / normal / strong / unknown
    body_atr: float | None = None
    range_atr: float | None = None
    body_range: float | None = None
    close_location_long: float | None = None   # (close-low)/range
    close_location_short: float | None = None  # (high-close)/range
    volume_state: str = "unknown"
    candle_reason: str = ""

    # ── features（特征值，供适配器复算旧口径）───────────────────────────────
    close: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    atr: float | None = None
    # 前 2 根均线值（日报 slope 判定：ma[t]-ma[t-2]）
    ma20_prev2: float | None = None
    ma60_prev2: float | None = None
    ma120_prev2: float | None = None

    # ── audit ──────────────────────────────────────────────────────────────
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """稳定、JSON 可序列化的字典；NaN/Infinity 一律降级为 None。"""
        def safe(value: float | None) -> float | None:
            if value is None:
                return None
            try:
                return value if math.isfinite(float(value)) else None
            except (TypeError, ValueError):
                return None

        return {
            "schema_version": self.schema_version,
            "config_hash": self.config_hash,
            "bar_index": self.bar_index,
            "bar_time": self.bar_time,
            "ready": self.ready,
            "missing_reason": self.missing_reason,
            "is_closed": self.is_closed,
            "trend": {
                "direction": self.trend_direction,
                "strength": self.trend_strength,
                "swing_score": self.swing_score,
                "ma_direction_score": self.ma_direction_score,
                "price_position_score": self.price_position_score,
                "adx": safe(self.adx),
                "reason": self.trend_reason,
            },
            "volatility": {
                "atr_price": safe(self.atr_price),
                "percentile": safe(self.vol_percentile),
                "change_5": safe(self.vol_change_5),
                "level": self.vol_level,
                "phase": self.vol_phase,
                "previous_phase": self.vol_previous_phase,
                "phase_age": self.vol_phase_age,
                "reason": self.vol_reason,
            },
            "candle": {
                "direction": self.candle_direction,
                "strength": self.candle_strength,
                "body_atr": safe(self.body_atr),
                "range_atr": safe(self.range_atr),
                "body_range": safe(self.body_range),
                "close_location_long": safe(self.close_location_long),
                "close_location_short": safe(self.close_location_short),
                "volume_state": self.volume_state,
                "reason": self.candle_reason,
            },
            "features": {
                "close": safe(self.close),
                "ma20": safe(self.ma20),
                "ma60": safe(self.ma60),
                "ma120": safe(self.ma120),
                "atr": safe(self.atr),
                "ma20_prev2": safe(self.ma20_prev2),
                "ma60_prev2": safe(self.ma60_prev2),
                "ma120_prev2": safe(self.ma120_prev2),
            },
            "reasons": list(self.reasons),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  摆动结构（因果确认）——与 trend_classifier 同源逻辑，但本模块独立实现，
#  避免 core 模块间循环依赖。
# ═══════════════════════════════════════════════════════════════════════════════

def swing_points(closes: list[float], pivot: int = 10) -> tuple[set[int], set[int]]:
    """找摆动高点和摆动低点（基于收盘价，窗口 [i-pivot, i+pivot]）。"""
    n = len(closes)
    highs: set[int] = set()
    lows: set[int] = set()
    for i in range(pivot, n - pivot):
        window = closes[i - pivot:i + pivot + 1]
        if closes[i] == max(window):
            highs.add(i)
        if closes[i] == min(window):
            lows.add(i)
    return highs, lows


def swing_score_at(
    closes: list[float],
    sw_highs: set[int],
    sw_lows: set[int],
    index: int,
    pivot: int = 10,
) -> int:
    """第 index 根 bar 的因果摆动结构分：HH+HL→2，LH+LL→-2，其余→0。

    只用已确认摆动点（idx + pivot <= index），不让未来 pivot 改写历史。
    """
    confirmed_highs = sorted(h for h in sw_highs if h + pivot <= index)
    confirmed_lows = sorted(l for l in sw_lows if l + pivot <= index)
    if len(confirmed_highs) < 2 or len(confirmed_lows) < 2:
        return 0
    h1, h2 = confirmed_highs[-2], confirmed_highs[-1]
    l1, l2 = confirmed_lows[-2], confirmed_lows[-1]
    hh = closes[h2] > closes[h1]
    hl = closes[l2] > closes[l1]
    lh = closes[h2] < closes[h1]
    ll = closes[l2] < closes[l1]
    if hh and hl:
        return 2
    if lh and ll:
        return -2
    return 0


def ma_direction_score_at(
    ma20: list[float | None], ma60: list[float | None], index: int, ma_slow: int = 60,
) -> int:
    """MA20 vs MA60 方向分：>→+1，<→-1，=→0；历史不足→0（与旧 trend 口径一致）。"""
    if index < ma_slow:
        return 0
    v20, v60 = ma20[index], ma60[index]
    if v20 is None or v60 is None:
        return 0
    if v20 > v60:
        return 1
    if v20 < v60:
        return -1
    return 0


def price_position_score_at(
    closes: list[float],
    ma60: list[float | None],
    index: int,
    window: int = 5,
    ma_slow: int = 60,
) -> int:
    """近 window 根收盘全在 MA60 上→+1，全在下→-1，否则 0；历史不足→0。"""
    if index < window or index < ma_slow:
        return 0
    rows = [closes[index - j] for j in range(window)]
    mas = [ma60[index - j] for j in range(window)]
    if any(v is None for v in mas):
        return 0
    if all(r > m for r, m in zip(rows, mas)):  # type: ignore[operator]
        return 1
    if all(r < m for r, m in zip(rows, mas)):  # type: ignore[operator]
        return -1
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
#  主计算
# ═══════════════════════════════════════════════════════════════════════════════

# 波动百分位就绪所需的最小根数（与 vol_percentile_lookback 一致）
VOL_WARMUP_OFFSET = 1  # _atr_percentile 从 index == lookback 起有值


def precompute_market_states(
    candles: list[dict],
    config: MarketStateConfig = DEFAULT_MARKET_STATE_CONFIG,
    volume_applicable: bool = True,
) -> list[MarketState]:
    """对全部 bars 预计算一次 MarketState，返回等长数组。

    所有指标均因果：只依赖 candles[:i+1]。摆动点在全序列上预计算，
    但每根 bar 只使用已确认（idx+pivot<=i）的摆动点。

    未收盘保护：从第一个 `is_closed=False` 的 bar 起，不再消费价格数据，
    该 bar 及其后全部输出 `ready=False` 的占位状态（缺失原因含「未收盘」）。
    """
    n = len(candles)
    if n == 0:
        return []
    # 未收盘 cutoff：第一个未收盘 bar 的索引（默认全部已收盘）。
    # 只消费 candles[:cutoff] 的 OHLC——cutoff 之后的 bar 可能只有 time/is_closed。
    cutoff = n
    for i in range(n):
        if not candles[i].get("is_closed", True):
            cutoff = i
            break
    closed_candles = candles[:cutoff]
    closes = [float(c["close"]) for c in closed_candles]
    highs = [float(c["high"]) for c in closed_candles]
    lows = [float(c["low"]) for c in closed_candles]
    times = [int(c["time"]) for c in closed_candles]
    volumes = [float(c.get("volume") or 0.0) for c in closed_candles]

    ma_fast = int(config.ma_fast)
    ma_slow = int(config.ma_slow)
    ma_tertiary = int(config.ma_tertiary)
    atr_period = int(config.atr_period)
    adx_period = int(config.adx_period)
    pivot = int(config.pivot)
    lookback = int(config.vol_percentile_lookback)
    vol_window = int(config.vol_change_window)

    ma20 = _sma(closes, ma_fast)
    ma60 = _sma(closes, ma_slow)
    ma120 = _sma(closes, ma_tertiary)
    atr = _atr(highs, lows, closes, atr_period)
    adx = _adx(highs, lows, closes, adx_period)
    atr_pct = _atr_percentile(highs, lows, closes, atr_period, lookback)
    sw_highs, sw_lows = swing_points(closes, pivot)

    cfg_hash = config.config_hash()
    states: list[MarketState] = []
    prev_phase: str | None = None
    prev_phase_age = 0

    for i in range(cutoff):
        reasons: list[str] = []

        # ── trend ──────────────────────────────────────────────────────────
        score = swing_score_at(closes, sw_highs, sw_lows, i, pivot)
        ma_dir = ma_direction_score_at(ma20, ma60, i, ma_slow)
        price_pos = price_position_score_at(closes, ma60, i, ma_slow=ma_slow)
        adx_val = adx[i]
        if ma60[i] is None:
            direction = "unknown"
        else:
            vote = score // 2 + ma_dir
            direction = "up" if vote > 0 else "down" if vote < 0 else "sideways"
        if adx_val is None:
            strength = "unknown"
        elif adx_val < config.adx_ranging:
            strength = "ranging"
        elif adx_val < config.adx_strong:
            strength = "weak"
        else:
            strength = "strong"
        trend_reason = (
            f"swing={score}, ma_dir={ma_dir}, price_pos={price_pos}, "
            f"adx={_fmt(adx_val)} (<{_fmt(config.adx_ranging)} ranging, "
            f"<{_fmt(config.adx_strong)} weak) → direction={direction}, strength={strength}"
        )
        reasons.append(f"trend: {trend_reason}")

        # ── volatility ─────────────────────────────────────────────────────
        atr_now = atr[i]
        close_now = closes[i]
        atr_price = (atr_now / close_now) if (atr_now is not None and close_now > 0) else None
        pct = atr_pct[i] if i < len(atr_pct) else None
        change5: float | None = None
        if i >= vol_window:
            prev5 = None
            # 用 atr/close 序列重算前 5 根（避免重建窗口）
            prev_atr = atr[i - vol_window]
            prev_close = closes[i - vol_window]
            if prev_atr is not None and prev_close > 0:
                prev5 = prev_atr / prev_close
            if atr_price is not None and prev5 is not None and prev5 > 0:
                change5 = (atr_price - prev5) / prev5
        if pct is None:
            level = "unknown"
            phase = "unknown"
        elif pct < config.vol_percentile_low:
            level = "low"
            if change5 is None or change5 <= 0:
                phase = "contraction"
            else:
                phase = "transition"
        elif pct > config.vol_percentile_high:
            level = "high"
            if change5 is None or change5 >= 0:
                phase = "expansion"
            else:
                phase = "transition"
        else:
            level = "normal"
            phase = "transition"
        # previous_phase / phase_age（序列化维护）
        if phase == "unknown":
            phase_age = 0
        elif phase == prev_phase:
            phase_age = prev_phase_age + 1
        else:
            phase_age = 1
        previous_phase = prev_phase if prev_phase is not None else "unknown"
        if phase != "unknown":
            prev_phase = phase
            prev_phase_age = phase_age
        vol_reason = (
            f"atr_price={_fmt(atr_price)}, percentile={_fmt(pct)} "
            f"(low<{config.vol_percentile_low}, high>{config.vol_percentile_high}), "
            f"change_5={_fmt(change5)} → level={level}, phase={phase}"
        )
        reasons.append(f"volatility: {vol_reason}")

        # ── candle ─────────────────────────────────────────────────────────
        o = float(candles[i]["open"])
        h = highs[i]
        l = lows[i]
        c = closes[i]
        body = abs(c - o)
        rng = h - l
        body_atr = (body / atr_now) if (atr_now is not None and atr_now > 0) else None
        range_atr = (rng / atr_now) if (atr_now is not None and atr_now > 0) else None
        body_range = (body / rng) if rng > 0 else None
        cl_long = ((c - l) / rng) if rng > 0 else None
        cl_short = ((h - c) / rng) if rng > 0 else None
        if c > o:
            candle_dir = "bullish"
        elif c < o:
            candle_dir = "bearish"
        else:
            candle_dir = "neutral"
        # 四项质量条件
        conds = [
            body_atr is not None and body_atr >= config.body_atr_threshold,
            range_atr is not None and range_atr >= config.range_atr_threshold,
            body_range is not None and body_range >= config.body_range_threshold,
            (cl_long is not None and cl_long >= config.close_location_threshold)
            if candle_dir == "bullish"
            else (cl_short is not None and cl_short >= config.close_location_threshold)
            if candle_dir == "bearish"
            else False,
        ]
        cond_count = sum(1 for x in conds if x)
        if atr_now is None:
            candle_strength = "unknown"
        elif cond_count >= 3:
            candle_strength = "strong"
        elif cond_count <= 1:
            candle_strength = "weak"
        else:
            candle_strength = "normal"
        # 成交量（复用 evidence.py 口径；缺失/为零/不适用 → unknown）
        if not volume_applicable or volumes[i] <= 0:
            volume_state = "unknown"
        else:
            volume_state = volume_evidence(candles, i, windows=(5, 10)).get("strength", "unknown")
        candle_reason = (
            f"body_atr={_fmt(body_atr)} (>= {config.body_atr_threshold}), "
            f"range_atr={_fmt(range_atr)} (>= {config.range_atr_threshold}), "
            f"body_range={_fmt(body_range)} (>= {config.body_range_threshold}), "
            f"close_location={'long' if candle_dir == 'bullish' else 'short' if candle_dir == 'bearish' else '-'}"
            f"={_fmt(cl_long if candle_dir == 'bullish' else cl_short)} "
            f"(>= {config.close_location_threshold}) → {candle_strength} ({cond_count}/4)"
        )
        reasons.append(f"candle: {candle_reason}")
        reasons.append(f"volume_state={volume_state}")

        # ── ready ──────────────────────────────────────────────────────────
        ready = (
            direction != "unknown"
            and strength != "unknown"
            and level != "unknown"
            and phase != "unknown"
            and candle_strength != "unknown"
        )
        missing_reason = None if ready else (
            "历史不足（需波动百分位窗口与趋势/强度指标就绪）"
        )

        states.append(MarketState(
            schema_version=config.schema_version,
            config_hash=cfg_hash,
            bar_index=i,
            bar_time=times[i],
            ready=ready,
            missing_reason=missing_reason,
            trend_direction=direction,
            trend_strength=strength,
            swing_score=score,
            ma_direction_score=ma_dir,
            price_position_score=price_pos,
            adx=adx_val,
            trend_reason=trend_reason,
            atr_price=atr_price,
            vol_percentile=pct,
            vol_change_5=change5,
            vol_level=level,
            vol_phase=phase,
            vol_previous_phase=previous_phase,
            vol_phase_age=phase_age,
            vol_reason=vol_reason,
            candle_direction=candle_dir,
            candle_strength=candle_strength,
            body_atr=body_atr,
            range_atr=range_atr,
            body_range=body_range,
            close_location_long=cl_long,
            close_location_short=cl_short,
            volume_state=volume_state,
            candle_reason=candle_reason,
            close=close_now,
            ma20=ma20[i],
            ma60=ma60[i],
            ma120=ma120[i],
            atr=atr_now,
            ma20_prev2=ma20[i - 2] if i >= 2 else None,
            ma60_prev2=ma60[i - 2] if i >= 2 else None,
            ma120_prev2=ma120[i - 2] if i >= 2 else None,
            reasons=tuple(reasons),
        ))
    # 未收盘（或其后的数据链含未收盘 bar）：不消费价格数据，输出占位状态
    for i in range(cutoff, n):
        states.append(MarketState(
            schema_version=config.schema_version,
            config_hash=cfg_hash,
            bar_index=i,
            bar_time=int(candles[i]["time"]),
            ready=False,
            missing_reason=f"bar 未收盘（is_closed=False，index={i}）",
            is_closed=False,
        ))
    return states


def market_state_at(
    candles: list[dict],
    index: int,
    config: MarketStateConfig = DEFAULT_MARKET_STATE_CONFIG,
    volume_applicable: bool = True,
) -> MarketState:
    """第 index 根 bar 的 MarketState。

    保证等于 `precompute_market_states(candles[:index+1], config)[-1]`。
    """
    if index < 0 or index >= len(candles):
        raise IndexError(f"market_state_at index out of range: {index}")
    states = precompute_market_states(candles[:index + 1], config, volume_applicable)
    return states[-1]


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"
