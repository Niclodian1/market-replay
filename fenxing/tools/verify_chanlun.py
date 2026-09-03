#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论多级别全链路结构验证 + BTC 历史 checkpoint + 次级别联动断言。

结构断言(对 1D/4H/1H 三档):
  笔: 方向严格交替、相邻端点连续(索引与价格)、间距符合新笔标准。
  线段: 方向交替、笔数为奇数、每段 ≥3 笔(末段除外)、端点连续。
  中枢: ZD < ZG、DD ≤ ZD、GG ≥ ZG、区间合法。
  买卖点: 一买/二买/三买 与 一卖/二卖/三卖 落于真实极值 bar。

历史 checkpoint(1D):
  2018-12 底部(~3222) 与 2022-11 底部(~15476) 应出现一买(笔中枢)。
  2021-11 顶部(~69000) 与 2025-10 顶部(~126200) 应出现一卖(笔中枢)。

分型歧义复验: 2025-04-07(崩溃低点 74508)应已被收盘确认 → 成为底分型。

联动断言: 每个买卖点含 sub(次级别确认)/upper(上级别语境)字段;
  1D 应至少若干点命中"次级别✓"。
"""

import pandas as pd

from chanlun import (
    DATA_FILES, analyze, build_pivots, build_segments, build_strokes,
    find_buy_points, find_sell_points, link_levels, macd_series,
)
from fractals import (
    enforce_alternation, find_fractals, localize_bottom, localize_top,
    process_inclusions,
)

DATE = __import__("datetime").date


def load(tf):
    df = pd.read_parquet(DATA_FILES[tf]).sort_values("time").reset_index(drop=True)
    epochs = [int(x) for x in df["time"].tolist()]
    bars = [
        {
            "idx": i,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
        }
        for i, r in df.iterrows()
    ]
    return bars, epochs


def main():
    errors = []

    def check(cond, msg):
        if not cond:
            errors.append(msg)

    def run_pipeline(tf):
        bars, epochs = load(tf)
        merged = process_inclusions(bars)
        tops, bottoms = find_fractals(merged, bars)
        tops, bottoms = enforce_alternation(merged, bars, tops, bottoms)
        strokes = build_strokes(merged, tops, bottoms)
        segments = build_segments(strokes)
        complete = segments[:-1] if segments else []
        _, _, hist = macd_series([b["close"] for b in bars])
        piv_s = build_pivots(strokes)
        piv_g = build_pivots(complete)
        buys_s = find_buy_points(strokes, piv_s, hist, merged)
        sells_s = find_sell_points(strokes, piv_s, hist, merged)
        buys_g = find_buy_points(complete, piv_g, hist, merged)
        sells_g = find_sell_points(complete, piv_g, hist, merged)
        return dict(merged=merged, tops=tops, bottoms=bottoms, strokes=strokes,
                    segments=segments, piv_s=piv_s, piv_g=piv_g,
                    buys_s=buys_s, sells_s=sells_s, buys_g=buys_g, sells_g=sells_g)

    pipelines = {tf: run_pipeline(tf) for tf in ("1D", "4H", "1H")}

    # ---------- 结构断言(三档) ----------
    for tf, P in pipelines.items():
        merged, strokes, segments = P["merged"], P["strokes"], P["segments"]
        for i, st in enumerate(strokes):
            check(st["dir"] in (1, -1), f"{tf} 笔{i} 方向非法")
            check(st["s_idx"] < st["e_idx"], f"{tf} 笔{i} 起止索引倒置")
            if i > 0:
                check(strokes[i - 1]["dir"] != st["dir"], f"{tf} 笔{i-1}/{i} 方向未交替")
                check(strokes[i - 1]["e_idx"] == st["s_idx"], f"{tf} 笔{i-1}/{i} 端点不连续")
                check(abs(strokes[i - 1]["e_price"] - st["s_price"]) < 1,
                      f"{tf} 笔{i-1}/{i} 端点价格不连续")
        for i, sg in enumerate(segments):
            check(sg["dir"] in (1, -1), f"{tf} 线段{i} 方向非法")
            if sg["complete"]:
                check(sg["n_strokes"] % 2 == 1, f"{tf} 线段{i} 笔数非奇数")
                check(sg["n_strokes"] >= 3, f"{tf} 线段{i} 笔数不足")
            if i > 0:
                check(segments[i - 1]["dir"] != sg["dir"], f"{tf} 线段{i-1}/{i} 方向未交替")
                check(segments[i - 1]["e_idx"] == sg["s_idx"], f"{tf} 线段{i-1}/{i} 端点不连续")
        for p in P["piv_s"] + P["piv_g"]:
            check(p["zd"] < p["zg"], f"{tf} 中枢 ZD>=ZG")
            check(p["dd"] <= p["zd"], f"{tf} 中枢 DD>ZD")
            check(p["gg"] >= p["zg"], f"{tf} 中枢 GG<ZG")

    # ---------- 买卖点落于真实极值(笔中枢, 三档) ----------
    for tf, P in pipelines.items():
        bars = load(tf)[0]
        merged, strokes = P["merged"], P["strokes"]
        for b in P["buys_s"]:
            lvl = strokes[b["level_idx"]]
            raw, px = localize_bottom(lvl["e_idx"], merged, bars)
            check(lvl["dir"] == -1, f"{tf} 买点{list(b.keys())} 所在笔非向下")
            check(abs(px - b["price"]) < 1, f"{tf} 买点 price {b['price']} 未落于真实极值 {px}")
        for s in P["sells_s"]:
            lvl = strokes[s["level_idx"]]
            raw, px = localize_top(lvl["e_idx"], merged, bars)
            check(lvl["dir"] == 1, f"{tf} 卖点 所在笔非向上")
            check(abs(px - s["price"]) < 1, f"{tf} 卖点 price {s['price']} 未落于真实极值 {px}")

    # ---------- 分型歧义复验: 2025-04-07 崩溃低点应成为底分型 ----------
    P1 = pipelines["1D"]
    bars = load("1D")[0]
    bottoms_apr = []
    for mi in P1["bottoms"]:
        raw, px = localize_bottom(mi, P1["merged"], bars)
        d = __import__("datetime").datetime.fromtimestamp(load("1D")[1][raw], __import__("datetime").timezone.utc).strftime("%Y-%m-%d")
        if d in ("2025-04-06", "2025-04-07", "2025-04-08"):
            bottoms_apr.append((d, px))
    check(any(d == "2025-04-07" and abs(px - 74508) < 1 for d, px in bottoms_apr),
          f"分型歧义未修复: 2025-04-07 崩溃低点未成为底分型 (got {bottoms_apr})")

    # ---------- 历史 checkpoint(1D, 笔中枢) ----------
    def b1s():
        return [(localize_bottom(P1["strokes"][b["level_idx"]]["e_idx"], P1["merged"], bars)[0], b["price"])
                for b in P1["buys_s"] if b["type"] == 1]
    def s1s():
        return [(localize_top(P1["strokes"][s["level_idx"]]["e_idx"], P1["merged"], bars)[0], s["price"])
                for s in P1["sells_s"] if s["type"] == 1]
    epochs = load("1D")[1]
    near = lambda raw, d0, days=45: abs(DATE.fromisoformat(
        __import__("datetime").datetime.fromtimestamp(epochs[raw], __import__("datetime").timezone.utc).strftime("%Y-%m-%d"))
        - DATE.fromisoformat(d0)).days <= days
    check(any(near(r, "2018-12-01") for r, _ in b1s()), "2018-12 底部附近未找到一买")
    check(any(near(r, "2022-11-01") for r, _ in b1s()), "2022-11 底部附近未找到一买")
    check(any(near(r, "2018-06-13", 10) for r, _ in b1s()), "2018-06 中间低点一买被未来中枢删掉了")
    check(any(near(r, "2019-12-18", 10) for r, _ in b1s()), "2019-12 中间低点一买被未来中枢删掉了")
    check(any(near(r, "2021-11-01") and p > 60000 for r, p in s1s()), "2021-11 顶部附近未找到一卖")
    check(any(near(r, "2025-10-01") and p > 100000 for r, p in s1s()), "2025-10 顶部附近未找到一卖")
    check(any(near(r, "2024-12-17", 10) and p > 100000 for r, p in s1s()), "2024-12 中间高点一卖被未来中枢删掉了")

    # ---------- 联动断言: analyze + link_levels ----------
    levels_out = {}
    for tf in ("1D", "4H", "1H"):
        bars_t, epochs_t = load(tf)
        levels_out[tf] = analyze(bars_t, epochs_t, tf)
    link_levels(levels_out)
    for tf in ("1D", "4H", "1H"):
        L = levels_out[tf]
        for key in ("buyPointsStrokeRaw", "sellPointsStrokeRaw",
                    "buyPointsSegmentRaw", "sellPointsSegmentRaw"):
            pts = L[key]
            for p in pts:
                check(isinstance(p.get("sub"), str) and p["sub"], f"{tf} {key} 点缺 sub")
                up = p.get("upper", "")
                check(up in ("—", "") or up.startswith("位于"),
                      f"{tf} {key} 点缺 upper: {up!r}")
                # 末端状态 + 最早可确认时间(实盘安全前提)
                check(isinstance(p.get("confirmedT"), int) and p["confirmedT"] >= p["t"],
                      f"{tf} {key} 点缺 confirmedT 或早于锚点")
                check(p.get("state") in ("confirmed", "forming"),
                      f"{tf} {key} 点 state 非法: {p.get('state')!r}")
                if p["k"] in ("一买", "一卖"):
                    check(p.get("tier") in ("明显", "临界"),
                          f"{tf} {key} 一买/一卖缺 tier: {p.get('tier')!r}")
        check("edge" in L["meta"] and L["meta"]["edge"].get("pending"),
              f"{tf} meta 缺 edge 末端摘要")
        # ---- 抢先背驰信号: 告警 → 背驰买/卖点 ----
        for a in L.get("beichiAlertsRaw", []):
            check(a["k"] == "告警" and a["side"] in ("买", "卖"),
                  f"{tf} 告警对象非法: {a}")
        for f in L.get("beichiPointsRaw", []):
            check(f["k"] in ("背驰买", "背驰卖") and f["side"] in ("买", "卖"),
                  f"{tf} 背驰点 k/side 非法")
            check(isinstance(f.get("confirmedT"), int) and isinstance(f.get("alertT"), int),
                  f"{tf} 背驰点缺 confirmedT/alertT")
            check(f["alertT"] <= f["t"], f"{tf} 告警晚于入场(alertT>t): {f.get('t')}")
            check(f.get("trigger") in ("底分型", "MACD柱缩短", "弱速率"),
                  f"{tf} 背驰点 trigger 非法: {f.get('trigger')!r}")
            check(isinstance(f.get("ratio"), (int, float)) and f["ratio"] <= 0.9,
                  f"{tf} 背驰点 ratio 应≤0.9(整笔完成): {f.get('ratio')}")
            if f["side"] == "买":
                check(f["k"] == "背驰买", f"{tf} 买侧应为背驰买: {f['k']}")
            else:
                check(f["k"] == "背驰卖", f"{tf} 卖侧应为背驰卖: {f['k']}")
        check(L["meta"].get("nBeichiAlert") == len(L["beichiAlertsRaw"]),
              f"{tf} nBeichiAlert 计数不符")
        if len(L.get("beichiPointsRaw", [])):
            check(L["meta"].get("nBeichiBuy") + L["meta"].get("nBeichiSell")
                  == len(L["beichiPointsRaw"]), f"{tf} 背驰点计数不符")
    n1d_hit = sum(1 for p in (levels_out["1D"]["buyPointsStrokeRaw"] + levels_out["1D"]["sellPointsStrokeRaw"])
                  if "✓" in p["sub"])
    check(n1d_hit >= 3, f"1D 次级别确认命中率过低 (仅 {n1d_hit} 个✓)")

    # ---------- 汇总 ----------
    print("=" * 78)
    for tf in ("1D", "4H", "1H"):
        m = levels_out[tf]["meta"]
        print(f"  {tf}: 笔{m['nStrokes']} 线段{m['nSegments']} 中枢{m['nPivotsStroke']}/{m['nPivotsSegment']}"
              f" 买点(笔){m['nBuy1S']}/{m['nBuy2S']}/{m['nBuy3S']}(一二三) "
              f"卖点(笔){m['nSell1S']}/{m['nSell2S']}/{m['nSell3S']}(一二三) "
              f"抢先(告警/买/卖){m['nBeichiAlert']}/{m['nBeichiBuy']}/{m['nBeichiSell']}")
    b1all = [(epochs[r], p) for r, p in b1s()]
    s1all = [(epochs[r], p) for r, p in s1s()]
    def fmt(ts):
        return __import__("datetime").datetime.fromtimestamp(ts, __import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    print("  1D 一买:", "  ".join(f"{fmt(ts)}@{p:.0f}" for ts, p in b1all))
    print("  1D 一卖:", "  ".join(f"{fmt(ts)}@{p:.0f}" for ts, p in s1all))
    print(f"  1D 次级别确认命中: {n1d_hit} 点 ✓")
    # 末端状态(实盘安全): 每个周期的 meta.edge
    print("  末端状态:")
    for tf in ("1D", "4H", "1H"):
        e = levels_out[tf]["meta"].get("edge", {})
        print(f"    {tf}: {e.get('lastBar', '—')} | {e.get('lastFractal', '—')} | "
              f"{e.get('lastStroke', '—')} | {e.get('pending', '—')}")
        lv = e.get("live")
        if lv:
            print(f"      抢先live: {lv.get('side')}侧[{lv.get('from')}~{lv.get('to')}] "
                  f"运行{lv.get('run')}/参考{lv.get('ref')} 比{lv.get('ratio')} "
                  f"{lv.get('note') or lv.get('alert') or ''}")
    print("=" * 78)
    if errors:
        print(f"\n[FAIL] {len(errors)} 处断言未通过:")
        for e in errors[:25]:
            print("  -", e)
        return 1
    print("\n[PASS] 全部结构断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
