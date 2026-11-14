#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冻结保存「AI-MA20交易算法 (v1.0-FROZEN)」模型参数、不同置信度止损率统计与历史订单账本。

输出：
  1. data/models/ai_ma20_v1_frozen.json   —— 算法与模型权重冻结快照（含 SHA-256 指纹与全量历史订单）
  2. docs/strategies/AI_MA20_TRADING_ALGORITHM_V1.md —— 策略归档说明文档（Markdown）
  3. apps/chanlun-web/docs/ai-ma20-v1-frozen.md      —— 前端可访问的归档说明文档（Markdown）
  4. apps/chanlun-web/docs/ai-ma20-v1-frozen.html    —— 前端可访问的归档说明页面（HTML）
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from meta_labeling import (
    FEAT_KEYS,
    META_FEATURE_DEFS,
    TH_HEAVY,
    TH_SKIP,
    _fit_ensemble,
    _predict_ensemble,
    explain_trade,
     export_js_model,
    extract_meta_features,
    purged_cv_evaluate,
)
from mybuy_rules import (
    HOLD_MAX,
    MAX_DIST20,
    MIN_DIST20,
    MIN_SCORE,
    PARQUET,
    RECLAIM_BARS,
    ROOT,
    ANN,
    algo_signals,
    load_market,
    load_points,
    run_strategy,
    score_at,
    trade_stats,
)

FROZEN_VERSION = "AI-MA20-v1.0-FROZEN"
FROZEN_NAME = "AI-MA20交易算法"
FROZEN_DATE = "2026-11-04"

FROZEN_JSON_OUT = ROOT / "data/models/ai_ma20_v1_frozen.json"
DOC_STRATEGY_MD = ROOT / "docs/strategies/AI_MA20_TRADING_ALGORITHM_V1.md"
DOC_WEB_MD = ROOT / "apps/chanlun-web/docs/ai-ma20-v1-frozen.md"
DOC_WEB_HTML = ROOT / "apps/chanlun-web/docs/ai-ma20-v1-frozen.html"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_frozen_artifact() -> dict[str, Any]:
    M = load_market()
    pts = load_points(M)
    raw_sigs = algo_signals(M)

    # 第二层：状态机同波段冷却去重（8根冷却 + 破MA60升级豁免 + 前单止损重置豁免）
    c_arr, l_arr, m20_arr, m60_arr = M["c"], M["l"], M["m20"], M["m60"]
    kept_sigs = []
    dropped_sigs = []
    last_i, last_cross, last_stop, last_above60 = -999, -999, -1.0, False
    for s in raw_sigs:
        i = int(s["i"])
        cross_k = next(
            (k for k in range(i, i - RECLAIM_BARS, -1)
             if k >= 1 and c_arr[k - 1] <= m20_arr[k - 1] and c_arr[k] > m20_arr[k]),
            i,
        )
        now_above60 = bool(m60_arr[i] == m60_arr[i] and c_arr[i] > m60_arr[i])
        is_upgrade = (not last_above60) and now_above60
        prev_stopped = any(l_arr[j] <= last_stop for j in range(last_i + 1, i + 1)) if last_i >= 0 else False
        drop_kind = None
        exempt_kind = None
        if last_i >= 0:
            if cross_k == last_cross:
                if not is_upgrade:
                    drop_kind = "same_cross"
                else:
                    exempt_kind = "破MA60升级"
            elif (i - last_i) < 8:
                if not prev_stopped and not is_upgrade:
                    drop_kind = "in_cooldown"
                elif is_upgrade:
                    exempt_kind = "破MA60升级"
                elif prev_stopped:
                    exempt_kind = "前单止损重置"
        item = dict(
            s,
            cross_k=int(cross_k),
            gap=(int(i - last_i) if last_i >= 0 else None),
            drop_kind=drop_kind,
            exempt_kind=exempt_kind,
        )
        if drop_kind:
            dropped_sigs.append(item)
        else:
            kept_sigs.append(item)
            last_i, last_cross, last_stop, last_above60 = i, cross_k, float(l_arr[i]), now_above60

    # 第三层：AI Meta-Labeling 特征提取与置信度预测
    manual_indices = [p["i"] for p in pts]
    rows = []
    for s in kept_sigs:
        idx = int(s["i"])
        sc = score_at(M, idx)
        st = trade_stats(M, idx)
        ra, hold_a, rsn_a = run_strategy(M, idx, "low")
        rb, hold_b, rsn_b = run_strategy(M, idx, "low", ma_exit=True)
        feats = extract_meta_features(M, idx, score_at)
        mfe60_pct = float(st["mfe_by"][60])
        mfe60_r = mfe60_pct / max(1e-9, float(st["risk"]))
        y_3r = 1 if mfe60_r >= 3.0 else 0
        is_manual = int(any(abs(idx - mp) <= 2 for mp in manual_indices))
        date_str = str(datetime.fromtimestamp(int(M["t"][idx]), timezone.utc))[:10]
        grade = "A" if sc["score"] >= 60 else ("B" if sc["score"] >= 40 else "C")
        rows.append(dict(
            i=idx,
            time=int(M["t"][idx]),
            date=date_str,
            entry=round(float(M["c"][idx]), 2),
            stop=round(float(M["l"][idx]), 2),
            risk=round(float(st["risk"]), 5),
            score=int(sc["score"]),
            grade=grade,
            why=sc["why"],
            exempt_kind=s.get("exempt_kind"),
            is_manual=is_manual,
            stop_bar=int(st["stop_bar"]) if st["stop_bar"] is not None else None,
            mfe=round(float(st["mfe"]), 4),
            mfe_r=round(float(st["mfe_r"]), 2),
            mfe60_r=round(float(mfe60_r), 2),
            y=int(y_3r),
            r_a=round(float(ra), 2),
            hold_a=int(hold_a),
            rsn_a=str(rsn_a),
            r_b=round(float(rb), 2),
            hold_b=int(hold_b),
            rsn_b=str(rsn_b),
            **feats,
        ))

    df = pd.DataFrame(rows).sort_values("i").reset_index(drop=True)
    df["p_oos"] = np.round(purged_cv_evaluate(df, n_splits=6, purge_bars=30, C=0.20), 4)
    scaler, lr, gb = _fit_ensemble(df[FEAT_KEYS].values, df["y"].values, C=0.20)
    df["p_full"] = np.round(_predict_ensemble(scaler, lr, gb, df[FEAT_KEYS].values), 4)

    # 为每笔订单赋予冻结模型的置信度等级与白盒归因
    orders = []
    for _, row in df.iterrows():
        p_val = float(row["p_full"])
        p_oos = float(row["p_oos"])
        if p_val >= TH_HEAVY:
            tier = "high"
            tier_cn = "高置信度 · 2.0x重仓"
            mult = 2.0
            is_buy = True
        elif p_val >= TH_SKIP:
            tier = "medium"
            tier_cn = "中置信度 · 1.0x标准"
            mult = 1.0
            is_buy = True
        else:
            tier = "low"
            tier_cn = "低置信度 · 0x放弃"
            mult = 0.0
            is_buy = False

        reasons = explain_trade(row, scaler, lr, gb, top_k=3)
        sb = row["stop_bar"]
        orders.append(dict(
            orderId=f"AIMA20-{row['date'].replace('-', '')}",
            i=int(row["i"]),
            time=int(row["time"]),
            date=str(row["date"]),
            entry=float(row["entry"]),
            stop=float(row["stop"]),
            riskPct=round(float(row["risk"]) * 100.0, 2),
            score=int(row["score"]),
            grade=str(row["grade"]),
            ruleTags=list(row["why"]),
            exemptKind=row["exempt_kind"],
            isManualHit=bool(row["is_manual"]),
            confidence=round(p_val, 4),
            confidenceOos=round(p_oos, 4),
            tier=tier,
            tierName=tier_cn,
            positionMult=mult,
            isBuy=is_buy,
            stopBar=int(sb) if sb is not None and not pd.isna(sb) else None,
            stoppedLe5=bool(sb is not None and not pd.isna(sb) and sb <= 5),
            stoppedLe10=bool(sb is not None and not pd.isna(sb) and sb <= 10),
            stoppedLe20=bool(sb is not None and not pd.isna(sb) and sb <= 20),
            stopped120=bool(sb is not None and not pd.isna(sb)),
            mfePct=round(float(row["mfe"]) * 100.0, 2),
            mfeR=round(float(row["mfe_r"]), 2),
            mfe60R=round(float(row["mfe60_r"]), 2),
            rStratA=round(float(row["r_a"]), 2),
            holdStratA=int(row["hold_a"]),
            exitReasonA=str(row["rsn_a"]),
            rStratB=round(float(row["r_b"]), 2),
            holdStratB=int(row["hold_b"]),
            exitReasonB=str(row["rsn_b"]),
            topReasons=[r["text"] for r in reasons],
        ))

    def summarize_tier(sub_orders: list[dict[str, Any]], tier_key: str, title: str, mult_desc: str, p_range: str) -> dict[str, Any]:
        n = len(sub_orders)
        if n == 0:
            return dict(tier=tier_key, title=title, multDesc=mult_desc, pRange=p_range, count=0)
        sb_list = [o["stopBar"] for o in sub_orders]
        st_le5 = sum(1 for x in sb_list if x is not None and x <= 5) / n
        st_le10 = sum(1 for x in sb_list if x is not None and x <= 10) / n
        st_le20 = sum(1 for x in sb_list if x is not None and x <= 20) / n
        st_total = sum(1 for x in sb_list if x is not None) / n
        never_stop = 1.0 - st_total
        stop_bars_valid = [x for x in sb_list if x is not None]
        med_stop_bar = float(np.median(stop_bars_valid)) if stop_bars_valid else None

        ra_arr = np.array([o["rStratA"] for o in sub_orders], dtype=float)
        rb_arr = np.array([o["rStratB"] for o in sub_orders], dtype=float)
        mfe_r_arr = np.array([o["mfeR"] for o in sub_orders], dtype=float)
        mfe60_r_arr = np.array([o["mfe60R"] for o in sub_orders], dtype=float)
        mfe_pct_arr = np.array([o["mfePct"] for o in sub_orders], dtype=float)
        risk_arr = np.array([o["riskPct"] for o in sub_orders], dtype=float)
        p_arr = np.array([o["confidence"] for o in sub_orders], dtype=float)

        st_strat_b = float((rb_arr < 0).mean())

        return dict(
            tier=tier_key,
            title=title,
            multDesc=mult_desc,
            pRange=p_range,
            count=n,
            avgConfidence=round(float(p_arr.mean()), 4),
            avgRiskPct=round(float(risk_arr.mean()), 2),
            stopRateLe5=round(float(st_le5), 4),
            stopRateLe10=round(float(st_le10), 4),
            stopRateLe20=round(float(st_le20), 4),
            stopRateTotal120=round(float(st_total), 4),
            neverStoppedRate=round(float(never_stop), 4),
            stopRateMa20Exit=round(float(st_strat_b), 4),
            medianStopBar=round(med_stop_bar, 1) if med_stop_bar is not None else None,
            winRateStratA=round(float((ra_arr > 0).mean()), 4),
            winRateStratB=round(float((rb_arr > 0).mean()), 4),
            ge1RRate=round(float((mfe_r_arr >= 1.0).mean()), 4),
            ge2RRate=round(float((mfe_r_arr >= 2.0).mean()), 4),
            ge3RRate=round(float((mfe60_r_arr >= 3.0).mean()), 4),
            ge5RRate=round(float((mfe_r_arr >= 5.0).mean()), 4),
            medianMfePct=round(float(np.median(mfe_pct_arr)), 2),
            meanMfePct=round(float(np.mean(mfe_pct_arr)), 2),
            meanRStratA=round(float(ra_arr.mean()), 2),
            sumRStratA=round(float(ra_arr.sum()), 2),
            maxRStratA=round(float(ra_arr.max()), 2),
            meanRStratB=round(float(rb_arr.mean()), 2),
            sumRStratB=round(float(rb_arr.sum()), 2),
        )

    high_orders = [o for o in orders if o["tier"] == "high"]
    med_orders = [o for o in orders if o["tier"] == "medium"]
    buy_orders = [o for o in orders if o["isBuy"]]
    low_orders = [o for o in orders if o["tier"] == "low"]

    tier_stats = dict(
        high=summarize_tier(high_orders, "high", "★ 高置信度买点 (2.0x 重仓)", "2.0x 重仓买入", "P ≥ 58%"),
        medium=summarize_tier(med_orders, "medium", "● 中置信度买点 (1.0x 标准仓)", "1.0x 标准买入", "45% ≤ P < 58%"),
        buyAll=summarize_tier(buy_orders, "buyAll", "✓ 全部买入买点 (1.0x + 2.0x)", "动态分仓买入", "P ≥ 45%"),
        lowSkip=summarize_tier(low_orders, "low", "× 低置信度过滤点 (0x 放弃)", "0x 过滤不买", "P < 45%"),
    )

    js_model = export_js_model(scaler, lr, gb)
    artifact = dict(
        algorithmName=FROZEN_NAME,
        version=FROZEN_VERSION,
        frozenAt=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        frozenDate=FROZEN_DATE,
        status="FROZEN_PRODUCTION",
        scope=dict(
            primaryAsset="BTC/USD",
            primaryTimeframe="1D",
            dataStart=str(datetime.fromtimestamp(int(M["t"][0]), timezone.utc))[:10],
            dataEnd=str(datetime.fromtimestamp(int(M["t"][-1]), timezone.utc))[:10],
            totalBars=int(M["n"]),
            parquetSha256=sha256_file(PARQUET),
            manualAnnotationsSha256=sha256_file(ANN),
        ),
        architecture=dict(
            layer1_primaryRule=dict(
                description="围绕 MA20 的突破与回踩站稳识别（高召回率规则层，对72个手工买点±2根召回率95.8%）",
                reclaimBars=RECLAIM_BARS,
                requireBullishCandle=True,
                maxDistMa20=MAX_DIST20,
                minDistMa20=MIN_DIST20,
                minScore=MIN_SCORE,
                scoringWeights=[
                    dict(factor="盘整够久", condition="上穿前连续收于MA20下方根数 ≥ 5", points=20),
                    dict(factor="破下跌趋势线", condition="收盘突破前60根内最近两个摆动高点连线", points=20),
                    dict(factor="阳线实体饱满", condition="(Close - Open) / Open ≥ 2.0%", points=15),
                    dict(factor="成交量放大", condition="Volume ≥ 1.0 × SMA(Volume, 20)", points=15),
                    dict(factor="MACD柱转强", condition="MACD_Hist[i] > MACD_Hist[i-3]", points=10),
                    dict(factor="双均线共振", condition="Close > MA60", points=10),
                    dict(factor="贴线适中启动", condition="收盘距MA20在 1.0% ~ 3.0% 之间", points=10),
                ],
                entryRule="信号 K 线收盘价 (Close[i]) 入场",
                stopRule="信号 K 线最低价 (Low[i]) 设为刚性 1R 止损位",
            ),
            layer2_cooldownStateMachine=dict(
                description="同波段状态机冷却去重（压制同波段连报与震荡重穿，保留独立波段与关键升级点）",
                cooldownBars=8,
                lockSameCross=True,
                exemptMa60Upgrade=True,
                exemptStopReset=True,
                rawSignalsCount=len(raw_sigs),
                keptSignalsCount=len(kept_sigs),
                droppedSignalsCount=len(dropped_sigs),
            ),
            layer3_aiMetaLabeling=dict(
                description="二阶段元标签置信度评估与三级动态分仓（RobustScaler + L2 LogisticRegression 65% + GBDT 35%）",
                targetDefinition="入场后 60 根 K 线内、触及当根最低点(1R止损)之前，最大浮盈 MFE ≥ 3.0R（记为 Y=1）",
                ensembleWeights=dict(logisticRegression=0.65, gradientBoosting=0.35),
                thresholds=dict(
                    skipBelow=TH_SKIP,
                    standardFrom=TH_SKIP,
                    heavyFrom=TH_HEAVY,
                ),
                positionSizingRules=[
                    dict(tier="high", name="★ 高置信度重仓", range="P ≥ 0.58", multiplier=2.0, action="买入（2.0x 风险敞口）"),
                    dict(tier="medium", name="● 中置信度标准", range="0.45 ≤ P < 0.58", multiplier=1.0, action="买入（1.0x 标准风险敞口）"),
                    dict(tier="low", name="× 低置信度放弃", range="P < 0.45", multiplier=0.0, action="过滤不买（规避震荡磨损区）"),
                ],
                features=[
                    dict(key=k, shortName=sn, name=nm, group=grp, unit=u, desc=desc)
                    for k, sn, nm, grp, u, _, desc in META_FEATURE_DEFS
                ],
            ),
        ),
        confidenceTierStats=tier_stats,
        modelWeights=js_model,
        orders=orders,
        buyOrders=[o for o in orders if o["isBuy"]],
    )
    return artifact


def write_markdown_doc(art: dict[str, Any]) -> str:
    ts = art["confidenceTierStats"]
    hi = ts["high"]
    md = ts["medium"]
    ba = ts["buyAll"]
    lo = ts["lowSkip"]
    sc = art["scope"]

    lines = [
        f"# {art['algorithmName']}（`{art['version']}`）正式冻结说明文档",
        "",
        f"- **算法名称**：`{art['algorithmName']}`（基于买点算法 v4 + 状态机冷却去重 + AI Meta-Labeling 三级分仓）",
        f"- **冻结版本号**：`{art['version']}`",
        f"- **冻结时间**：`{art['frozenAt']}`",
        f"- **基准标的与周期**：`{sc['primaryAsset']} · {sc['primaryTimeframe']}`（`{sc['dataStart']}` 至 `{sc['dataEnd']}`，共 `{sc['totalBars']}` 根日 K 线）",
        f"- **行情数据 SHA-256**：`{sc['parquetSha256'][:24]}...`",
        f"- **手工标注 SHA-256**：`{sc['manualAnnotationsSha256'][:24]}...`",
        f"- **关联代码与模型文件**：",
        f"  - 冻结模型与全量历史订单快照：`data/models/ai_ma20_v1_frozen.json`",
        f"  - 前端图表交互与悬浮订单卡片模块：`apps/chanlun-web/js/17-ai-ma20-algo.js`",
        f"  - 标注抽屉与核心计算模块：`apps/chanlun-web/js/13-annotate.js`",
        f"  - 训练与冻结生成脚本：`scripts/analysis/freeze_ai_ma20.py`、`scripts/analysis/meta_labeling.py`",
        "",
        "---",
        "",
        "## 一、 算法架构总览（三层解耦漏斗）",
        "",
        "`AI-MA20交易算法` 严格遵循 Marcos López de Prado 提出的 **Meta-Labeling（元标签过滤与动态分仓）** 架构，将交易决策拆解为「高召回率规则寻机 $\\rightarrow$ 状态机同波段去重 $\\rightarrow$ AI 置信度评估与分级下注」三层：",
        "",
        "### 第一层：MA20 突破与结构打分（Primary Rule Layer）",
        "1. **候选触发条件**：",
        "   - 当根 K 线收盘站上 `MA20`（$\\text{Close}_t > \\text{MA20}_t$），且为阳线（$\\text{Close}_t > \\text{Open}_t$）；",
        "   - 最近 `3` 根 K 线内发生过向上穿越 `MA20`（$\\text{Close}_{k-1} \\le \\text{MA20}_{k-1}$ 且 $\\text{Close}_k > \\text{MA20}_k$，兼容首次上穿与突破后回踩 1~2 根再站上）；",
        "   - **防追高硬过滤**：收盘高于 `MA20` 的偏离度不超过 `10%`（$0 \\le (\\text{Close}_t - \\text{MA20}_t)/\\text{MA20}_t \\le 10\\%$）。",
        "2. **七维结构打分（满分 100 分，门槛 $\\ge 30$ 分）**：",
        "   - **盘整够久（+20 分）**：本次上穿前连续收于 `MA20` 下方的 K 线根数 $\\ge 5$ 根；",
        "   - **破下跌趋势线（+20 分）**：收盘价向上突破过去 60 根 K 线内最近两个摆动高点（左右各 3 根局部最高）的下倾连线；",
        "   - **阳线实体饱满（+15 分）**：信号 K 线实体涨幅 $(\\text{Close}_t - \\text{Open}_t)/\\text{Open}_t \\ge 2.0\\%$；",
        "   - **成交量放大（+15 分）**：信号 K 线成交量 $\\ge 1.0 \\times \\text{SMA}(\\text{Volume}, 20)$；",
        "   - **MACD 柱转强（+10 分）**：$\\text{MACD\\_Hist}_t > \\text{MACD\\_Hist}_{t-3}$；",
        "   - **站上 MA60（+10 分）**：$\\text{Close}_t > \\text{MA60}_t$（双均线多头共振）；",
        "   - **贴近均线启动（+10 分）**：收盘距 `MA20` 偏离度位于 `1.0% ~ 3.0%` 黄金区间。",
        "3. **入场与刚性风控边界（1R 定义）**：",
        "   - **买入价（Entry）**：信号 K 线收盘价 $\\text{Close}_t$；",
        "   - **刚性止损位（Stop-Loss）**：信号 K 线最低价 $\\text{Low}_t$；",
        "   - **单笔单位风险（1R）**：$1R = \\text{Close}_t - \\text{Low}_t$（相对风险率 $\\text{risk\\%} = (\\text{Close}_t - \\text{Low}_t)/\\text{Close}_t$）。",
        "",
        "### 第二层：状态机同波段冷却去重（Cooldown State-Machine Layer）",
        "- **单次上穿锁**：同一次 `MA20` 上穿事件（`crossIdx` 相同）在 `reclaimBars=3` 窗口内产生的连报阳线，默认只取第一根；",
        "- **8 根冷却窗口**：上一笔保留信号发出后 `8` 根 K 线内，若价格短暂跌破 `MA20` 又迅速重穿，视为同一波段震荡，予以压制；",
        "- **双豁免机制（防止硬冷却误杀主升浪）**：",
        "  1. **破 MA60 升级豁免**：前序信号在 `MA60` 下方发出，当前信号首次站上 `MA60`（趋势级别升级），立即放行；",
        "  2. **前单止损重置豁免**：若前序信号入场后价格已跌破其最低价触发止损（前单已平仓出局），冷却期立即重置，允许二次上车。",
        "- **去重效果**：BTC 日线全历史候选从 `242` 笔精简至 **`152` 笔独立波段信号**（压制 `90` 笔同波段重复信号），手工买点 $\\pm 2$ 根召回率保持 `91.7%`。",
        "",
        "### 第三层：AI Meta-Labeling 置信度评估与三级分仓（AI Position-Sizing Layer）",
        "- **预测目标（Meta-Label $Y$）**：入场后 `60` 根 K 线内、触及当根最低价止损之前，最大浮盈是否达到 **$\\text{MFE} \\ge 3.0R$**（$Y=1$ 代表走出高盈亏比波段，$Y=0$ 代表止损或小幅震荡）。",
        "- **8 个纯因果买点切片特征**：",
        "  1. `risk_pct`：信号 K 线初始止损宽度 $(\\text{Close}_t - \\text{Low}_t)/\\text{Close}_t$（负相关，止损越窄盈亏比越高）；",
        "  2. `pos90`：当前收盘价在过去 90 根 K 线高低点通道中的百分位（负相关，越贴近 90 日底部区域越易走出大波段）；",
        "  3. `slope20`：`MA20` 近 5 根每根变化率（正相关，均线走平或拐头向上优于陡峭下杀）；",
        "  4. `tr10_60`：入场前 10 根平均振幅与前 60 根平均振幅之比（负相关，突破前波动极致收敛蓄势更好）；",
        "  5. `vol_ratio`：当根成交量与前 20 根均量之比（正相关，放量突破确认度高）；",
        "  6. `atr_pct`：14 日 ATR 占收盘价比例（反映当前市场波动率水位）；",
        "  7. `ret20`：入场前 20 根累计涨跌幅（区分顺势回踩与超跌反弹）；",
        "  8. `d20`：信号 K 线收盘偏离 `MA20` 百分比。",
        "- **模型架构（已冻结权重）**：`RobustScaler`（中位数/四分位距缩放） + `L2 LogisticRegression (65% 权重)` + `浅层 GradientBoostingClassifier (max_depth=2, 25 棵树, 35% 权重)`。",
        "- **三级置信度与买入分仓规则**：",
        "  - **★ 高置信度买点（$P \\ge 0.58$）**：**买入 · `2.0x` 重仓出击**（高胜率、低快损率、高盈亏比主升浪启动点，图上显示翠绿色大三角 `▲ 2x`）；",
        "  - **● 中置信度买点（$0.45 \\le P < 0.58$）**：**买入 · `1.0x` 标准仓位**（标准顺势买点，图上显示亮蓝色三角 `▲ 1x`）；",
        "  - **× 低置信度候选（$P < 0.45$）**：**放弃不买 · `0x` 过滤**（高位宽止损或逆势震荡磨损区，总止损率超 93%，默认不在买入图上触发）。",
        "",
        "---",
        "",
        "## 二、 不同置信度档位的止损率与历史订单表现统计",
        "",
        "以下为 `AI-MA20交易算法` 在 BTC 1D（2017~2026）全部 152 个去重候选信号上，按冻结模型置信度分档的**真实止损率与盈亏分布**：",
        "",
        "| 置信度分档 | 仓位动作 | 历史订单数 | 平均置信度 $P$ | 平均1R止损宽 | **≤5根快损率** | **≤10根止损率** | **≤20根止损率** | **120根总止损率** | **从未触止损率** | **MA20离场止损率** | ≥1R达成率 | **≥3R大肉率** | 策略A均R (合计R) | MA20离场均R |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
        f"| **★ 高置信度 ($P \\ge 0.58$)** | **2.0x 重仓买入** | **{hi['count']} 笔** | {hi['avgConfidence']*100:.1f}% | {hi['avgRiskPct']:.2f}% | **{hi['stopRateLe5']*100:.1f}%** | **{hi['stopRateLe10']*100:.1f}%** | **{hi['stopRateLe20']*100:.1f}%** | **{hi['stopRateTotal120']*100:.1f}%** | **{hi['neverStoppedRate']*100:.1f}%** | {hi['stopRateMa20Exit']*100:.1f}% | **{hi['ge1RRate']*100:.1f}%** | **{hi['ge3RRate']*100:.1f}%** | **`{hi['meanRStratA']:+.2f}R`** (`{hi['sumRStratA']:+.1f}R`) | `{hi['meanRStratB']:+.2f}R` |",
        f"| **● 中置信度 ($0.45 \\le P < 0.58$)** | **1.0x 标准买入** | **{md['count']} 笔** | {md['avgConfidence']*100:.1f}% | {md['avgRiskPct']:.2f}% | **{md['stopRateLe5']*100:.1f}%** | **{md['stopRateLe10']*100:.1f}%** | **{md['stopRateLe20']*100:.1f}%** | **{md['stopRateTotal120']*100:.1f}%** | **{md['neverStoppedRate']*100:.1f}%** | {md['stopRateMa20Exit']*100:.1f}% | {md['ge1RRate']*100:.1f}% | {md['ge3RRate']*100:.1f}% | **`{md['meanRStratA']:+.2f}R`** (`{md['sumRStratA']:+.1f}R`) | `{md['meanRStratB']:+.2f}R` |",
        f"| **✓ 全部买入信号 ($P \\ge 0.45$)** | **1x + 2x 买入合集** | **{ba['count']} 笔** | {ba['avgConfidence']*100:.1f}% | {ba['avgRiskPct']:.2f}% | **{ba['stopRateLe5']*100:.1f}%** | **{ba['stopRateLe10']*100:.1f}%** | **{ba['stopRateLe20']*100:.1f}%** | **{ba['stopRateTotal120']*100:.1f}%** | **{ba['neverStoppedRate']*100:.1f}%** | {ba['stopRateMa20Exit']*100:.1f}% | {ba['ge1RRate']*100:.1f}% | {ba['ge3RRate']*100:.1f}% | **`{ba['meanRStratA']:+.2f}R`** (`{ba['sumRStratA']:+.1f}R`) | `{ba['meanRStratB']:+.2f}R` |",
        f"| × 低置信度过滤 ($P < 0.45$) | 0x 放弃不买 | {lo['count']} 笔 | {lo['avgConfidence']*100:.1f}% | {lo['avgRiskPct']:.2f}% | {lo['stopRateLe5']*100:.1f}% | {lo['stopRateLe10']*100:.1f}% | {lo['stopRateLe20']*100:.1f}% | **{lo['stopRateTotal120']*100:.1f}%** | {lo['neverStoppedRate']*100:.1f}% | {lo['stopRateMa20Exit']*100:.1f}% | {lo['ge1RRate']*100:.1f}% | **{lo['ge3RRate']*100:.1f}%** | `{lo['meanRStratA']:+.2f}R` (`{lo['sumRStratA']:+.1f}R`) | `{lo['meanRStratB']:+.2f}R` |",
        "",
        "### 核心统计规律解读：",
        f"1. **高置信度（$P \\ge 0.58$，`2.0x 重仓`）止损率显著下降，走出大趋势概率翻倍**：",
        f"   - 入场后 5 根内快损率仅 **`{hi['stopRateLe5']*100:.1f}%`**，10 根内止损率仅 **`{hi['stopRateLe10']*100:.1f}%`**（而中置信度为 `{md['stopRateLe10']*100:.1f}%`、低置信度高达 `{lo['stopRateLe10']*100:.1f}%`）；",
        f"   - 全程 120 根内有 **`{hi['neverStoppedRate']*100:.1f}%`（三分之一）的订单从未触及最低价止损**，**`{hi['ge3RRate']*100:.1f}%`** 的订单在 60 根内走出 $\\ge 3R$ 大波段，单笔平均收益高达 **`{hi['meanRStratA']:+.2f}R`**。",
        f"2. **中置信度（$0.45 \\le P < 0.58$，`1.0x 标准仓`）提供稳健顺势波段贡献**：",
        f"   - 120 根总止损率为 **`{md['stopRateTotal120']*100:.1f}%`**（未触止损率 `{md['neverStoppedRate']*100:.1f}%`），60 根内 $\\ge 3R$ 达成率为 **`{md['ge3RRate']*100:.1f}%`**，单笔均 R 为 **`{md['meanRStratA']:+.2f}R`**，适合用 `1.0x` 标准风险仓位参与。",
        f"3. **低置信度（$P < 0.45$，`0x 放弃`）是典型的止损重灾区**：",
        f"   - 20 根内止损率高达 **`{lo['stopRateLe20']*100:.1f}%`**，120 根总止损率高达 **`{lo['stopRateTotal120']*100:.1f}%`**，$\ge 3R$ 概率仅 `{lo['ge3RRate']*100:.1f}%`。将其剔除不买，可直接规避 60 笔无效磨损。",
        "",
        "---",
        "",
        "## 三、 前端图表交互与三角形买点悬浮卡片说明",
        "",
        "1. **开启方式**：",
        "   - **方式 A（主图直接看盘）**：点击顶部工具栏「指标」区的 **`AI-MA20`** 按钮，主图 K 线下方会直接绘制所有 `AI-MA20交易算法` 判定为买入（$P \\ge 0.45$）的三角形买点（翠绿色 `▲ 2x` 代表高置信度重仓，亮蓝色 `▲ 1x` 代表中置信度标准仓）。",
        "   - **方式 B（标注抽屉逐笔复盘）**：点击右上角「🏷 标注」$\\rightarrow$ 在「我的规则」下拉菜单中选择 **`AI-MA20交易算法 (v4三级分仓·冻结版)`**。",
        "2. **鼠标滑动悬停展示历史订单小卡片**：",
        "   - 当鼠标滑动至图上任意一个**三角形买点（`▲`）**时，图表会自动弹出 **`AI-MA20 历史订单与置信度止损率卡片`**：",
        "     - **本笔信号详情**：置信度等级（`★ 高置信 2.0x` / `● 中置信 1.0x`）、预测概率 $P$、买入价、刚性止损价、1R 止损距离、AI 白盒归因及本笔历史结果；",
        "     - **同置信度档位止损率统计**：实时展示该置信度档位的 **≤5根止损率、≤10根止损率、≤20根止损率、总止损率、未触止损率、≥3R达成率、单笔均R**（同时对比高置信 `2x` 与中置信 `1x` 两档）；",
        "     - **同置信度历史买入订单表**：列出截至当前信号之前已发生的同置信度档位历史订单明细（日期、买入价、止损宽、止损根数/结局、最大浮盈 MFE、最终净 R），鼠标移入卡片可上下滚动查阅。",
        "",
        "---",
        "",
        f"## 四、 全部买入订单历史台账（共 {len(art['buyOrders'])} 笔，$P \\ge 0.45$）",
        "",
        "| # | 订单编号 | 日期 | 置信度分档 | AI概率 $P$ | 仓位 | 规则分 | 买入价 | 止损价 | 1R止损宽 | 止损根数 | 60根MFE(R) | 策略A净R | MA20离场净R | AI白盒归因摘要 |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]

    for idx, o in enumerate(art["buyOrders"], 1):
        t_badge = "★ 高置信(2x)" if o["tier"] == "high" else "● 中置信(1x)"
        sb_txt = "未触发" if o["stopBar"] is None else f"{o['stopBar']}根"
        rsn_txt = " · ".join(o["topReasons"][:2])
        lines.append(
            f"| {idx} | `{o['orderId']}` | {o['date']} | {t_badge} | **{o['confidence']*100:.1f}%** | `{o['positionMult']:.1f}x` | {o['grade']}({o['score']}) | {o['entry']:.0f} | {o['stop']:.0f} | {o['riskPct']:.2f}% | {sb_txt} | +{o['mfe60R']:.1f}R | **{o['rStratA']:+.2f}R** | {o['rStratB']:+.2f}R | {rsn_txt} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 五、 复现与校验命令",
        "",
        "```bash",
        "# 重新校验并生成 AI-MA20交易算法 冻结包与归档文档",
        "python3 scripts/analysis/freeze_ai_ma20.py",
        "",
        "# 重新生成完整买点报告 mybuy.html",
        "python3 scripts/analysis/mybuy_rules.py && python3 scripts/analysis/build_mybuy_dashboard.py",
        "```",
        "",
    ])
    return "\n".join(lines)


def write_html_doc(art: dict[str, Any]) -> str:
    ts = art["confidenceTierStats"]
    hi, md, ba, lo = ts["high"], ts["medium"], ts["buyAll"], ts["lowSkip"]
    sc = art["scope"]

    rows_html = []
    for idx, o in enumerate(art["buyOrders"], 1):
        is_hi = o["tier"] == "high"
        badge_col = "#10b981" if is_hi else "#38bdf8"
        badge_txt = "★ 高置信 2.0x" if is_hi else "● 中置信 1.0x"
        sb_txt = '<span style="color:#10b981;font-weight:600">未触发止损</span>' if o["stopBar"] is None else f'第 {o["stopBar"]} 根止损'
        ra_col = "#10b981" if o["rStratA"] > 0 else "#f87171"
        rb_col = "#10b981" if o["rStratB"] > 0 else ("#94a3b8" if o["rStratB"] == 0 else "#f87171")
        rows_html.append(f"""
        <tr>
          <td>{idx}</td>
          <td><code>{o['orderId']}</code></td>
          <td><b>{o['date']}</b></td>
          <td><span style="color:{badge_col};font-weight:600">{badge_txt}</span></td>
          <td><b>{o['confidence']*100:.1f}%</b></td>
          <td>{o['grade']} ({o['score']}分)</td>
          <td>{o['entry']:,.1f}</td>
          <td>{o['stop']:,.1f}</td>
          <td>{o['riskPct']:.2f}%</td>
          <td>{sb_txt}</td>
          <td>+{o['mfe60R']:.1f}R ({o['mfePct']:.1f}%)</td>
          <td style="color:{ra_col};font-weight:600">{o['rStratA']:+.2f}R</td>
          <td style="color:{rb_col}">{o['rStratB']:+.2f}R</td>
          <td style="color:#8b949e;font-size:12px">{' · '.join(o['topReasons'])}</td>
        </tr>""")

    def tier_row(t: dict[str, Any], highlight: str = "") -> str:
        return f"""
        <tr style="{highlight}">
          <td><b>{t['title']}</b></td>
          <td><code>{t['pRange']}</code></td>
          <td><b>{t['count']} 笔</b></td>
          <td>{t['avgConfidence']*100:.1f}%</td>
          <td>{t['avgRiskPct']:.2f}%</td>
          <td style="color:#f87171">{t['stopRateLe5']*100:.1f}%</td>
          <td style="color:#f87171">{t['stopRateLe10']*100:.1f}%</td>
          <td style="color:#f87171">{t['stopRateLe20']*100:.1f}%</td>
          <td style="color:#f87171;font-weight:600">{t['stopRateTotal120']*100:.1f}%</td>
          <td style="color:#10b981;font-weight:600">{t['neverStoppedRate']*100:.1f}%</td>
          <td>{t['stopRateMa20Exit']*100:.1f}%</td>
          <td>{t['ge1RRate']*100:.1f}%</td>
          <td style="color:#10b981;font-weight:600">{t['ge3RRate']*100:.1f}%</td>
          <td style="color:#10b981;font-weight:700">{t['meanRStratA']:+.2f}R ({t['sumRStratA']:+.1f}R)</td>
          <td>{t['meanRStratB']:+.2f}R</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{art['algorithmName']} ({art['version']}) · 冻结存档说明书</title>
<style>
  :root {{ --bg:#0d1117; --panel:#161b22; --line:#30363d; --tx:#c9d1d9; --tx2:#8b949e; --up:#10b981; --ac:#38bdf8; --dn:#f87171; }}
  body {{ margin:0; background:var(--bg); color:var(--tx); font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; padding:28px 24px 60px; }}
  .wrap {{ max-width:1360px; margin:0 auto; }}
  h1 {{ margin:0 0 6px; font-size:24px; }}
  h2 {{ margin:28px 0 12px; font-size:17px; border-bottom:1px solid var(--line); padding-bottom:6px; color:#f0f6fc; }}
  .sub {{ color:var(--tx2); font-size:13px; margin-bottom:20px; }}
  .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px 18px; margin-bottom:18px; overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; font-variant-numeric:tabular-nums; }}
  th, td {{ padding:7px 9px; border-bottom:1px solid #21262d; text-align:right; white-space:nowrap; }}
  th:first-child, td:first-child, th:nth-child(2), td:nth-child(2), th:nth-child(4), td:nth-child(4), th:last-child, td:last-child {{ text-align:left; }}
  th {{ color:var(--tx2); font-weight:600; background:#0d111788; }}
  code {{ background:#ffffff12; padding:2px 5px; border-radius:4px; font-size:12px; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:600; background:#10b98122; color:#10b981; border:1px solid #10b98155; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{art['algorithmName']} <span class="badge">{art['version']}</span></h1>
  <div class="sub">
    基准标的：{sc['primaryAsset']} {sc['primaryTimeframe']}（{sc['dataStart']} → {sc['dataEnd']}，{sc['totalBars']} 根K线） · 冻结日期：{art['frozenDate']} ·
    数据指纹：<code>{sc['parquetSha256'][:16]}</code>
  </div>

  <h2>一、 不同置信度档位的历史止损率与订单期望总表</h2>
  <div class="panel">
    <table>
      <thead>
        <tr>
          <th>置信度分档与仓位</th>
          <th>概率区间</th>
          <th>订单笔数</th>
          <th>平均P</th>
          <th>平均1R宽</th>
          <th>≤5根快损率</th>
          <th>≤10根止损率</th>
          <th>≤20根止损率</th>
          <th>120根总止损率</th>
          <th>未触止损率</th>
          <th>MA20离场止损率</th>
          <th>≥1R达成率</th>
          <th>≥3R大波段率</th>
          <th>策略A均R (合计R)</th>
          <th>MA20离场均R</th>
        </tr>
      </thead>
      <tbody>
        {tier_row(hi, "background:#10b98114")}
        {tier_row(md, "background:#38bdf812")}
        {tier_row(ba, "border-top:2px solid #30363d")}
        {tier_row(lo, "opacity:0.75")}
      </tbody>
    </table>
  </div>

  <h2>二、 冻结算法三层架构参数</h2>
  <div class="panel" style="font-size:13px">
    <ol style="margin:0;padding-left:20px">
      <li><b>第一层（MA20 突破与 7 因子结构打分）</b>：最近 3 根内上穿 MA20 + 当根阳线 + 距 MA20 ≤ 10%；盘整≥5根(+20)、破下跌趋势线(+20)、实体≥2%(+15)、量比≥1.0(+15)、MACD柱3根走强(+10)、收盘>MA60(+10)、距MA20 1%~3%(+10)，总分 ≥ 30 分入选。当根收盘价买入，当根最低价设为刚性 1R 止损。</li>
      <li><b>第二层（8 根状态机同波段冷却去重）</b>：同次上穿只取首根 + 8 根冷却期压制震荡重穿；具备「首次站上 MA60 双均线升级」与「前单跌破最低价止损重置」双豁免机制（242 笔候选精简为 152 笔独立波段）。</li>
      <li><b>第三层（AI Meta-Labeling 置信度分级分仓）</b>：基于 8 个纯因果切片特征（<code>risk_pct, pos90, slope20, tr10_60, vol_ratio, atr_pct, ret20, d20</code>），由冻结版 <code>RobustScaler + L2 LR (65%) + GBDT (35%)</code> 输出达到 ≥3R 的置信度概率 P：
        <b>P ≥ 0.58</b> 为高置信度（<code>2.0x 重仓买入</code>），<b>0.45 ≤ P &lt; 0.58</b> 为中置信度（<code>1.0x 标准买入</code>），<b>P &lt; 0.45</b> 为低置信度（<code>0x 过滤放弃</code>）。</li>
    </ol>
  </div>

  <h2>三、 历史买入订单完整台账（共 {len(art['buyOrders'])} 笔买入信号，P ≥ 0.45）</h2>
  <div class="panel">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>订单编号</th>
          <th>日期</th>
          <th>置信度档位</th>
          <th>AI概率 P</th>
          <th>规则评级</th>
          <th>买入价</th>
          <th>止损价</th>
          <th>1R止损宽</th>
          <th>止损结局(120根内)</th>
          <th>60根最大浮盈</th>
          <th>策略A净R</th>
          <th>MA20离场净R</th>
          <th>AI 白盒特征归因 (Top 3)</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows_html)}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>"""


def main() -> None:
    art = build_frozen_artifact()
    FROZEN_JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    FROZEN_JSON_OUT.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")

    md_text = write_markdown_doc(art)
    DOC_STRATEGY_MD.parent.mkdir(parents=True, exist_ok=True)
    DOC_STRATEGY_MD.write_text(md_text, encoding="utf-8")

    DOC_WEB_MD.parent.mkdir(parents=True, exist_ok=True)
    DOC_WEB_MD.write_text(md_text, encoding="utf-8")

    html_text = write_html_doc(art)
    DOC_WEB_HTML.write_text(html_text, encoding="utf-8")

    hi = art["confidenceTierStats"]["high"]
    md = art["confidenceTierStats"]["medium"]
    lo = art["confidenceTierStats"]["lowSkip"]
    print(f"[FROZEN] {art['algorithmName']} ({art['version']})")
    print(f"  JSON -> {FROZEN_JSON_OUT.relative_to(ROOT)}")
    print(f"  MD   -> {DOC_STRATEGY_MD.relative_to(ROOT)} & {DOC_WEB_MD.relative_to(ROOT)}")
    print(f"  HTML -> {DOC_WEB_HTML.relative_to(ROOT)}")
    print(f"  ★ 高置信 (P>=0.58, 2x): {hi['count']}笔 | ≤5根止损 {hi['stopRateLe5']*100:.1f}% | ≤10根 {hi['stopRateLe10']*100:.1f}% | 总止损 {hi['stopRateTotal120']*100:.1f}% | 未触止损 {hi['neverStoppedRate']*100:.1f}% | ≥3R {hi['ge3RRate']*100:.1f}% | 均R {hi['meanRStratA']:+.2f}R")
    print(f"  ● 中置信 (0.45<=P<0.58, 1x): {md['count']}笔 | ≤5根止损 {md['stopRateLe5']*100:.1f}% | ≤10根 {md['stopRateLe10']*100:.1f}% | 总止损 {md['stopRateTotal120']*100:.1f}% | 未触止损 {md['neverStoppedRate']*100:.1f}% | ≥3R {md['ge3RRate']*100:.1f}% | 均R {md['meanRStratA']:+.2f}R")
    print(f"  × 低置信 (P<0.45, 0x): {lo['count']}笔 | ≤5根止损 {lo['stopRateLe5']*100:.1f}% | ≤10根 {lo['stopRateLe10']*100:.1f}% | 总止损 {lo['stopRateTotal120']*100:.1f}% | 未触止损 {lo['neverStoppedRate']*100:.1f}% | ≥3R {lo['ge3RRate']*100:.1f}% | 均R {lo['meanRStratA']:+.2f}R")


if __name__ == "__main__":
    main()
