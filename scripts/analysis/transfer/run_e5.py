#!/usr/bin/env python3
"""E5：① 买点规则 vs 随机买入对照；② AI 置信度失灵的原因诊断；③ 传统规则打分能否当置信度。

① 随机对照（同一套止损/观察期/"好买点"定义，只换"什么时候买"）：
   B0 随便一根阳线 / B1 收在均线上方的阳线 / B2 刚站上均线的阳线（规则去掉打分和冷却）/ RULE 完整规则
② 诊断：
   - 打乱答案实验：把 BTC 买点的好坏标签随机打乱，模型照样能"自评"出高分 → 量化过拟合
   - 大环境特征：加入大级别趋势/回撤/波动环境特征，看跨品种 AUC 是否提升
③ 传统打分：规则总分、A/B/C 等级、7 条子规则各自对"好买点率"的提升（跨品种逐个计票）

用法：python3 scripts/analysis/transfer/run_e5.py
输出：reports/transfer/e5_<date>.json
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
import run_e4 as E4  # noqa: E402
from meta_labeling import _fit_ensemble, _predict_ensemble  # noqa: E402
from scale_spec import ScaleSpec  # noqa: E402

ROOT = HERE.parents[2]
RNG = np.random.default_rng(11)
PER_BASELINE = 800
COMPONENTS = {"盘整够久": "盘整够久", "破下跌趋势线": "破趋势线", "实体达标": "大阳线", "放量": "放量",
              "MACD转强": "MACD转强", "站上MA": "站上大均线", "贴近均线": "贴近均线"}
REGIME = ["hi_d", "hi_slope", "dd_w", "vol_pct"]
REGIME_CN = {"hi_d": "离大均线多远", "hi_slope": "大均线方向", "dd_w": "离近期高点多远", "vol_pct": "当前波动在历史里算高还是低"}


def regime_features(M: dict, i: int, sp: ScaleSpec) -> dict[str, float]:
    c, h, atr, mh = M["c"], M["h"], M["atr"], M["ma_hi"]
    a, ak, k = atr[i], sp.amp_k, sp.k
    L, W = sp.lin(20), sp.lin(120)
    if i < W or mh[i] != mh[i] or mh[i - L] != mh[i - L]:
        return {}
    ap = atr[i - W:i + 1] / c[i - W:i + 1]
    return dict(hi_d=(c[i] - mh[i]) / (a * ak),
                hi_slope=(mh[i] - mh[i - L]) / (L * a * k ** (sp.H - 1)),
                dd_w=(h[i - W:i + 1].max() - c[i]) / (a * ak),
                vol_pct=float((ap < ap[-1]).mean()))


def build() -> tuple[pd.DataFrame, pd.DataFrame]:
    rule_rows, base_rows = [], []
    for cls, rels in E4.ASSETS.items():
        for rel in rels:
            f = E4.find_file(rel)
            if f is None:
                continue
            df = pd.read_parquet(f).sort_values("time")
            df = df[df.time >= E4.SINCE].reset_index(drop=True)
            sym = Path(rel).name
            for n in (20, 60, 120):
                sp = E4.spec_for(n)
                if len(df) < sp.warmup + sp.horizon + 50:
                    continue
                res = engine.run(df, sp)
                M = res["M"]
                for s in res["kept"]:
                    o = s.get("out")
                    if not o:
                        continue
                    i = s["i"]
                    ft = E4.features(M, i, sp)
                    rg = regime_features(M, i, sp)
                    row = dict(cls=cls, sym=sym, n=n, t=int(M["t"][i]), y=o["y"], r=o["r_a"], score=s["score"],
                               **ft, **{k: rg.get(k, np.nan) for k in REGIME})
                    for tag, key in COMPONENTS.items():
                        row[key] = int(any(w.startswith(tag) for w in s["why"]))
                    rule_rows.append(row)
                # ---- 随机对照 ----
                c, o_, ma, atr = M["c"], M["o"], M["ma"], M["atr"]
                m = sp.trig_bars
                idx = np.arange(max(sp.warmup, m), M["n"] - sp.horizon - 1)
                ok_atr = (atr[idx] == atr[idx]) & (atr[idx] > 0) & (ma[idx] == ma[idx])
                idx = idx[ok_atr]
                bull1 = c[idx] > o_[idx]
                above = c[idx] > ma[idx]
                crossed = np.zeros(len(idx), bool)
                for j in range(sp.reclaim):
                    kk = idx - j
                    crossed |= (c[kk - 1] <= ma[kk - 1]) & (c[kk] > ma[kk])
                bullm = c[idx] > o_[idx - m + 1]
                near = (c[idx] - ma[idx]) / atr[idx] <= 2.4
                pools = {"B0 随便一根阳线": idx[bull1], "B1 均线上方的阳线": idx[bull1 & above],
                         "B2 刚站上均线的阳线": idx[above & crossed & bullm & near]}
                for name, pool in pools.items():
                    if len(pool) == 0:
                        continue
                    pick = RNG.choice(pool, size=min(PER_BASELINE, len(pool)), replace=False)
                    for i in pick:
                        oc = engine.outcome(M, int(i), sp)
                        if oc:
                            base_rows.append(dict(cls=cls, sym=sym, n=n, base=name, y=oc["y"], r=oc["r_a"]))
            print(f"  {cls:6s} {sym:10s} done", flush=True)
    return pd.DataFrame(rule_rows), pd.DataFrame(base_rows)


def clipr(r):
    return np.clip(r, -1, 20)


def part1(rule: pd.DataFrame, base: pd.DataFrame) -> dict:
    allb = pd.concat([base, rule.assign(base="RULE 完整规则")[["cls", "sym", "n", "base", "y", "r"]]])
    allb["rc"] = clipr(allb["r"])
    tab = allb.groupby(["n", "base"]).agg(N=("y", "size"), good=("y", "mean"), meanR=("rc", "mean"),
                                         medR=("rc", "median")).round(3)
    cls_tab = allb.groupby(["cls", "base"]).y.mean().unstack().round(3)
    # 逐品种配对：规则 vs 各对照，规则更好的品种占比
    per = allb.groupby(["sym", "n", "base"]).agg(good=("y", "mean"), meanR=("rc", "mean"), N=("y", "size")).reset_index()
    votes = {}
    for n in (20, 60, 120):
        p = per[per.n == n].pivot(index="sym", columns="base", values="good")
        cnt = per[(per.n == n) & (per.base == "RULE 完整规则")].set_index("sym").N
        p = p[cnt.reindex(p.index).fillna(0) >= 8]
        for b in ("B0 随便一根阳线", "B1 均线上方的阳线", "B2 刚站上均线的阳线"):
            d = (p["RULE 完整规则"] - p[b]).dropna()
            votes[f"MA{n} vs {b}"] = dict(assets=int(len(d)), rule_better=int((d > 0).sum()),
                                          median_diff=round(float(d.median()), 3))
    return dict(table=tab.reset_index().to_dict(orient="records"), by_cls=cls_tab.to_dict(orient="index"), votes=votes)


def part2(rule: pd.DataFrame) -> dict:
    out = {}
    btc = rule[(rule.sym == "BTC_USD") & (rule.n == 20)].sort_values("t").reset_index(drop=True)
    X, y = btc[E4.FEATS].to_numpy(float), btc.y.to_numpy()
    sc, lr, gb = _fit_ensemble(X, y)
    real_in = E4.auc(y, _predict_ensemble(sc, lr, gb, X))
    shuf = []
    for _ in range(50):
        ys = RNG.permutation(y)
        s_, l_, g_ = _fit_ensemble(X, ys)
        shuf.append(E4.auc(ys, _predict_ensemble(s_, l_, g_, X)))
    out["shuffle"] = dict(n=int(len(y)), real_insample_auc=round(real_in, 3),
                          shuffled_insample_auc_mean=round(float(np.mean(shuf)), 3),
                          shuffled_insample_auc_p95=round(float(np.quantile(shuf, .95)), 3))
    # 学习曲线：从全部样本里随机抽 N 个训练，其余品种测试
    lc = []
    pool = rule.dropna(subset=E4.FEATS).reset_index(drop=True)
    for N in (150, 500, 2000, 6000):
        ins, oos = [], []
        for _ in range(8):
            tr = pool.sample(n=min(N, len(pool) // 2), random_state=int(RNG.integers(1e9)))
            te = pool.drop(tr.index)
            s_, l_, g_ = _fit_ensemble(tr[E4.FEATS].values, tr.y.values)
            ins.append(E4.auc(tr.y.values, _predict_ensemble(s_, l_, g_, tr[E4.FEATS].values)))
            oos.append(E4.auc(te.y.values, _predict_ensemble(s_, l_, g_, te[E4.FEATS].values)))
        lc.append(dict(train_n=N, insample_auc=round(float(np.mean(ins)), 3), oos_auc=round(float(np.mean(oos)), 3)))
    out["learning_curve"] = lc
    # 大环境特征：留一品种 AUC 对比
    d = rule.dropna(subset=E4.FEATS + REGIME).reset_index(drop=True)
    res = {}
    for name, feats in (("原7个K线特征", E4.FEATS), ("只用4个大环境特征", REGIME), ("7+4 合并", E4.FEATS + REGIME)):
        p = np.zeros(len(d))
        for sym in d.sym.unique():
            m = (d.sym == sym).to_numpy()
            s_, l_, g_ = _fit_ensemble(d.loc[~m, feats].values, d.y.values[~m])
            p[m] = _predict_ensemble(s_, l_, g_, d.loc[m, feats].values)
        res[name] = {"全部": round(E4.auc(d.y.values, p), 3)}
        for n in (20, 60, 120):
            mm = (d.n == n).to_numpy()
            res[name][f"MA{n}"] = round(E4.auc(d.y.values[mm], p[mm]), 3)
    out["regime_loao_auc"] = dict(n=int(len(d)), result=res)
    out["single_feature_auc"] = {f: round(E4.auc(d.y.values, d[f].values), 3) for f in E4.FEATS + REGIME}
    return out


def part3(rule: pd.DataFrame) -> dict:
    out = {}
    out["score_auc"] = {f"MA{n}": round(E4.auc(rule.y[rule.n == n].values, rule.score[rule.n == n].values), 3)
                        for n in (20, 60, 120)}
    out["score_auc"]["全部"] = round(E4.auc(rule.y.values, rule.score.values), 3)
    g = pd.cut(rule.score, [0, 39, 59, 200], labels=["C(30-39)", "B(40-59)", "A(60+)"])
    out["grade"] = rule.assign(g=g, rc=clipr(rule.r)).groupby(["n", "g"], observed=True).agg(
        N=("y", "size"), good=("y", "mean"), meanR=("rc", "mean")).round(3).reset_index().to_dict(orient="records")
    comp = {}
    for key in COMPONENTS.values():
        has, no = rule[rule[key] == 1], rule[rule[key] == 0]
        per = rule.groupby(["sym", key]).y.mean().unstack()
        per = per.dropna()
        comp[key] = dict(share=round(float(rule[key].mean()), 3), good_with=round(float(has.y.mean()), 3),
                         good_without=round(float(no.y.mean()), 3),
                         lift=round(float(has.y.mean() - no.y.mean()), 3),
                         assets_positive=f"{int((per[1] > per[0]).sum())}/{len(per)}")
        for n in (20, 60, 120):
            r = rule[rule.n == n]
            comp[key][f"lift_MA{n}"] = round(float(r[r[key] == 1].y.mean() - r[r[key] == 0].y.mean()), 3)
    out["components"] = comp
    return out


def main() -> None:
    print("== 构建 ==")
    rule, base = build()
    print(f"规则买点 {len(rule)}，对照样本 {len(base)}")
    out = dict(part1=part1(rule, base), part2=part2(rule), part3=part3(rule))
    tag = f"{date.today():%Y%m%d}"
    (ROOT / f"reports/transfer/e5_{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    rule.to_parquet(ROOT / f"reports/transfer/e5_rule_{tag}.parquet")
    p1 = out["part1"]
    print("\n== ① 规则 vs 随机 ==")
    for r in p1["table"]:
        print(f"  MA{r['n']:<4} {r['base']:14s} N={r['N']:6d} 好买点率={r['good']:.3f} 平均R={r['meanR']:+.2f} 中位R={r['medR']:+.2f}")
    for k, v in p1["votes"].items():
        print(f"  {k:28s} 规则更好的品种 {v['rule_better']}/{v['assets']}  中位差 {v['median_diff']:+.3f}")
    print("  分品类好买点率:")
    for k, v in p1["by_cls"].items():
        print("   ", k, v)
    print("\n== ② 诊断 ==")
    print(json.dumps(out["part2"], ensure_ascii=False, indent=1))
    print("\n== ③ 传统打分 ==")
    print(json.dumps(out["part3"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
