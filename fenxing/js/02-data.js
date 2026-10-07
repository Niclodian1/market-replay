// js/02-data.js — 分钟级分片数据源(ChunkStore) + 全部数据访问器 + 本地MACD
// ---- 分钟级分片数据源(15F 裸K·指标级): 不做笔计算, 只服务复盘/练习 ----
// 数据由 scripts/ops/build_intraday_chunks.py 预切为月度 .bin(6×Float64 小端),
// 前端按 manifest 按需 fetch+解码, 内存中只保留滚动窗口。
const CHUNK_SYMBOLS = ['BTC', 'ETH', 'SOL', 'PEPE'];
// 练习器市场分组(与 symSelect 品种对应)
const MARKET_GROUPS = {
  '加密': ['BTC', 'ETH', 'SOL', 'PEPE', 'HYPE'],
  '美股': ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'TSLA', 'AMZN', 'BRK_B', 'JPM',
           'PLTR', 'COIN', 'AVGO', 'MU', 'WMT', 'AMD', 'NFLX', 'INTC', 'SNDK'],
  '港股': ['0700_HK', '9988_HK'],
  'A股': ['600519_SS', '002460_SZ', '300750_SZ', '600900_SS', '000333_SZ', '603259_SS', '300033_SZ'],
  '贵金属': ['XAUUSD', 'XAGUSD'],
  '指数': ['N225', 'SPX', 'NDX', '399006_SZ', '000688_SS'],
};
const CHUNK_TFS = ['15F'];
const TF_DIR = { '15F': '15m' };
const chunkedActive = () => CHUNK_TFS.includes(activeTF);
const MACD_WARMUP_BARS = 300;   // 指标预热: 窗口前多取的根数(EMA 收敛), 不参与显示

function lowerBoundTime(arr, ts){
  let lo = 0, hi = arr.length - 1, pos = 0;
  while (lo <= hi){
    const m = (lo + hi) >> 1;
    if (arr[m] <= ts){ pos = m; lo = m + 1; } else hi = m - 1;
  }
  return pos;
}

// file:// 等禁用 fetch 的环境: 以 <script src> 注入 .js 变体(与 data_*.js 同机制)
function loadScriptSrc(src){
  return new Promise((res, rej) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => res();
    s.onerror = () => rej(new Error('脚本加载失败 ' + src));
    document.head.appendChild(s);
  });
}
function decodeChunkBuf(buf){
  const n = Math.floor(buf.byteLength / 48);
  const f64 = new Float64Array(buf);
  const out = new Array(n);
  for (let i = 0; i < n; i++){
    const b = k => f64[i * 6 + k];
    out[i] = { time: b(0), open: b(1), high: b(2), low: b(3), close: b(4), volume: b(5) };
  }
  return out;
}

const ChunkStore = {
  manifests: {},   // key "SYM/iv" -> manifest | null
  win: null,       // 活动窗口 {key,sym,tf,sec,times[],candles[],preCloses[],baseIdx,loIdx,hiIdx,total}
  _extending: false,
  _loading: 0,
  _notify(){
    document.body.classList.toggle('chunk-loading', this._loading > 0);
  },

  async manifest(sym, tf){
    const key = `${sym}/${TF_DIR[tf]}`;
    if (this.manifests[key] === undefined){
      try {
        // cb 参数防浏览器缓存旧版分片索引
        const r = await fetch(`intraday/${key}/manifest.json?cb=${Date.now()}`);
        this.manifests[key] = r.ok ? await r.json() : null;
      } catch { this.manifests[key] = null; }
      if (!this.manifests[key]){
        // file:// 下 fetch 被禁 → 回退 <script> 注入的 manifest.js
        try {
          await loadScriptSrc(`intraday/${key}/manifest.js`);
          this.manifests[key] = (window.__CHUNK_MANIFESTS || {})[key] || null;
        } catch { this.manifests[key] = null; }
      }
    }
    return this.manifests[key];
  },

  async _loadChunk(sym, tf, file){
    const base = `intraday/${sym}/${TF_DIR[tf]}/${file}`;
    // 优先 fetch(http 环境, 二进制体积小); file:// 等场景回退 <script> 注入 .js
    try {
      const r = await fetch(base + '?cb=' + Date.now());
      if (r.ok) return decodeChunkBuf(await r.arrayBuffer());
    } catch {}
    const key = `${sym}/${TF_DIR[tf]}/${file.replace(/\.bin$/, '')}`;
    await loadScriptSrc(base.replace(/\.bin$/, '') + '.js');
    const rows = (window.__CHUNKS || {})[key];
    if (!rows) throw new Error('分片数据缺失 ' + key);
    const out = rows.map(r => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4], volume: r[5] }));
    delete window.__CHUNKS[key];   // 释放原始数组内存
    return out;
  },

  async loadWindow(sym, tf, tFrom, tTo){
    this._loading++; this._notify();
    try {
      return await this._loadWindowInner(sym, tf, tFrom, tTo);
    } finally {
      this._loading--; this._notify();
    }
  },
  async _loadWindowInner(sym, tf, tFrom, tTo){
    const man = await this.manifest(sym, tf);
    if (!man || !man.chunks.length) return false;
    let absBefore = 0, lo = -1, hi = -1;
    man.chunks.forEach((ch, i) => {
      if (ch.t1 < tFrom){ absBefore += ch.n; return; }
      if (ch.t0 > tTo) return;
      if (lo < 0) lo = i;
      hi = i;
    });
    if (lo < 0) return false;
    const parts = [];
    for (let i = lo; i <= hi; i++){
      try {
        parts.push(await this._loadChunk(sym, tf, man.chunks[i].file));
      } catch (err){
        console.warn('[ChunkStore] 分片读取失败:', man.chunks[i].file, err);
        return false;
      }
    }
    const bars = parts.flat();
    const di = lowerBoundTime(bars.map(c => c.time), tFrom);   // 预热段 [0, di)
    const display = bars.slice(di).filter(c => c.time <= tTo);
    if (!display.length) return false;
    this.win = {
      key: `${sym}/${TF_DIR[tf]}`, sym, tf, sec: man.sec,
      times: display.map(c => c.time),
      candles: display,
      preCloses: bars.slice(0, di).map(c => c.close),
      baseIdx: absBefore + di,          // 窗口第一根在全集中的绝对下标(trLabel 用)
      loIdx: lo, hiIdx: hi, total: man.total,
    };
    return true;
  },

  async extend(dir){
    // dir=+1 追加更晚一片 / -1 前插更早一片(复盘向左翻历史用)。返回新增根数(0=未加)
    this._loading++; this._notify();
    try {
      return await this._extendInner(dir);
    } finally {
      this._loading--; this._notify();
    }
  },
  async _extendInner(dir){
    const w = this.win; if (!w) return 0;
    const man = await this.manifest(w.sym, w.tf); if (!man) return 0;
    const ni = dir > 0 ? w.hiIdx + 1 : w.loIdx - 1;
    if (ni < 0 || ni >= man.chunks.length) return 0;
    const part = await this._loadChunk(w.sym, w.tf, man.chunks[ni].file);
    if (!part.length) return 0;
    if (dir > 0){
      const lastT = w.times[w.times.length - 1];
      const add = part.filter(c => c.time > lastT);
      if (!add.length) return 0;
      if (w.candles.length + add.length > 900000){ flashBtn('snapshotBtn', '⚠ 已达内存上限'); return 0; }
      w.candles = w.candles.concat(add);
      w.hiIdx = ni;
      return add.length;
    } else {
      const firstT = w.times[0];
      const add = part.filter(c => c.time < firstT);
      if (!add.length) return 0;
      if (w.candles.length + add.length > 900000){ flashBtn('snapshotBtn', '⚠ 已达内存上限'); return 0; }
      w.candles = add.concat(w.candles);
      w.baseIdx -= add.length;
      w.loIdx = ni;
      return add.length;
    }
  },
};

function manifestIndexAt(man, idx){
  // 绝对下标 → 该根K线的时间戳(manifest 前缀累加)
  let acc = 0;
  for (const ch of man.chunks){
    if (idx < acc + ch.n) return ch.t0 + (idx - acc) * man.sec;
    acc += ch.n;
  }
  return man.chunks[man.chunks.length - 1].t1;
}
// 前端本地 MACD(与引擎同口径: EMA 种子=首值, hist=diff-dea)
function macdLocal(closes){
  const ema = (vals, period) => {
    const k = 2 / (period + 1);
    let e = null;
    return vals.map(v => { e = e == null ? v : v * k + e * (1 - k); return e; });
  };
  const e12 = ema(closes, 12), e26 = ema(closes, 26);
  const diff = e12.map((x, i) => x - e26[i]);
  const dea = ema(diff, 9);
  const hist = diff.map((x, i) => x - dea[i]);
  return { diff, dea, hist };
}

// ---- 数据访问器 ----
let replayT = null;   // 复盘截止 epoch(null=完整数据)。所有访问器按此截断,
                      // 截断后的结构 = 因果引擎在该时刻的真实所见(无未来信息)。
const inReplay = () => replayT != null;
const before = ts => !inReplay() || ts <= replayT;
const L = () => DATA.levels[activeTF];
// 笔口径 → 数据键前缀: 老笔链系列以 old* 命名(oldStrokesRaw / oldPivotsStrokeRaw /
// oldBuyPointsSegmentRaw / oldBeichiPointsRaw ...), 与新笔主键一一对应。
const PK = n => strokeStd === 'old' ? 'old' + n.charAt(0).toUpperCase() + n.slice(1) : n;
function candlesFor(v){
  if (chunkedActive()){
    const w = ChunkStore.win;
    if (!w) return [];
    // 复盘/练习截断: 只暴露 replayT 之前的K线(否则盲测泄露未来)
    return inReplay() ? w.candles.filter(c => c.time <= replayT) : w.candles;
  }
  const all = (v === 'raw' ? L().candles : L().mergedCandles).map(c => ({ time:c[0], open:c[1], high:c[2], low:c[3], close:c[4] }));
  return inReplay() ? all.filter(c => c.time <= replayT) : all;
}
function volArrFor(v){
  if (chunkedActive()){
    const w = ChunkStore.win;
    if (!w) return [];
    const arr = w.candles.map(c => c.volume);
    return inReplay() ? arr.filter((_, i) => w.times[i] <= replayT) : arr;
  }
  const arr = v === 'raw' ? L().volume : L().mergedVolume;
  return inReplay() ? arr.filter((_, i) => ((v === 'raw' ? L().candles : L().mergedCandles)[i][0]) <= replayT) : arr;
}
function topMarks(v){ if (chunkedActive()) return []; return (v === 'raw' ? L().topMarkers : L().topMergedMarkers).filter(x => before(x.time)); }
function botMarks(v){ if (chunkedActive()) return []; return (v === 'raw' ? L().bottomMarkers : L().bottomMergedMarkers).filter(x => before(x.time)); }
function strokesFor(v){ if (chunkedActive()) return []; return ((v === 'raw' ? L()[PK('strokesRaw')] : L()[PK('strokesMerged')]) || []).filter(s => before(s[2])); }
function segmentsFor(v){ if (chunkedActive()) return []; return ((v === 'raw' ? L()[PK('segmentsRaw')] : L()[PK('segmentsMerged')]) || []).filter(s => before(s[2])); }
function pivotsFor(v, m){
  if (chunkedActive()) return [];
  const base = m === 'seg' ? 'pivotsSegment' : 'pivotsStroke';
  const all = (v === 'raw' ? L()[PK(base + 'Raw')] : L()[PK(base + 'Merged')]) || [];
  return all.filter(p => before(p[1]));
}
function buysFor(v, m){
  if (chunkedActive()) return [];
  const base = m === 'seg' ? 'buyPointsSegment' : 'buyPointsStroke';
  const all = (v === 'raw' ? L()[PK(base + 'Raw')] : L()[PK(base + 'Merged')]) || [];
  // 仅线段中枢模式加「·段」后缀; 笔中枢模式保持 一买/二买/三买 原名
  return all.filter(b => before(b.confirmedT))
            .map(b => (m === 'seg' ? {...b, k: b.k + '·段'} : {...b}));
}
function sellsFor(v, m){
  if (chunkedActive()) return [];
  const base = m === 'seg' ? 'sellPointsSegment' : 'sellPointsStroke';
  const all = (v === 'raw' ? L()[PK(base + 'Raw')] : L()[PK(base + 'Merged')]) || [];
  return all.filter(s => before(s.confirmedT))
            .map(s => (m === 'seg' ? {...s, k: s.k + '·段'} : {...s}));
}
function alertsFor(v){ if (chunkedActive()) return []; return ((v === 'raw' ? L()[PK('beichiAlertsRaw')] : L()[PK('beichiAlertsMerged')]) || []).filter(x => before(x.alertT ?? x.t)); }
// 背驰买卖点/盘整背驰跟随中枢模式: 笔级(beichiPoints*) 或 线段级(beichiPointsSegment*)
function fastFor(v, m){
  if (chunkedActive()) return [];
  const base = m === 'seg' ? 'beichiPointsSegment' : 'beichiPoints';
  return ((v === 'raw' ? L()[PK(base + 'Raw')] : L()[PK(base + 'Merged')]) || []).filter(x => before(x.confirmedT));
}
function panbeiFor(v, m){
  if (chunkedActive()) return [];
  const base = m === 'seg' ? 'panbeiPointsSegment' : 'panbeiPoints';
  return (((v === 'raw' ? L()[PK(base + 'Raw')] : L()[PK(base + 'Merged')]) || [])).filter(x => before(x.confirmedT));
}
function macdFor(v){
  if (chunkedActive()){
    const w = ChunkStore.win;
    if (!w) return { diff: [], dea: [], hist: [] };
    const closes = [...w.preCloses, ...w.candles.map(c => c.close)];
    const m = macdLocal(closes);
    const cut = w.preCloses.length;   // 预热段只喂指标不显示
    return { diff: m.diff.slice(cut), dea: m.dea.slice(cut), hist: m.hist.slice(cut) };
  }
  if (!inReplay()) return v === 'raw'
    ? { diff:L().macdDiff, dea:L().macdDea, hist:L().macdHist }
    : { diff:L().macdMergedDiff, dea:L().macdMergedDea, hist:L().macdMergedHist };
  const n = candlesFor(v).length;
  const [diff, dea, hist] = v === 'raw'
    ? [L().macdDiff, L().macdDea, L().macdHist]
    : [L().macdMergedDiff, L().macdMergedDea, L().macdMergedHist];
  return { diff: diff.slice(0, n), dea: dea.slice(0, n), hist: hist.slice(0, n) };
}
const kindColor = k => {
  const th = themeKey();
  if (k.startsWith('一买')) return BUY_COLORS[th].b1;
  if (k.startsWith('二买')) return BUY_COLORS[th].b2;
  if (k.startsWith('三买')) return BUY_COLORS[th].b3;
  if (k.startsWith('一卖')) return SELL_COLORS[th].s1;
  if (k.startsWith('二卖')) return SELL_COLORS[th].s2;
  if (k === '告警') return FAST_COLORS[th].alert;
  if (k === '背驰买') return FAST_COLORS[th].buy;
  if (k === '背驰卖') return FAST_COLORS[th].sell;
  if (k === '盘整背驰买') return PANBEI_COLORS[th].buy;
  if (k === '盘整背驰卖') return PANBEI_COLORS[th].sell;
  return SELL_COLORS[th].s3;
};

// ---- 回测交易数据 ----
const ALL_TRADES = () => window.TRADES_DATA || [];
function tradesFor(){
  // 回测交易基于新笔信号口径生成; 老笔为对照链, 无对应回测 → 返回空并在面板提示
  if (strokeStd === 'old' || chunkedActive()) return [];
  return ALL_TRADES().filter(x => x.tf === activeTF && x.mode === tradeMode && x.cfg === tradeCfg);
}
// 出场时间定位K线下标; 不在序列中(数据重算后边界变动)时二分取 <= ts 的最近K线
function locateIdx(ts){
  if (idxMap[ts] != null) return idxMap[ts];
  const cs = candlesFor(activeView);
  let lo = 0, hi = cs.length - 1, ans = null;
  while (lo <= hi){
    const mid = (lo + hi) >> 1;
    if (cs[mid].time <= ts){ ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}
const fmtRet = r => (r >= 0 ? '+' : '') + r.toFixed(1) + '%';
