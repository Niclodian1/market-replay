#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多级别联动买点回测 — 「大级别买点须由次级别一买引发」

原文依据:
  · 第43课: 任何大级别的转折, 都是由小级别的第一类买卖点引发的。
    → 大级别买点若找不到次级别趋势背驰(一买)支撑, 可靠性存疑。
  · 第27课 区间套: 大级别背驰点内部可逐级下钻找到次级别背驰, 精确定位拐点。

联动判定(「同一个底」):
  大级别买点端点时间 b.t 与 次级别一买端点时间 s.t 相差 <= 2 根大级别K线。
  例: 4H 二买的回调低点, 在 1H 图上恰好是一次下跌趋势背驰的最低点。

口径: 两级均用 fixA-enter(上轮回测已验证更优且更忠实原文)。
回测规则与 backtest.py 完全一致(confirmedT 收盘入场 / 买点最低价止损 /
大级别自身卖点平仓 / 只做多), 唯一差别是只允许「联动买点」开仓。
对照组: 同级别全部同类买点(不做次级别过滤)。
"""

from collections import defaultdict

import chanlun
from backtest import load_bars, run_backtest, stats

PAIRS = [("1D", "4H"), ("4H", "1H")]
BAR_SEC = {"1D": 86400, "4H": 14400, "1H": 3600}
KW = dict()


def collect(tf):
    bars, epochs = load_bars(tf)
    sig = chanlun._collect_causal_points(bars, epochs, tf, **KW)
    return bars, epochs, sig


def main():
    cache = {}
    for tf in ("1D", "4H", "1H"):
        cache[tf] = collect(tf)

    rows = []  # (big, mode, sub_mode, type, n_all, n_link, ...)
    print("=" * 112)
    print("联动率: 大级别买点端点 ±3根大级别K线内存在次级别一买端点 (与 chanlun.LINK_WINDOW 一致)")
    print("-" * 112)
    header = f"{'大级':4s} {'口径':7s} {'次级':7s} | " + " ".join(
        f"{ty}(总→联动)" for ty in ("一买", "二买", "三买"))
    print(header)

    results = []  # 明细交易供效果统计
    for big, sub in PAIRS:
        bars_b, ep_b, (bs, ss, bg, sg, _, _) = cache[big]
        _, _, (bsub_s, _, bsub_g, _, _, bsub_pb) = cache[sub]
        tol = 3 * BAR_SEC[big]  # 与 chanlun.LINK_WINDOW (1D:72h, 4H:24h = ±3根) 一致
        for mode, buys, sells in (("stroke", bs, ss), ("segment", bg, sg)):
            # 对照组: 全部买点回测
            trades_all = run_backtest(bars_b, ep_b, buys, sells, big, mode)
            for sub_mode, sub_b1 in (
                    ("stroke", [p for p in bsub_s if p["k"] == "一买"]),
                    ("segment", [p for p in bsub_g if p["k"] == "一买"])):
                linked = [b for b in buys
                          if any(abs(b["t"] - s["t"]) <= tol for s in sub_b1)]
                # 计数行
                cnt = []
                for ty in ("一买", "二买", "三买"):
                    tot = [b for b in buys if b["k"] == ty]
                    lk = [b for b in linked if b["k"] == ty]
                    cnt.append(f"{len(tot)}→{len(lk)}")
                print(f"{big:4s} {mode:7s} {sub_mode:7s} | " + " ".join(cnt))
                # 回测联动子集
                trades_lk = run_backtest(bars_b, ep_b, linked, sells, big, mode)
                rest = [b for b in buys if b not in linked]
                trades_no = run_backtest(bars_b, ep_b, rest, sells, big, mode)
                results.append((big, mode, sub_mode, trades_all, trades_lk, trades_no))

    print("=" * 112)
    print("\n效果对比 (入场=大级别买点, 止损=买点最低价, 离场=大级别卖点; 复利%)")
    print("=" * 130)
    print(f"{'大级':4s} {'口径':7s} {'次级':7s} | "
          f"{'全部: n/胜%/均%/复利%':>28s} | {'联动: n/胜%/均%/复利%':>28s} | "
          f"{'非联动: n/均%':>16s}")
    print("-" * 130)

    agg = defaultdict(lambda: {"lk": [], "no": [], "all": []})
    for big, mode, sub_mode, t_all, t_lk, t_no in results:
        sa, sl, sn = stats(t_all), stats(t_lk), stats(t_no)

        def fmt(s):
            if s.get("n", 0) == 0:
                return f"{'—':>28s}"
            return (f"{s['n']:3d}/{s['win%']:4.1f}/{s['avg']:+5.2f}/"
                    f"{s['total%']:+8.1f}")
        print(f"{big:4s} {mode:7s} {sub_mode:7s} | {fmt(sa):>28s} | "
              f"{fmt(sl):>28s} | {sn.get('n',0):3d}/{sn.get('avg',0):+.2f}%")
        key = (big, mode)
        agg[key]["lk"].extend(t_lk)
        agg[key]["no"].extend(t_no)
        agg[key]["all"].extend(t_all)

    # 汇总: 每个大级别×口径, 联动 vs 非联动(合并两种次级别口径去重后近似)
    print("=" * 130)
    print("\n汇总(同一大级别买点被两种次级别口径同时命中只计一次)")
    print("-" * 96)
    print(f"{'大级':4s} {'口径':7s} | {'全部: n/胜%/均%/复利%':>30s} | "
          f"{'联动: n/胜%/均%/复利%':>30s} | {'提升(pct)':>9s}")
    print("-" * 96)
    for (big, mode), d in sorted(agg.items()):
        # 去重: 按 entry_t+type 合并两种 sub 口径的联动集
        seen, lk_uni = set(), []
        for t in d["lk"]:
            k = (t["entry_t"], t["type"])
            if k not in seen:
                seen.add(k)
                lk_uni.append(t)
        no_uni = [t for t in d["all"]
                  if (t["entry_t"], t["type"]) not in seen]
        sa, sl, sn = stats(d["all"]), stats(lk_uni), stats(no_uni)

        def fmt(s):
            if s.get("n", 0) == 0:
                return f"{'—':>30s}"
            return (f"{s['n']:3d}/{s['win%']:4.1f}/{s['avg']:+5.2f}/"
                    f"{s['total%']:+9.1f}")
        lift = ""
        if sl.get("n") and sa.get("n"):
            lift = f"{sl['avg']-sa['avg']:+.2f}"
        print(f"{big:4s} {mode:7s} | {fmt(sa):>30s} | {fmt(sl):>30s} | {lift:>9s}")
    print("=" * 96)


if __name__ == "__main__":
    main()
