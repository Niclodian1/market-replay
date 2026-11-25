#!/usr/bin/env python3
"""构建复盘/实盘「最小充分信息集」静态上下文数据 apps/chanlun-web/data-view/context_data.js。

包含 3 块客观、无新闻噪音的历史回放上下文：
  1. macro      : US10Y (^TNX) / DXY (DX-Y.NYB) / VIX (^VIX) 日频历史（1980年起）
  2. benchmarks : NDX / SPX / BTC / 399006_SZ / 0700_HK 收盘序列（用于计算任意历史日相对强弱 RS）
  3. earnings   : 美股/港股历史与即将到来的季报日期 + EPS Surprise(%)

用法:
  python3 scripts/ops/build_context_data.py
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
OUT_JS = REPO / "apps" / "chanlun-web" / "data-view" / "context_data.js"
CACHE_DIR = DATA / "context"

MACRO_TICKERS = {
    "us10y": "^TNX",
    "dxy": "DX-Y.NYB",
    "vix": "^VIX",
}

BENCHMARK_FILES = {
    "NDX": DATA / "indices" / "NDX_1day.parquet",
    "SPX": DATA / "indices" / "SPX_1day.parquet",
    "BTC": DATA / "crypto" / "BTC_USD_1day.parquet",
    "CN": DATA / "indices" / "399006_SZ_1day.parquet",
    "HK": DATA / "indices" / "HSI_1day.parquet",
}

EARNINGS_TICKERS = {
    "NVDA": "NVDA", "AAPL": "AAPL", "MSFT": "MSFT", "GOOGL": "GOOGL",
    "META": "META", "TSLA": "TSLA", "AMZN": "AMZN", "AVGO": "AVGO",
    "AMD": "AMD", "MU": "MU", "INTC": "INTC", "SNDK": "SNDK",
    "PLTR": "PLTR", "COIN": "COIN", "NFLX": "NFLX", "WMT": "WMT",
    "JPM": "JPM", "BRK_B": "BRK-B",
    "TSM": "TSM", "LLY": "LLY", "XOM": "XOM", "FCX": "FCX",
    "0700_HK": "0700.HK", "9988_HK": "9988.HK",
}


def _round_or_none(v, digits: int = 2):
    if v is None:
        return None
    try:
        f = float(v)
        if not math.isfinite(f):
            return None
        return round(f, digits)
    except Exception:
        return None


def fetch_macro() -> dict:
    series_map: dict[str, pd.Series] = {}
    for key, ticker in MACRO_TICKERS.items():
        raw = yf.download(ticker, start="1980-01-01", progress=False, threads=False, auto_adjust=False)
        if raw.empty:
            print(f"[macro] ⚠ {key} ({ticker}) 下载为空")
            series_map[key] = pd.Series(dtype=float)
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        s = raw["Close"].dropna().copy()
        s.index = pd.to_datetime(s.index).strftime("%Y-%m-%d")
        s = s[~s.index.duplicated(keep="last")].sort_index()
        series_map[key] = s
        print(f"[macro] {key:<6} ({ticker:<8}) {len(s):>5} 天 ({s.index[0]}..{s.index[-1]}) 最新={s.iloc[-1]:.2f}")

    all_dates = sorted(set().union(*(s.index.tolist() for s in series_map.values())))
    # 前向填充(最多 5 个交易日)以对齐跨市场休市差异，保持历史因果不泄露未来
    df = pd.DataFrame({k: series_map[k].reindex(all_dates) for k in MACRO_TICKERS})
    df = df.ffill(limit=5)
    return {
        "dates": all_dates,
        "us10y": [_round_or_none(v, 2) for v in df["us10y"].tolist()],
        "dxy": [_round_or_none(v, 2) for v in df["dxy"].tolist()],
        "vix": [_round_or_none(v, 2) for v in df["vix"].tolist()],
    }


def build_benchmarks() -> dict:
    out = {}
    cutoff_1985 = int(pd.Timestamp("1985-01-01", tz="UTC").timestamp())
    for key, path in BENCHMARK_FILES.items():
        if not path.exists():
            continue
        df = pd.read_parquet(path).sort_values("time").drop_duplicates("time")
        df = df[df["time"] >= cutoff_1985]
        dates = pd.to_datetime(df["time"], unit="s", utc=True).dt.strftime("%Y-%m-%d").tolist()
        closes = [_round_or_none(v, 2) for v in df["close"].tolist()]
        out[key] = {"d": dates, "c": closes}
        print(f"[bench] {key:<6} {len(dates):>5} 天 ({dates[0]}..{dates[-1]})")
    return out


def _fetch_one_earnings(sym: str, yf_sym: str) -> tuple[str, list]:
    for attempt in range(2):
        try:
            ed = yf.Ticker(yf_sym).get_earnings_dates(limit=60)
            if ed is None or ed.empty:
                return sym, []
            rows = []
            seen: set[str] = set()
            for idx, row in ed.iterrows():
                ts_idx = pd.Timestamp(idx)
                d_str = ts_idx.strftime("%Y-%m-%d")
                if d_str in seen:
                    continue
                seen.add(d_str)
                surp = _round_or_none(row.get("Surprise(%)"), 1)
                rep = _round_or_none(row.get("Reported EPS"), 2)
                est = _round_or_none(row.get("EPS Estimate"), 2)
                timing = "AMC"
                try:
                    ny = ts_idx.tz_convert("America/New_York") if ts_idx.tzinfo else ts_idx
                    timing = "AMC" if ny.hour >= 16 else "BMO"
                except Exception:
                    timing = "AMC"
                # [date, surprisePct, reportedEps, estEps, timing('BMO'|'AMC')]
                rows.append([d_str, surp, rep, est, timing])
            rows.sort(key=lambda x: x[0])
            return sym, rows
        except Exception:
            if attempt == 0:
                time.sleep(1.0)
    return sym, []


def fetch_earnings() -> dict:
    out: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(_fetch_one_earnings, k, v): k for k, v in EARNINGS_TICKERS.items()}
        for f in as_completed(futs):
            sym, rows = f.result()
            if rows:
                out[sym] = rows
    print(f"[earn]  成功拉取 {len(out)}/{len(EARNINGS_TICKERS)} 个股票的历史财报日（共 {sum(len(v) for v in out.values())} 条）")
    return dict(sorted(out.items()))


def main() -> None:
    t0 = time.time()
    macro = fetch_macro()
    benchmarks = build_benchmarks()
    earnings = fetch_earnings()

    payload = {
        "meta": {
            "builtAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "macroRange": [macro["dates"][0], macro["dates"][-1]] if macro["dates"] else [],
            "earningsSymbols": len(earnings),
        },
        "macro": macro,
        "benchmarks": benchmarks,
        "earnings": earnings,
    }

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "context_cache.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_JS.with_suffix(".js.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("// Minimal Sufficient Context: US10Y/DXY/VIX + Benchmarks(RS) + Earnings Dates\n")
        f.write("window.MARKET_CONTEXT = ")
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    tmp.replace(OUT_JS)
    kb = OUT_JS.stat().st_size / 1024
    print(f"\n✓ 已生成 {OUT_JS.relative_to(REPO)} ({kb:.1f} KB, 耗时 {time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
