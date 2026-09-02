#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论完整技术体系 · 多级别联动 — Chan-Theory full pipeline for BTC 1D/4H/1H

在 fractals.py 分型层(包含处理 → 顶底分型 → 收盘确认 → 交替约束)之上, 复刻:
  笔   (strokes)   — 新笔标准: 相邻顶底分型中间 ≥5 根无包含K线(合并K线上间距 ≥4)
                     ★ 双口径各自成链(规范§3⚠C, 不混用): 新笔=信号主链;
                       老笔(间隔≥1根合并K、无价格条件)独立推导 线段→中枢→买卖点
                       对照链, 输出 old* 系列, 前端「笔口径」开关切换显示。
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
from pathlib import Path

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

# 行情数据统一存放在仓库根 data/ 目录下(按资产类别分文件夹)
CRYPTO_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "crypto"
DATA_FILES = {
    "1D": CRYPTO_DATA_DIR / "BTC_USD_1day.parquet",
    "4H": CRYPTO_DATA_DIR / "BTC_USD_4h.parquet",
    "1H": CRYPTO_DATA_DIR / "BTC_USD_1h.parquet",
}
SUB_RELATION = {"1D": "4H", "4H": "1H"}   # 大级别 → 次级别
UP_RELATION = {"4H": "1D", "1H": "4H"}    # 小级别 → 上级别
OUT_FILE = "data.js"

# --------------------------------------------------------------------------- #
# 参数(解释性选择, 均可调)                                                       #
# --------------------------------------------------------------------------- #
MIN_STROKE_SPAN = 5      # 新笔: 顶底中间合并K线 span(含) >= 5 → 间距 >= 4
BEICHI_RATIO = 0.9       # C段柱面积 < A段柱面积 * 0.9 → 判定背驰
TREND_DIV_RATIO = 0.75   # 趋势背驰(一买/一卖, 2026-08 用户定稿): C/A 同向柱【单柱均值】比阈值;
                         # <0.25 强 / <0.5 中 / 其余 弱 三档展示
BUY2_PARENT_Q = 20       # 二买/二卖的父一买/一卖存活窗(Q2, 2026-08 用户定稿: 放宽二买时间窗约束):
                         # 一买信号本身超 Q(8) 根即放弃, 但作为二买的结构前提,
                         # 其端点在 Q2(20) 根内仍可派生二买(回调不破前低的结构确认窗)
FAST_RATE = 0.75         # 抢先信号: 当前段面积积累速率 < 参考A速率*该值 → 记录 alertT(不作为成交)
FAST_MIN_BARS = 3        # 抢先信号: alertT 最早在第几根之后允许记录
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
LINK_WINDOW = {"1D": 72 * 3600, "4H": 24 * 3600}   # 大级别搜次级别一买/一卖的时间窗(秒)


# --------------------------------------------------------------------------- #
# 1. 笔 (strokes)                                                              #
# --------------------------------------------------------------------------- #
def build_strokes(merged, tops, bottoms, old=False):
    """从严格交替的顶底分型序列构建笔。

    merged: process_inclusions 输出(含 start/end 原始索引范围)。
    tops/bottoms: enforce_alternation 输出的合并K线下标(严格交替)。
    old=True: 老笔口径 — 间隔≥1根合并K、无价格条件(仅用于对照展示,
              不进入买卖点/回测管线; 新笔才是信号口径)。

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
            # 老笔口径(old=True): 间隔要求降为 ≥1(即至少隔1根合并K), 无价格条件。
            if i - li >= (1 if old else MIN_STROKE_SPAN - 1):
                up = lkind == "B" and kind == "T"
                ok = (
                    merged[i]["high"] > merged[li]["high"]
                    if up
                    else merged[i]["low"] < merged[li]["low"]
                ) if not old else True
                if ok:
                    strokes.append(_make_stroke(merged, last_end, (i, kind)))
                    last_end = (i, kind)
                # else: 价格条件不成立 → 丢弃新分型
            # else: 距离不足 → 丢弃新分型(视为中继噪声)
    return strokes


def build_strokes_resume(merged, tops, bottoms, old):
    """build_strokes 全量回退的加速版(因果管线专用)。

    与上次输入快照做值级前缀比较: 若旧输入是新输入的严格前缀(分型序列
    因果追加), 则复用分叉点之前已闭合的笔(丢弃最后一笔 —— 其终点可能被
    尾部延伸改写), 从末笔起点重放其后所有分型。决策只依赖历史(因果),
    重放结果与全量重建一致; 任何不一致返回 None 回落真全量。"""
    c = _strokes_fb_cache
    pf, pb, ps = c.get("ft"), c.get("fb"), c.get("strokes")
    if not ps or pf is None or pb is None:
        return None
    if len(pf) > len(tops) or tops[:len(pf)] != pf:
        return None
    if len(pb) > len(bottoms) or bottoms[:len(pb)] != pb:
        return None
    base = [dict(s) for s in ps[:-1]]
    if not base:
        return None
    st0 = base[-1]
    last_end = (st0["e_idx"], "T" if st0["dir"] == 1 else "B")
    seq = sorted([(i, "T") for i in tops if i > last_end[0]]
                 + [(i, "B") for i in bottoms if i > last_end[0]])
    strokes = base
    for i, kind in seq:
        li, lk = last_end
        if kind == lk:
            # 同性质: 更极端则替换开放端点, 并把上一笔终点向后延伸
            if (kind == "T" and merged[i]["high"] > merged[li]["high"]) or (
                    kind == "B" and merged[i]["low"] < merged[li]["low"]
            ):
                last_end = (i, kind)
                strokes[-1]["e_idx"] = i
                strokes[-1]["e_price"] = (
                    merged[i]["high"] if kind == "T" else merged[i]["low"])
                strokes[-1]["high"] = max(strokes[-1]["s_price"], strokes[-1]["e_price"])
                strokes[-1]["low"] = min(strokes[-1]["s_price"], strokes[-1]["e_price"])
        else:
            # 异类: 距离/价格条件与 build_strokes 同一口径
            if i - li >= (1 if old else MIN_STROKE_SPAN - 1):
                up = lk == "B" and kind == "T"
                ok = (
                    merged[i]["high"] > merged[li]["high"]
                    if up
                    else merged[i]["low"] < merged[li]["low"]
                ) if not old else True
                if ok:
                    strokes.append(_make_stroke(merged, last_end, (i, kind)))
                    last_end = (i, kind)
    return strokes


_strokes_fb_cache = {"ft": None, "fb": None, "strokes": None}


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
    """特征序列法线段划分。每段由奇数笔组成, 方向 = 首笔方向。

    ⚠ 增量复用不可行(2026-08 已验证并回退): 第二种情况(缺口)的终结判定
    具有「追溯性」—— 扫描途中遇到的缺口分型在当次数据下反向确认失败
    (线段延续), 数据变长后同一位置的确认可能成功(线段提前终结于更早处),
    即历史线段边界会随数据增长被修订。因此唯一与逐次全量重扫语义一致的
    实现就是全量重扫本身。曾试验「证据边界(_ev)内的闭合段复用」, 在 1H
    上产生 ±2 段的漂移(缺口分型在 487 处后置确认成功使 479 段提前终结),
    已删除。"""
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
def build_pivots(levels, _cache=None):
    """滑窗取连续3个次级别走势, 三者重叠([ZD,ZG]=[max(低),min(高)]) → 中枢;
    其后走势与[ZD,ZG]重叠则延伸(更新GG/DD与时间范围), 离开则结束。
    levels: 线段或笔(统一接口: s_idx/e_idx/high/low)。

    延伸口径(α): 任一段与[ZD,ZG]无重叠 → 中枢立即结束。
    (β 回拉延伸口径经回测证伪 — 放宽版产生 57 段怪物中枢, 见 tutorial
    第10章; 已于 2026-08 移除, 只保留 α。)

    增量模式(_cache=dict()): levels 只在尾部追加/末尾端点延伸(因果管线的
    保证), 因此「最后一个中枢」之前的一切结果不变。缓存记录已处理到的
    下标, 重入时只从最后一个未封口中枢处继续; 封口中枢直接复用。"""
    if _cache is None or not _cache.get("valid"):
        # 全量路径(原实现)
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

    # ---- 增量路径(仅 α 口径) ----
    pivots = _cache.get("pivots", [])
    done = _cache.get("done", 0)   # 已确认封口的 level 下标(其前结构冻结)
    n = len(levels)
    if n <= done + 2:
        _cache["n"] = n
        return pivots
    # 回退到最后一个未封口中枢之前重扫: 端点延伸可能改写最后一笔,
    # 故最后一个中枢(若其 level_end 触及尾部)作废, 更早的中枢复用。
    start = done
    if pivots and pivots[-1]["level_end"] >= n - 2:
        pivots = pivots[:-1]
    elif pivots:
        start = pivots[-1]["level_end"] + 1
    out = list(pivots)
    i = start
    while i + 2 < n:
        a, b, c = levels[i], levels[i + 1], levels[i + 2]
        zd = max(a["low"], b["low"], c["low"])
        zg = min(a["high"], b["high"], c["high"])
        if zd < zg:
            gg = max(a["high"], b["high"], c["high"])
            dd = min(a["low"], b["low"], c["low"])
            z_end = i + 2
            k = i + 3
            while k < n:
                lvl = levels[k]
                if lvl["low"] <= zg and lvl["high"] >= zd:
                    z_end = k
                    gg = max(gg, lvl["high"])
                    dd = min(dd, lvl["low"])
                    k += 1
                else:
                    break
            pv = {
                "level_start": i,
                "level_end": z_end,
                "zd": zd,
                "zg": zg,
                "gg": gg,
                "dd": dd,
                "s_idx": levels[i]["s_idx"],
                "e_idx": levels[z_end]["e_idx"],
            }
            out.append(pv)
            if z_end < n - 2:      # 已封口(其后还有≥2段且当前段未重叠) → 冻结
                pass
            else:
                break              # 未封口, 下次从这里继续
            i = z_end + 1
        else:
            i += 1
    # 冻结边界: 最后一个中枢若触及序列末端则保持开放; 否则封口到其结束
    sealed = out[-1]["level_end"] + 1 if (out and out[-1]["level_end"] < n - 2) else (
        out[-1]["level_start"] if out else 0)
    _cache.update({"pivots": out, "done": min(sealed, max(0, n - 2)), "n": n})
    return out


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


def _leg_hist_mean(hist, merged, lvl, is_down):
    """段内同方向 MACD 柱的【单柱均值】= 同向柱总面积 / 同向柱根数。

    2026-08 口径修订: 一买/一卖的力度判据从「总面积比」改为「单柱均值比」,
    消除比较段因K线根数不同带来的面积偏置(用户定稿口径)。"""
    s = merged[lvl["s_idx"]]["start"]
    e = merged[lvl["e_idx"]]["end"]
    tot = 0.0
    cnt = 0
    for h in hist[s:e + 1]:
        if is_down:
            if h < 0:
                tot -= h
                cnt += 1
        elif h > 0:
            tot += h
            cnt += 1
    return (tot / cnt if cnt else 0.0), cnt


def _div_strength(ratio):
    """背离强度分档(用户定稿): <0.25 强 / <0.5 中 / 其余 弱。"""
    if ratio < 0.25:
        return "强"
    if ratio < 0.5:
        return "中"
    return "弱"


def find_buy_points(levels, pivots, hist, merged, min_ratio=BEICHI_RATIO,
                    tail=None):
    """一买/二买/三买。levels: 线段或笔; pivots: 中枢; hist: 原始K线MACD柱。

    一买: 下跌中枢链(相邻中枢核心区间无重叠, 原文口径)的向下离开段 出现
          同向 MACD 柱【单柱均值】背驰。
          2026-08 原文严格口径修订:
            ① 相邻前中枢下沿须高于当前上沿(pivots[pi-1].zd > cur.zg),
               即两中枢核心区间无重叠 —— 趋势中前后中枢不能有任何重叠;
            ② C 段 = 中枢内(含其后一根单元)向下跌破【A 段低点】的最低向下笔,
               跌破进入段低点即视为向下离开段的计算化判据;
            ③ 力度 = 同向 MACD 柱单柱均值(总面积/同向柱根数, 消除段长偏差),
               C/A < TREND_DIV_RATIO(0.75) 判背驰, 按 0.25/0.5 分 强/中/弱。
    二买: 一买后 反弹一段 + 回调一段, 回调低点不破一买低点。
    三买: 中枢被向上离开后, 回抽段低点 ≥ ZG(不回中枢)。
    tail: 只考察最后 N 个中枢(增量优化)。因果管线下老中枢的信号已在更早
          的扫描中发出(seen 去重), 且 levels 冻结部分不会产生新候选;
          None = 全量(兼容独立调用)。链构建始终用完整 pivots 列表。
    """
    raw_range, green_area, _ = _areas(levels, pivots, hist, merged)
    buys = []
    n = len(levels)
    pivot_ids = range(len(pivots)) if tail is None else range(max(0, len(pivots) - tail), len(pivots))

    # ---- 一买(2026-08 原文严格口径, 见函数 docstring ①②③) ----
    for pi in pivot_ids:
        cur = pivots[pi]
        if pi == 0 or pivots[pi - 1]["zd"] <= cur["zg"]:
            continue  # 相邻中枢核心区间无重叠(原文: 趋势中前后中枢不能有任何重叠)
        si = cur["level_start"] - 1
        if si < 0 or levels[si]["dir"] != -1:
            continue
        A, a_idx = levels[si], si
        # C 段候选: 中枢内及其后一根单元中、跌破 A 段低点的最低向下笔
        c_idx, C = None, None
        for k in range(cur["level_start"], min(cur["level_end"] + 2, n)):
            lv = levels[k]
            if lv["dir"] == -1 and lv["e_price"] < A["e_price"] and \
                    (C is None or lv["e_price"] < C["e_price"]):
                C, c_idx = lv, k
        if C is None:
            continue   # 未跌破 A 段低点 → 不存在向下离开段, 不构成一买
        m_a, _ = _leg_hist_mean(hist, merged, A, True)
        m_c, n_c = _leg_hist_mean(hist, merged, C, True)
        if m_a <= 0 or n_c == 0 or m_c >= m_a * TREND_DIV_RATIO:
            continue   # 无同向柱可比 / 动能未衰竭
        ratio = m_c / m_a
        a_area, c_area = green_area(A), green_area(C)   # 总面积保留供教程/回测引用
        buys.append(
            {
                "type": 1, "level_idx": c_idx, "price": C["e_price"],
                "pi": pi, "a_idx": a_idx, "c_idx": c_idx,
                "a_area": a_area, "c_area": c_area,
                "ratio": ratio, "strength": _div_strength(ratio),
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
    for pi in pivot_ids:
        cur = pivots[pi]
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
                     tail=None):
    """一卖/二卖/三卖。与 find_buy_points 完全对称。

    一卖: 上升中枢链(前中枢更低)的向上离开段 出现 MACD 红柱面积背驰。
          后续若出现更高中枢, 已发出的一卖保留(失败点计入回测, 不事后删除)。
          A段 = 进入当前中枢的同向上涨段(原文口径)。
    二卖: 一卖后 回调一段 + 反弹一段, 反弹高点不破一卖高点。
    三卖: 中枢被向下离开后, 回抽段高点 ≤ ZD(不回中枢)。
    tail: 同 find_buy_points(增量优化, 只考察最后 N 个中枢)。
    """
    _, _, red_area = _areas(levels, pivots, hist, merged)
    sells = []
    n = len(levels)
    pivot_ids = range(len(pivots)) if tail is None else range(max(0, len(pivots) - tail), len(pivots))

    # ---- 一卖(2026-08 原文严格口径, 与一买对称: 升破 A 段高点 + 单柱均值背驰) ----
    for pi in pivot_ids:
        cur = pivots[pi]
        if pi == 0 or pivots[pi - 1]["zg"] >= cur["zd"]:
            continue  # 相邻中枢核心区间无重叠(原文: 趋势中前后中枢不能有任何重叠)
        si = cur["level_start"] - 1
        if si < 0 or levels[si]["dir"] != 1:
            continue
        A, a_idx = levels[si], si
        # C 段候选: 中枢内及其后一根单元中、升破 A 段高点的最高向上笔
        c_idx, C = None, None
        for k in range(cur["level_start"], min(cur["level_end"] + 2, n)):
            lv = levels[k]
            if lv["dir"] == 1 and lv["e_price"] > A["e_price"] and \
                    (C is None or lv["e_price"] > C["e_price"]):
                C, c_idx = lv, k
        if C is None:
            continue   # 未升破 A 段高点 → 不存在向上离开段, 不构成一卖
        m_a, _ = _leg_hist_mean(hist, merged, A, False)
        m_c, n_c = _leg_hist_mean(hist, merged, C, False)
        if m_a <= 0 or n_c == 0 or m_c >= m_a * TREND_DIV_RATIO:
            continue   # 无同向柱可比 / 动能未衰竭
        ratio = m_c / m_a
        a_area, c_area = red_area(A), red_area(C)
        sells.append(
            {
                "type": 1, "level_idx": c_idx, "price": C["e_price"],
                "pi": pi, "a_idx": a_idx, "c_idx": c_idx,
                "a_area": a_area, "c_area": c_area,
                "ratio": ratio, "strength": _div_strength(ratio),
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
    for pi in pivot_ids:
        cur = pivots[pi]
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
# 5b. 盘整背驰 (规范 §8.4, 原文第25课) — TODO P4                                #
# --------------------------------------------------------------------------- #
def find_divergence_in_pivot(levels, pivots, hist, merged,
                             min_ratio=BEICHI_RATIO, tail=None):
    """盘整背驰: 单中枢语境下, 离开段 C 与进入段 A 的同向力度比较。

    判据(规范 §8.4):
      - A = 进入中枢的同向段(levels[P.level_start - 1]);
      - C = 中枢结束后的第一个同向离开段;
      - C 须真离开: 买侧 e_price < DD(P), 卖侧 e_price > GG(P)
        (在中枢区间内的往返只是震荡, 不判);
      - 力度衰竭: area(C) < min_ratio * area(A)。
    scope="leave_only": 只看中枢结束后的第一个同向段(默认);
    返回点对象 k 由调用方命名(盘整背驰买/卖)。"""
    raw_range, green_area, red_area = _areas(levels, pivots, hist, merged)
    pts = []
    n = len(levels)
    ids = range(len(pivots)) if tail is None else range(max(0, len(pivots) - tail), len(pivots))
    for pi in ids:
        P = pivots[pi]
        ai = P["level_start"] - 1
        if ai < 0:
            continue
        A = levels[ai]
        # C = 中枢结束后的第一个与 A 同向的段
        ci = P["level_end"] + 1
        while ci < n and levels[ci]["dir"] != A["dir"]:
            ci += 1
        if ci >= n:
            continue
        C = levels[ci]
        if A["dir"] == -1:
            # 买侧: 向下离开须破 DD
            if C["e_price"] >= P["dd"]:
                continue
            a_area, c_area = green_area(A), green_area(C)
            side = "买"
        else:
            # 卖侧: 向上离开须破 GG
            if C["e_price"] <= P["gg"]:
                continue
            a_area, c_area = red_area(A), red_area(C)
            side = "卖"
        if a_area <= 0 or c_area >= a_area * min_ratio:
            continue
        pts.append({
            "type": "panbei", "side": side, "level_idx": ci, "price": C["e_price"],
            "pi": pi, "a_idx": ai, "c_idx": ci,
            "a_area": a_area, "c_area": c_area, "ratio": c_area / a_area,
            "k": "盘整背驰" + side,
        })
    return pts


# --------------------------------------------------------------------------- #
# 6. 产生说明(why) + 多级别联动(sub/upper)                                      #
# --------------------------------------------------------------------------- #
def _leg_area(L, hist, merged, pos, mag):
    """一段(笔)的绿/红柱面积合计(买侧绿柱取负为正, 卖侧红柱)。"""
    a0 = merged[L["s_idx"]]["start"]
    a1 = merged[L["e_idx"]]["end"]
    return sum(mag(h) for h in hist[a0 : a1 + 1] if pos(h))


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
    if t == "panbei":
        pv = pivots[pt["pi"]]
        if not is_sell:
            return (
                "中枢 [%s] ZD %.0f/ZG %.0f 向下离开段 C [%s] 跌破 DD %.0f; "
                "C/A 绿柱面积比 %.2f (<%s) 盘整背驰" % (
                    _pv(pv, levels, merged, disp), pv["zd"], pv["zg"],
                    _lv(levels[pt["c_idx"]], merged, disp), pv["dd"],
                    pt["ratio"], BEICHI_RATIO))
        return (
            "中枢 [%s] ZD %.0f/ZG %.0f 向上离开段 C [%s] 升破 GG %.0f; "
            "C/A 红柱面积比 %.2f (<%s) 盘整背驰" % (
                _pv(pv, levels, merged, disp), pv["zd"], pv["zg"],
                _lv(levels[pt["c_idx"]], merged, disp), pv["gg"],
                pt["ratio"], BEICHI_RATIO))
    if t == 1:
        pv = pivots[pt["pi"]]
        A = levels[pt["a_idx"]]
        st = pt.get("strength", "弱")
        if not is_sell:
            return (
                "下跌中枢链 [%s] ZD %.0f/ZG %.0f: C 段 [%s] 跌破 A 段 [%s] 低点 %.0f 确认离开; "
                "C/A 单柱均值比 %.2f (<%.2f) 趋势背驰·%s" % (
                    _pv(pv, levels, merged, disp), pv["zd"], pv["zg"],
                    _lv(levels[pt["c_idx"]], merged, disp), _lv(A, merged, disp),
                    A["e_price"], pt["ratio"], TREND_DIV_RATIO, st))
        return (
            "上升中枢链 [%s] ZD %.0f/ZG %.0f: C 段 [%s] 升破 A 段 [%s] 高点 %.0f 确认离开; "
            "C/A 单柱均值比 %.2f (<%.2f) 顶背驰·%s" % (
                _pv(pv, levels, merged, disp), pv["zd"], pv["zg"],
                _lv(levels[pt["c_idx"]], merged, disp), _lv(A, merged, disp),
                A["e_price"], pt["ratio"], TREND_DIV_RATIO, st))
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
        # 盘整背驰点(次级别): 二买/二卖的联动判据(规范§10.3)
        sub_panbei_buy = [
            p for p in levels_out[sub_tf]["_internal"].get("panbei_raw", [])
            if p["k"] == "盘整背驰买"
        ]
        sub_panbei_sell = [
            p for p in levels_out[sub_tf]["_internal"].get("panbei_raw", [])
            if p["k"] == "盘整背驰卖"
        ]
        for mode in ("stroke", "seg"):
            for p in levels_out[tf]["_internal"]["buys"][mode]:
                if p["k"] == "二买":
                    # 规范§14.3/10.3: 二买的次级别对应物是盘整背驰(第25课),
                    # 非次级别一买。先找盘背, 找不到再退化标 ✗。
                    hit = _closest(p["t"], sub_panbei_buy, win)
                    p["sub"] = (
                        "次级别 %s 盘整背驰买 %s @ %.0f ✓" % (
                            sub_tf, _fmt_epoch(hit["t"], sub_tf), hit["p"])
                        if hit else "次级别无盘整背驰 ✗")
                else:
                    hit = _closest(p["t"], sub_b1, win)
                    p["sub"] = (
                        "次级别 %s 一买 %s @ %.0f ✓" % (sub_tf, _fmt_epoch(hit["t"], sub_tf), hit["p"])
                        if hit else "次级别无同向趋势背驰 ✗")
            for p in levels_out[tf]["_internal"]["sells"][mode]:
                if p["k"] == "二卖":
                    hit = _closest(p["t"], sub_panbei_sell, win)
                    p["sub"] = (
                        "次级别 %s 盘整背驰卖 %s @ %.0f ✓" % (
                            sub_tf, _fmt_epoch(hit["t"], sub_tf), hit["p"])
                        if hit else "次级别无盘整背驰 ✗")
                else:
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


def _enforce_cached(merged, bars, tops, bottoms, _state={}):
    """enforce_alternation 的 memoize + 增量归并版(增量优化)。

    前提: 合并只改队尾, 已确认分型(i <= n_m-3)的合并区间 [start,end] 冻结,
    其 localize 极值价格不变 → 每个分型的 val 只算一次。
    归并增量: tops/bottoms 各自有序(主循环 append 后 sort), 与上次输出只在
    尾部不同 → 只重放「上次末尾之后」的新增分型。输出列表 rt/rb/outl 在
    _state 内跨调用维护、就地尾部更新, 每事件只花 O(重放尾) 而非 O(全序列)。
    别名契约: 因果主循环会把返回值直接绑定回 tops/bottoms 并在尾部追加新
    分型后再传入 —— 此处以「内部记录的长度 n_rt/n_rb」为准把追加部分视作
    待重放增量(先截掉再由重放统一补回, 避免重复); 其余形态的不一致走全量。
    键为 id(bars)(单进程内每次 _collect_causal_points 调用前由调用方清理)。"""
    val = _loc_val_cache.setdefault(id(bars), {})
    for i in tops:
        if i not in val:
            _, val[i] = localize_top(i, merged, bars)
    for i in bottoms:
        if i not in val:
            _, val[i] = localize_bottom(i, merged, bars)
    rt0, rb0 = _state.get("rt"), _state.get("rb")
    n_rt, n_rb = _state.get("n_rt", -1), _state.get("n_rb", -1)
    if rt0 is not None and rb0 is not None and n_rt >= 0 and \
            len(tops) >= n_rt and len(bottoms) >= n_rb and \
            (rt0 is tops or n_rt == 0 or rt0[n_rt - 1] == tops[n_rt - 1]) and \
            (rb0 is bottoms or n_rb == 0 or rb0[n_rb - 1] == bottoms[n_rb - 1]):
        rt, rb = rt0, rb0
        # 先取重放窗口(rt/rb 与调用方列表可能是同一对象, 截断会丢掉新追加
        # 的分型), 再截掉已同步前缀之外的部分。
        outl = _state["outl"]
        seen_from = outl[-1][0] if outl else -1
        seq = sorted([(i, "T") for i in tops if i > seen_from]
                     + [(i, "B") for i in bottoms if i > seen_from],
                     key=lambda x: x[0])
        del rt[n_rt:]          # 截掉调用方追加的部分, 由下方重放统一补回
        del rb[n_rb:]
        for i, kind in seq:
            if outl and outl[-1][1] == kind:
                j, _ = outl[-1]
                keep_new = (kind == "B" and val[i] < val[j]) or (
                    kind == "T" and val[i] > val[j])
                if keep_new:
                    outl[-1] = (i, kind)
                    (rb if kind == "B" else rt)[-1] = i
            else:
                outl.append((i, kind))
                (rb if kind == "B" else rt).append(i)
        _state["outl"], _state["rt"], _state["rb"] = outl, rt, rb
        _state["n_rt"], _state["n_rb"] = len(rt), len(rb)
        return rt, rb
    # 全量路径
    seq = sorted([(i, "T") for i in tops] + [(i, "B") for i in bottoms],
                 key=lambda x: x[0])
    outl = []
    rt, rb = [], []
    for i, kind in seq:
        if outl and outl[-1][1] == kind:
            j, _ = outl[-1]
            keep_new = (kind == "B" and val[i] < val[j]) or (
                kind == "T" and val[i] > val[j])
            if keep_new:
                outl[-1] = (i, kind)
                (rb if kind == "B" else rt)[-1] = i
        else:
            outl.append((i, kind))
            (rb if kind == "B" else rt).append(i)
    _state["outl"], _state["rt"], _state["rb"] = outl, rt, rb
    _state["n_rt"], _state["n_rb"] = len(rt), len(rb)
    return rt, rb

_loc_val_cache = {}   # id(bars) -> {merged_idx: 极值价格}


def _strokes_incremental(tops, bottoms):
    """build_strokes 的增量版: 只重算尾部受影响的笔。

    前提: enforce 输出只在尾部变化(新分型追加 / 连续同类替换末个 /
    末分型被更极端者替换 — 实测 1D 数据 760 次纯追加 vs 8 次尾部改写)。
    策略(避免每次 O(n) 排序/复制): state 记录上次序列长度与末元素。
    - 长度 +1 且前缀校验通过(末元素为新增) → 纯追加, 从上末元素单步重放;
    - 长度相同且仅末元素不同 → 尾部替换, 回退一位后重放;
    - 其他 → 回退全量。
    成笔标准由 _strokes_inc_old 决定(与 build_strokes 的 old 参数一致)。"""
    n = len(tops) + len(bottoms)
    prev_strokes, prev_seq = _strokes_inc_state[1], _strokes_inc_state[2]
    if prev_strokes is None or prev_seq is None:
        return None
    plen, plast, pend_anchor = prev_seq
    merged = _strokes_inc_merged[0]
    # 当前序列的末元素(交替序列按位置有序, 末元素 = max(末顶, 末底))
    last_t = tops[-1] if tops else -1
    last_b = bottoms[-1] if bottoms else -1
    cur_last = (last_t, "T") if last_t >= last_b else (last_b, "B")
    # 开放端点 anchor: 上次重放结束时的实际 last_end(可能落后于 plast —
    # 新分型可能因距离/价格不足被丢弃, 不推进开放端点)。
    if n == plen + 1 and cur_last[0] >= plast[0]:
        base_strokes = prev_strokes
        last_end = pend_anchor
        replay_start = min(plast[0], pend_anchor[0])
    elif n == plen and cur_last[0] >= plast[0]:
        # 同长度尾部替换: 旧末分型被更极端者替换(实测频率低)。
        # 安全重放起点 = 最后一笔的「起点」分型(起点不受尾部变化影响):
        # 截掉最后一笔, 从其起点分型之后重放。
        if not prev_strokes:
            return None
        st0 = prev_strokes[-1]
        s_idx = st0["s_idx"]
        s_kind = "B" if st0["dir"] == 1 else "T"   # 起点分型性质
        base_strokes = prev_strokes[:-1]
        last_end = (s_idx, s_kind)
        replay_start = s_idx
    else:
        return None
    out_strokes = [dict(s) for s in base_strokes]
    # 校验上一笔终点 = anchor 对应端点(anchor 是上次实际开放端点, 必与笔终点一致)
    li, lk = last_end
    if out_strokes:
        st = out_strokes[-1]
        exp_e = merged[li]
        ok = ((st["dir"] == 1 and st["e_price"] == exp_e["high"]) or
              (st["dir"] == -1 and st["e_price"] == exp_e["low"]))
        if not ok:
            return None
    else:
        return build_strokes(merged, tops, bottoms, old=_strokes_inc_old[0])
    # 重放 (last_end, cur_last] 之间的分型(局部扫描, 不全排序)
    pend = [(i, "T") for i in tops if last_end[0] < i <= cur_last[0]]
    bend = [(i, "B") for i in bottoms if last_end[0] < i <= cur_last[0]]
    tail_seq = sorted(pend + bend)
    if not tail_seq or tail_seq[-1] != cur_last:
        return None
    for i, kind in tail_seq:
        li, lk = last_end
        if kind == lk:
            if (kind == "T" and merged[i]["high"] > merged[li]["high"]) or (
                    kind == "B" and merged[i]["low"] < merged[li]["low"]):
                last_end = (i, kind)
                if out_strokes:
                    out_strokes[-1]["e_idx"] = i
                    out_strokes[-1]["e_price"] = (
                        merged[i]["high"] if kind == "T" else merged[i]["low"])
                    out_strokes[-1]["high"] = max(out_strokes[-1]["s_price"], out_strokes[-1]["e_price"])
                    out_strokes[-1]["low"] = min(out_strokes[-1]["s_price"], out_strokes[-1]["e_price"])
        else:
            old = _strokes_inc_old[0]
            if i - li >= (1 if old else MIN_STROKE_SPAN - 1):
                up = lk == "B" and kind == "T"
                ok = (
                    merged[i]["high"] > merged[li]["high"]
                    if up
                    else merged[i]["low"] < merged[li]["low"]
                ) if not old else True
                if ok:
                    out_strokes.append(_make_stroke(merged, last_end, (i, kind)))
                    last_end = (i, kind)
    if last_end != cur_last and last_end[0] > cur_last[0]:
        return None   # 重放越过终点 → 回退
    # 注意: last_end 可能停在 cur_last 之前(新分型被丢弃不推进端点) — 合法。
    # 锚点存「末笔终点」而非扫描位置: 校验恒一致; 两次成笔之间被丢弃的
    # 候选分型会在下次重放窗口内重新判定(决策因果确定, 结果与全量重建一致)。
    _strokes_inc_state[0], _strokes_inc_state[1], _strokes_inc_state[2] = \
        None, out_strokes, (n, cur_last, _strokes_end(out_strokes))
    return out_strokes

_strokes_inc_state = [None, None, None]     # [key, strokes, (len, 末元素, anchor=末笔终点)]
_strokes_inc_merged = [None]
_strokes_inc_old = [False]                  # 成笔口径: False=新笔 True=老笔(与 build_strokes old 对应)


def _strokes_end(strokes):
    """末笔的终点 (merged_idx, kind); 无笔时不可调用。"""
    st = strokes[-1]
    return (st["e_idx"], "T" if st["dir"] == 1 else "B")


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


def _collect_causal_points(bars, epochs, tf, stroke_old=False):
    """逐根 K 线只看 bars[:t+1] 生成买卖点/背驰点(已发出的点不再删除)。

    包含只改最后一根合并K; 分型只在右邻冻结后确认; 笔由已确认分型构建。
    背驰买/卖在弱速率 alert 当根收盘入场。
    口径已定稿(2026-08): A段=进入段(原文口径, 回测验证优于 strongest);
    中枢延伸=α 重叠口径(β 回拉延伸经回测证伪已移除)。
    stroke_old: 笔的成笔口径 — False=新笔(信号主链), True=老笔(对照链)。
    两口径各自独立成链(笔→线段→中枢→买卖点), 不混用(规范§3⚠C)。"""
    names_b = {1: "一买", 2: "二买", 3: "三买"}
    names_s = {1: "一卖", 2: "二卖", 3: "三卖"}
    merged = []
    hist = []
    macd_st = (None, None, None)
    seen = set()
    beichi_seen = set()
    buy_s, sell_s, buy_g, sell_g = [], [], [], []
    panbei = []
    beichi = []
    tops, bottoms = [], []
    strokes = []
    prev_stroke_sig = -1
    prev_n_strokes = 0
    piv_cache = {"valid": True}  # 增量缓存
    _strokes_fb_cache.clear()    # 笔续跑缓存(每次因果运行独立)
    _strokes_inc_state[0], _strokes_inc_state[1], _strokes_inc_state[2] = None, None, None
    _strokes_inc_old[0] = stroke_old
    _strokes_inc_merged[0] = merged  # 供增量笔构建访问当前合并序列
    run_buy = run_sell = 0.0
    ext_buy = ext_sell = None
    r0_buy = r0_sell = None
    # ---- 线段级抢先背驰状态(与笔级同一引擎/阈值, 仅级别不同) ----
    segs = []                    # 当前线段列表(笔数变化时在 _scan 内整体重建)
    segs_gen = [0]               # 线段代次: 每次重建 +1, 驱动方向镜像全量重同步
    beichi_sg = []
    panbei_sg = []
    beichi_seen_sg = set()
    sg_by_dir = {1: [], -1: []}  # 线段方向→下标镜像
    sg_dir_of = []
    sg_synced_gen = [-1]
    _seg_area_cache = {}         # id(seg) -> (seg 强引用, 卖侧面积, 买侧面积)
    run_buy_sg = run_sell_sg = 0.0
    ext_buy_sg = ext_sell_sg = None
    r0_buy_sg = r0_sell_sg = None

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
        if pt["type"] == "panbei":
            k = pt["k"]   # 盘整背驰买/卖
        else:
            k = (names_s if is_sell else names_b)[pt["type"]]
        return (k, epochs[e_raw], is_sell), e_idx, e_raw, px, k

    def _pack(pt, levels, pivots, is_sell, t, e_idx, e_raw, px, k):
        why = _explain(pt, levels, pivots, merged, _disp(t), is_sell)
        if pt.get("strength"):
            tier = "背驰" + pt["strength"]          # 一买/一卖: 强/中/弱 三档
        elif "ratio" in pt:
            tier = "明显" if pt.get("ratio", 1) < 0.7 else "临界"
        else:
            tier = None
        obj = {
            "t": epochs[e_raw], "p": round(pt.get("price", px), 2), "k": k,
            "confirmedT": epochs[t], "state": "confirmed",
            "tier": tier,
            "why": why, "sub": "", "upper": "", "_e": e_idx,
        }
        if "ratio" in pt:
            obj["ratio"] = round(pt["ratio"], 3)
        return obj

    def _scan(t):
        nonlocal prev_n_strokes, segs
        # 时效约束(规范§9.1, 工程参数Q=8): 一买/一卖确认超过 Q 根K线后
        # 不再是可交易入场, 放弃信号。统一施加于笔级与线段级、买侧与卖侧。
        def _expired(pt, e_raw):
            return pt["type"] == 1 and t - e_raw > 8

        # 增量优化: 笔只在尾部追加/延伸(因果保证), 中枢用缓存只扫尾部;
        # 买卖点候选也只可能在尾部 2 个中枢 + 末端出现(seen 集合保证不重复)。
        # 统一发射器: 时效预过滤(一买/一卖超 Q 根即弃, 规范§9.1) + 父存活校验
        # (二买/二卖的父一买/一卖若未成立则连带抑制 —— 二买的定义前提是一买成立),
        # 存活的候选去重后打包发出。
        piv_s = build_pivots(strokes, _cache=piv_cache)
        def _emit(cands, levels, pivots, is_sell, dst, prefix=()):
            alive1 = set()
            for pt in cands:
                if pt["type"] == 1:
                    _, _, e_raw0, _, _ = _key_of(pt, levels, is_sell)
                    # 一买信号本身超 Q(8) 根放弃; 但作为二买的结构前提,
                    # 存活窗放宽至 Q2(BUY2_PARENT_Q) 根 —— 回调不破前低的
                    # 结构在更长窗口内仍可被确认(用户定稿: 放宽二买时间窗)
                    if t - e_raw0 <= BUY2_PARENT_Q:
                        alive1.add(pt["level_idx"])
            for pt in cands:
                key, e_idx, e_raw, px, k = _key_of(pt, levels, is_sell)
                full_key = prefix + key
                if full_key in seen:
                    continue
                if pt["type"] == 1:
                    if _expired(pt, e_raw):
                        continue          # 一买信号本身超 Q(8) 根 → 放弃(规范§9.1)
                elif pt["type"] == 2:
                    # 父一买未在 Q2 存活窗内 → 连带抑制(二买前提是一买成立)
                    if (pt["b1_idx"] if not is_sell else pt["s1_idx"]) not in alive1:
                        continue
                seen.add(full_key)
                dst.append(_pack(pt, levels, pivots, is_sell, t, e_idx, e_raw, px, k))

        _emit(find_buy_points(strokes, piv_s, hist, merged, tail=2),
              strokes, piv_s, False, buy_s)
        _emit(find_sell_points(strokes, piv_s, hist, merged, tail=2),
              strokes, piv_s, True, sell_s)
        if len(strokes) != prev_n_strokes:
            prev_n_strokes = len(strokes)
            segs = build_segments(strokes)
            segs_gen[0] += 1           # 线段代次推进 → 方向镜像待重同步
            complete = segs[:-1] if segs else []
            if complete:
                piv_g = build_pivots(complete)
                _emit(find_buy_points(complete, piv_g, hist, merged, tail=2),
                      complete, piv_g, False, buy_g, ("g",))
                _emit(find_sell_points(complete, piv_g, hist, merged, tail=2),
                      complete, piv_g, True, sell_g, ("g",))
                # 线段级盘整背驰(与笔级同一判据, 级别为线段; seen 键加 "g","pb"
                # 前缀避免与笔级盘整背驰在同端点时相互去重)
                for pt in find_divergence_in_pivot(complete, piv_g, hist, merged, tail=2):
                    is_sell = pt["side"] == "卖"
                    key, e_idx, e_raw, px, k = _key_of(pt, complete, is_sell)
                    key = ("g", "pb") + key
                    if key in seen:
                        continue
                    seen.add(key)
                    panbei_sg.append(_pack(pt, complete, piv_g, is_sell, t, e_idx, e_raw, px, k))
        # ---- 盘整背驰(规范§8.4, 原文第25课): 笔级 + 线段级 ----
        for pt in find_divergence_in_pivot(strokes, piv_s, hist, merged, tail=2):
            is_sell = pt["side"] == "卖"
            key, e_idx, e_raw, px, k = _key_of(pt, strokes, is_sell)
            if key in seen:
                continue
            seen.add(key)
            panbei.append(_pack(pt, strokes, piv_s, is_sell, t, e_idx, e_raw, px, k))

    # 同向笔「下标」镜像: 笔只在尾部追加(因果不变式), 维护 dir→[笔下标] 的
    # 增量索引, 免去 _beichi_tick 每根K线对全量笔序列的 O(n) 过滤。
    # 只存下标不存引用, 端点延伸改写字段不影响索引; 方向快照用于尾部校验。
    by_dir = {1: [], -1: []}
    dir_of = []   # 已同步前缀每笔的方向快照

    def _sync_by_dir():
        n = len(strokes)
        ok = n >= len(dir_of)
        if ok and dir_of:
            if strokes[0]["dir"] != dir_of[0]:
                ok = False
            else:
                lo = max(0, len(dir_of) - 64)
                for i in range(lo, len(dir_of)):
                    if strokes[i]["dir"] != dir_of[i]:
                        ok = False
                        break
        if not ok:
            by_dir[1].clear()
            by_dir[-1].clear()
            dir_of.clear()
        for i in range(len(dir_of), n):
            d = strokes[i]["dir"]
            by_dir[d].append(i)
            dir_of.append(d)

    def _beichi_tick(t, is_sell):
        nonlocal run_buy, run_sell, ext_buy, ext_sell, r0_buy, r0_sell
        if not strokes:
            return
        want = 1 if is_sell else -1
        L = strokes[-1]
        pos = (lambda h: h > 0) if is_sell else (lambda h: h < 0)
        mag = (lambda h: h) if is_sell else (lambda h: -h)
        _sync_by_dir()
        idxs = by_dir[want]
        if L["dir"] == want:
            r0 = merged[L["s_idx"]]["start"]
            # prevs ≡ 同向笔中除去最后一笔(strokes[:-1] 的同向过滤)
            if len(idxs) < 2:
                return
            recent_js, plast_j = idxs[-4:-1], idxs[-2]
        elif L["dir"] == -want:
            r0 = merged[L["e_idx"]]["end"]
            # prevs ≡ 全部同向笔(L 异向, 不在 idxs 中)
            if not idxs:
                return
            recent_js, plast_j = idxs[-3:], idxs[-1]
        else:
            return
        if t < r0:
            return
        key = (is_sell, r0)
        if key in beichi_seen:
            return
        prevs_recent = [strokes[j] for j in recent_js]
        A_src = max(prevs_recent, key=lambda s: _leg_area(s, hist, merged, pos, mag))
        A = _leg_area(A_src, hist, merged, pos, mag)
        A_len = merged[A_src["e_idx"]]["end"] - merged[A_src["s_idx"]]["start"] + 1
        elapsed = t - r0 + 1
        if elapsed < FAST_MIN_BARS or A <= 0 or A_len <= 0:
            return
        plast_e_price = strokes[plast_j]["e_price"]
        h = hist[t]
        add = mag(h) if pos(h) else 0.0
        if is_sell:
            if r0_sell != r0:
                r0_sell, run_sell = r0, sum(mag(x) for x in hist[r0:t] if pos(x))
                ext_sell = max(bars[i]["high"] for i in range(r0, t)) if t > r0 else bars[t]["high"]
            run_sell += add
            ext_sell = bars[t]["high"] if ext_sell is None else max(ext_sell, bars[t]["high"])
            run, ext, new_ext = run_sell, ext_sell, ext_sell > plast_e_price
        else:
            if r0_buy != r0:
                r0_buy, run_buy = r0, sum(mag(x) for x in hist[r0:t] if pos(x))
                ext_buy = min(bars[i]["low"] for i in range(r0, t)) if t > r0 else bars[t]["low"]
            run_buy += add
            ext_buy = bars[t]["low"] if ext_buy is None else min(ext_buy, bars[t]["low"])
            run, ext, new_ext = run_buy, ext_buy, ext_buy < plast_e_price
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

    # ---- 线段级抢先背驰: 与笔级 _beichi_tick 同一引擎/阈值, 级别为线段 ----
    def _seg_area(s, is_sell):
        """线段同向 MACD 柱面积, 按 id+强引用缓存(强引用防 id 复用误读)。"""
        key = id(s)
        ent = _seg_area_cache.get(key)
        if ent is not None and ent[0] is s:
            if is_sell and ent[1] is not None:
                return ent[1]
            if not is_sell and ent[2] is not None:
                return ent[2]
        else:
            ent = (s, None, None)
        val = _leg_area(s, hist, merged,
                        (lambda h: h > 0) if is_sell else (lambda h: h < 0),
                        (lambda h: h) if is_sell else (lambda h: -h))
        _seg_area_cache[key] = (s, val if is_sell else ent[1], ent[2] if is_sell else val)
        return val

    def _sync_segs():
        """线段方向镜像。线段会整体重建且可能内部修订(缺口终结的追溯性),
        不能像笔那样做尾部校验的增量同步 —— 用显式代次计数, 每代全量重建。
        O(S) × S 次重建, 总量可忽略。"""
        if sg_synced_gen[0] == segs_gen[0]:
            return
        sg_synced_gen[0] = segs_gen[0]
        sg_by_dir[1].clear()
        sg_by_dir[-1].clear()
        sg_dir_of.clear()
        for i, sg in enumerate(segs):
            sg_by_dir[sg["dir"]].append(i)
            sg_dir_of.append(sg["dir"])

    def _beichi_tick_seg(t, is_sell):
        nonlocal run_buy_sg, run_sell_sg, ext_buy_sg, ext_sell_sg, r0_buy_sg, r0_sell_sg
        if len(segs) < 2:
            return
        want = 1 if is_sell else -1
        L = segs[-1]
        pos = (lambda h: h > 0) if is_sell else (lambda h: h < 0)
        mag = (lambda h: h) if is_sell else (lambda h: -h)
        _sync_segs()
        idxs = sg_by_dir[want]
        if L["dir"] == want:
            r0 = merged[L["s_idx"]]["start"]
            # prevs ≡ 同向线段中除去最后一段
            if len(idxs) < 2:
                return
            recent_js, plast_j = idxs[-4:-1], idxs[-2]
        elif L["dir"] == -want:
            r0 = merged[L["e_idx"]]["end"]
            # prevs ≡ 全部同向线段(L 异向, 不在 idxs 中)
            if not idxs:
                return
            recent_js, plast_j = idxs[-3:], idxs[-1]
        else:
            return
        if t < r0:
            return
        key = (is_sell, r0)
        if key in beichi_seen_sg:
            return
        A_src = max((segs[j] for j in recent_js),
                    key=lambda s: _seg_area(s, is_sell))
        A = _seg_area(A_src, is_sell)
        A_len = merged[A_src["e_idx"]]["end"] - merged[A_src["s_idx"]]["start"] + 1
        elapsed = t - r0 + 1
        if elapsed < FAST_MIN_BARS or A <= 0 or A_len <= 0:
            return
        plast_e_price = segs[plast_j]["e_price"]
        h = hist[t]
        add = mag(h) if pos(h) else 0.0
        if is_sell:
            if r0_sell_sg != r0:
                r0_sell_sg, run_sell_sg = r0, sum(mag(x) for x in hist[r0:t] if pos(x))
                ext_sell_sg = max(bars[i]["high"] for i in range(r0, t)) if t > r0 else bars[t]["high"]
            run_sell_sg += add
            ext_sell_sg = bars[t]["high"] if ext_sell_sg is None else max(ext_sell_sg, bars[t]["high"])
            run, ext, new_ext = run_sell_sg, ext_sell_sg, ext_sell_sg > plast_e_price
        else:
            if r0_buy_sg != r0:
                r0_buy_sg, run_buy_sg = r0, sum(mag(x) for x in hist[r0:t] if pos(x))
                ext_buy_sg = min(bars[i]["low"] for i in range(r0, t)) if t > r0 else bars[t]["low"]
            run_buy_sg += add
            ext_buy_sg = bars[t]["low"] if ext_buy_sg is None else min(ext_buy_sg, bars[t]["low"])
            run, ext, new_ext = run_buy_sg, ext_buy_sg, ext_buy_sg < plast_e_price
        if not (new_ext and run < A * BEICHI_RATIO):
            return
        if run >= A * (elapsed / A_len) * FAST_RATE:
            return
        beichi_seen_sg.add(key)
        side = "卖" if is_sell else "买"
        ratio = run / A
        beichi_sg.append({
            "_ri": t, "t": epochs[t], "p": round(bars[t]["close"], 2),
            "k": "背驰%s" % side, "side": side, "trigger": "弱速率",
            "alertT": epochs[t], "confirmedT": epochs[t], "state": "confirmed",
            "ratio": round(ratio, 3),
            "why": "%s侧·线段级: 跌/涨势运行面积 %.0f < 参考A %.0f (比 %.2f) 弱速率告警入场"
                   % (side, run, A, ratio),
        })

    for t, bar in enumerate(bars):
        append_merged_bar(merged, bar)
        macd_st, trip = _macd_step(bar["close"], macd_st)
        hist.append(trip[2])
        n_m = len(merged)
        # 过滤只会移除「超前」的下标(实际不存在), 常态为空转 → 用 O(1) 上界
        # 检查门控, 避免每根K线全量重建列表。
        if tops and tops[-1] > n_m - 3:
            tops = [i for i in tops if i <= n_m - 3]
        if bottoms and bottoms[-1] > n_m - 3:
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
            # 增量优化: enforce/locate 结果 memoize(分型区间冻结后不变),
            # 笔只从交替序列尾部重建(端点延伸只影响末笔)。
            ft, fb = _enforce_cached(merged, bars, tops, bottoms)
            strokes = _strokes_incremental(ft, fb)
            if strokes is None:
                # 笔增量失败 → 先尝试「分叉点续跑」(复用闭合笔前缀), 再回落全量。
                strokes = build_strokes_resume(merged, ft, fb, stroke_old)
                if strokes is None:
                    strokes = build_strokes(merged, ft, fb, old=stroke_old)
                # 同步续跑/全量缓存(供下一次回退使用)
                _strokes_fb_cache["ft"] = list(ft)
                _strokes_fb_cache["fb"] = list(fb)
                _strokes_fb_cache["strokes"] = [dict(s) for s in strokes]
                # 回退路径也要更新增量状态(否则 state 恒空, 增量永不触发)
                _lt = ft[-1] if ft else -1
                _lb = fb[-1] if fb else -1
                _last = (_lt, "T") if _lt >= _lb else (_lb, "B")
                # anchor: 全量重建后的实际开放端点 = 末笔终点(若有笔)
                _anchor = ((_strokes_end(strokes)), ) if strokes else (_last,)
                _strokes_inc_state[0], _strokes_inc_state[1], _strokes_inc_state[2] = \
                    None, strokes, (len(ft) + len(fb), _last, _anchor[0])
            tops, bottoms = ft, fb
            _scan(t)
        _beichi_tick(t, False)
        _beichi_tick(t, True)
        _beichi_tick_seg(t, False)
        _beichi_tick_seg(t, True)

    beichi.sort(key=lambda x: x["t"])
    beichi_sg.sort(key=lambda x: x["t"])
    return buy_s, sell_s, buy_g, sell_g, beichi, panbei, beichi_sg, panbei_sg


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
    # 老笔对照链(规范§3⚠C: 两口径各自成链, 不混用): 同一分型序列按老笔口径
    # 重建笔, 并独立推导 线段→中枢→买卖点 全链; 前端「笔口径」开关切换显示。
    old_strokes = build_strokes(merged, tops, bottoms, old=True)
    old_strokes_raw, old_strokes_mg = _emit_strokes(old_strokes, merged, bars, epochs)
    old_segments = build_segments(old_strokes)
    old_complete = old_segments[:-1] if old_segments else []
    old_piv_s = build_pivots(old_strokes)
    old_piv_g = build_pivots(old_complete)
    old_segs_raw, old_segs_mg = _emit_segments(old_segments, merged, bars, epochs)
    old_piv_s_raw, old_piv_s_mg = _emit_pivots(old_piv_s, old_strokes, merged, bars, epochs)
    old_piv_g_raw, old_piv_g_mg = _emit_pivots(old_piv_g, old_complete, merged, bars, epochs)
    segs_raw, segs_mg = _emit_segments(segments, merged, bars, epochs)
    piv_s_raw, piv_s_mg = _emit_pivots(piv_s, strokes, merged, bars, epochs)
    piv_g_raw, piv_g_mg = _emit_pivots(piv_g, complete, merged, bars, epochs)

    # 买卖点/背驰点: 逐根前缀因果生成(不读未来K)。图上的笔/分型/中枢仍是「截至最后一根」的结构。
    b_s_raw, s_s_raw, b_g_raw, s_g_raw, fa_p_raw, pb_raw, fa_ps_raw, pbsg_raw = \
        _collect_causal_points(bars, epochs, tf)
    # 老笔口径的买卖点/背驰点: 同一因果引擎、同一确认时序, 仅成笔标准不同。
    ob_s_raw, os_s_raw, ob_g_raw, os_g_raw, o_fa_p_raw, o_pb_raw, o_fa_ps_raw, o_pbsg_raw = \
        _collect_causal_points(bars, epochs, tf, stroke_old=True)

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
    fa_ps_mg, pbsg_mg = _clone_mg(fa_ps_raw), _clone_mg(pbsg_raw)
    ob_s_mg, os_s_mg = _clone_mg(ob_s_raw), _clone_mg(os_s_raw)
    ob_g_mg, os_g_mg = _clone_mg(ob_g_raw), _clone_mg(os_g_raw)
    o_fa_p_mg, o_pb_mg = _clone_mg(o_fa_p_raw), _clone_mg(o_pb_raw)
    o_fa_ps_mg, o_pbsg_mg = _clone_mg(o_fa_ps_raw), _clone_mg(o_pbsg_raw)

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
            "nOldStrokes": len(old_strokes), "nOldSegments": len(old_segments),
            "nOldPivotsStroke": len(old_piv_s), "nOldPivotsSegment": len(old_piv_g),
            "nBuy1S": cbs[1], "nBuy2S": cbs[2], "nBuy3S": cbs[3],
            "nBuy1G": cbg[1], "nBuy2G": cbg[2], "nBuy3G": cbg[3],
            "nSell1S": css[1], "nSell2S": css[2], "nSell3S": css[3],
            "nSell1G": csg[1], "nSell2G": csg[2], "nSell3G": csg[3],
            "nBeichiAlert": len(fa_a_raw),
            "nBeichiBuy": sum(1 for o in fa_p_raw if o["side"] == "买"),
            "nBeichiSell": sum(1 for o in fa_p_raw if o["side"] == "卖"),
            "nPanbeiBuy": sum(1 for o in pb_raw if o["k"] == "盘整背驰买"),
            "nPanbeiSell": sum(1 for o in pb_raw if o["k"] == "盘整背驰卖"),
            "edge": _terminal_edge(bars, epochs, merged, tops, bottoms,
                                   strokes, tf, hist),
            "edgeOld": _terminal_edge(bars, epochs, merged, tops, bottoms,
                                      old_strokes, tf, hist),
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
        "oldStrokesRaw": old_strokes_raw, "oldStrokesMerged": old_strokes_mg,
        "segmentsRaw": segs_raw, "segmentsMerged": segs_mg,
        "oldSegmentsRaw": old_segs_raw, "oldSegmentsMerged": old_segs_mg,
        "pivotsStrokeRaw": piv_s_raw, "pivotsStrokeMerged": piv_s_mg,
        "pivotsSegmentRaw": piv_g_raw, "pivotsSegmentMerged": piv_g_mg,
        "oldPivotsStrokeRaw": old_piv_s_raw, "oldPivotsStrokeMerged": old_piv_s_mg,
        "oldPivotsSegmentRaw": old_piv_g_raw, "oldPivotsSegmentMerged": old_piv_g_mg,
        "buyPointsStrokeRaw": b_s_raw, "buyPointsStrokeMerged": b_s_mg,
        "buyPointsSegmentRaw": b_g_raw, "buyPointsSegmentMerged": b_g_mg,
        "sellPointsStrokeRaw": s_s_raw, "sellPointsStrokeMerged": s_s_mg,
        "sellPointsSegmentRaw": s_g_raw, "sellPointsSegmentMerged": s_g_mg,
        "oldBuyPointsStrokeRaw": ob_s_raw, "oldBuyPointsStrokeMerged": ob_s_mg,
        "oldBuyPointsSegmentRaw": ob_g_raw, "oldBuyPointsSegmentMerged": ob_g_mg,
        "oldSellPointsStrokeRaw": os_s_raw, "oldSellPointsStrokeMerged": os_s_mg,
        "oldSellPointsSegmentRaw": os_g_raw, "oldSellPointsSegmentMerged": os_g_mg,
        "beichiAlertsRaw": fa_a_raw, "beichiAlertsMerged": fa_a_mg,
        "beichiPointsRaw": fa_p_raw, "beichiPointsMerged": fa_p_mg,
        "panbeiPointsRaw": pb_raw, "panbeiPointsMerged": _clone_mg(pb_raw),
        "beichiPointsSegmentRaw": fa_ps_raw, "beichiPointsSegmentMerged": fa_ps_mg,
        "panbeiPointsSegmentRaw": pbsg_raw, "panbeiPointsSegmentMerged": pbsg_mg,
        "oldBeichiAlertsRaw": [], "oldBeichiAlertsMerged": [],
        "oldBeichiPointsRaw": o_fa_p_raw, "oldBeichiPointsMerged": o_fa_p_mg,
        "oldPanbeiPointsRaw": o_pb_raw, "oldPanbeiPointsMerged": o_pb_mg,
        "oldBeichiPointsSegmentRaw": o_fa_ps_raw, "oldBeichiPointsSegmentMerged": o_fa_ps_mg,
        "oldPanbeiPointsSegmentRaw": o_pbsg_raw, "oldPanbeiPointsSegmentMerged": o_pbsg_mg,
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
        "panbei_raw": pb_raw,
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
def main(symbol="BTC", out_file=None):
    data_files = {tf: CRYPTO_DATA_DIR / f"{symbol}_USD_{tfs}.parquet"
                  for tf, tfs in (("1D", "1day"), ("4H", "4h"), ("1H", "1h"))}
    levels_out = {}
    for tf in ("1D", "4H", "1H"):
        df = pd.read_parquet(data_files[tf]).sort_values("time").reset_index(drop=True)
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
            "symbol": "%s/USD" % symbol,
            "timeframes": ["1D", "4H", "1H"],
            "subRelation": SUB_RELATION,
        },
        "levels": levels_out,
    }
    out_path = out_file or OUT_FILE
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("window.MARKET_DATA = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print("=" * 72)
    print("  品种: %s/USD → %s" % (symbol, out_path))
    for tf in ("1D", "4H", "1H"):
        m = levels_out[tf]["meta"]
        nB = m["nBuy1S"] + m["nBuy2S"] + m["nBuy3S"]
        nBg = m["nBuy1G"] + m["nBuy2G"] + m["nBuy3G"]
        nS = m["nSell1S"] + m["nSell2S"] + m["nSell3S"]
        nSg = m["nSell1G"] + m["nSell2G"] + m["nSell3G"]
        print("  %s: bars %d · 分型 %d/%d · 笔 %d · 线段 %d" % (
            tf, m["nBars"], m["nTops"], m["nBottoms"], m["nStrokes"], m["nSegments"]))
        print("     中枢 笔/线段 %d/%d" % (m["nPivotsStroke"], m["nPivotsSegment"]))
        print("     老笔链: 笔 %d · 线段 %d · 中枢 笔/线段 %d/%d (对照口径)" % (
            m.get("nOldStrokes", 0), m.get("nOldSegments", 0),
            m.get("nOldPivotsStroke", 0), m.get("nOldPivotsSegment", 0)))
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
                  + lvl.get("sellPointsSegmentRaw", []) + lvl.get("sellPointsSegmentMerged", [])
                  + lvl.get("oldBuyPointsStrokeRaw", []) + lvl.get("oldBuyPointsStrokeMerged", [])
                  + lvl.get("oldBuyPointsSegmentRaw", []) + lvl.get("oldBuyPointsSegmentMerged", [])
                  + lvl.get("oldSellPointsStrokeRaw", []) + lvl.get("oldSellPointsStrokeMerged", [])
                  + lvl.get("oldSellPointsSegmentRaw", []) + lvl.get("oldSellPointsSegmentMerged", [])):
            p.pop("_e", None)


if __name__ == "__main__":
    import sys
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    outf = ("data_%s.js" % sym.lower()) if len(sys.argv) > 2 or sym != "BTC" else None
    main(sym, out_file=outf)
