#!/usr/bin/env python3
"""BTC AI PoC · 迭代 v2：把 AI 从「预测方向」挪到「风控 / 过滤」。

v1 结论：LightGBM 直接预测 Triple-Barrier 方向 OOS AUC≈0.53，扣成本后无 alpha。
v2 假设：
  H1  BTC 的一阶矩（方向）几乎不可预测，但二阶矩（波动率）可预测 → AI 用于波动率预测 + 仓位缩放；
  H2  Meta-Labeling 应该叠加在「本身期望为正」的基础策略上（4h MA120 趋势跟随），并按收益幅度加权，
      而不是叠加在期望为负的短线突破上；
  H3  多周期趋势状态（日线 MA50 斜率）作为规则过滤，可减少震荡市的来回打脸。

策略（OOS 2020-01-01 起，全部计入单边 0.05% 手续费 + 0.05% 滑点，按仓位变化量收费）：
  S0  Trend          : 4h 收盘 > MA120（=20 日）满仓，否则空仓
  S1  Trend+MTF      : S0 且日线 MA50 斜率 > 0（日线数据只用已收盘的上一根日 K）
  S2  Trend+EWMAvol  : S0 × min(1, 目标波动 / EWMA 波动)
  S3  Trend+AIvol    : S0 × min(1, 目标波动 / LightGBM 预测波动)    ← AI 做风控
  S4  Trend+Meta     : S0，但每次入场先过 LightGBM meta 模型，p<0.5 则整段趋势不做 ← AI 做过滤
  S5  Trend+MTF+AIvol: S1 × AI 波动缩放（组合）
  基准: Buy & Hold
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_experiment import Config, build_features, equity_metrics, fold_starts, load_data, position_backtest  # noqa: E402

OUT = Path(__file__).resolve().parent / "results" / "v2_ai_as_risk"
VOL_H = 30            # 预测未来 30 根 4h（5 天）已实现波动
TARGET_VOL = 0.50     # 年化目标波动（BTC 长期年化波动约 60~80%，取 50% 只做降风险，不加杠杆）
REBAL_BAND = 0.10     # 仓位变化 < 10% 不调仓，控制换手成本
BARS_PER_YEAR = 6 * 365
REJECT_Q = 0.30       # meta 过滤：拒绝训练集打分分布下 30% 分位以下的入场（事前固定）


def block_bootstrap_sharpe_diff(a: pd.Series, b: pd.Series, n_boot: int = 2000, block: int = 20, seed: int = 7) -> dict:
    """日收益的移动块自助法：P(Sharpe_a - Sharpe_b <= 0)。"""
    ra = a.resample("1D").last().pct_change().dropna()
    rb = b.resample("1D").last().pct_change().dropna()
    idx = ra.index.intersection(rb.index)
    ra, rb = ra[idx].to_numpy(), rb[idx].to_numpy()
    n = len(ra)
    rng = np.random.default_rng(seed)
    sh = lambda r: r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0.0  # noqa: E731
    diffs = []
    for _ in range(n_boot):
        st = rng.integers(0, n - block, size=n // block + 1)
        ii = (st[:, None] + np.arange(block)).ravel()[:n]
        diffs.append(sh(ra[ii]) - sh(rb[ii]))
    diffs = np.array(diffs)
    return dict(sharpe_diff=float(sh(ra) - sh(rb)), ci95=[float(np.quantile(diffs, 0.025)), float(np.quantile(diffs, 0.975))],
                p_le_0=float((diffs <= 0).mean()))


def realized_fwd_vol(lr: pd.Series, h: int) -> pd.Series:
    """t 时刻的标签 = t+1..t+h 的对数收益标准差（年化）。只做标签，绝不进特征。"""
    return lr[::-1].rolling(h).std()[::-1].shift(-1) * np.sqrt(BARS_PER_YEAR)


def vol_forecast_wf(X: pd.DataFrame, y: pd.Series, ts: pd.Series, cfg: Config) -> tuple[np.ndarray, list[dict]]:
    n = len(X)
    pred = np.full(n, np.nan)
    starts = fold_starts(ts, cfg) + [n]
    ok = X.notna().all(axis=1).to_numpy()
    yl = np.log(y.to_numpy())
    rows = []
    for k in range(len(starts) - 1):
        s, e = starts[k], starts[k + 1]
        tr = ok & np.isfinite(yl) & (np.arange(n) < s - VOL_H - 1)      # 标签窗口不能跨进测试段（purge）
        te = ok & (np.arange(n) >= s) & (np.arange(n) < e)
        m = lgb.LGBMRegressor(objective="l2", learning_rate=0.03, num_leaves=15, max_depth=5, min_child_samples=200,
                              feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, n_estimators=300,
                              verbose=-1, random_state=cfg.seed)
        m.fit(X[tr], yl[tr])
        pred[te] = np.exp(m.predict(X[te]))
        rows.append(dict(test_start=str(ts.iloc[s].date()), n_train=int(tr.sum())))
    return pred, rows


def trend_trades(pos: np.ndarray) -> list[tuple[int, int]]:
    """把 0/1 仓位序列切成 (入场 bar, 出场 bar)。"""
    d = np.diff(np.r_[0, pos.astype(int), 0])
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def meta_filter_wf(X: pd.DataFrame, close: np.ndarray, pos: np.ndarray, ts: pd.Series, cfg: Config, start: int):
    """对趋势策略每一段持仓做 meta-labeling：label = 该段扣成本净收益 > 0，样本权重 = |净收益|。"""
    cost = 2 * (cfg.fee + cfg.slip)
    trades = [(a, b) for a, b in trend_trades(pos) if b < len(close)]
    tdf = pd.DataFrame(trades, columns=["entry", "exit"])
    tdf["net"] = close[tdf.exit] / close[tdf.entry] - 1 - cost
    tdf["label"] = (tdf.net > 0).astype(int)
    feats = X.iloc[tdf.entry].reset_index(drop=True)
    tdf["p"] = np.nan
    tdf["cut"] = np.nan
    starts = fold_starts(ts, cfg)
    starts = starts + [len(close)]
    for k in range(len(starts) - 1):
        s, e = starts[k], starts[k + 1]
        tr = (tdf.exit < s - 1) & feats.notna().all(axis=1)
        te = (tdf.entry >= s) & (tdf.entry < e) & feats.notna().all(axis=1)
        if tr.sum() < 40 or te.sum() == 0:
            continue
        m = lgb.LGBMClassifier(learning_rate=0.03, num_leaves=7, max_depth=3, n_estimators=150,
                               min_child_samples=max(10, int(tr.sum()) // 15), feature_fraction=0.7,
                               bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, verbose=-1, random_state=cfg.seed)
        m.fit(feats[tr], tdf.label[tr], sample_weight=tdf.net[tr].abs() + 1e-4)
        # 事前固定的拒绝规则：分数低于「训练集自身打分」30% 分位的入场才拒绝（加权训练使概率整体偏高，0.5 阈值失效）
        cut = np.quantile(m.predict_proba(feats[tr])[:, 1], REJECT_Q)
        tdf.loc[te, "p"] = m.predict_proba(feats[te])[:, 1]
        tdf.loc[te, "cut"] = cut
    keep = np.ones(len(close), dtype=bool)
    for _, r in tdf.iterrows():
        if r.entry >= start and np.isfinite(r.p) and r.p < r.cut:
            keep[int(r.entry):int(r.exit)] = False
    return keep, tdf


def apply_band(target: np.ndarray, band: float) -> np.ndarray:
    """仓位迟滞：只有目标仓位变化超过 band（或进出场 0↔正数）时才调仓。"""
    out = np.zeros_like(target)
    cur = 0.0
    for i, t in enumerate(target):
        if (t == 0) != (cur == 0) or abs(t - cur) > band:
            cur = t
        out[i] = cur
    return out


def main() -> int:
    cfg = Config(timeframe="4h")
    OUT.mkdir(parents=True, exist_ok=True)
    df = load_data(cfg)
    ts, c = df["ts"], df["close"]
    X = build_features(df)
    start = int(ts.searchsorted(pd.Timestamp(cfg.test_start, tz="UTC")))
    n = len(df)
    lr = np.log(c).diff()

    # --- 规则层
    trend = (c > c.rolling(120).mean()).to_numpy()
    d = df.set_index("ts")["close"].resample("1D").last()
    ma50 = d.rolling(50).mean()
    d_up = (ma50 > ma50.shift(5)).shift(1)       # 只用已收盘的上一根日 K → 无未来函数
    mtf = d_up.reindex(ts.dt.floor("1D")).astype("boolean").fillna(False).to_numpy().astype(bool)
    trend_mtf = trend & mtf

    # --- AI 波动率预测 vs EWMA 基线
    y_vol = realized_fwd_vol(lr, VOL_H)
    pred_ai, _ = vol_forecast_wf(X, y_vol, ts, cfg)
    ewma = (lr.pow(2).ewm(span=60, adjust=False).mean() ** 0.5 * np.sqrt(BARS_PER_YEAR)).to_numpy()
    m = (np.arange(n) >= start) & np.isfinite(pred_ai) & np.isfinite(y_vol.to_numpy())
    yt = y_vol.to_numpy()[m]

    def fc_stats(p: np.ndarray) -> dict:
        p = p[m]
        lr2 = np.log(yt) - np.log(p)
        return dict(corr_log=float(np.corrcoef(np.log(p), np.log(yt))[0, 1]),
                    rmse_log=float(np.sqrt(np.mean(lr2 ** 2))),
                    qlike=float(np.mean(yt ** 2 / p ** 2 - np.log(yt ** 2 / p ** 2) - 1)))
    vol_eval = {"lightgbm": fc_stats(pred_ai), "ewma60": fc_stats(ewma),
                "naive_trailing_30": fc_stats((lr.rolling(30).std() * np.sqrt(BARS_PER_YEAR)).to_numpy())}

    def scale(vol: np.ndarray) -> np.ndarray:
        return np.clip(TARGET_VOL / np.where(np.isfinite(vol) & (vol > 0), vol, np.inf), 0, 1)

    # --- Meta-labeling 过滤趋势段
    keep, tdf = meta_filter_wf(X, c.to_numpy(), trend, ts, cfg, start)
    oos_t = tdf[tdf.entry >= start]
    rej = oos_t.p < oos_t.cut
    meta_eval = dict(
        n_trend_trades_oos=int(len(oos_t)), n_scored=int(oos_t.p.notna().sum()),
        n_filtered=int(rej.sum()),
        base_win_rate=float(oos_t.label.mean()),
        kept_mean_net=float(oos_t[~rej].net.mean()), filtered_mean_net=float(oos_t[rej].net.mean()),
        kept_win_rate=float(oos_t[~rej].label.mean()), filtered_win_rate=float(oos_t[rej].label.mean()),
        kept_sum_net=float(oos_t[~rej].net.sum()), filtered_sum_net=float(oos_t[rej].net.sum()),
        filtered_top10_winners=int(oos_t.net.rank(ascending=False)[rej].le(10).sum()),
        top5_trades_share_of_pnl=float(oos_t.net.nlargest(5).sum() / oos_t.net.sum()) if oos_t.net.sum() > 0 else None,
    )

    pos = {
        "S0_Trend": trend.astype(float),
        "S1_Trend+MTF": trend_mtf.astype(float),
        "S2_Trend+EWMAvol": apply_band(trend * scale(ewma), REBAL_BAND),
        "S3_Trend+AIvol": apply_band(trend * scale(pred_ai), REBAL_BAND),
        "S4_Trend+Meta": (trend & keep).astype(float),
        "S5_Trend+MTF+AIvol": apply_band(trend_mtf * scale(pred_ai), REBAL_BAND),
        "BuyHold": np.ones(n),
    }
    summary, eqs = {}, {}
    for name, p in pos.items():
        eq, switches = position_backtest(df, p, start, cfg)
        mt = equity_metrics(eq)
        mt["exposure"] = float(p[start:].mean())
        mt["rebalances"] = switches
        mt["round_trips_per_month"] = float(len([1 for a, _ in trend_trades((p > 0).astype(int)) if a >= start]) / (mt["years"] * 12))
        summary[name] = mt
        eqs[name] = eq
    # 趋势段的交易层统计（S0 / S1 / S4）
    trade_stats = {}
    cost = 2 * (cfg.fee + cfg.slip)
    for name in ("S0_Trend", "S1_Trend+MTF", "S4_Trend+Meta"):
        tr = [(a, b) for a, b in trend_trades(pos[name].astype(int)) if a >= start and b < n]
        net = np.array([c.iat[b] / c.iat[a] - 1 - cost for a, b in tr])
        w, lo = net[net > 0], net[net <= 0]
        trade_stats[name] = dict(n=len(net), win_rate=float((net > 0).mean()), avg_win=float(w.mean()), avg_loss=float(lo.mean()),
                                 rr=float(w.mean() / -lo.mean()), expectancy=float(net.mean()),
                                 avg_bars=float(np.mean([b - a for a, b in tr])))
    # 年度分解
    yr = {}
    for name, eq in eqs.items():
        y = eq.resample("YE").last()
        prev = np.r_[eq.iloc[0], y.to_numpy()[:-1]]
        yr[name] = dict(zip(map(int, y.index.year), map(float, y.to_numpy() / prev - 1)))

    boot = {f"{a}_vs_{b}": block_bootstrap_sharpe_diff(eqs[a], eqs[b]) for a, b in [
        ("S0_Trend", "BuyHold"), ("S1_Trend+MTF", "S0_Trend"), ("S3_Trend+AIvol", "S2_Trend+EWMAvol"),
        ("S3_Trend+AIvol", "S0_Trend"), ("S4_Trend+Meta", "S0_Trend"), ("S5_Trend+MTF+AIvol", "BuyHold")]}

    res = dict(config=dict(target_vol=TARGET_VOL, vol_horizon_bars=VOL_H, rebalance_band=REBAL_BAND,
                           fee=cfg.fee, slip=cfg.slip, test_start=cfg.test_start, reject_q=REJECT_Q),
               vol_forecast_eval=vol_eval, meta_eval=meta_eval, summary=summary, trend_trade_stats=trade_stats,
               sharpe_bootstrap=boot, yearly_returns=yr)
    (OUT / "results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    pd.DataFrame(eqs).to_csv(OUT / "equity.csv")
    tdf.to_csv(OUT / "meta_trend_trades.csv", index=False)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(2, 1, figsize=(12, 9), gridspec_kw=dict(height_ratios=[3, 1.4]))
    for name, eq in eqs.items():
        ax[0].plot(eq.index, eq.to_numpy(), label=name, lw=1.4 if "AI" in name else 1.0)
        ax[1].plot(eq.index, (eq / eq.cummax() - 1).to_numpy(), lw=0.9)
    ax[0].set_yscale("log"); ax[0].legend(); ax[0].grid(alpha=0.3)
    ax[0].set_title("v2: AI as risk/filter layer on 4h trend-following (OOS 2020+, costs incl.)")
    ax[1].set_title("Drawdown"); ax[1].grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "equity_v2.png", dpi=110)

    print(json.dumps(dict(vol_forecast_eval=vol_eval, meta_eval=meta_eval, trend_trade_stats=trade_stats,
                          sharpe_bootstrap=boot), indent=1))
    for k, s in summary.items():
        print(f"{k:22s} CAGR {s['cagr']:7.1%}  MDD {s['max_dd']:7.1%}  Sharpe {s['sharpe']:5.2f}  Calmar {s['calmar']:5.2f}  "
              f"expo {s['exposure']:4.0%}  rebal {s['rebalances']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
