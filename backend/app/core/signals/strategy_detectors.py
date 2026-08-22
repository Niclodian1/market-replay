"""
三类交易策略探测器: 趋势 / 动能 / 反转。

优化版: 预计算 + 滑动窗口，避免 O(n²) 每 bar 扫描。
"""
from __future__ import annotations

from typing import Any

from app.core.indicators import _sma, _atr, _bollinger, _macd
from app.core.price_action import detect_pinbar, detect_engulfing
from app.core.signal_registry import SignalHit


# ═══════════════════════════════════════════════════════════════════════════════
#  共用工具
# ═══════════════════════════════════════════════════════════════════════════════

def _body_size(candle: dict) -> float:
    return abs(float(candle["close"]) - float(candle["open"]))


def _swing_points(highs: list[float], lows: list[float], pivot: int = 3):
    """预计算所有因果确认的波段高低点。返回 (high_indices, low_indices)。"""
    n = len(highs)
    h_idx = set()
    l_idx = set()
    for i in range(pivot, n - pivot):
        wh = highs[i - pivot:i + pivot + 1]
        wl = lows[i - pivot:i + pivot + 1]
        if highs[i] == max(wh):
            h_idx.add(i)
        if lows[i] == min(wl):
            l_idx.add(i)
    return h_idx, l_idx


def _fit_line(xs: list[int], ys: list[float]) -> tuple[float, float]:
    """最小二乘拟合 → (slope, intercept)。"""
    k = len(xs)
    if k < 2:
        return 0.0, ys[0] if ys else 0.0
    mx = sum(xs) / k
    my = sum(ys) / k
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0, my
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(k)) / denom
    return slope, my - slope * mx


def _latest_hit(name: str, arr: list[int], candles: list[dict], timeframe: str,
                strategy_type: str) -> list[SignalHit]:
    if not arr or arr[-1] == 0:
        return []
    s = arr[-1]
    c = candles[-1]
    return [SignalHit(
        name=name,
        direction="long" if s == 1 else "short",
        timeframe=timeframe,
        score=0.7,
        fired_at_ts=int(c["time"]),
        fired_price=float(c["close"]),
        payload={"strategy_type": strategy_type},
    )]


# ═══════════════════════════════════════════════════════════════════════════════
#  策略 1: 趋势交易
# ═══════════════════════════════════════════════════════════════════════════════

class TrendStrategyDetector:
    """
    趋势交易 —— 横盘突破 / 趋势线突破。

    横盘: 最近 100 根 K 线振幅 < 20% + 收盘突破区间边界 + 实体 > 前5根均值×1.5
    趋势线: ≥2个波段点连线 + 收盘突破 + 突破前有触碰验证
    """
    name = "trend"
    display_name = "趋势交易·结构突破"
    category = "strategy"
    description = "捕捉横盘区间或趋势线被打破后的新趋势启动，长期持仓，追踪 MA60 出场。"
    logic = "横盘突破 + 趋势线突破两类子信号。"
    direction_support = ["long", "short"]
    strategy_type = "trend"
    valid_regimes = {2, 3, 4, 5}

    params_schema: dict[str, Any] = {
        "range_lookback": 100,
        "range_amplitude_pct": 20.0,
        "body_multiplier": 1.5,
        "trendline_min_touches": 2,
        "pivot": 3,
    }

    def signal_array(self, candles: list[dict[str, Any]], params: dict[str, Any]) -> list[int]:
        p = {**self.params_schema, **params}
        n = len(candles)
        lookback = int(p["range_lookback"])
        if n < lookback + 20:
            return [0] * n

        closes = [float(c["close"]) for c in candles]
        highs  = [float(c["high"])  for c in candles]
        lows   = [float(c["low"])   for c in candles]
        amp_pct = float(p["range_amplitude_pct"]) / 100.0
        body_mult = float(p["body_multiplier"])
        pivot = int(p["pivot"])
        signals = [0] * n

        # ── 预计算: 波段点（只算一次）──
        sw_h, sw_l = _swing_points(highs, lows, pivot)

        # ── 预计算: 每根 bar 前 5 根平均实体 ──
        avg_bodies = [0.0] * n
        for i in range(5, n):
            bodies = [_body_size(candles[j]) for j in range(i - 4, i + 1)]
            avg_bodies[i] = sum(bodies) / 5

        # ── 滚动窗口: 高效维护 [i-lookback, i) 的 max/min ──
        # 使用简单方法（对 3000 根 K 线足够）—— 预计算滚动 max/min
        roll_high = [0.0] * n
        roll_low = [float("inf")] * n
        for i in range(lookback, n):
            roll_high[i] = max(highs[i - lookback:i])
            roll_low[i] = min(lows[i - lookback:i])

        for i in range(lookback + 1, n):
            if signals[i] != 0:
                continue

            # ── 横盘突破 ──
            seg_high = roll_high[i]
            seg_low = roll_low[i]
            range_amp = (seg_high - seg_low) / seg_low if seg_low > 0 else float("inf")

            if range_amp <= amp_pct:
                body = _body_size(candles[i])
                if body >= avg_bodies[i] * body_mult:
                    # 多头突破
                    if closes[i] > seg_high and closes[i - 1] <= seg_high:
                        signals[i] = 1
                        continue
                    # 空头突破
                    if closes[i] < seg_low and closes[i - 1] >= seg_low:
                        signals[i] = -1
                        continue

            # ── 趋势线突破 ──
            # 下降趋势线（渐次降低的高点→突破做多）
            # as-of 契约：摆动点只有经过右侧 pivot 根（idx + pivot <= i）后才确认可用
            recent_highs = sorted([idx for idx in sw_h if idx + pivot <= i and idx > i - lookback * 2])
            if len(recent_highs) >= 2:
                # 取最后几个渐次降低的高点
                desc = self._descending_sequence(recent_highs, highs)
                if len(desc) >= 2:
                    idxs = [x[0] for x in desc]
                    vals = [x[1] for x in desc]
                    slope, intercept = _fit_line(idxs, vals)
                    line_i = slope * i + intercept
                    line_prev = slope * (i - 1) + intercept
                    if closes[i - 1] <= line_prev and closes[i] > line_i:
                        if self._touches_line(highs, slope, intercept, i, lookback):
                            signals[i] = 1
                            continue

            # 上升趋势线（渐次升高的低点→突破做空）
            recent_lows = sorted([idx for idx in sw_l if idx + pivot <= i and idx > i - lookback * 2])
            if len(recent_lows) >= 2:
                asc = self._ascending_sequence(recent_lows, lows)
                if len(asc) >= 2:
                    idxs = [x[0] for x in asc]
                    vals = [x[1] for x in asc]
                    slope, intercept = _fit_line(idxs, vals)
                    line_i = slope * i + intercept
                    line_prev = slope * (i - 1) + intercept
                    if closes[i - 1] >= line_prev and closes[i] < line_i:
                        if self._touches_line(lows, slope, intercept, i, lookback):
                            signals[i] = -1

        return signals

    @staticmethod
    def _descending_sequence(indices: list[int], prices: list[float]) -> list[tuple[int, float]]:
        """取出渐次降低的高点序列。"""
        result = [(indices[-1], prices[indices[-1]])]
        for j in range(len(indices) - 2, -1, -1):
            if prices[indices[j]] > result[-1][1]:
                result.append((indices[j], prices[indices[j]]))
        result.reverse()
        return result

    @staticmethod
    def _ascending_sequence(indices: list[int], prices: list[float]) -> list[tuple[int, float]]:
        """取出渐次升高的低点序列。"""
        result = [(indices[-1], prices[indices[-1]])]
        for j in range(len(indices) - 2, -1, -1):
            if prices[indices[j]] < result[-1][1]:
                result.append((indices[j], prices[indices[j]]))
        result.reverse()
        return result

    @staticmethod
    def _touches_line(prices: list[float], slope: float, intercept: float,
                      current_idx: int, lookback: int) -> bool:
        """趋势线在突破前是否至少有 3 根 K 线触碰过。"""
        start = max(0, current_idx - lookback)
        touches = 0
        for j in range(start, current_idx):
            lv = slope * j + intercept
            if lv > 0 and abs(prices[j] - lv) / lv < 0.01:
                touches += 1
        return touches >= 3

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        arr = self.signal_array(candles, {})
        return _latest_hit(self.name, arr, candles, timeframe, self.strategy_type)


# ═══════════════════════════════════════════════════════════════════════════════
#  策略 2: 动能交易
# ═══════════════════════════════════════════════════════════════════════════════

class MomentumStrategyDetector:
    """
    动能交易 —— MA 粘合 + 波动收窄后的突破。

    MA20 与 MA60 收敛 + Bollinger 带宽收缩 + ATR 收缩 → 入场。
    """
    name = "momentum"
    display_name = "动能交易·蓄力突破"
    category = "strategy"
    description = "捕捉 MA 粘合+波动收窄后的爆发性突破。"
    logic = "MA收敛 + BB收缩 + ATR收缩 + 收盘突破 → 入场。"
    direction_support = ["long", "short"]
    strategy_type = "momentum"
    valid_regimes = {2, 3, 4}

    params_schema: dict[str, Any] = {
        "ma_fast": 20,
        "ma_slow": 60,
        "squeeze_pct": 15.0,        # MA 收敛百分位阈值（从10%放宽到15%）
        "bb_squeeze_pct": 30.0,     # BB 带宽收缩阈值（从20%放宽到30%）
        "atr_squeeze_pct": 40.0,    # ATR 收缩阈值（从30%放宽到40%）
        "body_atr_mult": 1.2,       # 实体 > N×ATR（从1.5降到1.2）
        "min_conditions": 2,         # 新增: 满足 N 个条件即触发（3→2）
    }

    def signal_array(self, candles: list[dict[str, Any]], params: dict[str, Any]) -> list[int]:
        p = {**self.params_schema, **params}
        n = len(candles)
        min_len = 120
        if n < min_len:
            return [0] * n

        closes = [float(c["close"]) for c in candles]
        highs  = [float(c["high"])  for c in candles]
        lows   = [float(c["low"])   for c in candles]

        ma_f = _sma(closes, int(p["ma_fast"]))
        ma_s = _sma(closes, int(p["ma_slow"]))
        upper, mid, lower = _bollinger(closes, 20, 2.0)
        atr = _atr(highs, lows, closes, 14)

        sq_pct = float(p["squeeze_pct"]) / 100.0
        bb_pct = float(p["bb_squeeze_pct"]) / 100.0
        atr_pct = float(p["atr_squeeze_pct"]) / 100.0
        body_mult = float(p["body_atr_mult"])
        min_cond = int(p.get("min_conditions", 2))  # 需要满足的条件数

        signals = [0] * n

        # ── 预计算 ──
        ma_ratio = [1.0] * n
        for i in range(60, n):
            if ma_f[i] and ma_s[i] and ma_s[i] > 0:
                ma_ratio[i] = abs(ma_f[i] - ma_s[i]) / ma_s[i]  # type: ignore[operator]

        bb_width = [1.0] * n
        for i in range(20, n):
            if upper[i] and lower[i] and mid[i] and mid[i] > 0:
                bb_width[i] = (upper[i] - lower[i]) / mid[i]  # type: ignore[operator]

        atr_ratio = [1.0] * n
        for i in range(14, n):
            if atr[i] and closes[i] > 0:
                atr_ratio[i] = atr[i] / closes[i]  # type: ignore[operator]

        for i in range(min_len, n):
            # 计数满足的 squeeze 条件
            cond_count = 0

            # 1) MA 粘合
            window = ma_ratio[max(0, i - 19):i + 1]
            ma_rank = sum(1 for r in window if r < ma_ratio[i]) / len(window)
            if ma_rank < sq_pct:
                cond_count += 1

            # 2) BB 收缩
            w50 = bb_width[max(0, i - 49):i + 1]
            bb_rank = sum(1 for w in w50 if w < bb_width[i]) / len(w50)
            if bb_rank < bb_pct:
                cond_count += 1

            # 3) ATR 收缩
            a50 = atr_ratio[max(0, i - 49):i + 1]
            atr_rank = sum(1 for a in a50 if a < atr_ratio[i]) / len(a50)
            if atr_rank < atr_pct:
                cond_count += 1

            if cond_count < min_cond:
                continue

            # 触发: 实体 > body_mult * ATR
            av = atr[i]
            if not av:
                continue
            body = _body_size(candles[i])
            if body < av * body_mult:
                continue

            # 突破方向
            if ma_f[i] and ma_f[i - 1]:
                if closes[i] > ma_f[i] and closes[i - 1] <= ma_f[i - 1]:  # type: ignore[operator]
                    signals[i] = 1
                elif closes[i] < ma_f[i] and closes[i - 1] >= ma_f[i - 1]:  # type: ignore[operator]
                    signals[i] = -1

        return signals

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        arr = self.signal_array(candles, {})
        return _latest_hit(self.name, arr, candles, timeframe, self.strategy_type)


# ═══════════════════════════════════════════════════════════════════════════════
#  策略 3: 反转交易
# ═══════════════════════════════════════════════════════════════════════════════

class ReversalStrategyDetector:
    """
    反转交易 —— 假突破 + MACD 背离 + K 线形态确认。

    价格创 20 根极值 + 收回 + MACD 柱背离 + 长下影/吞没 → 入场。
    """
    name = "reversal"
    display_name = "反转交易·衰竭反转"
    category = "strategy"
    description = "捕捉趋势末端的假突破+背离反转。"
    logic = "假突破 + MACD背离 + K线确认 → 入场。"
    direction_support = ["long", "short"]
    strategy_type = "reversal"
    valid_regimes = {0, 2, 3}

    params_schema: dict[str, Any] = {
        "extreme_lookback": 20,
        "retrace_bars": 2,
        "divergence_lookback": 5,
        "pinbar_ratio": 2.0,
        "require_all_three": False,  # False = 假突破 + (背离 OR K线形态) 即可
    }

    def signal_array(self, candles: list[dict[str, Any]], params: dict[str, Any]) -> list[int]:
        p = {**self.params_schema, **params}
        n = len(candles)
        ext_lb = int(p["extreme_lookback"])
        retrace = int(p["retrace_bars"])
        div_lb = int(p["divergence_lookback"])
        pr = float(p["pinbar_ratio"])
        require_all = bool(p.get("require_all_three", False))
        min_len = ext_lb + 60

        if n < min_len:
            return [0] * n

        closes = [float(c["close"]) for c in candles]
        highs  = [float(c["high"])  for c in candles]
        lows   = [float(c["low"])   for c in candles]

        _, _, hist = _macd(closes)
        pinbars = detect_pinbar(candles, ratio=pr)
        engulfings = detect_engulfing(candles)

        signals = [0] * n
        # as-of 契约：信号只记在确认/决策 bar（当前 bar i），禁止向历史 i_eff 回填。
        # 同一个恢复 bar（i_eff）在同一事件里只允许触发一次，避免相邻 bar 重复触发。
        last_emitted_long: int | None = None
        last_emitted_short: int | None = None

        for i in range(min_len, n):
            if hist[i] is None:
                continue

            # ── 做多 ──
            # 1) 假跌破: 创 ext_lb 根新低 + retrace 根内收回
            prev_low = min(lows[i - ext_lb:i])
            new_low_made = lows[i] <= prev_low or (i >= 1 and lows[i - 1] <= min(lows[i - ext_lb - 1:i - 1]))

            if new_low_made:
                # 检查收回
                for offset in range(retrace + 1):
                    ci = i - offset
                    if ci < ext_lb:
                        continue
                    ref_low = min(lows[ci - ext_lb:ci])
                    if closes[ci] > ref_low:
                        i_eff = ci
                        break
                else:
                    i_eff = None

                if i_eff is not None and i_eff != last_emitted_long:
                    # 2) MACD 底背离（条件参考 i_eff，全部使用 <= i 的数据，因果成立）
                    has_div = False
                    prev_trough = self._prev_hist_min(hist, i_eff, div_lb)
                    if prev_trough is not None and hist[i_eff] is not None and hist[prev_trough] is not None:
                        if closes[i_eff] < closes[prev_trough] and hist[i_eff] > hist[prev_trough]:  # type: ignore[operator]
                            has_div = True
                    # 3) 看涨形态
                    has_pattern = pinbars[i_eff] == 1 or engulfings[i_eff] == 1
                    # 触发: require_all → 三条件都满足；否则 背离 OR 形态 即可
                    if require_all:
                        if has_div and has_pattern:
                            signals[i] = 1
                            last_emitted_long = i_eff
                    else:
                        if has_div or has_pattern:
                            signals[i] = 1
                            last_emitted_long = i_eff

            # ── 做空 ──
            prev_high = max(highs[i - ext_lb:i])
            new_high_made = highs[i] >= prev_high or (i >= 1 and highs[i - 1] >= max(highs[i - ext_lb - 1:i - 1]))

            if new_high_made:
                for offset in range(retrace + 1):
                    ci = i - offset
                    if ci < ext_lb:
                        continue
                    ref_high = max(highs[ci - ext_lb:ci])
                    if closes[ci] < ref_high:
                        i_eff = ci
                        break
                else:
                    i_eff = None

                if i_eff is not None and i_eff != last_emitted_short:
                    # 2) MACD 顶背离（条件参考 i_eff，全部使用 <= i 的数据，因果成立）
                    has_div = False
                    prev_peak = self._prev_hist_max(hist, i_eff, div_lb)
                    if prev_peak is not None and hist[i_eff] is not None and hist[prev_peak] is not None:
                        if closes[i_eff] > closes[prev_peak] and hist[i_eff] < hist[prev_peak]:  # type: ignore[operator]
                            has_div = True
                    # 3) 看跌形态
                    has_pattern = pinbars[i_eff] == -1 or engulfings[i_eff] == -1
                    if require_all:
                        if has_div and has_pattern:
                            signals[i] = -1
                            last_emitted_short = i_eff
                    else:
                        if has_div or has_pattern:
                            signals[i] = -1
                            last_emitted_short = i_eff

        return signals

    @staticmethod
    def _prev_hist_min(hist: list, idx: int, lookback: int) -> int | None:
        """找 idx 之前 MACD 柱状图的上一段谷底位置（负值段的最小值）。"""
        start = max(0, idx - lookback * 3)
        neg = [(j, hist[j]) for j in range(start, idx) if hist[j] is not None and hist[j] < 0]
        if not neg:
            return None
        return min(neg, key=lambda x: x[1])[0]

    @staticmethod
    def _prev_hist_max(hist: list, idx: int, lookback: int) -> int | None:
        """找 idx 之前 MACD 柱状图的上一段峰顶位置（正值段的最大值）。"""
        start = max(0, idx - lookback * 3)
        pos = [(j, hist[j]) for j in range(start, idx) if hist[j] is not None and hist[j] > 0]
        if not pos:
            return None
        return max(pos, key=lambda x: x[1])[0]

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        arr = self.signal_array(candles, {})
        return _latest_hit(self.name, arr, candles, timeframe, self.strategy_type)


# ── 注册表 ────────────────────────────────────────────────────────────────────

STRATEGY_DETECTORS = [
    TrendStrategyDetector(),
    MomentumStrategyDetector(),
    ReversalStrategyDetector(),
]
