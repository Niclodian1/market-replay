#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三买持仓修正回测 — 「三买后持有至新趋势一卖」

原文假设: 三买 = 中枢新生确认, 其后的赢法是持有整个新趋势(新中枢→趋势延续),
          直至该趋势以趋势背驰(一卖)终结。
现行回测偏差: 遇任何卖点(含二卖/三卖)即平仓 — 等于在主升段中继下车,
          系统性低估三买。

A/B:
  base : 三买入场, 一/二/三卖任一确认收盘离场 (+买点最低价止损)
  hold : 三买入场, 仅一卖确认收盘离场 (+同样的止损)
其余完全一致(入场=confirmedT收盘, 止损=买点最低价, 口径=fixA-enter)。
"""

from datetime import datetime, timezone

import chanlun
from backtest import load_bars, run_backtest, stats

KW = dict()


def iso(e):
    return datetime.fromtimestamp(e, timezone.utc).strftime("%y-%m-%d")


def main():
    lines = []
    print("=" * 118)
    print(f"{'级别':4s} {'口径':8s} | {'base(遇卖即走): n/胜%/均%/复利%/PF/回撤%/持有时长':>46s} | "
          f"{'hold(仅一卖): n/胜%/均%/复利%/PF/回撤%/持有时长':>46s}")
    print("-" * 118)
    for tf in ("1D", "4H", "1H"):
        bars, epochs = load_bars(tf)
        bs, ss, bg, sg, *_ = chanlun._collect_causal_points(bars, epochs, tf, **KW)
        for mode, buys, sells in (("stroke", bs, ss), ("segment", bg, sg)):
            b3 = [b for b in buys if b["k"] == "三买"]
            if not b3:
                continue
            s1 = [s for s in sells if s["k"] == "一卖"]
            t_base = run_backtest(bars, epochs, b3, sells, tf, mode)
            t_hold = run_backtest(bars, epochs, b3, s1, tf, mode)
            sa, sh = stats(t_base), stats(t_hold)

            def fmt(s):
                if s.get("n", 0) == 0:
                    return f"{'无交易':>46s}"
                pf = s["pf"] if s["pf"] != float("inf") else 99
                return (f"{s['n']:4d}/{s['win%']:4.1f}%/{s['avg']:+6.2f}%/"
                        f"{s['total%']:+8.1f}%/{pf:4.1f}/{s['mdd%']:4.1f}%/{s['hold']:5.0f}根")
            line = (f"{tf:4s} {mode:8s} | {fmt(sa):>46s} | {fmt(sh):>46s}")
            print(line, flush=True)
            lines.append(line)

            # 逐笔配对: 同一笔三买两种离场的收益差(仅 stroke, 样本可读时打印)
            if mode == "stroke" and len(b3) <= 130:
                pairs = []
                by_key = {(t["entry_t"], t["type"]): t for t in t_base}
                for th in t_hold:
                    tb = by_key.get((th["entry_t"], th["type"]))
                    if tb:
                        pairs.append((tb, th))
                diff = sorted(pairs, key=lambda p: p[1]["ret"] - p[0]["ret"])
                lines.append(f"  [{tf}] 持仓修正改善最大的3笔: " + "; ".join(
                    f"{iso(p[1]['entry_t'])}: {p[0]['ret']:+.0f}%→{p[1]['ret']:+.0f}%"
                    for p in diff[-3:]))
                lines.append(f"  [{tf}] 持仓修正恶化最大的3笔: " + "; ".join(
                    f"{iso(p[1]['entry_t'])}: {p[0]['ret']:+.0f}%→{p[1]['ret']:+.0f}%"
                    for p in diff[:3]))
    print("=" * 118)
    with open("sanmai_hold_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
