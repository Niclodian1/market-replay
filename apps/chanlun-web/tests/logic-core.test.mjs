import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const LogicCore = require('../js/21-logic-core.js');

test('evalThreeLayerMatrix blocks long on 3/3 all-headwind and highlights 3/3 tailwind', () => {
  const crash = LogicCore.evalThreeLayerMatrix({ macro: 'headwind', crossMarket: 'headwind', endogenous: 'headwind' }, '多');
  assert.equal(crash.regime, 'all_headwind');
  assert.equal(crash.score, -3);
  assert.equal(crash.longBlocked, true);
  assert.equal(crash.shortBlocked, false);

  const crashShort = LogicCore.evalThreeLayerMatrix({ macro: 'headwind', crossMarket: 'headwind', endogenous: 'headwind' }, '空');
  assert.equal(crashShort.longBlocked, false);

  const bull = LogicCore.evalThreeLayerMatrix({ macro: 'tailwind', crossMarket: 'tailwind', endogenous: 'tailwind' }, '多');
  assert.equal(bull.regime, 'all_tailwind');
  assert.equal(bull.score, 3);
  assert.equal(bull.longBlocked, false);
});

test('evalBlackSwanGate enforces S-tier one-vote veto, cooling period and confirmation-bias warning', () => {
  const vetoed = LogicCore.evalBlackSwanGate({
    hasSystemicCrisis: true,
    crisisDaysElapsed: 1,
    reclaimedStructure: false,
    side: '多',
    usedNoDropAsBullish: true,
  });
  assert.equal(vetoed.veto, true);
  assert.equal(vetoed.coolingNeeded, true);
  assert.equal(vetoed.confirmationBiasWarn, true);

  const stillNoReclaim = LogicCore.evalBlackSwanGate({
    hasSystemicCrisis: true,
    crisisDaysElapsed: 5,
    reclaimedStructure: false,
    side: '多',
  });
  assert.equal(stillNoReclaim.veto, true);
  assert.equal(stillNoReclaim.coolingNeeded, false);

  const cleared = LogicCore.evalBlackSwanGate({
    hasSystemicCrisis: true,
    crisisDaysElapsed: 5,
    reclaimedStructure: true,
    side: '多',
  });
  assert.equal(cleared.veto, false);
});

test('evalFullyPricedIn separates dead-cat bounce, watch-only pool, and right-side trigger', () => {
  const deadCat = LogicCore.evalFullyPricedIn({
    upstreamBlunted: false,
    midstreamDiverged: false,
    downstreamResilient: true,
    realDeleveraging: false,
  });
  assert.equal(deadCat.stage, 'dead_cat_risk');
  assert.equal(deadCat.upstreamStillBleeding, true);

  const watchOnly = LogicCore.evalFullyPricedIn({
    upstreamBlunted: true,
    midstreamDiverged: true,
    downstreamResilient: true,
    realDeleveraging: true,
    secondBadNewsTest: true,
    lowVolRetest: true,
    rightSideBreakout: false,
  });
  assert.equal(watchOnly.stage, 'watch_only');

  const trigger = LogicCore.evalFullyPricedIn({
    upstreamBlunted: true,
    midstreamDiverged: false,
    downstreamResilient: true,
    realDeleveraging: true,
    secondBadNewsTest: true,
    lowVolRetest: true,
    rightSideBreakout: true,
  });
  assert.equal(trigger.stage, 'trigger_ready');
});

test('calcRiskSizing computes position sizing by stop risk and validates R:R', () => {
  const res = LogicCore.calcRiskSizing({
    equity: 100000,
    riskPct: 1.5,
    entry: 100,
    stop: 95,
    target: 115,
    side: '多',
    maxLeverage: 10,
  });
  assert.equal(res.valid, true);
  assert.equal(res.maxLossU, 1500);
  assert.equal(res.stopDistPct, 5);
  assert.equal(res.notionalU, 30000);
  assert.equal(res.suggestedLev, 1);
  assert.equal(res.suggestedMargin, 30000);
  assert.equal(res.rr, 3);
  assert.equal(res.flags.riskOverLimit, false);
  assert.equal(res.flags.rrGood, true);

  const overRisk = LogicCore.calcRiskSizing({
    equity: 100000,
    riskPct: 3,
    entry: 100,
    stop: 105,
    target: 92,
    side: '空',
  });
  assert.equal(overRisk.valid, true);
  assert.equal(overRisk.flags.riskOverLimit, true);
  assert.equal(overRisk.rr, 1.6);
  assert.equal(overRisk.flags.rrReject, true);

  const badStop = LogicCore.calcRiskSizing({ equity: 100000, riskPct: 1.5, entry: 100, stop: 102, side: '多' });
  assert.equal(badStop.valid, false);
});

test('calcTradeExcursion calculates exact MAE/MFE/R without entry-bar leakage', () => {
  // Bar tuples: [ts, open, high, low, close]
  const bars = [
    [1000, 90, 120, 80, 100], // entry bar: huge wick must NOT leak into MAE/MFE
    [2000, 100, 104, 97, 103], // low=97 (-3%, -0.6R)
    [3000, 103, 120, 101, 118], // high=120 (+20%, +4R)
    [4000, 118, 119, 109, 110], // exit at 110 (+10%, +2R)
    [5000, 110, 150, 70, 140], // after exitTs: must NOT leak
  ];
  const ex = LogicCore.calcTradeExcursion(bars, {
    side: '多',
    entryTs: 1000,
    exitTs: 4000,
    entryPrice: 100,
    stopPrice: 95,
    exitPrice: 110,
  });
  assert.equal(ex.barsHeld, 3);
  assert.equal(ex.realizedPct, 10);
  assert.equal(ex.realizedR, 2);
  assert.equal(ex.maePct, -3);
  assert.equal(ex.maeR, -0.6);
  assert.equal(ex.mfePct, 20);
  assert.equal(ex.mfeR, 4);
  assert.equal(ex.retention, 50);
});

test('computeAuditMetrics audits compliance penalty, poison trades, delay loss and driver breakdown', () => {
  const now = Date.now();
  const cards = [
    {
      id: '1', status: 'reviewed', lockedAt: now - 5000, orderFilledAt: now - 4000,
      compliance: { isPreLocked: true, violated: false, vetoTriggered: [] },
      capsule: { eventTier: 'S', logicType: 'resonance' },
      evolution: { falsified: false, followedPlan: true, exitReason: 'target_realized', metrics: { realizedR: 3.5 } },
      postmortem: { quadrant: 'rr', logicScore: 5, chartScore: 4 },
    },
    {
      id: '2', status: 'reviewed', lockedAt: now - 3000, orderFilledAt: now - 4000, // filled BEFORE locked -> violation!
      compliance: { isPreLocked: true, violated: false, vetoTriggered: [] },
      capsule: { eventTier: 'B', logicType: 'event' },
      evolution: { falsified: false, followedPlan: true, exitReason: 'target_realized', metrics: { realizedR: 2.0 } },
      postmortem: { quadrant: 'wr', logicScore: 2, chartScore: 3 },
    },
    {
      id: '3', status: 'reviewed', lockedAt: now - 2000, orderFilledAt: now - 1000,
      compliance: { isPreLocked: true, violated: false, vetoTriggered: [] },
      capsule: { eventTier: 'A', logicType: 'structure' },
      evolution: { falsified: true, followedPlan: false, exitReason: 'emotional_violation', metrics: { realizedR: -2.4 } },
      postmortem: { quadrant: 'wl', logicScore: 2, chartScore: 2, blindSpot: '权重分配错误' },
    },
  ];
  const audit = LogicCore.computeAuditMetrics(cards);
  assert.equal(audit.total, 3);
  assert.equal(audit.compliantCount, 1);
  assert.equal(audit.complianceRate, 33.3);
  assert.equal(audit.complianceStatus, 'penalty');
  assert.equal(audit.poisonCount, 1);
  assert.equal(audit.totalRAll, 3.1);
  assert.equal(audit.totalRWithoutPoison, 1.1);
  assert.equal(audit.delayExtraLossR, 1.4);
  assert.equal(audit.weightErrorCount, 1);
  assert.equal(audit.driverBreakdown[0].label, 'S级 · 共振');
  assert.equal(audit.driverBreakdown[0].sharePct, 100);
});

test('exportCardMarkdown generates complete dual-perspective Obsidian/Notion template', () => {
  const md = LogicCore.exportCardMarkdown({
    symbol: 'BTC', symbolLabel: 'BTC/USD', tf: '1D', status: 'reviewed',
    lockedAt: Date.UTC(2026, 7, 19, 12, 0),
    compliance: { isPreLocked: true },
    capsule: {
      side: '多', horizon: '3–10天波段', logicType: 'resonance', eventTier: 'S',
      threeLayer: { macro: 'tailwind', crossMarket: 'tailwind', endogenous: 'tailwind' },
      whyNow: '美财政部翻倍国债回购 + 空头 70% 极端拥挤',
      opponentTags: ['拥挤空头轧空/逼空'],
      entryPrice: 69000, stopPrice: 67000, riskPct: 1.5, targetPrice: 78000, plannedRR: 4.5,
      invalidPrice: '跌破 67000', invalidTime: '5 根日线不脱离成本',
    },
    evolution: {
      nodes: [{ barLabel: '2026-08-20', event: '突破 72000', reaction: '顺行', price: 72000 }],
      falsified: false, exitPrice: 79000, exitReason: 'target_realized', followedPlan: true,
      metrics: { realizedR: 5, maePct: -0.8, maeR: -0.28, mfePct: 17.4, mfeR: 6, retention: 83.3 },
    },
    postmortem: {
      realMainline: '财政流动性转向轧空', alignment: '完全吻合', logicScore: 5, chartScore: 4, quadrant: 'rr',
    },
    iteration: { keepAction: '按箱体上沿控损', fixRule: '政策落地前分批兑现' },
  });
  assert.match(md, /# 交易复盘卡 \| BTC\/USD \| 2026-08-19/);
  assert.match(md, /三层共振矩阵定性/);
  assert.match(md, /\[x\] \*\*逻辑对 \+ 赚钱\*\*/);
  assert.match(md, /MAE（最大浮亏）.*-0\.8%/);
  assert.match(md, /按箱体上沿控损/);
});
