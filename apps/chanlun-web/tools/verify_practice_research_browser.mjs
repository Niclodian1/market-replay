// Use a temporary Chrome profile. The test clears practice records on the test origin.
import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const origin=process.env.DESKTOP_TEST_URL||'http://127.0.0.1:8896';
const port=process.env.CDP_PORT||'9356';
const endpoint=`http://127.0.0.1:${port}`;
const tab=await (await fetch(endpoint+'/json/new?about:blank',{method:'PUT'})).json();
const ws=new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
const calls=new Map(), errors=[];
let seq=0;
ws.onmessage=e=>{
  const m=JSON.parse(e.data);
  if(m.id){
    const waiter=calls.get(m.id);calls.delete(m.id);
    if(m.error)waiter.reject(new Error(m.error.message));else waiter.resolve(m.result);
  }else if(m.method==='Page.javascriptDialogOpening'){
    if(m.params.type==='beforeunload')void call('Page.handleJavaScriptDialog',{accept:true});
    else {errors.push(m.params.message);void call('Page.handleJavaScriptDialog',{accept:false});}
  }else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text);
};
const call=(method,params={})=>new Promise((resolve,reject)=>{
  const id=++seq,t=setTimeout(()=>{calls.delete(id);reject(new Error('CDP timed out: '+method));},10000);
  calls.set(id,{resolve:r=>{clearTimeout(t);resolve(r);},reject:e=>{clearTimeout(t);reject(e);}});
  ws.send(JSON.stringify({id,method,params}));
});
const ev=async expression=>{
  const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
  if(r.exceptionDetails)throw new Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);
  return r.result.value;
};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const until=async expression=>{
  for(let i=0;i<150;i++){if(await ev(expression))return;await sleep(150);}
  throw new Error('Timed out: '+expression);
};
const out=await mkdtemp(join(tmpdir(),'practice-browser-'));
try{
  await call('Runtime.enable');
  await call('Page.enable');
  await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:out});
  await call('Emulation.setDeviceMetricsOverride',{width:1536,height:980,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:origin+'/index.html'});
  await until('window.__INIT_OK === true');
  await ev('PracticeStore.clear()');
  await ev(`(async()=>{
    alert = message=>{window.lastPracticeAlert=String(message)};
    document.getElementById('trainerBtn').click();
    prEl('trSymSel').value='BTC';prEl('trTfSel').value='1D';prEl('trStepsInput').value=20;
    prEl('trDrillSel').value='';prEl('prMode').value='research';prEl('prRule').value='e2e-ma20';
    prEl('trCfgStart').click();
  })()`);
  await until('typeof TR!=="undefined" && !!TR.research && trLocked()');
  await ev(`prEl('trainerDecision').click();prEl('prChoice').value='wait';prDecisionFields();
    prEl('prReason').value='尚未突破关键位置';prEl('prCondition').value='收盘突破箱体';prEl('prKind').value='整理后突破';`);
  const pick=async(key,offset)=>{
    await ev(`document.querySelector('[data-pr-pick="${key}"]').click()`);
    await sleep(400);
    const point=await ev(`(()=>{
      const bar=TR.research.bars.at(${offset}), r=prEl('chart').getBoundingClientRect();
      return {x:r.x+chart.timeScale().timeToCoordinate(bar[0]),y:r.y+100};
    })()`);
    await call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
    await call('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',clickCount:1});
    await call('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',clickCount:1});
    try{await until(`prDraft?.anchors.${key}!=null`);}
    catch(e){
      console.error('pick',key,point,await ev(`({pick:prPick,hint:prEl('practicePickHint').textContent,
        hit:document.elementFromPoint(${point.x},${point.y})?.outerHTML.slice(0,160)})`));throw e;
    }
  };
  await pick('start',-24);
  await pick('end',-8);
  await ev("prEl('prSubmit').click()");
  const before=await ev('TR.research.decisions[0]');
  assert.equal(before.choice,'wait');
  assert.equal(before.features.range.bars,17);
  assert.equal(before.outcomes,undefined);
  assert.equal(await ev('TR.research.bars.at(-1)[0]===replayT'),true);
  assert.equal(await ev('positions.length'),0);

  const advance=await ev(`(()=>{
    const previous=candlesFor('raw'), cached=previous===candlesFor('raw');
    const series=[candleSeries,volumeSeries,macdHistSeries,macdLineSeries,macdSignalSeries,...maSeries.map(m=>m.series)];
    let full=0;const patches=series.map(s=>{
      const set=s.setData, update=s.update, points=[];
      s.setData=function(...a){full++;return set.apply(this,a);};
      s.update=function(p){points.push(p);return update.call(this,p);};
      return {s,set,update,points};
    });
    try{prEl('trainerNext').click();}
    finally{for(const {s,set,update} of patches){s.setData=set;s.update=update;}}
    const cs=candlesFor('raw'), i=cs.length-1, m=macdFor('raw');
    return {cached,full,last:cs[i],oldThrough:previous.at(-1).time,through:replayT,shared:cs.at(-2)===previous.at(-1),
      points:patches.map(p=>p.points), values:[volArrFor('raw')[i],m.hist[i],m.diff[i],m.dea[i],
        ...maSeries.map(p=>sma(cs.map(c=>c.close),p.period)[i])]};
  })()`);
  assert.equal(advance.cached,true);
  assert.equal(advance.full,0,'one forward step must not reset any price or indicator series');
  assert.equal(advance.shared,true,'previously seen bars should remain stable');
  assert.ok(advance.through>advance.oldThrough);
  assert.ok(advance.points.every(p=>p.length===1&&p[0].time===advance.through),'only the newly visible bar may be appended');
  assert.deepEqual(advance.points[0][0],advance.last);
  advance.values.forEach((v,i)=>assert.ok(Math.abs(advance.points[i+1][0].value-v)<=Math.max(1e-12,Math.abs(v)*1e-10)));

  await ev(`prEl('trainerDecision').click();
    prEl('prParent').value=${JSON.stringify(before.opportunityId)};prEl('prParent').dispatchEvent(new Event('change'));
    prEl('prReason').value='突破关键位置';prEl('jCapital').value=1000;prEl('jLeverage').value=1;
    prEl('prSubmit').click();`);
  const entry=await ev('({p:positions[0],b:curBar(),d:TR.research.decisions[1]})');
  assert.equal(entry.p.initialStop,entry.b.low);
  assert.equal(entry.p.entries[0].price,entry.b.close);
  assert.equal(entry.d.previousDecisionId,before.id);
  assert.equal(entry.d.opportunityId,before.opportunityId);
  assert.deepEqual(entry.d.anchors,before.anchors);
  await ev(`updatePositionStop(positions[0],positions[0].stop*0.95,'给结构留空间');prCheckpoint();
    closePosObj(positions[0],0.5,'减半检查');prEl('trainerReveal').click();`);
  const done=await ev('tsLoad()[0]');
  assert.equal(done.trades.length,2);
  assert.equal(done.trades[1].exit_reason,'练习结束结算');
  assert.equal(done.trades[1].initial_stop,entry.b.low);
  assert.equal(done.research.events.filter(e=>e.action==='stop_change').length,1);
  assert.equal(done.research.decisions[1].outcomes.length,3);
  assert.ok(done.research.resultsThrough>done.research.visibleThrough);
  assert.equal(await ev('currentPracticeStats().orders'),1);
  assert.equal(await ev('currentPracticeStats().exits'),2);
  await ev("prEl('trReviewBtn').click();prEl('practiceBatch').open=true;prRenderBatch()");
  assert.ok((await ev("prEl('practiceBatchResults').textContent")).includes('e2e-ma20'));
  assert.ok((await ev("prEl('trHistory').textContent")).includes('延续等待'));
  if(process.env.PRACTICE_SCREENSHOT){
    const image=await call('Page.captureScreenshot',{format:'png'});
    await writeFile(process.env.PRACTICE_SCREENSHOT,Buffer.from(image.data,'base64'));
  }

  await ev("prEl('prExport').click()");
  let downloaded;
  for(let i=0;i<50&&!downloaded;i++){
    downloaded=(await readdir(out)).find(x=>x.endsWith('.json'));if(!downloaded)await sleep(100);
  }
  const dump=JSON.parse(await readFile(join(out,downloaded),'utf8'));
  assert.equal(dump.format,'opportunity-practice-v2');
  assert.equal(dump.sessions[0].research.decisions[1].opportunityId,before.opportunityId);
  const timeOrigin=await ev('performance.timeOrigin');
  await call('Page.reload',{ignoreCache:true});
  await until(`performance.timeOrigin!==${timeOrigin} && window.__INIT_OK===true && tsLoad().some(s=>s.research?.rule==="e2e-ma20")`);

  const legacyFile=process.env.PRACTICE_LEGACY_JSON||join(out,'legacy.json');
  const legacySample={id:1000,ts:1000,sym:'ETH',tf:'1D',cond:'完成',capital:1000,endCapital:1000,pnl:0,trades:[]};
  if(!process.env.PRACTICE_LEGACY_JSON)await writeFile(legacyFile,JSON.stringify({format:'opportunity-practice-v1',
    payload:{local:{chan_trainer_sessions_v1:JSON.stringify([legacySample])}}}));
  const {root}=await call('DOM.getDocument');
  const {nodeId}=await call('DOM.querySelector',{nodeId:root.nodeId,selector:'#prImport'});
  await call('DOM.setFileInputFiles',{nodeId,files:[legacyFile]});
  await until('prEl("practiceStorageStatus").textContent.includes("已导入")');
  const imported=await ev('tsLoad().filter(s=>!s.research).length');
  assert.ok(imported>0);
  await call('DOM.setFileInputFiles',{nodeId,files:[legacyFile]});
  await until('prEl("practiceStorageStatus").textContent.includes("已导入 0 局")');
  assert.equal(await ev('tsLoad().filter(s=>!s.research).length'),imported);
  assert.deepEqual(errors,[]);
  await ev('PracticeStore.clear()');
  console.log('PASS practice: chart anchors, wait→entry, initial stop, partial closes, 5/20/60 paths, export, reload, legacy import and dedup');
}finally{
  ws.close();
  await fetch(endpoint+'/json/close/'+tab.id).catch(()=>{});
  await rm(out,{recursive:true,force:true});
}
