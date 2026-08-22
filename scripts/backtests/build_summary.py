"""
汇总页面 —— 交易总览表格 + 图表嵌入。
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "reports"

REGIME_LABELS = {
    -1: "?", 0: "震荡低波", 1: "震荡高波", 2: "弱趋势低波",
    3: "弱趋势高波", 4: "强趋势低波", 5: "强趋势高波",
}

SYMBOL_COLORS = {"BTC-USD": "#F7931A", "ETH-USD": "#627EEA", "AAPL": "#555555", "GOOGL": "#4285F4"}


def load_trades(symbol, tf):
    path = OUTPUT_DIR / f"trades_{symbol.replace('-', '_')}_{tf}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def build_html():
    symbols = [("BTC-USD", "1day"), ("ETH-USD", "1day"), ("AAPL", "1day"), ("GOOGL", "1day")]

    # 加载所有交易数据
    all_data = []
    all_trades = []
    for symbol, tf in symbols:
        data = load_trades(symbol, tf)
        if not data:
            continue
        all_data.append(data)
        for t in data["trades"]:
            t["_symbol"] = symbol
            t["_tf"] = tf
            all_trades.append(t)

    # 汇总统计
    summary_rows = []
    for data in all_data:
        sym = data["symbol"]
        trades = data["trades"]
        if not trades:
            continue
        winning = [t for t in trades if t["pnl_pct"] >= 0]
        losing = [t for t in trades if t["pnl_pct"] < 0]
        wr = len(winning) / len(trades) * 100
        avg_pnl = sum(t["pnl_pct"] for t in trades) / len(trades)
        total_pnl = sum(t["pnl_amount"] for t in trades)

        # 按策略分
        by_strat = {}
        for stype in ["trend", "momentum", "reversal"]:
            st = [t for t in trades if t["strategy"] == stype]
            if st:
                sw = [t for t in st if t["pnl_pct"] >= 0]
                by_strat[stype] = {
                    "n": len(st), "wr": round(len(sw)/len(st)*100, 1),
                    "avg": round(sum(t["pnl_pct"] for t in st)/len(st), 2),
                    "total": round(sum(t["pnl_amount"] for t in st), 1),
                }
            else:
                by_strat[stype] = {"n": 0, "wr": 0, "avg": 0, "total": 0}

        summary_rows.append({
            "symbol": sym,
            "trades": len(trades),
            "wr": round(wr, 1),
            "avg_pnl": round(avg_pnl, 2),
            "total_pnl": round(total_pnl, 1),
            "by_strat": by_strat,
            "winning": len(winning),
            "losing": len(losing),
        })

    # 构建 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>策略回测 · 交易总览</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
h1 {{ color: #fff; margin-bottom: 8px; }}
.subtitle {{ color: #888; margin-bottom: 24px; }}
.card {{ background: #16213e; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #0f3460; }}
.card h2 {{ color: #e94560; margin-bottom: 12px; font-size: 18px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{ background: #0f3460; color: #a0b4d0; padding: 8px 12px; text-align: left; font-weight: 600; }}
td {{ padding: 6px 12px; border-bottom: 1px solid #1a1a3e; }}
tr:hover {{ background: rgba(233,69,96,0.05); }}
.positive {{ color: #26a69a; }}
.negative {{ color: #ef5350; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
.badge-trend {{ background: #1f77b4; color: #fff; }}
.badge-momentum {{ background: #ff7f0e; color: #fff; }}
.badge-reversal {{ background: #2ca02c; color: #fff; }}
.badge-long {{ background: #26a69a22; color: #26a69a; border: 1px solid #26a69a44; }}
.badge-short {{ background: #ef535022; color: #ef5350; border: 1px solid #ef535044; }}
.chart-link {{ display: inline-block; padding: 8px 16px; margin: 8px 8px 8px 0; background: #0f3460; color: #fff; border-radius: 6px; text-decoration: none; font-size: 14px; }}
.chart-link:hover {{ background: #e94560; }}
.filter-bar {{ margin-bottom: 16px; }}
.filter-bar select, .filter-bar input {{ background: #0f3460; color: #fff; border: 1px solid #1a3a6e; padding: 6px 12px; border-radius: 6px; margin-right: 8px; font-size: 13px; }}
.sort-btn {{ background: #0f3460; color: #a0b4d0; border: 1px solid #1a3a6e; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
.sort-btn:hover {{ background: #1a3a6e; }}
</style>
</head>
<body>
<h1>📊 策略回测 · 交易总览</h1>
<p class="subtitle">趋势 + 动能 + 反转 | $1000 本金 2× 杠杆 | 日线 | 生成于 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>

<div class="card">
<h2>📈 品种汇总</h2>
<table>
<thead><tr>
<th>品种</th><th>总交易</th><th>胜率</th><th>均盈%</th><th>总盈亏$</th>
<th>趋势</th><th>动能</th><th>反转</th>
</tr></thead>
<tbody>
"""

    for r in summary_rows:
        bs = r["by_strat"]
        html += f"""<tr>
<td style="color:{SYMBOL_COLORS.get(r['symbol'], '#fff')};font-weight:700">{r['symbol']}</td>
<td>{r['trades']}</td>
<td class="{'positive' if r['wr'] >= 50 else 'negative'}">{r['wr']}%</td>
<td class="{'positive' if r['avg_pnl'] >= 0 else 'negative'}">{r['avg_pnl']:+.2f}%</td>
<td class="{'positive' if r['total_pnl'] >= 0 else 'negative'}">${r['total_pnl']:+,.1f}</td>
<td><span class="badge badge-trend">{bs['trend']['n']}笔 {bs['trend']['wr']}% ${bs['trend']['total']:+,.0f}</span></td>
<td><span class="badge badge-momentum">{bs['momentum']['n']}笔 {bs['momentum']['wr']}% ${bs['momentum']['total']:+,.0f}</span></td>
<td><span class="badge badge-reversal">{bs['reversal']['n']}笔 {bs['reversal']['wr']}% ${bs['reversal']['total']:+,.0f}</span></td>
</tr>"""

    html += """
</tbody></table>
</div>

<div class="card">
<h2>🔗 交互式图表</h2>
"""

    for symbol, tf in symbols:
        fname = f"chart_{symbol.replace('-', '_')}_{tf}.html"
        dfname = f"trades_detail_{symbol.replace('-', '_')}_{tf}.html"
        color = SYMBOL_COLORS.get(symbol, "#888")
        html += f'<a class="chart-link" href="{fname}" target="_blank" style="border-left: 3px solid {color}">📊 {symbol} · {tf} (总览图)</a>\n'
        html += f'<a class="chart-link" href="{dfname}" target="_blank" style="border-left: 3px solid {color}">🔍 {symbol} · {tf} (逐笔图)</a>\n'

    html += """<p style="color:#888;margin-top:12px;font-size:13px">
📊 总览: 所有交易在一张图上 | 🔍 逐笔: 每笔交易独立图表 + 左侧导航 (绿=盈>5%, 黄=微盈, 红=亏)
</p>
</div>

<div class="card">
<h2>📋 全部交易记录</h2>
<div class="filter-bar">
<select id="filter-symbol" onchange="applyFilters()">
<option value="">全部品种</option>
"""
    for symbol, tf in symbols:
        html += f'<option value="{symbol}">{symbol}</option>\n'
    html += """</select>
<select id="filter-strategy" onchange="applyFilters()">
<option value="">全部策略</option>
<option value="trend">趋势</option>
<option value="momentum">动能</option>
<option value="reversal">反转</option>
</select>
<select id="filter-direction" onchange="applyFilters()">
<option value="">全部方向</option>
<option value="long">多头</option>
<option value="short">空头</option>
</select>
<select id="filter-result" onchange="applyFilters()">
<option value="">全部结果</option>
<option value="win">盈利</option>
<option value="loss">亏损</option>
</select>
<button class="sort-btn" onclick="sortBy('pnl_pct')">按盈亏%排序</button>
<button class="sort-btn" onclick="sortBy('pnl_amount')">按盈亏$排序</button>
<button class="sort-btn" onclick="sortBy('holding_bars')">按持仓K线数排序</button>
<span id="trade-count" style="color:#888;margin-left:16px"></span>
</div>
<div style="max-height:600px;overflow-y:auto">
<table id="trade-table">
<thead><tr>
<th>品种</th><th>策略</th><th>方向</th><th>入场时间</th><th>入场价</th>
<th>出场时间</th><th>出场价</th><th>出场原因</th>
<th>盈亏%</th><th>盈亏$</th><th>持仓K线</th><th>入场Regime</th>
</tr></thead>
<tbody>
"""
    # 按盈亏排序
    all_trades.sort(key=lambda t: t["pnl_pct"], reverse=True)
    for t in all_trades:
        et = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc).strftime("%Y-%m-%d")
        xt = datetime.fromtimestamp(t["exit_time"], tz=timezone.utc).strftime("%Y-%m-%d") if t["exit_time"] else "?"
        pnl_class = "positive" if t["pnl_pct"] >= 0 else "negative"
        dir_badge = f'<span class="badge badge-long">多</span>' if t["direction"] == "long" else f'<span class="badge badge-short">空</span>'
        strat_badge = f'<span class="badge badge-{t["strategy"]}">{t["strategy"]}</span>'

        html += f"""<tr data-symbol="{t['_symbol']}" data-strategy="{t['strategy']}" data-direction="{t['direction']}" data-result="{'win' if t['pnl_pct'] >= 0 else 'loss'}">
<td style="color:{SYMBOL_COLORS.get(t['_symbol'], '#fff')}">{t['_symbol']}</td>
<td>{strat_badge}</td>
<td>{dir_badge}</td>
<td>{et}</td>
<td>{t['entry_price']:.2f}</td>
<td>{xt}</td>
<td>{t['exit_price']:.2f}</td>
<td style="font-size:12px">{t['exit_reason']}</td>
<td class="{pnl_class}">{t['pnl_pct']:+.2f}%</td>
<td class="{pnl_class}">${t['pnl_amount']:+.1f}</td>
<td>{t['holding_bars']}</td>
<td style="font-size:12px">{REGIME_LABELS.get(t['regime_at_entry'], '?')}</td>
</tr>"""

    html += """
</tbody></table>
</div>
</div>

<script>
function applyFilters() {
    const symbol = document.getElementById('filter-symbol').value;
    const strategy = document.getElementById('filter-strategy').value;
    const direction = document.getElementById('filter-direction').value;
    const result = document.getElementById('filter-result').value;
    let count = 0;
    document.querySelectorAll('#trade-table tbody tr').forEach(row => {
        const match = (!symbol || row.dataset.symbol === symbol)
            && (!strategy || row.dataset.strategy === strategy)
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
    // extract value from td at correct column
    const colMap = { pnl_pct: 8, pnl_amount: 9, holding_bars: 10 };
    const col = colMap[field];
    rows.sort((a, b) => {
        const va = parseFloat(a.children[col].textContent.replace(/[^\\d.-]/g, ''));
        const vb = parseFloat(b.children[col].textContent.replace(/[^\\d.-]/g, ''));
        return vb - va;
    });
    rows.forEach(r => tbody.appendChild(r));
}

// init
applyFilters();
</script>
</body>
</html>"""

    output_path = OUTPUT_DIR / "index.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 汇总页面: {output_path}")


if __name__ == "__main__":
    build_html()
