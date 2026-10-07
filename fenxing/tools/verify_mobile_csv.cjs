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
  indexedDB: { open(){ throw new Error('no idb in csv test'); } },
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

const trade = {
  pid: 7, side: '多', leverage: 10, capital: 10000,
  entry_time: '2024-01-01', entry: 100.5, exit_time: '2024-01-05', exit_price: 110,
  stop: 90, tp: 120, adds: 1, ratio: 1, partial: false, pnl_u: 1000, pnl_pct_capital: 10,
  result: '盈', exit_reason: '手动平仓', logic: '突破', exit_logic: '止盈',
  entries: [
    { t: '2024-01-01', price: 100, capital: 10000, leverage: 10, logic: '突破' },
    { t: '2024-01-03', price: 105, capital: 500, leverage: 10, logic: '浮盈加仓' },
  ],
  entry_ts_list: [1, 3], entry_prices: [100, 105],
};
context.sessionFixture = {
  id: 's-add', ts: Date.UTC(2024, 0, 8), sym: 'BTC', tf: '1D', startTs: 1,
  steps: 20, capital: 100000, endCapital: 101000, pnl: 1000, cond: '完成',
  trades: [trade],
};
vm.runInContext('__setSessions([sessionFixture])', context);

const csv = vm.runInContext('buildTrainerCsv()', context);
assert.match(csv, /kind/);
assert.match(csv, /加仓/);
assert.match(csv, /浮盈加仓/);
assert.match(csv, /add_prices/);
assert.match(csv, /,tp,/);
assert.match(csv, /quality/);
assert.match(csv, /r_mult/);
assert.match(csv, /max_r/);
assert.ok(csv.includes('120'), csv);
assert.ok(csv.includes('105'), csv);
assert.ok(csv.includes('500'), csv);

const lines = csv.replace(/^\ufeff/, '').trim().split('\n');
assert.equal(lines.length, 3, 'header + 持仓 + 加仓');
assert.ok(lines[1].includes('持仓'), lines[1]);
assert.ok(lines[2].includes('加仓'), lines[2]);

context.tradeFixture = trade;
const rows = vm.runInContext('csvRowsForTrade({session_id:"s"}, tradeFixture)', context);
assert.equal(rows.length, 2);
assert.equal(rows[0].kind, '持仓');
assert.equal(rows[0].working_capital, 10500);
assert.equal(rows[1].kind, '加仓');
assert.equal(rows[1].capital, 500);
assert.equal(rows[1].entry_logic, '浮盈加仓');

context.partials = [
  { ...trade, pid: 8, capital: 5000, pnl_u: 400, adds: 0, entries: [trade.entries[0]], entry_ts_list: [1], entry_prices: [100] },
  { ...trade, pid: 8, capital: 5000, pnl_u: 600, adds: 1, entries: trade.entries, ratio: 1, partial: false },
];
const grouped = vm.runInContext('groupTradesByPid(partials)', context);
assert.equal(grouped.length, 1);
assert.equal(grouped[0].adds, 1);
assert.equal(grouped[0].entries.length, 2);
assert.equal(grouped[0].capital, 10000);

console.log('PASS: CSV writes 加仓 lots (price/capital/logic) and grouping keeps add entries');
