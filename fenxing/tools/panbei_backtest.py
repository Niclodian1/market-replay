#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘整背驰回测 — TODO P4 / 规范 §8.4

问题: 盘整背驰点作为独立信号的交易价值如何? 与三类买点重叠度多高?
口径: a_mode=enter, pullback_ext=False(与主系统一致)。
对照:
  A. 盘背买独立交易(confirmedT 收盘入场, 端点最低价止损, 一/二/三卖离场)
  B. 盘背买与一买/二买的时间重合率(±3根大级别K线) — 检验"二买的次级别
     对应物是盘整背驰"的联动假设
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # fenxing/ (chanlun.py/backtest.py)

from datetime import datetime, timezone

import chanlun
from backtest import load_bars, run_backtest, stats

KW = dict(a_mode="enter", pullback_ext=False)


def iso(e):
    return datetime.fromtimestamp(e, timezone.utc).strftime("%y-%m-%d %H:%M")


def main():
    lines = []
    print("=" * 110)
    print(f"{'级别':4s} {'口径':7s} | {'盘背买独立: n/胜%/均%/复利%/PF':>36s} | "
          f"{'与一买重合':>10s} {'与二买重合':>10s} {'独立盘背n':>8s}")
    print("-" * 110)
    for tf in ("1D", "4H", "1H"):
        bars, epochs = load_bars(tf)
        bs, ss, bg, sg, _, pb, *_ = chanlun._collect_causal_points(bars, epochs, tf, **KW)
        pb_buy = [p for p in pb if p["k"] == "盘整背驰买"]
        if not pb_buy:
            continue
        # 重合判定: 盘背买端点 ±3根大级别K线内存在一买/二买端点
        b1_t = sorted(p["t"] for p in bs if p["k"] == "一买") + \
               sorted(p["t"] for p in bg if p["k"] == "一买")
        b2_t = sorted(p["t"] for p in bs if p["k"] == "二买") + \
               sorted(p["t"] for p in bg if p["k"] == "二买")
        bar_sec = {"1D": 86400, "4H": 14400, "1H": 3600}[tf]
        tol = 3 * bar_sec
        hit_b1 = sum(1 for p in pb_buy if any(abs(p["t"] - t) <= tol for t in b1_t))
        hit_b2 = sum(1 for p in pb_buy if any(abs(p["t"] - t) <= tol for t in b2_t))
        both = sum(1 for p in pb_buy
                   if any(abs(p["t"] - t) <= tol for t in b1_t)
                   and any(abs(p["t"] - t) <= tol for t in b2_t))
        solo = len(pb_buy) - max(hit_b1, hit_b2)
        # 独立回测: 盘背买入场, 全部卖点离场
        all_sells = ss + sg
        trades = run_backtest(bars, epochs, pb_buy, all_sells, tf, "stroke")
        st = stats(trades)

        def fmt(s):
            if s.get("n", 0) == 0:
                return f"{'无交易':>36s}"
            pf = s["pf"] if s["pf"] != float("inf") else 99
            return (f"{s['n']:4d}/{s['win%']:4.1f}%/{s['avg']:+5.2f}%/"
                    f"{s['total%']:+7.1f}%/{pf:4.2f}")
        line = (f"{tf:4s} {'enter':7s} | {fmt(st):>36s} | "
                f"{hit_b1:>4d}/{len(pb_buy):<3d} {hit_b2:>4d}/{len(pb_buy):<3d} {both:>4d} {solo:>6d}")
        print(line, flush=True)
        lines.append(line)
        # 逐笔样例(前5笔)
        sample = "; ".join(
            f"{iso(tr['entry_t'])}@{tr['entry']:.0f}:{tr['ret']:+.1f}%({tr['reason']})"
            for tr in trades[:5])
        sline = f"  [{tf}] 样例: {sample}"
        print(sline)
        lines.append(sline)
    print("=" * 110)
    with open(Path(__file__).resolve().parents[2] / "fenxing" / "docs" / "reports" / "panbei_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
