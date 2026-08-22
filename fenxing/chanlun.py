#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论完整技术体系 · 多级别联动 — Chan-Theory full pipeline for BTC 1D/4H/1H

在 fractals.py 分型层(包含处理 → 顶底分型 → 收盘确认 → 交替约束)之上, 复刻:
  笔   (strokes)   — 新笔标准: 相邻顶底分型中间 ≥5 根无包含K线(合并K线上间距 ≥4)
  线段 (segments)  — 特征序列法(缠论 65/67/71 课): 反向笔构成特征序列,
                     做包含处理得标准特征序列, 检测顶/底分型, 按缺口第一/第二情况定终点
  中枢 (pivots)    — 至少三个连续次级别走势重叠: [ZD,ZG]=[max(低),min(高)],
                     GG=max(高), DD=min(低); 重叠则延伸, 离开则结束
                     ★ 两套: 线段中枢(以线段为次级别走势) 与 笔中枢(以笔为次级别走势)
  MACD 背驰        — EMA12/26, DEA=EMA9, 柱=DIFF-DEA; 比较同向 A(进入) 与 C(离开) 柱面积
  三类买点         — 一买(下跌中枢链离开段趋势背驰; 后续更低中枢不删除已发出的点)、
                     二买(一买后回调不创新低)、三买(向上离开中枢后回抽不回中枢)
  三类卖点         — 一卖(上升中枢链离开段顶背驰; 后续更高中枢不删除已发出的点)、
                     二卖(一卖后反弹不创新高)、三卖(向下离开中枢后回抽不回中枢)
  确认时点         — confirmedT = 该点在「已完成笔/线段前缀」上首次可判定时,
                     对应端点分型的收盘确认 bar(不是极值 bar 本身)

多级别联动(缠论"次级别"):
  对 1D/4H/1H 各自独立跑完整链路(同构级别)。
  级别关系: 1D ←(次级别) 4H ←(次级别) 1H。
  ★ 次级别确认 sub: 按原文"大级别背驰必由次级别背驰引发"——对每个级别的
    一买/一卖, 在其次级别上于时间窗内搜索同向趋势背驰(一买/一卖)。
  ★ 上级别语境 upper: 该点是否落在上级别某中枢时间区间内。
  每个买卖点给出"产生说明 why"(由哪个中枢、哪段离开、哪种背驰产生)。

输出: data.js —— window.MARKET_DATA (levels{1D,4H,1H}, 时间统一 epoch 秒)
"""

import json

import pandas as pd

from fractals import (
    append_merged_bar,
    close_confirm_index,
    enforce_alternation,
    find_fractals,
    localize_bottom,
    localize_top,
    process_inclusions,
)

DATA_FILES = {"1D": "BTC_USD_1day.parquet", "4H": "BTC_USD_4h.parquet", "1H": "BTC_USD_1h.parquet"}
SUB_RELATION = {"1D": "4H", "4H": "1H"}   # 大级别 → 次级别
UP_RELATION = {"4H": "1D", "1H": "4H"}    # 小级别 → 上级别
OUT_FILE = "data.js"

# --------------------------------------------------------------------------- #
# 参数(解释性选择, 均可调)                                                       #
# --------------------------------------------------------------------------- #
MIN_STROKE_SPAN = 5      # 新笔: 顶底中间合并K线 span(含) >= 5 → 间距 >= 4
BEICHI_RATIO = 0.9       # C段柱面积 < A段柱面积 * 0.9 → 判定背驰
FAST_RATE = 0.75         # 抢先信号: 当前段面积积累速率 < 参考A速率*该值 → 记录 alertT(不作为成交)
FAST_MIN_BARS = 3        # 抢先信号: alertT 最早在第几根之后允许记录
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
LINK_WINDOW = {"1D": 72 * 3600, "4H": 24 * 3600}   # 大级别搜次级别一买/一卖的时间窗(秒)


# --------------------------------------------------------------------------- #
# 1. 笔 (strokes)                                                              #
# --------------------------------------------------------------------------- #
def build_strokes(merged, tops, bottoms):
    """从严格交替的顶底分型序列构建笔。

    merged: process_inclusions 输出(含 start/end 原始索引范围)。
    tops/bottoms: enforce_alternation 输出的合并K线下标(严格交替)。

    关键: 笔的端点可被更极端的同性质分型"替换"——此时上一笔的终点
    应向后延伸(而非丢弃旧笔, 否则相邻笔端点不连续)。
    """
    seq = sorted([(i, "T") for i in tops] + [(i, "B") for i in bottoms])
    strokes = []
    last_end = None  # 当前开放端点 (idx, kind)
    for i, kind in seq:
        if last_end is None:
            last_end = (i, kind)
            continue
        li, lkind = last_end
        if kind == lkind:
            # 同性质: 更极端则替换开放端点, 并把上一笔终点向后延伸
            if (kind == "T" and merged[i]["high"] > merged[li]["high"]) or (
                kind == "B" and merged[i]["low"] < merged[li]["low"]
            ):
                last_end = (i, kind)
                if strokes:
                    strokes[-1]["e_idx"] = i
                    strokes[-1]["e_price"] = (
                        merged[i]["high"] if kind == "T" else merged[i]["low"]
                    )
                    strokes[-1]["high"] = max(
                        strokes[-1]["s_price"], strokes[-1]["e_price"]
                    )
                    strokes[-1]["low"] = min(
                        strokes[-1]["s_price"], strokes[-1]["e_price"]
                    )
        else:
            # 异类: 距离足够(顶底中间 ≥ MIN_STROKE_SPAN 合并K线) 且 价格条件成立 → 成笔
            if i - li >= MIN_STROKE_SPAN - 1:
                up = lkind == "B" and kind == "T"
                ok = (
                    merged[i]["high"] > merged[li]["high"]
                    if up
                    else merged[i]["low"] < merged[li]["low"]
                )
                if ok:
                    strokes.append(_make_stroke(merged, last_end, (i, kind)))
                    last_end = (i, kind)
                # else: 价格条件不成立 → 丢弃新分型
            # else: 距离不足 → 丢弃新分型(视为中继噪声)
    return strokes


def _make_stroke(merged, a, f):
    ai, akind = a
    fi, fkind = f
    if akind == "B":  # 上升笔: 底 → 顶
        s_price, e_price = merged[ai]["low"], merged[fi]["high"]
    else:  # 下降笔: 顶 → 底
        s_price, e_price = merged[ai]["high"], merged[fi]["low"]
    return {
        "dir": 1 if akind == "B" else -1,
        "s_idx": ai,
        "e_idx": fi,
        "s_price": s_price,
        "e_price": e_price,
        "high": max(s_price, e_price),
        "low": min(s_price, e_price),
    }


# --------------------------------------------------------------------------- #
# 2. 线段 (segments) — 特征序列法                                               #
# --------------------------------------------------------------------------- #
def _is_inclusive_bar(a, b):
    return (a["high"] >= b["high"] and a["low"] <= b["low"]) or (
        b["high"] >= a["high"] and b["low"] <= a["low"]
    )


def _merge_feature(procd, seg_dir):
    """特征序列元素包含处理。方向按线段方向(缠论: 向上取高高/高低, 向下取低低/低高)。"""
    while len(procd) >= 2 and _is_inclusive_bar(procd[-1], procd[-2]):
        cur, last = procd[-1], procd[-2]
        if seg_dir == 1:
            hi = max(last["high"], cur["high"])
            lo = max(last["low"], cur["low"])
        else:
            hi = min(last["high"], cur["high"])
            lo = min(last["low"], cur["low"])
        hi_src = last["hi_src"] if last["high"] >= cur["high"] else cur["hi_src"]
        lo_src = last["lo_src"] if last["low"] <= cur["low"] else cur["lo_src"]
        procd[-2] = {"high": hi, "low": lo, "hi_src": hi_src, "lo_src": lo_src}
        del procd[-1]


def _feature_fractal(a, b, c, seg_dir):
    """标准特征序列分型: 向上线段看顶分型, 向下线段看底分型(双向极值, 同分型定义)。"""
    if seg_dir == 1:
        return (
            b["high"] > a["high"]
            and b["high"] > c["high"]
            and b["low"] > a["low"]
            and b["low"] > c["low"]
        )
    return (
        b["low"] < a["low"]
        and b["low"] < c["low"]
        and b["high"] < a["high"]
        and b["high"] < c["high"]
    )


def _has_gap(a, b):
    """特征序列第1、2元素之间是否存在缺口(区间无重叠)。"""
    return a["low"] > b["high"] or b["low"] > a["high"]


def _reverse_confirmed(strokes, b_bar, seg_dir):
    """第二种情况(缺口): 需等待反向线段的特征序列出现分型确认, 原线段才结束。"""
    new_dir = -seg_dir
    src = b_bar["hi_src"] if seg_dir == 1 else b_bar["lo_src"]
    new_procd = []
    k = src + 1
    n = len(strokes)
    while k < n:
        s = strokes[k]
        if s["dir"] == seg_dir:  # 新线段特征序列 = 与原线段同向的笔
            new_procd.append({"high": s["high"], "low": s["low"], "hi_src": k, "lo_src": k})
            _merge_feature(new_procd, new_dir)
            if len(new_procd) >= 3 and _feature_fractal(
                new_procd[-3], new_procd[-2], new_procd[-1], new_dir
            ):
                return True
        k += 1
    return False


def build_segments(strokes):
    """特征序列法线段划分。每段由奇数笔组成, 方向 = 首笔方向。"""
    n = len(strokes)
    if n == 0:
        return []
    segments = []
    seg_start = 0
    while seg_start < n:
        seg_dir = strokes[seg_start]["dir"]
        procd = []  # 处理后的特征序列 bar: {high, low, hi_src, lo_src}
        seg_end = None
        j = seg_start
        while j < n:
            s = strokes[j]
            if s["dir"] == seg_dir:
                j += 1
                continue
            procd.append({"high": s["high"], "low": s["low"], "hi_src": j, "lo_src": j})
            _merge_feature(procd, seg_dir)
            if len(procd) >= 3:
                a, b, c = procd[-3], procd[-2], procd[-1]
                if _feature_fractal(a, b, c, seg_dir):
                    if not _has_gap(a, b):
                        # 第一种情况(无缺口): 线段在分型处结束
                        seg_end = (b["hi_src"] if seg_dir == 1 else b["lo_src"]) - 1
                        break
                    elif _reverse_confirmed(strokes, b, seg_dir):
                        # 第二种情况(有缺口): 反向特征序列出现分型 → 确认结束
                        seg_end = (b["hi_src"] if seg_dir == 1 else b["lo_src"]) - 1
                        break
                    # 否则线段延续
            j += 1
        if seg_end is None:
            seg_end = n - 1
        segments.append(_make_segment(strokes, seg_start, seg_end))
        if seg_end == n - 1:
            break
        seg_start = seg_end + 1
    for idx, seg in enumerate(segments):
        seg["complete"] = idx < len(segments) - 1  # 最后一段尚未被分型终结
    return segments


def _make_segment(strokes, s, e):
    return {
        "dir": strokes[s]["dir"],
        "s_idx": strokes[s]["s_idx"],
        "e_idx": strokes[e]["e_idx"],
        "s_price": strokes[s]["s_price"],
        "e_price": strokes[e]["e_price"],
        "high": max(st["high"] for st in strokes[s : e + 1]),
        "low": min(st["low"] for st in strokes[s : e + 1]),
        "n_strokes": e - s + 1,
    }


# --------------------------------------------------------------------------- #
# 3. 中枢 (pivots)                                                             #
# --------------------------------------------------------------------------- #
def build_pivots(levels, pullback_ext=False):
    """滑窗取连续3个次级别走势, 三者重叠([ZD,ZG]=[max(低),min(高)]) → 中枢;
    其后走势与[ZD,ZG]重叠则延伸(更新GG/DD与时间范围), 离开则结束。
    levels: 线段或笔(统一接口: s_idx/e_idx/high/low)。

    pullback_ext=False(现行): 任一段与[ZD,ZG]无重叠 → 中枢立即结束。
    pullback_ext=True(原文口径): 与区间无重叠视为「离开段」, 若其后某段重新
      回到[ZD,ZG](回拉)则中枢延伸至该段; 若始终不回拉, 中枢止于最后一个
      重叠段。对应原文「任何对中枢的回拉均构成中枢延伸」。"""
    pivots = []
    n = len(levels)
    i = 0
    while i + 2 < n:
        a, b, c = levels[i], levels[i + 1], levels[i + 2]
        zd = max(a["low"], b["low"], c["low"])
        zg = min(a["high"], b["high"], c["high"])
        if zd < zg:
            gg = max(a["high"], b["high"], c["high"])
            dd = min(a["low"], b["low"], c["low"])
            z_end = i + 2
            k = i + 3
            if pullback_ext:
                left = False  # 已出现未回拉的离开段
                while k < n:
                    lvl = levels[k]
                    if lvl["low"] <= zg and lvl["high"] >= zd:
                        z_end = k  # 回拉 → 延伸
                        gg = max(gg, lvl["high"])
                        dd = min(dd, lvl["low"])
                        left = False
                    else:
                        left = True
                    k += 1
            else:
                while k < n:
                    lvl = levels[k]
                    if lvl["low"] <= zg and lvl["high"] >= zd:  # 与中枢区间重叠 → 延伸
                        z_end = k
                        gg = max(gg, lvl["high"])
                        dd = min(dd, lvl["low"])
                        k += 1
                    else:
                        break
            pivots.append(
                {
                    "level_start": i,
                    "level_end": z_end,
                    "zd": zd,
                    "zg": zg,
                    "gg": gg,
                    "dd": dd,
                    "s_idx": levels[i]["s_idx"],
                    "e_idx": levels[z_end]["e_idx"],
                }
            )
            i = z_end + 1
        else:
            i += 1
    return pivots


# --------------------------------------------------------------------------- #
# 4. MACD 与背驰                                                               #
# --------------------------------------------------------------------------- #
def macd_series(closes, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    def ema(vals, period):
        k = 2.0 / (period + 1.0)
        out, e = [], None
        for v in vals:
            e = v if e is None else v * k + e * (1 - k)
            out.append(e)
        return out

    diff = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    dea = ema(diff, signal)
    hist = [d - s for d, s in zip(diff, dea)]
    return diff, dea, hist


# --------------------------------------------------------------------------- #
# 5. 三类买点 / 三类卖点                                                        #
# --------------------------------------------------------------------------- #
def _areas(levels, pivots, hist, merged):
    """返回 (raw_range, green_area, red_area) 闭包。"""

    def raw_range(lvl):
        return merged[lvl["s_idx"]]["start"], merged[lvl["e_idx"]]["end"]

    def green_area(lvl):
        r0, r1 = raw_range(lvl)
        return sum(-h for h in hist[r0 : r1 + 1] if h < 0)

    def red_area(lvl):
        r0, r1 = raw_range(lvl)
        return sum(h for h in hist[r0 : r1 + 1] if h > 0)

    return raw_range, green_area, red_area


def find_buy_points(levels, pivots, hist, merged, min_ratio=BEICHI_RATIO,
                    a_mode="strongest"):
    """一买/二买/三买。levels: 线段或笔; pivots: 中枢; hist: 原始K线MACD柱。

    一买: 下跌中枢链(前中枢更高)的向下离开段 出现 MACD 绿柱面积背驰。
          后续若出现更低中枢, 已发出的一买保留(失败点计入回测, 不事后删除)。
          a_mode="strongest": A段 = 整条中枢链内绿柱面积最大的向下笔(现行口径)。
          a_mode="enter":     A段 = 进入当前中枢的同向下跌段(原文口径:
                              离开最后中枢的 C 段 vs 进入该中枢的 B 段)。
    二买: 一买后 反弹一段 + 回调一段, 回调低点不破一买低点。
    三买: 中枢被向上离开后, 回抽段低点 ≥ ZG(不回中枢)。
    """
    raw_range, green_area, _ = _areas(levels, pivots, hist, merged)
    buys = []
    n = len(levels)

    # ---- 一买 ----
    for pi, cur in enumerate(pivots):
        if pi == 0 or pivots[pi - 1]["zd"] <= cur["zg"]:
            continue  # 无更高前中枢 → 非下跌中枢链
        # 向前构建降序中枢链: 前中枢 DD 高于当前 DD 即入链(允许嵌套/重叠的中枢,
        # 比严格"前ZD>后ZG"更能覆盖 2022 这类层层嵌套的下跌趋势)。
        chain = [cur]
        p = pi - 1
        while p >= 0 and pivots[p]["dd"] > cur["dd"]:
            chain.insert(0, pivots[p])
            p -= 1
        first = chain[0]
        # C段 = 本中枢内/后向下笔中最低者(创出新低)
        c_idx, C = None, None
        for k in range(cur["level_start"], min(cur["level_end"] + 2, n)):
            if levels[k]["dir"] == -1 and (C is None or levels[k]["e_price"] < C["e_price"]):
                C, c_idx = levels[k], k
        if a_mode == "enter":
            # 原文口径: B段 = 进入当前中枢的同向下跌段(即中枢第一段的前一段)
            si = cur["level_start"] - 1
            if si < 0 or levels[si]["dir"] != -1:
                continue
            A, a_idx = levels[si], si
        else:
            # A段 = 整条下跌中枢链内绿柱面积最大的向下笔(趋势最强段)——
            # 与 C 段同口径比较面积, 更能反映"末段较趋势最猛段衰竭"的趋势背驰。
            a_idx, A, max_a = None, None, 0.0
            for k in range(first["level_start"], chain[-1]["level_end"] + 1):
                lvl = levels[k]
                if lvl["dir"] == -1:
                    a = green_area(lvl)
                    if a > max_a:
                        max_a, A, a_idx = a, lvl, k
        if C is None or A is None:
            continue
        # C段须创出新低(低于链上前一中枢的 DD), 否则只是回试低点(属二买结构)
        if len(chain) >= 2 and C["e_price"] > chain[-2]["dd"]:
            continue
        a_area, c_area = green_area(A), green_area(C)
        if a_area > 0 and c_area < a_area * min_ratio:
            buys.append(
                {
                    "type": 1, "level_idx": c_idx, "price": C["e_price"],
                    "pi": pi, "a_idx": a_idx, "c_idx": c_idx,
                    "a_area": a_area, "c_area": c_area, "ratio": c_area / a_area,
                }
            )
    # ---- 二买: 一买后 反弹一段 + 回调一段, 回调低点不破一买低点 ----
    for b1 in [b for b in buys if b["type"] == 1]:
        li = b1["level_idx"]
        if li + 2 < n and levels[li + 1]["dir"] == 1 and levels[li + 2]["dir"] == -1:
            D = levels[li + 2]
            if D["e_price"] > b1["price"]:
                buys.append(
                    {
                        "type": 2, "level_idx": li + 2, "price": D["e_price"],
                        "b1_idx": b1["level_idx"], "b1_price": b1["price"],
                    }
                )
    # ---- 三买: 中枢向上离开后, 回抽段低点 ≥ ZG(不回中枢) ----
    for pi, cur in enumerate(pivots):
        li = cur["level_end"] + 1
        if li >= n:
            continue
        L = levels[li]
        if L["dir"] == -1:
            # 离开段即为向下回抽: 低点不破中枢上沿
            if L["low"] >= cur["zg"]:
                buys.append(
                    {"type": 3, "level_idx": li, "price": L["e_price"],
                     "pi": pi, "leave_idx": li, "pull_idx": li}
                )
        elif li + 1 < n and L["high"] > cur["zg"]:
            # 离开段向上突破, 再看回抽段
            D = levels[li + 1]
            if D["dir"] == -1 and D["low"] >= cur["zg"]:
                buys.append(
                    {"type": 3, "level_idx": li + 1, "price": D["e_price"],
                     "pi": pi, "leave_idx": li, "pull_idx": li + 1}
                )
    buys.sort(key=lambda b: (b["type"], b["level_idx"]))
    return buys


def find_sell_points(levels, pivots, hist, merged, min_ratio=BEICHI_RATIO,
                     a_mode="strongest"):
    """一卖/二卖/三卖。与 find_buy_points 完全对称。

    一卖: 上升中枢链(前中枢更低)的向上离开段 出现 MACD 红柱面积背驰。
          后续若出现更高中枢, 已发出的一卖保留(失败点计入回测, 不事后删除)。
          a_mode="strongest": A段 = 整条中枢链内红柱面积最大的向上笔(现行口径)。
          a_mode="enter":     A段 = 进入当前中枢的同向上涨段(原文口径)。
    二卖: 一卖后 回调一段 + 反弹一段, 反弹高点不破一卖高点。
    三卖: 中枢被向下离开后, 回抽段高点 ≤ ZD(不回中枢)。
    """
    _, _, red_area = _areas(levels, pivots, hist, merged)
    sells = []
    n = len(levels)

    # ---- 一卖 ----
    for pi, cur in enumerate(pivots):
        if pi == 0 or pivots[pi - 1]["zg"] >= cur["zd"]:
            continue  # 无更低前中枢 → 非上升中枢链
        # 向前构建升序中枢链: 前中枢 GG 低于当前 GG 即入链(与买点对称)。
        chain = [cur]
        p = pi - 1
        while p >= 0 and pivots[p]["gg"] < cur["gg"]:
            chain.insert(0, pivots[p])
            p -= 1
        first = chain[0]
        # C段 = 本中枢内/后向上笔中最高者(创出新高)
        c_idx, C = None, None
        for k in range(cur["level_start"], min(cur["level_end"] + 2, n)):
            if levels[k]["dir"] == 1 and (C is None or levels[k]["e_price"] > C["e_price"]):
                C, c_idx = levels[k], k
        if a_mode == "enter":
            # 原文口径: B段 = 进入当前中枢的同向上涨段
            si = cur["level_start"] - 1
            if si < 0 or levels[si]["dir"] != 1:
                continue
            A, a_idx = levels[si], si
        else:
            # A段 = 整条上升中枢链内红柱面积最大的向上笔(趋势最强段, 与买点对称)
            a_idx, A, max_a = None, None, 0.0
            for k in range(first["level_start"], chain[-1]["level_end"] + 1):
                lvl = levels[k]
                if lvl["dir"] == 1:
                    a = red_area(lvl)
                    if a > max_a:
                        max_a, A, a_idx = a, lvl, k
        if C is None or A is None:
            continue
        # C段须创出新高(高于链上前一中枢的 GG), 否则只是回试高点(属二卖结构)
        if len(chain) >= 2 and C["e_price"] < chain[-2]["gg"]:
            continue
        a_area, c_area = red_area(A), red_area(C)
        if a_area > 0 and c_area < a_area * min_ratio:
            sells.append(
                {
                    "type": 1, "level_idx": c_idx, "price": C["e_price"],
                    "pi": pi, "a_idx": a_idx, "c_idx": c_idx,
                    "a_area": a_area, "c_area": c_area, "ratio": c_area / a_area,
                }
            )
    # ---- 二卖: 一卖后 回调一段 + 反弹一段, 反弹高点不破一卖高点 ----
    for s1 in [s for s in sells if s["type"] == 1]:
        li = s1["level_idx"]
        if li + 2 < n and levels[li + 1]["dir"] == -1 and levels[li + 2]["dir"] == 1:
            R = levels[li + 2]
            if R["e_price"] < s1["price"]:
                sells.append(
                    {
                        "type": 2, "level_idx": li + 2, "price": R["e_price"],
                        "s1_idx": s1["level_idx"], "s1_price": s1["price"],
                    }
                )
    # ---- 三卖: 中枢向下离开后, 回抽段高点 ≤ ZD(不回中枢) ----
    for pi, cur in enumerate(pivots):
        li = cur["level_end"] + 1
        if li >= n:
            continue
        L = levels[li]
        if L["dir"] == 1:
            # 离开段即为向上回抽: 高点不破中枢下沿
            if L["high"] <= cur["zd"]:
                sells.append(
                    {"type": 3, "level_idx": li, "price": L["e_price"],
                     "pi": pi, "leave_idx": li, "pull_idx": li}
                )
        elif li + 1 < n and L["low"] < cur["zd"]:
            # 离开段向下破位, 再看回抽段
            D = levels[li + 1]
            if D["dir"] == 1 and D["high"] <= cur["zd"]:
                sells.append(
                    {"type": 3, "level_idx": li + 1, "price": D["e_price"],
                     "pi": pi, "leave_idx": li, "pull_idx": li + 1}
                )
    sells.sort(key=lambda b: (b["type"], b["level_idx"]))
    return sells


# --------------------------------------------------------------------------- #
# 6a. 抢先背驰信号(告警 → 背驰买/卖点入场)                                        #
# --------------------------------------------------------------------------- #
def _leg_area(L, hist, merged, pos, mag):
    """一段(笔)的绿/红柱面积合计(买侧绿柱取负为正, 卖侧红柱)。"""
    a0 = merged[L["s_idx"]]["start"]
    a1 = merged[L["e_idx"]]["end"]
    return sum(mag(h) for h in hist[a0 : a1 + 1] if pos(h))


def find_fast_beichi(levels, hist, merged, bars, epochs, tf, is_sell):
    """抢先背驰信号(笔级): 背驰买/卖点入场。

    is_sell=False → 买侧(下降笔, 绿柱); is_sell=True → 卖侧(上升笔, 红柱)。

    语义(笔完成后的背驰点, 不回标笔内 MACD 缩短日):
      - 语境门: 本笔相对前一笔同类段 创新低(买)/新高(卖)。
      - A 参考: 前 ≤3 笔同类段 绿/红柱面积最大者(已走完的参考段)。
      - 仅当整笔走完且总面积 < A*BEICHI_RATIO 时发出; 入场 = 端点分型确认 bar。
      - alertT 仍记录笔内首次弱于 A 速率的 bar, 仅供说明, 不作为成交时点。
      若整笔面积冲过阈值 → 不入场。

    返回 (alerts, entries): alerts 恒空(告警已移除), entries 为背驰买/卖点。
    """
    pos = (lambda h: h > 0) if is_sell else (lambda h: h < 0)
    mag = (lambda h: h) if is_sell else (lambda h: -h)
    side = "卖" if is_sell else "买"
    alerts, entries = [], []
    same = [i for i, lvl in enumerate(levels) if lvl["dir"] == (1 if is_sell else -1)]
    prevs = []  # (总面积, 端点价, bar数) 逐笔同类段
    for k in same:
        L = levels[k]
        r0 = merged[L["s_idx"]]["start"]
        r1 = merged[L["e_idx"]]["end"]
        total = _leg_area(L, hist, merged, pos, mag)
        L_len = r1 - r0 + 1
        if len(prevs) == 0:
            prevs.append((total, L["e_price"], L_len))
            continue
        A_entry = max(prevs[-3:], key=lambda p: p[0])
        A, A_len = A_entry[0], A_entry[2]
        new_low_high = (L["e_price"] < prevs[-1][1]) if not is_sell else (L["e_price"] > prevs[-1][1])
        if A > 0 and new_low_high and total < A * BEICHI_RATIO:
            # 累计面积(why / alertT 用); 成交只在笔完成后的分型确认 bar
            cums = [0.0] * (r1 - r0 + 1)
            s = 0.0
            for i in range(r0, r1 + 1):
                if pos(hist[i]):
                    s += mag(hist[i])
                cums[i - r0] = s
            alert_i = r0
            for i in range(r0, r1 + 1):
                elapsed = i - r0 + 1
                if elapsed < FAST_MIN_BARS or A_len <= 0:
                    continue
                expected = A * (elapsed / A_len) * FAST_RATE
                if cums[i - r0] < expected and cums[i - r0] < A * BEICHI_RATIO:
                    alert_i = i
                    break
            entry_i = _fractal_confirm_raw(L["e_idx"], merged, bars, is_top=is_sell)
            if entry_i is not None:
                entries.append({
                    "_ri": entry_i, "t": epochs[entry_i], "p": round(bars[entry_i]["close"], 2),
                    "k": "背驰%s" % side, "side": side, "trigger": "底分型",
                    "alertT": epochs[alert_i], "ratio": round(total / A, 3),
                    "why": _fast_why(side, entry_i, r0, r1, cums, A, total, epochs, tf, is_sell),
                })
        prevs.append((total, L["e_price"], L_len))
    return alerts, entries


def _fast_why(side, at_i, r0, r1, cums, A, total, epochs, tf, is_sell):
    """抢先信号说明: 参考段 A 面积、当前段运行面积、比率、告警/入场点。"""
    run = cums[at_i - r0] if r0 <= at_i <= r1 else total
    ratio = (run / A) if A > 0 else 0.0
    zhang = "涨" if is_sell else "跌"
    return ("%s侧: %s势运行绿/红柱面积 %d < 参考A %d (比 %.2f) 趋势背驰" %
            (side, zhang, round(run, 0), round(A, 0), ratio))


# --------------------------------------------------------------------------- #
# 6. 产生说明(why) + 多级别联动(sub/upper)                                      #
# --------------------------------------------------------------------------- #
def _fmt_epoch(ts, tf):
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(ts, timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M" if tf != "1D" else "%Y-%m-%d")


def _lv(lvl, merged, disp):
    return "%s→%s" % (disp[merged[lvl["s_idx"]]["start"]], disp[merged[lvl["e_idx"]]["end"]])


def _pv(pv, levels, merged, disp):
    a, b = levels[pv["level_start"]], levels[pv["level_end"]]
    return "%s~%s" % (disp[merged[a["s_idx"]]["start"]], disp[merged[b["e_idx"]]["end"]])


def _explain(pt, levels, pivots, merged, disp, is_sell):
    t = pt["type"]
    if t == 1:
        pv = pivots[pt["pi"]]
        if not is_sell:
            return (
                "下跌中枢链 [%s] ZD %.0f/ZG %.0f 向下离开段 C [%s] 创出当时新低; "
                "C/A 绿柱面积比 %.2f (<%s) 趋势背驰" % (
                    _pv(pv, levels, merged, disp), pv["zd"], pv["zg"],
                    _lv(levels[pt["c_idx"]], merged, disp), pt["ratio"], BEICHI_RATIO))
        return (
            "上升中枢链 [%s] ZD %.0f/ZG %.0f 向上离开段 C [%s] 创出当时新高; "
            "C/A 红柱面积比 %.2f (<%s) 顶背驰" % (
                _pv(pv, levels, merged, disp), pv["zd"], pv["zg"],
                _lv(levels[pt["c_idx"]], merged, disp), pt["ratio"], BEICHI_RATIO))
    if t == 2:
        ref = "一买" if not is_sell else "一卖"
        i0 = "b1_idx" if not is_sell else "s1_idx"
        p0 = "b1_price" if not is_sell else "s1_price"
        lvl = levels[pt[i0]]
        end = merged[lvl["e_idx"]]["end"]
        if not is_sell:
            return (
                "一买(%s @ %.0f) 之后 反弹一段 + 回调一段, 回调低点 %.0f > 一买低点 %.0f (不创新低)"
                % (disp[end], pt[p0], pt["price"], pt[p0]))
        return (
            "一卖(%s @ %.0f) 之后 回调一段 + 反弹一段, 反弹高点 %.0f < 一卖高点 %.0f (不创新高)"
            % (disp[end], pt[p0], pt["price"], pt[p0]))
    # t == 3
    pv = pivots[pt["pi"]]
    if not is_sell:
        return (
            "中枢 [%s] ZD %.0f/ZG %.0f 被向上离开 [%s] 后, 回抽段 [%s] 低点 %.0f ≥ ZG %.0f (不回中枢)"
            % (_pv(pv, levels, merged, disp), pv["zd"], pv["zg"],
               _lv(levels[pt["leave_idx"]], merged, disp),
               _lv(levels[pt["pull_idx"]], merged, disp), pt["price"], pv["zg"]))
    return (
        "中枢 [%s] ZD %.0f/ZG %.0f 被向下离开 [%s] 后, 回抽段 [%s] 高点 %.0f ≤ ZD %.0f (不回中枢)"
        % (_pv(pv, levels, merged, disp), pv["zd"], pv["zg"],
           _lv(levels[pt["leave_idx"]], merged, disp),
           _lv(levels[pt["pull_idx"]], merged, disp), pt["price"], pv["zd"]))


def _closest(epoch, points, win):
    best = None
    for p in points:
        dt = abs(p["t"] - epoch)
        if dt <= win and (best is None or dt < best[0]):
            best = (dt, p)
    return best[1] if best else None


def link_levels(levels_out):
    """多级别联动: 对每个级别的买卖点填 sub(次级别确认) 与 upper(上级别语境)。

    levels_out: dict TF -> analyze() 输出(其中 buyPoints*/sellPoints* 为含
    _e 内部键的对象列表)。此函数就地修改这些对象的 sub/upper 字段。
    """
    # sub: 大级别一买/一卖 ← 次级别同向趋势背驰
    for tf in SUB_RELATION:
        sub_tf = SUB_RELATION[tf]
        win = LINK_WINDOW[tf]
        sub_b1 = [
            p for mode in ("stroke", "seg")
            for p in levels_out[sub_tf]["_internal"]["buys"][mode] if p["k"] == "一买"
        ]
        sub_s1 = [
            p for mode in ("stroke", "seg")
            for p in levels_out[sub_tf]["_internal"]["sells"][mode] if p["k"] == "一卖"
        ]
        for mode in ("stroke", "seg"):
            for p in levels_out[tf]["_internal"]["buys"][mode]:
                hit = _closest(p["t"], sub_b1, win)
                p["sub"] = (
                    "次级别 %s 一买 %s @ %.0f ✓" % (sub_tf, _fmt_epoch(hit["t"], sub_tf), hit["p"])
                    if hit else "次级别无同向趋势背驰 ✗")
            for p in levels_out[tf]["_internal"]["sells"][mode]:
                hit = _closest(p["t"], sub_s1, win)
                p["sub"] = (
                    "次级别 %s 一卖 %s @ %.0f ✓" % (sub_tf, _fmt_epoch(hit["t"], sub_tf), hit["p"])
                    if hit else "次级别无同向趋势背驰 ✗")
    # upper: 小级别买卖点 是否落在上级别某中枢时间区间内
    for tf in UP_RELATION:
        up_tf = UP_RELATION[tf]
        up_ranges = []
        for mode in ("stroke", "seg"):
            for pv in levels_out[up_tf]["_internal"]["pivots_raw"][mode]:
                up_ranges.append((pv[0], pv[1], "%s中枢" % up_tf))
        for mode in ("stroke", "seg"):
            for p in levels_out[tf]["_internal"]["buys"][mode] + levels_out[tf]["_internal"]["sells"][mode]:
                inside = [r for r in up_ranges if r[0] <= p["t"] <= r[1]]
                if inside:
                    r = inside[0]
                    p["upper"] = "位于上级别 %s [%s~%s] 时间区间内" % (
                        r[2], _fmt_epoch(r[0], up_tf), _fmt_epoch(r[1], up_tf))
                else:
                    p["upper"] = "—"
    # 边界级别兜底: 1H 无次级别, 1D 无上级别
    for tf in levels_out:
        for mode in ("stroke", "seg"):
            for p in (levels_out[tf]["_internal"]["buys"][mode]
                      + levels_out[tf]["_internal"]["sells"][mode]):
                if not p.get("sub"):
                    p["sub"] = ("最小级别(%s), 无次级别可确认" % tf
                                if tf not in SUB_RELATION else "次级别无同向趋势背驰 ✗")
                if not p.get("upper"):
                    p["upper"] = "—"


# --------------------------------------------------------------------------- #
# 7. 单级别分析                                                               #
# --------------------------------------------------------------------------- #
def _live_beichi(bars, epochs, merged, strokes, hist, tf):
    """数据末端: 当前在途笔的抢先背驰状态(实时视角)。

    若最后一笔是创新低(买侧)/新高(卖侧)的同类段, 且其运行面积相对参考A
    低于背驰阈值 → 潜在背驰告警中(用户策略的"告警"实时档)。
    """
    if not strokes:
        return None
    L = strokes[-1]
    is_sell = (L["dir"] == 1)
    side = "卖" if is_sell else "买"
    r0 = merged[L["s_idx"]]["start"]
    r1 = merged[L["e_idx"]]["end"]
    pos = (lambda h: h > 0) if is_sell else (lambda h: h < 0)
    mag = (lambda h: h) if is_sell else (lambda h: -h)
    out = {"side": side, "from": _fmt_epoch(epochs[r0], tf),
           "to": _fmt_epoch(epochs[r1], tf)}
    areas = []
    for st in strokes:
        if st["dir"] != L["dir"]:
            continue
        areas.append((_leg_area(st, hist, merged, pos, mag), st["e_price"]))
    if len(areas) < 2:
        out["note"] = "无前笔同类段参考"
        return out
    A = max(a[0] for a in areas[:-1][-3:])
    run = sum(mag(h) for h in hist[r0 : r1 + 1] if pos(h))
    ratio = run / A if A > 0 else None
    new_low_high = (L["e_price"] < areas[-2][1]) if not is_sell else (L["e_price"] > areas[-2][1])
    out["ref"] = round(A, 0)
    out["run"] = round(run, 0)
    out["ratio"] = round(ratio, 2) if ratio is not None else None
    if not new_low_high:
        out["note"] = "未创新低/新高, 非抢先背驰语境"
    elif ratio is not None and ratio < BEICHI_RATIO:
        out["alert"] = "潜在背驰(运行面积 < 参考A的0.9); 是否已触发入场见抢先列表"
    else:
        out["alert"] = "无潜在背驰(运行面积仍强于参考)"
    return out


def _terminal_edge(bars, epochs, merged, tops, bottoms, strokes, tf, hist=None):
    """数据末端的在途结构摘要(实盘视角)。

    输出 meta.edge: 最后确认分型 / 最后一笔是否在途 / 是否有待确认分型 /
    抢先背驰实时状态(live)。实盘场景: 数据末尾的结构尚未被未来收盘确认,
    直接当"已确认"使用是危险的; 此摘要让调用方知道当前结构到哪一步了。
    """
    n, last_raw = len(bars), len(bars) - 1
    allf = sorted([(i, "T") for i in tops] + [(i, "B") for i in bottoms])
    out = {"lastBar": _fmt_epoch(epochs[last_raw], tf)}
    if allf:
        mi, kind = allf[-1]
        bi, px = (localize_top(mi, merged, bars) if kind == "T"
                  else localize_bottom(mi, merged, bars))
        out["lastFractal"] = "%s分型 @ %s @ %.0f" % (
            "顶" if kind == "T" else "底", _fmt_epoch(epochs[bi], tf), px)
        fx_end = merged[mi]["end"]
    else:
        fx_end = -1
    if strokes:
        st = strokes[-1]
        st_end = merged[st["e_idx"]]["end"]
        open_txt = " (在途, 数据末端可能延伸)" if st_end >= last_raw - 2 else ""
        out["lastStroke"] = "%s笔 [%s ~ %s]%s" % (
            "上升" if st["dir"] == 1 else "下降",
            _fmt_epoch(epochs[merged[st["s_idx"]]["start"]], tf),
            _fmt_epoch(epochs[st_end], tf), open_txt)
    if fx_end >= 0:
        bars_since = last_raw - fx_end
        if bars_since <= 0:
            out["pending"] = "末端分型即最后K线, 其收盘确认尚未到来 → 结构未闭合"
        else:
            out["pending"] = ("最后确认分型后已过 %d 根K线, 可能存在未确认的新分型"
                              "(需下一根收盘确认)" % bars_since)
    else:
        out["pending"] = "无分型数据"
    if hist is not None:
        out["live"] = _live_beichi(bars, epochs, merged, strokes, hist, tf)
    return out


def _macd_step(close, state):
    """在线 MACD(与 macd_series 同一递推, 只依赖当前及之前收盘)。"""
    k_f = 2.0 / (MACD_FAST + 1.0)
    k_s = 2.0 / (MACD_SLOW + 1.0)
    k_d = 2.0 / (MACD_SIGNAL + 1.0)
    ema_f, ema_s, ema_dea = state
    ema_f = close if ema_f is None else close * k_f + ema_f * (1 - k_f)
    ema_s = close if ema_s is None else close * k_s + ema_s * (1 - k_s)
    diff = ema_f - ema_s
    ema_dea = diff if ema_dea is None else diff * k_d + ema_dea * (1 - k_d)
    return (ema_f, ema_s, ema_dea), (diff, ema_dea, diff - ema_dea)


def _frozen_fractal_at(merged, bars, i, limit):
    """合并K线 i 在右邻已冻结时是否为顶/底分型。i 需满足 1 <= i <= len-3。"""
    if i < 1 or i > len(merged) - 3:
        return None
    left, mid, right = merged[i - 1], merged[i], merged[i + 1]
    if (
        mid["high"] > left["high"] and mid["high"] > right["high"]
        and mid["low"] > left["low"] and mid["low"] > right["low"]
        and close_confirm_index(mid, bars, True, limit) is not None
    ):
        return "T"
    if (
        mid["low"] < left["low"] and mid["low"] < right["low"]
        and mid["high"] < left["high"] and mid["high"] < right["high"]
        and close_confirm_index(mid, bars, False, limit) is not None
    ):
        return "B"
    return None


def _collect_causal_points(bars, epochs, tf, a_mode="strongest", pullback_ext=False):
    """逐根 K 线只看 bars[:t+1] 生成买卖点/背驰点(已发出的点不再删除)。

    包含只改最后一根合并K; 分型只在右邻冻结后确认; 笔由已确认分型构建。
    背驰买/卖在弱速率 alert 当根收盘入场。
    a_mode / pullback_ext: 透传给 find_buy/sell_points 与 build_pivots
    (用于 A/B 对照原文口径修正, 默认保持现行行为)。"""
    names_b = {1: "一买", 2: "二买", 3: "三买"}
    names_s = {1: "一卖", 2: "二卖", 3: "三卖"}
    merged = []
    hist = []
    macd_st = (None, None, None)
    seen = set()
    beichi_seen = set()
    buy_s, sell_s, buy_g, sell_g = [], [], [], []
    beichi = []
    tops, bottoms = [], []
    strokes = []
    prev_stroke_sig = -1
    prev_n_strokes = 0
    run_buy = run_sell = 0.0
    ext_buy = ext_sell = None
    r0_buy = r0_sell = None

    disp_cache = []

    def _disp(t):
        while len(disp_cache) <= t:
            disp_cache.append(_fmt_epoch(epochs[len(disp_cache)], tf))
        return disp_cache

    def _key_of(pt, levels, is_sell):
        lvl = levels[pt["level_idx"]]
        e_idx = lvl["e_idx"]
        e_raw, px = (
            localize_bottom(e_idx, merged, bars)
            if not is_sell else localize_top(e_idx, merged, bars)
        )
        k = (names_s if is_sell else names_b)[pt["type"]]
        return (k, epochs[e_raw], is_sell), e_idx, e_raw, px, k

    def _pack(pt, levels, pivots, is_sell, t, e_idx, e_raw, px, k):
        why = _explain(pt, levels, pivots, merged, _disp(t), is_sell)
        obj = {
            "t": epochs[e_raw], "p": round(pt.get("price", px), 2), "k": k,
            "confirmedT": epochs[t], "state": "confirmed",
            "tier": ("明显" if pt.get("ratio", 1) < 0.7 else "临界") if "ratio" in pt else None,
            "why": why, "sub": "", "upper": "", "_e": e_idx,
        }
        if "ratio" in pt:
            obj["ratio"] = round(pt["ratio"], 3)
        return obj

    def _scan(t):
        nonlocal prev_n_strokes
        piv_s = build_pivots(strokes, pullback_ext=pullback_ext)
        for pt in find_buy_points(strokes, piv_s, hist, merged, a_mode=a_mode):
            key, e_idx, e_raw, px, k = _key_of(pt, strokes, False)
            if key in seen:
                continue
            if pt["type"] == 1 and t - e_raw > 8:
                continue  # 一买确认超过 8 根, 已不是该低点的可交易入场
            seen.add(key)
            buy_s.append(_pack(pt, strokes, piv_s, False, t, e_idx, e_raw, px, k))
        for pt in find_sell_points(strokes, piv_s, hist, merged, a_mode=a_mode):
            key, e_idx, e_raw, px, k = _key_of(pt, strokes, True)
            if key in seen:
                continue
            if pt["type"] == 1 and t - e_raw > 8:
                continue
            seen.add(key)
            sell_s.append(_pack(pt, strokes, piv_s, True, t, e_idx, e_raw, px, k))
        if len(strokes) != prev_n_strokes:
            prev_n_strokes = len(strokes)
            segs = build_segments(strokes)
            complete = segs[:-1] if segs else []
            if complete:
                piv_g = build_pivots(complete, pullback_ext=pullback_ext)
                for pt in find_buy_points(complete, piv_g, hist, merged, a_mode=a_mode):
                    key, e_idx, e_raw, px, k = _key_of(pt, complete, False)
                    key = ("g",) + key
                    if key in seen:
                        continue
                    seen.add(key)
                    buy_g.append(_pack(pt, complete, piv_g, False, t, e_idx, e_raw, px, k))
                for pt in find_sell_points(complete, piv_g, hist, merged, a_mode=a_mode):
                    key, e_idx, e_raw, px, k = _key_of(pt, complete, True)
                    key = ("g",) + key
                    if key in seen:
                        continue
                    seen.add(key)
                    sell_g.append(_pack(pt, complete, piv_g, True, t, e_idx, e_raw, px, k))

    def _beichi_tick(t, is_sell):
        nonlocal run_buy, run_sell, ext_buy, ext_sell, r0_buy, r0_sell
        if not strokes:
            return
        want = 1 if is_sell else -1
        L = strokes[-1]
        pos = (lambda h: h > 0) if is_sell else (lambda h: h < 0)
        mag = (lambda h: h) if is_sell else (lambda h: -h)
        if L["dir"] == want:
            r0 = merged[L["s_idx"]]["start"]
            prevs = [s for s in strokes[:-1] if s["dir"] == want]
        elif L["dir"] == -want:
            r0 = merged[L["e_idx"]]["end"]
            prevs = [s for s in strokes if s["dir"] == want]
        else:
            return
        if not prevs or t < r0:
            return
        key = (is_sell, r0)
        if key in beichi_seen:
            return
        A_src = max(prevs[-3:], key=lambda s: _leg_area(s, hist, merged, pos, mag))
        A = _leg_area(A_src, hist, merged, pos, mag)
        A_len = merged[A_src["e_idx"]]["end"] - merged[A_src["s_idx"]]["start"] + 1
        elapsed = t - r0 + 1
        if elapsed < FAST_MIN_BARS or A <= 0 or A_len <= 0:
            return
        h = hist[t]
        add = mag(h) if pos(h) else 0.0
        if is_sell:
            if r0_sell != r0:
                r0_sell, run_sell = r0, sum(mag(x) for x in hist[r0:t] if pos(x))
                ext_sell = max(bars[i]["high"] for i in range(r0, t)) if t > r0 else bars[t]["high"]
            run_sell += add
            ext_sell = bars[t]["high"] if ext_sell is None else max(ext_sell, bars[t]["high"])
            run, ext, new_ext = run_sell, ext_sell, ext_sell > prevs[-1]["e_price"]
        else:
            if r0_buy != r0:
                r0_buy, run_buy = r0, sum(mag(x) for x in hist[r0:t] if pos(x))
                ext_buy = min(bars[i]["low"] for i in range(r0, t)) if t > r0 else bars[t]["low"]
            run_buy += add
            ext_buy = bars[t]["low"] if ext_buy is None else min(ext_buy, bars[t]["low"])
            run, ext, new_ext = run_buy, ext_buy, ext_buy < prevs[-1]["e_price"]
        if not (new_ext and run < A * BEICHI_RATIO):
            return
        if run >= A * (elapsed / A_len) * FAST_RATE:
            return
        beichi_seen.add(key)
        side = "卖" if is_sell else "买"
        ratio = run / A
        beichi.append({
            "_ri": t, "t": epochs[t], "p": round(bars[t]["close"], 2),
            "k": "背驰%s" % side, "side": side, "trigger": "弱速率",
            "alertT": epochs[t], "confirmedT": epochs[t], "state": "confirmed",
            "ratio": round(ratio, 3),
            "why": "%s侧: 跌/涨势运行面积 %.0f < 参考A %.0f (比 %.2f) 弱速率告警入场"
                   % (side, run, A, ratio),
        })

    for t, bar in enumerate(bars):
        append_merged_bar(merged, bar)
        macd_st, trip = _macd_step(bar["close"], macd_st)
        hist.append(trip[2])
        n_m = len(merged)
        tops = [i for i in tops if i <= n_m - 3]
        bottoms = [i for i in bottoms if i <= n_m - 3]
        kind = _frozen_fractal_at(merged, bars, n_m - 3, t + 1)
        changed = False
        if kind == "T" and (n_m - 3) not in tops:
            tops.append(n_m - 3)
            tops.sort()
            changed = True
        elif kind == "B" and (n_m - 3) not in bottoms:
            bottoms.append(n_m - 3)
            bottoms.sort()
            changed = True
        if changed:
            ft, fb = enforce_alternation(merged, bars, tops, bottoms)
            tops, bottoms = ft, fb
            strokes = build_strokes(merged, tops, bottoms)
            _scan(t)
        _beichi_tick(t, False)
        _beichi_tick(t, True)

    beichi.sort(key=lambda x: x["t"])
    return buy_s, sell_s, buy_g, sell_g, beichi


def analyze(bars, epochs, tf):
    """对单个周期跑完整链路, 返回可序列化的级别数据 dict(时间均为 epoch 秒)。

    买卖点对象含内部键 _e(合并K线下标, 供 merged 视图克隆时间)。
    """
    merged = process_inclusions(bars)
    tops, bottoms = find_fractals(merged, bars)
    tops, bottoms = enforce_alternation(merged, bars, tops, bottoms)
    strokes = build_strokes(merged, tops, bottoms)
    segments = build_segments(strokes)

    closes = [b["close"] for b in bars]
    diff, dea, hist = macd_series(closes)
    mdiff, mdea, mhist = macd_series([m["close"] for m in merged])

    piv_s = build_pivots(strokes)
    complete = segments[:-1] if segments else []
    piv_g = build_pivots(complete)

    strokes_raw, strokes_mg = _emit_strokes(strokes, merged, bars, epochs)
    segs_raw, segs_mg = _emit_segments(segments, merged, bars, epochs)
    piv_s_raw, piv_s_mg = _emit_pivots(piv_s, strokes, merged, bars, epochs)
    piv_g_raw, piv_g_mg = _emit_pivots(piv_g, complete, merged, bars, epochs)

    # 买卖点/背驰点: 逐根前缀因果生成(不读未来K)。图上的笔/分型/中枢仍是「截至最后一根」的结构。
    b_s_raw, s_s_raw, b_g_raw, s_g_raw, fa_p_raw = _collect_causal_points(bars, epochs, tf)

    def _clone_mg(pts):
        out = []
        for p in pts:
            e_idx = p.get("_e")
            if e_idx is None or e_idx >= len(merged):
                mgt = p["t"]
            else:
                mgt = epochs[merged[e_idx]["start"]]
            q = dict(p)
            q["t"] = mgt
            out.append(q)
        return out

    b_s_mg, s_s_mg = _clone_mg(b_s_raw), _clone_mg(s_s_raw)
    b_g_mg, s_g_mg = _clone_mg(b_g_raw), _clone_mg(s_g_raw)
    fa_a_raw, fa_a_mg = [], []
    fa_p_mg = _clone_mg(fa_p_raw)

    top_raw, bot_raw = _emit_fractals(tops, bottoms, merged, bars, epochs, False)
    top_mg, bot_mg = _emit_fractals(tops, bottoms, merged, bars, epochs, True)

    def cnt(points, names):
        return {i: sum(1 for p in points if p["k"] == names[i]) for i in (1, 2, 3)}

    names_b = {1: "一买", 2: "二买", 3: "三买"}
    names_s = {1: "一卖", 2: "二卖", 3: "三卖"}
    cbs, cbg = cnt(b_s_raw, names_b), cnt(b_g_raw, names_b)
    css, csg = cnt(s_s_raw, names_s), cnt(s_g_raw, names_s)

    level = {
        "meta": {
            "interval": tf,
            "firstDate": _fmt_epoch(epochs[0], tf),
            "lastDate": _fmt_epoch(epochs[-1], tf),
            "nBars": len(bars), "nMerged": len(merged),
            "nTops": len(tops), "nBottoms": len(bottoms),
            "nStrokes": len(strokes), "nSegments": len(segments),
            "nPivotsStroke": len(piv_s), "nPivotsSegment": len(piv_g),
            "nBuy1S": cbs[1], "nBuy2S": cbs[2], "nBuy3S": cbs[3],
            "nBuy1G": cbg[1], "nBuy2G": cbg[2], "nBuy3G": cbg[3],
            "nSell1S": css[1], "nSell2S": css[2], "nSell3S": css[3],
            "nSell1G": csg[1], "nSell2G": csg[2], "nSell3G": csg[3],
            "nBeichiAlert": len(fa_a_raw),
            "nBeichiBuy": sum(1 for o in fa_p_raw if o["side"] == "买"),
            "nBeichiSell": sum(1 for o in fa_p_raw if o["side"] == "卖"),
            "edge": _terminal_edge(bars, epochs, merged, tops, bottoms,
                                   strokes, tf, hist),
        },
        "candles": [[epochs[i], b["open"], b["high"], b["low"], b["close"]]
                    for i, b in enumerate(bars)],
        "volume": [round(b["volume"], 2) for b in bars],
        "mergedCandles": [[epochs[m["start"]], m["open"], m["high"], m["low"], m["close"]]
                          for m in merged],
        "mergedVolume": [round(m["volume"], 2) for m in merged],
        "topMarkers": top_raw, "bottomMarkers": bot_raw,
        "topMergedMarkers": top_mg, "bottomMergedMarkers": bot_mg,
        "strokesRaw": strokes_raw, "strokesMerged": strokes_mg,
        "segmentsRaw": segs_raw, "segmentsMerged": segs_mg,
        "pivotsStrokeRaw": piv_s_raw, "pivotsStrokeMerged": piv_s_mg,
        "pivotsSegmentRaw": piv_g_raw, "pivotsSegmentMerged": piv_g_mg,
        "buyPointsStrokeRaw": b_s_raw, "buyPointsStrokeMerged": b_s_mg,
        "buyPointsSegmentRaw": b_g_raw, "buyPointsSegmentMerged": b_g_mg,
        "sellPointsStrokeRaw": s_s_raw, "sellPointsStrokeMerged": s_s_mg,
        "sellPointsSegmentRaw": s_g_raw, "sellPointsSegmentMerged": s_g_mg,
        "beichiAlertsRaw": fa_a_raw, "beichiAlertsMerged": fa_a_mg,
        "beichiPointsRaw": fa_p_raw, "beichiPointsMerged": fa_p_mg,
        "macdDiff": [round(x, 4) for x in diff],
        "macdDea": [round(x, 4) for x in dea],
        "macdHist": [round(x, 4) for x in hist],
        "macdMergedDiff": [round(x, 4) for x in mdiff],
        "macdMergedDea": [round(x, 4) for x in mdea],
        "macdMergedHist": [round(x, 4) for x in mhist],
    }
    # 供联动使用的内部视图(不入 data.js)
    level["_internal"] = {
        "buys": {"stroke": b_s_raw, "seg": b_g_raw},
        "sells": {"stroke": s_s_raw, "seg": s_g_raw},
        "pivots_raw": {"stroke": piv_s_raw, "seg": piv_g_raw},
    }
    return level


def _emit_fractals(tops, bottoms, merged, bars, epochs, merged_view):
    top, bot = [], []
    for mi in tops:
        m = merged[mi]
        if merged_view:
            top.append({"time": epochs[m["start"]], "price": round(m["high"], 2)})
        else:
            bi, px = localize_top(mi, merged, bars)
            top.append({"time": epochs[bi], "price": round(px, 2)})
    for mi in bottoms:
        m = merged[mi]
        if merged_view:
            bot.append({"time": epochs[m["start"]], "price": round(m["low"], 2)})
        else:
            bi, px = localize_bottom(mi, merged, bars)
            bot.append({"time": epochs[bi], "price": round(px, 2)})
    return top, bot


def _fractal_kind(lvl, side):
    if lvl["dir"] == 1:
        return "B" if side == "s" else "T"
    return "T" if side == "s" else "B"


def _localize(midx, kind, merged, bars):
    if kind == "T":
        return localize_top(midx, merged, bars)[0]
    return localize_bottom(midx, merged, bars)[0]


def _emit_strokes(strokes, merged, bars, epochs):
    raw, mg = [], []
    for st in strokes:
        s_idx, e_idx = st["s_idx"], st["e_idx"]
        if st["dir"] == 1:
            s_raw = _localize(s_idx, "B", merged, bars)
            e_raw = _localize(e_idx, "T", merged, bars)
        else:
            s_raw = _localize(s_idx, "T", merged, bars)
            e_raw = _localize(e_idx, "B", merged, bars)
        raw.append([epochs[s_raw], st["s_price"], epochs[e_raw], st["e_price"], st["dir"]])
        mg.append([epochs[merged[s_idx]["start"]], st["s_price"],
                   epochs[merged[e_idx]["start"]], st["e_price"], st["dir"]])
    return raw, mg


def _emit_segments(segments, merged, bars, epochs):
    raw, mg = [], []
    for sg in segments:
        s_idx, e_idx = sg["s_idx"], sg["e_idx"]
        s_raw = _localize(s_idx, _fractal_kind(sg, "s"), merged, bars)
        e_raw = _localize(e_idx, _fractal_kind(sg, "e"), merged, bars)
        raw.append([epochs[s_raw], sg["s_price"], epochs[e_raw], sg["e_price"], sg["dir"]])
        mg.append([epochs[merged[s_idx]["start"]], sg["s_price"],
                   epochs[merged[e_idx]["start"]], sg["e_price"], sg["dir"]])
    return raw, mg


def _emit_pivots(pivots, levels, merged, bars, epochs):
    raw, mg = [], []
    for p in pivots:
        ls, le = levels[p["level_start"]], levels[p["level_end"]]
        s_raw = _localize(ls["s_idx"], _fractal_kind(ls, "s"), merged, bars)
        e_raw = _localize(le["e_idx"], _fractal_kind(le, "e"), merged, bars)
        raw.append([epochs[s_raw], epochs[e_raw], round(p["zd"], 2), round(p["zg"], 2),
                    round(p["gg"], 2), round(p["dd"], 2)])
        mg.append([epochs[merged[ls["s_idx"]]["start"]], epochs[merged[le["e_idx"]]["start"]],
                   round(p["zd"], 2), round(p["zg"], 2),
                   round(p["gg"], 2), round(p["dd"], 2)])
    return raw, mg


# --------------------------------------------------------------------------- #
# 8. 主流程                                                                    #
# --------------------------------------------------------------------------- #
def main():
    levels_out = {}
    for tf in ("1D", "4H", "1H"):
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
        levels_out[tf] = analyze(bars, epochs, tf)

    link_levels(levels_out)

    # merged 视图: 依据已联动(raw)克隆时间并同步 sub/upper
    _sync_merged(levels_out)

    # 序列化前剥离内部键
    _strip_internal(levels_out)

    data = {
        "meta": {
            "symbol": "BTC/USD",
            "timeframes": ["1D", "4H", "1H"],
            "subRelation": SUB_RELATION,
        },
        "levels": levels_out,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("window.MARKET_DATA = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print("=" * 72)
    for tf in ("1D", "4H", "1H"):
        m = levels_out[tf]["meta"]
        nB = m["nBuy1S"] + m["nBuy2S"] + m["nBuy3S"]
        nBg = m["nBuy1G"] + m["nBuy2G"] + m["nBuy3G"]
        nS = m["nSell1S"] + m["nSell2S"] + m["nSell3S"]
        nSg = m["nSell1G"] + m["nSell2G"] + m["nSell3G"]
        print("  %s: bars %d · 分型 %d/%d · 笔 %d · 线段 %d" % (
            tf, m["nBars"], m["nTops"], m["nBottoms"], m["nStrokes"], m["nSegments"]))
        print("     中枢 笔/线段 %d/%d" % (m["nPivotsStroke"], m["nPivotsSegment"]))
        print("     买点 笔/线段 %d/%d (一买%d·二买%d·三买%d / 一买%d·二买%d·三买%d)" % (
            nB, nBg, m["nBuy1S"], m["nBuy2S"], m["nBuy3S"], m["nBuy1G"], m["nBuy2G"], m["nBuy3G"]))
        print("     卖点 笔/线段 %d/%d (一卖%d·二卖%d·三卖%d / 一卖%d·二卖%d·三卖%d)" % (
            nS, nSg, m["nSell1S"], m["nSell2S"], m["nSell3S"], m["nSell1G"], m["nSell2G"], m["nSell3G"]))
        print("     抢先: 告警%d · 背驰买%d · 背驰卖%d" % (
            m.get("nBeichiAlert", 0), m.get("nBeichiBuy", 0), m.get("nBeichiSell", 0)))
        e = m.get("edge", {})
        if e:
            print("     末端: %s · %s · %s" % (e.get("lastFractal", "—"),
                  e.get("lastStroke", "—"), e.get("pending", "—")))
            lv = e.get("live")
            if lv:
                print("     抢先live: %s侧[%s~%s] 运行%s/参考%s 比%s %s" % (
                    lv.get("side", "?"), lv.get("from", "—"), lv.get("to", "—"),
                    lv.get("run", "?"), lv.get("ref", "?"), lv.get("ratio", "?"),
                    lv.get("note") or lv.get("alert") or ""))
    print("=" * 72)
    print("  输出: %s" % OUT_FILE)


def _sync_merged(levels_out):
    """联动完成后, 把 merged 视图的时间对齐到对应合并K线起点, 并同步 why/sub/upper。"""
    # 需要合并K线信息: 由 raw 对象 _e 定位。此处合并K线 start epoch 无法直接
    # 从输出取回, 故在 analyze 阶段已生成 merged 对象; 这里只同步 sub/upper 与时间。
    for tf in levels_out:
        lvl = levels_out[tf]
        for src, dst in [("buyPointsStrokeRaw", "buyPointsStrokeMerged"),
                         ("buyPointsSegmentRaw", "buyPointsSegmentMerged"),
                         ("sellPointsStrokeRaw", "sellPointsStrokeMerged"),
                         ("sellPointsSegmentRaw", "sellPointsSegmentMerged")]:
            for raw, mg in zip(lvl[src], lvl[dst]):
                mg["sub"], mg["upper"] = raw["sub"], raw["upper"]
                mg["why"] = raw["why"]


def _strip_internal(levels_out):
    for tf in levels_out:
        lvl = levels_out[tf]
        for key in list(lvl.keys()):
            if key == "_internal":
                del lvl[key]
        for p in (lvl.get("buyPointsStrokeRaw", []) + lvl.get("buyPointsStrokeMerged", [])
                  + lvl.get("buyPointsSegmentRaw", []) + lvl.get("buyPointsSegmentMerged", [])
                  + lvl.get("sellPointsStrokeRaw", []) + lvl.get("sellPointsStrokeMerged", [])
                  + lvl.get("sellPointsSegmentRaw", []) + lvl.get("sellPointsSegmentMerged", [])):
            p.pop("_e", None)


if __name__ == "__main__":
    main()
