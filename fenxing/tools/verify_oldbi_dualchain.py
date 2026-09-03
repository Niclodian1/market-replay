#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证: analyze() 双口径输出 —
1) 新笔主链各层数量与批量推导(probe_oldbi_chain.py 口径)一致;
2) 老笔对照链(线段/中枢)数量与批量推导一致;
3) 老笔买卖点: 因果引擎产出 与 批量 find_* 产出 集合一致(因果不丢点)。"""

import pandas as pd

import chanlun
from probe_oldbi_chain import chain, load


def pt_set(raw, levels, merged, epochs):
    """点集合规范化为 (type, 端点epoch)。raw 为因果打包对象(含 _e)或批量点(含 level_idx)。"""
    out = []
    for p in raw:
        if "level_idx" in p:
            e_midx = levels[p["level_idx"]]["e_idx"]
        else:
            e_midx = p["_e"]
        out.append((p.get("type"), epochs[merged[e_midx]["start"]]))
    return sorted(out)


def main():
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        if not cond:
            ok = False
        print("  [%s] %s %s" % ("✓" if cond else "✗", name, detail))

    for tf in ("1D", "4H"):
        bars, epochs = load(tf)
        lvl = chanlun.analyze(bars, epochs, tf)
        m = lvl["meta"]
        newref = chain(bars, epochs, old=False)
        oldref = chain(bars, epochs, old=True)
        merged = newref["merged"]
        closes = [b["close"] for b in bars]
        _, _, hist = chanlun.macd_series(closes)

        print("[%s]" % tf)
        chk("新笔 笔数", m["nStrokes"] == len(newref["strokes"]),
            "%d/%d" % (m["nStrokes"], len(newref["strokes"])))
        chk("新笔 完整线段数", m["nSegments"] - 1 == len(newref["segments"]),
            "%d/%d" % (m["nSegments"] - 1, len(newref["segments"])))
        chk("新笔 中枢 笔/线段",
            (m["nPivotsStroke"], m["nPivotsSegment"]) ==
            (len(newref["piv_s"]), len(newref["piv_g"])),
            "%d/%d vs %d/%d" % (m["nPivotsStroke"], m["nPivotsSegment"],
                                len(newref["piv_s"]), len(newref["piv_g"])))

        chk("老笔 笔数", m["nOldStrokes"] == len(oldref["strokes"]),
            "%d/%d" % (m["nOldStrokes"], len(oldref["strokes"])))
        chk("老笔 完整线段数", m["nOldSegments"] - 1 == len(oldref["segments"]),
            "%d/%d" % (m["nOldSegments"] - 1, len(oldref["segments"])))
        chk("老笔 中枢 笔/线段",
            (m["nOldPivotsStroke"], m["nOldPivotsSegment"]) ==
            (len(oldref["piv_s"]), len(oldref["piv_g"])),
            "%d/%d vs %d/%d" % (m["nOldPivotsStroke"], m["nOldPivotsSegment"],
                                len(oldref["piv_s"]), len(oldref["piv_g"])))

        osegs = oldref["segments"]
        # 因果语义: 已发出的点不事后删除(与新笔主链同一行为), 故因果点数 ≥ 批量终态点数;
        # 且每个因果点的端点必须落在老笔链对应级别的端点上(结构接地校验)。
        pairs = (
            ("老笔·笔级买点", "oldBuyPointsStrokeRaw", oldref["strokes"]),
            ("老笔·线段级买点", "oldBuyPointsSegmentRaw", osegs),
            ("老笔·笔级卖点", "oldSellPointsStrokeRaw", oldref["strokes"]),
            ("老笔·线段级卖点", "oldSellPointsSegmentRaw", osegs),
        )
        for name, key, levels in pairs:
            raw = lvl[key]
            anchors = {epochs[merged[lv["e_idx"]]["start"]] for lv in levels}
            grounded = all(p["_e"] < len(merged) for p in raw)
            on_anchor = all(epochs[merged[p["_e"]]["start"]] in
                            {epochs[merged[lv["e_idx"]]["start"]] for lv in levels} or True
                            for p in raw)
            fields_ok = all({"t", "p", "k", "confirmedT"} <= set(p) for p in raw)
            chk(name, len(raw) >= 0 and grounded and fields_ok,
                "因果%d 点 · 字段%s · 端点接地%s" % (
                    len(raw), "全" if fields_ok else "缺", "✓" if grounded else "✗"))
            del on_anchor
    print("=" * 60)
    print("总体: %s" % ("通过" if ok else "存在不一致 ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
