#!/usr/bin/env python3
"""AI-MA20 跨周期迁移实验 E0 / E1 / E2（全部免新标注）。

E0 标签对齐   ：BTC 1D 上 MA20 → MA60/MA120，不同缩放口径下 Y(MFE≥3R) 基率与止损率是否回到 MA20 水平。
E1 金标准检验 ：BTC 4H·MA120 / 1H·MA480（与日线 MA20 同一日历尺度）能否复现 72 个日线人工买点。
E2 重采样一致性：1D·MA60 ≈ 3D·MA20，1D·MA120 ≈ 6D·MA20，用来标定持续时间缩放指数 alpha。

用法：python3 scripts/analysis/transfer/run_experiments.py [--skip-1h]
输出：reports/transfer/e0_e2_<date>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
from scale_spec import V4_IDENTITY, ScaleSpec, naive, scaled  # noqa: E402

ROOT = HERE.parents[2]
DAY = 86400
RNG = np.random.default_rng(20261105)


def load(rel: str) -> pd.DataFrame:
    return pd.read_parquet(ROOT / rel).sort_values("time").reset_index(drop=True)


def manual_days() -> np.ndarray:
    ann = json.loads((ROOT / "annotations-BTC-1D-manual.json").read_text())
    return np.array(sorted(int(it["ts"]) // DAY for it in ann["items"]))


# ---------------------------------------------------------------- 匹配工具
def recall_days(sig_days: np.ndarray, truth: np.ndarray, tol: int) -> float:
    if len(sig_days) == 0:
        return 0.0
    s = np.sort(sig_days)
    pos = np.searchsorted(s, truth)
    best = np.full(len(truth), np.inf)
    for off in (-1, 0):
        idx = np.clip(pos + off, 0, len(s) - 1)
        best = np.minimum(best, np.abs(s[idx] - truth))
    return float((best <= tol).mean())


def chance_recall(sig_days: np.ndarray, truth: np.ndarray, tol: int, lo: int, hi: int, reps: int = 300) -> float:
    """把整套信号做循环平移（保持密度与聚集结构），估计「随机命中」召回率。"""
    span = hi - lo
    out = []
    for _ in range(reps):
        sh = RNG.integers(30, span - 30)
        out.append(recall_days(lo + (sig_days - lo + sh) % span, truth, tol))
    return float(np.mean(out))


def f1_days(a: np.ndarray, b: np.ndarray, tol: int) -> dict:
    """a=候选，b=参照；双向最近邻容差匹配。"""
    if len(a) == 0 or len(b) == 0:
        return dict(precision=0.0, recall=0.0, f1=0.0)
    p = recall_days(b, a, tol)   # 候选中能在参照里找到对应的比例
    r = recall_days(a, b, tol)
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return dict(precision=round(p, 3), recall=round(r, 3), f1=round(f1, 3))


def resample(df: pd.DataFrame, k: int, phase: int) -> pd.DataFrame:
    d = df.iloc[phase:].reset_index(drop=True)
    m = len(d) // k * k
    d = d.iloc[:m]
    g = np.arange(m) // k
    return pd.DataFrame(dict(
        time=d["time"].groupby(g).last().to_numpy(),      # 以最后一天作为该 K 的日期（收盘日）
        open=d["open"].groupby(g).first().to_numpy(),
        high=d["high"].groupby(g).max().to_numpy(),
        low=d["low"].groupby(g).min().to_numpy(),
        close=d["close"].groupby(g).last().to_numpy(),
        volume=d["volume"].groupby(g).sum().to_numpy(),
    ))


def sig_days(res: dict, which: str = "raw") -> np.ndarray:
    t = res["M"]["t"]
    return np.array([int(t[s["i"]]) // DAY for s in res[which]], dtype=np.int64)


# ---------------------------------------------------------------- 实验
def calibrate_s20(btc: pd.DataFrame) -> float:
    res = engine.run(btc, V4_IDENTITY)
    ra = [s["out"]["risk_atr"] for s in res["kept"] if s.get("out")]
    return float(np.nanmedian(ra))


def e0(btc: pd.DataFrame, s20: float) -> list[dict]:
    specs: list[ScaleSpec] = [
        V4_IDENTITY,
        scaled(20, alpha=1.0, H=0.5, s20=s20, label="MA20 atr+scaled-stop (新口径基准)"),
        naive(60, label="MA60 直接套用"),
        naive(120, label="MA120 直接套用"),
    ]
    for n in (60, 120):
        for a in (0.65, 1.0):
            for H in (0.5, 0.69):
                specs.append(scaled(n, alpha=a, H=H, s20=s20))
        # E2 最优结构（reclaim 同步缩放 + 触发K聚合 k 根）下的标签对齐
        for H in (0.5, 0.69):
            specs.append(scaled(n, alpha=0.65, H=H, s20=s20, reclaim_exp=0.65, trig_bars=n // 20,
                                label=f"MA{n} 推荐 a=0.65 rex=0.65 trig={n // 20} H={H}"))
    rows = []
    for sp in specs:
        res = engine.run(btc, sp)
        rows.append(dict(label=sp.label, spec=sp.describe(), **engine.summarize(res, sp)))
    return rows


def e1(btc: pd.DataFrame, s20: float, skip_1h: bool) -> list[dict]:
    truth = manual_days()
    rows = []

    def evaluate(df: pd.DataFrame, sp: ScaleSpec, tf: str):
        t0 = time.time()
        res = engine.run(df, sp, with_outcome=False)
        t = res["M"]["t"]
        lo, hi = int(t[sp.warmup]) // DAY, int(t[-1]) // DAY
        tr = truth[(truth >= lo) & (truth <= hi)]
        years = (hi - lo) / 365.25
        row = dict(tf=tf, label=sp.label, spec=sp.describe(), truth_n=int(len(tr)),
                   raw_per_year=round(len(res["raw"]) / years, 1),
                   kept_per_year=round(len(res["kept"]) / years, 1), sec=round(time.time() - t0, 1))
        for which in ("raw", "kept"):
            days = np.unique(sig_days(res, which))
            for tol in (0, 1, 2):
                rc = recall_days(days, tr, tol)
                ch = chance_recall(days, tr, tol, lo, hi)
                row[f"{which}_r{tol}"] = round(rc, 3)
                row[f"{which}_chance{tol}"] = round(ch, 3)
                row[f"{which}_lift{tol}"] = round(rc / ch, 2) if ch > 0 else None
        rows.append(row)
        print(f"  [E1] {tf:3s} {sp.label:45s} raw/yr={row['raw_per_year']:6.1f} "
              f"R±1={row['raw_r1']:.3f} (chance {row['raw_chance1']:.3f}, lift {row['raw_lift1']})", flush=True)

    evaluate(btc, V4_IDENTITY, "1D")
    evaluate(btc, scaled(20, alpha=1.0, H=0.5, s20=s20, label="MA20 atr 口径"), "1D")

    h4 = load("data/crypto/BTC_USD_4h.parquet")
    k = 6
    variants = [
        naive(120, label="4H MA120 直接套用"),
        scaled(120, alpha=0.65, H=0.5, s20=s20, label="a=0.65 reclaim不缩放 trig1"),
        scaled(120, alpha=1.0, H=0.5, s20=s20, label="a=1 reclaim不缩放 trig1"),
        scaled(120, alpha=1.0, H=0.5, s20=s20, reclaim_exp=1.0, label="a=1 reclaim×k trig1"),
        scaled(120, alpha=1.0, H=0.5, s20=s20, reclaim_exp=1.0, trig_bars=k, label="a=1 reclaim×k trig=k (完全重采样等价)"),
        scaled(120, alpha=0.65, H=0.5, s20=s20, reclaim_exp=0.65, trig_bars=k, label="a=0.65 reclaim×k^a trig=k"),
    ]
    for sp in variants:
        evaluate(h4, sp, "4H")

    if not skip_1h:
        h1 = load("data/crypto/BTC_USD_1h.parquet")
        k = 24
        for sp in (
            naive(480, label="1H MA480 直接套用"),
            scaled(480, alpha=1.0, H=0.5, s20=s20, reclaim_exp=1.0, trig_bars=k, label="a=1 reclaim×k trig=k (完全重采样等价)"),
            scaled(480, alpha=0.65, H=0.5, s20=s20, reclaim_exp=0.65, trig_bars=k, label="a=0.65 reclaim×k^a trig=k"),
        ):
            evaluate(h1, sp, "1H")
    return rows


def e2(btc: pd.DataFrame, s20: float) -> list[dict]:
    rows = []
    ref_spec = scaled(20, alpha=1.0, H=0.5, s20=s20, label="ref MA20 atr")
    for n, k in ((60, 3), (120, 6)):
        refs = []
        for p in range(k):
            r = engine.run(resample(btc, k, p), ref_spec, with_outcome=False)
            refs.append(dict(raw=sig_days(r, "raw"), kept=sig_days(r, "kept"),
                             lo=int(r["M"]["t"][ref_spec.warmup]) // DAY))
        cands: list[ScaleSpec] = [naive(n, label=f"MA{n} 直接套用")]
        for a in (0.5, 0.65, 0.8, 1.0):
            for rex in sorted({0.0, a}):
                for tb in (1, k):
                    cands.append(scaled(n, alpha=a, H=0.5, s20=s20, reclaim_exp=rex, trig_bars=tb,
                                        label=f"a={a} reclaim_exp={rex} trig={tb}"))
        for sp in cands:
            res = engine.run(btc, sp, with_outcome=False)
            out = dict(n=n, k=k, label=sp.label, spec=sp.describe())
            for which in ("raw", "kept"):
                c_days = sig_days(res, which)
                f1s, ratios = [], []
                for rf in refs:
                    lo = max(rf["lo"], int(res["M"]["t"][sp.warmup]) // DAY)
                    a_ = c_days[c_days >= lo]
                    b_ = rf[which][rf[which] >= lo]
                    f1s.append(f1_days(a_, b_, tol=k))
                    ratios.append(len(a_) / max(1, len(b_)))
                for key in ("precision", "recall", "f1"):
                    out[f"{which}_{key}"] = round(float(np.mean([x[key] for x in f1s])), 3)
                out[f"{which}_f1_phase_sd"] = round(float(np.std([x["f1"] for x in f1s])), 3)
                out[f"{which}_count_ratio"] = round(float(np.mean(ratios)), 2)
            rows.append(out)
            print(f"  [E2] MA{n} {sp.label:36s} kept F1={out['kept_f1']:.3f} (P={out['kept_precision']:.2f} "
                  f"R={out['kept_recall']:.2f} cnt×{out['kept_count_ratio']})  raw F1={out['raw_f1']:.3f}", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-1h", action="store_true")
    args = ap.parse_args()
    btc = load("data/crypto/BTC_USD_1day.parquet")
    s20 = calibrate_s20(btc)
    print(f"s20 (BTC MA20 kept signals, median (C-L)/ATR) = {s20:.3f}")
    print("== E0 ==")
    r0 = e0(btc, s20)
    for r in r0:
        print(f"  [E0] {r['label']:40s} kept={r.get('kept')} lab={r.get('labeled')} /yr={r.get('kept_per_year')} "
              f"Y={r.get('y_rate')} CI={r.get('y_ci95')} stop≤5'={r.get('stop_le5')} stop≤hz={r.get('stop_hz')} "
              f"riskATR={r.get('med_risk_atr')} risk%={r.get('med_risk_pct')} meanR={r.get('mean_r_a')}")
    print("== E1 ==")
    r1 = e1(btc, s20, args.skip_1h)
    print("== E2 ==")
    r2 = e2(btc, s20)
    out_dir = ROOT / "reports/transfer"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"e0_e2_{date.today():%Y%m%d}.json"
    path.write_text(json.dumps(dict(s20=s20, e0=r0, e1=r1, e2=r2), ensure_ascii=False, indent=1, default=float))
    print("saved", path)


if __name__ == "__main__":
    main()
