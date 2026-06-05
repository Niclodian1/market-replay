// ============================================================
// Dashboard, Watchlist, Alerts, Notes pages
// ============================================================

function DashboardPage({ activeSymbol, setRoute }) {
  const watch = window.MR.WATCHLIST.map((w) => ({ ...w, ...window.MR.SYMBOLS.find((s) => s.symbol === w.symbol) }));
  const bt = window.MR.BACKTEST;
  const notes = window.MR.REPLAY_NOTES;
  const alerts = window.MR.ALERTS;

  const gainers = [...watch].sort((a, b) => b.chp - a.chp).slice(0, 4);
  const losers = [...watch].sort((a, b) => a.chp - b.chp).slice(0, 4);

  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 14 }}>
          <div>
            <div className="section-title">总览</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, letterSpacing: "-0.01em" }}>早安 · 市场开盘前</div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn"><I.Plus /> 新增告警</button>
            <button className="btn primary" onClick={() => setRoute("chart")}><I.Chart /> 查看行情</button>
          </div>
        </div>

        {/* KPI row */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 14 }}>
          {[
            { l: "自选列表", v: watch.length, sub: "项", c: "var(--fg-0)" },
            { l: "活跃告警", v: alerts.filter((a) => a.is_active).length, sub: `共 ${alerts.length}`, c: "var(--accent)" },
            { l: "复盘笔记", v: notes.length, sub: "条", c: "var(--fg-0)" },
            { l: "策略年化", v: (bt.annualized_return_pct >= 0 ? "+" : "") + bt.annualized_return_pct.toFixed(1) + "%",
              sub: "NVDA · MA10/30", c: bt.annualized_return_pct >= 0 ? "var(--up)" : "var(--down)" },
          ].map((k, i) => (
            <div key={i} className="panel" style={{ padding: "14px 16px" }}>
              <div className="stat-label">{k.l}</div>
              <div className="mono" style={{ fontSize: 28, color: k.c, fontWeight: 600, marginTop: 4, letterSpacing: "-0.02em" }}>{k.v}</div>
              <div className="mono" style={{ color: "var(--fg-2)", fontSize: 10.5, letterSpacing: ".06em", textTransform: "uppercase", marginTop: 2 }}>{k.sub}</div>
            </div>
          ))}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12 }}>
          {/* Movers */}
          <div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
              <MoverPanel title="领涨" items={gainers} kind="up" />
              <MoverPanel title="领跌" items={losers} kind="down" />
            </div>

            {/* Market heatmap */}
            <div className="panel">
              <div className="panel-header">
                <span>市场热力</span>
                <span style={{ fontFamily: "var(--font-mono)", letterSpacing: 0, textTransform: "none", color: "var(--fg-2)" }}>自选 · 当日涨跌</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 2, padding: 2 }}>
                {watch.map((w) => {
                  const intensity = Math.min(1, Math.abs(w.chp) / 4);
                  const bg = w.chp >= 0
                    ? `rgba(38, 208, 124, ${0.1 + intensity * 0.6})`
                    : `rgba(255, 95, 109, ${0.1 + intensity * 0.6})`;
                  return (
                    <div key={w.symbol} style={{
                      padding: "14px 12px", background: bg, minHeight: 80,
                      display: "flex", flexDirection: "column", justifyContent: "space-between",
                    }}>
                      <div>
                        <div className="mono" style={{ fontWeight: 700, fontSize: 14, color: "var(--fg-0)" }}>{w.symbol}</div>
                        <div style={{ fontSize: 10.5, color: "var(--fg-1)", marginTop: 2 }}>{w.name}</div>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end" }}>
                        <span className="mono" style={{ fontSize: 12, color: "var(--fg-1)" }}>{w.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
                        <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: w.chp >= 0 ? "var(--up)" : "var(--down)" }}>
                          {w.chp >= 0 ? "+" : ""}{w.chp.toFixed(2)}%
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Right — alerts + recent notes */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="panel">
              <div className="panel-header">
                <span>告警</span>
                <button className="btn small ghost" onClick={() => setRoute("alerts")}>全部 →</button>
              </div>
              {alerts.slice(0, 4).map((a) => (
                <div key={a.id} style={{ padding: "10px 12px", borderBottom: "1px solid var(--line-1)", display: "flex", alignItems: "center", gap: 10 }}>
                  <span className={"chip " + (a.is_triggered ? "down" : a.is_active ? "accent" : "")} style={{ minWidth: 52, justifyContent: "center" }}>
                    {a.is_triggered ? "已触发" : a.is_active ? "监听中" : "暂停"}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, color: "var(--fg-0)", fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{a.name}</div>
                    <div className="mono" style={{ fontSize: 10.5, color: "var(--fg-2)" }}>
                      {a.symbol} · {conditionLabel(a.condition_type)} {a.threshold}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div className="panel">
              <div className="panel-header">
                <span>最近笔记</span>
                <button className="btn small ghost" onClick={() => setRoute("notes")}>全部 →</button>
              </div>
              {notes.slice(0, 3).map((n) => (
                <div key={n.id} style={{ padding: "10px 12px", borderBottom: "1px solid var(--line-1)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                    <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>{n.symbol}</span>
                    <span className="chip" style={{ fontSize: 9 }}>{n.timeframe.toUpperCase()}</span>
                    <span style={{ flex: 1 }} />
                    <span className="mono" style={{ color: "var(--fg-2)", fontSize: 10.5 }}>{n.note_time.slice(0, 10)}</span>
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--fg-1)", lineHeight: 1.5, overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                    {n.trend_view}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MoverPanel({ title, items, kind }) {
  return (
    <div className="panel">
      <div className="panel-header">
        <span>{title}</span>
        <span style={{ color: kind === "up" ? "var(--up)" : "var(--down)" }}>{kind === "up" ? <I.ArrowUp /> : <I.ArrowDown />}</span>
      </div>
      {items.map((w) => {
        const spark = window.MR.CANDLES[w.symbol].slice(-30).map((c) => c.close);
        return (
          <div key={w.symbol} style={{ padding: "8px 12px", borderBottom: "1px solid var(--line-1)", display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ flex: 1 }}>
              <div className="mono" style={{ fontSize: 12, color: "var(--fg-0)", fontWeight: 600 }}>{w.symbol}</div>
              <div style={{ fontSize: 10.5, color: "var(--fg-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 140 }}>{w.name}</div>
            </div>
            <Sparkline data={spark} width={60} height={20} color={w.chp >= 0 ? "var(--up)" : "var(--down)"} />
            <div style={{ minWidth: 64, textAlign: "right" }}>
              <div className="mono" style={{ fontSize: 12, color: "var(--fg-1)" }}>{w.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</div>
              <div className="mono" style={{ fontSize: 11, fontWeight: 600, color: w.chp >= 0 ? "var(--up)" : "var(--down)" }}>
                {w.chp >= 0 ? "+" : ""}{w.chp.toFixed(2)}%
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function conditionLabel(t) {
  return ({
    price_above: "价格 ≥", price_below: "价格 ≤",
    rsi_above: "RSI ≥", rsi_below: "RSI ≤",
    ma_cross_up: "均线金叉", ma_cross_down: "均线死叉",
  })[t] || t;
}

// ── Watchlist page ────────────────────────────────────────
function WatchlistPage({ setActiveSymbol, setRoute }) {
  const items = window.MR.WATCHLIST.map((w) => ({ ...w, ...window.MR.SYMBOLS.find((s) => s.symbol === w.symbol) }));
  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <div>
            <div className="section-title">我的自选</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{items.length} 个标的 · 实时监听中</div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn"><I.Filter /> 筛选</button>
            <button className="btn primary"><I.Plus /> 添加标的</button>
          </div>
        </div>
        <div className="panel" style={{ overflow: "hidden" }}>
          <div style={{
            display: "grid", gridTemplateColumns: "60px 1.5fr 0.8fr 1fr 1fr 1fr 0.8fr 1.2fr 100px",
            padding: "10px 14px", borderBottom: "1px solid var(--line-2)",
            fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: "var(--fg-2)", fontWeight: 600,
          }}>
            <span>#</span><span>标的</span><span>类型</span>
            <span style={{ textAlign: "right" }}>最新价</span>
            <span style={{ textAlign: "right" }}>涨跌</span>
            <span style={{ textAlign: "right" }}>涨跌幅</span>
            <span>走势 30d</span><span>添加时间</span><span></span>
          </div>
          {items.map((w, i) => {
            const spark = window.MR.CANDLES[w.symbol].slice(-30).map((c) => c.close);
            return (
              <div key={w.symbol}
                onClick={() => { setActiveSymbol(w.symbol); setRoute("chart"); }}
                style={{
                  display: "grid", gridTemplateColumns: "60px 1.5fr 0.8fr 1fr 1fr 1fr 0.8fr 1.2fr 100px",
                  alignItems: "center", padding: "10px 14px", borderBottom: "1px solid var(--line-1)",
                  cursor: "pointer", transition: "background .1s",
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = "var(--bg-2)"}
                onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
              >
                <span className="mono" style={{ color: "var(--fg-2)" }}>{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <div className="mono" style={{ fontWeight: 600, color: "var(--fg-0)" }}>{w.symbol}</div>
                  <div style={{ fontSize: 10.5, color: "var(--fg-2)" }}>{w.name}</div>
                </div>
                <span className="chip">{w.asset_type.toUpperCase()}</span>
                <span className="mono" style={{ textAlign: "right", color: "var(--fg-0)", fontSize: 13 }}>
                  {w.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                </span>
                <span className={"mono " + (w.ch >= 0 ? "up" : "down")} style={{ textAlign: "right" }}>
                  {w.ch >= 0 ? "+" : ""}{w.ch.toFixed(2)}
                </span>
                <span style={{ textAlign: "right" }}>
                  <span className={"chip " + (w.chp >= 0 ? "up" : "down")}>{w.chp >= 0 ? "+" : ""}{w.chp.toFixed(2)}%</span>
                </span>
                <span><Sparkline data={spark} width={70} height={22} color={w.chp >= 0 ? "var(--up)" : "var(--down)"} /></span>
                <span className="mono" style={{ color: "var(--fg-2)", fontSize: 11 }}>{w.created_at}</span>
                <span style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                  <button className="btn icon small" onClick={(e) => e.stopPropagation()}><I.Bell /></button>
                  <button className="btn icon small" onClick={(e) => e.stopPropagation()}><I.Trash /></button>
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Alerts page ───────────────────────────────────────────
function AlertsPage() {
  const [alerts, setAlerts] = React.useState(window.MR.ALERTS);
  const toggle = (id) => setAlerts((a) => a.map((x) => x.id === id ? { ...x, is_active: !x.is_active } : x));
  return (
    <div className="page">
      <div className="page-inner">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <div>
            <div className="section-title">告警管理</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
              {alerts.filter((a) => a.is_active).length} 活跃 · {alerts.filter((a) => a.is_triggered).length} 已触发
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn"><I.Discord /> 测试 Webhook</button>
            <button className="btn primary"><I.Plus /> 新建告警</button>
          </div>
        </div>

        <div className="panel">
          <div style={{
            display: "grid", gridTemplateColumns: "40px 1.4fr 1fr 1.6fr 0.8fr 1fr 1.3fr 120px",
            padding: "10px 14px", borderBottom: "1px solid var(--line-2)",
            fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: "var(--fg-2)", fontWeight: 600,
          }}>
            <span>状态</span><span>名称</span><span>标的</span><span>条件</span><span>周期</span><span>Webhook</span><span>创建/触发</span><span></span>
          </div>
          {alerts.map((a) => (
            <div key={a.id} style={{
              display: "grid", gridTemplateColumns: "40px 1.4fr 1fr 1.6fr 0.8fr 1fr 1.3fr 120px",
              alignItems: "center", padding: "12px 14px", borderBottom: "1px solid var(--line-1)",
            }}>
              <span>
                <span style={{
                  display: "inline-block", width: 8, height: 8, borderRadius: "50%",
                  background: a.is_triggered ? "var(--down)" : a.is_active ? "var(--up)" : "var(--fg-3)",
                  boxShadow: a.is_triggered ? "0 0 8px var(--down)" : a.is_active ? "0 0 6px var(--up)" : "none",
                }} />
              </span>
              <span style={{ fontWeight: 500, color: "var(--fg-0)", fontSize: 12.5 }}>{a.name}</span>
              <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>{a.symbol}</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--fg-1)" }}>
                {conditionLabel(a.condition_type)} <span style={{ color: "var(--accent)" }}>{a.threshold}</span>
              </span>
              <span className="chip">{a.timeframe.toUpperCase()}</span>
              <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--fg-2)", fontFamily: "var(--font-mono)" }}>
                <I.Discord /> …{a.discord_webhook_url.slice(-12)}
              </span>
              <span className="mono" style={{ fontSize: 11, color: "var(--fg-2)" }}>
                {a.is_triggered
                  ? <>触发 <span className="down">{a.triggered_at.slice(0, 10)}</span></>
                  : <>创建 {a.created_at.slice(0, 10)}</>
                }
              </span>
              <span style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                <label style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
                  <input type="checkbox" checked={a.is_active} onChange={() => toggle(a.id)} style={{ display: "none" }} />
                  <span style={{
                    width: 32, height: 18, borderRadius: 999, background: a.is_active ? "var(--up)" : "var(--bg-3)",
                    position: "relative", transition: "background .15s", border: "1px solid var(--line-1)",
                  }}>
                    <span style={{
                      position: "absolute", top: 1, left: a.is_active ? 15 : 1, width: 14, height: 14,
                      borderRadius: "50%", background: "#fff", transition: "left .15s",
                      boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
                    }} />
                  </span>
                </label>
                <button className="btn icon small"><I.Trash /></button>
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Notes page ───────────────────────────────────────────
function NotesPage({ setActiveSymbol, setRoute }) {
  const [filter, setFilter] = React.useState("all");
  const [selected, setSelected] = React.useState(window.MR.REPLAY_NOTES[0].id);
  const notes = filter === "all" ? window.MR.REPLAY_NOTES : window.MR.REPLAY_NOTES.filter((n) => n.symbol === filter);
  const active = notes.find((n) => n.id === selected) || notes[0];
  const symbols = [...new Set(window.MR.REPLAY_NOTES.map((n) => n.symbol))];

  return (
    <div className="page" style={{ display: "grid", gridTemplateColumns: "340px 1fr", overflow: "hidden", height: "100%" }}>
      <div style={{ borderRight: "1px solid var(--line-1)", display: "flex", flexDirection: "column", background: "var(--bg-1)" }}>
        <div style={{ padding: "14px 14px 10px" }}>
          <div className="section-title">复盘笔记</div>
          <div style={{ fontSize: 18, fontWeight: 700, marginTop: 4 }}>{window.MR.REPLAY_NOTES.length} 条</div>
        </div>
        <div style={{ display: "flex", gap: 4, padding: "4px 10px 10px", borderBottom: "1px solid var(--line-1)", flexWrap: "wrap" }}>
          <button className={"btn small" + (filter === "all" ? "" : " ghost")} onClick={() => setFilter("all")}>全部</button>
          {symbols.map((s) => (
            <button key={s} className={"btn small" + (filter === s ? "" : " ghost")} onClick={() => setFilter(s)} style={{ fontFamily: "var(--font-mono)" }}>{s}</button>
          ))}
        </div>
        <div style={{ flex: 1, overflow: "auto" }}>
          {notes.map((n) => (
            <div key={n.id} onClick={() => setSelected(n.id)}
              style={{
                padding: "12px 14px", borderBottom: "1px solid var(--line-1)", cursor: "pointer",
                background: selected === n.id ? "var(--bg-2)" : "transparent",
                boxShadow: selected === n.id ? "inset 2px 0 0 var(--accent)" : "none",
              }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                <span className="mono" style={{ fontWeight: 600, color: "var(--fg-0)" }}>{n.symbol}</span>
                <span className="chip" style={{ fontSize: 9 }}>{n.timeframe.toUpperCase()}</span>
                <span style={{ flex: 1 }} />
                <span className="mono" style={{ color: "var(--fg-2)", fontSize: 10.5 }}>{n.note_time.slice(0, 10)}</span>
              </div>
              <div style={{ fontSize: 11.5, color: "var(--fg-1)", lineHeight: 1.5, overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                {n.trend_view}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ overflow: "auto" }}>
        {active && (
          <div style={{ padding: "16px 20px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <span className="chip accent">复盘</span>
              <span className="mono" style={{ fontSize: 20, fontWeight: 700 }}>{active.symbol}</span>
              <span className="chip">{active.timeframe.toUpperCase()}</span>
              <span style={{ flex: 1 }} />
              <button className="btn small" onClick={() => { setActiveSymbol(active.symbol); setRoute("replay"); }}><I.Replay /> 回到此时</button>
              <button className="btn small ghost"><I.Trash /></button>
            </div>
            <div style={{ color: "var(--fg-2)", fontSize: 11, fontFamily: "var(--font-mono)", marginBottom: 14 }}>
              复盘起始 {active.replay_start_time.slice(0, 10)} · 笔记时间 {active.note_time.slice(0, 10)} · 创建 {active.created_at.slice(0, 16).replace("T", " ")}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 16 }}>
              {[
                { l: "支撑位", v: active.support_level, c: "var(--up)" },
                { l: "阻力位", v: active.resistance_level, c: "var(--down)" },
                { l: "止损", v: active.stop_loss, c: "var(--down)" },
                { l: "止盈", v: active.take_profit, c: "var(--up)" },
              ].map((x, i) => (
                <div key={i} className="panel" style={{ padding: "10px 14px" }}>
                  <div className="stat-label">{x.l}</div>
                  <div className="mono" style={{ fontSize: 18, color: x.c, fontWeight: 600, marginTop: 2 }}>
                    {x.v != null ? (x.v >= 1000 ? x.v.toLocaleString() : x.v) : "—"}
                  </div>
                </div>
              ))}
            </div>

            <NoteSection title="趋势判断" text={active.trend_view} />
            <NoteSection title="入场思路" text={active.entry_idea} />
            <NoteSection title="总结" text={active.summary} />

            {/* Mini chart */}
            <div className="panel" style={{ marginTop: 16 }}>
              <div className="panel-header">
                <span>价格快照</span>
                <span style={{ fontFamily: "var(--font-mono)", letterSpacing: 0, textTransform: "none", color: "var(--fg-2)" }}>
                  {active.symbol} · {active.timeframe}
                </span>
              </div>
              <div style={{ padding: "6px 0 0" }}>
                <CandlestickChart
                  candles={window.MR.CANDLES[active.symbol] || window.MR.CANDLES.AAPL}
                  showMA showVolume={false} showRSI={false} showMACD={false}
                  height={220}
                  supportLevel={active.support_level} resistanceLevel={active.resistance_level}
                  stopLoss={active.stop_loss} takeProfit={active.take_profit}
                />
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function NoteSection({ title, text }) {
  if (!text) return null;
  return (
    <div style={{ marginBottom: 16 }}>
      <div className="hline" style={{ marginBottom: 8 }}>{title}</div>
      <div style={{ color: "var(--fg-0)", fontSize: 13, lineHeight: 1.7 }}>{text}</div>
    </div>
  );
}

Object.assign(window, { DashboardPage, WatchlistPage, AlertsPage, NotesPage });
