"""MarketState 适配器测试（M1）。

验证 MarketState → 旧 Regime 六态 / trend 五态 / 日报 stage 的映射边界。
映射正确性的最终证明在 test_market_state_legacy_parity.py（golden 逐字段一致）。
"""
from __future__ import annotations

from app.core.market_state import MarketState, precompute_market_states
from app.core.market_state_adapters import (
    alignment_from_state,
    daily_stage_from_state,
    market_state_to_trend_array,
    regime_components_from_state,
    trend_regime_from_state,
)
from app.core.regime_classifier import _REGIME_MATRIX
from app.core.trend_classifier import REGIME_LABELS


def _ms(**kw) -> MarketState:
    base = dict(
        schema_version="m1-v1", config_hash="test", bar_index=0, bar_time=0, ready=True,
        close=100.0, ma20=100.0, ma60=100.0, ma120=100.0, atr=1.0,
        ma20_prev2=100.0, ma60_prev2=100.0, ma120_prev2=100.0,
    )
    base.update(kw)
    return MarketState(**base)


# ── Regime 六态坐标 ────────────────────────────────────────────────────────────

def test_regime_components_adx_tiers():
    """ADX 档：<20→0，20<=adx<35→1，>=35→2；None→0（与旧 _trend_level 一致）。"""
    assert regime_components_from_state(_ms(adx=None))[0] == 0
    assert regime_components_from_state(_ms(adx=19.9))[0] == 0
    assert regime_components_from_state(_ms(adx=20.0))[0] == 1
    assert regime_components_from_state(_ms(adx=34.9))[0] == 1
    assert regime_components_from_state(_ms(adx=35.0))[0] == 2


def test_regime_components_vol_tier():
    """波动档：pct<0.5→0，>=0.5→1；None→0（与旧 ATR_PCT_SPLIT 一致）。"""
    assert regime_components_from_state(_ms(vol_percentile=None))[1] == 0
    assert regime_components_from_state(_ms(vol_percentile=0.499))[1] == 0
    assert regime_components_from_state(_ms(vol_percentile=0.5))[1] == 1
    assert regime_components_from_state(_ms(vol_percentile=0.9))[1] == 1


def test_regime_components_map_to_known_matrix():
    """所有 (t,v) 组合都必须落入旧 _REGIME_MATRIX（id 0..5，标签存在）。"""
    for adx in (5.0, 25.0, 50.0):
        for pct in (0.2, 0.8):
            t, v = regime_components_from_state(_ms(adx=adx, vol_percentile=pct))
            assert (t, v) in _REGIME_MATRIX
            assert 0 <= _REGIME_MATRIX[(t, v)].id <= 5


# ── MA alignment ───────────────────────────────────────────────────────────────

def test_alignment_from_state_matches_legacy_ranges():
    """alignment：完美多头=+2，基本多头=+1，缠绕=0，基本空头=-1，完美空头=-2。"""
    assert alignment_from_state(_ms(close=110, ma20=105, ma60=100, ma120=95)) == 2
    assert alignment_from_state(_ms(close=106, ma20=105, ma60=100, ma120=102)) == 1
    assert alignment_from_state(_ms(close=102, ma20=105, ma60=100, ma120=95)) == 0
    assert alignment_from_state(_ms(close=94, ma20=95, ma60=100, ma120=98)) == -1
    assert alignment_from_state(_ms(close=90, ma20=95, ma60=100, ma120=105)) == -2
    # 数据不足 → 0
    assert alignment_from_state(_ms(close=None)) == 0
    assert alignment_from_state(_ms(ma120=None)) == 0


# ── trend 五态 ─────────────────────────────────────────────────────────────────

def test_trend_regime_from_state_scores():
    """三维打分 → 五态（与旧 score_to_regime 一致）。"""
    # swing +2, ma +1, price +1 = 4 → 强升
    assert trend_regime_from_state(_ms(swing_score=2, ma_direction_score=1, price_position_score=1)) == 2
    # swing +2, ma +1, price -1 = 2 → 弱升
    assert trend_regime_from_state(_ms(swing_score=2, ma_direction_score=1, price_position_score=-1)) == 1
    # 0 → 震荡
    assert trend_regime_from_state(_ms(swing_score=0, ma_direction_score=0, price_position_score=0)) == 0
    # swing -2 → score=-2 → 弱降 -1
    assert trend_regime_from_state(_ms(swing_score=-2, ma_direction_score=0, price_position_score=0)) == -1
    # swing -2, ma -1, price -1 = -4 → 强降
    assert trend_regime_from_state(_ms(swing_score=-2, ma_direction_score=-1, price_position_score=-1)) == -2
    # 所有可能分数必须落在合法五态
    for s1 in (-2, 0, 2):
        for s2 in (-1, 0, 1):
            for s3 in (-1, 0, 1):
                rid = trend_regime_from_state(_ms(swing_score=s1, ma_direction_score=s2, price_position_score=s3))
                assert rid in (-2, -1, 0, 1, 2)
                assert rid in REGIME_LABELS or rid in (-2, -1, 0, 1, 2)


def test_trend_array_matches_legacy_shape():
    """market_state_to_trend_array：前 60 根 -99、60..63 raw 有效但稳定为 -99、64 起稳定过滤。"""
    from tests.test_market_state import series_up

    candles = series_up(n=120)
    states = precompute_market_states(candles)
    arr = market_state_to_trend_array(states)
    assert len(arr) == 120
    assert all(v == -99 for v in arr[:60])
    assert all(v == -99 for v in arr[60:64]), "稳定过滤窗口不足时为 -99"
    assert all(v in (-99, -2, -1, 0, 1, 2) for v in arr[64:]), "稳定过滤后仍可保持 -99（切换中）"
    assert any(v != -99 for v in arr[64:]), "必须产生有效分类"


# ── 日报 stage ─────────────────────────────────────────────────────────────────

def _daily_ms(close, ma20, ma60, ma120, prev2, atr=2.0, index=121):
    return _ms(
        bar_index=index, close=close, ma20=ma20, ma60=ma60, ma120=ma120,
        ma20_prev2=prev2[0], ma60_prev2=prev2[1], ma120_prev2=prev2[2], atr=atr,
    )


def test_daily_stage_strong_bullish():
    ms = _daily_ms(close=110, ma20=105, ma60=100, ma120=95, prev2=(102.5, 97.5, 93))
    stage, phase, conclusion = daily_stage_from_state(ms)
    assert stage == "上涨趋势"
    assert phase == "continuation"
    assert "强劲上涨" in conclusion


def test_daily_stage_strong_bearish():
    ms = _daily_ms(close=90, ma20=95, ma60=100, ma120=105, prev2=(97.5, 102.5, 107))
    stage, phase, conclusion = daily_stage_from_state(ms)
    assert stage == "下跌趋势"
    assert phase == "continuation"
    assert "强劲下跌" in conclusion


def test_daily_stage_pullback():
    # 中期看多（ma120 above + ma60>ma120 + slopes up），但 ma20 位置 below → pullback
    ms = _daily_ms(close=103, ma20=105, ma60=102, ma120=100, prev2=(106, 100.5, 99))
    stage, phase, _ = daily_stage_from_state(ms)
    assert stage == "上涨趋势"
    assert phase == "pullback"


def test_daily_stage_unclear():
    # 均线交错 → 趋势不明确
    ms = _daily_ms(close=102, ma20=104, ma60=101, ma120=103, prev2=(104, 101, 103))
    stage, phase, conclusion = daily_stage_from_state(ms)
    assert stage == "趋势不明确"
    assert phase == "unclear"
    assert "交错" in conclusion


def test_daily_stage_insufficient_history():
    ms = _daily_ms(close=110, ma20=105, ma60=100, ma120=95, prev2=(102.5, 97.5, 93), index=100)
    stage, phase, conclusion = daily_stage_from_state(ms)
    assert stage == "数据不足"
    assert phase == "unclear"
    assert "均线历史不足" in conclusion


# ── 自定义周期参数透传（复验退回项）───────────────────────────────────────────

def _legacy_regime_id(candles, index, adx_period, atr_period, lookback) -> int:
    """旧公式手算：_adx + _atr_percentile + _trend_level/_vol_level。"""
    from app.core.indicators import _adx, _atr_percentile
    from app.core.regime_classifier import _REGIME_MATRIX, _trend_level, _vol_level

    if len(candles) < lookback or index < lookback:
        return -1
    highs = [float(c["high"]) for c in candles[:index + 1]]
    lows = [float(c["low"]) for c in candles[:index + 1]]
    closes = [float(c["close"]) for c in candles[:index + 1]]
    adx = _adx(highs, lows, closes, adx_period)
    atr_pct = _atr_percentile(highs, lows, closes, atr_period, lookback)
    t = _trend_level(adx[index])
    v = _vol_level(atr_pct[index])
    return _REGIME_MATRIX[(t, v)].id


def test_classify_regime_at_custom_periods_match_legacy_formula():
    """classify_regime_at 的自定义 adx_period/atr_period/lookback 必须透传给统一引擎。"""
    from tests.test_market_state import series_down, series_sideways, series_vol_cycle
    from app.core.regime_classifier import classify_regime_at

    for name, fn in [("down", series_down), ("sideways", series_sideways), ("vol_cycle", series_vol_cycle)]:
        candles = fn(n=300)
        idx = len(candles) - 1
        for periods in [(5, 5, 20), (10, 8, 50), (14, 14, 100)]:
            assert classify_regime_at(candles, idx, *periods).id == _legacy_regime_id(candles, idx, *periods), \
                f"[{name}] {periods}"
    # 自定义参数必须真的产生与默认不同的结果（否则测试空转）
    down = series_down(n=300)
    default_id = classify_regime_at(down, len(down) - 1, 14, 14, 100).id
    custom_id = classify_regime_at(down, len(down) - 1, 5, 5, 20).id
    assert default_id != custom_id, "自定义周期必须改变结果（复验场景：末态 5→4）"


def test_precompute_regime_arrays_custom_periods_match_legacy_formula():
    """precompute_regime_arrays 的自定义周期参数逐位置与旧公式一致。"""
    from tests.test_market_state import series_up
    from app.core.regime_classifier import precompute_regime_arrays

    def legacy_precompute_id(candles, index, adx_period, atr_period, lookback) -> int:
        # 旧 precompute_regime_arrays 语义：无「数据不足→-1」分支，
        # i<lookback 时 atr_pct/adx 为 None → t=0, v=0。
        from app.core.indicators import _adx, _atr_percentile
        from app.core.regime_classifier import _REGIME_MATRIX, _trend_level, _vol_level

        highs = [float(c["high"]) for c in candles[:index + 1]]
        lows = [float(c["low"]) for c in candles[:index + 1]]
        closes = [float(c["close"]) for c in candles[:index + 1]]
        adx = _adx(highs, lows, closes, adx_period)
        atr_pct = _atr_percentile(highs, lows, closes, atr_period, lookback)
        t = _trend_level(adx[index])
        v = _vol_level(atr_pct[index])
        return _REGIME_MATRIX[(t, v)].id

    candles = series_up(n=250)
    ids_custom, _, _ = precompute_regime_arrays(candles, 5, 5, 20)
    for i in range(0, 250):
        assert ids_custom[i] == legacy_precompute_id(candles, i, 5, 5, 20), f"index {i}"
    ids_default, _, _ = precompute_regime_arrays(candles, 14, 14, 100)
    assert any(a != b for a, b in zip(ids_custom, ids_default)), "自定义周期必须改变结果"
