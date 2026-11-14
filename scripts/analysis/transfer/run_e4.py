#!/usr/bin/env python3
"""E4：多品种合并评估 AI-MA20 置信度模型（MA20 / MA60 / MA120，全部自动标签，零人工标注）。

三个问题：
  A 「BTC 老师直接教」：只用 BTC·MA20 训练的无量纲模型，拿去给其它品种 / 其它周期打分，还灵不灵？
  B 「留一品种」      ：用其它所有品种训练、在没见过的品种上打分（跨品种泛化的标准考法）。
  C 「穿越时间」      ：只用 2015 年以前的数据训练，在 2016 年以后的数据上打分（防止"同一时期大家一起涨"的假象）。

用法：python3 scripts/analysis/transfer/run_e4.py
输出：reports/transfer/e4_<date>.json
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
sys.path.insert(0, str(HERE.parent))
import engine  # noqa: E402
from meta_labeling import _fit_ensemble, _predict_ensemble  # noqa: E402
from scale_spec import ScaleSpec, scaled  # noqa: E402

ROOT = HERE.parents[2]
SINCE = pd.Timestamp("2000-01-01").timestamp()
S20 = 1.015
RNG = np.random.default_rng(7)

ASSETS: dict[str, list[str]] = {
    "加密": ["crypto/BTC_USD", "crypto/ETH_USD", "crypto/SOL_USD", "crypto/PEPE_USD", "crypto/HYPE_USD"],
    "贵金属": ["gold/XAUUSD", "gold/XAGUSD"],
    "美股指数": ["indices/SPX", "indices/NDX"],
    "其它指数": ["indices/N225", "indices/000688_SS", "indices/399006_SZ"],
    "美股个股": [f"stocks/{s}" for s in ("AAPL", "AMD", "AMZN", "AVGO", "BRK_B", "COIN", "GOOGL", "INTC", "JPM", "META",
                                       "MSFT", "MU", "NFLX", "NVDA", "PLTR", "TSLA", "WMT")],
    "A股港股": [f"cn_equity/{s}" for s in ("000333_SZ", "002460_SZ", "300033_SZ", "300750_SZ", "600519_SS",
                                          "600900_SS", "603259_SS")] + ["hk_equity/0700_HK", "hk_equity/9988_HK"],
}
FEATS = ["atr_rel", "slope_n", "ret_n", "d_atr", "risk_n", "pos", "tr_ratio"]   # 7 个无量纲特征（不含成交量）
FEAT_CN = {"atr_rel": "最近波动是否放大", "slope_n": "均线斜率", "ret_n": "之前涨跌了多少", "d_atr": "离均线多远",
           "risk_n": "止损有多宽", "pos": "处在近期高低区间的哪个位置", "tr_ratio": "突破前是否窄幅蓄势"}


def find_file(rel: str) -> Path | None:
    for p in (ROOT / f"data/{rel}_1day.parquet",):
        if p.exists():
            return p
    hits = list((ROOT / "data").glob(f"*/{Path(rel).name}_1day.parquet"))
    return hits[0] if hits else None


def features(M: dict, i: int, sp: ScaleSpec) -> dict[str, float]:
    c, h, l, ma, atr = M["c"], M["h"], M["l"], M["ma"], M["atr"]
    k, ak = sp.k, sp.amp_k
    a = atr[i]
    L = sp.lin(5)
    w60, w90, w10, w20 = sp.lin(60), sp.lin(90), sp.lin(10), sp.lin(20)
    atr_rel = a / np.nanmean(atr[max(0, i - w60):i + 1])
    slope = (ma[i] - ma[i - L]) / (L * a * k ** (sp.H - 1))
    ret = (c[i] - c[i - w20]) / (a * ak)
    hi, lo = h[i - w90:i + 1].max(), l[i - w90:i + 1].min()
    rng = (h - l)
    tr_ratio = rng[i - w10:i].mean() / max(1e-12, rng[i - w60:i].mean())
    return dict(atr_rel=atr_rel, slope_n=slope, ret_n=ret, d_atr=(c[i] - ma[i]) / a,
                risk_n=(c[i] - engine.stop_price(M, i, sp)) / (a * ak),
                pos=(c[i] - lo) / max(1e-12, hi - lo), tr_ratio=tr_ratio)


def spec_for(n: int) -> ScaleSpec:
    k = n // 20
    return scaled(n, alpha=0.65, H=0.69, s20=S20, reclaim_exp=0.65 if k > 1 else 0.0, trig_bars=k)


def build_dataset() -> pd.DataFrame:
    rows = []
    for cls, rels in ASSETS.items():
        for rel in rels:
            f = find_file(rel)
            if f is None:
                print("  missing", rel); continue
            df = pd.read_parquet(f).sort_values("time")
            df = df[df.time >= SINCE].reset_index(drop=True)
            sym = Path(rel).name
            for n in (20, 60, 120):
                sp = spec_for(n)
                if len(df) < sp.warmup + sp.horizon + 50:
                    continue
                res = engine.run(df, sp)
                for s in res["kept"]:
                    o = s.get("out")
                    if not o:
                        continue
                    i = s["i"]
                    ft = features(res["M"], i, sp)
                    if not all(np.isfinite(list(ft.values()))):
                        continue
                    rows.append(dict(cls=cls, sym=sym, n=n, t=int(res["M"]["t"][i]), y=o["y"],
                                     r=o["r_a"], **ft))
            print(f"  {cls:6s} {sym:10s} done", flush=True)
    return pd.DataFrame(rows)


def auc(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, int)
    if y.min() == y.max():
        return float("nan")
    r = pd.Series(p).rank().to_numpy()
    npos = y.sum()
    return float((r[y == 1].sum() - npos * (npos + 1) / 2) / (npos * (len(y) - npos)))


def auc_ci(y, p, reps=400) -> tuple[float, float]:
    n = len(y)
    vals = []
    for _ in range(reps):
        idx = RNG.integers(0, n, n)
        v = auc(y[idx], p[idx])
        if v == v:
            vals.append(v)
    return (float(np.quantile(vals, .025)), float(np.quantile(vals, .975))) if vals else (np.nan, np.nan)


def tier_table(df: pd.DataFrame, p: np.ndarray, q_lo: float, q_hi: float) -> dict:
    tier = np.where(p >= q_hi, "重仓", np.where(p >= q_lo, "标准", "放弃"))
    out = {}
    for t in ("重仓", "标准", "放弃"):
        m = tier == t
        if m.sum() == 0:
            continue
        r = np.clip(df["r"].to_numpy()[m], -1, 20)
        out[t] = dict(n=int(m.sum()), share=round(float(m.mean()), 3), good_rate=round(float(df["y"].to_numpy()[m].mean()), 3),
                      mean_r_clip20=round(float(r.mean()), 2), median_r=round(float(np.median(r)), 2))
    return out


def report_groups(df: pd.DataFrame, p: np.ndarray, q_lo: float, q_hi: float, tag: str) -> list[dict]:
    rows = []
    groups = [("全部", np.ones(len(df), bool))]
    for n in (20, 60, 120):
        groups.append((f"MA{n}", (df.n == n).to_numpy()))
    for cls in ASSETS:
        groups.append((cls, (df.cls == cls).to_numpy()))
        for n in (20, 60, 120):
            groups.append((f"{cls}·MA{n}", ((df.cls == cls) & (df.n == n)).to_numpy()))
    for name, m in groups:
        if m.sum() < 15:
            continue
        y, pp = df["y"].to_numpy()[m], p[m]
        a = auc(y, pp)
        lo, hi = auc_ci(y, pp)
        rows.append(dict(exp=tag, group=name, n=int(m.sum()), base_good=round(float(y.mean()), 3),
                         auc=round(a, 3), auc_lo=round(lo, 3), auc_hi=round(hi, 3),
                         tiers=tier_table(df[m], pp, q_lo, q_hi)))
    return rows


def main() -> None:
    print("== 构建数据集 ==")
    df = build_dataset()
    X = lambda d: d[FEATS].to_numpy(float)  # noqa: E731
    print(f"样本 {len(df)}：", df.groupby("n").size().to_dict())
    out: dict = dict(n_samples=int(len(df)), by_n=df.groupby("n").size().to_dict(),
                     by_cls_n=df.groupby(["cls", "n"]).size().unstack(fill_value=0).to_dict(orient="index"),
                     base_good=df.groupby(["cls", "n"]).y.mean().round(3).unstack().to_dict(orient="index"))

    # A：BTC·MA20 单独训练 → 其它所有
    m_tr = ((df.sym == "BTC_USD") & (df.n == 20)).to_numpy()
    sc, lr, gb = _fit_ensemble(X(df[m_tr]), df.y[m_tr].to_numpy())
    p_tr = _predict_ensemble(sc, lr, gb, X(df[m_tr]))
    q_lo, q_hi = np.quantile(p_tr, 0.40), np.quantile(p_tr, 0.74)
    te = df[~m_tr].reset_index(drop=True)
    pA = _predict_ensemble(sc, lr, gb, X(te))
    out["A"] = report_groups(te, pA, q_lo, q_hi, "A_BTC老师")
    out["A_lr_coef"] = dict(zip(FEATS, np.round(lr.coef_[0], 3).tolist()))

    # B：留一品种（其余全部品种、全部周期合并训练）
    pB = np.zeros(len(df))
    for sym in df.sym.unique():
        m = (df.sym == sym).to_numpy()
        s_, l_, g_ = _fit_ensemble(X(df[~m]), df.y[~m].to_numpy())
        pB[m] = _predict_ensemble(s_, l_, g_, X(df[m]))
    s_all, l_all, g_all = _fit_ensemble(X(df), df.y.to_numpy())
    p_all = _predict_ensemble(s_all, l_all, g_all, X(df))
    out["B"] = report_groups(df, pB, np.quantile(p_all, .40), np.quantile(p_all, .74), "B_留一品种")
    out["pooled_lr_coef"] = dict(zip(FEATS, np.round(l_all.coef_[0], 3).tolist()))

    # C：时间外推（<2015 训练，>=2016 测试）
    t_cut_tr, t_cut_te = pd.Timestamp("2015-01-01").timestamp(), pd.Timestamp("2016-01-01").timestamp()
    tr, te = df[df.t < t_cut_tr], df[df.t >= t_cut_te].reset_index(drop=True)
    s_, l_, g_ = _fit_ensemble(X(tr), tr.y.to_numpy())
    p_tr = _predict_ensemble(s_, l_, g_, X(tr))
    pC = _predict_ensemble(s_, l_, g_, X(te))
    out["C"] = report_groups(te, pC, np.quantile(p_tr, .40), np.quantile(p_tr, .74), "C_时间外推")
    out["C_train_n"] = int(len(tr))

    path = ROOT / f"reports/transfer/e4_{date.today():%Y%m%d}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    df.to_parquet(ROOT / f"reports/transfer/e4_samples_{date.today():%Y%m%d}.parquet")

    for key in ("A", "B", "C"):
        print(f"\n== {key} ==")
        for r in out[key]:
            if "·" in r["group"] and r["n"] < 40:
                continue
            t = r["tiers"]
            fmt = lambda x: f"{t[x]['good_rate']:.2f}/{t[x]['mean_r_clip20']:+.2f}" if x in t else "-"  # noqa: E731
            print(f"  {r['group']:14s} n={r['n']:5d} 好买点率={r['base_good']:.2f}  AUC={r['auc']:.3f} "
                  f"[{r['auc_lo']:.2f},{r['auc_hi']:.2f}]  重仓 {fmt('重仓')}  标准 {fmt('标准')}  放弃 {fmt('放弃')}")
    print("\nBTC 模型系数:", out["A_lr_coef"])
    print("合并模型系数:", out["pooled_lr_coef"])
    print("saved", path)


if __name__ == "__main__":
    main()
