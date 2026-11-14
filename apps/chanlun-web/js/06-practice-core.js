// Pure practice calculations. Bar tuple: [UTC seconds, open, high, low, close, volume|null].
(function(root){
'use strict';
const VERSION = 'practice-evidence/1';
const HORIZONS = [5, 20, 60];
const mean = xs => xs.length && xs.every(Number.isFinite) ? xs.reduce((a,b)=>a+b,0)/xs.length : null;
const ratio = (a,b) => Number.isFinite(a) && Number.isFinite(b) && b > 0 ? a/b : null;
const validBar = b => Array.isArray(b) && b.slice(0,5).length === 5 && b.slice(0,5).every(Number.isFinite)
  && b[2] >= Math.max(b[1], b[4], b[3]) && b[3] <= Math.min(b[1],b[4]);
function features(bars, i, anchors = {}){
  if(!Number.isInteger(i)||i<0||i>=bars.length)return null;
  const cs = bars.slice(0,i+1), b = cs.at(-1);
  if (!validBar(b)) return null;
  const sma = (p, end=cs.length) => end >= p ? mean(cs.slice(end-p,end).map(x=>x[4])) : null;
  const tr = (x,k) => k ? Math.max(x[2]-x[3],Math.abs(x[2]-cs[k-1][4]),Math.abs(x[3]-cs[k-1][4])) : x[2]-x[3];
  const trs = cs.map(tr);
  const atr = trs.length >= 14 ? mean(trs.slice(-14)) : null;
  const ma = {};
  for (const p of [20,60,120]){
    const value = sma(p), prior = sma(p,cs.length-5);
    ma[p] = { value, change5Pct:prior>0 ? 100*(value/prior-1) : null, distanceATR:value != null ? ratio(b[4]-value,atr) : null };
  }
  const vol = cs.slice(-21,-1);
  let fast = cs[0][4], slow = fast, dea = 0, dif = 0, hist = 0, previousHist = null;
  cs.forEach((x,k)=>{
    fast += (x[4]-fast)*2/13; slow += (x[4]-slow)*2/27;
    dif = fast-slow; dea += (dif-dea)*2/10; previousHist = k ? hist : null; hist = dif-dea;
  });
  const a = cs.findIndex(x=>x[0]===anchors.start), z = cs.findIndex(x=>x[0]===anchors.end);
  let range = null;
  if (a>=0 && z>=a && z<cs.length-1){
    const part = cs.slice(a,z+1), half = Math.floor(part.length/2);
    const high = Math.max(...part.map(x=>x[2])), low = Math.min(...part.map(x=>x[3]));
    let peak=part[0][2], drawdown=0;
    for(const x of part.slice(1)){
      if(peak>0)drawdown=Math.max(drawdown,100*(peak-x[3])/peak);
      peak=Math.max(peak,x[2]);
    }
    range = { bars:part.length, high, low, rangeATR:ratio(high-low,atr), widthPct:high>0 ? 100*(high-low)/high : null,
      drawdownPct:drawdown,
      lastVsFirstTR:half>=2 ? ratio(mean(trs.slice(z-half+1,z+1)),mean(trs.slice(a,a+half))) : null,
      lastVsFirstVolume:half>=2 ? ratio(mean(part.slice(-half).map(x=>x[5])),mean(part.slice(0,half).map(x=>x[5]))) : null,
      breakoutATR:ratio(b[4]-high,atr) };
  }
  const recent=cs.slice(-20), hi=Math.max(...recent.map(x=>x[2])), lo=Math.min(...recent.map(x=>x[3]));
  return { version:VERSION, ma, atr14Simple:atr, recent20:{ bars:recent.length,
      closeInRange:ratio(b[4]-lo,hi-lo), distanceFromHighATR:ratio(hi-b[4],atr),
      closeChangePct:recent[0][4]>0 ? 100*(b[4]/recent[0][4]-1) : null },
    bodyATR:ratio(Math.abs(b[4]-b[1]),atr),
    closeInRange:ratio(b[4]-b[3],b[2]-b[3]), upperWick:ratio(b[2]-Math.max(b[1],b[4]),b[2]-b[3]),
    lowerWick:ratio(Math.min(b[1],b[4])-b[3],b[2]-b[3]), volumeRatio20:ratio(b[5],vol.length===20 ? mean(vol.map(x=>x[5])) : null),
    macd:cs.length>=35 ? { dif, dea, hist, previousHist, warmup:cs.length } : null, range };
}
// Reference entry occurs at the signal close. No price movement from that bar is credited.
function outcomes(bars, entryTime, entry, stop, side){
  const i = bars.findIndex(b=>b[0]===entryTime), dir = side==='空' ? -1 : 1;
  const risk = (entry-stop)*dir;
  if (i<0 || !Number.isFinite(entry) || entry<=0 || !Number.isFinite(stop) || stop<=0 || risk<=0) return [];
  return HORIZONS.map(horizon=>{
    let lower=0, upper=0, stopBars=null, fill=null, observed=0;
    for(let j=1;j<=horizon && i+j<bars.length;j++){
      const b=bars[i+j];
      if(!validBar(b)||b[0]<=bars[i+j-1][0])break;
      observed=j;
      const hit=dir===1 ? b[3]<=stop : b[2]>=stop;
      const openHit=(b[1]-stop)*dir<=0;
      const favorable=((dir===1 ? b[2] : b[3])-entry)*dir;
      if(hit){
        stopBars=j;
        fill=openHit ? b[1] : stop;
        if(!openHit){
          lower=Math.max(lower,(b[1]-entry)*dir);
          upper=Math.max(lower,favorable);
        }
        break;
      }
      lower=upper=Math.max(lower,favorable);
    }
    return { horizon, observed, resolved:stopBars!==null || observed===horizon, stopBars, simulatedStopFill:fill,
      ambiguous:upper>lower, mfePct:[lower/entry*100,upper/entry*100], mfeR:[lower/risk,upper/risk],
      targets:[2,3,5].map(r=>({ r, certain:lower>=r*risk, possible:upper>=r*risk })) };
  });
}
function positions(trades){
  const groups=new Map();
  for(const r of trades||[]){
    // Old exports may lack a PID; preserve each such row as an unknown separate position.
    const key=r.pid==null ? Symbol() : [r.pid,r.symbol,r.tf,r.entry_ts??r.entry_time,r.side].join('|');
    const p=groups.get(key)||{ symbol:r.symbol, pnl:0, capital:0, rows:0, closed:false };
    p.pnl+=Number(r.pnl_u)||0; p.capital+=Number(r.capital)||0; p.rows++;
    p.closed ||= r.partial!==true && !(Number(r.ratio)>0 && Number(r.ratio)<1);
    groups.set(key,p);
  }
  return [...groups.values()];
}
function opportunityRows(session){
  const by = new Map();
  for(const d of session.research?.decisions||[]){
    const key=d.opportunityId||d.id;
    const prev=by.get(key);
    // Once taken, the first entry owns the reference path for this opportunity.
    if (!prev || prev.choice!=='buy') by.set(key,d);
  }
  return [...by.values()];
}
function batchRows(sessions, horizon, filters={}){
  const rows=[], seen=new Set();
  for(const s of [...sessions].sort((a,b)=>a.ts-b.ts)){
    if(!s.research||s.cond==='进行中'||s.cond==='中断'||s.cond==='放弃')continue;
    const cohort=s.research.cohort||'legacy';
    if ((filters.cohort&&cohort!==filters.cohort)||(filters.sym&&s.sym!==filters.sym)||
        (filters.tf&&s.tf!==filters.tf)||(filters.rule&&s.research.rule!==filters.rule))continue;
    for(const d of opportunityRows(s)){
      if((filters.kind&&filters.kind!==d.kind)||(filters.choice&&filters.choice!==d.choice))continue;
      const key=JSON.stringify([cohort,s.sym,s.tf,d.barTime,d.side,s.research.rule,d.choice==='buy'?'buy':'review']);
      if(seen.has(key))continue;
      seen.add(key);
      rows.push({s,d,path:(d.outcomes||[]).find(p=>p.horizon===horizon)});
    }
  }
  return rows;
}
const api={VERSION,HORIZONS,validBar,features,outcomes,positions,opportunityRows,batchRows};
root.PracticeCore=api;
if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
