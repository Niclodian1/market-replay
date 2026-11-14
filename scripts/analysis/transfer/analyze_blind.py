#!/usr/bin/env python3
"""全量盲测评估与新置信度打分构建（350题：300正式 + 30重测 + 20陷阱）。

输出：reports/blind/blind_analysis_20261109.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact, spearmanr

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
from conditions import eval_conditions  # noqa: E402
from make_blind_items import prep  # noqa: E402
from run_e4 import find_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SINCE = pd.Timestamp("2000-01-01").timestamp()

# Symbol -> relative path map
SYM_MAP = {
    "BTC_USD": "crypto/BTC_USD", "ETH_USD": "crypto/ETH_USD", "SOL_USD": "crypto/SOL_USD",
    "XAUUSD": "gold/XAUUSD", "XAGUSD": "gold/XAGUSD",
    "SPX": "indices/SPX", "NDX": "indices/NDX", "N225": "indices/N225",
    "399006_SZ": "indices/399006_SZ", "000688_SS": "indices/000688_SS",
    "000333_SZ": "cn_equity/000333_SZ", "002460_SZ": "cn_equity/002460_SZ",
    "300033_SZ": "cn_equity/300033_SZ", "600519_SS": "cn_equity/600519_SS",
    "600900_SS": "cn_equity/600900_SS", "0700_HK": "hk_equity/0700_HK", "9988_HK": "hk_equity/9988_HK",
}
for s in ("AAPL", "AMD", "AMZN", "AVGO", "GOOGL", "INTC", "JPM", "META", "MSFT", "MU", "NFLX", "NVDA", "TSLA", "WMT", "BRK_B"):
    SYM_MAP[s] = f"stocks/{s}"


def load_all_series() -> dict[str, dict]:
    data = {}
    for sym, rel in SYM_MAP.items():
        f = find_file(rel)
        if f is None:
            raise FileNotFoundError(f"Missing {rel}")
        df = pd.read_parquet(f).sort_values("time")
        df = df[df.time >= SINCE].reset_index(drop=True)
        data[sym] = prep(df)
    return data


def compute_outcomes(M: dict, i: int, stop_norm: float | None) -> dict[str, Any]:
    """计算当根阳线上穿点 i 的多种远期真实表现（默认当根低点止损 vs 用户手点止损 vs 结构低点止损 + MA20离场）。"""
    c, h, l, ma20, atr = M["c"], M["h"], M["l"], M["ma20"], M["atr"]
    e = float(c[i])
    a = float(atr[i]) if (atr[i] == atr[i] and atr[i] > 0) else e * 0.02

    def eval_stop(st: float, prefix: str) -> dict[str, Any]:
        r0 = e - st
        if r0 <= 1e-12:
            r0 = 0.2 * a
            st = e - r0
        # 60根与120根视窗
        end60 = min(M["n"] - 1, i + 60)
        end120 = min(M["n"] - 1, i + 120)
        seg_l120 = l[i + 1:end120 + 1]
        hit = np.flatnonzero(seg_l120 <= st)
        stop_bar = int(hit[0]) + 1 if len(hit) else None

        # 在触及止损前的最大浮盈 (60根内 & 120根内)
        w60 = min(60, (stop_bar - 1) if stop_bar else (end60 - i))
        w120 = min(120, (stop_bar - 1) if stop_bar else (end120 - i))
        mfe60_r = float((h[i + 1:i + 1 + w60].max() - e) / r0) if w60 > 0 else 0.0
        mfe120_r = float((h[i + 1:i + 1 + w120].max() - e) / r0) if w120 > 0 else 0.0

        # 跌破MA20离场（你 98% 买点选择的离场方式）：
        # 规则：入场后逐根检查：若当根最低价 <= st，按 -1R 止损离场；否则若收盘跌破 MA20 (c[k] < ma20[k])，按 c[k] 收盘离场；若60根未离场按 c[end60] 离场
        ret_ma20_r = 0.0
        ret_ma20_pct = 0.0
        exit_bar = end60 - i
        for k in range(i + 1, end60 + 1):
            if l[k] <= st:
                ret_ma20_r = -1.0
                ret_ma20_pct = (st - e) / e * 100.0
                exit_bar = k - i
                break
            if c[k] < ma20[k]:
                ret_ma20_r = float((c[k] - e) / r0)
                ret_ma20_pct = float((c[k] - e) / e * 100.0)
                exit_bar = k - i
                break
        else:
            ret_ma20_r = float((c[end60] - e) / r0)
            ret_ma20_pct = float((c[end60] - e) / e * 100.0)

        return {
            f"{prefix}_risk_pct": round(r0 / e * 100, 3),
            f"{prefix}_risk_atr": round(r0 / a, 3),
            f"{prefix}_stop_le5": int(stop_bar is not None and stop_bar <= 5),
            f"{prefix}_stop_le20": int(stop_bar is not None and stop_bar <= 20),
            f"{prefix}_mfe60_r": round(mfe60_r, 3),
            f"{prefix}_mfe120_r": round(mfe120_r, 3),
            f"{prefix}_hit2r_60": int(mfe60_r >= 2.0),
            f"{prefix}_hit3r_60": int(mfe60_r >= 3.0),
            f"{prefix}_hit3r_120": int(mfe120_r >= 3.0),
            f"{prefix}_ma20_r": round(ret_ma20_r, 3),
            f"{prefix}_ma20_pct": round(ret_ma20_pct, 3),
            f"{prefix}_ma20_win": int(ret_ma20_r > 0),
            f"{prefix}_exit_bars": int(exit_bar),
        }

    # 1. 默认当根低点止损
    st_bar = float(l[i])
    # 2. 用户手点止损（若未点或 >=1.0，则回退当根低点）
    st_usr = float(e * stop_norm) if (stop_norm is not None and 0.1 < stop_norm < 0.9999) else st_bar
    # 3. 结构低点止损：前5根最低点 min(l[i-4:i+1])
    st_sw5 = float(l[max(0, i - 4):i + 1].min())

    # 纯价格涨幅（与止损设置无关，检验纯方向判断能力！）
    end20 = min(M["n"] - 1, i + 20)
    end60 = min(M["n"] - 1, i + 60)
    max_up20_atr = float((h[i + 1:end20 + 1].max() - e) / a)
    max_dn20_atr = float((e - l[i + 1:end20 + 1].min()) / a)
    max_up60_atr = float((h[i + 1:end60 + 1].max() - e) / a)
    ret20_atr = float((c[end20] - e) / a)
    ret60_atr = float((c[end60] - e) / a)
    # 20根内无条件跌破当根低点 vs 先涨2ATR再跌破当根低点
    up2atr_before_barlow = 0
    for k in range(i + 1, end60 + 1):
        if l[k] <= st_bar:
            break
        if h[k] - e >= 2.0 * a:
            up2atr_before_barlow = 1
            break

    out = dict(
        max_up20_atr=round(max_up20_atr, 3),
        max_dn20_atr=round(max_dn20_atr, 3),
        max_up60_atr=round(max_up60_atr, 3),
        ret20_atr=round(ret20_atr, 3),
        ret60_atr=round(ret60_atr, 3),
        ret20_pos=int(ret20_atr > 0),
        ret60_pos=int(ret60_atr > 0),
        up2atr_before_barlow=up2atr_before_barlow,
    )
    out.update(eval_stop(st_bar, "bar"))
    out.update(eval_stop(st_usr, "usr"))
    out.update(eval_stop(st_sw5, "sw5"))
    return out


def eval_extra_conditions(M: dict, i: int) -> dict[str, Any]:
    """补充从你文字笔记里新发现的条件（如 V8_上根大阴线）及更细粒度的趋势/位置特征。"""
    c, o, h, l, ma20, ma60, ma120, atr = (
        M["c"], M["o"], M["h"], M["l"], M["ma20"], M["ma60"], M["ma120"], M["atr"]
    )
    a = float(atr[i]) if (atr[i] == atr[i] and atr[i] > 0) else float(c[i]) * 0.02
    a_prev = float(atr[i - 1]) if (atr[i - 1] == atr[i - 1] and atr[i - 1] > 0) else a
    prev_bear_atr = max(0.0, float(o[i - 1] - c[i - 1]) / a_prev)
    prev_rng_atr = float(h[i - 1] - l[i - 1]) / a_prev
    # 上根或前两根有未消化大阴线（上根阴线实体 >= 1.0 ATR 且当根收盘未完全反包上根开盘价，或上根阴线 >= 1.2 ATR）
    v8_prev_big_bear = int(
        (o[i - 1] > c[i - 1] and prev_bear_atr >= 1.0 and c[i] < o[i - 1])
        or prev_bear_atr >= 1.3
    )
    # 均线空头排列/逆势细分：c[i] < ma60[i] 且 ma60 下行，或 c[i] < ma120[i] 且 ma120 下行
    ma60_down = bool(ma60[i] == ma60[i] and ma60[i - 10] == ma60[i - 10] and ma60[i] < ma60[i - 10] - 0.15 * a)
    ma120_down = bool(ma120[i] == ma120[i] and ma120[i - 20] == ma120[i - 20] and ma120[i] < ma120[i - 20] - 0.3 * a)
    below_ma60 = bool(ma60[i] == ma60[i] and c[i] < ma60[i])
    below_ma120 = bool(ma120[i] == ma120[i] and c[i] < ma120[i])
    v_counter_trend = int((below_ma60 and ma60_down) or (below_ma120 and ma120_down))

    # 均线粘合/多头：MA20 与 MA60 距离 < 1.2 ATR，或 c[i] > ma60[i] >= ma120[i]
    ma_dist_atr = abs(ma20[i] - ma60[i]) / a if ma60[i] == ma60[i] else 9.9
    q_ma_tight = int(ma_dist_atr <= 1.2)
    q_above_ma60 = int(ma60[i] == ma60[i] and c[i] > ma60[i])

    return dict(
        prev_bear_atr=round(prev_bear_atr, 3),
        V8_上根大阴线=v8_prev_big_bear,
        V9_均线压制逆势=v_counter_trend,
        Q6_均线紧贴=q_ma_tight,
        Q7_站上MA60=q_above_ma60,
        ma_dist_atr=round(ma_dist_atr, 3),
    )


def main() -> None:
    key = json.loads((ROOT / "reports/blind/key.json").read_text())
    ann = json.loads((ROOT / "reports/blind/session4_full_20261109.json").read_text())["answers"]
    data = load_all_series()

    rows = []
    for iid, k in key.items():
        if k["isWarmup"]:
            continue
        a = ann.get(iid)
        if not a:
            continue
        sym = k["symbol"]
        i = int(k["barIndex"])
        M = data[sym]
        conds = eval_conditions(M, i)
        extra = eval_extra_conditions(M, i)
        outs = compute_outcomes(M, i, a.get("stopPrice"))

        # Clean stale rejectReasons on yes
        dec = a["decision"]
        rej = [] if dec == "yes" else (a.get("rejectReasons") or [])
        main_rej = None if dec == "yes" else a.get("mainRejectReason")
        ctx = a.get("context") or {}
        strg = a.get("strength") or {}
        rsk = a.get("risk") or {}

        rows.append(dict(
            id=iid,
            session=k["session"],
            group=k["group"],
            symbol=sym,
            date=k["date"],
            barIndex=i,
            candidateType=k["candidateType"],
            isRepeatOf=k["isRepeatOf"],
            isTrap=bool(k["isTrap"]),
            isHoldout=bool(k["isHoldout"]),
            decision=dec,
            is_yes=int(dec == "yes"),
            confidence=int(a.get("confidence") or 3),
            setup_usr=a.get("setup"),
            dualMA_usr=a.get("dualMA"),
            priorDrop_usr=ctx.get("priorDrop"),
            trend_usr=ctx.get("trend"),
            position_usr=ctx.get("position"),
            rangeStage_usr=ctx.get("rangeStage"),
            strength_usr=strg.get("overall"),
            issues_usr=strg.get("issues") or [],
            marubozu_usr=bool(strg.get("marubozu")),
            stopPrice_norm=a.get("stopPrice"),
            stopTouched=bool(a.get("stopTouched")),
            rr_usr=rsk.get("rr"),
            vol_usr=rsk.get("vol"),
            rejectReasons=rej,
            mainRejectReason=main_rej,
            quality_usr=a.get("quality") or [],
            exitPlan_usr=a.get("exitPlan"),
            note=a.get("note") or "",
            secondsSpent=int(a.get("secondsSpent") or 0),
            **k["features"],
            **conds,
            **extra,
            **outs,
        ))

    df = pd.DataFrame(rows)
    print(f"Loaded {len(df)} formal/repeat/trap items.")

    # Save enriched dataframe for fast interactive analysis
    df.to_parquet(ROOT / "reports/blind/enriched_350.parquet", index=False)
    print("Saved reports/blind/enriched_350.parquet")


if __name__ == "__main__":
    main()
