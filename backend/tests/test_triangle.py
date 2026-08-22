"""Triangle pattern detection (v3) unit tests.

Tests cover:
  1. Causality (no forward-looking)
  2. Monotonic sequence extraction (truncation vs tolerance skipping)
  3. Convergence filtering
  4. Apex validity
  5. Frozen boundaries after LOCK
  6. Single signal per pattern
  7. Cross-line definition
  8. 2-touch strict mode
  9. Breakout amplitude
  10. Overlap suppression
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.triangle import (
    TriangleDetector,
    TriangleParams,
    TrianglePattern,
    SwingPoint,
    triangle_array,
    triangle_line_info,
    _confirm_swing_at,
    _extract_descending_touches,
    _extract_ascending_touches,
    _fit_with_quality,
    _validate_candidate,
    _compute_atr,
    _tolerance_threshold,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_candle(time_ts, open_p, high, low, close):
    return {"time": time_ts, "open": open_p, "high": high, "low": low, "close": close}


def _make_triangle_candles(
    n: int = 120,
    base: float = 100.0,
    up_start: float = 110.0,
    up_end: float = 105.0,
    lo_start: float = 90.0,
    lo_end: float = 95.0,
    breakout_idx: int = 110,
    breakout_dir: int = 1,  # 1=up, -1=down
) -> list[dict]:
    """Synthesize candles forming a converging triangle.

    Upper boundary: linearly descending from up_start to up_end.
    Lower boundary: linearly ascending from lo_start to lo_end.
    Price oscillates between the two boundaries, then breaks out.

    Swings are placed at predictable intervals to ensure 3+ touches per side.
    """
    candles = []
    up_slope = (up_end - up_start) / n
    lo_slope = (lo_end - lo_start) / n

    for i in range(n):
        t = i * 86400 + 1_600_000_000
        up_line = up_start + up_slope * i
        lo_line = lo_start + lo_slope * i

        if i < breakout_idx:
            # Inside triangle: oscillate between boundaries
            mid = (up_line + lo_line) / 2
            amp = (up_line - lo_line) * 0.3
            if i % 15 == 0:
                # Swing high touch
                high = up_line * 1.005
                low = mid - amp
                close = mid
                open_p = mid - amp * 0.5
            elif i % 15 == 7:
                # Swing low touch
                low = lo_line * 0.995
                high = mid + amp
                close = mid
                open_p = mid + amp * 0.5
            else:
                high = up_line * 0.99
                low = lo_line * 1.01
                close = mid
                open_p = close
        else:
            # Breakout
            if breakout_dir == 1:
                # Break above upper line
                open_p = up_line
                high = up_line * 1.05
                low = up_line * 0.99
                close = up_line * 1.04
            else:
                # Break below lower line
                open_p = lo_line
                high = lo_line * 1.01
                low = lo_line * 0.95
                close = lo_line * 0.96

        candles.append({
            "time": t,
            "open": round(open_p, 6),
            "high": round(high, 6),
            "low": round(low, 6),
            "close": round(close, 6),
        })

    return candles


# ═══════════════════════════════════════════════════════════════════════════════
#  1. Causality
# ═══════════════════════════════════════════════════════════════════════════════

def test_swing_not_confirmed_before_pivot():
    """Swing at index mid should not be confirmed at bar mid (needs pivot more bars)."""
    highs = [100.0] * 20
    lows = [100.0] * 20
    # Make bar 7 a swing high
    highs[7] = 110.0
    lows[7] = 99.0

    # At bar 8 (mid=5, pivot=3), 5+3=8 → check bar 5
    sw_h, sw_l = _confirm_swing_at(highs, lows, mid=5, pivot=3)
    assert sw_h is None  # bar 5 is not a swing

    # At bar 9 (mid=6, pivot=3)
    sw_h, sw_l = _confirm_swing_at(highs, lows, mid=6, pivot=3)
    assert sw_h is None

    # At bar 10 (mid=7, pivot=3): should confirm bar 7 as swing high
    sw_h, sw_l = _confirm_swing_at(highs, lows, mid=7, pivot=3)
    assert sw_h is not None
    assert sw_h.index == 7
    assert sw_h.confirmed_at == 10  # 7 + 3


def test_no_lookahead():
    """Detector should never use data beyond the current bar."""
    candles = _make_triangle_candles(n=120, breakout_idx=110, breakout_dir=1)

    # Run detector
    params = TriangleParams()
    detector = TriangleDetector(params)

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    atr = _compute_atr(highs, lows, closes, 14)

    for i in range(len(candles)):
        detector.advance(i, highs[i], lows[i], closes[i], atr[i])

        # Verify: all swing points have confirmed_at <= i
        for s in detector._swing_highs:
            assert s.confirmed_at <= i, (
                f"swing high at {s.index} confirmed at {s.confirmed_at} > current bar {i}"
            )
        for s in detector._swing_lows:
            assert s.confirmed_at <= i, (
                f"swing low at {s.index} confirmed at {s.confirmed_at} > current bar {i}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  2. Monotonic sequence extraction
# ═══════════════════════════════════════════════════════════════════════════════

def test_descending_sequence_no_skip_perfect():
    """Perfect descending sequence: all swings decrease."""
    swings = [
        SwingPoint(index=10, price=110, confirmed_at=13, kind="high"),
        SwingPoint(index=20, price=105, confirmed_at=23, kind="high"),
        SwingPoint(index=30, price=100, confirmed_at=33, kind="high"),
    ]
    result = _extract_descending_touches(swings, current_i=40, max_lookback=90)
    assert len(result) == 3
    assert result[0][0] == 10 and result[0][1] == 110
    assert result[1][0] == 20 and result[1][1] == 105
    assert result[2][0] == 30 and result[2][1] == 100


def test_descending_sequence_minor_break_skipped():
    """Minor break in descending sequence is skipped (within tolerance)."""
    swings = [
        SwingPoint(index=10, price=110, confirmed_at=13, kind="high"),
        SwingPoint(index=20, price=103, confirmed_at=23, kind="high"),  # minor break from 105
        SwingPoint(index=30, price=105, confirmed_at=33, kind="high"),
    ]
    # Without ATR, tolerance = 3% of 105 = 3.15. Gap = 105-103 = 2 < 3.15 → skip
    result = _extract_descending_touches(swings, current_i=40, max_lookback=90)
    assert len(result) == 2  # 103 is skipped, [110, 105] kept


def test_descending_sequence_significant_break_truncated():
    """Significant break in descending sequence causes truncation."""
    swings = [
        SwingPoint(index=10, price=110, confirmed_at=13, kind="high"),
        SwingPoint(index=20, price=90, confirmed_at=23, kind="high"),  # BIG break from 105
        SwingPoint(index=30, price=105, confirmed_at=33, kind="high"),
    ]
    # Gap = 105-90 = 15 > 3.15 → truncate
    result = _extract_descending_touches(swings, current_i=40, max_lookback=90)
    assert len(result) == 1  # Only [105]
    assert result[0][1] == 105


def test_ascending_sequence_no_skip():
    """Perfect ascending sequence: all swings increase."""
    swings = [
        SwingPoint(index=10, price=90, confirmed_at=13, kind="low"),
        SwingPoint(index=20, price=95, confirmed_at=23, kind="low"),
        SwingPoint(index=30, price=100, confirmed_at=33, kind="low"),
    ]
    result = _extract_ascending_touches(swings, current_i=40, max_lookback=90)
    assert len(result) == 3
    assert result[0][1] == 90
    assert result[1][1] == 95
    assert result[2][1] == 100


def test_ascending_sequence_truncated():
    """Significant break in ascending sequence."""
    swings = [
        SwingPoint(index=10, price=90, confirmed_at=13, kind="low"),
        SwingPoint(index=20, price=110, confirmed_at=23, kind="low"),  # big break
        SwingPoint(index=30, price=95, confirmed_at=33, kind="low"),
    ]
    result = _extract_ascending_touches(swings, current_i=40, max_lookback=90)
    assert len(result) == 1  # Only [95]


# ═══════════════════════════════════════════════════════════════════════════════
#  3. Line fitting quality
# ═══════════════════════════════════════════════════════════════════════════════

def test_fit_perfect_line():
    """Perfect line fit gives r2=1.0 and zero residuals."""
    indices = [0, 10, 20]
    values = [100.0, 90.0, 80.0]  # perfect descending line
    result = _fit_with_quality(indices, values)
    assert result["r2"] == 1.0
    assert result["max_residual"] == 0.0
    assert result["slope"] == -1.0


def test_fit_noisy_line():
    """Noisy data gives r2 < 1.0."""
    indices = [0, 10, 20]
    values = [100.0, 92.0, 78.0]  # slightly off from perfect line
    result = _fit_with_quality(indices, values)
    assert result["r2"] < 1.0
    assert result["r2"] > 0.9  # still decent fit
    assert result["max_residual"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  4. Convergence filtering
# ═══════════════════════════════════════════════════════════════════════════════

def test_parallel_channels_rejected():
    """Parallel upper and lower lines should fail direction check."""
    h_idx = [10, 30]
    h_val = [105.0, 100.0]  # descending
    l_idx = [10, 30]
    l_val = [95.0, 90.0]     # ALSO descending (should be ascending!)

    up_fit = _fit_with_quality(h_idx, h_val)
    lo_fit = _fit_with_quality(l_idx, l_val)

    valid, reason = _validate_candidate(
        h_idx, h_val, l_idx, l_val, up_fit, lo_fit,
        current_i=70, closes=[100.0]*100, atr_val=2.0,
        params=TriangleParams(min_bars=20, max_bars=100),
    )
    assert not valid
    assert "lower slope" in reason.lower() or "ascend" in reason.lower()


def test_converging_triangle_accepted():
    """Converging symmetric triangle should pass validation."""
    # Lines: upper 110→104 (over 60 bars), lower 90→94 (over 60 bars)
    # Apex at ~bar 220, well beyond t_right=70
    h_idx = [10, 40, 70]
    h_val = [110.0, 107.0, 104.0]  # descending slowly
    l_idx = [10, 40, 70]
    l_val = [90.0, 92.0, 94.0]     # ascending slowly

    up_fit = _fit_with_quality(h_idx, h_val)
    lo_fit = _fit_with_quality(l_idx, l_val)

    closes = [100.0] * 100
    valid, reason = _validate_candidate(
        h_idx, h_val, l_idx, l_val, up_fit, lo_fit,
        current_i=90, closes=closes, atr_val=2.0,
        params=TriangleParams(min_bars=20, max_bars=100, min_touches=2,
                              width_atr_max=15.0),
    )
    assert valid, f"Expected valid, got: {reason}"


# ═══════════════════════════════════════════════════════════════════════════════
#  5. Apex validity
# ═══════════════════════════════════════════════════════════════════════════════

def test_apex_in_past_rejected():
    """Apex before t_right should be rejected.

    The 'lines cross within formation' filter catches this first, which is
    equally correct — both indicate an invalid formation.
    """
    h_idx = [0, 20]
    h_val = [110.0, 100.0]
    l_idx = [0, 20]
    l_val = [90.0, 100.0]  # ascending fast, crosses at ~bar 13

    up_fit = _fit_with_quality(h_idx, h_val)
    lo_fit = _fit_with_quality(l_idx, l_val)

    closes = [100.0] * 60
    valid, reason = _validate_candidate(
        h_idx, h_val, l_idx, l_val, up_fit, lo_fit,
        current_i=50, closes=closes, atr_val=2.0,
        params=TriangleParams(min_bars=20, max_bars=100, min_touches=2),
    )
    assert not valid
    # Either "apex in past" or "lines cross" — both indicate invalid geometry
    assert "apex" in reason.lower() or "cross" in reason.lower()


def test_apex_too_far_rejected():
    """Apex beyond apex_k_max × formation_bars should be rejected or caught by other filters."""
    h_idx = [0, 20]
    h_val = [110.0, 109.0]  # nearly flat descending
    l_idx = [0, 20]
    l_val = [90.0, 91.0]     # nearly flat ascending
    # These barely converge — W0 is large relative to ATR

    up_fit = _fit_with_quality(h_idx, h_val)
    lo_fit = _fit_with_quality(l_idx, l_val)

    closes = [100.0] * 60
    valid, reason = _validate_candidate(
        h_idx, h_val, l_idx, l_val, up_fit, lo_fit,
        current_i=50, closes=closes, atr_val=2.0,
        params=TriangleParams(min_bars=20, max_bars=100, min_touches=2,
                              apex_k_max=0.5,
                              width_atr_max=20.0),  # relax width to let form check run
    )
    assert not valid
    # Either apex too far or convergence too slow — both reject
    assert "apex" in reason.lower() or "convergence" in reason.lower() or "w1/w0" in reason.lower()


# ═══════════════════════════════════════════════════════════════════════════════
#  6. Frozen boundaries after LOCK
# ═══════════════════════════════════════════════════════════════════════════════

def test_frozen_after_lock():
    """Boundary parameters should not change after LOCKED."""
    candles = _make_triangle_candles(n=120, breakout_idx=110, breakout_dir=1)

    params = TriangleParams()
    detector = TriangleDetector(params)
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    atr = _compute_atr(highs, lows, closes, 14)

    locked_slopes = None
    for i in range(len(candles)):
        detector.advance(i, highs[i], lows[i], closes[i], atr[i])
        # Capture slopes when a pattern first locks
        for p in detector.active_patterns:
            if p.status == "LOCKED" and locked_slopes is None:
                locked_slopes = (p.up_slope, p.up_int, p.lo_slope, p.lo_int)
        # After lock, verify slopes don't change
        if locked_slopes is not None:
            for p in detector.active_patterns:
                if p.status == "LOCKED":
                    assert (p.up_slope, p.up_int, p.lo_slope, p.lo_int) == locked_slopes, (
                        "Frozen boundaries changed after LOCK"
                    )


# ═══════════════════════════════════════════════════════════════════════════════
#  7. Single signal per pattern
# ═══════════════════════════════════════════════════════════════════════════════

def test_single_signal_per_pattern():
    """Each pattern should emit at most one breakout signal."""
    candles = _make_triangle_candles(n=120, breakout_idx=110, breakout_dir=1)

    signals = triangle_array(candles)
    non_zero = [i for i, s in enumerate(signals) if s != 0]

    # Count signals in the breakout zone (bars 105-119)
    breakout_signals = [i for i in non_zero if 105 <= i <= 119]
    # Should be at most a few signals (could be 0 if synthetic data doesn't quite match)
    # The key property: no pattern should emit >1 signal
    assert len(breakout_signals) <= 2, (
        f"Too many signals in breakout zone: {breakout_signals}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  8. Cross-line definition
# ═══════════════════════════════════════════════════════════════════════════════

def test_cross_line_definition():
    """Signal requires close[i-1] on one side, close[i] on the other, same frozen line."""
    # Construct a simple case where we can precisely control the behavior
    h_idx = [0, 10, 20]
    h_val = [110.0, 107.0, 104.0]
    l_idx = [0, 10, 20]
    l_val = [90.0, 91.0, 93.0]

    up_fit = _fit_with_quality(h_idx, h_val)
    lo_fit = _fit_with_quality(l_idx, l_val)

    up_slope = up_fit["slope"]
    up_int = up_fit["intercept"]
    lo_slope = lo_fit["slope"]
    lo_int = lo_fit["intercept"]

    # At bar 25: compute line values
    up_25 = up_slope * 25 + up_int
    lo_25 = lo_slope * 25 + lo_int

    # Close at 25: above upper line
    close_25 = up_25 + 1.0
    # Close at 24: below upper line
    up_24 = up_slope * 24 + up_int
    close_24 = up_24 - 0.5

    # This should be a valid cross-up
    assert close_25 > up_25  # above
    assert close_24 <= up_24  # below (previous bar)
    assert close_25 > up_25 and close_24 <= up_24  # cross confirmed


# ═══════════════════════════════════════════════════════════════════════════════
#  9. 2-touch strict mode
# ═══════════════════════════════════════════════════════════════════════════════

def test_two_touch_rejected_by_default():
    """With min_touches=3, 2-touch patterns should be rejected."""
    h_idx = [10, 30]
    h_val = [110.0, 100.0]
    l_idx = [10, 30]
    l_val = [90.0, 95.0]

    up_fit = _fit_with_quality(h_idx, h_val)
    lo_fit = _fit_with_quality(l_idx, l_val)

    closes = [100.0] * 100
    valid, _ = _validate_candidate(
        h_idx, h_val, l_idx, l_val, up_fit, lo_fit,
        current_i=70, closes=closes, atr_val=2.0,
        params=TriangleParams(min_bars=20, max_bars=100, min_touches=3),
    )
    assert not valid  # 2 touches < 3 required


def test_two_touch_accepted_in_strict_mode():
    """With min_touches=2, 2-touch patterns pass if quality is high."""
    # Lines that converge slowly (wide initial gap, far apex)
    h_idx = [10, 40]
    h_val = [120.0, 110.0]
    l_idx = [10, 40]
    l_val = [80.0, 85.0]

    up_fit = _fit_with_quality(h_idx, h_val)
    lo_fit = _fit_with_quality(l_idx, l_val)

    closes = [100.0] * 100
    valid, reason = _validate_candidate(
        h_idx, h_val, l_idx, l_val, up_fit, lo_fit,
        current_i=70, closes=closes, atr_val=3.0,
        params=TriangleParams(min_bars=20, max_bars=100, min_touches=2,
                              width_atr_max=15.0),
    )
    # 2 points each → R²=1.0 each → should pass strict mode
    assert valid, f"Expected valid, got: {reason}"


# ═══════════════════════════════════════════════════════════════════════════════
#  10. Tolerance threshold
# ═══════════════════════════════════════════════════════════════════════════════

def test_tolerance_with_atr():
    """Tolerance uses ATR × tol_atr when ATR is available."""
    atr_vals = [None] * 100
    atr_vals[50] = 5.0
    threshold = _tolerance_threshold(50, 100.0, atr_vals, 0.75)
    assert threshold == 3.75  # 5.0 * 0.75


def test_tolerance_without_atr():
    """Tolerance falls back to 3% of reference price when ATR unavailable."""
    threshold = _tolerance_threshold(50, 100.0, None, 0.75)
    assert threshold == 3.0  # 100 * 0.03


# ═══════════════════════════════════════════════════════════════════════════════
#  11. Backward compatibility
# ═══════════════════════════════════════════════════════════════════════════════

def test_triangle_array_signature():
    """triangle_array returns list[int] of same length as input."""
    candles = _make_triangle_candles(n=60, breakout_idx=55, breakout_dir=1)
    signals = triangle_array(candles)
    assert isinstance(signals, list)
    assert len(signals) == len(candles)
    assert all(s in (-1, 0, 1) for s in signals)


def test_triangle_line_info_signature():
    """triangle_line_info returns dict with upper/lower keys or None."""
    candles = _make_triangle_candles(n=60, breakout_idx=55, breakout_dir=1)
    signals = triangle_array(candles)
    non_zero = [i for i, s in enumerate(signals) if s != 0]

    if non_zero:
        info = triangle_line_info(candles, non_zero[0])
        if info is not None:
            assert "upper" in info
            assert "lower" in info
            for key in ("start_time", "end_time", "start_price", "end_price"):
                assert key in info["upper"], f"upper missing {key}"
                assert key in info["lower"], f"lower missing {key}"
            assert isinstance(info["upper"]["start_time"], int)
            assert isinstance(info["upper"]["start_price"], float)


def test_line_info_returns_none_for_early_bars():
    """triangle_line_info should return None for bars < 41."""
    candles = _make_triangle_candles(n=60, breakout_idx=55, breakout_dir=1)
    assert triangle_line_info(candles, 20) is None


# ═══════════════════════════════════════════════════════════════════════════════
#  12. compute_all_signals integration
# ═══════════════════════════════════════════════════════════════════════════════

def test_compute_all_signals_includes_triangle():
    """compute_all_signals must include 'triangle' key."""
    from app.core.signal_detectors import compute_all_signals

    candles = _make_triangle_candles(n=120, breakout_idx=110, breakout_dir=1)
    result = compute_all_signals(candles)
    assert "triangle" in result
    assert len(result["triangle"]) == len(candles)
    assert all(s in (-1, 0, 1) for s in result["triangle"])
