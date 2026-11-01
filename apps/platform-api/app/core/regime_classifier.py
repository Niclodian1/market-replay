"""
市场状态分类器 —— 3×2 矩阵 (趋势强度 × 波动水平)。

在信号检测之前运行，决定当前环境适合哪类策略。
配合 MA 排列做方向过滤。

Regime 编号:
  0: 震荡·低波  (Ranging, Low Vol)
  1: 震荡·高波  (Ranging, High Vol)
  2: 弱趋势·低波 (Weak Trend, Low Vol)
  3: 弱趋势·高波 (Weak Trend, High Vol)
  4: 强趋势·低波 (Strong Trend, Low Vol)
  5: 强趋势·高波 (Strong Trend, High Vol)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional



# ── 阈值 ──────────────────────────────────────────────────────────────────────

ADX_RANGING = 20       # < 20 → 震荡
ADX_STRONG = 35        # > 35 → 强趋势
ATR_PCT_SPLIT = 0.5    # < 0.5 → 低波


# ── 数据结构 ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Regime:
    """单根 K 线的市场状态快照。"""
    id: int               # 0-5
    trend: str            # "ranging" | "weak_trend" | "strong_trend"
    volatility: str       # "low" | "high"
    label: str            # 中文描述

    @property
    def is_trending(self) -> bool:
        return self.trend != "ranging"

    @property
    def is_ranging(self) -> bool:
        return self.trend == "ranging"

    @property
    def is_low_vol(self) -> bool:
        return self.volatility == "low"

    @property
    def is_strong_trend(self) -> bool:
        return self.trend == "strong_trend"


# ── 矩阵 ───────────────────────────────────────────────────────────────────────

_REGIME_MATRIX: dict[tuple[int, int], Regime] = {
    (0, 0): Regime(0, "ranging",       "low",  "震荡低波·死水"),
    (0, 1): Regime(1, "ranging",       "high", "震荡高波·双向扫损"),
    (1, 0): Regime(2, "weak_trend",    "low",  "弱趋势低波·安静爬坡"),
    (1, 1): Regime(3, "weak_trend",    "high", "弱趋势高波·暴风雨前夜"),
    (2, 0): Regime(4, "strong_trend",  "low",  "强趋势低波·最可靠"),
    (2, 1): Regime(5, "strong_trend",  "high", "强趋势高波·冲顶/恐慌"),
}


# ── 分类逻辑 ───────────────────────────────────────────────────────────────────

def _trend_level(adx: Optional[float]) -> int:
    if adx is None or adx < ADX_RANGING:
        return 0  # 震荡
    if adx < ADX_STRONG:
        return 1  # 弱趋势
    return 2      # 强趋势


def _vol_level(atr_pct: Optional[float]) -> int:
    if atr_pct is None or atr_pct < ATR_PCT_SPLIT:
        return 0  # 低波
    return 1      # 高波


def classify_regime_at(
    candles: list[dict],
    index: int,
    adx_period: int = 14,
    atr_period: int = 14,
    lookback: int = 100,
) -> Regime:
    """返回第 index 根 K 线时刻的市场状态（用于回测中给历史交易打标签）。

    M1：内部改用统一 MarketState 预计算（经 market_state_adapters），
    输出与原实现逐字段一致（由 legacy parity 测试锁定）。
    """
    if len(candles) < lookback or index < lookback:
        return Regime(-1, "unknown", "unknown", "数据不足")

    from app.core.market_state import MarketStateConfig, market_state_at
    from app.core.market_state_adapters import regime_components_from_state

    # 自定义周期参数必须透传给统一引擎（与旧公式一致）
    cfg = MarketStateConfig(
        adx_period=adx_period,
        atr_period=atr_period,
        vol_percentile_lookback=lookback,
    )
    ms = market_state_at(candles, index, cfg)
    components = regime_components_from_state(ms)
    if components is None:
        # 未收盘占位 → 数据不足（不可用）
        return Regime(-1, "unknown", "unknown", "数据不足")
    t, v = components
    return _REGIME_MATRIX[(t, v)]


def classify_current_regime(
    candles: list[dict],
    adx_period: int = 14,
    atr_period: int = 14,
    lookback: int = 100,
) -> Regime:
    """返回当前最后一根 K 线的市场状态。"""
    return classify_regime_at(candles, len(candles) - 1, adx_period, atr_period, lookback)


# ── 均线方向 ───────────────────────────────────────────────────────────────────

def alignment_direction(candles: list[dict], index: int | None = None) -> int:
    """
    返回当前均线排列方向: +2/+1=偏多, -2/-1=偏空, 0=缠绕。

    用于方向过滤: 偏多排列只做多，偏空排列只做空。
    M1：内部改用统一 MarketState 预计算。
    """
    from app.core.market_state import market_state_at
    from app.core.market_state_adapters import alignment_from_state

    idx = index if index is not None else len(candles) - 1
    if idx < 0 or idx >= len(candles):
        return 0
    ms = market_state_at(candles, idx)
    return alignment_from_state(ms)


# ── 策略-Regime 权限映射 ────────────────────────────────────────────────────────

# 每个策略类型在哪些 Regime 下被允许触发。
# "allow" = 正常仓位，"caution" = 降仓位(50%), "deny" = 不触发。
#
# 趋势交易: 弱趋势/强趋势 低波优先，震荡谨慎，高波降仓位
# 动能交易: 弱趋势/强趋势 低波最优，震荡不触发，高波降仓位
# 反转交易: 震荡低波主力，弱趋势高波可谨慎，强趋势禁用

TREND_ALLOWED   = {2, 3}       # 弱趋势低波 + 弱趋势高波（主力环境，数据确认）
TREND_CAUTION   = {0, 4, 5}    # 震荡低波(BTC 2笔全盈) + 强趋势(近末端风险)
# → Regime 0: 回测 BTC 2笔全盈+4.78%，开放尝试但降仓位
# → Regime 4: 回测 BTC 13笔-2.77%，从 allowed 降为 caution
# → Regime 5: 回测普遍负收益，维持 caution

MOMENTUM_ALLOWED  = {2, 3}     # 弱趋势（动能策略的天然环境）
MOMENTUM_CAUTION  = {0, 4}     # 震荡(可尝试) + 强趋势低波
MOMENTUM_DENIED   = {1, 5}     # 震荡高波 + 强趋势高波

REVERSAL_ALLOWED  = {0, 2}     # 震荡低波 + 弱趋势低波（反转甜区）
REVERSAL_CAUTION  = {1, 3, 4}  # 震荡高波 + 弱趋势高波 + 强趋势低波（可尝试）
REVERSAL_DENIED   = {5}        # 强趋势高波（唯一禁做——冲顶/恐慌时反转风险最高）
# → 放宽反转准入以增加样本量。前次回测仅7笔，样本不足无法评估。


def regime_permits(regime_id: int, strategy_type: str) -> tuple[bool, float]:
    """
    返回 (是否允许, 仓位系数)。

    仓位系数: 1.0 = 正常, 0.5 = 降仓位, 0.0 = 不触发。
    """
    maps = {
        "trend":    (TREND_ALLOWED, TREND_CAUTION),
        "momentum": (MOMENTUM_ALLOWED, MOMENTUM_CAUTION),
        "reversal": (REVERSAL_ALLOWED, REVERSAL_CAUTION),
    }
    allowed, caution = maps.get(strategy_type, (set(), set()))
    if regime_id in allowed:
        return True, 1.0
    if regime_id in caution:
        return True, 0.5
    return False, 0.0


# ── 方向过滤 ───────────────────────────────────────────────────────────────────

def direction_permitted(alignment: int, signal_direction: str) -> bool:
    """
    信号方向是否被均线排列允许。

    规则:
      - 排列 +1/+2 (偏多) → 只做多
      - 排列 -1/-2 (偏空) → 只做空
      - 排列 0 (缠绕) → 双向允许（市场不确定，都做）
    """
    if alignment == 0:
        return True  # 缠绕组双向
    if alignment >= 1 and signal_direction == "long":
        return True
    if alignment <= -1 and signal_direction == "short":
        return True
    return False


# ── 稳定检测 ───────────────────────────────────────────────────────────────────

def regime_is_stable(
    candles: list[dict],
    window: int = 5,
    required_ratio: float = 0.8,
) -> bool:
    """
    检查最近 window 根 K 线的 Regime 是否稳定（≥ required_ratio 落在同一格）。

    用于避免在市场状态切换边界频繁进出。
    """
    if len(candles) < window:
        return False

    # M1 整改：未收盘 cutoff 预检。序列中出现首个显式 is_closed=False 即数据未确认，
    # 当前稳定性必须为 False（不得让有效 regime 压过 -1，也不得让全 -1 窗口误判稳定）。
    # 字段缺省（无 is_closed 键）仍视为已收盘，全收盘与 warmup 旧行为不变。
    if any(not row.get("is_closed", True) for row in candles):
        return False

    regimes = [
        classify_regime_at(candles, i).id
        for i in range(len(candles) - window, len(candles))
    ]
    from collections import Counter
    dominant_count = Counter(regimes).most_common(1)[0][1]
    return dominant_count / window >= required_ratio


# ── 批量预计算（用于回测加速）────────────────────────────────────────────────

def precompute_regime_arrays(
    candles: list[dict],
    adx_period: int = 14,
    atr_period: int = 14,
    lookback: int = 100,
) -> tuple[list[int], list[int], list[bool]]:
    """
    一次性预计算所有 bar 的 regime ID、均线排列、是否降仓位。

    返回三条等长数组，回测循环中直接查表（O(1)），避免每 bar 重新跑 ADX。
    M1：内部改用统一 MarketState 预计算；参数保留以兼容旧签名，但阈值/周期
    由 MarketStateConfig（默认工程值）驱动，输出与原实现逐位一致。
    """
    from app.core.market_state import MarketStateConfig, precompute_market_states
    from app.core.market_state_adapters import alignment_from_state, regime_components_from_state

    # 自定义周期参数必须透传给统一引擎（与旧公式一致）
    cfg = MarketStateConfig(
        adx_period=adx_period,
        atr_period=atr_period,
        vol_percentile_lookback=lookback,
    )
    states = precompute_market_states(candles, cfg)
    n = len(candles)
    regime_ids = [-1] * n
    regime_alignments = [0] * n
    regime_cautions = [False] * n

    for i in range(n):
        components = regime_components_from_state(states[i])
        if components is not None:
            t, v = components
            r = _REGIME_MATRIX.get((t, v))
            if r is not None:
                regime_ids[i] = r.id
        # 未收盘占位 → 保持 -1（不可用）
        regime_alignments[i] = alignment_from_state(states[i])
        # caution 在回测时由策略类型决定，这里先不填

    return regime_ids, regime_alignments, regime_cautions
