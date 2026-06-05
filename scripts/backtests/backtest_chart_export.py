"""
每品种导出一张 plotly 交互式 HTML：
- 底层 K 线 + 趋势 MA120 + 当前选中 MA
- 每笔交易：入场 ▲、出场 ▼（按出场原因染色）、入→出虚线连线（盈绿亏红）
- 下拉框切换 (策略, MA) 组合
"""

import os

try:
    import plotly.graph_objects as go
except ImportError as _:
    raise SystemExit("缺少 plotly：请 pip install plotly")

COST = 0.0015  # 与 backtest_ma_strategies.py 的 FEE_RATE + SLIPPAGE 保持一致

REASON_COLOR = {
    "止损":   "#ef5350",
    "保本":   "#ff9800",
    "均线":   "#42a5f5",
    "强制平仓": "#9e9e9e",
}


def _fmt_date(d):
    return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]


def _build_trade_traces(trades, dates, closes, label):
    """每个 (策略, MA) 生成 4 条 trace：入场、出场、盈利连线、亏损连线。"""
    if not trades:
        return [
            go.Scatter(x=[], y=[], mode="markers", name=f"{label} 入场",     visible=False),
            go.Scatter(x=[], y=[], mode="markers", name=f"{label} 出场",     visible=False),
            go.Scatter(x=[], y=[], mode="lines",   name=f"{label} 盈利连线", visible=False),
            go.Scatter(x=[], y=[], mode="lines",   name=f"{label} 亏损连线", visible=False),
        ]

    e_x, e_y, e_text = [], [], []
    x_x, x_y, x_text, x_color = [], [], [], []
    win_x, win_y, loss_x, loss_y = [], [], [], []

    for ei, xi, ret, reason, sl, rr, max_ret in trades:
        if reason in ("止损", "保本"):
            exit_px = sl * (1 - COST)
        else:
            exit_px = closes[xi] * (1 - COST)
        entry_px = closes[ei] * (1 + COST)

        e_x.append(dates[ei]); e_y.append(closes[ei])
        e_text.append(
            f"<b>入场</b> · {label}<br>"
            f"日期 {_fmt_date(dates[ei])}<br>"
            f"收盘 {closes[ei]:.4f}<br>"
            f"有效价 {entry_px:.4f}<br>"
            f"止损 {sl:.4f}"
        )

        x_x.append(dates[xi]); x_y.append(exit_px)
        x_color.append(REASON_COLOR.get(reason, "#999"))
        rr_str = f"{rr:.2f}R" if rr is not None else "-"
        pl = "盈" if ret > 0 else "亏"
        x_text.append(
            f"<b>出场 [{reason}] · {pl}</b><br>"
            f"日期 {_fmt_date(dates[xi])}<br>"
            f"价格 {exit_px:.4f}<br>"
            f"收益 {ret*100:.2f}%<br>"
            f"最大浮盈 {max_ret*100:.2f}%<br>"
            f"盈亏比 {rr_str}"
        )

        if ret > 0:
            win_x.extend([dates[ei], dates[xi], None])
            win_y.extend([closes[ei], exit_px, None])
        else:
            loss_x.extend([dates[ei], dates[xi], None])
            loss_y.extend([closes[ei], exit_px, None])

    return [
        go.Scatter(
            x=e_x, y=e_y, mode="markers",
            marker=dict(symbol="triangle-up", size=11, color="#26a69a",
                        line=dict(width=1, color="#0d4d3f")),
            name=f"{label} 入场",
            hovertext=e_text, hoverinfo="text", visible=False,
        ),
        go.Scatter(
            x=x_x, y=x_y, mode="markers",
            marker=dict(symbol="triangle-down", size=11, color=x_color,
                        line=dict(width=1, color="#222")),
            name=f"{label} 出场",
            hovertext=x_text, hoverinfo="text", visible=False,
        ),
        go.Scatter(
            x=win_x, y=win_y, mode="lines",
            line=dict(color="rgba(38,166,154,0.55)", width=1.2, dash="dot"),
            name=f"{label} 盈利连线", hoverinfo="skip", visible=False,
        ),
        go.Scatter(
            x=loss_x, y=loss_y, mode="lines",
            line=dict(color="rgba(239,83,80,0.55)", width=1.2, dash="dot"),
            name=f"{label} 亏损连线", hoverinfo="skip", visible=False,
        ),
    ]


def export_symbol_html(sym, name, ohlc, breakout, pullback, out_path):
    dates     = ohlc["dates"]
    opens     = ohlc["opens"]
    highs     = ohlc["highs"]
    lows      = ohlc["lows"]
    closes    = ohlc["closes"]
    sma_by_n  = ohlc["sma_by_n"]
    trend_sma = ohlc["trend_sma"]
    ma_periods = sorted(sma_by_n.keys())

    fig = go.Figure()

    # 0: 蜡烛图
    fig.add_trace(go.Candlestick(
        x=dates, open=opens, high=highs, low=lows, close=closes,
        name=sym,
        increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        showlegend=False,
    ))
    # 1: 趋势 MA120
    fig.add_trace(go.Scatter(
        x=dates, y=trend_sma, mode="lines",
        line=dict(color="#888", width=1, dash="dash"),
        name="趋势 MA120", hoverinfo="skip",
    ))
    # 2..2+N-1: 各 MA 线（仅选中显示）
    for n in ma_periods:
        fig.add_trace(go.Scatter(
            x=dates, y=sma_by_n[n], mode="lines",
            line=dict(color="#5aa7ff", width=1.6),
            name=f"MA{n}", hoverinfo="skip", visible=False,
        ))

    # 交易 trace 组：(策略 × MA) × 4 条
    strategies = [
        ("A突破", {r["n"]: r for r in (breakout or [])}),
        ("B回调", {r["n"]: r for r in (pullback or [])}),
    ]
    stats_map = {}
    for s_idx, (s_label, by_n) in enumerate(strategies):
        for n in ma_periods:
            r = by_n.get(n)
            trades = r["trade_log"] if r else []
            for t in _build_trade_traces(trades, dates, closes, f"{s_label}/MA{n}"):
                fig.add_trace(t)
            stats_map[(s_idx, n)] = r

    n_ma_lines = len(ma_periods)
    n_groups   = len(strategies) * n_ma_lines

    # ── 下拉按钮 ──
    buttons = []
    for s_idx, (s_label, _by_n) in enumerate(strategies):
        for ni, n in enumerate(ma_periods):
            # 可见性：[candle, trend, ...MA lines..., ...group traces × 4...]
            v = [True, True]
            for nj in range(n_ma_lines):
                v.append(nj == ni)
            sel_group = s_idx * n_ma_lines + ni
            for g in range(n_groups):
                on = (g == sel_group)
                v.extend([on, on, on, on])

            r = stats_map[(s_idx, n)]
            if r:
                rr = f"{r['avg_rr']:.2f}R" if r["avg_rr"] else "-"
                title = (
                    f"{sym} ({name})  |  <b>{s_label}</b> · MA={n}  |  "
                    f"收益 {r['total_return']:.1f}%  ·  交易 {r['n_trades']} 笔  ·  "
                    f"胜率 {r['win_rate']:.1f}%  ·  回撤 {r['max_drawdown']:.1f}%  ·  "
                    f"盈亏比 {rr}"
                )
            else:
                title = f"{sym} ({name})  |  <b>{s_label}</b> · MA={n}  |  无交易"

            buttons.append(dict(
                label=f"{s_label} / MA{n}",
                method="update",
                args=[{"visible": v}, {"title.text": title}],
            ))

    # 默认显示第一个组合
    default = buttons[0]
    for i, vis in enumerate(default["args"][0]["visible"]):
        fig.data[i].visible = vis
    init_title = default["args"][1]["title.text"]

    fig.update_layout(
        title=dict(text=init_title, x=0.01, xanchor="left", font=dict(size=13)),
        xaxis=dict(rangeslider=dict(visible=False), type="date"),
        yaxis=dict(autorange=True, fixedrange=False),
        template="plotly_white",
        height=780,
        margin=dict(l=50, r=20, t=110, b=40),
        hovermode="closest",
        updatemenus=[dict(
            buttons=buttons, direction="down",
            x=0.0, y=1.12, xanchor="left", yanchor="bottom",
            showactive=True, bgcolor="#f5f7fa", font=dict(size=11),
            pad=dict(l=4, r=4, t=4, b=4),
        )],
        legend=dict(orientation="h", y=-0.06, font=dict(size=10)),
    )

    fig.write_html(out_path, include_plotlyjs="cdn")


def write_index(symbols_map, out_dir):
    rows = []
    for code, name in symbols_map.items():
        safe = code.replace("/", "-")
        rows.append(f'<li><a href="{safe}.html">{code} — {name}</a></li>')
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>均线策略交易图表</title><style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:720px;"
        "margin:40px auto;padding:0 20px;color:#222;}"
        "h1{font-size:20px;border-bottom:1px solid #eee;padding-bottom:8px;}"
        "ul{list-style:none;padding:0;}"
        "li{padding:8px 0;border-bottom:1px solid #f3f3f3;}"
        "a{color:#1f6feb;text-decoration:none;font-size:14px;}"
        "a:hover{text-decoration:underline;}"
        "p{color:#666;font-size:13px;}"
        "</style></head><body>"
        "<h1>均线策略 — 每品种交易图表</h1>"
        "<p>点击品种查看交互式 K 线 + 每笔交易标注。图内下拉框可切换 (策略 × MA) 组合。</p>"
        f"<ul>{''.join(rows)}</ul></body></html>"
    )
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
