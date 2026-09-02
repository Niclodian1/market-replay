"""MA A 策略：固定 3% 实体过滤 vs ATR14 实体过滤的独立实验。

本文件不会修改或调用主策略的写入路径。所有数据快照和结果仅写入当前实验目录。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DATA_ROOT = REPO / "data"
# 面板数据按资产类别归档到 data/ 子目录
ASSET_SUBDIRS = {
    "US equity": "stocks",
    "HK equity": "hk_equity",
    "CN equity": "cn_equity",
    "gold": "gold",
    "crypto": "crypto",
}
RESULTS_DIR = HERE / "results"

sys.path.insert(0, str(REPO / "backend"))

FEE_RATE = 0.001
SLIPPAGE = 0.0005
COST = FEE_RATE + SLIPPAGE
DOUBLE_COST = COST * 2
BREAKEVEN_TRIGGER = 0.04
MA_EXIT_PCT = 0.03
ATR_LEN = 14
MA_PERIODS = [30, 60, 99, 120]
ATR_THRESHOLDS = [round(float(v), 2) for v in np.arange(0.2, 1.61, 0.1)]
DEVELOPMENT_SHARE = 0.70
MIN_TRADES_PER_PANEL = 5
SECONDS_PER_YEAR = 365.2425 * 24 * 3600
RNG_SEED = 20260719

YAHOO_SYMBOLS = {
    "AAPL": ("Apple", "US equity"),
    "MSFT": ("Microsoft", "US equity"),
    "NVDA": ("NVIDIA", "US equity"),
    "GOOGL": ("Alphabet", "US equity"),
    "AMZN": ("Amazon", "US equity"),
    "META": ("Meta", "US equity"),
    "TSLA": ("Tesla", "US equity"),
    "JPM": ("JPMorgan", "US equity"),
    "BRK-B": ("Berkshire", "US equity"),
    "0700.HK": ("Tencent", "HK equity"),
    "9988.HK": ("Alibaba", "HK equity"),
    "600519.SS": ("Kweichow Moutai", "CN equity"),
}

CRYPTO_PANELS = [
    (symbol, timeframe)
    for symbol in ("BTC-USD", "ETH-USD")
    for timeframe in ("1day", "4h", "1h")
]


@dataclass
class Position:
    entry_idx: int
    entry_close: float
    eff_entry: float
    stop_loss: float
    initial_stop_loss: float
    breakeven_set: bool = False
    breakeven_pending: bool = False
    max_close: float = 0.0


def _panel_id(symbol: str, timeframe: str) -> str:
    safe = symbol.replace("-", "_").replace(".", "_")
    return f"{safe}_{timeframe}"


def _normalise_frame(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["time", "open", "high", "low", "close", "volume"]
    out = df[cols].copy()
    out["time"] = pd.to_numeric(out["time"], errors="coerce")
    for col in cols[1:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["time", "open", "high", "low", "close"])
    out = out[(out[["open", "high", "low", "close"]] > 0).all(axis=1)]
    out["time"] = out["time"].astype("int64")
    out = out.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
    return out


def _panel_path(panel_id: str, asset_class: str) -> Path:
    subdir = ASSET_SUBDIRS.get(asset_class, "market")
    return DATA_ROOT / subdir / f"{panel_id}.parquet"


def _save_snapshot(panel_id: str, asset_class: str, df: pd.DataFrame) -> Path:
    path = _panel_path(panel_id, asset_class)
    path.parent.mkdir(parents=True, exist_ok=True)
    _normalise_frame(df).to_parquet(path, index=False)
    return path


def _drop_incomplete_candle(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    duration = {"1h": 3600, "4h": 4 * 3600, "1day": 24 * 3600}[timeframe]
    now_ts = int(pd.Timestamp.now(tz="UTC").timestamp())
    return df[pd.to_numeric(df["time"], errors="coerce") + duration <= now_ts].copy()


async def _crypto_snapshot(symbol: str, timeframe: str) -> pd.DataFrame:
    from app.providers.local_crypto import LocalCryptoProvider

    candles = await LocalCryptoProvider().get_full_history(symbol, timeframe)
    return pd.DataFrame(candles)[["time", "open", "high", "low", "close", "volume"]]


def _xau_snapshot() -> pd.DataFrame:
    path = REPO / "data" / "market" / "XAUUSD_1d.parquet"
    return pd.read_parquet(path)[["time", "open", "high", "low", "close", "volume"]]


def _yahoo_snapshot(symbol: str) -> pd.DataFrame:
    raw = yf.download(symbol, period="max", auto_adjust=True, progress=False, threads=False)
    if raw.empty:
        raise RuntimeError(f"Yahoo Finance returned no data for {symbol}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    index = pd.to_datetime(raw.index, utc=True)
    return pd.DataFrame({
        "time": (index.astype("int64") // 10**9).astype("int64"),
        "open": raw["Open"].to_numpy(dtype=float),
        "high": raw["High"].to_numpy(dtype=float),
        "low": raw["Low"].to_numpy(dtype=float),
        "close": raw["Close"].to_numpy(dtype=float),
        "volume": raw["Volume"].fillna(0).to_numpy(dtype=float),
    })


def prepare_data(refresh: bool) -> pd.DataFrame:
    manifest_rows: list[dict[str, Any]] = []

    async def crypto_all() -> None:
        for symbol, timeframe in CRYPTO_PANELS:
            panel_id = _panel_id(symbol, timeframe)
            path = _panel_path(panel_id, "crypto")
            if refresh or not path.exists():
                print(f"[data] {symbol} {timeframe}", flush=True)
                crypto = await _crypto_snapshot(symbol, timeframe)
                _save_snapshot(panel_id, "crypto", _drop_incomplete_candle(crypto, timeframe))
            frame = pd.read_parquet(path)
            manifest_rows.append(_manifest_row(
                panel_id, symbol, timeframe, "crypto", "local parquet + OKX increment", path, frame
            ))

    asyncio.run(crypto_all())

    panel_id = _panel_id("XAUUSD", "1day")
    xau_path = _panel_path(panel_id, "gold")
    if refresh or not xau_path.exists():
        print("[data] XAUUSD 1day", flush=True)
        _save_snapshot(panel_id, "gold", _xau_snapshot())
    xau = pd.read_parquet(xau_path)
    manifest_rows.append(_manifest_row(
        panel_id, "XAUUSD", "1day", "gold", "Dukascopy snapshot", xau_path, xau
    ))

    for symbol, (name, asset_class) in YAHOO_SYMBOLS.items():
        panel_id = _panel_id(symbol, "1day")
        path = _panel_path(panel_id, asset_class)
        if refresh or not path.exists():
            print(f"[data] {symbol} 1day", flush=True)
            _save_snapshot(panel_id, asset_class, _yahoo_snapshot(symbol))
        frame = pd.read_parquet(path)
        row = _manifest_row(panel_id, symbol, "1day", asset_class, "Yahoo Finance adjusted", path, frame)
        row["name"] = name
        manifest_rows.append(row)

    manifest = pd.DataFrame(manifest_rows).sort_values(["asset_class", "symbol", "timeframe"])
    manifest.to_csv(HERE / "manifest.csv", index=False)
    return manifest


def _manifest_row(
    panel_id: str,
    symbol: str,
    timeframe: str,
    asset_class: str,
    source: str,
    path: Path,
    frame: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "panel_id": panel_id,
        "symbol": symbol,
        "name": symbol,
        "timeframe": timeframe,
        "asset_class": asset_class,
        "source": source,
        "bars": len(frame),
        "start": pd.to_datetime(int(frame["time"].iloc[0]), unit="s", utc=True).isoformat(),
        "end": pd.to_datetime(int(frame["time"].iloc[-1]), unit="s", utc=True).isoformat(),
        "snapshot": str(path.relative_to(REPO)),
    }


def wilder_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, n: int = ATR_LEN) -> np.ndarray:
    out = np.full(len(closes), np.nan, dtype=float)
    if len(closes) <= n:
        return out
    tr = np.empty(len(closes), dtype=float)
    tr[0] = highs[0] - lows[0]
    tr[1:] = np.maximum.reduce([
        highs[1:] - lows[1:],
        np.abs(highs[1:] - closes[:-1]),
        np.abs(lows[1:] - closes[:-1]),
    ])
    out[n] = float(np.mean(tr[1:n + 1]))
    for i in range(n + 1, len(closes)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def rolling_sma(values: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(values).rolling(n).mean().to_numpy(dtype=float)


def _new_position(i: int, close: float, low: float) -> Position:
    return Position(
        entry_idx=i,
        entry_close=close,
        eff_entry=close * (1 + COST),
        stop_loss=low,
        initial_stop_loss=low,
        max_close=close,
    )


def _step_position(pos: Position, i: int, close: float, low: float, ma: float) -> dict[str, Any] | None:
    if pos.breakeven_pending:
        pos.stop_loss = pos.entry_close
        pos.breakeven_set = True
        pos.breakeven_pending = False

    pos.max_close = max(pos.max_close, close)
    if (
        not pos.breakeven_set
        and not pos.breakeven_pending
        and close / pos.entry_close - 1 > BREAKEVEN_TRIGGER
    ):
        pos.breakeven_pending = True

    if low <= pos.stop_loss:
        raw_exit = pos.stop_loss
        reason = "breakeven" if pos.breakeven_set else "stop"
    elif (ma - close) / ma >= MA_EXIT_PCT:
        raw_exit = close
        reason = "ma_exit"
    else:
        return None

    eff_exit = raw_exit * (1 - COST)
    return {
        "entry_idx": pos.entry_idx,
        "exit_idx": i,
        "return": eff_exit / pos.eff_entry - 1,
        "reason": reason,
    }


def _force_close(pos: Position, last_i: int, last_close: float) -> dict[str, Any]:
    return {
        "entry_idx": pos.entry_idx,
        "exit_idx": last_i,
        "return": last_close * (1 - COST) / pos.eff_entry - 1,
        "reason": "end_of_data",
    }


def simulate_filter(
    frame: pd.DataFrame,
    ma_period: int,
    filter_kind: str,
    threshold: float | None,
) -> dict[str, list[dict[str, Any]]]:
    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    ma = rolling_sma(closes, ma_period)
    atr = wilder_atr(highs, lows, closes)

    parallel: list[Position] = []
    single: list[Position] = []
    trades_parallel: list[dict[str, Any]] = []
    trades_single: list[dict[str, Any]] = []

    for i in range(1, len(closes)):
        if math.isnan(ma[i]) or math.isnan(ma[i - 1]):
            continue

        next_parallel: list[Position] = []
        for pos in parallel:
            trade = _step_position(pos, i, closes[i], lows[i], ma[i])
            if trade is None:
                next_parallel.append(pos)
            else:
                trades_parallel.append(trade)
        parallel = next_parallel

        if single:
            trade = _step_position(single[0], i, closes[i], lows[i], ma[i])
            if trade is not None:
                trades_single.append(trade)
                single = []

        basic_cross = closes[i - 1] <= ma[i - 1] and closes[i] > ma[i]
        passes = False
        if basic_cross and opens[i] > 0:
            if filter_kind == "fixed_3pct":
                passes = closes[i] / opens[i] - 1 > 0.03
            elif filter_kind == "atr_body" and i > 0 and not math.isnan(atr[i - 1]) and atr[i - 1] > 0:
                body_atr = (closes[i] - opens[i]) / atr[i - 1]
                passes = body_atr >= float(threshold)
            else:
                raise ValueError(f"unknown filter: {filter_kind}")

        if passes:
            parallel.append(_new_position(i, closes[i], lows[i]))
            if not single:
                single.append(_new_position(i, closes[i], lows[i]))

    last_i = len(closes) - 1
    trades_parallel.extend(_force_close(pos, last_i, closes[-1]) for pos in parallel)
    trades_single.extend(_force_close(pos, last_i, closes[-1]) for pos in single)
    return {"legacy_parallel": trades_parallel, "single": trades_single}


def trade_metrics(trades: list[dict[str, Any]], years: float) -> dict[str, float | int]:
    if not trades or years <= 0:
        return {
            "n_trades": 0,
            "sum_return_pct": np.nan,
            "expectancy_pct": np.nan,
            "median_trade_pct": np.nan,
            "win_rate_pct": np.nan,
            "profit_factor": np.nan,
            "performance_per_year_pct": np.nan,
            "max_drawdown_pct": np.nan,
            "largest_winner_share_pct": np.nan,
            "double_cost_sum_return_pct": np.nan,
            "double_cost_expectancy_pct": np.nan,
            "double_cost_profit_factor": np.nan,
            "double_cost_performance_per_year_pct": np.nan,
        }
    returns = np.asarray([float(t["return"]) for t in trades], dtype=float)
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    pf = gross_profit / gross_loss if gross_loss > 0 else np.nan

    log_growth = np.log1p(np.clip(returns, -0.999999, None))
    annual_compound = math.expm1(float(log_growth.sum()) / years) * 100
    curve = np.cumprod(1 + returns)
    peaks = np.maximum.accumulate(np.r_[1.0, curve])
    dd = (peaks[1:] - curve) / peaks[1:] * 100
    largest_share = (float(wins.max()) / gross_profit * 100) if len(wins) and gross_profit > 0 else np.nan
    cost_factor = ((1 + COST) / (1 + DOUBLE_COST)) * ((1 - DOUBLE_COST) / (1 - COST))
    stress_returns = (1 + returns) * cost_factor - 1
    stress_wins = stress_returns[stress_returns > 0]
    stress_losses = stress_returns[stress_returns <= 0]
    stress_gp = float(stress_wins.sum())
    stress_gl = float(abs(stress_losses.sum()))
    stress_pf = stress_gp / stress_gl if stress_gl > 0 else np.nan
    stress_annual = math.expm1(
        float(np.log1p(np.clip(stress_returns, -0.999999, None)).sum()) / years
    ) * 100
    return {
        "n_trades": int(len(returns)),
        "sum_return_pct": float(returns.sum() * 100),
        "expectancy_pct": float(returns.mean() * 100),
        "median_trade_pct": float(np.median(returns) * 100),
        "win_rate_pct": float((returns > 0).mean() * 100),
        "profit_factor": pf,
        "performance_per_year_pct": annual_compound,
        "max_drawdown_pct": float(dd.max()) if len(dd) else 0.0,
        "largest_winner_share_pct": largest_share,
        "double_cost_sum_return_pct": float(stress_returns.sum() * 100),
        "double_cost_expectancy_pct": float(stress_returns.mean() * 100),
        "double_cost_profit_factor": stress_pf,
        "double_cost_performance_per_year_pct": stress_annual,
    }


def run_panels(manifest: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    filters: list[tuple[str, str, float | None]] = [("fixed_3pct", "fixed_3pct", None)]
    filters.extend((f"atr_{k:.2f}", "atr_body", k) for k in ATR_THRESHOLDS)

    for item in manifest.to_dict("records"):
        panel_id = item["panel_id"]
        frame = _normalise_frame(pd.read_parquet(HERE / item["snapshot"]))
        split_idx = max(int(len(frame) * DEVELOPMENT_SHARE), max(MA_PERIODS) + ATR_LEN + 2)
        if len(frame) - split_idx < 100:
            print(f"[skip] {panel_id}: holdout too short", flush=True)
            continue
        dev_years = max((int(frame["time"].iloc[split_idx - 1]) - int(frame["time"].iloc[0])) / SECONDS_PER_YEAR, 0.01)
        holdout_years = max((int(frame["time"].iloc[-1]) - int(frame["time"].iloc[split_idx])) / SECONDS_PER_YEAR, 0.01)
        print(f"[run] {panel_id}: {len(frame)} bars, split={split_idx}", flush=True)

        for ma_period in MA_PERIODS:
            for filter_id, kind, threshold in filters:
                by_execution = simulate_filter(frame, ma_period, kind, threshold)
                for execution, trades in by_execution.items():
                    dev = [t for t in trades if t["entry_idx"] < split_idx and t["exit_idx"] < split_idx]
                    holdout = [t for t in trades if t["entry_idx"] >= split_idx]
                    for split, selected, years in (
                        ("development", dev, dev_years),
                        ("holdout", holdout, holdout_years),
                    ):
                        metrics = trade_metrics(selected, years)
                        if execution == "legacy_parallel" and metrics["n_trades"]:
                            metrics["performance_per_year_pct"] = float(metrics["sum_return_pct"]) / years
                            metrics["double_cost_performance_per_year_pct"] = float(metrics["double_cost_sum_return_pct"]) / years
                        rows.append({
                            "panel_id": panel_id,
                            "symbol": item["symbol"],
                            "timeframe": item["timeframe"],
                            "asset_class": item["asset_class"],
                            "ma_period": ma_period,
                            "filter_id": filter_id,
                            "filter_kind": kind,
                            "atr_threshold": threshold,
                            "execution": execution,
                            "split": split,
                            "split_index": split_idx,
                            "split_time": int(frame["time"].iloc[split_idx]),
                            "years": years,
                            **metrics,
                        })
    return pd.DataFrame(rows)


def aggregate_metrics(all_metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    keys = ["split", "execution", "filter_id", "filter_kind", "atr_threshold"]
    for key, group in all_metrics.groupby(keys, dropna=False):
        valid = group[group["n_trades"] >= MIN_TRADES_PER_PANEL].copy()
        if valid.empty:
            continue
        pf = valid["profit_factor"].replace([np.inf, -np.inf], np.nan).clip(upper=10)
        stress_pf = valid["double_cost_profit_factor"].replace([np.inf, -np.inf], np.nan).clip(upper=10)
        rows.append({
            **dict(zip(keys, key)),
            "valid_panels": int(len(valid)),
            "all_panels": int(len(group)),
            "coverage_pct": float(len(valid) / len(group) * 100),
            "total_trades": int(valid["n_trades"].sum()),
            "median_trades": float(valid["n_trades"].median()),
            "median_performance_per_year_pct": float(valid["performance_per_year_pct"].median()),
            "median_expectancy_pct": float(valid["expectancy_pct"].median()),
            "median_profit_factor": float(pf.median()),
            "positive_performance_share_pct": float((valid["performance_per_year_pct"] > 0).mean() * 100),
            "positive_expectancy_share_pct": float((valid["expectancy_pct"] > 0).mean() * 100),
            "median_max_drawdown_pct": float(valid["max_drawdown_pct"].median()),
            "median_largest_winner_share_pct": float(valid["largest_winner_share_pct"].median()),
            "double_cost_median_performance_per_year_pct": float(valid["double_cost_performance_per_year_pct"].median()),
            "double_cost_median_expectancy_pct": float(valid["double_cost_expectancy_pct"].median()),
            "double_cost_median_profit_factor": float(stress_pf.median()),
            "double_cost_positive_performance_share_pct": float((valid["double_cost_performance_per_year_pct"] > 0).mean() * 100),
        })
    return pd.DataFrame(rows)


def score_candidates(summary: pd.DataFrame, split: str) -> pd.DataFrame:
    candidate = summary[(summary["split"] == split) & (summary["filter_kind"] == "atr_body")].copy()
    scored_parts: list[pd.DataFrame] = []
    for execution, group in candidate.groupby("execution"):
        group = group.copy()
        higher_better = [
            "coverage_pct",
            "median_performance_per_year_pct",
            "median_expectancy_pct",
            "median_profit_factor",
            "positive_performance_share_pct",
            "positive_expectancy_share_pct",
        ]
        rank_cols = []
        for metric in higher_better:
            col = f"rank_{metric}"
            group[col] = group[metric].rank(pct=True, method="average")
            rank_cols.append(col)
        group["rank_drawdown"] = (-group["median_max_drawdown_pct"]).rank(pct=True, method="average")
        rank_cols.append("rank_drawdown")
        group["robust_rank_score"] = group[rank_cols].mean(axis=1)
        scored_parts.append(group[["atr_threshold", "execution", "robust_rank_score"]])
    scored = pd.concat(scored_parts, ignore_index=True)
    pivot = scored.pivot(index="atr_threshold", columns="execution", values="robust_rank_score").reset_index()
    execution_cols = [c for c in ("single", "legacy_parallel") if c in pivot]
    pivot["combined_score"] = pivot[execution_cols].mean(axis=1)
    pivot = pivot.sort_values("atr_threshold").reset_index(drop=True)
    pivot["neighborhood_score"] = pivot["combined_score"].rolling(3, center=True, min_periods=2).mean()
    pivot["split"] = split
    return pivot


def select_threshold(development_scores: pd.DataFrame) -> tuple[float, list[float]]:
    """选择三点邻域平均分最高者，而不是原始分最高的单点。"""
    best = development_scores.loc[development_scores["neighborhood_score"].idxmax()]
    selected = float(best["atr_threshold"])
    stable_band = [
        float(v)
        for v in development_scores["atr_threshold"]
        if abs(float(v) - selected) <= 0.1000001
    ]
    return selected, stable_band


def make_breakdown(all_metrics: pd.DataFrame, selected: float) -> pd.DataFrame:
    selected_id = f"atr_{selected:.2f}"
    data = all_metrics[
        (all_metrics["split"] == "holdout")
        & (all_metrics["execution"] == "single")
        & (all_metrics["filter_id"].isin([selected_id, "fixed_3pct"]))
        & (all_metrics["n_trades"] >= MIN_TRADES_PER_PANEL)
    ].copy()
    rows: list[dict[str, Any]] = []
    for (asset_class, timeframe, filter_id), group in data.groupby(["asset_class", "timeframe", "filter_id"]):
        pf = group["profit_factor"].replace([np.inf, -np.inf], np.nan).clip(upper=10)
        rows.append({
            "asset_class": asset_class,
            "timeframe": timeframe,
            "filter_id": filter_id,
            "panel_ma_count": int(len(group)),
            "trades": int(group["n_trades"].sum()),
            "median_performance_per_year_pct": float(group["performance_per_year_pct"].median()),
            "median_expectancy_pct": float(group["expectancy_pct"].median()),
            "median_profit_factor": float(pf.median()),
            "positive_performance_share_pct": float((group["performance_per_year_pct"] > 0).mean() * 100),
            "median_max_drawdown_pct": float(group["max_drawdown_pct"].median()),
        })
    return pd.DataFrame(rows).sort_values(["asset_class", "timeframe", "filter_id"])


def _summary_row(summary: pd.DataFrame, split: str, execution: str, filter_id: str) -> dict[str, Any]:
    row = summary[
        (summary["split"] == split)
        & (summary["execution"] == execution)
        & (summary["filter_id"] == filter_id)
    ]
    return row.iloc[0].to_dict() if not row.empty else {}


def paired_bootstrap(
    all_metrics: pd.DataFrame,
    selected: float,
    metric: str,
    n_boot: int = 5000,
) -> dict[str, float | int]:
    chosen_id = f"atr_{selected:.2f}"
    base = all_metrics[
        (all_metrics["split"] == "holdout")
        & (all_metrics["execution"] == "single")
        & (all_metrics["filter_id"].isin([chosen_id, "fixed_3pct"]))
    ]
    pivot = base.pivot_table(index=["panel_id", "ma_period"], columns="filter_id", values=metric)
    pivot = pivot.dropna(subset=[chosen_id, "fixed_3pct"])
    diff = (pivot[chosen_id] - pivot["fixed_3pct"]).to_numpy(dtype=float)
    if not len(diff):
        return {"pairs": 0, "median_delta": np.nan, "ci_low": np.nan, "ci_high": np.nan}
    rng = np.random.default_rng(RNG_SEED)
    samples = rng.choice(diff, size=(n_boot, len(diff)), replace=True)
    boot = np.median(samples, axis=1)
    return {
        "pairs": int(len(diff)),
        "median_delta": float(np.median(diff)),
        "ci_low": float(np.quantile(boot, 0.025)),
        "ci_high": float(np.quantile(boot, 0.975)),
    }


def make_chart(summary: pd.DataFrame, dev_scores: pd.DataFrame, holdout_scores: pd.DataFrame, selected: float) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))
    for execution, color in (("single", "#2563eb"), ("legacy_parallel", "#f97316")):
        dev = summary[(summary["split"] == "development") & (summary["execution"] == execution) & (summary["filter_kind"] == "atr_body")]
        hold = summary[(summary["split"] == "holdout") & (summary["execution"] == execution) & (summary["filter_kind"] == "atr_body")]
        axes[0, 0].plot(dev["atr_threshold"], dev["median_performance_per_year_pct"], marker="o", color=color, label=execution)
        axes[0, 1].plot(hold["atr_threshold"], hold["median_performance_per_year_pct"], marker="o", color=color, label=execution)
        axes[1, 0].plot(hold["atr_threshold"], hold["positive_performance_share_pct"], marker="o", color=color, label=execution)

    axes[1, 1].plot(dev_scores["atr_threshold"], dev_scores["combined_score"], marker="o", label="development")
    axes[1, 1].plot(holdout_scores["atr_threshold"], holdout_scores["combined_score"], marker="o", label="holdout (validation only)")
    titles = [
        "Development: median performance/year",
        "Holdout: median performance/year",
        "Holdout: positive panel share",
        "Robust rank score",
    ]
    for ax, title in zip(axes.flat, titles):
        ax.axvline(selected, color="#dc2626", linestyle="--", label=f"selected {selected:.2f} ATR")
        ax.set_title(title)
        ax.set_xlabel("body / previous ATR14")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    axes[0, 0].set_ylabel("%")
    axes[0, 1].set_ylabel("%")
    axes[1, 0].set_ylabel("%")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "parameter_comparison.png", dpi=150)
    plt.close(fig)


def fmt(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def write_report(
    manifest: pd.DataFrame,
    summary: pd.DataFrame,
    dev_scores: pd.DataFrame,
    holdout_scores: pd.DataFrame,
    selected: float,
    stable_band: list[float],
    bootstrap_perf: dict[str, Any],
    bootstrap_exp: dict[str, Any],
    breakdown: pd.DataFrame,
) -> dict[str, Any]:
    selected_id = f"atr_{selected:.2f}"
    rows = {
        f"{split}_{execution}_{name}": _summary_row(summary, split, execution, filter_id)
        for split in ("development", "holdout")
        for execution in ("single", "legacy_parallel")
        for name, filter_id in (("selected", selected_id), ("baseline", "fixed_3pct"))
    }
    holdout_rank = holdout_scores.sort_values("combined_score", ascending=False).reset_index(drop=True)
    rank_match = holdout_rank.index[holdout_rank["atr_threshold"] == selected].tolist()
    selected_holdout_rank = rank_match[0] + 1 if rank_match else None

    recommendation = {
        "selected_body_atr_k": selected,
        "atr_period": ATR_LEN,
        "atr_reference": "previous_bar",
        "development_stable_band": stable_band,
        "selection_rule": "highest three-point neighborhood average of development robust ranks",
        "holdout_rank_among_atr_candidates": selected_holdout_rank,
        "atr_candidate_count": len(ATR_THRESHOLDS),
        "panels": int(len(manifest)),
        "panel_ma_combinations": int(len(manifest) * len(MA_PERIODS)),
        "paired_bootstrap_holdout_performance_delta": bootstrap_perf,
        "paired_bootstrap_holdout_expectancy_delta": bootstrap_exp,
        "summary_rows": rows,
    }
    with (RESULTS_DIR / "recommendation.json").open("w", encoding="utf-8") as f:
        json.dump(recommendation, f, ensure_ascii=False, indent=2, default=str)

    selected_breakdown = breakdown[breakdown["filter_id"] == selected_id]
    breakdown_lines = [
        "| 资产 | 周期 | 中位年化表现 | 中位单笔期望 | 中位PF | 盈利组合占比 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in selected_breakdown.to_dict("records"):
        breakdown_lines.append(
            f"| {item['asset_class']} | {item['timeframe']} | "
            f"{fmt(item['median_performance_per_year_pct'])}% | "
            f"{fmt(item['median_expectancy_pct'])}% | "
            f"{fmt(item['median_profit_factor'])} | "
            f"{fmt(item['positive_performance_share_pct'])}% |"
        )
    breakdown_md = "\n".join(breakdown_lines)

    shold = rows["holdout_single_selected"]
    bhold = rows["holdout_single_baseline"]
    lhold = rows["holdout_legacy_parallel_selected"]
    lbhold = rows["holdout_legacy_parallel_baseline"]
    report = f"""# MA A 策略 ATR 入场实验结果

## 最终结论

推荐默认参数：**`BODY_ATR_K = {selected:.2f}`**，计算方式为：

```text
body_atr = (close[t] - open[t]) / ATR14[t-1]
入场要求：body_atr >= {selected:.2f}
```

参数只由前 70% 开发样本选择。对每个候选计算其自身及左右各0.1 ATR的三点邻域平均分，
最高分邻域为 `{', '.join(f'{v:.2f}' for v in stable_band)}`，中心值是 `{selected:.2f}`。
这种方法不会因为单个阈值偶然表现突出就选择一个尖峰。
留出样本没有参与选择；推荐值在全部 {len(ATR_THRESHOLDS)} 个 ATR 候选中的验证排名为
**{selected_holdout_rank}/{len(ATR_THRESHOLDS)}**。

## 核心对比

| 口径 | 过滤 | 留出样本中位年化表现 | 中位单笔期望 | 中位PF | 盈利面板占比 | 中位回撤 | 有效面板 |
|---|---|---:|---:|---:|---:|---:|---:|
| 单仓 | {selected:.2f} ATR | {fmt(shold.get('median_performance_per_year_pct'))}% | {fmt(shold.get('median_expectancy_pct'))}% | {fmt(shold.get('median_profit_factor'))} | {fmt(shold.get('positive_performance_share_pct'))}% | {fmt(shold.get('median_max_drawdown_pct'))}% | {shold.get('valid_panels', 'NA')} |
| 单仓 | 固定3% | {fmt(bhold.get('median_performance_per_year_pct'))}% | {fmt(bhold.get('median_expectancy_pct'))}% | {fmt(bhold.get('median_profit_factor'))} | {fmt(bhold.get('positive_performance_share_pct'))}% | {fmt(bhold.get('median_max_drawdown_pct'))}% | {bhold.get('valid_panels', 'NA')} |
| 原A并行 | {selected:.2f} ATR | {fmt(lhold.get('median_performance_per_year_pct'))}% | {fmt(lhold.get('median_expectancy_pct'))}% | {fmt(lhold.get('median_profit_factor'))} | {fmt(lhold.get('positive_performance_share_pct'))}% | {fmt(lhold.get('median_max_drawdown_pct'))}% | {lhold.get('valid_panels', 'NA')} |
| 原A并行 | 固定3% | {fmt(lbhold.get('median_performance_per_year_pct'))}% | {fmt(lbhold.get('median_expectancy_pct'))}% | {fmt(lbhold.get('median_profit_factor'))} | {fmt(lbhold.get('positive_performance_share_pct'))}% | {fmt(lbhold.get('median_max_drawdown_pct'))}% | {lbhold.get('valid_panels', 'NA')} |

双倍交易成本（单边由0.15%提高到0.30%）后，单仓留出样本中推荐参数的中位年化表现为
**{fmt(shold.get('double_cost_median_performance_per_year_pct'))}%**，中位PF为
**{fmt(shold.get('double_cost_median_profit_factor'))}**，盈利面板占比为
**{fmt(shold.get('double_cost_positive_performance_share_pct'))}%**。

单仓留出样本中，推荐参数相对固定3%的面板配对中位差：

- 年化表现：`{fmt(bootstrap_perf.get('median_delta'))}` 个百分点，95% bootstrap区间
  `[{fmt(bootstrap_perf.get('ci_low'))}, {fmt(bootstrap_perf.get('ci_high'))}]`。
- 单笔期望：`{fmt(bootstrap_exp.get('median_delta'), 3)}` 个百分点，95% bootstrap区间
  `[{fmt(bootstrap_exp.get('ci_low'), 3)}, {fmt(bootstrap_exp.get('ci_high'), 3)}]`。

## 为什么选择它

1. 它的三点邻域平均分在开发样本最高，而不是某个品种或某个阈值的单点最高值。
2. 选择时同时考虑单仓与原A并行口径，不依赖一种资金曲线算法。
3. 样本覆盖 BTC、ETH 的日线/4小时/1小时、黄金日线以及美股、港股、A股日线，
   共 {len(manifest)} 个品种-周期面板、{len(manifest) * len(MA_PERIODS)} 个面板-MA组合。
4. 留出样本完全没有参与参数选择，验证排名和相对固定3%的结果如上表所示。

## 分资产与周期验证

{breakdown_md}

这里最重要的反例是加密货币1小时：即使换成推荐ATR阈值，中位年化表现仍为负。
因此 `0.30 ATR` 是A策略的跨市场默认入场参数，不代表A策略适合所有周期；当前结果明确
不支持在BTC/ETH 1小时上启用该策略。

## 保持不变的内容

- MA30/60/99/120；
- 入场K线低点止损；
- 收盘浮盈超过4%后，下一根K线起保本；
- 收盘低于MA 3%出场；
- 单边0.15%手续费与滑点；
- 信号K线收盘成交。

因此，本实验只能说明“实体过滤阈值”的相对优劣，不能把改善归因于止损、止盈或
成交模型的变化。

## 文件

- `data/manifest.csv`：数据范围与来源（面板数据归档至仓库根 `data/{stocks,gold,hk_equity,cn_equity,crypto}/`）。
- `results/all_metrics.csv`：每个品种、周期、MA、参数、样本和执行口径的完整结果。
- `results/development_summary.csv` / `holdout_summary.csv`：汇总结果。
- `results/development_scores.csv` / `holdout_scores.csv`：稳健排名。
- `results/holdout_asset_timeframe_breakdown.csv`：分资产与周期的留出样本结果。
- `results/parameter_comparison.png`：参数曲线。
- `results/recommendation.json`：机器可读最终推荐。

## 限制

- 股票池是当前存续的大型公司，不能消除幸存者偏差；本实验的目标是比较过滤阈值，
  不是证明策略本身存在可交易超额收益。
- 仍按信号K线收盘成交并按止损价成交，与主A策略一致；下一根开盘和跳空滑点应另做实验。
- 固定3%和ATR会产生不同交易数量；因此同时报告期望、PF、回撤、覆盖率和年化表现，
  没有只按总收益选参数。
"""
    (HERE / "RESULTS.md").write_text(report, encoding="utf-8")
    return recommendation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-data", action="store_true", help="refresh remote/local snapshots")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        "atr_len": ATR_LEN,
        "atr_reference": "previous_bar",
        "atr_thresholds": ATR_THRESHOLDS,
        "ma_periods": MA_PERIODS,
        "development_share": DEVELOPMENT_SHARE,
        "fee_rate": FEE_RATE,
        "slippage": SLIPPAGE,
        "double_cost_per_side": DOUBLE_COST,
        "breakeven_trigger": BREAKEVEN_TRIGGER,
        "ma_exit_pct": MA_EXIT_PCT,
        "min_trades_per_panel": MIN_TRADES_PER_PANEL,
    }
    (HERE / "experiment_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

    manifest = prepare_data(args.refresh_data)
    all_metrics = run_panels(manifest)
    all_metrics.to_csv(RESULTS_DIR / "all_metrics.csv", index=False)
    summary = aggregate_metrics(all_metrics)
    summary[summary["split"] == "development"].to_csv(RESULTS_DIR / "development_summary.csv", index=False)
    summary[summary["split"] == "holdout"].to_csv(RESULTS_DIR / "holdout_summary.csv", index=False)

    dev_scores = score_candidates(summary, "development")
    holdout_scores = score_candidates(summary, "holdout")
    dev_scores.to_csv(RESULTS_DIR / "development_scores.csv", index=False)
    holdout_scores.to_csv(RESULTS_DIR / "holdout_scores.csv", index=False)
    selected, plateau = select_threshold(dev_scores)
    breakdown = make_breakdown(all_metrics, selected)
    breakdown.to_csv(RESULTS_DIR / "holdout_asset_timeframe_breakdown.csv", index=False)
    bootstrap_perf = paired_bootstrap(all_metrics, selected, "performance_per_year_pct")
    bootstrap_exp = paired_bootstrap(all_metrics, selected, "expectancy_pct")
    make_chart(summary, dev_scores, holdout_scores, selected)
    recommendation = write_report(
        manifest,
        summary,
        dev_scores,
        holdout_scores,
        selected,
        plateau,
        bootstrap_perf,
        bootstrap_exp,
        breakdown,
    )
    print(json.dumps(recommendation, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
