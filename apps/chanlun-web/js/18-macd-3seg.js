// js/18-macd-3seg.js — MACD 柱形图三段顶/底背离（半木夏）图表层
// 数据: data-view/macd3seg_data.js ← scripts/strategies/run_macd_3seg_validation.py（Python 逐K线因果引擎生成）
// 显示: 主图 3底买/3顶卖/次日损/硬损 标记 + 硬止损水平线 + 三段价格极值连线；MACD 副图三段柱峰值连线。
// 盲判/复盘: 只显示「截止时刻」及以前已经确认的事件（引擎零未来函数，截断前缀与全量结果一致）。
'use strict';

const MACD3SEG = {
  on: false,               // 默认保持主图干净, 需要时可在「指标」菜单开启 三段背离
  open: false,
  cardMin: false,
  panelTab: 'cards',      // 'cards' (全品种统计卡片墙) | 'entries' (当前品种信号列表)
  marketFilter: 'ALL',    // 'ALL' | '黄金/贵金属' | '美股' | 'A股' | '港股/其他' | '加密'
  sel: null,
  pinned: false,
  popup: null,
  symCard: null,
  _cache: null,
  _lastSymTf: null,
};
try {
  const saved = localStorage.getItem('chanlun.macd3seg.v3');
  if (saved === 'on') MACD3SEG.on = true;
  // 品种统计卡默认折叠(只留一行标题), 避免遮挡主图; 用户展开过则记住
  MACD3SEG.cardMin = localStorage.getItem('chanlun.macd3seg.cardMin') !== '0';
} catch (_) {}

const M3S_COL = { buy: '#0d9f86', sell: '#ef4452', stop: '#f59e0b', seg: '#8b5cf6' };

function m3sFmtDate(ts) {
  const d = new Date(ts * 1000);
  const s = d.toISOString().slice(0, 16).replace('T', ' ');
  return s.replace(' 00:00', '');
}
function m3sFmtHist(v) {
  if (v == null || !Number.isFinite(v)) return '—';
  const a = Math.abs(v);
  return a >= 10 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : v.toFixed(3);
}
function m3sPct(v, d = 1) {
  if (v == null || !Number.isFinite(v)) return '—';
  return (v * 100).toFixed(d) + '%';
}
function m3sR(v) {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(2) + 'R';
}

function m3sSummarizeTrades(trs) {
  const n = trs ? trs.length : 0;
  if (!n) {
    return {
      n: 0, valid_n: 0, init_risk_mean: 0,
      next_stop_n: 0, next_stop_rate: 0, next_stop_ret: 0,
      true_stop_n: 0, true_stop_rate: 0, hard_stop_50_rate: 0,
      mfe15_mean: 0, mfe30_mean: 0, mfe50_mean: 0,
      valid_mfe15_mean: 0, valid_mfe30_mean: 0, valid_mfe50_mean: 0, valid_mfe50_med: 0,
      valid_r_mean: 0, valid_r_med: 0, ge1r_rate: 0, ge2r_rate: 0, ge3r_rate: 0,
    };
  }
  const mean = arr => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
  const median = arr => {
    if (!arr.length) return 0;
    const s = arr.slice().sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : 0.5 * (s[m - 1] + s[m]);
  };
  const ns = trs.filter(x => x.next_stopped);
  const valid = trs.filter(x => !x.next_stopped);
  const tsN = trs.filter(x => x.true_stopped).length;
  const hs50N = trs.filter(x => x.hard_stop_50).length;
  return {
    n,
    valid_n: valid.length,
    init_risk_mean: mean(trs.map(x => x.init_risk || 0)),
    next_stop_n: ns.length,
    next_stop_rate: ns.length / n,
    next_stop_ret: mean(ns.map(x => x.ret || 0)),
    true_stop_n: tsN,
    true_stop_rate: tsN / n,
    hard_stop_50_rate: hs50N / n,
    mfe15_mean: mean(trs.map(x => x.mfe15 || 0)),
    mfe30_mean: mean(trs.map(x => x.mfe30 || 0)),
    mfe50_mean: mean(trs.map(x => x.mfe50 || 0)),
    valid_mfe15_mean: mean(valid.map(x => x.mfe15 || 0)),
    valid_mfe30_mean: mean(valid.map(x => x.mfe30 || 0)),
    valid_mfe50_mean: mean(valid.map(x => x.mfe50 || 0)),
    valid_mfe50_med: median(valid.map(x => x.mfe50 || 0)),
    valid_r_mean: mean(valid.map(x => x.r_mult50 || 0)),
    valid_r_med: median(valid.map(x => x.r_mult50 || 0)),
    ge1r_rate: trs.filter(x => (x.r_mult50 || 0) >= 1).length / n,
    ge2r_rate: trs.filter(x => (x.r_mult50 || 0) >= 2).length / n,
    ge3r_rate: trs.filter(x => (x.r_mult50 || 0) >= 3).length / n,
  };
}

// 浏览器端零未来函数逐K线因果状态机（与 scripts/strategies/macd_3seg_divergence.py 完全同构）
function macd3segComputeJS(cs) {
  const n = cs ? cs.length : 0;
  if (n < 36) return { events: [], trades: [] };
  const P = {
    fast: 12, slow: 26, signal: 9,
    near_zero_frac: 0.10, confirm_frac: 0.30, noise_frac: 0.20,
    scale_window: 100, merge_blip_max_bars: 3, s3_min_frac: 0.05,
    min_shrink_frac: 0.02, warmup: 35, max_hold: 50,
  };
  const t = new Array(n), h_ = new Array(n), l_ = new Array(n), c = new Array(n), hist = new Array(n);
  let emaF = cs[0].close, emaS = cs[0].close, dea = 0;
  const kF = 2 / (P.fast + 1), kS = 2 / (P.slow + 1), kSig = 2 / (P.signal + 1);
  for (let i = 0; i < n; i++) {
    t[i] = cs[i].time; h_[i] = cs[i].high; l_[i] = cs[i].low; c[i] = cs[i].close;
    if (i > 0) {
      emaF = c[i] * kF + emaF * (1 - kF);
      emaS = c[i] * kS + emaS * (1 - kS);
    }
    const dif = emaF - emaS;
    dea = i === 0 ? dif : (dif * kSig + dea * (1 - kSig));
    hist[i] = dif - dea;
  }

  const segments = [];
  let live = null, openTr = null, holding = [], tradeSeq = 0;
  const events = [], trades = [];

  const scaleAt = idx => {
    const a = Math.max(0, idx - P.scale_window + 1);
    let m = 0;
    for (let j = a; j <= idx; j++) { const v = Math.abs(hist[j]); if (v > m) m = v; }
    return m;
  };
  const segStats = (seg, upto) => {
    const s = seg.start, e = upto == null ? seg.end : Math.min(seg.end, upto);
    let k = s, kp = s, kc = s;
    for (let j = s + 1; j <= e; j++) {
      if (seg.sign > 0 ? hist[j] > hist[k] : hist[j] < hist[k]) k = j;
      if (seg.sign > 0 ? h_[j] > h_[kp] : l_[j] < l_[kp]) kp = j;
      if (seg.sign > 0 ? c[j] > c[kc] : c[j] < c[kc]) kc = j;
    }
    const px = seg.sign > 0 ? h_[kp] : l_[kp];
    return { sign: seg.sign, start: s, end: e, peak: hist[k], peak_bar: k, peak_abs: Math.abs(hist[k]), px, px_bar: kp, wick: px, wick_bar: kp, close_ext: c[kc], close_bar: kc };
  };
  const segDict = (seg, upto, tag) => {
    const st = segStats(seg, upto);
    return {
      tag, sign: st.sign, start_time: t[st.start], end_time: t[st.end],
      start_date: m3sFmtDate(t[st.start]), end_date: m3sFmtDate(t[st.end]),
      hist_peak: st.peak, hist_time: t[st.peak_bar], hist_date: m3sFmtDate(t[st.peak_bar]),
      price: st.px, price_time: t[st.px_bar], price_date: m3sFmtDate(t[st.px_bar]),
      wick: st.wick, wick_time: t[st.wick_bar],
    };
  };
  const finishSeg = (seg, end) => {
    seg.end = end; seg.trough = null;
    const st = segStats(seg);
    seg.noise = st.peak_abs < P.noise_frac * scaleAt(end);
    segments.push(seg);
  };
  const closeTrade = (tr, i, px, kind, label, note) => {
    const long = tr.side === 'long';
    tr.exit_bar = i; tr.exit_time = t[i]; tr.exit_date = m3sFmtDate(t[i]);
    tr.exit_price = px; tr.exit_kind = kind; tr.exit_label = label;
    tr.ret = long ? (px / tr.entry - 1) : (1 - px / tr.entry);
    tr.bars = i - tr.entry_bar;
    events.push({ kind, label, bar: i, time: t[i], date: m3sFmtDate(t[i]), price: px, side: tr.side, trade_id: tr.id, attempt: tr.attempt, stop_price: tr.stop, hist: hist[i], note });
  };

  for (let i = 0; i < n; i++) {
    // 1. check open (T+1)
    if (openTr && openTr.entry_bar === i - 1) {
      const tr = openTr; openTr = null;
      const seg = tr.seg, long = tr.side === 'long';
      const h = hist[i], hp = hist[i - 1];
      const hard = long ? (l_[i] <= tr.stop) : (h_[i] >= tr.stop);
      const momFail = long ? (h <= hp) : (h >= hp);
      if (hard || momFail) {
        let px = tr.stop, kind = 'stop_hard', label = '硬止损', note = 'T+1 盘中触及第三段极值';
        if (hard && momFail) note += '，且收盘柱子未继续缩短';
        else if (!hard) { px = c[i]; kind = 'stop_next'; label = '次日止损'; note = 'T+1 收盘柱子未继续缩短'; }
        closeTrade(tr, i, px, kind, label, note);
        seg.state = 'stopped'; seg.stop_bar = i; seg.rearm_ref = tr.stop;
      } else {
        seg.state = 'consumed'; holding.push(tr);
      }
    }
    // 2. check holding
    if (holding.length) {
      const keep = [];
      for (const tr of holding) {
        const long = tr.side === 'long';
        if ((long && l_[i] <= tr.stop) || (!long && h_[i] >= tr.stop)) {
          closeTrade(tr, i, tr.stop, 'stop_hard', '硬止损', '持仓期间触及第三段极值');
          if (tr.seg === live) { tr.seg.state = 'stopped'; tr.seg.stop_bar = i; tr.seg.rearm_ref = tr.stop; }
        } else if (i - tr.entry_bar >= P.max_hold) {
          closeTrade(tr, i, c[i], 'expire', '持有到期', `${P.max_hold} 根到期统计`);
        } else keep.push(tr);
      }
      holding = keep;
    }
    // 3. update segments
    const h = hist[i];
    const sgn = h > 0 ? 1 : (h < 0 ? -1 : (live ? live.sign : 1));
    if (!live) {
      live = { sign: sgn, start: i, end: i, noise: false, trough: null, state: 'idle', rearm_ref: null, stop_bar: null, n_signals: 0 };
    } else if (sgn !== live.sign) {
      finishSeg(live, i - 1);
      const blip = segments[segments.length - 1];
      let merged = false;
      if (segments.length >= 2) {
        const prev = segments[segments.length - 2];
        if (blip.noise && prev.sign === sgn && prev.state === 'stopped' && prev.stop_bar != null && prev.stop_bar >= blip.start && (blip.end - blip.start + 1) <= P.merge_blip_max_bars && prev.end === blip.start - 1) {
          segments.pop(); segments.pop();
          prev.end = i; prev.noise = false; live = prev; merged = true;
        }
      }
      if (!merged) live = { sign: sgn, start: i, end: i, noise: false, trough: null, state: 'idle', rearm_ref: null, stop_bar: null, n_signals: 0 };
    } else {
      live.end = i;
      const ah = Math.abs(h);
      if (live.trough == null) {
        let ref = 0;
        for (let j = live.start; j < i; j++) { const v = Math.abs(hist[j]); if (v > ref) ref = v; }
        if (ref > 0 && ref >= P.noise_frac * scaleAt(i) && ah <= P.near_zero_frac * ref) live.trough = i;
      } else {
        const trIdx = live.trough;
        if (ah <= Math.abs(hist[trIdx])) live.trough = i;
        else {
          let refPeak = 0, tentPeak = 0;
          for (let j = live.start; j <= trIdx; j++) { const v = Math.abs(hist[j]); if (v > refPeak) refPeak = v; }
          for (let j = trIdx + 1; j <= i; j++) { const v = Math.abs(hist[j]); if (v > tentPeak) tentPeak = v; }
          if (tentPeak >= P.confirm_frac * refPeak) {
            finishSeg(live, trIdx);
            live = { sign: live.sign, start: trIdx + 1, end: i, noise: false, trough: null, state: 'idle', rearm_ref: null, stop_bar: null, n_signals: 0 };
          }
        }
      }
    }
    // 4. try trigger
    if (i < P.warmup || openTr) continue;
    let s3 = null;
    if (live && live.start <= i - 1) s3 = live;
    else if (segments.length && segments[segments.length - 1].end === i - 1) s3 = segments[segments.length - 1];
    if (!s3 || s3.state === 'open' || s3.state === 'consumed') continue;
    const hp = hist[i - 1], sign = s3.sign;
    if (sign < 0 && !(hp < 0 && h > hp)) continue;
    if (sign > 0 && !(hp > 0 && h < hp)) continue;
    // Only the last two qualifying segments are used; older segments cannot affect the signal.
    let s1=null, s2=null;
    for(let j=segments.length-1;j>=0;j--){
      const seg=segments[j];
      if(seg.sign!==sign||seg.noise||seg.end>=s3.start)continue;
      if(!s2)s2=seg;
      else {s1=seg;break;}
    }
    if(!s1)continue;
    const a = segStats(s1), b = segStats(s2);
    const c3 = segStats({ sign, start: s3.start, end: i });
    const c3h = segStats(s3, i - 1);
    const wickOk = sign < 0 ? (a.wick > b.wick && b.wick > c3.wick) : (a.wick < b.wick && b.wick < c3.wick);
    const closeOk = sign < 0 ? (a.close_ext > b.close_ext && b.close_ext > c3.close_ext) : (a.close_ext < b.close_ext && b.close_ext < c3.close_ext);
    const priceOk = wickOk || closeOk;
    const k = 1.0 - P.min_shrink_frac;
    const histOk = b.peak_abs < k * a.peak_abs && c3h.peak_abs < k * b.peak_abs && c3h.peak_abs >= P.s3_min_frac * b.peak_abs;
    if (!priceOk || !histOk) continue;
    if (s3.state === 'stopped') {
      if (s3.stop_bar == null || i <= s3.stop_bar) continue;
      let ext = sign < 0 ? Infinity : -Infinity;
      for (let j = s3.stop_bar + 1; j <= i; j++) {
        ext = sign < 0 ? Math.min(ext, l_[j]) : Math.max(ext, h_[j]);
      }
      if (!(sign < 0 ? ext < s3.rearm_ref : ext > s3.rearm_ref)) continue;
    }
    tradeSeq += 1;
    s3.n_signals += 1;
    s3.state = 'open';
    let stop = sign < 0 ? Infinity : -Infinity;
    for (let j = s3.start; j <= i; j++) stop = sign < 0 ? Math.min(stop, l_[j]) : Math.max(stop, h_[j]);
    const side = sign < 0 ? 'long' : 'short';
    const segs = [segDict(s1, null, 'S1'), segDict(s2, null, 'S2'), segDict({ sign, start: s3.start, end: i }, null, 'S3')];
    segs[2].hist_peak = c3h.peak; segs[2].hist_time = t[c3h.peak_bar]; segs[2].hist_date = m3sFmtDate(t[c3h.peak_bar]);
    const tr = { id: tradeSeq, side, entry_bar: i, entry_time: t[i], entry_date: m3sFmtDate(t[i]), entry: c[i], stop, attempt: s3.n_signals, seg: s3, segments: segs };
    openTr = tr;
    trades.push(tr);
    events.push({
      kind: side === 'long' ? 'buy' : 'sell',
      label: side === 'long' ? '3段底背离买' : '3段顶背离卖',
      bar: i, time: t[i], date: m3sFmtDate(t[i]), price: c[i], side, trade_id: tr.id,
      attempt: s3.n_signals, stop_price: stop, segments: segs, hist: h,
      note: (s3.n_signals > 1 ? '二次入场' : '首次入场') + ((h > 0 ? 1 : -1) !== sign ? '（柱子已变色）' : ''),
    });
  }

  // 计算每笔信号的初始止损宽、次日止损、真实止损与 15/30/50 根触损前最大浮盈 (MFE)
  const trMap = {};
  for (const tr of trades) {
    const i = tr.entry_bar, long = tr.side === 'long', entry = tr.entry, stop = tr.stop;
    const initRisk = long ? (entry - stop) / entry : (stop - entry) / entry;
    const nextStopped = (tr.exit_kind === 'stop_next' || tr.exit_kind === 'stop_hard') && tr.bars === 1;
    let hardBar = null;
    for (let j = i + 1; j <= Math.min(n - 1, i + 50); j++) {
      if ((long && l_[j] <= stop) || (!long && h_[j] >= stop)) { hardBar = j - i; break; }
    }
    const calcMfe = w => {
      if (nextStopped) return 0;
      let endK = Math.min(n - 1, i + w);
      if (hardBar != null && hardBar <= w) endK = i + hardBar - 1;
      if (endK <= i) return 0;
      let m = 0;
      for (let j = i + 1; j <= endK; j++) {
        const r = long ? (h_[j] / entry - 1) : (1 - l_[j] / entry);
        if (r > m) m = r;
      }
      return m;
    };
    tr.init_risk = initRisk;
    tr.next_stopped = nextStopped;
    tr.mfe15 = calcMfe(15);
    tr.mfe30 = calcMfe(30);
    tr.mfe50 = calcMfe(50);
    tr.r_mult50 = initRisk > 1e-6 ? tr.mfe50 / initRisk : 0;
    tr.true_stopped = nextStopped || (hardBar != null && hardBar <= 50 && tr.mfe50 < initRisk);
    tr.hard_stop_50 = nextStopped || (hardBar != null && hardBar <= 50);
    trMap[tr.id] = tr;
  }
  for (const ev of events) {
    if ((ev.kind === 'buy' || ev.kind === 'sell') && trMap[ev.trade_id]) {
      const tr = trMap[ev.trade_id];
      ev.init_risk = tr.init_risk;
      ev.mfe15 = tr.mfe15; ev.mfe30 = tr.mfe30; ev.mfe50 = tr.mfe50;
      ev.r_mult50 = tr.r_mult50;
      ev.next_stopped = tr.next_stopped;
      ev.true_stopped = tr.true_stopped;
    }
  }
  const summary = {
    all: m3sSummarizeTrades(trades),
    long: m3sSummarizeTrades(trades.filter(x => x.side === 'long')),
    short: m3sSummarizeTrades(trades.filter(x => x.side === 'short')),
  };
  return { events, trades, summary };
}

function macd3segSeries() {
  const key = `${currentSym}|${activeTF}`;
  const all = window.MACD3SEG_DATA && window.MACD3SEG_DATA.series;
  if (all && all[key]) return all[key];
  const cs = candlesFor('raw');
  if (!cs || !cs.length) return null;
  if (MACD3SEG._cache?.bars !== cs) {
    MACD3SEG._cache = { bars:cs, value:macd3segComputeJS(cs) };
  }
  return MACD3SEG._cache.value;
}
function macd3segCurrentSummary() {
  const s = macd3segSeries();
  if (!s) return null;
  const cut = macd3segCutoff();
  if (cut === Infinity && s.summary && s.summary.all) return s.summary;
  const trs = (s.trades || []).filter(tr => tr.entry_time <= cut);
  return {
    all: m3sSummarizeTrades(trs),
    long: m3sSummarizeTrades(trs.filter(x => x.side === 'long')),
    short: m3sSummarizeTrades(trs.filter(x => x.side === 'short')),
  };
}
function macd3segSymDisplayName(sym) {
  const sel = document.getElementById('symSelect');
  if (sel) {
    const opt = [...sel.options].find(o => o.value === sym);
    if (opt) return opt.textContent.trim();
  }
  return sym;
}
function macd3segCutoff() {
  let t = Infinity;
  if (typeof inReplay === 'function' && inReplay()) t = Math.min(t, replayT);
  if (typeof ANN !== 'undefined' && ANN.on && ANN.hideFuture && ANN.sigs && ANN.sigs[ANN.idx]) {
    const c = candlesFor('raw')[ANN.sigs[ANN.idx].i];
    if (c) t = Math.min(t, c.time);
  }
  return t;
}
function macd3segEvents() {
  const s = macd3segSeries();
  if (!s) return [];
  const cut = macd3segCutoff();
  return s.events.filter(e => e.time <= cut);
}
function macd3segEntries() { return macd3segEvents().filter(e => e.kind === 'buy' || e.kind === 'sell'); }
function macd3segExitOf(tradeId) {
  return macd3segEvents().find(e => e.trade_id === tradeId && e.kind !== 'buy' && e.kind !== 'sell') || null;
}
// 原始K线时间 → 当前视图(原始/合并)中包含它的那根
function macd3segViewTime(t, cs) {
  if (typeof cover67IndicatorBar === 'function') {
    const j = cover67IndicatorBar(t, cs);
    return j >= 0 ? cs[j].time : null;
  }
  return t;
}

// ---------------------------------------------------------------- 标记 --
function macd3segMarkers(view) {
  if (!MACD3SEG.on) return [];
  const cs = candlesFor(view), out = [];
  for (const e of macd3segEvents()) {
    const time = macd3segViewTime(e.time, cs);
    if (time == null) continue;
    const sel = MACD3SEG.sel === e.trade_id;
    if (e.kind === 'buy') {
      out.push({ time, id: 'm3s:' + e.trade_id, position: 'belowBar', shape: 'arrowUp', color: M3S_COL.buy,
        size: sel ? 1.6 : 1.1, text: e.attempt > 1 ? '3底买②' : '3底买' });
    } else if (e.kind === 'sell') {
      out.push({ time, id: 'm3s:' + e.trade_id, position: 'aboveBar', shape: 'arrowDown', color: M3S_COL.sell,
        size: sel ? 1.6 : 1.1, text: e.attempt > 1 ? '3顶卖②' : '3顶卖' });
    } else if (e.kind === 'stop_next' || e.kind === 'stop_hard') {
      out.push({ time, id: 'm3s:' + e.trade_id, position: e.side === 'long' ? 'belowBar' : 'aboveBar', shape: 'circle',
        color: M3S_COL.stop, size: 0.8, text: e.kind === 'stop_next' ? '次日损' : '硬损' });
    }
  }
  return out;
}

// ---------------------------------------------------------------- 画线 --
function m3sArrow(ctx, x1, y1, x2, y2, color, width, dash) {
  ctx.save();
  ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = width;
  ctx.setLineDash(dash || []);
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  ctx.setLineDash([]);
  const a = Math.atan2(y2 - y1, x2 - x1), L = 7;
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - L * Math.cos(a - 0.4), y2 - L * Math.sin(a - 0.4));
  ctx.lineTo(x2 - L * Math.cos(a + 0.4), y2 - L * Math.sin(a + 0.4));
  ctx.closePath(); ctx.fill();
  ctx.restore();
}

class Macd3SegRenderer {
  constructor(prim) { this._prim = prim; }
  draw(target) {
    if (!MACD3SEG.on) return;
    const pane = this._prim._pane, series = this._prim._series, ts = this._prim._chart.timeScale();
    const events = macd3segEvents(), entries = events.filter(e=>e.kind==='buy'||e.kind==='sell');
    if (!entries.length) return;
    const exits = pane === 'main' ? new Map(events.filter(e=>e.kind!=='buy'&&e.kind!=='sell').map(e=>[e.trade_id,e])) : null;
    const cs = candlesFor(activeView);
    const X = t => { const vt = macd3segViewTime(t, cs); return vt == null ? null : ts.timeToCoordinate(vt); };
    const Y = v => series.priceToCoordinate(v);
    target.useMediaCoordinateSpace(scope => {
      const ctx = scope.context, W = scope.mediaSize.width;
      ctx.font = '600 10px system-ui, sans-serif';
      for (const e of entries) {
        const sel = MACD3SEG.sel === e.trade_id;
        const anySel = MACD3SEG.sel != null;
        const col = e.kind === 'buy' ? M3S_COL.buy : M3S_COL.sell;
        const alpha = sel ? 1 : (anySel ? 0.25 : 0.7);
        const segs = e.segments || [];
        ctx.globalAlpha = alpha;
        if (pane === 'main') {
          // 三段价格极值连线（影线高/低点）
          const pts = segs.map(s => ({ x: X(s.wick_time), y: Y(s.wick), tag: s.tag }));
          for (let k = 0; k + 1 < pts.length; k++) {
            const a = pts[k], b = pts[k + 1];
            if (a.x == null || b.x == null || a.y == null || b.y == null) continue;
            if (Math.max(a.x, b.x) < -20 || Math.min(a.x, b.x) > W + 20) continue;
            m3sArrow(ctx, a.x, a.y, b.x, b.y, col, sel ? 2 : 1.3, [5, 3]);
          }
          ctx.fillStyle = col;
          ctx.textAlign = 'center';
          for (const p of pts) {
            if (p.x == null || p.y == null || p.x < -20 || p.x > W + 20) continue;
            ctx.beginPath(); ctx.arc(p.x, p.y, sel ? 3.5 : 2.5, 0, Math.PI * 2); ctx.fill();
            ctx.fillText(p.tag, p.x, e.kind === 'buy' ? p.y + 14 : p.y - 7);
          }
          // 硬止损水平线: 入场 → 离场（未离场则延伸 15 根）
          const x0 = X(e.time), ys = Y(e.stop_price);
          if (x0 != null && ys != null) {
            const ex = exits.get(e.trade_id);
            let x1 = ex ? X(ex.time) : null;
            if (x1 == null) {
              const bs = this._prim._chart.timeScale().options().barSpacing || 6;
              x1 = x0 + bs * 15;
            }
            if (Math.max(x0, x1) >= -20 && Math.min(x0, x1) <= W + 20) {
              ctx.strokeStyle = M3S_COL.stop; ctx.lineWidth = sel ? 1.8 : 1.1; ctx.setLineDash([6, 4]);
              ctx.beginPath(); ctx.moveTo(x0, ys); ctx.lineTo(Math.max(x1, x0 + 12), ys); ctx.stroke();
              ctx.setLineDash([]);
              ctx.fillStyle = M3S_COL.stop; ctx.textAlign = 'left';
              if (sel) ctx.fillText(`止损 ${fmt(e.stop_price)}`, x0 + 4, e.kind === 'buy' ? ys + 12 : ys - 5);
            }
          }
        } else {
          // MACD 副图: 三段柱峰值连线
          const pts = segs.map(s => ({ x: X(s.hist_time), y: Y(s.hist_peak), tag: s.tag }));
          for (let k = 0; k + 1 < pts.length; k++) {
            const a = pts[k], b = pts[k + 1];
            if (a.x == null || b.x == null || a.y == null || b.y == null) continue;
            if (Math.max(a.x, b.x) < -20 || Math.min(a.x, b.x) > W + 20) continue;
            m3sArrow(ctx, a.x, a.y, b.x, b.y, col, sel ? 2 : 1.3, [4, 3]);
          }
          ctx.fillStyle = col; ctx.textAlign = 'center';
          for (const p of pts) {
            if (p.x == null || p.y == null || p.x < -20 || p.x > W + 20) continue;
            ctx.beginPath(); ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2); ctx.fill();
            ctx.fillText(p.tag, p.x, e.kind === 'buy' ? p.y + 13 : p.y - 6);
          }
        }
        ctx.globalAlpha = 1;
        ctx.textAlign = 'left';
      }
    });
  }
}
class Macd3SegView {
  constructor(prim) { this._prim = prim; this._r = new Macd3SegRenderer(prim); }
  zOrder() { return 'top'; }
  update() {}
  renderer() { return this._r; }
}
class Macd3SegPrimitive {
  constructor(series, chartRef, pane) { this._series = series; this._chart = chartRef; this._pane = pane; this._view = new Macd3SegView(this); this._req = null; }
  attached(p) { this._req = p.requestUpdate; }
  detached() { this._req = null; }
  updateAllViews() {}
  paneViews() { return [this._view]; }
  requestUpdate() { if (this._req) this._req(); }
}

// ---------------------------------------------------------------- 信号详情 + 品种表现卡 --
function macd3segPopup(tradeId, point, pin = false) {
  const e = macd3segEntries().find(x => x.trade_id === tradeId);
  const el = MACD3SEG.popup;
  if (!e || !el) return;
  if (pin) MACD3SEG.pinned = true;
  const ex = macd3segExitOf(tradeId);
  const long = e.kind === 'buy';
  const sumObj = macd3segCurrentSummary();
  const dirSt = sumObj ? (long ? sumObj.long : sumObj.short) : null;
  const allSt = sumObj ? sumObj.all : null;
  const initRisk = e.init_risk != null ? e.init_risk : (long ? (e.price - e.stop_price) / e.price : (e.stop_price - e.price) / e.price);
  const row = s => `<tr><td>${s.tag}</td><td>${s.start_date} ~ ${s.end_date}</td><td>${fmt(s.wick)}<small> (${s.price_date})</small></td><td>${m3sFmtHist(s.hist_peak)}<small> (${s.hist_date})</small></td></tr>`;
  const ret = ex ? (long ? ex.price / e.price - 1 : 1 - ex.price / e.price) : null;
  const symTitle = macd3segSymDisplayName(currentSym);

  el.innerHTML = `<div class="c67-panel-head">
      <div><b style="color:${long ? M3S_COL.buy : M3S_COL.sell}">${e.label}${e.attempt > 1 ? ' · 二次入场' : ''}</b>
      <small style="color:var(--muted);margin-left:6px">${symTitle} · ${activeTF}</small></div>
      <button class="btn" data-m3s-close>×</button>
    </div>
    <div class="ann-grid">
      <span>确认收盘</span><b>${e.date}</b>
      <span>入场价 (收盘)</span><b>${fmt(e.price)}</b>
      <span>硬止损 ${long ? 'L3' : 'P3'}</span><b style="color:${M3S_COL.stop}">${fmt(e.stop_price)} (${m3sPct(initRisk, 2)})</b>
      <span>本单最大浮盈</span><b style="color:var(--up)">${e.mfe50 != null ? `+${m3sPct(e.mfe50, 2)} (${m3sR(e.r_mult50)})` : '—'}</b>
      <span>离场结果</span><b>${ex ? `${ex.date} ${ex.label} @${fmt(ex.price)} (${(ret * 100).toFixed(1)}%)` : '持有中 / 未到截止'}</b>
    </div>
    ${dirSt && dirSt.n ? `
    <div class="m3s-card-sec">${symTitle} · <span style="color:${long ? M3S_COL.buy : M3S_COL.sell}">${e.label}</span> 历史统计 (共 ${dirSt.n} 笔 / 全部背离 ${allSt.n} 笔)</div>
    <div class="m3s-stat-grid">
      <div class="m3s-stat-cell"><span>次日(T+1)止损率</span><b style="color:${dirSt.next_stop_rate <= 0.30 ? 'var(--up)' : 'var(--warn)'}">${m3sPct(dirSt.next_stop_rate)}</b><small>全部 ${m3sPct(allSt.next_stop_rate)}</small></div>
      <div class="m3s-stat-cell"><span>真实止损率(未达1R)</span><b style="color:${dirSt.true_stop_rate <= 0.52 ? 'var(--up)' : 'var(--warn)'}">${m3sPct(dirSt.true_stop_rate)}</b><small>全部 ${m3sPct(allSt.true_stop_rate)}</small></div>
      <div class="m3s-stat-cell"><span>全样本均最大盈利</span><b style="color:var(--accent)">+${m3sPct(dirSt.mfe50_mean, 2)}</b><small>全部 +${m3sPct(allSt.mfe50_mean, 2)}</small></div>
      <div class="m3s-stat-cell"><span>有效单15/30根MFE</span><b style="color:var(--up)">+${m3sPct(dirSt.valid_mfe15_mean, 1)} / +${m3sPct(dirSt.valid_mfe30_mean, 1)}</b><small>剔除次日止损(${dirSt.valid_n}笔)</small></div>
      <div class="m3s-stat-cell"><span>有效单50根均最大盈利</span><b style="color:var(--up)">+${m3sPct(dirSt.valid_mfe50_mean, 2)}</b><small>中位 +${m3sPct(dirSt.valid_mfe50_med, 2)}</small></div>
      <div class="m3s-stat-cell"><span>有效单最大盈亏比</span><b style="color:var(--warn)">${m3sR(dirSt.valid_r_mean)}</b><small>≥2R率 ${m3sPct(dirSt.ge2r_rate)}</small></div>
    </div>` : ''}
    <table class="m3s-tbl"><thead><tr><th>段</th><th>区间</th><th>${long ? '低点' : '高点'}</th><th>柱峰值</th></tr></thead><tbody>${(e.segments || []).map(row).join('')}</tbody></table>`;
  el.hidden = false;
  const box = document.getElementById('chart').getBoundingClientRect();
  const x = point ? box.left + point.x + 16 : box.left + box.width / 2 - 190;
  const y = point ? box.top + point.y - 30 : box.top + 60;
  el.style.left = Math.max(8, Math.min(innerWidth - (el.offsetWidth || 410) - 8, x)) + 'px';
  el.style.top = Math.max(8, Math.min(innerHeight - (el.offsetHeight || 310) - 8, y)) + 'px';
  el.querySelector('[data-m3s-close]').onclick = () => { MACD3SEG.pinned = false; el.hidden = true; };
}

// ---------------------------------------------------------------- 主图右上角当前品种背离统计卡片 --
function macd3segRenderSymCard() {
  const el = MACD3SEG.symCard;
  if (!el) return;
  if (!MACD3SEG.on) { el.hidden = true; return; }
  const sum = macd3segCurrentSummary();
  if (!sum || !sum.all) { el.hidden = true; return; }
  el.hidden = false;
  const symTitle = macd3segSymDisplayName(currentSym);
  const a = sum.all, l = sum.long, s = sum.short;
  if (MACD3SEG.cardMin) {
    el.innerHTML = `<div class="m3s-sc-head" style="margin:0;border:none;padding:0">
      <span><b style="color:${M3S_COL.seg}">三段背离</b> · ${symTitle} (${a.n}笔 · 有效MFE <b style="color:var(--up)">+${m3sPct(a.valid_mfe50_mean, 1)}</b>)</span>
      <div style="display:flex;gap:4px">
        <button class="m3s-mini-btn" data-m3s-wall>全品种卡片墙</button>
        <button class="m3s-mini-btn" data-m3s-min title="展开品种背离统计卡片">展开 ▾</button>
      </div>
    </div>`;
  } else {
    const colRow = (label, col, st) => `
      <tr>
        <td style="color:${col};font-weight:700">${label} (${st.n})</td>
        <td>${m3sPct(st.init_risk_mean, 2)}</td>
        <td style="color:${st.next_stop_rate <= 0.30 ? 'var(--up)' : 'var(--warn)'}">${m3sPct(st.next_stop_rate)}</td>
        <td style="color:${st.true_stop_rate <= 0.52 ? 'var(--up)' : 'var(--warn)'}">${m3sPct(st.true_stop_rate)}</td>
        <td style="color:var(--accent)">+${m3sPct(st.mfe50_mean, 2)}</td>
        <td style="color:var(--up);font-weight:700">+${m3sPct(st.valid_mfe15_mean, 1)} / +${m3sPct(st.valid_mfe30_mean, 1)} / +${m3sPct(st.valid_mfe50_mean, 1)}</td>
        <td style="color:var(--warn);font-weight:700">${m3sR(st.valid_r_mean)}</td>
      </tr>`;
    el.innerHTML = `<div class="m3s-sc-head">
        <div><b style="color:${M3S_COL.seg}">MACD三段背离 · 品种表现卡</b> <span style="color:var(--muted)">${symTitle} (${activeTF})</span></div>
        <div style="display:flex;gap:4px">
          <button class="m3s-mini-btn active" data-m3s-wall title="查看美股/黄金/A股/加密全部品种止损率与最大盈利率卡片墙">全品种卡片墙 ⊞</button>
          <button class="m3s-mini-btn" data-m3s-min title="折叠卡片">收起 ▴</button>
        </div>
      </div>
      <table class="m3s-sc-tbl">
        <thead>
          <tr>
            <th>方向 (笔数)</th>
            <th title="入场价到第三段极值(L3/P3)的平均初始止损距离">均止损宽</th>
            <th title="T+1收盘柱未继续缩短或盘中触及硬止损的比例">次日止损率</th>
            <th title="次日止损 + 50根内未达1R盈利即触及硬止损的比例">真实止损率</th>
            <th title="含次日止损单在内的50根触损前平均最大浮盈">全样本均MFE</th>
            <th title="剔除次日止损后有效持仓单在15/30/50根内的平均最大盈利率">有效单最大盈利率 (15/30/50根)</th>
            <th title="有效持仓单50根平均最大盈亏比 (MFE50 / 初始止损1R)">均盈亏比</th>
          </tr>
        </thead>
        <tbody>
          ${colRow('全部背离', 'var(--ink)', a)}
          ${colRow('3底买(多)', 'var(--up)', l)}
          ${colRow('3顶卖(空)', 'var(--down)', s)}
        </tbody>
      </table>`;
  }
  const minBtn = el.querySelector('[data-m3s-min]');
  if (minBtn) minBtn.onclick = () => {
    MACD3SEG.cardMin = !MACD3SEG.cardMin;
    try { localStorage.setItem('chanlun.macd3seg.cardMin', MACD3SEG.cardMin ? '1' : '0'); } catch (_) {}
    macd3segRenderSymCard();
  };
  const wallBtn = el.querySelector('[data-m3s-wall]');
  if (wallBtn) wallBtn.onclick = () => {
    MACD3SEG.open = true;
    MACD3SEG.panelTab = 'cards';
    macd3segRefresh();
  };
}

// ---------------------------------------------------------------- 全品种卡片墙 & 信号面板 --
function macd3segBuildCardWallHtml() {
  const allSeries = (window.MACD3SEG_DATA && window.MACD3SEG_DATA.series) || {};
  const keys = Object.keys(allSeries).filter(k => k.endsWith('|1D'));
  const markets = ['ALL', '黄金/贵金属', '美股', 'A股', '港股/其他', '加密'];
  const filterTabs = markets.map(m => {
    const label = m === 'ALL' ? `全部品种 (${keys.length})` : m;
    return `<button class="m3s-tab-btn ${MACD3SEG.marketFilter === m ? 'active' : ''}" data-m3s-mkt="${m}">${label}</button>`;
  }).join('');

  const filtered = keys.filter(k => {
    if (MACD3SEG.marketFilter === 'ALL') return true;
    const mkt = (allSeries[k].meta && allSeries[k].meta.market) || '';
    return mkt === MACD3SEG.marketFilter;
  });

  const cards = filtered.map(k => {
    const item = allSeries[k];
    const meta = item.meta || {};
    const sym = meta.sym || k.split('|')[0];
    const name = meta.name || sym;
    const mkt = meta.market || '';
    const a = (item.summary && item.summary.all) || {};
    const l = (item.summary && item.summary.long) || {};
    const s = (item.summary && item.summary.short) || {};
    const isCur = sym === currentSym;
    return `<div class="m3s-sym-card-item ${isCur ? 'current' : ''}" data-m3s-goto="${sym}" title="点击切换到 ${name} 图表并查看所有三段背离买卖点">
      <div class="m3s-sci-head">
        <div><b>${name}</b> <span class="m3s-mkt-tag">${mkt}</span></div>
        <span class="m3s-sci-cnt">共 <b>${a.n || 0}</b> 笔 <small>(<span style="color:var(--up)">买${l.n || 0}</span>/<span style="color:var(--down)">卖${s.n || 0}</span>)</small></span>
      </div>
      <div class="m3s-sci-grid">
        <div><span>次日(T+1)止损率</span><b style="color:${(a.next_stop_rate || 0) <= 0.30 ? 'var(--up)' : 'var(--warn)'}">${m3sPct(a.next_stop_rate)}</b></div>
        <div><span>真实止损率(未达1R)</span><b style="color:${(a.true_stop_rate || 0) <= 0.53 ? 'var(--up)' : 'var(--warn)'}">${m3sPct(a.true_stop_rate)}</b></div>
        <div><span>均初始止损(1R)</span><b>${m3sPct(a.init_risk_mean, 2)}</b></div>
        <div><span>全样本均最大盈利</span><b style="color:var(--accent)">+${m3sPct(a.mfe50_mean, 2)}</b></div>
        <div><span>有效单均最大盈利</span><b style="color:var(--up)">+${m3sPct(a.valid_mfe50_mean, 2)}</b></div>
        <div><span>有效单最大盈亏比</span><b style="color:var(--warn)">${m3sR(a.valid_r_mean)}</b></div>
      </div>
      <div class="m3s-sci-foot">
        <span><b style="color:var(--up)">3底买(${l.n || 0}笔)</b>：真实损 <b>${m3sPct(l.true_stop_rate)}</b> · 有效MFE <b style="color:var(--up)">+${m3sPct(l.valid_mfe50_mean, 2)}</b> (${m3sR(l.valid_r_mean)})</span>
        <span><b style="color:var(--down)">3顶卖(${s.n || 0}笔)</b>：真实损 <b>${m3sPct(s.true_stop_rate)}</b> · 有效MFE <b style="color:var(--down)">+${m3sPct(s.valid_mfe50_mean, 2)}</b> (${m3sR(s.valid_r_mean)})</span>
      </div>
    </div>`;
  }).join('');

  return `<div class="m3s-filter-bar">${filterTabs}</div>
    <div class="m3s-card-wall-grid">${cards || '<p class="note">暂无匹配品种数据。</p>'}</div>`;
}

function macd3segRefresh() {
  const symTf = `${currentSym}|${activeTF}`;
  if (MACD3SEG._lastSymTf && MACD3SEG._lastSymTf !== symTf) {
    MACD3SEG.sel = null;
    MACD3SEG.pinned = false;
    if (MACD3SEG.popup) MACD3SEG.popup.hidden = true;
  }
  MACD3SEG._lastSymTf = symTf;
  const btn = document.getElementById('macd3segToggle'), list = document.getElementById('macd3segList'), panel = document.getElementById('macd3segPanel');
  if (!btn) return;
  const s = macd3segSeries(), entries = macd3segEntries();
  btn.classList.toggle('active', MACD3SEG.on);
  btn.setAttribute('aria-pressed', String(MACD3SEG.on));
  btn.textContent = MACD3SEG.on ? `三段背离 ${entries.length}` : '三段背离';
  list.classList.toggle('active', MACD3SEG.open);
  list.textContent = `背离卡片墙/列表`;
  panel.hidden = !MACD3SEG.open;
  macd3segRenderSymCard();

  if (MACD3SEG.open) {
    panel.style.top = Math.ceil(document.querySelector('header').getBoundingClientRect().bottom + 8) + 'px';
    const symTitle = macd3segSymDisplayName(currentSym);
    panel.querySelector('[data-m3s-title]').textContent = `MACD三段背离 · ${symTitle} (${activeTF}) · 共 ${entries.length} 次信号`;
    const tabCardsBtn = panel.querySelector('[data-m3s-ptab="cards"]');
    const tabEntriesBtn = panel.querySelector('[data-m3s-ptab="entries"]');
    if (tabCardsBtn) tabCardsBtn.classList.toggle('active', MACD3SEG.panelTab === 'cards');
    if (tabEntriesBtn) {
      tabEntriesBtn.classList.toggle('active', MACD3SEG.panelTab === 'entries');
      tabEntriesBtn.textContent = `当前品种信号明细 (${entries.length})`;
    }
    const bodyEl = panel.querySelector('[data-m3s-entries]');
    if (MACD3SEG.panelTab === 'cards') {
      bodyEl.innerHTML = macd3segBuildCardWallHtml();
    } else {
      bodyEl.innerHTML = !s
        ? '<p class="note">当前品种/周期暂无 K 线数据。</p>'
        : entries.slice().reverse().map(e => {
          const ex = macd3segExitOf(e.trade_id);
          const tag = ex && (ex.kind === 'stop_next' || ex.kind === 'stop_hard') && ex.bar === e.bar + 1 ? '次日止损' : (ex ? ex.label : '进行中');
          const mfeTxt = e.mfe50 != null ? ` · 最大浮盈 +${m3sPct(e.mfe50, 1)}(${m3sR(e.r_mult50)})` : '';
          return `<button class="c67-entry" data-m3s-id="${e.trade_id}" aria-current="${MACD3SEG.sel === e.trade_id}">
            <span style="color:${e.kind === 'buy' ? M3S_COL.buy : M3S_COL.sell}">${e.label}${e.attempt > 1 ? '②' : ''}</span><b>${e.date}</b>
            <small>入场 ${fmt(e.price)} · 止损 ${fmt(e.stop_price)} · ${tag}${mfeTxt}</small></button>`;
        }).join('') || '<p class="note">截止时刻之前没有信号。</p>';
    }
  }
  for (const p of MACD3SEG.prims || []) p.requestUpdate();
}
function macd3segSelect(tradeId) {
  const e = macd3segEntries().find(x => x.trade_id === tradeId);
  if (!e) return;
  MACD3SEG.sel = tradeId;
  candleSeries.setMarkers(buildMarkers(activeView));
  const cs = candlesFor(activeView);
  const j = typeof cover67IndicatorBar === 'function' ? cover67IndicatorBar((e.segments && e.segments[0] ? e.segments[0].start_time : e.time), cs) : -1;
  const k = typeof cover67IndicatorBar === 'function' ? cover67IndicatorBar(e.time, cs) : -1;
  if (j >= 0 && k >= 0) {
    const apply = () => chart.timeScale().setVisibleLogicalRange({ from: j - 10, to: k + 30 });
    apply(); requestAnimationFrame(apply); setTimeout(apply, 160);
  }
  macd3segRefresh();
}
function macd3segEnable(on) {
  MACD3SEG.on = !!on;
  if (!on) { MACD3SEG.open = false; MACD3SEG.sel = null; MACD3SEG.pinned = false; if (MACD3SEG.popup) MACD3SEG.popup.hidden = true; }
  try { localStorage.setItem('chanlun.macd3seg.v3', on ? 'on' : 'off'); } catch (_) {}
  candleSeries.setMarkers(buildMarkers(activeView));
  macd3segRefresh();
}

(function () {
  const bar = document.getElementById('indicatorToggle');
  if (!bar) return;
  const btn = document.createElement('button');
  btn.id = 'macd3segToggle'; btn.textContent = '三段背离';
  btn.title = 'MACD柱形图三段顶/底背离（半木夏）：3底买/3顶卖、次日动能止损、硬止损线、三段连线与品种统计卡';
  const list = document.createElement('button');
  list.id = 'macd3segList'; list.textContent = '背离卡片墙/列表';
  list.title = '查看美股、黄金、A股、加密各品种独立计算的止损率与最大盈利率统计卡片墙及交易信号明细';
  bar.appendChild(btn); bar.appendChild(list);

  // 主图右上角常驻的当前品种背离统计小卡片
  const chartBox = document.getElementById('chart');
  if (chartBox) {
    const sc = document.createElement('aside');
    sc.id = 'macd3segSymCard';
    sc.className = 'm3s-sym-float-card';
    sc.hidden = true;
    chartBox.appendChild(sc);
    MACD3SEG.symCard = sc;
  }

  const panel = document.createElement('section');
  panel.id = 'macd3segPanel'; panel.className = 'c67-indicator-panel m3s-wide-panel'; panel.hidden = true;
  panel.innerHTML = `<div class="c67-panel-head">
      <b data-m3s-title></b>
      <div style="display:flex;gap:6px;align-items:center">
        <button class="m3s-tab-btn active" data-m3s-ptab="cards">全品种统计卡片墙 (黄金/美股/A股)</button>
        <button class="m3s-tab-btn" data-m3s-ptab="entries">当前品种信号明细</button>
        <button class="btn" data-m3s-hide>×</button>
      </div>
    </div>
    <p class="note">规则：价格三次新低(高) + 柱子两次缩短；确认缩短当根收盘入场；第三极值硬止损；次日柱子未继续缩短即止损。点击下方任意品种卡片即可切换图表。<a href="docs/macd-3seg-divergence.html" target="_blank" rel="noopener">算法说明 ↗</a></p>
    <div class="c67-entries m3s-panel-body" data-m3s-entries></div>`;
  document.body.appendChild(panel);

  const pop = document.createElement('section');
  pop.className = 'c67-volume-popup m3s-popup'; pop.hidden = true;
  document.body.appendChild(pop);
  MACD3SEG.popup = pop;

  const st = document.createElement('style');
  st.textContent = `
    .m3s-tbl{width:100%;border-collapse:collapse;font-size:11px;margin:6px 0;font-variant-numeric:tabular-nums}
    .m3s-tbl th,.m3s-tbl td{padding:3px 5px;border-bottom:1px solid var(--border);text-align:left}.m3s-tbl small{color:var(--muted)}
    .m3s-popup{max-width:440px;z-index:1250}
    .m3s-card-sec{font-size:11px;font-weight:700;color:var(--ink-2);margin:8px 0 4px;padding-top:6px;border-top:1px solid var(--border)}
    .m3s-stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-bottom:6px}
    .m3s-stat-cell{background:var(--surface-2);border:1px solid var(--border);border-radius:6px;padding:4px 6px;text-align:center}
    .m3s-stat-cell span{display:block;font-size:10px;color:var(--muted)}
    .m3s-stat-cell b{display:block;font-size:12px;font-variant-numeric:tabular-nums;margin:1px 0;color:var(--ink)}
    .m3s-stat-cell small{display:block;font-size:9.5px;color:var(--muted)}
    .m3s-sym-float-card{position:absolute;top:36px;left:12px;z-index:18;background:color-mix(in srgb,var(--surface) 92%,transparent);border:1px solid var(--border-strong);border-radius:8px;padding:7px 10px;color:var(--ink);font-size:11px;backdrop-filter:blur(8px);box-shadow:var(--shadow);max-width:calc(100% - 90px);pointer-events:auto}
    .m3s-sc-head{display:flex;justify-content:space-between;align-items:center;gap:10px;padding-bottom:5px;margin-bottom:5px;border-bottom:1px solid var(--border)}
    .m3s-sc-tbl{border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums}
    .m3s-sc-tbl th,.m3s-sc-tbl td{padding:3px 7px;text-align:right;border-bottom:1px solid var(--border)}
    .m3s-sc-tbl th:first-child,.m3s-sc-tbl td:first-child{text-align:left;padding-left:2px}
    .m3s-sc-tbl th{color:var(--muted);font-weight:600;font-size:10px}
    .m3s-mini-btn,.m3s-tab-btn{background:var(--panel);color:var(--ink-2);border:1px solid var(--border);border-radius:5px;padding:2px 8px;font-size:11px;cursor:pointer}
    .m3s-mini-btn:hover,.m3s-tab-btn:hover,.m3s-mini-btn.active,.m3s-tab-btn.active{background:var(--accent-soft);color:var(--accent);border-color:var(--accent-border)}
    .m3s-wide-panel{width:min(860px,calc(100vw - 24px)) !important;max-height:80vh !important;display:flex;flex-direction:column}
    .m3s-panel-body{overflow-y:auto;max-height:calc(80vh - 95px);padding-right:4px}
    .m3s-filter-bar{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px}
    .m3s-card-wall-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(255px,1fr));gap:8px}
    .m3s-sym-card-item{background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;cursor:pointer;transition:border-color 0.15s,transform 0.12s}
    .m3s-sym-card-item:hover{border-color:var(--accent);transform:translateY(-1px)}
    .m3s-sym-card-item.current{border-color:var(--up);box-shadow:0 0 0 1px var(--up-border)}
    .m3s-sci-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;padding-bottom:4px;border-bottom:1px solid var(--border);font-size:12px}
    .m3s-mkt-tag{font-size:10px;padding:1px 5px;border-radius:4px;background:var(--panel);color:var(--muted);margin-left:4px}
    .m3s-sci-cnt{font-size:11px;color:var(--ink-2)}
    .m3s-sci-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin-bottom:6px}
    .m3s-sci-grid>div{background:var(--plane);border:1px solid var(--border);border-radius:5px;padding:3px 5px;text-align:center}
    .m3s-sci-grid span{display:block;font-size:9.5px;color:var(--muted)}
    .m3s-sci-grid b{font-size:11.5px;font-variant-numeric:tabular-nums;color:var(--ink)}
    .m3s-sci-foot{display:flex;flex-direction:column;gap:2px;font-size:10.5px;color:var(--muted);border-top:1px solid var(--border);padding-top:4px}
  `;
  document.head.appendChild(st);

  btn.addEventListener('click', () => macd3segEnable(!MACD3SEG.on));
  list.addEventListener('click', () => { if (!MACD3SEG.on) macd3segEnable(true); MACD3SEG.open = !MACD3SEG.open; macd3segRefresh(); });
  panel.querySelector('[data-m3s-hide]').addEventListener('click', () => { MACD3SEG.open = false; macd3segRefresh(); });
  panel.addEventListener('click', async ev => {
    const ptab = ev.target.closest('[data-m3s-ptab]');
    if (ptab) {
      MACD3SEG.panelTab = ptab.dataset.m3sPtab;
      macd3segRefresh();
      return;
    }
    const mktBtn = ev.target.closest('[data-m3s-mkt]');
    if (mktBtn) {
      MACD3SEG.marketFilter = mktBtn.dataset.m3sMkt;
      macd3segRefresh();
      return;
    }
    const cardItem = ev.target.closest('[data-m3s-goto]');
    if (cardItem) {
      const targetSym = cardItem.dataset.m3sGoto;
      if (!MACD3SEG.on) MACD3SEG.on = true;
      if (typeof switchSymbol === 'function' && targetSym !== currentSym) {
        await switchSymbol(targetSym);
      }
      candleSeries.setMarkers(buildMarkers(activeView));
      macd3segRefresh();
      return;
    }
    const b = ev.target.closest('[data-m3s-id]');
    if (b) {
      macd3segSelect(Number(b.dataset.m3sId));
      macd3segPopup(Number(b.dataset.m3sId), null, true);
    }
  });

  MACD3SEG.prims = [];
  if (typeof candleSeries !== 'undefined') {
    const p = new Macd3SegPrimitive(candleSeries, chart, 'main');
    candleSeries.attachPrimitive(p); MACD3SEG.prims.push(p);
  }
  if (typeof macdHistSeries !== 'undefined' && typeof macdChart !== 'undefined') {
    const p = new Macd3SegPrimitive(macdHistSeries, macdChart, 'macd');
    macdHistSeries.attachPrimitive(p); MACD3SEG.prims.push(p);
  }
  chart.subscribeCrosshairMove(param => {
    if (!MACD3SEG.on || MACD3SEG.pinned || !param || !param.point) return;
    const id = param.hoveredObjectId;
    if (typeof id === 'string' && id.startsWith('m3s:')) {
      const tid = Number(id.slice(4));
      if (MACD3SEG.sel !== tid || (MACD3SEG.popup && MACD3SEG.popup.hidden)) {
        MACD3SEG.sel = tid;
        macd3segPopup(tid, param.point, false);
      }
    }
  });
  chart.subscribeClick(param => {
    if (!MACD3SEG.on || (typeof annChartPickBusy === 'function' && annChartPickBusy())) return;
    const id = param && param.hoveredObjectId;
    if (typeof id === 'string' && id.startsWith('m3s:')) {
      const tid = Number(id.slice(4));
      MACD3SEG.sel = tid;
      candleSeries.setMarkers(buildMarkers(activeView));
      macd3segRefresh();
      macd3segPopup(tid, param.point, true);
    } else if (MACD3SEG.pinned && MACD3SEG.popup && !MACD3SEG.popup.hidden) {
      MACD3SEG.pinned = false;
      MACD3SEG.popup.hidden = true;
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && MACD3SEG.popup) {
      MACD3SEG.pinned = false;
      MACD3SEG.popup.hidden = true;
    }
  });
  window.addEventListener('resize', macd3segRefresh);
  if (new URLSearchParams(location.search).get('macd3seg') === '1') MACD3SEG.on = true;
  candleSeries.setMarkers(buildMarkers(activeView));
  macd3segRefresh();
})();
