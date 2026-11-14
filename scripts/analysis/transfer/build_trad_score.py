#!/usr/bin/env python3
"""检验同时对齐「你的肉眼判断 (is_yes)」与「真实行情正期望 (hit3r / up2atr / ma20_r)」的传统查表评分。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from analyze_blind import load_all_series  # noqa: E402
from make_blind_items import candidates as raw_cands, prep  # noqa: E402
import run_e4 as E4  # noqa: E402
import engine  # noqa: E402
from scale_spec import V4_IDENTITY  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def score_traditional(M: dict, i: int) -> dict:
    """纯因果传统规则查表打分（0~100分，基础分 50 分）。

    完全基于盲测同时验证对「你买不买」和「真涨不涨」双双有效的 5 个硬条件：
      1. 盘整收敛度 (box25_atr 与 dist_hi20_atr)：近25根箱体窄、离近20根高点近（非刚崩盘深坑乱晃）
      2. 均线粘合与趋势 (ma20_60_gap 与 s20/s60/d60)：MA20与MA60粘合收敛，且MA20/MA60走平或向上、站上MA60
      3. 当根阳线力度 (body_r + close_pos + body_atr/rng_ratio)：K线形态饱满 + 相对波动达标（无2%绝对值）
      4. 位置适中不追顶 (pos250 与 d60_atr)：不在250根极高位乖离透支区
      5. 一票否决/重扣项：上根大阴线未反包、长上影插针、逆势均线空头散发、盘整巨震未走完
    """
    c, o, h, l = M["c"], M["o"], M["h"], M["l"]
    ma20, ma60, ma120, atr = M["ma20"], M["ma60"], M["ma120"], M["atr"]
    a = float(atr[i]) if (atr[i] == atr[i] and atr[i] > 0) else float(c[i]) * 0.02
    rng = max(1e-12, float(h[i] - l[i]))

    # --- 维度 1：盘整收敛 vs 震荡没走完 ---
    box25_atr = (float(h[i - 25:i].max()) - float(l[i - 25:i].min())) / a
    dist_hi20_atr = (float(h[i - 20:i].max()) - float(c[i])) / a
    # 收敛良好：近25根振幅 <= 5.5 ATR 且 当根收盘离近20根最高点 <= 1.2 ATR（已在箱体上沿突破）
    cond_box_tight = int(box25_atr <= 5.5 and dist_hi20_atr <= 1.2)
    # 震荡没走完/刚暴跌深坑：近25根振幅 > 7.5 ATR 或 离近20根高点还有 > 2.5 ATR 的阻力墙
    veto_chop_wide = int(box25_atr > 7.5 or dist_hi20_atr > 2.5)

    # --- 维度 2：均线粘合与顺势 vs 逆势压制 ---
    s20 = (ma20[i] - ma20[i - 5]) / (5 * a)
    s60 = (ma60[i] - ma60[i - 10]) / (10 * a) if (ma60[i] == ma60[i] and ma60[i - 10] == ma60[i - 10]) else 0.0
    s120 = (ma120[i] - ma120[i - 20]) / (20 * a) if (ma120[i] == ma120[i] and ma120[i - 20] == ma120[i - 20]) else 0.0
    above60 = bool(ma60[i] == ma60[i] and c[i] >= ma60[i])
    above120 = bool(ma120[i] == ma120[i] and c[i] >= ma120[i])
    ma20_60_gap = abs(ma20[i] - ma60[i]) / a if ma60[i] == ma60[i] else 9.9

    # 双均线粘合共振：MA20 与 MA60 距离 <= 1.2 ATR 且站上 MA60
    cond_ma_squeeze = int(ma20_60_gap <= 1.2 and above60)
    # 顺势：MA20 拐头或走平 (s20 >= -0.03) 且 (站上MA60 或 MA60/MA120 向上)
    cond_trend_up = int(s20 >= -0.03 and (above60 or s60 >= 0.0 or s120 >= 0.02))
    # 逆势压制：在 MA60 下方且 MA60 明显下行 (s60 < -0.04)，或 MA20 与 MA60 距离极大 (> 2.2 ATR) 且在 MA60 下方
    veto_counter_trend = int((not above60 and s60 < -0.04) or (not above60 and ma20_60_gap > 2.2))

    # --- 维度 3：K线形态 + 相对波动（不看2%绝对值） ---
    body = float(c[i] - o[i])
    body_r = body / rng
    close_pos = float(c[i] - l[i]) / rng
    up_wick_r = float(h[i] - c[i]) / rng
    rng10 = float(np.mean(h[i - 10:i] - l[i - 10:i]))
    rng_ratio = rng / max(1e-12, rng10)
    body_atr = body / a

    # 有力阳线：实体占比 >= 0.60 且 收盘靠上 (>= 0.70) 且 相对波动达标 (body_atr >= 0.55 或 rng_ratio >= 1.1) 且当根风险不过宽 (<= 2.2 ATR)
    risk_atr = float(c[i] - l[i]) / a
    cond_strong_candle = int(
        body_r >= 0.60 and close_pos >= 0.70 and (body_atr >= 0.55 or rng_ratio >= 1.1) and risk_atr <= 2.2
    )
    # 力度不够 / 插针 / 止损过宽
    veto_weak_or_wick = int(
        body_r < 0.45 or up_wick_r > 0.40 or (body_atr < 0.40 and rng_ratio < 0.95) or risk_atr > 2.5
    )

    # --- 维度 4：上根大阴线 & 位置透支 ---
    prev1_bear = float(o[i - 1] - c[i - 1]) / a
    veto_prev_big_bear = int(prev1_bear >= 0.95 and c[i] < o[i - 1])

    hi250, lo250 = float(h[i - 250:i + 1].max()), float(l[i - 250:i + 1].min())
    pos250 = (c[i] - lo250) / max(1e-12, hi250 - lo250)
    d60_atr = (c[i] - ma60[i]) / a if ma60[i] == ma60[i] else 0.0
    # 位置太高（你在250根视野里看到处于顶部 > 0.80）
    veto_high_pos = int(pos250 > 0.80)
    # v2.1 M2: MA60 多头乖离严重透支 (> 3.2 ATR)
    veto_bull_overext = int(d60_atr > 3.2)
    # v2.1 M1: 宏观空头压制（价格在 MA120 下方且 MA120、MA60 双双下行）
    macro_bear = int((not above120) and (s120 < -0.04) and (s60 < -0.02))

    # --- 查表合成分数 (0 ~ 100) ---
    score = 50
    # 加分项（共 +50）
    score += 15 * cond_box_tight
    score += 15 * cond_ma_squeeze
    score += 10 * cond_trend_up
    score += 10 * cond_strong_candle
    # 扣分/否决项
    score -= 20 * veto_chop_wide
    score -= 20 * veto_counter_trend
    score -= 20 * veto_weak_or_wick
    score -= 20 * veto_prev_big_bear
    score -= 15 * veto_high_pos
    if veto_bull_overext:
        score = min(70, score - 20)
    if macro_bear and score >= 75:
        score = 70

    score = int(np.clip(score, 0, 100))

    # v2.1 M3: 极窄止损噪音底限保护 (< 0.75 ATR 自适应托底，上限 1.25 ATR)
    st_sw3 = float(l[max(0, i - 2):i + 1].min())
    exec_stop = float(l[i])
    if (float(c[i]) - exec_stop) < 0.75 * a:
        exec_stop = max(min(st_sw3, float(c[i]) - 0.75 * a), float(c[i]) - 1.25 * a)

    return dict(
        cond_box_tight=cond_box_tight,
        cond_ma_squeeze=cond_ma_squeeze,
        cond_trend_up=cond_trend_up,
        cond_strong_candle=cond_strong_candle,
        veto_chop_wide=veto_chop_wide,
        veto_counter_trend=veto_counter_trend,
        veto_weak_or_wick=veto_weak_or_wick,
        veto_prev_big_bear=veto_prev_big_bear,
        veto_high_pos=veto_high_pos,
        veto_bull_overext=veto_bull_overext,
        macro_bear=macro_bear,
        exec_stop=exec_stop,
        struct_stop3=st_sw3,
        trad_score=score,
    )


def main() -> None:
    df = pd.read_parquet(ROOT / "reports/blind/enriched_350.parquet")
    data = load_all_series()

    rows = [score_traditional(data[r["symbol"]], int(r["barIndex"])) for _, r in df.iterrows()]
    fdf = pd.concat([df, pd.DataFrame(rows)], axis=1)

    base = fdf[(~fdf["isTrap"]) & (fdf["isRepeatOf"].isna())].copy()
    train = base[~base["isHoldout"]]
    hold = base[base["isHoldout"]]
    traps = fdf[fdf["isTrap"]]

    print("=== 1. 盲测 300 题 & 留出集 90 题 AUC ===")
    for target in ("is_yes", "bar_hit3r_60", "sw5_hit3r_60", "up2atr_before_barlow", "bar_ma20_win"):
        a_all = roc_auc_score(base[target], base["trad_score"])
        a_tr = roc_auc_score(train[target], train["trad_score"])
        a_ho = roc_auc_score(hold[target], hold["trad_score"])
        print(f"Target={target:22s} | All_300={a_all:.3f} | Train_210={a_tr:.3f} | Holdout_90={a_ho:.3f}")

    print(f"\nTraps (N=20): mean score={traps['trad_score'].mean():.1f}, >=65 rate={(traps['trad_score']>=65).mean():.1%}")

    for name, sub in [("All_300", base), ("Train_210", train), ("Holdout_90", hold)]:
        print(f"\n=== {name} 分档表现 ===")
        bins = [
            (-1, 34, "D档(<35 否决)"),
            (35, 54, "C档(35-54 观望)"),
            (55, 74, "B档(55-74 合格)"),
            (75, 100, "A档(>=75 强推)"),
        ]
        for lo, hi, label in bins:
            b = sub[(sub["trad_score"] >= lo) & (sub["trad_score"] <= hi)]
            if len(b) == 0:
                continue
            print(
                f"{label:15s} | N={len(b):3d} | 你买={b['is_yes'].mean():.1%} "
                f"| 3R率(当根)={b['bar_hit3r_60'].mean():.1%} "
                f"| 3R率(5根结构)={b['sw5_hit3r_60'].mean():.1%} "
                f"| 先涨2ATR率={b['up2atr_before_barlow'].mean():.1%} "
                f"| MA20胜率={b['bar_ma20_win'].mean():.1%} (均R={b['bar_ma20_r'].mean():+.2f}) "
                f"| 5根止损={b['bar_stop_le5'].mean():.1%}"
            )

    # Save full trad_score results into blind_analysis_20261109.json
    out_path = ROOT / "reports/blind/blind_analysis_20261109.json"
    rep = json.loads(out_path.read_text())
    bins = [
        (-1, 34, "D档(<35 否决)"),
        (35, 54, "C档(35-54 观望)"),
        (55, 74, "B档(55-74 合格)"),
        (75, 100, "A档(>=75 强推)"),
    ]
    buckets = {}
    for name, sub in [("All_300", base), ("Train_210", train), ("Holdout_90", hold)]:
        buckets[name] = {}
        for lo, hi, label in bins:
            b = sub[(sub["trad_score"] >= lo) & (sub["trad_score"] <= hi)]
            if len(b) == 0:
                continue
            buckets[name][label] = {
                "n": int(len(b)),
                "user_buy_rate": round(float(b["is_yes"].mean()), 3),
                "bar_hit3r_60": round(float(b["bar_hit3r_60"].mean()), 3),
                "sw5_hit3r_60": round(float(b["sw5_hit3r_60"].mean()), 3),
                "up2atr_before_barlow": round(float(b["up2atr_before_barlow"].mean()), 3),
                "bar_ma20_win": round(float(b["bar_ma20_win"].mean()), 3),
                "bar_ma20_r": round(float(b["bar_ma20_r"].mean()), 3),
                "bar_stop_le5": round(float(b["bar_stop_le5"].mean()), 3),
            }
    rep["M5_traditional_score"] = {
        "auc": {
            t: {
                "All_300": round(float(roc_auc_score(base[t], base["trad_score"])), 3),
                "Train_210": round(float(roc_auc_score(train[t], train["trad_score"])), 3),
                "Holdout_90": round(float(roc_auc_score(hold[t], hold["trad_score"])), 3),
            }
            for t in ("is_yes", "bar_hit3r_60", "sw5_hit3r_60", "up2atr_before_barlow", "bar_ma20_win")
        },
        "traps_mean_score": round(float(traps["trad_score"].mean()), 1),
        "traps_ge55_rate": round(float((traps["trad_score"] >= 55).mean()), 3),
        "buckets": buckets,
    }
    out_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    print("Updated", out_path)


if __name__ == "__main__":
    main()
