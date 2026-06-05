"""
本地 crypto 数据层（混合模式）。

历史底座来自 data/crypto/*.parquet（Binance 全量历史，可回溯至 2017-08），
最近增量再用 OKX 公开接口补齐（parquet 落地后到现在的缺口），
从而做到「历史拉满 + 不缺最新」。

parquet 文件命名：{BASE}{QUOTE}_{周期}.parquet，例如 BTCUSDT_1d.parquet。
列：open_time(UTC, tz-aware), open, high, low, close, volume, ...（其余列忽略）。

symbol 归一化：BTC-USD / BTC/USD / BTC-USDT → BTCUSDT。
周期映射：1h→1h、4h→4h、1day→1d；本地不提供 15min / 1week（自动回退远端）。
"""
from __future__ import annotations

import math
import os
import time as _time
from pathlib import Path
from typing import Optional

import pandas as pd

from app.providers.binance import BinanceProvider, is_crypto_symbol

# ─── 路径 ────────────────────────────────────────────────────────────────────
# Docker 下 REPO_ROOT=/repo（仓库根挂载点）；本地回退到源码相对路径的仓库根。
_REPO_ROOT = Path(os.environ.get("REPO_ROOT", str(Path(__file__).resolve().parents[3])))
_DATA_DIR = _REPO_ROOT / "data" / "crypto"

# 项目周期 → parquet 文件后缀
_TF_SUFFIX: dict[str, str] = {
    "1h":   "1h",
    "4h":   "4h",
    "1day": "1d",
}

# ─── 缓存 ────────────────────────────────────────────────────────────────────
# parquet 文件是静态的，解析一次后常驻内存。
_parquet_cache: dict[tuple[str, str], list[dict]] = {}
# 拼接好的「parquet + OKX 增量」全量序列，带 TTL，避免每次图表请求都打 OKX。
_full_series_cache: dict[tuple[str, str], tuple[list[dict], float]] = {}
_FULL_SERIES_TTL = 60  # 秒


def _normalize_symbol(symbol: str) -> str:
    """BTC-USD / BTC/USD / BTC-USDT → BTCUSDT。"""
    s = symbol.upper().replace("/", "-")
    s = s.replace("-", "")
    # BTCUSD → BTCUSDT（与 parquet 文件名对齐）
    if s.endswith("USD") and not s.endswith("USDT"):
        s = s + "T"
    return s


def _file_path(symbol: str, timeframe: str) -> Optional[Path]:
    suffix = _TF_SUFFIX.get(timeframe)
    if suffix is None:
        return None
    return _DATA_DIR / f"{_normalize_symbol(symbol)}_{suffix}.parquet"


def has_local_data(symbol: str, timeframe: str) -> bool:
    """该 symbol+周期 是否有本地 parquet 可用（且是 crypto）。"""
    if not is_crypto_symbol(symbol):
        return False
    path = _file_path(symbol, timeframe)
    return path is not None and path.exists()


def _load_parquet(symbol: str, timeframe: str) -> list[dict]:
    """读取 parquet 并转为标准 candle dict 列表（时间升序，带内存缓存）。"""
    key = (_normalize_symbol(symbol), timeframe)
    cached = _parquet_cache.get(key)
    if cached is not None:
        return cached

    path = _file_path(symbol, timeframe)
    if path is None or not path.exists():
        _parquet_cache[key] = []
        return []

    df = pd.read_parquet(path, columns=["open_time", "open", "high", "low", "close", "volume"])
    candles: list[dict] = []
    for row in df.itertuples(index=False):
        o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
        if any(math.isnan(x) for x in (o, h, l, c)):
            continue
        candles.append({
            "time":   int(pd.Timestamp(row.open_time).timestamp()),
            "open":   o,
            "high":   h,
            "low":    l,
            "close":  c,
            "volume": float(row.volume or 0),
        })
    candles.sort(key=lambda x: x["time"])
    _parquet_cache[key] = candles
    return candles


def _merge_dedup(base: list[dict], extra: list[dict]) -> list[dict]:
    """按 time 合并去重（extra 优先），返回升序列表。"""
    by_time = {c["time"]: c for c in base}
    for c in extra:
        by_time[c["time"]] = c
    return [by_time[t] for t in sorted(by_time)]


class LocalCryptoProvider:
    """
    混合数据源：parquet 历史 + OKX 最近增量。
    接口与 BinanceProvider / YahooFinanceProvider 对齐
    （get_full_history / get_time_series_since / get_time_series）。
    """

    def __init__(self) -> None:
        self._remote = BinanceProvider()  # 实为 OKX 接口

    async def _remote_since(self, symbol: str, timeframe: str, since_ts: int) -> list[dict]:
        try:
            return await self._remote.get_time_series_since(symbol, timeframe, since_ts)
        except Exception:
            # 远端不可用（离线 / 限流）时退化为纯本地，不阻断主流程
            return []

    async def _full_series(self, symbol: str, timeframe: str) -> list[dict]:
        """parquet 全量 + OKX 补齐落地后缺口，带 TTL 缓存。"""
        key = (_normalize_symbol(symbol), timeframe)
        now = _time.time()
        cached = _full_series_cache.get(key)
        if cached is not None and now - cached[1] < _FULL_SERIES_TTL:
            return cached[0]

        base = _load_parquet(symbol, timeframe)
        if not base:
            # 无本地历史 → 全量走远端
            series = await self._remote.get_full_history(symbol, timeframe)
        else:
            gap = await self._remote_since(symbol, timeframe, base[-1]["time"])
            series = _merge_dedup(base, gap)

        _full_series_cache[key] = (series, now)
        return series

    async def get_full_history(self, symbol: str, timeframe: str) -> list[dict]:
        return await self._full_series(symbol, timeframe)

    async def get_time_series_since(
        self,
        symbol: str,
        timeframe: str,
        since_ts: int,
    ) -> list[dict]:
        """增量同步：parquet 中晚于 since_ts 的部分 + OKX 补齐 parquet 之后的缺口。"""
        base = _load_parquet(symbol, timeframe)
        if not base:
            return await self._remote_since(symbol, timeframe, since_ts)

        local_tail = [c for c in base if c["time"] > since_ts]
        gap = await self._remote_since(symbol, timeframe, max(since_ts, base[-1]["time"]))
        merged = _merge_dedup(local_tail, gap)
        return [c for c in merged if c["time"] > since_ts]

    async def get_time_series(
        self,
        symbol: str,
        timeframe: str,
        outputsize: int = 500,
        before: Optional[int] = None,
    ) -> list[dict]:
        """图表 / 指标用：从全量序列切片返回。"""
        series = await self._full_series(symbol, timeframe)
        if before is not None:
            series = [c for c in series if c["time"] < before]
        if not series:
            raise ValueError(f"未找到 {symbol} 的本地数据")
        return series[-outputsize:]


def pick_series_provider(symbol: str, timeframe: str):
    """
    为图表 / 指标 / 结构分析等「实时取数」接口选择 provider：
    crypto 且本地有 parquet → LocalCryptoProvider；其余 → YahooFinanceProvider。
    返回的对象都实现 get_time_series(symbol, timeframe, outputsize, before=...)。
    """
    if has_local_data(symbol, timeframe):
        return LocalCryptoProvider()
    from app.providers.yahoo_finance import get_provider
    return get_provider()
