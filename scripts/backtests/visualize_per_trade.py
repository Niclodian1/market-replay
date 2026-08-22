"""
逐笔交易可视化 —— 每笔交易独立图表，支持翻页浏览。
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.providers.local_crypto import LocalCryptoProvider
from app.providers.yahoo_finance import YahooFinanceProvider
from app.core.strategy_backtest_engine import StrategyBacktester, AccountConfig
from app.core.indicators import _sma

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "reports"
OUTPUT_DIR.mkdir(exist_ok=True)

CONTEXT_BEFORE = 50   # 入场前的 K 线数
CONTEXT_AFTER = 20    # 出场后的 K 线数

REGIME_LABELS = {
    -1: "?", 0: "震荡低波", 1: "震荡高波", 2: "弱趋势低波",
    3: "弱趋势高波", 4: "强趋势低波", 5: "强趋势高波",
}

SYMBOL_COLORS = {"BTC-USD": "#F7931A", "ETH-USD": "#627EEA", "AAPL": "#555555", "GOOGL": "#4285F4"}

SYMBOLS_TF_SRC = [
    ("BTC-USD", "1day", "crypto"),
    ("ETH-USD", "1day", "crypto"),
    ("AAPL", "1day", "stock"),
    ("GOOGL", "1day", "stock"),
]


def load_data(symbol, tf, source):
    if source == "crypto":
        return asyncio.run(LocalCryptoProvider().get_full_history(symbol, tf))
    else:
        return asyncio.run(YahooFinanceProvider().get_time_series(symbol, tf, outputsize=5000))


def _find_idx(candles, timestamp):
    import bisect
    times = [c["time"] for c in candles]
    idx = bisect.bisect_left(times, timestamp)
    if idx < len(times) and times[idx] == timestamp:
        return idx
    return -1


def make_trade_chart(trade, candles, trade_num, total_trades):
    """为单笔交易生成独立 Plotly 图表。"""
    entry_idx = _find_idx(candles, trade["entry_time"])
    exit_idx = _find_idx(candles, trade["exit_time"]) if trade["exit_time"] else entry_idx + trade["holding_bars"]

    if entry_idx < 0:
        return None

    # 取上下文窗口
    start = max(0, entry_idx - CONTEXT_BEFORE)
    end = min(len(candles), max(entry_idx, exit_idx) + CONTEXT_AFTER + 1)
    window = candles[start:end]

    times = [datetime.fromtimestamp(c["time"], tz=timezone.utc) for c in window]
    closes = [c["close"] for c in window]
    opens = [c["open"] for c in window]
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]

    # MA
    ma20 = _sma([float(c) for c in closes], min(20, len(closes)))
    ma60 = _sma([float(c) for c in closes], min(60, len(closes)))

    entry_rel = entry_idx - start
    exit_rel = exit_idx - start
    trade_direction = trade["direction"]
    is_long = trade_direction == "long"
    pnl = trade["pnl_pct"]
    pnl_color = "#26a69a" if pnl >= 0 else "#ef5350"

    # 子图
    fig = make_subplots(
        rows=1, cols=1,
        subplot_titles=(
            f"#{trade_num}/{total_trades} | {trade['_symbol']} · {trade['_tf']} | "
            f"{trade['strategy']} {trade_direction} | "
            f"盈亏 <span style='color:{pnl_color}'>{pnl:+.2f}%</span> | "
            f"入场 {trade['entry_price']:.2f} → 出场 {trade['exit_price']:.2f} | "
            f"原因: {trade['exit_reason']}"
        ),
    )

    # K 线
    fig.add_trace(go.Candlestick(
        x=times, open=opens, high=highs, low=lows, close=closes,
        name="K线", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ))

    # MA 线
    ma20_vals = [ma20[i] if i < len(ma20) else None for i in range(len(times))]
    ma60_vals = [ma60[i] if i < len(ma60) else None for i in range(len(times))]
    fig.add_trace(go.Scatter(x=times, y=ma20_vals, name="MA20",
                             line=dict(color="#FFD700", width=1, dash="dot")))
    fig.add_trace(go.Scatter(x=times, y=ma60_vals, name="MA60",
                             line=dict(color="#FF6B6B", width=1, dash="dot")))

    # 入场标记
    if 0 <= entry_rel < len(times):
        fig.add_trace(go.Scatter(
            x=[times[entry_rel]], y=[trade["entry_price"]],
            mode="markers+text",
            marker=dict(
                symbol="triangle-up" if is_long else "triangle-down",
                size=18, color="#00ff00" if is_long else "#ff4444",
                line=dict(width=2, color="white"),
            ),
            text=["入场"],
            textposition="top center" if is_long else "bottom center",
            textfont=dict(color="white", size=14, family="Arial Black"),
            name="入场",
            showlegend=False,
        ))

    # 出场标记
    if 0 <= exit_rel < len(times):
        fig.add_trace(go.Scatter(
            x=[times[exit_rel]], y=[trade["exit_price"]],
            mode="markers+text",
            marker=dict(
                symbol="diamond", size=16, color=pnl_color,
                line=dict(width=2, color="white"),
            ),
            text=[f"出场\n{trade['exit_reason']}"],
            textposition="middle right",
            textfont=dict(color=pnl_color, size=12),
            name="出场",
            showlegend=False,
        ))

    # 入场到出场的连线（虚线）
    if 0 <= entry_rel < len(times) and 0 <= exit_rel < len(times):
        fig.add_trace(go.Scatter(
            x=[times[entry_rel], times[exit_rel]],
            y=[trade["entry_price"], trade["exit_price"]],
            mode="lines",
            line=dict(color=pnl_color, width=2, dash="dash"),
            showlegend=False,
        ))

    # 持仓区间高亮
    if 0 <= entry_rel < len(times) and 0 <= exit_rel < len(times):
        fig.add_vrect(
            x0=times[entry_rel], x1=times[exit_rel],
            fillcolor="green" if pnl >= 0 else "red",
            opacity=0.06, line_width=0,
        )

    # 进场 K 线的止损标记（趋势/动能为该 bar 极值，反转为极值外扩）
    if 0 <= entry_rel < len(times):
        # 估算止损位
        entry_bar = window[entry_rel]
        if trade["strategy"] == "reversal":
            stop = entry_bar["low"] * 0.98 if is_long else entry_bar["high"] * 1.02
        else:
            stop = entry_bar["low"] if is_long else entry_bar["high"]

        fig.add_hline(
            y=stop, line_dash="dot", line_color="red", opacity=0.5,
            annotation_text="止损≈", annotation_position="right",
        )

    # 布局信息
    info_text = (
        f"策略: {trade['strategy']} | 方向: {trade_direction} | "
        f"入场 Regime: {REGIME_LABELS.get(trade['regime_at_entry'], '?')} | "
        f"持仓: {trade['holding_bars']}根K线 | "
        f"最大浮盈: {trade['max_profit_pct']:+.1f}% | "
        f"风险金额: ${trade['risk_amount']:.1f} | "
        f"仓位: ${trade['position_size']:.1f}"
    )

    fig.update_layout(
        title=dict(font=dict(size=14)),
        xaxis_rangeslider_visible=False,
        height=600,
        hovermode="x unified",
        template="plotly_dark",
        margin=dict(l=40, r=40, t=60, b=40),
        annotations=[
            dict(
                x=0.02, y=0.98, xref="paper", yref="paper",
                text=info_text, showarrow=False,
                font=dict(size=11, color="#888"),
                bgcolor="rgba(0,0,0,0.3)",
            ),
        ],
    )

    return fig


def build_trade_pages(symbol, tf, trades, candles):
    """为该品种生成一个 HTML 文件，内含所有逐笔图表 + 导航。"""
    trades_sorted = sorted(trades, key=lambda t: t["pnl_pct"], reverse=True)
    total = len(trades_sorted)

    # 只对前 100 笔和后 20 笔生成图表（中间的大量交易对审查意义不大）
    # 用户可以通过 JSON 查看全部
    top_n = min(80, total)
    bottom_n = min(20, total - top_n)
    selected_indices = list(range(top_n)) + list(range(total - bottom_n, total))
    selected_indices = sorted(set(selected_indices))  # 去重并排序

    charts_html_parts = []
    for rank, ti in enumerate(selected_indices):
        t = trades_sorted[ti]
        t["_symbol"] = symbol
        t["_tf"] = tf
        fig = make_trade_chart(t, candles, ti + 1, total)
        if fig is None:
            continue
        chart_div = fig.to_html(full_html=False, include_plotlyjs=False, div_id=f"chart_{ti}")
        charts_html_parts.append((ti, chart_div))

    # 构建导航
    nav_items = []
    for ti, _ in charts_html_parts:
        t = trades_sorted[ti]
        pnl = t["pnl_pct"]
        pnl_emoji = "🟢" if pnl >= 5 else "🟡" if pnl >= 0 else "🔴"
        nav_items.append(
            f'<a href="#chart_{ti}" class="nav-link">'
            f'{pnl_emoji} #{ti+1} {t["strategy"][:4]} {t["direction"]} '
            f'{pnl:+.1f}%</a>'
        )

    # 主 HTML
    safe = symbol.replace("-", "_")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>逐笔交易 · {symbol} · {tf}</title>
<script src="https://cdn.plot.ly/plotly-3.1.0.min.js"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; display: flex; }}
#sidebar {{ width: 280px; height: 100vh; overflow-y: auto; background: #0f0f23; border-right: 1px solid #1a1a3e; padding: 16px; position: fixed; }}
#sidebar h2 {{ color: #e94560; font-size: 16px; margin-bottom: 12px; }}
#main {{ margin-left: 280px; padding: 20px; flex: 1; }}
.nav-link {{ display: block; padding: 6px 10px; color: #a0b0c0; text-decoration: none; font-size: 13px; border-radius: 4px; margin-bottom: 2px; font-family: 'SF Mono', monospace; }}
.nav-link:hover {{ background: #1a1a3e; color: #fff; }}
.chart-container {{ margin-bottom: 8px; border: 1px solid #1a1a3e; border-radius: 8px; overflow: hidden; }}
.stats-box {{ position: sticky; top: 0; background: #0f0f23; padding: 12px; margin-bottom: 16px; border-radius: 8px; border: 1px solid #1a3a6e; }}
.stats-box .stat {{ display: inline-block; margin-right: 20px; font-size: 13px; }}
.stats-box .stat .val {{ font-size: 18px; font-weight: 700; }}
.pos {{ color: #26a69a; }}
.neg {{ color: #ef5350; }}
</style>
</head>
<body>
<div id="sidebar">
<div class="stats-box">
  <div class="stat">总交易 <div class="val" style="color:#fff">{total}</div></div>
  <div class="stat">盈利 <div class="val pos">{sum(1 for t in trades_sorted if t['pnl_pct']>=0)}</div></div>
  <div class="stat">亏损 <div class="val neg">{sum(1 for t in trades_sorted if t['pnl_pct']<0)}</div></div>
  <div class="stat">总盈亏 <div class="val {'pos' if sum(t['pnl_amount'] for t in trades_sorted) >=0 else 'neg'}">${sum(t['pnl_amount'] for t in trades_sorted):+,.1f}</div></div>
</div>
<h2>📊 点击跳转</h2>
{"".join(nav_items)}
</div>
<div id="main">
<h1 style="margin-bottom:20px">{symbol} · {tf} — 逐笔交易图表</h1>
<p style="color:#888;margin-bottom:24px">
  显示交易: 前 {top_n} 笔（最佳）+ 后 {bottom_n} 笔（最差）| 共 {len(selected_indices)} 张图表
</p>
"""

    for ti, chart_div in charts_html_parts:
        html += f'<div class="chart-container" id="chart_{ti}">{chart_div}</div>\n'

    html += """
</div>
</body>
</html>"""

    filepath = OUTPUT_DIR / f"trades_detail_{safe}_{tf}.html"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✅ {filepath} ({len(selected_indices)} 张图表)")
    return filepath


def main():
    account = AccountConfig(initial_capital=1000.0)
    tester = StrategyBacktester(account)

    print("生成逐笔交易图表...\n")

    for symbol, tf, source in SYMBOLS_TF_SRC:
        print(f"── {symbol} · {tf} ──")
        candles = load_data(symbol, tf, source)
        if len(candles) < 150:
            print(f"  ⚠ 数据不足")
            continue

        report = tester.run(candles, symbol, tf)

        # 将 TradeRecord 转为 dict
        trades = []
        for t in report.trades:
            trades.append({
                "strategy": t.strategy_type,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "pnl_pct": t.pnl_pct,
                "pnl_amount": t.pnl_amount,
                "max_profit_pct": t.max_profit_pct,
                "max_loss_pct": t.max_loss_pct,
                "holding_bars": t.holding_bars,
                "regime_at_entry": t.regime_at_entry,
                "risk_amount": t.risk_amount,
                "position_size": t.position_size,
            })

        build_trade_pages(symbol, tf, trades, candles)

    # 更新 index.html 增加链接
    print(f"\n✅ 全部完成 → {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
