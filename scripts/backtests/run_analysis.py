"""
趋势+信号矩阵回测 —— 端到端执行 + HTML 报告。

用法:
    cd market-replay
    python3 scripts/backtests/run_analysis.py

产出:
    reports/signal_matrix.html  — 热力图 + 交易明细
    reports/signal_matrix.json — 结构化数据
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.providers.local_crypto import LocalCryptoProvider
from app.providers.yahoo_finance import YahooFinanceProvider
from app.core.trend_classifier import classify
from app.core.signal_detectors import compute_all_signals, SIGNAL_LABELS
from app.core.matrix_backtest import MatrixBacktester, REGIME_LABELS

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "reports"
OUTPUT_DIR.mkdir(exist_ok=True)

SYMBOLS = [
    ("BTC-USD", "1day", "crypto"),
    ("ETH-USD", "1day", "crypto"),
    ("AAPL", "1day", "stock"),
    ("GOOGL", "1day", "stock"),
]

REGIME_ORDER = [2, 1, 0, -1, -2]  # 显示顺序
REGIME_COLORS = {
    2: "#006400", 1: "#228B22", 0: "#888888", -1: "#8B0000", -2: "#FF4500",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  数据加载
# ═══════════════════════════════════════════════════════════════════════════════

def load_data(symbol: str, tf: str, source: str) -> list[dict]:
    if source == "crypto":
        return asyncio.run(LocalCryptoProvider().get_full_history(symbol, tf))
    else:
        return asyncio.run(YahooFinanceProvider().get_time_series(symbol, tf, outputsize=5000))


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML 报告
# ═══════════════════════════════════════════════════════════════════════════════

def _heatmap_color(win_rate: float, trades: int) -> str:
    """根据胜率返回颜色。笔数太少则灰色。"""
    if trades < 5:
        return "#2a2a2a"  # 样本不足
    if win_rate >= 60:
        return "#006400"
    if win_rate >= 50:
        return "#228B22"
    if win_rate >= 40:
        return "#8B7500"
    if win_rate >= 30:
        return "#8B4513"
    return "#8B0000"


def _pnl_class(val: float) -> str:
    return "positive" if val >= 0 else "negative"


def _ts_to_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def build_html(reports: list, symbol_data: list[dict]) -> str:
    """构建完整的 HTML 报告页面。"""
    # 合并所有品种的交易
    all_trades = []
    for r in reports:
        for t in r.all_trades:
            t._symbol = r.symbol  # type: ignore
            all_trades.append(t)

    signal_names = reports[0].signal_names if reports else []

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>信号矩阵回测 · 热力图</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
h1 {{ color: #fff; margin-bottom: 4px; }}
.subtitle {{ color: #888; margin-bottom: 24px; font-size: 14px; }}
h2 {{ color: #e94560; margin: 24px 0 12px; font-size: 18px; }}
.card {{ background: #16213e; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #0f3460; }}
.heatmap {{ border-collapse: collapse; font-size: 13px; width: 100%; }}
.heatmap th {{ background: #0f3460; color: #a0b4d0; padding: 10px 14px; text-align: center; font-weight: 600; }}
.heatmap td {{ padding: 12px 14px; border: 2px solid #1a1a2e; text-align: center; min-width: 110px; }}
.heatmap .signal-name {{ text-align: left; color: #fff; font-weight: 600; background: #0f0f23; }}
.heatmap .cell-stats {{ font-size: 12px; line-height: 1.5; }}
.heatmap .cell-stats .wr {{ font-size: 16px; font-weight: 700; }}
.heatmap .cell-stats .detail {{ color: #aaa; }}
.positive {{ color: #26a69a; }}
.negative {{ color: #ef5350; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.badge-long {{ background: #26a69a22; color: #26a69a; border: 1px solid #26a69a44; }}
.badge-short {{ background: #ef535022; color: #ef5350; border: 1px solid #ef535044; }}
.badge-signal {{ background: #0f3460; color: #a0b4d0; }}
.filter-bar {{ margin-bottom: 16px; }}
.filter-bar select, .filter-bar input {{ background: #0f3460; color: #fff; border: 1px solid #1a3a6e; padding: 6px 12px; border-radius: 6px; margin-right: 8px; font-size: 13px; }}
.sort-btn {{ background: #0f3460; color: #a0b4d0; border: 1px solid #1a3a6e; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
.sort-btn:hover {{ background: #e94560; }}
.summary-item {{ display: inline-block; background: #0f0f23; border: 1px solid #1a3a6e; border-radius: 8px; padding: 12px 18px; margin: 4px; min-width: 160px; }}
.summary-item .label {{ color: #888; font-size: 12px; }}
.summary-item .value {{ font-size: 20px; font-weight: 700; }}
.tab-btn {{ background: #0f3460; color: #a0b4d0; border: none; padding: 8px 16px; border-radius: 6px 6px 0 0; cursor: pointer; font-size: 14px; margin-right: 4px; }}
.tab-btn.active {{ background: #e94560; color: #fff; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}
</style>
</head>
<body>
<h1>📊 信号 × 趋势状态 矩阵回测</h1>
<p class="subtitle">8 信号 × 5 状态 | $1000/笔 | 结构止损 + ATR追踪 | 生成于 {now_str}</p>

<div class="card">
<h2>🔥 热力图: 胜率% (笔数)</h2>
<p style="color:#888;margin-bottom:12px;font-size:13px">绿色=高胜率, 红色=低胜率, 深灰=样本不足(&lt;5笔)</p>
<table class="heatmap">
<thead>
<tr><th>信号</th>
"""
    for rid in REGIME_ORDER:
        label = REGIME_LABELS.get(rid, "?")
        html += f'<th style="border-bottom:3px solid {REGIME_COLORS.get(rid, "#888")}">{label}<br><span style="font-size:11px;color:#888">ID:{rid}</span></th>'
    html += '</tr></thead><tbody>'

    for sig_name in signal_names:
        label = SIGNAL_LABELS.get(sig_name, sig_name)
        html += f'<tr><td class="signal-name">{label}</td>'
        for rid in REGIME_ORDER:
            # 汇总所有品种的该格统计
            total_trades = 0
            total_wins = 0
            total_pnl = 0.0
            for r in reports:
                cell = r.matrix.get(sig_name, {}).get(rid)
                if cell and cell.trades > 0:
                    total_trades += cell.trades
                    total_wins += int(cell.win_rate / 100 * cell.trades)
                    total_pnl += cell.avg_pnl * cell.trades

            if total_trades > 0:
                wr = round(total_wins / total_trades * 100, 1)
                avg_pnl = round(total_pnl / total_trades, 2)
                color = _heatmap_color(wr, total_trades)
                html += f'''<td style="background:{color}">
<div class="cell-stats">
<div class="wr" style="color:#fff">{wr}%</div>
<div class="detail">{total_trades}笔</div>
<div class="detail {_pnl_class(avg_pnl)}">{avg_pnl:+.1f}%</div>
</div></td>'''
            else:
                html += '<td style="background:#2a2a2a;color:#555">—</td>'
        html += '</tr>'
    html += '</tbody></table></div>'

    # — 品种维度热力图 —
    for r in reports:
        html += f'<div class="card"><h2>📈 {r.symbol} · {r.timeframe}</h2>'
        html += '<table class="heatmap"><thead><tr><th>信号</th>'
        for rid in REGIME_ORDER:
            label = REGIME_LABELS.get(rid, "?")
            html += f'<th style="border-bottom:3px solid {REGIME_COLORS.get(rid, "#888")}">{label}</th>'
        html += '</tr></thead><tbody>'
        for sig_name in signal_names:
            label = SIGNAL_LABELS.get(sig_name, sig_name)
            html += f'<tr><td class="signal-name">{label}</td>'
            for rid in REGIME_ORDER:
                cell = r.matrix.get(sig_name, {}).get(rid)
                if cell and cell.trades > 0:
                    color = _heatmap_color(cell.win_rate, cell.trades)
                    html += f'''<td style="background:{color}">
<div class="cell-stats">
<div class="wr" style="color:#fff">{cell.win_rate}%</div>
<div class="detail">{cell.trades}笔</div>
<div class="detail {_pnl_class(cell.avg_pnl)}">{cell.avg_pnl:+.1f}%</div>
<div class="detail" style="font-size:10px">PF:{cell.profit_factor}</div>
</div></td>'''
                else:
                    html += '<td style="background:#2a2a2a;color:#555">—</td>'
            html += '</tr>'
        html += '</tbody></table></div>'

    # — 全局汇总 —
    total_n = len(all_trades)
    wins_n = sum(1 for t in all_trades if t.pnl_pct >= 0)
    losses_n = total_n - wins_n
    total_pnl = sum(t.pnl_amount for t in all_trades)
    wr = wins_n / total_n * 100 if total_n > 0 else 0
    avg_pnl = sum(t.pnl_pct for t in all_trades) / total_n if total_n > 0 else 0

    html += f'''<div class="card">
<h2>📋 全局统计</h2>
<div style="display:flex;flex-wrap:wrap;gap:8px">
<div class="summary-item"><div class="label">总交易</div><div class="value" style="color:#fff">{total_n}</div></div>
<div class="summary-item"><div class="label">胜率</div><div class="value {_pnl_class(wr-50)}">{wr:.1f}%</div></div>
<div class="summary-item"><div class="label">均盈</div><div class="value {_pnl_class(avg_pnl)}">{avg_pnl:+.2f}%</div></div>
<div class="summary-item"><div class="label">总盈亏</div><div class="value {_pnl_class(total_pnl)}">${total_pnl:+,.0f}</div></div>
<div class="summary-item"><div class="label">盈利笔</div><div class="value positive">{wins_n}</div></div>
<div class="summary-item"><div class="label">亏损笔</div><div class="value negative">{losses_n}</div></div>
</div></div>'''

    # — 核心发现 —
    html += '<div class="card"><h2>💡 核心发现</h2>'
    html += '<h3 style="color:#26a69a;margin-top:12px">✅ 正期望组合（胜率>45% & 均盈>0 & ≥5笔）</h3>'
    found_positive = False
    for sig_name in signal_names:
        for rid in REGIME_ORDER:
            total_trades = 0
            total_wins = 0
            total_pnl = 0.0
            for r in reports:
                cell = r.matrix.get(sig_name, {}).get(rid)
                if cell and cell.trades > 0:
                    total_trades += cell.trades
                    total_wins += int(cell.win_rate / 100 * cell.trades)
                    total_pnl += cell.avg_pnl * cell.trades
            if total_trades >= 5:
                wr_all = round(total_wins / total_trades * 100, 1)
                avg_all = round(total_pnl / total_trades, 2)
                if wr_all > 45 and avg_all > 0:
                    found_positive = True
                    rlabel = REGIME_LABELS.get(rid, "?")
                    slabel = SIGNAL_LABELS.get(sig_name, sig_name)
                    html += f'<div style="padding:4px 0">🟢 <b>{slabel}</b> 在 <b>{rlabel}</b>: {total_trades}笔, 胜率{wr_all}%, 均盈{avg_all:+.1f}%</div>'
    if not found_positive:
        html += '<div style="color:#888">暂无满足条件的组合（可能样本不足）</div>'

    html += '<h3 style="color:#ef5350;margin-top:12px">⚠️ 噪声/负期望组合（胜率<35% & 均盈<0 & ≥5笔）</h3>'
    found_noise = False
    for sig_name in signal_names:
        for rid in REGIME_ORDER:
            total_trades = 0
            total_wins = 0
            total_pnl = 0.0
            for r in reports:
                cell = r.matrix.get(sig_name, {}).get(rid)
                if cell and cell.trades > 0:
                    total_trades += cell.trades
                    total_wins += int(cell.win_rate / 100 * cell.trades)
                    total_pnl += cell.avg_pnl * cell.trades
            if total_trades >= 5:
                wr_all = round(total_wins / total_trades * 100, 1)
                avg_all = round(total_pnl / total_trades, 2)
                if wr_all < 35 and avg_all < 0:
                    found_noise = True
                    rlabel = REGIME_LABELS.get(rid, "?")
                    slabel = SIGNAL_LABELS.get(sig_name, sig_name)
                    html += f'<div style="padding:4px 0">🔴 <b>{slabel}</b> 在 <b>{rlabel}</b>: {total_trades}笔, 胜率{wr_all}%, 均盈{avg_all:+.1f}%</div>'
    if not found_noise:
        html += '<div style="color:#888">暂无满足条件的噪声组合</div>'
    html += '</div>'

    # — 交易明细 —
    trades_sorted = sorted(all_trades, key=lambda t: t.pnl_pct, reverse=True)
    html += '''<div class="card">
<h2>📋 全部交易记录</h2>
<div class="filter-bar">
<select id="filter-symbol" onchange="applyFilters()"><option value="">全部品种</option>'''
    for sym, _, _ in SYMBOLS:
        html += f'<option value="{sym}">{sym}</option>'
    html += '''</select>
<select id="filter-signal" onchange="applyFilters()"><option value="">全部信号</option>'''
    for sig_name in signal_names:
        html += f'<option value="{sig_name}">{SIGNAL_LABELS.get(sig_name, sig_name)}</option>'
    html += '''</select>
<select id="filter-regime" onchange="applyFilters()"><option value="">全部状态</option>'''
    for rid in REGIME_ORDER:
        html += f'<option value="{rid}">{REGIME_LABELS.get(rid, "?")}</option>'
    html += '''</select>
<select id="filter-direction" onchange="applyFilters()"><option value="">全部方向</option>
<option value="long">多头</option><option value="short">空头</option></select>
<select id="filter-result" onchange="applyFilters()"><option value="">全部结果</option>
<option value="win">盈利</option><option value="loss">亏损</option></select>
<button class="sort-btn" onclick="sortBy('pnl_pct')">按盈亏%排序</button>
<button class="sort-btn" onclick="sortBy('pnl_amount')">按盈亏$排序</button>
<button class="sort-btn" onclick="sortBy('bars')">按持仓K线排序</button>
<span id="trade-count" style="color:#888;margin-left:16px"></span>
</div>
<div style="max-height:600px;overflow-y:auto">
<table id="trade-table">
<thead><tr>
<th>品种</th><th>信号</th><th>方向</th><th>入场</th><th>入场价</th>
<th>出场</th><th>出场价</th><th>原因</th>
<th>盈亏%</th><th>盈亏$</th><th>K线</th><th>状态</th>
</tr></thead><tbody>'''

    for t in trades_sorted:
        sym = getattr(t, '_symbol', '?')
        et = _ts_to_str(t.entry_time)
        xt = _ts_to_str(t.exit_time)
        dir_badge = '<span class="badge badge-long">多</span>' if t.direction == "long" else '<span class="badge badge-short">空</span>'
        sig_badge = f'<span class="badge badge-signal">{SIGNAL_LABELS.get(t.signal, t.signal)}</span>'
        rlabel = REGIME_LABELS.get(t.regime_at_entry, "?")

        html += f'''<tr data-symbol="{sym}" data-signal="{t.signal}" data-regime="{t.regime_at_entry}"
data-direction="{t.direction}" data-result="{'win' if t.pnl_pct >= 0 else 'loss'}">
<td style="color:#fff;font-weight:600">{sym}</td>
<td>{sig_badge}</td>
<td>{dir_badge}</td>
<td>{et}</td>
<td>{t.entry_price:.2f}</td>
<td>{xt}</td>
<td>{t.exit_price:.2f}</td>
<td style="font-size:12px">{t.exit_reason}</td>
<td class="{_pnl_class(t.pnl_pct)}">{t.pnl_pct:+.2f}%</td>
<td class="{_pnl_class(t.pnl_amount)}">${t.pnl_amount:+.1f}</td>
<td>{t.holding_bars}</td>
<td style="font-size:12px">{rlabel}</td>
</tr>'''

    html += '''
</tbody></table></div></div>

<script>
function applyFilters() {
    const symbol = document.getElementById('filter-symbol').value;
    const signal = document.getElementById('filter-signal').value;
    const regime = document.getElementById('filter-regime').value;
    const direction = document.getElementById('filter-direction').value;
    const result = document.getElementById('filter-result').value;
    let count = 0;
    document.querySelectorAll('#trade-table tbody tr').forEach(row => {
        const match = (!symbol || row.dataset.symbol === symbol)
            && (!signal || row.dataset.signal === signal)
            && (!regime || row.dataset.regime === regime)
            && (!direction || row.dataset.direction === direction)
            && (!result || row.dataset.result === result);
        row.style.display = match ? '' : 'none';
        if (match) count++;
    });
    document.getElementById('trade-count').textContent = `显示 ${count} 笔`;
}

function sortBy(field) {
    const tbody = document.querySelector('#trade-table tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const colMap = { pnl_pct: 8, pnl_amount: 9, bars: 10 };
    const col = colMap[field];
    rows.sort((a, b) => {
        const va = parseFloat(a.children[col].textContent.replace(/[^\\d.-]/g, ''));
        const vb = parseFloat(b.children[col].textContent.replace(/[^\\d.-]/g, ''));
        return vb - va;
    });
    rows.forEach(r => tbody.appendChild(r));
}
applyFilters();
</script>
</body>
</html>'''

    return html


# ═══════════════════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    tester = MatrixBacktester()
    all_reports = []
    all_matrix_data = []

    for symbol, tf, source in SYMBOLS:
        t0 = time.time()
        print(f"── {symbol} · {tf} ──")

        try:
            candles = load_data(symbol, tf, source)
        except Exception as e:
            print(f"  ⚠ 数据加载失败: {e}")
            continue

        if len(candles) < 150:
            print(f"  ⚠ 数据不足: {len(candles)}根")
            continue

        # 趋势分类
        print("  趋势分类...")
        regimes = classify(candles)

        # 信号检测
        print("  信号检测...")
        signals_map = compute_all_signals(candles)

        # 统计信号频次
        signal_counts = {name: sum(1 for s in arr if s != 0) for name, arr in signals_map.items()}
        print(f"  信号触发: {signal_counts}")

        # 统计 regime 分布
        from collections import Counter
        regime_dist = Counter(r for r in regimes if r != -99)
        print(f"  状态分布: {dict(regime_dist)}")

        # 矩阵回测
        print("  回测中...")
        report = tester.run(candles, signals_map, regimes, symbol, tf)
        elapsed = time.time() - t0

        wins = sum(1 for t in report.all_trades if t.pnl_pct >= 0)
        total = len(report.all_trades)
        wr = wins / total * 100 if total > 0 else 0
        total_pnl = sum(t.pnl_amount for t in report.all_trades)
        print(f"  {total}笔 | 胜率{wr:.1f}% | 总盈亏${total_pnl:+.0f} | {elapsed:.1f}s")

        all_reports.append(report)

        # 构建 JSON 数据
        matrix_data = {
            "symbol": symbol,
            "timeframe": tf,
            "total_trades": total,
            "win_rate": round(wr, 1),
            "total_pnl": round(total_pnl, 2),
            "signal_counts": signal_counts,
            "regime_distribution": {str(k): v for k, v in regime_dist.items()},
            "matrix": {},
        }
        for sig_name in report.signal_names:
            matrix_data["matrix"][sig_name] = {}
            for rid in REGIME_ORDER:
                cell = report.matrix.get(sig_name, {}).get(rid)
                if cell and cell.trades > 0:
                    matrix_data["matrix"][sig_name][str(rid)] = {
                        "trades": cell.trades,
                        "win_rate": cell.win_rate,
                        "avg_pnl": cell.avg_pnl,
                        "profit_factor": cell.profit_factor,
                        "max_win": cell.max_win,
                        "max_loss": cell.max_loss,
                        "max_consecutive_losses": cell.max_consecutive_losses,
                        "avg_bars": cell.avg_bars,
                    }
        all_matrix_data.append(matrix_data)

    # — 生成 HTML —
    print("\n生成报告...")
    html = build_html(all_reports, all_matrix_data)
    html_path = OUTPUT_DIR / "signal_matrix.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 热力图: {html_path}")

    # — 生成 JSON —
    json_path = OUTPUT_DIR / "signal_matrix.json"
    trades_json = []
    for r in all_reports:
        for t in r.all_trades:
            trades_json.append({
                "symbol": r.symbol,
                "signal": t.signal,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "pnl_pct": t.pnl_pct,
                "pnl_amount": t.pnl_amount,
                "holding_bars": t.holding_bars,
                "regime_at_entry": t.regime_at_entry,
                "max_favorable_pct": t.max_favorable_pct,
            })
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbols": all_matrix_data,
            "trades": trades_json,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ JSON: {json_path}")

    print(f"\n✅ 全部完成 → {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
