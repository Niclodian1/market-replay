const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const origin = 'https://example.test';
const scope = origin + '/kpractice/';
const events = {};
const stores = new Map();
const keyOf = req => new URL(typeof req === 'string' ? req : req.url, scope).href;
const caches = {
  async keys() { return [...stores.keys()]; },
  async delete(key) { return stores.delete(key); },
  async open(name) {
    if (!stores.has(name)) stores.set(name, new Map());
    const data = stores.get(name);
    return {
      async keys() { return [...data.keys()].map(url => new Request(url)); },
      async match(req) { return data.get(keyOf(req))?.clone(); },
      async put(req, response) { data.set(keyOf(req), response.clone()); },
      async addAll(urls) { for (const url of urls) data.set(keyOf(url), new Response('shell')); },
    };
  },
};
let online = true;
const context = vm.createContext({
  caches, URL, console, Request, Response,
  fetch: async input => {
    if (!online) throw new Error('offline');
    const url = typeof input === 'string' ? input : input.url;
    if (url.includes('/data/') && !url.endsWith('catalog.json')) return new Response('net-kline');
    if (url.includes('index.html') || /kpractice\/?$/.test(url)) return new Response('fresh-html');
    return new Response('shell');
  },
  self: { registration: { scope }, location: { origin }, clients: { claim: async () => {} },
    skipWaiting: async () => {}, addEventListener: (name, fn) => { events[name] = fn; } },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../mobile/sw.js'), 'utf8'), context);
(async () => {
  const old = await caches.open('kpractice-v25');
  await old.put('./data/btc.json', new Response('cached candles'));
  await old.put('./data/catalog.json', new Response('old catalog'));
  const unrelated = await caches.open('another-app');
  await unrelated.put(origin + '/other/file', new Response('preserve me'));
  let work;
  events.install({ waitUntil: promise => { work = promise; } }); await work;
  events.activate({ waitUntil: promise => { work = promise; } }); await work;
  assert.equal(await (await (await caches.open('kpractice-data-v1')).match('./data/btc.json')).text(), 'cached candles');
  assert.ok(!(await caches.keys()).includes('kpractice-v25'));
  assert.ok((await caches.keys()).includes('another-app'));
  const fetchThrough = async url => {
    let response;
    const extra = [];
    events.fetch({
      request: new Request(url),
      respondWith: promise => { response = promise; },
      waitUntil: p => { extra.push(p); },
    });
    const text = await (await response).text();
    await Promise.all(extra);
    return text;
  };
  assert.equal(await fetchThrough(scope + 'index.html'), 'fresh-html');
  assert.equal(await fetchThrough(scope + 'data/eth.json'), 'net-kline');
  online = false;
  assert.equal(await fetchThrough(scope + 'data/btc.json'), 'cached candles');
  assert.equal(await fetchThrough(scope + 'data/eth.json'), 'net-kline');
  assert.equal(await fetchThrough(scope + 'index.html'), 'fresh-html');
  console.log('PASS service-worker upgrade retains candles; kline put is not on the response path');
})().catch(e => { console.error(e); process.exitCode = 1; });
