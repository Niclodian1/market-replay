"""M3A 历史结果标注 —— 数据契约（schema_version=m3a-v1）。

仅作数据契约（对 outcome_labeling.label_decision(...).to_dict() 与
summarize(...) 输出做严格 Pydantic 校验），**不是** DB/API schema，
不接入生产 monitor，不持久化、不发送。

严格性（第一轮整改加固，暗卷全覆盖）：
  - 全模型 ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)；
  - **可验证审计**：decision_snapshot 用 M2 MarketFusionOut 重验证；
    decision_digest 用严格 canonical JSON 重算（无 default=str）；顶层
    symbol/as_of/signal_time/direction/reference/config_hash、三层 snapshots、
    event_state、risk 全与 decision_snapshot 一致；snapshot 三层
    role/timeframe 固定（context/1week、setup/1day、trigger/4h）且可用；
  - **风险口径**：risk.method='wilder_atr'、period=14；atr_abs 等于决策
    trigger features.atr；atr_pct == atr_abs/reference；
  - **标签语义**：reference == trigger features.close；mfe_pct≈mfe_r*atr_pct、
    mae 同理；path bar_time 从 signal_time+step 严格连续、长度==window、
    末步与 end_time/end_close/end 状态一致；
  - **汇总可信**：touch 四类之和==samples；fake_rate==negative_first/
    (samples-ambiguous)（分母 0 → null）；每窗口 group/transition samples
    总和==窗口 samples；positive_first_rate==positive_first/samples；
    行唯一且稳定（已排序）；samples==0 ⇒ 均值全 None。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.market_fusion import (
    AlignedPeriodOut,
    ConfigHashOut,
    MarketFusionOut,
)

SCHEMA_VERSION = "m3a-v1"
WINDOWS = (5, 10, 20, 40)
HEX16 = r"^[0-9a-f]{16}$"
FIXED_ROLE_TIMEFRAMES = {"context": "1week", "setup": "1day", "trigger": "4h"}

VolPhase = Literal["contraction", "transition", "expansion", "unknown"]
TrendStrength = Literal["ranging", "weak", "strong", "unknown"]
CandleStrength = Literal["weak", "normal", "strong", "unknown"]
FirstTouch = Literal["positive_first", "negative_first", "neither", "ambiguous_same_bar"]
MarketDirection = Literal["up", "down", "sideways", "unknown"]


def _close(a: float | None, b: float | None, rel: float = 1e-9,
           abs_: float = 1e-12) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= abs_ + rel * max(abs(a), abs(b))


def _canonical_digest(decision_dict: dict[str, Any]) -> str:
    """与 app.core.outcome_labeling.decision_digest 同源的严格 canonical digest
    （sort_keys + allow_nan=False，无 default=str）。"""
    payload = json.dumps(decision_dict, sort_keys=True, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class MarketStateConfigOut(BaseModel):
    """完整 M1 MarketStateConfig 字段快照（与 MarketStateConfig 一一对应）。

    第三轮收口：重算 hash 必须 == config_hash.market_state；atr_period 必须
    为 14（Wilder ATR(14) 冻结）。
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    schema_version: str = Field(pattern=r"^m1-v1$")
    adx_ranging: float
    adx_strong: float
    vol_percentile_low: float
    vol_percentile_high: float
    vol_percentile_lookback: int = Field(ge=1)
    vol_change_window: int = Field(ge=1)
    body_atr_threshold: float
    range_atr_threshold: float
    body_range_threshold: float
    close_location_threshold: float
    pivot: int = Field(ge=1)
    ma_fast: int = Field(ge=1)
    ma_slow: int = Field(ge=1)
    ma_tertiary: int = Field(ge=1)
    atr_period: Literal[14] = 14
    adx_period: int = Field(ge=1)


class ReplayBarOut(BaseModel):
    """事件时点 replay context 单根 bar（原始起点到事件 bar，time/OHLC/volume）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    time: int = Field(ge=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def _ohlc_ok(self) -> "ReplayBarOut":
        if not (self.high >= max(self.open, self.close)
                >= min(self.open, self.close) >= self.low):
            raise ValueError(
                f"replay bar time={self.time} OHLC 关系非法: "
                f"high={self.high} open={self.open} close={self.close} low={self.low}"
            )
        return self


class PathStepOut(BaseModel):
    """窗口内单根 bar 完整快照（含 OHLC 与 volume，供 schema 独立复算审计）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    bar_time: int = Field(ge=0)
    vol_phase: VolPhase
    trend_strength: TrendStrength
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def _ohlc_ok(self) -> "PathStepOut":
        if not (self.high >= max(self.open, self.close)
                >= min(self.open, self.close) >= self.low):
            raise ValueError(
                f"path 步 bar_time={self.bar_time} OHLC 关系非法: "
                f"high={self.high} open={self.open} close={self.close} low={self.low}"
            )
        return self


class WindowOutcomeOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    window: Literal[5, 10, 20, 40]
    matured: bool
    censor_code: Optional[Literal["label_as_of_not_reached", "unclosed", "missing_bars"]] = None
    censor_reason: Optional[str] = None
    mfe_r: Optional[float] = None
    mfe_pct: Optional[float] = None
    mae_r: Optional[float] = None
    mae_pct: Optional[float] = None
    first_touch: Optional[FirstTouch] = None
    fake_breakout: Optional[bool] = None
    end_vol_phase: Optional[VolPhase] = None
    end_trend_strength: Optional[TrendStrength] = None
    end_close: Optional[float] = None
    end_time: Optional[int] = None
    path: list[PathStepOut] = Field(default_factory=list)

    @model_validator(mode="after")
    def _cross_constraints(self) -> "WindowOutcomeOut":
        if self.matured:
            if self.censor_code is not None:
                raise ValueError("matured=True 时 censor_code 必须为 None")
            if self.censor_reason is not None:
                raise ValueError("matured=True 时 censor_reason 必须为 None")
            for f in ("mfe_r", "mae_r", "mfe_pct", "mae_pct"):
                v = getattr(self, f)
                if v is None:
                    raise ValueError(f"matured=True 时 {f} 必须非空")
                if v < 0:
                    raise ValueError(f"matured=True 时 {f} 必须非负（从 0 起）")
            if self.first_touch is None:
                raise ValueError("matured=True 时 first_touch 必须非空")
            if self.first_touch == "ambiguous_same_bar":
                if self.fake_breakout is not None:
                    raise ValueError(
                        "ambiguous_same_bar 时 fake_breakout 必须为 null"
                        "（不得假设 OHLC 内路径）"
                    )
            elif self.first_touch == "negative_first":
                if self.fake_breakout is not True:
                    raise ValueError("negative_first 时 fake_breakout 必须为 true")
            else:  # positive_first / neither
                if self.fake_breakout is not False:
                    raise ValueError(
                        "positive_first/neither 时 fake_breakout 必须为 false"
                    )
            if self.end_vol_phase is None or self.end_trend_strength is None:
                raise ValueError("matured=True 时 end_vol_phase/end_trend_strength 必须非空")
            if self.end_close is None or self.end_time is None:
                raise ValueError("matured=True 时 end_close/end_time 必须非空")
            if len(self.path) != self.window:
                raise ValueError(
                    f"matured=True 时 path 长度必须等于窗口 {self.window}"
                    f"（不含触发 bar），收到 {len(self.path)}"
                )
        else:
            if self.censor_code is None:
                raise ValueError("matured=False 时 censor_code 必须非空")
            if self.censor_reason is None or not self.censor_reason.strip():
                raise ValueError("matured=False 时 censor_reason 必须非空")
            for f in ("mfe_r", "mae_r", "mfe_pct", "mae_pct", "first_touch",
                      "fake_breakout", "end_vol_phase", "end_trend_strength",
                      "end_close", "end_time"):
                if getattr(self, f) is not None:
                    raise ValueError(f"matured=False 时 {f} 必须为 None")
            if self.path:
                raise ValueError("matured=False 时 path 必须为空")
        return self


class RiskOut(BaseModel):
    """风险口径（第一轮整改）：Wilder ATR(14)，来自决策快照 features.atr。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    method: Literal["wilder_atr"] = "wilder_atr"
    period: Literal[14] = 14
    atr_abs: float = Field(gt=0)
    atr_pct: float = Field(gt=0)


class EventStateOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    vol_phase: VolPhase
    trend_strength: TrendStrength


class SnapshotsOut(BaseModel):
    """三层条件快照（与 M2 AlignedPeriodOut 同构，含完整 market_state）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    context: AlignedPeriodOut
    setup: AlignedPeriodOut
    trigger: AlignedPeriodOut


class OutcomeLabelOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    schema_version: str = Field(pattern=r"^m3a-v1$")
    decision_digest: str = Field(pattern=HEX16)
    symbol: str
    as_of: int = Field(ge=0)
    label_as_of: int = Field(ge=0)
    signal_time: int = Field(ge=0)
    direction: Literal["long", "short"]
    reference_price: float = Field(gt=0)
    risk: RiskOut
    config_hash: ConfigHashOut
    event_state: EventStateOut
    snapshots: SnapshotsOut
    decision_snapshot: MarketFusionOut
    market_state_config: MarketStateConfigOut
    replay_context: list[ReplayBarOut]
    windows: list[WindowOutcomeOut]

    @model_validator(mode="after")
    def _semantic_checks(self) -> "OutcomeLabelOut":
        # 窗口集合/顺序恰好 [5,10,20,40]
        ws = [w.window for w in self.windows]
        if ws != list(WINDOWS):
            raise ValueError(
                f"windows 必须恰好按序为 {list(WINDOWS)}，收到 {ws}"
            )
        if self.label_as_of < self.as_of:
            raise ValueError(
                f"label_as_of({self.label_as_of}) 必须 >= 决策 as_of({self.as_of})"
            )
        snap = self.decision_snapshot
        # ── digest 重算（严格 canonical JSON，与引擎同源）─────────────────
        recomputed = _canonical_digest(snap.model_dump())
        if recomputed != self.decision_digest:
            raise ValueError(
                f"decision_digest 重算不匹配（{recomputed} != {self.decision_digest}，"
                "decision_snapshot 被篡改）"
            )
        # ── 顶层与 decision_snapshot 一致 ─────────────────────────────────
        if snap.symbol != self.symbol:
            raise ValueError(f"顶层 symbol({self.symbol}) 必须等于 decision_snapshot({snap.symbol})")
        if snap.as_of != self.as_of:
            raise ValueError(f"顶层 as_of({self.as_of}) 必须等于 decision_snapshot({snap.as_of})")
        if snap.trigger_event.signal_time != self.signal_time:
            raise ValueError("顶层 signal_time 必须等于 decision_snapshot.trigger_event.signal_time")
        if snap.direction != self.direction:
            raise ValueError(f"顶层 direction({self.direction}) 必须等于 decision_snapshot({snap.direction})")
        if not _close(snap.trigger_event.reference_price, self.reference_price):
            raise ValueError("顶层 reference_price 必须等于 decision_snapshot.trigger_event.reference_price")
        if snap.config_hash.market_state != self.config_hash.market_state:
            raise ValueError("config_hash.market_state 必须等于 decision_snapshot")
        if snap.config_hash.fusion != self.config_hash.fusion:
            raise ValueError("config_hash.fusion 必须等于 decision_snapshot")
        # ── 三层 snapshots 一致 + role/timeframe 固定 + 可用 ─────────────
        for role, tf in FIXED_ROLE_TIMEFRAMES.items():
            period = getattr(self.snapshots, role)
            sp = getattr(snap, role)
            if period.model_dump() != sp.model_dump():
                raise ValueError(f"snapshots.{role} 必须与 decision_snapshot.{role} 逐字段一致")
            if period.role != role or period.timeframe != tf:
                raise ValueError(
                    f"snapshots.{role} role/timeframe 必须固定为 {role}/{tf}，"
                    f"收到 {period.role}/{period.timeframe}"
                )
            if not period.available:
                raise ValueError(f"eligible 决策的 {role} 快照必须 available")
            if period.market_state is None or not period.market_state.ready \
                    or not period.market_state.is_closed:
                raise ValueError(
                    f"eligible 决策的 {role} 快照 market_state 必须 ready 且 is_closed"
                )
        # ── event_state / risk 与 decision_snapshot 一致 ──────────────────
        ms = snap.trigger.market_state
        if ms is None:
            raise ValueError("eligible 决策的 trigger 快照必须含 market_state")
        if self.event_state.vol_phase != ms.volatility.phase:
            raise ValueError("event_state.vol_phase 必须等于 decision_snapshot trigger 的 phase")
        if self.event_state.trend_strength != ms.trend.strength:
            raise ValueError("event_state.trend_strength 必须等于 decision_snapshot trigger 的 strength")
        if self.risk.method != "wilder_atr" or self.risk.period != 14:
            raise ValueError("risk.method/period 必须为 wilder_atr/14")
        if not _close(self.risk.atr_abs, ms.features.atr):
            raise ValueError("risk.atr_abs 必须等于 decision_snapshot trigger features.atr")
        if not _close(self.risk.atr_pct, self.risk.atr_abs / self.reference_price):
            raise ValueError("risk.atr_pct 必须等于 atr_abs / reference_price")
        # ── reference == trigger features.close ───────────────────────────
        if not _close(self.reference_price, ms.features.close):
            raise ValueError("reference_price 必须等于 trigger market_state features.close")
        # ── 每窗口语义（mfe/mae pct 等式、path 时序、end 一致性）──────────
        step = self.snapshots.trigger.bar_close_time - self.snapshots.trigger.bar_time
        if step is None or step <= 0:
            raise ValueError("snapshots.trigger 步长非法")
        # 第二轮整改：schema 从 path OHLC 独立复算每个成熟窗口的 MFE/MAE
        # 与首触及/fake（成套伪造也拒绝）
        entry = self.reference_price
        atr_abs = self.risk.atr_abs
        up_level = entry + atr_abs
        dn_level = entry - atr_abs
        for w in self.windows:
            if not w.matured:
                continue
            mfe_abs = 0.0
            mae_abs = 0.0
            first: str | None = None
            for p in w.path:
                h, l = p.high, p.low
                if self.direction == "long":
                    fav, unfav = h - entry, entry - l
                    tp = h >= up_level
                    tm = l <= dn_level
                else:  # short 镜像
                    fav, unfav = entry - l, h - entry
                    tp = l <= dn_level
                    tm = h >= up_level
                mfe_abs = max(mfe_abs, fav)
                mae_abs = max(mae_abs, unfav)
                if first is None:
                    if tp and tm:
                        first = "ambiguous_same_bar"
                    elif tp:
                        first = "positive_first"
                    elif tm:
                        first = "negative_first"
            mfe_abs = max(0.0, mfe_abs)
            mae_abs = max(0.0, mae_abs)
            if first is None:
                first = "neither"
            if not _close(w.mfe_r, mfe_abs / atr_abs):
                raise ValueError(f"窗口 {w.window}: mfe_r 与 path OHLC 复算不一致")
            if not _close(w.mae_r, mae_abs / atr_abs):
                raise ValueError(f"窗口 {w.window}: mae_r 与 path OHLC 复算不一致")
            if not _close(w.mfe_pct, mfe_abs / entry):
                raise ValueError(f"窗口 {w.window}: mfe_pct 与 path OHLC 复算不一致")
            if not _close(w.mae_pct, mae_abs / entry):
                raise ValueError(f"窗口 {w.window}: mae_pct 与 path OHLC 复算不一致")
            if w.first_touch != first:
                raise ValueError(
                    f"窗口 {w.window}: first_touch({w.first_touch}) 与 path OHLC 复算"
                    f"（{first}）不一致"
                )
            expected_fake = (None if first == "ambiguous_same_bar"
                             else first == "negative_first")
            if w.fake_breakout != expected_fake:
                raise ValueError(
                    f"窗口 {w.window}: fake_breakout({w.fake_breakout}) 与"
                    f" first_touch({first}) 不一致"
                )
            if not _close(w.mfe_pct, w.mfe_r * self.risk.atr_pct):
                raise ValueError(f"窗口 {w.window}: mfe_pct 必须 ≈ mfe_r * atr_pct")
            if not _close(w.mae_pct, w.mae_r * self.risk.atr_pct):
                raise ValueError(f"窗口 {w.window}: mae_pct 必须 ≈ mae_r * atr_pct")
            expected_times = [self.signal_time + k * step
                              for k in range(1, w.window + 1)]
            got_times = [p.bar_time for p in w.path]
            if got_times != expected_times:
                raise ValueError(
                    f"窗口 {w.window}: path bar_time 必须从 signal_time+step 严格"
                    f"连续（期望 {expected_times}，收到 {got_times}）"
                )
            if w.end_time != expected_times[-1]:
                raise ValueError(
                    f"窗口 {w.window}: end_time({w.end_time}) 必须等于窗口末根 "
                    f"bar_time({expected_times[-1]})"
                )
            last = w.path[-1]
            if not _close(w.end_close, last.close):
                raise ValueError(f"窗口 {w.window}: end_close 必须等于路径末步 close")
            if w.end_vol_phase != last.vol_phase or w.end_trend_strength != last.trend_strength:
                raise ValueError(f"窗口 {w.window}: end 状态必须等于路径末步状态")
        # ── 跨窗口约束（第三轮收口）───────────────────────────────────────
        matured_ws = [w.window for w in self.windows if w.matured]
        if matured_ws not in ([], [5], [5, 10], [5, 10, 20], [5, 10, 20, 40]):
            raise ValueError(
                f"成熟窗口必须是 [5,10,20,40] 的连续前缀，收到 {matured_ws}"
            )
        # ── 时序语义（最终审查）───────────────────────────────────────────
        # 窗口 w 第 w 根未来 bar 收盘 = signal_time + (w+1)*step；成熟要求
        # label_as_of >= 该边界；未成熟时 censor_code 与边界严格对应：
        # label_as_of_not_reached ⟺ label_as_of < 边界；边界已到只能
        # missing_bars/unclosed（schema 单凭标签无法区分后二者，文档说明）。
        for w in self.windows:
            boundary = self.signal_time + (w.window + 1) * step
            if w.matured:
                if self.label_as_of < boundary:
                    raise ValueError(
                        f"窗口 {w.window} 成熟但 label_as_of({self.label_as_of}) < "
                        f"signal_time+(window+1)*step（边界 {boundary}）"
                    )
            else:
                if w.censor_code == "label_as_of_not_reached":
                    if self.label_as_of >= boundary:
                        raise ValueError(
                            f"窗口 {w.window}: label_as_of({self.label_as_of}) 已到"
                            f"边界（{boundary}）时 censor_code 不得为 "
                            f"label_as_of_not_reached（只能 missing_bars/unclosed）"
                        )
                else:  # missing_bars / unclosed
                    if self.label_as_of < boundary:
                        raise ValueError(
                            f"窗口 {w.window}: label_as_of({self.label_as_of}) 未到"
                            f"边界（{boundary}）时 censor_code 只能为 "
                            f"label_as_of_not_reached，收到 {w.censor_code}"
                        )
        by_window = {w.window: w for w in self.windows}
        for sa, sb in ((5, 10), (10, 20), (20, 40)):
            wa, wb = by_window[sa], by_window[sb]
            if not wa.matured or not wb.matured:
                continue
            # 较短成熟 path 必须逐字段等于较长成熟 path 前缀
            if [p.model_dump() for p in wa.path] != [p.model_dump() for p in wb.path[:sa]]:
                raise ValueError(
                    f"窗口 {sa} 的 path 必须等于窗口 {sb} 的 path 前 {sa} 根"
                )
            # MFE/MAE 随窗口不减
            if wb.mfe_r < wa.mfe_r or wb.mae_r < wa.mae_r:
                raise ValueError(f"窗口 {sb} 的 MFE/MAE 不得小于窗口 {sa}")
            if wb.mfe_pct < wa.mfe_pct or wb.mae_pct < wa.mae_pct:
                raise ValueError(f"窗口 {sb} 的 MFE/MAE pct 不得小于窗口 {sa}")
            # 若短窗已触及（非 neither），长窗 first_touch/fake 不得改变
            if wa.first_touch != "neither":
                if wb.first_touch != wa.first_touch or wb.fake_breakout != wa.fake_breakout:
                    raise ValueError(
                        f"窗口 {sa} 已触及（{wa.first_touch}），窗口 {sb} 的 "
                        "first_touch/fake_breakout 不得改变"
                    )
        # ── 自包含可重放：M1 重放验证（第三轮收口）───────────────────────
        from app.core.market_state import MarketStateConfig, precompute_market_states
        cfg = MarketStateConfig(**self.market_state_config.model_dump())
        if cfg.atr_period != 14:
            raise ValueError("market_state_config.atr_period 必须为 14")
        if cfg.config_hash() != self.config_hash.market_state:
            raise ValueError("market_state_config 重算 hash 必须等于 config_hash.market_state")
        ctx = [b.model_dump() for b in self.replay_context]
        if not ctx:
            raise ValueError("replay_context 不能为空")
        if ctx[-1]["time"] != self.signal_time:
            raise ValueError("replay_context 末根 bar_time 必须等于 signal_time")
        if not _close(ctx[-1]["close"], self.reference_price):
            raise ValueError("replay_context 末根 close 必须等于 reference_price")
        for i in range(1, len(ctx)):
            if ctx[i]["time"] - ctx[i - 1]["time"] != step:
                raise ValueError("replay_context 必须严格连续（4H 步长）")
        # 事件末状态：重跑 precompute 的最后一根必须与决策 trigger market_state 一致
        event_states = precompute_market_states(list(ctx), cfg)
        event_last = event_states[-1]
        expected_trigger = self.decision_snapshot.trigger.market_state
        if event_last.to_dict() != expected_trigger.model_dump():
            raise ValueError(
                "replay_context 重放的事件末状态必须与决策 trigger market_state 一致"
            )
        # 每个成熟窗口：replay_context + path 重建 → 逐 bar 验证 M1 状态
        for w in self.windows:
            if not w.matured:
                continue
            window_bars = [
                {"time": p.bar_time, "open": p.open, "high": p.high,
                 "low": p.low, "close": p.close, "volume": p.volume}
                for p in w.path
            ]
            states = precompute_market_states(list(ctx) + window_bars, cfg)
            base = len(ctx)
            for k, p in enumerate(w.path):
                st = states[base + k]
                if st.vol_phase != p.vol_phase or st.trend_strength != p.trend_strength:
                    raise ValueError(
                        f"窗口 {w.window} 第 {k + 1} 根：path 的 vol_phase/"
                        f"trend_strength 与 M1 重放不一致"
                    )
        return self


class TouchCountsOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    positive_first: int = Field(ge=0)
    negative_first: int = Field(ge=0)
    neither: int = Field(ge=0)
    ambiguous_same_bar: int = Field(ge=0)


class SummaryWindowOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    window: Literal[5, 10, 20, 40]
    samples: int = Field(ge=0)
    censored: int = Field(ge=0)
    mean_mfe_r: Optional[float] = None
    mean_mae_r: Optional[float] = None
    mean_mfe_pct: Optional[float] = None
    mean_mae_pct: Optional[float] = None
    touches: TouchCountsOut
    fake_rate: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _cross(self) -> "SummaryWindowOut":
        if self.samples == 0:
            for f in ("mean_mfe_r", "mean_mae_r", "mean_mfe_pct", "mean_mae_pct"):
                if getattr(self, f) is not None:
                    raise ValueError(f"samples==0 时 {f} 必须为 None")
            t0 = self.touches
            if (t0.positive_first or t0.negative_first or t0.neither
                    or t0.ambiguous_same_bar):
                raise ValueError("samples==0 时 touch 四类计数必须全 0")
            if self.fake_rate is not None:
                raise ValueError("samples==0 时 fake_rate 必须为 None")
        else:
            for f in ("mean_mfe_r", "mean_mae_r", "mean_mfe_pct", "mean_mae_pct"):
                if getattr(self, f) is None:
                    raise ValueError(f"samples>0 时 {f} 必须非空")
            if self.mean_mfe_r < 0 or self.mean_mae_r < 0 \
                    or self.mean_mfe_pct < 0 or self.mean_mae_pct < 0:
                raise ValueError("均值必须非负")
        # touch 四类之和必须等于 samples（每成熟样本恰属一类）
        t = self.touches
        if t.positive_first + t.negative_first + t.neither + t.ambiguous_same_bar != self.samples:
            raise ValueError(
                "touch 四类计数之和必须等于 samples（每成熟样本恰属一类）"
            )
        # fake_rate == negative_first / (samples - ambiguous)（分母 0 → null）
        denom = self.samples - t.ambiguous_same_bar
        if self.fake_rate is None:
            if denom != 0:
                raise ValueError(
                    f"fake_rate 分母非零（{denom}）时不得为 null"
                )
        else:
            if denom == 0:
                raise ValueError("fake_rate 分母为 0（全 ambiguous/无样本）时必须为 null")
            if not _close(self.fake_rate, t.negative_first / denom, rel=1e-9):
                raise ValueError(
                    f"fake_rate({self.fake_rate}) 必须等于 negative_first("
                    f"{t.negative_first})/(samples-ambiguous)({denom})"
                )
        return self


class SummaryGroupOut(BaseModel):
    """三层条件分组（第一轮整改补齐维度：direction/event_type/Context/Setup/Trigger）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    window: Literal[5, 10, 20, 40]
    direction: Literal["long", "short"]
    event_type: str
    context_direction: MarketDirection
    context_strength: TrendStrength
    setup_direction: MarketDirection
    setup_strength: TrendStrength
    setup_vol_phase: VolPhase
    trigger_vol_phase: VolPhase
    trigger_candle_strength: CandleStrength
    trigger_volume_state: str
    samples: int = Field(ge=1)
    positive_first: int = Field(ge=0)
    positive_first_rate: float = Field(ge=0, le=1)
    mean_mfe_r: float = Field(ge=0)
    mean_mae_r: float = Field(ge=0)

    @model_validator(mode="after")
    def _cross(self) -> "SummaryGroupOut":
        if self.positive_first > self.samples:
            raise ValueError(
                f"positive_first({self.positive_first}) 不能大于 samples({self.samples})"
            )
        if not _close(self.positive_first_rate, self.positive_first / self.samples):
            raise ValueError(
                "positive_first_rate 必须等于 positive_first / samples"
            )
        return self


class SummaryTransitionOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    window: Literal[5, 10, 20, 40]
    event_vol_phase: VolPhase
    end_vol_phase: VolPhase
    event_trend_strength: TrendStrength
    end_trend_strength: TrendStrength
    samples: int = Field(ge=1)
    positive_first: int = Field(ge=0)
    positive_first_rate: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _cross(self) -> "SummaryTransitionOut":
        if self.positive_first > self.samples:
            raise ValueError(
                f"positive_first({self.positive_first}) 不能大于 samples({self.samples})"
            )
        if not _close(self.positive_first_rate, self.positive_first / self.samples):
            raise ValueError(
                "positive_first_rate 必须等于 positive_first / samples"
            )
        return self


class OutcomeSummaryOut(BaseModel):
    """汇总契约（第二轮整改：可审计元信息 + 稳定排序）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    schema_version: str = Field(pattern=r"^m3a-v1$")
    symbol: str
    label_as_of: int = Field(ge=0)
    config_hash: Optional[ConfigHashOut] = None  # 空报告为 None，否则必填
    decision_digests: list[str]
    n_decisions: int = Field(ge=0)
    windows: list[SummaryWindowOut]
    groups: list[SummaryGroupOut]
    transitions: list[SummaryTransitionOut]

    @model_validator(mode="after")
    def _semantic_checks(self) -> "OutcomeSummaryOut":
        ws = [w.window for w in self.windows]
        if ws != list(WINDOWS):
            raise ValueError(
                f"windows 必须恰好按序为 {list(WINDOWS)}，收到 {ws}"
            )
        for w in self.windows:
            if w.samples + w.censored != self.n_decisions:
                raise ValueError(
                    f"窗口 {w.window}: samples({w.samples}) + censored({w.censored})"
                    f" 必须等于 n_decisions({self.n_decisions})"
                )
        # ── 可审计元信息（第二轮整改）────────────────────────────────────
        if self.n_decisions == 0:
            if self.symbol != "":
                raise ValueError("n_decisions==0 时 symbol 必须为空串")
            if self.config_hash is not None:
                raise ValueError("n_decisions==0 时 config_hash 必须为 None（空报告）")
        else:
            if self.config_hash is None:
                raise ValueError("n_decisions>0 时 config_hash 必须非空")
        if len(self.decision_digests) != self.n_decisions:
            raise ValueError(
                f"decision_digests 数量({len(self.decision_digests)}) 必须等于 "
                f"n_decisions({self.n_decisions})"
            )
        if len(set(self.decision_digests)) != len(self.decision_digests):
            raise ValueError("decision_digests 必须唯一")
        if self.decision_digests != sorted(self.decision_digests):
            raise ValueError("decision_digests 必须稳定排序")
        for dd in self.decision_digests:
            if len(dd) != 16 or any(c not in "0123456789abcdef" for c in dd):
                raise ValueError(f"decision_digest 必须为 16 位小写 hex: {dd!r}")
        # ── 每窗口：group/transition samples 总和 == 窗口 samples ────────
        for w in self.windows:
            gw = [g for g in self.groups if g.window == w.window]
            tw = [t for t in self.transitions if t.window == w.window]
            if sum(g.samples for g in gw) != w.samples:
                raise ValueError(
                    f"窗口 {w.window}: groups samples 总和({sum(g.samples for g in gw)})"
                    f" 必须等于窗口 samples({w.samples})"
                )
            if sum(t.samples for t in tw) != w.samples:
                raise ValueError(
                    f"窗口 {w.window}: transitions samples 总和({sum(t.samples for t in tw)})"
                    f" 必须等于窗口 samples({w.samples})"
                )
        # ── 行唯一且严格排序（第二轮整改：倒序拒绝）────────────────────
        gkeys = [
            (g.window, g.direction, g.event_type, g.context_direction,
             g.context_strength, g.setup_direction, g.setup_strength,
             g.setup_vol_phase, g.trigger_vol_phase, g.trigger_candle_strength,
             g.trigger_volume_state)
            for g in self.groups
        ]
        if len(gkeys) != len(set(gkeys)):
            raise ValueError("groups 行必须唯一（同窗口同条件只能一行）")
        if gkeys != sorted(gkeys):
            raise ValueError("groups 必须按公开 key 严格排序（稳定排序契约）")
        tkeys = [
            (t.window, t.event_vol_phase, t.end_vol_phase,
             t.event_trend_strength, t.end_trend_strength)
            for t in self.transitions
        ]
        if len(tkeys) != len(set(tkeys)):
            raise ValueError("transitions 行必须唯一（同窗口同迁移只能一行）")
        if tkeys != sorted(tkeys):
            raise ValueError("transitions 必须按公开 key 严格排序（稳定排序契约）")
        return self


def validate_outcome_label(label_dict: dict[str, Any]) -> OutcomeLabelOut:
    """严格校验单事件结果标签（label_decision(...).to_dict() 必须通过）。"""
    return OutcomeLabelOut.model_validate(label_dict)


def validate_outcome_summary(summary_dict: dict[str, Any]) -> OutcomeSummaryOut:
    """严格校验汇总结果（summarize(...) 输出必须通过）。"""
    return OutcomeSummaryOut.model_validate(summary_dict)


def validate_outcome_summary_against_labels(
    summary_dict: dict[str, Any],
    labels: list[Any],
) -> OutcomeSummaryOut:
    """**source-linked validator**（第三轮收口）：summary 与源 labels 的审计入口。

    `validate_outcome_summary` 只承诺「严格结构与内部算术」，**无法**仅凭
    summary 自身证明源数据（如 group 均值被篡改、digests 被同长度替换）。
    本函数：
      1. 对源 labels 全部先严格校验（validate_outcome_label，伪造/NaN 拒绝）；
      2. 用 summarize(labels) 重新计算并**逐字段比较**；
      3. 不匹配 → ValueError（源数据审计失败）；
      4. 最后对 summary_dict 做结构校验。
    文档不得再暗示 summary schema 单独能证明来源——source-linked validator
    才是审计入口。

    局部导入 app.core.outcome_labeling 以避免循环依赖。
    """
    from app.core.outcome_labeling import summarize

    recomputed = summarize(labels)  # 内部对每个 label 先严格校验
    if recomputed != summary_dict:
        raise ValueError(
            "summary 与源 labels 重算不一致（源数据审计失败：group 均值/计数、"
            "digests 或 config_hash 被篡改）"
        )
    return validate_outcome_summary(summary_dict)
