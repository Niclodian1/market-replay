#!/usr/bin/env python3
"""跨 40 品种（加密/美股/贵金属/指数/A股港股）统一扫描器：基于 350 题盲测验证的「当根阳线上穿 MA20 + 4加5扣传统查表分」。

用法：
  python3 scripts/analysis/transfer/scan_trad_ma20.py
输出：
  reports/transfer/trad_ma20_40assets_20261109.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_e4 as E4  # noqa: E402
from analyze_blind import compute_outcomes  # noqa: E402
from build_trad_score import score_traditional  # noqa: E402
from make_blind_items import is_exact_bull_cross, prep  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def sim_v21_ma20_r(M: dict, i: int, st_init: float) -> tuple[float, int]:
    """v2.1 严格因果离场：执行止损 + k>=3 破 MA20 收盘离场 + 2.5R保本/4.0R锁+1.2R。"""
    c, h, l, ma20 = M["c"], M["h"], M["l"], M["ma20"]
    e = float(c[i])
    r0 = max(e * 1e-4, e - st_init)
    cur_stop = st_init
    end = min(M["n"] - 1, i + 120)
    mfe_r = 0.0
    for j in range(i + 1, end + 1):
        k = j - i
        if l[j] <= cur_stop:
            r = (cur_stop - e) / r0
            return round(r, 3), int(r > 0)
        ur_r = (float(h[j]) - e) / r0
        if ur_r > mfe_r:
            mfe_r = ur_r
        if k >= 3 and ma20[j] == ma20[j] and c[j] < ma20[j]:
            r = (float(c[j]) - e) / r0
            return round(r, 3), int(r > 0)
        if mfe_r >= 2.5 and c[j] > e + 0.2 * r0:
            cur_stop = max(cur_stop, e + 0.05 * r0)
        if mfe_r >= 4.0 and c[j] > e + 1.4 * r0:
            cur_stop = max(cur_stop, e + 1.2 * r0)
    r = (float(c[end]) - e) / r0
    return round(r, 3), int(r > 0)


def scan_all() -> dict:
    fng_path = ROOT / "data/crypto/fng_daily.json"
    fng_map = json.loads(fng_path.read_text(encoding="utf-8")) if fng_path.exists() else {}
    rows = []
    latest_signals = []
    for cls, rels in E4.ASSETS.items():
        for rel in rels:
            f = E4.find_file(rel)
            if f is None:
                continue
            df = pd.read_parquet(f).sort_values("time")
            df = df[df.time >= E4.SINCE].reset_index(drop=True)
            if len(df) < 300:
                continue
            sym = Path(rel).name
            M = prep(df)
            c, l, m60 = M["c"], M["l"], M["ma60"]

            # 扫描所有满足「当根阳线上穿 MA20」的候选点 + 8根同波段冷却去重
            last_i, last_stop, last_above60 = -999, -1.0, False
            for i in range(255, M["n"]):
                if not is_exact_bull_cross(M, i):
                    continue
                now_above60 = bool(m60[i] == m60[i] and c[i] >= m60[i])
                is_up = (not last_above60) and now_above60
                prev_stopped = bool(last_i >= 0 and (l[last_i + 1:i + 1] <= last_stop).any())
                if last_i >= 0 and (i - last_i) < 8 and not is_up and not prev_stopped:
                    continue

                sc = score_traditional(M, i)
                st_sw3 = float(sc["struct_stop3"])
                exec_stop = float(sc["exec_stop"])
                last_i, last_stop, last_above60 = i, float(l[i]), now_above60

                dt = str(pd.to_datetime(int(M["t"][i]), unit="s").date())
                if cls == "crypto" and dt in fng_map:
                    fng_v = fng_map[dt]
                    if 56 <= fng_v <= 74:
                        sc["trad_score"] = max(0, min(50, sc["trad_score"] - 35))
                tier = "A档(>=75 2x)" if sc["trad_score"] >= 75 else (
                    "B档(55-74 1x)" if sc["trad_score"] >= 55 else (
                        "C档(35-54 观望)" if sc["trad_score"] >= 35 else "D档(<35 否决)"
                    )
                )

                # 若离末尾超过 60 根，统计远期结果
                if i + 60 < M["n"]:
                    out = compute_outcomes(M, i, exec_stop / c[i])
                    v21_r, v21_win = sim_v21_ma20_r(M, i, exec_stop)
                    rows.append(dict(
                        cls=cls, sym=sym, date=dt, i=i, tier=tier,
                        trad_score=sc["trad_score"],
                        bar_hit3r_60=out["bar_hit3r_60"],
                        usr_hit3r_60=out["usr_hit3r_60"],
                        up2atr=out["up2atr_before_barlow"],
                        ma20_win=v21_win,
                        ma20_r=v21_r,
                        stop_le5=out["usr_stop_le5"],
                    ))
                # 记录每个品种最近一次 A/B 档买点
                if sc["trad_score"] >= 55:
                    latest_signals.append(dict(
                        cls=cls, sym=sym, date=dt, score=sc["trad_score"], tier=tier,
                        entry=round(float(c[i]), 4),
                        stop_bar=round(float(l[i]), 4),
                        stop_exec=round(exec_stop, 4),
                        stop_struct3=round(st_sw3, 4),
                        risk_pct=round((c[i] - exec_stop) / c[i] * 100, 2),
                    ))

    d = pd.DataFrame(rows)
    by_tier = {}
    for t, g in d.groupby("tier"):
        by_tier[t] = dict(
            n=int(len(g)),
            bar_hit3r_60=round(float(g["bar_hit3r_60"].mean()), 3),
            struct3_hit3r_60=round(float(g["usr_hit3r_60"].mean()), 3),
            up2atr_rate=round(float(g["up2atr"].mean()), 3),
            ma20_win=round(float(g["ma20_win"].mean()), 3),
            ma20_r_mean=round(float(g["ma20_r"].mean()), 3),
            struct3_stop_le5=round(float(g["stop_le5"].mean()), 3),
        )

    by_cls = {}
    for cl, g in d.groupby("cls"):
        a_b = g[g["trad_score"] >= 55]
        c_d = g[g["trad_score"] < 55]
        by_cls[cl] = dict(
            n_total=int(len(g)),
            n_buy_ab=int(len(a_b)),
            ab_up2atr=round(float(a_b["up2atr"].mean()), 3) if len(a_b) else None,
            cd_up2atr=round(float(c_d["up2atr"].mean()), 3) if len(c_d) else None,
            ab_hit3r=round(float(a_b["bar_hit3r_60"].mean()), 3) if len(a_b) else None,
            cd_hit3r=round(float(c_d["bar_hit3r_60"].mean()), 3) if len(c_d) else None,
            ab_stop_le5=round(float(a_b["stop_le5"].mean()), 3) if len(a_b) else None,
            cd_stop_le5=round(float(c_d["stop_le5"].mean()), 3) if len(c_d) else None,
        )

    latest_per_sym = (
        pd.DataFrame(latest_signals)
        .sort_values("date")
        .groupby("sym")
        .tail(1)
        .sort_values(["date", "score"], ascending=[False, False])
        .to_dict("records")
    )

    res = dict(total_evaluated=int(len(d)), by_tier=by_tier, by_asset_class=by_cls, latest_per_symbol=latest_per_sym[:20])
    out_path = ROOT / "reports/transfer/trad_ma20_40assets_20261109.json"
    out_path.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"Scanned {len(d)} signals across 40 assets -> {out_path}")
    for k, v in sorted(by_tier.items()):
        print(f"  {k:16s}: N={v['n']:4d} | 3R率={v['bar_hit3r_60']:.1%} | 先涨2ATR={v['up2atr_rate']:.1%} | 3根结构止损<=5根率={v['struct3_stop_le5']:.1%} | MA20均R={v['ma20_r_mean']:+.2f}R")
    return res


if __name__ == "__main__":
    scan_all()
