const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const store = {};
const context = vm.createContext({
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  },
  indexedDB: { open() { throw new Error('no idb'); } },
  console,
  Date, JSON, Math, Promise, Number, Array, Object, String, Boolean,
  toast() {},
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../mobile/js/persist.js'), 'utf8'), context);
const run = code => vm.runInContext(code, context);

const wed = Date.parse('2026-09-23T12:00:00+08:00');
const mon = run(`weekStartMs(${wed})`);
assert.equal(new Date(mon).getDay(), 1);
assert.equal(new Date(run(`nextWeekStartMs(${mon})`)).getDay(), 1);

const mk = (ts, cond) => ({ ts, cond });
run(`savedHistory.sessions = []`);
let w = run(`weeklyCheckin(${wed}, [])`);
assert.equal(w.count, 0);
assert.equal(w.goal, 20);
assert.equal(w.done, false);
assert.equal(w.streak, 0);

const wedStart = wed;
const thisWeek = Array.from({ length: 12 }, (_, i) => mk(wedStart + i * 60000, '完成'));
thisWeek.push(mk(wedStart + 13 * 60000, '放弃'));
thisWeek.push(mk(wedStart + 14 * 60000, '爆仓'));
w = run(`weeklyCheckin(${wed}, ${JSON.stringify(thisWeek)})`);
assert.equal(w.count, 13, JSON.stringify(w));
assert.equal(w.days[2], 13);
assert.equal(w.done, false);
assert.equal(w.streak, 0);

const lastMon = mon - 7 * 86400000;
const prevWeek = Array.from({ length: 20 }, (_, i) => mk(lastMon + i * 3600000, '完成'));
w = run(`weeklyCheckin(${wed}, ${JSON.stringify(thisWeek.concat(prevWeek))})`);
assert.equal(w.count, 13);
assert.equal(w.streak, 1, 'incomplete current week keeps last week streak');

const fullThis = Array.from({ length: 20 }, (_, i) => mk(mon + i * 3600000, '完成'));
w = run(`weeklyCheckin(${wed}, ${JSON.stringify(fullThis.concat(prevWeek))})`);
assert.equal(w.count, 20);
assert.equal(w.done, true);
assert.equal(w.streak, 2);

console.log('PASS weekly checkin: Monday week, skip 放弃, 爆仓 counts, streak skips incomplete current week');
