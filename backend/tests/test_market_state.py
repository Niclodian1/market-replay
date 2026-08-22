"""MarketState 核心引擎测试（M1）。

覆盖：
  - 逐前缀因果一致性：precompute(candles[:k+1])[j] == precompute(candles)[j]
    对全部历史位置成立（不一致数必须为 0）；
  - market_state_at == 相同前缀批量预计算的最后一项；
  - expansion→contraction→transition→expansion 明确波动路径；
  - K 线质量定向：强阳/强阴/普通/十字零振幅/缺 ATR/缺 volume；
  - 阈值边界、配置变更、config hash、重复回放一致；
  - append future bar 不改变既有状态；to_dict 可序列化且无 NaN/Infinity。
"""
from __future__ import annotations

import json
import math

import pytest

from app.core.market_state import (
    DEFAULT_MARKET_STATE_CONFIG,
    MarketStateConfig,
    market_state_at,
    precompute_market_states,
)


# ── 确定性序列（与 legacy parity 测试 / gen_m1_golden.py 逐字一致）───────────

_START = 1_700_000_000
_STEP = 3600


def make_series(n, drift_fn, wave_fn, base=100.0):
    out = []
    for i in range(n):
        d = drift_fn(i)
        w = wave_fn(i)
        close = base * d * w
        openp = base * drift_fn(i - 1) * wave_fn(i - 1) if i else close
        out.append({
            "time": _START + i * _STEP,
            "open": round(openp, 6),
            "high": round(max(openp, close) * 1.012, 6),
            "low": round(min(openp, close) * 0.988, 6),
            "close": round(close, 6),
            "volume": 1000 + 50 * ((i * 37) % 11),
        })
    return out


def series_up(n=500):
    return make_series(n, lambda i: 1 + 0.002 * i, lambda i: 1 + 0.05 * math.sin(i / 7.0) + 0.02 * math.sin(i / 2.9))


def series_down(n=500):
    return make_series(n, lambda i: 1 - 0.0015 * i, lambda i: 1 + 0.05 * math.sin(i / 7.0) + 0.02 * math.sin(i / 2.9))


def series_sideways(n=500):
    return make_series(n, lambda i: 1.0, lambda i: 1 + 0.04 * math.sin(i / 9.0) + 0.015 * math.sin(i / 3.1))


def series_vol_cycle(n=500):
    def wave(i):
        amp = 0.02 + 0.06 * (0.5 + 0.5 * math.sin(i / 70.0))
        return 1 + amp * math.sin(i / 4.0)
    return make_series(n, lambda i: 1 + 0.0006 * i, wave)


# ── 1. 逐前缀因果一致性 ────────────────────────────────────────────────────────

PREFIX_SERIES = [
    pytest.param(series_up, id="up"),
    pytest.param(series_down, id="down"),
    pytest.param(series_vol_cycle, id="vol_cycle"),
]


@pytest.mark.parametrize("series_fn", PREFIX_SERIES)
def test_prefix_consistency_every_historical_position(series_fn):
    """追加任何未来 K 线不得改变已产生的 MarketState（每个历史位置不一致数=0）。"""
    candles = series_fn(n=500)
    full = precompute_market_states(candles)
    mismatches = 0
    checked = 0
    first_bad = None
    for k in range(500):
        prefix = precompute_market_states(candles[:k + 1])
        for j in range(k + 1):
            checked += 1
            if prefix[j] != full[j]:
                mismatches += 1
                if first_bad is None:
                    first_bad = (k, j, full[j].bar_time)
    assert checked > 0
    assert mismatches == 0, f"前缀不一致 {mismatches}/{checked}，首个 k={first_bad[0]} j={first_bad[1]}"


@pytest.mark.parametrize("series_fn", PREFIX_SERIES)
def test_append_future_bar_does_not_change_existing(series_fn):
    """append 一根未来 bar 后，既有位置的 MarketState 不变。"""
    candles = series_fn(n=300)
    base = precompute_market_states(candles)
    extended = precompute_market_states(candles + [candles[-1]])
    for j in range(300):
        assert base[j] == extended[j], f"bar {j} 被未来 bar 改变"


def test_market_state_at_equals_batch_prefix_last():
    """market_state_at == 相同前缀批量预计算的最后一项。"""
    candles = series_up(n=200)
    for i in (0, 13, 99, 100, 150, 199):
        ms = market_state_at(candles, i)
        prefix_last = precompute_market_states(candles[:i + 1])[-1]
        assert ms == prefix_last, f"index {i} 不一致"
    with pytest.raises(IndexError):
        market_state_at(candles, 200)


def test_to_dict_json_serializable_no_nan():
    """to_dict 稳定、JSON 可序列化，不含 NaN/Infinity。"""
    candles = series_vol_cycle(n=200)
    for state in precompute_market_states(candles):
        d = state.to_dict()
        json.dumps(d)

        def check(obj):
            if isinstance(obj, float):
                assert math.isfinite(obj), f"非有限值: {obj}"
            elif isinstance(obj, dict):
                for v in obj.values():
                    check(v)
            elif isinstance(obj, list):
                for v in obj:
                    check(v)
        check(d)


# ── 2. 波动路径与 K 线质量定向 ─────────────────────────────────────────────────

def _compressed(phases: list[str]) -> list[str]:
    out = []
    for p in phases:
        if not out or out[-1] != p:
            out.append(p)
    return out


def test_volatility_cycle_has_explicit_expansion_contraction_transition_path():
    """波动周期序列必须呈现 expansion→contraction→transition→expansion 的路径。"""
    states = precompute_market_states(series_vol_cycle(n=500))
    phases = [s.vol_phase for s in states if s.ready]
    comp = _compressed(phases)
    # 存在按顺序出现的 expansion < contraction < transition < expansion
    def find(seq, target):
        pos = -1
        for t in target:
            pos = seq.index(t, pos + 1)
        return pos
    try:
        find(comp, ["expansion", "contraction", "transition", "expansion"])
    except ValueError:
        pytest.fail(f"波动路径缺失 expansion→contraction→transition→expansion；实际 {comp}")
    # 同时覆盖 low/normal/high 三档
    levels = {s.vol_level for s in states if s.ready}
    assert {"low", "normal", "high"} <= levels


def _plateau(n=140, base=100.0):
    """稳定平台（波动恒定），供 K 线质量定向测试。"""
    return [{
        "time": _START + i * _STEP,
        "open": base - 0.1,
        "high": base + 0.6,
        "low": base - 0.6,
        "close": base,
        "volume": 1000.0,
    } for i in range(n)]


@pytest.mark.parametrize("body,close,open,high,low,expect_dir,expect_strength", [
    # 强阳：body_atr/range_atr/body_range/close_location_long 全部满足 → strong
    (2.0, 101.5, 99.5, 101.8, 99.4, "bullish", "strong"),
    # 强阴：镜像 → strong
    (2.0, 98.5, 100.5, 100.6, 98.2, "bearish", "strong"),
])
def test_candle_strong_directional(body, close, open, high, low, expect_dir, expect_strength):
    candles = _plateau()
    candles[-1].update(open=open, high=high, low=low, close=close)
    ms = market_state_at(candles, len(candles) - 1)
    assert ms.candle_direction == expect_dir
    assert ms.candle_strength == expect_strength
    assert ms.body_atr is not None and ms.range_atr is not None
    assert ms.body_range is not None
    assert ms.close_location_long is not None and ms.close_location_short is not None


def test_candle_normal_middle_quality():
    """中等质量：满足 2/4 条件 → normal。"""
    candles = _plateau()
    candles[-1].update(open=99.8, high=101.6, low=99.4, close=101.0)
    ms = market_state_at(candles, len(candles) - 1)
    assert ms.candle_direction == "bullish"
    assert ms.candle_strength == "normal"


def test_candle_doji_zero_amplitude_degrades_to_weak():
    """十字（close==open）与零振幅（high==low）安全降级为 weak。"""
    candles = _plateau()
    candles[-1].update(open=100.0, high=100.6, low=99.4, close=100.0)  # 十字
    ms = market_state_at(candles, len(candles) - 1)
    assert ms.candle_direction == "neutral"
    assert ms.candle_strength == "weak"
    assert ms.body_range is not None and ms.body_range == 0.0

    candles = _plateau()
    candles[-1].update(open=100.0, high=100.0, low=100.0, close=100.0)  # 零振幅
    ms = market_state_at(candles, len(candles) - 1)
    assert ms.candle_direction == "neutral"
    assert ms.candle_strength == "weak"
    assert ms.body_range is None and ms.close_location_long is None


def test_candle_missing_atr_degrades_to_unknown():
    """历史不足（无 ATR）→ candle_strength=unknown。"""
    candles = _plateau(n=5)
    ms = market_state_at(candles, len(candles) - 1)
    assert ms.atr is None
    assert ms.candle_strength == "unknown"


def test_volume_state_unknown_when_missing_zero_or_not_applicable():
    """缺失/为零/不适用 → volume_state=unknown（不影响价格状态）。"""
    candles = _plateau()
    candles[-1]["volume"] = 0.0
    ms = market_state_at(candles, len(candles) - 1)
    assert ms.volume_state == "unknown"

    ms = market_state_at(candles, len(candles) - 1, volume_applicable=False)
    assert ms.volume_state == "unknown"

    candles = _plateau()
    ms = market_state_at(candles, len(candles) - 1)
    assert ms.volume_state != "unknown"  # 正常序列有量


# ── 3. 阈值边界 / 配置变更 / 回放 ──────────────────────────────────────────────

def test_adx_strength_threshold_boundaries():
    """ADX 阈值被使用：默认 20/35 与全部归为 ranging 的配置产生不同强度。"""
    candles = series_up(n=300)
    base = precompute_market_states(candles)
    cfg_all_ranging = MarketStateConfig(adx_ranging=200.0, adx_strong=300.0)
    states = precompute_market_states(candles, cfg_all_ranging)
    changed = sum(1 for a, b in zip(base, states) if a.trend_strength != b.trend_strength)
    assert changed > 0, "ADX 阈值变更必须改变强度判定"
    assert all(s.trend_strength == "ranging" for s in states if s.trend_strength != "unknown")


def test_vol_level_threshold_boundaries():
    """波动百分位 0.30/0.70：<0.30 low，>0.70 high，中间 normal。"""
    candles = series_vol_cycle(n=300)
    base = precompute_market_states(candles)
    cfg = MarketStateConfig(vol_percentile_low=0.0, vol_percentile_high=1.0)
    states = precompute_market_states(candles, cfg)
    changed = sum(1 for a, b in zip(base, states) if a.vol_level != b.vol_level)
    assert changed > 0, "波动百分位阈值变更必须改变 level 判定"
    assert all(s.vol_level in ("normal", "unknown") for s in states)


def test_candle_threshold_boundary_contains_equal():
    """K 线质量阈值边界：恰好等于阈值算满足（>=）。"""
    # 平台 atr≈1.2；最后一根 range=1.3 → atr[139]=(1.2*13+1.3)/14≈1.2071
    atr_last = (1.2 * 13 + 1.3) / 14.0
    body = 0.8 * atr_last           # body_atr 恰好 0.8
    low, high = 99.4, 100.7         # range=1.3
    close = low + 0.75 * (high - low)  # close_location_long 恰好 0.75
    candles = _plateau()
    candles[-1].update(open=close - body, high=high, low=low, close=close)
    ms = market_state_at(candles, len(candles) - 1)
    assert ms.body_atr is not None and abs(ms.body_atr - 0.8) < 1e-9
    assert ms.range_atr is not None and ms.range_atr >= 1.0
    assert ms.body_range is not None and ms.body_range >= 0.6
    assert ms.close_location_long is not None and abs(ms.close_location_long - 0.75) < 1e-9
    assert ms.candle_strength == "strong", "恰好等于阈值应算满足（4/4）"
    # 提高阈值 0.8→0.81：body_atr 条件不再满足 → 3/4 → normal
    raised = market_state_at(
        candles, len(candles) - 1, MarketStateConfig(body_atr_threshold=0.81),
    )
    assert raised.body_atr == ms.body_atr
    assert raised.candle_strength == "normal", "阈值提高后该条件不再满足"


def test_config_hash_changes_with_config_and_replay_is_stable():
    """config hash 随配置变化；同配置重复回放完全一致。"""
    c1 = MarketStateConfig()
    c2 = MarketStateConfig(adx_strong=40.0)
    h1, h2 = c1.config_hash(), c2.config_hash()
    assert h1 == c1.config_hash(), "同配置 hash 必须稳定"
    assert h1 != h2, "配置变更必须改变 hash"
    assert len(h1) == 16
    candles = series_up(n=200)
    a = precompute_market_states(candles, c1)
    b = precompute_market_states(candles, c1)
    assert [s.to_dict() for s in a] == [s.to_dict() for s in b], "重复回放必须一致"
    c = precompute_market_states(candles, c2)
    assert c[0].config_hash == h2


def test_ready_and_missing_reason_warmup():
    """warmup 前 ready=False 且缺失原因非空；warmup 后 ready=True。"""
    candles = series_up(n=150)
    states = precompute_market_states(candles)
    assert states[50].ready is False
    assert states[50].missing_reason is not None
    assert states[149].ready is True
    assert states[149].missing_reason is None


# ── 未收盘 K 线保护 ─────────────────────────────────────────────────────────────

def test_last_unclosed_bar_not_consumed():
    """末根 is_closed=False → ready=False 且不消费其价格数据；已收盘 bar 不受影响。"""
    candles = series_up(n=200)
    closed = precompute_market_states(candles)
    open_series = [dict(c) for c in candles]
    open_series[-1]["is_closed"] = False
    states = precompute_market_states(open_series)
    last = states[-1]
    assert last.ready is False
    assert "未收盘" in (last.missing_reason or "")
    assert last.trend_direction == "unknown"
    assert last.vol_phase == "unknown"
    assert last.candle_strength == "unknown"
    assert last.close is None and last.adx is None and last.atr is None
    assert last.ma20 is None and last.ma60 is None and last.ma120 is None
    # 已收盘 bar 的状态与全收盘序列逐位一致
    for j in range(len(candles) - 1):
        assert states[j] == closed[j], f"已收盘 bar {j} 被未收盘 bar 改变"
    # market_state_at 对未收盘 index 返回 not-ready
    ms = market_state_at(open_series, len(open_series) - 1)
    assert ms.ready is False
    assert "未收盘" in (ms.missing_reason or "")


def test_unclosed_middle_bar_marks_tail_not_ready():
    """中间 bar 未收盘 → 从该 bar 起全部不消费（not-ready），此前已收盘 bar 不变。"""
    candles = series_up(n=200)
    closed = precompute_market_states(candles)
    bad = [dict(c) for c in candles]
    bad[150]["is_closed"] = False
    states = precompute_market_states(bad)
    for i in range(150):
        assert states[i] == closed[i], f"已收盘 bar {i} 被污染"
    for i in range(150, 200):
        assert states[i].ready is False
        assert "未收盘" in (states[i].missing_reason or "")


def test_unclosed_bar_in_batch_prefix_consistency():
    """含未收盘 bar 的序列，其已收盘前缀仍满足逐前缀一致性。"""
    candles = series_up(n=150)
    open_series = [dict(c) for c in candles]
    open_series[-1]["is_closed"] = False
    full = precompute_market_states(open_series)
    prefix = precompute_market_states(open_series[:100])
    for j in range(100):
        assert prefix[j] == full[j]


def test_unclosed_bar_missing_ohlc_not_consumed():
    """缺少 OHLC 的未收盘 bar：不消费其价格数据（无 KeyError），输出占位。"""
    candles = series_up(n=200)
    closed = precompute_market_states(candles)
    open_series = [dict(c) for c in candles[:-1]] + [
        {"time": candles[-1]["time"], "is_closed": False},  # 只有 time/is_closed
    ]
    states = precompute_market_states(open_series)
    last = states[-1]
    assert last.ready is False and last.is_closed is False
    assert "未收盘" in (last.missing_reason or "")
    assert last.close is None and last.ma20 is None and last.adx is None and last.atr is None
    for j in range(len(candles) - 1):
        assert states[j] == closed[j], f"已收盘 bar {j} 被未收盘 bar 改变"


def test_legacy_consumers_unavailable_on_unclosed_last_bar():
    """三个正式消费者在末根未收盘时不产生有效状态：regime -1 / trend -99 / 日报 ValueError。"""
    from app.core.daily_report_analysis import analyze_instrument
    from app.core.regime_classifier import classify_regime_at, precompute_regime_arrays
    from app.core.trend_classifier import classify

    candles = series_up(n=200)
    open_series = [dict(c) for c in candles[:-1]] + [
        {"time": candles[-1]["time"], "is_closed": False},
    ]
    assert classify_regime_at(open_series, 199).id == -1, "regime 未收盘必须不可用（-1）"
    ids, _, _ = precompute_regime_arrays(open_series)
    assert ids[-1] == -1 and ids[-2] != -1
    arr = classify(open_series)
    assert arr[-1] == -99 and arr[-2] != -99, "trend 未收盘必须不可用（-99）"
    with pytest.raises(ValueError):
        analyze_instrument("SYN", "SYNUSD", ["crypto"], open_series, session_timezone="Asia/Shanghai")
    # 全收盘对照：末根可用
    assert classify_regime_at(candles, 199).id != -1
    assert classify(candles)[-1] != -99


def test_legacy_consumers_unavailable_from_unclosed_cutoff():
    """从第一个未收盘 bar 起，regime/trend 消费者全部不可用；此前正常。"""
    from app.core.regime_classifier import precompute_regime_arrays
    from app.core.trend_classifier import classify

    candles = series_up(n=200)
    bad = [dict(c) for c in candles]
    bad[150]["is_closed"] = False
    ids, _, _ = precompute_regime_arrays(bad)
    assert ids[149] != -1
    assert all(v == -1 for v in ids[150:]), "cutoff 起 regime 必须 -1"
    arr = classify(bad)
    assert arr[149] != -99
    assert all(v == -99 for v in arr[150:]), "cutoff 起 trend 必须 -99"


def test_regime_is_stable_false_on_unclosed_last_bar():
    """末根未收盘（仅 time/is_closed、无 OHLC）→ regime_is_stable 必须 False，且不抛 KeyError。"""
    from app.core.regime_classifier import regime_is_stable

    candles = series_up(n=200)
    open_series = [dict(c) for c in candles[:-1]] + [
        {"time": candles[-1]["time"], "is_closed": False},
    ]
    # 修复前：4 个有效 regime 压过 1 个 -1 → 0.8 >= 0.8 → True（静默放行）
    assert regime_is_stable(open_series, window=5, required_ratio=0.8) is False


def test_regime_is_stable_false_on_unclosed_middle_bar():
    """中间未收盘且之后仍有完整 K 线 → regime_is_stable 必须 False。"""
    from app.core.regime_classifier import regime_is_stable

    candles = series_up(n=200)
    bad = [dict(c) for c in candles]
    bad[150]["is_closed"] = False
    assert regime_is_stable(bad, window=5, required_ratio=0.8) is False


def test_analyze_instrument_valueerror_on_unclosed_middle_missing_ohlc():
    """中间缺 OHLC 未收盘、后面仍有完整 K 线 → 受控 ValueError（而非 KeyError）。"""
    from app.core.daily_report_analysis import analyze_instrument

    candles = series_up(n=200)
    bad = [dict(c) for c in candles]
    bad[150] = {"time": candles[150]["time"], "is_closed": False}  # 只有 time/is_closed
    with pytest.raises(ValueError) as excinfo:
        analyze_instrument("SYN", "SYNUSD", ["crypto"], bad, session_timezone="Asia/Shanghai")
    # 错误信息必须包含 symbol 与未收盘/cutoff 含义（区分 ValueError 与 KeyError）
    assert "SYN" in str(excinfo.value)
    assert "未收盘" in str(excinfo.value)


def test_analyze_instrument_unclosed_control_full_closed():
    """全收盘对照：analyze_instrument 正常输出，不抛任何异常（ValueError 与 KeyError 均不可出现）。"""
    from app.core.daily_report_analysis import analyze_instrument

    candles = series_up(n=200)
    item = analyze_instrument("SYN", "SYNUSD", ["crypto"], candles, session_timezone="Asia/Shanghai")
    assert item.symbol == "SYN"
    assert item.close == float(candles[-1]["close"])
    assert item.stage in ("上涨趋势", "下跌趋势", "趋势不明确", "数据不足")


def test_all_states_have_required_fields_and_values():
    """MarketState 字段取值域约束。"""
    candles = series_up(n=300)
    for s in precompute_market_states(candles):
        assert s.trend_direction in ("up", "down", "sideways", "unknown")
        assert s.trend_strength in ("ranging", "weak", "strong", "unknown")
        assert s.vol_level in ("low", "normal", "high", "unknown")
        assert s.vol_phase in ("contraction", "transition", "expansion", "unknown")
        assert s.candle_direction in ("bullish", "bearish", "neutral")
        assert s.candle_strength in ("weak", "normal", "strong", "unknown")
        assert s.volume_state in ("unknown", "low", "normal", "elevated", "high")
        assert s.swing_score in (-2, 0, 2)
        assert s.ma_direction_score in (-1, 0, 1)
        assert s.price_position_score in (-1, 0, 1)
        assert s.bar_index >= 0 and s.bar_time > 0
