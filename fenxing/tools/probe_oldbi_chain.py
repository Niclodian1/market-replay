#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针: 老笔 vs 新笔 — 两口径下 线段/中枢/买卖点 全链差异量化。

对每个周期:
  新笔链: build_strokes(old=False) → build_segments → build_pivots → 买卖点
  老笔链: build_strokes(old=True)  → build_segments → build_pivots → 买卖点
比较各层对象数量与端点差异。
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # fenxing/ (chanlun.py/backtest.py)

import pandas as pd
from pathlib import Path

import chanlun

_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "crypto"
DATA_FILES = {
    "1D": _DATA_DIR / "BTC_USD_1day.parquet",
    "4H": _DATA_DIR / "BTC_USD_4h.parquet",
    "1H": _DATA_DIR / "BTC_USD_1h.parquet",
}


def load(tf):
    df = pd.read_parquet(DATA_FILES[tf]).sort_values("time").reset_index(drop=True)
    epochs = [int(x) for x in df["time"].tolist()]
    bars = []
    for idx, row in df.iterrows():
        bars.append({
            "idx": idx,
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]),
        })
    return bars, epochs


def chain(bars, epochs, old):
    merged = chanlun.process_inclusions(bars)
    tops, bottoms = chanlun.find_fractals(merged, bars)
    tops, bottoms = chanlun.enforce_alternation(merged, bars, tops, bottoms)
    strokes = chanlun.build_strokes(merged, tops, bottoms, old=old)
    segments = chanlun.build_segments(strokes)
    complete = segments[:-1] if segments else []
    piv_s = chanlun.build_pivots(strokes)
    piv_g = chanlun.build_pivots(complete)
    closes = [b["close"] for b in bars]
    _, _, hist = chanlun.macd_series(closes)
    buys_s = chanlun.find_buy_points(strokes, piv_s, hist, merged)
    sells_s = chanlun.find_sell_points(strokes, piv_s, hist, merged)
    buys_g = chanlun.find_buy_points(complete, piv_g, hist, merged)
    sells_g = chanlun.find_sell_points(complete, piv_g, hist, merged)
    return dict(
        strokes=strokes, segments=complete, piv_s=piv_s, piv_g=piv_g,
        buys_s=buys_s, sells_s=sells_s, buys_g=buys_g, sells_g=sells_g,
        merged=merged, epochs=epochs,
    )


def key_of_levels(levels):
    """levels(笔或线段)的端点签名列表。"""
    return [(lv["dir"], lv["s_idx"], lv["e_idx"]) for lv in levels]


def key_of_pivots(pivots):
    return [(p["level_start"], p["level_end"]) for p in pivots]


def key_of_points(pts):
    return sorted((p["type"], p["level_idx"]) for p in pts)


def fmt_pt_epochs(pts, levels, merged, epochs):
    out = []
    for t, li in key_of_points(pts):
        lv = levels[li]
        out.append((t, epochs[merged[lv["e_idx"]]["start"]]))
    return out


def main():
    for tf in ("1D", "4H", "1H"):
        bars, epochs = load(tf)
        new = chain(bars, epochs, old=False)
        old = chain(bars, epochs, old=True)

        ks_new, ks_old = key_of_levels(new["strokes"]), key_of_levels(old["strokes"])
        common = min(len(ks_new), len(ks_old))
        div = next((i for i in range(common) if ks_new[i] != ks_old[i]), None)

        print("=" * 76)
        print("[%s] 笔: 新笔 %d · 老笔 %d" % (tf, len(ks_new), len(ks_old)))
        if div is None:
            print("  笔序列: 完全一致" if len(ks_new) == len(ks_old) else
                  "  笔序列: 前缀一致, 尾部老笔多 %d 笔" % (len(ks_old) - len(ks_new)))
        else:
            print("  笔序列: 第 %d 个端点开始分叉" % div)
            for tag, ks, ch in (("新", ks_new, new), ("老", ks_old, old)):
                d0, e0 = ks[div][1], ks[div][2]
                print("    %s笔[%d]: dir=%d %s ~ %s" % (
                    tag, div, ks[div][0],
                    ch["epochs"][ch["merged"][d0]["start"]],
                    ch["epochs"][ch["merged"][e0]["end"]]))

        seg_n, seg_o = key_of_levels(new["segments"]), key_of_levels(old["segments"])
        same_seg = seg_n == seg_o
        print("  线段(完整): 新笔口径 %d 段 · 老笔口径 %d 段 · %s" % (
            len(seg_n), len(seg_o), "一致" if same_seg else "不一致"))
        if not same_seg:
            common2 = min(len(seg_n), len(seg_o))
            d2 = next((i for i in range(common2) if seg_n[i] != seg_o[i]), None)
            if d2 is not None:
                print("    第 %d 段开始分叉:" % d2)
                for tag, sg, ch in (("新", seg_n, new), ("老", seg_o, old)):
                    d0, e0 = sg[d2][1], sg[d2][2]
                    print("      %s: dir=%d %s ~ %s" % (
                        tag, sg[d2][0],
                        ch["epochs"][ch["merged"][d0]["start"]],
                        ch["epochs"][ch["merged"][e0]["end"]]))

        pv_n, pv_o = key_of_pivots(new["piv_s"]), key_of_pivots(old["piv_s"])
        print("  笔中枢: 新 %d · 老 %d · %s" % (
            len(pv_n), len(pv_o), "一致" if pv_n == pv_o else "不一致"))
        pg_n, pg_o = key_of_pivots(new["piv_g"]), key_of_pivots(old["piv_g"])
        print("  线段中枢: 新 %d · 老 %d · %s" % (
            len(pg_n), len(pg_o), "一致" if pg_n == pg_o else "不一致"))

        for name, nk, ok_, nl, ol in (
                ("线段级买点", key_of_points(new["buys_g"]), key_of_points(old["buys_g"]),
                 new["buys_g"], old["buys_g"]),
                ("线段级卖点", key_of_points(new["sells_g"]), key_of_points(old["sells_g"]),
                 new["sells_g"], old["sells_g"])):
            print("  %s: 新 %d 个 %s · 老 %d 个 %s · %s" % (
                name, len(nk), fmt_pt_epochs(nl, new["segments"], new["merged"], new["epochs"]),
                len(ok_), fmt_pt_epochs(ol, old["segments"], old["merged"], old["epochs"]),
                "一致" if nk == ok_ else "不一致"))

    print("=" * 76)


if __name__ == "__main__":
    main()
