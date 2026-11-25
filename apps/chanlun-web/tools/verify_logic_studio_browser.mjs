// 每品种 S 级红/绿悬浮框 + 收敛版 2-Tab 逻辑工坊 全链路无头 Chrome 端到端验证。
// 用法: 启动静态服务(python3 -m http.server 8877 --directory apps/chanlun-web),
//       Chrome --headless=new --remote-debugging-port=9336,
//       再运行: node apps/chanlun-web/tools/verify_logic_studio_browser.mjs
// 环境变量: DESKTOP_TEST_URL, CDP_PORT, SHOT_DIR(可选, 输出验收截图目录)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const origin = process.env.DESKTOP_TEST_URL || 'http://127.0.0.1:8877';
const cdpPort = process.env.CDP_PORT || '9336';
const shotDir = process.env.SHOT_DIR || '';

const tabs = await (await fetch(`http://127.0.0.1:${cdpPort}/json/list`)).json();
const page = tabs.find(t => t.type === 'page');
if (!page) throw new Error('no CDP page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let id = 0;
const pending = new Map();
const errors = [];
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id) {
    const p = pending.get(m.id);
    pending.delete(m.id);
    m.error ? p.reject(m.error) : p.resolve(m.result);
  } else if (m.method === 'Runtime.exceptionThrown') {
    errors.push((m.params.exceptionDetails.exception?.description || '').split('\n')[0]);
  }
};
const call = (method, params = {}) => new Promise((resolve, reject) => {
  const n = ++id;
  pending.set(n, { resolve, reject });
  ws.send(JSON.stringify({ id: n, method, params }));
});
const ev = async expression => {
  const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 500));
  return r.result.value;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (expr, timeout = 25000) => {
  const s = Date.now();
  while (Date.now() - s < timeout) {
    if (await ev(expr)) return;
    await sleep(120);
  }
  throw new Error('Timed out: ' + expr);
};
const saveShot = async name => {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  const { data } = await call('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(shotDir, name), Buffer.from(data, 'base64'));
  console.log('  📸 saved', name);
};

await call('Runtime.enable');
await call('Page.enable');
await call('Network.enable');
await call('Network.setCacheDisabled', { cacheDisabled: true });
await call('Emulation.setDeviceMetricsOverride', { width: 1520, height: 920, deviceScaleFactor: 2, mobile: false });
await ev(`try { window.onbeforeunload = null; if (typeof TR !== 'undefined') TR.active = false; localStorage.clear(); } catch {}`);
await call('Page.navigate', { url: origin + '/index.html' });
await until(`window.__INIT_OK === true && typeof window.openLogicStudio === 'function' && typeof window.getCurrentSBg === 'function'`);
await sleep(900);
errors.length = 0;

// 1. 极简单行前端 + 3 Tab 侧栏 + 主屏 S 级悬浮框挂载校验
const layoutCheck = await ev(`(() => {
  const tb = document.querySelector('.toolbar').getBoundingClientRect();
  const ctxDisp = getComputedStyle(document.getElementById('ctxBar')).display;
  const removedIds = ['labBtn','maSlopeBtn','maTrendBtn','maDualBtn','annBtn','manualAddToolbar','cover67IndicatorToggle','chanDetailsBtn','statsCard'];
  const leftovers = removedIds.filter(x => document.getElementById(x) !== null);
  const tabs = [...document.querySelectorAll('#sideTabs .side-tab')].map(b => b.dataset.sideTab);
  const activeTab = document.querySelector('#sideTabs .side-tab.active')?.dataset.sideTab;
  const sbgInChart = !!document.querySelector('.chart-box #sBgFloat');
  return { tbH: Math.round(tb.height), ctxDisp, leftovers, tabs, activeTab, hasResizer: !!document.getElementById('sideResizer'), sbgInChart };
})()`);
assert.ok(layoutCheck.tbH <= 50, '单行顶部栏高度应 <= 50px, 实际=' + layoutCheck.tbH);
assert.equal(layoutCheck.ctxDisp, 'none', '常态下上下文副栏应隐藏');
assert.deepEqual(layoutCheck.leftovers, [], '历史冗余控件应已移除');
assert.deepEqual(layoutCheck.tabs, ['logic', 'trade', 'practice'], '侧栏应收敛为 3 Tab');
assert.equal(layoutCheck.activeTab, 'logic', '默认应聚焦逻辑 Tab');
assert.equal(layoutCheck.hasResizer, true, '应存在侧栏拖拽手柄');
assert.equal(layoutCheck.sbgInChart, true, '#sBgFloat 应挂载在 .chart-box 内');
console.log('✓ 1. 极简单行主栏(' + layoutCheck.tbH + 'px) + 3 Tab + 主屏 S 级悬浮框就绪');

// 2. 每品种独立 S 级红/绿悬浮框：BTC 绿框(看多) vs NVDA 红框(看空) + 盲测隐藏 + 快速下单逆势 0.5% 联动
await ev(`(() => {
  document.querySelector('#sBgFloat [data-sbg-act="edit"]').click();
})()`);
await sleep(100);
await ev(`(() => {
  document.querySelector('#sBgFloat [data-sbg-bias="bull"]').click();
  document.getElementById('sbgHeadlineInp').value = '降息路径预期升温 + 现货 ETF 持续净流入，宏观流动性与风险偏好共振';
  document.querySelector('#sBgFloat [data-sbg-act="save"]').click();
})()`);
await sleep(180);
const btcHud = await ev(`({
  cls: document.getElementById('sBgFloat').className,
  sideCls: document.querySelector('#logicSideHost .ls-side-sbg')?.className || '',
  rec: window.getCurrentSBg(),
})`);
assert.ok(btcHud.cls.includes('sbg-bull'), 'BTC 悬浮框应切换为绿色看多样式');
assert.ok(btcHud.sideCls.includes('sbg-bull'), '侧栏 S 级卡片应同步 BTC 绿色看多样式');
assert.equal(btcHud.rec.bias, 'bull');
await saveShot('shot_01_sbg_bull_dark.png');
console.log('✓ 2a. BTC S 级悬浮框绿色看多编辑与侧栏同步正常');

// Cmd+K 品种分层（核心主池 30 vs 全部 46）+ 模糊搜索切到 TLT/TSM/NVDA + 验证最小充分信息集状态带与财报 E 标记
await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }))`);
await until(`!document.getElementById('symPalette')?.hidden`);
const coreLen = await ev(`document.querySelectorAll('#symPalette .sp-item').length`);
assert.equal(coreLen, 30, 'Cmd+K 默认应显示核心主池 30 个品种');
await saveShot('ctx_palette_core.png');
await ev(`document.querySelector('#symPalette [data-sp-scope="all"]').click()`);
assert.equal(await ev(`document.querySelectorAll('#symPalette .sp-item').length`), 46, '切到全部应显示 46 个品种');
await ev(`document.querySelector('#symPalette [data-sp-scope="core"]').click()`);
// 先验证新纳入的宏观品种 TLT、跨行业个股 TSM 以及港股 RS/HSI 基准升级
await ev(`(() => {
  const inp = document.querySelector('#symPalette .sp-input');
  inp.value = '20年美债';
  inp.dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await sleep(120);
assert.equal(await ev(`document.querySelectorAll('#symPalette .sp-item').length`), 1, '搜20年美债应命中 TLT');
await ev(`document.querySelector('#symPalette .sp-item').click()`);
await until(`currentSym === 'TLT'`);
await sleep(220);
const tltCheck = await ev(`({ bars: candlesFor('raw').length, txt: document.getElementById('ctxStrip')?.textContent || '' })`);
assert.ok(tltCheck.bars > 5000 && tltCheck.txt.includes('RS/SPX'), 'TLT 应加载完整日线并显示 RS/SPX: ' + JSON.stringify(tltCheck));

await ev(`switchSymbol('TSM')`);
await until(`currentSym === 'TSM'`);
await sleep(220);
const tsmEarn = await ev(`(window.MarketContext?.earningsMarkers('raw') || []).length`);
assert.ok(tsmEarn >= 10, 'TSM 应绘制历史财报日 E 标记，实际=' + tsmEarn);

await ev(`switchSymbol('0700_HK')`);
await until(`currentSym === '0700_HK'`);
await sleep(220);
const hkTxt = await ev(`document.getElementById('ctxStrip')?.textContent || ''`);
assert.ok(hkTxt.includes('RS/HSI'), '腾讯 RS 基准应升级为 HSI: ' + hkTxt);

// 切回 NVDA 完成红框与上下文条回归
await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true }))`);
await until(`!document.getElementById('symPalette')?.hidden`);
await ev(`(() => {
  const inp = document.querySelector('#symPalette .sp-input');
  inp.value = '英伟达';
  inp.dispatchEvent(new Event('input', { bubbles: true }));
})()`);
await sleep(120);
assert.equal(await ev(`document.querySelectorAll('#symPalette .sp-item').length`), 1, '搜英伟达应命中 1 项');
await ev(`document.querySelector('#symPalette .sp-item').click()`);
await until(`currentSym === 'NVDA'`);
await sleep(300);

// 验证 NVDA 的「最小充分信息集」状态带（10Y / DXY / VIX / RS/NDX / 52W）与历史财报标记 E+/E-
const ctxCheck = await ev(`(() => {
  const txt = document.getElementById('ctxStrip')?.textContent || '';
  const earnMarks = window.MarketContext?.earningsMarkers('raw') || [];
  return { txt, earnCount: earnMarks.length };
})()`);
assert.ok(ctxCheck.txt.includes('10Y') && ctxCheck.txt.includes('DXY') && ctxCheck.txt.includes('VIX'), '状态带应显示 10Y/DXY/VIX: ' + ctxCheck.txt);
assert.ok(ctxCheck.txt.includes('RS/NDX') && ctxCheck.txt.includes('52W'), '状态带应显示 RS/NDX 与 52W 水位: ' + ctxCheck.txt);
assert.ok(ctxCheck.earnCount >= 10, 'NVDA 应绘制历史财报日 E 标记，实际=' + ctxCheck.earnCount);
console.log('✓ 2a+. Cmd+K 核心主池(30)/全部(46) + TLT/TSM 新品种切换 + 宏观水位 + RS + 52W + 财报标记(NVDA ' + ctxCheck.earnCount + '条 / TSM ' + tsmEarn + '条)全部就绪');

const nvdaInitial = await ev(`({
  cls: document.getElementById('sBgFloat').className,
  rec: window.getCurrentSBg(),
})`);
assert.ok(nvdaInitial.cls.includes('sbg-neutral'), '切到 NVDA 后悬浮框应重置为 NVDA 独立记录(中性)');
assert.equal(nvdaInitial.rec.headline, '', 'NVDA 初始背景应为空，不串用 BTC 背景');

// 为 NVDA 设置红色看空 S 级背景
await ev(`(() => {
  document.querySelector('#sBgFloat [data-sbg-act="edit"]').click();
})()`);
await sleep(80);
await ev(`(() => {
  document.querySelector('#sBgFloat [data-sbg-bias="bear"]').click();
  document.getElementById('sbgHeadlineInp').value = 'AI 芯片出口监管升级杀估值 + 资本开支预期放缓，空头主线压制';
  document.querySelector('#sBgFloat [data-sbg-act="save"]').click();
})()`);
await sleep(180);
assert.ok((await ev(`document.getElementById('sBgFloat').className`)).includes('sbg-bear'), 'NVDA 悬浮框应切换为红色看空样式');
await saveShot('shot_02_nvda_bear_dark.png');

// 浅色主题下核验 NVDA 红框观感
await ev(`document.getElementById('themeBtn').click()`);
await sleep(220);
assert.equal(await ev(`document.documentElement.dataset.theme`), 'light');
await saveShot('shot_03_nvda_bear_light.png');
await ev(`document.getElementById('themeBtn').click()`);
await sleep(180);

// 折叠成单行胶囊与展开
await ev(`document.querySelector('#sBgFloat [data-sbg-act="toggle-collapse"]').click()`);
await sleep(100);
assert.ok((await ev(`document.getElementById('sBgFloat').classList.contains('is-collapsed')`)), '点击收起应变为单行胶囊');
await ev(`document.querySelector('#sBgFloat [data-sbg-act="toggle-collapse"]').click()`);
await sleep(100);
assert.ok(!(await ev(`document.getElementById('sBgFloat').classList.contains('is-collapsed')`)), '再次点击应恢复展开');

// 盲测模式必须自动隐藏 S 级悬浮框，防泄露品种信息
const blindDisp = await ev(`(() => {
  document.body.classList.add('trainer-blind');
  const d = getComputedStyle(document.getElementById('sBgFloat')).display;
  document.body.classList.remove('trainer-blind');
  return d;
})()`);
assert.equal(blindDisp, 'none', '盲测模式必须自动隐藏 #sBgFloat');

// 在 NVDA 红框(看空)下进入复盘按 B 做多，验证快速下单栏逆势警示与 0.5% 算仓联动
const nvdaT = await ev(`(() => { const cs = candlesFor('raw'); return cs[Math.floor(cs.length * 0.7)].time; })()`);
await ev(`enterReplay(${nvdaT})`);
await sleep(220);
await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'b', bubbles: true }))`);
await sleep(180);
const qoCounter = await ev(`(() => {
  const warn = document.querySelector('#quickOrder .qo-sbg-warn')?.textContent || '';
  const btn = document.querySelector('#quickOrder .qo-risk-btn');
  return { warn, btnTxt: btn?.textContent || '', btnRp: btn?.dataset.qoRp || '' };
})()`);
assert.ok(qoCounter.warn.includes('逆 S 级红框') && qoCounter.warn.includes('0.5%'), '红框下快速做多应显示逆势警示: ' + qoCounter.warn);
assert.equal(qoCounter.btnRp, '0.5', '红框下快速做多算仓按钮应自动切为 0.5%');
assert.ok(qoCounter.btnTxt.includes('0.5%'), '算仓按钮文案应提示 0.5%: ' + qoCounter.btnTxt);
await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); exitReplay();`);
await sleep(200);

// 切回 BTC，验证每品种独立记忆完好保留
await ev(`switchSymbol('BTC')`);
await until(`currentSym === 'BTC'`);
await sleep(250);
const btcAgain = await ev(`({ cls: document.getElementById('sBgFloat').className, rec: window.getCurrentSBg() })`);
assert.ok(btcAgain.cls.includes('sbg-bull') && btcAgain.rec.headline.includes('ETF'), '切回 BTC 后原有绿框与背景应完好保留');
console.log('✓ 2b. NVDA 红框独立记忆 + 深浅主题 + 胶囊折叠 + 盲测隐藏 + 快速下单逆势 0.5% 联动全部通过');

// 3. 收敛版 2-Tab 逻辑工坊 + 按需「📖 速查清单」抽屉校验
await ev(`window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', bubbles: true }))`);
await until(`!document.getElementById('logicStudioRoot').hidden`);
const studioTabs = await ev(`[...document.querySelectorAll('#logicStudioRoot .ls-nav-tab')].map(b => b.dataset.lsTab)`);
assert.deepEqual(studioTabs, ['gate', 'review'], '逻辑工坊应收敛为 2 个 Tab: gate / review');
assert.ok(await ev(`!!document.querySelector('#logicStudioRoot [data-ls-ref="toggle"]')`), '应存在按需展开的速查清单按钮');

// 3a. 逆绿框做空提示 + 0.5% 按钮 + 黑天鹅/冲动一票否决
await ev(`document.querySelector('#logicStudioRoot [data-g-side="空"]').click()`);
await sleep(100);
assert.ok((await ev(`document.querySelector('#logicStudioRoot .ls-counter-bar')?.textContent || ''`)).includes('逆 S 级绿框'), 'BTC 绿框下选做空应触发逆势红黄条提醒');
await ev(`document.querySelector('#logicStudioRoot [data-g-side="多"]').click()`);
await sleep(100);
await ev(`(() => {
  const s = document.querySelector('#logicStudioRoot [data-g-swan="hasSystemicCrisis"]');
  s.checked = true; s.dispatchEvent(new Event('change', { bubbles: true }));
  const imp = document.querySelector('#logicStudioRoot [data-g-impulse]');
  imp.checked = true; imp.dispatchEvent(new Event('change', { bubbles: true }));
})()`);
await sleep(100);
const vetoCheck = await ev(`({
  txt: document.querySelector('#logicStudioRoot .ls-modal-body').textContent,
  lockDis: document.querySelector('#logicStudioRoot [data-g-act="lock-only"]').disabled,
})`);
assert.ok(vetoCheck.txt.includes('S 级黑天鹅一票否决'), '勾选黑天鹅冷却期应显示一票否决警示');
assert.equal(vetoCheck.lockDis, true, '勾选冲动一票否决后锁定按钮应禁用');

// 恢复开关并展开「📖 速查清单」校验充分定价 4 项验收 + 币本位 OI + 冲突对照 + 模式/禁忌
await ev(`(() => {
  const s = document.querySelector('#logicStudioRoot [data-g-swan="hasSystemicCrisis"]');
  s.checked = false; s.dispatchEvent(new Event('change', { bubbles: true }));
  const imp = document.querySelector('#logicStudioRoot [data-g-impulse]');
  imp.checked = false; imp.dispatchEvent(new Event('change', { bubbles: true }));
  document.querySelector('#logicStudioRoot [data-ls-ref="toggle"]').click();
})()`);
await sleep(120);
await ev(`(() => { const c = document.querySelector('[data-p-chk="downstreamResilient"]'); c.checked = true; c.dispatchEvent(new Event('change', { bubbles: true })); })()`);
assert.ok((await ev(`document.querySelector('.ls-ref-drawer').textContent`)).includes('上游仍在失血'), '仅下游抗跌应触发上游失血警报');
await ev(`(() => {
  for (const k of ['upstreamBlunted','midstreamDiverged','realDeleveraging','secondBadNewsTest','lowVolRetest']) {
    const c = document.querySelector('[data-p-chk="' + k + '"]');
    c.checked = true; c.dispatchEvent(new Event('change', { bubbles: true }));
  }
})()`);
assert.ok((await ev(`document.querySelector('.ls-ref-drawer').textContent`)).includes('进入充分定价观察池'), '前三项+上游止血应判为进入观察池');
await ev(`(() => { const c = document.querySelector('[data-p-chk="rightSideBreakout"]'); c.checked = true; c.dispatchEvent(new Event('change', { bubbles: true })); })()`);
const refTxt = await ev(`document.querySelector('.ls-ref-drawer').textContent`);
assert.ok(refTxt.includes('右侧做多扳机就绪'), '4 项全通应判为右侧做多扳机就绪');
assert.ok(refTxt.includes('CoinGlass') && refTxt.includes('CoinAnk') && refTxt.includes('Coinalyze'), '速查清单应含三大币本位 OI 监控外链');
assert.ok(refTxt.includes('逻辑冲突快速决策对照表') && refTxt.includes('模式卡片库'), '速查清单应内置冲突对照与模式/禁忌库');
await saveShot('shot_04_studio_gate_ref.png');
await ev(`document.querySelector('[data-ls-ref="close"]').click(); closeLogicStudio();`);
console.log('✓ 3. 收敛版 2-Tab 工坊 + 逆势护栏 + 速查清单抽屉全部通过');

// 4. 全闭环：进入复盘 → 30 秒开仓卡锁定并一键开仓 → 步进平仓自动 MAE/MFE → 复盘本 2x2 归因与合盘统计
const repPair = await ev(`(() => {
  const cs = candlesFor('raw');
  for (let i = Math.floor(cs.length * 0.55); i < cs.length - 30; i++) {
    if (cs[i + 10].close <= cs[i].close * 1.06) continue;
    let hit = false;
    for (let k = i + 1; k <= i + 10; k++) if (cs[k].low <= cs[i].low) { hit = true; break; }
    if (!hit) return { t0: cs[i].time, tMid: cs[i + 4].time, t1: cs[i + 10].time, entry: cs[i].close };
  }
  return null;
})()`);
assert.ok(repPair, '应找到可回放样本区间');
await ev(`enterReplay(${repPair.t0})`);
await sleep(250);
assert.equal(await ev(`getComputedStyle(document.getElementById('ctxBar')).display`), 'flex', '复盘态上下文副栏应展开');

await ev(`openLogicStudio('gate', {
  side: '多',
  whyNow: '降息预期升温 + 现货 ETF 持续净流入，放量突破震荡箱体上沿',
  techEntry: '放量阳线突破箱体上沿 + MA20 拐头向上',
  targetPrice: +(${repPair.entry} * 1.15).toFixed(2),
  exitAnchor: '空头清算放量滞涨兑现',
})`);
await sleep(150);
await saveShot('shot_05_studio_gate.png');

await ev(`document.querySelector('[data-g-act="lock-and-open"]').click()`);
await sleep(250);
assert.equal(await ev(`positions.length`), 1, '锁定并开仓应创建 1 笔持仓');
assert.ok(await ev(`!!positions[0].logicCardId`), '持仓应绑定 logicCardId');
assert.ok(await ev(`!!document.querySelector('.pos-logic-pill.linked')`), '持仓卡应显示已锁准入卡徽章');

await ev(`setReplayT(${repPair.tMid})`);
await sleep(120);
await ev(`setReplayT(${repPair.t1})`);
await sleep(150);
await ev(`closePosObj(positions[0], 1, '逻辑兑现止盈')`);
await sleep(200);

// 打开「2. 复盘本」核对时间胶囊只读 + MAE/MFE 自动计算 + 2×2 归因 + 顶条合规大盘
await ev(`openLogicStudio('review')`);
await sleep(200);
const reviewCheck = await ev(`(() => {
  const lockedInputs = document.querySelectorAll('.ls-capsule-locked input, .ls-capsule-locked textarea, .ls-capsule-locked select').length;
  const txt = document.querySelector('.ls-metrics-strip')?.textContent || '';
  return { lockedInputs, txt };
})()`);
assert.equal(reviewCheck.lockedInputs, 0, '模块一时间胶囊锁定后必须零可编辑输入');
assert.ok(reviewCheck.txt.includes('R') && reviewCheck.txt.includes('%'), '平仓后应自动计算实际 R / MAE / MFE: ' + reviewCheck.txt);

await ev(`(() => {
  document.querySelector('[data-d-score="logicScore"][data-val="5"]').click();
  document.querySelector('[data-d-score="chartScore"][data-val="4"]').click();
  document.querySelector('[data-d-quad="rr"]').click();
})()`);
await sleep(180);
const topTxt = await ev(`document.querySelector('.ls-review-top')?.textContent || ''`);
assert.ok(topTxt.includes('合规率 100%'), '复盘本顶条应实时显示合规率 100%: ' + topTxt);
assert.ok(topTxt.includes('1笔 (100%)'), '2x2 四象限条应实时更新数量与占比: ' + topTxt);

// 测试 2x2 顶条一键筛选交互
await ev(`document.querySelector('[data-a-quad="wr"]').click()`);
await sleep(100);
assert.equal(await ev(`document.querySelectorAll('.ls-arc-item').length`), 0, '筛选毒药单应为 0 条');
await ev(`document.querySelector('[data-a-quad="wr"]').click()`);
await sleep(100);
assert.equal(await ev(`document.querySelectorAll('.ls-arc-item').length`), 1, '取消筛选应恢复 1 条');
await saveShot('shot_06_studio_review.png');
console.log('✓ 4. 30秒开仓卡锁存开仓 → 平仓自动 MAE/MFE → 复盘本 2x2 归因与合盘筛选全闭环通过');

assert.deepEqual(errors, [], '全流程运行时零 JS 异常: ' + JSON.stringify(errors));
console.log('=== ALL CONVERGED LOGIC STUDIO & PER-SYMBOL S-TIER HUD E2E CHECKS PASSED ===');
ws.close();
