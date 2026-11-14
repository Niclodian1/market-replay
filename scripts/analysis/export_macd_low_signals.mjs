// Export exactly the current chart's bottomDivergence candidates, retaining confirmation time.
import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const engine=require('../../apps/chanlun-web/js/13-annotation-signals.js');
const root=path.resolve(path.dirname(new URL(import.meta.url).pathname),'../..');
const out=path.join(root,'reports/macd-low-breakeven-2026-11-11');
const plan=JSON.parse(fs.readFileSync(path.join(out,'catalog.json')));
const cutoff=Date.parse('2026-11-12T00:00:00+08:00')/1000;
const fd=fs.openSync(path.join(out,'series.jsonl'),'w');
const meta=[];
function emit(sym,name,market,tf,bars,source,hash){
 bars=bars.filter(b=>b[0]<cutoff).map(b=>{
   const h=Math.max(b[2],b[3]),l=Math.min(b[2],b[3]);
   return [b[0],Math.min(h,Math.max(l,b[1])),h,l,Math.min(h,Math.max(l,b[4]))];
 });
 for(let i=0;i<bars.length;i++)if(!bars[i].every(Number.isFinite)||(i&&bars[i][0]<=bars[i-1][0]))throw Error('Invalid OHLC '+sym+' '+tf+' '+i);
 const cs=bars.map(b=>({time:b[0],open:b[1],high:b[2],low:b[3],close:b[4]}));
 const signals=engine.bottomDivergence(cs).map(s=>({low:s.i,confirm:s.evidence.confirmIndex,strength:s.evidence.strength,
   tier:s.evidence.tier,reference:s.evidence.referenceIndex,segmentStart:s.evidence.segment[0],referenceLow:s.evidence.referenceLow,
   lowPrice:s.evidence.lowPrice,currentHist:s.evidence.currentHist,referenceHist:s.evidence.referenceHist}));
 // Prefix comparisons use the confirmation, not the back-painted low marker.
 for(const n of [...new Set([Math.min(80,bars.length),Math.floor(bars.length*.47),Math.floor(bars.length*.83),...signals.slice(0,3).map(s=>s.confirm+1)])].filter(n=>n>=60)){
  const prefix=engine.bottomDivergence(cs.slice(0,n)).map(s=>[s.i,s.evidence.confirmIndex,s.evidence.referenceIndex,s.evidence.strength]);
  const full=signals.filter(s=>s.confirm<n).map(s=>[s.low,s.confirm,s.reference,s.strength]);
  if(JSON.stringify(prefix)!==JSON.stringify(full))throw Error('Future leakage '+sym+' '+tf+' '+n);
 }
 const row={sym,name,market,tf,bars,signals,source,hash};
 fs.writeSync(fd,JSON.stringify(row)+'\n');
 meta.push({sym,name,market,tf,source,hash,bars:bars.length,signals:signals.length,start:bars[0]?.[0],end:bars.at(-1)?.[0]});
 console.log(sym,tf,bars.length,signals.length);
}
for(const item of plan){
 const source=path.join(root,'apps/chanlun-web/data-view/'+(item.sym==='BTC'?'data.js':'data_'+item.sym.toLowerCase()+'.js'));
 const text=fs.readFileSync(source,'utf8'),context={window:{}};
 vm.runInNewContext(text,context,{timeout:10000});
 const m=context.window.MARKET_DATA;
 const hash=crypto.createHash('sha256').update(text).digest('hex');
 for(const [tf,level] of Object.entries(m.levels))emit(item.sym,item.name,item.market,tf,Array.from(level.candles,b=>Array.from(b)),source,hash);
 if(!['BTC','ETH','SOL','PEPE'].includes(item.sym))continue;
 const dir=path.join(root,'apps/chanlun-web/intraday',item.sym,'15m');
 const manifest=JSON.parse(fs.readFileSync(path.join(dir,'manifest.json'),'utf8'));
 const h=crypto.createHash('sha256'),bars=[];
 for(const ch of manifest.chunks){
  const buf=fs.readFileSync(path.join(dir,ch.file));h.update(buf);
  for(let i=0;i<buf.length;i+=48)bars.push(Array.from({length:5},(_,j)=>buf.readDoubleLE(i+j*8)));
 }
 emit(item.sym,item.name,item.market,'15F',bars,dir,h.digest('hex'));
}
fs.closeSync(fd);
fs.writeFileSync(path.join(out,'catalog-result.json'),JSON.stringify({series:meta,engineSha256:crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'apps/chanlun-web/js/13-annotation-signals.js'))).digest('hex')},null,2));
