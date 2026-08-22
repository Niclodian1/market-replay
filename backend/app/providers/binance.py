"""Binance public market-data providers for Spot and USD-M perpetuals.

Canonical product symbols deliberately encode the venue for derivatives:

* ``BTC-USD`` / ``BTC-USDT`` -> Binance Spot ``BTCUSDT``
* ``BINANCE:BTCUSDT``        -> Binance USD-M perpetual ``BTCUSDT``
* ``BINANCE:AAPLUSDT``       -> Binance TradFi perpetual ``AAPLUSDT``

No API key is required.  Keeping spot and derivatives as different product
identities prevents prices and volumes from different markets being spliced
into one indicator series.
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx


SPOT_BASE_URLS = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)
FUTURES_BASE_URLS = ("https://fapi.binance.com",)
FUTURES_PREFIX = "BINANCE:"

_INTERVAL_MAP: dict[str, str] = {
    "15min": "15m",
    "1h": "1h",
    "4h": "4h",
    "1day": "1d",
    "1week": "1w",
}

_FULL_HISTORY_DAYS: dict[str, int] = {
    "15min": 365 * 9,
    "1h": 365 * 9,
    "4h": 365 * 9,
    "1day": 365 * 10,
    "1week": 365 * 10,
}

_SPOT_HISTORY_START_MS = int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
_FUTURES_HISTORY_START_MS = int(datetime(2019, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)


def is_binance_futures_symbol(symbol: str) -> bool:
    return symbol.strip().upper().startswith(FUTURES_PREFIX)


def is_crypto_symbol(symbol: str) -> bool:
    """Return whether an unprefixed product should use the Binance spot feed."""
    s = symbol.strip().upper().replace("/", "-")
    if is_binance_futures_symbol(s):
        return False
    return any(s.endswith(suffix) for suffix in ("-USD", "-USDT", "-BTC", "-ETH", "-BNB"))


def to_binance_spot_symbol(symbol: str) -> str:
    s = symbol.strip().upper().replace("/", "-").replace("-", "")
    if s.startswith("BINANCE:"):
        raise ValueError(f"{symbol} 是币安永续代码，不能作为现货请求")
    if s.endswith("USD") and not s.endswith("USDT"):
        s += "T"
    if not s or not s.isalnum():
        raise ValueError(f"无效的 Binance 现货代码：{symbol}")
    return s


def to_binance_futures_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if not s.startswith(FUTURES_PREFIX):
        raise ValueError(f"币安永续代码必须使用 {FUTURES_PREFIX} 前缀：{symbol}")
    s = s[len(FUTURES_PREFIX):]
    if s.startswith("PERP:"):
        s = s[len("PERP:"):]
    s = s.replace("/", "").replace("-", "")
    if not s or not s.isalnum():
        raise ValueError(f"无效的 Binance 永续代码：{symbol}")
    return s


def to_canonical_futures_symbol(exchange_symbol: str) -> str:
    return f"{FUTURES_PREFIX}{exchange_symbol.upper()}"


def _parse_klines(rows: list[list[Any]], *, now_ms: int | None = None) -> list[dict]:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    candles: list[dict] = []
    for row in rows:
        try:
            o, h, l, c, volume = map(float, row[1:6])
            if any(math.isnan(value) for value in (o, h, l, c, volume)):
                continue
            close_time = int(row[6])
            candle = {
                "time": int(row[0]) // 1000,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": volume,
                "is_closed": close_time < now_ms,
            }
            # Preserve useful Binance-native evidence without making callers
            # depend on fields that other providers cannot supply.
            if len(row) > 10:
                candle.update({
                    "quote_volume": float(row[7]),
                    "trade_count": int(row[8]),
                    "taker_buy_volume": float(row[9]),
                    "taker_buy_quote_volume": float(row[10]),
                })
            candles.append(candle)
        except (TypeError, ValueError, IndexError):
            continue
    candles.sort(key=lambda candle: candle["time"])
    return candles


class _BinanceKlineProvider:
    source = "binance"
    source_label = "Binance"
    _base_urls: tuple[str, ...]
    _klines_path: str
    _ticker_path: str
    _history_start_ms: int
    _page_limit: int

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None
        for base_url in self._base_urls:
            try:
                response = await self._get_client().get(f"{base_url}{path}", params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def _exchange_symbol(self, symbol: str) -> str:
        raise NotImplementedError

    async def _fetch_klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[dict]:
        if interval not in _INTERVAL_MAP:
            raise ValueError(f"Binance 不支持周期：{interval}")
        params: dict[str, Any] = {
            "symbol": self._exchange_symbol(symbol),
            "interval": _INTERVAL_MAP[interval],
            "limit": min(max(1, limit), self._page_limit),
        }
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        rows = await self._request(self._klines_path, params)
        if not isinstance(rows, list):
            raise ValueError("Binance K 线响应格式异常")
        return _parse_klines(rows)

    async def get_time_series(
        self,
        symbol: str,
        interval: str,
        outputsize: int = 500,
        before: Optional[int] = None,
    ) -> list[dict]:
        """Return up to ``outputsize`` candles, paging backwards when needed."""
        if outputsize <= 0:
            return []
        end_ms = before * 1000 - 1 if before is not None else int(time.time() * 1000)
        result: list[dict] = []
        while len(result) < outputsize:
            page_size = min(self._page_limit, outputsize - len(result))
            page = await self._fetch_klines(symbol, interval, limit=page_size, end_ms=end_ms)
            if not page:
                break
            result = page + result
            oldest_ms = page[0]["time"] * 1000
            if len(page) < page_size or oldest_ms <= self._history_start_ms:
                break
            end_ms = oldest_ms - 1
            await asyncio.sleep(0)
        deduped = {candle["time"]: candle for candle in result}
        candles = [deduped[key] for key in sorted(deduped)]
        if not candles:
            raise ValueError(f"Binance 未找到 {symbol} 的 {interval} K线")
        return candles[-outputsize:]

    async def get_time_series_since(self, symbol: str, interval: str, since_ts: int) -> list[dict]:
        """Fetch all candles whose open time is strictly after ``since_ts``."""
        start_ms = since_ts * 1000 + 1
        result: list[dict] = []
        while True:
            page = await self._fetch_klines(
                symbol,
                interval,
                limit=self._page_limit,
                start_ms=start_ms,
            )
            if not page:
                break
            result.extend(page)
            if len(page) < self._page_limit:
                break
            next_start = page[-1]["time"] * 1000 + 1
            if next_start <= start_ms:
                break
            start_ms = next_start
            await asyncio.sleep(0.03)
        deduped = {candle["time"]: candle for candle in result if candle["time"] > since_ts}
        return [deduped[key] for key in sorted(deduped)]

    async def get_full_history(self, symbol: str, interval: str) -> list[dict]:
        """Page forward from the market's earliest possible launch date."""
        start_ms = self._history_start_ms
        result: list[dict] = []
        while True:
            page = await self._fetch_klines(
                symbol,
                interval,
                limit=self._page_limit,
                start_ms=start_ms,
            )
            if not page:
                break
            result.extend(page)
            if len(page) < self._page_limit:
                break
            next_start = page[-1]["time"] * 1000 + 1
            if next_start <= start_ms:
                break
            start_ms = next_start
            await asyncio.sleep(0.03)
        deduped = {candle["time"]: candle for candle in result}
        return [deduped[key] for key in sorted(deduped)]


class BinanceSpotProvider(_BinanceKlineProvider):
    source = "binance_spot"
    source_label = "Binance Spot"
    _base_urls = SPOT_BASE_URLS
    _klines_path = "/api/v3/klines"
    _ticker_path = "/api/v3/ticker/24hr"
    _history_start_ms = _SPOT_HISTORY_START_MS
    _page_limit = 1000

    def _exchange_symbol(self, symbol: str) -> str:
        return to_binance_spot_symbol(symbol)

    async def get_quote(self, symbol: str) -> dict:
        exchange_symbol = self._exchange_symbol(symbol)
        row = await self._request(self._ticker_path, {"symbol": exchange_symbol})
        price = float(row["lastPrice"])
        return {
            "symbol": symbol,
            "provider_symbol": exchange_symbol,
            "display_symbol": exchange_symbol,
            "name": f"{exchange_symbol} Spot",
            "price": price,
            "change": float(row.get("priceChange") or 0),
            "change_percent": float(row.get("priceChangePercent") or 0),
            "volume": float(row.get("volume") or 0),
            "day_notional_volume": float(row.get("quoteVolume") or 0),
            "source": self.source,
            "source_label": self.source_label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_quotes_batch(self, symbols: list[str]) -> list[Optional[dict]]:
        rows = await asyncio.gather(*(self.get_quote(symbol) for symbol in symbols), return_exceptions=True)
        return [None if isinstance(row, Exception) else row for row in rows]


class BinanceFuturesProvider(_BinanceKlineProvider):
    source = "binance_futures"
    source_label = "Binance USD-M Futures"
    _base_urls = FUTURES_BASE_URLS
    _klines_path = "/fapi/v1/klines"
    _ticker_path = "/fapi/v1/ticker/24hr"
    _history_start_ms = _FUTURES_HISTORY_START_MS
    _page_limit = 1500

    def __init__(self) -> None:
        super().__init__()
        self._exchange_info_cache: tuple[dict[str, dict[str, Any]], float] | None = None

    def _exchange_symbol(self, symbol: str) -> str:
        return to_binance_futures_symbol(symbol)

    async def get_exchange_info(self, ttl: int = 300) -> dict[str, dict[str, Any]]:
        cached = self._exchange_info_cache
        if cached is not None and time.monotonic() - cached[1] < ttl:
            return cached[0]
        payload = await self._request("/fapi/v1/exchangeInfo")
        markets = {
            str(item.get("symbol", "")).upper(): item
            for item in payload.get("symbols", [])
            if item.get("symbol") and item.get("status") == "TRADING"
        }
        self._exchange_info_cache = (markets, time.monotonic())
        return markets

    @staticmethod
    def _is_tradfi(meta: dict[str, Any]) -> bool:
        return str(meta.get("contractType") or "").upper() == "TRADIFI_PERPETUAL"

    async def search(
        self,
        query: str,
        limit: int = 20,
        aliases: Optional[list[str]] = None,
    ) -> list[dict]:
        markets = await self.get_exchange_info()
        needle = query.strip().upper()
        alias_set = {alias.strip().upper() for alias in aliases or [] if alias.strip()}
        results = []
        for exchange_symbol, meta in markets.items():
            base_asset = str(meta.get("baseAsset") or exchange_symbol.removesuffix("USDT"))
            if (
                needle not in exchange_symbol
                and needle not in base_asset.upper()
                and base_asset.upper() not in alias_set
            ):
                continue
            tradfi = self._is_tradfi(meta)
            results.append({
                "symbol": to_canonical_futures_symbol(exchange_symbol),
                "display_symbol": f"{base_asset}-PERP",
                "name": f"{base_asset} {'美股' if tradfi else 'USD-M'}永续",
                "type": "equity_perpetual" if tradfi else "perpetual",
                "exchange": "Binance TradFi" if tradfi else "Binance Futures",
                "currency": str(meta.get("quoteAsset") or "USDT"),
                "source": self.source,
                "source_label": "Binance TradFi Perpetual" if tradfi else self.source_label,
            })
            if len(results) >= limit:
                break
        return results

    async def get_quote(self, symbol: str) -> dict:
        exchange_symbol = self._exchange_symbol(symbol)
        ticker_result, premium_result, oi_result, markets_result = await asyncio.gather(
            self._request(self._ticker_path, {"symbol": exchange_symbol}),
            self._request("/fapi/v1/premiumIndex", {"symbol": exchange_symbol}),
            self._request("/fapi/v1/openInterest", {"symbol": exchange_symbol}),
            self.get_exchange_info(),
            return_exceptions=True,
        )
        if isinstance(ticker_result, Exception):
            raise ticker_result
        premium = {} if isinstance(premium_result, Exception) else premium_result
        oi = {} if isinstance(oi_result, Exception) else oi_result
        markets = {} if isinstance(markets_result, Exception) else markets_result
        meta = markets.get(exchange_symbol, {})
        tradfi = self._is_tradfi(meta)
        base_asset = str(meta.get("baseAsset") or exchange_symbol.removesuffix("USDT"))
        price = float(ticker_result["lastPrice"])
        open_interest = float(oi.get("openInterest") or 0)
        mark_price = float(premium.get("markPrice") or price)
        return {
            "symbol": symbol,
            "provider_symbol": exchange_symbol,
            "display_symbol": f"{base_asset}-PERP",
            "name": f"{base_asset} {'美股' if tradfi else 'USD-M'}永续",
            "price": price,
            "change": float(ticker_result.get("priceChange") or 0),
            "change_percent": float(ticker_result.get("priceChangePercent") or 0),
            "volume": float(ticker_result.get("volume") or 0),
            "day_notional_volume": float(ticker_result.get("quoteVolume") or 0),
            "funding_rate": float(premium.get("lastFundingRate") or 0),
            "open_interest": open_interest,
            "open_interest_value": open_interest * mark_price,
            "mark_price": mark_price,
            "oracle_price": float(premium.get("indexPrice") or mark_price),
            "source": self.source,
            "source_label": "Binance TradFi Perpetual" if tradfi else self.source_label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_quotes_batch(self, symbols: list[str]) -> list[Optional[dict]]:
        rows = await asyncio.gather(*(self.get_quote(symbol) for symbol in symbols), return_exceptions=True)
        return [None if isinstance(row, Exception) else row for row in rows]


# Backwards-compatible name used by the existing candle store.
BinanceProvider = BinanceSpotProvider

_spot_provider: BinanceSpotProvider | None = None
_futures_provider: BinanceFuturesProvider | None = None


def get_binance_spot_provider() -> BinanceSpotProvider:
    global _spot_provider
    if _spot_provider is None:
        _spot_provider = BinanceSpotProvider()
    return _spot_provider


def get_binance_futures_provider() -> BinanceFuturesProvider:
    global _futures_provider
    if _futures_provider is None:
        _futures_provider = BinanceFuturesProvider()
    return _futures_provider


async def close_binance_providers() -> None:
    providers = [provider for provider in (_spot_provider, _futures_provider) if provider is not None]
    await asyncio.gather(*(provider.close() for provider in providers), return_exceptions=True)


def get_full_history_days(interval: str) -> int:
    return _FULL_HISTORY_DAYS.get(interval, 365 * 9)
