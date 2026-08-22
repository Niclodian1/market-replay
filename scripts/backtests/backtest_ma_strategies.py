"""
第一阶段均线突破策略回测。

固定品种：BTC、AAPL、XAUUSD、贵州茅台；固定参数：MA30/60/99/120。
策略 A：单均线突破（参考 backtest_ma_breakout.py），允许并行多笔持仓，
每笔持仓独立维护止损与盈亏。
"""

import datetime
import os
from pathlib import Path
import asyncio
import sys
import yfinance as yf
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

# ── 品种 ──────────────────────────────────────────────────────────────────────
SYMBOLS = {
    "BTC-USD":   "BTC",
    "AAPL":      "Apple",
    "XAUUSD":    "现货黄金",
    "600519.SS": "贵州茅台",
}

FEE_RATE = 0.001
SLIPPAGE = 0.0005
COST     = FEE_RATE + SLIPPAGE

BODY_RISE_MIN     = 0.03
BREAKEVEN_TRIGGER = 0.04

MA_PERIODS = [30, 60, 99, 120]
TREND_MA   = 120  # 策略 B 的趋势过滤均线

END_DATE = datetime.date.today().isoformat()
CRYPTO_SYMBOLS = {"BTC-USD"}


# ── 工具：滚动均线 / RSI ──────────────────────────────────────────────────────

RSI_PERIOD = 14


def rolling_rsi(values, n=RSI_PERIOD):
    """Wilder 平滑 RSI，长度与 values 相同，前 n 个为 None。"""
    out = [None] * len(values)
    if len(values) <= n:
        return out
    gains, losses = [], []
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains) / n
    avg_l = sum(losses) / n
    out[n] = 100 - 100 / (1 + avg_g / avg_l) if avg_l else 100.0
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
        out[i] = 100 - 100 / (1 + avg_g / avg_l) if avg_l else 100.0
    return out


def rolling_sma(values, n):
    out = [None] * len(values)
    if len(values) < n:
        return out
    s = sum(values[:n])
    out[n - 1] = s / n
    for i in range(n, len(values)):
        s += values[i] - values[i - n]
        out[i] = s / n
    return out


# ── 出场判定（单笔仓位） ─────────────────────────────────────────────────────

def _try_exit(p, i, c, lo, ma, prev_c, prev_ma, ma_exit_pct=0.0):
    """更新 p 的浮动止损/最高价；若触发出场返回 (eff_exit, exit_reason)，否则 (None, None)。
    ma_exit_pct: 收盘低于均线的最小幅度阈值。
      0    = 收盘跌破均线即出场
      0.03 = 收盘低于均线 3% 及以上才出场（可跨多根K线累积）
    判断仅看当根收盘与当根均线的距离，与前一根无关。
    """
    # 上一根K线收盘已触发保本条件，本根K线才正式生效
    if p.get("breakeven_pending"):
        p["stop_loss"]      = p["entry_close"]
        p["breakeven_set"]  = True
        p["breakeven_pending"] = False

    if c > p["max_close"]:
        p["max_close"] = c
    # 收盘触发保本条件：下一根K线起止损移至成本价
    if not p["breakeven_set"] and not p.get("breakeven_pending") and c / p["entry_close"] - 1 > BREAKEVEN_TRIGGER:
        p["breakeven_pending"] = True

    # 收盘低于均线的幅度 ≥ ma_exit_pct 即触发均线出场（无需穿越动作，累积跌幅即可）
    ma_exit   = (ma is not None and ma > 0 and (ma - c) / ma >= ma_exit_pct)
    stop_exit = lo <= p["stop_loss"]

    if stop_exit:
        return p["stop_loss"] * (1 - COST), ("保本" if p["breakeven_set"] else "止损")
    if ma_exit:
        return c * (1 - COST), "均线"
    return None, None


def _close_position(p, i, eff_exit, reason, trade_rets, trade_log):
    ret     = eff_exit / p["eff_entry"] - 1
    risk    = p["eff_entry"] - p["initial_stop_loss"]  # 始终用入场时的初始止损
    reward  = eff_exit - p["eff_entry"]
    rr      = (reward / risk) if (risk > 0 and ret > 0) else None
    max_ret = p["max_close"] * (1 - COST) / p["eff_entry"] - 1
    trade_rets.append(ret)
    trade_log.append((p["entry_idx"], i, ret, reason, p["stop_loss"], rr, max_ret))
    return ret


def _step(open_positions, i, c, lo, ma, prev_c, prev_ma, trade_rets, trade_log, ma_exit_pct=0.0):
    """处理所有持仓的出场；返回 (still_open, realized_delta, unrealized_total)。"""
    still_open = []
    realized_delta = 0.0
    for p in open_positions:
        eff_exit, reason = _try_exit(p, i, c, lo, ma, prev_c, prev_ma, ma_exit_pct)
        if reason:
            realized_delta += _close_position(p, i, eff_exit, reason, trade_rets, trade_log)
        else:
            still_open.append(p)
    unreal = sum((c * (1 - COST) / p["eff_entry"] - 1) for p in still_open)
    return still_open, realized_delta, unreal


def _force_close_all(open_positions, closes, trade_rets, trade_log):
    realized_delta = 0.0
    last_i = len(closes) - 1
    for p in open_positions:
        eff_exit = closes[-1] * (1 - COST)
        realized_delta += _close_position(p, last_i, eff_exit, "强制平仓", trade_rets, trade_log)
    return realized_delta


# ── 策略 A：单均线突破（并行持仓） ───────────────────────────────────────────

def run_breakout(closes, lows, opens, dates, n):
    """K线收盘上穿 MA + 实体涨幅 > 3% 入场。已有持仓时仍可开新仓，每笔单独计算。"""
    if len(closes) <= n:
        return None
    sma = rolling_sma(closes, n)

    open_positions = []
    trade_rets, trade_log, curve = [], [], []
    realized = 0.0

    for i in range(len(closes)):
        c, lo, ma = closes[i], lows[i], sma[i]
        if ma is None:
            curve.append(1.0 + realized)
            continue
        prev_ma = sma[i - 1] if i > 0 else None
        prev_c  = closes[i - 1] if i > 0 else None

        # 先处理所有已有仓位的出场（均线出场需跌破幅度 > 3%）
        open_positions, d, _ = _step(open_positions, i, c, lo, ma, prev_c, prev_ma,
                                     trade_rets, trade_log, ma_exit_pct=0.03)
        realized += d

        # 再判断是否开新仓（已有持仓不影响）
        if prev_ma is not None and prev_c is not None:
            op = opens[i]
            body_rise = (c / op - 1) if op and op > 0 else 0
            if prev_c <= prev_ma and c > ma and body_rise > BODY_RISE_MIN:
                open_positions.append({
                    "eff_entry": c * (1 + COST), "entry_close": c,
                    "stop_loss": lo, "initial_stop_loss": lo,
                    "breakeven_set": False, "breakeven_pending": False, "max_close": c, "entry_idx": i,
                })

        unreal = sum((c * (1 - COST) / p["eff_entry"] - 1) for p in open_positions)
        curve.append(1.0 + realized + unreal)

    realized += _force_close_all(open_positions, closes, trade_rets, trade_log)
    if curve:
        curve[-1] = 1.0 + realized

    return summarize(n, realized, trade_rets, trade_log, curve, dates, closes)


# ── 策略 B：MA120 之上的均线回调入场（并行持仓） ─────────────────────────────

def run_pullback(closes, lows, opens, dates, n, trend_sma, rsi_vals):
    """收盘 > MA120 + low 触及 MAn + 收盘 > MAn + RSI(14) < 50 入场；已有持仓时仍可开新仓。"""
    if len(closes) <= max(n, TREND_MA):
        return None
    sma = rolling_sma(closes, n)

    open_positions = []
    trade_rets, trade_log, curve = [], [], []
    realized = 0.0

    for i in range(len(closes)):
        c, lo, ma = closes[i], lows[i], sma[i]
        tma = trend_sma[i]
        rsi = rsi_vals[i]
        if ma is None or tma is None or rsi is None:
            curve.append(1.0 + realized)
            continue
        prev_ma = sma[i - 1] if i > 0 else None
        prev_c  = closes[i - 1] if i > 0 else None

        open_positions, d, _ = _step(open_positions, i, c, lo, ma, prev_c, prev_ma,
                                     trade_rets, trade_log, ma_exit_pct=0.03)
        realized += d

        if lo <= ma and c > ma and c > tma and rsi < 50:
            open_positions.append({
                "eff_entry": c * (1 + COST), "entry_close": c,
                "stop_loss": lo, "initial_stop_loss": lo,
                "breakeven_set": False, "max_close": c, "entry_idx": i,
            })

        unreal = sum((c * (1 - COST) / p["eff_entry"] - 1) for p in open_positions)
        curve.append(1.0 + realized + unreal)

    realized += _force_close_all(open_positions, closes, trade_rets, trade_log)
    if curve:
        curve[-1] = 1.0 + realized

    return summarize(n, realized, trade_rets, trade_log, curve, dates, closes)


# ── 通用：统计（每笔独立计 P&L，total_return = 累计单笔收益之和） ────────────

def summarize(n, realized, trade_rets, trade_log, curve, dates, closes):
    if not trade_rets:
        return None
    total_return = realized * 100
    n_trades = len(trade_rets)
    wins     = [r for r in trade_rets if r > 0]
    losses   = [r for r in trade_rets if r <= 0]
    win_rate = len(wins) / n_trades * 100
    max_profit = max(trade_rets) * 100
    avg_profit = (sum(wins) / len(wins) * 100) if wins else 0.0

    # curve 是“每笔 100% 名义本金、逐笔收益相加”的独立交易曲线，不是账户净值。
    # 因此回撤使用累计收益的百分点差，而不是除以可能小于零的虚拟净值。
    peak, max_dd = curve[0], 0.0
    for v in curve:
        if v > peak:
            peak = v
        dd = (peak - v) * 100
        if dd > max_dd:
            max_dd = dd

    rr_values = [t[5] for t in trade_log if t[5] is not None]
    avg_rr = (sum(rr_values) / len(rr_values)) if rr_values else None
    expectancy = sum(trade_rets) / n_trades * 100
    avg_loss = (sum(losses) / len(losses) * 100) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    return {
        "n": n, "total_return": total_return, "n_trades": n_trades,
        "max_drawdown": max_dd, "win_rate": win_rate,
        "max_profit": max_profit, "avg_profit": avg_profit, "avg_rr": avg_rr,
        "avg_loss": avg_loss, "expectancy": expectancy,
        "profit_factor": profit_factor,
        "aggregation_method": "sum_of_independent_trade_returns",
        "trade_log": trade_log, "dates": dates, "closes": closes,
    }


# ── 数据下载 + 双策略调用 ────────────────────────────────────────────────────

def _load_crypto_candles(ticker, timeframe):
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    from app.providers.local_crypto import LocalCryptoProvider

    candles = asyncio.run(LocalCryptoProvider().get_full_history(ticker, timeframe))
    if not candles:
        return None
    return {
        "dates": [c["time"] for c in candles],
        "opens": [c["open"] for c in candles],
        "highs": [c["high"] for c in candles],
        "lows": [c["low"] for c in candles],
        "closes": [c["close"] for c in candles],
        "volumes": [c.get("volume", 0) for c in candles],
    }


def _load_yfinance_daily(ticker):
    df = yf.download(ticker, period="max", auto_adjust=True, progress=False)
    if df.empty:
        return None
    return {
        "dates": df.index.tolist(),
        "opens": df["Open"].squeeze().tolist(),
        "highs": df["High"].squeeze().tolist(),
        "lows": df["Low"].squeeze().tolist(),
        "closes": df["Close"].squeeze().tolist(),
        "volumes": df["Volume"].squeeze().fillna(0).tolist() if "Volume" in df else [0] * len(df),
    }


def _completed_daily_source(ticker, source):
    """Normalise timestamps and remove a still-forming final daily candle."""
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    from app.core.ma_opportunities import completed_daily_candles

    def timestamp(value):
        return int(value.timestamp()) if hasattr(value, "timestamp") else int(value)

    candles = [
        {
            "time": timestamp(source["dates"][i]),
            "open": source["opens"][i],
            "high": source["highs"][i],
            "low": source["lows"][i],
            "close": source["closes"][i],
            "volume": source.get("volumes", [0] * len(source["dates"]))[i],
        }
        for i in range(len(source["dates"]))
    ]
    completed = completed_daily_candles(ticker, candles)
    return {
        "dates": [c["time"] for c in completed],
        "opens": [c["open"] for c in completed],
        "highs": [c["high"] for c in completed],
        "lows": [c["low"] for c in completed],
        "closes": [c["close"] for c in completed],
        "volumes": [c.get("volume", 0) for c in completed],
    }


def backtest_symbol(ticker, timeframe="1day"):
    print(f"  下载数据: {ticker} {timeframe} ...", end=" ", flush=True)
    if ticker == "XAUUSD" and timeframe == "1day":
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / "backend"))
        from app.providers.dukascopy import get_xau_provider
        candles = asyncio.run(get_xau_provider().get_full_history(ticker, timeframe))
        ohlc_src = {
            "dates": [c["time"] for c in candles],
            "opens": [c["open"] for c in candles],
            "highs": [c["high"] for c in candles],
            "lows": [c["low"] for c in candles],
            "closes": [c["close"] for c in candles],
            "volumes": [c.get("volume", 0) for c in candles],
        }
        data_source = "Dukascopy XAUUSD Bid"
    elif ticker in CRYPTO_SYMBOLS and timeframe in ("1day", "4h"):
        ohlc_src = _load_crypto_candles(ticker, timeframe)
        data_source = "Binance历史 + OKX增量"
    elif timeframe == "1day":
        ohlc_src = _load_yfinance_daily(ticker)
        data_source = "Yahoo Finance"
    else:
        print("该周期无数据源，跳过")
        return None
    if ohlc_src and timeframe == "1day":
        ohlc_src = _completed_daily_source(ticker, ohlc_src)
    if not ohlc_src or len(ohlc_src["closes"]) < max(MA_PERIODS) + 10:
        print("无数据，跳过")
        return None
    closes = ohlc_src["closes"]
    highs  = ohlc_src["highs"]
    lows   = ohlc_src["lows"]
    opens  = ohlc_src["opens"]
    volumes = ohlc_src.get("volumes", [0] * len(closes))
    dates  = ohlc_src["dates"]
    print(f"{len(closes)} 条K线  ({str(dates[0])[:10]} ~ {str(dates[-1])[:10]})")

    sma_by_n  = {n: rolling_sma(closes, n) for n in MA_PERIODS}

    out = {"breakout": [], "pullback": []}
    for n in MA_PERIODS:
        r = run_breakout(closes, lows, opens, dates, n)
        if r: out["breakout"].append(r)

    out["breakout_best"] = max(out["breakout"], key=lambda x: x["total_return"]) if out["breakout"] else None
    out["pullback_best"] = None
    out["ohlc"] = {
        "dates": dates, "opens": opens, "highs": highs, "lows": lows,
        "closes": closes, "volumes": volumes,
        "sma_by_n": sma_by_n, "trend_sma": rolling_sma(closes, TREND_MA),
        "data_source": data_source,
        "adjusted": ticker in {"AAPL", "600519.SS"},
    }
    return out


# ── Excel 样式 ────────────────────────────────────────────────────────────────

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
SUBHDR_FILL = PatternFill("solid", fgColor="2E75B6")
BEST_FILL   = PatternFill("solid", fgColor="FFD966")
SEC_FILL    = PatternFill("solid", fgColor="2E75B6")
COL_FILL    = PatternFill("solid", fgColor="4472C4")
ALT_FILL    = PatternFill("solid", fgColor="F5F5F5")
WHITE_FILL  = PatternFill("solid", fgColor="FFFFFF")
WIN_FILL    = PatternFill("solid", fgColor="E2EFDA")
LOSS_FILL   = PatternFill("solid", fgColor="FCE4D6")
thin   = Side(style="thin", color="CCCCCC")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def _cell(ws, row, col, value=None, fmt=None, bold=False, size=10,
          color="000000", fill=None, align="center", wrap=False):
    cell = ws.cell(row, col)
    cell.value     = value
    cell.font      = Font(name="Arial", bold=bold, size=size, color=color)
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    cell.border    = BORDER
    if fill: cell.fill = fill
    if fmt:  cell.number_format = fmt
    return cell


def _hdr(ws, row, col, text, fill=HEADER_FILL, color="FFFFFF", size=10):
    return _cell(ws, row, col, text, bold=True, color=color, fill=fill, size=size)


def color_ret(val):
    if val > 0: return "375623"
    if val < 0: return "C00000"
    return "000000"


def fmt_date(d):
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]


# ── Sheet：策略汇总 ──────────────────────────────────────────────────────────

def write_summary_sheet(wb, all_results, title, key):
    ws = wb.create_sheet(title)

    ws.merge_cells("A1:K1")
    c = ws["A1"]
    c.value = (f"{title}  |  手续费 {FEE_RATE*100:.2f}%  滑点 {SLIPPAGE*100:.2f}%  "
               f"MA {MA_PERIODS}  |  截至 {END_DATE}")
    c.font  = Font(name="Arial", bold=True, size=12, color="FFFFFF")
    c.fill  = HEADER_FILL
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    headers = ["品种", "MA 周期 N", "总收益率 %", "交易次数", "最大回撤 %",
               "胜率 %", "最大单笔盈利 %", "平均盈利 %", "平均盈亏比 R", "是否最优", "备注"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 2, col, h, fill=SUBHDR_FILL)
    ws.row_dimensions[2].height = 22

    row = 3
    for sym, data in all_results.items():
        name = SYMBOLS.get(sym, sym)
        results = data.get(key) if data else None
        best    = data.get(f"{key}_best") if data else None
        if not results:
            _cell(ws, row, 1, f"{sym} ({name})", align="left")
            ws.merge_cells(f"B{row}:K{row}")
            _cell(ws, row, 2, "无数据", color="C00000")
            row += 1
            continue

        start_row = row
        for r in results:
            is_best = (best is not None and r["n"] == best["n"])
            fill = WIN_FILL if is_best else (ALT_FILL if row % 2 == 0 else WHITE_FILL)
            _cell(ws, row, 1,  f"{sym}\n({name})", align="left", fill=fill)
            _cell(ws, row, 2,  r["n"], fmt="0", fill=fill)
            _cell(ws, row, 3,  r["total_return"], fmt='0.00"%"',
                  color=color_ret(r["total_return"]), bold=is_best, fill=fill)
            _cell(ws, row, 4,  r["n_trades"], fmt="0", fill=fill)
            _cell(ws, row, 5,  r["max_drawdown"], fmt='0.00"%"',
                  color="C00000" if r["max_drawdown"] > 50 else "000000", fill=fill)
            _cell(ws, row, 6,  r["win_rate"], fmt='0.0"%"', fill=fill)
            _cell(ws, row, 7,  r["max_profit"], fmt='0.00"%"',
                  color=color_ret(r["max_profit"]), fill=fill)
            _cell(ws, row, 8,  r["avg_profit"], fmt='0.00"%"',
                  color=color_ret(r["avg_profit"]), fill=fill)
            avg_rr = r["avg_rr"]
            _cell(ws, row, 9,
                  round(avg_rr, 2) if avg_rr is not None else "-",
                  fmt="0.00" if avg_rr is not None else None, fill=fill)
            _cell(ws, row, 10, "★ 最优" if is_best else "",
                  bold=is_best, color="375623" if is_best else "000000", fill=fill)
            _cell(ws, row, 11, "", fill=fill)
            row += 1

        if row - start_row > 1:
            ws.merge_cells(f"A{start_row}:A{row-1}")
            ws[f"A{start_row}"].alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True)

    widths = [18, 10, 13, 10, 13, 10, 18, 14, 13, 10, 10]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"


# ── Sheet：策略对比（最优参数） ───────────────────────────────────────────────

def write_compare_sheet(wb, all_results):
    ws = wb.create_sheet("策略对比 - 最优")
    ws.merge_cells("A1:L1")
    c = ws["A1"]
    c.value = "两策略最优参数对比（按策略 A 总收益率排序）"
    c.font  = Font(name="Arial", bold=True, size=13, color="FFFFFF")
    c.fill  = HEADER_FILL
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    headers = ["品种代码", "品种名称",
               "A.最优MA", "A.收益%", "A.交易", "A.回撤%", "A.胜率%",
               "B.最优MA", "B.收益%", "B.交易", "B.回撤%", "B.胜率%"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 2, col, h, fill=SUBHDR_FILL)

    items = []
    for sym, data in all_results.items():
        if not data: continue
        a = data.get("breakout_best")
        b = data.get("pullback_best")
        if a or b:
            items.append((sym, a, b))
    items.sort(key=lambda x: (x[1]["total_return"] if x[1] else -1e9), reverse=True)

    for rank, (sym, a, b) in enumerate(items, 1):
        row  = rank + 2
        fill = ALT_FILL if rank % 2 == 0 else WHITE_FILL
        _cell(ws, row, 1, sym, fill=fill)
        _cell(ws, row, 2, SYMBOLS.get(sym, sym), fill=fill)
        if a:
            _cell(ws, row, 3, a["n"], fmt="0", bold=True, fill=fill)
            _cell(ws, row, 4, a["total_return"], fmt='0.00"%"',
                  color=color_ret(a["total_return"]), bold=True, fill=fill)
            _cell(ws, row, 5, a["n_trades"], fmt="0", fill=fill)
            _cell(ws, row, 6, a["max_drawdown"], fmt='0.00"%"', fill=fill)
            _cell(ws, row, 7, a["win_rate"], fmt='0.0"%"', fill=fill)
        else:
            for col in range(3, 8): _cell(ws, row, col, "-", fill=fill)
        if b:
            _cell(ws, row, 8, b["n"], fmt="0", bold=True, fill=fill)
            _cell(ws, row, 9, b["total_return"], fmt='0.00"%"',
                  color=color_ret(b["total_return"]), bold=True, fill=fill)
            _cell(ws, row, 10, b["n_trades"], fmt="0", fill=fill)
            _cell(ws, row, 11, b["max_drawdown"], fmt='0.00"%"', fill=fill)
            _cell(ws, row, 12, b["win_rate"], fmt='0.0"%"', fill=fill)
        else:
            for col in range(8, 13): _cell(ws, row, col, "-", fill=fill)

    widths = [12, 14, 10, 12, 10, 12, 10, 10, 12, 10, 12, 10]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"


# ── Sheet：逐笔明细 ─────────────────────────────────────────────────────────

TRADE_COLS = ["入场日期", "出场日期", "出场原因", "入场有效价", "出场有效价",
              "单笔收益率 %", "最大盈利率 %", "盈亏比 R", "盈亏"]
N_TRADE_COLS = len(TRADE_COLS)


def write_trade_sheet(wb, sym, results, best_n, prefix):
    safe_name = f"{prefix}-{sym}".replace("/", "-").replace(":", "-")[:28]
    ws = wb.create_sheet(safe_name)

    ws.merge_cells(f"A1:{get_column_letter(N_TRADE_COLS)}1")
    c = ws["A1"]
    c.value = (f"{prefix} | {sym} ({SYMBOLS.get(sym, sym)})  —  "
               f"手续费 {FEE_RATE*100:.2f}%  滑点 {SLIPPAGE*100:.2f}%  |  最优 MA={best_n}")
    c.font  = Font(name="Arial", bold=True, size=12, color="FFFFFF")
    c.fill  = HEADER_FILL
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26

    cur_row = 2
    for r in results:
        n       = r["n"]
        is_best = (n == best_n)
        trades  = r["trade_log"]
        dates   = r["dates"]
        closes  = r["closes"]

        sec_fill  = BEST_FILL if is_best else SEC_FILL
        sec_color = "8B4513" if is_best else "FFFFFF"
        ws.merge_cells(f"A{cur_row}:{get_column_letter(N_TRADE_COLS)}{cur_row}")
        title_cell = ws.cell(cur_row, 1)
        _prefix = f"{'★ 最优  ' if is_best else ''}MA = {n}  |  "
        _base   = (f"总收益 {r['total_return']:.2f}%  |  "
                   f"交易 {r['n_trades']} 笔  |  "
                   f"胜率 {r['win_rate']:.1f}%  |  "
                   f"最大回撤 {r['max_drawdown']:.1f}%  |  "
                   f"最大单笔盈利 {r['max_profit']:.2f}%")
        title_cell.value = (
            _prefix + _base + f"  |  平均盈亏比 {r['avg_rr']:.2f}R"
            if r['avg_rr'] else _prefix + _base
        )
        title_cell.font      = Font(name="Arial", bold=True, size=10, color=sec_color)
        title_cell.fill      = sec_fill
        title_cell.alignment = Alignment(horizontal="left", vertical="center")
        title_cell.border    = BORDER
        ws.row_dimensions[cur_row].height = 20
        cur_row += 1

        for col, h in enumerate(TRADE_COLS, 1):
            _hdr(ws, cur_row, col, h, fill=COL_FILL, size=9)
        ws.row_dimensions[cur_row].height = 18
        cur_row += 1

        if not trades:
            ws.merge_cells(f"A{cur_row}:{get_column_letter(N_TRADE_COLS)}{cur_row}")
            _cell(ws, cur_row, 1, "该参数下无交易记录", color="888888",
                  fill=WHITE_FILL, align="left")
            cur_row += 1
        else:
            for (ei, xi, ret, reason, sl_px, rr, max_ret) in trades:
                fill = WIN_FILL if ret > 0 else LOSS_FILL
                entry_date = fmt_date(dates[ei])
                exit_date  = fmt_date(dates[xi])
                entry_px   = closes[ei] * (1 + COST)
                exit_px    = (sl_px * (1 - COST) if reason in ("止损", "保本")
                              else closes[xi] * (1 - COST))
                reason_color = ("C00000" if reason == "止损"
                                else "D06000" if reason == "保本"
                                else "000000")
                _cell(ws, cur_row, 1, entry_date, fill=fill)
                _cell(ws, cur_row, 2, exit_date,  fill=fill)
                _cell(ws, cur_row, 3, reason,     fill=fill, color=reason_color)
                _cell(ws, cur_row, 4, round(entry_px, 6), fmt="#,##0.######", fill=fill)
                _cell(ws, cur_row, 5, round(exit_px, 6),  fmt="#,##0.######", fill=fill)
                _cell(ws, cur_row, 6, ret * 100, fmt='0.00"%"',
                      color=color_ret(ret), bold=(ret > 0), fill=fill)
                _cell(ws, cur_row, 7, max_ret * 100, fmt='0.00"%"',
                      color="375623", bold=True, fill=fill)
                if rr is not None:
                    _cell(ws, cur_row, 8, round(rr, 2), fmt="0.00",
                          color="375623", bold=True, fill=fill)
                else:
                    _cell(ws, cur_row, 8, "-", color="888888", fill=fill)
                _cell(ws, cur_row, 9, "盈" if ret > 0 else "亏",
                      color="375623" if ret > 0 else "C00000",
                      bold=True, fill=fill)
                cur_row += 1
        cur_row += 1

    widths = [14, 14, 10, 16, 16, 15, 15, 12, 8]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main(only_symbol=None, skip_excel=False, skip_html=False, timeframe="1day"):
    if timeframe not in ("1day", "4h"):
        raise SystemExit("未知周期：仅支持 1day / 4h")
    targets = [only_symbol] if only_symbol else (list(CRYPTO_SYMBOLS) if timeframe == "4h" else list(SYMBOLS.keys()))
    if only_symbol and only_symbol not in SYMBOLS:
        raise SystemExit(f"未知品种：{only_symbol}（合法范围：{', '.join(SYMBOLS)})")

    all_results = {}
    for sym in targets:
        print(f"\n[{sym}]")
        data = backtest_symbol(sym, timeframe=timeframe)
        all_results[sym] = data
        if data:
            for key, label in [("breakout_best", "突破"), ("pullback_best", "回调")]:
                b = data.get(key)
                if b:
                    rr = f"  盈亏比={b['avg_rr']:.2f}R" if b["avg_rr"] else ""
                    print(f"  [{label}] 最优 MA={b['n']}  收益={b['total_return']:.1f}%  "
                          f"胜率={b['win_rate']:.1f}%  回撤={b['max_drawdown']:.1f}%{rr}")
                else:
                    print(f"  [{label}] 无有效交易")

    # ── Excel：仅在全量回测时输出（单品种增量更新跳过） ──
    _SCRIPT_DIR = Path(__file__).resolve().parent
    if timeframe == "1day" and not skip_excel and not only_symbol:
        out_path = str(_SCRIPT_DIR / "data" / "ma_strategies_backtest.xlsx")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        print("\n生成 Excel ...")
        wb = openpyxl.Workbook()
        default = wb.active
        wb.remove(default)
        write_compare_sheet(wb, all_results)
        write_summary_sheet(wb, all_results, "策略A - 单均线突破（均线出场>3%）", "breakout")
        write_summary_sheet(wb, all_results, "策略B - MA120上回调+RSI<50", "pullback")
        for sym, data in all_results.items():
            if not data: continue
            if data.get("breakout") and data.get("breakout_best"):
                write_trade_sheet(wb, sym, data["breakout"], data["breakout_best"]["n"], "A突破")
            if data.get("pullback") and data.get("pullback_best"):
                write_trade_sheet(wb, sym, data["pullback"], data["pullback_best"]["n"], "B回调")
        wb.save(out_path)
        print(f"✅ 已保存至 {out_path}")

    # ── 导出 JSON 供前端 /ma-strategies 页面读取 ──
    from backtest_json_export import export_json, write_manifest
    json_dir = str(Path(__file__).resolve().parents[2] / "backend" / "data" / ("ma_strategies" if timeframe == "1day" else f"ma_strategies_{timeframe}"))
    os.makedirs(json_dir, exist_ok=True)
    print(f"\n导出 JSON → {json_dir}")
    for sym, data in all_results.items():
        if not data or "ohlc" not in data:
            continue
        safe = sym.replace("/", "-")
        best_b = (data.get("breakout_best") or {}).get("n")
        best_p = (data.get("pullback_best") or {}).get("n")
        export_json(
            sym, SYMBOLS.get(sym, sym), data["ohlc"],
            data.get("breakout") or [], data.get("pullback") or [],
            os.path.join(json_dir, f"{safe}.json"),
            breakout_best=best_b, pullback_best=best_p,
        )
        print(f"  ✓ {sym}.json")
    # manifest 仅在全量回测时刷新（单品种不动 manifest）
    if not only_symbol:
        write_manifest(SYMBOLS, json_dir)
    print(f"✅ 已导出 JSON 到 {json_dir}/")

    if skip_html or only_symbol or timeframe != "1day":
        return

    # ── 交互式 HTML 图表（可选保留，与网站集成无关） ──
    try:
        from backtest_chart_export import export_symbol_html, write_index
    except ImportError as e:
        print(f"⚠️  跳过 HTML 图表导出：{e}（请 pip install plotly）")
        return

    chart_dir = "/Users/zhangzheyu/market-replay/data/ma_strategies_charts"
    os.makedirs(chart_dir, exist_ok=True)
    print(f"\n生成交互式 HTML 图表 → {chart_dir}")
    for sym, data in all_results.items():
        if not data or "ohlc" not in data:
            continue
        safe = sym.replace("/", "-")
        out_html = os.path.join(chart_dir, f"{safe}.html")
        export_symbol_html(sym, SYMBOLS.get(sym, sym), data["ohlc"],
                           data.get("breakout") or [], data.get("pullback") or [],
                           out_html)
    write_index(SYMBOLS, chart_dir)
    print(f"✅ 已保存 HTML 图表至 {chart_dir}/index.html")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="只回测该品种（默认全部）")
    parser.add_argument("--timeframe", choices=["1day", "4h"], default="1day", help="回测周期")
    parser.add_argument("--no-excel", action="store_true", help="跳过 Excel 输出")
    parser.add_argument("--no-html", action="store_true", help="跳过 plotly HTML 输出")
    args = parser.parse_args()
    main(only_symbol=args.symbol, skip_excel=args.no_excel, skip_html=args.no_html, timeframe=args.timeframe)
