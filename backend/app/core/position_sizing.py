"""
仓位管理 —— 生存层。

- 基于风险预算反推仓位（止损距离 → 仓位大小）
- 敞口分配（多信号按优先级排序，依次占用可用敞口）
- 杠杆控制

核心公式:
  仓位 = risk_amount / (|entry - stop| / entry)
  实际 = min(仓位 × leverage, 剩余敞口)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── 全局参数 ───────────────────────────────────────────────────────────────────

@dataclass
class AccountConfig:
    """账户级配置。"""
    initial_capital: float = 1000.0
    max_leverage: float = 2.0         # 最大杠杆倍数
    max_total_exposure: float = 2000.0  # 最大总敞口 ($)

    @property
    def max_levered_capital(self) -> float:
        return self.initial_capital * self.max_leverage


# ── 策略风险预算 ───────────────────────────────────────────────────────────────

# 每类策略的单笔风险预算（占初始资金百分比）
STRATEGY_RISK_BUDGET: dict[str, float] = {
    "trend":    2.0,   # 趋势交易: 2% = $20
    "momentum": 1.5,   # 动能交易: 1.5% = $15
    "reversal": 1.0,   # 反转交易: 1% = $10
}

# 仓位系数: regime_permits 返回的 caution 系数与策略风险预算叠加
# caution 时仓位 = 风险预算 × 0.5


# ── 数据结构 ───────────────────────────────────────────────────────────────────

@dataclass
class SignalOrder:
    """待执行的交易信号，含计算后的仓位。"""
    strategy_type: str        # "trend" | "momentum" | "reversal"
    symbol: str
    direction: str            # "long" | "short"
    entry_price: float
    stop_loss: float
    timeframe: str
    priority: int             # 1=趋势 > 2=动能 > 3=反转
    regime_id: int
    regime_caution: bool      # regime 降仓位标志
    risk_budget_pct: float = 0.0
    position_size: float = 0.0
    risk_amount: float = 0.0
    filled: bool = False
    reason: str = ""


@dataclass
class AllocationResult:
    """敞口分配结果。"""
    orders: list[SignalOrder] = field(default_factory=list)
    total_exposure_used: float = 0.0
    total_exposure_available: float = 2000.0
    total_risk: float = 0.0      # 总计风险金额


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def compute_position_size(
    account: float,
    risk_budget_pct: float,
    entry_price: float,
    stop_loss: float,
    leverage: float,
    regime_caution: bool = False,
) -> tuple[float, float]:
    """
    基于止损距离反推仓位。

    Args:
        account: 当前账户资金
        risk_budget_pct: 单笔风险预算百分比 (如 2.0 表示 2%)
        entry_price: 入场价格
        stop_loss: 止损价格
        leverage: 杠杆倍数
        regime_caution: 是否为 regime 降仓位模式

    Returns:
        (仓位大小, 实际风险金额)
    """
    if entry_price <= 0 or stop_loss <= 0:
        return 0.0, 0.0

    # Regime 降仓位 → 风险预算减半
    effective_risk_pct = risk_budget_pct * (0.5 if regime_caution else 1.0)
    risk_amount = account * (effective_risk_pct / 100.0)

    # 价格风险 = |入场 - 止损| / 入场
    price_risk_pct = abs(entry_price - stop_loss) / entry_price
    if price_risk_pct <= 0:
        return 0.0, 0.0

    # 无杠杆仓位 = 风险金额 / 价格风险百分比
    unlevered_position = risk_amount / price_risk_pct

    # 加杠杆
    levered_position = unlevered_position * leverage

    # 实际风险 = levered_position * price_risk_pct（应 ≈ risk_amount）
    actual_risk = levered_position * price_risk_pct

    return round(levered_position, 2), round(actual_risk, 2)


PRIORITY_MAP = {"trend": 1, "momentum": 2, "reversal": 3}


def allocate_exposure(
    signals: list[SignalOrder],
    account: AccountConfig,
) -> AllocationResult:
    """
    按优先级分配敞口。

    流程:
      1. 按 priority 升序排列（趋势 > 动能 > 反转）
      2. 同 priority 按 risk_pct（实际亏损百分比）升序排列
      3. 依次分配，敞口用完为止
      4. 单笔不超过总敞口上限
    """
    # 排序: 先按优先级，再按风险百分比
    sorted_signals = sorted(
        signals,
        key=lambda s: (s.priority, abs(s.entry_price - s.stop_loss) / s.entry_price),
    )

    available = account.max_total_exposure
    result = AllocationResult(
        total_exposure_available=available,
    )

    for sig in sorted_signals:
        if available <= 0:
            sig.reason = "敞口已用完"
            result.orders.append(sig)
            continue

        # 计算仓位
        risk_budget = STRATEGY_RISK_BUDGET.get(sig.strategy_type, 1.5)
        position, risk_amt = compute_position_size(
            account=account.initial_capital,
            risk_budget_pct=risk_budget,
            entry_price=sig.entry_price,
            stop_loss=sig.stop_loss,
            leverage=account.max_leverage,
            regime_caution=sig.regime_caution,
        )

        if position <= 0:
            sig.reason = "无法计算有效仓位"
            result.orders.append(sig)
            continue

        # 受可用敞口约束
        allocated = min(position, available)
        sig.position_size = allocated
        sig.risk_budget_pct = risk_budget
        sig.risk_amount = risk_amt * (allocated / position) if position > 0 else 0
        sig.filled = True

        available -= allocated
        result.total_exposure_used += allocated
        result.total_risk += sig.risk_amount
        result.orders.append(sig)

    return result


def build_signal_order(
    strategy_type: str,
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    timeframe: str,
    regime_id: int,
    regime_caution: bool = False,
) -> SignalOrder:
    """从策略信号构建待分配订单。"""
    return SignalOrder(
        strategy_type=strategy_type,
        symbol=symbol,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        timeframe=timeframe,
        priority=PRIORITY_MAP.get(strategy_type, 99),
        regime_id=regime_id,
        regime_caution=regime_caution,
    )


# ── 出场规则 ───────────────────────────────────────────────────────────────────

def trend_exit_rules() -> dict:
    """趋势交易出场规则。"""
    return {
        "initial_stop": "入场K线最低点(多) / 最高点(空)",
        "breakeven_trigger_pct": 3.0,      # 浮盈 > 3% 触发保本
        "trail_ma_period": 60,              # 追踪 MA
        "trail_consecutive_bars": 2,        # 连续 N 根收盘破位才出场
        "reverse_structure": True,           # 反向结构突破 → 出场
    }


def momentum_exit_rules() -> dict:
    """动能交易出场规则。"""
    return {
        "initial_stop": "入场K线反向极端",
        "breakeven_trigger_pct": 2.0,
        "exhaustion_atr_peak_ratio": 0.6,   # ATR 缩到峰值的 60%
        "trail_ma_period": 20,
    }


def reversal_exit_rules() -> dict:
    """反转交易出场规则。"""
    return {
        "initial_stop_buffer_atr": 0.5,      # 破位极值外 + 0.5 ATR
        "target_1_ma": 60,                   # 目标 1: 回到 MA60 → 平 50%
        "target_2_structure": True,           # 目标 2: 结构阻力位 → 全平
        "time_stop_bars": 15,                # 15 根 K 线未达目标 1 → 全平
        "partial_exit_pct": 50.0,             # 第一次止盈比例
    }


EXIT_RULES = {
    "trend": trend_exit_rules(),
    "momentum": momentum_exit_rules(),
    "reversal": reversal_exit_rules(),
}
