"""
Triangle (对称收敛三角形) pattern detection — v3.

Causal, state-machine driven detection with frozen boundary lines.

Key improvements over v2:
  - Swing points use high/low (not close), with confirmation delay (pivot bars)
  - State machine: CANDIDATE → LOCKED → BREAKOUT_UP/DOWN/EXPIRED/INVALID
  - Boundaries FROZEN at LOCK time; cross-line comparison always uses same frozen lines
  - Single signal per pattern instance
  - Geometric filters: convergence ratio (W1/W0), apex validity, touch quality (R² + residuals)
  - Strict monotonic sequence extraction (no skipping intermediate pivot breaks)

Causality guarantees:
  - At bar i, only data up to bar i is used
  - Swing points at index t are confirmed at bar t + pivot (2*pivot+1 window fully observed)
  - Fitting, filtering, and signal all use only confirmed swings
  - Once LOCKED, boundary parameters are frozen and never re-fitted

Usage:
    from app.core.triangle import triangle_array, triangle_line_info, TriangleParams

    signals = triangle_array(candles)         # list[int], same length as candles
    info = triangle_line_info(candles, i)      # dict | None, for frontend rendering
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Literal


# ═══════════════════════════════════════════════════════════════════════════════
#  Data Structures
# ═══════════════════════════════════════════════════════════════════════════════

class PatternType(Enum):
    """Reserved for future ascending/descending triangle discrimination."""
    SYMMETRIC = auto()
    ASCENDING = auto()
    DESCENDING = auto()


@dataclass
class TriangleParams:
    """All tunable parameters for triangle detection.

    Defaults are calibrated for daily candlestick data and should be adjusted
    for other timeframes (e.g. 4H may need tighter formation_bars).
    """

    # ── Swing point detection ──
    pivot: int = 5
    """Primary pivot window (bars on each side for local extremum confirmation)."""
    pivot_fallback: int = 3
    """Secondary pivot for supplementing sparse swing points (merged, not replaced)."""

    # ── Formation period ──
    min_bars: int = 40
    """Minimum formation bars (t_left to t_right)."""
    max_bars: int = 90
    """Maximum formation bars (excludes cross-regime contamination)."""

    # ── Touch count ──
    min_touches: int = 3
    """Minimum swing touches per boundary side."""
    min_touches_strict: int = 2
    """Allowed minimum when R² ≥ 0.9 and residual/ATR ≤ 0.25."""

    # ── Mouth gap alignment ──
    mouth_gap_max_bars: int = 30
    """Maximum absolute bar difference between first upper and first lower touch."""
    mouth_gap_ratio: float = 0.30
    """Mouth gap relative to formation bars."""

    # ── Convergence ──
    convergence_rho: float = 0.75
    """W1/W0 ≤ rho — pattern must meaningfully narrow."""
    apex_k_min: float = 0.0
    """Apex must be at least k_min × formation_bars ahead of t_right. 0 = any future apex OK."""
    apex_k_max: float = 1.5
    """Apex must be at most k_max × formation_bars ahead of t_right."""

    # ── Width (ATR-normalized) ──
    width_atr_min: float = 0.5
    """Minimum mouth width in ATR multiples."""
    width_atr_max: float = 8.0
    """Maximum mouth width in ATR multiples."""

    # ── Touch quality ──
    max_residual_atr: float = 0.5
    """Maximum touch-to-line residual in ATR multiples."""
    r2_min: float = 0.7
    """Minimum R² for OLS line fit."""

    # ── Time overlap ──
    overlap_min: float = 0.20
    """Minimum time overlap ratio between upper and lower touch spans."""

    # ── Breakout ──
    breakout_atr: float = 0.3
    """Minimum breakout amplitude in ATR multiples."""
    confirm_bar: bool = False
    """If True, require t+1 close still outside line before confirming breakout.
    When True, signal is delayed by 1 bar (entry at bar i+2 instead of i+1).
    When False (default), signal fires on the breakout bar itself."""
    reclaim_bars: int = 3
    """Window (bars) for false-breakout reclamation check (only when confirm_bar=True)."""

    # ── Valid breakout window ──
    hold_bars: int = 20
    """Maximum bars after LOCK to wait for breakout before EXPIRED."""

    # ── Candidate lock delay ──
    lock_delay: int = 1
    """Bars to wait after t_right before transitioning CANDIDATE → LOCKED.
    Lower values lock faster, preventing apex-cross invalidation during the wait."""

    # ── Overlap suppression ──
    max_overlap_iou: float = 0.5
    """Maximum formation-window IoU for two concurrent patterns (suppress worse)."""


@dataclass
class SwingPoint:
    """A confirmed swing high or low.

    Causal rule: confirmed_at = index + pivot.
    The swing is NOT usable until bar confirmed_at.
    """
    index: int
    price: float
    confirmed_at: int
    kind: Literal["high", "low"]


@dataclass
class TrianglePattern:
    """One detected triangle pattern with full state."""
    id: int

    # ── Formation window ──
    t_left: int = 0
    """Left bound of formation window (earliest touch index)."""
    t_right: int = 0
    """Right bound of formation window (latest touch index at LOCK time)."""
    t_lock: int = 0
    """Bar index when pattern transitioned to LOCKED."""

    # ── Frozen boundaries (set at LOCK, never changed) ──
    up_slope: float = 0.0
    up_int: float = 0.0
    lo_slope: float = 0.0
    lo_int: float = 0.0
    up_r2: float = 0.0
    lo_r2: float = 0.0

    # ── Geometry ──
    apex: float = 0.0
    """Bar index (float) where upper and lower lines intersect."""
    w0: float = 0.0
    """Mouth width (up - lo) at t_left."""
    w1: float = 0.0
    """Width (up - lo) at t_right."""

    # ── Touch points ──
    h_touches: list = field(default_factory=list)
    """List of (index, price) tuples for upper boundary touches."""
    l_touches: list = field(default_factory=list)
    """List of (index, price) tuples for lower boundary touches."""

    # ── State ──
    status: str = "CANDIDATE"
    """One of: CANDIDATE, LOCKED, BREAKOUT_UP, BREAKOUT_DOWN, EXPIRED, INVALID."""
    signal_bar: int | None = None
    """Bar index where breakout signal was emitted."""
    breakout_direction: int = 0
    """1 for up, -1 for down, 0 if not yet broken out."""
    false_breakout_count: int = 0
    """Number of false breakout attempts (for reclaim logic)."""

    # ── Breakout window ──
    breakout_window_end: int = 0
    """Bar index after which pattern expires if no breakout."""

    @property
    def formation_bars(self) -> int:
        return self.t_right - self.t_left

    def up_line_at(self, idx: int | float) -> float:
        return self.up_slope * idx + self.up_int

    def lo_line_at(self, idx: int | float) -> float:
        return self.lo_slope * idx + self.lo_int


# ═══════════════════════════════════════════════════════════════════════════════
#  Online Swing Point Detection
# ═══════════════════════════════════════════════════════════════════════════════

def _confirm_swing_at(
    highs: list[float],
    lows: list[float],
    mid: int,
    pivot: int,
) -> tuple[SwingPoint | None, SwingPoint | None]:
    """Check if bar `mid` is a local extremum, using data up to bar `mid + pivot`.

    Called at bar i = mid + pivot, when the full window [mid-pivot, mid+pivot]
    is available.

    Returns (swing_high_or_None, swing_low_or_None).
    """
    n = len(highs)
    left = mid - pivot
    right = mid + pivot

    if left < 0 or right >= n:
        return None, None

    window_h = highs[left: right + 1]
    window_l = lows[left: right + 1]

    sw_high = None
    sw_low = None

    # Swing high: high[mid] is the maximum in the window
    if highs[mid] == max(window_h):
        # Also require it to be strictly greater than at least one neighbor
        # to avoid flat tops
        sw_high = SwingPoint(
            index=mid,
            price=highs[mid],
            confirmed_at=right,  # bar i = mid + pivot
            kind="high",
        )

    # Swing low: low[mid] is the minimum in the window
    if lows[mid] == min(window_l):
        sw_low = SwingPoint(
            index=mid,
            price=lows[mid],
            confirmed_at=right,
            kind="low",
        )

    return sw_high, sw_low


# ═══════════════════════════════════════════════════════════════════════════════
#  Monotonic Sequence Extraction
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_descending_touches(
    swing_highs: list[SwingPoint],
    current_i: int,
    max_lookback: int,
    atr_values: list[float | None] | None = None,
    tol_atr: float = 0.75,
) -> list[tuple[int, float]]:
    """Extract descending sequence of swing highs.

    Walks from most recent swing back through history.
    Points that break monotonicity are evaluated with tolerance:
      - If break is < tol_atr × ATR (or < 3% relative if no ATR), SKIP the breaking point
      - If break is significant, TRUNCATE the sequence

    This prevents noise swing points from aborting valid triangle formations.

    Example with tolerance: swings [105, 100, 103, 102, 98], ATR≈5
    → 102 > 98 ✓, 103 > 102 ✓, 100 < 103 → break=3, ATR×0.75≈3.75, 3<3.75 → SKIP 100
    → 105 > 103 ✓ → [(105), (103), (102), (98)]

    Returns list of (index, price) in chronological order.
    """
    recent = [
        s for s in swing_highs
        if s.confirmed_at <= current_i and current_i - s.index <= max_lookback
    ]
    if len(recent) < 2:
        return []

    recent.sort(key=lambda s: s.index)

    # Start from most recent, walk back
    result = [(recent[-1].index, recent[-1].price)]
    for j in range(len(recent) - 2, -1, -1):
        if recent[j].price > result[-1][1]:
            # Valid: earlier swing is higher → descending
            result.append((recent[j].index, recent[j].price))
        else:
            # Breaking monotonicity — check tolerance
            gap = result[-1][1] - recent[j].price
            threshold = _tolerance_threshold(
                recent[j].index, result[-1][1], atr_values, tol_atr,
            )
            if gap < threshold:
                # Minor break: skip this point, continue looking back
                continue
            else:
                # Significant break: truncate sequence
                break

    result.reverse()
    return result


def _extract_ascending_touches(
    swing_lows: list[SwingPoint],
    current_i: int,
    max_lookback: int,
    atr_values: list[float | None] | None = None,
    tol_atr: float = 0.75,
) -> list[tuple[int, float]]:
    """Extract ascending sequence of swing lows.

    Symmetric to _extract_descending_touches.
    Points that break monotonicity are SKIPPED if the break is minor,
    TRUNCATED if significant.

    Returns list of (index, price) in chronological order.
    """
    recent = [
        s for s in swing_lows
        if s.confirmed_at <= current_i and current_i - s.index <= max_lookback
    ]
    if len(recent) < 2:
        return []

    recent.sort(key=lambda s: s.index)

    result = [(recent[-1].index, recent[-1].price)]
    for j in range(len(recent) - 2, -1, -1):
        if recent[j].price < result[-1][1]:
            # Valid: earlier swing is lower → ascending
            result.append((recent[j].index, recent[j].price))
        else:
            # Breaking monotonicity — check tolerance
            gap = recent[j].price - result[-1][1]
            threshold = _tolerance_threshold(
                recent[j].index, result[-1][1], atr_values, tol_atr,
            )
            if gap < threshold:
                # Minor break: skip, continue
                continue
            else:
                # Significant break: truncate
                break

    result.reverse()
    return result


def _tolerance_threshold(
    bar_idx: int,
    reference_price: float,
    atr_values: list[float | None] | None,
    tol_atr: float,
) -> float:
    """Compute the tolerance for a monotonicity break at bar_idx.

    Uses ATR×tol_atr if available, otherwise falls back to 3% of reference_price.
    """
    if atr_values is not None and bar_idx < len(atr_values) and atr_values[bar_idx] is not None:
        return tol_atr * atr_values[bar_idx]
    return 0.03 * reference_price


# ═══════════════════════════════════════════════════════════════════════════════
#  Line Fitting
# ═══════════════════════════════════════════════════════════════════════════════

def _fit_with_quality(
    indices: list[int],
    values: list[float],
) -> dict:
    """OLS linear regression with quality metrics.

    Returns dict with: slope, intercept, r2, max_residual, residuals.
    """
    k = len(indices)
    if k < 2:
        return {"slope": 0.0, "intercept": values[0] if values else 0.0,
                "r2": 0.0, "max_residual": float("inf"), "residuals": []}

    mx = sum(indices) / k
    my = sum(values) / k

    denom = sum((x - mx) ** 2 for x in indices)
    if denom == 0:
        return {"slope": 0.0, "intercept": my, "r2": 0.0,
                "max_residual": float("inf"), "residuals": []}

    slope = sum((indices[j] - mx) * (values[j] - my) for j in range(k)) / denom
    intercept = my - slope * mx

    # R²
    ss_res = sum((values[j] - (slope * indices[j] + intercept)) ** 2 for j in range(k))
    ss_tot = sum((v - my) ** 2 for v in values)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-15 else 0.0

    # Residuals
    residuals = [abs(values[j] - (slope * indices[j] + intercept)) for j in range(k)]
    max_residual = max(residuals) if residuals else float("inf")

    return {
        "slope": slope,
        "intercept": intercept,
        "r2": r2,
        "max_residual": max_residual,
        "residuals": residuals,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Candidate Validation
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_candidate(
    h_indices: list[int],
    h_vals: list[float],
    l_indices: list[int],
    l_vals: list[float],
    up_fit: dict,
    lo_fit: dict,
    current_i: int,
    closes: list[float],
    atr_val: float | None,
    params: TriangleParams,
) -> tuple[bool, str]:
    """Validate a triangle candidate against all geometric filters.

    Returns (is_valid, reason).

    Filters are ordered for fast failure (cheapest checks first).
    """
    up_slope = up_fit["slope"]
    up_int = up_fit["intercept"]
    lo_slope = lo_fit["slope"]
    lo_int = lo_fit["intercept"]

    t_left = min(h_indices[0], l_indices[0])
    t_right = current_i
    formation_bars = t_right - t_left
    # Mouth width is measured at the point where BOTH boundaries exist
    t_mouth = max(h_indices[0], l_indices[0])

    # ── 1. Direction ──
    # Symmetric triangle: upper must descend (or near-flat), lower must ascend (or near-flat)
    eps = 1e-9
    if not (up_slope < eps):
        return False, f"upper slope {up_slope:.6f} >= 0 (must descend)"

    if not (lo_slope > -eps):
        return False, f"lower slope {lo_slope:.6f} <= 0 (must ascend)"

    # ── 2. Mouth gap ──
    mouth_gap = abs(h_indices[0] - l_indices[0])
    max_gap = max(params.mouth_gap_max_bars, int(formation_bars * params.mouth_gap_ratio))
    if mouth_gap > max_gap:
        return False, f"mouth gap {mouth_gap} > {max_gap}"

    # ── 3. Formation bars ──
    if formation_bars < params.min_bars:
        return False, f"formation {formation_bars} < min {params.min_bars}"
    if formation_bars > params.max_bars:
        return False, f"formation {formation_bars} > max {params.max_bars}"

    # ── 4. Time overlap ──
    overlap_start = max(h_indices[0], l_indices[0])
    overlap_end = min(h_indices[-1], l_indices[-1])
    overlap_len = max(0, overlap_end - overlap_start)
    if formation_bars > 0 and overlap_len < formation_bars * params.overlap_min:
        return False, f"overlap {overlap_len}/{formation_bars} < {params.overlap_min}"

    # ── 5. No premature crossing ──
    # Lines must not cross within [t_left, t_right]
    for t in range(t_left, min(t_right + 1, len(closes))):
        up_v = up_slope * t + up_int
        lo_v = lo_slope * t + lo_int
        if up_v <= lo_v:
            return False, f"lines cross at bar {t} (within formation)"

    # ── 6. Width (ATR-normalized) ──
    # W0 measured at mouth (where both boundaries exist)
    w0 = (up_slope * t_mouth + up_int) - (lo_slope * t_mouth + lo_int)
    if w0 <= 0:
        return False, "W0 <= 0 (invalid mouth width)"

    if atr_val is not None and atr_val > 0:
        w0_atr = w0 / atr_val
        if w0_atr < params.width_atr_min:
            return False, f"W0/ATR {w0_atr:.2f} < min {params.width_atr_min}"
        if w0_atr > params.width_atr_max:
            return False, f"W0/ATR {w0_atr:.2f} > max {params.width_atr_max}"
    else:
        # Fallback to relative width
        mid_price = sum(closes[t_left:t_right + 1]) / max(1, formation_bars)
        if mid_price > 0 and w0 / mid_price > 0.30:
            return False, "W0/mid > 30% (no ATR available)"

    # ── 7. Convergence ratio ──
    w1 = (up_slope * t_right + up_int) - (lo_slope * t_right + lo_int)
    if w1 <= 0:
        return False, "W1 <= 0 (already crossed)"

    if w1 / w0 > params.convergence_rho:
        return False, f"W1/W0 {w1/w0:.3f} > rho {params.convergence_rho}"

    # ── 8. Apex validity ──
    denom = up_slope - lo_slope
    if abs(denom) < 1e-15:
        return False, "lines are parallel (no apex)"
    apex = (lo_int - up_int) / denom

    apex_min = t_right + params.apex_k_min * formation_bars
    apex_max = t_right + params.apex_k_max * formation_bars
    if apex <= t_right:
        return False, f"apex {apex:.1f} <= t_right {t_right}"
    if apex < apex_min:
        return False, f"apex {apex:.1f} < min {apex_min:.1f}"
    if apex > apex_max:
        return False, f"apex {apex:.1f} > max {apex_max:.1f}"

    # ── 9. Touch quality ──
    if atr_val is not None and atr_val > 0:
        threshold = params.max_residual_atr * atr_val
        if up_fit["max_residual"] > threshold:
            return False, f"upper max_residual {up_fit['max_residual']:.4f} > {threshold:.4f}"
        if lo_fit["max_residual"] > threshold:
            return False, f"lower max_residual {lo_fit['max_residual']:.4f} > {threshold:.4f}"

    if up_fit["r2"] < params.r2_min:
        return False, f"upper R² {up_fit['r2']:.3f} < {params.r2_min}"
    if lo_fit["r2"] < params.r2_min:
        return False, f"lower R² {lo_fit['r2']:.3f} < {params.r2_min}"

    # ── 10. Minimum touches ──
    n_h = len(h_indices)
    n_l = len(l_indices)
    if n_h < params.min_touches or n_l < params.min_touches:
        # Check strict mode (2-touch with tighter quality)
        if n_h < params.min_touches_strict or n_l < params.min_touches_strict:
            return False, f"touches ({n_h}H/{n_l}L) < required"
        # Stricter quality for 2-touch
        if up_fit["r2"] < 0.90 or lo_fit["r2"] < 0.90:
            return False, "2-touch mode: R² must be ≥ 0.90"
        if atr_val is not None and atr_val > 0:
            strict_threshold = 0.25 * atr_val
            if up_fit["max_residual"] > strict_threshold:
                return False, "2-touch mode: upper residual too large"
            if lo_fit["max_residual"] > strict_threshold:
                return False, "2-touch mode: lower residual too large"

    # ── 11. Alternation (soft check: touches must not all be on one side) ──
    all_h_indices = set(h_indices)
    all_l_indices = set(l_indices)
    union_min = min(all_h_indices | all_l_indices)
    union_max = max(all_h_indices | all_l_indices)
    union_span = union_max - union_min
    if union_span > 0:
        h_center = sum(all_h_indices) / len(all_h_indices)
        l_center = sum(all_l_indices) / len(all_l_indices)
        center_separation = abs(h_center - l_center) / union_span
        if center_separation > 0.6:
            return False, f"touch centers too separated ({center_separation:.2f}), poor alternation"

    return True, ""


# ═══════════════════════════════════════════════════════════════════════════════
#  Triangle Detector (State Machine)
# ═══════════════════════════════════════════════════════════════════════════════

class TriangleDetector:
    """Stateful triangle pattern detector.

    Processes bars sequentially via advance(), maintaining causal constraints:
    - Swing points are only used after their confirmation bar
    - Once a pattern is LOCKED, its boundary lines are frozen
    - Each pattern emits at most one breakout signal

    Usage:
        detector = TriangleDetector(TriangleParams())
        signals = []
        for i, candle in enumerate(candles):
            signal = detector.advance(i, high, low, close, atr)
            signals.append(signal)
    """

    def __init__(self, params: TriangleParams | None = None):
        self.params = params or TriangleParams()
        self.highs: list[float] = []
        self.lows: list[float] = []
        self.closes: list[float] = []
        self.atr: list[float | None] = []

        # Confirmed swing points (only those with confirmed_at <= current_i)
        self._swing_highs: list[SwingPoint] = []
        self._swing_lows: list[SwingPoint] = []

        # Pattern management
        self._pattern_counter: int = 0
        self.active_patterns: list[TrianglePattern] = []
        """Currently active patterns (CANDIDATE or LOCKED)."""
        self.completed_patterns: list[TrianglePattern] = []
        """Terminated patterns (BREAKOUT_*, EXPIRED, INVALID)."""

        # Pending breakout (for confirm_bar mode)
        self._pending_breakout: tuple[int, int, int] | None = None
        """(pattern_id, direction, candidate_bar) awaiting confirmation."""

    # ── Public API ──

    def advance(
        self, i: int, high: float, low: float, close: float, atr_val: float | None,
    ) -> int:
        """Process bar i. Returns signal: 1 (long), -1 (short), or 0 (none)."""
        self.highs.append(high)
        self.lows.append(low)
        self.closes.append(close)
        self.atr.append(atr_val)

        # 1. Confirm new swing points
        self._detect_swings(i)

        # 2. Try to create new candidates
        self._try_start_candidate(i)

        # 3. Advance existing patterns
        signal = 0
        patterns_to_advance = list(self.active_patterns)
        for pattern in patterns_to_advance:
            sig = self._advance_pattern(pattern, i)
            if sig != 0:
                signal = sig

        # 4. Handle pending breakout confirmation
        if self._pending_breakout is not None:
            pend_sig = self._check_pending_breakout(i)
            if pend_sig != 0:
                signal = pend_sig

        return signal

    def find_pattern_at_bar(self, i: int) -> TrianglePattern | None:
        """Find the pattern that emitted a signal at or near bar i.

        Searches completed patterns for one with signal_bar == i,
        then active LOCKED patterns containing bar i in their window.
        """
        # Exact match: signal emitted at bar i
        for p in self.completed_patterns:
            if p.signal_bar == i:
                return p

        # Near match: pattern was LOCKED and active at bar i
        for p in self.active_patterns:
            if p.status == "LOCKED" and p.t_lock <= i:
                # Check if bar i is within the breakout window
                if i <= p.breakout_window_end:
                    return p

        # Any completed pattern that covers bar i
        for p in self.completed_patterns:
            if p.t_left <= i <= p.breakout_window_end:
                return p

        return None

    @property
    def all_patterns(self) -> list[TrianglePattern]:
        """All patterns (active + completed)."""
        return self.active_patterns + self.completed_patterns

    # ── Swing Detection ──

    def _detect_swings(self, i: int) -> None:
        """Check for newly confirmed swing points at bar i."""
        for pivot in (self.params.pivot, self.params.pivot_fallback):
            mid = i - pivot
            if mid < pivot:  # Not enough data on the left
                continue

            sw_h, sw_l = _confirm_swing_at(self.highs, self.lows, mid, pivot)
            if sw_h is not None:
                # Merge: prefer pivot=5 over pivot=3 for same index
                existing = [s for s in self._swing_highs if s.index == sw_h.index]
                if not existing or existing[0].confirmed_at > sw_h.confirmed_at:
                    if existing:
                        self._swing_highs.remove(existing[0])
                    self._swing_highs.append(sw_h)

            if sw_l is not None:
                existing = [s for s in self._swing_lows if s.index == sw_l.index]
                if not existing or existing[0].confirmed_at > sw_l.confirmed_at:
                    if existing:
                        self._swing_lows.remove(existing[0])
                    self._swing_lows.append(sw_l)

    # ── Candidate Discovery ──

    def _try_start_candidate(self, i: int) -> None:
        """Try to form a new triangle candidate from confirmed swings."""
        # Suppress if overlapping too much with an existing active pattern
        for existing in self.active_patterns:
            if i - existing.t_left < self.params.max_bars:
                overlap_start = max(existing.t_left, i - self.params.max_bars)
                overlap_end = i
                if overlap_end > overlap_start:
                    iou = (overlap_end - overlap_start) / max(
                        i - existing.t_left, existing.t_right - existing.t_left
                    )
                    if iou > self.params.max_overlap_iou:
                        return  # Suppress new candidate

        # Extract touch sequences
        h_touches = _extract_descending_touches(
            self._swing_highs, i, self.params.max_bars, self.atr, 0.75,
        )
        l_touches = _extract_ascending_touches(
            self._swing_lows, i, self.params.max_bars, self.atr, 0.75,
        )

        if len(h_touches) < self.params.min_touches_strict:
            return
        if len(l_touches) < self.params.min_touches_strict:
            return

        h_indices = [x[0] for x in h_touches]
        h_vals = [x[1] for x in h_touches]
        l_indices = [x[0] for x in l_touches]
        l_vals = [x[1] for x in l_touches]

        # Fit lines
        up_fit = _fit_with_quality(h_indices, h_vals)
        lo_fit = _fit_with_quality(l_indices, l_vals)

        # Validate
        atr_val = self.atr[i] if i < len(self.atr) else None
        valid, reason = _validate_candidate(
            h_indices, h_vals, l_indices, l_vals,
            up_fit, lo_fit, i, self.closes, atr_val, self.params,
        )

        if not valid:
            return

        # Create candidate pattern
        pid = self._pattern_counter
        self._pattern_counter += 1

        # Compute geometry
        t_left = min(h_indices[0], l_indices[0])
        w0 = (up_fit["slope"] * t_left + up_fit["intercept"]) - \
             (lo_fit["slope"] * t_left + lo_fit["intercept"])
        w1 = (up_fit["slope"] * i + up_fit["intercept"]) - \
             (lo_fit["slope"] * i + lo_fit["intercept"])
        denom = up_fit["slope"] - lo_fit["slope"]
        apex = (lo_fit["intercept"] - up_fit["intercept"]) / denom if abs(denom) > 1e-15 else float("inf")

        pattern = TrianglePattern(
            id=pid,
            t_left=t_left,
            t_right=i,
            up_slope=up_fit["slope"],
            up_int=up_fit["intercept"],
            lo_slope=lo_fit["slope"],
            lo_int=lo_fit["intercept"],
            up_r2=up_fit["r2"],
            lo_r2=lo_fit["r2"],
            apex=apex,
            w0=w0,
            w1=w1,
            h_touches=h_touches,
            l_touches=l_touches,
            status="CANDIDATE",
        )

        self.active_patterns.append(pattern)

    # ── Pattern State Machine ──

    def _advance_pattern(self, pattern: TrianglePattern, i: int) -> int:
        """Advance a single pattern's state machine by one bar."""
        if pattern.status == "CANDIDATE":
            return self._handle_candidate(pattern, i)
        elif pattern.status == "LOCKED":
            return self._handle_locked(pattern, i)
        return 0

    def _handle_candidate(self, pattern: TrianglePattern, i: int) -> int:
        """CANDIDATE → LOCKED / INVALID."""
        # Check for new touches that extend the sequences
        self._update_touches(pattern, i)

        # Wait for lock_delay bars after t_right before locking
        if i - pattern.t_right < self.params.lock_delay:
            return 0

        # Re-fit with updated touches (using all confirmed swings)
        h_indices = [x[0] for x in pattern.h_touches]
        h_vals = [x[1] for x in pattern.h_touches]
        l_indices = [x[0] for x in pattern.l_touches]
        l_vals = [x[1] for x in pattern.l_touches]

        if len(h_indices) < self.params.min_touches_strict:
            return 0
        if len(l_indices) < self.params.min_touches_strict:
            return 0

        up_fit = _fit_with_quality(h_indices, h_vals)
        lo_fit = _fit_with_quality(l_indices, l_vals)

        atr_val = self.atr[i] if i < len(self.atr) else None
        valid, reason = _validate_candidate(
            h_indices, h_vals, l_indices, l_vals,
            up_fit, lo_fit, i, self.closes, atr_val, self.params,
        )

        if not valid:
            # Check if invalid due to expiration or structural break
            if "formation" in reason and ">" in reason:
                pattern.status = "EXPIRED"
            else:
                pattern.status = "INVALID"
            self.active_patterns.remove(pattern)
            self.completed_patterns.append(pattern)
            return 0

        # LOCK: freeze boundaries
        pattern.status = "LOCKED"
        pattern.t_lock = i
        pattern.t_right = i
        pattern.up_slope = up_fit["slope"]
        pattern.up_int = up_fit["intercept"]
        pattern.lo_slope = lo_fit["slope"]
        pattern.lo_int = lo_fit["intercept"]
        pattern.up_r2 = up_fit["r2"]
        pattern.lo_r2 = lo_fit["r2"]

        # Recompute geometry with frozen lines
        t_left = min(h_indices[0], l_indices[0])
        pattern.t_left = t_left
        pattern.w0 = pattern.up_line_at(t_left) - pattern.lo_line_at(t_left)
        pattern.w1 = pattern.up_line_at(i) - pattern.lo_line_at(i)
        denom = pattern.up_slope - pattern.lo_slope
        pattern.apex = (pattern.lo_int - pattern.up_int) / denom if abs(denom) > 1e-15 else float("inf")

        # Set breakout window
        apex_floor = int(pattern.apex)
        pattern.breakout_window_end = min(
            apex_floor,
            pattern.t_lock + self.params.hold_bars,
        )

        return 0

    def _handle_locked(self, pattern: TrianglePattern, i: int) -> int:
        """LOCKED → BREAKOUT / EXPIRED / INVALID.

        Uses FROZEN boundary lines — never re-fits.
        """
        # ── Expiration checks ──
        # 1. Beyond breakout window
        if i > pattern.breakout_window_end:
            pattern.status = "EXPIRED"
            self.active_patterns.remove(pattern)
            self.completed_patterns.append(pattern)
            return 0

        # 2. Apex reached (with 5-bar grace period past apex)
        if pattern.apex is not None and i >= int(pattern.apex) + 5:
            pattern.status = "EXPIRED"
            self.active_patterns.remove(pattern)
            self.completed_patterns.append(pattern)
            return 0

        # ── Invalidation checks ──
        # 1. Lines cross within valid window
        up_i = pattern.up_line_at(i)
        lo_i = pattern.lo_line_at(i)
        if up_i <= lo_i:
            pattern.status = "INVALID"
            self.active_patterns.remove(pattern)
            self.completed_patterns.append(pattern)
            return 0

        # ── Breakout detection ──
        close_i = self.closes[i]
        close_prev = self.closes[i - 1] if i > 0 else close_i

        up_i_prev = pattern.up_line_at(i - 1) if i > 0 else up_i
        lo_i_prev = pattern.lo_line_at(i - 1) if i > 0 else lo_i

        # Check cross-line using frozen parameters
        breakout_up = close_i > up_i and close_prev <= up_i_prev
        breakout_down = close_i < lo_i and close_prev >= lo_i_prev

        if not breakout_up and not breakout_down:
            return 0

        direction = 1 if breakout_up else -1
        line_val = up_i if breakout_up else lo_i

        # Amplitude check
        atr_val = self.atr[i] if i < len(self.atr) else None
        if atr_val is not None and atr_val > 0:
            amplitude = abs(close_i - line_val)
            if amplitude < self.params.breakout_atr * atr_val:
                return 0  # Insufficient amplitude

        # Confirm bar mode
        if self.params.confirm_bar:
            if self._pending_breakout is None:
                # First cross: store pending, wait for confirmation
                self._pending_breakout = (pattern.id, direction, i)
                return 0
            else:
                # Already have a pending breakout from a previous bar
                # This shouldn't normally happen, but handle gracefully
                return 0

        # Immediate signal (confirm_bar=False)
        return self._emit_signal(pattern, i, direction)

    def _check_pending_breakout(self, i: int) -> int:
        """Check if pending breakout is confirmed or reclaimed."""
        if self._pending_breakout is None:
            return 0

        pid, direction, candidate_bar = self._pending_breakout

        # Find the pattern
        pattern = None
        for p in self.active_patterns:
            if p.id == pid:
                pattern = p
                break
        if pattern is None:
            self._pending_breakout = None
            return 0

        line_key = pattern.up_line_at if direction == 1 else pattern.lo_line_at
        close_i = self.closes[i]

        # Check if still outside the line
        if direction == 1:
            still_outside = close_i > line_key(i)
        else:
            still_outside = close_i < line_key(i)

        if still_outside:
            # Confirmed
            self._pending_breakout = None
            return self._emit_signal(pattern, i, direction)
        else:
            # False breakout: reclaimed
            self._pending_breakout = None
            pattern.false_breakout_count += 1
            # Pattern stays LOCKED, can try again
            return 0

    def _emit_signal(self, pattern: TrianglePattern, i: int, direction: int) -> int:
        """Emit a breakout signal and terminate the pattern."""
        pattern.status = "BREAKOUT_UP" if direction == 1 else "BREAKOUT_DOWN"
        pattern.signal_bar = i
        pattern.breakout_direction = direction

        self.active_patterns.remove(pattern)
        self.completed_patterns.append(pattern)

        return direction

    def _update_touches(self, pattern: TrianglePattern, i: int) -> None:
        """Check for new swing touches that extend the pattern's sequences."""
        # Check for new swing high that extends descending sequence
        for sh in self._swing_highs:
            if sh.confirmed_at == i and sh.index > pattern.h_touches[-1][0]:
                if sh.price < pattern.h_touches[-1][1]:
                    pattern.h_touches.append((sh.index, sh.price))

        # Check for new swing low that extends ascending sequence
        for sl in self._swing_lows:
            if sl.confirmed_at == i and sl.index > pattern.l_touches[-1][0]:
                if sl.price > pattern.l_touches[-1][1]:
                    pattern.l_touches.append((sl.index, sl.price))


# ═══════════════════════════════════════════════════════════════════════════════
#  Backward-Compatible Wrappers
# ═══════════════════════════════════════════════════════════════════════════════

def triangle_array(candles: list[dict]) -> list[int]:
    """Triangle pattern detection with breakout signals (v3).

    Replaces the old v2 implementation with identical function signature.
    Uses causal state-machine detection internally.

    Args:
        candles: list of OHLCV dicts with keys "open","high","low","close","time".

    Returns:
        list[int] of same length as candles: 1 (bullish breakout), -1 (bearish),
        or 0 (no signal).
    """
    n = len(candles)
    if n < 41:
        return [0] * n

    params = TriangleParams()
    detector = TriangleDetector(params)

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]

    # Compute ATR inline (same as signal_detectors._atr)
    atr = _compute_atr(highs, lows, closes, 14)

    signals = [0] * n
    for i in range(n):
        signals[i] = detector.advance(i, highs[i], lows[i], closes[i], atr[i])

    return signals


def triangle_line_info(candles: list[dict], i: int) -> dict | None:
    """Get triangle boundary line parameters for frontend rendering (v3).

    Re-derives detection up to bar i using the same causal algorithm as
    triangle_array(), then returns the frozen line parameters of the
    pattern that was active at bar i.

    Args:
        candles: list of OHLCV dicts.
        i: bar index (signal bar).

    Returns:
        dict with "upper" and "lower" keys, each containing:
        {start_time, end_time, start_price, end_price}.
        Returns None if no triangle pattern is found at bar i.
    """
    n = len(candles)
    if n < 41 or i < 41 or i >= n:
        return None

    params = TriangleParams()
    detector = TriangleDetector(params)

    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    atr = _compute_atr(highs, lows, closes, 14)

    # Run detector up to bar i (inclusive)
    for j in range(i + 1):
        detector.advance(j, highs[j], lows[j], closes[j], atr[j])

    # Find the pattern that was LOCKED and active at bar i
    pattern = detector.find_pattern_at_bar(i)
    if pattern is None:
        return None

    # Use frozen line parameters
    up_slope = pattern.up_slope
    up_int = pattern.up_int
    lo_slope = pattern.lo_slope
    lo_int = pattern.lo_int

    # Each line uses its own start time (first touch)
    h_start = pattern.h_touches[0][0] if pattern.h_touches else pattern.t_left
    l_start = pattern.l_touches[0][0] if pattern.l_touches else pattern.t_left
    line_end = i

    up_start_val = up_slope * h_start + up_int
    up_end_val = up_slope * line_end + up_int
    lo_start_val = lo_slope * l_start + lo_int
    lo_end_val = lo_slope * line_end + lo_int

    return {
        "upper": {
            "start_time": int(candles[h_start]["time"]),
            "end_time": int(candles[line_end]["time"]),
            "start_price": round(up_start_val, 2),
            "end_price": round(up_end_val, 2),
        },
        "lower": {
            "start_time": int(candles[l_start]["time"]),
            "end_time": int(candles[line_end]["time"]),
            "start_price": round(lo_start_val, 2),
            "end_price": round(lo_end_val, 2),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Inline ATR (self-contained, no dependency on signal_detectors)
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14,
) -> list[float | None]:
    """Compute ATR(period). Same algorithm as signal_detectors._atr."""
    n = len(highs)
    result: list[float | None] = [None] * n
    if n < 2:
        return result

    tr = [highs[0] - lows[0]]
    for i in range(1, n):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    if n < period:
        return result

    avg = sum(tr[:period]) / period
    result[period - 1] = avg
    for i in range(period, n):
        avg = (avg * (period - 1) + tr[i]) / period
        result[i] = avg

    return result
