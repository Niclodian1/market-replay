#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论顶底分型计算 — Chan-Theory fractal computation for BTC_USD_1day.parquet

流程 (pipeline):
  1. 包含关系处理 (K-line inclusion processing)
     相邻两根K线若一根完全覆盖另一根(高≥高 且 低≤低, 或反之), 即构成"包含关系"。
     此时按已确立的方向合并:
        向上趋势 → 取高高(两高取大)、高低(两低取大);
        向下趋势 → 取低高(两高取小)、低低(两低取小)。
     方向由更早一根已合并K线确立: 后一根高点高于前一根 → 上, 否则 → 下。
     合并会向后持续传播, 直到合并结果不再与更早K线存在包含关系。

  2. 分型识别 (fractal identification, 按原文双向极值 + 收盘确认)
     按缠论原文(第62课): 中间K线须为三根中的双向极值——
        中间K线高点为三者最高 且 低点也为三者最高 → 顶分型
        中间K线低点为三者最低 且 高点也为三者最低 → 底分型
     再叠加用户自定义的收盘确认(确认K线取原始K线, 而非合并K线):
        顶分型需其后一根原始K线收盘跌破中间K线最低价
        底分型需其后一根原始K线收盘升破中间K线最高价
     确认窗口(2026-08 放宽): 合并中K线结束后的 CONFIRM_WINDOW 根原始K线内,
        **任意一根**收盘价反向突破中K线极值即确认。放宽前只看第一根原始K线
        (bars[mid["end"]+1]), 会漏掉崩盘/急涨类结构——如 2024-08-05 崩溃低点
        49000: 其后第一根 08-07 收盘 55134 未升破中K线高 57041, 而 08-08
        收盘 61686 已升破, 故旧规则误拒该底分型。取原始K线而非下一根合并K线
        收盘的理由不变(合并K线收盘是合并区间最后一根原始K线的收盘, 会被包含
        合并遮蔽, 如 2025-04 崩溃低点 04-07 即因此曾被误拒)。

  3. 交替约束 (alternation enforcement)
     合并K线的局部极值天然严格交替(顶-底-顶-底)。收盘确认会剔除未通过
     确认的中间分型, 使输出出现"连续同类"。此时连续同类中仅保留更极端的
     一个, 恢复严格的顶底交替(缠论笔/线段的标准输入)。

  4. 定位 (localization)
     合并K线可能由多根原始K线合并而成。把顶分型标记定位到该合并区间内
     真正最高点所在的原始bar, 底分型定位到真正最低点所在的原始bar,
     使标记落在最真实的价格极值上。

输出: data.js —— window.MARKET_DATA (前端 data.js 直接 <script> 引入, 可 file:// 打开)
"""

import json
from pathlib import Path

import pandas as pd

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "crypto" / "BTC_USD_1day.parquet"
OUT_FILE = "data.js"


# --------------------------------------------------------------------------- #
# 1. 包含关系处理                                                              #
# --------------------------------------------------------------------------- #
def is_inclusive(a, b):
    """a、b 两根K线是否互相包含(存在包含关系)。"""
    return (a["high"] >= b["high"] and a["low"] <= b["low"]) or (
        b["high"] >= a["high"] and b["low"] <= a["low"]
    )


def append_merged_bar(merged, bar):
    """把一根原始K线并入 merged(只改最后一根, 不回写更早的已冻结合并K)。"""
    merged.append(
        {
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar["volume"],
            "start": bar["idx"],
            "end": bar["idx"],
        }
    )
    while len(merged) >= 2 and is_inclusive(merged[-1], merged[-2]):
        cur, last = merged[-1], merged[-2]
        if len(merged) >= 3:
            prev = merged[-3]
            going_up = last["high"] >= prev["high"]
        else:
            going_up = cur["high"] >= last["high"]

        if going_up:  # 向上: 高高 + 高低
            merged[-2] = {
                "open": last["open"],
                "high": max(last["high"], cur["high"]),
                "low": max(last["low"], cur["low"]),
                "close": cur["close"],
                "volume": last["volume"] + cur["volume"],
                "start": last["start"],
                "end": cur["end"],
            }
        else:  # 向下: 低高 + 低低
            merged[-2] = {
                "open": last["open"],
                "high": min(last["high"], cur["high"]),
                "low": min(last["low"], cur["low"]),
                "close": cur["close"],
                "volume": last["volume"] + cur["volume"],
                "start": last["start"],
                "end": cur["end"],
            }
        del merged[-1]


def process_inclusions(bars):
    """
    bars: list[dict] —— {open, high, low, close, volume, idx}
    返回合并后的K线列表, 每根含 start/end(原始索引闭区间)。
    合并时 volume 累加(合并K线的成交量 = 所包含原始K线之和)。
    """
    merged = []
    for b in bars:
        append_merged_bar(merged, b)
    return merged


# --------------------------------------------------------------------------- #
# 2. 分型识别 (按原文双向极值 + 收盘确认)                                        #
# --------------------------------------------------------------------------- #
# 收盘确认窗口: 合并中K线结束后, 取其后最多这么多根原始K线, 任意一根收盘价
# 反向突破中K线极值(顶: 跌破其最低; 底: 升破其最高)即确认。
# 放宽自"仅第一根"(2026-08): 2024-08-05 崩溃低点 49000 的确认收盘在合并中
# K线后第 2 根(08-08 收盘 61686 升破 57041), 旧规则只看 08-07 收盘 55134 而误拒。
CONFIRM_WINDOW = 3


def close_confirm_index(mid, bars, is_top, limit=None):
    """收盘确认的原始 bar 下标; 窗口内未突破则 None。

    limit: 只看到 bars[:limit](因果前缀); 默认 len(bars)。
    """
    lo, hi = mid["low"], mid["high"]
    n = len(bars) if limit is None else min(limit, len(bars))
    for j in range(mid["end"] + 1, min(mid["end"] + 1 + CONFIRM_WINDOW, n)):
        c = bars[j]["close"]
        if is_top and c < lo:
            return j
        if (not is_top) and c > hi:
            return j
    return None


def _close_confirmed(mid, bars, is_top):
    """合并中K线结束后的 CONFIRM_WINDOW 根原始K线内是否有收盘反向突破中K线极值。

    mid: 合并K线(start/end 为原始索引闭区间); is_top: 是否顶分型。
    """
    return close_confirm_index(mid, bars, is_top) is not None


def find_fractals(merged, bars):
    """在合并K线上识别顶/底分型, 返回各自在 merged 中的下标列表。

    按缠论原文(第62课)定义——中间K线须为三根中的**双向极值**:
      顶分型: 中间K线高点为三者最高, 且低点也为三者最高。
      底分型: 中间K线低点为三者最低, 且高点也为三者最低。
    再叠加用户自定义的收盘确认(CONFIRM_WINDOW 根窗口内任意一根原始K线):
      顶分型需其后某根原始K线收盘价跌破中间K线最低价;
      底分型需其后某根原始K线收盘价升破中间K线最高价。
    """
    tops, bottoms = [], []
    for i in range(1, len(merged) - 1):
        left, mid, right = merged[i - 1], merged[i], merged[i + 1]
        if (
            mid["high"] > left["high"]
            and mid["high"] > right["high"]
            and mid["low"] > left["low"]
            and mid["low"] > right["low"]
            and _close_confirmed(mid, bars, is_top=True)
        ):
            tops.append(i)
        elif (
            mid["low"] < left["low"]
            and mid["low"] < right["low"]
            and mid["high"] < left["high"]
            and mid["high"] < right["high"]
            and _close_confirmed(mid, bars, is_top=False)
        ):
            bottoms.append(i)
    return tops, bottoms


# --------------------------------------------------------------------------- #
# 3. 交替约束(连续同类仅保留更极端者)                                           #
# --------------------------------------------------------------------------- #
def enforce_alternation(merged, bars, tops, bottoms):
    """
    合并K线局部极值天然严格交替; 收盘确认会剔除未确认的中间分型, 使输出
    出现"连续同类"。对每个分型取其真实极值价格, 连续同类中保留更极端的一个,
    保证最终输出严格 顶-底-顶-底 交替。
    """
    seq = sorted([(i, "T") for i in tops] + [(i, "B") for i in bottoms], key=lambda x: x[0])
    # 每个分型的真实极值价格(用于比较孰更极端)
    val = {}
    for i in tops:
        _, val[i] = localize_top(i, merged, bars)
    for i in bottoms:
        _, val[i] = localize_bottom(i, merged, bars)

    out = []
    for i, kind in seq:
        if out and out[-1][1] == kind:  # 与上一输出同类 → 保留更极端者
            j, _ = out[-1]
            keep_new = (kind == "B" and val[i] < val[j]) or (kind == "T" and val[i] > val[j])
            if keep_new:
                out[-1] = (i, kind)
        else:
            out.append((i, kind))
    return [i for i, k in out if k == "T"], [i for i, k in out if k == "B"]


# --------------------------------------------------------------------------- #
# 4. 分型定位到原始极值bar                                                      #
# --------------------------------------------------------------------------- #
def localize_top(merged_idx, merged, bars):
    """顶分型 → 合并区间 [start, end] 内最高点所在的原始bar 及价格。"""
    m = merged[merged_idx]
    best = m["start"]
    for j in range(m["start"], m["end"] + 1):
        if bars[j]["high"] > bars[best]["high"]:
            best = j
    return best, bars[best]["high"]


def localize_bottom(merged_idx, merged, bars):
    """底分型 → 合并区间 [start, end] 内最低点所在的原始bar 及价格。"""
    m = merged[merged_idx]
    best = m["start"]
    for j in range(m["start"], m["end"] + 1):
        if bars[j]["low"] < bars[best]["low"]:
            best = j
    return best, bars[best]["low"]


# --------------------------------------------------------------------------- #
# 主流程                                                                        #
# --------------------------------------------------------------------------- #
def main():
    df = pd.read_parquet(DATA_FILE)
    df = df.sort_values("time").reset_index(drop=True)
    dates = (
        pd.to_datetime(df["time"], unit="s", utc=True)
        .dt.strftime("%Y-%m-%d")
        .tolist()
    )

    bars = []
    for idx, row in df.iterrows():
        bars.append(
            {
                "idx": idx,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )

    merged = process_inclusions(bars)
    tops, bottoms = find_fractals(merged, bars)
    tops, bottoms = enforce_alternation(merged, bars, tops, bottoms)

    # 原始K线: [time, open, high, low, close]; volume 平行数组
    candles = [
        [dates[i], b["open"], b["high"], b["low"], b["close"]] for i, b in enumerate(bars)
    ]
    volume = [round(b["volume"], 2) for b in bars]
    # 合并K线(处理包含关系后)及其成交量(累加)
    merged_candles = [
        [dates[m["start"]], m["open"], m["high"], m["low"], m["close"]] for m in merged
    ]
    merged_volume = [round(m["volume"], 2) for m in merged]

    # 顶分型标记: 原始视图定位到真正最高点; 合并视图定位到合并K线起点
    top_markers, top_merged_markers = [], []
    for mi in tops:
        bi, px = localize_top(mi, merged, bars)
        m = merged[mi]
        top_markers.append({"time": dates[bi], "price": round(px, 2)})
        top_merged_markers.append(
            {"time": dates[m["start"]], "price": round(m["high"], 2)}
        )

    bottom_markers, bottom_merged_markers = [], []
    for mi in bottoms:
        bi, px = localize_bottom(mi, merged, bars)
        m = merged[mi]
        bottom_markers.append({"time": dates[bi], "price": round(px, 2)})
        bottom_merged_markers.append(
            {"time": dates[m["start"]], "price": round(m["low"], 2)}
        )

    data = {
        "meta": {
            "symbol": "BTC/USD",
            "interval": "1D",
            "firstDate": dates[0],
            "lastDate": dates[-1],
            "nBars": len(bars),
            "nMerged": len(merged),
            "nTops": len(tops),
            "nBottoms": len(bottoms),
        },
        "candles": candles,
        "volume": volume,
        "mergedCandles": merged_candles,
        "mergedVolume": merged_volume,
        "topMarkers": top_markers,
        "bottomMarkers": bottom_markers,
        "topMergedMarkers": top_merged_markers,
        "bottomMergedMarkers": bottom_merged_markers,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("window.MARKET_DATA = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    m = data["meta"]
    print("=" * 56)
    print(f"  原始K线数        : {m['nBars']}")
    print(f"  合并K线数        : {m['nMerged']}  (包含关系处理后)")
    print(f"  顶分型 / 底分型  : {m['nTops']} / {m['nBottoms']}")
    print(f"  日期范围         : {m['firstDate']} ~ {m['lastDate']}")
    print(f"  输出             : {OUT_FILE}  ({len(candles)} 根K线)")
    print("=" * 56)


if __name__ == "__main__":
    main()
