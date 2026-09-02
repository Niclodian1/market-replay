// js/00-app-core.js — 全局数据句柄 / 周期标签 / 品种切换
"use strict";
let DATA = window.MARKET_DATA;
const QP = new URLSearchParams(location.search);
// 回测交易层仅 BTC 生成(trades_data.js), 其余品种切换时清空避免残留
window._BTC_TRADES = window.TRADES_DATA || null;
let currentSym = 'BTC';

// 周期标签页按当前品种可用级别重建(股票/黄金等仅有 1D); 分钟级分片周期异步探测追加
function syncTfTabs(){
  const tfs = (DATA.meta.timeframes && DATA.meta.timeframes.length) ? [...DATA.meta.timeframes] : ['1D'];
  if (!tfs.includes(activeTF)) activeTF = tfs[0];   // 新品种无此级别(如从 BTC 15F 切到股票)时回退
  renderTfTabs(tfs);
  refreshChunkTabs();
}
function renderTfTabs(tfs){
  document.getElementById('tfToggle').innerHTML =
    tfs.map(tf => `<button data-tf="${tf}"${tf === activeTF ? ' class="active"' : ''}${CHUNK_TFS.includes(tf) ? ' title="裸K·指标级(无笔计算), 仅复盘/练习"' : ''}>${tf}</button>`).join('');
}
async function refreshChunkTabs(){
  const sym = currentSym;
  if (!CHUNK_SYMBOLS.includes(sym)) return;
  const extra = [];
  for (const tf of CHUNK_TFS){
    const man = await ChunkStore.manifest(sym, tf);
    if (man && man.total >= TR.LOOKBACK + TR.maxStep + 5) extra.push(tf);   // 满足练习器最小窗口才显示
  }
  if (sym !== currentSym || !extra.length) return;
  const box = document.getElementById('tfToggle');
  if ([...box.querySelectorAll('button')].some(b => extra.includes(b.dataset.tf))) return;
  box.insertAdjacentHTML('beforeend',
    extra.map(tf => `<button data-tf="${tf}"${tf === activeTF ? ' class="active"' : ''} title="裸K·指标级(无笔计算), 仅复盘/练习">${tf}</button>`).join(''));
}

// 品种切换: 动态加载 data_<sym>.js 并整体刷新
async function switchSymbol(sym, force){
  if (!force && currentSym === sym) return;
  stopPlay();
  try {
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      // BTC 主数据固定为 data.js(与默认加载同一份), 其余品种为 data_<sym>.js
      s.src = sym === 'BTC' ? 'data.js' : 'data_' + sym.toLowerCase() + '.js';
      s.onload = res; s.onerror = () => rej(new Error('加载 ' + s.src + ' 失败'));
      document.head.appendChild(s);
    });
    DATA = window['MARKET_DATA'];
    currentSym = sym;
    ChunkStore.win = null;                          // 切品种释放分钟级窗口
    document.body.classList.remove('tf-chunked');
    document.getElementById('replayDate').type = 'date';
    window.TRADES_DATA = sym === 'BTC' ? window._BTC_TRADES : null;
    const sel = document.getElementById('symSelect');
    if (sel.value !== sym) sel.value = sym;
    replayT = null;
    document.getElementById('replayCtl').style.display = 'none';
    document.getElementById('replayBtn').textContent = '进入复盘';
    document.getElementById('replayBtn').classList.remove('active');
    syncTfTabs();
    chart.applyOptions(chartOptions());
    volumeChart.applyOptions({ ...chartOptions(), grid: { vertLines: { visible: false }, horzLines: { color: t().grid } }, timeScale: { ...chartOptions().timeScale, visible: false } });
    macdChart.applyOptions({ ...chartOptions(), grid: { vertLines: { visible: false }, horzLines: { color: t().grid } }, timeScale: { ...chartOptions().timeScale, visible: false } });
    document.title = DATA.meta.symbol + ' · 缠论多级别联动';
    document.getElementById('symbolBadge').textContent = `${DATA.meta.symbol} · ${activeTF}`;
    const ls = document.querySelector('.legend-symbol');
    if (ls) ls.textContent = DATA.meta.symbol;
    refreshAll();
    chart.timeScale().fitContent();
    renderJournal();   // 切品种重建持仓卡与图表侧持仓线
    if (positions.length) flashBtn('snapshotBtn', '⚠ 有持仓, 结算需切回原品种/周期');
    refreshChunkTabs();   // 异步探测并追加分片周期按钮(15F)
  } catch (e) { alert(e.message); }
}
document.getElementById('symSelect').addEventListener('change', e => switchSymbol(e.target.value));
