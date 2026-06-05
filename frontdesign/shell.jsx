// ============================================================
// Shell: Rail, Topbar, Sidebar, Statusbar
// ============================================================

function Rail({ route, setRoute }) {
  const items = [
    { k: "dashboard", label: "总览",   icon: I.Dash   },
    { k: "chart",     label: "行情",   icon: I.Chart  },
    { k: "replay",    label: "复盘",   icon: I.Replay },
    { k: "watchlist", label: "自选",   icon: I.Star   },
    { k: "alerts",    label: "告警",   icon: I.Bell   },
    { k: "backtest",  label: "回测",   icon: I.Flask  },
    { k: "notes",     label: "笔记",   icon: I.Notes  },
  ];
  return (
    <div className="rail">
      <div className="rail-logo">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 17l5-6 4 3 5-8 4 5"/>
          <path d="M3 21h18"/>
        </svg>
      </div>
      {items.map((it) => {
        const Icon = it.icon;
        return (
          <button key={it.k} className={"rail-btn" + (route === it.k ? " active" : "")} onClick={() => setRoute(it.k)} title={it.label}>
            <Icon />
          </button>
        );
      })}
      <div className="rail-spacer" />
      <button className="rail-btn" title="设置"><I.Gear /></button>
    </div>
  );
}

function Topbar({ theme, setTheme, activeSymbol, setActiveSymbol, route }) {
  const [searchOpen, setSearchOpen] = React.useState(false);
  const [q, setQ] = React.useState("");
  const results = q
    ? window.MR.SYMBOLS.filter((s) =>
        s.symbol.toLowerCase().includes(q.toLowerCase()) || s.name.toLowerCase().includes(q.toLowerCase())
      )
    : window.MR.SYMBOLS.slice(0, 5);
  const active = window.MR.SYMBOLS.find((s) => s.symbol === activeSymbol);

  React.useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); setSearchOpen(true); }
      if (e.key === "Escape") setSearchOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="topbar">
      <div className="topbar-title">MARKET REPLAY <span style={{ color: "var(--accent)", marginLeft: 6 }}>/ 行情复盘</span></div>
      <div className="topbar-divider" />
      <div className="topbar-ticker">
        <span className="sym">{active.symbol}</span>
        <span style={{ color: "var(--fg-2)" }}>· {active.name}</span>
        <span>{active.price.toLocaleString()}</span>
        <span className={active.ch >= 0 ? "up" : "down"}>
          {active.ch >= 0 ? "▲" : "▼"} {active.ch >= 0 ? "+" : ""}{active.ch.toFixed(2)} ({active.chp.toFixed(2)}%)
        </span>
      </div>
      <div style={{ flex: 1 }} />

      <button className="btn ghost" onClick={() => setSearchOpen(true)} style={{ gap: 8 }}>
        <I.Search /> <span style={{ color: "var(--fg-2)" }}>搜索标的…</span>
        <span className="kbd">⌘</span><span className="kbd">K</span>
      </button>

      <div className="topbar-divider" />

      <div className="tweak-segment" style={{ height: 28 }}>
        <button className={theme === "dark" ? "on" : ""} onClick={() => setTheme("dark")} title="深色"><I.Moon /></button>
        <button className={theme === "light" ? "on" : ""} onClick={() => setTheme("light")} title="浅色"><I.Sun /></button>
      </div>

      {searchOpen && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 1000,
          display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: 100,
        }} onClick={() => setSearchOpen(false)}>
          <div className="panel fade-in" style={{ width: 560, padding: 0 }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 14px", borderBottom: "1px solid var(--line-1)" }}>
              <I.Search />
              <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="输入标的代码或名称，如 AAPL / 苹果 / BTC"
                style={{ flex: 1, background: "transparent", border: 0, outline: "none", color: "var(--fg-0)", fontSize: 14 }} />
              <span className="kbd">ESC</span>
            </div>
            <div style={{ maxHeight: 340, overflow: "auto" }}>
              {results.map((r) => (
                <div key={r.symbol}
                  onClick={() => { setActiveSymbol(r.symbol); setSearchOpen(false); }}
                  style={{ padding: "10px 14px", borderBottom: "1px solid var(--line-1)", display: "flex", alignItems: "center", gap: 12, cursor: "pointer" }}
                  onMouseEnter={(e) => e.currentTarget.style.background = "var(--bg-2)"}
                  onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <span className="chip mono" style={{ minWidth: 70, justifyContent: "center" }}>{r.asset_type.toUpperCase()}</span>
                  <div style={{ flex: 1 }}>
                    <div className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>{r.symbol}</div>
                    <div style={{ color: "var(--fg-2)", fontSize: 11 }}>{r.name} · {r.exchange}</div>
                  </div>
                  <div className="mono" style={{ color: "var(--fg-0)" }}>{r.price.toLocaleString()}</div>
                  <div className={"mono " + (r.ch >= 0 ? "up" : "down")} style={{ minWidth: 70, textAlign: "right" }}>
                    {r.ch >= 0 ? "+" : ""}{r.chp.toFixed(2)}%
                  </div>
                </div>
              ))}
              {results.length === 0 && (
                <div style={{ padding: 40, textAlign: "center", color: "var(--fg-2)" }}>未找到匹配的标的</div>
              )}
            </div>
            <div style={{ padding: "8px 14px", fontSize: 10.5, color: "var(--fg-2)", borderTop: "1px solid var(--line-1)", letterSpacing: ".1em" }}>
              数据来自 YAHOO FINANCE · 支持股票 / ETF / 指数 / 加密
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Statusbar({ activeSymbol }) {
  const [now, setNow] = React.useState(new Date());
  React.useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const tz = now.toTimeString().split(" ")[0];
  return (
    <div className="status">
      <span><span className="dot" /> API 在线</span>
      <span className="status-sep">·</span>
      <span>DB: postgres://market_replay</span>
      <span className="status-sep">·</span>
      <span>数据源: yfinance</span>
      <span className="status-sep">·</span>
      <span>当前标的: <span style={{ color: "var(--fg-0)" }}>{activeSymbol}</span></span>
      <div style={{ flex: 1 }} />
      <span>告警调度器: 60s</span>
      <span className="status-sep">·</span>
      <span>UTC {new Date(now.getTime() + now.getTimezoneOffset() * 60000).toTimeString().split(" ")[0]}</span>
      <span className="status-sep">·</span>
      <span>LOCAL {tz}</span>
      <span className="status-sep">·</span>
      <span>v1.0.0</span>
    </div>
  );
}

function WatchlistSide({ activeSymbol, setActiveSymbol }) {
  const [tab, setTab] = React.useState("all");
  const [q, setQ] = React.useState("");
  const items = window.MR.WATCHLIST
    .map((w) => ({ ...w, ...window.MR.SYMBOLS.find((s) => s.symbol === w.symbol) }))
    .filter((w) => tab === "all" || w.asset_type === tab)
    .filter((w) => !q || w.symbol.toLowerCase().includes(q.toLowerCase()) || w.name.toLowerCase().includes(q.toLowerCase()));

  const tabs = [
    { k: "all", label: "全部", n: window.MR.WATCHLIST.length },
    { k: "stock", label: "股票", n: window.MR.WATCHLIST.filter((w) => w.asset_type === "stock").length },
    { k: "etf", label: "ETF", n: window.MR.WATCHLIST.filter((w) => w.asset_type === "etf").length },
    { k: "crypto", label: "加密", n: window.MR.WATCHLIST.filter((w) => w.asset_type === "crypto").length },
    { k: "index", label: "指数", n: window.MR.WATCHLIST.filter((w) => w.asset_type === "index").length },
  ];

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "10px 10px 8px", borderBottom: "1px solid var(--line-1)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <span className="section-title">自选列表</span>
          <button className="btn icon small" title="添加"><I.Plus /></button>
        </div>
        <div style={{ position: "relative" }}>
          <I.Search style={{ position: "absolute", left: 8, top: 7, color: "var(--fg-2)" }} />
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="筛选..." style={{ paddingLeft: 28, width: "100%" }} />
        </div>
      </div>
      <div style={{ display: "flex", gap: 2, padding: "6px 8px", borderBottom: "1px solid var(--line-1)", overflowX: "auto" }}>
        {tabs.map((t) => (
          <button key={t.k} className={"btn small " + (tab === t.k ? "" : "ghost")} onClick={() => setTab(t.k)} style={{
            background: tab === t.k ? "var(--bg-3)" : "transparent",
            border: tab === t.k ? "1px solid var(--line-2)" : "1px solid transparent",
            color: tab === t.k ? "var(--fg-0)" : "var(--fg-2)",
            fontSize: 10.5, letterSpacing: ".08em",
          }}>
            {t.label} <span style={{ color: "var(--fg-3)", marginLeft: 3 }}>{t.n}</span>
          </button>
        ))}
      </div>
      <div style={{ flex: 1, overflow: "auto" }}>
        {items.map((item) => {
          const candles = window.MR.CANDLES[item.symbol];
          const spark = candles.slice(-40).map((c) => c.close);
          const active = item.symbol === activeSymbol;
          return (
            <div key={item.id}
              onClick={() => setActiveSymbol(item.symbol)}
              style={{
                padding: "8px 10px",
                borderBottom: "1px solid var(--line-1)",
                cursor: "pointer",
                background: active ? "var(--bg-2)" : "transparent",
                boxShadow: active ? "inset 2px 0 0 var(--accent)" : "none",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600, fontSize: 12 }}>{item.symbol}</span>
                  <span className="chip" style={{ fontSize: 9, padding: "0 5px", height: 16 }}>{item.asset_type.toUpperCase()}</span>
                </div>
                <span className={"mono " + (item.ch >= 0 ? "up" : "down")} style={{ fontSize: 11 }}>
                  {item.ch >= 0 ? "+" : ""}{item.chp.toFixed(2)}%
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <span style={{ color: "var(--fg-2)", fontSize: 10.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 90 }}>{item.name}</span>
                <Sparkline data={spark} width={64} height={18} color={item.ch >= 0 ? "var(--up)" : "var(--down)"} />
                <span className="mono" style={{ color: "var(--fg-1)", fontSize: 11, minWidth: 50, textAlign: "right" }}>
                  {item.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ padding: "8px 10px", borderTop: "1px solid var(--line-1)", fontSize: 10, color: "var(--fg-2)", fontFamily: "var(--font-mono)", letterSpacing: ".04em" }}>
        共 {items.length} 项 · 按 <span className="kbd" style={{ padding: "0 4px" }}>↑</span> <span className="kbd" style={{ padding: "0 4px" }}>↓</span> 切换
      </div>
    </div>
  );
}

Object.assign(window, { Rail, Topbar, Statusbar, WatchlistSide });
