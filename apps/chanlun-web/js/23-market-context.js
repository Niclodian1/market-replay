// js/23-market-context.js — 「最小充分信息集」历史上下文引擎与主图精简状态带 (#ctxStrip)
//   1) 宏观总阀门温度计：US10Y / DXY / VIX（严格因果，按历史当天读取 + 近 20 交易日趋势箭头）
//   2) 相对强弱 RS：标的 20 日超额收益 vs 基准（NDX / SPX / BTC / 创业板 / 腾讯）
//   3) 周期水位标尺：距 52 周高点回撤 % + 所处阶段（盲测同样可用，纯相对指标不泄露坐标）
//   4) 业绩锚与防雷：历史财报日 E+/E- 标记（可一键开关）+ 距下次财报倒计时（盲测仅提示防雷不泄露日期/业绩）
//   5) 品种分层注册表：S / A 核心主池 (23) vs B / C 观察池 (16)
(function (root) {
  'use strict';

  const EARN_PREF_KEY = 'chanlun.ctx.earn.v1';

  // 品种分层（来自 5 维综合评估：周期 / 流动性 / 技术纯度 / 宏观映射 / 业绩锚定）
  const TIER_MAP = {
    // S 级 · 核心压舱石 (18)
    BTC: 'S', ETH: 'S', SOL: 'S', XAUUSD: 'S', XAGUSD: 'S',
    TLT: 'S', USDJPY: 'S',
    SPX: 'S', NDX: 'S',
    NVDA: 'S', TSM: 'S', META: 'S', TSLA: 'S', MSFT: 'S', AAPL: 'S', GOOGL: 'S', AMZN: 'S', AVGO: 'S',
    // A 级 · 高弹性周期/跨行业龙头/事件主池 (12)
    AMD: 'A', MU: 'A', COIN: 'A', PLTR: 'A', NFLX: 'A',
    LLY: 'A', XOM: 'A', FCX: 'A',
    '0700_HK': 'A', '9988_HK': 'A', N225: 'A', HSI: 'A',
    // B 级 · 慢趋势/风格对照/宽基 (6)
    WMT: 'B', JPM: 'B', BRK_B: 'B', INTC: 'B',
    '399006_SZ': 'B', '000688_SS': 'B',
    // C 级 · 短样本/Meme/T+1停牌特征个股 (10)
    SNDK: 'C', HYPE: 'C', PEPE: 'C',
    '600519_SS': 'C', '300750_SZ': 'C', '002460_SZ': 'C', '600900_SS': 'C',
    '000333_SZ': 'C', '603259_SS': 'C', '300033_SZ': 'C',
  };

  const CORE_SYMBOLS = Object.keys(TIER_MAP).filter(s => TIER_MAP[s] === 'S' || TIER_MAP[s] === 'A');
  const CORE_SET = new Set(CORE_SYMBOLS);

  // 标的 → 相对强弱基准 { key: context.benchmarks key, label }
  const BENCH_MAP = {
    ETH: { key: 'BTC', label: 'BTC' },
    SOL: { key: 'BTC', label: 'BTC' },
    PEPE: { key: 'BTC', label: 'BTC' },
    HYPE: { key: 'BTC', label: 'BTC' },
    BTC: { key: 'NDX', label: 'NDX' },
    NVDA: { key: 'NDX', label: 'NDX' }, TSM: { key: 'NDX', label: 'NDX' },
    AAPL: { key: 'NDX', label: 'NDX' }, MSFT: { key: 'NDX', label: 'NDX' },
    GOOGL: { key: 'NDX', label: 'NDX' }, META: { key: 'NDX', label: 'NDX' },
    TSLA: { key: 'NDX', label: 'NDX' }, AMZN: { key: 'NDX', label: 'NDX' },
    AVGO: { key: 'NDX', label: 'NDX' }, AMD: { key: 'NDX', label: 'NDX' },
    MU: { key: 'NDX', label: 'NDX' }, INTC: { key: 'NDX', label: 'NDX' },
    SNDK: { key: 'NDX', label: 'NDX' }, PLTR: { key: 'NDX', label: 'NDX' },
    COIN: { key: 'NDX', label: 'NDX' }, NFLX: { key: 'NDX', label: 'NDX' },
    LLY: { key: 'SPX', label: 'SPX' }, XOM: { key: 'SPX', label: 'SPX' },
    FCX: { key: 'SPX', label: 'SPX' }, TLT: { key: 'SPX', label: 'SPX' },
    USDJPY: { key: 'SPX', label: 'SPX' }, HSI: { key: 'SPX', label: 'SPX' },
    WMT: { key: 'SPX', label: 'SPX' }, JPM: { key: 'SPX', label: 'SPX' },
    BRK_B: { key: 'SPX', label: 'SPX' }, XAUUSD: { key: 'SPX', label: 'SPX' },
    XAGUSD: { key: 'SPX', label: 'SPX' }, NDX: { key: 'SPX', label: 'SPX' },
    N225: { key: 'SPX', label: 'SPX' },
    '0700_HK': { key: 'HK', label: 'HSI' },
    '9988_HK': { key: 'HK', label: 'HSI' },
    '600519_SS': { key: 'CN', label: '创业板' }, '300750_SZ': { key: 'CN', label: '创业板' },
    '002460_SZ': { key: 'CN', label: '创业板' }, '600900_SS': { key: 'CN', label: '创业板' },
    '000333_SZ': { key: 'CN', label: '创业板' }, '603259_SS': { key: 'CN', label: '创业板' },
    '300033_SZ': { key: 'CN', label: '创业板' }, '000688_SS': { key: 'CN', label: '创业板' },
  };

  function tsToYmd(ts) {
    const d = new Date(ts * 1000);
    return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
  }

  function ymdToEpoch(ymd) {
    const [y, m, d] = ymd.split('-').map(Number);
    return Date.UTC(y, m - 1, d) / 1000;
  }

  // 二分查找 dates 中 <= target 的最大下标（严格因果，绝不读取未来）
  function leIndex(dates, target) {
    if (!Array.isArray(dates) || !dates.length) return -1;
    let lo = 0, hi = dates.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (dates[mid] <= target) { ans = mid; lo = mid + 1; }
      else hi = mid - 1;
    }
    return ans;
  }

  function macroAt(ts, ctxData) {
    const data = ctxData || root.MARKET_CONTEXT;
    const m = data?.macro;
    if (!m || !Array.isArray(m.dates) || !m.dates.length || ts == null) return null;
    const ymd = typeof ts === 'string' ? ts : tsToYmd(ts);
    const i = leIndex(m.dates, ymd);
    if (i < 0) return null;
    // 若距离最近宏观记录超过 10 天（早于 1980 年等），不显示陈旧数值
    if (Math.abs(ymdToEpoch(ymd) - ymdToEpoch(m.dates[i])) > 86400 * 10) return null;
    const prev = Math.max(0, i - 20);
    const diff = (arr) => (arr[i] != null && arr[prev] != null) ? +(arr[i] - arr[prev]).toFixed(2) : null;
    return {
      date: m.dates[i],
      us10y: m.us10y[i],
      dxy: m.dxy[i],
      vix: m.vix[i],
      d10y: diff(m.us10y),
      dDxy: diff(m.dxy),
      dVix: diff(m.vix),
    };
  }

  function rsAt(sym, ts, bars, barIdx, ctxData) {
    const spec = BENCH_MAP[sym];
    const data = ctxData || root.MARKET_CONTEXT;
    const bench = spec && data?.benchmarks?.[spec.key];
    if (!spec || !bench || !Array.isArray(bars) || barIdx == null || barIdx < 20) return null;
    const cur = bars[barIdx], prev = bars[barIdx - 20];
    if (!cur || !prev || !(prev.close > 0)) return null;
    const yNow = tsToYmd(cur.time), yPrev = tsToYmd(prev.time);
    const biNow = leIndex(bench.d, yNow), biPrev = leIndex(bench.d, yPrev);
    if (biNow < 0 || biPrev < 0 || biNow <= biPrev) return null;
    const bNow = bench.c[biNow], bPrev = bench.c[biPrev];
    if (!(bNow > 0) || !(bPrev > 0)) return null;
    const symRet = (cur.close / prev.close - 1) * 100;
    const benchRet = (bNow / bPrev - 1) * 100;
    return {
      benchKey: spec.key,
      benchLabel: spec.label,
      excess20: +(symRet - benchRet).toFixed(1),
      symRet20: +symRet.toFixed(1),
      benchRet20: +benchRet.toFixed(1),
    };
  }

  function cycleAt(bars, barIdx) {
    if (!Array.isArray(bars) || barIdx == null || barIdx < 20) return null;
    const from = Math.max(0, barIdx - 252);
    let hi = -Infinity, lo = Infinity;
    for (let k = from; k <= barIdx; k++) {
      const b = bars[k];
      if (b.high > hi) hi = b.high;
      if (b.low < lo) lo = b.low;
    }
    const close = bars[barIdx]?.close;
    if (!(hi > 0) || !(close > 0)) return null;
    const dd52w = +((close / hi - 1) * 100).toFixed(1);
    const pos52w = hi > lo ? Math.round(((close - lo) / (hi - lo)) * 100) : 50;
    let stage = '回调/震荡', tone = 'muted';
    if (dd52w >= -5) { stage = '新高区'; tone = 'up'; }
    else if (dd52w >= -15) { stage = '强势区'; tone = 'accent'; }
    else if (dd52w <= -30) { stage = '深熊区'; tone = 'down'; }
    return { dd52w, pos52w, hi52w: hi, lo52w: lo, stage, tone };
  }

  function isReportPublicOnDay(row, ymd) {
    // 盘后 (AMC) 财报在当日收盘后才公布，复盘当日收盘 K 线尚未公开，需严格 > 财报日才可见；
    // 盘前/盘中 (BMO) 或旧版无时段字段的记录按 <= 财报日可见。
    const timing = row[4];
    if (timing === 'AMC') return row[0] < ymd;
    return row[0] <= ymd;
  }

  function earningsAt(sym, ts, ctxData) {
    const data = ctxData || root.MARKET_CONTEXT;
    const list = data?.earnings?.[sym];
    if (!Array.isArray(list) || !list.length || ts == null) return null;
    const ymd = typeof ts === 'string' ? ts : tsToYmd(ts);
    const curEpoch = ymdToEpoch(ymd);
    let last = null, next = null;
    for (const row of list) {
      if (isReportPublicOnDay(row, ymd)) last = row;
      else { next = row; break; }
    }
    const daysSinceLast = last ? Math.round((curEpoch - ymdToEpoch(last[0])) / 86400) : null;
    const daysToNext = next ? Math.round((ymdToEpoch(next[0]) - curEpoch) / 86400) : null;
    const nextIsTonightAmc = !!(next && daysToNext === 0 && next[4] === 'AMC');
    return {
      last: last ? { date: last[0], surprise: last[1], reported: last[2], estimate: last[3], timing: last[4] || null, daysAgo: daysSinceLast } : null,
      next: next ? { date: next[0], daysTo: daysToNext, timing: next[4] || null } : null,
      upcomingWarn: (daysToNext != null && daysToNext > 0 && daysToNext <= 5) || nextIsTonightAmc,
      recentReport: last != null && daysSinceLast != null && daysSinceLast >= 0 && daysSinceLast <= 5 && last[1] != null,
    };
  }

  function snapshotFor(sym, ts, bars, barIdx, ctxData) {
    const idx = barIdx != null ? barIdx : (Array.isArray(bars) ? bars.length - 1 : -1);
    const useTs = ts ?? (bars?.[idx]?.time ?? null);
    return {
      sym,
      tier: TIER_MAP[sym] || 'B',
      isCore: CORE_SET.has(sym),
      macro: macroAt(useTs, ctxData),
      rs: rsAt(sym, useTs, bars, idx, ctxData),
      cycle: cycleAt(bars, idx),
      earnings: earningsAt(sym, useTs, ctxData),
    };
  }

  // ---- 浏览器交互层：主图精简状态带 (#ctxStrip) + 财报日图上标记 E+/E- ----
  let earnOverlayOn = true;
  try {
    const saved = localStorage.getItem(EARN_PREF_KEY);
    if (saved === '0') earnOverlayOn = false;
  } catch {}

  function isEarnOverlayOn() { return earnOverlayOn; }
  function setEarnOverlay(on) {
    earnOverlayOn = !!on;
    try { localStorage.setItem(EARN_PREF_KEY, earnOverlayOn ? '1' : '0'); } catch {}
    const btn = root.document?.querySelector('#overlayToggle button[data-ov="earn"]');
    if (btn) {
      btn.classList.toggle('active', earnOverlayOn);
      btn.setAttribute('aria-pressed', String(earnOverlayOn));
    }
    if (typeof candleSeries !== 'undefined' && typeof buildMarkers === 'function' && typeof activeView !== 'undefined') {
      candleSeries.setMarkers(buildMarkers(activeView));
    }
  }
  function toggleEarnOverlay() { setEarnOverlay(!earnOverlayOn); }

  function earningsMarkers(v) {
    if (!earnOverlayOn) return [];
    if (typeof trLocked === 'function' && trLocked()) return [];
    if (typeof activeTF !== 'undefined' && activeTF !== '1D') return [];
    const sym = typeof currentSym !== 'undefined' ? currentSym : 'BTC';
    const list = root.MARKET_CONTEXT?.earnings?.[sym];
    if (!Array.isArray(list) || !list.length) return [];
    const cs = typeof candlesFor === 'function' ? candlesFor(v || 'raw') : [];
    if (!cs.length) return [];
    const maxTs = cs[cs.length - 1].time;
    const minTs = cs[0].time;
    const times = cs.map(c => c.time);
    const out = [];
    const used = new Set();
    for (const row of list) {
      const eTs = ymdToEpoch(row[0]);
      const isAmc = row[4] === 'AMC';
      // 盘后 (AMC) 财报结果仅在财报日之后的下一交易日才已公开，避免复盘在当日收盘前提前看到业绩
      const minShowTs = isAmc ? (eTs + 1) : eTs;
      if (minShowTs < minTs || minShowTs > maxTs) continue;
      let lo = 0, hi = times.length - 1, hit = -1;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (times[mid] >= minShowTs) { hit = mid; hi = mid - 1; }
        else lo = mid + 1;
      }
      if (hit < 0 || times[hit] - eTs > 86400 * 5) continue;
      const barT = times[hit];
      if (used.has(barT)) continue;
      used.add(barT);
      const surp = row[1];
      const color = surp == null ? '#64748b' : (surp >= 0 ? '#089981' : '#f23645');
      const text = surp == null ? 'E' : (surp >= 0 ? 'E+' : 'E-');
      out.push({ time: barT, position: 'belowBar', color, shape: 'circle', text, size: 0.6 });
    }
    return out;
  }

  function arrowOf(delta, th) {
    if (delta == null) return '';
    if (delta >= th) return '↑';
    if (delta <= -th) return '↓';
    return '→';
  }

  function hasEarnings(sym) {
    const s = sym || (typeof currentSym !== 'undefined' ? currentSym : 'BTC');
    const list = root.MARKET_CONTEXT?.earnings?.[s];
    return Array.isArray(list) && list.length > 0;
  }

  function syncEarnButton() {
    const btn = root.document?.querySelector('#overlayToggle button[data-ov="earn"]');
    if (!btn) return;
    const avail = hasEarnings();
    btn.hidden = !avail;
    btn.classList.toggle('active', earnOverlayOn);
    btn.setAttribute('aria-pressed', String(earnOverlayOn));
  }

  function renderStrip(hoverTs) {
    const el = root.document?.getElementById('ctxStrip');
    if (!el) return;
    syncEarnButton();
    if (typeof candlesFor !== 'function') { el.innerHTML = ''; return; }
    const cs = candlesFor(typeof activeView !== 'undefined' ? activeView : 'raw');
    if (!cs.length) { el.innerHTML = ''; return; }
    const locked = typeof trLocked === 'function' && trLocked();
    const sym = typeof currentSym !== 'undefined' ? currentSym : 'BTC';
    let idx = cs.length - 1;
    if (hoverTs != null && typeof idxMap !== 'undefined' && idxMap[hoverTs] != null) {
      idx = idxMap[hoverTs];
    }
    const bar = cs[idx];
    if (!bar) { el.innerHTML = ''; return; }
    const snap = snapshotFor(sym, bar.time, cs, idx);
    const pills = [];

    if (!locked && snap.macro) {
      const m = snap.macro;
      if (m.us10y != null) {
        const arr = arrowOf(m.d10y, 0.18);
        const cls = arr === '↑' ? 'tone-warn' : arr === '↓' ? 'tone-up' : 'tone-muted';
        const tip = `10年期美债收益率 (${m.date})：${m.us10y}%` + (m.d10y != null ? `（20日 ${m.d10y >= 0 ? '+' : ''}${m.d10y}%）。利率上行压制成长股估值，下行利好风险资产。` : '');
        pills.push(`<button type="button" class="ctx-pill ${cls}" data-ctx-head="10年期美债收益率 (10Y)" data-ctx-desc="${tip}" title="${tip}">10Y <b>${m.us10y.toFixed(2)}%</b>${arr}</button>`);
      }
      if (m.dxy != null) {
        const arr = arrowOf(m.dDxy, 1.5);
        const cls = arr === '↑' ? 'tone-warn' : arr === '↓' ? 'tone-up' : 'tone-muted';
        const tip = `美元指数 DXY (${m.date})：${m.dxy}` + (m.dDxy != null ? `（20日 ${m.dDxy >= 0 ? '+' : ''}${m.dDxy}）。美元走强通常收紧全球流动性，走弱利好加密与黄金。` : '');
        pills.push(`<button type="button" class="ctx-pill ${cls}" data-ctx-head="美元指数 (DXY)" data-ctx-desc="${tip}" title="${tip}">DXY <b>${m.dxy.toFixed(1)}</b>${arr}</button>`);
      }
      if (m.vix != null) {
        const cls = m.vix >= 28 ? 'tone-down' : m.vix >= 20 ? 'tone-warn' : m.vix < 15 ? 'tone-up' : 'tone-muted';
        const tip = `VIX 恐慌指数 (${m.date})：${m.vix}` + (m.dVix != null ? `（20日 ${m.dVix >= 0 ? '+' : ''}${m.dVix}）。<15 偏平静，≥20 波动放大，≥28 恐慌区。` : '');
        pills.push(`<button type="button" class="ctx-pill ${cls}" data-ctx-head="标普恐慌指数 (VIX)" data-ctx-desc="${tip}" title="${tip}">VIX <b>${m.vix.toFixed(1)}</b></button>`);
      }
    }

    if (!locked && snap.rs) {
      const r = snap.rs;
      const cls = r.excess20 >= 2 ? 'tone-up' : r.excess20 <= -2 ? 'tone-down' : 'tone-muted';
      const tip = `近 20 交易日相对强弱：本标的 ${r.symRet20 >= 0 ? '+' : ''}${r.symRet20}% vs 基准 ${r.benchLabel} ${r.benchRet20 >= 0 ? '+' : ''}${r.benchRet20}%（超额 ${r.excess20 >= 0 ? '+' : ''}${r.excess20}%）。正值说明强于大盘。`;
      pills.push(`<button type="button" class="ctx-pill ${cls}" data-ctx-head="20日相对强弱 (RS/${r.benchLabel})" data-ctx-desc="${tip}" title="${tip}">RS/${r.benchLabel} <b>${r.excess20 >= 0 ? '+' : ''}${r.excess20}%</b></button>`);
    }

    if (snap.cycle) {
      const c = snap.cycle;
      const tip = `过去 52 周高点 ${c.hi52w.toPrecision(5)} / 低点 ${c.lo52w.toPrecision(5)}，当前距高点 ${c.dd52w}%（位于区间 ${c.pos52w}% 分位 · ${c.stage}）。`;
      pills.push(`<button type="button" class="ctx-pill tone-${c.tone}" data-ctx-head="52周周期水位 (${c.stage})" data-ctx-desc="${tip}" title="${tip}">52W <b>${c.dd52w}%</b> · ${c.stage}</button>`);
    }

    if (snap.earnings) {
      const e = snap.earnings;
      if (locked) {
        // 盲测模式只做防雷预警，绝不泄露具体日期或财报数字
        if (e.upcomingWarn) {
          const tip = '盲测防雷提醒：未来 5 个自然日内将公布财报，留意隔夜跳空风险（盲测隐藏具体日期）。';
          pills.push(`<button type="button" class="ctx-pill tone-warn" data-ctx-head="财报防雷提醒" data-ctx-desc="${tip}" title="${tip}">⚠ 临近财报</button>`);
        }
      } else {
        if (e.recentReport) {
          const s = e.last.surprise;
          const beat = s >= 0;
          const epsInfo = e.last.reported != null && e.last.estimate != null ? `（实际 EPS ${e.last.reported} / 预期 ${e.last.estimate}）` : '';
          const tip = `最近财报日 ${e.last.date}${epsInfo}，业绩${beat ? '超预期' : '不及预期'} ${beat ? '+' : ''}${s}%。`;
          pills.push(`<button type="button" class="ctx-pill ${beat ? 'tone-up' : 'tone-down'}" data-ctx-head="财报业绩 (${beat ? '超预期' : '不及预期'})" data-ctx-desc="${tip}" title="${tip}">E ${beat ? 'Beat' : 'Miss'} <b>${beat ? '+' : ''}${s}%</b></button>`);
        } else if (e.upcomingWarn) {
          const tonight = e.next.daysTo === 0 && e.next.timing === 'AMC';
          const tip = tonight
            ? `今日 (${e.next.date}) 盘后将公布财报，收盘前结果尚未公开，注意隔夜跳空风险。`
            : `下次财报日期 ${e.next.date}（距今 ${e.next.daysTo} 天），注意财报前降仓或防范隔夜跳空。`;
          pills.push(`<button type="button" class="ctx-pill tone-warn" data-ctx-head="临近财报日" data-ctx-desc="${tip}" title="${tip}">⚠ ${tonight ? '今晚盘后财报' : `距财报 <b>${e.next.daysTo}天</b>`}</button>`);
        }
      }
    }

    if (typeof root.closeCtxTip === 'function') root.closeCtxTip();
    el.innerHTML = pills.join('');
    el.hidden = pills.length === 0;
  }

  function setupContextStrip() {
    if (!root.document) return;
    syncEarnButton();
    renderStrip(null);
  }

  const api = {
    TIER_MAP,
    CORE_SYMBOLS,
    isCore: sym => CORE_SET.has(sym),
    tierOf: sym => TIER_MAP[sym] || 'B',
    hasEarnings,
    syncEarnButton,
    tsToYmd,
    macroAt,
    rsAt,
    cycleAt,
    earningsAt,
    snapshotFor,
    earningsMarkers,
    isEarnOverlayOn,
    setEarnOverlay,
    toggleEarnOverlay,
    render: renderStrip,
    setup: setupContextStrip,
  };

  root.MarketContext = api;
  if (typeof module !== 'undefined') module.exports = api;
  if (root.document) {
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', setupContextStrip, { once: true });
    else setupContextStrip();
  }
})(typeof window !== 'undefined' ? window : globalThis);
