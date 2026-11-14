"""本地行情读取：所有离线任务（回填、审计、实验）共用同一份 parquet 解析。

与 ``providers.local_crypto`` 的区别：那个面向「实时增量 + 缓存」，
这里只做「一次性读全量」，支持多种 parquet 列名（``time`` / ``open_time`` / DatetimeIndex）。
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]

DATA_DIRS: tuple[str, ...] = (
    "crypto",
    "stocks",
    "indices",
    "cn_equity",
    "hk_equity",
    "gold",
    "forex",
)

_TF_SUFFIX = {
    "15min": ("15min", "15m"),
    "1h": ("1h",),
    "4h": ("4h",),
    "1day": ("1day", "1d"),
    "1week": ("1week", "1w"),
}

_LOCAL_STEM_ALIASES: dict[str, tuple[str, ...]] = {
    "^NDX": ("NDX",),
    "NDX": ("NDX",),
    "^IXIC": ("NDX",),
    "IXIC": ("NDX",),
    "纳指": ("NDX",),
    "纳斯达克": ("NDX",),
    "纳斯达克100": ("NDX",),
    "^GSPC": ("SPX",),
    "SPX": ("SPX",),
    "GSPC": ("SPX",),
    "标普500": ("SPX",),
    "^N225": ("N225",),
    "N225": ("N225",),
    "GC=F": ("XAUUSD",),
    "XAUUSD": ("XAUUSD",),
    "XAU_USD": ("XAUUSD",),
    "SI=F": ("XAGUSD",),
    "XAGUSD": ("XAGUSD",),
    "XAG_USD": ("XAGUSD",),
    "BRK-B": ("BRK_B",),
    "BRK.B": ("BRK_B",),
    "BRK_B": ("BRK_B",),
    "BTC-USD": ("BTCUSDT", "BTC_USD", "BTC"),
    "BTC_USD": ("BTCUSDT", "BTC_USD", "BTC"),
    "BTC": ("BTCUSDT", "BTC_USD", "BTC"),
    "ETH-USD": ("ETHUSDT", "ETH_USD", "ETH"),
    "ETH_USD": ("ETHUSDT", "ETH_USD", "ETH"),
    "ETH": ("ETHUSDT", "ETH_USD", "ETH"),
    "SOL-USD": ("SOLUSDT", "SOL"),
    "SOL": ("SOLUSDT", "SOL"),
}


def _candidate_stems(symbol: str) -> list[str]:
    raw = symbol.strip().upper()
    cleaned = raw.lstrip("^").replace("/", "_").replace("-", "_").replace(".", "_")
    stems: list[str] = []
    for alias in _LOCAL_STEM_ALIASES.get(raw, ()) + _LOCAL_STEM_ALIASES.get(cleaned, ()):
        if alias not in stems:
            stems.append(alias)
    for s in (cleaned, raw.replace("-", "_").replace(".", "_")):
        if s and s not in stems:
            stems.append(s)
    return stems


def resolve_parquet(symbol: str, timeframe: str) -> Path | None:
    suffixes = _TF_SUFFIX.get(timeframe, (timeframe,))
    for stem in _candidate_stems(symbol):
        for directory in DATA_DIRS:
            for suffix in suffixes:
                candidate = REPO_ROOT / "data" / directory / f"{stem}_{suffix}.parquet"
                if candidate.exists():
                    return candidate
    return None


@lru_cache(maxsize=16)
def read_local_bars(symbol: str, timeframe: str) -> tuple[dict[str, Any], ...]:
    """读取全量 K 线（升序，标准字段）。找不到数据返回空元组。"""
    path = resolve_parquet(symbol, timeframe)
    if path is None:
        return ()
    frame = pd.read_parquet(path)
    if frame.empty:
        return ()
    frame = frame.reset_index()
    if "time" in frame.columns:
        times = frame["time"]
    elif "open_time" in frame.columns:
        times = frame["open_time"]
    elif "date" in frame.columns:
        times = frame["date"]
    else:
        raise ValueError(f"{path.name} 缺少时间列（time/open_time/date）")
    if pd.api.types.is_datetime64_any_dtype(times):
        seconds = times.astype("int64") // 10**9
    else:
        numeric = pd.to_numeric(times, errors="coerce")
        # 兼容毫秒时间戳
        seconds = (numeric // 1000).where(numeric > 10**11, numeric)

    bars: list[dict[str, Any]] = []
    for row in frame.assign(ts=seconds).itertuples(index=False):
        open_, high, low, close = (
            float(row.open),
            float(row.high),
            float(row.low),
            float(row.close),
        )
        if any(math.isnan(value) for value in (open_, high, low, close)):
            continue
        volume = getattr(row, "volume", 0.0)
        bars.append(
            {
                "time": int(row.ts),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": float(volume) if volume == volume else 0.0,
            }
        )
    bars.sort(key=lambda item: item["time"])
    return tuple(bars)


def clear_cache() -> None:
    read_local_bars.cache_clear()
