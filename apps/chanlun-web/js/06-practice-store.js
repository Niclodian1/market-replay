// Sessions live in IndexedDB; the old localStorage archive remains readable.
const PracticeStore = (() => {
  const oldKey='chan_trainer_sessions_v1', fallbackKey='chan_practice_fallback_v2';
  const parse=k=>{try{const v=JSON.parse(localStorage.getItem(k));return Array.isArray(v)?v:[];}catch{return [];}};
  const cache=new Map([...parse(oldKey),...parse(fallbackKey)].filter(s=>s&&s.id!=null).map(s=>[s.id,s]));
  const touched=new Set();
  let db, serial=Promise.resolve(), ownActive=null, cleared=false;
  const notify=msg=>{
    const el=document.getElementById('practiceStorageStatus');
    if(el)el.textContent=msg;
  };
  function fallback(error){
    try{
      localStorage.setItem(fallbackKey,JSON.stringify([...cache.values()]));
      notify('已保存到浏览器备用存档。');
    }catch{
      notify('浏览器未能保存练习。请立即导出 JSON 备份。');
      console.warn('练习存档写入失败',error);
    }
  }
  function transact(mode, fn){
    return new Promise((resolve,reject)=>{
      const tx=db.transaction('sessions',mode), store=tx.objectStore('sessions');
      let request=fn(store);
      tx.oncomplete=()=>resolve(request?.result);
      tx.onabort=tx.onerror=()=>reject(tx.error||new Error('练习存档事务失败'));
    });
  }
  const ready=(async()=>{
    try{
      db=await new Promise((resolve,reject)=>{
        const req=indexedDB.open('chan_practice_archive',1);
        req.onupgradeneeded=()=>req.result.createObjectStore('sessions',{keyPath:'id'});
        req.onsuccess=()=>resolve(req.result);
        req.onerror=()=>reject(req.error);
        req.onblocked=()=>reject(new Error('练习存档被其他页面占用'));
      });
      const rows=await transact('readonly',store=>store.getAll());
      if(cleared)return;
      const byId=new Map(rows.map(s=>[s.id,s]));
      const missing=[...cache.values()].filter(s=>!touched.has(s.id)&&(!byId.has(s.id)||(s.savedAt||0)>(byId.get(s.id).savedAt||0)));
      for(const s of rows)if(!touched.has(s.id)&&(s.savedAt||0)>=(cache.get(s.id)?.savedAt||0))cache.set(s.id,s);
      if(missing.length)await transact('readwrite',store=>missing.forEach(s=>store.put(s)));
    }catch(error){ db=null; fallback(error); }
  })();
  function run(fn){
    serial=serial.then(()=>ready).then(async()=>{
      if(!db){fallback();return;}
      try{await transact('readwrite',fn);}
      catch(error){fallback(error);}
    });
    return serial;
  }
  function put(s){
    const row=structuredClone({...s,savedAt:Date.now()});
    cache.set(row.id,row);touched.add(row.id);
    return run(store=>store.put(row));
  }
  function list(includeActive=false){
    return [...cache.values()].filter(s=>includeActive||s.id!==ownActive||s.cond!=='进行中')
      .map(s=>s.cond==='进行中'&&s.id!==ownActive ? {...s,cond:'中断'} : s)
      .sort((a,b)=>b.ts-a.ts||b.id-a.id);
  }
  function clear(){
    cleared=true;
    cache.forEach((_,id)=>touched.add(id));cache.clear();
    try{localStorage.removeItem(oldKey);localStorage.removeItem(fallbackKey);}catch{}
    return run(store=>store.clear());
  }
  return {ready,put,list,clear,active:id=>{ownActive=id;}};
})();
