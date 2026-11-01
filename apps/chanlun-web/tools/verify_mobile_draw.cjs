const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const src = fs.readFileSync(path.join(__dirname, '../mobile/js/charts.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../mobile/index.html'), 'utf8');
assert.match(src, /function fibPrice\(p1, p2, r\)\{ return p2 \+ \(p1 - p2\) \* r; \}/);
assert.match(html, /data-tool="fib"/);
assert.match(html, /data-tool="poly"/);
assert.match(html, /id="drawDock"/);
assert.match(html, /id="chartSettingsBtn"/);
assert.match(src, /function setChartSettingsOpen/);
assert.match(src, /function legendFromPointer/);
assert.match(src, /type: 'fib'/);
assert.match(src, /type: 'poly'/);
assert.match(src, /_drawCursor/);
assert.match(src, /trackFibCursor/);
assert.match(src, /fibAim/);

function fibPrice(p1, p2, r){ return p2 + (p1 - p2) * r; }
assert.equal(fibPrice(100, 200, 0), 200);
assert.equal(fibPrice(100, 200, 1), 100);
assert.equal(fibPrice(100, 200, 0.5), 150);
assert.ok(Math.abs(fibPrice(100, 200, 0.618) - 138.2) < 1e-10);
assert.ok(Math.abs(fibPrice(200, 100, 0.618) - 161.8) < 1e-10);

console.log('PASS fib formula, polyline/fib tools, collapsible settings');
