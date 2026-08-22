"""M2 多周期融合测试（任务 3：证明与文档）。

覆盖（任务书要求 ≥12 项，本文件 21 项）：
  1. 到达顺序不变：同一组唯一时间 bars 乱序重排 → 融合结果完全一致；
  2. close 边界：close_time==as_of 可用、早 1 秒不可用（晚 1 秒不改变正确性）；
  3. 未来/未收盘缺 OHLC 不消费（无 KeyError，cutoff 后不可见）；
  4. 追加未来 bars 不改结果；
  5. 三类状态不足（周/日/4H 各自历史不足 → ctx/setup/trigger_not_ready）；
  6. 逆势拒绝（Context 与 Trigger 方向相反 → ctx_reject_direction）；
  7. Setup 拒绝（日线方向与周线/Trigger 不同向 → setup_inconsistent）；
  8. buy/sell 归一（buy→long、sell→short；其余方向非法）；
  9. 陈旧 Trigger 拒绝（signal_time 不是最新已收盘 4H bar → trigger_stale）；
  10. Trigger 周期错误（非 4H → trigger_wrong_period）与方向非法；
  11. 成功路径：三层全 pass → eligible，含完整审计证据；
  12. 证据（vol/candle/volume）不参与硬门控；
  13. schema/JSON/config 回放：Pydantic 严格校验、JSON 无 NaN、重复 evaluate 一致；
  14. 逐前缀/不同 as_of 循环：历史 decision 不被未来改变（不只测辅助函数）；
  15. 非法 timeframe → ValueError；缺周期/重复时间 → 受控不可用；
  16. 周线 sideways → ctx_sideways 拒绝。
"""
from __future__ import annotations

import json
import math
import random

import pytest

from app.core.market_state import MarketStateConfig
from app.core.multi_timeframe import (
    TIMEFRAME_DURATIONS,
    align_all_periods,
    align_period,
    evaluate,
)
from app.schemas.market_fusion import validate_decision

# ═══════════════════════════════════════════════════════════════════════════════
#  确定性序列构造
# ═══════════════════════════════════════════════════════════════════════════════

T0 = 1_700_000_000
N4 = 480  # 4H bar 数（覆盖 ~80 天，足以让周线 ready）
STEP4 = TIMEFRAME_DURATIONS["4h"]
STEP1D = TIMEFRAME_DURATIONS["1day"]
STEP1W = TIMEFRAME_DURATIONS["1week"]


def make_bars(n: int, step: int, *, drift: float = 0.008, wave: float = 0.02) -> list[dict]:
    """确定性 OHLC 序列（时间按 step 递增，唯一 time）。"""
    out = []
    for i in range(n):
        close = 100.0 * (1 + drift * i) * (1 + wave * math.sin(i / 9.0))
        op = 100.0 * (1 + drift * max(0, i - 1)) * (1 + wave * math.sin((i - 1) / 9.0))
        out.append({
            "time": T0 + i * step,
            "open": round(op, 6),
            "high": round(max(op, close) * 1.012, 6),
            "low": round(min(op, close) * 0.988, 6),
            "close": round(close, 6),
            "volume": 1000 + (i * 37) % 11,
        })
    return out


def make_candles(*, wave4: float = 0.02, wave1d: float = 0.02, wave1w: float = 0.02,
                 drift4: float = 0.008, drift1d: float = 0.008, drift1w: float = 0.008,
                 n4: int = N4) -> dict[str, list[dict]]:
    """三周期对齐序列：4H 每 14400 秒、1day 每 86400、1week 每 604800。"""
    return {
        "4h": make_bars(n4, STEP4, drift=drift4, wave=wave4),
        "1day": make_bars(n4 // 6 + 2, STEP1D, drift=drift1d, wave=wave1d),
        "1week": make_bars(n4 // 42 + 4, STEP1W, drift=drift1w, wave=wave1w),
    }


def as_of_4h_close(bar_index: int) -> int:
    """4H bar bar_index 刚收盘的时刻（close_time）。"""
    return T0 + (bar_index + 1) * STEP4


# 缩短 warmup 的 M1 配置：~11 根即可让 MarketState ready（测试专用）
QUICK = MarketStateConfig(
    vol_percentile_lookback=10,
    vol_percentile_low=0.2,
    vol_percentile_high=0.8,
    vol_change_window=3,
    ma_fast=5,
    ma_slow=10,
    ma_tertiary=15,
    atr_period=5,
    adx_period=5,
    pivot=3,
)

# 4H bar 470 已收盘（三周期都 ready 的最小 as_of 之后）
AS_OF = as_of_4h_close(470)
SIG = T0 + 470 * STEP4  # 对齐后最新已收盘 4H bar 的 bar_time

# 4H 序列各 bar 的真实 close（与 make_bars 逐位一致，round 6）；
# Trigger 的 reference_price 必须来自对齐 4H bar（整改 7：强化契约）
_4H_CLOSES = [
    round(100.0 * (1 + 0.008 * i) * (1 + 0.02 * math.sin(i / 9.0)), 6)
    for i in range(N4)
]
REF_CLOSE = _4H_CLOSES[470]


def make_trigger(signal_time: int = SIG, direction: str = "long",
                 timeframe: str = "4h", event_type: str = "ma_breakout",
                 reference_price: float | None = None) -> dict:
    """模拟 monitor 事件字典（与 signals/monitor_signals._event 同构）。
    reference_price 默认取对齐 4H bar 的真实 close（与 M1 MarketState.close 同源）。"""
    if reference_price is None:
        k = (signal_time - T0) // STEP4
        reference_price = _4H_CLOSES[k]
    return {
        "strategy": "ma",
        "event_type": event_type,
        "direction": direction,
        "severity": "signal",
        "signal_time": signal_time,
        "reference_price": reference_price,
        "summary": "收盘上穿 MA60",
        "payload": {
            "schema_version": "v1",
            "signal_type": event_type,
            "direction": direction,
            "state": "signal",
            "timeframe": timeframe,
            "occurred_at": signal_time,
            "confirmed_at": signal_time,
            "reference_price": reference_price,
            "bar_closed": True,
            "evidence": {},
            "parameters": {},
        },
    }


def run(candles=None, as_of: int = AS_OF, trigger: dict | None = None):
    if candles is None:
        candles = make_candles()
    if trigger is None:
        trigger = make_trigger()
    return evaluate("BTCUSDT", candles, as_of, trigger, QUICK)


# ═══════════════════════════════════════════════════════════════════════════════
#  1. 到达顺序不变
# ═══════════════════════════════════════════════════════════════════════════════

def test_reorder_same_result():
    """同一组唯一时间 bars 乱序重排 → 融合结果完全一致（到达顺序不变）。"""
    candles = make_candles()
    rng = random.Random(2026)
    shuffled = {k: list(v) for k, v in candles.items()}
    for key in shuffled:
        rng.shuffle(shuffled[key])
    base = run(candles)
    reordered = run(shuffled)
    assert base.to_dict() == reordered.to_dict()
    assert base.eligible == reordered.eligible


# ═══════════════════════════════════════════════════════════════════════════════
#  2. close 边界
# ═══════════════════════════════════════════════════════════════════════════════

def test_close_boundary_exact_as_of_usable():
    """close_time == as_of：4H bar 470 恰好可用，Trigger 匹配 → 三层 pass。"""
    d = run()
    assert d.eligible is True
    assert d.aligned["trigger"].bar_time == SIG
    assert d.aligned["trigger"].bar_close_time == AS_OF


def test_close_boundary_one_second_early_unusable():
    """close_time == as_of 早 1 秒：bar 470 未收盘，对齐落到 bar 469，
    Trigger 时间陈旧 → trigger_stale（晚 1 秒不改变正确性）。"""
    d = run(as_of=AS_OF - 1)
    assert d.aligned["trigger"].bar_time == T0 + 469 * STEP4
    assert "trigger_stale" in d.reason_codes
    assert d.eligible is False


def test_close_boundary_one_second_later_next_bar_usable():
    """as_of 晚 1 秒：bar 470 仍已收盘（消费），bar 471 仍不可见；
    除 as_of 字段外，对齐与门控结果与精确时刻完全一致。"""
    d1 = run()
    d2 = run(as_of=AS_OF + 1)
    out1, out2 = d1.to_dict(), d2.to_dict()
    out1.pop("as_of")
    out2.pop("as_of")
    assert out1 == out2


# ═══════════════════════════════════════════════════════════════════════════════
#  3. 未来 / 未收盘缺 OHLC 不消费
# ═══════════════════════════════════════════════════════════════════════════════

def test_future_unclosed_missing_ohlc_not_consumed():
    """未来追加只有 time/is_closed=False 的 bar（无 OHLC）：不消费、无 KeyError，
    结果与无该 bar 完全一致。"""
    candles = make_candles()
    base = run(candles)
    future = dict(candles)
    future["4h"] = list(candles["4h"]) + [
        {"time": T0 + (N4 + 3) * STEP4, "is_closed": False}
    ]
    future["1day"] = list(candles["1day"]) + [
        {"time": T0 + (N4 // 6 + 5) * STEP1D, "is_closed": False}
    ]
    out = run(future)
    assert out.to_dict() == base.to_dict()


def test_unclosed_bar_inside_prefix_is_cutoff():
    """已收盘 bar 之后插入一根显式未收盘 bar（无 OHLC），其后的完整 K 线
    一律不可见：无 KeyError，对齐结果与未插入一致。"""
    candles = make_candles()
    base = run(candles)
    tampered = dict(candles)
    # 在 bar 460 位置插入未收盘 bar（460 根 4H 之前都正常），bar 461+ 不可见
    cut = list(candles["4h"][:461]) + [{"time": T0 + 461 * STEP4, "is_closed": False}]
    cut += list(candles["4h"][462:])
    tampered["4h"] = cut
    d = run(tampered)
    # cutoff 在 461：对齐落到 bar 460 或更早 → Trigger 必然陈旧
    assert d.aligned["trigger"].bar_time <= T0 + 460 * STEP4
    assert d.to_dict() != base.to_dict() or d.aligned["trigger"].bar_time != SIG


# ═══════════════════════════════════════════════════════════════════════════════
#  4. 追加未来不改结果
# ═══════════════════════════════════════════════════════════════════════════════

def test_append_future_bars_no_change():
    """as_of 固定，追加带完整 OHLC 的未来 bars → 融合结果逐字段一致。"""
    candles = make_candles()
    base = run(candles)
    grown = {k: list(v) for k, v in candles.items()}
    for i in range(8):
        grown["4h"].append(make_bars(1, STEP4, drift=0.008)[0] | {"time": T0 + (N4 + i) * STEP4})
    out = run(grown)
    assert out.to_dict() == base.to_dict()


# ═══════════════════════════════════════════════════════════════════════════════
#  5. 三类状态不足
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("short,expect_code", [
    ("1week", "ctx_not_ready"),
    ("1day", "setup_not_ready"),
    ("4h", "trigger_not_ready"),
])
def test_insufficient_history_rejected(short, expect_code):
    """各周期历史不足 → 对应角色受控不可用并拒绝（不抛异常，可审计）。"""
    candles = make_candles()
    candles[short] = make_bars(5, TIMEFRAME_DURATIONS[short])
    d = run(candles)
    assert d.eligible is False
    assert expect_code in d.reason_codes
    # 拒绝必须返回完整审计结果
    assert d.to_dict()["context"] is not None
    assert d.to_dict()["trigger_event"] is not None


# ═══════════════════════════════════════════════════════════════════════════════
#  6. 逆势拒绝
# ═══════════════════════════════════════════════════════════════════════════════

def test_context_rejects_opposite_trigger():
    """周线 up（long）但 Trigger short → Context 逆势拒绝，说明具体层。"""
    d = run(trigger=make_trigger(direction="short"))
    assert d.eligible is False
    assert d.reason_codes == ("ctx_reject_direction", "setup_inconsistent", "trigger_ok")
    assert "逆势" in d.explanations[0]


# ═══════════════════════════════════════════════════════════════════════════════
#  7. Setup 拒绝
# ═══════════════════════════════════════════════════════════════════════════════

def test_setup_rejects_inconsistent_direction():
    """周线/4H 均为 up（long），日线 down → Setup 不一致拒绝。"""
    candles = make_candles(drift1d=-0.008)
    d = run(candles)
    assert d.eligible is False
    assert d.reason_codes == ("ctx_ok", "setup_inconsistent", "trigger_ok")


def test_weekly_sideways_rejected():
    """周线 sideways → Context 方向拒绝（sideways/unknown 不映射 long/short）。"""
    candles = make_candles(drift1w=0.0, wave1w=0.0)
    d = run(candles)
    assert d.aligned["context"].market_state.trend_direction == "sideways"
    assert d.eligible is False
    assert "ctx_sideways" in d.reason_codes


# ═══════════════════════════════════════════════════════════════════════════════
#  8. buy/sell 归一
# ═══════════════════════════════════════════════════════════════════════════════

def test_buy_sell_normalization():
    """buy→long（成功路径）；sell→short（周线 up 下被逆势拒绝，方向已归一）。"""
    d_buy = run(trigger=make_trigger(direction="buy"))
    assert d_buy.eligible is True
    assert d_buy.direction == "long"
    d_sell = run(trigger=make_trigger(direction="sell"))
    assert d_sell.reason_codes[0] == "ctx_reject_direction"  # sell 已归一为 short
    assert d_sell.to_dict()["trigger_event"]["normalized_direction"] == "short"


def test_bad_direction_rejected():
    """方向既非 long/buy 也非 short/sell → trigger_bad_direction。"""
    d = run(trigger=make_trigger(direction="up"))
    assert d.eligible is False
    assert "trigger_bad_direction" in d.reason_codes


# ═══════════════════════════════════════════════════════════════════════════════
#  9. 陈旧 Trigger 拒绝
# ═══════════════════════════════════════════════════════════════════════════════

def test_stale_trigger_rejected():
    """signal_time 是上一根已收盘 4H bar → trigger_stale。"""
    d = run(trigger=make_trigger(signal_time=SIG - STEP4))
    assert d.eligible is False
    assert "trigger_stale" in d.reason_codes


# ═══════════════════════════════════════════════════════════════════════════════
#  10. Trigger 周期错误
# ═══════════════════════════════════════════════════════════════════════════════

def test_wrong_period_trigger_rejected():
    """事件显式周期为 1day → trigger_wrong_period。"""
    d = run(trigger=make_trigger(timeframe="1day"))
    assert d.eligible is False
    assert "trigger_wrong_period" in d.reason_codes


# ═══════════════════════════════════════════════════════════════════════════════
#  11. 成功路径
# ═══════════════════════════════════════════════════════════════════════════════

def test_success_path_full_audit():
    """三层均明确 pass：eligible=True、direction=long、三角色快照齐全、
    完整审计证据，Pydantic schema 严格校验通过。"""
    d = run()
    assert d.eligible is True
    assert d.direction == "long"
    assert d.reason_codes == ("ctx_ok", "setup_ok", "trigger_ok")
    out = d.to_dict()
    for role in ("context", "setup", "trigger"):
        assert out[role]["available"] is True
        assert out[role]["market_state"]["ready"] is True
    assert out["trigger_event"]["event_type"] == "ma_breakout"
    assert out["trigger_event"]["normalized_direction"] == "long"
    assert set(out["config_hash"]) == {"market_state", "fusion"}
    # 严格 schema 校验
    validated = validate_decision(out)
    assert validated.eligible is True
    assert validated.direction == "long"
    # 证据字段存在且可序列化
    ev = out["evidence"]
    assert set(ev) == {"volatility", "candle", "volume"}
    assert set(ev["volatility"]) == {"context", "setup", "trigger"}


# ═══════════════════════════════════════════════════════════════════════════════
#  12. 证据不参与硬门控
# ═══════════════════════════════════════════════════════════════════════════════

def test_evidence_not_hard_gate():
    """波动 phase/path、K 线 strength、volume 只写证据：不同波动形态的序列
    只要方向门控通过就同样 eligible，证据值不改变 eligible。
    注意：Trigger 的 reference_price 必须取对应序列 4H bar 的真实 close
    （整改 7 强化契约），与证据无关。"""
    calm = make_candles(wave4=0.02)
    wild = make_candles(wave4=0.09, wave1d=0.09, wave1w=0.09)
    trig_wild = make_trigger(reference_price=wild["4h"][470]["close"])
    trig_wild["payload"]["reference_price"] = wild["4h"][470]["close"]
    d_calm = evaluate("BTCUSDT", calm, AS_OF, make_trigger(), QUICK)
    d_wild = evaluate("BTCUSDT", wild, AS_OF, trig_wild, QUICK)
    ev_calm = d_calm.to_dict()["evidence"]
    ev_wild = d_wild.to_dict()["evidence"]
    # 两组证据确有差异（波动 level 不同），但都不参与门控
    assert ev_calm["volatility"] != ev_wild["volatility"]
    assert d_calm.eligible is True
    assert d_wild.eligible is True
    assert d_calm.direction == d_wild.direction == "long"


# ═══════════════════════════════════════════════════════════════════════════════
#  13. schema / JSON / config 回放
# ═══════════════════════════════════════════════════════════════════════════════

def test_schema_json_config_replay():
    """to_dict 稳定：JSON round-trip 一致、无 NaN/Infinity、
    重复 evaluate 结果与 config_hash 一致。"""
    d1 = run()
    out1 = d1.to_dict()
    text = json.dumps(out1, ensure_ascii=False)
    assert "NaN" not in text and "Infinity" not in text and "nan" not in text
    assert json.loads(text) == out1
    # 重复 evaluate → 完全一致（可回放）
    d2 = run()
    assert d2.to_dict() == out1
    assert d1.to_dict()["config_hash"] == d2.to_dict()["config_hash"]
    # 配置变更 → hash 变化
    other = MarketStateConfig(vol_percentile_lookback=12)
    d3 = run(trigger=make_trigger(), candles=make_candles())
    from app.core.multi_timeframe import evaluate as _evaluate
    d4 = _evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), other)
    assert d4.to_dict()["config_hash"]["market_state"] != out1["config_hash"]["market_state"]


# ═══════════════════════════════════════════════════════════════════════════════
#  14. 逐前缀 / 不同 as_of：历史 decision 不被未来改变
# ═══════════════════════════════════════════════════════════════════════════════

def test_prefix_history_immutable_across_asofs():
    """对多个 as_of（4H bar 收盘时刻）分别用「截至该时刻的前缀」与
    「含未来 bars 的全量序列」计算，逐 as_of 断言融合结果完全一致——
    证明历史 decision 不被未来 K 线改变（覆盖 eligible 与拒绝两类路径）。"""
    candles = make_candles()
    full = {k: list(v) for k, v in candles.items()}
    checked = 0
    eligible_seen = 0
    for k in range(30, 472, 6):  # 含 warmup 前后、三周期 ready 前后
        as_of = as_of_4h_close(k)
        trig = make_trigger(signal_time=T0 + k * STEP4)
        # 前缀：三周期各自只保留 close_time <= as_of 的 bar（含未来未加）
        prefix = {}
        for tf, bars in full.items():
            dur = TIMEFRAME_DURATIONS[tf]
            prefix[tf] = [b for b in bars if int(b["time"]) + dur <= as_of]
        d_prefix = evaluate("BTCUSDT", prefix, as_of, trig, QUICK)
        # 全量：同一 as_of 但序列含全部未来 bars
        d_full = evaluate("BTCUSDT", full, as_of, trig, QUICK)
        assert d_prefix.to_dict() == d_full.to_dict(), f"as_of={as_of} 前后不一致"
        checked += 1
        if d_full.eligible:
            eligible_seen += 1
    assert checked >= 70, "逐前缀循环覆盖不足"
    assert eligible_seen >= 1, "至少一个 as_of 走成功路径（不能被未来改变）"


# ═══════════════════════════════════════════════════════════════════════════════
#  15. 非法 timeframe / 缺周期 / 重复时间
# ═══════════════════════════════════════════════════════════════════════════════

def test_illegal_timeframe_raises_valueerror():
    """非法 timeframe → ValueError（受控错误，不是 KeyError/NaN）。"""
    with pytest.raises(ValueError, match="非法 timeframe"):
        align_period([], "1h", AS_OF, "trigger", QUICK)


def test_missing_period_controlled_unavailable():
    """缺 1week 周期 → ctx 受控不可用（ctx_not_ready），不抛异常。"""
    candles = make_candles()
    del candles["1week"]
    d = run(candles)
    assert d.eligible is False
    assert d.reason_codes[0] == "ctx_not_ready"
    assert d.to_dict()["context"]["available"] is False


def test_duplicate_time_unavailable():
    """冲突重复时间（出现在已收盘可见区域内）→ 受控不可用
    （不抛异常、不产生 NaN 状态），端到端拒绝且可审计。"""
    candles = make_candles()
    dup = list(candles["4h"])
    # 在已收盘前缀内（bar 300 前）复制一根 bar，造成 time 重复
    dup.insert(300, dict(dup[299]))
    dup_tf = {"4h": dup, "1day": candles["1day"], "1week": candles["1week"]}
    p = align_all_periods("S", dup_tf, AS_OF, QUICK)
    assert p["trigger"].available is False
    assert "重复" in (p["trigger"].unavailable_reason or "")
    # 端到端：受控拒绝，可审计
    d = evaluate("S", dup_tf, AS_OF, make_trigger(), QUICK)
    assert d.eligible is False
    assert "trigger_not_ready" in d.reason_codes


# ═══════════════════════════════════════════════════════════════════════════════
#  复验整改（2026-08-13 管理者退回反例）：以下只追加，不改/不删上面 24 项
# ═══════════════════════════════════════════════════════════════════════════════

def test_trigger_missing_timeframe_rejected():
    """反例1：顶层与 payload 均无 timeframe → 事件无法证明来自 4H，
    必须 trigger_wrong_period 拒绝，绝不能 eligible=True。"""
    ev = {"event_type": "x", "direction": "long", "signal_time": SIG,
          "reference_price": 100.0, "payload": {}}
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_wrong_period" in d.reason_codes


def test_trigger_conflicting_timeframe_rejected():
    """顶层 timeframe 与 payload.timeframe 同时存在但冲突 → 拒绝，不能只信顶层。"""
    ev = make_trigger()  # payload timeframe=4h
    ev["timeframe"] = "4h"       # 顶层 4h
    ev["payload"]["timeframe"] = "1day"  # payload 1day → 冲突
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_wrong_period" in d.reason_codes
    # 反向：顶层 1day / payload 4h 同样拒绝
    ev = make_trigger()
    ev["timeframe"] = "1day"
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_wrong_period" in d.reason_codes


def test_trigger_bad_signal_time_rejected():
    """反例2：signal_time 非法（'bad'）→ 受控 trigger_bad_time 拒绝，不抛异常。"""
    ev = make_trigger()
    ev["signal_time"] = "bad"
    d = run(trigger=ev)  # 不得抛 ValueError
    assert d.eligible is False
    assert "trigger_bad_time" in d.reason_codes
    assert d.to_dict()["trigger_event"]["signal_time"] is None


def test_trigger_bad_reference_rejected():
    """反例2：reference_price='bad' → to_dict/schema 可生成（降级 None），
    记 trigger_bad_reference，禁止 NaN 输出。"""
    ev = make_trigger()
    ev["reference_price"] = "bad"
    d = run(trigger=ev)
    out = d.to_dict()  # 不得抛 ValueError
    assert "trigger_bad_reference" in d.reason_codes
    assert out["trigger_event"]["reference_price"] is None
    import json as _json
    text = _json.dumps(out, ensure_ascii=False)
    assert "NaN" not in text and "Infinity" not in text
    validate_decision(out)  # schema 必须可生成


def test_trigger_nan_inf_fields_rejected():
    """非有限 signal_time/reference_price（NaN/Inf）→ 受控拒绝/降级，无 NaN。"""
    for bad in (float("nan"), float("inf"), "-inf"):
        ev = make_trigger()
        ev["signal_time"] = bad
        d = run(trigger=ev)
        assert d.eligible is False
        assert "trigger_bad_time" in d.reason_codes
        assert d.to_dict()["trigger_event"]["signal_time"] is None
    ev = make_trigger()
    ev["reference_price"] = float("nan")
    d = run(trigger=ev)
    assert "trigger_bad_reference" in d.reason_codes
    assert d.to_dict()["trigger_event"]["reference_price"] is None


def test_trigger_payload_conflicts_rejected():
    """payload.direction / occurred_at / confirmed_at / bar_closed 与顶层或
    对齐契约冲突 → trigger_payload_conflict 拒绝（防只校验外壳）。"""
    # payload.direction 与顶层冲突
    ev = make_trigger(direction="long")
    ev["payload"]["direction"] = "short"
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes
    # occurred_at 与 signal_time 冲突
    ev = make_trigger()
    ev["payload"]["occurred_at"] = SIG - STEP4
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes
    # confirmed_at 与 signal_time 冲突
    ev = make_trigger()
    ev["payload"]["confirmed_at"] = SIG - 2 * STEP4
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes
    # bar_closed=False（事件来自未收盘 bar）
    ev = make_trigger()
    ev["payload"]["bar_closed"] = False
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes


def test_custom_config_drives_behavior():
    """反例3：MultiTimeframeConfig 全部字段必须真正驱动行为（防空转）：
    改 buy_sell_map 影响方向归一、改 direction_map 影响 Context 方向、
    改 durations 影响 close_time 对齐。"""
    from app.core.multi_timeframe import MultiTimeframeConfig as Cfg
    # buy_sell_map 驱动归一
    cfg = Cfg(buy_sell_map={"buy": "long", "sell": "short", "go_long": "long"})
    ev = make_trigger(direction="go_long")
    d = evaluate("S", make_candles(), AS_OF, ev, QUICK, cfg)
    assert d.eligible is True
    assert d.to_dict()["trigger_event"]["normalized_direction"] == "long"
    # direction_map 驱动 Context 方向映射（up→short → 与 long Trigger 逆势拒绝）
    cfg2 = Cfg(direction_map={"up": "short", "down": "long"})
    d2 = evaluate("S", make_candles(), AS_OF, make_trigger(), QUICK, cfg2)
    assert d2.eligible is False
    assert d2.reason_codes[0] == "ctx_reject_direction"
    # durations 驱动 close_time：1day 时长改 1h → 日线消费根数改变 → 对齐 bar 改变
    cfg3 = Cfg(durations={"1week": 604800, "1day": 3600, "4h": 14400})
    candles3 = {"4h": make_candles()["4h"],
                "1day": make_bars(1870, 3600),  # 每 1 小时一根，覆盖超过 AS_OF
                "1week": make_candles()["1week"]}
    d3 = evaluate("S", candles3, AS_OF, make_trigger(), QUICK, cfg3)
    d_default = evaluate("S", candles3, AS_OF, make_trigger(), QUICK)
    assert d3.aligned["setup"].bar_time != d_default.aligned["setup"].bar_time
    assert d3.to_dict()["config_hash"]["fusion"] != d_default.to_dict()["config_hash"]["fusion"]


def test_invalid_fusion_config_rejected():
    """反例3：配置完整验证——roles 集合、正时长、映射值 long/short 非法即 ValueError。"""
    from app.core.multi_timeframe import MultiTimeframeConfig as Cfg
    with pytest.raises(ValueError, match="roles"):
        Cfg(roles={"context": "1week", "setup": "1day"})  # 缺 trigger
    with pytest.raises(ValueError, match="duration"):
        Cfg(durations={"1week": 604800, "1day": 0, "4h": 14400})  # 非正时长
    with pytest.raises(ValueError, match="duration"):
        Cfg(durations={"1week": 604800, "1day": -86400, "4h": 14400})
    with pytest.raises(ValueError, match="direction_map|buy_sell_map"):
        Cfg(direction_map={"up": "banana", "down": "short"})
    with pytest.raises(ValueError, match="roles"):
        Cfg(roles={"context": "1week", "setup": "1h", "trigger": "4h"},
            durations={"1week": 604800, "1day": 86400, "4h": 14400, "1h": 3600})


def test_schema_semantic_validation():
    """反例4：Pydantic 语义约束——Literal 枚举 + 交叉校验，非法 dict 必须
    ValidationError（eligible=True 无方向、reason_codes 为空、role 错等）。"""
    from pydantic import ValidationError
    d = run()
    out = d.to_dict()
    # 正常通过
    validate_decision(out)
    # eligible=True 但 direction 非法
    bad = dict(out); bad["direction"] = "banana"
    with pytest.raises(ValidationError):
        validate_decision(bad)
    # eligible=True 但 reason_codes 为空
    bad = dict(out); bad["reason_codes"] = []
    with pytest.raises(ValidationError):
        validate_decision(bad)
    # eligible=True 但 codes 非三个 ok
    bad = dict(out); bad["reason_codes"] = ["ctx_ok", "setup_ok"]
    with pytest.raises(ValidationError):
        validate_decision(bad)
    # eligible=False 但 direction 非 None
    rej = run(trigger=make_trigger(direction="short"))
    rbad = rej.to_dict(); rbad["direction"] = "long"
    with pytest.raises(ValidationError):
        validate_decision(rbad)
    # context.role 错误
    bad = dict(out); bad["context"]["role"] = "wrong"
    with pytest.raises(ValidationError):
        validate_decision(bad)
    # context.timeframe 错误
    bad = dict(out); bad["context"]["timeframe"] = "4h"
    with pytest.raises(ValidationError):
        validate_decision(bad)
    # 未知 reason code
    bad = dict(out); bad["reason_codes"] = ["ctx_ok", "setup_ok", "banana"]
    with pytest.raises(ValidationError):
        validate_decision(bad)
    # reason_codes 与 explanations 不等长
    bad = dict(out); bad["explanations"] = ["x"]
    with pytest.raises(ValidationError):
        validate_decision(bad)


def test_bad_bar_time_raises_clear_valueerror():
    """行情 bar 缺 time / time 非法 → 明确 ValueError（非 KeyError/未说明异常）；
    cutoff bar 可缺 OHLC 但必须有合法 time。"""
    candles = make_candles()
    # 缺 time
    bad = {"4h": [dict(c, **{"time": None}) for c in candles["4h"]],
           "1day": candles["1day"], "1week": candles["1week"]}
    with pytest.raises(ValueError, match="time"):
        evaluate("S", bad, AS_OF, make_trigger(), QUICK)
    # time='bad'
    bad = {"4h": [dict(c, **{"time": "bad"}) for c in candles["4h"]],
           "1day": candles["1day"], "1week": candles["1week"]}
    with pytest.raises(ValueError, match="time"):
        evaluate("S", bad, AS_OF, make_trigger(), QUICK)
    # cutoff bar（is_closed=False）无 OHLC 但有合法 time → 正常受控处理
    cut = list(candles["4h"][:300]) + [{"time": T0 + 300 * STEP4, "is_closed": False}]
    ok = {"4h": cut, "1day": candles["1day"], "1week": candles["1week"]}
    d = evaluate("S", ok, AS_OF, make_trigger(), QUICK)
    assert d.aligned["trigger"].bar_time == T0 + 299 * STEP4
    assert d.eligible is False  # 事件对不上对齐 bar → 拒绝（受控）


def test_reference_price_missing_rejected():
    """reference_price 缺失 → trigger_bad_reference（事件契约必须含参考价）。"""
    ev = make_trigger()
    del ev["reference_price"]
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_bad_reference" in d.reason_codes


# ═══════════════════════════════════════════════════════════════════════════════
#  第二轮复验反例（2026-08-13 管理者退回 A–E）：只追加，不改/不删上面 35 项
# ═══════════════════════════════════════════════════════════════════════════════

def test_config_roles_is_fixed_business_rule():
    """反例A：roles 是固定业务法则，不是可配置项——任何 roles 输入必须拒绝
    （自定义 roles 会与 schema 硬编码角色矛盾）。"""
    from app.core.multi_timeframe import MultiTimeframeConfig as Cfg
    with pytest.raises(ValueError, match="roles"):
        Cfg(roles={"context": "1day", "setup": "1week", "trigger": "4h"})
    with pytest.raises(ValueError, match="roles"):
        Cfg(roles={"context": "1week", "setup": "1day", "trigger": "1h"})
    # 固定映射运行时一致（ROLE_TIMEFRAMES 唯一来源）
    from app.core.multi_timeframe import ROLE_TIMEFRAMES
    assert ROLE_TIMEFRAMES == {"context": "1week", "setup": "1day", "trigger": "4h"}


def test_config_deep_frozen():
    """反例B：配置深冻结——构造后调用者原 dict 变化、cfg 字段赋值都不能改变
    hash/行为（历史可重放）。"""
    from app.core.multi_timeframe import MultiTimeframeConfig as Cfg
    d = {"1week": 604800, "1day": 86400, "4h": 14400}
    cfg = Cfg(durations=d)
    d["4h"] = 1  # 构造前原 dict 后改
    assert cfg.durations["4h"] == 14400  # 不受影响
    h1 = cfg.config_hash()
    with pytest.raises(TypeError):
        cfg.durations["4h"] = 1          # cfg 字段后改失败
    with pytest.raises(TypeError):
        cfg.direction_map["up"] = "short"
    with pytest.raises(TypeError):
        cfg.buy_sell_map["buy"] = "short"
    assert cfg.config_hash() == h1       # hash 不变


def test_config_buy_sell_map_normalized():
    """整改1：buy_sell_map 键规范化为非空小写；重复归一键拒绝；
    durations 恰为三键；direction_map 恰有 up/down。"""
    from app.core.multi_timeframe import MultiTimeframeConfig as Cfg
    cfg = Cfg(buy_sell_map={"BUY": "long", "Sell": "short"})
    assert dict(cfg.buy_sell_map) == {"buy": "long", "sell": "short"}
    with pytest.raises(ValueError, match="重复"):
        Cfg(buy_sell_map={"buy": "long", "Buy": "short"})
    with pytest.raises(ValueError):
        Cfg(buy_sell_map={" ": "long"})  # 空键
    with pytest.raises(ValueError, match="up"):
        Cfg(direction_map={"up": "long"})  # 缺 down
    with pytest.raises(ValueError, match="三键"):
        Cfg(durations={"1week": 604800, "4h": 14400})  # 缺 1day
    with pytest.raises(ValueError):
        Cfg(schema_version="m9-x")  # schema_version 固定


def test_payload_not_in_output_and_no_nan():
    """反例C + 整改3：M2 输出不携带原始 payload；任意深层 NaN/set 不影响
    to_dict；json.dumps(..., allow_nan=False) 必须通过。"""
    ev = make_trigger()
    ev["payload"]["evidence"] = {"x": float("nan"), "s": {1, 2}}
    d = run(trigger=ev)
    assert d.eligible is True  # 证据只写不门控，但输出不受污染
    out = d.to_dict()
    assert "payload" not in out["trigger_event"]
    text = json.dumps(out, allow_nan=False)  # 不得抛异常
    assert "NaN" not in text and "Infinity" not in text
    validate_decision(out)


def test_payload_direction_banana_conflict():
    """反例D：payload.direction 存在但无法归一 → trigger_payload_conflict。"""
    ev = make_trigger()
    ev["payload"]["direction"] = "banana"
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes


def test_bar_closed_strict_true():
    """反例D：bar_closed 存在时必须严格为 bool True；0/'true'/None/False 均 conflict。"""
    for bad in (0, "true", None, False, 1):
        ev = make_trigger()
        ev["payload"]["bar_closed"] = bad
        d = run(trigger=ev)
        assert d.eligible is False, f"bar_closed={bad!r}"
        assert "trigger_payload_conflict" in d.reason_codes
    assert run(trigger=make_trigger()).eligible is True  # True 通过


def test_event_type_required():
    """整改4：event_type 必须非空 → trigger_bad_event_type。"""
    for bad in ("", None):
        ev = make_trigger()
        ev["event_type"] = bad
        d = run(trigger=ev)
        assert d.eligible is False
        assert "trigger_bad_event_type" in d.reason_codes


def test_payload_signal_type_must_match_event_type():
    """整改4：payload.signal_type 存在时必须等于顶层 event_type。"""
    ev = make_trigger(event_type="ma_breakout")
    ev["payload"]["signal_type"] = "other"
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes


def test_payload_reference_must_match_top():
    """整改4：payload.reference_price 存在时必须有限正数且等于顶层。"""
    ev = make_trigger()
    ev["payload"]["reference_price"] = 999.0  # 与顶层不一致
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes
    ev = make_trigger()
    ev["payload"]["reference_price"] = "bad"  # 非法
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes
    ev = make_trigger()
    ev["payload"]["reference_price"] = -5.0  # 非正
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_payload_conflict" in d.reason_codes


def test_reference_must_match_4h_close():
    """整改7：reference_price 必须与对齐 4H MarketState.close 一致（极小容差）；
    错价 → trigger_bad_reference。payload.reference_price 必须与顶层同步。"""
    ev = make_trigger()
    ev["reference_price"] = round(REF_CLOSE + 1.0, 6)  # 错价（顶层与 payload 同步）
    ev["payload"]["reference_price"] = round(REF_CLOSE + 1.0, 6)
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_bad_reference" in d.reason_codes
    # 精确一致（同一 round 来源）→ 通过
    ev = make_trigger()
    ev["reference_price"] = REF_CLOSE
    ev["payload"]["reference_price"] = REF_CLOSE
    assert run(trigger=ev).eligible is True
    # 极小容差内 → 通过
    ev = make_trigger()
    ev["reference_price"] = REF_CLOSE + 1e-7
    ev["payload"]["reference_price"] = REF_CLOSE + 1e-7
    assert run(trigger=ev).eligible is True
    # 明显偏差 → 拒绝
    ev = make_trigger()
    ev["reference_price"] = REF_CLOSE * 1.01
    ev["payload"]["reference_price"] = REF_CLOSE * 1.01
    d = run(trigger=ev)
    assert d.eligible is False
    assert "trigger_bad_reference" in d.reason_codes


def test_schema_strict_forgery_rejected():
    """反例E：伪造 eligible=True（market_state/bar_time/close_time 全 None、
    config_hash 任意键）→ ValidationError。"""
    from pydantic import ValidationError
    out = run().to_dict()
    out["config_hash"] = {"anything": "goes"}
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = run().to_dict()
    for r in ("context", "setup", "trigger"):
        out[r]["market_state"] = None
        out[r]["bar_time"] = None
        out[r]["bar_close_time"] = None
    with pytest.raises(ValidationError):
        validate_decision(out)
    # config_hash 非 16 位 hex
    out = run().to_dict()
    out["config_hash"]["market_state"] = "nothex"
    with pytest.raises(ValidationError):
        validate_decision(out)


def test_schema_available_requires_full_state():
    """整改5：available=True 时 unavailable_reason=None、state 非空且
    ready/is_closed、config_hash 链一致、close_time>bar_time。"""
    from pydantic import ValidationError
    out = run().to_dict()
    out["context"]["unavailable_reason"] = "x"
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = run().to_dict()
    out["context"]["market_state"]["ready"] = False
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = run().to_dict()
    out["context"]["market_state"]["is_closed"] = False
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = run().to_dict()
    out["trigger"]["config_hash"] = "0" * 16  # 与 state 不一致
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = run().to_dict()
    out["trigger"]["bar_close_time"] = out["trigger"]["bar_time"]
    with pytest.raises(ValidationError):
        validate_decision(out)


def test_evaluate_missing_all_periods_still_validates_as_of():
    """整改6：缺全部周期时也必须先验证 as_of；bool/负数/非整数拒绝。"""
    with pytest.raises(ValueError, match="as_of"):
        evaluate("S", {}, True, make_trigger(), QUICK)
    with pytest.raises(ValueError, match="as_of"):
        evaluate("S", {}, -1, make_trigger(), QUICK)
    with pytest.raises(ValueError, match="as_of"):
        evaluate("S", {}, "123", make_trigger(), QUICK)
    # 合法 as_of + 缺全部周期 → 受控拒绝（不异常）
    d = evaluate("S", {}, AS_OF, make_trigger(), QUICK)
    assert d.eligible is False
    assert "ctx_not_ready" in d.reason_codes
    assert "setup_not_ready" in d.reason_codes
    assert "trigger_not_ready" in d.reason_codes


def test_trigger_event_non_dict_controlled():
    """整改6：trigger_event 非 dict → 受控 ValueError（写明契约）。"""
    with pytest.raises(ValueError, match="trigger_event"):
        evaluate("S", make_candles(), AS_OF, "not-a-dict", QUICK)
    with pytest.raises(ValueError, match="trigger_event"):
        evaluate("S", make_candles(), AS_OF, None, QUICK)


def test_json_roundtrip_strict_still_valid():
    """整改5：allow_nan=False 序列化后 JSON roundtrip 仍通过严格 schema。"""
    out = run().to_dict()
    text = json.dumps(out, allow_nan=False)
    restored = json.loads(text)
    assert validate_decision(restored).eligible is True


# ═══════════════════════════════════════════════════════════════════════════════
#  第三轮复验反例（2026-08-13 管理者退回：决策快照深不可变 + schema 语义等价）
#  只追加，不改/不删上面 50 项
# ═══════════════════════════════════════════════════════════════════════════════

def test_decision_snapshot_immune_to_caller_edits():
    """FAIL1：evaluate 返回后修改调用者 ev（event_type/direction/summary/
    timeframe）→ 已生成决策的 to_dict 不得变化（TriggerSnapshot 快照解耦）。"""
    ev = make_trigger()
    d = evaluate("BTCUSDT", make_candles(), AS_OF, ev, QUICK)
    out_before = d.to_dict()
    ev["event_type"] = "hacked"
    ev["direction"] = "short"
    ev["summary"] = "hacked"
    ev["timeframe"] = "1day"
    assert d.to_dict() == out_before
    assert d.to_dict()["trigger_event"]["event_type"] == "ma_breakout"
    assert d.to_dict()["trigger_event"]["raw_direction"] == "long"
    assert d.to_dict()["trigger_event"]["summary"] == "收盘上穿 MA60"


def test_decision_internal_fields_frozen():
    """FAIL2/3：d._direction_map 赋值、d.aligned[...] 赋值均失败；
    内部 AlignedPeriod/MarketState 亦不可变。"""
    d = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK)
    with pytest.raises(TypeError):
        d._direction_map["up"] = "short"
    with pytest.raises(TypeError):
        d.aligned["trigger"] = None
    with pytest.raises(TypeError):
        d.aligned["context"] = d.aligned["trigger"]
    assert d.direction == "long"  # 未被篡改
    assert d.aligned["trigger"].bar_time == SIG


def test_to_dict_mutation_no_writeback():
    """修改 to_dict() 返回值（direction/codes/event_type/evidence）不得反写对象。"""
    d = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK)
    out = d.to_dict()
    out["direction"] = "short"
    out["reason_codes"] = []
    out["explanations"] = []
    out["trigger_event"]["event_type"] = "hacked"
    out["evidence"]["volatility"]["trigger"]["level"] = "high"
    assert d.direction == "long"
    assert d.to_dict()["direction"] == "long"
    assert d.to_dict()["trigger_event"]["event_type"] == "ma_breakout"
    assert d.to_dict()["evidence"]["volatility"]["trigger"]["level"] != "high"


def test_schema_eligible_iff_ok_codes():
    """FAIL4：eligible 与三个 OK codes 严格等价——eligible=False 配全 OK/空
    codes、eligible=True 配含 reject code 均必须 ValidationError。"""
    from pydantic import ValidationError
    rej = evaluate("BTCUSDT", make_candles(), AS_OF,
                   make_trigger(direction="short"), QUICK).to_dict()
    assert rej["eligible"] is False
    rej["reason_codes"] = ["ctx_ok", "setup_ok", "trigger_ok"]
    rej["explanations"] = ["a", "b", "c"]
    with pytest.raises(ValidationError):
        validate_decision(rej)
    rej = evaluate("BTCUSDT", make_candles(), AS_OF,
                   make_trigger(direction="short"), QUICK).to_dict()
    rej["reason_codes"] = []
    rej["explanations"] = []
    with pytest.raises(ValidationError):
        validate_decision(rej)
    okd = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    okd["reason_codes"] = ["ctx_ok", "setup_ok", "trigger_bad_time"]
    okd["explanations"] = ["a", "b", "c"]
    with pytest.raises(ValidationError):
        validate_decision(okd)
    # 正常路径仍通过
    assert validate_decision(evaluate(
        "BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()).eligible is True


def test_schema_nested_nan_inf_rejected():
    """FAIL5a：evidence 嵌套 NaN/Inf（volatility/candle 数值）必须 ValidationError
    （typed 角色模型 + allow_inf_nan=False）。"""
    from pydantic import ValidationError
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["evidence"]["volatility"]["trigger"]["atr_price"] = float("nan")
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["evidence"]["candle"]["context"]["body_atr"] = float("inf")
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["evidence"]["volatility"]["setup"]["percentile"] = float("-inf")
    with pytest.raises(ValidationError):
        validate_decision(out)
    # evidence 角色键固定：多余键拒绝
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["evidence"]["volatility"]["other"] = None
    with pytest.raises(ValidationError):
        validate_decision(out)


def test_schema_eligible_reference_must_match_close():
    """FAIL5b：eligible=True 时 reference_price 篡改为 1.1 倍 → ValidationError；
    且必须在 1e-6 相对容差内匹配 trigger.close。"""
    from pydantic import ValidationError
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["trigger_event"]["reference_price"] = round(REF_CLOSE * 1.1, 6)
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["trigger_event"]["reference_price"] = None
    with pytest.raises(ValidationError):
        validate_decision(out)
    # 正常
    ok = validate_decision(evaluate(
        "BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict())
    assert ok.eligible is True


def test_schema_eligible_event_contract_fields():
    """eligible=True 时 event_type/raw_direction 非空、timeframe=='4h'。"""
    from pydantic import ValidationError
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["trigger_event"]["event_type"] = ""
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["trigger_event"]["raw_direction"] = ""
    with pytest.raises(ValidationError):
        validate_decision(out)
    out = evaluate("BTCUSDT", make_candles(), AS_OF, make_trigger(), QUICK).to_dict()
    out["trigger_event"]["timeframe"] = "1day"
    with pytest.raises(ValidationError):
        validate_decision(out)
