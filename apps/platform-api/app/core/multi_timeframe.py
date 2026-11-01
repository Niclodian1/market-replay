"""M2 多周期角色融合 —— 纯计算、可回放、不触碰 V1（2026-08-13，第二轮复验整改）。

目标（M2 任务书）：
  周线定 Context（大环境方向）→ 日线定 Setup（候选结构）→ 4H 给 Trigger（入场时机）；
  同一 as_of 下，任何到达顺序 / 未来追加都产生完全一致的融合结果；
  只用已收盘数据；输出可解释、可回放的影子决策，不改变 V1 任何行为。

时间契约（领导拍板，首版只服务 crypto UTC）：
  - bar.time 是开盘秒（epoch）；close_time = time + duration
    （1week=604800，1day=86400，4h=14400）。
  - 每周期先按 time 稳定排序；仅消费「close_time <= as_of 且未显式
    is_closed=False」的连续前缀。首个未收盘或 close_time>as_of 的 bar 即
    cutoff（其 OHLC 可缺，**不读取**），之后全部不可见；过滤先于任何 OHLC 读取。
  - close_time == as_of 可用；as_of-1 秒不可用。

配置（第二轮复验整改）：
  - roles 是 M2 首版**固定业务法则**，不是可配置项：运行统一使用
    ROLE_TIMEFRAMES 固定映射（context→1week、setup→1day、trigger→4h），
    MultiTimeframeConfig 不暴露 roles 字段（传入即 ValueError）；
  - durations 只允许恰好 1week/1day/4h 三键，值（正整数秒）可配置；
  - schema_version 固定 m2-v1 不可任意输入；
  - direction_map 必须恰有 up/down，值 ∈ {long, short}；
  - buy_sell_map 键规范化为非空小写（重复归一键拒绝），值 ∈ {long, short}；
  - **深冻结**：构造后调用者原 dict 变化或 cfg 字段赋值都不能改变
    hash/行为（只读映射 + config_hash 显式序列化真实字段）。

事件契约（第二轮复验整改）：
  - M2 输出**不携带原始 payload**（摘要字段已足够审计，避免未验证对象污染）；
  - 事件必须证明来自 4H：顶层与 payload.timeframe 一致且等于 Trigger 周期；
  - signal_time 必须为合法整数秒且等于对齐 bar_time；
  - 方向 long/buy→long、short/sell→short；payload.direction 存在必须可归一
    且等于顶层；bar_closed 存在必须严格为 bool True；
  - reference_price 必须为有限正数且与对齐 4H MarketState.close 一致
    （极小容差；M3 结果标注可信的前提）；
  - event_type 非空；payload.signal_type 存在必须等于顶层 event_type；
  - occurred_at/confirmed_at 存在必须等于 signal_time；
  - 任何不可信字段受控拒绝/降级，to_dict 永远可 json.dumps(allow_nan=False)。

纯函数：无网络 / 无数据库 / 无时钟读取。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from app.core.market_state import (
    DEFAULT_MARKET_STATE_CONFIG,
    MarketState,
    MarketStateConfig,
    precompute_market_states,
)

SCHEMA_VERSION = "m2-v1"

# 默认时长（秒）：close_time = time + duration。crypto UTC。
TIMEFRAME_DURATIONS: dict[str, int] = {
    "1week": 604800,
    "1day": 86400,
    "4h": 14400,
}

# 角色 → 周期：M2 首版**固定业务法则**（任务书拍板；1H/股票延后）。
ROLE_TIMEFRAMES: dict[str, str] = {
    "context": "1week",
    "setup": "1day",
    "trigger": "4h",
}

# reference_price 与对齐 4H close 的相对容差（极小，防浮点噪声）
REFERENCE_TOLERANCE = 1e-6

# 少假设门控（任务书拍板）
#  - 周线 up/down 映射 long/short；sideways/unknown 拒绝；
#  - 日线方向必须与周线及 Trigger 同向；
#  - 4H 状态只要求 ready，方向/强度不额外过滤；
#  - Trigger 事件方向 long/buy→long、short/sell→short。


@dataclass(frozen=True)
class MultiTimeframeConfig:
    """M2 融合配置（冻结 + 深冻结，第二轮复验整改）。

    - durations：1week/1day/4h 三键正整数秒，驱动 close_time 计算（值可配置）；
    - direction_map：恰有 up/down → long/short，驱动 Context/Setup 方向映射；
    - buy_sell_map：Trigger 事件方向别名（键规范化为非空小写，值 long/short）。

    roles 不是配置项（固定业务法则，传入即 ValueError）；schema_version 固定。
    构造后任何映射只读（MappingProxyType），调用者原 dict 后改不影响
    hash/行为；config_hash 显式序列化真实字段。
    """

    schema_version: str = SCHEMA_VERSION
    durations: dict[str, int] = field(
        default_factory=lambda: dict(TIMEFRAME_DURATIONS)
    )
    direction_map: dict[str, str] = field(
        default_factory=lambda: {"up": "long", "down": "short"}
    )
    buy_sell_map: dict[str, str] = field(
        default_factory=lambda: {"buy": "long", "sell": "short"}
    )

    def __init__(
        self,
        *,
        schema_version: str = SCHEMA_VERSION,
        durations: dict[str, int] | None = None,
        direction_map: dict[str, str] | None = None,
        buy_sell_map: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        if extra:
            raise ValueError(
                f"MultiTimeframeConfig 不支持字段 {sorted(extra)}"
                f"（roles 是固定业务法则，首版不可配置）"
            )
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(
            self, "durations",
            dict(durations) if durations is not None else dict(TIMEFRAME_DURATIONS),
        )
        object.__setattr__(
            self, "direction_map",
            dict(direction_map) if direction_map is not None
            else {"up": "long", "down": "short"},
        )
        object.__setattr__(
            self, "buy_sell_map",
            dict(buy_sell_map) if buy_sell_map is not None
            else {"buy": "long", "sell": "short"},
        )
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version 固定为 {SCHEMA_VERSION!r}，收到 {self.schema_version!r}"
            )
        if set(self.durations) != set(TIMEFRAME_DURATIONS):
            raise ValueError(
                f"durations 必须恰为 {sorted(TIMEFRAME_DURATIONS)} 三键"
                f"（值可配置），收到 {sorted(self.durations)}"
            )
        durations: dict[str, int] = {}
        for tf, dur in self.durations.items():
            if isinstance(dur, bool) or not isinstance(dur, int) or dur <= 0:
                raise ValueError(f"duration 必须为正整数秒: {tf}={dur!r}")
            durations[tf] = int(dur)
        if set(self.direction_map) != {"up", "down"}:
            raise ValueError(
                f"direction_map 必须恰有 up/down 两键，收到 {sorted(self.direction_map)}"
            )
        direction_map: dict[str, str] = {}
        for key, val in self.direction_map.items():
            if val not in ("long", "short"):
                raise ValueError(
                    f"direction_map[{key!r}] 必须为 'long'/'short'，收到 {val!r}"
                )
            direction_map[key] = val
        # buy_sell_map 键规范化为非空小写；重复归一键拒绝
        buy_sell_map: dict[str, str] = {}
        for key, val in self.buy_sell_map.items():
            nk = str(key).strip().lower()
            if not nk:
                raise ValueError(f"buy_sell_map 键不能为空（收到 {key!r}）")
            if nk in buy_sell_map:
                raise ValueError(f"buy_sell_map 键规范化后重复: {nk!r}")
            if val not in ("long", "short"):
                raise ValueError(
                    f"buy_sell_map[{key!r}] 必须为 'long'/'short'，收到 {val!r}"
                )
            buy_sell_map[nk] = val
        # 深冻结：只读映射（构造后不可变，hash/行为与调用方原 dict 解耦）
        object.__setattr__(self, "durations", MappingProxyType(durations))
        object.__setattr__(self, "direction_map", MappingProxyType(direction_map))
        object.__setattr__(self, "buy_sell_map", MappingProxyType(buy_sell_map))

    @property
    def trigger_timeframe(self) -> str:
        return ROLE_TIMEFRAMES["trigger"]

    def config_hash(self) -> str:
        """稳定序列化**真实被消费字段** → sha256 前 16 位（审计/回放校验）。"""
        payload = json.dumps({
            "schema_version": self.schema_version,
            "durations": dict(self.durations),
            "direction_map": dict(self.direction_map),
            "buy_sell_map": dict(self.buy_sell_map),
        }, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


DEFAULT_MULTI_TIMEFRAME_CONFIG = MultiTimeframeConfig()


def _safe(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        return value if math.isfinite(float(value)) else None
    except (TypeError, ValueError):
        return None


def _parse_int_seconds(value: Any, field_name: str) -> int | None:
    """受控解析整数秒：缺失 / 非数值 / NaN / Inf / 负数 / 非整数 → None。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < 0 or f != int(f):
        return None
    return int(f)


def _parse_reference(value: Any) -> float | None:
    """受控解析参考价：缺失 / 非数值 / NaN / Inf → None。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _validate_as_of(as_of: Any) -> None:
    """as_of 必须为非负整数秒（bool/负数/非整数/缺失 → ValueError）。"""
    if isinstance(as_of, bool) or not isinstance(as_of, int) or as_of < 0:
        raise ValueError(f"as_of 必须为非负整数秒，收到 {as_of!r}")


def _bar_time(candle: dict) -> int:
    """bar 开盘秒（int 校验）：缺 time / 非法 / NaN / Inf / 负数 / 非整数 →
    明确 ValueError（行情输入契约错误，不允许未说明异常穿透）；
    cutoff bar 可缺 OHLC 但必须有合法 time。"""
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


# ═══════════════════════════════════════════════════════════════════════════════
#  任务 1：时间对齐（每周期截至 as_of 的最后已收盘 bar）
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class AlignedPeriod:
    """单周期截至 as_of 的对齐结果（frozen，纯计算结果）。"""

    role: str                          # context / setup / trigger
    timeframe: str                     # 1week / 1day / 4h
    available: bool                    # 周期数据可用且 MarketState ready
    unavailable_reason: str | None = None
    bar_time: int | None = None        # 最后已收盘 bar 开盘秒
    bar_close_time: int | None = None  # = bar_time + duration
    config_hash: str = ""
    market_state: MarketState | None = None
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "timeframe": self.timeframe,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "bar_time": self.bar_time,
            "bar_close_time": self.bar_close_time,
            "config_hash": self.config_hash,
            "market_state": self.market_state.to_dict() if self.market_state else None,
            "reasons": list(self.reasons),
        }


def align_period(
    candles: list[dict],
    timeframe: str,
    as_of: int,
    role: str,
    config: MarketStateConfig = DEFAULT_MARKET_STATE_CONFIG,
    m2_config: MultiTimeframeConfig = DEFAULT_MULTI_TIMEFRAME_CONFIG,
) -> AlignedPeriod:
    """对齐单个周期：返回截至 as_of 的最后已收盘 bar 及其 MarketState。

    流程（顺序敏感）：
      1. timeframe / as_of 合法性校验（非法 → ValueError）；
      2. 按 time 稳定排序（唯一 time 前提下重排结果相同；重复 time → 受控不可用）；
      3. 扫描 cutoff：首个「显式 is_closed=False」或「close_time > as_of」；
         只消费其前连续前缀，cutoff bar 本身及之后全部不可见；
      4. 只把已收盘前缀交给 M1 precompute_market_states（OHLC 读取发生在
         过滤之后，cutoff 处缺 OHLC 不会 KeyError）。

    时长来自 m2_config.durations（配置真正驱动 close_time）；角色周期固定
    ROLE_TIMEFRAMES（roles 不是配置项）。
    """
    if timeframe not in m2_config.durations:
        raise ValueError(
            f"非法 timeframe: {timeframe!r}（配置支持 {sorted(m2_config.durations)}）"
        )
    _validate_as_of(as_of)
    duration = int(m2_config.durations[timeframe])
    cfg_hash = config.config_hash()

    sorted_candles = sorted(candles, key=_bar_time)
    if not sorted_candles:
        return AlignedPeriod(
            role, timeframe, False,
            unavailable_reason="无已收盘 bar（截至 as_of 前无数据）",
            config_hash=cfg_hash,
            reasons=("cutoff@0（无已收盘 bar）",),
        )

    # ── cutoff 扫描（先于任何 OHLC 读取）──────────────────────────────────
    # 审计 reasons 只保留「消费摘要」（消费 bars / 最后 bar_time），不记录
    # cutoff 触发条件——同一 as_of 下「传恰截至前缀」与「传含未来全量序列」
    # 必须逐字段一致（重放一致性），触发条件是输入形态差异而非对齐结果差异。
    seen: set[int] = set()
    cutoff = len(sorted_candles)
    for i, c in enumerate(sorted_candles):
        t = _bar_time(c)
        if t in seen:
            return AlignedPeriod(
                role, timeframe, False,
                unavailable_reason="冲突重复时间",
                config_hash=cfg_hash,
                reasons=(f"bar time={t} 出现多次，无法确定唯一对齐",),
            )
        seen.add(t)
        close_time = t + duration
        is_unclosed = c.get("is_closed", True) is False
        if is_unclosed or close_time > as_of:
            cutoff = i
            break

    closed = sorted_candles[:cutoff]
    if not closed:
        return AlignedPeriod(
            role, timeframe, False,
            unavailable_reason="无已收盘 bar（截至 as_of 前无数据）",
            config_hash=cfg_hash,
            reasons=("cutoff@0（无已收盘 bar）",),
        )

    # ── 只消费已收盘前缀（M1 引擎在此读取 OHLC，cutoff 之后不可见）────────
    states = precompute_market_states(closed, config)
    last = states[-1]
    bar_time = int(last.bar_time)
    bar_close_time = bar_time + duration
    consumption = f"cutoff@{len(closed)}（消费 bars={len(closed)}，最后 bar_time={bar_time}）"
    if not last.ready:
        return AlignedPeriod(
            role, timeframe, False,
            unavailable_reason=last.missing_reason or "历史不足",
            bar_time=bar_time, bar_close_time=bar_close_time,
            config_hash=cfg_hash, market_state=last,
            reasons=(consumption,),
        )
    return AlignedPeriod(
        role, timeframe, True,
        bar_time=bar_time, bar_close_time=bar_close_time,
        config_hash=cfg_hash, market_state=last,
        reasons=(consumption,),
    )


def align_all_periods(
    symbol: str,
    candles_by_timeframe: dict[str, list[dict]],
    as_of: int,
    config: MarketStateConfig = DEFAULT_MARKET_STATE_CONFIG,
    m2_config: MultiTimeframeConfig = DEFAULT_MULTI_TIMEFRAME_CONFIG,
) -> dict[str, AlignedPeriod]:
    """对齐三个角色周期。角色 → 周期映射固定 ROLE_TIMEFRAMES（第二轮整改：
    roles 不是配置项）；candles_by_timeframe 的 key 为 timeframe
    （1week / 1day / 4h）；缺周期 → 该角色受控不可用（不抛异常）。
    as_of 在任何周期处理前先校验（缺全部周期也不能绕过）。
    symbol 仅用于审计可追溯性（当前未进入结果字段，为未来回放保留）。
    """
    _validate_as_of(as_of)
    out: dict[str, AlignedPeriod] = {}
    for role, tf in ROLE_TIMEFRAMES.items():
        candles = candles_by_timeframe.get(tf)
        if candles is None:
            out[role] = AlignedPeriod(
                role, tf, False,
                unavailable_reason=f"缺少 {tf} 周期数据",
                config_hash=config.config_hash(),
            )
        else:
            out[role] = align_period(candles, tf, as_of, role, config, m2_config)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  任务 2：角色融合与事件决策
# ═══════════════════════════════════════════════════════════════════════════════

# 稳定 reason codes（层 + code + 中文解释）
REASON_OK = {
    "context": ("ctx_ok", "周线 Context 就绪且方向明确（up→long / down→short）"),
    "setup": ("setup_ok", "日线 Setup 就绪且与周线 Context 及 4H Trigger 同向"),
    "trigger": ("trigger_ok", "4H Trigger 就绪，事件来自最新已收盘 4H bar 且方向合法"),
}
REASON_REJECT = {
    "ctx_not_ready": ("context", "周线 Context 未就绪：{detail}"),
    "ctx_sideways": ("context", "周线方向为 {detail}（sideways/unknown 拒绝，仅 up/down 可映射 long/short）"),
    "ctx_reject_direction": ("context", "Context 逆势：周线方向 {detail} 与 4H Trigger 方向相反，拒绝"),
    "setup_not_ready": ("setup", "日线 Setup 未就绪：{detail}"),
    "setup_inconsistent": ("setup", "日线 Setup 不一致：{detail}（日线方向必须与周线及 Trigger 同向）"),
    "trigger_not_ready": ("trigger", "4H Trigger 未就绪：{detail}"),
    "trigger_wrong_period": ("trigger", "Trigger 周期错误：{detail}（必须证明来自 4H；缺失/冲突/非 4H 均拒绝）"),
    "trigger_stale": ("trigger", "Trigger 时间陈旧：{detail}（必须来自对齐后的最新已收盘 4H bar）"),
    "trigger_bad_time": ("trigger", "Trigger 时间非法：{detail}（signal_time 必须为合法整数秒）"),
    "trigger_bad_direction": ("trigger", "Trigger 方向非法：{detail}（仅 long/buy→long、short/sell→short）"),
    "trigger_bad_reference": ("trigger", "Trigger 参考价非法：{detail}（reference_price 必须为有限正数且与对齐 4H close 一致）"),
    "trigger_bad_event_type": ("trigger", "Trigger 事件类型非法：{detail}（event_type 必须非空）"),
    "trigger_payload_conflict": ("trigger", "Trigger payload 与顶层/对齐契约冲突：{detail}"),
}

# 全部合法 reason codes（schema 枚举与运行时共用同一来源）
ALL_REASON_CODES: tuple[str, ...] = tuple(
    [ok for _, ok in REASON_OK.values()]
    + list(REASON_REJECT)
)


def normalize_direction(
    raw: Any,
    m2_config: MultiTimeframeConfig = DEFAULT_MULTI_TIMEFRAME_CONFIG,
) -> str | None:
    """Trigger 事件方向归一（别名表来自 m2_config.buy_sell_map，配置驱动）：
    long/buy→long、short/sell→short、自定义别名→映射值；其余→None。"""
    if raw is None:
        return None
    v = str(raw).strip().lower()
    if v in ("long", "short"):
        return v
    return m2_config.buy_sell_map.get(v)


def market_direction_to_trade(direction: str, m2_config: MultiTimeframeConfig) -> str | None:
    """周线/日线 MarketState 方向 → 交易方向（配置 direction_map 驱动）。"""
    return m2_config.direction_map.get(direction)


def event_timeframe(trigger_event: dict) -> tuple[str | None, bool]:
    """事件显式周期（顶层或 payload.timeframe）：
    返回 (周期或 None, 是否冲突)。顶层与 payload 同时存在但不同 → 冲突。"""
    payload = trigger_event.get("payload")
    top_raw = trigger_event.get("timeframe")
    pl_raw = payload.get("timeframe") if isinstance(payload, dict) else None

    def norm(v: Any) -> str | None:
        if v is None:
            return None
        return str(v).strip().lower()

    top, pl = norm(top_raw), norm(pl_raw)
    if top is not None and pl is not None and top != pl:
        return top, True
    return (top if top is not None else pl), False


def validate_trigger_event(
    trigger_event: dict,
    aligned_bar_time: int | None,
    m2_config: MultiTimeframeConfig,
) -> tuple[list[tuple[str, str]], str | None]:
    """校验 Trigger 事件与 M2 契约的一致性（第二轮整改：防只校验外壳）。

    返回 (rejections, normalized_direction)；rejections 为空 = 事件合法。
    rejections 为 [(code, detail)]，全部为 trigger 层稳定 code：
      trigger_bad_event_type / trigger_bad_direction / trigger_wrong_period /
      trigger_bad_time / trigger_stale / trigger_payload_conflict。
    任何不可信字段在此受控拒绝，不抛异常。
    """
    rejects: list[tuple[str, str]] = []
    payload = trigger_event.get("payload")
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        rejects.append((
            "trigger_payload_conflict",
            f"payload 必须为 dict，收到 {type(payload).__name__}",
        ))

    # ── event_type 非空 ───────────────────────────────────────────────────
    ev_type = trigger_event.get("event_type")
    if ev_type is None or str(ev_type).strip() == "":
        rejects.append(("trigger_bad_event_type", repr(ev_type)))

    # ── 顶层方向 ──────────────────────────────────────────────────────────
    trig_dir = normalize_direction(trigger_event.get("direction"), m2_config)
    if trig_dir is None:
        rejects.append(("trigger_bad_direction", repr(trigger_event.get("direction"))))

    # ── payload 校验（存在字段必须与顶层/对齐契约一致）───────────────────
    if isinstance(payload, dict):
        if "direction" in payload:
            pl_dir = normalize_direction(payload["direction"], m2_config)
            if pl_dir is None:
                rejects.append((
                    "trigger_payload_conflict",
                    f"payload.direction={payload['direction']!r} 无法归一",
                ))
            elif trig_dir is not None and pl_dir != trig_dir:
                rejects.append((
                    "trigger_payload_conflict",
                    f"payload.direction={payload['direction']!r} ≠ 顶层 "
                    f"direction={trigger_event.get('direction')!r}",
                ))
        if "signal_type" in payload:
            if payload["signal_type"] != ev_type:
                rejects.append((
                    "trigger_payload_conflict",
                    f"payload.signal_type={payload['signal_type']!r} ≠ "
                    f"event_type={ev_type!r}",
                ))
        if "reference_price" in payload:
            pl_ref = _parse_reference(payload["reference_price"])
            top_ref = _parse_reference(trigger_event.get("reference_price"))
            if pl_ref is None or pl_ref <= 0:
                rejects.append((
                    "trigger_payload_conflict",
                    f"payload.reference_price={payload['reference_price']!r} 非法",
                ))
            elif top_ref is not None and pl_ref != top_ref:
                rejects.append((
                    "trigger_payload_conflict",
                    f"payload.reference_price={pl_ref} ≠ 顶层 reference_price",
                ))
        sig_for_payload = _parse_int_seconds(
            trigger_event.get("signal_time"), "signal_time"
        )
        for fld in ("occurred_at", "confirmed_at"):
            if fld in payload:
                v = _parse_int_seconds(payload[fld], fld)
                if sig_for_payload is None or v != sig_for_payload:
                    rejects.append((
                        "trigger_payload_conflict",
                        f"payload.{fld}={payload[fld]!r} ≠ signal_time",
                    ))
        if "bar_closed" in payload:
            if payload["bar_closed"] is not True:
                rejects.append((
                    "trigger_payload_conflict",
                    f"payload.bar_closed={payload['bar_closed']!r} 必须严格为 True",
                ))
        # 周期：顶层与 payload 必须一致且等于 Trigger 周期；缺失/冲突拒绝
        ev_tf, tf_conflict = event_timeframe(trigger_event)
        trigger_tf = m2_config.trigger_timeframe
        if tf_conflict:
            rejects.append((
                "trigger_wrong_period",
                f"顶层 timeframe={trigger_event.get('timeframe')!r} 与 "
                f"payload.timeframe={payload.get('timeframe')!r} 冲突",
            ))
        elif ev_tf is None:
            rejects.append(("trigger_wrong_period", "事件未声明周期（顶层与 payload 均无 timeframe）"))
        elif ev_tf != trigger_tf:
            rejects.append(("trigger_wrong_period", f"{ev_tf}（必须为 {trigger_tf}）"))

    # ── signal_time：合法整数秒且 == 对齐 bar_time ────────────────────────
    sig = _parse_int_seconds(trigger_event.get("signal_time"), "signal_time")
    if sig is None:
        rejects.append(("trigger_bad_time", repr(trigger_event.get("signal_time"))))
    elif aligned_bar_time is not None and sig != aligned_bar_time:
        rejects.append(("trigger_stale", f"signal_time={sig}，对齐 4H bar_time={aligned_bar_time}"))
    elif aligned_bar_time is None:
        rejects.append(("trigger_stale", f"signal_time={sig}，无对齐 4H bar"))

    return rejects, trig_dir


@dataclass(frozen=True)
class TriggerSnapshot:
    """evaluate 时一次性复制的规范化 Trigger 审计快照（frozen，不可变）。

    第三轮整改根因：FusionDecision 不得保存调用者原始 trigger_event 的可变
    引用（否则调用者后续修改 ev 会改变已生成决策的 to_dict）。本快照只含
    经校验/受控的字段，构造后与原始事件完全解耦。
    """

    event_type: str
    raw_direction: str
    normalized_direction: str | None
    signal_time: int | None
    reference_price: float | None
    summary: str | None
    timeframe: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "raw_direction": self.raw_direction,
            "normalized_direction": self.normalized_direction,
            "signal_time": self.signal_time,
            "reference_price": self.reference_price,
            "summary": self.summary,
            "timeframe": self.timeframe,
        }


def _controlled_summary(value: Any) -> str | None:
    """summary 受控字符串（文档契约）：None → None；str → 原样；
    其他类型 → str() 稳定转换（dict/list/set 等不进入输出，仅存其字符串
    表示，避免未验证对象污染稳定 JSON）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


@dataclass(frozen=True)
class FusionDecision:
    """端到端融合决策（frozen，纯计算结果；to_dict 稳定 JSON 无 NaN）。

    第三轮整改：不保存原始 trigger_event（只存 TriggerSnapshot 快照）；
    aligned 与 _direction_map 构造时复制并深冻结为 MappingProxyType，
    调用者修改原事件/原 candles/原 config 或尝试修改 decision 内部
    （d.aligned[...]=、d._direction_map[...]=）均不得改变 to_dict/hash/
    direction；to_dict 每次返回全新普通 dict/list，修改返回值不影响对象。
    """

    schema_version: str
    symbol: str
    as_of: int
    aligned: dict[str, AlignedPeriod]
    trigger: TriggerSnapshot
    decisions: tuple[tuple[str, str, str], ...]  # (layer, code, explanation)
    market_state_config_hash: str
    fusion_config_hash: str
    _direction_map: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_MULTI_TIMEFRAME_CONFIG.direction_map),
        repr=False,
    )

    def __post_init__(self) -> None:
        # 深冻结：aligned / _direction_map 只读（值本身为 frozen dataclass）
        object.__setattr__(self, "aligned", MappingProxyType(dict(self.aligned)))
        object.__setattr__(
            self, "_direction_map", MappingProxyType(dict(self._direction_map))
        )

    # ── 派生属性 ──────────────────────────────────────────────────────────
    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(code for _, code, _ in self.decisions)

    @property
    def explanations(self) -> tuple[str, ...]:
        return tuple(exp for _, _, exp in self.decisions)

    @property
    def rejected(self) -> bool:
        return any(code != "ok" and not code.endswith("_ok") for _, code, _ in self.decisions)

    @property
    def all_layers_ok(self) -> bool:
        codes = self.reason_codes
        return all(code in ("ctx_ok", "setup_ok", "trigger_ok") for code in codes) and len(codes) == 3

    @property
    def eligible(self) -> bool:
        return not self.rejected and self.all_layers_ok

    @property
    def direction(self) -> str | None:
        if not self.eligible:
            return None
        ctx = self.aligned["context"]
        if ctx.market_state is None:
            return None
        return self._direction_map.get(ctx.market_state.trend_direction)

    # ── 证据（只写证据，不做硬门槛；未经 M3 统计不得宣称优势）────────────
    def _evidence(self) -> dict[str, Any]:
        vol: dict[str, Any] = {}
        candle: dict[str, Any] = {}
        volume: dict[str, Any] = {}
        for role, p in self.aligned.items():
            ms = p.market_state
            if ms is None:
                vol[role] = None
                candle[role] = None
                volume[role] = None
                continue
            vol[role] = {
                "level": ms.vol_level,
                "phase": ms.vol_phase,
                "atr_price": _safe(ms.atr_price),
                "percentile": _safe(ms.vol_percentile),
                "change_5": _safe(ms.vol_change_5),
            }
            candle[role] = {
                "direction": ms.candle_direction,
                "strength": ms.candle_strength,
                "body_atr": _safe(ms.body_atr),
                "close_location_long": _safe(ms.close_location_long),
                "close_location_short": _safe(ms.close_location_short),
            }
            volume[role] = ms.volume_state
        return {"volatility": vol, "candle": candle, "volume": volume}

    def _trigger_summary(self) -> dict[str, Any]:
        """Trigger 审计摘要：只读 TriggerSnapshot 快照（不携带原始 payload，
        与调用者原事件完全解耦）。"""
        return self.trigger.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "as_of": self.as_of,
            "direction": self.direction,
            "eligible": self.eligible,
            "context": self.aligned["context"].to_dict(),
            "setup": self.aligned["setup"].to_dict(),
            "trigger": self.aligned["trigger"].to_dict(),
            "trigger_event": self._trigger_summary(),
            "reason_codes": list(self.reason_codes),
            "explanations": list(self.explanations),
            "config_hash": {
                "market_state": self.market_state_config_hash,
                "fusion": self.fusion_config_hash,
            },
            "evidence": self._evidence(),
        }


def _ref_matches(ref: float, close: float | None) -> bool:
    """reference_price 与对齐 4H close 的相对容差匹配（极小，防浮点噪声）。"""
    if close is None or close <= 0:
        return True
    return abs(ref - close) <= REFERENCE_TOLERANCE * abs(close)


def evaluate(
    symbol: str,
    candles_by_timeframe: dict[str, list[dict]],
    as_of: int,
    trigger_event: dict,
    market_state_config: MarketStateConfig = DEFAULT_MARKET_STATE_CONFIG,
    fusion_config: MultiTimeframeConfig = DEFAULT_MULTI_TIMEFRAME_CONFIG,
) -> FusionDecision:
    """端到端角色融合（任务 2 公开入口）。

    门控（少假设，任务书拍板）：
      Context 过滤方向（周线 up/down→long/short，逆势拒绝）
      → Setup 过滤结构（日线方向与周线及 Trigger 同向）
      → Trigger 决定入场（4H ready；事件周期/时间/方向/payload/参考价校验）。
    任何一层不满足 → eligible=False 并保留完整审计；拒绝也必须返回结果，
    不抛异常（非法 timeframe / 非法 bar time / 非法配置 / 非法 as_of /
    trigger_event 非 dict 属调用方错误 → ValueError，契约见文档）。
    """
    _validate_as_of(as_of)
    if not isinstance(trigger_event, dict):
        raise ValueError(
            f"trigger_event 必须为 dict（monitor 事件字典），收到 "
            f"{type(trigger_event).__name__}"
        )
    aligned = align_all_periods(symbol, candles_by_timeframe, as_of,
                                market_state_config, fusion_config)
    decisions: list[tuple[str, str, str]] = []

    def _reject(layer: str, code: str, detail: str) -> None:
        _, template = REASON_REJECT[code]
        decisions.append((layer, code, template.format(detail=detail)))

    def _ok(layer: str) -> None:
        code, text = REASON_OK[layer]
        decisions.append((layer, code, text))

    ctx = aligned["context"]
    setup = aligned["setup"]
    trig = aligned["trigger"]

    # ── Trigger 事件校验（先于门控，供同向比较；拒绝项记入 trigger 层）────
    trig_rejects, trig_dir = validate_trigger_event(
        trigger_event, trig.bar_time, fusion_config,
    )
    trigger_rejected = bool(trig_rejects)
    for code, detail in trig_rejects:
        layer = REASON_REJECT[code][0]
        _reject(layer, code, detail)

    # ── reference_price：有限正数且与对齐 4H close 一致（极小容差）────────
    raw_ref = trigger_event.get("reference_price")
    ref = _parse_reference(raw_ref)
    if ref is None or ref <= 0:
        detail = "缺失" if raw_ref is None else repr(raw_ref)
        _reject("trigger", "trigger_bad_reference", detail)
        trigger_rejected = True
    else:
        close4 = trig.market_state.close if trig.market_state else None
        if not _ref_matches(ref, close4):
            _reject(
                "trigger", "trigger_bad_reference",
                f"reference_price={ref} 与对齐 4H close={close4} 不一致",
            )
            trigger_rejected = True

    # ── context 层 ────────────────────────────────────────────────────────
    if not ctx.available:
        _reject("context", "ctx_not_ready", ctx.unavailable_reason or "无可用周线 bar")
    else:
        wdir = ctx.market_state.trend_direction if ctx.market_state else "unknown"
        ctx_dir = market_direction_to_trade(wdir, fusion_config)
        if ctx_dir is None:
            _reject("context", "ctx_sideways", wdir)
        elif trig_dir is not None and trig_dir != ctx_dir:
            _reject(
                "context", "ctx_reject_direction",
                f"{wdir}（{ctx_dir}）vs Trigger {trig_dir}",
            )
        else:
            _ok("context")

    # ── setup 层 ──────────────────────────────────────────────────────────
    if not setup.available:
        _reject("setup", "setup_not_ready", setup.unavailable_reason or "无可用日线 bar")
    else:
        sdir = setup.market_state.trend_direction if setup.market_state else "unknown"
        sdir_trade = market_direction_to_trade(sdir, fusion_config)
        ctx_dir_now = (
            market_direction_to_trade(ctx.market_state.trend_direction, fusion_config)
            if ctx.available and ctx.market_state else None
        )
        if sdir_trade is None:
            _reject("setup", "setup_inconsistent", f"日线方向 {sdir} 无法映射 long/short")
        elif trig_dir is not None and sdir_trade != trig_dir:
            _reject("setup", "setup_inconsistent", f"日线 {sdir}（{sdir_trade}）≠ Trigger {trig_dir}")
        elif ctx_dir_now is not None and sdir_trade != ctx_dir_now:
            _reject("setup", "setup_inconsistent", f"日线 {sdir}（{sdir_trade}）≠ 周线 {ctx_dir_now}")
        else:
            _ok("setup")

    # ── trigger 层（4H 状态 ready）────────────────────────────────────────
    if not trig.available:
        _reject("trigger", "trigger_not_ready", trig.unavailable_reason or "无可用 4H bar")
        trigger_rejected = True
    if not trigger_rejected:
        _ok("trigger")

    return FusionDecision(
        schema_version=SCHEMA_VERSION,
        symbol=symbol,
        as_of=as_of,
        aligned=aligned,
        trigger=TriggerSnapshot(
            event_type=str(trigger_event.get("event_type") or ""),
            raw_direction=str(trigger_event.get("direction") or ""),
            normalized_direction=trig_dir,
            signal_time=_parse_int_seconds(
                trigger_event.get("signal_time"), "signal_time"
            ),
            reference_price=ref,
            summary=_controlled_summary(trigger_event.get("summary")),
            timeframe=event_timeframe(trigger_event)[0],
        ),
        decisions=tuple(decisions),
        market_state_config_hash=market_state_config.config_hash(),
        fusion_config_hash=fusion_config.config_hash(),
        _direction_map=dict(fusion_config.direction_map),
    )
