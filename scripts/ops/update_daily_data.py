#!/usr/bin/env python3
"""增量补齐 data/ 目录下的 1D 日线数据（本地 parquet 数据仓库）。

覆盖范围:
  - 美股: AAPL MSFT NVDA GOOGL META TSLA AMZN BRK-B JPM
          + PLTR COIN AVGO MU WMT AMD NFLX          (Yahoo Finance, auto_adjust)
  - A股:  002460.SZ 300750.SZ 600900.SS 000333.SZ 603259.SS 300033.SZ (同上)
  - 贵金属: XAUUSD + XAGUSD白银 (Dukascopy → 刷新 data/market/<SYM>_1d.parquet 缓存,
          并同步到 data/gold/<SYM>_1day.parquet)
  - 加密新品种(仅 --init-new): SOL PEPE (Binance现货) / HYPE (Hyperliquid原生)
          三级别 1D+4H+1H → data/crypto/<SYM>_USD_*.parquet

用法:
  python3 scripts/ops/update_daily_data.py            # 增量补齐到最近完整交易日
  python3 scripts/ops/update_daily_data.py --init-new # 初始化缺失的新品种全量数据
  python3 scripts/ops/update_daily_data.py --manifest # 仅重建 manifest.csv
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO / "data"
MANIFEST_PATH = REPO / "experiments" / "ma_a_atr_entry" / "manifest.csv"
DAY_SECONDS = 24 * 3600
TF_SECONDS = {"1day": DAY_SECONDS, "4h": 4 * 3600, "1h": 3600}

# panel_id → (yahoo_symbol, 显示名, 子目录)
STOCK_PANELS = {
    "AAPL_1day": ("AAPL", "Apple", "stocks"),
    "MSFT_1day": ("MSFT", "Microsoft", "stocks"),
    "NVDA_1day": ("NVDA", "NVIDIA", "stocks"),
    "GOOGL_1day": ("GOOGL", "Alphabet", "stocks"),
    "META_1day": ("META", "Meta", "stocks"),
    "TSLA_1day": ("TSLA", "Tesla", "stocks"),
    "AMZN_1day": ("AMZN", "Amazon", "stocks"),
    "BRK_B_1day": ("BRK-B", "Berkshire", "stocks"),
    "JPM_1day": ("JPM", "JPMorgan", "stocks"),
    # ---- 2026-08 新增 ----
    "PLTR_1day": ("PLTR", "Palantir", "stocks"),
    "COIN_1day": ("COIN", "Coinbase", "stocks"),
    "AVGO_1day": ("AVGO", "Broadcom", "stocks"),
    "MU_1day": ("MU", "Micron", "stocks"),
    "WMT_1day": ("WMT", "Walmart", "stocks"),
    "AMD_1day": ("AMD", "AMD", "stocks"),
    "NFLX_1day": ("NFLX", "Netflix", "stocks"),
}
# A股: panel_id → (yahoo_symbol, 显示名); 落盘 cn_equity/, 与 600519_SS 同源同口径
CN_PANELS = {
    "002460_SZ_1day": ("002460.SZ", "赣锋锂业"),
    "300750_SZ_1day": ("300750.SZ", "宁德时代"),
    "600900_SS_1day": ("600900.SS", "长江电力"),
    "000333_SZ_1day": ("000333.SZ", "美的集团"),
    "603259_SS_1day": ("603259.SS", "药明康德"),
    "300033_SZ_1day": ("300033.SZ", "同花顺"),
}
# 加密三级别: panel前缀 → (canonical_symbol, 来源); 1D+4H+1H 对齐 BTC/ETH 口径
CRYPTO_INIT = {
    "SOL_USD": ("SOL-USD", "binance"),
    "PEPE_USD": ("PEPE-USD", "binance"),
    "HYPE_USD": ("HL:HYPE", "hyperliquid"),   # Hyperliquid 原生所, TGE 2024-11-29 起
}
# 贵金属: panel_id → (dukascopy pair, product符号); parquet 落盘 data/gold/(贵金属目录)
METAL_PANELS = {
    "XAUUSD_1day": ("XAU-USD", "XAUUSD"),
    "XAGUSD_1day": ("XAG-USD", "XAGUSD"),
}


def _drop_incomplete_candle(df: pd.DataFrame, bar_seconds: int = DAY_SECONDS) -> pd.DataFrame:
    """过滤未走完的 bar(收盘时间必须已过); bar_seconds 按周期取值。"""
    now_ts = int(pd.Timestamp.now(tz="UTC").timestamp())
    return df[pd.to_numeric(df["time"], errors="coerce") + bar_seconds <= now_ts].copy()


def _fetch_yahoo_increment(symbol: str, start: pd.Timestamp) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(symbol, start=start.strftime("%Y-%m-%d"), auto_adjust=True,
                      progress=False, threads=False)
    if raw.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    return _yahoo_frame(raw)


def _yahoo_frame(raw) -> pd.DataFrame:
    """yfinance DataFrame → 标准六列(time秒/open/high/low/close/volume)。"""
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


def init_stock_full(panel_id: str, symbol: str, subdir: str) -> None:
    """新品种首次全量下载(period=max, 与 run_experiment.py 同口径)。"""
    import yfinance as yf

    raw = yf.download(symbol, period="max", auto_adjust=True, progress=False, threads=False)
    if raw.empty:
        raise RuntimeError(f"{symbol} Yahoo 全量下载为空")
    frame = _drop_incomplete_candle(_yahoo_frame(raw))
    out = DATA_ROOT / subdir / f"{panel_id}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out, index=False)
    last = pd.to_datetime(int(frame["time"].iloc[-1]), unit="s", utc=True).date()
    print(f"[init]   {symbol:<10} 全量 {len(frame):>6} 根 → {subdir}/{panel_id} (截止 {last})")


def update_stock(panel_id: str, symbol: str, overlap_days: int = 7, subdir: str = "stocks") -> None:
    path = DATA_ROOT / subdir / f"{panel_id}.parquet"
    if not path.exists():
        init_stock_full(panel_id, symbol, subdir)   # 缺失即自动全量初始化
        return
    old = pd.read_parquet(path)
    last_ts = int(old["time"].iloc[-1])
    start = pd.Timestamp(last_ts, unit="s", tz="UTC") - pd.Timedelta(days=overlap_days)
    fresh = _drop_incomplete_candle(_fetch_yahoo_increment(symbol, start))
    merged = (
        pd.concat([old, fresh], ignore_index=True)
        .sort_values("time")
        .drop_duplicates("time", keep="last")
        .reset_index(drop=True)
    )
    added = len(merged) - len(old)
    merged.to_parquet(path, index=False)
    new_last = pd.to_datetime(int(merged["time"].iloc[-1]), unit="s", utc=True).date()
    print(f"[stocks] {symbol:<6} +{added:>4} bars → 截止 {new_last}")


# ---- 加密新品种初始化: Binance 现货 / Hyperliquid → 标准六列 parquet ----
def _candles_frame(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    cols = ["time", "open", "high", "low", "close", "volume"]
    df = df[cols].astype({"time": "int64"})
    return df.sort_values("time").drop_duplicates("time").reset_index(drop=True)


async def _fetch_crypto_full(canonical: str, source: str, interval: str) -> list[dict]:
    """全史K线。Binance 内部自动翻页; Hyperliquid 单次≤5000根, 这里手动向前翻页。"""
    sys.path.insert(0, str(REPO / "backend"))
    if source == "binance":
        from app.providers.binance import BinanceSpotProvider

        return await BinanceSpotProvider().get_time_series(canonical, interval, outputsize=10**7)
    if source == "hyperliquid":
        from app.providers.hyperliquid import get_hyperliquid_provider

        prov = get_hyperliquid_provider()
        out: list[dict] = []
        seen: set[int] = set()
        before = None
        while True:
            try:
                batch = await prov.get_time_series(canonical, interval, outputsize=5000, before=before)
            except Exception:
                if out:
                    break          # 已拿到部分数据, 翻页尽头/网络尾错可接受
                raise
            fresh = [c for c in batch if c["time"] not in seen]
            out.extend(fresh)
            seen.update(c["time"] for c in fresh)
            if len(batch) < 5000 or not fresh:
                break
            before = min(c["time"] for c in batch)
        return sorted(out, key=lambda c: c["time"])
    raise ValueError(f"未知加密来源: {source}")


def init_crypto(prefix: str, canonical: str, source: str) -> None:
    async def _fetch_all():
        # 三个级别共用同一事件循环: provider 的 httpx 连接绑定 loop,
        # 跨 asyncio.run 复用单例会报 "Event loop is closed"
        return {tf: await _fetch_crypto_full(canonical, source, tf) for tf in TF_SECONDS}

    frames = asyncio.run(_fetch_all())
    for tf, candles in frames.items():
        frame = _drop_incomplete_candle(_candles_frame(candles), TF_SECONDS[tf])
        path = DATA_ROOT / "crypto" / f"{prefix}_{tf}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        last = pd.to_datetime(int(frame["time"].iloc[-1]), unit="s", utc=True)
        print(f"[crypto] {prefix:<8} {tf:<5} {len(frame):>6} 根 → 截止 {last.date()} {last.hour:02d}:00")


def init_new_panels() -> None:
    """下载全部缺失的新品种面板(美股/A股 yfinance 全量 + 加密三级别)。"""
    missing_stocks = [(pid, sym, sub) for pid, (sym, _n, sub) in STOCK_PANELS.items()
                      if not (DATA_ROOT / sub / f"{pid}.parquet").exists()]
    for pid, sym, sub in missing_stocks:
        init_stock_full(pid, sym, sub)
    missing_cn = [(pid, sym) for pid, (sym, _n) in CN_PANELS.items()
                  if not (DATA_ROOT / "cn_equity" / f"{pid}.parquet").exists()]
    for pid, sym in missing_cn:
        init_stock_full(pid, sym, "cn_equity")
    for prefix, (canonical, source) in CRYPTO_INIT.items():
        if any(not (DATA_ROOT / "crypto" / f"{prefix}_{tf}.parquet").exists() for tf in TF_SECONDS):
            init_crypto(prefix, canonical, source)
    print(f"[init-new] 完成: 新增美股 {len(missing_stocks)} / A股 {len(missing_cn)} / 加密 "
          f"{sum(1 for p in CRYPTO_INIT if (DATA_ROOT / 'crypto' / f'{p}_1day.parquet').exists())} 个品种")


def update_metal(panel_id: str) -> None:
    """贵金属日线增量(Dukascopy): provider 内部维护 market/ 缓存, 这里同步面板文件。"""
    sys.path.insert(0, str(REPO / "backend"))
    from app.providers.dukascopy import DukascopyMetalProvider

    pair, product = METAL_PANELS[panel_id]
    candles = asyncio.run(DukascopyMetalProvider(pair, product).get_full_history(product, "1day"))

    frame = pd.DataFrame(candles)[["time", "open", "high", "low", "close", "volume"]]
    complete = _drop_incomplete_candle(frame)
    metal_path = DATA_ROOT / "gold" / f"{panel_id}.parquet"
    old = pd.read_parquet(metal_path) if metal_path.exists() else complete.iloc[0:0]
    complete.to_parquet(metal_path, index=False)
    added = len(complete) - len(old)
    last = pd.to_datetime(int(complete["time"].iloc[-1]), unit="s", utc=True).date()
    print(f"[metal]  {product:<6} +{added:>4} bars → 截止 {last} (缓存 {product}_1d.parquet: {len(frame)} 行)")


def rebuild_manifest() -> None:
    rows = []
    for panel_id, (symbol, name, subdir) in STOCK_PANELS.items():
        rows.append(_row(panel_id, symbol, name,
                         "1day", "US equity", "Yahoo Finance adjusted",
                         DATA_ROOT / subdir / f"{panel_id}.parquet"))
    for panel_id, (symbol, name) in CN_PANELS.items():
        rows.append(_row(panel_id, symbol, name,
                         "1day", "CN equity", "Yahoo Finance adjusted",
                         DATA_ROOT / "cn_equity" / f"{panel_id}.parquet"))
    for panel_id in METAL_PANELS:
        pair, product = METAL_PANELS[panel_id]
        rows.append(_row(panel_id, product, product, "1day", "gold", "Dukascopy snapshot",
                         DATA_ROOT / "gold" / f"{panel_id}.parquet"))
    crypto_sources = {"BTC-USD": None, "ETH-USD": None}
    for prefix, (canonical, source) in CRYPTO_INIT.items():
        crypto_sources[canonical] = source
        source_label = {"binance": "Binance spot full history",
                        "hyperliquid": "Hyperliquid native candles"}[source]
        for timeframe in TF_SECONDS:
            rows.append(_row(f"{prefix}_{timeframe}", canonical, canonical, timeframe, "crypto",
                             source_label,
                             DATA_ROOT / "crypto" / f"{prefix}_{timeframe}.parquet"))
    for symbol in ("BTC-USD", "ETH-USD"):
        safe = symbol.replace("-", "_")
        for timeframe, tfs in (("1day", "1day"), ("4h", "4h"), ("1h", "1h")):
            rows.append(_row(f"{safe}_{timeframe}", symbol, symbol, timeframe, "crypto",
                             "local parquet + OKX increment",
                             DATA_ROOT / "crypto" / f"{safe}_{tfs}.parquet"))
    manifest = pd.DataFrame([r for r in rows if r]).sort_values(["asset_class", "symbol", "timeframe"])
    manifest.to_csv(MANIFEST_PATH, index=False)
    print(f"[manifest] {MANIFEST_PATH} ({len(manifest)} panels)")


def _row(panel_id, symbol, name, timeframe, asset_class, source, path: Path) -> dict | None:
    if not path.exists():
        return None   # 面板尚未初始化(如未跑 --init-new), 跳过不报错
    frame = pd.read_parquet(path)
    return {
        "panel_id": panel_id,
        "symbol": symbol,
        "name": name,
        "timeframe": timeframe,
        "asset_class": asset_class,
        "source": source,
        "bars": len(frame),
        "start": pd.to_datetime(int(frame["time"].iloc[0]), unit="s", utc=True).isoformat(),
        "end": pd.to_datetime(int(frame["time"].iloc[-1]), unit="s", utc=True).isoformat(),
        "snapshot": str(path.relative_to(REPO)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="store_true", help="仅重建 manifest.csv")
    parser.add_argument("--init-new", action="store_true",
                        help="下载缺失的新品种全量数据(美股/A股 yfinance + 加密三级别), 不做增量")
    args = parser.parse_args()

    if args.manifest:
        rebuild_manifest()
        return

    if args.init_new:
        init_new_panels()
        rebuild_manifest()
        return

    for panel_id, (symbol, _name, subdir) in STOCK_PANELS.items():
        update_stock(panel_id, symbol, subdir=subdir)
    for panel_id, (symbol, _name) in CN_PANELS.items():
        update_stock(panel_id, symbol, subdir="cn_equity")
    for panel_id in METAL_PANELS:
        update_metal(panel_id)
    rebuild_manifest()


if __name__ == "__main__":
    main()
