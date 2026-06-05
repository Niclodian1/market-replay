"""
【实验·不动主回测系统】策略 C 参数邻域鲁棒性扫描（平台 vs 尖峰）

固定策略 C 的全部逻辑（当根同时上穿两根均线 + 实体涨幅 > 3%；止损/保本/均线出场不变），
只把 (fast MA, slow MA) 在参考点 21/55 与 30/60 周围扫一圈，看「总收益」面是
平台（邻域普遍能赚 → 稳健）还是尖峰（只有个别格子亮 → 过拟合/脆）。

品种：BTC-USD / ETH-USD    周期：1day / 4h / 1h
产出（均在 experiments/ 下，独立文件）：
  - strategy_c_robustness_heatmap.png   6 个面板（品种×周期）的收益热力图
  - strategy_c_robustness_grids.xlsx    各面板数值网格（带色阶）+ 交易笔数网格
"""

import datetime
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib.patches import Rectangle

import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter

# ── 让本实验能 import 主回测脚本 ─────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts" / "backtests"))

from backtest_ma_strategies_c import load_candles, run_variant  # noqa: E402
from backtest_ma_strategies import rolling_sma  # noqa: E402

# ── 实验配置 ─────────────────────────────────────────────────────────────────
SYMBOLS = {"BTC-USD": "BTC", "ETH-USD": "ETH"}
TIMEFRAMES = ["1day", "4h", "1h"]

FAST_LIST = [18, 21, 24, 27, 30, 34]
SLOW_LIST = [45, 50, 55, 60, 65, 70]
REF_POINTS = [(21, 55), (30, 60)]   # 两个参考池，热力图上框出来

END_DATE = datetime.date.today().isoformat()
PNG_PATH = _REPO / "experiments" / "strategy_c_robustness_heatmap.png"
XLSX_PATH = _REPO / "experiments" / "strategy_c_robustness_grids.xlsx"


def compute_crossed_by_bar(closes, sma_fast, sma_slow):
    """每根 K 线是否同时上穿 fast 与 slow（两根都满足 prev_c≤prev_ma 且 c>ma）。"""
    n = len(closes)
    out = [[] for _ in range(n)]
    for i in range(1, n):
        c, pc = closes[i], closes[i - 1]
        crossed = []
        for ma_arr, period in ((sma_fast, "f"), (sma_slow, "s")):
            ma, pma = ma_arr[i], ma_arr[i - 1]
            if ma is not None and pma is not None and pc <= pma and c > ma:
                crossed.append(period)
        out[i] = crossed
    return out


def sweep_panel(symbol, tf):
    """返回 (ret_grid, ntrade_grid)，shape = [len(FAST), len(SLOW)]，NaN=无效/无交易。"""
    candles = load_candles(symbol, tf)
    closes = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]
    lows = [c["low"] for c in candles]
    times = [c["time"] for c in candles]

    periods = sorted(set(FAST_LIST + SLOW_LIST))
    sma = {p: rolling_sma(closes, p) for p in periods}

    ret_grid = np.full((len(FAST_LIST), len(SLOW_LIST)), np.nan)
    nt_grid = np.full((len(FAST_LIST), len(SLOW_LIST)), np.nan)

    for fi, f in enumerate(FAST_LIST):
        for si, s in enumerate(SLOW_LIST):
            if f >= s:
                continue
            crossed_raw = compute_crossed_by_bar(closes, sma[f], sma[s])
            # 把 "f"/"s" 标记映射回真实周期，供 run_variant 取 exit MA = min(crossed)
            crossed_by_bar = []
            for marks in crossed_raw:
                crossed_by_bar.append([f if m == "f" else s for m in marks])
            # exit MA = min(crossed)，run_variant 需要 sma_by_n 含 f、s
            sma_by_n = {f: sma[f], s: sma[s]}
            r = run_variant(closes, opens, lows, times, sma_by_n, crossed_by_bar, target_k=2)
            if r:
                ret_grid[fi, si] = r["total_return"]
                nt_grid[fi, si] = r["n_trades"]
            else:
                ret_grid[fi, si] = np.nan
                nt_grid[fi, si] = 0
    return ret_grid, nt_grid


# ── 热力图 ───────────────────────────────────────────────────────────────────

def draw_heatmaps(panels):
    fig, axes = plt.subplots(len(SYMBOLS), len(TIMEFRAMES),
                             figsize=(5.2 * len(TIMEFRAMES), 4.6 * len(SYMBOLS)))
    fig.suptitle(
        f"Strategy C robustness sweep — total return (%)  |  entry: cross both MAs + body>3%  |  as of {END_DATE}",
        fontsize=13, fontweight="bold", y=0.99)

    for ri, symbol in enumerate(SYMBOLS):
        for ci, tf in enumerate(TIMEFRAMES):
            ax = axes[ri][ci]
            ret_grid = panels[(symbol, tf)]["ret"]
            nt_grid = panels[(symbol, tf)]["nt"]

            finite = ret_grid[np.isfinite(ret_grid)]
            if finite.size:
                vmin, vmax = float(np.min(finite)), float(np.max(finite))
                if vmin < 0 < vmax:
                    norm = TwoSlopeNorm(vmin=vmin, vcenter=0.0, vmax=vmax)
                elif vmax <= 0:
                    norm = TwoSlopeNorm(vmin=vmin, vcenter=min(-1e-6, vmax), vmax=0.0) if vmax < 0 else None
                else:
                    norm = TwoSlopeNorm(vmin=0.0, vcenter=max(1e-6, vmin), vmax=vmax) if vmin > 0 else None
            else:
                norm = None

            im = ax.imshow(ret_grid, cmap="RdYlGn", norm=norm, aspect="auto", origin="upper")
            ax.set_title(f"{SYMBOLS[symbol]} · {tf}", fontsize=11, fontweight="bold")
            ax.set_xticks(range(len(SLOW_LIST)))
            ax.set_xticklabels(SLOW_LIST)
            ax.set_yticks(range(len(FAST_LIST)))
            ax.set_yticklabels(FAST_LIST)
            ax.set_xlabel("slow MA")
            ax.set_ylabel("fast MA")

            for fi in range(len(FAST_LIST)):
                for si in range(len(SLOW_LIST)):
                    v = ret_grid[fi, si]
                    if not np.isfinite(v):
                        continue
                    n = nt_grid[fi, si]
                    ax.text(si, fi, f"{v:.0f}%\nn={int(n)}",
                            ha="center", va="center", fontsize=7.5,
                            color="black")

            # 框出参考点
            for (rf, rs) in REF_POINTS:
                if rf in FAST_LIST and rs in SLOW_LIST:
                    fi, si = FAST_LIST.index(rf), SLOW_LIST.index(rs)
                    ax.add_patch(Rectangle((si - 0.5, fi - 0.5), 1, 1,
                                           fill=False, edgecolor="black", lw=2.2))

            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(str(PNG_PATH), dpi=130)
    plt.close(fig)


# ── Excel 数值网格（带色阶） ─────────────────────────────────────────────────

HDR_FILL = PatternFill("solid", fgColor="1F3864")
SUB_FILL = PatternFill("solid", fgColor="2E75B6")
REF_FILL = PatternFill("solid", fgColor="FFD966")


def _grid_block(ws, top, left, title, grid, ref_set, number_fmt):
    ws.cell(top, left).value = title
    ws.cell(top, left).font = Font(bold=True, size=11, color="FFFFFF")
    ws.cell(top, left).fill = HDR_FILL
    ws.merge_cells(start_row=top, start_column=left, end_row=top, end_column=left + len(SLOW_LIST))

    # 列头：slow
    ws.cell(top + 1, left).value = "fast＼slow"
    ws.cell(top + 1, left).font = Font(bold=True, color="FFFFFF")
    ws.cell(top + 1, left).fill = SUB_FILL
    for si, s in enumerate(SLOW_LIST):
        cc = ws.cell(top + 1, left + 1 + si)
        cc.value = s
        cc.font = Font(bold=True, color="FFFFFF")
        cc.fill = SUB_FILL
        cc.alignment = Alignment(horizontal="center")

    first_data_col = left + 1
    for fi, f in enumerate(FAST_LIST):
        rc = ws.cell(top + 2 + fi, left)
        rc.value = f
        rc.font = Font(bold=True, color="FFFFFF")
        rc.fill = SUB_FILL
        rc.alignment = Alignment(horizontal="center")
        for si, s in enumerate(SLOW_LIST):
            cell = ws.cell(top + 2 + fi, left + 1 + si)
            v = grid[fi, si]
            cell.value = round(float(v), 1) if np.isfinite(v) else None
            cell.number_format = number_fmt
            cell.alignment = Alignment(horizontal="center")
            if (f, s) in ref_set:
                cell.fill = REF_FILL
                cell.font = Font(bold=True)

    last_row = top + 1 + len(FAST_LIST)
    rng = f"{get_column_letter(first_data_col)}{top+2}:{get_column_letter(left+len(SLOW_LIST))}{last_row}"
    ws.conditional_formatting.add(rng, ColorScaleRule(
        start_type="min", start_color="F8696B",
        mid_type="num", mid_value=0, mid_color="FFEB84",
        end_type="max", end_color="63BE7B"))
    return last_row


def write_grids_excel(panels):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ref_set = set(REF_POINTS)
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            ws = wb.create_sheet(f"{SYMBOLS[symbol]}-{tf}"[:28])
            ws.cell(1, 1).value = (f"策略C 鲁棒性扫描 | {symbol} ({SYMBOLS[symbol]}) · {tf}  "
                                   f"（黄底=参考池 21/55、30/60；色阶：红<0<绿）")
            ws.cell(1, 1).font = Font(bold=True, size=12)
            ws.merge_cells("A1:H1")
            r1 = _grid_block(ws, 3, 1, "总收益率 %", panels[(symbol, tf)]["ret"], ref_set, '0.0"%"')
            _grid_block(ws, r1 + 3, 1, "交易笔数", panels[(symbol, tf)]["nt"], ref_set, "0")
            ws.column_dimensions["A"].width = 12
            for ci in range(len(SLOW_LIST)):
                ws.column_dimensions[get_column_letter(2 + ci)].width = 10
    wb.save(str(XLSX_PATH))


def main():
    panels = {}
    for symbol in SYMBOLS:
        print(f"\n[{symbol}]")
        for tf in TIMEFRAMES:
            print(f"  扫描 {tf}（{len(FAST_LIST)}×{len(SLOW_LIST)} 网格）...", flush=True)
            ret_grid, nt_grid = sweep_panel(symbol, tf)
            panels[(symbol, tf)] = {"ret": ret_grid, "nt": nt_grid}
            finite = ret_grid[np.isfinite(ret_grid)]
            if finite.size:
                pos = int(np.sum(finite > 0))
                print(f"    收益范围 {finite.min():.0f}% ~ {finite.max():.0f}%  |  "
                      f"中位数 {np.median(finite):.0f}%  |  赚钱格子 {pos}/{finite.size}")

    print("\n生成热力图 PNG ...")
    draw_heatmaps(panels)
    print(f"✅ {PNG_PATH}")
    print("生成数值网格 Excel ...")
    write_grids_excel(panels)
    print(f"✅ {XLSX_PATH}")


if __name__ == "__main__":
    main()
