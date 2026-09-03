#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""背离时间语义探针: 核实"明显背离是否在前三根K线内即可确认"。

对每个 一买/一卖(笔中枢, 三档):
  - 极值bar/价格、A段与C段的原始bar区间与长度
  - A段/C段面积、比率(该比率在极值bar收盘即定型)
  - 极值bar 及其后3根bar 的 MACD 柱(看动能是否立即衰竭)
  - 当前管线"结构确认"滞后: 从极值到回弹顶分型/回落底分型(下一反向笔端点)
    之间的原始bar数 —— 即当前实现比"背离本身可判定"晚多久
"""
import pandas as pd
from datetime import datetime, timezone

from chanlun import DATA_FILES, macd_series, _areas, build_pivots, build_strokes, find_buy_points, find_sell_points
from fractals import (enforce_alternation, find_fractals, localize_bottom,
                      localize_top, process_inclusions)


def load(tf):
    df = pd.read_parquet(DATA_FILES[tf]).sort_values("time").reset_index(drop=True)
    epochs = [int(x) for x in df["time"].tolist()]
    bars = [{"idx": i, "open": float(r.open), "high": float(r.high),
             "low": float(r.low), "close": float(r.close),
             "volume": float(r.volume)} for i, r in df.iterrows()]
    return bars, epochs


def fmt(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def analyze_tf(tf):
    bars, epochs = load(tf)
    merged = process_inclusions(bars)
    tops, bottoms = find_fractals(merged, bars)
    tops, bottoms = enforce_alternation(merged, bars, tops, bottoms)
    strokes = build_strokes(merged, tops, bottoms)
    _, _, hist = macd_series([b["close"] for b in bars])
    piv = build_pivots(strokes)
    buys = find_buy_points(strokes, piv, hist, merged)
    sells = find_sell_points(strokes, piv, hist, merged)
    _, green_area, red_area = _areas(strokes, piv, hist, merged)
    return bars, epochs, merged, strokes, hist, buys, sells, green_area, red_area


def one_signal(pt, strokes, merged, bars, epochs, hist, area_fn, localize, is_sell):
    C = strokes[pt["c_idx"]]
    A = strokes[pt["a_idx"]]
    a_area = area_fn(A)
    c_area = area_fn(C)
    ratio = c_area / a_area if a_area > 0 else float("inf")
    c_s, c_e = merged[C["s_idx"]]["start"], merged[C["e_idx"]]["end"]
    a_s, a_e = merged[A["s_idx"]]["start"], merged[A["e_idx"]]["end"]
    ext = localize(C["e_idx"], merged, bars)[0]          # 极值原始bar
    # 结构确认: C段之后的反向笔端点(= 回弹顶/回落底分型)
    lag = None
    if pt["c_idx"] + 1 < len(strokes) and strokes[pt["c_idx"] + 1]["dir"] == (-1 if is_sell else 1):
        lag = localize(strokes[pt["c_idx"] + 1]["e_idx"], merged, bars)[0] - ext
    hist_ext = [round(hist[ext + k], 0) if ext + k < len(hist) else None for k in range(4)]
    return {
        "date": fmt(epochs[ext]), "p": round(pt["price"], 0),
        "A_len": a_e - a_s + 1, "C_len": c_e - c_s + 1,
        "A_area": round(a_area, 0), "C_area": round(c_area, 0),
        "ratio": round(ratio, 2), "hist@ext+0..3": hist_ext,
        "lag_bars": lag,
    }


def main():
    for tf in ("1D", "4H", "1H"):
        (bars, epochs, merged, strokes, hist,
         buys, sells, green_area, red_area) = analyze_tf(tf)
        rows = []
        for b in buys:
            if b["type"] == 1:
                rows.append(one_signal(b, strokes, merged, bars, epochs, hist,
                                       green_area, localize_bottom, False))
        for s in sells:
            if s["type"] == 1:
                rows.append(one_signal(s, strokes, merged, bars, epochs, hist,
                                       red_area, localize_top, True))
        n = len(rows)
        if not n:
            print(f"=== {tf}: 无一买/一卖 ===")
            continue
        ratios = sorted(r["ratio"] for r in rows)
        obvious = sum(1 for r in rows if r["ratio"] < 0.7)
        lags = [r["lag_bars"] for r in rows if r["lag_bars"] is not None]
        c_lens = [r["C_len"] for r in rows]
        print(f"=== {tf}: 一买+一卖 {n} 个 ===")
        print(f"  比率 中位 {ratios[n//2]:.2f}  min {ratios[0]:.2f}  max {ratios[-1]:.2f}  "
              f"明显(<0.7) {obvious}/{n}")
        if lags:
            lags_s = sorted(lags)
            print(f"  C段长度 中位 {sorted(c_lens)[n//2]} bars (范围 {min(c_lens)}~{max(c_lens)})")
            print(f"  结构确认滞后 中位 {lags_s[len(lags_s)//2]} bars "
                  f"(min {lags_s[0]}, max {lags_s[-1]})  无回弹笔(末端信号) {len(rows)-len(lags)}")
        else:
            print("  全部为末端信号(无结构滞后数据)")
        # 示例: 最新3个(时间最新)
        print("  最新3例:")
        for r in rows[-3:]:
            print(f"    {r['date']} p={r['p']}  A={r['A_len']}bars/{r['A_area']}  "
                  f"C={r['C_len']}bars/{r['C_area']}  比={r['ratio']}  滞后={r['lag_bars']}bars  "
                  f"MACD@极值+0..3={r['hist@ext+0..3']}")
        print()


if __name__ == "__main__":
    main()
