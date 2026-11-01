const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const context = vm.createContext({
  document: { documentElement: { dataset: { theme: 'dark' } }, getElementById: () => null },
  Number, Math, String, Date, JSON, Array, Map, Set, Promise, console,
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../mobile/js/core.js'), 'utf8'), context);

const fmt = n => vm.runInContext(`fmt(${JSON.stringify(n)})`, context);
const roundPrice = n => vm.runInContext(`roundPrice(${JSON.stringify(n)})`, context);
const priceFormatFor = n => vm.runInContext(`priceFormatFor(${JSON.stringify(n)})`, context);

assert.equal(fmt(1234.56), '1,234.6');
assert.equal(fmt(12.34), '12.34');
assert.equal(fmt(0.1234), '0.1234');
assert.match(fmt(0.0000062), /0\.0₅62/);
assert.match(fmt(6.2e-7), /0\.0₆62/);
assert.match(fmt(-0.0000062), /^-0\.0₅/);
assert.ok(fmt(0.0000062).length < 12, fmt(0.0000062));

const ohlc = `O ${fmt(6.2e-6)}  H ${fmt(6.45e-6)}  L ${fmt(5.98e-6)}  C ${fmt(6.12e-6)}`;
assert.ok(ohlc.length < 48, ohlc);
assert.doesNotMatch(ohlc, /0\.00000/);

const pepe = 6.2e-7;
assert.ok(roundPrice(pepe) > 0);
assert.notEqual(+pepe.toFixed(2), pepe);
assert.ok(Math.abs(roundPrice(pepe) - pepe) < 1e-12);
assert.equal(priceFormatFor(pepe).type, 'custom');
assert.ok(priceFormatFor(pepe).minMove <= 1e-9);
assert.equal(priceFormatFor(pepe).formatter(pepe), fmt(pepe));

console.log('PASS: compact PEPE price format keeps OHLC short and non-zero');
