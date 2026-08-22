"""
真实数据回测 —— BTC/ETH/AAPL/GOOGL × 1day/4h × 三类策略组合。
"""
import asyncio, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.providers.local_crypto import LocalCryptoProvider
from app.providers.yahoo_finance import YahooFinanceProvider
from app.core.strategy_backtest_engine import StrategyBacktester, AccountConfig

REGIME_LABELS = {
    0: "震荡低波", 1: "震荡高波", 2: "弱趋势低波",
    3: "弱趋势高波", 4: "强趋势低波", 5: "强趋势高波",
}

SYMBOLS_TF_SRC = [
    ("BTC-USD", "1day", "crypto"),
    ("BTC-USD", "4h", "crypto"),
    ("ETH-USD", "1day", "crypto"),
    ("ETH-USD", "4h", "crypto"),
    ("AAPL", "1day", "stock"),
    ("AAPL", "4h", "stock"),
    ("GOOGL", "1day", "stock"),
    ("GOOGL", "4h", "stock"),
]


def load_data(symbol, tf, source):
    if source == "crypto":
        candles = asyncio.run(LocalCryptoProvider().get_full_history(symbol, tf))
    else:
        candles = asyncio.run(YahooFinanceProvider().get_time_series(symbol, tf, outputsize=5000))
    # crypto 4h 取最近一年
    if tf == "4h" and len(candles) > 2500:
        candles = candles[-2500:]
    return candles


def pnl_str(v):
    return f"{'+' if v > 0 else ''}{v:.1f}%"


def main():
    account = AccountConfig(initial_capital=1000.0)
    tester = StrategyBacktester(account)

    print(f"{'='*90}")
    print(f"  策略组合回测 · 趋势+动能+反转 · ${account.initial_capital:.0f}本金 {account.max_leverage}×杠杆")
    print(f"{'='*90}")

    all_reports = []

    for symbol, tf, source in SYMBOLS_TF_SRC:
        t0 = time.time()

        try:
            candles = load_data(symbol, tf, source)
        except Exception as e:
            print(f"\n── {symbol} · {tf} ──  ⚠ 数据加载失败: {e}")
            continue

        if len(candles) < 150:
            print(f"\n── {symbol} · {tf} ──  ⚠ 数据不足 ({len(candles)}根)")
            continue

        start = time.strftime("%Y-%m-%d", time.gmtime(candles[0]["time"]))
        end = time.strftime("%Y-%m-%d", time.gmtime(candles[-1]["time"]))

        try:
            report = tester.run(candles, symbol, tf)
        except Exception as e:
            print(f"\n── {symbol} · {tf} ──  ⚠ 回测异常: {e}")
            continue

        elapsed = time.time() - t0
        all_reports.append((symbol, tf, report))

        # ── 总览 ──
        print(f"\n{'─'*90}")
        print(f"  {symbol} · {tf}  |  {len(candles)}根 {start}→{end}  |  {elapsed:.1f}s")
        print(f"  总收益 {pnl_str(report.total_return_pct):>8s}  "
              f"回撤 {report.max_drawdown_pct:>5.1f}%  "
              f"夏普 {report.sharpe_ratio:>5.2f}  "
              f"胜率 {report.win_rate_pct:>5.1f}%  "
              f"交易 {report.total_trades}笔")

        # ── 按策略 ──
        has_data = False
        for stype in ["trend", "momentum", "reversal"]:
            stats = report.by_strategy.get(stype)
            if stats and stats["trades"] > 0:
                if not has_data:
                    print(f"  {'─'*60}")
                    print(f"  {'策略':>10}  {'笔数':>5}  {'胜率':>6}  {'均盈':>8}  {'总盈亏':>10}")
                    has_data = True
                print(f"  {stype:>10}  {stats['trades']:>5}  {stats['win_rate']:>5.1f}%  "
                      f"{stats['avg_pnl_pct']:>+7.2f}%  ${stats['total_pnl']:>+9.1f}")

        # ── 按 Regime ──
        if report.by_regime:
            print(f"  {'─'*60}")
            print(f"  {'Regime':>12}  {'笔数':>5}  {'胜率':>6}  {'均盈':>8}")
            for rid in sorted(report.by_regime.keys()):
                s = report.by_regime[rid]
                label = REGIME_LABELS.get(rid, f"R{rid}")
                print(f"  {label:>12}  {s['trades']:>5}  {s['win_rate']:>5.1f}%  "
                      f"{s['avg_pnl_pct']:>+7.2f}%")

    # ── 跨品种汇总 ──
    print(f"\n{'='*90}")
    print(f"  跨品种汇总")
    print(f"{'='*90}")
    print(f"  {'品种·周期':<20} {'收益':>8} {'回撤':>6} {'夏普':>6} {'胜率':>6} {'交易':>5} | "
          f"{'趋势':>12} {'动能':>12} {'反转':>12}")
    print(f"  {'─'*20} {'─'*8} {'─'*6} {'─'*6} {'─'*6} {'─'*5} | {'─'*12} {'─'*12} {'─'*12}")

    for symbol, tf, r in all_reports:
        t_s = r.by_strategy.get("trend", {})
        m_s = r.by_strategy.get("momentum", {})
        r_s = r.by_strategy.get("reversal", {})
        t_pnl = t_s.get("total_pnl", 0) if t_s else 0
        m_pnl = m_s.get("total_pnl", 0) if m_s else 0
        r_pnl = r_s.get("total_pnl", 0) if r_s else 0
        print(f"  {symbol+' · '+tf:<20} {pnl_str(r.total_return_pct):>8s} "
              f"{r.max_drawdown_pct:>5.1f}% {r.sharpe_ratio:>5.2f} "
              f"{r.win_rate_pct:>5.1f}% {r.total_trades:>5} | "
              f"${t_pnl:>+10.1f} ${m_pnl:>+10.1f} ${r_pnl:>+10.1f}")

    print(f"\n{'='*90}")
    print("  完成")

if __name__ == "__main__":
    main()
