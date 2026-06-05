// ============================================================
// Replay mode page
// ============================================================

function ReplayPage({ activeSymbol }) {
  const candles = window.MR.CANDLES[activeSymbol];
  const sym = window.MR.SYMBOLS.find((s) => s.symbol === activeSymbol);

  // Start ~60% through the data by default
  const defaultStart = Math.floor(candles.length * 0.6);
  const [startIdx] = React.useState(defaultStart);
  const [cutoff, setCutoff] = React.useState(defaultStart);
  const [playing, setPlaying] = React.useState(false);
  const [speed, setSpeed] = React.useState(400); // ms per candle
  const [timeframe] = React.useState("1day");

  // Note form state
  const [form, setForm] = React.useState({
    trend_view: "", support_level: "", resistance_level: "",
    entry_idea: "", stop_loss: "", take_profit: "", summary: "",
  });
  const [savedNotes, setSavedNotes] = React.useState([]);

  React.useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      setCutoff((i) => {
        if (i >= candles.length - 1) { setPlaying(false); return i; }
        return i + 1;
      });
    }, speed);
    return () => clearInterval(t);
  }, [playing, speed, candles.length]);

  const current = candles[cutoff];
  const totalSteps = candles.length - 1;
  const progress = ((cutoff - startIdx) / Math.max(1, totalSteps - startIdx)) * 100;

  const fmtDate = (t) => {
    const d = new Date(t * 1000);
    return d.getFullYear() + "-" + (d.getMonth() + 1).toString().padStart(2, "0") + "-" + d.getDate().toString().padStart(2, "0");
  };

  const speeds = [
    { v: 2000, l: "0.5×" },
    { v: 1000, l: "1×" },
    { v: 400,  l: "2×" },
    { v: 200,  l: "5×" },
    { v: 80,   l: "10×" },
  ];

  const saveNote = () => {
    if (!form.trend_view && !form.summary) return;
    const note = {
      id: Date.now(),
      symbol: activeSymbol, timeframe,
      note_time: current.time,
      ...form,
      support_level: form.support_level ? parseFloat(form.support_level) : null,
      resistance_level: form.resistance_level ? parseFloat(form.resistance_level) : null,
      stop_loss: form.stop_loss ? parseFloat(form.stop_loss) : null,
      take_profit: form.take_profit ? parseFloat(form.take_profit) : null,
    };
    setSavedNotes([note, ...savedNotes]);
    setForm({ trend_view: "", support_level: "", resistance_level: "", entry_idea: "", stop_loss: "", take_profit: "", summary: "" });
  };

  return (
    <div className="page" style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 0 }}>
      {/* Left — chart + controls */}
      <div style={{ borderRight: "1px solid var(--line-1)", display: "flex", flexDirection: "column", minWidth: 0 }}>
        {/* Replay header */}
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--line-1)", background: "var(--bg-1)", display: "flex", alignItems: "center", gap: 14 }}>
          <div className="pulse" />
          <div>
            <div className="section-title" style={{ color: "var(--accent)" }}>复盘模式 · REPLAY</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 3 }}>
              <span className="mono" style={{ fontSize: 16, fontWeight: 700 }}>{sym.symbol}</span>
              <span style={{ color: "var(--fg-2)" }}>{sym.name}</span>
              <span className="chip">{timeframe.toUpperCase()}</span>
            </div>
          </div>
          <div style={{ flex: 1 }} />
          <div className="stat">
            <div className="stat-label">当前日期</div>
            <div className="mono" style={{ color: "var(--fg-0)", fontSize: 13 }}>{fmtDate(current.time)}</div>
          </div>
          <div className="stat">
            <div className="stat-label">起始日期</div>
            <div className="mono" style={{ color: "var(--fg-2)", fontSize: 13 }}>{fmtDate(candles[startIdx].time)}</div>
          </div>
          <div className="stat">
            <div className="stat-label">进度</div>
            <div className="mono" style={{ color: "var(--fg-0)", fontSize: 13 }}>
              {cutoff - startIdx + 1} / {totalSteps - startIdx + 1}
            </div>
          </div>
        </div>

        {/* Chart */}
        <div style={{ flex: 1, overflow: "auto" }}>
          <CandlestickChart
            candles={candles}
            cutoff={cutoff}
            showMA showVolume showRSI showMACD
            height={540}
            highlightCurrent
            supportLevel={form.support_level ? parseFloat(form.support_level) : null}
            resistanceLevel={form.resistance_level ? parseFloat(form.resistance_level) : null}
            stopLoss={form.stop_loss ? parseFloat(form.stop_loss) : null}
            takeProfit={form.take_profit ? parseFloat(form.take_profit) : null}
          />
        </div>

        {/* Controls */}
        <div style={{ padding: "12px 16px", background: "var(--bg-1)", borderTop: "1px solid var(--line-1)" }}>
          {/* Progress bar */}
          <div style={{ position: "relative", height: 6, background: "var(--bg-2)", borderRadius: 3, marginBottom: 12, overflow: "hidden" }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: progress + "%", background: "linear-gradient(90deg, var(--accent-dim), var(--accent))" }} />
            <div style={{ position: "absolute", left: `calc(${progress}% - 6px)`, top: -3, width: 12, height: 12, background: "var(--accent)", borderRadius: "50%", boxShadow: "0 0 0 3px var(--bg-1)" }} />
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button className="btn" onClick={() => setCutoff(startIdx)} title="回到起点"><I.Rewind /></button>
            <button className="btn" onClick={() => setCutoff((i) => Math.max(startIdx, i - 1))} title="上一根"><I.StepBack /></button>
            <button className="btn primary" onClick={() => setPlaying((p) => !p)} style={{ width: 100, justifyContent: "center" }}>
              {playing ? <><I.Pause /> 暂停</> : <><I.Play /> 播放</>}
            </button>
            <button className="btn" onClick={() => setCutoff((i) => Math.min(candles.length - 1, i + 1))} title="下一根"><I.Step /></button>

            <div style={{ flex: 1 }} />

            <div className="tweak-segment">
              {speeds.map((s) => (
                <button key={s.v} className={speed === s.v ? "on" : ""} onClick={() => setSpeed(s.v)} style={{ minWidth: 42, fontFamily: "var(--font-mono)" }}>{s.l}</button>
              ))}
            </div>
          </div>

          {/* Quick stats current candle */}
          <div style={{ display: "flex", gap: 20, marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--line-1)", fontFamily: "var(--font-mono)", fontSize: 11.5 }}>
            <span style={{ color: "var(--fg-2)" }}>O <span style={{ color: "var(--fg-0)" }}>{current.open.toFixed(2)}</span></span>
            <span style={{ color: "var(--fg-2)" }}>H <span style={{ color: "var(--fg-0)" }}>{current.high.toFixed(2)}</span></span>
            <span style={{ color: "var(--fg-2)" }}>L <span style={{ color: "var(--fg-0)" }}>{current.low.toFixed(2)}</span></span>
            <span style={{ color: "var(--fg-2)" }}>C <span style={{ color: current.close >= current.open ? "var(--up)" : "var(--down)" }}>{current.close.toFixed(2)}</span></span>
            <span style={{ color: "var(--fg-2)" }}>VOL <span style={{ color: "var(--fg-0)" }}>{(current.volume / 1e6).toFixed(2)}M</span></span>
            <div style={{ flex: 1 }} />
            <span style={{ color: "var(--fg-2)" }}>快捷键: <span className="kbd">Space</span> 播放 · <span className="kbd">←</span> <span className="kbd">→</span> 步进</span>
          </div>
        </div>
      </div>

      {/* Right — notes panel */}
      <div style={{ display: "flex", flexDirection: "column", minHeight: 0, background: "var(--bg-1)" }}>
        <div className="panel-header" style={{ border: 0, borderBottom: "1px solid var(--line-1)", height: 40 }}>
          <span>复盘笔记</span>
          <span className="mono" style={{ color: "var(--fg-2)", letterSpacing: 0, textTransform: "none" }}>
            {fmtDate(current.time)}
          </span>
        </div>

        <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10, overflow: "auto", flex: 1 }}>
          <div className="tweak-row">
            <label>趋势判断</label>
            <textarea className="input" rows="2" value={form.trend_view}
              onChange={(e) => setForm({ ...form, trend_view: e.target.value })}
              placeholder="当前结构如何？方向判断？"
              style={{ height: "auto", padding: "6px 8px", resize: "vertical", fontFamily: "var(--font-ui)", fontSize: 12, lineHeight: 1.5 }} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div className="tweak-row">
              <label style={{ color: "var(--up)" }}>支撑位</label>
              <input className="input mono" value={form.support_level}
                onChange={(e) => setForm({ ...form, support_level: e.target.value })} placeholder="0.00" />
            </div>
            <div className="tweak-row">
              <label style={{ color: "var(--down)" }}>阻力位</label>
              <input className="input mono" value={form.resistance_level}
                onChange={(e) => setForm({ ...form, resistance_level: e.target.value })} placeholder="0.00" />
            </div>
          </div>

          <div className="tweak-row">
            <label>入场思路</label>
            <textarea className="input" rows="2" value={form.entry_idea}
              onChange={(e) => setForm({ ...form, entry_idea: e.target.value })}
              placeholder="条件 / 位置 / 仓位..."
              style={{ height: "auto", padding: "6px 8px", resize: "vertical", fontFamily: "var(--font-ui)", fontSize: 12, lineHeight: 1.5 }} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <div className="tweak-row">
              <label style={{ color: "var(--down)" }}>止损</label>
              <input className="input mono" value={form.stop_loss}
                onChange={(e) => setForm({ ...form, stop_loss: e.target.value })} placeholder="0.00" />
            </div>
            <div className="tweak-row">
              <label style={{ color: "var(--up)" }}>止盈</label>
              <input className="input mono" value={form.take_profit}
                onChange={(e) => setForm({ ...form, take_profit: e.target.value })} placeholder="0.00" />
            </div>
          </div>

          <div className="tweak-row">
            <label>总结</label>
            <textarea className="input" rows="2" value={form.summary}
              onChange={(e) => setForm({ ...form, summary: e.target.value })}
              placeholder="关键结论，下一步计划..."
              style={{ height: "auto", padding: "6px 8px", resize: "vertical", fontFamily: "var(--font-ui)", fontSize: 12, lineHeight: 1.5 }} />
          </div>

          <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
            <button className="btn primary" onClick={saveNote} style={{ flex: 1, justifyContent: "center" }}>
              <I.Check /> 保存笔记
            </button>
            <button className="btn"
              onClick={() => setForm({ trend_view: "", support_level: "", resistance_level: "", entry_idea: "", stop_loss: "", take_profit: "", summary: "" })}>
              清空
            </button>
          </div>

          {/* Saved notes this session */}
          {savedNotes.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div className="hline">本次会话 · {savedNotes.length} 条</div>
              {savedNotes.map((n) => (
                <div key={n.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line-1)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span className="mono" style={{ color: "var(--fg-2)", fontSize: 10.5 }}>{fmtDate(n.note_time)}</span>
                    {n.support_level && <span className="mono" style={{ color: "var(--up)", fontSize: 10.5 }}>S:{n.support_level}</span>}
                    {n.resistance_level && <span className="mono" style={{ color: "var(--down)", fontSize: 10.5 }}>R:{n.resistance_level}</span>}
                  </div>
                  {n.trend_view && <div style={{ color: "var(--fg-0)", fontSize: 11.5, lineHeight: 1.5 }}>{n.trend_view}</div>}
                  {n.summary && <div style={{ color: "var(--fg-2)", fontSize: 11, marginTop: 2, lineHeight: 1.4 }}>— {n.summary}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ReplayPage });
