// ============================================================
// Chart page (primary)
// ============================================================

function ChartPage({ activeSymbol }) {
  const [timeframe, setTimeframe] = React.useState("1day");
  const [logScale, setLogScale] = React.useState(false);
  const [showMA, setShowMA] = React.useState(true);
  const [showVolume, setShowVolume] = React.useState(true);
  const [showRSI, setShowRSI] = React.useState(true);
  const [showMACD, setShowMACD] = React.useState(true);
  const [crosshairIdx, setCrosshairIdx] = React.useState(null);

  const candles = window.MR.CANDLES[activeSymbol];
  const sym = window.MR.SYMBOLS.find((s) => s.symbol === activeSymbol);

  const tfs = ["15min", "1h", "4h", "1day", "1week"];

  // latest stats
  const last = candles[candles.length - 1];
  const prev = candles[candles.length - 2];
  const high52 = Math.max(...candles.map((c) => c.high));
  const low52 = Math.min(...candles.map((c) => c.low));

  return (
    <div className="page">
      <div className="page-inner" style={{ padding: 0 }}>
        {/* Header with title + stats row */}
        <div style={{ padding: "14px 16px 10px", borderBottom: "1px solid var(--line-1)", background: "var(--bg-1)" }}>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 18, flexWrap: "wrap" }}>
            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <span className="mono" style={{ fontSize: 24, fontWeight: 700, color: "var(--fg-0)", letterSpacing: "-0.01em" }}>{sym.symbol}</span>
                <span style={{ color: "var(--fg-2)", fontSize: 13 }}>{sym.name}</span>
                <span className="chip">{sym.exchange}</span>
                <span className="chip">{sym.asset_type.toUpperCase()}</span>
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginTop: 4 }}>
                <span className="mono" style={{ fontSize: 30, fontWeight: 600, color: "var(--fg-0)", letterSpacing: "-0.02em" }}>
                  {sym.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                </span>
                <span className={"mono " + (sym.ch >= 0 ? "up" : "down")} style={{ fontSize: 14, fontWeight: 600 }}>
                  {sym.ch >= 0 ? "▲" : "▼"} {sym.ch >= 0 ? "+" : ""}{sym.ch.toFixed(2)}
                </span>
                <span className={"chip " + (sym.ch >= 0 ? "up" : "down")}>
                  {sym.chp >= 0 ? "+" : ""}{sym.chp.toFixed(2)}%
                </span>
                <span style={{ color: "var(--fg-2)", fontSize: 11, fontFamily: "var(--font-mono)" }}>USD · 实时</span>
              </div>
            </div>

            <div style={{ flex: 1 }} />

            <div style={{ display: "flex", gap: 18, alignItems: "center" }}>
              {[
                { l: "开盘", v: last.open.toFixed(2) },
                { l: "最高", v: last.high.toFixed(2) },
                { l: "最低", v: last.low.toFixed(2) },
                { l: "前收", v: prev.close.toFixed(2) },
                { l: "52W高", v: high52.toFixed(2) },
                { l: "52W低", v: low52.toFixed(2) },
                { l: "成交量", v: (last.volume / 1e6).toFixed(2) + "M" },
              ].map((x, i) => (
                <div key={i} className="stat">
                  <div className="stat-label">{x.l}</div>
                  <div className="stat-value mono" style={{ fontSize: 14 }}>{x.v}</div>
                </div>
              ))}
            </div>

            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn"><I.Star /> 加入自选</button>
              <button className="btn primary"><I.Replay /> 进入复盘</button>
            </div>
          </div>
        </div>

        {/* Toolbar */}
        <div style={{
          padding: "8px 16px", display: "flex", alignItems: "center", gap: 10,
          borderBottom: "1px solid var(--line-1)", background: "var(--bg-1)",
        }}>
          <div className="tweak-segment">
            {tfs.map((t) => (
              <button key={t} className={timeframe === t ? "on" : ""} onClick={() => setTimeframe(t)}
                style={{ minWidth: 50, fontFamily: "var(--font-mono)", letterSpacing: ".04em", textTransform: "uppercase" }}>
                {t}
              </button>
            ))}
          </div>
          <div className="topbar-divider" />
          <button className={"btn small" + (showMA ? "" : " ghost")} onClick={() => setShowMA((v) => !v)} style={{
            background: showMA ? "var(--bg-3)" : "transparent",
            borderColor: showMA ? "var(--line-2)" : "var(--line-1)",
          }}>
            <span style={{ color: "var(--ma20)" }}>●</span> MA20
            <span style={{ color: "var(--ma60)" }}>●</span> MA60
          </button>
          <button className={"btn small" + (showVolume ? "" : " ghost")} onClick={() => setShowVolume((v) => !v)}>成交量</button>
          <button className={"btn small" + (showRSI ? "" : " ghost")} onClick={() => setShowRSI((v) => !v)}>RSI</button>
          <button className={"btn small" + (showMACD ? "" : " ghost")} onClick={() => setShowMACD((v) => !v)}>MACD</button>
          <div style={{ flex: 1 }} />
          <button className="btn small" onClick={() => setLogScale((v) => !v)}>
            {logScale ? <I.Check /> : null} 对数刻度
          </button>
          <span style={{ color: "var(--fg-2)", fontSize: 11, fontFamily: "var(--font-mono)" }}>
            {candles.length} 根K线
          </span>
        </div>

        {/* Chart */}
        <div style={{ padding: "4px 0" }}>
          <CandlestickChart
            candles={candles}
            showMA={showMA} showVolume={showVolume} showRSI={showRSI} showMACD={showMACD}
            logScale={logScale}
            height={620}
            crosshairIdx={crosshairIdx}
            onCrosshair={setCrosshairIdx}
          />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ChartPage });
