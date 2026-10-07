const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('path');
const vm = require('node:vm');

const localStorage = {
  _d: {},
  getItem(k){ return Object.prototype.hasOwnProperty.call(this._d, k) ? this._d[k] : null; },
  setItem(k, v){ this._d[k] = String(v); },
  removeItem(k){ delete this._d[k]; },
};
const context = vm.createContext({
  localStorage,
  indexedDB: { open(){ throw new Error('no idb in session test'); } },
  console,
  Date, Math, JSON, String, Number, Array, Map, Set, Promise,
  fmtTs: (ts, tf) => `${tf || 'tf'}:${ts}`,
  fmtTimeReal: (ts) => 'r' + ts,
  toast(){},
  document: {
    getElementById: () => null,
    querySelectorAll: () => [],
    documentElement: { dataset: {} },
  },
  THEMES: { dark: {} },
  themeKey: () => 'dark',
  t: () => ({ plane: '#000' }),
  applyOverlayPreferences(){},
  TR: { initCapital: 50000 }, replayT: null, positions: [], posSeq: 1, drawRecs: [], chart: null,
});
vm.runInContext(
  fs.readFileSync(path.join(__dirname, '../mobile/js/persist.js'), 'utf8') +
  '\n; globalThis.__setSessions = arr => { savedHistory.sessions = arr; };',
  context);

assert.equal(vm.runInContext('sessionCapital()', context), 50000);
assert.equal(vm.runInContext('formatDuration(0)', context), '0秒');
assert.equal(vm.runInContext('formatDuration(4500)', context), '5秒');
assert.equal(vm.runInContext('formatDuration(125000)', context), '2分05秒');
assert.equal(vm.runInContext('formatDuration(3723000)', context), '1小时2分3秒');
assert.equal(vm.runInContext('sessionPnlPct({ capital: 50000, pnl: 2500 })', context).toFixed(1), '5.0');
assert.ok(Math.abs(vm.runInContext('maxDrawdown([{eq:100},{eq:120},{eq:90}])', context) - 25) < 1e-9);

const session = {
  id: 's1', ts: Date.UTC(2024, 0, 8), sym: 'BTC', tf: '1D', startTs: 1,
  steps: 20, capital: 50000, endCapital: 52500, pnl: 2500, cond: '完成',
  quality: 'good', structure: '强趋势', turn: '下跌转涨', durationMs: 125000,
  signals: [{ t: 10, note: '量不够先观察' }, { t: 20, note: '' }],
  curve: [{ step: 0, eq: 50000 }, { step: 1, eq: 54000 }, { step: 2, eq: 52500 }],
  trades: [],
};
vm.runInContext('__setSessions(sessionFixture)', Object.assign(context, { sessionFixture: [session] }));
const csv = vm.runInContext('buildTrainerCsv()', context);
const header = csv.replace(/^\ufeff/, '').split('\n')[0].split(',');
for (const col of ['structure', 'turn', 'duration_sec', 'signals', 'signal_notes', 'session_pnl_pct', 'session_max_dd']){
  assert.ok(header.includes(col), col);
}
assert.ok(csv.includes('强趋势'), csv);
assert.ok(csv.includes('下跌转涨'), csv);
assert.ok(csv.includes('量不够先观察'), csv);
assert.ok(csv.includes('"125"'), csv);

const cells = line => {
  const out = [];
  let i = 0;
  while (i <= line.length){
    if (line[i] === '"'){
      let s = '', j = i + 1;
      while (j < line.length){
        if (line[j] === '"' && line[j + 1] === '"'){ s += '"'; j += 2; continue; }
        if (line[j] === '"'){ j++; break; }
        s += line[j++];
      }
      out.push(s);
      i = j + (line[j] === ',' ? 1 : 0);
      if (j >= line.length) break;
    } else {
      const j = line.indexOf(',', i);
      if (j < 0){ out.push(line.slice(i)); break; }
      out.push(line.slice(i, j));
      i = j + 1;
    }
  }
  return out;
};
const row = cells(csv.replace(/^\ufeff/, '').trim().split('\n')[1]);
assert.equal(row[header.indexOf('structure')], '强趋势');
assert.equal(row[header.indexOf('turn')], '下跌转涨');
assert.equal(row[header.indexOf('duration_sec')], '125');
assert.equal(row[header.indexOf('session_pnl_pct')], '5.00');
assert.ok(+row[header.indexOf('session_max_dd')] > 0);
assert.ok(row[header.indexOf('signals')].includes('1D:10'));
assert.ok(row[header.indexOf('signal_notes')].includes('量不够先观察'));

console.log('PASS session capital, duration, tags, signals, drawdown, CSV');
