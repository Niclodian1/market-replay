"""
8 个信号检测器。统一契约:

    signal_array(candles: list[dict]) -> list[int]
    → 1 = 做多, -1 = 做空, 0 = 无信号
    → 与输入等长
    → 每根 bar 只看当前及之前的数据

信号清单:
  1. Pinbar          — 长影线反转
  2. Engulfing       — 三连衰竭+反向吞没
  3. Triangle        — 因果状态机三角检测 (v3, 委托 triangle.py)
  4. Box             — 100 根波动区间
  5. Channel         — 平行通道
  6. Trendline Break — 趋势线突破
  7. False Breakout  — Box 假突破
  8. MACD Momentum   — 动能柱衰减
"""
from __future__ import annotations

from app.core.triangle import triangle_array as _triangle_v3
from app.core.triangle import triangle_line_info as _triangle_line_v3


# ═══════════════════════════════════════════════════════════════════════════════
#  基础指标（自包含）
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
#  K 线基础量度
# ═══════════════════════════════════════════════════════════════════════════════

def _body(c: dict) -> float:
    return abs(float(c["close"]) - float(c["open"]))


def _upper_wick(c: dict) -> float:
    return float(c["high"]) - max(float(c["close"]), float(c["open"]))


def _lower_wick(c: dict) -> float:
    return min(float(c["close"]), float(c["open"])) - float(c["low"])


def _range_pct(c: dict) -> float:
    o = float(c["open"])
    if o <= 0:
        return 0.0
    return (float(c["high"]) - float(c["low"])) / o


def _is_bullish(c: dict) -> bool:
    return float(c["close"]) > float(c["open"])


def _is_bearish(c: dict) -> bool:
    return float(c["close"]) < float(c["open"])


# ═══════════════════════════════════════════════════════════════════════════════
#  摆动点工具
# ═══════════════════════════════════════════════════════════════════════════════

def _swing_points(closes: list[float], pivot: int) -> tuple[set[int], set[int]]:
    """找摆动高点和低点（收盘价极值）。pivot=5(紧) 或 20(宽)。"""
    n = len(closes)
    highs: set[int] = set()
    lows: set[int] = set()
    window = pivot
    for i in range(window, n - window):
        w = closes[i - window: i + window + 1]
        if closes[i] == max(w):
            highs.add(i)
        if closes[i] == min(w):
            lows.add(i)
    return highs, lows


def _fit_line(indices: list[int], values: list[float]) -> tuple[float, float]:
    """最小二乘拟合 → (斜率, 截距)。"""
    k = len(indices)
    if k < 2:
        return 0.0, values[0] if values else 0.0
    mx = sum(indices) / k
    my = sum(values) / k
    denom = sum((x - mx) ** 2 for x in indices)
    if denom == 0:
        return 0.0, my
    slope = sum((indices[j] - mx) * (values[j] - my) for j in range(k)) / denom
    return slope, my - slope * mx


# ═══════════════════════════════════════════════════════════════════════════════
#  信号 1: Pinbar
# ═══════════════════════════════════════════════════════════════════════════════

def pinbar_array(candles: list[dict]) -> list[int]:
    """
    看涨: 下影线 ≥ 实体×3, 上影线 < 实体, 下影线/open > 2%
    看跌: 上影线 ≥ 实体×3, 下影线 < 实体, 上影线/open > 2%
    """
    n = len(candles)
    signals = [0] * n
    for i in range(n):
        c = candles[i]
        body = _body(c)
        if body < 1e-10:
            continue
        uw = _upper_wick(c)
        lw = _lower_wick(c)
        o = float(c["open"])
        if o <= 0:
            continue

        # 看涨 Pinbar
        if lw >= body * 3 and uw < body and lw / o > 0.02:
            signals[i] = 1
        # 看跌 Pinbar
        elif uw >= body * 3 and lw < body and uw / o > 0.02:
            signals[i] = -1
    return signals


def _pinbar_dir(c: dict) -> int:
    """返回单根 bar 的 Pinbar 方向: 1/-1/0。用于其他信号中做确认。"""
    body = _body(c)
    if body < 1e-10:
        return 0
    uw = _upper_wick(c)
    lw = _lower_wick(c)
    o = float(c["open"])
    if o <= 0:
        return 0
    if lw >= body * 3 and uw < body and lw / o > 0.02:
        return 1
    if uw >= body * 3 and lw < body and uw / o > 0.02:
        return -1
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
#  信号 2: Engulfing
# ═══════════════════════════════════════════════════════════════════════════════

def engulfing_array(candles: list[dict]) -> list[int]:
    """
    前置: 连续 3 根同向 K 线 + 第 3 根波动 > 前两根
    触发: 第 4 根反向 + body > 第 3 根 body + 实体完全覆盖第 3 根实体
    """
    n = len(candles)
    signals = [0] * n
    if n < 4:
        return signals

    for i in range(3, n):
        b1, b2, b3, b4 = candles[i - 3], candles[i - 2], candles[i - 1], candles[i]

        # 看涨吞没: 前 3 根阴线 + 第 4 根阳线
        if (_is_bearish(b1) and _is_bearish(b2) and _is_bearish(b3) and _is_bullish(b4)):
            r1, r2, r3 = _range_pct(b1), _range_pct(b2), _range_pct(b3)
            if r3 > r1 and r3 > r2:
                body3, body4 = _body(b3), _body(b4)
                if body4 > body3:
                    if float(b4["open"]) <= float(b3["close"]) and float(b4["close"]) >= float(b3["open"]):
                        signals[i] = 1

        # 看跌吞没: 前 3 根阳线 + 第 4 根阴线
        if (_is_bullish(b1) and _is_bullish(b2) and _is_bullish(b3) and _is_bearish(b4)):
            r1, r2, r3 = _range_pct(b1), _range_pct(b2), _range_pct(b3)
            if r3 > r1 and r3 > r2:
                body3, body4 = _body(b3), _body(b4)
                if body4 > body3:
                    if float(b4["open"]) >= float(b3["close"]) and float(b4["close"]) <= float(b3["open"]):
                        signals[i] = -1

    return signals


def _engulfing_dir(candles: list[dict], i: int) -> int:
    """返回第 i 根 bar 的吞没方向: 1/-1/0。用于其他信号中做确认。"""
    if i < 3:
        return 0
    arr = engulfing_array(candles)
    return arr[i] if i < len(arr) else 0


# ═══════════════════════════════════════════════════════════════════════════════
#  信号 3: Triangle（三角形中继）
# ═══════════════════════════════════════════════════════════════════════════════

def triangle_array(candles: list[dict]) -> list[int]:
    """
    三角形中继形态检测 (v3 — 因果状态机)。

    使用 triangle.py 的 TriangleDetector：
      - 摆动点: high/low 局部极值，pivot=5 + 3 合并，确认延迟 pivot 根 bar
      - 状态机: CANDIDATE → LOCKED → BREAKOUT_UP/DOWN/EXPIRED/INVALID
      - LOCKED 后边界冻结，每形态最多触发一次信号
      - 过滤: 方向、口部对齐、形成期、宽度(ATR)、收敛比 W1/W0、apex、贴边质量
      - 突破: 收盘跨冻结线 + 振幅 ≥ breakout_atr × ATR

    因果性保证: bar i 只使用 confirmed_at ≤ i 的摆动点；LOCKED 后线参数冻结。
    """
    return _triangle_v3(candles)


def _find_valid_trendline(
    sw_points: set[int], closes: list[float], price_extremes: list[float],
    current_i: int, descending: bool,
) -> tuple[int, int, float, float] | None:
    """
    在 [current_i-120, current_i) 找一条「真正的」趋势线。

    descending=True:  下降压力线 → 用摆动点的 high 连线，所有 high 在线下方
    descending=False: 上升支撑线 → 用摆动点的 low 连线，所有 low 在线上方

    摆动点仍按收盘价定位，但连线用极值。

    Returns: (prev_idx, curr_idx, slope, intercept) or None
    """
    lookback_start = max(0, current_i - 120)
    recent = sorted([s for s in sw_points if lookback_start <= s < current_i])
    if len(recent) < 2:
        return None

    curr = recent[-1]

    for prev in reversed(recent[:-1]):
        # 距离至少 10 根
        if curr - prev < 10:
            continue

        # 连线——用极值，不用收盘价
        curr_val = price_extremes[curr]
        prev_val = price_extremes[prev]

        # 方向检查
        if descending and prev_val <= curr_val:
            continue
        if not descending and prev_val >= curr_val:
            continue

        slope = (curr_val - prev_val) / (curr - prev)
        intercept = prev_val - slope * prev

        # 斜率检查
        if descending and slope >= 0:
            continue
        if not descending and slope <= 0:
            continue

        # 验证：prev 到 current_i-1，允许 ≤5% 的 bar 穿透（容差 3%）
        total_bars = current_i - prev
        if total_bars <= 0:
            continue
        penetrations = 0
        for j in range(prev, current_i):
            line_val = slope * j + intercept
            if line_val <= 0:
                penetrations += 1
            elif descending:
                if price_extremes[j] > line_val * 1.03:
                    penetrations += 1
            else:
                if price_extremes[j] < line_val * 0.97:
                    penetrations += 1
        if penetrations / total_bars > 0.05:
            continue

        if True:
            return (prev, curr, slope, intercept)

    return None


def triangle_line_info(candles: list[dict], i: int) -> dict | None:
    """返回第 i 根 bar 的三角形边界线参数（供前端画线），v3。

    使用 triangle.py 的因果状态机：
      - 重新运行 detector 到 bar i
      - 返回该 bar 活跃形态的冻结线参数
      - 每条线用自己的第一个触点作为起始点
    """
    return _triangle_line_v3(candles, i)


def _descending_sequence_closes(indices: list[int], closes: list[float]) -> list[tuple[int, float]]:
    """取出渐次降低的摆动高点序列（从最近往回找）。"""
    result = [(indices[-1], closes[indices[-1]])]
    for j in range(len(indices) - 2, -1, -1):
        if closes[indices[j]] > result[-1][1]:
            result.append((indices[j], closes[indices[j]]))
    result.reverse()
    return result


def _ascending_sequence_closes(indices: list[int], closes: list[float]) -> list[tuple[int, float]]:
    """取出渐次升高的摆动低点序列（从最近往回找）。"""
    result = [(indices[-1], closes[indices[-1]])]
    for j in range(len(indices) - 2, -1, -1):
        if closes[indices[j]] < result[-1][1]:
            result.append((indices[j], closes[indices[j]]))
    result.reverse()
    return result


def _check_buildup(candles: list[dict], current_i: int, slope: float, intercept: float,
                   side: str) -> bool:
    """
    检查最近 3 根 bar 是否连续触及边界但收盘在边界「内侧」。
    side='upper': high 触及上边界，收盘在下方
    side='lower': low 触及下边界，收盘在上方
    """
    check_bars = 3
    if current_i < check_bars:
        return False
    for offset in range(check_bars):
        idx = current_i - check_bars + offset
        line_val = slope * idx + intercept
        c = candles[idx]
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        if line_val <= 0:
            return False
        if side == "upper":
            # high 触及（误差 < 0.5%）但收盘在下方
            if abs(high - line_val) / line_val > 0.005:
                return False
            if close >= line_val:
                return False
        else:
            if abs(low - line_val) / line_val > 0.005:
                return False
            if close <= line_val:
                return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
#  信号 4: Box（长期波动区间）
# ═══════════════════════════════════════════════════════════════════════════════

def box_array(candles: list[dict]) -> list[int]:
    """
    窗口: 100 根
    上沿: 100 根内最高收盘价
    下沿: 100 根内最低收盘价
    有效: 上沿距今 > 30 根 AND 下沿距今 > 30 根 AND 5% < 宽度 < 30%
    信号: 接近边界 + Pinbar/Engulfing 确认 → 反转; 收盘突破 → 突破
    """
    n = len(candles)
    signals = [0] * n
    if n < 100:
        return signals

    closes = [float(c["close"]) for c in candles]

    for i in range(100, n):
        window_closes = closes[i - 100: i]

        hi_close = max(window_closes)
        lo_close = min(window_closes)

        # 找到最高/最低收盘价出现的位置
        hi_idx_in_window = max(j for j, v in enumerate(window_closes) if v == hi_close)
        lo_idx_in_window = max(j for j, v in enumerate(window_closes) if v == lo_close)

        hi_age = 99 - hi_idx_in_window  # 距今多少根
        lo_age = 99 - lo_idx_in_window

        if hi_age < 30 or lo_age < 30:
            continue  # 近 30 根内有过新高或新低 → 区间不成立

        width = (hi_close - lo_close) / lo_close
        if width < 0.05 or width > 0.30:
            continue

        # — 接近下沿做多 / 接近上沿做空 —
        near_lo = (closes[i] - lo_close) / lo_close < width * 0.20
        near_hi = (hi_close - closes[i]) / hi_close < width * 0.20

        if near_lo:
            if _pinbar_dir(candles[i]) == 1 or _engulfing_dir(candles, i) == 1:
                signals[i] = 1

        if near_hi:
            if _pinbar_dir(candles[i]) == -1 or _engulfing_dir(candles, i) == -1:
                signals[i] = -1

        # — 突破 —
        if closes[i] > hi_close:
            signals[i] = 1
        elif closes[i] < lo_close:
            signals[i] = -1

    return signals


def box_boundaries(candles: list[dict]) -> list[dict | None]:
    """
    返回每根 bar 的 Box 边界（如果区间有效），供 False Breakout 使用。

    返回 [{upper, lower, valid}, ...] 与 candles 等长。
    """
    n = len(candles)
    result: list[dict | None] = [None] * n
    if n < 100:
        return result

    closes = [float(c["close"]) for c in candles]

    for i in range(100, n):
        window_closes = closes[i - 100: i]

        hi_close = max(window_closes)
        lo_close = min(window_closes)

        hi_idx_in_window = max(j for j, v in enumerate(window_closes) if v == hi_close)
        lo_idx_in_window = max(j for j, v in enumerate(window_closes) if v == lo_close)

        hi_age = 99 - hi_idx_in_window
        lo_age = 99 - lo_idx_in_window

        if hi_age < 30 or lo_age < 30:
            continue

        width = (hi_close - lo_close) / lo_close
        if width < 0.05 or width > 0.30:
            continue

        result[i] = {"upper": hi_close, "lower": lo_close, "valid": True}

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  信号 5: Channel（平行通道）
# ═══════════════════════════════════════════════════════════════════════════════

def channel_array(candles: list[dict]) -> list[int]:
    """
    上轨: ≥2 个摆动高点收盘价连线
    下轨: ≥2 个摆动低点收盘价连线
    斜率差 < 15% → 通道成立
    触及 + 确认 → 信号
    """
    n = len(candles)
    signals = [0] * n
    if n < 50:
        return signals

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    sw_h, sw_l = _swing_points(closes, 5)

    for i in range(50, n):
        lookback_start = max(0, i - 120)

        window_highs = sorted([h for h in sw_h if lookback_start <= h < i])
        window_lows = sorted([l for l in sw_l if lookback_start <= l < i])

        if len(window_highs) < 2 or len(window_lows) < 2:
            continue

        # 取最近两个高点和低点连线
        h_indices = window_highs[-2:]
        h_vals = [closes[idx] for idx in h_indices]
        up_slope, up_int = _fit_line(h_indices, h_vals)

        l_indices = window_lows[-2:]
        l_vals = [closes[idx] for idx in l_indices]
        lo_slope, lo_int = _fit_line(l_indices, l_vals)

        # 平行检测
        max_slope = max(abs(up_slope), abs(lo_slope))
        if max_slope > 0 and abs(up_slope - lo_slope) / max_slope > 0.15:
            continue

        up_line_i = up_slope * i + up_int
        lo_line_i = lo_slope * i + lo_int
        if up_line_i <= lo_line_i:
            continue

        # — 触及上轨 —
        if abs(highs[i] - up_line_i) / up_line_i < 0.005:
            if _pinbar_dir(candles[i]) == -1 or _engulfing_dir(candles, i) == -1:
                signals[i] = -1

        # — 触及下轨 —
        if abs(lows[i] - lo_line_i) / lo_line_i < 0.005:
            if _pinbar_dir(candles[i]) == 1 or _engulfing_dir(candles, i) == 1:
                signals[i] = 1

        # — 蓄势后突破 —
        buildup_up = _check_buildup(candles, i, up_slope, up_int, "upper")
        buildup_lo = _check_buildup(candles, i, lo_slope, lo_int, "lower")

        if buildup_up and closes[i] > up_line_i:
            signals[i] = 1
        if buildup_lo and closes[i] < lo_line_i:
            signals[i] = -1

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
#  信号 6: Trendline Break（趋势线突破）
# ═══════════════════════════════════════════════════════════════════════════════

def trendline_break_array(candles: list[dict]) -> list[int]:
    """
    摆动高点/低点: 收盘价 > 前后各 20 根（20根内无更高/更低）
    ≥2 个渐次降低的高点 → 下降趋势线（突破做多）
    ≥2 个渐次升高的低点 → 上升趋势线（跌破做空）
    两摆动点之间 ≥ 20 根，突破 bar 距最近摆动点 ≥ 20 根
    第一次触及 → 不产生信号
    有效突破 → 收盘突破 + 波动率 > 前 5 根均值
    """
    n = len(candles)
    signals = [0] * n
    if n < 80:
        return signals

    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    opens = [float(c["open"]) for c in candles]
    sw_h, sw_l = _swing_points(closes, 20)

    for i in range(80, n):
        lookback_start = max(0, i - 90)

        # 下降趋势线（高点渐次降低 → 突破做多）
        window_highs = sorted([h for h in sw_h if lookback_start <= h < i])
        if len(window_highs) >= 2:
            desc = _descending_sequence_closes(window_highs, closes)
            if len(desc) >= 2:
                h_idx = [x[0] for x in desc]
                h_val = [x[1] for x in desc]
                # 距离检查
                if h_idx[-1] - h_idx[0] >= 20 and i - h_idx[-1] >= 20:
                    slope, intercept = _fit_line(h_idx, h_val)
                    line_i = slope * i + intercept
                    line_prev = slope * (i - 1) + intercept

                    # 第一次触及 → 观察，不放信号
                    # 有效突破: 收盘突破 + 波动放大
                    range_i = (highs[i] - lows[i]) / opens[i] if opens[i] > 0 else 0
                    avg_range_5 = sum(
                        (highs[i - j] - lows[i - j]) / opens[i - j]
                        for j in range(1, 6) if opens[i - j] > 0
                    ) / 5.0

                    if closes[i] > line_i and closes[i - 1] <= line_prev:
                        if range_i > avg_range_5:
                            signals[i] = 1

        # 上升趋势线（低点渐次升高 → 跌破做空）
        window_lows = sorted([l for l in sw_l if lookback_start <= l < i])
        if len(window_lows) >= 2:
            asc = _ascending_sequence_closes(window_lows, closes)
            if len(asc) >= 2:
                l_idx = [x[0] for x in asc]
                l_val = [x[1] for x in asc]
                if l_idx[-1] - l_idx[0] >= 20 and i - l_idx[-1] >= 20:
                    slope, intercept = _fit_line(l_idx, l_val)
                    line_i = slope * i + intercept
                    line_prev = slope * (i - 1) + intercept

                    range_i = (highs[i] - lows[i]) / opens[i] if opens[i] > 0 else 0
                    avg_range_5 = sum(
                        (highs[i - j] - lows[i - j]) / opens[i - j]
                        for j in range(1, 6) if opens[i - j] > 0
                    ) / 5.0

                    if closes[i] < line_i and closes[i - 1] >= line_prev:
                        if range_i > avg_range_5:
                            signals[i] = -1

    return signals


def trendline_line_info(candles: list[dict], i: int) -> dict | None:
    """返回第 i 根 bar 的趋势线参数（供前端画线）。"""
    n = len(candles)
    if n < 80 or i < 80 or i >= n:
        return None

    closes = [float(c["close"]) for c in candles]
    sw_h, sw_l = _swing_points(closes, 20)
    lookback_start = max(0, i - 100)

    # 下降趋势线（做多信号）
    window_highs = sorted([h for h in sw_h if lookback_start <= h < i])
    if len(window_highs) >= 2:
        desc = _descending_sequence_closes(window_highs, closes)
        if len(desc) >= 2:
            h_idx = [x[0] for x in desc]
            h_val = [x[1] for x in desc]
            if h_idx[-1] - h_idx[0] >= 20 and i - h_idx[-1] >= 20:
                slope, intercept = _fit_line(h_idx, h_val)
                start_val = slope * h_idx[0] + intercept
                end_val = slope * i + intercept
                return {
                    "start_time": int(candles[h_idx[0]]["time"]),
                    "end_time": int(candles[i]["time"]),
                    "start_price": round(start_val, 2),
                    "end_price": round(end_val, 2),
                    "kind": "descending",
                }

    # 上升趋势线（做空信号）
    window_lows = sorted([l for l in sw_l if lookback_start <= l < i])
    if len(window_lows) >= 2:
        asc = _ascending_sequence_closes(window_lows, closes)
        if len(asc) >= 2:
            l_idx = [x[0] for x in asc]
            l_val = [x[1] for x in asc]
            if l_idx[-1] - l_idx[0] >= 20 and i - l_idx[-1] >= 20:
                slope, intercept = _fit_line(l_idx, l_val)
                start_val = slope * l_idx[0] + intercept
                end_val = slope * i + intercept
                return {
                    "start_time": int(candles[l_idx[0]]["time"]),
                    "end_time": int(candles[i]["time"]),
                    "start_price": round(start_val, 2),
                    "end_price": round(end_val, 2),
                    "kind": "ascending",
                }

    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  信号 7: False Breakout（结合 Box 使用）
# ═══════════════════════════════════════════════════════════════════════════════

def false_breakout_array(candles: list[dict]) -> list[int]:
    """
    前提: Box 区间有效。
    类型 1 — Pinbar 假突破: 影线突破 Box 边界，但收盘收回 + 满足 Pinbar 条件
    类型 2 — 吞没假突破: Bar1 突破 Box 边界，Bar2 反向吞没收回
    """
    n = len(candles)
    signals = [0] * n
    if n < 101:
        return signals

    boxes = box_boundaries(candles)
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]

    for i in range(101, n):
        box = boxes[i]
        if box is None or not box.get("valid"):
            continue

        upper, lower = box["upper"], box["lower"]

        # — 类型 1: Pinbar 假突破 —
        # 假突破上沿: high 突破 upper 但 close 收回，且该 bar 满足 pinbar
        if highs[i] > upper and closes[i] < upper:
            if _pinbar_dir(candles[i]) == -1:
                signals[i] = -1  # 假突破 → 做空
        # 假跌破下沿: low 跌破 lower 但 close 收回
        if lows[i] < lower and closes[i] > lower:
            if _pinbar_dir(candles[i]) == 1:
                signals[i] = 1  # 假跌破 → 做多

        # — 类型 2: 吞没假突破 —
        if i >= 1 and boxes[i - 1] and boxes[i - 1].get("valid"):
            prev_close = closes[i - 1]
            # Bar1 收盘突破上沿，Bar2 反向吞没收回
            if prev_close > upper and closes[i] < upper:
                if _engulfing_dir(candles, i) == -1:
                    signals[i] = -1
            # Bar1 收盘跌破下沿，Bar2 反向吞没收回
            if prev_close < lower and closes[i] > lower:
                if _engulfing_dir(candles, i) == 1:
                    signals[i] = 1

    return signals


# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
#  信号 9: MACD Divergence（背离）
# ═══════════════════════════════════════════════════════════════════════════════

def macd_divergence_array(candles: list[dict]) -> list[int]:
    """MACD 柱极值背离（口径统一：动能极值相对减弱）。

    当前动能段极值 vs 穿过≥3根反向柱的前一同向段极值；
    底背离（做多）/ 顶背离（做空），返回逐根 ±1 数组。
    """
    from app.core.divergence_detectors import macd_extreme_divergence_array
    return macd_extreme_divergence_array(candles)


def _is_hist_trough(hist: list[float | None], i: int) -> bool:
    """hist[i] 是前后各 3 根中的最低点（谷底）。"""
    if hist[i] is None:
        return False
    for offset in range(-3, 4):
        if offset == 0:
            continue
        j = i + offset
        if j < 0 or j >= len(hist):
            continue
        if hist[j] is not None and hist[j] <= hist[i]:
            return False
    return True


def _is_hist_peak(hist: list[float | None], i: int) -> bool:
    """hist[i] 是前后各 3 根中的最高点（峰顶）。"""
    if hist[i] is None:
        return False
    for offset in range(-3, 4):
        if offset == 0:
            continue
        j = i + offset
        if j < 0 or j >= len(hist):
            continue
        if hist[j] is not None and hist[j] >= hist[i]:
            return False
    return True


def _find_prev_trough(hist: list[float | None], i: int) -> int | None:
    """在 [i-50, i-10] 找前一个谷底（负值，局部最低点）。"""
    start = max(0, i - 50)
    end = i - 10
    best = None
    best_val = float("inf")
    for j in range(end, start - 1, -1):
        if hist[j] is not None and hist[j] < 0 and _is_hist_trough(hist, j):
            if hist[j] < best_val:
                best_val = hist[j]
                best = j
    return best


def _find_prev_peak(hist: list[float | None], i: int) -> int | None:
    """在 [i-50, i-10] 找前一个峰顶（正值，局部最高点）。"""
    start = max(0, i - 50)
    end = i - 10
    best = None
    best_val = float("-inf")
    for j in range(end, start - 1, -1):
        if hist[j] is not None and hist[j] > 0 and _is_hist_peak(hist, j):
            if hist[j] > best_val:
                best_val = hist[j]
                best = j
    return best


# ═══════════════════════════════════════════════════════════════════════════════
#  批量计算
# ═══════════════════════════════════════════════════════════════════════════════

SIGNAL_NAMES = [
    "pinbar", "engulfing", "triangle", "box",
    "channel", "trendline_break", "false_breakout",
    "macd_divergence",
]

SIGNAL_LABELS = {
    "pinbar": "Pinbar",
    "engulfing": "Engulfing",
    "triangle": "Triangle",
    "box": "Box",
    "channel": "Channel",
    "trendline_break": "Trendline Break",
    "false_breakout": "False Breakout",
    "macd_divergence": "MACD Divergence",
}


def compute_all_signals(candles: list[dict]) -> dict[str, list[int]]:
    """一次性计算全部 8 个信号的 signal_array。"""
    return {
        "pinbar": pinbar_array(candles),
        "engulfing": engulfing_array(candles),
        "triangle": triangle_array(candles),
        "box": box_array(candles),
        "channel": channel_array(candles),
        "trendline_break": trendline_break_array(candles),
        "false_breakout": false_breakout_array(candles),
        "macd_divergence": macd_divergence_array(candles),
    }
