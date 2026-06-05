"""
单均线突破策略回测
MA 参数范围：10~200，步长 10
品种：BTC-USD, ETH-USD + 美股市值前 10
含：手续费 + 滑点 / 盈亏比（盈利单）/ 全历史数据 / Excel 全均线明细
"""

import datetime
import yfinance as yf
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

# ── 品种 ──────────────────────────────────────────────────────────────────────
SYMBOLS = {
    "BTC-USD": "BTC",
    "ETH-USD": "ETH",
    "AAPL":    "Apple",
    "MSFT":    "Microsoft",
    "NVDA":    "NVIDIA",
    "GOOGL":   "Alphabet",
    "AMZN":    "Amazon",
    "META":    "Meta",
    "TSLA":    "Tesla",
    "AVGO":    "Broadcom",
    "BRK-B":   "Berkshire",
    "JPM":     "JPMorgan",
    "0700.HK": "腾讯",
    "9988.HK": "阿里巴巴",
    "1810.HK": "小米",
}

# ── 交易成本 ──────────────────────────────────────────────────────────────────
FEE_RATE = 0.001    # 单边手续费 0.1%
SLIPPAGE = 0.0005   # 单边滑点  0.05%
COST     = FEE_RATE + SLIPPAGE   # 单边总成本 0.15%

BODY_RISE_MIN     = 0.03   # K线实体上涨幅度门槛（收盘/开盘 - 1 > 3%）
BREAKEVEN_TRIGGER = 0.04   # 入场后上涨超过 4% 触发保本，止损移至入场收盘价

MA_PERIODS = list(range(10, 210, 10))   # 10, 20, ..., 200（共 20 个）

END_DATE = datetime.date.today().isoformat()


# ── 核心回测逻辑 ──────────────────────────────────────────────────────────────

def run_single(closes, lows, opens, dates, n):
    """
    单品种、单 MA 参数回测。
    trade_log 元素：(entry_idx, exit_idx, ret, exit_reason, stop_loss_px, rr)
    rr = 盈亏比，仅盈利单有值，亏损单为 None。
    入场额外条件：K线实体上涨幅度 (close/open - 1) > BODY_RISE_MIN。
    """
    if len(closes) <= n:
        return None

    sma = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        sma[i] = sum(closes[i - n + 1: i + 1]) / n

    position      = 0
    eff_entry     = 0.0   # 含成本的入场有效价
    entry_close   = 0.0   # 入场K收盘价（保本基准）
    stop_loss     = 0.0   # 止损价（入场K最低点，保本后移至入场收盘价）
    breakeven_set = False  # 是否已触发保本
    max_close     = 0.0   # 持仓期间最高收盘价
    capital       = 1.0
    trade_rets    = []
    curve         = []
    trade_log     = []
    entry_idx     = -1

    for i in range(len(closes)):
        c  = closes[i]
        lo = lows[i]
        ma = sma[i]

        if ma is None:
            curve.append(capital)
            continue

        if position == 0:
            if i > 0 and sma[i - 1] is not None:
                op = opens[i]
                body_rise = (c / op - 1) if op and op > 0 else 0
                if (closes[i - 1] <= sma[i - 1] and c > ma
                        and body_rise > BODY_RISE_MIN):
                    eff_entry     = c * (1 + COST)
                    entry_close   = c
                    stop_loss     = lo
                    breakeven_set = False
                    max_close     = c
                    entry_idx     = i
                    position      = 1
            curve.append(capital)

        else:
            if c > max_close:
                max_close = c

            # 保本：上涨超过 4% 后把止损移至入场收盘价
            if not breakeven_set and c / entry_close - 1 > BREAKEVEN_TRIGGER:
                stop_loss     = entry_close
                breakeven_set = True

            ma_exit   = (ma is not None and c < ma)
            stop_exit = lo <= stop_loss

            exit_reason = None
            eff_exit    = None

            if stop_exit:
                eff_exit    = stop_loss * (1 - COST)
                exit_reason = "保本" if breakeven_set else "止损"
            elif ma_exit:
                eff_exit    = c * (1 - COST)
                exit_reason = "均线"

            if exit_reason:
                ret      = eff_exit / eff_entry - 1
                risk     = eff_entry - stop_loss
                reward   = eff_exit  - eff_entry
                rr       = (reward / risk) if (risk > 0 and ret > 0) else None
                max_ret  = max_close * (1 - COST) / eff_entry - 1

                capital *= (eff_exit / eff_entry)
                trade_rets.append(ret)
                trade_log.append((entry_idx, i, ret, exit_reason, stop_loss, rr, max_ret))
                position = 0
                curve.append(capital)
            else:
                curve.append(capital * (c * (1 - COST) / eff_entry))

    # 强制平仓
    if position == 1:
        eff_exit = closes[-1] * (1 - COST)
        ret      = eff_exit / eff_entry - 1
        risk     = eff_entry - stop_loss
        reward   = eff_exit  - eff_entry
        rr       = (reward / risk) if (risk > 0 and ret > 0) else None
        max_ret  = max_close * (1 - COST) / eff_entry - 1
        capital *= (eff_exit / eff_entry)
        trade_rets.append(ret)
        trade_log.append((entry_idx, len(closes) - 1, ret, "强制平仓", stop_loss, rr, max_ret))
        curve[-1] = capital

    if not trade_rets:
        return None

    total_return = (capital - 1) * 100
    n_trades     = len(trade_rets)
    wins         = [r for r in trade_rets if r > 0]
    win_rate     = len(wins) / n_trades * 100
    max_profit   = max(trade_rets) * 100
    avg_profit   = (sum(wins) / len(wins) * 100) if wins else 0.0

    peak   = curve[0]
    max_dd = 0.0
    for v in curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    rr_values = [t[5] for t in trade_log if t[5] is not None]
    avg_rr    = (sum(rr_values) / len(rr_values)) if rr_values else None

    return {
        "n":            n,
        "total_return": total_return,
        "n_trades":     n_trades,
        "max_drawdown": max_dd,
        "win_rate":     win_rate,
        "max_profit":   max_profit,
        "avg_profit":   avg_profit,
        "avg_rr":       avg_rr,
        "trade_log":    trade_log,
        "dates":        dates,
        "closes":       closes,
    }


def backtest_symbol(ticker):
    print(f"  下载数据: {ticker} ...", end=" ", flush=True)
    df = yf.download(ticker, period="max", auto_adjust=True, progress=False)
    if df.empty or len(df) < 30:
        print("无数据，跳过")
        return None, None

    closes = df["Close"].squeeze().tolist()
    lows   = df["Low"].squeeze().tolist()
    opens  = df["Open"].squeeze().tolist()
    dates  = df.index.tolist()
    print(f"{len(df)} 条日线  ({str(dates[0])[:10]} ~ {str(dates[-1])[:10]})")

    results = []
    for n in MA_PERIODS:
        r = run_single(closes, lows, opens, dates, n)
        if r:
            results.append(r)

    if not results:
        return None, None

    best = max(results, key=lambda x: x["total_return"])
    return results, best


# ── Excel 样式工具 ────────────────────────────────────────────────────────────

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
SUBHDR_FILL = PatternFill("solid", fgColor="2E75B6")
BEST_FILL   = PatternFill("solid", fgColor="FFD966")   # 金色：最优 MA 区块标题
SEC_FILL    = PatternFill("solid", fgColor="2E75B6")   # 蓝色：普通 MA 区块标题
COL_FILL    = PatternFill("solid", fgColor="4472C4")   # 列头
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
    if fill:
        cell.fill = fill
    if fmt:
        cell.number_format = fmt
    return cell


def _hdr(ws, row, col, text, fill=HEADER_FILL, color="FFFFFF", size=10):
    c = _cell(ws, row, col, text, bold=True, color=color, fill=fill, size=size)
    return c


def color_ret(val):
    if val > 0:
        return "375623"
    elif val < 0:
        return "C00000"
    return "000000"


def fmt_date(d):
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]


# ── Sheet 1：所有参数汇总 ─────────────────────────────────────────────────────

def write_summary_sheet(wb, all_results):
    ws = wb.active
    ws.title = "汇总 - 各参数表现"

    ws.merge_cells("A1:K1")
    c = ws["A1"]
    c.value     = f"单均线突破策略回测汇总  |  手续费 {FEE_RATE*100:.2f}%  滑点 {SLIPPAGE*100:.2f}%  MA 10~200 步长 10  |  截至 {END_DATE}"
    c.font      = Font(name="Arial", bold=True, size=12, color="FFFFFF")
    c.fill      = HEADER_FILL
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    headers = ["品种", "MA 周期 N", "总收益率 %", "交易次数", "最大回撤 %",
               "胜率 %", "最大单笔盈利 %", "平均盈利 %", "平均盈亏比 R", "是否最优", "备注"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 2, col, h, fill=SUBHDR_FILL)
    ws.row_dimensions[2].height = 22

    row = 3
    for sym, (results, best) in all_results.items():
        name = SYMBOLS.get(sym, sym)
        if results is None:
            _cell(ws, row, 1, f"{sym} ({name})", align="left")
            ws.merge_cells(f"B{row}:K{row}")
            _cell(ws, row, 2, "无数据", color="C00000")
            row += 1
            continue

        start_row = row
        for r in results:
            is_best = r["n"] == best["n"]
            fill    = WIN_FILL if is_best else (ALT_FILL if row % 2 == 0 else WHITE_FILL)

            _cell(ws, row, 1,  f"{sym}\n({name})",       align="left",   fill=fill)
            _cell(ws, row, 2,  r["n"],       fmt="0",     fill=fill)
            _cell(ws, row, 3,  r["total_return"], fmt='0.00"%"',
                  color=color_ret(r["total_return"]), bold=is_best, fill=fill)
            _cell(ws, row, 4,  r["n_trades"], fmt="0",    fill=fill)
            _cell(ws, row, 5,  r["max_drawdown"], fmt='0.00"%"',
                  color="C00000" if r["max_drawdown"] > 50 else "000000", fill=fill)
            _cell(ws, row, 6,  r["win_rate"],  fmt='0.0"%"', fill=fill)
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
            ws["A" + str(start_row)].alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True)

    widths = [18, 10, 13, 10, 13, 10, 18, 14, 13, 10, 10]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"


# ── Sheet 2：最优参数汇总 ─────────────────────────────────────────────────────

def write_best_sheet(wb, all_results):
    ws = wb.create_sheet("最优参数汇总")

    ws.merge_cells("A1:J1")
    c = ws["A1"]
    c.value     = "各品种最优均线参数一览（按总收益率排名）"
    c.font      = Font(name="Arial", bold=True, size=13, color="FFFFFF")
    c.fill      = HEADER_FILL
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    headers = ["排名", "品种代码", "品种名称", "最优 MA", "总收益率 %",
               "交易次数", "最大回撤 %", "胜率 %", "平均盈利 %", "平均盈亏比 R"]
    for col, h in enumerate(headers, 1):
        _hdr(ws, 2, col, h, fill=SUBHDR_FILL)

    ranked = [(sym, best) for sym, (_, best) in all_results.items() if best]
    ranked.sort(key=lambda x: x[1]["total_return"], reverse=True)

    for rank, (sym, best) in enumerate(ranked, 1):
        row  = rank + 2
        fill = ALT_FILL if rank % 2 == 0 else WHITE_FILL
        _cell(ws, row, 1,  rank,   fmt="0",        fill=fill, bold=True)
        _cell(ws, row, 2,  sym,                    fill=fill)
        _cell(ws, row, 3,  SYMBOLS.get(sym, sym),  fill=fill)
        _cell(ws, row, 4,  best["n"], fmt="0",     fill=fill, bold=True)
        _cell(ws, row, 5,  best["total_return"], fmt='0.00"%"',
              color=color_ret(best["total_return"]), bold=True, fill=fill)
        _cell(ws, row, 6,  best["n_trades"],  fmt="0",      fill=fill)
        _cell(ws, row, 7,  best["max_drawdown"], fmt='0.00"%"',
              color="C00000" if best["max_drawdown"] > 50 else "000000", fill=fill)
        _cell(ws, row, 8,  best["win_rate"],  fmt='0.0"%"', fill=fill)
        _cell(ws, row, 9,  best["avg_profit"], fmt='0.00"%"',
              color=color_ret(best["avg_profit"]), fill=fill)
        avg_rr = best["avg_rr"]
        _cell(ws, row, 10,
              round(avg_rr, 2) if avg_rr is not None else "-",
              fmt="0.00" if avg_rr is not None else None, fill=fill)

    widths = [8, 12, 14, 10, 13, 10, 13, 10, 13, 13]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"


# ── Sheet 3+：各品种全均线逐笔明细 ───────────────────────────────────────────

TRADE_COLS = ["入场日期", "出场日期", "出场原因", "入场有效价", "出场有效价",
              "单笔收益率 %", "最大盈利率 %", "盈亏比 R", "盈亏"]
N_TRADE_COLS = len(TRADE_COLS)


def write_trade_sheet(wb, sym, results, best_n):
    safe_name = sym.replace("/", "-").replace(":", "-")[:28]
    ws = wb.create_sheet(safe_name)

    # 顶部品种标题
    ws.merge_cells(f"A1:{get_column_letter(N_TRADE_COLS)}1")
    c = ws["A1"]
    c.value     = (f"{sym} ({SYMBOLS.get(sym, sym)})  —  手续费 {FEE_RATE*100:.2f}%  "
                   f"滑点 {SLIPPAGE*100:.2f}%  |  最优 MA={best_n}")
    c.font      = Font(name="Arial", bold=True, size=12, color="FFFFFF")
    c.fill      = HEADER_FILL
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26

    cur_row = 2

    for r in results:
        n       = r["n"]
        is_best = (n == best_n)
        trades  = r["trade_log"]
        dates   = r["dates"]
        closes  = r["closes"]

        # ── 区块标题行 ──
        sec_fill = BEST_FILL if is_best else SEC_FILL
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

        # ── 列头行 ──
        for col, h in enumerate(TRADE_COLS, 1):
            _hdr(ws, cur_row, col, h, fill=COL_FILL, size=9)
        ws.row_dimensions[cur_row].height = 18
        cur_row += 1

        # ── 逐笔数据 ──
        if not trades:
            ws.merge_cells(f"A{cur_row}:{get_column_letter(N_TRADE_COLS)}{cur_row}")
            _cell(ws, cur_row, 1, "该参数下无交易记录", color="888888",
                  fill=WHITE_FILL, align="left")
            cur_row += 1
        else:
            for idx, (ei, xi, ret, reason, sl_px, rr, max_ret) in enumerate(trades):
                fill = WIN_FILL if ret > 0 else LOSS_FILL

                entry_date = fmt_date(dates[ei])
                exit_date  = fmt_date(dates[xi])
                entry_px   = closes[ei] * (1 + COST)
                exit_px    = (sl_px * (1 - COST) if reason in ("止损", "保本")
                              else closes[xi] * (1 - COST))

                reason_color = ("C00000" if reason == "止损"
                                else "D06000" if reason == "保本"
                                else "000000")
                _cell(ws, cur_row, 1, entry_date,          fill=fill, align="center")
                _cell(ws, cur_row, 2, exit_date,           fill=fill, align="center")
                _cell(ws, cur_row, 3, reason,              fill=fill, color=reason_color)
                _cell(ws, cur_row, 4, round(entry_px, 6),  fmt="#,##0.######", fill=fill)
                _cell(ws, cur_row, 5, round(exit_px, 6),   fmt="#,##0.######", fill=fill)
                _cell(ws, cur_row, 6, ret * 100,           fmt='0.00"%"',
                      color=color_ret(ret), bold=(ret > 0), fill=fill)
                _cell(ws, cur_row, 7, max_ret * 100,       fmt='0.00"%"',
                      color="375623", bold=True, fill=fill)
                if rr is not None:
                    _cell(ws, cur_row, 8, round(rr, 2),    fmt="0.00",
                          color="375623", bold=True, fill=fill)
                else:
                    _cell(ws, cur_row, 8, "-",             color="888888", fill=fill)
                _cell(ws, cur_row, 9, "盈" if ret > 0 else "亏",
                      color="375623" if ret > 0 else "C00000",
                      bold=True, fill=fill)
                cur_row += 1

        # 空行分隔
        cur_row += 1

    # 列宽
    widths = [14, 14, 10, 16, 16, 15, 15, 12, 8]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = f"A2"


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    all_results = {}

    for sym in SYMBOLS:
        print(f"\n[{sym}]")
        results, best = backtest_symbol(sym)
        all_results[sym] = (results, best)
        if best:
            rr_str = f"  平均盈亏比={best['avg_rr']:.2f}R" if best["avg_rr"] else ""
            print(f"  最优 MA={best['n']}  收益={best['total_return']:.1f}%  "
                  f"胜率={best['win_rate']:.1f}%  回撤={best['max_drawdown']:.1f}%{rr_str}")

    import os
    out_path = "/Users/zhangzheyu/market-replay/data/ma_breakout_backtest.xlsx"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print("\n生成 Excel ...")
    wb = openpyxl.Workbook()
    write_summary_sheet(wb, all_results)
    write_best_sheet(wb, all_results)

    for sym, (results, best) in all_results.items():
        if results and best:
            write_trade_sheet(wb, sym, results, best["n"])

    wb.save(out_path)
    print(f"✅ 已保存至 {out_path}")


if __name__ == "__main__":
    main()
