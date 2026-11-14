#!/usr/bin/env python3
"""四层条件编码 + 关 0（与你的 BTC 历史判断对齐）+ 关 1–2（40 品种逐条验证与去重）。

按你的最新口径：
  - 跨品种力度不看 2% 绝对值，只看「K线形态（实体占整根K线比例、收盘位置）+ 相对波动（相对前10根平均振幅 / ATR）」。
  - 四层：P 形态(互斥) / E 环境(分组) / V 否决(一票否决) / Q 质量(加分)。

用法：python3 scripts/analysis/transfer/conditions.py
输出：reports/transfer/conditions_gate0_2_<date>.json
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
import run_e4 as E4  # noqa: E402
from make_blind_items import candidates as raw_cands, pivots, prep  # noqa: E402
from scale_spec import V4_IDENTITY  # noqa: E402

ROOT = HERE.parents[2]


def eval_conditions(M: dict, i: int) -> dict:
    """在候选点 i 上计算全部 P / E / V / Q 条件（纯因果，仅用 <= i）。"""
    c, o, h, l, v = M["c"], M["o"], M["h"], M["l"], M["v"]
    ma20, ma60, ma120, atr, dif, hist = M["ma20"], M["ma60"], M["ma120"], M["atr"], M["dif"], M["hist"]
    a = atr[i] if (atr[i] == atr[i] and atr[i] > 0) else c[i] * 0.02
    rng = max(1e-12, h[i] - l[i])
    body = c[i] - o[i]
    body_r = body / rng                                 # K线形态：实体占整根K线比例（光头光脚≈1）
    close_pos = (c[i] - l[i]) / rng
    up_wick_r = (h[i] - max(c[i], o[i])) / rng
    rng10 = float(np.mean(h[i - 10:i] - l[i - 10:i]))
    rng_ratio = (h[i] - l[i]) / max(1e-12, rng10)       # 相对波动 1：当根振幅 / 前10根均振幅
    body_atr = body / a                                 # 相对波动 2：当根实体 / ATR14

    # ---- 第 1 层：形态（按优先级互斥）----
    above_prev = (c[i - 20:i - 3] > ma20[i - 20:i - 3]).sum() >= 12
    dip_bars = (c[i - 5:i] <= ma20[i - 5:i]).sum()
    dip_depth = (ma20[i - 5:i] - l[i - 5:i]).max() / a
    up_ma60 = ma60[i] == ma60[i] and ma60[i - 10] == ma60[i - 10] and ma60[i] > ma60[i - 10]
    p_pullback = bool(above_prev and 1 <= dip_bars <= 5 and dip_depth <= 1.8 and up_ma60 and c[i] > ma20[i])

    lows = pivots(l, i + 1, 3, 60, high=False)
    p_div = bool(len(lows) >= 2 and lows[-1] - lows[-2] >= 8 and l[lows[-1]] < l[lows[-2]]
                 and dif[lows[-1]] > dif[lows[-2]] and hist[i] > hist[i - 1])

    ph = pivots(h, i, 3, 90, high=True)
    p_tl = False
    tl_touches = 0
    if len(ph) >= 2 and ph[-1] - ph[-2] >= 12 and h[ph[-1]] < h[ph[-2]]:
        b1, b2 = ph[-2], ph[-1]
        sl = (h[b2] - h[b1]) / (b2 - b1)
        line = h[b2] + sl * (i - b2)
        p_tl = bool(c[i] > line and c[i - 1] <= (h[b2] + sl * (i - 1 - b2)) * 1.005)
        tl_touches = sum(abs(h[j] - (h[b2] + sl * (j - b2))) <= 0.35 * a for j in ph)

    box_w = (h[i - 15:i].max() - l[i - 15:i].min()) / a
    p_box = bool(box_w <= 6.5 and c[i] > ma20[i])
    setup = "P1_回踩收回" if p_pullback else ("P2_底背离" if p_div else ("P3_破趋势线" if p_tl else ("P4_盘整突破" if p_box else "P5_其它")))

    # ---- 第 2 层：环境 ----
    dd120 = (h[i - 120:i + 1].max() - c[i]) / a
    e_drop = "跌得多" if dd120 >= 8 else ("跌得少" if dd120 < 4 else "跌中等")
    if ma120[i] == ma120[i] and ma120[i - 20] == ma120[i - 20]:
        s120 = (ma120[i] - ma120[i - 20]) / (20 * a)
        e_trend = "向上" if (c[i] > ma120[i] and s120 > 0.02) else ("向下" if (c[i] < ma120[i] and s120 < -0.02) else "走平")
    else:
        e_trend = "走平"
    hi250, lo250 = h[i - 250:i + 1].max(), l[i - 250:i + 1].min()
    pos250 = (c[i] - lo250) / max(1e-12, hi250 - lo250)
    e_pos = "低位" if pos250 < 0.30 else ("高位" if pos250 > 0.80 else "中位")

    # ---- 第 3 层：否决（跨品种：不看 2% 绝对值，只看 K 线形态 + 相对波动）----
    # V1 力度不够：三种子情况 + 综合口径（满足"形态好 + 波动够"即不算弱；光头光脚放宽波动要求）
    v1_shape_bad = body_r < 0.50 or close_pos < 0.60            # 有明显上下影线、实体占比小
    v1_vol_small = body_atr < 0.45 and rng_ratio < 1.0          # 和前几根波动差不多且实体不到 0.45 ATR
    marubozu = body_r >= 0.78 and close_pos >= 0.85
    v1_weak = bool((v1_shape_bad or v1_vol_small) and not (marubozu and body_atr >= 0.30))

    risk_atr = (c[i] - l[i]) / a
    upside_atr = (h[i - 60:i].max() - c[i]) / a
    v2_wide_stop = bool(risk_atr > 2.2)
    v2_bad_rr = bool(0 < upside_atr < 1.5 * risk_atr and pos250 > 0.6)
    ap_cur = a / c[i]
    ap_win = atr[i - 250:i + 1] / c[i - 250:i + 1]
    v3_high_vol = bool(np.nanmean(ap_win < ap_cur) > 0.85 or a / np.nanmean(atr[i - 60:i]) > 1.45)
    d120_atr = (c[i] - ma120[i]) / a if ma120[i] == ma120[i] else 0.0
    v4_high_pos = bool(pos250 > 0.85 and d120_atr > 3.0)
    below_cnt = int((c[i - 10:i] < ma20[i - 10:i]).sum())
    v5_early_range = bool(below_cnt < 4 and not p_pullback)
    v6_wick_bear = bool(body <= 0 or up_wick_r > 0.45)
    crosses10 = sum(c[k - 1] <= ma20[k - 1] and c[k] > ma20[k] for k in range(i - 10, i))
    v7_whipsaw = bool(crosses10 >= 1 and (c[i - 5:i] < ma20[i - 5:i]).any())

    # ---- 第 4 层：质量 ----
    q1_dual = bool(ma60[i] == ma60[i] and c[i] > ma60[i] and (c[i - 3:i] <= ma60[i - 3:i]).any())
    r_early = np.mean(h[i - 20:i - 10] - l[i - 20:i - 10])
    q2_narrow = bool(rng10 / max(1e-12, r_early) < 0.75)
    q3_tl3 = bool(tl_touches >= 3)
    av20 = float(np.mean(v[i - 20:i]))
    q5_vol = bool(av20 > 0 and v[i] / av20 >= 1.5)

    return dict(
        setup=setup, e_drop=e_drop, e_trend=e_trend, e_pos=e_pos,
        V1_力度不够=int(v1_weak), V1a_K线不好看=int(v1_shape_bad), V1b_波动太小=int(v1_vol_small),
        V2_止损太宽=int(v2_wide_stop), V2b_盈亏比差=int(v2_bad_rr), V3_波动太大=int(v3_high_vol),
        V4_位置太高=int(v4_high_pos), V5_震荡初期=int(v5_early_range), V6_插针阴线=int(v6_wick_bear),
        V7_刚假突破=int(v7_whipsaw),
        Q1_双均线=int(q1_dual), Q2_盘整收敛=int(q2_narrow), Q3_趋势线3触=int(q3_tl3), Q5_放量=int(q5_vol),
        marubozu=int(marubozu), body_r=round(body_r, 3), body_atr=round(body_atr, 3), rng_ratio=round(rng_ratio, 3),
    )


def gate0_btc() -> dict:
    """关 0：用 BTC 上已有的 184 条审阅记录，检验 V1–V4 与你的文字理由是否一致。"""
    df = pd.read_parquet(ROOT / "data/crypto/BTC_USD_1day.parquet").sort_values("time").reset_index(drop=True)
    M = prep(df)
    tmap = {int(t): i for i, t in enumerate(M["t"])}
    rows = []
    for it in json.loads((ROOT / "annotations-BTC-1D.json").read_text())["items"]:
        i = tmap.get(int(it["features"]["time"]))
        if i is None or i < 255:
            continue
        n = it.get("note") or ""
        rows.append(dict(dec=it["decision"], note=n, **eval_conditions(M, i)))
    d = pd.DataFrame(rows)
    yes = d[d.dec == "yes"]
    no_str = d[(d.dec == "no") & d.note.str.contains("力度|有力|波动太小|小阳线")]
    no_vol = d[(d.dec == "no") & d.note.str.contains("波动太大|波动大|止损|盈亏比")]
    no_pos = d[(d.dec == "no") & d.note.str.contains("高位|位置|过高")]
    return dict(
        V1_只看形态加相对波动=dict(抓到你的力度不够=round(float(no_str["V1_力度不够"].mean()), 3),
                                   误杀你的买点=round(float(yes["V1_力度不够"].mean()), 3),
                                   n_力度不够=len(no_str), n_买=len(yes)),
        V2V3_波动止损=dict(抓到=round(float((no_vol["V2_止损太宽"] | no_vol["V3_波动太大"]).mean()), 3),
                           误杀=round(float((yes["V2_止损太宽"] | yes["V3_波动太大"]).mean()), 3), n=len(no_vol)),
        V4_位置高=dict(抓到=round(float((no_pos["e_pos"] == "高位").mean()), 3),
                       误杀=round(float((yes["e_pos"] == "高位").mean()), 3), n=len(no_pos)),
        买点形态分布=yes["setup"].value_counts().to_dict(),
    )


def gate12_all_assets() -> dict:
    """关 1–2：40 个品种全部候选点上，逐条检验每项条件的单独效果与相关性。"""
    rows = []
    for cls, rels in E4.ASSETS.items():
        for rel in rels:
            f = E4.find_file(rel)
            if f is None:
                continue
            df = pd.read_parquet(f).sort_values("time")
            df = df[df.time >= E4.SINCE].reset_index(drop=True)
            if len(df) < 420:
                continue
            M = prep(df)
            M_eng = engine.prepare(df, V4_IDENTITY)
            cands = sorted(set(sum(raw_cands(M).values(), [])))
            for i in cands:
                oc = engine.outcome(M_eng, i, V4_IDENTITY)
                if oc:
                    rows.append(dict(cls=cls, sym=Path(rel).name, t=int(M["t"][i]), y=oc["y"], r=oc["r_a"],
                                     **eval_conditions(M, i)))
    d = pd.DataFrame(rows)
    conds = [c for c in d.columns if c.startswith(("V", "Q"))]
    cut = pd.Timestamp("2016-01-01").timestamp()
    g1 = {}
    for c in conds:
        diff = d[d[c] == 1].y.mean() - d[d[c] == 0].y.mean()
        per = d.groupby(["sym", c]).y.mean().unstack().dropna()
        want_neg = c.startswith("V")
        consistent = int(((per[1] < per[0]) if want_neg else (per[1] > per[0])).sum())
        d1 = d[d.t < cut]; d2 = d[d.t >= cut]
        diff_pre = d1[d1[c] == 1].y.mean() - d1[d1[c] == 0].y.mean()
        diff_post = d2[d2[c] == 1].y.mean() - d2[d2[c] == 0].y.mean()
        g1[c] = dict(触发比例=round(float(d[c].mean()), 3),
                     触发时好买点率=round(float(d[d[c] == 1].y.mean()), 3),
                     未触发好买点率=round(float(d[d[c] == 0].y.mean()), 3),
                     差值=round(float(diff), 3),
                     前期差值=round(float(diff_pre), 3), 后期差值=round(float(diff_post), 3),
                     方向一致品种=f"{consistent}/{len(per)}")
    by_setup = d.groupby("setup").agg(N=("y", "size"), good=("y", "mean")).round(3).to_dict(orient="index")
    by_env = d.groupby(["e_trend", "e_pos"]).agg(N=("y", "size"), good=("y", "mean")).round(3).reset_index().to_dict("records")
    corr = d[conds].corr().round(2)
    high_corr = [(a, b, float(corr.loc[a, b])) for idx, a in enumerate(conds) for b in conds[idx + 1:]
                 if abs(corr.loc[a, b]) >= 0.4]
    return dict(n_candidates=len(d), by_setup=by_setup, by_env=by_env, gate1=g1, high_corr_pairs=high_corr)


def main() -> None:
    g0 = gate0_btc()
    print("== 关 0（BTC 历史判断对齐）==")
    print(json.dumps(g0, ensure_ascii=False, indent=1))
    g12 = gate12_all_assets()
    print("\n== 关 1–2（40 品种候选点 N=%d）==" % g12["n_candidates"])
    print("按形态:", g12["by_setup"])
    print("按环境:", g12["by_env"])
    for k, v in g12["gate1"].items():
        print(f"  {k:14s} 触发={v['触发比例']:.2f}  差值={v['差值']:+.3f} (前{v['前期差值']:+.3f}/后{v['后期差值']:+.3f})  品种一致={v['方向一致品种']}")
    print("相关≥0.4:", g12["high_corr_pairs"])
    out = ROOT / f"reports/transfer/conditions_gate0_2_{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(dict(gate0=g0, gate12=g12), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
