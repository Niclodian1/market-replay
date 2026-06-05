"""
W 底 + MACD 看涨背离策略回测（Elder《以交易为生》）。

数据：BTC，三个周期 1D / 4H / 1H，使用项目内 OKX 数据源（BinanceProvider）。
产出：
  reports/w_macd/btc_w_macd_backtest.xlsx
  reports/w_macd/btc_1day_trades.html
  reports/w_macd/btc_4h_trades.html
  reports/w_macd/btc_1h_trades.html

运行：python backtest_w_macd.py
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.providers.binance import BinanceProvider  # noqa: E402

# ------------------------------ 配置 ------------------------------

OUT_DIR = os.path.join(ROOT, "reports", "w_macd")
CACHE_DIR = os.path.join(ROOT, "backend", "data", "w_macd_cache")
SYMBOL = "BTC-USD"
TIMEFRAMES = ["1day", "4h", "1h"]
COST = 0.0015  # 0.1% 手续费 + 0.05% 滑点

PARAMS_BY_TF: dict[str, dict] = {
    "1day": dict(swing_window=5,  min_distance=15, tolerance_pct=0.035, min_rally_pct=0.07,
                 downtrend_lookback=90,  min_decline_pct=0.10, max_hold=40),
    "4h":   dict(swing_window=8,  min_distance=30, tolerance_pct=0.025, min_rally_pct=0.05,
                 downtrend_lookback=240, min_decline_pct=0.08, max_hold=60),
    "1h":   dict(swing_window=12, min_distance=48, tolerance_pct=0.02,  min_rally_pct=0.035,
                 downtrend_lookback=480, min_decline_pct=0.06, max_hold=120),
}

# ------------------------------ 指标 ------------------------------


def _ema(values: list[float], period: int) -> list[Optional[float]]:
    n = len(values)
    result: list[Optional[float]] = [None] * n
    if n < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, n):
        result[i] = values[i] * k + result[i - 1] * (1 - k)  # type: ignore[operator]
    return result


def _macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    n = len(values)
    ema_f = _ema(values, fast)
    ema_s = _ema(values, slow)
    macd_line: list[Optional[float]] = [
        (ema_f[i] - ema_s[i]) if (ema_f[i] is not None and ema_s[i] is not None) else None
        for i in range(n)
    ]
    first = next((i for i, v in enumerate(macd_line) if v is not None), None)
    if first is None:
        return macd_line, [None] * n, [None] * n
    valid = [v for v in macd_line if v is not None]
    sig_valid = _ema(valid, signal)
    sig_line: list[Optional[float]] = [None] * n
    for j, i in enumerate(range(first, n)):
        sig_line[i] = sig_valid[j]
    hist: list[Optional[float]] = [
        (macd_line[i] - sig_line[i])  # type: ignore[operator]
        if (macd_line[i] is not None and sig_line[i] is not None)
        else None
        for i in range(n)
    ]
    return macd_line, sig_line, hist


# ------------------------------ 数据加载 ------------------------------


def _cache_path(tf: str) -> str:
    return os.path.join(CACHE_DIR, f"btc_{tf}.json")


async def _fetch_btc(tf: str) -> list[dict]:
    provider = BinanceProvider()
    return await provider.get_full_history(SYMBOL, tf)


def load_btc(tf: str, force: bool = False) -> list[dict]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = _cache_path(tf)
    if not force and os.path.exists(cp):
        with open(cp, "r") as f:
            return json.load(f)
    print(f"  [{tf}] 拉取 OKX 全量历史 ...", flush=True)
    candles = asyncio.run(_fetch_btc(tf))
    with open(cp, "w") as f:
        json.dump(candles, f)
    print(f"  [{tf}] 已缓存 {len(candles)} 根 → {cp}", flush=True)
    return candles


# ------------------------------ W底 + 背离信号 ------------------------------


def find_w_bottoms(
    candles: list[dict], swing_window: int, min_distance: int,
    tolerance_pct: float, min_rally_pct: float,
    downtrend_lookback: int, min_decline_pct: float,
) -> list[tuple[int, int, int]]:
    """
    严格的 W 底识别（Elder 风格三段式）：

      ── 第一段：清晰下跌
         在 j 之前的 downtrend_lookback 窗口内找到一根高点 peak，
         peak 之后价格延续下跌到 idx1 = 该窗口内"收盘价最低"的那根，
         decline = (high[peak] − close[idx1]) / high[peak] ≥ min_decline_pct。

      ── 第二段：中间反弹
         idx1 → j 之间出现一笔反弹，最高点 mid_idx，
         rally = (high[mid_idx] − close[idx1]) / close[idx1] ≥ min_rally_pct。

      ── 第三段：再次触及第一段最低收盘价
         j 的 low 进入 close[idx1] 的 ±tolerance 区间，且 close[j] 未明显跌破。
         j 同时被 swing_window 确认为局部低点。

    返回 (idx1, j, mid_idx) 三元组；MACD 背离过滤由调用方完成。
    """
    n = len(candles)
    out: list[tuple[int, int, int]] = []
    if n < downtrend_lookback + swing_window + min_distance:
        return out

    lows = [c["low"] for c in candles]
    highs = [c["high"] for c in candles]
    closes = [c["close"] for c in candles]

    last_j = -1
    for j in range(downtrend_lookback + swing_window, n - swing_window):
        if j - last_j < min_distance:
            pass  # 重叠检查放到结尾，先允许进入

        # 第三段先做廉价检查：j 必须是 swing_window 内的局部低点
        cur_low = lows[j]
        is_swing_low = True
        for k in range(j - swing_window, j + swing_window + 1):
            if k == j:
                continue
            if lows[k] < cur_low:
                is_swing_low = False
                break
        if not is_swing_low:
            continue

        # 第一段：在 [j - downtrend_lookback, j - min_distance] 范围内寻找"收盘价最低"作为 idx1
        lo, hi = max(0, j - downtrend_lookback), j - min_distance
        if hi - lo < swing_window * 2:
            continue
        idx1 = lo
        c_min = closes[lo]
        for k in range(lo + 1, hi + 1):
            if closes[k] < c_min:
                c_min = closes[k]
                idx1 = k
        if c_min <= 0:
            continue

        # 找前置 peak：idx1 之前 downtrend_lookback 窗口内的最高 high
        plo = max(0, idx1 - downtrend_lookback)
        if idx1 - plo < swing_window:
            continue
        peak_idx = plo
        p_max = highs[plo]
        for k in range(plo + 1, idx1):
            if highs[k] > p_max:
                p_max = highs[k]
                peak_idx = k
        if peak_idx >= idx1:
            continue
        decline = (p_max - c_min) / p_max
        if decline < min_decline_pct:
            continue
        # idx1 必须是 peak→idx1 区间的收盘最低点（保证下跌延续到 idx1）
        if min(closes[peak_idx:idx1 + 1]) < c_min - 1e-9:
            continue

        # 第二段：idx1 → j 之间的反弹高点
        if j - idx1 < min_distance:
            continue
        mid_slice = highs[idx1 + 1:j]
        if not mid_slice:
            continue
        mid_high = max(mid_slice)
        rally = (mid_high - c_min) / c_min
        if rally < min_rally_pct:
            continue
        mid_idx = idx1 + 1 + mid_slice.index(mid_high)

        # 第三段：j 的 low 重新触及 close[idx1] 的容差区间
        lower_band = c_min * (1 - tolerance_pct)
        upper_band = c_min * (1 + tolerance_pct)
        if not (lower_band <= lows[j] <= upper_band):
            continue
        # close[j] 未明显跌破前段收盘低（守住支撑）
        if closes[j] < c_min * (1 - tolerance_pct):
            continue
        # j 与 idx1 之间确实出现了反弹回撤结构（mid_idx 在中段，j 之前已开始回落）
        if mid_idx <= idx1 or mid_idx >= j:
            continue

        # 与上一笔 W 底不重叠
        if last_j >= 0 and j - last_j < min_distance:
            continue

        out.append((idx1, j, mid_idx))
        last_j = j

    return out


def macd_bull_div(hist: list[Optional[float]], lows: list[float], idx1: int, idx2: int) -> bool:
    """看涨背离：价格 idx2 低 <= idx1 低（或差距 < 0.5%），但 hist[idx2] > hist[idx1]，且 hist 在 idx2 附近抬头。"""
    h1, h2 = hist[idx1], hist[idx2]
    if h1 is None or h2 is None:
        return False
    if h2 <= h1:
        return False
    # 第二底处 histogram 正在抬头
    if idx2 >= 1 and hist[idx2 - 1] is not None and h2 <= hist[idx2 - 1]:  # type: ignore[operator]
        return False
    return True


def macd_bear_div(hist: list[Optional[float]], highs: list[float], i: int, look_back: int = 20) -> bool:
    """从 i 向前 look_back 找前一个 hist 局部峰值；若当前价创新高、hist 低于该峰值则视为看跌背离。"""
    if i < look_back + 2:
        return False
    if hist[i] is None or hist[i - 1] is None:
        return False
    # 寻找前一个 hist 局部峰
    prev_peak_idx = None
    prev_peak_val = -math.inf
    for k in range(i - look_back, i - 2):
        hv = hist[k]
        hkm = hist[k - 1] if k - 1 >= 0 else None
        hkp = hist[k + 1] if k + 1 < len(hist) else None
        if hv is None or hkm is None or hkp is None:
            continue
        if hv > hkm and hv > hkp and hv > 0 and hv > prev_peak_val:
            prev_peak_val = hv
            prev_peak_idx = k
    if prev_peak_idx is None:
        return False
    # 价格创新高 + hist 当前峰 < 前峰
    if highs[i] <= highs[prev_peak_idx]:
        return False
    cur_h = hist[i]
    if cur_h is None or cur_h >= prev_peak_val:
        return False
    # 当前必须是局部 hist 峰（已下行）
    if hist[i - 1] is not None and cur_h > hist[i - 1]:  # type: ignore[operator]
        return False
    return True


# ------------------------------ 回测 ------------------------------


@dataclass
class Trade:
    tf: str
    entry_idx: int
    entry_ts: int
    entry_px: float
    exit_idx: int
    exit_ts: int
    exit_px: float
    stop: float
    target: float
    pnl_pct: float
    max_favor_pct: float
    max_adverse_pct: float
    bars_held: int
    exit_reason: str  # 止损 / 目标 / 背离 / 超时
    bottom1_idx: int
    bottom2_idx: int
    neck_idx: int


EXIT_REASONS = ("止损", "目标", "背离", "超时")


def run_single_tf(candles: list[dict], tf: str) -> list[Trade]:
    params = PARAMS_BY_TF[tf]
    n = len(candles)
    if n < 100:
        return []

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    opens = [c["open"] for c in candles]
    times = [c["time"] for c in candles]

    _, _, hist = _macd(closes)

    pairs = find_w_bottoms(
        candles,
        swing_window=params["swing_window"],
        min_distance=params["min_distance"],
        tolerance_pct=params["tolerance_pct"],
        min_rally_pct=params["min_rally_pct"],
        downtrend_lookback=params["downtrend_lookback"],
        min_decline_pct=params["min_decline_pct"],
    )

    trades: list[Trade] = []
    last_exit_idx = -1
    for idx1, idx2, mid_idx in pairs:
        # 背离过滤
        if not macd_bull_div(hist, lows, idx1, idx2):
            continue
        entry_idx = idx2 + 1
        if entry_idx >= n:
            continue
        if entry_idx <= last_exit_idx:  # 不重叠开仓
            continue

        entry_px = opens[entry_idx] * (1 + COST)
        stop = min(lows[idx1], lows[idx2]) * (1 - 0.005)
        target = highs[mid_idx]  # 颈线
        if target <= entry_px or stop >= entry_px:
            continue

        max_hold = params["max_hold"]
        exit_idx = min(entry_idx + max_hold, n - 1)
        exit_reason = "超时"
        exit_px = closes[exit_idx] * (1 - COST)
        max_favor = -math.inf
        max_adverse = math.inf

        for k in range(entry_idx, min(entry_idx + max_hold + 1, n)):
            hi, lo = highs[k], lows[k]
            # 跟踪极值（按收盘后的最高最低）
            max_favor = max(max_favor, (hi - entry_px) / entry_px)
            max_adverse = min(max_adverse, (lo - entry_px) / entry_px)
            # 止损优先（同根 K 内若同时触发止损与目标，保守按止损）
            if lo <= stop:
                exit_idx = k
                exit_px = stop * (1 - COST)
                exit_reason = "止损"
                break
            if hi >= target:
                exit_idx = k
                exit_px = target * (1 - COST)
                exit_reason = "目标"
                break
            # 背离离场（盈利状态下）
            if closes[k] > entry_px and macd_bear_div(hist, highs, k):
                nxt = k + 1
                if nxt < n:
                    exit_idx = nxt
                    exit_px = opens[nxt] * (1 - COST)
                    exit_reason = "背离"
                    break
            if k == min(entry_idx + max_hold, n - 1):
                exit_idx = k
                exit_px = closes[k] * (1 - COST)
                exit_reason = "超时"
                break

        pnl = (exit_px - entry_px) / entry_px * 100
        bars = exit_idx - entry_idx + 1
        trades.append(Trade(
            tf=tf,
            entry_idx=entry_idx, entry_ts=times[entry_idx], entry_px=entry_px,
            exit_idx=exit_idx, exit_ts=times[exit_idx], exit_px=exit_px,
            stop=stop, target=target,
            pnl_pct=pnl,
            max_favor_pct=(max_favor if max_favor != -math.inf else 0.0) * 100,
            max_adverse_pct=(max_adverse if max_adverse != math.inf else 0.0) * 100,
            bars_held=bars,
            exit_reason=exit_reason,
            bottom1_idx=idx1, bottom2_idx=idx2, neck_idx=mid_idx,
        ))
        last_exit_idx = exit_idx

    return trades


def summarize(trades: list[Trade]) -> dict:
    n = len(trades)
    if n == 0:
        return dict(n_trades=0, win_rate=0.0, avg_win=0.0, avg_loss=0.0,
                    max_win=0.0, max_loss=0.0, profit_factor=0.0,
                    cum_return=0.0, max_drawdown=0.0, avg_bars=0.0)
    wins = [t.pnl_pct for t in trades if t.pnl_pct > 0]
    losses = [t.pnl_pct for t in trades if t.pnl_pct <= 0]
    cum = 1.0
    curve = [1.0]
    for t in trades:
        cum *= (1 + t.pnl_pct / 100)
        curve.append(cum)
    peak = curve[0]
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        dd = (v - peak) / peak * 100
        mdd = min(mdd, dd)
    sum_w = sum(wins)
    sum_l = abs(sum(losses))
    return dict(
        n_trades=n,
        win_rate=len(wins) / n * 100,
        avg_win=sum_w / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        max_win=max(wins) if wins else 0.0,
        max_loss=min(losses) if losses else 0.0,
        profit_factor=(sum_w / sum_l) if sum_l > 0 else (float("inf") if sum_w > 0 else 0.0),
        cum_return=(cum - 1) * 100,
        max_drawdown=mdd,
        avg_bars=sum(t.bars_held for t in trades) / n,
    )


# ------------------------------ Excel 输出 ------------------------------

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
SUBHDR_FILL = PatternFill("solid", fgColor="2E75B6")
WIN_FILL = PatternFill("solid", fgColor="E2EFDA")
LOSS_FILL = PatternFill("solid", fgColor="FCE4D6")
WHITE = Font(color="FFFFFF", bold=True)
CENTER = Alignment(horizontal="center", vertical="center")


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def write_excel(per_tf: dict[str, tuple[list[Trade], dict]], path: str):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"

    headers = ["周期", "交易次数", "胜率%", "平均盈%", "平均亏%", "最大盈%", "最大亏%",
               "盈亏比", "累计收益%", "最大回撤%", "平均持仓K线"]
    for i, h in enumerate(headers, 1):
        c = ws.cell(1, i, h)
        c.fill = HEADER_FILL
        c.font = WHITE
        c.alignment = CENTER

    # 合计
    all_trades: list[Trade] = []
    for tf, (ts, _s) in per_tf.items():
        all_trades.extend(ts)
    overall = summarize(all_trades)

    rows = []
    for tf in TIMEFRAMES:
        s = per_tf[tf][1]
        rows.append([tf.upper(), s["n_trades"], s["win_rate"], s["avg_win"], s["avg_loss"],
                     s["max_win"], s["max_loss"],
                     s["profit_factor"] if math.isfinite(s["profit_factor"]) else "∞",
                     s["cum_return"], s["max_drawdown"], s["avg_bars"]])
    rows.append(["合计", overall["n_trades"], overall["win_rate"], overall["avg_win"], overall["avg_loss"],
                 overall["max_win"], overall["max_loss"],
                 overall["profit_factor"] if math.isfinite(overall["profit_factor"]) else "∞",
                 overall["cum_return"], overall["max_drawdown"], overall["avg_bars"]])

    for r_idx, row in enumerate(rows, 2):
        for c_idx, val in enumerate(row, 1):
            c = ws.cell(r_idx, c_idx, val)
            c.alignment = CENTER
            if c_idx >= 3 and isinstance(val, (int, float)) and not isinstance(val, bool):
                c.number_format = "0.00"
        if row[0] == "合计":
            for c_idx in range(1, len(row) + 1):
                ws.cell(r_idx, c_idx).fill = SUBHDR_FILL
                ws.cell(r_idx, c_idx).font = WHITE

    widths = [10, 10, 10, 10, 10, 10, 10, 10, 12, 12, 14]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # 每周期明细
    detail_headers = ["#", "入场时间", "入场价", "止损", "目标(颈线)", "出场时间", "出场价",
                      "收益%", "最大浮盈%", "最大浮亏%", "持仓K线", "离场原因"]
    for tf in TIMEFRAMES:
        trades = per_tf[tf][0]
        dws = wb.create_sheet(tf.upper())
        for i, h in enumerate(detail_headers, 1):
            c = dws.cell(1, i, h)
            c.fill = HEADER_FILL
            c.font = WHITE
            c.alignment = CENTER
        for r_idx, t in enumerate(trades, 2):
            row = [
                r_idx - 1,
                _fmt_ts(t.entry_ts), round(t.entry_px, 2),
                round(t.stop, 2), round(t.target, 2),
                _fmt_ts(t.exit_ts), round(t.exit_px, 2),
                round(t.pnl_pct, 2),
                round(t.max_favor_pct, 2),
                round(t.max_adverse_pct, 2),
                t.bars_held, t.exit_reason,
            ]
            fill = WIN_FILL if t.pnl_pct > 0 else LOSS_FILL
            for c_idx, val in enumerate(row, 1):
                c = dws.cell(r_idx, c_idx, val)
                c.alignment = CENTER
                c.fill = fill
        widths_d = [6, 18, 10, 10, 12, 18, 10, 10, 12, 12, 10, 10]
        for i, w in enumerate(widths_d, 1):
            dws.column_dimensions[get_column_letter(i)].width = w

    wb.save(path)


# ------------------------------ 可视化 ------------------------------


def export_trade_charts(candles: list[dict], trades: list[Trade], tf: str, out_path: str):
    """每周期一份 HTML，下拉切换每笔交易；上 K 线 + 标记，下 MACD。"""
    if not trades:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"<html><body><p>{tf}: 无交易</p></body></html>")
        return

    times = [datetime.fromtimestamp(c["time"], tz=timezone.utc) for c in candles]
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows_ = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    macd_line, sig_line, hist = _macd(closes)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.04,
        subplot_titles=("", ""),
    )

    # 每笔交易生成一组 trace（默认全部 visible=False，按钮切换）
    n_traces_per_trade = 7  # candle, markers, stop-line, target-line, hist, dif, dea
    base_traces_count = 0  # 不放公共底图，每笔独立窗口

    trade_meta: list[dict] = []

    for ti, t in enumerate(trades):
        pre = 60
        post = 20
        lo_i = max(0, min(t.bottom1_idx, t.entry_idx) - pre)
        hi_i = min(len(candles) - 1, t.exit_idx + post)
        x = times[lo_i:hi_i + 1]
        o = opens[lo_i:hi_i + 1]
        h = highs[lo_i:hi_i + 1]
        l = lows_[lo_i:hi_i + 1]
        c = closes[lo_i:hi_i + 1]
        m_h = hist[lo_i:hi_i + 1]
        m_m = macd_line[lo_i:hi_i + 1]
        m_s = sig_line[lo_i:hi_i + 1]

        vis = (ti == 0)
        # 1: K 线
        fig.add_trace(go.Candlestick(
            x=x, open=o, high=h, low=l, close=c,
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
            name=f"#{ti+1} K线", showlegend=False, visible=vis,
        ), row=1, col=1)
        # 2: 标记（两底 + 颈线高点 + 入场 + 出场）
        mk_x = [
            times[t.bottom1_idx], times[t.bottom2_idx], times[t.neck_idx],
            times[t.entry_idx], times[t.exit_idx],
        ]
        mk_y = [
            lows_[t.bottom1_idx], lows_[t.bottom2_idx], highs[t.neck_idx],
            t.entry_px, t.exit_px,
        ]
        mk_text = ["前底", "后底", "颈线", f"入场 {t.entry_px:.2f}",
                   f"出场[{t.exit_reason}] {t.exit_px:.2f} ({t.pnl_pct:+.2f}%)"]
        mk_color = ["#888", "#888", "#1f77b4", "#26a69a",
                    "#ef5350" if t.exit_reason == "止损" else
                    ("#42a5f5" if t.exit_reason == "目标" else
                     ("#ff9800" if t.exit_reason == "背离" else "#9e9e9e"))]
        mk_sym = ["triangle-down", "triangle-down", "diamond", "triangle-up", "triangle-down"]
        fig.add_trace(go.Scatter(
            x=mk_x, y=mk_y, mode="markers+text",
            marker=dict(size=[10, 12, 10, 14, 14], symbol=mk_sym, color=mk_color,
                        line=dict(width=1, color="#222")),
            text=mk_text, textposition="top center", textfont=dict(size=10),
            name=f"#{ti+1} 标记", showlegend=False, visible=vis,
            hoverinfo="text", hovertext=mk_text,
        ), row=1, col=1)
        # 3: 止损水平线
        fig.add_trace(go.Scatter(
            x=[x[0], x[-1]], y=[t.stop, t.stop], mode="lines",
            line=dict(color="#ef5350", width=1, dash="solid"),
            name=f"#{ti+1} 止损 {t.stop:.2f}", showlegend=False, visible=vis,
            hoverinfo="skip",
        ), row=1, col=1)
        # 4: 目标水平线
        fig.add_trace(go.Scatter(
            x=[x[0], x[-1]], y=[t.target, t.target], mode="lines",
            line=dict(color="#42a5f5", width=1, dash="dash"),
            name=f"#{ti+1} 目标 {t.target:.2f}", showlegend=False, visible=vis,
            hoverinfo="skip",
        ), row=1, col=1)
        # 5: MACD histogram
        hist_colors = ["#26a69a" if (v is not None and v >= 0) else "#ef5350" for v in m_h]
        fig.add_trace(go.Bar(
            x=x, y=m_h, marker_color=hist_colors, name=f"#{ti+1} Hist",
            showlegend=False, visible=vis,
        ), row=2, col=1)
        # 6: DIF
        fig.add_trace(go.Scatter(
            x=x, y=m_m, mode="lines",
            line=dict(color="#5aa7ff", width=1.2),
            name=f"#{ti+1} DIF", showlegend=False, visible=vis,
            hoverinfo="skip",
        ), row=2, col=1)
        # 7: DEA
        fig.add_trace(go.Scatter(
            x=x, y=m_s, mode="lines",
            line=dict(color="#ff9800", width=1.2),
            name=f"#{ti+1} DEA", showlegend=False, visible=vis,
            hoverinfo="skip",
        ), row=2, col=1)

        trade_meta.append(dict(
            label=f"#{ti+1} · {_fmt_ts(t.entry_ts)} · {t.pnl_pct:+.2f}% [{t.exit_reason}]",
            title=(f"BTC {tf.upper()} #{ti+1} | 入场 {t.entry_px:.2f} · "
                   f"止损 {t.stop:.2f} · 目标 {t.target:.2f} · "
                   f"出场[{t.exit_reason}] {t.exit_px:.2f} · "
                   f"收益 {t.pnl_pct:+.2f}% · 持仓 {t.bars_held} 根 · "
                   f"最大浮盈 {t.max_favor_pct:+.2f}% / 最大浮亏 {t.max_adverse_pct:+.2f}%"),
        ))

    # 构造下拉按钮
    total_traces = len(trades) * n_traces_per_trade
    buttons = []
    for ti in range(len(trades)):
        vis = [False] * total_traces
        for k in range(n_traces_per_trade):
            vis[ti * n_traces_per_trade + k] = True
        buttons.append(dict(
            label=trade_meta[ti]["label"],
            method="update",
            args=[{"visible": vis}, {"title.text": trade_meta[ti]["title"]}],
        ))

    fig.update_layout(
        title=dict(text=trade_meta[0]["title"], x=0.01, xanchor="left", font=dict(size=12)),
        template="plotly_white",
        height=820,
        margin=dict(l=50, r=20, t=120, b=40),
        hovermode="x unified",
        xaxis=dict(rangeslider=dict(visible=False)),
        xaxis2=dict(rangeslider=dict(visible=False)),
        updatemenus=[dict(
            buttons=buttons, direction="down",
            x=0.0, y=1.12, xanchor="left", yanchor="bottom",
            showactive=True, bgcolor="#f5f7fa", font=dict(size=10),
            pad=dict(l=4, r=4, t=4, b=4),
        )],
    )

    fig.write_html(out_path, include_plotlyjs="cdn")


# ------------------------------ Main ------------------------------


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    per_tf: dict[str, tuple[list[Trade], dict]] = {}

    for tf in TIMEFRAMES:
        print(f"[{tf}] 加载数据 ...", flush=True)
        candles = load_btc(tf)
        if len(candles) < 100:
            print(f"  数据过少 ({len(candles)})，跳过")
            per_tf[tf] = ([], summarize([]))
            continue
        print(f"  {len(candles)} 根 K 线，跑回测 ...", flush=True)
        trades = run_single_tf(candles, tf)
        stats = summarize(trades)
        print(f"  → {stats['n_trades']} 笔交易 · 胜率 {stats['win_rate']:.1f}% · "
              f"累计 {stats['cum_return']:+.1f}% · 回撤 {stats['max_drawdown']:.1f}%", flush=True)
        per_tf[tf] = (trades, stats)

        html_path = os.path.join(OUT_DIR, f"btc_{tf}_trades.html")
        export_trade_charts(candles, trades, tf, html_path)
        print(f"  图表 → {html_path}", flush=True)

    xlsx = os.path.join(OUT_DIR, "btc_w_macd_backtest.xlsx")
    write_excel(per_tf, xlsx)
    print(f"\nExcel → {xlsx}")


if __name__ == "__main__":
    main()
