"""
交易可视化 —— 交互式 HTML 图表。

每品种日线生成一张大图:
  - 主面板: K线 + MA20/MA60 + 入场箭头 + 出场标记
  - 副面板1: Regime 热力图
  - 副面板2: 权益曲线

同时导出详细交易日志 JSON 供前端消费。
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd

from app.providers.local_crypto import LocalCryptoProvider
from app.providers.yahoo_finance import YahooFinanceProvider
from app.core.strategy_backtest_engine import StrategyBacktester, AccountConfig, TradeRecord
from app.core.regime_classifier import precompute_regime_arrays

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "reports"
OUTPUT_DIR.mkdir(exist_ok=True)

REGIME_COLORS = {
    -1: "#888888",
    0: "#90EE90",   # 震荡低波 — 浅绿
    1: "#FFB347",   # 震荡高波 — 橙色
    2: "#228B22",   # 弱趋势低波 — 深绿
    3: "#FF8C00",   # 弱趋势高波 — 暗橙
    4: "#006400",   # 强趋势低波 — 墨绿
    5: "#FF4500",   # 强趋势高波 — 红橙
}

REGIME_LABELS = {
    -1: "数据不足", 0: "震荡低波", 1: "震荡高波",
    2: "弱趋势低波", 3: "弱趋势高波", 4: "强趋势低波", 5: "强趋势高波",
}

STRATEGY_COLORS = {
    "trend": "#1f77b4",     # 蓝
    "momentum": "#ff7f0e",  # 橙
    "reversal": "#2ca02c",  # 绿
}

SYMBOLS_TF_SRC = [
    ("BTC-USD", "1day", "crypto"),
    ("ETH-USD", "1day", "crypto"),
    ("AAPL", "1day", "stock"),
    ("GOOGL", "1day", "stock"),
]


def load_data(symbol, tf, source):
    if source == "crypto":
        candles = asyncio.run(LocalCryptoProvider().get_full_history(symbol, tf))
    else:
        candles = asyncio.run(YahooFinanceProvider().get_time_series(symbol, tf, outputsize=5000))
    return candles


def make_chart(symbol, tf, candles, trades, regimes, equity_curve):
    """为单个品种生成交互式 Plotly 图表。"""
    n = len(candles)
    # 取最近 500 根 K 线以保持图表可读
    if n > 500:
        start_idx = n - 500
        start_ts = candles[start_idx]["time"]
        candles = candles[start_idx:]
        regimes = regimes[start_idx:]
        equity_curve = equity_curve[start_idx:]
        # 过滤交易（按时间）
        trades = [t for t in trades if t.entry_time >= start_ts]

    times = [datetime.fromtimestamp(c["time"], tz=timezone.utc) for c in candles]
    closes = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    # MA 计算
    from app.core.indicators import _sma
    ma20 = _sma([float(c) for c in closes], 20)
    ma60 = _sma([float(c) for c in closes], 60)

    # ── 子图布局 ──
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.55, 0.15, 0.30],
        subplot_titles=(f"{symbol} · {tf}", "市场状态 (Regime)", "权益曲线 ($)"),
    )

    # ── Panel 1: K 线 ──
    fig.add_trace(go.Candlestick(
        x=times, open=opens, high=highs, low=lows, close=closes,
        name="K线",
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
    ), row=1, col=1)

    # MA 线
    ma20_vals = [ma20[i] if i < len(ma20) else None for i in range(len(times))]
    ma60_vals = [ma60[i] if i < len(ma60) else None for i in range(len(times))]
    fig.add_trace(go.Scatter(x=times, y=ma20_vals, name="MA20",
                             line=dict(color="#FFD700", width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=times, y=ma60_vals, name="MA60",
                             line=dict(color="#FF6B6B", width=1)), row=1, col=1)

    # ── Panel 1: 入场/出场标记 ──
    for stype, color in STRATEGY_COLORS.items():
        st_trades = [t for t in trades if t.strategy_type == stype]
        if not st_trades:
            continue

        # 入场点
        entry_indices = []
        entry_prices = []
        entry_texts = []
        for t in st_trades:
            ei = _find_idx(candles, t.entry_time)
            if 0 <= ei < len(times):
                entry_indices.append(ei)
                entry_prices.append(t.entry_price)
                entry_texts.append(f"{stype}<br>{t.direction}<br>入场:{t.entry_price:.2f}")

        if entry_indices:
            entry_times = [times[i] for i in entry_indices]
            fig.add_trace(go.Scatter(
                x=entry_times, y=entry_prices,
                mode="markers",
                marker=dict(symbol="triangle-up" if t.direction == "long" else "triangle-down",
                           size=12, color=color, line=dict(width=1, color="white")),
                name=f"{stype} 入场",
                text=entry_texts, hoverinfo="text",
                showlegend=True,
            ), row=1, col=1)

        # 出场点 — 按盈亏分色
        win_trades = [t for t in st_trades if t.pnl_pct >= 0]
        loss_trades = [t for t in st_trades if t.pnl_pct < 0]

        for subset, label_suffix, exit_color in [
            (win_trades, "盈", "#00ff00"),
            (loss_trades, "亏", "#ff0000"),
        ]:
            exit_data = []
            for t in subset:
                ei = _find_idx(candles, t.exit_time)
                if 0 <= ei < len(times):
                    exit_data.append((ei, t.exit_price, t.exit_reason, t.pnl_pct))
            if exit_data:
                exit_times = [times[d[0]] for d in exit_data]
                exit_prices = [d[1] for d in exit_data]
                exit_texts = [f"{stype} {label_suffix}<br>原因:{d[2]}<br>盈亏:{d[3]:.2f}%" for d in exit_data]
                fig.add_trace(go.Scatter(
                    x=exit_times, y=exit_prices,
                    mode="markers",
                    marker=dict(symbol="diamond", size=8, color=exit_color,
                               line=dict(width=1, color="white")),
                    name=f"{stype} 出场({label_suffix})",
                    text=exit_texts, hoverinfo="text",
                    showlegend=True,
                ), row=1, col=1)

    # ── Panel 2: Regime 热力图 ──
    regime_colors_bar = [REGIME_COLORS.get(r, "#888") for r in regimes[:len(times)]]
    fig.add_trace(go.Bar(
        x=times, y=[1]*len(times),
        marker=dict(color=regime_colors_bar),
        text=[REGIME_LABELS.get(r, "?") for r in regimes[:len(times)]],
        hoverinfo="text",
        showlegend=False,
        width=1000*3600*24*0.9,  # ~daily bar width
    ), row=2, col=1)

    # ── Panel 3: 权益曲线 ──
    eq_times = [times[min(i, len(times)-1)] for i in range(len(equity_curve))]
    eq_vals = [e["value"] for e in equity_curve[:len(times)]]
    fig.add_trace(go.Scatter(
        x=eq_times, y=eq_vals,
        name="权益",
        line=dict(color="#2196F3", width=1.5),
        fill="tozeroy",
        fillcolor="rgba(33,150,243,0.1)",
    ), row=3, col=1)
    # 初始资金线
    fig.add_hline(y=1000, line_dash="dash", line_color="gray", row=3, col=1)

    # ── 布局 ──
    fig.update_layout(
        title=dict(text=f"{symbol} · {tf} — 策略组合交易标注", font=dict(size=18)),
        xaxis_rangeslider_visible=False,
        height=1000,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        template="plotly_dark",
    )
    fig.update_yaxes(title_text="价格", row=1, col=1)
    fig.update_yaxes(title_text="Regime", showticklabels=False, row=2, col=1)
    fig.update_yaxes(title_text="权益 ($)", row=3, col=1)

    return fig


def _find_idx(candles, timestamp):
    """在 candles 中二分查找 timestamp 对应的 index。"""
    times = [c["time"] for c in candles]
    import bisect
    idx = bisect.bisect_left(times, timestamp)
    if idx < len(times) and times[idx] == timestamp:
        return idx
    return -1


def export_trades_json(symbol, tf, trades, candles):
    """导出交易日志到 JSON（前端可消费）。"""
    data = {
        "symbol": symbol,
        "timeframe": tf,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_trades": len(trades),
        "trades": [],
    }
    for t in trades:
        entry_idx = _find_idx(candles, t.entry_time)
        exit_idx = _find_idx(candles, t.exit_time) if t.exit_time else -1
        entry_c = candles[entry_idx] if 0 <= entry_idx < len(candles) else {}
        exit_c = candles[exit_idx] if 0 <= exit_idx < len(candles) else {}
        data["trades"].append({
            "strategy": t.strategy_type,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "entry_price": t.entry_price,
            "entry_candle": {
                "time": entry_c.get("time"),
                "open": entry_c.get("open"),
                "high": entry_c.get("high"),
                "low": entry_c.get("low"),
                "close": entry_c.get("close"),
            },
            "exit_time": t.exit_time,
            "exit_price": t.exit_price,
            "exit_candle": {
                "time": exit_c.get("time"),
                "open": exit_c.get("open"),
                "high": exit_c.get("high"),
                "low": exit_c.get("low"),
                "close": exit_c.get("close"),
            },
            "exit_reason": t.exit_reason,
            "pnl_pct": t.pnl_pct,
            "pnl_amount": t.pnl_amount,
            "max_profit_pct": t.max_profit_pct,
            "max_loss_pct": t.max_loss_pct,
            "holding_bars": t.holding_bars,
            "regime_at_entry": t.regime_at_entry,
            "regime_label": REGIME_LABELS.get(t.regime_at_entry, "未知"),
            "risk_amount": t.risk_amount,
            "position_size": t.position_size,
        })

    filepath = OUTPUT_DIR / f"trades_{symbol.replace('-', '_')}_{tf}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  📄 交易日志: {filepath} ({len(trades)}笔)")


def main():
    account = AccountConfig(initial_capital=1000.0)
    tester = StrategyBacktester(account)

    print("生成交易可视化...\n")

    for symbol, tf, source in SYMBOLS_TF_SRC:
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

        # 运行回测
        report = tester.run(candles, symbol, tf)
        elapsed = time.time() - t0

        # 预计算 regime
        regime_ids, _, _ = precompute_regime_arrays(candles)

        print(f"  交易: {report.total_trades}笔 | 收益: {report.total_return_pct:.1f}% | {elapsed:.1f}s")

        # 导出 JSON
        export_trades_json(symbol, tf, report.trades, candles)

        # 生成 HTML 图表
        fig = make_chart(symbol, tf, candles, report.trades, regime_ids, report.equity_curve)
        html_path = OUTPUT_DIR / f"chart_{symbol.replace('-', '_')}_{tf}.html"
        fig.write_html(str(html_path))
        print(f"  📊 图表: {html_path}")

    print(f"\n✅ 全部导出到 {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
