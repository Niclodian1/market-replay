// ============================================================
// Candlestick chart — pure SVG, renders:
//   - price pane with candles, MA20/MA60
//   - volume bars
//   - RSI pane (optional)
//   - MACD pane (optional)
//   - crosshair + OHLC readout
// Accepts `cutoff` (index, inclusive) for replay mode, and
// `markers` for backtest trades.
// ============================================================

function CandlestickChart({
  candles, cutoff = null, markers = [],
  showRSI = true, showMACD = true, showVolume = true,
  showMA = true, logScale = false, height = 560,
  onCrosshair = null, crosshairIdx = null,
  supportLevel = null, resistanceLevel = null,
  stopLoss = null, takeProfit = null,
  highlightCurrent = false,
}) {
  const ref = React.useRef(null);
  const [w, setW] = React.useState(900);
  const [hover, setHover] = React.useState(null);

  React.useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((entries) => {
      setW(entries[0].contentRect.width);
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const data = cutoff != null ? candles.slice(0, cutoff + 1) : candles;
  const closes = data.map((c) => c.close);

  const ma20 = showMA ? window.MR.sma(closes, 20) : [];
  const ma60 = showMA ? window.MR.sma(closes, 60) : [];
  const rsiArr = showRSI ? window.MR.rsi(closes, 14) : [];
  const macdRes = showMACD ? window.MR.macd(closes) : { line: [], signal: [], hist: [] };

  // Layout
  const padL = 56, padR = 64, padT = 8, padB = 18;
  const innerW = Math.max(100, w - padL - padR);
  // Panes heights
  const priceH = Math.round(height * (showRSI && showMACD ? 0.52 : showRSI || showMACD ? 0.64 : 0.82));
  const volH   = showVolume ? Math.round(height * 0.1) : 0;
  const rsiH   = showRSI ? Math.round(height * (showMACD ? 0.18 : 0.22)) : 0;
  const macdH  = showMACD ? Math.round(height * (showRSI ? 0.18 : 0.22)) : 0;
  const gap = 6;
  const totalH = priceH + (showVolume ? gap + volH : 0) + (showRSI ? gap + rsiH : 0) + (showMACD ? gap + macdH : 0);

  // X scale
  const n = data.length;
  const bw = Math.max(2, Math.min(14, innerW / Math.max(n, 1) * 0.8));
  const step = innerW / Math.max(n, 1);
  const xAt = (i) => padL + step * (i + 0.5);

  // Price Y scale
  let minP = Infinity, maxP = -Infinity;
  for (let i = 0; i < data.length; i++) {
    minP = Math.min(minP, data[i].low);
    maxP = Math.max(maxP, data[i].high);
  }
  if (supportLevel != null) { minP = Math.min(minP, supportLevel); maxP = Math.max(maxP, supportLevel); }
  if (resistanceLevel != null) { minP = Math.min(minP, resistanceLevel); maxP = Math.max(maxP, resistanceLevel); }
  if (stopLoss != null) { minP = Math.min(minP, stopLoss); maxP = Math.max(maxP, stopLoss); }
  if (takeProfit != null) { minP = Math.min(minP, takeProfit); maxP = Math.max(maxP, takeProfit); }
  const rng = maxP - minP;
  minP -= rng * 0.06; maxP += rng * 0.06;

  const fY = logScale
    ? (() => {
        const lmin = Math.log(Math.max(minP, 0.0001));
        const lmax = Math.log(Math.max(maxP, 0.0001));
        return (p) => padT + (priceH - 4) * (1 - (Math.log(Math.max(p, 0.0001)) - lmin) / (lmax - lmin));
      })()
    : (p) => padT + (priceH - 4) * (1 - (p - minP) / (maxP - minP));

  // Volume Y scale
  const volTop = padT + priceH + gap;
  const maxV = Math.max(1, ...data.map((c) => c.volume || 0));
  const fVY = (v) => volTop + volH - (volH - 2) * (v / maxV);

  // RSI scale
  const rsiTop = volTop + (showVolume ? volH + gap : 0);
  const fRsi = (v) => rsiTop + (rsiH - 4) * (1 - v / 100);

  // MACD scale
  const macdTop = rsiTop + (showRSI ? rsiH + gap : 0);
  const validMacd = macdRes.line.filter((v) => v != null);
  const validSig = macdRes.signal.filter((v) => v != null);
  const validHist = macdRes.hist.filter((v) => v != null);
  let macdMin = 0, macdMax = 0;
  [...validMacd, ...validSig, ...validHist].forEach((v) => { macdMin = Math.min(macdMin, v); macdMax = Math.max(macdMax, v); });
  const macdAbs = Math.max(Math.abs(macdMin), Math.abs(macdMax), 0.001);
  const fMacd = (v) => macdTop + macdH / 2 - (v / macdAbs) * (macdH / 2 - 2);

  // Y axis ticks for price
  const ticks = 5;
  const priceTicks = [];
  for (let i = 0; i <= ticks; i++) {
    const p = logScale
      ? Math.exp(Math.log(minP) + (i / ticks) * (Math.log(maxP) - Math.log(minP)))
      : minP + (i / ticks) * (maxP - minP);
    priceTicks.push(p);
  }

  // X axis labels (time)
  const xLabels = [];
  const labelEvery = Math.max(1, Math.floor(n / 6));
  for (let i = 0; i < n; i += labelEvery) {
    xLabels.push({ i, t: data[i].time });
  }

  // Build path for MAs
  const pathFrom = (arr, yFn = fY) => {
    let d = ""; let started = false;
    for (let i = 0; i < arr.length; i++) {
      if (arr[i] == null) continue;
      const x = xAt(i), y = yFn(arr[i]);
      d += (started ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
      started = true;
    }
    return d;
  };

  const formatPrice = (p) => {
    if (p == null) return "—";
    if (p >= 1000) return p.toFixed(2);
    if (p >= 10) return p.toFixed(2);
    if (p >= 1) return p.toFixed(3);
    return p.toFixed(4);
  };
  const formatVol = (v) => v >= 1e9 ? (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(v);
  const fmtDate = (t) => {
    const d = new Date(t * 1000);
    return (d.getMonth() + 1).toString().padStart(2, "0") + "/" + d.getDate().toString().padStart(2, "0");
  };
  const fmtDateFull = (t) => {
    const d = new Date(t * 1000);
    return d.getFullYear() + "-" + (d.getMonth() + 1).toString().padStart(2, "0") + "-" + d.getDate().toString().padStart(2, "0");
  };

  // Crosshair
  const handleMove = (e) => {
    const rect = ref.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const i = Math.max(0, Math.min(n - 1, Math.round((x - padL) / step - 0.5)));
    const y = e.clientY - rect.top;
    setHover({ i, y });
    if (onCrosshair) onCrosshair(i);
  };
  const handleLeave = () => { setHover(null); if (onCrosshair) onCrosshair(null); };

  const hoveredIdx = crosshairIdx ?? hover?.i;
  const hov = hoveredIdx != null ? data[hoveredIdx] : data[data.length - 1];
  const hovCh = hov && hoveredIdx > 0 ? hov.close - data[hoveredIdx - 1].close : 0;
  const hovChp = hov && hoveredIdx > 0 ? (hovCh / data[hoveredIdx - 1].close) * 100 : 0;

  return (
    <div ref={ref} style={{ position: "relative", width: "100%", height: totalH, fontFamily: "var(--font-mono)" }}>
      {/* OHLC overlay */}
      {hov && (
        <div style={{
          position: "absolute", top: 6, left: 10, zIndex: 2,
          display: "flex", gap: 14, fontSize: 11, color: "var(--fg-1)",
          background: "rgba(10,13,18,0.7)", backdropFilter: "blur(6px)",
          padding: "4px 10px", borderRadius: 4, border: "1px solid var(--line-1)",
        }}>
          <span style={{ color: "var(--fg-2)" }}>{fmtDateFull(hov.time)}</span>
          <span>O <span style={{ color: "var(--fg-0)" }}>{formatPrice(hov.open)}</span></span>
          <span>H <span style={{ color: "var(--fg-0)" }}>{formatPrice(hov.high)}</span></span>
          <span>L <span style={{ color: "var(--fg-0)" }}>{formatPrice(hov.low)}</span></span>
          <span>C <span style={{ color: hov.close >= hov.open ? "var(--up)" : "var(--down)" }}>{formatPrice(hov.close)}</span></span>
          <span style={{ color: hovCh >= 0 ? "var(--up)" : "var(--down)" }}>
            {hovCh >= 0 ? "+" : ""}{hovCh.toFixed(2)} ({hovChp.toFixed(2)}%)
          </span>
          <span style={{ color: "var(--fg-2)" }}>VOL {formatVol(hov.volume)}</span>
          {showMA && ma20[hoveredIdx] != null && <span style={{ color: "var(--ma20)" }}>MA20 {formatPrice(ma20[hoveredIdx])}</span>}
          {showMA && ma60[hoveredIdx] != null && <span style={{ color: "var(--ma60)" }}>MA60 {formatPrice(ma60[hoveredIdx])}</span>}
        </div>
      )}

      <svg width={w} height={totalH} onMouseMove={handleMove} onMouseLeave={handleLeave} style={{ display: "block" }}>
        {/* Grid — price pane */}
        {priceTicks.map((p, i) => (
          <g key={"g" + i}>
            <line x1={padL} x2={padL + innerW} y1={fY(p)} y2={fY(p)} stroke="var(--line-1)" strokeDasharray="2 3" />
            <text x={padL + innerW + 6} y={fY(p) + 3} fill="var(--fg-2)" fontSize="10.5">{formatPrice(p)}</text>
          </g>
        ))}
        {/* X axis labels */}
        {xLabels.map((l) => (
          <text key={"xl" + l.i} x={xAt(l.i)} y={padT + priceH + (showVolume ? volH + gap : 0) + (showVolume ? 0 : 14)} textAnchor="middle" fill="var(--fg-2)" fontSize="10">{fmtDate(l.t)}</text>
        ))}

        {/* Support / Resistance / SL / TP lines */}
        {supportLevel != null && (
          <g>
            <line x1={padL} x2={padL + innerW} y1={fY(supportLevel)} y2={fY(supportLevel)} stroke="var(--up)" strokeDasharray="4 4" opacity="0.7" />
            <text x={padL + 4} y={fY(supportLevel) - 3} fill="var(--up)" fontSize="10">支撑 {formatPrice(supportLevel)}</text>
          </g>
        )}
        {resistanceLevel != null && (
          <g>
            <line x1={padL} x2={padL + innerW} y1={fY(resistanceLevel)} y2={fY(resistanceLevel)} stroke="var(--down)" strokeDasharray="4 4" opacity="0.7" />
            <text x={padL + 4} y={fY(resistanceLevel) - 3} fill="var(--down)" fontSize="10">阻力 {formatPrice(resistanceLevel)}</text>
          </g>
        )}
        {stopLoss != null && (
          <g>
            <line x1={padL} x2={padL + innerW} y1={fY(stopLoss)} y2={fY(stopLoss)} stroke="var(--down)" strokeDasharray="2 3" opacity="0.5" />
            <text x={padL + innerW - 50} y={fY(stopLoss) - 3} fill="var(--down)" fontSize="10">SL {formatPrice(stopLoss)}</text>
          </g>
        )}
        {takeProfit != null && (
          <g>
            <line x1={padL} x2={padL + innerW} y1={fY(takeProfit)} y2={fY(takeProfit)} stroke="var(--up)" strokeDasharray="2 3" opacity="0.5" />
            <text x={padL + innerW - 50} y={fY(takeProfit) - 3} fill="var(--up)" fontSize="10">TP {formatPrice(takeProfit)}</text>
          </g>
        )}

        {/* Candles */}
        {data.map((c, i) => {
          const up = c.close >= c.open;
          const color = up ? "var(--up)" : "var(--down)";
          const x = xAt(i);
          const yO = fY(c.open), yC = fY(c.close), yH = fY(c.high), yL = fY(c.low);
          const bodyTop = Math.min(yO, yC), bodyBot = Math.max(yO, yC);
          const isLast = i === data.length - 1 && highlightCurrent;
          return (
            <g key={"c" + i} opacity={isLast ? 1 : 0.95}>
              <line x1={x} x2={x} y1={yH} y2={yL} stroke={color} strokeWidth="1" />
              <rect x={x - bw / 2} y={bodyTop} width={bw} height={Math.max(1, bodyBot - bodyTop)} fill={color} />
              {isLast && (
                <rect x={x - bw / 2 - 1} y={bodyTop - 1} width={bw + 2} height={Math.max(3, bodyBot - bodyTop + 2)} fill="none" stroke="var(--accent)" strokeWidth="1" />
              )}
            </g>
          );
        })}

        {/* MA lines */}
        {showMA && <path d={pathFrom(ma20)} stroke="var(--ma20)" strokeWidth="1.2" fill="none" />}
        {showMA && <path d={pathFrom(ma60)} stroke="var(--ma60)" strokeWidth="1.2" fill="none" />}

        {/* Trade markers (for backtest) */}
        {markers.map((m, idx) => {
          const i = data.findIndex((c) => c.time === m.time);
          if (i < 0) return null;
          const x = xAt(i);
          const y = fY(m.price);
          const up = m.type === "buy";
          return (
            <g key={"m" + idx}>
              <circle cx={x} cy={y} r="4" fill={up ? "var(--up)" : "var(--down)"} stroke="var(--bg-0)" strokeWidth="1.5" />
              <text x={x} y={up ? y + 14 : y - 6} textAnchor="middle" fontSize="9" fill={up ? "var(--up)" : "var(--down)"} fontWeight="700">
                {up ? "B" : "S"}
              </text>
            </g>
          );
        })}

        {/* Volume pane */}
        {showVolume && (
          <g>
            <line x1={padL} x2={padL + innerW} y1={volTop} y2={volTop} stroke="var(--line-1)" />
            <text x={padL} y={volTop + 10} fill="var(--fg-2)" fontSize="9.5" fontWeight="600">VOL</text>
            {data.map((c, i) => {
              const up = c.close >= c.open;
              const x = xAt(i);
              return (
                <rect key={"v" + i}
                  x={x - bw / 2} y={fVY(c.volume)}
                  width={bw} height={volTop + volH - fVY(c.volume)}
                  fill={up ? "var(--up)" : "var(--down)"} opacity="0.5"
                />
              );
            })}
          </g>
        )}

        {/* RSI pane */}
        {showRSI && (
          <g>
            <line x1={padL} x2={padL + innerW} y1={rsiTop} y2={rsiTop} stroke="var(--line-1)" />
            <text x={padL} y={rsiTop + 10} fill="var(--fg-2)" fontSize="9.5" fontWeight="600">RSI(14)</text>
            <line x1={padL} x2={padL + innerW} y1={fRsi(70)} y2={fRsi(70)} stroke="var(--down)" strokeDasharray="2 3" opacity="0.4" />
            <line x1={padL} x2={padL + innerW} y1={fRsi(30)} y2={fRsi(30)} stroke="var(--up)" strokeDasharray="2 3" opacity="0.4" />
            <line x1={padL} x2={padL + innerW} y1={fRsi(50)} y2={fRsi(50)} stroke="var(--line-1)" strokeDasharray="2 3" />
            <text x={padL + innerW + 4} y={fRsi(70) + 3} fill="var(--fg-2)" fontSize="9">70</text>
            <text x={padL + innerW + 4} y={fRsi(30) + 3} fill="var(--fg-2)" fontSize="9">30</text>
            <path d={pathFrom(rsiArr, fRsi)} stroke="var(--info)" strokeWidth="1.3" fill="none" />
          </g>
        )}

        {/* MACD pane */}
        {showMACD && (
          <g>
            <line x1={padL} x2={padL + innerW} y1={macdTop} y2={macdTop} stroke="var(--line-1)" />
            <text x={padL} y={macdTop + 10} fill="var(--fg-2)" fontSize="9.5" fontWeight="600">MACD(12,26,9)</text>
            <line x1={padL} x2={padL + innerW} y1={fMacd(0)} y2={fMacd(0)} stroke="var(--line-1)" />
            {macdRes.hist.map((v, i) => {
              if (v == null) return null;
              const x = xAt(i);
              const y0 = fMacd(0), y = fMacd(v);
              const up = v >= 0;
              return <rect key={"h" + i} x={x - bw / 2} y={Math.min(y, y0)} width={bw} height={Math.max(1, Math.abs(y - y0))} fill={up ? "var(--up)" : "var(--down)"} opacity="0.55" />;
            })}
            <path d={pathFrom(macdRes.line, fMacd)} stroke="var(--info)" strokeWidth="1.2" fill="none" />
            <path d={pathFrom(macdRes.signal, fMacd)} stroke="var(--accent)" strokeWidth="1.2" fill="none" />
          </g>
        )}

        {/* Crosshair */}
        {hoveredIdx != null && data[hoveredIdx] && (
          <g pointerEvents="none">
            <line x1={xAt(hoveredIdx)} x2={xAt(hoveredIdx)} y1={padT} y2={padT + totalH - padT - padB} stroke="var(--fg-3)" strokeDasharray="3 3" opacity="0.6" />
            {hover?.y != null && (
              <>
                <line x1={padL} x2={padL + innerW} y1={hover.y} y2={hover.y} stroke="var(--fg-3)" strokeDasharray="3 3" opacity="0.6" />
              </>
            )}
          </g>
        )}
      </svg>
    </div>
  );
}

// Simple sparkline for sidebar
function Sparkline({ data, width = 80, height = 24, color = "var(--up)" }) {
  if (!data || !data.length) return null;
  let min = Infinity, max = -Infinity;
  for (const v of data) { min = Math.min(min, v); max = Math.max(max, v); }
  const rng = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / rng) * height;
    return x.toFixed(1) + "," + y.toFixed(1);
  }).join(" ");
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.2" />
    </svg>
  );
}

// Equity curve chart (backtest)
function EquityCurve({ equity, trades, bhStart, width, height = 260 }) {
  const ref = React.useRef(null);
  const [w, setW] = React.useState(width || 600);
  React.useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver((e) => setW(e[0].contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const padL = 56, padR = 12, padT = 12, padB = 28;
  const innerW = w - padL - padR;
  const innerH = height - padT - padB;

  const values = equity.map((e) => e.value);
  const initialVal = values[0];
  // Buy-and-hold reference
  const bh = equity.map((e, i) => (bhStart ? (bhStart * (equity[i].value / initialVal) * 0 + bhStart * (1 + (i / (equity.length - 1)) * 0)) : null));
  // Build B&H curve based on close ratio
  // We'll fake it: B&H = initial * (close[i]/close[0]) — here we approximate using equity length
  const bhCurve = equity.map((e, i) => initialVal * (1 + ((i / (equity.length - 1)) * (bhStart || 0)) / 100));

  const allVals = [...values, ...bhCurve];
  let min = Math.min(...allVals), max = Math.max(...allVals);
  const rng = max - min || 1;
  min -= rng * 0.04; max += rng * 0.04;

  const xAt = (i) => padL + (i / (equity.length - 1)) * innerW;
  const yAt = (v) => padT + innerH * (1 - (v - min) / (max - min));

  const path = (vals) => vals.map((v, i) => (i ? "L" : "M") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1)).join(" ");

  const fmt = (t) => {
    const d = new Date(t * 1000);
    return d.getFullYear() + "-" + (d.getMonth() + 1).toString().padStart(2, "0");
  };

  const ticks = 4;
  const yTicks = [];
  for (let i = 0; i <= ticks; i++) yTicks.push(min + (i / ticks) * (max - min));

  // Build area path for equity
  const areaPath = path(values) + ` L ${xAt(values.length - 1).toFixed(1)},${padT + innerH} L ${padL},${padT + innerH} Z`;

  return (
    <div ref={ref} style={{ width: "100%", height }}>
      <svg width={w} height={height} style={{ display: "block" }}>
        <defs>
          <linearGradient id="eqGrad" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={padL} x2={padL + innerW} y1={yAt(v)} y2={yAt(v)} stroke="var(--line-1)" strokeDasharray="2 3" />
            <text x={padL - 6} y={yAt(v) + 3} textAnchor="end" fill="var(--fg-2)" fontSize="10" fontFamily="var(--font-mono)">
              ${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </text>
          </g>
        ))}

        <line x1={padL} x2={padL + innerW} y1={yAt(initialVal)} y2={yAt(initialVal)} stroke="var(--fg-3)" strokeDasharray="3 4" opacity="0.5" />

        <path d={areaPath} fill="url(#eqGrad)" />
        <path d={path(bhCurve)} fill="none" stroke="var(--fg-3)" strokeWidth="1.2" strokeDasharray="3 3" />
        <path d={path(values)} fill="none" stroke="var(--accent)" strokeWidth="1.6" />

        {/* trade markers */}
        {trades && trades.map((t, idx) => {
          const ei = equity.findIndex((e) => e.time >= t.entry_time);
          const xi = equity.findIndex((e) => e.time >= t.exit_time);
          if (ei < 0 || xi < 0) return null;
          const ey = yAt(equity[ei].value);
          const xy = yAt(equity[xi].value);
          return (
            <g key={idx}>
              <circle cx={xAt(ei)} cy={ey} r="3" fill="var(--up)" stroke="var(--bg-0)" strokeWidth="1" />
              <circle cx={xAt(xi)} cy={xy} r="3" fill={t.pnl_pct >= 0 ? "var(--info)" : "var(--down)"} stroke="var(--bg-0)" strokeWidth="1" />
            </g>
          );
        })}

        {/* X labels */}
        {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
          const idx = Math.floor(f * (equity.length - 1));
          return <text key={i} x={xAt(idx)} y={height - 8} fill="var(--fg-2)" fontSize="10" textAnchor="middle" fontFamily="var(--font-mono)">{fmt(equity[idx].time)}</text>;
        })}
      </svg>
    </div>
  );
}

Object.assign(window, { CandlestickChart, Sparkline, EquityCurve });
