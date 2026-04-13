"""
Yahoo Finance 数据层 —— 基于 yfinance 库。
无需 API Key，免费，支持股票 / ETF / 指数 / 加密货币。

symbol 格式说明：
  股票/ETF：AAPL、SPY、QQQ
  加密货币：BTC-USD、ETH-USD（也接受 BTC/USD，自动转换）
  指数：    ^GSPC（S&P500）、^IXIC（Nasdaq）、^DJI
"""
import asyncio
import math
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import yfinance as yf

# ─── 内存缓存 ────────────────────────────────────────────────────────────────
class _Cache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, ttl: int = 60) -> Optional[Any]:
        import time
        entry = self._store.get(key)
        if entry and time.time() - entry[1] < ttl:
            return entry[0]
        return None

    def set(self, key: str, value: Any) -> None:
        import time
        self._store[key] = (value, time.time())


_cache = _Cache()

# ─── 周期映射 ────────────────────────────────────────────────────────────────
_INTERVAL_MAP: dict[str, str] = {
    "15min": "15m",
    "1h":    "1h",
    "4h":    "1h",   # 拉 1h 后聚合成 4h
    "1day":  "1d",
    "1week": "1wk",
}

_PERIOD_MAP: dict[str, str] = {
    "15min": "60d",
    "1h":    "2y",
    "4h":    "2y",
    "1day":  "5y",
    "1week": "max",
}

_TYPE_MAP: dict[str, str] = {
    "equity":         "stock",
    "etf":            "etf",
    "mutualfund":     "fund",
    "index":          "index",
    "cryptocurrency": "crypto",
    "currency":       "currency",
    "futures":        "futures",
}


def _normalize(symbol: str) -> str:
    """BTC/USD → BTC-USD"""
    return symbol.replace("/", "-")


def _df_to_candles(df: pd.DataFrame) -> list[dict]:
    """pandas DataFrame → 标准 candle dict 列表"""
    candles = []
    for ts_idx, row in df.iterrows():
        # 转 UTC Unix 时间戳
        try:
            unix_ts = int(ts_idx.timestamp())
        except Exception:
            unix_ts = int(pd.Timestamp(ts_idx).timestamp())

        o, h, l, c = row.get("Open"), row.get("High"), row.get("Low"), row.get("Close")
        v = row.get("Volume", 0)

        # 过滤 None / NaN
        if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in [o, h, l, c]):
            continue

        candles.append({
            "time":   unix_ts,
            "open":   float(o),   # type: ignore[arg-type]
            "high":   float(h),   # type: ignore[arg-type]
            "low":    float(l),   # type: ignore[arg-type]
            "close":  float(c),   # type: ignore[arg-type]
            "volume": float(v or 0),
        })
    return candles


def _aggregate_to_4h(candles: list[dict]) -> list[dict]:
    """将 1h K线按 UTC 4 小时窗口聚合为 4h"""
    if not candles:
        return []
    window = 4 * 3600
    groups: dict[int, list[dict]] = {}
    for c in candles:
        key = (c["time"] // window) * window
        groups.setdefault(key, []).append(c)

    result = []
    for key in sorted(groups):
        g = groups[key]
        result.append({
            "time":   g[0]["time"],
            "open":   g[0]["open"],
            "high":   max(x["high"] for x in g),
            "low":    min(x["low"]  for x in g),
            "close":  g[-1]["close"],
            "volume": sum(x["volume"] for x in g),
        })
    return result


# ─── Provider ────────────────────────────────────────────────────────────────
class YahooFinanceProvider:

    # ── 搜索 ──────────────────────────────────────────────────────────────
    async def search(self, query: str) -> list[dict]:
        cache_key = f"yf:search:{query}"
        if (cached := _cache.get(cache_key, ttl=300)) is not None:
            return cached

        def _do() -> list[dict]:
            try:
                res = yf.Search(query, max_results=15, news_count=0)
                quotes = res.quotes or []
            except Exception:
                quotes = []

            results = []
            for item in quotes:
                qt = str(item.get("quoteType", "")).lower()
                results.append({
                    "symbol":   item.get("symbol", ""),
                    "name":     item.get("longname") or item.get("shortname") or "",
                    "type":     _TYPE_MAP.get(qt, qt),
                    "exchange": item.get("exchange", ""),
                    "currency": item.get("currency", ""),
                })
            return results

        result = await asyncio.to_thread(_do)
        _cache.set(cache_key, result)
        return result

    # ── 报价 ──────────────────────────────────────────────────────────────
    async def get_quote(self, symbol: str) -> dict:
        yf_sym = _normalize(symbol)
        cache_key = f"yf:quote:{yf_sym}"
        if (cached := _cache.get(cache_key, ttl=30)) is not None:
            return cached

        def _do() -> dict:
            ticker = yf.Ticker(yf_sym)
            fi = ticker.fast_info

            price = float(getattr(fi, "last_price", 0) or 0)
            prev  = float(getattr(fi, "previous_close", price) or price)
            change   = price - prev
            chg_pct  = (change / prev * 100) if prev else 0.0

            # 尝试获取名称（fast_info 不含名称，从 info 拿，失败则用 symbol）
            name = yf_sym
            try:
                info = ticker.info
                name = info.get("longName") or info.get("shortName") or yf_sym
            except Exception:
                pass

            return {
                "symbol":         symbol,
                "name":           name,
                "price":          round(price, 6),
                "change":         round(change, 6),
                "change_percent": round(chg_pct, 4),
                "volume":         int(getattr(fi, "regular_market_volume", 0) or 0),
                "timestamp":      datetime.now(timezone.utc).isoformat(),
            }

        result = await asyncio.to_thread(_do)
        _cache.set(cache_key, result)
        return result

    # ── K线 ───────────────────────────────────────────────────────────────
    async def get_time_series(
        self,
        symbol: str,
        interval: str,
        outputsize: int = 500,
        before: Optional[int] = None,  # Unix 时间戳，仅返回此时间之前的数据
    ) -> list[dict]:
        from datetime import timedelta

        yf_sym = _normalize(symbol)
        cache_key = f"yf:ts:{yf_sym}:{interval}:{before or 'latest'}"
        if (cached := _cache.get(cache_key, ttl=60)) is not None:
            return cached[-outputsize:]

        yf_interval = _INTERVAL_MAP.get(interval, "1d")
        yf_period   = _PERIOD_MAP.get(interval, "5y")

        # before 参数：拉取指定时间点之前的历史数据
        _LOOKBACK_DAYS = {
            "15min": 60, "1h": 730, "4h": 730,
            "1day": 365 * 10, "1week": 365 * 20,
        }

        def _do() -> pd.DataFrame:
            ticker = yf.Ticker(yf_sym)
            if before:
                end_dt   = datetime.fromtimestamp(before, tz=timezone.utc)
                days_back = _LOOKBACK_DAYS.get(interval, 365 * 5)
                start_dt = end_dt - timedelta(days=days_back)
                df = ticker.history(
                    start=start_dt, end=end_dt,
                    interval=yf_interval,
                    auto_adjust=True,
                    prepost=False,
                )
            else:
                df = ticker.history(
                    period=yf_period,
                    interval=yf_interval,
                    auto_adjust=True,
                    prepost=False,
                )
            return df

        df = await asyncio.to_thread(_do)

        if df is None or df.empty:
            raise ValueError(f"未找到 {symbol} 的数据，请确认 symbol 格式正确（加密货币用 BTC-USD）")

        candles = _df_to_candles(df)

        if interval == "4h":
            candles = _aggregate_to_4h(candles)

        _cache.set(cache_key, candles)
        return candles[-outputsize:]


# ─── 单例 ────────────────────────────────────────────────────────────────────
_provider: Optional[YahooFinanceProvider] = None


def get_provider() -> YahooFinanceProvider:
    global _provider
    if _provider is None:
        _provider = YahooFinanceProvider()
    return _provider
