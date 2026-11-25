// js/21-logic-core.js — 开单准入、三层共振、黑天鹅一票否决、充分定价验收、MAE/MFE、2x2 归因与 Markdown 导出纯计算内核
// 零 DOM 依赖、UMD 导出（浏览器 window.LogicCore / Node CommonJS 单测复用）
(function (root) {
  'use strict';

  const VERSION = 'logic-studio/2.0';

  const LOGIC_TYPES = [
    { id: 'resonance', label: 'A+B 双重共振', short: '共振', desc: '外部事件驱动 + 关键价格结构同时共振（最高胜率）' },
    { id: 'event', label: 'A 类 · 事件/基本面驱动', short: '事件驱动', desc: '宏观流动性、政策、财报预期差、跨市场传导滞后' },
    { id: 'structure', label: 'B 类 · 结构/统计优势', short: '结构优势', desc: '大趋势回调关键均线支撑、窄幅整理末端突破等规则位' },
  ];

  const EVENT_TIERS = [
    { id: 'S', label: 'S 级 · 总阀门', period: '数月～1年以上', desc: '美联储利率路径、财政部发债/回购、核心通胀拐点、系统性信用危机' },
    { id: 'A', label: 'A 级 · 主引擎', period: '1～6个月', desc: 'ETF 持续净流拐点、巨头 CapEx 上修/砍单、重大监管政策落地' },
    { id: 'B', label: 'B 级 · 战术级', period: '数天～2周', desc: '龙头财报超预期、高管变动、项目重大升级、局部事件' },
    { id: 'C', label: 'C 级 · 纯噪音', period: '数分钟～1天', desc: '名人/分析师口水战、社媒情绪宣泄、旧闻复述（建议直接屏蔽）' },
  ];

  const HORIZONS = ['短线', '中线', '长线'];

  const OPPONENT_PRESETS = [
    '恐慌盘割肉出清',
    '拥挤空头轧空/逼空',
    '踏空资金 FOMO 补涨',
    '高倍多头连环爆仓',
    '利好兑现主力派发',
    '跨市场定价滞后盘',
    '机构风控被动去杠杆',
  ];

  const IMPULSE_VETOS = [
    {
      id: 'fomo_probe',
      text: '「先轻仓买一点占个坑，免得踏空」',
      reason: '没想清止损与赔率边界的试仓，本质是赌博',
    },
    {
      id: 'blind_dip',
      text: '「跌这么多肯定要反弹了」',
      reason: '缺乏驱动与右侧承接的纯主观猜底（禁止用绝对跌幅代替定价进度）',
    },
    {
      id: 'revenge_trade',
      text: '「刚才那笔止损了，这波要一把捞回来」',
      reason: '情绪报复单，必须强制离屏冷却',
    },
  ];

  const EXIT_REASONS = [
    { id: 'hard_stop', label: '触发硬止损', compliant: true },
    { id: 'falsified_early', label: '逻辑证伪提前走', compliant: true },
    { id: 'time_stop', label: '时间止损离场', compliant: true },
    { id: 'target_realized', label: '逻辑兑现止盈', compliant: true },
    { id: 'emotional_violation', label: '情绪化违纪离场/扛单', compliant: false },
  ];

  const QUADRANT_META = {
    rr: {
      id: 'rr',
      label: '逻辑对 + 赚钱',
      short: '复利单',
      tone: 'up',
      action: '核心复利来源 → 提炼共振特征，沉淀为标准模式卡片',
    },
    rl: {
      id: 'rl',
      label: '逻辑对 + 亏钱',
      short: '正常损耗',
      tone: 'accent',
      action: '正常概率经营成本 → 仅检查入场精度与止损宽度，不盲改大逻辑',
    },
    wl: {
      id: 'wl',
      label: '逻辑错 + 亏钱',
      short: '认知/违纪错单',
      tone: 'down',
      action: '排查认知盲区、权重错配或执行变形 → 写入禁忌清单',
    },
    wr: {
      id: 'wr',
      label: '逻辑错 + 赚钱',
      short: '☠️ 高危毒药单',
      tone: 'warn',
      action: '最危险的运气/扛单盈利 → 剥离出有效正收益，严禁复制此次开仓逻辑',
    },
  };

  // 5 类逻辑冲突快速决策对照表（融合第一轮 4 类 + BTC 60亿被盗第 5 类）
  const CONFLICT_MATRIX = [
    {
      id: 'good_fund_bad_macro',
      state: '基本面好 vs 宏观/情绪差',
      signal: '利好财报/消息落地后「该涨不涨」、高开低走破位',
      meaning: '市场正在给估值杀 / 风险溢价定价（PE 压缩压过 EPS）',
      action: '绝不凭基本面盲目抄底；顺势做空或空仓等「利空砸不动」',
      recommendedSide: '空/空仓',
    },
    {
      id: 'bad_event_low_val',
      state: '一次性利空 vs 估值/筹码极低',
      signal: '边界清晰利空发酵但「该跌不跌」、缩量筑底或长下影承接',
      meaning: '悲观预期已充分计价（Priced in），空头筹码枯竭',
      action: '放弃追空，准备切入反转逻辑，等右侧结构突破试多',
      recommendedSide: '多(等右侧)',
    },
    {
      id: 'long_good_short_bad',
      state: '长线看好 vs 短线走坏',
      signal: '大周期基本面完好，但日线/周线跌破趋势均线',
      meaning: '周期错配（EPS 与 PE 博弈期），用长线理由指导短线开仓',
      action: '短线交易者坚决止损离场；长线配置者放弃杠杆，等右侧或极轻仓分批',
      recommendedSide: '按周期二选一',
    },
    {
      id: 'tech_vs_black_swan',
      state: '技术面支撑/历史复刻 vs S 级系统性黑天鹅',
      signal: '头部交易所巨额被盗/暴雷/稳定币脱锚初期，价格在 MA120 等支撑位短期横盘没大跌',
      meaning: '不是买盘承接，而是机构风控滞后 + 流动性冻结造成的「假抗跌」，严防确认偏误',
      action: '一票否决所有常规技术买点！强制空仓等待 ≥3～5 天冷却期 + 币本位去杠杆出清',
      recommendedSide: '强制空仓',
    },
    {
      id: 'balanced_chop',
      state: '双边逻辑势均力敌',
      signal: '价格无序宽幅拉锯、上下影齐飞、没有明确趋势与放量方向',
      meaning: '主力资金分歧极大，市场本身在盲猜',
      action: '直接空仓跳过，不做高难度硬骨头，换逻辑更纯粹的标的',
      recommendedSide: '空仓跳过',
    },
  ];

  // 跨市场充分定价日常监控快捷外链（来自最新对话整理）
  const MONITOR_LINKS = [
    {
      id: 'coinglass',
      name: 'CoinGlass',
      tag: '合约持仓 / ETF / 爆仓',
      url: 'https://www.coinglass.com/pro/futures/OpenInterest',
      tip: '日常 5 分钟体检：查全网 OI、资金费率、爆仓与现货 ETF 净流向',
    },
    {
      id: 'coinank',
      name: 'CoinAnk',
      tag: '全网持仓（含 Hyperliquid）',
      url: 'https://coinank.com/zh/oi',
      tip: '覆盖 CEX + CME + Hyperliquid 链上永续，右上角务必切为「币本位」',
    },
    {
      id: 'coinalyze',
      name: 'Coinalyze',
      tag: 'K 线 + 聚合 OI 副图',
      url: 'https://coinalyze.net/bitcoin/open-interest/',
      tip: '核对「价格暴跌 + 币本位 OI 断崖骤降」真实去杠杆出清瞬间最顺手',
    },
  ];

  // 预置模式卡片（来自文档实战案例，用户可自由编辑或删除）
  const DEFAULT_PATTERNS = [
    {
      id: 'pat-macro-squeeze',
      name: '熊市极端悲观下的「宏观流动性突袭轧空」',
      tier: 'S',
      logicType: 'resonance',
      trigger: '价格长期横盘筑底 + 市场看空拥挤度极高（预测市场/费率极端偏空） + 央行/财政部突发流动性干预信号',
      entryAnchor: '首根放量突破震荡箱体的日线收盘或次日回踩箱体上沿；以箱体上沿为硬止损，盈亏比通常 > 3:1',
      invalidLine: '日线收盘跌回突破箱体内部，或 3～5 根 K 线未能脱离成本区',
      exitRule: '① 空头清算放巨量且舆情从极度恐慌切为一致看多（对手盘消失）；② 政策进入正式落地窗口期「买预期、卖事实」滞涨即收割',
      trap: '切忌盈利后把波段轧空逻辑擅自改成长线牛市信仰，导致 25%+ 利润全数坐回电梯',
      builtin: true,
    },
    {
      id: 'pat-cross-lag',
      name: '跨市场定价传导滞后（二阶映射狙击）',
      tier: 'A',
      logicType: 'resonance',
      trigger: '核心锚点资产（如美股龙头/美债/ETH）因重磅催化已放量剧烈重定价，强关联二阶标的尚未跟进',
      entryAnchor: '确认关联标的仍停留在原高位/低位震荡结构内尚未破位，以前高/前低作窄止损顺传导方向介入',
      invalidLine: '主锚点迅速反转证伪，或二阶标的不跟反走突破防守极值',
      exitRule: '二阶标的完成补跌/补涨并出现情绪极端放量减速时果断一刀切平仓',
      trap: '不要在直接标的一阶脉冲已经走完、没好止损位时情绪追单',
      builtin: true,
    },
  ];

  const DEFAULT_TABOOS = [
    {
      id: 'taboo-black-swan',
      title: '【S 级黑天鹅一票否决律】',
      rule: '头部交易所巨额被盗/暴雷、稳定币脱锚、大型机构破产等系统性流动性危机爆发时，所有常规技术买点（MA20/60/120、历史形态复刻）自动作废，强制空仓观望。',
      source: '系统预置铁律',
      createdAt: 0,
    },
    {
      id: 'taboo-cooling',
      title: '【大雷「3～5 日冷却验证期」铁律】',
      rule: '边界未明的大型利空，严禁凭数小时或 1 天内「没大跌」就误判为「买盘强/该跌不跌」。必须等 3～5 个完整交易日消化机构风控盘与提币潮，且放量收复起跌结构后再评估做多。',
      source: '系统预置铁律',
      createdAt: 0,
    },
  ];

  // ---- 1. 三层共振矩阵（宏观流动性 × 跨市场风险偏好 × 标的内生结构）----
  function layerVal(v) {
    if (v === 'tailwind' || v === 1 || v === '+1') return 1;
    if (v === 'headwind' || v === -1 || v === '-1') return -1;
    return 0;
  }

  function evalThreeLayerMatrix(layers = {}, side = '多') {
    const m = layerVal(layers.macro);
    const c = layerVal(layers.crossMarket);
    const e = layerVal(layers.endogenous);
    const score = m + c + e;
    const headwinds = [m, c, e].filter(x => x === -1).length;
    const tailwinds = [m, c, e].filter(x => x === 1).length;

    let regime = 'mixed';
    let label = '分歧震荡期';
    let tone = 'muted';
    let advice = '多空因子存在分歧，交由盘面 K 线与关键催化剂压力测试担任最终裁判。';

    if (headwinds === 3) {
      regime = 'all_headwind';
      label = '3/3 全逆风 · 流动性坍塌高危';
      tone = 'down';
      advice = '禁用一切左侧抄底与均线支撑买点（MA20/60/120 地基已空），只能空仓避险或顺势反弹承压做空。';
    } else if (tailwinds === 3) {
      regime = 'all_tailwind';
      label = '3/3 全顺风 · 高赔率主升共振';
      tone = 'up';
      advice = '流动性、风险偏好与标的自身结构全面共振，关键均线回踩（MA20/60/120）胜率显著跃升。';
    } else if (headwinds === 2) {
      regime = 'headwind_bias';
      label = '2 逆风 vs 1 顺风 · 偏空博弈';
      tone = 'warn';
      advice = '大环境偏紧，做多须降仓位并严格等待右侧确认；若宏观顺风但单一标的利空，留意「该跌不跌」反转。';
    } else if (tailwinds === 2) {
      regime = 'tailwind_bias';
      label = '2 顺风 vs 1 逆风 · 偏多结构';
      tone = 'up';
      advice = '整体顺风占优，重点排查唯一逆风项是否属于 S 级不可抗力，再以结构位控制止损。';
    }

    const longBlocked = headwinds === 3 && side === '多';
    const shortBlocked = tailwinds === 3 && side === '空';
    return {
      values: { macro: m, crossMarket: c, endogenous: e },
      score,
      headwinds,
      tailwinds,
      regime,
      label,
      tone,
      advice,
      longBlocked,
      shortBlocked,
    };
  }

  // ---- 2. S 级系统性黑天鹅一票否决 & 确认偏误防呆 ----
  function evalBlackSwanGate(opts = {}) {
    const hasCrisis = !!opts.hasSystemicCrisis;
    const side = opts.side || '多';
    const days = Number(opts.crisisDaysElapsed);
    const daysValid = Number.isFinite(days) && days >= 0 ? days : 0;
    const reclaimed = !!opts.reclaimedStructure;
    const usedNoDrop = !!opts.usedNoDropAsBullish;

    if (!hasCrisis) {
      return { veto: false, confirmationBiasWarn: false, coolingNeeded: false, message: '' };
    }
    const coolingNeeded = daysValid < 3;
    const veto = side === '多' && (coolingNeeded || !reclaimed);
    const confirmationBiasWarn = side === '多' && usedNoDrop && (coolingNeeded || !reclaimed);

    let message = '';
    if (veto) {
      message = coolingNeeded
        ? `触发【S 级黑天鹅一票否决】：系统性危机仅过去 ${daysValid} 天（不足 3～5 日冷却验证期），常规均线支撑与历史复刻自动作废！初期「没大跌」多为机构风控滞后与流动性冻结假象，严禁左侧做多。`
        : '触发【S 级黑天鹅一票否决】：虽已过 3 日冷却，但价格尚未放量收复事件起跌结构，暂不解除做多禁令。';
    } else if (side === '多') {
      message = '已通过 3～5 日冷却与右侧结构收复前提，仍需核验币本位 OI 是否真实去杠杆出清。';
    } else {
      message = '系统性危机期间顺势防崩盘/做空，留意政策干预或极端空头拥挤引发的剧烈空头回补。';
    }
    return { veto, confirmationBiasWarn, coolingNeeded, daysElapsed: daysValid, message };
  }

  // ---- 3. 充分定价（Fully Priced in）自上而下 + 4 项硬验收 ----
  function evalFullyPricedIn(input = {}) {
    const upstream = !!input.upstreamBlunted;       // 第1层：美债收益率/DXY 鹰派钝化
    const midstream = !!input.midstreamDiverged;    // 第2层：纳指/AI 不破前低 & VIX 不创新高
    const downstream = !!input.downstreamResilient; // 第3层：标的相对抗跌 / ETF 流出收窄

    const c1 = !!input.realDeleveraging;            // 1. 币本位 OI 缩水 25%~40%+ & 费率负/Skew 倒挂（已区分 CME）
    const c2 = !!input.secondBadNewsTest;           // 2. 沉淀 ≥3~5 天后通过二次利空压力测试
    const c3 = !!input.lowVolRetest;                // 3. 放量下跌后缩量二次探底不破前低
    const c4 = !!input.rightSideBreakout;           // 4. 右侧放量突破震荡区间/收复最后反弹高点

    const chainPassed = [upstream, midstream, downstream].filter(Boolean).length;
    const hardCount = [c1, c2, c3, c4].filter(Boolean).length;
    const coreThree = c1 && c2 && c3;
    const upstreamStillBleeding = !upstream && !midstream;

    let stage = 'not_priced_in';
    let label = '尚未充分定价 · 严禁左侧抄底';
    let tone = 'down';
    let guidance = '上游仍在失血或去杠杆尚未出清，下跌中的单日阳线大概率是死猫跳或空头回补反弹。';

    if (coreThree && c4 && !upstreamStillBleeding) {
      stage = 'trigger_ready';
      label = '右侧做多扳机就绪（充分定价 + 结构破坏）';
      tone = 'up';
      guidance = '上游已出现止血钝化，且 4 项硬指标全部通过，允许按 1%～2% 以损定仓执行右侧做多。';
    } else if (coreThree && !upstreamStillBleeding) {
      stage = 'watch_only';
      label = '进入充分定价观察池（终止做空 · 等右侧突破）';
      tone = 'warn';
      guidance = '做空赔率已消失，应止盈空单并将标的移入重点观察池；但未出第 4 项右侧突破前，勿重仓抄底耗在磨底震荡里。';
    } else if (downstream && upstreamStillBleeding) {
      stage = 'dead_cat_risk';
      label = '高危假企稳：上游仍在失血（木桶短板警报）';
      tone = 'down';
      guidance = '第 1 层（美债/美元）与第 2 层（纳指/VIX）均未止血，下游单独抗跌极易补跌；底部永远由最晚止血的上游决定。';
    } else if (hardCount >= 2) {
      stage = 'partial';
      label = `部分指标达标（${hardCount}/4）· 继续观察`;
      tone = 'muted';
      guidance = '尚缺关键出清或压力测试证据，切忌用「已经跌了 40%」代替定价进度。';
    }

    return {
      chainPassed,
      hardCount,
      coreThree,
      upstreamStillBleeding,
      stage,
      label,
      tone,
      guidance,
    };
  }

  // ---- 4. 以损定仓与预期盈亏比引擎 ----
  function calcRiskSizing({ equity, riskPct = 1.5, entry, stop, target = null, side = '多', maxLeverage = 10 }) {
    const eq = Number(equity);
    const rp = Number(riskPct);
    const en = Number(entry);
    const st = Number(stop);
    const tg = target != null && target !== '' ? Number(target) : null;
    const maxLev = Math.max(1, Number(maxLeverage) || 10);
    const dir = side === '空' ? -1 : 1;

    if (!Number.isFinite(eq) || eq <= 0 || !Number.isFinite(en) || en <= 0 || !Number.isFinite(st) || st <= 0) {
      return { valid: false, error: '请填写有效的账户资金、入场价与硬止损价' };
    }
    const riskPerUnit = (en - st) * dir;
    if (riskPerUnit <= 0) {
      return {
        valid: false,
        error: side === '空' ? '空单硬止损必须高于入场价' : '多单硬止损必须低于入场价',
      };
    }
    if (!Number.isFinite(rp) || rp <= 0) {
      return { valid: false, error: '单笔风险占比须大于 0%' };
    }

    const stopDistPct = (riskPerUnit / en) * 100;
    const maxLossU = eq * (rp / 100);
    const notionalU = maxLossU / (stopDistPct / 100);
    const effectiveLev = notionalU / eq;
    const suggestedLev = Math.min(maxLev, Math.max(1, Math.ceil(effectiveLev)));
    const suggestedMargin = notionalU / suggestedLev;
    const qty = notionalU / en;

    let rr = null;
    let targetDistPct = null;
    if (tg != null && Number.isFinite(tg) && tg > 0) {
      const rewardPerUnit = (tg - en) * dir;
      rr = rewardPerUnit / riskPerUnit;
      targetDistPct = (rewardPerUnit / en) * 100;
    }

    const flags = {
      riskOverLimit: rp > 2.0 + 1e-9,
      leverageCapped: effectiveLev > maxLev,
      rrReject: rr != null && rr < 2.0,
      rrWarn: rr != null && rr >= 2.0 && rr < 2.5,
      rrGood: rr != null && rr >= 2.5,
    };

    return {
      valid: true,
      side,
      entry: en,
      stop: st,
      target: tg,
      riskPct: +rp.toFixed(2),
      stopDistPct: +stopDistPct.toFixed(2),
      maxLossU: +maxLossU.toFixed(2),
      notionalU: +notionalU.toFixed(2),
      effectiveLev: +effectiveLev.toFixed(2),
      suggestedLev,
      suggestedMargin: +suggestedMargin.toFixed(2),
      qty: +qty.toPrecision(6),
      rr: rr != null ? +rr.toFixed(2) : null,
      targetDistPct: targetDistPct != null ? +targetDistPct.toFixed(2) : null,
      flags,
    };
  }

  // ---- 5. 持仓期自动 MAE / MFE / R 量化体检（零未来函数：严格限定 [entryTs .. exitTs]）----
  function barFields(b) {
    if (Array.isArray(b)) return { time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] };
    return { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close };
  }

  function calcTradeExcursion(bars, { side = '多', entryTs, exitTs, entryPrice, stopPrice = null, exitPrice }) {
    const en = Number(entryPrice);
    const ex = Number(exitPrice);
    const st = stopPrice != null && stopPrice !== '' ? Number(stopPrice) : null;
    const dir = side === '空' ? -1 : 1;
    if (!Number.isFinite(en) || en <= 0 || !Number.isFinite(ex) || ex <= 0) {
      return null;
    }
    const riskUnit = st != null && Number.isFinite(st) && (en - st) * dir > 0 ? (en - st) * dir : null;
    const realizedMove = (ex - en) * dir;
    const realizedPct = (realizedMove / en) * 100;
    const realizedR = riskUnit ? realizedMove / riskUnit : null;

    let worstMove = Math.min(0, realizedMove);
    let bestMove = Math.max(0, realizedMove);
    let barsHeld = 0;

    if (Array.isArray(bars) && bars.length && Number.isFinite(entryTs)) {
      const endTs = Number.isFinite(exitTs) ? exitTs : Infinity;
      for (let i = 0; i < bars.length; i++) {
        const b = barFields(bars[i]);
        if (b.time <= entryTs) continue; // 开仓当根收盘入场，不计当根历史波动
        if (b.time > endTs) break;
        barsHeld++;
        const adv = ((dir === 1 ? b.low : b.high) - en) * dir;
        const fav = ((dir === 1 ? b.high : b.low) - en) * dir;
        if (adv < worstMove) worstMove = adv;
        if (fav > bestMove) bestMove = fav;
      }
    }

    const maePct = (worstMove / en) * 100; // <= 0
    const mfePct = (bestMove / en) * 100;  // >= 0
    const maeR = riskUnit ? worstMove / riskUnit : null;
    const mfeR = riskUnit ? bestMove / riskUnit : null;
    const retention = mfePct > 1e-6 ? realizedMove / bestMove : null;

    const hints = [];
    if (maeR != null && maeR <= -0.85 && realizedMove > 0) hints.push('MAE 逼近止损（入场偏急或止损偏窄）');
    if (mfeR != null && mfeR >= 2.0 && realizedR != null && realizedR < mfeR * 0.4) {
      hints.push('曾达 ≥2R 但大幅回吐（注意止盈兑现窗口，防坐电梯）');
    }
    if (realizedR != null && realizedR < -1.15) {
      hints.push('实际亏损超出计划 -1R（疑似拖延离场/扛单或跳空滑点）');
    }

    return {
      barsHeld,
      realizedPct: +realizedPct.toFixed(2),
      realizedR: realizedR != null ? +realizedR.toFixed(2) : null,
      maePct: +maePct.toFixed(2),
      maeR: maeR != null ? +maeR.toFixed(2) : null,
      mfePct: +mfePct.toFixed(2),
      mfeR: mfeR != null ? +mfeR.toFixed(2) : null,
      retention: retention != null ? +(retention * 100).toFixed(1) : null,
      hints,
    };
  }

  // ---- 6. 20 笔横向汇总与合规率审计（PDF 第 7 页 & 第 10 页三大核心数字）----
  function isCardCompliant(card) {
    if (!card) return false;
    const c = card.compliance || {};
    if (c.violated) return false;
    if (c.isPreLocked === false) return false;
    if ((c.vetoTriggered || []).length > 0) return false;
    // 时间戳铁律：成交时间早于锁卡时间 → 违纪毒药单
    if (card.orderFilledAt != null && card.lockedAt != null && card.orderFilledAt < card.lockedAt) return false;
    if (card.evolution?.exitReason === 'emotional_violation') return false;
    return !!card.lockedAt;
  }

  function computeAuditMetrics(cards, opts = {}) {
    const all = Array.isArray(cards) ? cards : [];
    const windowSize = opts.windowSize || 20; // 0 表示全量
    const days = opts.days || 0;
    const now = opts.now || Date.now();

    let list = all
      .filter(c => c && c.status !== 'draft')
      .sort((a, b) => (b.lockedAt || b.createdAt || 0) - (a.lockedAt || a.createdAt || 0));

    if (days > 0) {
      const cutoff = now - days * 86400 * 1000;
      list = list.filter(c => (c.lockedAt || c.createdAt || 0) >= cutoff);
    }
    if (windowSize > 0 && list.length > windowSize) {
      list = list.slice(0, windowSize);
    }

    const total = list.length;
    const compliantCount = list.filter(isCardCompliant).length;
    const complianceRate = total ? +(compliantCount / total * 100).toFixed(1) : null;

    let complianceStatus = 'empty';
    let complianceAdvice = '暂无已锁定记录，先用 60 秒开仓准入卡开始第一笔规范交易。';
    if (total > 0) {
      if (complianceRate >= 90) {
        complianceStatus = 'ok';
        complianceAdvice = '合规率 ≥90%：执行纪律过关！若账户仍亏损，请聚焦优化逻辑筛选框架或止损位精度。';
      } else if (complianceRate >= 70) {
        complianceStatus = 'warn';
        complianceAdvice = '合规率 70%～89%：存在偶发手痒单，建议打开「严格门禁」拦截无卡下单。';
      } else {
        complianceStatus = 'penalty';
        complianceAdvice = '⚠️ 合规率 <70%（系统失控警报）：建议立刻将单笔风险上限砍半（1.5% → 0.5%），直至连续 10 笔 100% 合规！';
      }
    }

    const quadrants = {
      rr: { count: 0, pct: 0, totalR: 0, avgR: null },
      rl: { count: 0, pct: 0, totalR: 0, avgR: null },
      wl: { count: 0, pct: 0, totalR: 0, avgR: null },
      wr: { count: 0, pct: 0, totalR: 0, avgR: null },
    };

    let reviewedCount = 0;
    let totalRAll = 0;
    let totalRWithoutPoison = 0;
    let delayExtraLossR = 0;
    let logicScoreSum = 0, logicScoreN = 0;
    let chartScoreSum = 0, chartScoreN = 0;
    let blindSpotCount = 0, weightErrorCount = 0;
    const driverMap = {}; // key -> { label, count, positiveR, totalR }

    for (const card of list) {
      const pm = card.postmortem || {};
      const ev = card.evolution || {};
      const m = ev.metrics || {};
      const r = Number.isFinite(m.realizedR) ? m.realizedR : null;
      const q = pm.quadrant;

      if (q && quadrants[q]) {
        reviewedCount++;
        quadrants[q].count++;
        if (r != null) quadrants[q].totalR += r;
      }

      if (r != null) {
        totalRAll += r;
        const isPoison = q === 'wr' || (!isCardCompliant(card) && r > 0);
        if (!isPoison) totalRWithoutPoison += r;

        // 指标 2：被证伪后拖延离场 / 违纪离场多亏了多少 R（超出 -1R 的部分，或证伪仍扛单的额外亏损）
        if ((ev.falsified && !ev.followedPlan && r < 0) || ev.exitReason === 'emotional_violation') {
          if (r < -1) delayExtraLossR += Math.abs(r + 1);
          else if (r < 0) delayExtraLossR += Math.abs(r);
        }

        // 指标 3：哪类驱动主线贡献了核心正 R 收益
        const cap = card.capsule || {};
        const tierKey = cap.eventTier ? `${cap.eventTier}级` : '纯结构/未分级';
        const typeObj = LOGIC_TYPES.find(t => t.id === cap.logicType);
        const dKey = `${tierKey} · ${typeObj ? typeObj.short : '未分类'}`;
        if (!driverMap[dKey]) driverMap[dKey] = { label: dKey, count: 0, positiveR: 0, totalR: 0 };
        driverMap[dKey].count++;
        driverMap[dKey].totalR += r;
        if (r > 0 && q !== 'wr') driverMap[dKey].positiveR += r;
      }

      if (Number.isFinite(pm.logicScore)) { logicScoreSum += pm.logicScore; logicScoreN++; }
      if (Number.isFinite(pm.chartScore)) { chartScoreSum += pm.chartScore; chartScoreN++; }
      if (pm.blindSpot === '已公开漏看') blindSpotCount++;
      if (pm.blindSpot === '权重分配错误') weightErrorCount++;
    }

    for (const q of Object.values(quadrants)) {
      q.pct = reviewedCount ? +(q.count / reviewedCount * 100).toFixed(1) : 0;
      q.totalR = +q.totalR.toFixed(2);
      q.avgR = q.count ? +(q.totalR / q.count).toFixed(2) : null;
    }

    const totalPosR = Object.values(driverMap).reduce((a, x) => a + x.positiveR, 0);
    const driverBreakdown = Object.values(driverMap)
      .map(d => ({
        ...d,
        positiveR: +d.positiveR.toFixed(2),
        totalR: +d.totalR.toFixed(2),
        sharePct: totalPosR > 1e-6 ? +(d.positiveR / totalPosR * 100).toFixed(1) : 0,
      }))
      .sort((a, b) => b.positiveR - a.positiveR || b.totalR - a.totalR);

    const poisonCount = quadrants.wr.count;
    const poisonRate = reviewedCount ? +(poisonCount / reviewedCount * 100).toFixed(1) : 0;

    return {
      total,
      reviewedCount,
      pendingReviewCount: list.filter(c => c.status === 'closed' || (c.status === 'locked' && !c.postmortem?.quadrant)).length,
      compliantCount,
      complianceRate,
      complianceStatus,
      complianceAdvice,
      quadrants,
      poisonCount,
      poisonRate,
      totalRAll: +totalRAll.toFixed(2),
      totalRWithoutPoison: +totalRWithoutPoison.toFixed(2),
      delayExtraLossR: +delayExtraLossR.toFixed(2),
      driverBreakdown,
      avgLogicScore: logicScoreN ? +(logicScoreSum / logicScoreN).toFixed(1) : null,
      avgChartScore: chartScoreN ? +(chartScoreSum / chartScoreN).toFixed(1) : null,
      blindSpotCount,
      weightErrorCount,
    };
  }

  // ---- 7. 标准化双视角 Markdown 导出（兼容 PDF 第 9–10 页模板 + 最新三层共振/充分定价）----
  function fmtIsoDate(ms) {
    if (!ms) return new Date().toISOString().slice(0, 10);
    return new Date(ms).toISOString().slice(0, 10);
  }
  function fmtIsoDateTime(ms) {
    if (!ms) return '未锁定';
    return new Date(ms).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
  }
  function layerLabel(v) {
    const n = layerVal(v);
    return n > 0 ? '顺风(+1)' : n < 0 ? '逆风(-1)' : '中性(0)';
  }

  function exportCardMarkdown(card) {
    const cap = card.capsule || {};
    const ev = card.evolution || {};
    const pm = card.postmortem || {};
    const it = card.iteration || {};
    const m = ev.metrics || {};
    const dateStr = fmtIsoDate(card.lockedAt || card.createdAt);
    const sym = card.symbolLabel || card.symbol || '标的';
    const three = evalThreeLayerMatrix(cap.threeLayer || {}, cap.side || '多');
    const isEvent = cap.logicType === 'event' || cap.logicType === 'resonance';
    const isStruct = cap.logicType === 'structure' || cap.logicType === 'resonance';
    const compliant = isCardCompliant(card);
    const exitLbl = EXIT_REASONS.find(x => x.id === ev.exitReason)?.label || ev.exitReason || '—';

    const nodesMd = (ev.nodes || []).length
      ? ev.nodes.map((n, i) => `  - 节点 ${i + 1}（${n.barLabel || fmtIsoDateTime(n.ts)}）：${n.event || '—'} → [盘面反应：${n.reaction || '—'}${n.price != null ? ` @ ${n.price}` : ''}]`).join('\n')
      : '  - （暂无盘中演化节点）';

    const qCheck = id => (pm.quadrant === id ? 'x' : ' ');
    const alignMark = val => (pm.alignment === val ? `[x] ${val}` : `[ ] ${val}`);
    const fngLine = cap.fngValue != null ? `FGI=${cap.fngValue}（${cap.fngLabel || ''}）` : '—';
    const pricedSummary = cap.pricedInSummary ? `\n- **充分定价前置验收**：${cap.pricedInSummary}` : '';
    const conflictSummary = cap.conflictNote ? `\n- **逻辑冲突 If-Then 剧本**：${cap.conflictNote}` : '';

    return [
      `# 交易复盘卡 | ${sym} | ${dateStr}`,
      '',
      `> **复盘铁律**：模块一锁定于 \`${fmtIsoDateTime(card.lockedAt)}\`（原稿不可篡改）；合规状态：**${compliant ? '✅ 合规（事前锁卡）' : '⚠️ 违纪/补录'}**。模块三在整段行情兑现后填写。`,
      '',
      '---',
      '',
      '## 一、时间胶囊（开仓前/当时的盲视视角）',
      '',
      `- **交易标的 / 方向 / 预期周期**：${sym} / 做${cap.side || '多'} / ${cap.horizon || '—'}（级别：${card.tf || '1D'}）`,
      `- **三层共振矩阵定性**：宏观流动性 \`${layerLabel(cap.threeLayer?.macro)}\` · 跨市场风险偏好 \`${layerLabel(cap.threeLayer?.crossMarket)}\` · 标的内生结构 \`${layerLabel(cap.threeLayer?.endogenous)}\` → **${three.label}**`,
      `- **系统性黑天鹅检视**：${cap.blackSwan?.hasSystemicCrisis ? `存在系统性危机（已过 ${cap.blackSwan.crisisDaysElapsed ?? 0} 天，结构收复：${cap.blackSwan.reclaimedStructure ? '是' : '否'}）` : '无系统性黑天鹅事件'}${pricedSummary}`,
      '- **当时已获取的核心信息**：',
      `  1. 宏观/行业信息（${cap.eventTier ? cap.eventTier + ' 级' : '未分级'}）：${cap.infoMacro || '—'}`,
      `  2. 市场情绪/对手盘拥挤度（${fngLine}）：${(cap.opponentTags || []).join('、') || '—'}${cap.opponentNote ? ' · ' + cap.opponentNote : ''}`,
      '- **当时的行情走势与技术结构**：',
      `  - 大周期背景：${cap.techBig || cap.maSnap || '—'}`,
      `  - 入场级结构：${cap.techEntry || '—'}`,
      '- **我的核心入场逻辑（二选一或共振）**：',
      `  - [${isEvent ? 'x' : ' '}] 事件/驱动逻辑（Why Now）：${cap.whyNow || '—'}`,
      `  - [${isStruct ? 'x' : ' '}] 结构/统计优势逻辑：${cap.techEntry || '—'}${conflictSummary}`,
      '- **事前写死的风控边界**：',
      `  - 入场价：\`${cap.entryPrice ?? '—'}\` ｜ 硬止损价：\`${cap.stopPrice ?? '—'}\` ｜ 单笔风险占比：\`${cap.riskPct ?? '—'}%\``,
      `  - **逻辑证伪条件**：价格 ${cap.invalidPrice || '—'} ｜ 时间上限 ${cap.invalidTime || '—'}`,
      `  - **预期兑现目标**：\`${cap.targetPrice ?? '—'}\`（初始预期盈亏比 \`${cap.plannedRR != null ? cap.plannedRR + ' : 1' : '—'}\`）｜ 止盈信号：${cap.exitAnchor || '—'}`,
      '',
      '---',
      '',
      '## 二、持仓演进与离场审计（盘中动态记录）',
      '',
      '- **开仓后的行情演化轨迹**：',
      nodesMd,
      '- **逻辑证伪与离场判定**：',
      `  - 逻辑是否被证伪：${ev.falsified ? `是（${ev.falsifyType || '未注明类型'}）` : '否'}`,
      `  - 实际离场价：\`${ev.exitPrice ?? '—'}\` ｜ 实际盈亏比（R 倍数）：\`${m.realizedR != null ? (m.realizedR >= 0 ? '+' : '') + m.realizedR + ' R' : '—'}\``,
      `  - 离场原因：${exitLbl}（按计划执行：${ev.followedPlan === false ? '否' : '是'}）`,
      '- **关键量化体检（自动计算）**：',
      `  - **MAE（最大浮亏）**：\`${m.maePct != null ? m.maePct + '%' : '—'}\`（${m.maeR != null ? m.maeR + 'R' : '—'}）`,
      `  - **MFE（最大浮盈）**：\`${m.mfePct != null ? '+' + m.mfePct + '%' : '—'}\`（${m.mfeR != null ? '+' + m.mfeR + 'R' : '—'}）｜ 利润留存率：\`${m.retention != null ? m.retention + '%' : '—'}\``,
      '',
      '---',
      '',
      '## 三、终局回看与主线归因（行情兑现后的上帝视角）',
      '',
      `- **整段行情最终走了多远**：${pm.fullMoveRange || '—'}`,
      '- **事后审视：真正主导这段行情的核心主线是什么？**',
      `  - [${pm.isNoise ? ' ' : (pm.realMainline ? 'x' : ' ')}] 明确主线主导：${pm.realMainline || '—'}`,
      `  - [${pm.isNoise ? 'x' : ' '}] 纯属随机波动/资金博弈噪音（噪音，不归因）`,
      '- **认知偏差对比**：',
      `  - 当时逻辑 vs 真实主线：${alignMark('完全吻合')} / ${alignMark('部分吻合')} / ${alignMark('完全错位')}`,
      `  - 偏差归因：${pm.blindSpot || '—'} ｜ 逻辑分：\`${pm.logicScore ?? '—'}/5\` ｜ K线执行分：\`${pm.chartScore ?? '—'}/5\``,
      '- **2×2 终局定性**：',
      `  - [${qCheck('rr')}] **逻辑对 + 赚钱**（标准复利单）`,
      `  - [${qCheck('rl')}] **逻辑对 + 亏钱**（正常概率损耗）`,
      `  - [${qCheck('wl')}] **逻辑错 + 亏钱**（认知盲区或违纪）`,
      `  - [${qCheck('wr')}] **逻辑错 + 赚钱**（高危毒药单 → 剥离运气）`,
      '',
      '---',
      '',
      '## 四、沉淀与下一次迭代',
      '',
      `1. **本次交易最值得保留的 1 个动作**：${it.keepAction || '—'}`,
      `2. **下次遇到同类走势，必须修正的 1 个硬规则**：${it.fixRule || '—'}`,
      '',
    ].join('\n');
  }

  const api = {
    VERSION,
    LOGIC_TYPES,
    EVENT_TIERS,
    HORIZONS,
    OPPONENT_PRESETS,
    IMPULSE_VETOS,
    EXIT_REASONS,
    QUADRANT_META,
    CONFLICT_MATRIX,
    MONITOR_LINKS,
    DEFAULT_PATTERNS,
    DEFAULT_TABOOS,
    evalThreeLayerMatrix,
    evalBlackSwanGate,
    evalFullyPricedIn,
    calcRiskSizing,
    calcTradeExcursion,
    isCardCompliant,
    computeAuditMetrics,
    exportCardMarkdown,
  };

  root.LogicCore = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
