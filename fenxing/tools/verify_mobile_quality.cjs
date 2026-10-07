const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const localStorage = {
  _d: {},
  getItem(k){ return Object.prototype.hasOwnProperty.call(this._d, k) ? this._d[k] : null; },
  setItem(k, v){ this._d[k] = String(v); },
  removeItem(k){ delete this._d[k]; },
};
const context = vm.createContext({
  localStorage,
  indexedDB: { open(){ throw new Error('no idb in quality test'); } },
  console,
  Date, Math, JSON, String, Number, Array, Map, Set, Promise,
  fmtTs: (ts) => 't' + ts,
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
  TR: {}, replayT: null, positions: [], posSeq: 1, drawRecs: [], chart: null,
});
vm.runInContext(
  fs.readFileSync(path.join(__dirname, '../mobile/js/persist.js'), 'utf8') +
  '\n; globalThis.__setSessions = arr => { savedHistory.sessions = arr; };',
  context);

assert.equal(vm.runInContext("sessionQualityText('good')", context), '好题');
assert.equal(vm.runInContext("sessionQualityText('poor')", context), '没意义');
assert.equal(vm.runInContext("sessionQualityText('')", context), '');
assert.equal(vm.runInContext("sessionQualityText(undefined)", context), '');

const base = {
  id: 's-q', ts: Date.UTC(2024, 0, 8), sym: 'BTC', tf: '1D', startTs: 1,
  steps: 20, capital: 100000, endCapital: 100000, pnl: 0, cond: '完成', trades: [],
};
context.sessionsFixture = [
  { ...base, id: 'good', quality: 'good' },
  { ...base, id: 'poor', quality: 'poor' },
  { ...base, id: 'skip' },
];
vm.runInContext('__setSessions(sessionsFixture)', context);

const csv = vm.runInContext('buildTrainerCsv()', context);
const header = csv.replace(/^\ufeff/, '').split('\n')[0];
assert.ok(header.split(',').includes('quality'), header);
assert.ok(header.split(',').includes('structure'), header);
assert.ok(header.split(',').includes('duration_sec'), header);
assert.ok(header.split(',').includes('session_pnl_pct'), header);
assert.ok(header.split(',').includes('session_max_dd'), header);
assert.ok(csv.includes('好题'), csv);
assert.ok(csv.includes('没意义'), csv);
const lines = csv.replace(/^\ufeff/, '').trim().split('\n');
assert.equal(lines.length, 4);
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
const qualityIdx = header.split(',').indexOf('quality');
assert.equal(cells(lines[1])[qualityIdx], '好题');
assert.equal(cells(lines[2])[qualityIdx], '没意义');
assert.equal(cells(lines[3])[qualityIdx], '');

console.log('PASS session quality labels and CSV column');
