// ============================================================
// Backtest page — signature moment
// Left: strategy config + metrics
// Right: equity curve, trades table
// ============================================================

function BacktestPage({ activeSymbol }) {
  const [symbol, setSymbol] = React.useState(activeSymbol === "^GSPC" ? "NVDA" : activeSymbol);
  const [strategy, setStrategy] = React.useState("ma_cross");
  const [timeframe, setTimeframe] = React.useState("1day");
  const [initialCapital, setInitialCapital] = React.useState(10000);
  const [fast, setFast] = React.useState(10);
  const [slow, setSlow] = React.useState(30);
  const [oversold, setOversold] = React.useState(30);
  const [overbought, setOverbought] = React.useState(70);

  const result = React.useMemo(() => window.MR.buildBacktest(symbol, strategy), [symbol, strategy]);
  const candles = window.MR.CANDLES[symbol];
  const sym = window.MR.SYMBOLS.find((s) => s.symbol === symbol);

  const [selectedTrade, setSelectedTrade] = React.useState(null);

  const fmt = (n, d = 2) => n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtDate = (t) => {
    const d = new Date(t * 1000);
    return d.getFullYear() + "-" + (d.getMonth() + 1).toString().padStart(2, "0") + "-" + d.getDate().toString().padStart(2, "0");
  };

  // Trade markers on candle chart
  const markers = result.trades.flatMap((t) => [
    { time: t.entry_time, price: t.entry_price, type: "buy" },
    { time: t.exit_time, price: t.exit_price, type: "sell" },
  ]);

  const metrics = [
    { label: "总收益率", value: (result.total_return_pct >= 0 ? "+" : "") + fmt(result.total_return_pct) + "%",
      color: result.total_return_pct >= 0 ? "var(--up)" : "var(--down)", big: true },
    { label: "年化收益", value: (result.annualized_return_pct >= 0 ? "+" : "") + fmt(result.annualized_return_pct) + "%",
      color: result.annualized_return_pct >= 0 ? "var(--up)" : "var(--down)" },
    { label: "最大回撤", value: "-" + fmt(result.max_drawdown_pct) + "%", color: "var(--down)" },
    { label: "夏普比率", value: fmt(result.sharpe_ratio, 2), color: result.sharpe_ratio >= 1 ? "var(--up)" : "var(--fg-0)" },
    { label: "胜率", value: fmt(result.win_rate_pct, 1) + "%", color: "var(--fg-0)" },
    { label: "交易笔数", value: result.total_trades, color: "var(--fg-0)" },
    { label: "盈/亏笔数", value: `${result.winning_trades} / ${result.losing_trades}`, color: "var(--fg-0)" },
    { label: "Buy & Hold", value: (result.buy_hold_return_pct >= 0 ? "+" : "") + fmt(result.buy_hold_return_pct) + "%",
      color: "var(--fg-1)" },
  ];

  const alpha = result.total_return_pct - result.buy_hold_return_pct;

  return (
    <div className="page" style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 0, overflow: "hidden", height: "100%" }}>
      {/* Left config */}
      <div style={{ borderRight: "1px solid var(--line-1)", background: "var(--bg-1)", overflow: "auto", display: "flex", flexDirection: "column" }}>
        <div className="panel-header" style={{ border: 0, borderBottom: "1px solid var(--line-1)" }}>
          <span>策略配置</span>
        </div>
        <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="tweak-row">
            <label>标的</label>
            <select className="input mono" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {window.MR.SYMBOLS.map((s) => <option key={s.symbol} value={s.symbol}>{s.symbol} — {s.name}</option>)}
            </select>
          </div>

          <div className="tweak-row">
            <label>策略类型</label>
            <div className="tweak-segment">
              <button className={strategy === "ma_cross" ? "on" : ""} onClick={() => setStrategy("ma_cross")}>MA 双均线</button>
              <button className={strategy === "rsi" ? "on" : ""} onClick={() => setStrategy("rsi")}>RSI</button>
              <button className={strategy === "macd" ? "on" : ""} onClick={() => setStrategy("macd")}>MACD</button>
            </div>
          </div>

          <div className="tweak-row">
            <label>周期</label>
            <div className="tweak-segment">
              {["15min", "1h", "4h", "1day", "1week"].map((t) => (
                <button key={t} className={timeframe === t ? "on" : ""} onClick={() => setTimeframe(t)} style={{ fontFamily: "var(--font-mono)" }}>{t}</button>
              ))}
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div className="tweak-row">
              <label>起始日期</label>
              <input className="input mono" defaultValue={result.start_date} />
            </div>
            <div className="tweak-row">
              <label>结束日期</label>
              <input className="input mono" defaultValue={result.end_date} />
            </div>
          </div>

          <div className="tweak-row">
            <label>初始资金 (USD)</label>
            <input className="input mono" value={initialCapital} onChange={(e) => setInitialCapital(Number(e.target.value))} />
          </div>

          {strategy === "ma_cross" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div className="tweak-row">
                <label>快线周期</label>
                <input className="input mono" type="number" value={fast} onChange={(e) => setFast(Number(e.target.value))} />
              </div>
              <div className="tweak-row">
                <label>慢线周期</label>
                <input className="input mono" type="number" value={slow} onChange={(e) => setSlow(Number(e.target.value))} />
              </div>
            </div>
          )}

          {strategy === "rsi" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              <div className="tweak-row">
                <label>超卖阈值</label>
                <input className="input mono" type="number" value={oversold} onChange={(e) => setOversold(Number(e.target.value))} />
              </div>
              <div className="tweak-row">
                <label>超买阈值</label>
                <input className="input mono" type="number" value={overbought} onChange={(e) => setOverbought(Number(e.target.value))} />
              </div>
            </div>
          )}

          <button className="btn primary" style={{ height: 34, justifyContent: "center", fontSize: 13 }}>
            <I.Flask /> 运行回测
          </button>

          <div style={{ padding: 10, background: "var(--bg-2)", borderRadius: 4, border: "1px solid var(--line-1)", fontSize: 11, color: "var(--fg-2)", lineHeight: 1.6 }}>
            <div style={{ color: "var(--fg-0)", fontWeight: 600, marginBottom: 4, fontSize: 11.5 }}>策略说明</div>
            {strategy === "ma_cross" && "快线上穿慢线（金叉）买入，下穿（死叉）卖出。全仓操作，不考虑手续费。"}
            {strategy === "rsi" && "RSI 由低位上穿超卖线买入，由高位下穿超买线卖出。"}
            {strategy === "macd" && "MACD 柱状上穿零轴信号买入，下穿信号卖出。"}
          </div>
        </div>
      </div>

      {/* Right — result */}
      <div style={{ overflow: "auto" }}>
        {/* Header */}
        <div style={{ padding: "14px 18px", borderBottom: "1px solid var(--line-1)", background: "var(--bg-1)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <span className="chip accent">回测结果</span>
            <span className="mono" style={{ fontSize: 16, fontWeight: 700, color: "var(--fg-0)" }}>{symbol}</span>
            <span style={{ color: "var(--fg-2)" }}>{sym.name}</span>
            <span className="chip">{timeframe.toUpperCase()}</span>
            <span className="chip">{strategy === "ma_cross" ? `MA(${fast},${slow})` : strategy === "rsi" ? `RSI(${oversold}/${overbought})` : "MACD(12,26,9)"}</span>
            <span style={{ color: "var(--fg-2)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
              {result.start_date} → {result.end_date}
            </span>
          </div>

          {/* Headline verdict */}
          <div style={{ display: "flex", alignItems: "flex-end", gap: 24, padding: "6px 0 2px" }}>
            <div>
              <div className="stat-label">策略总收益</div>
              <div className="mono" style={{ fontSize: 48, fontWeight: 600, letterSpacing: "-0.02em", color: result.total_return_pct >= 0 ? "var(--up)" : "var(--down)", lineHeight: 1 }}>
                {result.total_return_pct >= 0 ? "+" : ""}{fmt(result.total_return_pct)}%
              </div>
              <div style={{ color: "var(--fg-2)", fontSize: 11, marginTop: 4, fontFamily: "var(--font-mono)" }}>
                ${fmt(result.initial_capital)} → ${fmt(result.initial_capital * (1 + result.total_return_pct / 100))}
              </div>
            </div>
            <div style={{ flex: 1 }} />
            <div>
              <div className="stat-label">相对 Buy & Hold</div>
              <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: alpha >= 0 ? "var(--up)" : "var(--down)" }}>
                {alpha >= 0 ? "+" : ""}{fmt(alpha)}% <span style={{ fontSize: 11, color: "var(--fg-2)", marginLeft: 4 }}>ALPHA</span>
              </div>
            </div>
          </div>
        </div>

        {/* Metrics strip */}
        <div style={{ padding: "12px 18px", borderBottom: "1px solid var(--line-1)", background: "var(--bg-1)",
          display: "grid", gridTemplateColumns: "repeat(8, 1fr)", gap: 0 }}>
          {metrics.map((m, i) => (
            <div key={i} style={{ borderRight: i < metrics.length - 1 ? "1px solid var(--line-1)" : "none", padding: "0 12px" }}>
              <div className="stat-label">{m.label}</div>
              <div className="mono" style={{ fontSize: m.big ? 18 : 16, color: m.color, fontWeight: 600, marginTop: 2 }}>{m.value}</div>
            </div>
          ))}
        </div>

        {/* Equity curve */}
        <div style={{ padding: "16px 18px", background: "var(--bg-0)", borderBottom: "1px solid var(--line-1)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div className="section-title">权益曲线 · EQUITY CURVE</div>
            <div style={{ display: "flex", gap: 14, fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--fg-2)" }}>
              <span><span style={{ display: "inline-block", width: 16, height: 2, background: "var(--accent)", marginRight: 4, verticalAlign: "middle" }} /> 策略</span>
              <span><span style={{ display: "inline-block", width: 16, borderTop: "1.5px dashed var(--fg-3)", marginRight: 4, verticalAlign: "middle" }} /> Buy & Hold</span>
              <span><span style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: "var(--up)", marginRight: 4, verticalAlign: "middle" }} /> 买入</span>
              <span><span style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: "var(--down)", marginRight: 4, verticalAlign: "middle" }} /> 卖出</span>
            </div>
          </div>
          <EquityCurve equity={result.equity_curve} trades={result.trades} bhStart={result.buy_hold_return_pct} height={240} />
        </div>

        {/* Price chart with trade markers */}
        <div style={{ padding: "16px 18px", background: "var(--bg-0)", borderBottom: "1px solid var(--line-1)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div className="section-title">价格 + 交易信号</div>
            <div style={{ fontSize: 11, color: "var(--fg-2)", fontFamily: "var(--font-mono)" }}>{result.trades.length} 次交易</div>
          </div>
          <CandlestickChart
            candles={candles}
            showMA showVolume={false} showRSI={false} showMACD={false}
            height={260}
            markers={markers}
          />
        </div>

        {/* Trades table */}
        <div style={{ padding: "0 0 24px 0" }}>
          <div className="panel-header" style={{ border: 0, padding: "12px 18px" }}>
            <span>交易明细 · {result.trades.length}</span>
            <button className="btn small ghost">导出 CSV</button>
          </div>
          <div style={{ padding: "0 18px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "40px 1fr 1fr 1fr 1fr 1fr 1fr", padding: "6px 10px", borderBottom: "1px solid var(--line-2)", fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: "var(--fg-2)", fontWeight: 600 }}>
              <span>#</span><span>入场日期</span><span>入场价</span><span>出场日期</span><span>出场价</span><span style={{ textAlign: "right" }}>持仓天数</span><span style={{ textAlign: "right" }}>盈亏%</span>
            </div>
            {result.trades.map((t, i) => (
              <div key={i}
                onClick={() => setSelectedTrade(i === selectedTrade ? null : i)}
                style={{
                  display: "grid", gridTemplateColumns: "40px 1fr 1fr 1fr 1fr 1fr 1fr",
                  padding: "8px 10px", borderBottom: "1px solid var(--line-1)",
                  fontFamily: "var(--font-mono)", fontSize: 12, cursor: "pointer",
                  background: selectedTrade === i ? "var(--bg-2)" : "transparent",
                }}
              >
                <span style={{ color: "var(--fg-2)" }}>#{i + 1}</span>
                <span style={{ color: "var(--fg-0)" }}>{fmtDate(t.entry_time)}</span>
                <span style={{ color: "var(--fg-1)" }}>${fmt(t.entry_price)}</span>
                <span style={{ color: "var(--fg-0)" }}>{fmtDate(t.exit_time)}</span>
                <span style={{ color: "var(--fg-1)" }}>${fmt(t.exit_price)}</span>
                <span style={{ textAlign: "right", color: "var(--fg-2)" }}>{Math.round((t.exit_time - t.entry_time) / 86400)}d</span>
                <span style={{ textAlign: "right", color: t.pnl_pct >= 0 ? "var(--up)" : "var(--down)", fontWeight: 600 }}>
                  {t.pnl_pct >= 0 ? "+" : ""}{fmt(t.pnl_pct)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { BacktestPage });
