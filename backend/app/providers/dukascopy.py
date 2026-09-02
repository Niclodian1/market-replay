"""Dukascopy XAUUSD daily history with a durable local parquet fallback.

The public product symbol stays ``XAUUSD``. Dukascopy exposes compressed
delta-encoded yearly candle buckets; this provider normalises those buckets to
the project's candle shape and persists the merged daily series locally.
"""
from __future__ import annotations

import asyncio
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd


_REPO_ROOT = Path(os.environ.get("REPO_ROOT", str(Path(__file__).resolve().parents[3])))
_CACHE_PATH = _REPO_ROOT / "data" / "market" / "XAUUSD_1d.parquet"
_START_YEAR = 1999
_BASE_URL = "https://jetta.dukascopy.com/v1/candles/day/XAU-USD/BID"
_memory_cache: tuple[list[dict], float] | None = None


def is_xauusd_symbol(symbol: str) -> bool:
    return symbol.upper().replace("/", "") in {"XAUUSD", "XAUUSD=X"}


def _decode_bucket(data: dict) -> list[dict]:
    times = data.get("times") or []
    columns = [data.get(key) or [] for key in ("opens", "highs", "lows", "closes", "volumes")]
    if not times or any(len(column) != len(times) for column in columns):
        return []

    multiplier = float(data["multiplier"])
    shift = int(data["shift"])
    timestamp = int(data["timestamp"])
    values = [round(float(data[key]) / multiplier) for key in ("open", "high", "low", "close")]
    scale = max(0, int(round(-math.log10(multiplier)))) if multiplier < 1 else 0

    candles: list[dict] = []
    for index, delta in enumerate(times):
        timestamp += int(delta) * shift
        for column_index in range(4):
            values[column_index] += int(columns[column_index][index])
        volume = float(columns[4][index] or 0)
        # This is the same flat-bar filter used by the upstream downloader.
        if volume == 0:
            continue
        candles.append({
            "time": timestamp // 1000,
            "open": round(values[0] * multiplier, scale),
            "high": round(values[1] * multiplier, scale),
            "low": round(values[2] * multiplier, scale),
            "close": round(values[3] * multiplier, scale),
            "volume": volume,
        })
    return candles


def _load_local(path: Path) -> list[dict]:
    if not path.exists():
        return []
    frame = pd.read_parquet(path)
    records = frame.to_dict("records")
    return [
        {
            "time": int(row["time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0) or 0),
        }
        for row in records
    ]


def _save_local(candles: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(candles).to_parquet(path, index=False)


async def _fetch_year(client: httpx.AsyncClient, year: int, url: str) -> list[dict]:
    current_year = datetime.now(timezone.utc).year
    if year == current_year:
        start_ms = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        url = f"{url}?from={start_ms}"
    else:
        url = f"{url}/{year}"
    response = await client.get(url)
    response.raise_for_status()
    return _decode_bucket(response.json())


class DukascopyMetalProvider:
    """Dukascopy 贵金属日线(XAU-USD / XAG-USD ...): 年度桶增量合并 + 本地 parquet 缓存。

    pair: Dukascopy 产品对(如 "XAU-USD"); product: 本地产品符号(如 "XAUUSD"),
    决定缓存路径 data/market/{product}_1d.parquet 与符号校验口径。
    """

    _start_year = _START_YEAR

    def __init__(self, pair: str = "XAU-USD", product: str | None = None):
        self._pair = pair.upper()
        self._product = (product or self._pair.replace("-", "")).upper()
        self._url = f"https://jetta.dukascopy.com/v1/candles/day/{self._pair}/BID"
        self._cache_path = _REPO_ROOT / "data" / "market" / f"{self._product}_1d.parquet"
        self._memory_cache: tuple[list[dict], float] | None = None

    def _validate_symbol(self, symbol: str) -> bool:
        return symbol.upper().replace("/", "").replace("=", "") == self._product

    async def get_full_history(self, symbol: str, interval: str) -> list[dict]:
        if not self._validate_symbol(symbol) or interval != "1day":
            raise ValueError(f"Dukascopy {self._product} 第一阶段仅提供日线")

        now = time.monotonic()
        if self._memory_cache is not None and now - self._memory_cache[1] < 300:
            return self._memory_cache[0]

        local = await asyncio.to_thread(_load_local, self._cache_path)
        current_year = datetime.now(timezone.utc).year
        years = range(self._start_year, current_year + 1) if not local else range(max(self._start_year, current_year - 1), current_year + 1)
        semaphore = asyncio.Semaphore(8)

        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            async def fetch(year: int) -> list[dict]:
                async with semaphore:
                    return await _fetch_year(client, year, self._url)

            fetched = await asyncio.gather(*(fetch(year) for year in years), return_exceptions=True)

        updates = [candle for result in fetched if not isinstance(result, Exception) for candle in result]
        if not updates and not local:
            errors = [str(result) for result in fetched if isinstance(result, Exception)]
            raise ValueError(f"{self._product} 历史数据暂不可用：{errors[0] if errors else '空数据'}")

        merged = {candle["time"]: candle for candle in local}
        merged.update({candle["time"]: candle for candle in updates})
        candles = [merged[key] for key in sorted(merged)]
        if updates:
            await asyncio.to_thread(_save_local, candles, self._cache_path)
        self._memory_cache = (candles, now)
        return candles

    async def get_time_series(
        self,
        symbol: str,
        interval: str,
        outputsize: int = 500,
        before: Optional[int] = None,
    ) -> list[dict]:
        candles = await self.get_full_history(symbol, interval)
        if before is not None:
            candles = [candle for candle in candles if candle["time"] < before]
        return candles[-outputsize:]

    async def get_time_series_since(self, symbol: str, interval: str, since_ts: int) -> list[dict]:
        candles = await self.get_full_history(symbol, interval)
        return [candle for candle in candles if candle["time"] > since_ts]

    async def get_quote(self, symbol: str) -> dict:
        candles = await self.get_full_history(symbol, "1day")
        if not candles:
            raise ValueError(f"{self._product} 暂无报价")
        latest = candles[-1]
        previous = candles[-2] if len(candles) > 1 else latest
        change = latest["close"] - previous["close"]
        return {
            "symbol": self._product,
            "name": "现货" + ("黄金" if self._product == "XAUUSD" else self._product),
            "price": latest["close"],
            "change": change,
            "change_percent": change / previous["close"] * 100 if previous["close"] else 0,
            "volume": latest.get("volume", 0),
            "timestamp": datetime.fromtimestamp(latest["time"], timezone.utc).isoformat(),
            "source": "dukascopy",
            "source_label": "Dukascopy",
        }


class DukascopyXauProvider(DukascopyMetalProvider):
    """向后兼容别名: 黄金日线 provider(行为与旧版完全一致)。"""

    def __init__(self):
        super().__init__("XAU-USD", "XAUUSD")


_provider = DukascopyXauProvider()


def get_xau_provider() -> DukascopyXauProvider:
    return _provider
