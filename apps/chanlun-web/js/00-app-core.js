// js/00-app-core.js — 全局数据句柄 / 周期标签 / 品种切换
"use strict";
let DATA = window.MARKET_DATA;
const QP = new URLSearchParams(location.search);
// 回测交易层仅 BTC 生成(trades_data.js), 其余品种切换时清空避免残留
window._BTC_TRADES = window.TRADES_DATA || null;
let currentSym = 'BTC';

// 周期标签页按品种实际数据动态生成：单日线品种自动降噪为平静徽章，多周期加密品种完整保留 1D/4H/1H 及分片周期
function availableTfsForCurrent() {
  const base = (DATA?.meta?.timeframes && DATA.meta.timeframes.length) ? [...DATA.meta.timeframes] : ['1D'];
  const isChunk = typeof CHUNK_TFS !== 'undefined' && CHUNK_TFS.includes(activeTF);
  if (isChunk && !base.includes(activeTF)) base.push(activeTF);
  return base;
}
function syncTfTabs(){
  const tfs = availableTfsForCurrent();
  const isChunk = typeof CHUNK_TFS !== 'undefined' && CHUNK_TFS.includes(activeTF);
  if (!isChunk && !tfs.includes(activeTF)) activeTF = tfs[0];
  renderTfTabs(tfs);
}
function renderTfTabs(tfs){
  const box = document.getElementById('tfToggle');
  if (!box) return;
  const list = Array.isArray(tfs) && tfs.length ? tfs : availableTfsForCurrent();
  box.classList.toggle('tf-single', list.length <= 1);
  box.innerHTML =
    list.map(tf => `<button data-tf="${tf}"${tf === activeTF ? ' class="active"' : ''}${list.length <= 1 ? ' tabindex="-1" aria-label="当前周期 ' + tf + '"' : ''}>${tf}</button>`).join('');
}
async function refreshChunkTabs(){
  if (typeof CHUNK_SYMBOLS === 'undefined' || !CHUNK_SYMBOLS.includes(currentSym)) return;
  const sym0 = currentSym;
  const base = (DATA?.meta?.timeframes && DATA.meta.timeframes.length) ? [...DATA.meta.timeframes] : ['1D'];
  const extra = [];
  for (const tf of (typeof CHUNK_TFS !== 'undefined' ? CHUNK_TFS : [])) {
    if (base.includes(tf)) continue;
    try {
      const m = await ChunkStore.manifest(sym0, tf);
      if (m && m.total) extra.push(tf);
    } catch {}
  }
  if (currentSym !== sym0) return;
  renderTfTabs([...base, ...extra]);
}

// 品种代码别名归一化(兼容 URL 参数/外部链接/指数符号 ^NDX、纳指、399006.SZ 等)
const SYM_ALIASES = {
  '^NDX': 'NDX', 'NDX': 'NDX', '^IXIC': 'NDX', 'IXIC': 'NDX',
  'NAS100': 'NDX', 'NASDAQ': 'NDX', 'NASDAQ100': 'NDX',
  '纳指': 'NDX', '纳指100': 'NDX', '纳斯达克': 'NDX', '纳斯达克100': 'NDX',
  '^GSPC': 'SPX', 'SPX': 'SPX', 'SP500': 'SPX', 'S&P500': 'SPX',
  '标普': 'SPX', '标普500': 'SPX',
  '^N225': 'N225', 'N225': 'N225', 'NIKKEI': 'N225', '日经': 'N225', '日经225': 'N225',
  '^HSI': 'HSI', 'HSI': 'HSI', '恒指': 'HSI', '恒生': 'HSI', '恒生指数': 'HSI',
  'JPY=X': 'USDJPY', 'USD/JPY': 'USDJPY', 'USD-JPY': 'USDJPY', 'USDJPY': 'USDJPY', '日元': 'USDJPY', '美元日元': 'USDJPY',
  '美债': 'TLT', '20年美债': 'TLT', '长债': 'TLT',
  '台积电': 'TSM', '礼来': 'LLY', '埃克森美孚': 'XOM', '自由港': 'FCX', '铜矿': 'FCX',
  'GC=F': 'XAUUSD', 'XAU/USD': 'XAUUSD', 'XAU-USD': 'XAUUSD', 'GOLD': 'XAUUSD', '黄金': 'XAUUSD', '现货黄金': 'XAUUSD',
  'SI=F': 'XAGUSD', 'XAG/USD': 'XAGUSD', 'XAG-USD': 'XAGUSD', 'SILVER': 'XAGUSD', '白银': 'XAGUSD', '现货白银': 'XAGUSD',
  'BRK-B': 'BRK_B', 'BRK.B': 'BRK_B',
  '0700.HK': '0700_HK', '9988.HK': '9988_HK',
  '000688.SS': '000688_SS', '399006.SZ': '399006_SZ',
  'BTC-USD': 'BTC', 'BTC/USD': 'BTC', 'BTCUSDT': 'BTC', 'BTC/USDT': 'BTC',
  'ETH-USD': 'ETH', 'ETH/USD': 'ETH', 'ETHUSDT': 'ETH', 'ETH/USDT': 'ETH',
  'SOL-USD': 'SOL', 'SOL/USD': 'SOL', 'SOLUSDT': 'SOL', 'SOL/USDT': 'SOL',
};
function normalizeSymKey(raw){
  if (!raw) return 'BTC';
  let s = String(raw).trim().toUpperCase();
  if (SYM_ALIASES[s]) return SYM_ALIASES[s];
  if (SYM_ALIASES[String(raw).trim()]) return SYM_ALIASES[String(raw).trim()];
  if (s.startsWith('^')) s = s.slice(1);
  s = s.replace(/_(1D|4H|1H|15F|15M)$/i, '');
  if (SYM_ALIASES[s]) return SYM_ALIASES[s];
  s = s.replace(/[.\-]/g, '_');
  return SYM_ALIASES[s] || s;
}

// 品种切换: 动态加载 data_<sym>.js 并整体刷新
let _switchSymSeq = 0;
async function switchSymbol(sym, force){
  sym = normalizeSymKey(sym);
  if (!force && currentSym === sym){
    const sel0 = document.getElementById('symSelect');
    if (sel0 && sel0.value !== currentSym) sel0.value = currentSym;
    return;
  }
  const reqId = ++_switchSymSeq;
  stopPlay();
  document.body.classList.add('sym-loading');
  const src = sym === 'BTC' ? 'data-view/data.js' : 'data-view/data_' + sym.toLowerCase() + '.js';
  try {
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      // BTC 主数据固定为 data.js(与默认加载同一份), 其余品种为 data_<sym>.js
      s.src = src;
      s.onload = () => { s.remove(); res(); };
      s.onerror = () => { s.remove(); rej(new Error('加载品种数据失败 (' + src + ')，请确认本地已生成或线上已部署 data-view 数据文件')); };
      document.head.appendChild(s);
    });
    if (reqId !== _switchSymSeq) return;
    if (!window['MARKET_DATA'] || !window['MARKET_DATA'].levels){
      throw new Error('品种数据格式异常 (' + src + ')');
    }
    const prevSym = currentSym;
    DATA = window['MARKET_DATA'];
    currentSym = sym;
    ChunkStore.win = null;                          // 切品种释放分钟级窗口
    document.body.classList.remove('tf-chunked');
    document.getElementById('replayDate').type = 'date';
    window.TRADES_DATA = sym === 'BTC' ? window._BTC_TRADES : null;
    const sel = document.getElementById('symSelect');
    if (sel && sel.value !== sym) sel.value = sym;
    replayT = null;
    document.getElementById('replayCtl').style.display = 'none';
    const rInfo = document.getElementById('replayInfo');
    if (rInfo) rInfo.textContent = '';
    document.getElementById('replayBtn').textContent = '进入复盘';
    document.getElementById('replayBtn').classList.remove('active');
    if (typeof QO !== 'undefined' && QO.open && typeof qoClose === 'function') qoClose();
    if (typeof updateModeBadge === 'function') updateModeBadge();
    if (typeof selectTrade === 'function') selectTrade(null);
    const td = document.getElementById('tradeDetail');
    if (td) td.style.display = 'none';
    window._measure = null;
    if (typeof syncT !== 'undefined') syncT = null;
    if (typeof pointCardEl !== 'undefined' && pointCardEl) pointCardEl.style.display = 'none';
    if (typeof aiMa20HideHoverCard === 'function') aiMa20HideHoverCard(true);
    for (const id of ['markerTip', 'distTag']){
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    }
    if (typeof window.syncSymTrigger === 'function') window.syncSymTrigger();
    syncTfTabs();
    chart.applyOptions(chartOptions());
    volumeChart.applyOptions({ ...chartOptions(), autoSize: false, grid: { vertLines: { visible: false }, horzLines: { color: t().grid } }, timeScale: { ...chartOptions().timeScale, visible: false } });
    macdChart.applyOptions({ ...chartOptions(), autoSize: false, grid: { vertLines: { visible: false }, horzLines: { color: t().grid } }, timeScale: { ...chartOptions().timeScale, visible: false } });
    document.title = DATA.meta.symbol + ' · 行情复盘与逻辑工坊';
    document.getElementById('symbolBadge').textContent = `${DATA.meta.symbol} · ${activeTF}`;
    const ls = document.querySelector('.legend-symbol');
    if (ls) ls.textContent = DATA.meta.symbol;
    if (typeof window.MarketContext?.syncEarnButton === 'function') window.MarketContext.syncEarnButton();
    refreshAll();
    if (typeof window.renderLegendBar === 'function') window.renderLegendBar(null);
    chart.timeScale().fitContent();
    const fitRange = chart.timeScale().getVisibleLogicalRange();
    if (fitRange) {
      volumeChart.timeScale().setVisibleLogicalRange(fitRange);
      macdChart.timeScale().setVisibleLogicalRange(fitRange);
    }
    if (typeof alignPanes === 'function') alignPanes();
    if (typeof window.onDrawSymbolChange === 'function') window.onDrawSymbolChange(prevSym, sym);
    renderJournal();   // 切品种重建持仓卡与图表侧持仓线
    if (typeof window.syncSBgHud === 'function') window.syncSBgHud();
    if (typeof window.renderLogicSidebar === 'function') window.renderLogicSidebar();
    const chartBox = document.querySelector('.chart-box');
    if (chartBox && !window.uiMotion?.prefersReduced?.()) {
      chartBox.classList.remove('sym-swap-in');
      void chartBox.offsetWidth;
      chartBox.classList.add('sym-swap-in');
    }
    if (positions.length) flashBtn('snapshotBtn', '⚠ 有持仓，结算需切回原品种/周期');
  } catch (e) {
    const sel = document.getElementById('symSelect');
    if (sel && sel.value !== currentSym) sel.value = currentSym;
    if (typeof window.showToast === 'function') window.showToast('✗ ' + e.message, 'err', 3600);
    else alert(e.message);
  } finally {
    if (reqId === _switchSymSeq) document.body.classList.remove('sym-loading');
  }
}
document.getElementById('symSelect').addEventListener('change', e => switchSymbol(e.target.value));
