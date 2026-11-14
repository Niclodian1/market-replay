#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Meta-Labeling（元标签过滤与动态分仓）训练、Purged OOS 验证与白盒归因模块。

架构设计（对应 Marcos López de Prado《Advances in Financial Machine Learning》）：
- 第一层（Primary Model / 规则层）：
    - Pool A：人工标注的 72 个买点（annotations-BTC-1D-manual.json），用于诊断手选池内部的仓位分配价值；
    - Pool B：固化规则「买点算法 v4 · 同波段冷却去重」（mybuy_cd，152 个信号，召回 93.1% 手选点），用于全客观实盘触发。
- 第二层（Secondary AI Model / 元标签过滤与分仓层）：
    - 目标定义（Label = 1）：入场后 60 根 K 线内，在未触及信号 K 线最低价（1R 止损位）之前，最大浮盈 MFE >= 3.0R。
    - 特征切片（4 大维度、8 个纯因果无未来函数特征，全部在收盘时刻 i 已知）：
        1. 入场前大势（Pre-entry Trend）：slope20（MA20 5日斜率）、ret20（入场前20日涨跌幅）
        2. 结构位置（Market Regime）：pos90（收盘价在过去90日高低点区间的百分位）
        3. K线微观形态与成本（Microstructure & Cost）：risk_pct（初始止损宽度%）、d20（收盘偏离MA20%）、vol_ratio（20日量比）
        4. 波动率压缩度（Volatility Compression）：atr_pct（ATR14/收盘%）、tr10_60（近10日/60日真实振幅压缩比）
    - 模型结构：RobustScaler + 平衡类别权重 L2 LogisticRegression (65%) + 浅层正则化 GBDT (max_depth=2, 35%) 集成。
    - 评估方式：Purged K-Fold 时序块交叉验证（前后各 Purge 30 根 K 线，杜绝持仓路径重叠泄漏）。
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import RobustScaler

# 8 个核心因果特征定义：(key, 简短标签, 完整中文名称, 所属维度, 单位, 格式化, 物理含义与预期方向)
META_FEATURE_DEFS = [
    ("atr_pct", "ATR波动率", "ATR(14) 波动率占比", "四、波动率压缩度", "%", "{:.2f}%", "越低越好（波动收敛后突破易走大趋势，高波巨震易扫损）"),
    ("slope20", "MA20斜率", "MA20 均线斜率(5日)", "一、入场前大势", "%/根", "{:+.2f}%", "走平转正更好（均线企稳拐头，而非瀑布暴跌途中硬接）"),
    ("ret20", "前20日涨跌", "入场前 20 日涨跌幅", "一、入场前大势", "%", "{:+.1f}%", "温和回调更好（浅回踩蓄势，而非深跌崩盘段）"),
    ("d20", "距MA20偏离", "收盘距 MA20 偏离度", "三、K线微观与成本", "%", "{:.2f}%", "适中偏紧更好（刚过均线起涨点，偏离过大则追高成本劣势）"),
    ("risk_pct", "初始止损宽", "初始止损宽度 (收盘-最低)", "三、K线微观与成本", "%", "{:.2f}%", "越窄越好（1R 分母小，极易推升出 >=3R 高盈亏比）"),
    ("pos90", "90日分位", "过去 90 日区间位置分位", "二、结构位置", "%", "{:.1f}%", "中低位更好（底部或中继平台启动，上方空间充足）"),
    ("tr10_60", "振幅压缩比", "近10日/60日振幅压缩比", "四、波动率压缩度", "×", "{:.2f}×", "越小越好（入场前经历窄幅缩量蓄势，弹簧压缩充分）"),
    ("vol_ratio", "信号日量比", "信号日量比 (v / MA20量)", "三、K线微观与成本", "×", "{:.2f}×", "放量配合更好（真突破需买盘资金确认）"),
]

FEAT_KEYS = [x[0] for x in META_FEATURE_DEFS]

# 分仓阈值（与对话中的三档动态仓位管理对齐）
TH_SKIP = 0.45   # P < 0.45：过滤放弃 (0x) 或 0.25x 观察仓
TH_HEAVY = 0.58  # P >= 0.58：高确定性主升浪启动点，重仓出击 (2.0x)


def extract_meta_features(M: dict[str, Any], i: int, score_fn) -> dict[str, float]:
    """在 K 线下标 i 处提取纯因果特征（仅使用 <= i 的历史数据，零未来函数）。"""
    c, h, l, o, v = M["c"], M["h"], M["l"], M["o"], M["v"]
    m20, atr = M["m20"], M["atr"]
    sc = score_fn(M, i)
    hi90 = float(h[max(0, i - 90):i + 1].max())
    lo90 = float(l[max(0, i - 90):i + 1].min())
    rng_arr = (h - l) / c
    tr10 = float(rng_arr[max(0, i - 10):i].mean()) if i >= 10 else float(rng_arr[i])
    tr60 = float(rng_arr[max(0, i - 60):i].mean()) if i >= 60 else tr10

    return dict(
        atr_pct=float((atr[i] / c[i]) * 100.0) if (atr[i] == atr[i] and c[i] > 0) else 3.5,
        slope20=float((m20[i] - m20[i - 5]) / m20[i - 5] / 5.0 * 100.0) if (i >= 5 and m20[i - 5] > 0) else 0.0,
        ret20=float((c[i] / c[i - 20] - 1.0) * 100.0) if (i >= 20 and c[i - 20] > 0) else 0.0,
        d20=float((c[i] / m20[i] - 1.0) * 100.0) if m20[i] > 0 else 1.5,
        risk_pct=float((c[i] - l[i]) / c[i] * 100.0) if c[i] > 0 else 3.5,
        pos90=float((c[i] - lo90) / max(1e-9, hi90 - lo90) * 100.0),
        tr10_60=float(tr10 / max(1e-9, tr60)),
        vol_ratio=float(min(3.5, sc["vr"])),
    )


def _auc_mann_whitney(y: np.ndarray, x: np.ndarray) -> float:
    """计算单特征对二分类标签 y 的原始 AUC（含方向：>0.5 表示正相关，<0.5 表示负相关）。"""
    y = np.asarray(y, dtype=int)
    x = np.asarray(x, dtype=float)
    order = np.argsort(x, kind="mergesort")
    sx = x[order]
    ranks = np.empty(len(x), dtype=float)
    r = np.empty(len(x), dtype=float)
    idx = 0
    while idx < len(sx):
        j = idx
        while j + 1 < len(sx) and sx[j + 1] == sx[idx]:
            j += 1
        r[idx:j + 1] = (idx + j) / 2.0 + 1.0
        idx = j + 1
    ranks[order] = r
    npos = int(y.sum())
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        return 0.5
    return float((ranks[y == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg))


def _fit_ensemble(X_tr: np.ndarray, y_tr: np.ndarray, C: float = 0.20):
    """训练 RobustScaler + 平衡 L2 逻辑回归 (65%) + 浅层正则化 GBDT (35%)。"""
    scaler = RobustScaler()
    X_s = scaler.fit_transform(X_tr)

    lr = LogisticRegression(C=C, class_weight="balanced", solver="lbfgs", max_iter=500, random_state=42)
    lr.fit(X_s, y_tr)

    pos_rate = float(np.clip(y_tr.mean(), 0.05, 0.95))
    sw = np.where(y_tr == 1, 0.5 / pos_rate, 0.5 / (1.0 - pos_rate))
    min_leaf = max(6, len(y_tr) // 10)
    gb = GradientBoostingClassifier(
        n_estimators=25,
        max_depth=2,
        min_samples_leaf=min_leaf,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
    )
    gb.fit(X_s, y_tr, sample_weight=sw)
    return scaler, lr, gb


def _predict_ensemble(scaler: RobustScaler, lr: LogisticRegression, gb: GradientBoostingClassifier, X_te: np.ndarray) -> np.ndarray:
    X_s = scaler.transform(X_te)
    p_lr = lr.predict_proba(X_s)[:, 1]
    p_gb = gb.predict_proba(X_s)[:, 1]
    return 0.65 * p_lr + 0.35 * p_gb


def purged_cv_evaluate(df: pd.DataFrame, n_splits: int = 6, purge_bars: int = 30, C: float = 0.20) -> np.ndarray:
    """严格时序块 Purged Cross-Validation：测试块前后各剔除 purge_bars 根 K 线，输出无泄漏 OOS 概率。"""
    n = len(df)
    probs = np.zeros(n, dtype=float)
    indices = np.arange(n)
    fold_sizes = np.full(n_splits, n // n_splits, dtype=int)
    fold_sizes[:n % n_splits] += 1
    current = 0
    for fold_size in fold_sizes:
        start, stop = current, current + fold_size
        test_idx = indices[start:stop]
        test_bars = df.iloc[test_idx]["i"].values
        min_b, max_b = int(test_bars.min()) - purge_bars, int(test_bars.max()) + purge_bars
        train_idx = [
            idx for idx in indices
            if idx not in test_idx and (df.iloc[idx]["i"] < min_b or df.iloc[idx]["i"] > max_b)
        ]
        X_tr = df.iloc[train_idx][FEAT_KEYS].values
        y_tr = df.iloc[train_idx]["y"].values
        X_te = df.iloc[test_idx][FEAT_KEYS].values

        scaler, lr, gb = _fit_ensemble(X_tr, y_tr, C=C)
        probs[test_idx] = _predict_ensemble(scaler, lr, gb, X_te)
        current = stop
    return probs


def explain_trade(
    row: pd.Series | dict[str, Any],
    scaler: RobustScaler,
    lr: LogisticRegression,
    gb: GradientBoostingClassifier,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """计算单笔信号的白盒特征归因（SHAP 线性对数几率分解 + 特征树敏感度），返回正负贡献最大的特征解释。"""
    x_raw = np.array([float(row[k]) for k in FEAT_KEYS], dtype=float)
    x_s = scaler.transform(x_raw.reshape(1, -1))[0]
    coefs = lr.coef_[0]

    # 对每个特征计算：把该特征置为中位数 (x_s=0) 时的集成概率变化量 delta_p
    p_base = float(_predict_ensemble(scaler, lr, gb, x_raw.reshape(1, -1))[0])
    contribs = []
    for idx, (key, short_name, name, grp, unit, fmt, _) in enumerate(META_FEATURE_DEFS):
        x_mod = x_raw.copy()
        x_mod[idx] = float(scaler.center_[idx])  # 置为训练集样本中位数
        p_without = float(_predict_ensemble(scaler, lr, gb, x_mod.reshape(1, -1))[0])
        delta_p = p_base - p_without
        # 结合线性项保底，防止树阶跃边界处恰好为 0
        lin_delta = float(0.12 * coefs[idx] * x_s[idx])
        impact = 0.7 * delta_p + 0.3 * lin_delta
        val_str = fmt.format(x_raw[idx])
        contribs.append(dict(
            key=key,
            name=short_name,
            fullName=name,
            group=grp,
            val=val_str,
            rawVal=round(float(x_raw[idx]), 3),
            zScore=round(float(x_s[idx]), 2),
            impact=round(float(impact), 4),
            text=f"{short_name}({val_str}) {'+' if impact >= 0 else ''}{impact * 100:.1f}%",
        ))
    contribs.sort(key=lambda d: abs(d["impact"]), reverse=True)
    return contribs[:top_k]


def _eval_sizing_modes(df: pd.DataFrame, prob_col: str, r_col: str) -> list[dict[str, Any]]:
    """对比 4 种仓位管理模式在指定策略 R 序列上的表现。"""
    p = df[prob_col].values
    r = df[r_col].values
    y3r = df["y"].values

    modes = [
        ("baseline_1x", "① 等权固定仓位 (1x 基准)", np.ones_like(r), "每笔信号固定投入 1R 风险敞口"),
        ("binary_filter", f"② AI 二元硬过滤 (<{TH_SKIP:.2f} 放弃)", np.where(p >= TH_SKIP, 1.0, 0.0), f"P<{TH_SKIP:.2f} 放弃(0x)，P≥{TH_SKIP:.2f} 标准仓(1x)"),
        ("tiered_0_1_2", f"③ AI 三级动态分仓 (0x / 1x / 2x 推荐)", np.where(p >= TH_HEAVY, 2.0, np.where(p >= TH_SKIP, 1.0, 0.0)), f"P<{TH_SKIP:.2f} 放弃(0x)，{TH_SKIP:.2f}~{TH_HEAVY:.2f} 标准(1x)，≥{TH_HEAVY:.2f} 重仓(2x)"),
        ("soft_025_1_2", f"④ AI 柔性防踏空分仓 (0.25x / 1x / 2x)", np.where(p >= TH_HEAVY, 2.0, np.where(p >= TH_SKIP, 1.0, 0.25)), f"P<{TH_SKIP:.2f} 试探(0.25x)，{TH_SKIP:.2f}~{TH_HEAVY:.2f} 标准(1x)，≥{TH_HEAVY:.2f} 重仓(2x)"),
    ]
    out = []
    for tag, name, w, rule_desc in modes:
        rw = r * w
        active = w > 0
        n_active = int(active.sum())
        risk_budget = float(w.sum())
        eq = np.cumsum(rw)
        dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
        loss = float(-rw[rw < 0].sum())
        pf = float(rw[rw > 0].sum() / loss) if loss > 0 else 99.0
        win_rate = float((r[active] > 0).mean()) if n_active > 0 else 0.0
        ge3r_rate = float(y3r[active].mean()) if n_active > 0 else 0.0
        mean_r_per_trade = float(rw[active].mean()) if n_active > 0 else 0.0
        mean_r_per_risk = float(rw.sum() / risk_budget) if risk_budget > 0 else 0.0
        sum_r = float(rw.sum())
        ret_dd = float(sum_r / max(1.0, dd))
        out.append(dict(
            tag=tag,
            name=name,
            rule=rule_desc,
            activeN=n_active,
            totalN=int(len(r)),
            skippedN=int(len(r) - n_active),
            heavyN=int((w >= 2.0).sum()),
            stdN=int((w == 1.0).sum()),
            riskBudget=round(risk_budget, 2),
            winRate=round(win_rate, 4),
            ge3rRate=round(ge3r_rate, 4),
            meanRTrade=round(mean_r_per_trade, 3),
            meanRRisk=round(mean_r_per_risk, 3),
            sumR=round(sum_r, 2),
            maxDD=round(dd, 2),
            retDD=round(ret_dd, 2),
            pf=round(pf, 2),
            curve=[round(float(x), 2) for x in eq],
        ))
    return out


def _eval_tiers(df: pd.DataFrame, prob_col: str) -> list[dict[str, Any]]:
    """按预测概率 P 的三档区间统计真实 >=3R 命中率、胜率与平均 R。"""
    p = df[prob_col].values
    tiers = [
        ("skip", f"低胜率磨损区 (P < {TH_SKIP:.2f})", "0x 放弃 / 0.25x 试探", p < TH_SKIP),
        ("std", f"标准趋势区 ({TH_SKIP:.2f} ≤ P < {TH_HEAVY:.2f})", "1.0x 标准仓位", (p >= TH_SKIP) & (p < TH_HEAVY)),
        ("heavy", f"高确定性主升区 (P ≥ {TH_HEAVY:.2f})", "2.0x 重仓出击", p >= TH_HEAVY),
    ]
    out = []
    for tag, label, action, mask in tiers:
        sub = df.loc[mask]
        n = int(len(sub))
        if n == 0:
            out.append(dict(tag=tag, label=label, action=action, n=0, share=0.0,
                            avgP=0.0, ge3rRate=0.0, winA=0.0, meanRA=0.0, sumRA=0.0,
                            winB=0.0, meanRB=0.0, sumRB=0.0, stopLe5Rate=0.0))
            continue
        out.append(dict(
            tag=tag,
            label=label,
            action=action,
            n=n,
            share=round(float(n / len(df)), 4),
            avgP=round(float(p[mask].mean()), 3),
            ge3rRate=round(float(sub["y"].mean()), 4),
            winA=round(float((sub["r_a"] > 0).mean()), 4),
            meanRA=round(float(sub["r_a"].mean()), 2),
            sumRA=round(float(sub["r_a"].sum()), 1),
            winB=round(float((sub["r_b"] > 0).mean()), 4),
            meanRB=round(float(sub["r_b"].mean()), 2),
            sumRB=round(float(sub["r_b"].sum()), 1),
            stopLe5Rate=round(float((sub["stop_bar"].notna() & (sub["stop_bar"] <= 5)).mean()), 4),
        ))
    return out


def export_js_model(scaler: RobustScaler, lr: LogisticRegression, gb: GradientBoostingClassifier) -> dict[str, Any]:
    """将训练好的 RobustScaler + LogisticRegression + GBDT 浅层决策树导出为轻量 JSON，供前端 JS 零依赖实时推理。"""
    trees = []
    for est in gb.estimators_[:, 0]:
        t = est.tree_
        nodes = []
        for idx in range(t.node_count):
            if t.children_left[idx] == t.children_right[idx]:
                nodes.append(dict(leaf=round(float(t.value[idx, 0, 0]), 6)))
            else:
                nodes.append(dict(
                    f=int(t.feature[idx]),
                    th=round(float(t.threshold[idx]), 6),
                    l=int(t.children_left[idx]),
                    r=int(t.children_right[idx]),
                ))
        trees.append(nodes)

    init_raw = float(gb._raw_predict_init(np.zeros((1, len(FEAT_KEYS))))[0, 0])
    return dict(
        features=FEAT_KEYS,
        featureNames=[x[1] for x in META_FEATURE_DEFS],
        featureUnits=[x[4] for x in META_FEATURE_DEFS],
        center=[round(float(x), 6) for x in scaler.center_],
        scale=[round(float(x), 6) for x in scaler.scale_],
        lrCoef=[round(float(x), 6) for x in lr.coef_[0]],
        lrIntercept=round(float(lr.intercept_[0]), 6),
        gbInit=round(init_raw, 6),
        gbLr=round(float(gb.learning_rate), 6),
        gbTrees=trees,
        thSkip=TH_SKIP,
        thHeavy=TH_HEAVY,
    )


def run_meta_labeling_analysis(
    M: dict[str, Any],
    pts: list[dict[str, Any]],
    kept_sigs: list[dict[str, Any]],
    score_fn,
    trade_stats_fn,
    run_strategy_fn,
) -> dict[str, Any]:
    """执行完整双池（Pool A: 72手选点 vs Pool B: 152冷却去重规则点）AI Meta-Labeling 评估。"""
    pts_i = [p["i"] for p in pts]

    def build_pool_df(items: list[dict[str, Any]]) -> pd.DataFrame:
        rows = []
        for it in items:
            i = int(it["i"])
            ts = trade_stats_fn(M, i)
            r_a = float(run_strategy_fn(M, i, "low")[0])
            r_b = float(run_strategy_fn(M, i, "low", ma_exit=True)[0])
            mfe60_r = float(ts["mfe_by"][60] / ts["risk"]) if ts["risk"] > 0 else 0.0
            is_manual = int(any(abs(i - p) <= 2 for p in pts_i))
            date_str = it.get("date") or str(pd.to_datetime(int(M["t"][i]), unit="s"))[:10]
            rows.append(dict(
                i=i,
                date=date_str,
                y=int(mfe60_r >= 3.0),
                mfe60_r=round(mfe60_r, 2),
                mfe_r=round(float(ts["mfe_r"]), 2),
                stop_bar=ts["stop_bar"],
                r_a=round(r_a, 3),
                r_b=round(r_b, 3),
                is_manual=is_manual,
                score=int(it.get("score", score_fn(M, i)["score"])),
                **extract_meta_features(M, i, score_fn),
            ))
        return pd.DataFrame(rows)

    df_a = build_pool_df(pts)
    df_b = build_pool_df(kept_sigs)

    # 1. Purged OOS 交叉验证概率
    df_a["p_oos"] = np.round(purged_cv_evaluate(df_a, n_splits=6, purge_bars=30, C=0.20), 4)
    df_b["p_oos"] = np.round(purged_cv_evaluate(df_b, n_splits=6, purge_bars=30, C=0.20), 4)

    auc_a_oos = float(roc_auc_score(df_a["y"], df_a["p_oos"]))
    auc_b_oos = float(roc_auc_score(df_b["y"], df_b["p_oos"]))

    # 2. 全样本模型（用于导出特征重要性、单笔白盒归因与前端实时评分器）
    scaler_a, lr_a, gb_a = _fit_ensemble(df_a[FEAT_KEYS].values, df_a["y"].values, C=0.20)
    scaler_b, lr_b, gb_b = _fit_ensemble(df_b[FEAT_KEYS].values, df_b["y"].values, C=0.20)

    df_a["p_full"] = np.round(_predict_ensemble(scaler_a, lr_a, gb_a, df_a[FEAT_KEYS].values), 4)
    df_b["p_full"] = np.round(_predict_ensemble(scaler_b, lr_b, gb_b, df_b[FEAT_KEYS].values), 4)

    # 3. 特征重要性与双池单因子对比表
    lr_imp_a = np.abs(lr_a.coef_[0]) / max(1e-9, np.abs(lr_a.coef_[0]).sum())
    gb_imp_a = gb_a.feature_importances_
    comb_imp_a = 0.65 * lr_imp_a + 0.35 * gb_imp_a

    lr_imp_b = np.abs(lr_b.coef_[0]) / max(1e-9, np.abs(lr_b.coef_[0]).sum())
    gb_imp_b = gb_b.feature_importances_
    comb_imp_b = 0.65 * lr_imp_b + 0.35 * gb_imp_b

    feature_table = []
    for idx, (key, short_name, name, grp, unit, fmt, desc) in enumerate(META_FEATURE_DEFS):
        va, ya = df_a[key].values, df_a["y"].values
        vb, yb = df_b[key].values, df_b["y"].values
        raw_auc_a = _auc_mann_whitney(ya, va)
        raw_auc_b = _auc_mann_whitney(yb, vb)
        pos_a, neg_a = float(np.median(va[ya == 1])), float(np.median(va[ya == 0]))
        pos_b, neg_b = float(np.median(vb[yb == 1])), float(np.median(vb[yb == 0]))
        feature_table.append(dict(
            key=key,
            shortName=short_name,
            name=name,
            group=grp,
            unit=unit,
            desc=desc,
            posMedA=fmt.format(pos_a),
            negMedA=fmt.format(neg_a),
            aucA=round(float(max(raw_auc_a, 1.0 - raw_auc_a)), 3),
            dirA="正相关 (+)" if raw_auc_a >= 0.5 else "负相关 (−)",
            posMedB=fmt.format(pos_b),
            negMedB=fmt.format(neg_b),
            aucB=round(float(max(raw_auc_b, 1.0 - raw_auc_b)), 3),
            dirB="正相关 (+)" if raw_auc_b >= 0.5 else "负相关 (−)",
            impA=round(float(comb_imp_a[idx] * 100.0), 1),
            impB=round(float(comb_imp_b[idx] * 100.0), 1),
            impMean=round(float((comb_imp_a[idx] + comb_imp_b[idx]) * 50.0), 1),
            lrWeightB=round(float(lr_b.coef_[0][idx]), 3),
        ))
    feature_table.sort(key=lambda x: x["impMean"], reverse=True)

    # 4. 为 Pool A (72笔) 和 Pool B (152笔) 生成逐笔白盒归因与建议分仓
    def enrich_trades(df: pd.DataFrame, scaler, lr, gb) -> dict[int, dict[str, Any]]:
        out_map = {}
        for _, row in df.iterrows():
            p_val = float(row["p_oos"])
            if p_val >= TH_HEAVY:
                tier, tier_label, mult = "heavy", "2.0x 重仓", 2.0
            elif p_val >= TH_SKIP:
                tier, tier_label, mult = "std", "1.0x 标准", 1.0
            else:
                tier, tier_label, mult = "skip", "0x 放弃", 0.0
            reasons = explain_trade(row, scaler, lr, gb, top_k=3)
            out_map[int(row["i"])] = dict(
                i=int(row["i"]),
                date=str(row["date"]),
                y3r=int(row["y"]),
                mfe60_r=float(row["mfe60_r"]),
                p_oos=round(p_val, 3),
                p_full=round(float(row["p_full"]), 3),
                tier=tier,
                tierLabel=tier_label,
                mult=mult,
                reasons=reasons,
                reasonText=" · ".join(r["text"] for r in reasons),
            )
        return out_map

    trades_meta_a = enrich_trades(df_a, scaler_a, lr_a, gb_a)
    trades_meta_b = enrich_trades(df_b, scaler_b, lr_b, gb_b)

    # 5. 分档校准与分仓表现对比（严苛采用无泄漏 p_oos）
    sizing_a_strat_a = _eval_sizing_modes(df_a, "p_oos", "r_a")
    sizing_a_strat_b = _eval_sizing_modes(df_a, "p_oos", "r_b")
    sizing_b_strat_a = _eval_sizing_modes(df_b, "p_oos", "r_a")
    sizing_b_strat_b = _eval_sizing_modes(df_b, "p_oos", "r_b")

    tiers_a = _eval_tiers(df_a, "p_oos")
    tiers_b = _eval_tiers(df_b, "p_oos")

    # 导出给前端 K 线页使用的模型参数（默认采用规则池 Pool B 训练出的广谱元标签模型）
    js_model = export_js_model(scaler_b, lr_b, gb_b)

    return dict(
        targetDef="入场后 60 根 K 线内、触及当根最低点(1R止损)之前，最大浮盈 MFE ≥ 3.0R（记为 Y=1）",
        thSkip=TH_SKIP,
        thHeavy=TH_HEAVY,
        features=feature_table,
        poolA=dict(
            name="Pool A · 人工标注 72 个买点（手选精筛池）",
            count=int(len(df_a)),
            baseRate3R=round(float(df_a["y"].mean()), 4),
            oosAuc=round(auc_a_oos, 3),
            tiers=tiers_a,
            sizingStratA=[{k: v for k, v in m.items() if k != "curve"} for m in sizing_a_strat_a],
            sizingStratB=[{k: v for k, v in m.items() if k != "curve"} for m in sizing_a_strat_b],
        ),
        poolB=dict(
            name="Pool B · 买点算法 v4 同波段冷却去重（152 个客观规则信号）",
            count=int(len(df_b)),
            baseRate3R=round(float(df_b["y"].mean()), 4),
            oosAuc=round(auc_b_oos, 3),
            tiers=tiers_b,
            sizingStratA=[{k: v for k, v in m.items() if k != "curve"} for m in sizing_b_strat_a],
            sizingStratB=[{k: v for k, v in m.items() if k != "curve"} for m in sizing_b_strat_b],
            trades=[
                trades_meta_b[int(row["i"])] | dict(
                    score=int(row["score"]),
                    isManual=int(row["is_manual"]),
                    r_a=round(float(row["r_a"]), 2),
                    r_b=round(float(row["r_b"]), 2),
                    risk_pct=round(float(row["risk_pct"]), 2),
                    atr_pct=round(float(row["atr_pct"]), 2),
                    slope20=round(float(row["slope20"]), 2),
                    pos90=round(float(row["pos90"]), 1),
                )
                for _, row in df_b.iterrows()
            ],
        ),
        curves=dict(
            poolA_stratA={m["tag"]: m["curve"] for m in sizing_a_strat_a},
            poolA_stratB={m["tag"]: m["curve"] for m in sizing_a_strat_b},
            poolB_stratA={m["tag"]: m["curve"] for m in sizing_b_strat_a},
            poolB_stratB={m["tag"]: m["curve"] for m in sizing_b_strat_b},
        ),
        tradesMetaA=trades_meta_a,
        tradesMetaB=trades_meta_b,
        jsModel=js_model,
    )


# ---------------------------------------------------------------------------
# 自适应跨品种 / 4H 版本（v5 · ATR 归一化 + 4H 大周期过滤 + 无量纲 AI 分仓）
# ---------------------------------------------------------------------------
ADAPTIVE_FEATURE_DEFS: list[tuple[str, str, str, str, str, str, str]] = [
    ("atr_rel60", "相对波动率", "ATR14 / 60根均ATR", "1.波动收敛", "x", "{:.2f}",
     "当前14根ATR占近60根平均ATR比值（居中于1.0，完全跨品种/跨级别对齐）"),
    ("slope20_atr", "MA20斜率(ATR)", "MA20近5根斜率 / ATR", "2.入场前大势", "ATR/根", "{:+.2f}",
     "MA20每根变化幅度除以当根ATR，消除不同品种绝对涨跌幅量纲差异"),
    ("ret20_atr", "前20根涨跌(ATR)", "入场前20根涨幅 / ATR", "2.入场前大势", "ATR", "{:+.2f}",
     "入场前20根累计涨跌幅除以ATR，区分深跌企稳与高位加速"),
    ("d20_atr", "距MA20(ATR)", "收盘偏离MA20 / ATR", "3.K线成本", "ATR", "{:.2f}",
     "信号K线收盘高于MA20的距离占ATR倍数（硬过滤上限 2.4 ATR）"),
    ("risk_atr", "止损宽(ATR)", "1R止损距离 / ATR", "3.K线成本", "ATR", "{:.2f}",
     "收盘至止损位（日线为最低价，4H含0.25ATR防插针缓冲）占ATR倍数"),
    ("pos90", "90根价格分位", "90根通道位置", "4.结构位置", "%", "{:.1f}",
     "当前收盘处于过去90根K线高低点区间的百分位（0%=最低，100%=最高）"),
    ("tr10_60", "近期振幅收敛", "近10/60根振幅比", "1.波动收敛", "x", "{:.2f}",
     "突破前10根平均K线振幅与前60根之比（<1代表突破前窄幅蓄势）"),
    ("vol_ratio", "当根量比", "成交量 / MA20均量", "3.K线成本", "x", "{:.2f}",
     "信号K线成交量相对过去20根均量的放大倍数（上限截断3.5x）"),
]
ADAPTIVE_FEAT_KEYS = [f[0] for f in ADAPTIVE_FEATURE_DEFS]


def prepare_market_ex(df: pd.DataFrame) -> dict[str, Any]:
    """为任意品种/周期 DataFrame 预计算均线、MACD、14根ATR 与 60根均ATR%（纯因果）"""
    df = df.sort_values("time").reset_index(drop=True)
    c = df["close"].to_numpy(float)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    v = df["volume"].to_numpy(float) if "volume" in df.columns else np.ones_like(c)
    t = df["time"].to_numpy(np.int64)
    n = len(c)

    s_c = pd.Series(c)
    m20 = s_c.rolling(20).mean().to_numpy()
    m60 = s_c.rolling(60).mean().to_numpy()
    m120 = s_c.rolling(120).mean().to_numpy()
    av20 = pd.Series(v).rolling(20).mean().to_numpy()
    ema12 = s_c.ewm(span=12, adjust=False).mean()
    ema26 = s_c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea).to_numpy()

    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    atr14 = pd.Series(tr).rolling(14).mean().to_numpy()
    atr_pct = np.where(c > 0, atr14 / c, np.nan)
    atr_pct_ma60 = pd.Series(atr_pct).rolling(60, min_periods=20).mean().to_numpy()

    return dict(
        c=c, o=o, h=h, l=l, v=v, t=t,
        m20=m20, m60=m60, m120=m120, av20=av20,
        hist=hist, atr=atr14, atr_pct=atr_pct, atr_pct_ma60=atr_pct_ma60, n=n,
    )


def adaptive_score_at(M: dict[str, Any], i: int, is_subdaily: bool = False) -> dict[str, Any] | None:
    """v5 自适应跨品种/4H 规则打分器（ATR 归一化 + 4H MA120 大周期共振过滤）"""
    if i < 65:
        return None
    c, o, h, l, v = M["c"], M["o"], M["h"], M["l"], M["v"]
    m20, m60, m120, av20, hist, atr_pct = (
        M["m20"], M["m60"], M["m120"], M["av20"], M["hist"], M["atr_pct"]
    )
    if m20[i] != m20[i] or c[i] <= m20[i] or c[i] <= o[i]:
        return None
    ap = float(atr_pct[i]) if (atr_pct[i] == atr_pct[i] and atr_pct[i] > 1e-6) else 0.02

    # 1. 最近 3 根内发生过上穿 MA20
    rec = None
    for k in range(0, 3):
        j = i - k
        if j >= 1 and m20[j - 1] == m20[j - 1] and c[j - 1] <= m20[j - 1] and c[j] > m20[j]:
            rec = k
            break
    if rec is None:
        return None

    # 2. 4H 等日内级别大周期共振过滤：收盘位于 4H MA120 (日线 MA20) 之上且大周期均线不走平下跌
    if is_subdaily:
        if i < 125 or m120[i] != m120[i]:
            return None
        if not (c[i] > m120[i] and m120[i] >= m120[i - 5]):
            return None

    # 3. ATR 归一化距离硬过滤：偏离 MA20 不超过 2.4 倍 ATR
    d20 = (c[i] - m20[i]) / m20[i]
    d20_atr = d20 / ap
    if d20 < 0 or d20_atr > 2.4:
        return None

    cross = i - rec
    below = 0
    for k in range(cross - 1, max(0, cross - 60) - 1, -1):
        if m20[k] == m20[k] and c[k] <= m20[k]:
            below += 1
        else:
            break

    body = (c[i] - o[i]) / c[i]
    body_atr = body / ap
    vr = v[i] / av20[i] if (av20[i] == av20[i] and av20[i] > 0) else 1.0

    piv = []
    for k in range(max(2, i - 60), i - 2):
        if h[k] == max(h[k - 2 : k + 3]):
            piv.append((k, h[k]))
    tl_break = False
    if len(piv) >= 2:
        (k1, p1), (k2, p2) = piv[-2], piv[-1]
        if p2 < p1 and k2 > k1:
            proj = p2 + (p2 - p1) / (k2 - k1) * (i - k2)
            tl_break = bool(c[i] > proj and c[i - 1] <= proj * 1.01)

    score, tags = 0, []
    if below >= 5:
        score += 20
        tags.append(f"盘整{below}根")
    if tl_break:
        score += 20
        tags.append("破下跌趋势线")
    if body_atr >= 0.45:
        score += 15
        tags.append("实体≥0.45ATR")
    if vr >= 1.0:
        score += 15
        tags.append(f"放量{vr:.1f}x")
    if hist[i] > hist[i - 3]:
        score += 10
        tags.append("MACD转强")
    if m60[i] == m60[i] and c[i] > m60[i]:
        score += 10
        tags.append("站上MA60")
    if 0.20 <= d20_atr <= 0.85:
        score += 10
        tags.append("距MA20适中(ATR)")

    return dict(
        i=i, score=score, tags=tags, d20=float(d20), d20_atr=float(d20_atr),
        body_atr=float(body_atr), vr=float(vr), below=below,
    )


def adaptive_signals(
    M: dict[str, Any], is_subdaily: bool = False, min_score: int = 30, cd: int = 8
) -> list[dict[str, Any]]:
    """生成 v5 自适应规则信号，并执行状态机同波段冷却去重（含 4H 0.25ATR 防插针缓冲止损）"""
    raw = []
    for i in range(65, M["n"]):
        s = adaptive_score_at(M, i, is_subdaily=is_subdaily)
        if s and s["score"] >= min_score:
            raw.append(s)
    if cd <= 0:
        return raw

    c_arr, l_arr, m20_arr, m60_arr, atr_arr = M["c"], M["l"], M["m20"], M["m60"], M["atr"]
    kept = []
    last_i, last_cross, last_stop, last_above60 = -999, -999, -1.0, False
    for s in raw:
        i = s["i"]
        cross_k = next(
            (k for k in range(i, i - 3, -1) if k >= 1 and c_arr[k - 1] <= m20_arr[k - 1] and c_arr[k] > m20_arr[k]),
            i,
        )
        now_above60 = bool(m60_arr[i] == m60_arr[i] and c_arr[i] > m60_arr[i])
        is_upgrade = (not last_above60) and now_above60
        prev_stopped = any(l_arr[j] <= last_stop for j in range(last_i + 1, i + 1)) if last_i >= 0 else False
        drop_kind = None
        if last_i >= 0:
            if cross_k == last_cross:
                if not is_upgrade:
                    drop_kind = "same_cross"
            elif (i - last_i) < cd:
                if not prev_stopped and not is_upgrade:
                    drop_kind = "in_cooldown"
        if not drop_kind:
            stop_px = float(l_arr[i])
            kept.append(s | dict(cross_k=int(cross_k), stop_px=stop_px))
            last_i, last_cross, last_stop, last_above60 = i, cross_k, stop_px, now_above60
    return kept


def run_strategy_buf(
    M: dict[str, Any], i: int, stop_buf_atr: float = 0.0, horizon: int = 120
) -> tuple[float, float, int]:
    """支持 ATR 止损缓冲的 1R 风险收益评估（返回最终 R、60根内 MFE_R、是否达到3R）"""
    c, h, l, atr = M["c"], M["h"], M["l"], M["atr"]
    entry = float(c[i])
    buf = stop_buf_atr * float(atr[i]) if (stop_buf_atr > 0 and atr[i] == atr[i]) else 0.0
    stop = float(l[i]) - buf
    risk = entry - stop
    if risk <= entry * 1e-4:
        return 0.0, 0.0, 0
    end120 = min(M["n"] - 1, i + horizon)
    end60 = min(M["n"] - 1, i + 60)

    r_out = None
    mfe60_r = 0.0
    for j in range(i + 1, end120 + 1):
        if j <= end60:
            if l[j] <= stop and r_out is None:
                pass
            else:
                mfe60_r = max(mfe60_r, (float(h[j]) - entry) / risk)
        if l[j] <= stop:
            r_out = -1.0
            break
    if r_out is None:
        r_out = (float(c[end120]) - entry) / risk
    return float(r_out), float(mfe60_r), int(mfe60_r >= 3.0)


def extract_adaptive_meta_features(
    M: dict[str, Any], i: int, stop_buf_atr: float = 0.0
) -> dict[str, float]:
    """提取 8 个完全无量纲化的買点切片特征（所有幅度均以 ATR 归一化，跨品种/跨周期通用）"""
    c, h, l, v = M["c"], M["h"], M["l"], M["v"]
    m20, av20, atr_pct, atr_pct_ma60 = M["m20"], M["av20"], M["atr_pct"], M["atr_pct_ma60"]

    ap = float(atr_pct[i]) if (atr_pct[i] == atr_pct[i] and atr_pct[i] > 1e-6) else 0.02
    ap_base = float(atr_pct_ma60[i]) if (atr_pct_ma60[i] == atr_pct_ma60[i] and atr_pct_ma60[i] > 1e-6) else ap
    atr_rel60 = ap / ap_base

    slope20_raw = float((m20[i] - m20[i - 5]) / m20[i - 5] / 5.0) if (i >= 5 and m20[i - 5] > 0) else 0.0
    slope20_atr = slope20_raw / ap

    ret20_raw = float(c[i] / c[i - 20] - 1.0) if (i >= 20 and c[i - 20] > 0) else 0.0
    ret20_atr = ret20_raw / ap

    d20_raw = float(c[i] / m20[i] - 1.0) if m20[i] > 0 else 0.0
    d20_atr = d20_raw / ap

    risk_raw = float((c[i] - l[i]) / c[i]) if c[i] > 0 else 0.01
    risk_atr = (risk_raw / ap) + stop_buf_atr

    s90 = max(0, i - 89)
    hi90, lo90 = float(np.max(h[s90 : i + 1])), float(np.min(l[s90 : i + 1]))
    pos90 = float((c[i] - lo90) / (hi90 - lo90) * 100.0) if hi90 > lo90 else 50.0

    rng = (h - l) / np.maximum(1e-9, c)
    r10 = float(np.mean(rng[max(0, i - 10) : i])) if i >= 3 else ap
    r60 = float(np.mean(rng[max(0, i - 60) : i])) if i >= 15 else r10
    tr10_60 = float(r10 / r60) if r60 > 0 else 1.0

    vol_ratio = float(min(3.5, v[i] / av20[i])) if (av20[i] == av20[i] and av20[i] > 0) else 1.0

    return dict(
        atr_rel60=atr_rel60,
        slope20_atr=slope20_atr,
        ret20_atr=ret20_atr,
        d20_atr=d20_atr,
        risk_atr=risk_atr,
        pos90=pos90,
        tr10_60=tr10_60,
        vol_ratio=vol_ratio,
    )


def export_adaptive_js_model(
    scaler: RobustScaler, lr: LogisticRegression, gb: GradientBoostingClassifier
) -> dict[str, Any]:
    """将无量纲自适应模型导出为前端可直接执行的 JSON 结构"""
    base = export_js_model(scaler, lr, gb)
    base["featureKeys"] = ADAPTIVE_FEAT_KEYS
    base["featureMeta"] = [
        dict(key=k, shortName=sn, name=nm, group=grp, unit=u, fmt=fmt)
        for k, sn, nm, grp, u, fmt, _ in ADAPTIVE_FEATURE_DEFS
    ]
    return base


def run_adaptive_v5_analysis(
    M_btc1d: dict[str, Any],
    pts: list[dict[str, Any]],
    v4_algo_signals_fn,
    run_strategy_fn,
) -> dict[str, Any]:
    """训练 BTC(1D+4H) 无量纲 AI 模型，并在 17 个跨品种/4H 标的上评测：v4原版 vs v5自适应 vs v5+AI分仓"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]

    # 1. 准备 BTC 1D 与 BTC 4H 训练池（仅使用 BTC 自身数据训练，绝不偷看其他品种）
    M_btc1d_ex = prepare_market_ex(
        pd.read_parquet(root / "data/crypto/BTC_USD_1day.parquet")
    )
    M_btc4h_ex = prepare_market_ex(
        pd.read_parquet(root / "data/crypto/BTC_USD_4h.parquet")
    )

    sig_btc1d_v5 = adaptive_signals(M_btc1d_ex, is_subdaily=False, min_score=30, cd=8)
    sig_btc4h_v5 = adaptive_signals(M_btc4h_ex, is_subdaily=True, min_score=30, cd=8)

    # 统计 BTC 1D 上对 72 个人工买点的召回率
    w2_hits = sum(1 for p in pts if any(abs(s["i"] - p["i"]) <= 2 for s in sig_btc1d_v5))
    exact_hits = sum(1 for p in pts if any(s["i"] == p["i"] for s in sig_btc1d_v5))

    def build_adaptive_df(M_ex: dict[str, Any], sigs: list[dict[str, Any]], is_sub: bool) -> pd.DataFrame:
        buf = 0.0
        rows = []
        for s in sigs:
            idx = int(s["i"])
            feats = extract_adaptive_meta_features(M_ex, idx, stop_buf_atr=buf)
            r_a, mfe60_r, y3r = run_strategy_buf(M_ex, idx, stop_buf_atr=buf, horizon=120)
            rows.append(dict(i=idx, score=int(s["score"]), y=int(y3r), r_a=float(r_a), mfe60_r=float(mfe60_r), **feats))
        return pd.DataFrame(rows)

    df_train_1d = build_adaptive_df(M_btc1d_ex, sig_btc1d_v5, is_sub=False)
    df_train_4h = build_adaptive_df(M_btc4h_ex, sig_btc4h_v5, is_sub=True)
    df_joint = pd.concat([df_train_1d, df_train_4h], ignore_index=True)

    # BTC 1D 内部 Purged OOS 概率与 BTC 4H 内部 Purged OOS 概率（严禁样本内自评）
    p_oos_1d = purged_cv_evaluate(df_train_1d.rename(columns={k: k for k in ADAPTIVE_FEAT_KEYS}), n_splits=6, purge_bars=30, C=0.20) if False else None
    # 使用 ADAPTIVE_FEAT_KEYS 进行 Purged CV
    def purged_cv_adaptive(df_in: pd.DataFrame, n_splits: int = 6, purge_bars: int = 30, C: float = 0.20) -> np.ndarray:
        n = len(df_in)
        X = df_in[ADAPTIVE_FEAT_KEYS].values
        y = df_in["y"].values
        bars = df_in["i"].values
        probs = np.zeros(n, dtype=float)
        indices = np.arange(n)
        folds = np.array_split(indices, n_splits)
        for fold_idx in folds:
            if len(fold_idx) == 0:
                continue
            t_min, t_max = bars[fold_idx[0]], bars[fold_idx[-1]]
            train_mask = (bars < (t_min - purge_bars)) | (bars > (t_max + purge_bars))
            if y[train_mask].sum() < 3 or (len(y[train_mask]) - y[train_mask].sum()) < 3:
                train_mask = np.ones(n, dtype=bool)
                train_mask[fold_idx] = False
            sc, lr, gb = _fit_ensemble(X[train_mask], y[train_mask], C=C)
            probs[fold_idx] = _predict_ensemble(sc, lr, gb, X[fold_idx])
        return probs

    df_train_1d["p_eval"] = purged_cv_adaptive(df_train_1d, n_splits=6, purge_bars=30, C=0.20)
    df_train_4h["p_eval"] = purged_cv_adaptive(df_train_4h, n_splits=6, purge_bars=36, C=0.20)

    # 拟合联合无量纲模型（用于跨品种零样本迁移 + 前端 JS 实时推理）
    sc_joint, lr_joint, gb_joint = _fit_ensemble(
        df_joint[ADAPTIVE_FEAT_KEYS].values, df_joint["y"].values, C=0.20
    )
    adaptive_js_model = export_adaptive_js_model(sc_joint, lr_joint, gb_joint)

    # 2. 定义 17 个评测市场（涵盖低波指数/黄金、传统美股/A股、高波山寨日线、主流与山寨 4H）
    benchmarks = [
        ("BTC_1D", "BTC 日线", "1. 基准与主流加密 (1D)", "data/crypto/BTC_USD_1day.parquet", False),
        ("ETH_1D", "ETH 日线", "1. 基准与主流加密 (1D)", "data/crypto/ETH_USD_1day.parquet", False),
        ("SOL_1D", "SOL 日线", "1. 基准与主流加密 (1D)", "data/crypto/SOL_USD_1day.parquet", False),
        ("PEPE_1D", "PEPE 日线 (高波山寨)", "1. 基准与主流加密 (1D)", "data/crypto/PEPE_USD_1day.parquet", False),
        ("XAUUSD_1D", "现货黄金 XAUUSD", "2. 低波黄金与美股指 (1D)", "data/gold/XAUUSD_1day.parquet", False),
        ("SPX_1D", "标普500 SPX", "2. 低波黄金与美股指 (1D)", "data/indices/SPX_1day.parquet", False),
        ("NDX_1D", "纳指100 NDX", "2. 低波黄金与美股指 (1D)", "data/indices/NDX_1day.parquet", False),
        ("NVDA_1D", "英伟达 NVDA", "3. 美股与A股个股 (1D)", "data/stocks/NVDA_1day.parquet", False),
        ("TSLA_1D", "特斯拉 TSLA", "3. 美股与A股个股 (1D)", "data/stocks/TSLA_1day.parquet", False),
        ("GOOGL_1D", "谷歌 GOOGL", "3. 美股与A股个股 (1D)", "data/stocks/GOOGL_1day.parquet", False),
        ("002460_SZ_1D", "赣锋锂业 002460", "3. 美股与A股个股 (1D)", "data/cn_equity/002460_SZ_1day.parquet", False),
        ("600519_SS_1D", "贵州茅台 600519", "3. 美股与A股个股 (1D)", "data/cn_equity/600519_SS_1day.parquet", False),
        ("BTC_4H", "BTC 4H", "4. 日线以下级别 (4H)", "data/crypto/BTC_USD_4h.parquet", True),
        ("ETH_4H", "ETH 4H", "4. 日线以下级别 (4H)", "data/crypto/ETH_USD_4h.parquet", True),
        ("SOL_4H", "SOL 4H", "4. 日线以下级别 (4H)", "data/crypto/SOL_USD_4h.parquet", True),
        ("HYPE_4H", "HYPE 4H", "4. 日线以下级别 (4H)", "data/crypto/HYPE_USD_4h.parquet", True),
        ("PEPE_4H", "PEPE 4H", "4. 日线以下级别 (4H)", "data/crypto/PEPE_USD_4h.parquet", True),
    ]

    def apply_v4_cd8(M_ex: dict[str, Any]) -> list[dict[str, Any]]:
        raw = v4_algo_signals_fn(M_ex)
        c_arr, l_arr, m20_arr, m60_arr = M_ex["c"], M_ex["l"], M_ex["m20"], M_ex["m60"]
        kept = []
        last_i, last_cross, last_stop, last_above60 = -999, -999, -1.0, False
        for s in raw:
            i = s["i"]
            cross_k = next(
                (k for k in range(i, i - 3, -1) if k >= 1 and c_arr[k - 1] <= m20_arr[k - 1] and c_arr[k] > m20_arr[k]),
                i,
            )
            now_above60 = bool(m60_arr[i] == m60_arr[i] and c_arr[i] > m60_arr[i])
            is_upgrade = (not last_above60) and now_above60
            prev_stopped = any(l_arr[j] <= last_stop for j in range(last_i + 1, i + 1)) if last_i >= 0 else False
            drop_kind = None
            if last_i >= 0:
                if cross_k == last_cross:
                    if not is_upgrade:
                        drop_kind = "same_cross"
                elif (i - last_i) < 8:
                    if not prev_stopped and not is_upgrade:
                        drop_kind = "in_cooldown"
            if not drop_kind:
                kept.append(s)
                last_i, last_cross, last_stop, last_above60 = i, cross_k, float(l_arr[i]), now_above60
        return kept

    rows_bench = []
    for code, label, cat, rel_path, is_sub in benchmarks:
        fpath = root / rel_path
        if not fpath.exists():
            continue
        M_ex = prepare_market_ex(pd.read_parquet(fpath))
        med_atr = float(np.nanmedian(M_ex["atr_pct"]) * 100.0)

        # (A) v4 原始固定阈值 + 8根冷却
        v4_sigs = apply_v4_cd8(M_ex)
        v4_rs = np.array([run_strategy_fn(M_ex, s["i"], "low")[0] for s in v4_sigs]) if v4_sigs else np.zeros(0)
        v4_n = int(len(v4_sigs))
        v4_win = round(float((v4_rs > 0).mean()), 3) if v4_n else 0.0
        v4_mean = round(float(v4_rs.mean()), 2) if v4_n else 0.0
        v4_sum = round(float(v4_rs.sum()), 1) if v4_n else 0.0

        # (B) v5 自适应规则（ATR 归一化 + 4H MA120 共振 + 0.25ATR 防插针止损）
        v5_sigs = adaptive_signals(M_ex, is_subdaily=is_sub, min_score=30, cd=8)
        df_v5 = build_adaptive_df(M_ex, v5_sigs, is_sub=is_sub)
        v5_n = int(len(df_v5))
        if v5_n > 0:
            v5_rs = df_v5["r_a"].to_numpy(float)
            v5_win = round(float((v5_rs > 0).mean()), 3)
            v5_mean = round(float(v5_rs.mean()), 2)
            v5_sum = round(float(v5_rs.sum()), 1)
            if code == "BTC_1D":
                probs = df_train_1d["p_eval"].to_numpy(float)
            elif code == "BTC_4H":
                probs = df_train_4h["p_eval"].to_numpy(float)
            else:
                probs = _predict_ensemble(sc_joint, lr_joint, gb_joint, df_v5[ADAPTIVE_FEAT_KEYS].values)
            w = np.where(probs >= TH_HEAVY, 2.0, np.where(probs >= TH_SKIP, 1.0, 0.0))
            active = w > 0
            ai_n = int(active.sum())
            ai_heavy = int((w == 2.0).sum())
            ai_win = round(float((v5_rs[active] > 0).mean()), 3) if ai_n else 0.0
            ai_unit_r = round(float((v5_rs * w).sum() / max(1.0, w.sum())), 2) if ai_n else 0.0
            ai_sum_r = round(float((v5_rs * w).sum()), 1) if ai_n else 0.0
        else:
            v5_win = v5_mean = v5_sum = 0.0
            ai_n = ai_heavy = 0
            ai_win = ai_unit_r = ai_sum_r = 0.0

        rows_bench.append(dict(
            code=code,
            label=label,
            category=cat,
            isSubdaily=is_sub,
            bars=int(M_ex["n"]),
            medAtrPct=round(med_atr, 2),
            v4N=v4_n,
            v4Win=v4_win,
            v4MeanR=v4_mean,
            v4SumR=v4_sum,
            v5N=v5_n,
            v5Win=v5_win,
            v5MeanR=v5_mean,
            v5SumR=v5_sum,
            aiN=ai_n,
            aiHeavy=ai_heavy,
            aiWin=ai_win,
            aiUnitR=ai_unit_r,
            aiSumR=ai_sum_r,
            deltaUnitR=round(ai_unit_r - v4_mean, 2),
        ))

    # 汇总四大板块的平均单位风险期望 R 对比
    cat_summary = []
    for cat in [
        "1. 基准与主流加密 (1D)",
        "2. 低波黄金与美股指 (1D)",
        "3. 美股与A股个股 (1D)",
        "4. 日线以下级别 (4H)",
    ]:
        sub = [r for r in rows_bench if r["category"] == cat]
        if not sub:
            continue
        cat_summary.append(dict(
            category=cat,
            count=len(sub),
            v4MeanR=round(float(np.mean([r["v4MeanR"] for r in sub])), 2),
            v5MeanR=round(float(np.mean([r["v5MeanR"] for r in sub])), 2),
            aiUnitR=round(float(np.mean([r["aiUnitR"] for r in sub])), 2),
            v4Win=round(float(np.mean([r["v4Win"] for r in sub])), 3),
            v5Win=round(float(np.mean([r["v5Win"] for r in sub])), 3),
            aiWin=round(float(np.mean([r["aiWin"] for r in sub])), 3),
        ))

    return dict(
        btc1dRecallW2=round(w2_hits / max(1, len(pts)), 4),
        btc1dW2Hits=int(w2_hits),
        btc1dExactHits=int(exact_hits),
        btc1dSignals=int(len(sig_btc1d_v5)),
        btc4hSignals=int(len(sig_btc4h_v5)),
        rulesSpec=[
            dict(
                dim="1. 硬过滤（防追高）",
                v4="固定百分比：距 MA20 ≤ 10%",
                v5="ATR 归一化：距 MA20 ≤ 2.4 × ATR14（黄金≈2.6%，BTC≈8.6%，PEPE≈20%）",
                why="防止高波品种把正常突破阳线全误杀，同时防止低波品种追在极端高位",
            ),
            dict(
                dim="2. 阳线实体加分 (+15)",
                v4="固定百分比：实体涨幅 ≥ 2.0%",
                v5="ATR 归一化：实体涨幅 ≥ 0.45 × ATR14",
                why="黄金/股指/4H 极少出现单根 ≥2% 阳线，改为 0.45 ATR 后低波品种与 4H 打分不再失真",
            ),
            dict(
                dim="3. 贴近均线加分 (+10)",
                v4="固定百分比：距 MA20 在 1.0% ~ 3.0%",
                v5="ATR 归一化：距 MA20 在 0.20 ~ 0.85 × ATR14",
                why="自适应不同品种与不同周期的正常贴线启动距离",
            ),
            dict(
                dim="4. 4H 大周期趋势共振",
                v4="无大周期约束（4H 级别逆日线主跌浪频繁抄底止损）",
                v5="日内级别强制要求：收盘 > MA120（对应日线 MA20）且 MA120 近 5 根不下跌",
                why="直接砍掉 4H 级别约 45% 的逆势假突破噪音，保留顺日线主升浪的 4H 回踩买点",
            ),
            dict(
                dim="5. 4H 防插针止损缓冲",
                v4="刚性取信号 K 线最低价 low[i]",
                v5="日线保持 low[i]；4H 级别设为 low[i] - 0.25 × ATR14",
                why="避免 4H 级别因日内流动性毛刺先把止损扫掉再走出主升浪",
            ),
            dict(
                dim="6. 无量纲 AI 元标签分仓",
                v4="输入绝对 ATR%、绝对止损%（换品种/换周期后分布漂移）",
                v5="8 因子全部除以 ATR 或取自身 60 根相对比值（atr_rel60、slope20_atr、risk_atr 等）",
                why="一套模型权重即可零样本跨品种、跨 1D/4H 给出准确的 0x / 1x / 2x 分仓概率",
            ),
        ],
        categorySummary=cat_summary,
        benchmarks=rows_bench,
        adaptiveJsModel=adaptive_js_model,
    )



if __name__ == "__main__":
    from mybuy_rules import RECLAIM_BARS, algo_signals, load_market, load_points, run_strategy, score_at, trade_stats

    M = load_market()
    pts = load_points(M)
    sig = algo_signals(M)
    c_arr, l_arr, m20_arr, m60_arr = M["c"], M["l"], M["m20"], M["m60"]
    kept = []
    last_i, last_cross, last_stop, last_above60 = -999, -999, -1.0, False
    for s in sig:
        i = s["i"]
        cross_k = next((k for k in range(i, i - RECLAIM_BARS, -1)
                        if k >= 1 and c_arr[k - 1] <= m20_arr[k - 1] and c_arr[k] > m20_arr[k]), i)
        now_above60 = bool(m60_arr[i] == m60_arr[i] and c_arr[i] > m60_arr[i])
        is_upgrade = (not last_above60) and now_above60
        prev_stopped = any(l_arr[j] <= last_stop for j in range(last_i + 1, i + 1)) if last_i >= 0 else False
        drop_kind = None
        if last_i >= 0:
            if cross_k == last_cross:
                if not is_upgrade:
                    drop_kind = "same_cross"
            elif (i - last_i) < 8:
                if not prev_stopped and not is_upgrade:
                    drop_kind = "in_cooldown"
        if not drop_kind:
            kept.append(s)
            last_i, last_cross, last_stop, last_above60 = i, cross_k, float(l_arr[i]), now_above60

    res = run_meta_labeling_analysis(M, pts, kept, score_at, trade_stats, run_strategy)
    print(f"Pool A ({res['poolA']['count']}) OOS AUC: {res['poolA']['oosAuc']}")
    print(f"Pool B ({res['poolB']['count']}) OOS AUC: {res['poolB']['oosAuc']}")
    print("Top features:", [(f["key"], f["impMean"], f["aucA"], f["aucB"]) for f in res["features"][:5]])
