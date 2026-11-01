import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../js/13-annotate.js',import.meta.url),'utf8');
const functions=source.slice(source.indexOf('function annImportData('),source.indexOf('function annTip('))
  +source.slice(source.indexOf('function annExportData('),source.indexOf('function annExport()'));
const time=Date.UTC(2025,0,31)/1000;
function session(){
  const tips=[];
  const context={ANN:{decisions:{}},currentSym:'BTC',activeTF:'1D',ANN_SOURCES:{manual_buy_v1:{name:'我的买点'}},
    candlesFor:()=>[{time,open:100,high:110,low:90,close:105}],fmtTime:t=>String(t),annDate:t=>new Date(t*1000).toISOString().slice(0,10),
    annTip:t=>tips.push(t),annSave:()=>true,annBuildSigs(){},annRender(){},annSumRender(){},
    annFeatures(){throw new Error('手选样本不能退回含未来收益的旧特征计算');}};
  vm.createContext(context);vm.runInContext(functions,context);
  return {context,tips};
}
const manual={source:'manual_buy_v1',decision:'yes',manual:{schema:'manual-buy/1',reason:'按我的压力位突破收盘买入',pattern:'free',execution:{basis:'selected_bar_close',price:105}},
  manualEvidence:{schema:'manual-buy/1',valid:true,decisionTime:time,execution:{basis:'selected_bar_close',price:105}},features:{time,price:{close:105}}};
test('新正样本与旧拒绝样本各存各的，导入导出保留理由、执行价和证据',()=>{
  const {context:c}=session();
  const legacy={source:'ma20_simple_v2',decision:'no',reasons:['力度不够'],features:{time,price:{close:105}}};
  c.annImportData(JSON.stringify({symbol:'BTC',tf:'1D',items:[manual,legacy]}));
  const out=JSON.parse(JSON.stringify(c.annExportData()));
  assert.equal(out.count,2);assert.equal(out.unmarkedPolicy,'unknown');
  const saved=out.items.find(i=>i.source==='manual_buy_v1');
  assert.deepEqual(saved.manual,manual.manual);assert.deepEqual(saved.manualEvidence,manual.manualEvidence);
  assert.equal(saved.annotationStage,'positive_collection');assert.equal(saved.sampleStatus,'confirmed_positive');
  assert.equal(out.items.find(i=>i.source==='ma20_simple_v2').decision,'no');
});
test('草稿也能备份，移除的点不混入有效样本；未知位置不生成负样本',()=>{
  const {context:c}=session();
  c.annImportData(JSON.stringify({items:[{...manual,decision:null}]}));
  const out=c.annExportData();assert.equal(out.count,1);assert.equal(out.items[0].sampleStatus,'draft');
  assert.equal(out.items[0].decision,null);
  c.ANN.decisions[`BTC|1D|manual_buy_v1|${time}`].manual.archived=true;
  assert.equal(c.annExportData().count,0);
});
test('不同品种／周期的导入被拒绝，已有标注不会被错配覆盖',()=>{
  const {context:c,tips}=session();
  for(const metadata of [{symbol:'ETH',tf:'1D'},{symbol:'BTC',tf:'4H'}]){
    c.annImportData(JSON.stringify({...metadata,items:[manual]}));
    assert.equal(Object.keys(c.ANN.decisions).length,0);
  }
  assert.equal(tips.length,2);
});
