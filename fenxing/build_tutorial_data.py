#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论教学案例数据生成 — 从 BTC 本地数据为每个知识点提取典型实例

输出: tutorial_data.js → window.TUTORIAL_CASES
每个案例: {tf, title, candles, merged, overlays:{strokes,segments,pivots,marks,notes,
          featureSeq}, macd:{hist,aRange,cRange}, facts:{...}}
坐标系与 data.js 一致: 时间 = epoch 秒, 价格 = USD。
"""

import json
from pathlib import Path

import pandas as pd

from chanlun import (
    build_pivots, build_segments, build_strokes, enforce_alternation,
    find_buy_points, find_fractals, find_sell_points, localize_bottom,
    localize_top, macd_series, process_inclusions,
)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "crypto"
DATA_FILES = {
    "1D": _DATA_DIR / "BTC_USD_1day.parquet",
    "4H": _DATA_DIR / "BTC_USD_4h.parquet",
    "1H": _DATA_DIR / "BTC_USD_1h.parquet",
}
CASES = {}


# --------------------------------------------------------------------------- #
class Level:
    """单个周期的完整结构缓存。"""

    def __init__(self, tf):
        df = pd.read_parquet(DATA_FILES[tf]).sort_values("time").reset_index(drop=True)
        self.tf = tf
        self.epochs = [int(x) for x in df["time"].tolist()]
        self.bars = [
            {"idx": i, "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
            for i, r in df.iterrows()
        ]
        self.merged = process_inclusions(self.bars)
        tops, bottoms = find_fractals(self.merged, self.bars)
        self.tops, self.bottoms = enforce_alternation(self.merged, self.bars, tops, bottoms)
        self.strokes = build_strokes(self.merged, self.tops, self.bottoms)
        self.segments = build_segments(self.strokes)
        self.complete_segs = self.segments[:-1]
        self.piv_s = build_pivots(self.strokes)
        self.piv_g = build_pivots(self.complete_segs)
        closes = [b["close"] for b in self.bars]
        _, _, self.hist = macd_series(closes)
        self.buys = find_buy_points(self.strokes, self.piv_s, self.hist, self.merged,
                                    a_mode="enter")
        self.sells = find_sell_points(self.strokes, self.piv_s, self.hist, self.merged,
                                      a_mode="enter")

    # ---- helpers ----
    def m_start(self, mi):  # 合并K线下标 -> 起始原始bar epoch
        return self.epochs[self.merged[mi]["start"]]

    def pt_time(self, lvl, side):  # 笔端点 -> 定位后原始bar epoch
        mi = lvl["e_idx"] if side == "e" else lvl["s_idx"]
        kind = ("T" if lvl["dir"] == 1 else "B") if side == "e" else \
               ("T" if lvl["dir"] == -1 else "B")
        bi = localize_top(mi, self.merged, self.bars)[0] if kind == "T" else \
             localize_bottom(mi, self.merged, self.bars)[0]
        return self.epochs[bi]

    def window(self, i0, i1):
        i0, i1 = max(0, i0), min(len(self.bars) - 1, i1)
        return {
            "i0": i0, "i1": i1,
            "candles": [[self.epochs[i], b["open"], b["high"], b["low"], b["close"]]
                        for i, b in enumerate(self.bars) if i0 <= i <= i1],
            "merged": [[self.epochs[m["start"]], m["open"], m["high"], m["low"], m["close"]]
                       for m in self.merged if i0 <= m["start"] and m["end"] <= i1],
        }

    def strokes_overlay(self, i0, i1):
        out = []
        for st in self.strokes:
            t0, t1 = self.m_start(st["s_idx"]), self.m_start(st["e_idx"])
            if i0 <= self.merged[st["s_idx"]]["start"] and self.merged[st["e_idx"]]["end"] <= i1:
                out.append([t0, st["s_price"], t1, st["e_price"], st["dir"]])
        return out

    def fractal_marks(self, i0, i1):
        marks = []
        for mi in self.tops:
            m = self.merged[mi]
            if i0 <= m["start"] and m["end"] <= i1:
                bi, px = localize_top(mi, self.merged, self.bars)
                marks.append({"t": self.epochs[bi], "p": round(px, 2),
                              "shape": "arrowDown", "color": "#e0554d", "text": ""})
        for mi in self.bottoms:
            m = self.merged[mi]
            if i0 <= m["start"] and m["end"] <= i1:
                bi, px = localize_bottom(mi, self.merged, self.bars)
                marks.append({"t": self.epochs[bi], "p": round(px, 2),
                              "shape": "arrowUp", "color": "#3da47b", "text": ""})
        return sorted(marks, key=lambda x: x["t"])

    def iso(self, raw_i):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.epochs[raw_i], timezone.utc).strftime("%Y-%m-%d")


def add_case(cid, tf, title, subtitle, data):
    data["id"], data["tf"], data["title"], data["subtitle"] = cid, tf, title, subtitle
    CASES[cid] = data


def fmt_d(L, epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# ① 包含关系                                                                    #
# --------------------------------------------------------------------------- #
def case_inclusion(L):
    best, best_score = None, 0
    for j, m in enumerate(L.merged):
        span = m["end"] - m["start"] + 1
        if span >= 3:
            ctx = sum(L.merged[k]["end"] - L.merged[k]["start"] + 1
                      for k in range(max(0, j - 2), min(len(L.merged), j + 3)))
            if ctx > best_score:
                best_score, best = ctx, j
    m = L.merged[best]
    i0, i1 = m["start"] - 5, m["end"] + 7
    w = L.window(i0, i1)
    notes = [{"t": L.epochs[m["start"]], "p": m["high"],
              "text": f"合并为1根({m['start']}-{m['end']})"}]
    add_case("inclusion-1", L.tf, "包含关系的连续合并",
             f"{L.iso(m['start'])} 前后: 多根K线因互相包含被合并为一根",
             {"w": w, "notes": notes,
              "facts": {"span": f"{m['start']}–{m['end']}",
                        "n": m["end"] - m["start"] + 1}})


# --------------------------------------------------------------------------- #
# ② 分型                                                                        #
# --------------------------------------------------------------------------- #
def pick_clean_fractal(L, kind):
    """挑形态标准的分型: 中K双向极值幅度大、右邻确认快。"""
    arr = L.tops if kind == "T" else L.bottoms
    loc = localize_top if kind == "T" else localize_bottom
    best, best_score = None, -1
    for mi in arr:
        m = L.merged[mi]
        if mi < 1 or mi > len(L.merged) - 2:
            continue
        lft, rgt = L.merged[mi - 1], L.merged[mi + 1]
        if kind == "T":
            score = min(m["high"] - lft["high"], m["high"] - rgt["high"])
        else:
            score = min(lft["low"] - m["low"], rgt["low"] - m["low"])
        gap = m["end"] - m["start"]
        if score > best_score and gap <= 2:
            best_score, best = score, mi
    return best


def case_fractal(L, kind):
    mi = pick_clean_fractal(L, kind)
    m = L.merged[mi]
    i0, i1 = m["start"] - 6, max(m["end"] + 4, m["end"] + 3)
    w = L.window(i0, i1)
    lft, rgt = L.merged[mi - 1], L.merged[mi + 1]
    name = "顶分型" if kind == "T" else "底分型"
    notes = [
        {"t": L.epochs[max(lft["start"], i0)], "p": lft["high"] if kind == "T" else lft["low"],
         "text": "左K"},
        {"t": L.epochs[m["start"]], "p": m["high"] if kind == "T" else m["low"],
         "text": f"中K({name[0]}K)"},
        {"t": L.epochs[min(rgt["start"], i1)], "p": rgt["high"] if kind == "T" else rgt["low"],
         "text": "右K"},
    ]
    add_case(f"fractal-{kind.lower()}", L.tf, f"标准{name}",
             f"{L.iso(m['start'])}: 中K为三根中的双向极值",
             {"w": w, "notes": notes,
              "facts": {"mid": f"{L.iso(m['start'])}",
                        "hi": round(m["high"], 0), "lo": round(m["low"], 0)}})


# --------------------------------------------------------------------------- #
# ③ 笔                                                                          #
# --------------------------------------------------------------------------- #
def case_stroke(L, direction):
    cand = []
    for k, st in enumerate(L.strokes):
        if st["dir"] != direction:
            continue
        n_merged = st["e_idx"] - st["s_idx"] + 1
        amp = abs(st["e_price"] - st["s_price"]) / st["s_price"]
        if 6 <= n_merged <= 12 and 0.05 <= amp <= 0.35:
            cand.append((k, st))
    if not cand:
        return
    k, st = cand[len(cand) // 2]
    i0 = L.merged[st["s_idx"]]["start"] - 4
    i1 = L.merged[st["e_idx"]]["end"] + 4
    w = L.window(i0, i1)
    nm = "上升笔" if direction == 1 else "下降笔"
    add_case(f"stroke-{'up' if direction==1 else 'down'}", L.tf, f"标准{nm}",
             f"{L.iso(L.merged[st['s_idx']]['start'])} → {L.iso(L.merged[st['e_idx']]['start'])}: "
             f"底分型连到顶分型, 一笔合计≥5根合并K线" if direction == 1 else
             f"{L.iso(L.merged[st['s_idx']]['start'])} → {L.iso(L.merged[st['e_idx']]['start'])}: "
             f"顶分型连到底分型, 一笔合计≥5根合并K线",
             {"w": w,
              "strokes": L.strokes_overlay(i0, i1),
              "notes": [
                  {"t": L.pt_time(st, "s"), "p": st["s_price"], "text": "笔起点"},
                  {"t": L.pt_time(st, "e"), "p": st["e_price"], "text": "笔终点"}],
              "facts": {"bars": st["e_idx"] - st["s_idx"] + 1,
                        "amp": f"{abs(st['e_price']/st['s_price']-1)*100:.1f}%"}},
             )


# --------------------------------------------------------------------------- #
# ④ 线段(含特征序列)                                                             #
# --------------------------------------------------------------------------- #
def case_segment(L, direction):
    cand = [(k, sg) for k, sg in enumerate(L.complete_segs)
            if sg["dir"] == direction and sg["n_strokes"] >= 5 and sg["n_strokes"] <= 9]
    if not cand:
        cand = [(k, sg) for k, sg in enumerate(L.complete_segs) if sg["dir"] == direction]
    if not cand:
        return
    k, sg = cand[len(cand) // 2]
    i0 = L.merged[sg["s_idx"]]["start"] - 3
    i1 = L.merged[sg["e_idx"]]["end"] + 3
    w = L.window(i0, i1)
    # 特征序列: 与线段反向的笔的高低区间
    # 注意: sg.s_idx/e_idx 是 merged 下标, 先换算回 strokes 数组下标
    ks = next(k for k, st in enumerate(L.strokes) if st["s_idx"] == sg["s_idx"])
    ke = next(k for k, st in enumerate(L.strokes) if st["e_idx"] == sg["e_idx"])
    fs = []
    for st in L.strokes[ks:ke + 1]:
        if st["dir"] != sg["dir"]:
            fs.append({"t": L.m_start(st["s_idx"]), "lo": st["low"], "hi": st["high"]})
    nm = "向上线段" if direction == 1 else "向下线段"
    add_case(f"segment-{'up' if direction==1 else 'down'}", L.tf, nm,
             f"{L.iso(L.merged[sg['s_idx']]['start'])} → {L.iso(L.merged[sg['e_idx']]['start'])}: "
             f"{sg['n_strokes']}笔构成的完整线段, 蓝框=特征序列(反向笔的高低区间)",
             {"w": w, "strokes": L.strokes_overlay(i0, i1),
              "segments": [[L.m_start(sg["s_idx"]), sg["s_price"],
                            L.m_start(sg["e_idx"]), sg["e_price"], sg["dir"]]],
              "featureSeq": fs,
              "facts": {"n": sg["n_strokes"]}})


# --------------------------------------------------------------------------- #
# ⑤ 中枢                                                                        #
# --------------------------------------------------------------------------- #
def case_pivot(L, which, pivots, levels):
    cand = []
    for p in pivots:
        n = p["level_end"] - p["level_start"] + 1
        rng = (p["zg"] - p["zd"]) / p["zd"]
        span = (L.merged[levels[p["level_end"]]["e_idx"]]["end"]
                - L.merged[levels[p["level_start"]]["s_idx"]]["start"])
        if which == "pivot-segment":
            ok = 4 <= n and span <= 350
        else:
            ok = 5 <= n <= 8 and 0.02 <= rng <= 0.15
        if ok:
            cand.append(p)
    if not cand:
        cand = sorted(pivots, key=lambda p: L.merged[levels[p["level_end"]]["e_idx"]]["end"]
                      - L.merged[levels[p["level_start"]]["s_idx"]]["start"])
    if not cand:
        return
    if not cand:
        return
    p = cand[len(cand) // 2]
    s_lvl, e_lvl = levels[p["level_start"]], levels[p["level_end"]]
    i0 = L.merged[s_lvl["s_idx"]]["start"] - 5
    i1 = L.merged[e_lvl["e_idx"]]["end"] + 8
    w = L.window(i0, i1)
    piv = [[L.m_start(s_lvl["s_idx"]), L.m_start(e_lvl["e_idx"]),
            round(p["zd"], 2), round(p["zg"], 2)]]
    notes = [
        {"t": (L.m_start(s_lvl["s_idx"]) + L.m_start(e_lvl["e_idx"])) // 2,
         "p": p["zg"], "text": f"ZG={p['zg']:.0f}"},
        {"t": (L.m_start(s_lvl["s_idx"]) + L.m_start(e_lvl["e_idx"])) // 2,
         "p": p["zd"], "text": f"ZD={p['zd']:.0f}"},
    ]
    add_case(which, L.tf, "笔中枢" if which == "pivot-stroke" else "线段中枢",
             f"{fmt_d(L, L.m_start(s_lvl['s_idx']))} ~ {fmt_d(L, L.m_start(e_lvl['e_idx']))}: "
             f"{p['level_end']-p['level_start']+1}段重叠, ZG={p['zg']:.0f}/ZD={p['zd']:.0f}",
             {"w": w, "strokes": L.strokes_overlay(i0, i1), "pivots": piv,
              "notes": notes, "facts": {"zg": round(p["zg"], 0), "zd": round(p["zd"], 0),
                                        "gg": round(p["gg"], 0), "dd": round(p["dd"], 0)}})


# --------------------------------------------------------------------------- #
# ⑥ 趋势(两个同向中枢)                                                           #
# --------------------------------------------------------------------------- #
def case_trend(L, pivots, levels):
    """趋势案例: 相邻两个依次抬升的中枢(B 完全高于 A), 组件由 levels 给出。"""
    cands = []
    for k in range(1, len(pivots)):
        a, b = pivots[k - 1], pivots[k]
        if b["zg"] > a["zg"] and b["zd"] > a["zd"]:
            sa, sb = levels[a["level_start"]], levels[a["level_end"]]
            tb = levels[b["level_start"]], levels[b["level_end"]]
            i0 = L.merged[sa["s_idx"]]["start"] - 5
            i1 = L.merged[tb[1]["e_idx"]]["end"] + 8
            cands.append((b["zg"] / max(a["zg"], 1), i0, i1, a, b))
    if not cands:
        print("  [warn] trend case: no ascending pivot pair")
        return
    cands = [c for c in cands if c[2] - c[1] <= 320]
    if not cands:
        return
    _, i0, i1, a, b = max(cands, key=lambda x: x[0])
    sa, sb = levels[a["level_start"]], levels[a["level_end"]]
    tb = levels[b["level_start"]], levels[b["level_end"]]
    w = L.window(i0, i1)
    piv = [[L.m_start(sa["s_idx"]), L.m_start(sa["e_idx"]),
            round(a["zd"], 2), round(a["zg"], 2)],
           [L.m_start(tb[0]["s_idx"]), L.m_start(tb[1]["e_idx"]),
            round(b["zd"], 2), round(b["zg"], 2)]]
    add_case("trend-1", L.tf, "趋势: 至少两个依次同向的走势中枢",
             f"{L.iso(L.merged[sa['s_idx']]['start'])} 起: 中枢A(ZG {a['zg']:.0f})"
             f" → 中枢B(ZG {b['zg']:.0f}), B完全高于A ⇒ 上涨趋势",
             {"w": w, "pivots": piv, "strokes": L.strokes_overlay(i0, i1),
              "facts": {"aZG": round(a["zg"], 0), "bZG": round(b["zg"], 0)}})


def levels_sa(p, L):
    return L.complete_segs[p["level_start"]]


def levels_sb(p, L):
    return L.complete_segs[p["level_end"]]


# --------------------------------------------------------------------------- #
# ⑦ 背驰                                                                        #
# --------------------------------------------------------------------------- #
def case_beichi(L, is_sell):
    pts = [p for p in (L.sells if is_sell else L.buys) if p["type"] == 1 and p.get("ratio")]
    # 教科书式背驰: 面积比适中(0.25~0.85), C段与A段长度可比(避免超短C段的假极值)
    pts = [p for p in pts if 0.25 <= p["ratio"] <= 0.85
           and (L.merged[L.strokes[p["c_idx"]]["e_idx"]]["end"]
                - L.merged[L.strokes[p["c_idx"]]["s_idx"]]["start"])
           >= 0.4 * (L.merged[L.strokes[p["a_idx"]]["e_idx"]]["end"]
                     - L.merged[L.strokes[p["a_idx"]]["s_idx"]]["start"])]
    if not pts:
        return
    pts.sort(key=lambda p: abs(p["ratio"] - 0.55))
    pt = pts[0]
    C = L.strokes[pt["c_idx"]]
    A = L.strokes[pt["a_idx"]]
    i0 = L.merged[A["s_idx"]]["start"] - 10
    i1 = L.merged[C["e_idx"]]["end"] + 12
    w = L.window(i0, i1)
    hist = [round(L.hist[i], 4) for i in range(w["i0"], w["i1"] + 1)]
    nm = "顶背驰(一卖)" if is_sell else "底背驰(一买)"
    col = "#e0554d" if is_sell else "#3da47b"
    marks = [{"t": L.pt_time(C, "e"), "p": round(pt["price"], 2),
              "shape": "circle", "color": col, "text": nm[:2]}]
    add_case(("beichi-sell" if is_sell else "beichi-buy"), L.tf, nm,
             f"{L.iso(L.merged[C['e_idx']]['start'])}: C段柱面积 {pt['c_area']:.0f} < "
             f"A段 {pt['a_area']:.0f}, 面积比 {pt['ratio']:.2f} (<0.9 判定背驰)",
             {"w": w, "strokes": L.strokes_overlay(i0, i1), "marks": marks,
              "macd": {"hist": hist},
              "aRange": [L.epochs[L.merged[A["s_idx"]]["start"]],
                         L.epochs[L.merged[A["e_idx"]]["end"]]],
              "cRange": [L.epochs[L.merged[C["s_idx"]]["start"]],
                         L.epochs[L.merged[C["e_idx"]]["end"]]],
              "facts": {"a": round(pt["a_area"], 0), "c": round(pt["c_area"], 0),
                        "ratio": round(pt["ratio"], 2)}})


# --------------------------------------------------------------------------- #
# ⑧ 三类买卖点                                                                   #
# --------------------------------------------------------------------------- #
def case_bsp(L, ptype, is_sell):
    pool = [p for p in (L.sells if is_sell else L.buys) if p["type"] == ptype]
    if not pool:
        return
    pt = pool[len(pool) // 3]
    li = pt["level_idx"]
    lvl = L.strokes[li]
    i0 = L.merged[lvl["s_idx"]]["start"] - 40
    i1 = L.merged[lvl["e_idx"]]["end"] + 25
    w = L.window(i0, i1)
    # 相关中枢
    piv = None
    if "pi" in pt:
        pv = L.piv_s[pt["pi"]]
        sl, el = L.strokes[pv["level_start"]], L.strokes[pv["level_end"]]
        piv = [[L.m_start(sl["s_idx"]), L.m_start(el["e_idx"]),
                round(pv["zd"], 2), round(pv["zg"], 2)]]
    names = {1: "一买", 2: "二买", 3: "三买"}
    names_s = {1: "一卖", 2: "二卖", 3: "三卖"}
    nm = (names_s if is_sell else names)[ptype]
    marks = [{"t": L.pt_time(lvl, "e"), "p": round(pt["price"], 2),
              "shape": "circle", "color": "#e0554d" if is_sell else "#3da47b", "text": nm}]
    add_case(f"{'sell' if is_sell else 'buy'}{ptype}", L.tf, nm,
             _bsp_desc(L, pt, ptype, is_sell),
             {"w": w, "strokes": L.strokes_overlay(i0, i1),
              "pivots": piv, "marks": marks, "facts": {}})


def _bsp_desc(L, pt, ptype, is_sell):
    d1 = fmt_d(L, L.pt_time(L.strokes[pt["level_idx"]], "e"))
    if ptype == 1:
        return (f"{d1}: {'上涨' if is_sell else '下跌'}趋势末端 MACD 柱面积"
                f"{'顶' if is_sell else '底'}背驰(C/A={pt.get('ratio', 0):.2f})")
    if ptype == 2:
        return (f"{d1}: 一{'卖' if is_sell else '买'}后次级别回调"
                f"{'反弹高点不创新高' if is_sell else '不创新低'}")
    return (f"{d1}: 向下离开中枢后回抽, 高点 {pt['price']:.0f} 不破 ZD"
            if is_sell else
            f"{d1}: 向上离开中枢后回抽, 低点 {pt['price']:.0f} 不破 ZG")


# --------------------------------------------------------------------------- #
# ⑨ 区间套(多级别联动)                                                           #
# --------------------------------------------------------------------------- #
def case_nest(L4, L1):
    target = None
    for p in L4.buys:
        if p["type"] == 1 and abs(p["price"] - 31404) < 300:
            target = p
            break
    if target is None:
        cands = [p for p in L4.buys if p["type"] == 1]
        target = cands[len(cands) // 2]
    te = L4.pt_time(L4.strokes[target["c_idx"]], "e")
    ei4 = next(i for i, e in enumerate(L4.epochs) if e >= te)
    i0, i1 = ei4 - 70, ei4 + 30
    w4 = L4.window(i0, i1)
    j0 = next(i for i, e in enumerate(L1.epochs) if e >= L4.epochs[w4["i0"]])
    j1 = next((i for i, e in enumerate(L1.epochs) if e > L4.epochs[w4["i1"]]), len(L1.epochs) - 1)
    w1 = L1.window(j0, j1)
    # 1H 在该窗口内的一买
    m1 = []
    for p in L1.buys:
        if p["type"] == 1:
            t = L1.pt_time(L1.strokes[p["c_idx"]], "e")
            if w1["candles"][0][0] <= t <= w1["candles"][-1][0]:
                m1.append({"t": t, "p": round(p["price"], 2), "shape": "circle",
                           "color": "#f0a848", "text": "1H一买"})
    add_case("nest", "4H+1H", "区间套: 大级别一买内部的次级别一买",
             "上图4H一买(白圈)内部, 下图1H同窗口出现自己的趋势背驰(橙圈)",
             {"w": w4, "w2": w1, "marks": [{"t": te, "p": round(target["price"], 2),
                                            "shape": "circle", "color": "#ffffff",
                                            "text": "4H一买"}],
              "marks2": m1, "facts": {"date": fmt_d(L4, te)}})


# --------------------------------------------------------------------------- #
def main():
    print("building levels ...", flush=True)
    L1D = Level("1D")
    L4H = Level("4H")
    L1H = Level("1H")

    case_inclusion(L1D)
    case_fractal(L1D, "T")
    case_fractal(L1D, "B")
    case_stroke(L4H, 1)
    case_stroke(L4H, -1)
    case_segment(L1D, 1)
    case_segment(L1D, -1)
    case_pivot(L4H, "pivot-stroke", L4H.piv_s, L4H.strokes)
    case_pivot(L4H, "pivot-segment", L4H.piv_g, L4H.complete_segs)
    case_trend(L1D, L1D.piv_s, L1D.strokes)
    case_beichi(L4H, False)
    case_beichi(L1D, True)
    for tp in (1, 2, 3):
        case_bsp(L4H, tp, False)
        case_bsp(L4H, tp, True)
    case_nest(L4H, L1H)

    with open("tutorial_data.js", "w", encoding="utf-8") as f:
        f.write("window.TUTORIAL_CASES = ")
        json.dump(CASES, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(f"cases: {len(CASES)}")
    for cid, c in CASES.items():
        n = len(c["w"]["candles"])
        print(f"  {cid:16s} {c['tf']:6s} {n:4d}根  {c['subtitle'][:50]}")


if __name__ == "__main__":
    main()
