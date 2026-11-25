// js/05-journal.js — 账户制交易日志(v2): 多笔持仓 / 比例平仓 / 爆仓 / CSV
// ---- 复盘开单 + 交易日志(localStorage 持久化, CSV 导出) ----
// 支持盈利加仓(只能用本笔订单浮盈: 还可加 = 浮盈 − 已投用加仓本金) + 一键加满 + 平仓逻辑必填;
// 练习(盲测)模式加仓理由可留空, 自动记为「浮盈加仓」; 复盘模式仍必填, 保持规则沉淀。
// 浮动盈亏随回放逐根K线实时刷新(updateLivePnl 由 refreshAll 驱动)。
// v2 账户制: 每模式独立本金(初始100,000U), 开仓占用保证金、平仓结算盈亏;
// 支持多笔同时持仓与按比例部分平仓; 单笔亏损≥其保证金自动离场,
// 账户净值≤0 视为爆仓(练习强制结束, 复盘重置本金)。盈利加仓仍限本笔浮盈。
const JKEY = 'chan_journal_v1';
const ACCT_KEY = 'chan_account_v2';
const INIT_CAPITAL = 100000;
const jLoad = () => { try { return JSON.parse(localStorage.getItem(JKEY)) || []; } catch { return []; } };
function jSave(arr){
  try{localStorage.setItem(JKEY,JSON.stringify(arr));}
  catch(error){
    if(!trLocked()||!TR.research)throw error;
    console.warn('交易日志写入失败；练习操作仍会写入练习存档。',error);
  }
}
const acctLoad = () => { try { return JSON.parse(localStorage.getItem(ACCT_KEY)) || {}; } catch { return {}; } };
const acctSave = o => localStorage.setItem(ACCT_KEY, JSON.stringify(o));
const curModeKey = () => (TR.active && !TR.revealed) ? 'trainer' : 'replay';
const acctBal = () => {
  const a = acctLoad(), m = curModeKey();
  return typeof a[m] === 'number' ? a[m] : INIT_CAPITAL;
};
const acctAdj = d => {
  const a = acctLoad(), m = curModeKey();
  const base = typeof a[m] === 'number' ? a[m] : INIT_CAPITAL;
  a[m] = +(base + d).toFixed(6);
  acctSave(a);
};
const acctResetTo = v => { const a = acctLoad(); a[curModeKey()] = v; acctSave(a); };
const acctReset = () => acctResetTo(INIT_CAPITAL);

let positions = [];      // 多笔持仓 [{id, sym, tf, side, stop, entries:[{t,price,capital,leverage,logic}]}]
let posSeq = 1;
const posById = id => positions.find(p => p.id === id);
// 持仓绑定开仓时的品种+周期: 结算/浮盈/止损只在匹配的布局下执行,
// 否则切品种后 curBar 取到别品种价格 → 入场/出场价混源(天文数字盈亏 bug 的根源)
const posLayoutOk = pos => (pos.sym === currentSym || pos.sym === DATA.meta.symbol) && pos.tf === activeTF;

function curBar(){
  const cs = candlesFor(trLocked() ? 'raw' : activeView);
  return cs.length ? cs[cs.length-1] : null;
}
const posCapital = pos => pos.entries.reduce((a,e) => a + e.capital, 0);
// 账户实际占用的保证金 = 首笔开仓本金(随部分平仓等比缩水)。
// 加仓本金只能来自本笔浮盈(见 addBudget), 并非从余额划扣; 若把它也算进占用/平仓退还,
// 净值会凭空多出加仓额(旧版 bug: 加仓 A 后净值立刻 +A, 平仓时再退还 A)。
const posMargin = pos => pos.entries[0] ? pos.entries[0].capital : 0;
function posAvgEntry(pos){
  // 名义价值加权开仓价
  const nv = pos.entries.reduce((a,e) => a + e.price * e.capital * e.leverage, 0);
  const dn = pos.entries.reduce((a,e) => a + e.capital * e.leverage, 0);
  return dn ? nv / dn : 0;
}
function pnlOf(pos, price){
  const dir = pos.side === '多' ? 1 : -1;
  return pos.entries.reduce((a,e) => a + (price - e.price) / e.price * dir * e.leverage * e.capital, 0);
}
function posValue(pos, price){
  // 当前持仓金额(名义价值) = Σ 数量(币) × 现价; 数量 = 本金×杠杆/入场价, 多空同理
  return pos.entries.reduce((a,e) => a + e.capital * e.leverage / e.price, 0) * price;
}
// R 基准: 首次设止损时锁定"止损打到的亏损金额"(保证金×杠杆×价差%); 之后移动止损不改 R
function ensureRisk(pos){
  if (pos.r0 != null) return pos.r0;
  if (pos.stop == null) return null;
  const avg = posAvgEntry(pos);
  if (!avg) return null;
  const risk = Math.abs(avg - pos.stop) / avg * pos.entries.reduce((a,e) => a + e.capital * e.leverage, 0);
  if (!isFinite(risk) || risk <= 0) return null;
  pos.r0 = +risk.toFixed(6);
  return pos.r0;
}
// 当前盈利 R 倍数 = 浮动盈亏 / R 基准
function rNow(pos, price){
  const r0 = ensureRisk(pos);
  return r0 ? pnlOf(pos, price) / r0 : null;
}
const fmtR = r => (r >= 0 ? '+' : '') + r.toFixed(2) + 'R';
// 盈利加仓预算: 只能用本笔订单浮盈加仓。
// 还可加 = 当前浮动盈亏 − 已投用的加仓本金(首笔开仓本金不计入消耗);
// 浮盈回吐时余额随之收缩, 保底为 0(浮亏状态禁止加仓)。
function addBudget(pos){
  if (!pos || !posLayoutOk(pos)) return { u:0, used:0, avail:0 };
  const bar = curBar();
  const u = bar ? pnlOf(pos, bar.close) : 0;
  const used = pos.entries.slice(1).reduce((a,e) => a + e.capital, 0);
  return { u, used, avail: Math.max(0, u - used) };
}
// 账户净值 = 可用余额 + 全部在仓占用保证金 + 当前布局持仓浮动盈亏
// (跨品种持仓的保证金同样已从可用余额扣除, 切换品种时不能漏加否则净值虚减甚至误判爆仓)
const acctEquity = () => {
  const bar = curBar();
  const cur = positions.filter(p => posLayoutOk(p));
  const used = positions.reduce((a,p) => a + posMargin(p), 0);
  const floatPnl = bar ? cur.reduce((a,p) => a + pnlOf(p, bar.close), 0) : 0;
  return acctBal() + used + floatPnl;
};
// ---- 练习标记: 开仓/加仓/出场在图表K线上的位置 ----
const posColorById = pid => POS_COLORS[(pid - 1) % POS_COLORS.length];
// 汇总本局全部入场事件: 在仓的逐笔 + 已平仓记录里的逐笔(平仓后仍可回看入场位)
function practiceEntries(){
  const out = [];
  for (const pos of positions){
    pos.entries.forEach((e, i) => out.push({
      time: e.t, price: e.price, side: pos.side, isAdd: i > 0, pid: pos.id, color: posColor(pos),
    }));
  }
  for (const r of TR.session){
    const live = positions.find(p => p.id === r.pid);
    if (!live){
      // 已完全离场的仓位: 入场事件由记录补(部分平仓的在仓持仓已在上面提供, 不重复计)
      (r.entry_ts_list || []).forEach((t, i) => out.push({
        time: t, price: (r.entry_prices || [])[i] ?? r.entry, side: r.side, isAdd: i > 0,
        pid: r.pid, color: posColorById(r.pid), closed: true,
      }));
    }
    if (r.exit_ts != null) out.push({
      time: r.exit_ts, price: r.exit_price, side: r.side, isExit: true, pid: r.pid,
      color: r.pnl_u >= 0 ? t().up : t().down,
    });
  }
  return out.sort((a,b) => a.time - b.time);
}
const POS_EXPECTS = ['短线', '中线', '长线'];
const POS_EXP_CLS = { '短线': 'exp-short', '中线': 'exp-mid', '长线': 'exp-long' };
const EXIT_PRESETS = [
  { val: '保本', cls: 'preset-be' },
  { val: '止盈', cls: 'preset-tp' },
  { val: '保护利润', cls: 'preset-lock' },
];
const ADD_PRESETS = ['突破', '回踩', '顺势'];

const fmtLogic = (s, n=16) => { s = String(s||''); return s.length > n ? s.slice(0, n) + '…' : s; };
// ---- 乖离档位行(持仓卡): 精简单行, 详释放悬浮 tooltip ----
const BIAS_TIP = '乖离档位: MA20乖离=收盘对MA20的偏离%。'
  + '一档 ≥+20% 或滚动分位≥P97 → 减1/3(MA60乖离≥+30%可减半); '
  + '二档 ≥+30% 或≥P99 → 再减1/3; 恐慌回补 ≤-20% 或≤P2 → 减仓现金分批买回; 尾仓 收盘破MA20离场。'
  + '分位=当前乖离在该品种自身约1年窗口中的位置(低波动品种以分位档为准); 空头反向参考。';
function biasRowInner(pos, b){
  b = b !== undefined ? b : biasSnapshot();
  if (!b) return '';
  const c = BIAS_CFG;
  const sgn = v => (v >= 0 ? '+' : '') + v.toFixed(1);
  const hit2 = b.b20 >= c.l2 || (b.p20 != null && b.p20 >= c.p2);
  const hit1 = !hit2 && (b.b20 >= c.l1 || (b.p20 != null && b.p20 >= c.p1));
  const panic = b.b20 <= c.rb || (b.p20 != null && b.p20 <= c.pr);
  const pTxt = b.p20 != null ? `P${Math.round(b.p20 * 100)}` : '—';
  let badge = '';
  if (hit2) badge = '<span class="bias-pill down">⚡ 二档减1/3</span>';
  else if (hit1) badge = `<span class="bias-pill warn">⚡ 一档${b.b60 >= c.b60Confirm ? '减半' : '减1/3'}</span>`;
  else if (panic) badge = '<span class="bias-pill up">❄ 恐慌回补</span>';
  else if (b.b20 < 0) badge = '<span class="bias-pill muted">破MA20</span>';
  return `<span>MA20 <b>${sgn(b.b20)}%</b> <small>(${pTxt})</small> · MA60 ${sgn(b.b60)}%</span>${badge}`;
}
function budgetBarInner(b){
  const frac = b.u > 0 ? Math.max(0, Math.min(1, b.avail / b.u)) : 0;
  return `<div class="pos-budget-top tnum">
      <span>可加浮盈 <b data-b-avail style="color:${b.avail > 0 ? 'var(--up)' : 'var(--muted)'}">${b.avail.toFixed(1)}U</b></span>
      <span class="pos-budget-sub" data-b-sub>已加 ${b.used.toFixed(0)} / 浮盈 ${b.u >= 0 ? '+' : ''}${b.u.toFixed(0)}U</span>
    </div>
    <div class="pos-budget-bar"><i data-b-fill style="transform:scaleX(${frac.toFixed(3)})"></i></div>`;
}
function patchBudgetBar(bdEl, b){
  const availEl = bdEl.querySelector('[data-b-avail]');
  const subEl = bdEl.querySelector('[data-b-sub]');
  const fillEl = bdEl.querySelector('[data-b-fill]');
  if (!availEl || !subEl || !fillEl){ bdEl.innerHTML = budgetBarInner(b); return; }
  availEl.textContent = `${b.avail.toFixed(1)}U`;
  availEl.style.color = b.avail > 0 ? 'var(--up)' : 'var(--muted)';
  subEl.textContent = `已加 ${b.used.toFixed(0)} / 浮盈 ${b.u >= 0 ? '+' : ''}${b.u.toFixed(0)}U`;
  const frac = b.u > 0 ? Math.max(0, Math.min(1, b.avail / b.u)) : 0;
  fillEl.style.transform = `scaleX(${frac.toFixed(3)})`;
}
function renderJournal(){
  const posBadge = document.getElementById('sideTabPosBadge');
  if (posBadge){
    const prev = posBadge.textContent;
    posBadge.textContent = positions.length ? String(positions.length) : '';
    posBadge.classList.toggle('on', positions.length > 0);
    if (positions.length > 0 && String(positions.length) !== prev) window.uiMotion?.popBadge?.(posBadge);
  }
  const box = document.getElementById('jPositions');
  if (!box) return;
  document.getElementById('journalMode').textContent =
    inReplay() ? `· ${activeTF} @ ${fmtTime(replayT)}` : '';
  document.getElementById('jOpen').disabled = !inReplay();
  const bar = curBar();
  let html = '';
  if (!inReplay()){
    html += `<div class="journal-idle-tip">
      <span>进入复盘或盲测练习后，按 <kbd>B</kbd> / <kbd>S</kbd> 即可快速模拟开平仓</span>
      <div class="jit-btns">
        <button type="button" class="btn btn-outline" data-idle-act="replay">进入复盘</button>
        <button type="button" class="btn btn-outline" data-idle-act="trainer">开始练习</button>
      </div>
    </div>`;
  }
  // 账户卡: 净值 / 浮盈 / 可用 / 占用 / 杠杆
  const floatSum = bar ? positions.filter(p => posLayoutOk(p)).reduce((a,p) => a + pnlOf(p, bar.close), 0) : 0;
  const used = positions.reduce((a,p) => a + posMargin(p), 0);
  const eq = acctBal() + used + floatSum;
  const notional = bar ? positions.filter(p => posLayoutOk(p)).reduce((a,p) => a + posValue(p, bar.close), 0) : 0;
  const levTotal = eq > 0 ? notional / eq : 0;
  const eqBase = (TR.active && TR.capital0) ? TR.capital0 : INIT_CAPITAL;
  const eqCol = eq > eqBase ? 'var(--up)' : (eq < eqBase ? 'var(--down)' : 'var(--ink)');
  const resetDisabled = positions.length > 0 || Math.abs(acctBal() - INIT_CAPITAL) < 0.01;
  const resetTitle = positions.length > 0 ? '请先平掉当前持仓后再重置本金' : (Math.abs(acctBal() - INIT_CAPITAL) < 0.01 ? '当前已是初始本金' : '重置账户本金为 100,000U');
  html += `<div class="acct-card" data-acct>
    <div class="acct-main">
      <div class="acct-kpi"><span class="acct-k">净值</span><b class="acct-eq tnum" data-aeq style="color:${eqCol}">${eq.toFixed(0)}U</b></div>
      <div class="acct-kpi"><span class="acct-k">浮盈</span><span class="acct-flt tnum" data-fsum style="color:${floatSum >= 0 ? 'var(--up)' : 'var(--down)'}">${floatSum >= 0 ? '+' : ''}${floatSum.toFixed(1)}U</span></div>
      <button type="button" class="btn btn-ghost acct-reset" id="jResetAcct" title="${resetTitle}"${resetDisabled ? ' disabled' : ''}>重置</button>
    </div>
    <div class="acct-sub tnum">
      <span>可用 <b data-abal>${acctBal().toFixed(0)}U</b></span>
      <span>占用 <b data-aused>${used.toFixed(0)}</b>U</span>
      <span>杠杆 <b data-alev title="总持仓名义价值 ÷ 账户净值">${notional > 0 ? levTotal.toFixed(2) + 'x' : '—'}</b></span>
    </div>
  </div>`;
  if (positions.length){
    html += `<button type="button" class="btn btn-close-all" id="jCloseAll" title="按当前收盘价一键平掉当前布局全部持仓">⚡ 一键全平 (${positions.length})</button>`;
  }
  const biasSnap = positions.length ? biasSnapshot() : null;
  for (const pos of positions){
    const ok = posLayoutOk(pos);
    const cap = posCapital(pos);
    const u = ok && bar ? pnlOf(pos, bar.close) : 0;
    const pct = cap ? u / cap * 100 : 0;
    const col = u >= 0 ? 'var(--up)' : 'var(--down)';
    const pv = ok && bar ? posValue(pos, bar.close) : cap;
    const lastLev = pos.entries[pos.entries.length-1].leverage;
    const bdInit = addBudget(pos);
    const rv = ok && bar ? rNow(pos, bar.close) : null;
    if (rv != null) pos.maxR = Math.max(pos.maxR ?? rv, rv);
    const exp = pos.expect || '中线';
    const expCls = POS_EXP_CLS[exp] || 'exp-mid';
    const avgPx = posAvgEntry(pos);
    const canBe = ok && bar && (pos.side === '多' ? avgPx < bar.close : avgPx > bar.close);
    const rBadge = pos.stop != null
      ? `<span data-r="${pos.id}" class="pos-r tnum" style="color:${rv != null ? (rv >= 0 ? 'var(--up)' : 'var(--down)') : 'var(--muted)'}" title="${pos.maxR != null ? '最大收盘 ' + fmtR(pos.maxR) : ''}">${rv != null ? `R ${fmtR(rv)} <small>最大 ${fmtR(pos.maxR)}</small>` : '—'}</span>`
      : '<span class="pos-r pos-r-empty">无止损</span>';
    const biasRow = ok
      ? `<div data-bias="${pos.id}" title="${BIAS_TIP}" class="pos-bias tnum">${biasRowInner(pos, biasSnap)}</div>`
      : '';
    const isShort = pos.side === '空';
    const lCard = typeof window.logicCardForPos === 'function' ? window.logicCardForPos(pos) : null;
    const logicPill = lCard
      ? `<button type="button" class="pos-logic-pill linked" data-act="logic-card" data-pid="${pos.id}" title="打开关联时间胶囊 / 记节点">🧭 已锁</button>`
      : `<button type="button" class="pos-logic-pill" data-act="logic-card" data-pid="${pos.id}" title="补建逻辑复盘卡">＋卡</button>`;
    const legsHtml = pos.entries.length > 1
      ? `<div class="pos-legs">${pos.entries.map((e,i) =>
          `<div class="pos-leg tnum" title="${(e.logic || '').replace(/"/g,'&quot;')}"><i class="leg-k">${i ? '加' : '开'}</i><b>${e.price}</b><span>${e.leverage}x · ${e.capital.toFixed(0)}U</span></div>`
        ).join('')}</div>`
      : '';
    html += `<div class="pos-card ${isShort ? 'pos-short' : 'pos-long'}" data-pid="${pos.id}">
      <div class="pos-head">
        <span class="pos-title">
          <b class="pos-id">#${pos.id}</b>
          <span class="pos-side-tag ${isShort ? 'short' : 'long'}">${pos.side}</span>
          <button type="button" class="pos-exp-tag ${expCls}" data-act="cycle-expect" data-pid="${pos.id}" title="点击切换预期：短线 / 中线 / 长线">${exp}</button>
          ${pos.entries.length > 1 ? `<span class="pos-legs-cnt tnum">×${pos.entries.length}</span>` : ''}
        </span>
        ${logicPill}
      </div>
      ${ok ? '' : `<div class="pos-warn"><span>⚠ 绑定 ${pos.sym} ${pos.tf}</span><button type="button" class="btn ts-mini" data-act="switch-sym" data-sym="${pos.sym}" data-pid="${pos.id}">切回 ${pos.sym}</button></div>`}
      <div class="pos-pnl-row">
        <span data-float="${pos.id}" class="pos-float tnum" style="color:${col}">${u >= 0 ? '+' : ''}${u.toFixed(1)}U <em class="pos-pct ${u >= 0 ? 'up' : 'down'}">${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%</em></span>
        ${rBadge}
      </div>
      <div class="pos-kpi3 tnum">
        <div><span>均价</span><b>${fmt(avgPx)}</b></div>
        <div><span>止损</span><button type="button" class="pos-kpi-btn" data-act="stop-toggle" data-pid="${pos.id}" title="点击调整止损 / 一键移至保本">${pos.stop ?? '未设'}<i>✎</i></button></div>
        <div><span>仓位</span><b>${pv.toFixed(0)}U</b><small data-plev="${pos.id}">${ok && eq > 0 ? ' ' + (pv/eq).toFixed(2) + 'x' : ''}</small></div>
      </div>
      ${biasRow}
      ${legsHtml}
      <div class="pos-btns">
        <button type="button" class="btn btn-outline pos-act-btn${bdInit.avail <= 0 ? ' is-muted' : ''}" data-act="add-toggle" data-pid="${pos.id}" title="${bdInit.avail > 0 ? '用浮盈加仓' : '需有可用浮盈才可加仓'}">＋ 加仓</button>
        <button type="button" class="btn btn-outline pos-act-btn" data-act="stop-toggle" data-pid="${pos.id}">移止损</button>
        <button type="button" class="btn btn-outline pos-act-btn pos-act-exit" data-act="close-toggle" data-pid="${pos.id}">平仓</button>
      </div>
      <div class="sec-stop pos-subform" style="display:none;">
        <div class="pos-stop-row">
          <button type="button" class="btn ts-mini pos-be-btn" data-act="stop-be" data-pid="${pos.id}" title="${canBe ? '把止损设为当前持仓均价' : '浮盈脱离成本后可用'}"${canBe ? '' : ' disabled'}>🛡 移至保本</button>
          <input type="number" class="inp-stop-quick" step="any" value="${pos.stop ?? ''}" placeholder="止损价 (留空取消)">
          <button type="button" class="btn btn-primary ts-mini" data-act="stop-save" data-pid="${pos.id}">保存</button>
        </div>
      </div>
      <div class="sec-add pos-subform" style="display:none;">
        <div data-budget="${pos.id}" class="pos-budget">${budgetBarInner(bdInit)}</div>
        <div class="pos-pct-row">
          ${[25, 50, 75, 100].map(p => `<button type="button" class="pos-chip" data-act="add-pct" data-val="${p}">${p === 100 ? '全额' : p + '%'}</button>`).join('')}
        </div>
        <div class="pos-form-grid2">
          <label class="field">本金 U<input type="number" class="inp-add-cap" min="0" step="0.01" placeholder="≤${bdInit.avail.toFixed(0)}"></label>
          <label class="field">杠杆<input type="number" class="inp-add-lev" value="${lastLev}" min="1" max="125"></label>
          <label class="field">加仓价<input type="number" class="inp-add-price" placeholder="收盘"></label>
          <label class="field">新止损<input type="number" class="inp-add-stop" placeholder="${pos.stop ?? '不变'}"></label>
        </div>
        <div class="pos-chips-row">
          ${ADD_PRESETS.map(x => `<button type="button" class="pos-chip" data-act="add-chip" data-val="${x}">${x}</button>`).join('')}
          ${trLocked() ? '<span class="pos-sub-hint">留空=浮盈加仓</span>' : ''}
        </div>
        <input type="text" class="pos-inline-inp inp-add-logic" maxlength="120" placeholder="${trLocked() ? '留空=浮盈加仓' : '加仓理由（点上方标签或输入）'}">
        <div class="pos-sub-actions">
          <button type="button" class="btn btn-primary" data-act="add-ok" data-pid="${pos.id}">确认加仓</button>
          <button type="button" class="btn btn-outline pos-full-btn" data-act="add-full" data-pid="${pos.id}" title="用全部可加浮盈一键加满">⚡ 加满 <span data-full="${pos.id}">${bdInit.avail > 0 ? bdInit.avail.toFixed(1) + 'U' : ''}</span></button>
        </div>
      </div>
      <div class="sec-exit pos-subform" style="display:none;">
        <div class="pos-exit-ratio-row">
          <div class="pos-ratio-seg">
            ${[25, 50, 75, 100].map(x => `<button type="button" class="pos-rbtn${x === 100 ? ' active' : ''}" data-act="ratio-chip" data-val="${x}">${x}%</button>`).join('')}
          </div>
          <label class="pos-ratio-custom"><input type="number" class="inp-exit-ratio" value="100" min="1" max="100" step="1"><span>%</span></label>
        </div>
        <div class="pos-exit-presets">
          ${EXIT_PRESETS.map(p => `<button type="button" class="pos-epreset ${p.cls}" data-act="exit-chip" data-val="${p.val}">${p.val}</button>`).join('')}
        </div>
        <input type="text" class="pos-inline-inp inp-exit-logic" maxlength="120" placeholder="平仓备注（点上方预设或自填）">
        <button type="button" class="btn pos-close-go ${u >= 0 ? 'is-profit' : 'is-loss'}" data-act="close-ok" data-pid="${pos.id}">
          <span>确认平仓 · <b data-exit-btn-r="${pos.id}">100%</b></span>
          <span class="tnum pos-exit-est" data-exit-est="${pos.id}">${u >= 0 ? '+' : ''}${u.toFixed(1)}U</span>
        </button>
      </div>
    </div>`;
  }
  if (!positions.length){
    html += `<p class="note pos-empty-note">暂无持仓</p>`;
  }
  const hist = jLoad().slice(-8).reverse();
  if (hist.length){
    html += `<div class="j-hist-title">最近平仓</div>
      <table class="j-table"><thead><tr><th>时间</th><th>单子</th><th>入→出</th><th class="num">盈亏U</th></tr></thead><tbody>` +
      hist.map(t => {
        const col = t.pnl_u >= 0 ? 'var(--up)' : 'var(--down)';
        const tip = `${t.expect ? '[' + t.expect + '] ' : ''}${t.exit_reason || ''}${t.exit_logic ? ' · ' + t.exit_logic : ''}`.replace(/"/g,'&quot;');
        const wFirst = candlesFor('raw')[0]?.time;
        const tLbl = (trLocked() && t.exit_ts != null && wFirst != null &&
                      t.exit_ts >= wFirst && t.exit_ts <= (replayT ?? Infinity))
          ? trLabel(t.exit_ts) : t.exit_time;
        const expTag = t.expect ? `<span class="j-exp-dot ${POS_EXP_CLS[t.expect] || ''}">${t.expect[0]}</span>` : '';
        return `<tr title="${tip}"><td>${tLbl}</td><td>${t.side}${t.adds > 0 ? '+'+t.adds : ''}${expTag}</td><td class="tnum">${t.entry}→${t.exit_price}</td>
          <td class="num tnum" style="color:${col}">${t.pnl_u >= 0 ? '+' : ''}${(+t.pnl_u).toFixed(1)}</td></tr>`;
      }).join('') + '</tbody></table>';
  }
  box.innerHTML = html;
  const closed = jLoad();
  const sumEl = document.getElementById('jSummary');
  const expBtn = document.getElementById('jExport');
  const clrBtn = document.getElementById('jClear');
  if (expBtn) expBtn.disabled = closed.length === 0;
  if (clrBtn) clrBtn.disabled = closed.length === 0;
  if (closed.length){
    const tot = closed.reduce((a,t) => a + t.pnl_u, 0);
    const win = closed.filter(t => t.pnl_u > 0).length;
    sumEl.textContent = `${closed.length}笔 · 胜率${(win/closed.length*100).toFixed(0)}% · ${tot >= 0 ? '+' : ''}${tot.toFixed(0)}U`;
  } else sumEl.textContent = '';
  refreshPositionLines();
}
function updateExitEstimate(card, pos){
  if (!card || !pos) return;
  const bar = curBar();
  const u = posLayoutOk(pos) && bar ? pnlOf(pos, bar.close) : 0;
  const inp = card.querySelector('.inp-exit-ratio');
  const raw = inp ? parseFloat(inp.value) : 100;
  const pct = Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : 100;
  const est = u * (pct / 100);
  const estEl = card.querySelector(`[data-exit-est="${pos.id}"]`);
  if (estEl) estEl.textContent = `${est >= 0 ? '+' : ''}${est.toFixed(1)}U`;
  const rLbl = card.querySelector(`[data-exit-btn-r="${pos.id}"]`);
  if (rLbl) rLbl.textContent = `${Math.round(pct)}%`;
  const goBtn = card.querySelector('.pos-close-go');
  if (goBtn){
    goBtn.classList.toggle('is-profit', est >= 0);
    goBtn.classList.toggle('is-loss', est < 0);
  }
}
// 浮动盈亏实时刷新: 回放每根K线/切视图时由 refreshAll 驱动, 只改文本不重建表单(避免打断输入)
function updateLivePnl(){
  document.getElementById('journalMode').textContent =
    inReplay() ? `· ${activeTF} @ ${fmtTime(replayT)}` : '';
  const bar = curBar();
  if (!bar) return;
  // 账户行
  const balEl = document.querySelector('[data-abal]');
  if (balEl){
    const mine = positions.filter(p => posLayoutOk(p));
    const floatSum = mine.reduce((a,p) => a + pnlOf(p, bar.close), 0);
    const used = positions.reduce((a,p) => a + posMargin(p), 0);
    const eq = acctBal() + used + floatSum;
    const fsEl = document.querySelector('[data-fsum]');
    const eqEl = document.querySelector('[data-aeq]');
    const usEl = document.querySelector('[data-aused]');
    balEl.textContent = acctBal().toFixed(0) + 'U';
    if (usEl) usEl.textContent = used.toFixed(0);
    if (fsEl){
      const nextTxt = (floatSum >= 0 ? '+' : '') + floatSum.toFixed(1) + 'U';
      const prevTxt = fsEl.textContent;
      fsEl.textContent = nextTxt;
      fsEl.style.color = floatSum >= 0 ? 'var(--up)' : 'var(--down)';
      if (prevTxt && prevTxt !== nextTxt && mine.length) window.uiMotion?.pulseNum?.(fsEl, floatSum >= 0 ? 'up' : 'down');
    }
    if (eqEl){
      const nextEq = eq.toFixed(0) + 'U';
      const prevEq = eqEl.textContent;
      eqEl.textContent = nextEq;
      const base = (TR.active && TR.capital0) ? TR.capital0 : INIT_CAPITAL;
      eqEl.style.color = eq > base ? 'var(--up)' : (eq < base ? 'var(--down)' : '');
      if (prevEq && prevEq !== nextEq && mine.length) window.uiMotion?.pulseNum?.(eqEl, eq >= base ? 'up' : 'down');
    }
    const notional = mine.reduce((a,p) => a + posValue(p, bar.close), 0);
    const levEl = document.querySelector('[data-alev]');
    if (levEl) levEl.textContent = notional > 0 && eq > 0 ? (notional / eq).toFixed(2) + 'x' : '—';
  }
  // 各持仓卡: 浮盈 + R 倍数 + 对净值杠杆 + 加仓预算 + 预计平仓盈亏
  const mineNow = positions.filter(p => posLayoutOk(p));
  const eqNow = acctBal() + positions.reduce((a,p) => a + posMargin(p), 0) + mineNow.reduce((a,p) => a + pnlOf(p, bar.close), 0);
  const biasSnap = positions.length ? biasSnapshot() : null;
  for (const pos of positions){
    if (!posLayoutOk(pos)){
      const bEl0 = document.querySelector(`[data-bias="${pos.id}"]`);
      if (bEl0) bEl0.innerHTML = `<span style="opacity:.55">切回 ${pos.sym} ${pos.tf} 查看</span>`;
      continue;
    }
    const u = pnlOf(pos, bar.close);
    const cap = posCapital(pos);
    const pct = cap ? u / cap * 100 : 0;
    const f = document.querySelector(`[data-float="${pos.id}"]`);
    if (f){
      f.innerHTML = `${u >= 0 ? '+' : ''}${u.toFixed(1)}U <em class="pos-pct ${u >= 0 ? 'up' : 'down'}">${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%</em>`;
      f.style.color = u >= 0 ? 'var(--up)' : 'var(--down)';
    }
    const levP = document.querySelector(`[data-plev="${pos.id}"]`);
    if (levP) levP.textContent = eqNow > 0 ? ' ' + (posValue(pos, bar.close) / eqNow).toFixed(2) + 'x' : '';
    const rEl = document.querySelector(`[data-r="${pos.id}"]`);
    const r = rNow(pos, bar.close);
    if (r != null) pos.maxR = Math.max(pos.maxR ?? r, r);
    if (rEl){
      rEl.innerHTML = r != null ? `R ${fmtR(r)} <small>最大 ${fmtR(pos.maxR)}</small>` : '—';
      rEl.style.color = r != null ? (r >= 0 ? 'var(--up)' : 'var(--down)') : 'var(--muted)';
    }
    const bd = document.querySelector(`[data-budget="${pos.id}"]`);
    const b = addBudget(pos);
    if (bd){
      patchBudgetBar(bd, b);
      const fullEl = document.querySelector(`[data-full="${pos.id}"]`);
      if (fullEl) fullEl.textContent = b.avail > 0 ? b.avail.toFixed(1) + 'U' : '';
    }
    const biasEl = document.querySelector(`[data-bias="${pos.id}"]`);
    if (biasEl) biasEl.innerHTML = biasRowInner(pos, biasSnap);
    const card = document.querySelector(`.pos-card[data-pid="${pos.id}"]`);
    if (card){
      updateExitEstimate(card, pos);
      const addToggle = card.querySelector('[data-act="add-toggle"]');
      if (addToggle) addToggle.classList.toggle('is-muted', b.avail <= 0);
      const beBtn = card.querySelector('[data-act="stop-be"]');
      if (beBtn){
        const avgPx = posAvgEntry(pos);
        const canBe = pos.side === '多' ? avgPx < bar.close : avgPx > bar.close;
        beBtn.disabled = !canBe;
        beBtn.title = canBe ? '把止损设为当前持仓均价' : '浮盈脱离成本后可用';
      }
    }
  }
  // 图上快捷弹层(若打开): 浮盈随K线刷新
  if (posPopup){
    const p = posById(posPopup.pid);
    const fEl = posPopupEl.querySelector('[data-pp-float]');
    if (p && fEl){
      const u2 = bar ? pnlOf(p, bar.close) : 0;
      const cap2 = posCapital(p);
      fEl.textContent = `浮动 ${u2 >= 0 ? '+' : ''}${u2.toFixed(1)}U (${cap2 ? (u2/cap2*100 >= 0 ? '+' : '') + (u2/cap2*100).toFixed(1) : '0.0'}%)`;
      fEl.style.color = u2 >= 0 ? 'var(--up)' : 'var(--down)';
    }
  }
  updateStopLossEstimate();   // 开仓价留空=当前收盘, 随K线刷新止损预估
  if (typeof qoSyncBar === 'function') qoSyncBar();
}
// 止损预估: 输入止损价实时展示预计亏损金额(按表单保证金/杠杆/方向; 开仓价留空=当前收盘)
function updateStopLossEstimate(){
  const el = document.getElementById('jStopLoss');
  if (!el) return;
  const stopRaw = document.getElementById('jStop').value.trim();
  const stop = +stopRaw;
  if (!stopRaw || !isFinite(stop)){
    el.textContent = '输入止损价可预估亏损金额';
    el.style.color = 'var(--muted)';
    return;
  }
  const bar = curBar();
  const entryRaw = document.getElementById('jEntry').value.trim();
  const entry = entryRaw ? +entryRaw : (bar ? bar.close : null);
  if (entry == null || !isFinite(entry) || !entry){ el.textContent = ''; return; }
  const cap = +document.getElementById('jCapital').value || 0;
  const lev = Math.min(125, Math.max(1, +document.getElementById('jLeverage').value || 1));
  const side = document.getElementById('jSide').value;
  const dir = side === '多' ? 1 : -1;
  const pnl = (stop - entry) / entry * dir * lev * cap;   // 止损触发时盈亏(应为负)
  const pxTxt = stop.toLocaleString('en-US', { maximumFractionDigits: Math.abs(stop) >= 1 ? 2 : 8 });
  if (pnl >= 0){
    el.textContent = `⚠ 止损价在${side === '多' ? '开仓价上方' : '开仓价下方'}，${side}单不会触发止损`;
    el.style.color = 'var(--down)';
    return;
  }
  const pct = cap > 0 ? Math.abs(pnl / cap * 100) : 0;
  el.innerHTML = `止损预计亏损 <b style="color:var(--down)">${pnl.toFixed(2)}U</b>` +
    `（占保证金 ${pct.toFixed(1)}% · 触发价 ${pxTxt}）`;
  el.style.color = 'var(--ink-2)';
}
// 加仓执行(「确认加仓」与「一键加满」共用): fillMax=true 时忽略本金输入, 用全部可加浮盈。
// 练习模式(盲测中)加仓理由留空默认「浮盈加仓」; 复盘模式仍必填, 保持规则沉淀。
function addPosFromCard(pos, card, fillMax){
  if (!inReplay()){ alert('仅复盘模式可加仓'); return false; }
  if (!posLayoutOk(pos)){ alert(`该持仓绑定 ${pos.sym} ${pos.tf}, 请切回原品种与周期再加仓`); return false; }
  const q = sel => card.querySelector(sel);
  const logic = q('.inp-add-logic').value.trim() || (trLocked() ? '浮盈加仓' : '');
  if (!logic){ alert('请点选或填写加仓理由（必填）'); return false; }
  const bar = curBar(); if (!bar) return false;
  const bd = addBudget(pos);
  let wantCap = fillMax ? bd.avail : (+q('.inp-add-cap').value || 0);
  if (wantCap <= 0){
    alert(fillMax ? '本笔订单暂无可加浮盈(浮亏状态, 或浮盈已全部投用于加仓)' : '请填写加仓本金');
    return false;
  }
  if (wantCap > bd.avail + 0.01){
    alert(`只能用本笔订单浮盈加仓: 浮盈 ${bd.u.toFixed(2)}U − 已加 ${bd.used.toFixed(2)}U = 还可加 ${bd.avail.toFixed(2)}U`);
    return false;
  }
  if (wantCap > bd.avail) wantCap = +bd.avail.toFixed(6);
  pos.entries.push({
    t: bar.time,
    price: trLocked() ? bar.close : (+q('.inp-add-price').value || bar.close),
    capital: fillMax ? +bd.avail.toFixed(6) : wantCap,
    leverage: Math.min(125, Math.max(1, +q('.inp-add-lev').value || pos.entries[pos.entries.length-1].leverage)),
    logic,
    step: TR.active && !TR.revealed ? TR.step : null,
  });
  const ns = q('.inp-add-stop').value;
  if (ns && Number.isFinite(+ns) && +ns > 0) updatePositionStop(pos,+ns,logic);
  ensureRisk(pos);   // 若此前无止损/无 R, 此时锁定; 已有 R 则移动止损不改基准
  if (TR.active && !TR.revealed){   // 练习局: 标记发生过加仓并刷新净值曲线
    TR.hadAdds = true;
    const leg=pos.entries.at(-1);
    prEvent('add',pos,{price:leg.price,capital:leg.capital,leverage:leg.leverage,reason:logic});
    if (typeof recordEquity === 'function') recordEquity();
  }
  renderJournal();
  prCheckpoint();
  refreshAll({ light: true });   // 立即在图上标记加仓位置
  flashBtn('snapshotBtn', `${fillMax ? '✓ 一键加满 ' + wantCap.toFixed(2) + 'U · ' : '✓ '}新成本 ${posAvgEntry(pos).toPrecision(6)}`);
  return true;
}
// 平掉指定比例(r∈[0.01,1]): 释放对应保证金并结算盈亏; r=1 整笔移除, 否则剩余仓位等比缩水
// force=true 用于练习揭示/爆仓等程序性强平(此时必为同布局)
function closePosObj(pos, r, exitLogic, reason = '手动平仓', force = false){
  if (!force && !posLayoutOk(pos)){
    alert(`该持仓绑定在 ${pos.sym} ${pos.tf}，请先切回原品种与周期再结算（防止跨品种价格混源）。`);
    return false;
  }
  const bar = curBar();
  if (!bar) return false;
  if (!force && bar.time < pos.entries[0].t){
    alert('当前K线早于该持仓的开仓时间（复盘已回退），请前进到开仓之后再平仓。');
    return false;
  }
  const pnl = pnlOf(pos, bar.close) * r;
  const rec = makeRec(pos, r, bar.close, bar.time, pnl, exitLogic, reason);
  const arr = jLoad(); arr.push(rec); jSave(arr);
  if (r >= 1 && typeof window.onLogicTradeClosed === 'function') window.onLogicTradeClosed(rec, pos);
  if (TR.active && !TR.revealed){   // 练习局: 记入本局战绩, 揭示时统一入账; 同步累计加仓贡献
    TR.session.push(rec);
    prEvent('exit',pos,{price:rec.exit_price,fraction:r,pnl:rec.pnl_u,exitType:reason,reason:exitLogic});
    TR.addPnlRealized = (TR.addPnlRealized || 0) + (rec.add_pnl || 0);
    if (rec.adds > 0) TR.hadAdds = true;
  }
  acctAdj(posMargin(pos) * r + pnl);
  if (r >= 1) positions = positions.filter(p => p !== pos);
  else pos.entries.forEach(e => { e.capital = +(e.capital * (1 - r)).toFixed(6); });
  if (TR.active && !TR.revealed && typeof recordEquity === 'function') recordEquity();
  prCheckpoint();
  renderJournal();
  refreshAll({ light: true });   // 出场标记立即上图(练习局)
  return true;
}
function makeRec(pos, r, exitPrice, exitTs, pnl, exitLogic, reason){
  const capClosed = posCapital(pos) * r;
  // 加仓效果对照: 仅首仓、同一出场价/比例下的盈亏; 与实际之差 = 加仓贡献
  const noAddPnl = pnlOf({ ...pos, entries: [pos.entries[0]] }, exitPrice) * r;
  return {
    symbol: DATA.meta.symbol, tf: activeTF,
    mode: TR.active && !TR.revealed ? 'trainer' : 'replay',
    pid: pos.id,
    side: pos.side, expect: pos.expect || '中线', leverage: pos.entries[0].leverage, capital: +capClosed.toFixed(2),
    entry_ts: pos.entries[0].t, exit_ts: exitTs,
    entry_ts_list: pos.entries.map(e => e.t), entry_prices: pos.entries.map(e => e.price),
    entry_time: fmtTimeReal(pos.entries[0].t), entry: +posAvgEntry(pos).toFixed(2),
    exit_time: fmtTimeReal(exitTs), exit_price: exitPrice,
    stop: pos.stop ?? '', initial_stop: pos.initialStop ?? null, opportunity_id: pos.opportunityId ?? null, logic_card_id: pos.logicCardId ?? null, logic: pos.entries[0].logic,
    adds: pos.entries.length - 1, ratio: +r.toFixed(2), partial: r < 1,
    entries: pos.entries.map(e => ({t: fmtTimeReal(e.t), price: e.price, capital: e.capital, leverage: e.leverage, logic: e.logic, step: e.step ?? null })),
    exit_logic: exitLogic,
    tags: pos.tags ?? null, quick: pos.quick ?? null,
    pnl_u: +pnl.toFixed(2), pnl_pct_capital: +(pnl/Math.max(capClosed,1e-9)*100).toFixed(2),
    no_add_pnl: +noAddPnl.toFixed(2),
    add_pnl: +(pnl - noAddPnl).toFixed(2),
    // R 倍数: r0=止损打到的亏损金额(基准), r_final=本笔盈亏/R, max_r=持仓期间最大到达 R
    r0: pos.r0 ?? null,
    r_final: pos.r0 ? +(pnl / pos.r0).toFixed(2) : null,
    max_r: pos.maxR != null ? +pos.maxR.toFixed(2) : null,
    result: pnl > 0 ? '盈' : (pnl < 0 ? '亏' : '平'),
    exit_reason: reason + (r < 1 ? ` ${Math.round(r*100)}%` : ''),
  };
}
// 逐根检查: 单笔止损/单笔爆仓(亏损≥其保证金) → 整笔离场; 仅检查当前布局匹配的持仓
function checkStopAndLiquidate(){
  const bar = curBar();
  const mine = positions.filter(p => posLayoutOk(p));
  if (!bar || !mine.length) return;
  let changed = false;
  for (const pos of mine){
    // 开仓当根及之前的K线不检查(含复盘回退到开仓前): 持仓在那时尚不存在
    if (bar.time <= pos.entries[0].t) continue;
    const margin = posMargin(pos);
    const long = pos.side === '多';
    const hitStop = pos.stop != null && (long ? bar.low <= pos.stop : bar.high >= pos.stop);
    // 单笔爆仓按盘中最不利价判定(旧版只看收盘, 影线穿透爆仓价仍存活)
    const worst = long ? bar.low : bar.high;
    const liq = pnlOf(pos, worst) <= -margin;
    if (!hitStop && !liq) continue;
    // 止损成交价: 跳空越过止损按开盘价成交(复盘与练习一致)
    const stopPx = hitStop ? (long ? Math.min(bar.open, pos.stop) : Math.max(bar.open, pos.stop)) : null;
    const stopFirst = hitStop && pnlOf(pos, stopPx) > -margin;
    const px = stopFirst ? stopPx : (hitStop ? stopPx : worst);
    const pnl = Math.max(stopFirst ? pnlOf(pos, px) : -margin, -margin);
    const rec = makeRec(pos, 1, px, bar.time, pnl, '', stopFirst ? '触发止损' : '单笔爆仓');
    const arr = jLoad(); arr.push(rec); jSave(arr);
    if (typeof window.onLogicTradeClosed === 'function') window.onLogicTradeClosed(rec, pos);
    if (TR.active && !TR.revealed){
      TR.session.push(rec);
      prEvent('exit',pos,{price:px,fraction:1,pnl:rec.pnl_u,exitType:rec.exit_reason});
      TR.addPnlRealized = (TR.addPnlRealized || 0) + (rec.add_pnl || 0);
      if (rec.adds > 0) TR.hadAdds = true;
    }
    acctAdj(margin + pnl);
    positions = positions.filter(p => p !== pos);
    if (TR.active && !TR.revealed && typeof recordEquity === 'function') recordEquity();
    changed = true;
  }
  if (changed) renderJournal();
}
// 账户级爆仓: 净值≤0 → 强平全部; 练习直接强制揭示, 复盘提示后重置本金
function checkAccountBlowup(){
  if (!inReplay()) return false;
  // 以净值为准(不设持仓数前置条件): 单笔满仓亏光后余额归零同样判定爆仓
  if (acctEquity() > 0) return false;
  for (const pos of [...positions].filter(p => posLayoutOk(p)))
    closePosObj(pos, 1, '账户净值归零, 全部强平', '账户爆仓', true);
  acctSave({...acctLoad(), [curModeKey()]: 0});
  if (TR.active && !TR.revealed){
    revealTrainer(true, '💥 账户爆仓, 练习强制结束');
  } else {
    window.showToast?.('💥 账户净值 ≤ 0，已全部强平并重置本金为 100,000U', 'err', 4200);
    acctReset();
    renderJournal();
  }
  return true;
}
// ---- 两级入场标签: 一级分类(市场环境/入场信号必填) → 二级选项 ----
function ltSelected(cat, group){
  const sel = group
    ? `#jLogicTags .logic-tag.active[data-cat="${cat}"][data-group="${group}"]`
    : `#jLogicTags .logic-tag.active[data-cat="${cat}"]`;
  return [...document.querySelectorAll(sel)].map(b => b.dataset.val);
}
function ltMissing(){
  const miss = [];
  if (!ltSelected('market', 'ma120dir').length) miss.push('MA120方向');
  if (!ltSelected('market', 'pricepos').length) miss.push('价格位置');
  if (!ltSelected('entry').length) miss.push('入场信号');
  return miss;
}
function selectedLogicTags(){
  const parts = [];
  const market = ltSelected('market');
  if (market.length) parts.push('市场: ' + market.join('·'));
  const entry = ltSelected('entry');
  if (entry.length) parts.push('入场: ' + entry.join('、'));
  const pattern = ltSelected('pattern');
  if (pattern.length) parts.push('形态: ' + pattern.join('、'));
  return parts.join(' | ');
}
function updateLtCounts(){
  for (const cat of ['market', 'entry', 'pattern']){
    const n = ltSelected(cat).length;
    const el = document.querySelector(`#jLogicTags [data-count="${cat}"]`);
    if (el){ el.textContent = n; el.classList.toggle('on', n > 0); }
  }
}
function combineEntryLogic(tags, text){
  return [tags, (text || '').trim()].filter(Boolean).join(' · ');
}
// 一键全平: 平掉当前布局全部持仓(异布局持仓需切回后处理, 防跨品种价格混源)
function closeAllPositions(){
  const mine = positions.filter(p => posLayoutOk(p));
  const others = positions.length - mine.length;
  if (!mine.length){ window.showToast?.('⚠ 当前品种/周期下没有可平持仓' + (others ? `（另有 ${others} 笔绑定其他布局）` : ''), 'warn', 2600); return; }
  if (!confirm(`确认一键平掉全部 ${mine.length} 笔持仓?\n离场依据将统一记录为「一键全部平仓」。`)) return;
  for (const pos of [...mine]) closePosObj(pos, 1, '一键全部平仓', '一键全部平仓');
  if (others) flashBtn('snapshotBtn', `⚠ 另有 ${others} 笔异布局持仓未平`);
}
function openJournalPosition(opts = {}){
  let fail = message => { (opts.onError || alert)(message); return false; };
  if (!inReplay()) return fail('请先进入复盘模式');
  if (!opts.logicCardId && typeof window.logicStrictGateCheck === 'function'){
    const gate = window.logicStrictGateCheck(opts.side ?? document.getElementById('jSide').value);
    if (gate && !gate.ok){
      if (typeof window.openLogicStudio === 'function') window.openLogicStudio('gate', { side: opts.side ?? document.getElementById('jSide').value });
      return fail(gate.message);
    }
  }
  // 市场环境没选时按当根数据自动判定(MA120方向/价格位置), 入场信号至少 1 个(所有开仓入口一致)
  if (!ltSelected('entry').length) return fail('请至少选 1 个入场信号');
  const autoMarket = typeof qoAutoFillMarket === 'function' ? qoAutoFillMarket() : [];
  // 自动补的环境标签只用于本单: 开仓后(或失败返回时)撤掉, 下一单按新K线重新判定
  const clearAuto = () => autoMarket.forEach(v => document.querySelector(`#jLogicTags .logic-tag[data-cat="market"][data-val="${v}"]`)?.classList.remove('active'));
  const fail0 = fail;
  fail = m => { clearAuto(); if (typeof updateLtCounts === 'function') updateLtCounts(); return fail0(m); };
  const miss = ltMissing();
  if (!opts.details && miss.length) return fail('还缺: ' + miss.join('、') + '（数据不足以自动判定时需手选）');
  const logic = opts.logic ?? combineEntryLogic(selectedLogicTags(), document.getElementById('jLogic').value);
  if (!logic) return fail('请记录开仓理由');
  const bar = curBar();
  if (!bar) return fail('当前没有可用 K 线');
  const side = opts.side ?? document.getElementById('jSide').value;
  const research = trLocked() && TR.research?.mode === 'research';
  const margin = +(document.getElementById('jCapital').value || 100000);
  if (!Number.isFinite(margin) || margin <= 0) return fail('保证金须大于 0');
  if (margin > acctBal()) return fail(`余额不足：当前可用本金 ${acctBal().toFixed(2)}U。可在右侧开仓表单调整保证金。`);
  // 练习中一律按当前收盘成交: 自填更优价格等于事后挂单, 会污染练习结果
  const price = opts.entry ?? (trLocked() ? bar.close : (+document.getElementById('jEntry').value || bar.close));
  const input = document.getElementById('jStop').value.trim();
  const stop = opts.stop ?? (research ? prDefaultStop(bar,side,price) : input ? +input : null);
  if (!Number.isFinite(price) || price <= 0) return fail('开仓价须大于 0');
  if (research && stop == null) return fail('此 K 线没有有效的初始止损距离。');
  if (stop != null && (!Number.isFinite(stop)||stop<=0||(side==='多'?stop>=price:stop<=price)))
    return fail('初始止损必须在开仓价的不利方向。');
  acctAdj(-margin);
  const expectRaw = opts.expect || document.getElementById('jExpect')?.value || '中线';
  const expect = POS_EXPECTS.includes(expectRaw) ? expectRaw : '中线';
  const np = {
    id: posSeq++, sym: currentSym, tf: activeTF, side, expect, stop, initialStop:stop, r0:null, maxR:null,
    logicCardId: opts.logicCardId ?? null,
    entries:[{t:bar.time,price,capital:margin,leverage:Math.min(125,Math.max(1,+document.getElementById('jLeverage').value||10)),
      logic,step:trLocked()?TR.step:null}],
    tags:{market:ltSelected('market'),entry:ltSelected('entry'),pattern:ltSelected('pattern')},
    quick: opts.quick ?? (autoMarket.length ? { version:'quick-order/1', via:'form',
      tagSource:Object.fromEntries(autoMarket.map(v => [v, 'auto'])) } : null),
  };
  positions.push(np);
  clearAuto(); if (typeof updateLtCounts === 'function') updateLtCounts();
  ensureRisk(np);
  prRecordOrder(np,bar,opts.details ?? (research?{reason:logic,basis:'signal_close_and_extreme',invalid:side==='空'?'价格触及当前 K 线高点':'价格触及当前 K 线低点'}:null),logic);
  prCheckpoint();
  renderJournal();
  refreshAll({ light: true });
  return true;
}
function updatePositionStop(pos, next, reason){
  const old=pos.stop;
  if(old===next)return;
  pos.stop=next;
  prEvent('stop_change',pos,{from:old,to:next,reason});
}
function setupJournal(){
  document.getElementById('jLogicTags').addEventListener('click', e => {
    const catBtn = e.target.closest('.lt-cat');
    if (catBtn){
      const cat = catBtn.dataset.cat;
      const panel = document.querySelector(`#jLogicTags .lt-panel[data-panel="${cat}"]`);
      const wasOpen = panel && panel.style.display !== 'none';
      document.querySelectorAll('#jLogicTags .lt-panel').forEach(p => p.style.display = 'none');
      document.querySelectorAll('#jLogicTags .lt-cat').forEach(b => b.classList.remove('active'));
      if (panel && !wasOpen){ panel.style.display = 'flex'; catBtn.classList.add('active'); }
      return;
    }
    const b = e.target.closest('.logic-tag');
    if (!b) return;
    if (b.dataset.group === 'ma120dir' || b.dataset.group === 'pricepos'){
      const was = b.classList.contains('active');   // 市场环境两组: 同组单选, 再点取消
      document.querySelectorAll(`#jLogicTags .logic-tag[data-group="${b.dataset.group}"]`)
        .forEach(x => x.classList.toggle('active', x === b ? !was : false));
    } else {
      b.classList.toggle('active');
    }
    updateLtCounts();
  });
  // 止损预估: 保证金/杠杆/方向/开仓价/止损价变化时实时刷新
  ['jStop', 'jEntry', 'jCapital', 'jLeverage', 'jSide'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('input', updateStopLossEstimate);
    el.addEventListener('change', updateStopLossEstimate);
  });
  document.getElementById('jOpen').addEventListener('click', () => openJournalPosition());
  // 持仓卡事件委托: 加仓/平仓表单切换 + 确认动作 + 重置本金 + 空闲快捷入口
  document.getElementById('jPositions').addEventListener('click', e => {
    const idleBtn = e.target.closest('[data-idle-act]');
    if (idleBtn){
      document.getElementById(idleBtn.dataset.idleAct === 'trainer' ? 'trainerBtn' : 'replayBtn')?.click();
      return;
    }
    if (e.target.id === 'jResetAcct'){
      if (positions.length){ alert('有未平仓位, 请先平仓再重置本金'); return; }
      const m = curModeKey() === 'trainer' ? '练习' : '复盘';
      if (confirm(`确认把${m}本金重置为 ${INIT_CAPITAL.toLocaleString()}U? (清零该模式累计盈亏)`)){
        acctReset(); renderJournal(); flashBtn('snapshotBtn', '✓ 本金已重置');
      }
      return;
    }

    if (e.target.id === 'jCloseAll'){
      closeAllPositions();
      return;
    }
    const b = e.target.closest('button[data-act]');
    if (!b) return;
    // 注意: 按钮自身也带 data-pid, 必须向上找 .pos-card 容器(含加仓/平仓表单)
    const card = b.closest('.pos-card');
    const pos = card ? posById(+card.dataset.pid) : null;
    if (!pos) return;
    const q = sel => card.querySelector(sel);
    const showOnly = (secName, actBtnName) => {
      for (const s of card.querySelectorAll('.sec-add,.sec-exit,.sec-stop')) s.style.display = 'none';
      card.querySelectorAll('.pos-act-btn').forEach(btn => btn.classList.remove('is-open'));
      if (secName){
        const el = q(secName);
        if (el) el.style.display = 'flex';
        if (actBtnName) card.querySelector(`.pos-act-btn[data-act="${actBtnName}"]`)?.classList.add('is-open');
      }
    };
    switch (b.dataset.act){
      case 'switch-sym': {
        const targetSym = b.dataset.sym;
        if (targetSym && typeof switchSymbol === 'function') switchSymbol(targetSym);
        break;
      }
      case 'cycle-expect': {
        const cur = POS_EXPECTS.indexOf(pos.expect || '中线');
        pos.expect = POS_EXPECTS[(cur + 1) % POS_EXPECTS.length];
        prCheckpoint();
        renderJournal();
        window.showToast?.(`#${pos.id} 预期已切换为「${pos.expect}」`, 'info', 1400);
        break;
      }
      case 'logic-card': {
        const lCard = typeof window.logicCardForPos === 'function' ? window.logicCardForPos(pos) : null;
        if (lCard && typeof window.openLogicStudio === 'function'){
          window.openLogicStudio('review');
          document.querySelector(`[data-a-sel="${lCard.id}"]`)?.click();
        } else if (typeof window.openLogicStudio === 'function'){
          window.openLogicStudio('gate', { side: pos.side, horizon: pos.expect || '中线', entryPrice: posAvgEntry(pos), stopPrice: pos.stop });
        }
        break;
      }
      case 'stop-toggle': {
        if (!posLayoutOk(pos)){ window.showToast?.(`⚠ 该持仓绑定 ${pos.sym} ${pos.tf}，请先切回后再调止损`, 'warn', 2600); return; }
        const open = q('.sec-stop')?.style.display === 'none';
        showOnly(open ? '.sec-stop' : null, open ? 'stop-toggle' : null);
        if (open) q('.inp-stop-quick')?.focus();
        break;
      }
      case 'stop-be': {
        if (!posLayoutOk(pos)) return;
        const avg = +posAvgEntry(pos).toPrecision(7);
        const nowBar = curBar();
        if (nowBar && (pos.side === '多' ? avg >= nowBar.close : avg <= nowBar.close)){
          window.showToast?.('⚠ 当前未脱离成本，暂无法直接移至保本', 'warn', 2200);
          return;
        }
        updatePositionStop(pos, avg, '移至保本');
        ensureRisk(pos);
        prCheckpoint();
        renderJournal();
        window.showToast?.(`🛡 #${pos.id} 止损已移至保本 ${avg}`, 'ok', 1800);
        break;
      }
      case 'stop-save': {
        if (!posLayoutOk(pos)) return;
        const v = parseFloat(q('.inp-stop-quick')?.value);
        const nowBar = curBar();
        if (!isNaN(v) && v > 0 && nowBar && (pos.side === '多' ? v >= nowBar.close : v <= nowBar.close)
            && !confirm(`止损 ${v} 已在当前收盘价 ${nowBar.close} 不利侧，下根开盘将触发离场，确定保存？`)) return;
        updatePositionStop(pos, (isNaN(v) || v <= 0) ? null : v, '卡片调损');
        ensureRisk(pos);
        prCheckpoint();
        renderJournal();
        window.showToast?.(isNaN(v) || v <= 0 ? `#${pos.id} 已取消止损` : `✓ #${pos.id} 止损更新为 ${v}`, 'ok', 1600);
        break;
      }
      case 'add-toggle': {
        const bd = addBudget(pos);
        if (bd.avail <= 0){
          window.showToast?.(bd.u <= 0
            ? '本笔订单暂无浮盈，盈利后即可用浮盈加仓'
            : `可加浮盈已用完（已加 ${bd.used.toFixed(1)}U）`, 'info', 2200);
          window.uiMotion?.shake?.(b);
          return;
        }
        const open = q('.sec-add').style.display === 'none';
        showOnly(open ? '.sec-add' : null, open ? 'add-toggle' : null);
        if (open) q('.inp-add-cap')?.focus();
        break;
      }
      case 'add-pct': {
        const bd = addBudget(pos);
        const pct = +b.dataset.val || 100;
        const amt = Math.floor(bd.avail * pct) / 100;
        const capInp = q('.inp-add-cap');
        if (capInp) capInp.value = amt > 0 ? amt.toFixed(2) : '';
        card.querySelectorAll('[data-act="add-pct"]').forEach(x => x.classList.toggle('active', x === b));
        break;
      }
      case 'add-chip': {
        const inp = q('.inp-add-logic');
        const parts = inp.value.split(/[,，、·]\s*/).map(x => x.trim()).filter(Boolean);
        const v = b.dataset.val, i = parts.indexOf(v);
        if (i >= 0) parts.splice(i, 1); else parts.push(v);
        inp.value = parts.join('、');
        b.classList.toggle('active', i < 0);
        break;
      }
      case 'close-toggle': {
        if (!posLayoutOk(pos)){ window.showToast?.(`⚠ 该持仓绑定 ${pos.sym} ${pos.tf}，请先切回后再平仓`, 'warn', 2600); return; }
        const open = q('.sec-exit').style.display === 'none';
        showOnly(open ? '.sec-exit' : null, open ? 'close-toggle' : null);
        if (open){ updateExitEstimate(card, pos); q('.inp-exit-logic')?.focus(); }
        break;
      }
      case 'add-ok':
        if (!addPosFromCard(pos, card, false)) window.uiMotion?.shake?.(q('.sec-add') || card);
        break;
      case 'add-full':
        if (!addPosFromCard(pos, card, true)) window.uiMotion?.shake?.(q('.sec-add') || card);
        break;
      case 'exit-chip': {
        const ta = q('.inp-exit-logic');
        const parts = ta.value.split(/[,，、·]\s*/).map(x => x.trim()).filter(Boolean);
        const v = b.dataset.val, i = parts.indexOf(v);
        if (i >= 0) parts.splice(i, 1); else parts.push(v);
        ta.value = parts.join('、');
        b.classList.toggle('active', i < 0);
        break;
      }
      case 'ratio-chip':
        q('.inp-exit-ratio').value = b.dataset.val;
        card.querySelectorAll('[data-act="ratio-chip"]').forEach(x => x.classList.toggle('active', x === b));
        updateExitEstimate(card, pos);
        break;
      case 'close-ok': {
        const logic = q('.inp-exit-logic').value.trim();
        if (!logic){
          window.uiMotion?.shake?.(q('.sec-exit') || card);
          q('.inp-exit-logic')?.focus();
          window.showToast?.('请选择平仓预设（保本 / 止盈 / 保护利润）或填写备注', 'warn', 2400);
          return;
        }
        const rawRatio = q('.inp-exit-ratio').value.trim();
        let ratio = rawRatio === '' ? 1 : +rawRatio / 100;
        if (!Number.isFinite(ratio) || ratio <= 0){
          window.uiMotion?.shake?.(q('.inp-exit-ratio') || card);
          alert('平仓比例须在 1%~100% 之间');
          return;
        }
        ratio = Math.min(1, Math.max(0.01, ratio));
        closePosObj(pos, ratio, logic);
        break;
      }
    }
  });
  document.getElementById('jPositions').addEventListener('input', e => {
    if (!e.target.classList.contains('inp-exit-ratio')) return;
    const card = e.target.closest('.pos-card');
    const pos = card ? posById(+card.dataset.pid) : null;
    if (!pos) return;
    const v = e.target.value.trim();
    card.querySelectorAll('[data-act="ratio-chip"]').forEach(x => x.classList.toggle('active', x.dataset.val === v));
    updateExitEstimate(card, pos);
  });
  document.getElementById('jPositions').addEventListener('keydown', e => {
    const card = e.target.closest('.pos-card');
    if (!card) return;
    if (e.key === 'Escape'){
      e.preventDefault();
      for (const s of card.querySelectorAll('.sec-add,.sec-exit,.sec-stop')) s.style.display = 'none';
      card.querySelectorAll('.pos-act-btn').forEach(btn => btn.classList.remove('is-open'));
      return;
    }
    if (e.key !== 'Enter') return;
    if (e.target.classList.contains('inp-stop-quick')){ e.preventDefault(); card.querySelector('[data-act="stop-save"]')?.click(); }
    else if (e.target.classList.contains('inp-add-logic')){ e.preventDefault(); card.querySelector('[data-act="add-ok"]')?.click(); }
    else if (e.target.classList.contains('inp-exit-logic')){ e.preventDefault(); card.querySelector('[data-act="close-ok"]')?.click(); }
  });
  document.getElementById('jExport').addEventListener('click', () => {
    const arr = jLoad();
    if (!arr.length){ window.showToast?.('暂无交易历史记录可导出', 'info', 2000); return; }
    const cols = ['symbol','tf','side','expect','leverage','capital','entry_time','entry','exit_time','exit_price','stop','initial_stop','opportunity_id','adds','exit_logic','pnl_u','pnl_pct_capital','result','exit_reason','logic','r0','r_final','max_r'];
    const esc = v => '"' + String(v ?? '').replace(/"/g,'""') + '"';
    const csv = '\ufeff' + cols.join(',') + '\n' +
      arr.map(r => cols.map(c => esc(r[c])).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `trade_journal_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    window.showToast?.(`✓ 已导出 ${arr.length} 笔交易 CSV`, 'ok', 2000);
  });
  document.getElementById('jClear').addEventListener('click', () => {
    const prev = jLoad();
    if (!prev.length) return;
    if (confirm(`确认清空全部 ${prev.length} 笔交易历史？`)){
      jSave([]); renderJournal();
      const recKey = r => `${r.symbol}|${r.pid}|${r.entry_ts}|${r.exit_ts}|${r.ratio}|${r.pnl_u}`;
      window.showToast?.(`已清空 ${prev.length} 笔交易记录`, 'info', {
        ms: 5500,
        action: {
          label: '撤销',
          onClick: () => {
            const newer = jLoad();
            const seen = new Set(newer.map(recKey));
            const merged = [...prev.filter(r => !seen.has(recKey(r))), ...newer];
            jSave(merged);
            renderJournal();
            window.showToast?.(`✓ 已恢复 ${prev.length} 笔交易记录`, 'ok', 1800);
          },
        },
      });
    }
  });
}

// ---- 持仓图表侧展示: 每笔持仓一条彩色虚线(入场均价), 有止损另画红细虚线;
//      点击虚线弹快捷面板(加仓/改止损), 不同持仓不同颜色 ----
const POS_COLORS = ['#2962ff', '#ff9800', '#00bcd4', '#9c27b0', '#e91e63', '#26a69a'];
const posColor = pos => POS_COLORS[(pos.id - 1) % POS_COLORS.length];
let posPriceLines = [];   // {pid, kind:'entry'|'stop', line}
let posPopup = null;      // {pid}
const posPopupEl = document.createElement('div');
posPopupEl.className = 'pos-popup';
posPopupEl.style.display = 'none';
document.body.appendChild(posPopupEl);

function refreshPositionLines(){
  for (const l of posPriceLines){
    try { candleSeries.removePriceLine(l.line); } catch {}
  }
  posPriceLines = [];
  if (!inReplay() || !positions.length) { closePosPopup(); return; }
  const bar = curBar();
  for (const pos of positions){
    const color = posColor(pos);
    const entry = posAvgEntry(pos);
    // 标题只留编号(方向/价格看卡片与轴标): 长文字会压在最新K线区域影响读图
    posPriceLines.push({ pid: pos.id, kind: 'entry', line: candleSeries.createPriceLine({
      price: entry, color, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true, title: `#${pos.id} 成本`,
    })});
    if (pos.stop != null){
      posPriceLines.push({ pid: pos.id, kind: 'stop', line: candleSeries.createPriceLine({
        price: pos.stop, color: t().down, lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true, title: `${pos.id}损`,
      })});
    }
  }
}

function closePosPopup(){
  posPopupEl.style.display = 'none';
  posPopup = null;
}

function openPosPopup(pos, point){
  posPopup = { pid: pos.id };
  const bar = curBar();
  const u = bar ? pnlOf(pos, bar.close) : 0;
  const cap = posCapital(pos);
  posPopupEl.innerHTML = `
    <div class="pp-head" style="color:${posColor(pos)}">#${pos.id} ${pos.side} · ${pos.expect || '中线'}</div>
    <div class="pp-row tnum">均价 ${fmt(posAvgEntry(pos))} · 本金 ${posMargin(pos).toFixed(0)}U</div>
    <div class="pp-row tnum" data-pp-float style="font-weight:700;color:${u >= 0 ? 'var(--up)' : 'var(--down)'}">${u >= 0 ? '+' : ''}${u.toFixed(1)}U (${cap ? (u/cap*100 >= 0 ? '+' : '') + (u/cap*100).toFixed(1) : '0.0'}%)</div>
    <div class="pp-actions">
      <button class="btn" data-pp="add">加仓</button>
      <button class="btn" data-pp="exit">平仓</button>
      <button class="btn" data-pp="stop">止损</button>
      <button class="btn" data-pp="close-pop">✕</button>
    </div>
    <div class="pp-stop" style="display:none;">
      <input type="number" class="pp-stop-inp" step="any" value="${pos.stop ?? ''}" placeholder="止损价"><input class="pp-stop-reason" type="text" maxlength="200" placeholder="理由(可选)">
      <button class="btn btn-primary" data-pp="stop-save">保存</button>
    </div>`;
  posPopupEl.style.display = 'flex';
  // 定位: 弹层放在点击点【左侧】—— 最新K线在图表右缘, 右侧展开会挡住最新走势
  const rc = document.getElementById('chart').getBoundingClientRect();
  const POP_W = 200;
  let px = rc.left + point.x - 16 - POP_W;
  if (px < rc.left + 8) px = rc.left + point.x + 16;   // 左侧放不下再回右侧
  let py = rc.top + point.y - 12;
  px = Math.max(rc.left + 8, Math.min(px, rc.right - POP_W - 8));
  py = Math.max(8, Math.min(py, window.innerHeight - 200));
  posPopupEl.style.left = px + 'px';
  posPopupEl.style.top = py + 'px';
}

// 点击命中检测: 视口 y 与各持仓入场/止损线的像素距离 <7px 视为点击该线
chart.subscribeClick(param => {
  if (prChartPicking()) return;
  if (typeof qoIsOpen === 'function' && qoIsOpen()) return;   // 快速下单条打开时, 点图 = 设止损
  if (posPopup && posPopupEl.style.display !== 'none'){
    // 点击已有弹层时交给弹层按钮处理(弹层是 body 子元素, 不在 chart 上)
  }
  if (!param.point || !positions.length || !inReplay()){ if (posPopup) closePosPopup(); return; }
  let hit = null, best = 1e9;
  for (const l of posPriceLines){
    const pos = posById(l.pid);
    if (!pos) continue;
    const price = l.kind === 'entry' ? posAvgEntry(pos) : pos.stop;
    if (price == null) continue;
    const py = candleSeries.priceToCoordinate(price);
    if (py == null) continue;
    const d = Math.abs(py - param.point.y);
    if (d < 7 && d < best){ best = d; hit = pos; }
  }
  if (!hit){ if (posPopup) closePosPopup(); return; }
  openPosPopup(hit, param.point);
});

posPopupEl.addEventListener('click', e => {
  const b = e.target.closest('button[data-pp]');
  if (!b || !posPopup) return;
  const pos = posById(posPopup.pid);
  if (!pos){ closePosPopup(); return; }
  switch (b.dataset.pp){
    case 'add': {
      closePosPopup();
      if (typeof setSideTab === 'function') setSideTab('trade');
      const card = document.querySelector(`.pos-card[data-pid="${pos.id}"]`);
      if (!card){ window.showToast?.('未找到该持仓卡片，请在右侧「交易」面板操作', 'warn', 2200); return; }
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      for (const s of card.querySelectorAll('.sec-add,.sec-exit,.sec-stop')) s.style.display = 'none';
      card.querySelectorAll('.pos-act-btn').forEach(btn => btn.classList.remove('is-open'));
      const f = card.querySelector('.sec-add');
      if (f){
        f.style.display = 'flex';
        card.querySelector('.pos-act-btn[data-act="add-toggle"]')?.classList.add('is-open');
        const bd = addBudget(pos);
        const cap = f.querySelector('.inp-add-cap');
        if (cap){ cap.value = bd.avail > 0 ? bd.avail.toFixed(2) : ''; cap.focus(); }
      }
      break;
    }
    case 'exit': {
      closePosPopup();
      if (typeof setSideTab === 'function') setSideTab('trade');
      const card = document.querySelector(`.pos-card[data-pid="${pos.id}"]`);
      if (!card) return;
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      for (const s of card.querySelectorAll('.sec-add,.sec-exit,.sec-stop')) s.style.display = 'none';
      card.querySelectorAll('.pos-act-btn').forEach(btn => btn.classList.remove('is-open'));
      const f = card.querySelector('.sec-exit');
      if (f){
        f.style.display = 'flex';
        card.querySelector('.pos-act-btn[data-act="close-toggle"]')?.classList.add('is-open');
        updateExitEstimate(card, pos);
      }
      break;
    }
    case 'stop': {
      const st = posPopupEl.querySelector('.pp-stop');
      st.style.display = st.style.display === 'none' ? 'flex' : 'none';
      break;
    }
    case 'stop-save': {
      const v = parseFloat(posPopupEl.querySelector('.pp-stop-inp').value);
      const reason = posPopupEl.querySelector('.pp-stop-reason')?.value.trim()||'';
      if(trLocked()&&TR.research?.mode==='research'&&!reason){alert('请填写调整止损的理由。');return;}
      const nowBar = curBar();
      if (!isNaN(v) && v > 0 && nowBar && (pos.side === '多' ? v >= nowBar.close : v <= nowBar.close)
          && !confirm(`止损 ${v} 已在当前收盘价 ${nowBar.close} 的${pos.side === '多' ? '上方' : '下方'}，下一根K线开盘就会触发离场。确定保存?`)) return;
      updatePositionStop(pos,(isNaN(v) || v <= 0) ? null : v,reason);
      prCheckpoint();
      ensureRisk(pos);   // 首次设置止损时锁定 R 基准
      renderJournal();   // 同步卡片显示 + 重画持仓线
      closePosPopup();
      break;
    }
    case 'close-pop': closePosPopup(); break;
  }
});
