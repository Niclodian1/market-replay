from app.core.signal_strength import STRENGTH_LABELS, evaluate_signal_strength, signal_type_label


def test_macd_extreme_divergence_is_confirmed_tier():
    expected = {"15min": 3, "1h": 4, "4h": 5, "1day": 5}
    for timeframe in expected:
        strength = evaluate_signal_strength(
            "macd_extreme_divergence",
            timeframe,
            "signal",
        )
        assert strength.stars == expected[timeframe]
        assert strength.label == STRENGTH_LABELS[expected[timeframe]]


def test_standard_breakouts_scale_with_timeframe():
    expected = {"15min": 2, "1h": 3, "4h": 4, "1day": 5}
    for event_type in ("ma_breakout", "trendline_breakout"):
        actual = {
            timeframe: evaluate_signal_strength(event_type, timeframe).stars
            for timeframe in expected
        }
        assert actual == expected


def test_confirmed_signals_are_one_tier_stronger_with_five_star_cap():
    expected = {"15min": 3, "1h": 4, "4h": 5, "1day": 5}
    for event_type in (
        "ma_retest",
        "macd_extreme_divergence",
        "convergence_breakout",
    ):
        actual = {
            timeframe: evaluate_signal_strength(event_type, timeframe).stars
            for timeframe in expected
        }
        assert actual == expected


def test_unknown_event_uses_conservative_severity_fallback():
    assert evaluate_signal_strength("future_watch", "1day", "watch").stars == 1
    assert evaluate_signal_strength("future_signal", "4h", "signal").stars == 4
    assert evaluate_signal_strength("future_confirmed", "1h", "confirmed").stars == 4
    assert signal_type_label("future_signal", "structure") == "结构"


def test_chanlun_type_labels():
    labels = {
        "chanlun_buy1": "缠论一买",
        "chanlun_buy2": "缠论二买",
        "chanlun_buy3": "缠论三买",
        "chanlun_sell1": "缠论一卖",
        "chanlun_sell2": "缠论二卖",
        "chanlun_sell3": "缠论三卖",
        "chanlun_beichi_buy": "缠论背驰买",
        "chanlun_beichi_sell": "缠论背驰卖",
    }
    for event_type, expected in labels.items():
        assert signal_type_label(event_type, "chanlun") == expected


def test_chanlun_primary_signal_uses_confirmed_strength_scale():
    expected = {"15min": 3, "1h": 4, "4h": 5, "1day": 5}
    for event_type in ("chanlun_buy1", "chanlun_sell1", "chanlun_beichi_buy", "chanlun_beichi_sell"):
        actual = {
            timeframe: evaluate_signal_strength(event_type, timeframe).stars
            for timeframe in expected
        }
        assert actual == expected


def test_chanlun_secondary_signal_uses_standard_strength_scale():
    expected = {"15min": 2, "1h": 3, "4h": 4, "1day": 5}
    for event_type in ("chanlun_buy2", "chanlun_buy3", "chanlun_sell2", "chanlun_sell3"):
        actual = {
            timeframe: evaluate_signal_strength(event_type, timeframe).stars
            for timeframe in expected
        }
        assert actual == expected
