"""
交互式图表查看器 —— 逐笔交易标注。

每个品种一个独立 HTML，左侧 TradingView K线图，右侧交易列表。
一次只显示一笔交易的入场/出场标注。
"""
import asyncio
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.providers.local_crypto import LocalCryptoProvider
from app.providers.yahoo_finance import YahooFinanceProvider

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.core.signal_detectors import triangle_line_info, trendline_line_info

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "reports" / "charts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SIGNAL_COLORS = {
    "pinbar": "#2196F3", "engulfing": "#FF9800", "triangle": "#9C27B0",
    "box": "#4CAF50", "channel": "#FFEB3B", "trendline_break": "#00BCD4",
    "false_breakout": "#F44336", "macd_momentum": "#FF6B6B", "macd_divergence": "#FFD93D",
}
SIGNAL_NAMES_ZH = {
    "pinbar": "Pinbar", "engulfing": "吞没", "triangle": "三角形",
    "box": "箱体", "channel": "通道", "trendline_break": "趋势线突破",
    "false_breakout": "假突破", "macd_momentum": "MACD动能减速", "macd_divergence": "MACD背离",
}


def load_data(symbol: str, source: str) -> list[dict]:
    if source == "crypto":
        return asyncio.run(LocalCryptoProvider().get_full_history(symbol, "1day"))
    else:
        return asyncio.run(YahooFinanceProvider().get_time_series(symbol, "1day", outputsize=5000))


def _compute_macd(candles: list[dict]) -> list[dict]:
    """计算 MACD 柱状图数据。返回 [{time, value, color}]。"""
    closes = [float(c["close"]) for c in candles]
    times = [int(c["time"]) for c in candles]
    n = len(closes)
    if n < 26:
        return []

    # EMA
    def ema(values, period):
        result = [None] * len(values)
        k = 2.0 / (period + 1)
        result[period - 1] = sum(values[:period]) / period
        for i in range(period, len(values)):
            result[i] = values[i] * k + result[i - 1] * (1 - k)
        return result

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [None] * n
    for i in range(26, n):
        if ema12[i] is not None and ema26[i] is not None:
            macd_line[i] = ema12[i] - ema26[i]

    first = next((i for i, v in enumerate(macd_line) if v is not None), None)
    if first is None:
        return []

    valid = [v for v in macd_line if v is not None]
    sig_valid = ema(valid, 9)
    signal_line = [None] * n
    for j, i in enumerate(range(first, n)):
        signal_line[i] = sig_valid[j]

    result = []
    for i in range(n):
        if macd_line[i] is not None and signal_line[i] is not None:
            val = macd_line[i] - signal_line[i]
            result.append({
                "time": times[i],
                "value": round(val, 6),
                "color": "#26a69a" if val >= 0 else "#ef5350",
            })
    return result


def build_html(symbol: str, candles: list[dict], trades: list[dict]) -> str:
    candle_js = []
    for c in candles:
        candle_js.append({
            "time": int(c["time"]),
            "open": float(c["open"]), "high": float(c["high"]),
            "low": float(c["low"]), "close": float(c["close"]),
        })

    macd_js = _compute_macd(candles)

    # 构建时间→索引的映射（用于信号条定位）
    time_to_idx = {int(c["time"]): idx for idx, c in enumerate(candles)}

    # 计算线条元数据（三角形和趋势线）—— key 必须和 trades_js 中的 id 对齐
    line_metadata = {}
    for idx, t in enumerate(trades):
        if t["signal"] in ("triangle", "trendline_break"):
            entry_ts = t["entry_time"]
            if entry_ts in time_to_idx:
                entry_idx = time_to_idx[entry_ts]
                signal_idx = entry_idx - 1  # 入场bar的前一根才是信号触发bar
                if signal_idx >= 0:
                    if t["signal"] == "triangle":
                        info = triangle_line_info(candles, signal_idx)
                    else:
                        info = trendline_line_info(candles, signal_idx)
                    if info:
                        line_metadata[idx] = info

    trades_js = []
    for i, t in enumerate(trades):
        trades_js.append({
            "id": i, "signal": t["signal"], "direction": t["direction"],
            "entry_time": t["entry_time"], "entry_price": t["entry_price"],
            "exit_time": t["exit_time"], "exit_price": t["exit_price"],
            "exit_reason": t["exit_reason"], "pnl_pct": t["pnl_pct"],
            "pnl_amount": t["pnl_amount"], "holding_bars": t["holding_bars"],
            "regime": t["regime_at_entry"],
            "max_fav_pct": t.get("max_favorable_pct", 0),
        })

    by_signal: dict[str, list] = defaultdict(list)
    for t in trades:
        by_signal[t["signal"]].append(t)

    wins = sum(1 for t in trades if t["pnl_pct"] >= 0)
    total = len(trades)
    total_pnl = sum(t["pnl_amount"] for t in trades)

    legend_html = "".join(
        f'<span style="display:inline-block;margin:4px 12px;color:{SIGNAL_COLORS.get(s,"#888")}">'
        f'● {SIGNAL_NAMES_ZH.get(s,s)} ({len(tr)}笔)</span>'
        for s, tr in sorted(by_signal.items())
    )
    signal_options = "".join(
        f'<option value="{s}">{SIGNAL_NAMES_ZH.get(s,s)}</option>'
        for s in sorted(by_signal.keys())
    )

    wr_color = '#26a69a' if (wins / total >= 0.5 if total else False) else '#ef5350'
    pnl_color = '#26a69a' if total_pnl >= 0 else '#ef5350'

    # ── 构建 HTML ──
    parts = []
    parts.append(f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>交易标注 — {symbol}</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #131722; color: #d1d4dc; display: flex; height: 100vh; overflow: hidden; }}
#chart-container {{ flex: 1; position: relative; }}
#sidebar {{ width: 340px; background: #1e222d; border-left: 1px solid #2a2e39; display: flex; flex-direction: column; overflow: hidden; }}
#sidebar-header {{ padding: 16px; border-bottom: 1px solid #2a2e39; }}
#sidebar-header h2 {{ font-size: 16px; color: #fff; margin-bottom: 4px; }}
.stats-row {{ display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }}
.stat {{ background: #2a2e39; padding: 6px 10px; border-radius: 6px; font-size: 13px; }}
.stat .v {{ font-size: 16px; font-weight: 700; }}
.green {{ color: #26a69a; }}
.red {{ color: #ef5350; }}
#filters {{ padding: 12px 16px; border-bottom: 1px solid #2a2e39; }}
#filters select {{ background: #2a2e39; color: #d1d4dc; border: 1px solid #3a3e49; padding: 6px 10px; border-radius: 4px; margin: 4px 4px 4px 0; font-size: 12px; }}
#trade-list {{ flex: 1; overflow-y: auto; padding: 0; }}
.trade-item {{ padding: 10px 16px; border-bottom: 1px solid #2a2e39; cursor: pointer; font-size: 13px; transition: background 0.15s; }}
.trade-item:hover {{ background: #2a2e39; }}
.trade-item.active {{ background: #3a3e49; border-left: 3px solid #2196F3; }}
.trade-item .top-row {{ display: flex; justify-content: space-between; align-items: center; }}
.trade-item .signal-tag {{ font-size: 11px; padding: 1px 6px; border-radius: 3px; }}
.trade-item .dir-tag {{ font-size: 11px; padding: 1px 6px; border-radius: 3px; }}
.trade-item .detail {{ font-size: 11px; color: #787b86; margin-top: 4px; }}
.trade-item .detail-row {{ display: flex; justify-content: space-between; margin-top: 2px; }}
#legend {{ position: absolute; bottom: 10px; left: 10px; background: rgba(30,34,45,0.9); padding: 10px 14px; border-radius: 8px; font-size: 11px; z-index: 10; max-width: 500px; }}
#hint {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); color: #787b86; font-size: 16px; z-index: 5; pointer-events: none; }}
</style>
</head>
<body>
<div id="chart-container">
  <div id="hint">👆 点击右侧列表查看交易</div>
  <div id="legend">{legend_html}</div>
</div>
<div id="sidebar">
  <div id="sidebar-header">
    <h2>{symbol} · 日线</h2>
    <div class="stats-row">
      <div class="stat">总交易 <div class="v" style="color:#fff">{total}</div></div>
      <div class="stat">胜率 <div class="v" style="color:{wr_color}">{wins/total*100:.1f}%</div></div>
      <div class="stat">总盈亏 <div class="v" style="color:{pnl_color}">${total_pnl:+,.0f}</div></div>
    </div>
  </div>
  <div id="filters">
    <select id="filter-signal" onchange="applyFilters()">
      <option value="">全部信号</option>
      {signal_options}
    </select>
    <select id="filter-dir" onchange="applyFilters()">
      <option value="">全部方向</option>
      <option value="long">多头</option>
      <option value="short">空头</option>
    </select>
    <select id="filter-result" onchange="applyFilters()">
      <option value="">全部结果</option>
      <option value="win">盈利</option>
      <option value="loss">亏损</option>
    </select>
    <select id="sort-by" onchange="applyFilters()">
      <option value="pnl_desc">盈亏% ↓</option>
      <option value="pnl_asc">盈亏% ↑</option>
      <option value="time_asc">时间 ↑</option>
      <option value="time_desc">时间 ↓</option>
    </select>
  </div>
  <div id="trade-list"></div>
</div>
''')

    parts.append(f'''<script>
const CANDLES = {json.dumps(candle_js)};
const TRADES = {json.dumps(trades_js)};
const MACD = {json.dumps(macd_js)};
const LINE_META = {json.dumps(line_metadata)};
const SIGNAL_COLORS = {json.dumps(SIGNAL_COLORS)};
let currentTrade = null;
let infoEntryLine = null, infoExitLine = null;
let drawnLineSeries = [];  // 存储绘制的线条序列

// ── 图表 ──
const chart = LightweightCharts.createChart(document.getElementById('chart-container'), {{
    height: window.innerHeight,
    layout: {{ background: {{ type: 'solid', color: '#131722' }}, textColor: '#d1d4dc' }},
    grid: {{ vertLines: {{ color: '#2a2e39' }}, horzLines: {{ color: '#2a2e39' }} }},
    crosshair: {{ mode: 0 }},
    rightPriceScale: {{ borderColor: '#2a2e39' }},
    timeScale: {{ borderColor: '#2a2e39', timeVisible: true }},
}});

const candleSeries = chart.addCandlestickSeries({{
    upColor: '#26a69a', downColor: '#ef5350', borderUpColor: '#26a69a', borderDownColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
}});
candleSeries.setData(CANDLES);

// ── MACD 柱状图（底部副图）──
const macdSeries = chart.addHistogramSeries({{
    priceFormat: {{ type: 'volume' }},
    priceScaleId: 'macd',
}});
chart.priceScale('macd').applyOptions({{
    scaleMargins: {{ top: 0.82, bottom: 0.05 }},
}});
macdSeries.setData(MACD);

// ── 单笔标注 ──
function clearOverlays() {{
    candleSeries.setMarkers([]);
    if (infoEntryLine) {{ candleSeries.removePriceLine(infoEntryLine); infoEntryLine = null; }}
    if (infoExitLine) {{ candleSeries.removePriceLine(infoExitLine); infoExitLine = null; }}
    drawnLineSeries.forEach(ls => chart.removeSeries(ls));
    drawnLineSeries = [];
}}

function showTrade(trade) {{
    clearOverlays();
    if (!trade) {{ document.getElementById('hint').style.display = 'block'; return; }}
    document.getElementById('hint').style.display = 'none';

    const color = SIGNAL_COLORS[trade.signal] || '#888';
    const isLong = trade.direction === 'long';
    const isWin = trade.pnl_pct >= 0;
    const pnlColor = isWin ? '#26a69a' : '#ef5350';

    candleSeries.setMarkers([
        {{
            time: trade.entry_time,
            position: isLong ? 'belowBar' : 'aboveBar',
            color: color, shape: isLong ? 'arrowUp' : 'arrowDown',
            text: trade.signal + '\\\\n入场 ' + trade.entry_price.toFixed(2),
            size: 3,
        }},
        {{
            time: trade.exit_time,
            position: isLong ? 'aboveBar' : 'belowBar',
            color: pnlColor, shape: 'circle',
            text: trade.exit_reason + '\\\\n' + (trade.pnl_pct>=0?'+':'') + trade.pnl_pct.toFixed(2) + '%',
            size: 3,
        }},
    ]);

    infoEntryLine = candleSeries.createPriceLine({{
        price: trade.entry_price, color: color, lineWidth: 2, lineStyle: 2,
        axisLabelVisible: true, title: '入场',
    }});
    infoExitLine = candleSeries.createPriceLine({{
        price: trade.exit_price, color: pnlColor, lineWidth: 2, lineStyle: 2,
        axisLabelVisible: true, title: '出场',
    }});

    // — 画三角形边界线 / 趋势线 —
    const meta = LINE_META[trade.id];
    if (meta) {{
        // 三角形/趋势线：实线，共享主图价格轴
        if (meta.upper) {{
            const lsUp = chart.addLineSeries({{ color: color, lineWidth: 2 }});
            lsUp.setData([{{ time: meta.upper.start_time, value: meta.upper.start_price }}, {{ time: meta.upper.end_time, value: meta.upper.end_price }}]);
            drawnLineSeries.push(lsUp);
            const lsLo = chart.addLineSeries({{ color: color, lineWidth: 2 }});
            lsLo.setData([{{ time: meta.lower.start_time, value: meta.lower.start_price }}, {{ time: meta.lower.end_time, value: meta.lower.end_price }}]);
            drawnLineSeries.push(lsLo);
        }} else if (meta.start_time) {{
            const ls = chart.addLineSeries({{ color: color, lineWidth: 2 }});
            ls.setData([{{ time: meta.start_time, value: meta.start_price }}, {{ time: meta.end_time, value: meta.end_price }}]);
            drawnLineSeries.push(ls);
        }}
    }}
}}

// ── 信息卡 ──
let infoCard = null;
function showInfoCard(trade) {{
    if (!infoCard) {{
        infoCard = document.createElement('div');
        infoCard.id = 'info-card';
        infoCard.style.cssText = 'position:absolute;top:10px;right:10px;background:rgba(30,34,45,0.95);padding:16px;border-radius:8px;border:1px solid #2a2e39;z-index:10;min-width:220px;font-size:13px;';
        document.getElementById('chart-container').appendChild(infoCard);
    }}
    if (!trade) {{ infoCard.style.display = 'none'; return; }}
    infoCard.style.display = 'block';
    const color = SIGNAL_COLORS[trade.signal] || '#888';
    const pc = trade.pnl_pct >= 0 ? 'green' : 'red';
    const dir = trade.direction === 'long' ? '多头' : '空头';
    const dirColor = trade.direction === 'long' ? '#26a69a' : '#ef5350';
    infoCard.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <span style="padding:2px 8px;border-radius:3px;background:${{color}}22;color:${{color}};font-weight:600">${{trade.signal}}</span>
        <span style="padding:2px 8px;border-radius:3px;font-weight:600;color:${{dirColor}}">${{dir}}</span>
    </div>
    <table style="width:100%;font-size:12px;color:#d1d4dc">
        <tr><td style="color:#787b86">入场日</td><td>${{new Date(trade.entry_time*1000).toISOString().slice(0,10)}}</td><td style="text-align:right">$${{trade.entry_price.toFixed(2)}}</td></tr>
        <tr><td style="color:#787b86">出场日</td><td>${{new Date(trade.exit_time*1000).toISOString().slice(0,10)}}</td><td style="text-align:right">$${{trade.exit_price.toFixed(2)}}</td></tr>
        <tr><td style="color:#787b86">原因</td><td colspan="2">${{trade.exit_reason}}</td></tr>
        <tr><td style="color:#787b86">持仓</td><td colspan="2">${{trade.holding_bars}} 根K线</td></tr>
        <tr><td style="color:#787b86">最大浮盈</td><td colspan="2">${{trade.max_fav_pct>=0?'+':''}}${{trade.max_fav_pct.toFixed(2)}}%</td></tr>
    </table>
    <div style="font-size:18px;font-weight:700;margin-top:8px;color:${{trade.pnl_pct>=0?'#26a69a':'#ef5350'}}">
        ${{trade.pnl_pct>=0?'+':''}}${{trade.pnl_pct.toFixed(2)}}% / $${{trade.pnl_amount.toFixed(1)}}
    </div>`;
}}

// ── 交易列表 ──
function renderTradeList(filtered) {{
    const container = document.getElementById('trade-list');
    container.innerHTML = filtered.map(t => {{
        const color = SIGNAL_COLORS[t.signal] || '#888';
        const pc = t.pnl_pct >= 0 ? 'green' : 'red';
        const entryDate = new Date(t.entry_time*1000).toISOString().slice(0,10);
        const exitDate = new Date(t.exit_time*1000).toISOString().slice(0,10);
        const dirName = t.direction === 'long' ? '多' : '空';
        const dirColor = t.direction === 'long' ? '#26a69a' : '#ef5350';
        return `<div class="trade-item" data-id="${{t.id}}" onclick="focusTrade(${{t.id}})">
          <div class="top-row">
            <span class="signal-tag" style="background:${{color}}22;color:${{color}}">${{t.signal}}</span>
            <span class="dir-tag" style="background:${{dirColor}}22;color:${{dirColor}}">${{dirName}}</span>
            <span class="${{pc}}" style="font-weight:700;font-size:14px">${{t.pnl_pct>=0?'+':''}}${{t.pnl_pct.toFixed(2)}}%</span>
          </div>
          <div class="detail-row">
            <span>${{entryDate}} → ${{exitDate}}</span>
            <span>${{t.holding_bars}}K | ${{t.exit_reason}}</span>
          </div>
        </div>`;
    }}).join('');
}}

function applyFilters() {{
    const sig = document.getElementById('filter-signal').value;
    const dir = document.getElementById('filter-dir').value;
    const result = document.getElementById('filter-result').value;
    const sort = document.getElementById('sort-by').value;

    let filtered = TRADES.filter(t => {{
        if (sig && t.signal !== sig) return false;
        if (dir && t.direction !== dir) return false;
        if (result === 'win' && t.pnl_pct < 0) return false;
        if (result === 'loss' && t.pnl_pct >= 0) return false;
        return true;
    }});

    if (sort === 'pnl_desc') filtered.sort((a,b) => b.pnl_pct - a.pnl_pct);
    else if (sort === 'pnl_asc') filtered.sort((a,b) => a.pnl_pct - b.pnl_pct);
    else if (sort === 'time_asc') filtered.sort((a,b) => a.entry_time - b.entry_time);
    else if (sort === 'time_desc') filtered.sort((a,b) => b.entry_time - a.entry_time);

    renderTradeList(filtered);
    clearSelection();
}}

function focusTrade(id) {{
    const t = TRADES.find(tr => tr.id === id);
    if (!t) return;
    currentTrade = t;

    document.querySelectorAll('.trade-item').forEach(el => el.classList.remove('active'));
    const el = document.querySelector('.trade-item[data-id="' + id + '"]');
    if (el) {{ el.classList.add('active'); el.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }}); }}

    showTrade(t);
    showInfoCard(t);

    const margin = Math.max((t.exit_time - t.entry_time) * 1.5, 86400 * 20);
    chart.timeScale().setVisibleRange({{
        from: t.entry_time - margin,
        to: t.exit_time + margin,
    }});
}}

function clearSelection() {{
    currentTrade = null;
    clearOverlays();
    showInfoCard(null);
    document.querySelectorAll('.trade-item').forEach(el => el.classList.remove('active'));
    document.getElementById('hint').style.display = 'block';
}}

// 点击图表空白处取消选中
chart.subscribeClick(() => clearSelection());

applyFilters();

// 键盘: ↑↓ 浏览, Esc 取消
document.addEventListener('keydown', e => {{
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {{
        if (document.activeElement && document.activeElement.tagName === 'SELECT') return;
        e.preventDefault();
        const items = document.querySelectorAll('.trade-item');
        const active = document.querySelector('.trade-item.active');
        let idx = active ? Array.from(items).indexOf(active) : (e.key === 'ArrowUp' ? items.length : -1);
        if (e.key === 'ArrowUp') idx = Math.max(0, idx - 1);
        else idx = Math.min(items.length - 1, idx + 1);
        if (items[idx]) items[idx].click();
    }}
    if (e.key === 'Escape') clearSelection();
}});
</script>
</body>
</html>''')

    return "".join(parts)


def main():
    json_path = OUTPUT_DIR.parent / "signal_matrix.json"
    if not json_path.exists():
        print("❌ 未找到 signal_matrix.json，请先运行 run_analysis.py")
        return

    with open(json_path) as f:
        data = json.load(f)

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for t in data["trades"]:
        by_symbol[t["symbol"]].append(t)

    sources = {"BTC-USD": "crypto", "ETH-USD": "crypto", "AAPL": "stock", "GOOGL": "stock"}

    for symbol in sorted(by_symbol.keys()):
        trades = by_symbol[symbol]
        source = sources.get(symbol, "stock")
        print(f"── {symbol}: 加载K线数据...")

        try:
            candles = load_data(symbol, source)
        except Exception as e:
            print(f"  ⚠ 数据加载失败: {e}")
            continue

        print(f"  {len(candles)} 根K线, {len(trades)} 笔交易")

        html = build_html(symbol, candles, trades)
        fname = symbol.replace("-", "_").replace("/", "_")
        filepath = OUTPUT_DIR / f"{fname}.html"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        size_kb = len(html) / 1024
        print(f"  ✅ {filepath} ({size_kb:.0f} KB)")

    print(f"\n✅ 全部完成 → {OUTPUT_DIR}/")
    print("用浏览器打开 reports/charts/AAPL.html 即可查看。")


if __name__ == "__main__":
    main()
