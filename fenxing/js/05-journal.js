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
const jSave = arr => localStorage.setItem(JKEY, JSON.stringify(arr));
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
  const cs = candlesFor(activeView);
  return cs.length ? cs[cs.length-1] : null;
}
const posCapital = pos => pos.entries.reduce((a,e) => a + e.capital, 0);
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
  if (!pos) return { u:0, used:0, avail:0 };
  const bar = curBar();
  const u = bar ? pnlOf(pos, bar.close) : 0;
  const used = pos.entries.slice(1).reduce((a,e) => a + e.capital, 0);
  return { u, used, avail: Math.max(0, u - used) };
}
// 账户净值 = 可用余额 + 占用保证金 + 当前布局持仓浮动盈亏
// (保证金只是被锁定并未消失; 旧版漏加占用 → 满仓开仓后任何浮亏都会被误判为爆仓)
const acctEquity = () => {
  const bar = curBar();
  const cur = positions.filter(p => posLayoutOk(p));
  const used = cur.reduce((a,p) => a + posCapital(p), 0);
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
const fmtLogic = (s, n=16) => { s = String(s||''); return s.length > n ? s.slice(0, n) + '…' : s; };
function renderJournal(){
  const box = document.getElementById('jPositions');
  if (!box) return;
  document.getElementById('journalMode').textContent =
    inReplay() ? `· ${activeTF} @ ${fmtTime(replayT)}` : '(需进入复盘模式)';
  document.getElementById('jOpen').disabled = !inReplay();
  const bar = curBar();
  // 练习(盲测)模式下加仓理由可留空, 自动记为「浮盈加仓」; 复盘模式仍要求填写
  const addLogicLabel = trLocked() ? '加仓逻辑 / 依据 (留空=浮盈加仓)' : '加仓逻辑 / 依据 (必填)';
  let html = '';
  // 账户行: 本金/占用/浮动/净值
  const floatSum = bar ? positions.filter(p => posLayoutOk(p)).reduce((a,p) => a + pnlOf(p, bar.close), 0) : 0;
  const used = positions.filter(p => posLayoutOk(p)).reduce((a,p) => a + posCapital(p), 0);
  const eq = acctBal() + used + floatSum;   // 净值含占用保证金
  const notional = bar ? positions.filter(p => posLayoutOk(p)).reduce((a,p) => a + posValue(p, bar.close), 0) : 0;
  const levTotal = eq > 0 ? notional / eq : 0;
  const eqBase = (TR.active && TR.capital0) ? TR.capital0 : INIT_CAPITAL;   // 练习局按本局本金配色
  const eqCol = eq > eqBase ? 'var(--up)' : (eq < eqBase ? 'var(--down)' : 'var(--ink-2)');
  html += `<div data-acct style="border:1px dashed var(--border); border-radius:6px; padding:5px 8px; margin-top:8px; font-size:12px;">
    本金 <b data-abal>${acctBal().toFixed(2)}U</b> · 占用 <span data-aused>${used.toFixed(0)}</span>U ·
    浮动 <span data-fsum style="font-weight:700;color:${floatSum >= 0 ? 'var(--up)' : 'var(--down)'}">${floatSum >= 0 ? '+' : ''}${floatSum.toFixed(2)}U</span> ·
    净值 <b data-aeq style="color:${eqCol}">${eq.toFixed(2)}U</b> ·
    持仓杠杆 <b data-alev title="全部持仓名义价值 ÷ 账户净值">${notional > 0 ? levTotal.toFixed(2) + 'x' : '—'}</b>
    <button class="btn" id="jResetAcct" title="清零盈亏, 恢复初始本金(需先平掉全部持仓)" style="margin-left:6px;">重置本金</button>
  </div>`;
  // 一键全平: 放在单子(持仓卡)上方, 大按钮; 无持仓时隐藏
  if (positions.length){
    html += `<button class="btn" id="jCloseAll" title="平掉当前品种/周期下的全部持仓(按当前收盘价, 离场依据统一记录为「一键全部平仓」)"
      style="width:100%; margin-top:8px; padding:9px 0; font-size:14px; font-weight:700; letter-spacing:.5px; background:rgba(242,54,69,.12); border:1px solid var(--down); color:var(--down); border-radius:6px; cursor:pointer;">⚡ 一键全平（${positions.length} 笔）</button>`;
  }
  for (const pos of positions){
    const ok = posLayoutOk(pos);   // 跨品种/周期持仓: 只可查看, 结算需切回原布局
    const cap = posCapital(pos);
    const u = ok && bar ? pnlOf(pos, bar.close) : 0;
    const pct = cap ? u / cap * 100 : 0;
    const col = u >= 0 ? 'var(--up)' : 'var(--down)';
    const pv = ok && bar ? posValue(pos, bar.close) : cap;
    const lastLev = pos.entries[pos.entries.length-1].leverage;
    const bdInit = addBudget(pos);
    const rv = ok && bar ? rNow(pos, bar.close) : null;
    if (rv != null) pos.maxR = Math.max(pos.maxR ?? rv, rv);
    const rRow = pos.stop != null
      ? `<span data-r="${pos.id}" style="font-weight:600; font-size:12px;">${rv != null ? `R ${fmtR(rv)} · 最大 ${fmtR(pos.maxR)}` : 'R —'}</span><br>`
      : '<span style="opacity:.55; font-size:11px;">未设止损 · 无 R 基准</span><br>';
    html += `<div class="pos-card" data-pid="${pos.id}" style="border:1px solid var(--border); border-radius:6px; padding:6px 8px; margin-top:8px;">
      <b>#${pos.id} ${pos.side}</b> · ${pos.entries.length}笔 · 加权开仓 ${fmt(posAvgEntry(pos))} · 止损 ${pos.stop ?? '—'}${ok ? '' : ` <span style="color:var(--down); font-weight:600;">⚠ 绑定 ${pos.sym} ${pos.tf}, 请切回后结算</span>`}<br>
      <span style="opacity:.85">持仓金额 ${pv.toFixed(1)} U · 对净值 <b data-plev="${pos.id}">${ok && eq > 0 ? (pv/eq).toFixed(2) + 'x' : '—'}</b></span><br>
      <span data-float="${pos.id}" style="font-size:15px;font-weight:700;color:${col}">浮动盈亏 ${u >= 0 ? '+' : ''}${u.toFixed(1)} U (${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%)</span><br>
      ${rRow}
      ${pos.entries.map((e,i) =>
        `<span style="opacity:.72" title="${e.logic.replace(/"/g,'&quot;')}">${i+1}) ${e.price} · ${e.leverage}x · ${e.capital}U · ${fmtLogic(e.logic)}</span>`
      ).join('<br>')}
      <div style="display:flex; gap:6px; margin-top:5px;">
        <button class="btn" data-act="add-toggle" data-pid="${pos.id}">加仓</button>
        <button class="btn" data-act="close-toggle" data-pid="${pos.id}">平仓</button>
      </div>
      <div class="sec-add" style="display:none; flex-direction:column; gap:5px; margin-top:6px; border-top:1px dashed var(--border); padding-top:6px;">
        <div data-budget="${pos.id}" style="font-size:11px; opacity:.8;"></div>
        <div style="display:flex; gap:4px;">
          <label style="flex:1">本金U(≤浮盈)<input type="number" class="inp-add-cap" min="0" step="0.01" placeholder="≤还可加" style="width:100%;background:var(--plane);color:inherit;border:1px solid var(--border);border-radius:4px;padding:3px 5px;"></label>
          <label style="flex:1">杠杆<input type="number" class="inp-add-lev" value="${lastLev}" min="1" max="125" style="width:100%;background:var(--plane);color:inherit;border:1px solid var(--border);border-radius:4px;padding:3px 5px;"></label>
          <label style="flex:1">加仓价<input type="number" class="inp-add-price" placeholder="当前收盘" style="width:100%;background:var(--plane);color:inherit;border:1px solid var(--border);border-radius:4px;padding:3px 5px;"></label>
        </div>
        <label>止损价(留空=维持 ${pos.stop ?? '无'})<input type="number" class="inp-add-stop" placeholder="留空不变" style="width:100%;background:var(--plane);color:inherit;border:1px solid var(--border);border-radius:4px;padding:3px 5px;"></label>
        <label>${addLogicLabel}<textarea class="inp-add-logic" rows="2" placeholder="例: 盈利加仓: 次级别二买确认, 突破前高回踩不破 (练习模式留空=浮盈加仓)" style="width:100%;background:var(--plane);color:inherit;border:1px solid var(--border);border-radius:4px;padding:3px 5px;font:inherit;"></textarea></label>
        <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
          <button class="btn" data-act="add-ok" data-pid="${pos.id}">确认加仓(按当前K线)</button>
          <button class="btn" data-act="add-full" data-pid="${pos.id}" title="把该笔订单当前全部可加浮盈一次性加为保证金(按当前收盘价, 沿用上次杠杆; 练习模式理由留空自动记为「浮盈加仓」)">⚡ 一键加满 <span data-full="${pos.id}">${bdInit.avail > 0 ? bdInit.avail.toFixed(2) + 'U' : ''}</span></button>
        </div>
      </div>
      <div class="sec-exit" style="display:none; flex-direction:column; gap:5px; margin-top:6px; border-top:1px dashed var(--border); padding-top:6px;">
        <div style="display:flex; gap:4px; align-items:center;">
          <label style="flex:1">平仓比例%<input type="number" class="inp-exit-ratio" value="100" min="1" max="100" step="1" style="width:100%;background:var(--plane);color:inherit;border:1px solid var(--border);border-radius:4px;padding:3px 5px;"></label>
          <span style="opacity:.7; flex:1">按当前收盘价</span>
        </div>
        <label>平仓逻辑 / 离场依据 (必填)<textarea class="inp-exit-logic" rows="2" placeholder="例: 段顶+次级别顶背驰, 减半落袋 / 破位清仓" style="width:100%;background:var(--plane);color:inherit;border:1px solid var(--border);border-radius:4px;padding:3px 5px;font:inherit;"></textarea></label>
        <button class="btn" data-act="close-ok" data-pid="${pos.id}" style="align-self:flex-start;">确认平仓(当前收盘)</button>
      </div>
    </div>`;
  }
  if (!positions.length){
    html += `<p class="note" style="margin-top:8px;">暂无持仓。可同时开多笔不同方向的仓位, 各自独立止损。</p>`;
  }
  const hist = jLoad().slice(-8).reverse();
  if (hist.length){
    html += `<table style="width:100%; font-size:11px; margin-top:8px; border-collapse:collapse;">
      <tr style="opacity:.6"><td>时间</td><td>方向</td><td>入场→出场</td><td>盈亏U</td></tr>` +
      hist.map(t => {
        const col = t.pnl_u >= 0 ? 'var(--up)' : 'var(--down)';
        const tip = `${t.exit_reason || ''}${t.exit_logic ? ' · ' + t.exit_logic : ''}`.replace(/"/g,'&quot;');
        // 盲测态: 仅本局练习窗口内的记录打相对标签; 旧局记录显示真实时间(与本局定位无关)
        const wFirst = candlesFor('raw')[0]?.time;
        const tLbl = (trLocked() && t.exit_ts != null && wFirst != null &&
                      t.exit_ts >= wFirst && t.exit_ts <= (replayT ?? Infinity))
          ? trLabel(t.exit_ts) : t.exit_time;
        return `<tr title="${tip}"><td>${tLbl}</td><td>${t.side}${t.adds > 0 ? '+'+t.adds : ''}</td><td>${t.entry}→${t.exit_price}</td>
          <td style="color:${col};text-align:right">${t.pnl_u >= 0 ? '+' : ''}${(+t.pnl_u).toFixed(1)}</td></tr>`;
      }).join('') + '</table>';
  }
  box.innerHTML = html;
  const closed = jLoad();
  const sumEl = document.getElementById('jSummary');
  if (closed.length){
    const tot = closed.reduce((a,t) => a + t.pnl_u, 0);
    const win = closed.filter(t => t.pnl_u > 0).length;
    sumEl.textContent = `共${closed.length}笔 · 胜率${(win/closed.length*100).toFixed(0)}% · 累计${tot >= 0 ? '+' : ''}${tot.toFixed(0)}U`;
  } else sumEl.textContent = '';
  refreshPositionLines();   // 持仓线随开仓/平仓/加仓/改止损同步重画
}
// 浮动盈亏实时刷新: 回放每根K线/切视图时由 refreshAll 驱动, 只改文本不重建表单(避免打断输入)
function updateLivePnl(){
  document.getElementById('journalMode').textContent =
    inReplay() ? `· ${activeTF} @ ${fmtTime(replayT)}` : '(需进入复盘模式)';
  const bar = curBar();
  if (!bar) return;
  // 账户行
  const balEl = document.querySelector('[data-abal]');
  if (balEl){
    const mine = positions.filter(p => posLayoutOk(p));
    const floatSum = mine.reduce((a,p) => a + pnlOf(p, bar.close), 0);
    const used = mine.reduce((a,p) => a + posCapital(p), 0);
    const eq = acctBal() + used + floatSum;   // 净值含占用保证金
    const fsEl = document.querySelector('[data-fsum]');
    const eqEl = document.querySelector('[data-aeq]');
    const usEl = document.querySelector('[data-aused]');
    balEl.textContent = acctBal().toFixed(2) + 'U';
    if (usEl) usEl.textContent = used.toFixed(0);
    if (fsEl){ fsEl.textContent = (floatSum >= 0 ? '+' : '') + floatSum.toFixed(2) + 'U';
      fsEl.style.color = floatSum >= 0 ? 'var(--up)' : 'var(--down)'; }
    if (eqEl){ eqEl.textContent = eq.toFixed(2) + 'U';
      const base = (TR.active && TR.capital0) ? TR.capital0 : INIT_CAPITAL;
      eqEl.style.color = eq > base ? 'var(--up)' : (eq < base ? 'var(--down)' : ''); }
    const notional = mine.reduce((a,p) => a + posValue(p, bar.close), 0);
    const levEl = document.querySelector('[data-alev]');
    if (levEl) levEl.textContent = notional > 0 && eq > 0 ? (notional / eq).toFixed(2) + 'x' : '—';
  }
  // 各持仓卡: 浮盈 + R 倍数 + 对净值杠杆 + 加仓预算
  const mineNow = positions.filter(p => posLayoutOk(p));
  const eqNow = acctBal() + mineNow.reduce((a,p) => a + posCapital(p), 0) + mineNow.reduce((a,p) => a + pnlOf(p, bar.close), 0);
  for (const pos of positions){
    if (!posLayoutOk(pos)) continue;   // 异布局持仓: 切回后才显示浮盈
    const u = pnlOf(pos, bar.close);
    const cap = posCapital(pos);
    const f = document.querySelector(`[data-float="${pos.id}"]`);
    if (f){
      f.textContent = `浮动盈亏 ${u >= 0 ? '+' : ''}${u.toFixed(1)} U (${u/cap >= 0 ? '+' : ''}${(u/cap*100).toFixed(1)}%)`;
      f.style.color = u >= 0 ? 'var(--up)' : 'var(--down)';
    }
    const levP = document.querySelector(`[data-plev="${pos.id}"]`);
    if (levP) levP.textContent = eqNow > 0 ? (posValue(pos, bar.close) / eqNow).toFixed(2) + 'x' : '—';
    const rEl = document.querySelector(`[data-r="${pos.id}"]`);
    const r = rNow(pos, bar.close);
    if (r != null) pos.maxR = Math.max(pos.maxR ?? r, r);
    if (rEl){
      rEl.textContent = r != null ? `R ${fmtR(r)} · 最大 ${fmtR(pos.maxR)}` : 'R —';
      rEl.style.color = r >= 0 ? 'var(--up)' : 'var(--down)';
    }
    const bd = document.querySelector(`[data-budget="${pos.id}"]`);
    if (bd){
      const b = addBudget(pos);
      bd.innerHTML = `本笔浮盈 <b style="color:${b.u >= 0 ? 'var(--up)' : 'var(--down)'}">${b.u >= 0 ? '+' : ''}${b.u.toFixed(2)}U</b>` +
        ` · 已加仓 ${b.used.toFixed(2)}U · 还可加 <b>${b.avail.toFixed(2)}U</b><span style="opacity:.7">（只能用该笔订单的浮盈加仓）</span>`;
      const fullEl = document.querySelector(`[data-full="${pos.id}"]`);
      if (fullEl) fullEl.textContent = b.avail > 0 ? b.avail.toFixed(2) + 'U' : '';
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
  if (!logic){ alert('加仓逻辑必填——记录加仓依据才能沉淀加仓规则'); return false; }
  const bar = curBar(); if (!bar) return false;
  const bd = addBudget(pos);
  const wantCap = fillMax ? bd.avail : (+q('.inp-add-cap').value || 0);
  if (wantCap <= 0){
    alert(fillMax ? '本笔订单暂无可加浮盈(浮亏状态, 或浮盈已全部投用于加仓)' : '请填写加仓本金');
    return false;
  }
  if (wantCap > bd.avail + 1e-9){
    alert(`只能用本笔订单浮盈加仓: 浮盈 ${bd.u.toFixed(2)}U − 已加 ${bd.used.toFixed(2)}U = 还可加 ${bd.avail.toFixed(2)}U`);
    return false;
  }
  pos.entries.push({
    t: bar.time,
    price: +q('.inp-add-price').value || bar.close,
    capital: fillMax ? +bd.avail.toFixed(6) : wantCap,
    leverage: Math.min(125, +q('.inp-add-lev').value || pos.entries[pos.entries.length-1].leverage),
    logic,
    step: TR.active && !TR.revealed ? TR.step : null,
  });
  const ns = q('.inp-add-stop').value;
  if (ns) pos.stop = +ns;
  ensureRisk(pos);   // 若此前无止损/无 R, 此时锁定; 已有 R 则移动止损不改基准
  if (TR.active && !TR.revealed){   // 练习局: 标记发生过加仓并刷新净值曲线
    TR.hadAdds = true;
    if (typeof recordEquity === 'function') recordEquity();
  }
  renderJournal();
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
  const pnl = pnlOf(pos, bar.close) * r;
  const rec = makeRec(pos, r, bar.close, bar.time, pnl, exitLogic, reason);
  const arr = jLoad(); arr.push(rec); jSave(arr);
  if (TR.active && !TR.revealed){   // 练习局: 记入本局战绩, 揭示时统一入账; 同步累计加仓贡献
    TR.session.push(rec);
    TR.addPnlRealized = (TR.addPnlRealized || 0) + (rec.add_pnl || 0);
    if (rec.adds > 0) TR.hadAdds = true;
  }
  acctAdj(posCapital(pos) * r + pnl);
  if (r >= 1) positions = positions.filter(p => p !== pos);
  else pos.entries.forEach(e => { e.capital = +(e.capital * (1 - r)).toFixed(6); });
  if (TR.active && !TR.revealed && typeof recordEquity === 'function') recordEquity();
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
    side: pos.side, leverage: pos.entries[0].leverage, capital: +capClosed.toFixed(2),
    entry_ts: pos.entries[0].t, exit_ts: exitTs,
    entry_ts_list: pos.entries.map(e => e.t), entry_prices: pos.entries.map(e => e.price),
    entry_time: fmtTimeReal(pos.entries[0].t), entry: +posAvgEntry(pos).toFixed(2),
    exit_time: fmtTimeReal(exitTs), exit_price: exitPrice,
    stop: pos.stop ?? '', logic: pos.entries[0].logic,
    adds: pos.entries.length - 1, ratio: +r.toFixed(2), partial: r < 1,
    entries: pos.entries.map(e => ({t: fmtTimeReal(e.t), price: e.price, capital: e.capital, leverage: e.leverage, logic: e.logic, step: e.step ?? null })),
    exit_logic: exitLogic,
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
    const cap = posCapital(pos);
    const hitStop = pos.stop != null &&
      ((pos.side === '多' && bar.low <= pos.stop) ||
       (pos.side === '空' && bar.high >= pos.stop));
    const liq = pnlOf(pos, bar.close) <= -cap;
    if (!hitStop && !liq) continue;
    const px = hitStop ? pos.stop : bar.close;
    const pnl = hitStop ? pnlOf(pos, pos.stop) : -cap;
    const rec = makeRec(pos, 1, px, bar.time, Math.max(pnl, -cap), '', hitStop ? '触发止损' : '单笔爆仓');
    const arr = jLoad(); arr.push(rec); jSave(arr);
    if (TR.active && !TR.revealed){
      TR.session.push(rec);
      TR.addPnlRealized = (TR.addPnlRealized || 0) + (rec.add_pnl || 0);
      if (rec.adds > 0) TR.hadAdds = true;
    }
    acctAdj(cap + Math.max(pnl, -cap));
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
    alert('账户净值 ≤ 0, 已全部强平。本金重置为初始 100,000U。');
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
  const entry = ltSelected('entry', 'ma_break').concat(ltSelected('entry', 'ma_pullback'));
  if (entry.length) parts.push('入场: ' + entry.join('、'));
  const mom = ltSelected('entry', 'momentum');
  if (mom.length) parts.push('动能: ' + mom.join('、'));
  const cd = ltSelected('entry', 'candle');
  if (cd.length) parts.push('裸K: ' + cd.join('、'));
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
  if (!mine.length){ alert('当前品种/周期下没有可平持仓' + (others ? `（另有 ${others} 笔绑定其他布局, 请切回后处理）` : '')); return; }
  if (!confirm(`确认一键平掉全部 ${mine.length} 笔持仓?\n离场依据将统一记录为「一键全部平仓」。`)) return;
  for (const pos of [...mine]) closePosObj(pos, 1, '一键全部平仓', '一键全部平仓');
  if (others) flashBtn('snapshotBtn', `⚠ 另有 ${others} 笔异布局持仓未平`);
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
  document.getElementById('jOpen').addEventListener('click', () => {
    if (!inReplay()){ alert('请先进入复盘模式'); return; }
    const miss = ltMissing();
    if (miss.length){ alert(`市场环境/入场信号必填——还缺: ${miss.join('、')}。请点开对应一级标签选择二级标签`); return; }
    const logic = combineEntryLogic(selectedLogicTags(), document.getElementById('jLogic').value);
    if (!logic){ alert('开单逻辑/信号必填'); return; }
    const bar = curBar();
    if (!bar) return;
    // 保证金 = 表单本金; 从账户余额划扣(多笔持仓各自独立占用)
    const margin = +(document.getElementById('jCapital').value) || 100000;
    if (margin > acctBal()){
      alert(`余额不足: 当前可用本金 ${acctBal().toFixed(2)}U`);
      return;
    }
    acctAdj(-margin);
    const np = {
      id: posSeq++,
      sym: currentSym, tf: activeTF,   // 绑定开仓布局(用键名, 与 posLayoutOk/切换逻辑一致)
      side: document.getElementById('jSide').value,
      stop: document.getElementById('jStop').value ? +document.getElementById('jStop').value : null,
      r0: null, maxR: null,            // R 基准(首次设止损时锁定) / 最大到达 R
      entries: [{
        t: bar.time,
        price: +document.getElementById('jEntry').value || bar.close,
        capital: margin,
        leverage: Math.min(125, +document.getElementById('jLeverage').value || 10),
        logic,
        step: TR.active && !TR.revealed ? TR.step : null,   // 练习局: 记录开仓步号(信号K影响复盘用)
      }],
    };
    positions.push(np);
    ensureRisk(np);   // 开仓即带止损 → 立即锁定 R 基准
    renderJournal();
    refreshAll({ light: true });   // 立即在图上标记开仓位置
  });
  // 持仓卡事件委托: 加仓/平仓表单切换 + 确认动作 + 重置本金
  document.getElementById('jPositions').addEventListener('click', e => {
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
    const showOnly = secName => {
      for (const s of card.querySelectorAll('.sec-add,.sec-exit')) s.style.display = 'none';
      if (secName) q(secName).style.display = 'flex';
    };
    switch (b.dataset.act){
      case 'add-toggle': {
        const bd = addBudget(pos);
        if (bd.avail <= 0){
          alert(bd.u <= 0
            ? '本笔订单暂无浮盈, 无法加仓——只能用该笔订单的浮盈加仓'
            : `浮盈 ${bd.u.toFixed(1)}U 已全部投用于加仓(已加 ${bd.used.toFixed(1)}U), 暂无可加余额`);
          return;
        }
        showOnly(q('.sec-add').style.display === 'none' ? '.sec-add' : null);
        break;
      }
      case 'close-toggle':
        if (!posLayoutOk(pos)){ alert(`该持仓绑定 ${pos.sym} ${pos.tf}, 请切回原品种与周期再平仓`); return; }
        showOnly(q('.sec-exit').style.display === 'none' ? '.sec-exit' : null);
        break;
      case 'add-ok':
        addPosFromCard(pos, card, false);
        break;
      case 'add-full':
        // 一键加满: 用该笔订单当前全部可加浮盈加仓
        addPosFromCard(pos, card, true);
        break;
      case 'close-ok': {
        const logic = q('.inp-exit-logic').value.trim();
        if (!logic){ alert('平仓逻辑必填——记录离场依据(止盈/背驰/破位)才能沉淀离场规则'); return; }
        let ratio = (+q('.inp-exit-ratio').value || 100) / 100;
        ratio = Math.min(1, Math.max(0.01, ratio));
        closePosObj(pos, ratio, logic);
        break;
      }
    }
  });
  document.getElementById('jExport').addEventListener('click', () => {
    const arr = jLoad();
    if (!arr.length){ alert('暂无历史记录'); return; }
    const cols = ['symbol','tf','side','leverage','capital','entry_time','entry','exit_time','exit_price','stop','adds','exit_logic','pnl_u','pnl_pct_capital','result','exit_reason','logic','r0','r_final','max_r'];
    const esc = v => '"' + String(v ?? '').replace(/"/g,'""') + '"';
    const csv = '\ufeff' + cols.join(',') + '\n' +
      arr.map(r => cols.map(c => esc(r[c])).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `trade_journal_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
  });
  document.getElementById('jClear').addEventListener('click', () => {
    if (confirm('确认清空全部交易历史?')){ jSave([]); renderJournal(); }
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
    <div class="pp-head" style="color:${posColor(pos)}">#${pos.id} ${pos.side} · ${pos.entries.length}笔</div>
    <div class="pp-row">均价 ${fmt(posAvgEntry(pos))} · 保证金 ${cap.toFixed(0)}U</div>
    <div class="pp-row" data-pp-float style="font-weight:700;color:${u >= 0 ? 'var(--up)' : 'var(--down)'}">浮动 ${u >= 0 ? '+' : ''}${u.toFixed(1)}U (${cap ? (u/cap*100 >= 0 ? '+' : '') + (u/cap*100).toFixed(1) : '0.0'}%)</div>
    ${pos.entries.map((e,i) => `<div class="pp-row" style="opacity:.75">${i+1}) ${e.isAdd ? '加' : '开'} @ ${e.price.toPrecision(6)} · ${e.capital}U${e.leverage>1?` · ${e.leverage}x`:''}</div>`).join('')}
    <div class="pp-actions">
      <button class="btn" data-pp="add">加仓</button>
      <button class="btn" data-pp="stop">改止损</button>
      <button class="btn" data-pp="close-pop">✕</button>
    </div>
    <div class="pp-stop" style="display:none;">
      <input type="number" class="pp-stop-inp" step="any" value="${pos.stop ?? ''}" placeholder="留空=取消止损">
      <button class="btn" data-pp="stop-save">保存</button>
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
      const card = document.querySelector(`.pos-card[data-pid="${pos.id}"]`);
      if (!card){ alert('未找到该持仓卡片, 请在右侧面板操作'); return; }
      card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      for (const s of card.querySelectorAll('.sec-add,.sec-exit')) s.style.display = 'none';
      const f = card.querySelector('.sec-add');
      if (f){
        f.style.display = 'flex';
        const bd = addBudget(pos);
        const cap = f.querySelector('.inp-add-cap');
        if (cap){ cap.value = bd.avail > 0 ? bd.avail.toFixed(2) : ''; cap.focus(); }
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
      pos.stop = (isNaN(v) || v <= 0) ? null : v;
      ensureRisk(pos);   // 首次设置止损时锁定 R 基准
      renderJournal();   // 同步卡片显示 + 重画持仓线
      closePosPopup();
      break;
    }
    case 'close-pop': closePosPopup(); break;
  }
});
