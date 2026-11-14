const fs=require('fs'),assert=require('assert/strict'),path=require('path'),E=require('../../js/13-ma20-ai-engine.js');
const root=path.resolve(__dirname,'../../../..'),readModel=()=>JSON.parse(fs.readFileSync(path.join(root,'apps/chanlun-web/js/13-ma20-ai-model.js'),'utf8').split(' = ')[1].trim().replace(/;$/,''));const m=readModel();
const expected=JSON.parse(fs.readFileSync(path.join(root,'reports/ma20-ai-2026-11-04/parity.json')));
let maxError=0;for(const row of expected.rows)for(const [name,score]of Object.entries(row.scores)){const actual=E.score(row,m.models[name]);maxError=Math.max(maxError,Math.abs(score-actual));assert.ok(Math.abs(score-actual)<1e-10,`${name} ${row.time}: ${actual} vs ${score}`)}
const pack=JSON.parse(require('child_process').execFileSync(process.execPath,[path.join(__dirname,'features.cjs')],{maxBuffer:30e6}));const cs=pack.candles;
// Real volumes loaded directly; future rows must not alter any earlier feature.
const source=fs.readFileSync(path.join(root,'apps/chanlun-web/data-view/data.js'),'utf8'),vs=JSON.parse(source.slice(source.indexOf('{'),source.lastIndexOf('}')+1)).levels['1D'].volume;
const full=E.compute(cs,vs);for(let i=200;i<cs.length;i+=227){assert.deepEqual(E.compute(cs.slice(0,i+1),vs.slice(0,i+1)).rows.at(-1),full.rows[i]);}
const zero=E.compute([]);assert.equal(zero.rows.length,0);const missing=E.compute(cs.slice(0,80));assert.equal(missing.rows.at(-1).values[13],null);
const scaled=E.compute(cs,vs.map(v=>v*7));for(let i=180;i<cs.length;i+=211){for(const j of [13,14])assert.ok(Math.abs(full.rows[i].values[j]-scaled.rows[i].values[j])<1e-10);}
const heldout=m.report.models[m.selected].splits.test;assert.equal(m.gatePassed,heldout.recall>=.85&&heldout.negative_rejection>=.2,'gate must match frozen checks');assert.equal(m.featureNames.length,26);assert.equal(m.scope.symbol,'BTC');assert.ok(!m.featureNames.some(n=>/future|mfe|stoprate|annotation|time|year/i.test(n)));
console.log(`PASS ${expected.rows.length} rows Python/JS scoring for both models; max error ${maxError}; causal prefixes; missing/scaled volume; advisory gate`);
