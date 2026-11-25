import test from 'node:test';
import assert from 'node:assert/strict';
import MC from '../js/23-market-context.js';

const MOCK_CTX = {
  macro: {
    dates: Array.from({ length: 25 }, (_, i) => `2025-01-${String(i + 1).padStart(2, '0')}`),
    us10y: Array.from({ length: 25 }, (_, i) => +(4.0 + i * 0.02).toFixed(2)),
    dxy: Array.from({ length: 25 }, (_, i) => +(105.0 - i * 0.1).toFixed(2)),
    vix: Array.from({ length: 25 }, (_, i) => +(15.0 + i * 0.5).toFixed(2)),
  },
  benchmarks: {
    NDX: {
      d: Array.from({ length: 25 }, (_, i) => `2025-01-${String(i + 1).padStart(2, '0')}`),
      c: Array.from({ length: 25 }, (_, i) => 100 + i), // +20% over 20 bars (104 -> 124)
    },
  },
  earnings: {
    NVDA: [
      ['2025-01-10', 8.5, 0.89, 0.82],
      ['2025-01-24', -4.2, 0.70, 0.73],
    ],
  },
};

test('MarketContext: 品种分层注册表准确区分核心主池(30)与观察池', () => {
  assert.equal(MC.CORE_SYMBOLS.length, 30);
  assert.equal(MC.tierOf('NVDA'), 'S');
  assert.equal(MC.tierOf('BTC'), 'S');
  assert.equal(MC.tierOf('TLT'), 'S');
  assert.equal(MC.tierOf('USDJPY'), 'S');
  assert.equal(MC.tierOf('TSM'), 'S');
  assert.equal(MC.tierOf('HSI'), 'A');
  assert.equal(MC.tierOf('LLY'), 'A');
  assert.equal(MC.tierOf('XOM'), 'A');
  assert.equal(MC.tierOf('FCX'), 'A');
  assert.equal(MC.tierOf('0700_HK'), 'A');
  assert.equal(MC.isCore('NVDA'), true);
  assert.equal(MC.isCore('TLT'), true);
  assert.equal(MC.isCore('600900_SS'), false);
  assert.equal(MC.isCore('PEPE'), false);
});

test('MarketContext: 宏观水位严格因果(不读取未来日期)并正确算 20 日变化', () => {
  const ts12 = Date.UTC(2025, 0, 12) / 1000;
  const m = MC.macroAt(ts12, MOCK_CTX);
  assert.equal(m.date, '2025-01-12');
  assert.equal(m.us10y, 4.22);
  assert.equal(m.d10y, 0.22); // vs index 0 (4.00)
  // 周末/非交易日回退取最近不晚于当天的交易日
  const ts27 = Date.UTC(2025, 0, 27) / 1000;
  const m2 = MC.macroAt(ts27, MOCK_CTX);
  assert.equal(m2.date, '2025-01-25');
});

test('MarketContext: 20日相对强弱 RS 与 52W 周期水位计算准确', () => {
  const bars = Array.from({ length: 25 }, (_, i) => {
    const close = 100 * Math.pow(1.015, i); // 强势跑赢 NDX
    return { time: Date.UTC(2025, 0, i + 1) / 1000, open: close, high: close * 1.01, low: close * 0.99, close };
  });
  const rs = MC.rsAt('NVDA', bars[24].time, bars, 24, MOCK_CTX);
  assert.equal(rs.benchLabel, 'NDX');
  assert.ok(rs.excess20 > 10, '应显著跑赢基准 NDX: ' + rs.excess20);

  const cyc = MC.cycleAt(bars, 24);
  assert.equal(cyc.stage, '新高区');
  assert.ok(cyc.dd52w <= 0 && cyc.dd52w >= -2);
});

test('MarketContext: 财报日严格因果（财报前只预警倒计时，不泄露未公布业绩）', () => {
  const beforeTs = Date.UTC(2025, 0, 21) / 1000; // 距 2025-01-24 财报 3 天
  const eBefore = MC.earningsAt('NVDA', beforeTs, MOCK_CTX);
  assert.equal(eBefore.upcomingWarn, true);
  assert.equal(eBefore.next.daysTo, 3);
  assert.equal(eBefore.last.date, '2025-01-10'); // 绝不提前返回 1/24 财报数字

  const onDayTs = Date.UTC(2025, 0, 24) / 1000;
  const eOn = MC.earningsAt('NVDA', onDayTs, MOCK_CTX);
  assert.equal(eOn.recentReport, true);
  assert.equal(eOn.last.surprise, -4.2);

  // 盘后 (AMC) 财报：当日收盘前绝不提前泄露业绩（仅预警「今晚盘后财报」），次日收盘才公开
  const amcCtx = {
    earnings: {
      NVDA: [
        ['2025-01-10', 8.5, 0.89, 0.82, 'AMC'],
        ['2025-02-26', 12.4, 0.95, 0.85, 'AMC'],
      ],
    },
  };
  const reportDayClose = Date.UTC(2025, 1, 26) / 1000;
  const eSameDay = MC.earningsAt('NVDA', reportDayClose, amcCtx);
  assert.equal(eSameDay.last.date, '2025-01-10');
  assert.equal(eSameDay.next.date, '2025-02-26');
  assert.equal(eSameDay.next.daysTo, 0);
  assert.equal(eSameDay.upcomingWarn, true);
  assert.equal(eSameDay.recentReport, false);

  const nextDayClose = Date.UTC(2025, 1, 27) / 1000;
  const eNextDay = MC.earningsAt('NVDA', nextDayClose, amcCtx);
  assert.equal(eNextDay.last.date, '2025-02-26');
  assert.equal(eNextDay.last.surprise, 12.4);
  assert.equal(eNextDay.recentReport, true);
});
