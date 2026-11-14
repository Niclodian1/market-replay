#!/usr/bin/env python3
"""从 enriched_350.parquet 计算 Q1–Q5 全套盲测统计与新规则评分表现。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact, mannwhitneyu, spearmanr

ROOT = Path(__file__).resolve().parents[3]


def kappa(a: np.ndarray, b: np.ndarray) -> float:
    po = float((a == b).mean())
    p1 = float(a.mean()) * float(b.mean()) + float((1 - a).mean()) * float((1 - b).mean())
    return (po - p1) / max(1e-12, 1.0 - p1)


def summarize_slice(d: pd.DataFrame) -> dict:
    if len(d) == 0:
        return {"n": 0}
    return {
        "n": int(len(d)),
        "bar_hit2r_60": round(float(d["bar_hit2r_60"].mean()), 3),
        "bar_hit3r_60": round(float(d["bar_hit3r_60"].mean()), 3),
        "bar_hit3r_120": round(float(d["bar_hit3r_120"].mean()), 3),
        "bar_ma20_win": round(float(d["bar_ma20_win"].mean()), 3),
        "bar_ma20_r": round(float(d["bar_ma20_r"].mean()), 3),
        "usr_hit2r_60": round(float(d["usr_hit2r_60"].mean()), 3),
        "usr_hit3r_60": round(float(d["usr_hit3r_60"].mean()), 3),
        "usr_hit3r_120": round(float(d["usr_hit3r_120"].mean()), 3),
        "usr_ma20_win": round(float(d["usr_ma20_win"].mean()), 3),
        "usr_ma20_r": round(float(d["usr_ma20_r"].mean()), 3),
        "sw5_hit3r_60": round(float(d["sw5_hit3r_60"].mean()), 3),
        "sw5_ma20_r": round(float(d["sw5_ma20_r"].mean()), 3),
        "up2atr_before_barlow": round(float(d["up2atr_before_barlow"].mean()), 3),
        "ret20_pos": round(float(d["ret20_pos"].mean()), 3),
        "ret20_atr": round(float(d["ret20_atr"].mean()), 3),
        "ret60_atr": round(float(d["ret60_atr"].mean()), 3),
        "max_up20_atr": round(float(d["max_up20_atr"].mean()), 3),
        "bar_stop_le5": round(float(d["bar_stop_le5"].mean()), 3),
        "usr_stop_le5": round(float(d["usr_stop_le5"].mean()), 3),
    }


def main() -> None:
    df = pd.read_parquet(ROOT / "reports/blind/enriched_350.parquet")

    # 1. Q4: 标注质量、陷阱题与跨场重测一致性
    by_sess = (
        df.groupby("session")["decision"]
        .value_counts()
        .unstack(fill_value=0)
        .to_dict(orient="index")
    )
    traps = df[df["isTrap"]]
    trap_stats = {
        "n": int(len(traps)),
        "decisions": traps["decision"].value_counts().to_dict(),
        "reject_rate": round(float((traps["decision"] != "yes").mean()), 3),
        "yes_items": traps[traps["decision"] == "yes"][["id", "symbol", "date", "confidence", "note"]].to_dict("records"),
    }

    # 30对跨场重测题 (R-01..R-30 vs 原题)
    reps = df[df["isRepeatOf"].notna()].copy()
    orig_map = df.set_index("id").to_dict(orient="index")
    rep_pairs = []
    for _, r in reps.iterrows():
        o = orig_map[r["isRepeatOf"]]
        rep_pairs.append({
            "rep_id": r["id"],
            "orig_id": r["isRepeatOf"],
            "rep_sess": int(r["session"]),
            "orig_sess": int(o["session"]),
            "rep_dec": r["decision"],
            "orig_dec": o["decision"],
            "rep_yes": int(r["is_yes"]),
            "orig_yes": int(o["is_yes"]),
            "rep_conf": int(r["confidence"]),
            "orig_conf": int(o["confidence"]),
            "rep_main_rej": r["mainRejectReason"],
            "orig_main_rej": o["mainRejectReason"],
            "rep_setup": r["setup_usr"],
            "orig_setup": o["setup_usr"],
            "rep_stop": r["stopPrice_norm"],
            "orig_stop": o["stopPrice_norm"],
        })
    rp_df = pd.DataFrame(rep_pairs)
    exact_dec = float((rp_df["rep_dec"] == rp_df["orig_dec"]).mean())
    bin_agree = float((rp_df["rep_yes"] == rp_df["orig_yes"]).mean())
    kap = kappa(rp_df["rep_yes"].to_numpy(), rp_df["orig_yes"].to_numpy())
    # signed confidence: +conf for yes, -conf for no, 0 for unsure
    s_rep = np.where(rp_df["rep_dec"] == "yes", rp_df["rep_conf"], np.where(rp_df["rep_dec"] == "no", -rp_df["rep_conf"], 0))
    s_orig = np.where(rp_df["orig_dec"] == "yes", rp_df["orig_conf"], np.where(rp_df["orig_dec"] == "no", -rp_df["orig_conf"], 0))
    conf_corr = float(spearmanr(s_rep, s_orig).statistic)

    q4_quality = {
        "total_items": int(len(df)),
        "overall_decisions": df["decision"].value_counts().to_dict(),
        "by_session": by_sess,
        "median_seconds": round(float(df["secondsSpent"].median()), 1),
        "mean_seconds": round(float(df["secondsSpent"].mean()), 1),
        "trap_stats": trap_stats,
        "repeat_30": {
            "n_pairs": int(len(rp_df)),
            "exact_3way_agreement": round(exact_dec, 3),
            "binary_yes_no_agreement": round(bin_agree, 3),
            "cohen_kappa": round(kap, 3),
            "signed_conf_spearman": round(conf_corr, 3),
            "both_yes": int(((rp_df["rep_yes"] == 1) & (rp_df["orig_yes"] == 1)).sum()),
            "both_no": int(((rp_df["rep_yes"] == 0) & (rp_df["orig_yes"] == 0)).sum()),
            "disagreements": rp_df[rp_df["rep_yes"] != rp_df["orig_yes"]][
                ["rep_id", "orig_id", "orig_sess", "rep_sess", "orig_dec", "rep_dec", "orig_conf", "rep_conf", "orig_main_rej", "rep_main_rej"]
            ].to_dict("records"),
        },
    }

    # 2. Q1: 你的肉眼判断是否有真实的样本外正期望？（在300道正式题 B-001..B-300 上评估）
    base = df[(~df["isTrap"]) & (df["isRepeatOf"].isna())].copy()
    yes_b = base[base["is_yes"] == 1]
    no_b = base[base["is_yes"] == 0]

    # Fisher exact p-values for YES vs NO on key metrics
    pvals = {}
    for col in ("bar_hit2r_60", "bar_hit3r_60", "bar_hit3r_120", "bar_ma20_win", "usr_hit2r_60", "usr_hit3r_60", "usr_ma20_win", "up2atr_before_barlow", "ret20_pos", "bar_stop_le5", "usr_stop_le5"):
        tab = [
            [int(yes_b[col].sum()), int(len(yes_b) - yes_b[col].sum())],
            [int(no_b[col].sum()), int(len(no_b) - no_b[col].sum())],
        ]
        _, p_two = fisher_exact(tab)
        pvals[col] = round(float(p_two), 4)

    # Breakdown by Group
    by_group = {}
    for g, sub in base.groupby("group"):
        by_group[g] = {
            "all": summarize_slice(sub),
            "yes": summarize_slice(sub[sub["is_yes"] == 1]),
            "no": summarize_slice(sub[sub["is_yes"] == 0]),
        }

    # Breakdown by CandidateType
    by_cand = {}
    for ct, sub in base.groupby("candidateType"):
        by_cand[ct] = {
            "all": summarize_slice(sub),
            "yes": summarize_slice(sub[sub["is_yes"] == 1]),
            "no": summarize_slice(sub[sub["is_yes"] == 0]),
        }

    # Breakdown by Confidence on YES
    by_conf_yes = {}
    for c_val, sub in yes_b.groupby("confidence"):
        by_conf_yes[int(c_val)] = summarize_slice(sub)

    # Breakdown by Train (isHoldout=False) vs Holdout (isHoldout=True)
    train_b = base[~base["isHoldout"]]
    hold_b = base[base["isHoldout"]]

    q1_edge = {
        "base_300": {
            "all": summarize_slice(base),
            "yes": summarize_slice(yes_b),
            "no": summarize_slice(no_b),
            "fisher_p_two_sided": pvals,
        },
        "train_210": {
            "yes": summarize_slice(train_b[train_b["is_yes"] == 1]),
            "no": summarize_slice(train_b[train_b["is_yes"] == 0]),
        },
        "holdout_90": {
            "yes": summarize_slice(hold_b[hold_b["is_yes"] == 1]),
            "no": summarize_slice(hold_b[hold_b["is_yes"] == 0]),
        },
        "by_group": by_group,
        "by_candidate_type": by_cand,
        "by_confidence_on_yes": by_conf_yes,
    }

    # 3. Q5: 止损对比（当根低点 vs 你手点的止损 vs 5根结构低点）
    yes_touched = yes_b[yes_b["stopTouched"]]
    q5_stop = {
        "n_yes": int(len(yes_b)),
        "n_stop_touched": int(len(yes_touched)),
        "bar_stop_risk_pct_median": round(float(yes_b["bar_risk_pct"].median()), 2),
        "usr_stop_risk_pct_median": round(float(yes_b["usr_risk_pct"].median()), 2),
        "sw5_stop_risk_pct_median": round(float(yes_b["sw5_risk_pct"].median()), 2),
        "bar_stop_risk_atr_median": round(float(yes_b["bar_risk_atr"].median()), 2),
        "usr_stop_risk_atr_median": round(float(yes_b["usr_risk_atr"].median()), 2),
        "sw5_stop_risk_atr_median": round(float(yes_b["sw5_risk_atr"].median()), 2),
        "usr_wider_than_bar_rate": round(float((yes_b["usr_risk_pct"] > yes_b["bar_risk_pct"] + 0.05).mean()), 3),
        "usr_tighter_than_bar_rate": round(float((yes_b["usr_risk_pct"] < yes_b["bar_risk_pct"] - 0.05).mean()), 3),
        "usr_near_sw5_rate": round(float((np.abs(yes_b["usr_risk_atr"] - yes_b["sw5_risk_atr"]) <= 0.35).mean()), 3),
        "comparison_on_yes": {
            "bar_low": {
                "stop_le5": round(float(yes_b["bar_stop_le5"].mean()), 3),
                "stop_le20": round(float(yes_b["bar_stop_le20"].mean()), 3),
                "hit2r_60": round(float(yes_b["bar_hit2r_60"].mean()), 3),
                "hit3r_60": round(float(yes_b["bar_hit3r_60"].mean()), 3),
                "ma20_win": round(float(yes_b["bar_ma20_win"].mean()), 3),
                "ma20_r_mean": round(float(yes_b["bar_ma20_r"].mean()), 3),
                "ma20_pct_mean": round(float(yes_b["bar_ma20_pct"].mean()), 2),
            },
            "user_custom": {
                "stop_le5": round(float(yes_b["usr_stop_le5"].mean()), 3),
                "stop_le20": round(float(yes_b["usr_stop_le20"].mean()), 3),
                "hit2r_60": round(float(yes_b["usr_hit2r_60"].mean()), 3),
                "hit3r_60": round(float(yes_b["usr_hit3r_60"].mean()), 3),
                "ma20_win": round(float(yes_b["usr_ma20_win"].mean()), 3),
                "ma20_r_mean": round(float(yes_b["usr_ma20_r"].mean()), 3),
                "ma20_pct_mean": round(float(yes_b["usr_ma20_pct"].mean()), 2),
            },
            "swing_low_5": {
                "stop_le5": round(float(yes_b["sw5_stop_le5"].mean()), 3),
                "stop_le20": round(float(yes_b["sw5_stop_le20"].mean()), 3),
                "hit2r_60": round(float(yes_b["sw5_hit2r_60"].mean()), 3),
                "hit3r_60": round(float(yes_b["sw5_hit3r_60"].mean()), 3),
                "ma20_win": round(float(yes_b["sw5_ma20_win"].mean()), 3),
                "ma20_r_mean": round(float(yes_b["sw5_ma20_r"].mean()), 3),
                "ma20_pct_mean": round(float(yes_b["sw5_ma20_pct"].mean()), 2),
            },
        },
    }

    # 4. Q2 & Q3: 你的主观标签 & 客观编码条件 vs 你的判断 & 真实行情结果
    # 4a. 你的不买理由分布及真实表现（验证你拒绝掉的是不是真坑）
    rej_stats = {}
    for r_tag in ("逆势", "震荡没走完", "力度不够", "位置太高", "均线未粘合", "波动太大/止损太宽", "盈亏比不够"):
        mask = no_b["rejectReasons"].apply(lambda x: r_tag in x)
        sub = no_b[mask]
        rej_stats[r_tag] = summarize_slice(sub)

    # 检查文字笔记里提到「阴线」的题目
    note_bear = no_b[no_b["note"].str.contains("阴线", na=False)]
    rej_stats["笔记提到上根大阴线"] = summarize_slice(note_bear)

    # 4b. 你在买点上标的 setup / trend / position / priorDrop / dualMA / quality 对应的真实胜率
    usr_tag_outcomes = {
        "setup_on_yes": {k: summarize_slice(g) for k, g in yes_b.groupby("setup_usr", dropna=False)},
        "trend_all": {str(k): {**summarize_slice(g), "buy_rate": round(float(g["is_yes"].mean()), 3)} for k, g in base.groupby("trend_usr", dropna=False)},
        "position_all": {str(k): {**summarize_slice(g), "buy_rate": round(float(g["is_yes"].mean()), 3)} for k, g in base.groupby("position_usr", dropna=False)},
        "priorDrop_on_yes": {str(k): summarize_slice(g) for k, g in yes_b.groupby("priorDrop_usr", dropna=False)},
        "dualMA_on_yes": {str(k): summarize_slice(g) for k, g in yes_b.groupby("dualMA_usr", dropna=False)},
    }

    # 4c. 客观代码条件 (V/Q/E/P) 对「你的买卖决定(is_yes)」和「真实行情表现」的预测力
    code_conds = [
        "V1_力度不够", "V1a_K线不好看", "V1b_波动太小", "V2_止损太宽", "V2b_盈亏比差",
        "V3_波动太大", "V4_位置太高", "V5_震荡初期", "V6_插针阴线", "V7_刚假突破",
        "V8_上根大阴线", "V9_均线压制逆势",
        "Q1_双均线", "Q2_盘整收敛", "Q3_趋势线3触", "Q5_放量", "Q6_均线紧贴", "Q7_站上MA60", "marubozu"
    ]
    cond_eval = {}
    for c in code_conds:
        c1 = base[base[c] == 1]
        c0 = base[base[c] == 0]
        cond_eval[c] = {
            "trig_rate": round(float(base[c].mean()), 3),
            "n_1": int(len(c1)),
            "buy_rate_when_1": round(float(c1["is_yes"].mean()), 3) if len(c1) else None,
            "buy_rate_when_0": round(float(c0["is_yes"].mean()), 3) if len(c0) else None,
            "buy_diff": round(float(c1["is_yes"].mean() - c0["is_yes"].mean()), 3) if len(c1) and len(c0) else None,
            "bar_hit3r_60_when_1": round(float(c1["bar_hit3r_60"].mean()), 3) if len(c1) else None,
            "bar_hit3r_60_when_0": round(float(c0["bar_hit3r_60"].mean()), 3) if len(c0) else None,
            "sw5_hit3r_60_when_1": round(float(c1["sw5_hit3r_60"].mean()), 3) if len(c1) else None,
            "sw5_hit3r_60_when_0": round(float(c0["sw5_hit3r_60"].mean()), 3) if len(c0) else None,
            "bar_ma20_r_when_1": round(float(c1["bar_ma20_r"].mean()), 3) if len(c1) else None,
            "bar_ma20_r_when_0": round(float(c0["bar_ma20_r"].mean()), 3) if len(c0) else None,
            "up2atr_when_1": round(float(c1["up2atr_before_barlow"].mean()), 3) if len(c1) else None,
            "up2atr_when_0": round(float(c0["up2atr_before_barlow"].mean()), 3) if len(c0) else None,
        }

    out = {
        "Q4_quality": q4_quality,
        "Q1_edge": q1_edge,
        "Q5_stop": q5_stop,
        "Q2_Q3_reject_reasons": rej_stats,
        "Q2_Q3_user_tags": usr_tag_outcomes,
        "Q2_Q3_code_conditions": cond_eval,
    }
    out_path = ROOT / "reports/blind/blind_analysis_20261109.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print("Wrote", out_path)


if __name__ == "__main__":
    main()
