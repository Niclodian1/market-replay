"""
TradingView Pine Script 生成器。

从回测 JSON 中读取交易记录，生成 Pine Script v5 脚本。
在 K 线图上标注每笔交易的入场和出场位置。

用法:
    python3 scripts/backtests/generate_tradingview.py

产出:
    reports/tradingview/{SYMBOL}.pine       — 每个品种一个脚本（所有信号汇总，Triangle 取前100+后50）
    reports/tradingview/{SYMBOL}_{SIGNAL}.pine — 每个信号的详细脚本

导入 TradingView:
    1. 打开 Pine Editor（底部面板）
    2. 粘贴 .pine 文件内容
    3. 点击 "Add to chart"
    4. 可以叠加多个 indicator（先加汇总版，再加想看细节的信号版）
"""
import json
from pathlib import Path
from collections import defaultdict

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "reports" / "tradingview"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SIGNAL_COLORS: dict[str, str] = {
    "pinbar":           "color.rgb(33, 150, 243)",     # 蓝
    "engulfing":        "color.rgb(255, 152, 0)",      # 橙
    "triangle":         "color.rgb(156, 39, 176)",     # 紫
    "box":              "color.rgb(76, 175, 80)",      # 绿
    "channel":          "color.rgb(255, 235, 59)",     # 黄
    "trendline_break":  "color.rgb(0, 188, 212)",      # 青
    "false_breakout":   "color.rgb(244, 67, 54)",      # 红
    "macd_momentum":    "color.rgb(255, 107, 107)",    # 浅红
    "macd_divergence":  "color.rgb(255, 217, 61)",     # 金黄
}

SIGNAL_LABELS: dict[str, str] = {
    "pinbar": "PB", "engulfing": "ENG", "triangle": "TRI",
    "box": "BOX", "channel": "CH", "trendline_break": "TL",
    "false_breakout": "FB", "macd_momentum": "MACD-M",
    "macd_divergence": "MACD-D",
}

TRIANGLE_TOP_N = 100   # Triangle 取前 N 笔最佳
TRIANGLE_BOTTOM_N = 50  # Triangle 取后 N 笔最差


def _ts_ms(ts_val: int) -> str:
    """Unix 秒 → Unix 毫秒（Pine Script 的 time 变量以毫秒为单位）。"""
    return str(int(ts_val) * 1000)


def _ts_array(var_name: str, trades: list[dict], key: str) -> str:
    """生成 timestamp 数组声明。每行一个值，用数字毫秒格式，最可靠。"""
    vals = [_ts_ms(t[key]) for t in trades]
    body = ",\n".join(f"    {v}" for v in vals)
    return f"var int[] {var_name} = array.from(\n{body}\n)"


def generate_signal_pine(symbol: str, sig_name: str, trades: list[dict]) -> str:
    """为一个信号生成 Pine Script（完整交易列表，不截断）。"""
    trades = sorted(trades, key=lambda t: t["entry_time"])
    long_trades = [t for t in trades if t["direction"] == "long"]
    short_trades = [t for t in trades if t["direction"] == "short"]
    color = SIGNAL_COLORS.get(sig_name, "color.new(#888888, 0)")
    label = SIGNAL_LABELS.get(sig_name, sig_name[:4].upper())

    lines: list[str] = []
    lines.append("//@version=5")
    lines.append(f'indicator("{label} — {symbol}", overlay=true)')
    lines.append("")

    if long_trades:
        lines.append(f"// 多头入场 ({len(long_trades)}笔)")
        lines.append(_ts_array(f"le_{sig_name}", long_trades, "entry_time"))
        lines.append(_ts_array(f"lx_{sig_name}", long_trades, "exit_time"))
        lines.append(f"plotshape(array.includes(le_{sig_name}, time) ? low : na, '{label} L', shape.triangleup, location.belowbar, {color}, size=size.small)")
        lines.append(f"plotshape(array.includes(lx_{sig_name}, time) ? high : na, '{label} L out', shape.circle, location.abovebar, array.includes(lx_{sig_name}, time) and close > open ? color.green : color.red, size=size.tiny)")
        lines.append("")

    if short_trades:
        lines.append(f"// 空头入场 ({len(short_trades)}笔)")
        lines.append(_ts_array(f"se_{sig_name}", short_trades, "entry_time"))
        lines.append(_ts_array(f"sx_{sig_name}", short_trades, "exit_time"))
        lines.append(f"plotshape(array.includes(se_{sig_name}, time) ? high : na, '{label} S', shape.triangledown, location.abovebar, {color}, size=size.small)")
        lines.append(f"plotshape(array.includes(sx_{sig_name}, time) ? low : na, '{label} S out', shape.circle, location.belowbar, array.includes(sx_{sig_name}, time) and close < open ? color.green : color.red, size=size.tiny)")
        lines.append("")

    return "\n".join(lines)


def generate_summary_pine(symbol: str, trades: list[dict]) -> str:
    """为一个品种生成汇总 Pine Script（所有信号，Triangle 截断）。"""
    by_signal: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_signal[t["signal"]].append(t)

    lines: list[str] = []
    lines.append("//@version=5")
    lines.append(f'indicator("Signal Matrix — {symbol}", overlay=true, max_labels_count=500)')
    lines.append("")

    total_shown = 0
    for sig_name in sorted(by_signal.keys()):
        sig_trades = by_signal[sig_name]
        sig_trades.sort(key=lambda t: t["entry_time"])

        # Triangle: 截断到前 N 和后 N
        if sig_name == "triangle" and len(sig_trades) > TRIANGLE_TOP_N + TRIANGLE_BOTTOM_N:
            by_pnl = sorted(sig_trades, key=lambda t: t["pnl_pct"], reverse=True)
            top = by_pnl[:TRIANGLE_TOP_N]
            bottom = by_pnl[-TRIANGLE_BOTTOM_N:]
            selected = sorted(top + bottom, key=lambda t: t["entry_time"])
            skipped = len(sig_trades) - len(selected)
        else:
            selected = sig_trades
            skipped = 0

        total_shown += len(selected)
        label = SIGNAL_LABELS.get(sig_name, sig_name[:4].upper())
        color = SIGNAL_COLORS.get(sig_name, "color.new(#888888, 0)")

        long_trades = [t for t in selected if t["direction"] == "long"]
        short_trades = [t for t in selected if t["direction"] == "short"]

        note = f" ({len(selected)} shown" + (f", {skipped} skipped)" if skipped else "") + ")"
        lines.append(f"// ── {sig_name}{note} ──")

        if long_trades:
            lines.append(_ts_array(f"le_{sig_name}", long_trades, "entry_time"))
            lines.append(_ts_array(f"lx_{sig_name}", long_trades, "exit_time"))
            lines.append(f"is_le_{sig_name} = array.includes(le_{sig_name}, time)")
            lines.append(f"is_lx_{sig_name} = array.includes(lx_{sig_name}, time)")
            lines.append(f"plotshape(is_le_{sig_name} ? low : na, '{label}', shape.triangleup, location.belowbar, {color}, size=size.small)")
            lines.append(f"plotshape(is_lx_{sig_name} ? high : na, '', shape.xcross, location.abovebar, is_lx_{sig_name} and close > open ? color.rgb(38,166,154,70) : color.rgb(239,83,80,70), size=size.tiny)")
            lines.append("")

        if short_trades:
            lines.append(_ts_array(f"se_{sig_name}", short_trades, "entry_time"))
            lines.append(_ts_array(f"sx_{sig_name}", short_trades, "exit_time"))
            lines.append(f"is_se_{sig_name} = array.includes(se_{sig_name}, time)")
            lines.append(f"is_sx_{sig_name} = array.includes(sx_{sig_name}, time)")
            lines.append(f"plotshape(is_se_{sig_name} ? high : na, '{label}', shape.triangledown, location.abovebar, {color}, size=size.small)")
            lines.append(f"plotshape(is_sx_{sig_name} ? low : na, '', shape.xcross, location.belowbar, is_sx_{sig_name} and close < open ? color.rgb(38,166,154,70) : color.rgb(239,83,80,70), size=size.tiny)")
            lines.append("")

    lines.append("// ============================================================")
    lines.append(f"//  图例 ({total_shown}笔):")
    for sig_name, label in SIGNAL_LABELS.items():
        if sig_name in by_signal:
            n = len(by_signal[sig_name])
            lines.append(f"//    {label} = {sig_name} ({n}笔)")
    lines.append("//  ▲ = 多头入场  ▼ = 空头入场  ✕ = 出场")
    lines.append("// ============================================================")

    return "\n".join(lines)


def main():
    json_path = OUTPUT_DIR.parent / "signal_matrix.json"
    if not json_path.exists():
        print(f"❌ 未找到 {json_path}，请先运行 run_analysis.py")
        return

    with open(json_path) as f:
        data = json.load(f)

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for t in data["trades"]:
        by_symbol[t["symbol"]].append(t)

    for symbol, trades in sorted(by_symbol.items()):
        fname = symbol.replace("-", "_").replace("/", "_")
        print(f"── {symbol}: {len(trades)} 笔交易")

        # 汇总版
        summary = generate_summary_pine(symbol, trades)
        sp = OUTPUT_DIR / f"{fname}.pine"
        with open(sp, "w", encoding="utf-8") as f:
            f.write(summary)
        print(f"  汇总: {sp.name} ({len(summary)} 字符)")

        # 每个信号单独版
        by_signal: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            by_signal[t["signal"]].append(t)
        for sig_name in sorted(by_signal.keys()):
            sig_trades = by_signal[sig_name]
            sig_pine = generate_signal_pine(symbol, sig_name, sig_trades)
            fp = OUTPUT_DIR / f"{fname}_{sig_name}.pine"
            with open(fp, "w", encoding="utf-8") as f:
                f.write(sig_pine)
            print(f"    {sig_name}: {fp.name} ({len(sig_pine)} 字符, {len(sig_trades)}笔)")

    print(f"\n✅ 全部完成 → {OUTPUT_DIR}/")
    print("在 TradingView Pine Editor 中粘贴 .pine 文件即可使用。")


if __name__ == "__main__":
    main()
