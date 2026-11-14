#!/usr/bin/env python3
"""BTC AI 交易模块 PoC：LightGBM + Triple-Barrier + Walk-Forward + 真实摩擦成本。

两条实验线（同一套特征 / 标签 / 回测引擎 / 成本口径）：
  1) pure_ml   ：每根 K 线都是候选，LightGBM 直接预测「止盈先于止损触发」的概率，超过盈亏平衡概率+安全边际才入场；
  2) meta      ：规则策略（MA20 上穿 + MA120 趋势过滤）先产生基础买点，LightGBM 只做 Meta-Labeling 过滤器；
  基准         ：Buy & Hold、MA(20 日) 趋势均线、规则策略本身（不加 AI）。

防作弊纪律（全部写死在代码里，不靠自觉）：
  - 特征只用 t 时刻收盘前已知数据（rolling/shift 全部向后看），不输入原始价格，只输入收益率/比值/标准化量；
  - 入场 = 信号 K 线收盘价（+滑点），出场 = ATR 止损 / ATR 止盈 / 时间屏障，同根 K 线止损止盈同时触发一律按止损算，
    跳空穿越止损按开盘价成交；
  - Walk-Forward 扩张窗口，按季度重训；训练样本必须满足「标签结束时间 < 测试段开始 - embargo」（purge + embargo）；
  - 模型超参数、概率阈值规则在看测试集之前固定（阈值 = 盈亏平衡胜率 + 固定 margin，不在测试集上调参）；
  - 成本：单边手续费 fee + 单边滑点 slip，进出各收一次；
  - 显著性：同池随机抽样检验（模型挑的交易 vs 随机挑同样数量的交易）+ 打乱标签的 placebo 检验 + 对抗验证测分布漂移。

数据源：默认读仓库 data/crypto/BTCUSDT_{tf}.parquet（Binance 现货）；也支持 --csv 本地 CSV 或 --source ccxt 在线拉取。

用法：
    python3 experiments/btc_ai_poc/run_experiment.py                      # 主配置 4h, PT2/SL1/H24
    python3 experiments/btc_ai_poc/run_experiment.py --suite              # 主配置 + 稳健性配置 + placebo
    python3 experiments/btc_ai_poc/run_experiment.py --timeframe 1h --horizon 24 --tag tf1h
    python3 experiments/btc_ai_poc/run_experiment.py --csv my_btc_4h.csv  # 列: timestamp,open,high,low,close,volume
"""
from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

warnings.filterwarnings("ignore", category=UserWarning)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = Path(__file__).resolve().parent / "results"
BARS_PER_DAY = {"1h": 24, "4h": 6, "1d": 1}


# ----------------------------------------------------------------------------- config
@dataclass
class Config:
    timeframe: str = "4h"
    pt: float = 2.0            # 止盈 = pt × ATR14
    sl: float = 1.0            # 止损 = sl × ATR14
    horizon: int = 24          # 时间屏障（根 K 线）
    fee: float = 0.0005        # 单边手续费 0.05%
    slip: float = 0.0005       # 单边滑点 0.05%
    margin: float = 0.05       # 入场概率 > 盈亏平衡概率 + margin（事前固定）
    test_start: str = "2020-01-01"
    retrain_days: int = 91     # 季度重训
    train_window_days: int = 0 # 0 = 扩张窗口；>0 = 滚动窗口
    placebo: bool = False      # 训练标签随机打乱（应当无 alpha）
    seed: int = 7
    tag: str = "main"
    csv: str | None = None
    source: str = "local"
    n_perm: int = 5000
    lgb_params: dict = field(default_factory=lambda: dict(
        objective="binary", learning_rate=0.03, num_leaves=15, max_depth=5,
        min_child_samples=200, feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1,
        lambda_l2=5.0, n_estimators=300, verbose=-1,
    ))


# ----------------------------------------------------------------------------- data
def load_data(cfg: Config) -> pd.DataFrame:
    if cfg.csv:
        df = pd.read_csv(cfg.csv)
        ts_col = next(c for c in df.columns if c.lower() in {"timestamp", "time", "date", "open_time", "datetime"})
        ts = df[ts_col]
        df["ts"] = pd.to_datetime(ts, unit="ms", utc=True) if np.issubdtype(ts.dtype, np.number) else pd.to_datetime(ts, utc=True)
    elif cfg.source == "ccxt":
        import ccxt  # 延迟导入：仅在线拉取时需要

        ex = ccxt.binance({"enableRateLimit": True})
        since, rows = ex.parse8601("2017-08-17T00:00:00Z"), []
        while True:
            batch = ex.fetch_ohlcv("BTC/USDT", cfg.timeframe, since=since, limit=1000)
            if not batch:
                break
            rows += batch
            since = batch[-1][0] + 1
            if len(batch) < 1000:
                break
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    else:
        df = pd.read_parquet(REPO_ROOT / "data" / "crypto" / f"BTCUSDT_{cfg.timeframe}.parquet")
        df["ts"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    # 丢掉最后一根可能未收盘的 K 线
    return df.iloc[:-1].reset_index(drop=True)


# ----------------------------------------------------------------------------- features
def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """全部为平稳化特征：收益率、波动率、ATR 归一化的均线偏离/斜率、区间位置、成交量 z 分数等。

    所有 rolling/shift 只看过去 → t 时刻特征在 t 收盘时即可得到，不含未来信息。
    """
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]
    lr = np.log(c).diff()
    a = atr(df)
    atr_pct = a / c
    f = pd.DataFrame(index=df.index)
    for k in (1, 3, 6, 12, 24, 48, 120):
        f[f"ret_{k}"] = np.log(c / c.shift(k))
    for k in (12, 48, 120):
        f[f"vol_{k}"] = lr.rolling(k).std()
    f["vol_ratio"] = f["vol_12"] / f["vol_120"]
    f["atr_pct"] = atr_pct
    for k in (20, 50, 120, 300):
        ma = c.rolling(k).mean()
        f[f"ma_dev_{k}"] = (c / ma - 1) / atr_pct
        f[f"ma_slope_{k}"] = (ma / ma.shift(5) - 1) / atr_pct
    f["rsi_14"] = rsi(c, 14)
    sd20 = c.rolling(20).std()
    f["bb_pos"] = (c - c.rolling(20).mean()) / (2 * sd20)
    for k in (24, 120):
        lo, hi = l.rolling(k).min(), h.rolling(k).max()
        f[f"range_pos_{k}"] = (c - lo) / (hi - lo)
    f["dd_300"] = c / h.rolling(300).max() - 1
    f["body_atr"] = (c - o) / a
    f["upper_wick_atr"] = (h - np.maximum(c, o)) / a
    f["lower_wick_atr"] = (np.minimum(c, o) - l) / a
    lv = np.log(v.replace(0, np.nan))
    f["vol_z_120"] = (lv - lv.rolling(120).mean()) / lv.rolling(120).std()
    f["vol_z_24"] = (lv - lv.rolling(24).mean()) / lv.rolling(24).std()
    if "taker_buy_base" in df:
        tb = df["taker_buy_base"] / v.replace(0, np.nan)
        f["taker_ratio"] = tb
        f["taker_ratio_6"] = tb.rolling(6).mean() - tb.rolling(120).mean()
    if "trades" in df:
        lt = np.log(df["trades"].replace(0, np.nan))
        f["trades_z_120"] = (lt - lt.rolling(120).mean()) / lt.rolling(120).std()
    f["hour"] = df["ts"].dt.hour
    f["dow"] = df["ts"].dt.dayofweek
    return f.replace([np.inf, -np.inf], np.nan)


# ----------------------------------------------------------------------------- triple barrier
def triple_barrier(df: pd.DataFrame, atr_s: pd.Series, cfg: Config) -> pd.DataFrame:
    """对每根 K 线模拟一笔「收盘做多」交易，返回出场位置/价格/类型与净收益（与回测完全同口径）。

    出场优先级（保守）：开盘跳空穿止损→开盘价止损；开盘跳空过止盈→开盘价止盈；
    同一根内止损与止盈都被触及→按止损；到时间屏障→收盘价平仓。
    """
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    a = atr_s.to_numpy()
    n, H = len(df), cfg.horizon
    exit_idx = np.full(n, -1)
    exit_px = np.full(n, np.nan)
    exit_type = np.zeros(n, dtype=np.int8)   # 1=tp, -1=sl, 0=time, 9=未完成
    for t in range(n - 1):
        if not np.isfinite(a[t]):
            continue
        entry = c[t]
        stop, tp = entry - cfg.sl * a[t], entry + cfg.pt * a[t]
        j0, j1 = t + 1, min(t + H, n - 1)
        hit = (l[j0:j1 + 1] <= stop) | (h[j0:j1 + 1] >= tp)
        if hit.any():
            j = j0 + int(np.argmax(hit))
            if o[j] <= stop:
                exit_px[t], exit_type[t] = o[j], -1
            elif o[j] >= tp:
                exit_px[t], exit_type[t] = o[j], 1
            elif l[j] <= stop:
                exit_px[t], exit_type[t] = stop, -1
            else:
                exit_px[t], exit_type[t] = tp, 1
        else:
            j = j1
            exit_px[t], exit_type[t] = c[j], (0 if t + H <= n - 1 else 9)
        exit_idx[t] = j
    entry_eff = c * (1 + cfg.slip)
    exit_eff = exit_px * (1 - cfg.slip)
    net = exit_eff * (1 - cfg.fee) / (entry_eff * (1 + cfg.fee)) - 1
    risk_pct = cfg.sl * a / c  # 1R 占入场价比例
    out = pd.DataFrame({"exit_idx": exit_idx, "exit_px": exit_px, "exit_type": exit_type,
                        "net": net, "risk_pct": risk_pct})
    out["label"] = np.where(out["exit_type"] == 9, np.nan, (out["exit_type"] == 1).astype(float))
    out.loc[out["exit_idx"] < 0, "label"] = np.nan
    return out


# ----------------------------------------------------------------------------- primary rule
def primary_signal(df: pd.DataFrame) -> pd.Series:
    """基础规则买点：收盘上穿 MA20，且收盘在 MA120 之上、MA120 向上（顺势突破）。"""
    c = df["close"]
    ma20, ma120 = c.rolling(20).mean(), c.rolling(120).mean()
    cross = (c > ma20) & (c.shift(1) <= ma20.shift(1))
    return (cross & (c > ma120) & (ma120 > ma120.shift(5))).fillna(False)


# ----------------------------------------------------------------------------- walk-forward
def fold_starts(ts: pd.Series, cfg: Config) -> list[int]:
    start = pd.Timestamp(cfg.test_start, tz="UTC")
    bounds, cur = [], start
    while cur < ts.iloc[-1]:
        idx = int(ts.searchsorted(cur))
        if idx < len(ts):
            bounds.append(idx)
        cur += pd.Timedelta(days=cfg.retrain_days)
    return bounds


def walk_forward(X: pd.DataFrame, y: pd.Series, exit_idx: np.ndarray, cand: np.ndarray, ts: pd.Series,
                 cfg: Config, rng: np.random.Generator) -> tuple[np.ndarray, list[dict], pd.Series]:
    """扩张(或滚动)窗口滚动前推。只在候选样本 cand 上训练/预测。

    purge：训练样本的标签结束位置 exit_idx 必须 < 测试段起点 - embargo（embargo = horizon 根）。
    """
    n = len(X)
    proba = np.full(n, np.nan)
    starts = fold_starts(ts, cfg) + [n]
    folds, gains = [], []
    embargo = cfg.horizon
    bpd = BARS_PER_DAY.get(cfg.timeframe, 1)
    feat_ok = X.notna().all(axis=1).to_numpy()
    for k in range(len(starts) - 1):
        s, e = starts[k], starts[k + 1]
        tr = cand & feat_ok & np.isfinite(y.to_numpy()) & (exit_idx >= 0) & (exit_idx < s - embargo)
        tr &= np.arange(n) < s
        if cfg.train_window_days > 0:
            tr &= np.arange(n) >= s - cfg.train_window_days * bpd
        te = cand & feat_ok & (np.arange(n) >= s) & (np.arange(n) < e)
        if tr.sum() < 100 or te.sum() == 0:
            continue
        ytr = y.to_numpy()[tr].copy()
        if cfg.placebo:
            rng.shuffle(ytr)
        params = dict(cfg.lgb_params)
        if tr.sum() < 5000:
            # 小样本（meta-labeling 只有几百条）：事前固定的缩放规则，避免 min_child_samples > 样本数导致树不分裂
            params.update(num_leaves=7, max_depth=3, n_estimators=150,
                          min_child_samples=max(10, int(tr.sum()) // 15))
        model = lgb.LGBMClassifier(random_state=cfg.seed, **params)
        model.fit(X[tr], ytr)
        proba[te] = model.predict_proba(X[te])[:, 1]
        gains.append(pd.Series(model.booster_.feature_importance("gain"), index=X.columns))
        yte = y.to_numpy()[te]
        m = np.isfinite(yte)
        auc = roc_auc_score(yte[m], proba[te][m]) if m.sum() > 10 and 0 < yte[m].mean() < 1 else np.nan
        folds.append(dict(fold=k, test_start=str(ts.iloc[s].date()), n_train=int(tr.sum()), n_test=int(te.sum()),
                          train_pos_rate=float(ytr.mean()), test_pos_rate=float(np.nanmean(yte)), auc=float(auc)))
    imp = pd.concat(gains, axis=1).mean(axis=1).sort_values(ascending=False) if gains else pd.Series(dtype=float)
    return proba, folds, imp / imp.sum()


# ----------------------------------------------------------------------------- backtest
def run_trades(df: pd.DataFrame, tb: pd.DataFrame, signal: np.ndarray, start: int, cfg: Config) -> tuple[pd.Series, pd.DataFrame]:
    """单仓位、不加杠杆、全资金进出。信号 K 线收盘入场，出场严格按 triple_barrier 预计算结果（同口径）。"""
    n = len(df)
    c = df["close"].to_numpy()
    eq = np.ones(n)
    cur, t, trades = 1.0, start, []
    eq[:start] = 1.0
    while t < n:
        if signal[t] and tb["exit_idx"].iat[t] > t:
            e = int(tb["exit_idx"].iat[t])
            entry_eff = c[t] * (1 + cfg.slip)
            base = cur * (1 - cfg.fee) / entry_eff
            eq[t] = cur * (1 - cfg.fee) / (1 + cfg.slip)  # 入场当根已付成本（按收盘价估值）
            for j in range(t + 1, e):
                eq[j] = base * c[j]
            net = float(tb["net"].iat[t])
            cur *= 1 + net
            eq[e] = cur
            trades.append(dict(entry_idx=t, exit_idx=e, entry_time=df["ts"].iat[t], exit_time=df["ts"].iat[e],
                               exit_type=int(tb["exit_type"].iat[t]), net=net,
                               r_mult=net / float(tb["risk_pct"].iat[t]), bars=e - t))
            t = e + 1
            if t < n:
                eq[t - 1] = cur
            continue
        eq[t] = cur
        t += 1
    s = pd.Series(eq[start:], index=df["ts"].iloc[start:].to_numpy())
    return s, pd.DataFrame(trades)


def position_backtest(df: pd.DataFrame, pos: np.ndarray, start: int, cfg: Config) -> tuple[pd.Series, int]:
    """持仓型策略（B&H / 均线）：pos[t] 在 t 收盘决定并在 t 收盘成交，持有到下一根；每次换手收 fee+slip。"""
    c = df["close"].to_numpy()
    r = np.zeros(len(df))
    r[1:] = c[1:] / c[:-1] - 1
    p = pos.astype(float).copy()
    p[:start] = 0
    held = np.zeros(len(df))
    held[1:] = p[:-1]
    turn = np.abs(np.diff(np.r_[0.0, p]))
    cost = turn * (cfg.fee + cfg.slip)
    eq = np.cumprod((1 + held * r) * (1 - cost))
    s = pd.Series(eq[start:] / eq[start - 1] if start > 0 else eq, index=df["ts"].iloc[start:].to_numpy())
    return s, int((turn[start:] > 0).sum())


def equity_metrics(eq: pd.Series) -> dict:
    d = eq.resample("1D").last().dropna()
    dr = d.pct_change().dropna()
    years = (d.index[-1] - d.index[0]).days / 365.25
    total = d.iloc[-1] / d.iloc[0] - 1
    cagr = (d.iloc[-1] / d.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    mdd = float((d / d.cummax() - 1).min())
    sharpe = dr.mean() / dr.std() * math.sqrt(365) if dr.std() > 0 else np.nan
    return dict(total_return=float(total), cagr=float(cagr), max_dd=mdd, sharpe=float(sharpe),
                calmar=float(cagr / abs(mdd)) if mdd < 0 else np.nan, years=float(years))


def trade_metrics(tr: pd.DataFrame, years: float) -> dict:
    if tr.empty:
        return dict(n_trades=0)
    w, lo = tr.loc[tr.net > 0, "net"], tr.loc[tr.net <= 0, "net"]
    return dict(
        n_trades=int(len(tr)), trades_per_month=float(len(tr) / (years * 12)),
        win_rate=float((tr.net > 0).mean()), avg_win=float(w.mean()) if len(w) else 0.0,
        avg_loss=float(lo.mean()) if len(lo) else 0.0,
        rr=float(w.mean() / abs(lo.mean())) if len(w) and len(lo) else np.nan,
        expectancy_pct=float(tr.net.mean()), expectancy_r=float(tr.r_mult.mean()),
        avg_bars=float(tr.bars.mean()), tp_share=float((tr.exit_type == 1).mean()),
        sl_share=float((tr.exit_type == -1).mean()), time_share=float((tr.exit_type == 0).mean()),
        exposure=float(tr.bars.sum()),
    )


def random_pool_test(pool_net: np.ndarray, chosen_mean: float, k: int, n_perm: int, rng: np.random.Generator) -> float:
    """同池随机抽样：从同一候选池随机抽 k 笔，均值 >= 模型所选均值的比例（单侧 p 值）。"""
    if k == 0 or len(pool_net) < k:
        return float("nan")
    sims = np.array([rng.choice(pool_net, size=k, replace=False).mean() for _ in range(n_perm)])
    return float((sims >= chosen_mean).mean())


def adversarial_auc(X: pd.DataFrame, split: int, cfg: Config) -> float:
    """对抗验证：训练期 vs 测试期特征能否被区分；AUC 越接近 1 → 分布漂移越严重。"""
    ok = X.notna().all(axis=1).to_numpy()
    idx = np.arange(len(X))
    yy = (idx >= split).astype(int)
    perm = np.random.default_rng(cfg.seed).permutation(np.where(ok)[0])
    half = len(perm) // 2
    m = lgb.LGBMClassifier(n_estimators=200, num_leaves=15, min_child_samples=200, verbose=-1, random_state=cfg.seed)
    feats = [c for c in X.columns if c not in ("hour", "dow")]
    m.fit(X.iloc[perm[:half]][feats], yy[perm[:half]])
    return float(roc_auc_score(yy[perm[half:]], m.predict_proba(X.iloc[perm[half:]][feats])[:, 1]))


def yearly_returns(eqs: dict[str, pd.Series]) -> pd.DataFrame:
    rows = {}
    for name, eq in eqs.items():
        y = eq.resample("YE").last()
        prev = pd.concat([pd.Series([eq.iloc[0]], index=[eq.index[0]]), y.iloc[:-1]])
        rows[name] = pd.Series(y.to_numpy() / prev.to_numpy() - 1, index=y.index.year)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- main experiment
def run(cfg: Config, save_plots: bool = True) -> dict:
    rng = np.random.default_rng(cfg.seed)
    out_dir = OUT_ROOT / cfg.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    df = load_data(cfg)
    X = build_features(df)
    a = atr(df)
    tb = triple_barrier(df, a, cfg)
    y = tb["label"]
    ts = df["ts"]
    start = int(ts.searchsorted(pd.Timestamp(cfg.test_start, tz="UTC")))
    n = len(df)
    exit_idx = tb["exit_idx"].to_numpy()
    feat_ok = X.notna().all(axis=1).to_numpy()
    cost_rt = 2 * (cfg.fee + cfg.slip)
    # 盈亏平衡概率（label=止盈先到；忽略时间屏障的近似）：p*PT*atr - (1-p)*SL*atr - cost = 0
    atr_pct = (a / df["close"]).to_numpy()
    p_be = (cfg.sl * atr_pct + cost_rt) / ((cfg.pt + cfg.sl) * atr_pct)

    prim = primary_signal(df).to_numpy() & feat_ok
    all_cand = feat_ok.copy()

    # --- 1) pure ML
    proba_ml, folds_ml, imp_ml = walk_forward(X, y, exit_idx, all_cand, ts, cfg, rng)
    sig_ml = np.nan_to_num(proba_ml, nan=0) > p_be + cfg.margin
    # --- 2) meta-labeling（同一特征 + 规则信号上下文）
    proba_meta, folds_meta, imp_meta = walk_forward(X, y, exit_idx, prim, ts, cfg, rng)
    sig_meta = prim & (np.nan_to_num(proba_meta, nan=0) > p_be + cfg.margin)
    # 只在 walk-forward 实际覆盖的区间评估
    covered = np.zeros(n, dtype=bool)
    covered[start:] = True
    sig_ml &= covered
    sig_meta &= covered
    sig_prim = prim & covered

    eq_ml, tr_ml = run_trades(df, tb, sig_ml, start, cfg)
    eq_meta, tr_meta = run_trades(df, tb, sig_meta, start, cfg)
    eq_prim, tr_prim = run_trades(df, tb, sig_prim, start, cfg)
    bpd = BARS_PER_DAY.get(cfg.timeframe, 1)
    eq_bh, _ = position_backtest(df, np.ones(n), start, cfg)
    ma_len = 20 * bpd
    ma_pos = (df["close"] > df["close"].rolling(ma_len).mean()).to_numpy()
    eq_ma, ma_switches = position_backtest(df, ma_pos, start, cfg)

    strategies = {
        "AI_pure_ML": (eq_ml, tr_ml),
        "AI_meta_filter": (eq_meta, tr_meta),
        "Rule_primary": (eq_prim, tr_prim),
        "BuyHold": (eq_bh, None),
        f"MA{20}d_trend": (eq_ma, None),
    }
    summary = {}
    for name, (eq, tr) in strategies.items():
        em = equity_metrics(eq)
        row = dict(em)
        if tr is not None:
            row.update(trade_metrics(tr, em["years"]))
            row["exposure"] = row.get("exposure", 0) / max(1, n - start)
        elif name.startswith("MA"):
            row.update(dict(n_trades=ma_switches // 2, exposure=float(ma_pos[start:].mean())))
        else:
            row.update(dict(n_trades=1, exposure=1.0))
        summary[name] = row

    # --- 显著性：模型挑的交易 vs 同池随机挑
    test_mask = covered & np.isfinite(tb["net"].to_numpy()) & (tb["exit_type"].to_numpy() != 9)
    pool_all = tb["net"].to_numpy()[test_mask & all_cand]
    pool_prim = tb["net"].to_numpy()[test_mask & prim]
    sig_tests = {
        "AI_pure_ML_vs_random_all_bars": dict(
            k=len(tr_ml), chosen_mean=float(tr_ml.net.mean()) if len(tr_ml) else None,
            pool_mean=float(pool_all.mean()),
            p_value=random_pool_test(pool_all, tr_ml.net.mean(), len(tr_ml), cfg.n_perm, rng) if len(tr_ml) else None),
        "AI_meta_vs_random_primary": dict(
            k=len(tr_meta), chosen_mean=float(tr_meta.net.mean()) if len(tr_meta) else None,
            pool_mean=float(pool_prim.mean()),
            p_value=random_pool_test(pool_prim, tr_meta.net.mean(), len(tr_meta), cfg.n_perm, rng) if len(tr_meta) else None),
    }
    # 不受单仓位约束的「信号层」统计：所有被选中的 bar 的平均净收益 vs 未被选中
    sel_ml = sig_ml & test_mask
    sel_meta = sig_meta & test_mask
    signal_layer = dict(
        ml_selected_mean=float(np.nanmean(tb["net"].to_numpy()[sel_ml])) if sel_ml.any() else None,
        ml_rejected_mean=float(np.nanmean(tb["net"].to_numpy()[test_mask & ~sig_ml])),
        meta_selected_mean=float(np.nanmean(tb["net"].to_numpy()[sel_meta])) if sel_meta.any() else None,
        meta_rejected_mean=float(np.nanmean(tb["net"].to_numpy()[test_mask & prim & ~sig_meta])),
        n_ml_selected_bars=int(sel_ml.sum()), n_meta_selected=int(sel_meta.sum()), n_primary=int((test_mask & prim).sum()),
    )
    # 阈值敏感性（诊断用，不参与决策）：不同 margin 下 pure ML 选中 bar 的平均净收益
    sens = []
    for mg in (-0.05, 0.0, 0.025, 0.05, 0.075, 0.10, 0.15):
        s = (np.nan_to_num(proba_ml, nan=0) > p_be + mg) & test_mask
        sm = (np.nan_to_num(proba_meta, nan=0) > p_be + mg) & test_mask & prim
        sens.append(dict(margin=mg, ml_n=int(s.sum()), ml_mean_net=float(np.nanmean(tb["net"].to_numpy()[s])) if s.any() else None,
                         meta_n=int(sm.sum()), meta_mean_net=float(np.nanmean(tb["net"].to_numpy()[sm])) if sm.any() else None))
    # 概率分箱校准：预测概率 vs 实际止盈率
    m = test_mask & np.isfinite(proba_ml) & np.isfinite(y.to_numpy())
    calib = pd.DataFrame({"p": proba_ml[m], "y": y.to_numpy()[m], "net": tb["net"].to_numpy()[m]})
    calib["bin"] = pd.qcut(calib["p"], 10, labels=False, duplicates="drop")
    calib_tbl = calib.groupby("bin").agg(p_mean=("p", "mean"), hit_rate=("y", "mean"), mean_net=("net", "mean"), n=("y", "size"))
    oos_auc_ml = float(roc_auc_score(calib["y"], calib["p"]))
    mm = test_mask & prim & np.isfinite(proba_meta) & np.isfinite(y.to_numpy())
    oos_auc_meta = float(roc_auc_score(y.to_numpy()[mm], proba_meta[mm])) if mm.sum() > 20 else None

    adv = adversarial_auc(X, start, cfg)
    base_rate_by_year = pd.Series(y.to_numpy(), index=ts.dt.year).groupby(level=0).mean()
    yr = yearly_returns({k: v[0] for k, v in strategies.items()})

    result = dict(
        config={k: v for k, v in asdict(cfg).items() if k != "lgb_params"}, lgb_params=cfg.lgb_params,
        data=dict(first=str(ts.iloc[0]), last=str(ts.iloc[-1]), bars=n, test_bars=n - start),
        cost_round_trip=cost_rt, median_p_breakeven=float(np.nanmedian(p_be[start:])),
        median_atr_pct=float(np.nanmedian(atr_pct[start:])),
        median_cost_in_R=float(np.nanmedian(cost_rt / (cfg.sl * atr_pct[start:]))),
        summary=summary, significance=sig_tests, signal_layer=signal_layer, threshold_sensitivity=sens,
        oos_auc=dict(pure_ml=oos_auc_ml, meta=oos_auc_meta),
        folds=dict(pure_ml=folds_ml, meta=folds_meta),
        calibration=calib_tbl.reset_index().to_dict(orient="records"),
        feature_importance=dict(pure_ml=imp_ml.head(15).round(4).to_dict(), meta=imp_meta.head(15).round(4).to_dict()),
        adversarial_auc_train_vs_test=adv,
        label_base_rate_by_year={int(k): float(v) for k, v in base_rate_by_year.items()},
        yearly_returns={int(k): {c: (None if pd.isna(v) else float(v)) for c, v in row.items()} for k, row in yr.iterrows()},
    )
    (out_dir / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    for name, (_, tr) in strategies.items():
        if tr is not None and not tr.empty:
            tr.to_csv(out_dir / f"trades_{name}.csv", index=False)
    pd.DataFrame({k: v[0] for k, v in strategies.items()}).to_csv(out_dir / "equity.csv")
    if save_plots:
        plot(strategies, out_dir, cfg, folds_ml, folds_meta)
    return result


def plot(strategies: dict, out_dir: Path, cfg: Config, folds_ml: list, folds_meta: list) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(3, 1, figsize=(12, 12), gridspec_kw=dict(height_ratios=[3, 1.4, 1.4]))
    for name, (eq, _) in strategies.items():
        axes[0].plot(eq.index, eq.to_numpy(), label=name, lw=1.3 if name.startswith("AI") else 1.0)
    axes[0].set_yscale("log")
    axes[0].set_title(f"BTCUSDT {cfg.timeframe} OOS equity (log) | PT{cfg.pt}/SL{cfg.sl} ATR, H={cfg.horizon}, "
                      f"cost {2 * (cfg.fee + cfg.slip):.2%} RT  [{cfg.tag}]")
    axes[0].legend()
    axes[0].grid(alpha=0.3)
    for name, (eq, _) in strategies.items():
        axes[1].plot(eq.index, (eq / eq.cummax() - 1).to_numpy(), lw=0.9, label=name)
    axes[1].set_title("Drawdown")
    axes[1].grid(alpha=0.3)
    fm = pd.DataFrame(folds_ml)
    fmm = pd.DataFrame(folds_meta)
    if not fm.empty:
        axes[2].plot(pd.to_datetime(fm.test_start), fm.auc, marker="o", label="pure ML fold AUC")
    if not fmm.empty:
        axes[2].plot(pd.to_datetime(fmm.test_start), fmm.auc, marker="s", label="meta fold AUC")
    axes[2].axhline(0.5, color="k", lw=0.8, ls="--")
    axes[2].set_title("Walk-forward OOS AUC per quarter")
    axes[2].legend()
    axes[2].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "equity_drawdown_auc.png", dpi=110)
    plt.close(fig)


# ----------------------------------------------------------------------------- CLI
SUITE = [
    dict(tag="main_4h_pt2_sl1_h24"),
    dict(tag="placebo_4h", placebo=True),
    dict(tag="cost_zero_4h", fee=0.0, slip=0.0),
    dict(tag="rolling3y_4h", train_window_days=3 * 365),
    dict(tag="wide_4h_pt3_sl1.5_h48", pt=3.0, sl=1.5, horizon=48),
    dict(tag="trend_4h_pt6_sl2_h120", pt=6.0, sl=2.0, horizon=120),
    dict(tag="tf1h_pt2_sl1_h24", timeframe="1h", horizon=24),
    dict(tag="tf1d_pt2_sl1_h10", timeframe="1d", horizon=10, retrain_days=182),
]


def headline(res: dict) -> str:
    rows = []
    for name, s in res["summary"].items():
        rows.append(f"  {name:16s} CAGR {s['cagr']:7.1%}  MDD {s['max_dd']:7.1%}  Sharpe {s['sharpe']:5.2f}  "
                    f"Calmar {s['calmar']:5.2f}  n={s.get('n_trades', 0):4d}  "
                    f"win {s.get('win_rate', float('nan')):5.1%}  RR {s.get('rr', float('nan')):4.2f}  "
                    f"E[R] {s.get('expectancy_r', float('nan')):6.3f}")
    sig = res["significance"]
    rows.append(f"  OOS AUC pure={res['oos_auc']['pure_ml']:.3f} meta={res['oos_auc']['meta']}  "
                f"adv-AUC={res['adversarial_auc_train_vs_test']:.3f}  "
                f"p(ML)={sig['AI_pure_ML_vs_random_all_bars']['p_value']}  p(meta)={sig['AI_meta_vs_random_primary']['p_value']}")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for f_name, f_def in Config.__dataclass_fields__.items():
        if f_name == "lgb_params":
            continue
        default = f_def.default
        if isinstance(default, bool):
            ap.add_argument(f"--{f_name.replace('_', '-')}", action="store_true", default=default)
        else:
            ap.add_argument(f"--{f_name.replace('_', '-')}", type=type(default) if default is not None else str, default=default)
    ap.add_argument("--suite", action="store_true", help="跑主配置 + 稳健性/对照配置")
    args = vars(ap.parse_args())
    suite = args.pop("suite")
    base = {k: v for k, v in args.items()}
    runs = [dict(base, **over) for over in SUITE] if suite else [base]
    index = {}
    for r in runs:
        cfg = Config(**r)
        print(f"\n=== {cfg.tag} ({cfg.timeframe}, PT{cfg.pt}/SL{cfg.sl}/H{cfg.horizon}, fee {cfg.fee}, slip {cfg.slip}, "
              f"placebo={cfg.placebo}) ===", flush=True)
        res = run(cfg)
        print(headline(res), flush=True)
        index[cfg.tag] = {k: res[k] for k in ("summary", "significance", "oos_auc", "adversarial_auc_train_vs_test")}
    if suite:
        (OUT_ROOT / "suite_index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
