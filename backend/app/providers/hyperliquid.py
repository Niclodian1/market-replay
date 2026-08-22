"""Hyperliquid public perpetual-market data provider, including HIP-3 DEXs."""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

import httpx


API_URL = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"
SYMBOL_PREFIX = "HL:"
_STOCK_ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,11}$")

_INTERVAL_MAP = {
    "15min": ("15m", 15 * 60 * 1000),
    "1h": ("1h", 60 * 60 * 1000),
    "4h": ("4h", 4 * 60 * 60 * 1000),
    "1day": ("1d", 24 * 60 * 60 * 1000),
    "1week": ("1w", 7 * 24 * 60 * 60 * 1000),
}


def is_hyperliquid_symbol(symbol: str) -> bool:
    return symbol.upper().startswith(SYMBOL_PREFIX)


def to_hyperliquid_coin(symbol: str) -> str:
    if not is_hyperliquid_symbol(symbol):
        raise ValueError(f"不是 Hyperliquid 标的：{symbol}")
    coin = symbol[len(SYMBOL_PREFIX):].strip()
    if not coin:
        raise ValueError("Hyperliquid 标的代码不能为空")
    if ":" in coin:
        dex, asset = coin.split(":", 1)
        if not dex or not asset:
            raise ValueError(f"Hyperliquid HIP-3 标的格式错误：{symbol}")
        return f"{dex.lower()}:{asset.upper()}"
    return coin.upper()


def to_canonical_symbol(coin: str) -> str:
    return f"{SYMBOL_PREFIX}{coin.upper()}"


class HyperliquidProvider:
    def __init__(self) -> None:
        # allPerpMetas is a relatively large response once many HIP-3 DEXs exist.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0))
        self._meta_cache: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]], float]] = {}
        self._meta_inflight: dict[str, asyncio.Task] = {}
        self._dex_cache: tuple[list[dict[str, Any]], float] | None = None
        self._all_meta_cache: tuple[list[dict[str, Any]], float] | None = None
        self._annotation_cache: tuple[dict[str, dict[str, Any]], float] | None = None
        self._candle_cache: dict[str, tuple[list[dict], float]] = {}
        self._stock_alias_cache: dict[str, tuple[str | None, float]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, payload: dict[str, Any]) -> Any:
        response = await self._client.post(API_URL, json=payload)
        response.raise_for_status()
        return response.json()

    async def get_perp_dexes(self, ttl: int = 300) -> list[dict[str, Any]]:
        cached = self._dex_cache
        if cached is not None and time.monotonic() - cached[1] < ttl:
            return cached[0]

        payload = await self._post({"type": "perpDexs"})
        if not isinstance(payload, list):
            raise ValueError("Hyperliquid DEX 列表格式异常")
        dexes: list[dict[str, Any]] = [{"name": "", "fullName": "Hyperliquid"}]
        dexes.extend(item for item in payload if isinstance(item, dict) and item.get("name"))
        self._dex_cache = (dexes, time.monotonic())
        return dexes

    async def get_perp_dex_names(self, ttl: int = 300) -> list[str]:
        return [str(item["name"]) for item in await self.get_perp_dexes(ttl) if item.get("name")]

    async def get_all_perp_metadata(self, ttl: int = 300) -> list[dict[str, Any]]:
        cached = self._all_meta_cache
        if cached is not None and time.monotonic() - cached[1] < ttl:
            return cached[0]
        payload = await self._post({"type": "allPerpMetas"})
        if not isinstance(payload, list):
            raise ValueError("Hyperliquid 全市场元数据格式异常")
        metas = [item for item in payload if isinstance(item, dict)]
        self._all_meta_cache = (metas, time.monotonic())
        return metas

    async def get_concise_annotations(self, ttl: int = 300) -> dict[str, dict[str, Any]]:
        cached = self._annotation_cache
        if cached is not None and time.monotonic() - cached[1] < ttl:
            return cached[0]
        payload = await self._post({"type": "perpConciseAnnotations"})
        annotations: dict[str, dict[str, Any]] = {}
        if isinstance(payload, list):
            for row in payload:
                if isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) and isinstance(row[1], dict):
                    annotations[row[0].upper()] = row[1]
        self._annotation_cache = (annotations, time.monotonic())
        return annotations

    async def get_metadata(
        self,
        dex: str = "",
        ttl: int = 10,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        dex = dex.lower()
        cached = self._meta_cache.get(dex)
        if cached is not None and time.monotonic() - cached[2] < ttl:
            return cached[0], cached[1]

        task = self._meta_inflight.get(dex)
        if task is None or task.done():
            task = asyncio.create_task(self._fetch_metadata(dex))
            self._meta_inflight[dex] = task
        try:
            return await asyncio.shield(task)
        except Exception:
            # A slightly old context is preferable to a chart that never opens.
            if cached is not None and time.monotonic() - cached[2] < 300:
                return cached[0], cached[1]
            raise
        finally:
            if self._meta_inflight.get(dex) is task and task.done():
                self._meta_inflight.pop(dex, None)

    async def _fetch_metadata(self, dex: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        request: dict[str, Any] = {"type": "metaAndAssetCtxs"}
        if dex:
            request["dex"] = dex
        payload = await self._post(request)
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError("Hyperliquid 市场元数据格式异常")
        universe = payload[0].get("universe", [])
        contexts = payload[1]
        self._meta_cache[dex] = (universe, contexts, time.monotonic())
        return universe, contexts

    @staticmethod
    def _is_stock_alias_candidate(symbol: str) -> bool:
        ticker = symbol.strip().upper()
        if is_hyperliquid_symbol(ticker) or ticker.startswith("^") or "/" in ticker:
            return False
        if ticker.endswith(("-USD", "-USDT", "-PERP")):
            return False
        return bool(_STOCK_ALIAS_RE.fullmatch(ticker))

    async def resolve_stock_aliases(
        self,
        symbols: list[str],
        ttl: int = 300,
    ) -> dict[str, str]:
        """Map plain stock tickers (AAPL) to active HIP-3 symbols when listed."""
        now = time.monotonic()
        resolved: dict[str, str] = {}
        pending: list[str] = []
        for symbol in symbols:
            ticker = symbol.strip().upper()
            if not self._is_stock_alias_candidate(ticker):
                continue
            cached = self._stock_alias_cache.get(ticker)
            if cached is not None and now - cached[1] < ttl:
                if cached[0] is not None:
                    resolved[symbol] = cached[0]
                continue
            pending.append(symbol)

        if not pending:
            return resolved

        # Most Hyperliquid equity perps are deployed on the XYZ HIP-3 DEX. This
        # compact endpoint is much faster than downloading every DEX catalog.
        xyz_error: Exception | None = None
        try:
            universe, _ = await self.get_metadata(dex="xyz", ttl=ttl)
        except Exception as exc:
            xyz_error = exc
            universe = []
        coins_by_ticker = {
            str(item.get("name", "")).split(":", 1)[-1].upper(): str(item.get("name", ""))
            for item in universe
            if item.get("name") and not item.get("isDelisted")
        }

        unresolved = {
            symbol.strip().upper()
            for symbol in pending
            if symbol.strip().upper() not in coins_by_ticker
        }

        # Cover equity markets deployed outside XYZ. A cached all-DEX catalog is
        # free; a cold catalog request gets a short budget before Yahoo fallback.
        all_meta = self._all_meta_cache
        if unresolved and (all_meta is None or now - all_meta[1] >= ttl):
            try:
                all_payload = await asyncio.wait_for(self.get_all_perp_metadata(ttl=ttl), timeout=3)
                all_meta = self._all_meta_cache or (all_payload, time.monotonic())
            except Exception:
                pass
        if unresolved and all_meta is not None and now - all_meta[1] < ttl:
            for meta in all_meta[0]:
                for item in meta.get("universe", []):
                    coin = str(item.get("name", ""))
                    if ":" not in coin or item.get("isDelisted"):
                        continue
                    coins_by_ticker.setdefault(coin.split(":", 1)[-1].upper(), coin)

        if not universe and all_meta is None and xyz_error is not None:
            raise xyz_error

        for symbol in pending:
            ticker = symbol.strip().upper()
            coin = coins_by_ticker.get(ticker)
            canonical = to_canonical_symbol(coin) if coin else None
            self._stock_alias_cache[ticker] = (canonical, now)
            if canonical is not None:
                resolved[symbol] = canonical
        return resolved

    async def search(
        self,
        query: str,
        limit: int = 20,
        aliases: Optional[list[str]] = None,
    ) -> list[dict]:
        metadata_result, dex_result, annotation_result = await asyncio.gather(
            self.get_all_perp_metadata(ttl=300),
            self.get_perp_dexes(ttl=300),
            self.get_concise_annotations(ttl=300),
            return_exceptions=True,
        )
        dexes = [] if isinstance(dex_result, Exception) else dex_result
        if isinstance(metadata_result, Exception):
            # Fall back to querying every DEX individually so a transient failure of
            # the larger allPerpMetas response never hides HIP-3 markets.
            dex_names = [str(item.get("name") or "") for item in dexes] or [""]
            metadata_batches = await asyncio.gather(
                *(self.get_metadata(dex=dex, ttl=300) for dex in dex_names),
                return_exceptions=True,
            )
            metadata = [
                {"universe": batch[0]}
                for batch in metadata_batches
                if not isinstance(batch, Exception)
            ]
            if not metadata:
                raise metadata_result
        else:
            metadata = metadata_result
        annotations = {} if isinstance(annotation_result, Exception) else annotation_result
        dex_labels = {
            str(item.get("name", "")).lower(): str(item.get("fullName") or item.get("name") or "Hyperliquid")
            for item in dexes
        }
        alias_set = {alias.strip().upper() for alias in aliases or [] if alias.strip()}
        needle = query.strip().upper()
        results = []
        for meta in metadata:
            for item in meta.get("universe", []):
                coin = str(item.get("name", ""))
                if not coin or item.get("isDelisted"):
                    continue
                dex, separator, ticker = coin.partition(":")
                if not separator:
                    dex, ticker = "", dex
                annotation = annotations.get(coin.upper(), {})
                category = str(annotation.get("category") or "")
                display_name = str(annotation.get("displayName") or "")
                raw_keywords = annotation.get("keywords") or []
                keywords = " ".join(str(keyword) for keyword in raw_keywords) if isinstance(raw_keywords, list) else str(raw_keywords)
                dex_label = dex_labels.get(dex.lower(), dex.upper() or "Hyperliquid")
                haystack = " ".join(
                    (coin, ticker, display_name, keywords, dex, dex_label, category, "Hyperliquid", "perpetual")
                ).upper()
                if needle not in haystack and ticker.upper() not in alias_set:
                    continue
                exchange = "Hyperliquid" if not dex else f"Hyperliquid · {dex_label}"
                currency = "USDC" if not dex or meta.get("collateralToken") == 0 else "USD"
                results.append({
                    "symbol": to_canonical_symbol(coin),
                    "display_symbol": f"{ticker}-PERP",
                    "name": f"{ticker} Perpetual" if not dex else f"{ticker} Perpetual ({dex_label})",
                    "type": "perpetual",
                    "exchange": exchange,
                    "currency": currency,
                    "source": "hyperliquid",
                    "source_label": exchange,
                })
                if len(results) >= limit:
                    return results
        return results

    async def _resolve_symbol(
        self,
        symbol: str,
        ttl: int = 5,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        requested_coin = to_hyperliquid_coin(symbol)
        dex = requested_coin.split(":", 1)[0].lower() if ":" in requested_coin else ""
        universe, contexts = await self.get_metadata(dex=dex, ttl=ttl)
        index = next(
            (i for i, item in enumerate(universe) if str(item.get("name", "")).upper() == requested_coin.upper()),
            None,
        )
        if index is None or index >= len(contexts):
            raise ValueError(f"Hyperliquid 未找到 {requested_coin} 永续合约")
        return str(universe[index].get("name") or requested_coin), universe[index], contexts[index]

    @staticmethod
    def _quote_payload(symbol: str, coin: str, meta: dict[str, Any], context: dict[str, Any], now: str) -> dict:
        ticker = coin.split(":", 1)[-1]
        dex = coin.split(":", 1)[0] if ":" in coin else ""
        price = float(context.get("midPx") or context.get("markPx") or 0)
        previous = float(context.get("prevDayPx") or price)
        if price <= 0:
            raise ValueError(f"Hyperliquid {coin} 暂无有效报价")
        open_interest = float(context.get("openInterest") or 0)
        source_label = "Hyperliquid" if not dex else f"Hyperliquid · {dex.upper()}"
        return {
            "symbol": symbol,
            "display_symbol": f"{ticker}-PERP",
            "name": f"{ticker} Perpetual" if not dex else f"{ticker} Perpetual ({dex.upper()})",
            "price": price,
            "change": price - previous,
            "change_percent": (price - previous) / previous * 100 if previous else 0,
            "volume": float(context.get("dayBaseVlm") or 0),
            "day_notional_volume": float(context.get("dayNtlVlm") or 0),
            "funding_rate": float(context.get("funding") or 0),
            "open_interest": open_interest,
            "open_interest_value": open_interest * price,
            "mark_price": float(context.get("markPx") or price),
            "oracle_price": float(context.get("oraclePx") or price),
            "max_leverage": int(meta.get("maxLeverage") or 0),
            "source": "hyperliquid",
            "source_label": source_label,
            "timestamp": now,
        }

    async def get_quote(self, symbol: str) -> dict:
        coin, meta, context = await self._resolve_symbol(symbol, ttl=30)
        return self._quote_payload(symbol, coin, meta, context, datetime.now(timezone.utc).isoformat())

    async def get_quotes_batch(self, symbols: list[str]) -> list[Optional[dict]]:
        results: list[Optional[dict]] = []
        groups: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        for index, symbol in enumerate(symbols):
            coin = to_hyperliquid_coin(symbol)
            dex = coin.split(":", 1)[0].lower() if ":" in coin else ""
            groups[dex].append((index, symbol, coin))
            results.append(None)

        dex_names = list(groups)
        metadata_batches = await asyncio.gather(
            *(self.get_metadata(dex=dex, ttl=30) for dex in dex_names),
            return_exceptions=True,
        )
        now = datetime.now(timezone.utc).isoformat()
        for dex, metadata in zip(dex_names, metadata_batches):
            if isinstance(metadata, Exception):
                continue
            universe, contexts = metadata
            by_coin = {
                str(item.get("name", "")).upper(): (str(item.get("name", "")), item, contexts[i])
                for i, item in enumerate(universe)
                if i < len(contexts)
            }
            for index, symbol, requested_coin in groups[dex]:
                pair = by_coin.get(requested_coin.upper())
                if pair is None:
                    continue
                coin, meta, context = pair
                try:
                    results[index] = self._quote_payload(symbol, coin, meta, context, now)
                except (TypeError, ValueError):
                    continue
        return results

    async def get_time_series(
        self,
        symbol: str,
        interval: str,
        outputsize: int = 500,
        before: Optional[int] = None,
    ) -> list[dict]:
        coin, _, _ = await self._resolve_symbol(symbol, ttl=300)
        if interval not in _INTERVAL_MAP:
            raise ValueError(f"Hyperliquid 不支持周期：{interval}")
        hl_interval, interval_ms = _INTERVAL_MAP[interval]
        end_ms = before * 1000 - 1 if before is not None else int(time.time() * 1000)
        start_ms = max(0, end_ms - interval_ms * (outputsize + 5))
        cache_key = f"{coin}:{hl_interval}:{outputsize}:{before or 'latest'}"
        cached = self._candle_cache.get(cache_key)
        ttl = 300 if before is not None else 15
        if cached is not None and time.monotonic() - cached[1] < ttl:
            return cached[0]

        try:
            rows = await self._post({
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": hl_interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            })
        except Exception:
            if cached is not None and time.monotonic() - cached[1] < 300:
                return cached[0]
            raise
        candles = []
        for row in rows or []:
            try:
                timestamp = int(row["t"]) // 1000
                if before is not None and timestamp >= before:
                    continue
                candles.append({
                    "time": timestamp,
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row.get("v") or 0),
                })
            except (KeyError, TypeError, ValueError):
                continue
        candles.sort(key=lambda candle: candle["time"])
        result = candles[-outputsize:]
        if not result:
            raise ValueError(f"Hyperliquid 未找到 {coin} 的 {interval} K线")
        self._candle_cache[cache_key] = (result, time.monotonic())
        if len(self._candle_cache) > 256:
            oldest_key = min(self._candle_cache, key=lambda key: self._candle_cache[key][1])
            self._candle_cache.pop(oldest_key, None)
        return result

    async def get_full_history(self, symbol: str, interval: str) -> list[dict]:
        """Hyperliquid exposes at most the most recent 5000 candles."""
        return await self.get_time_series(symbol, interval, outputsize=5000)

    async def get_time_series_since(self, symbol: str, interval: str, since_ts: int) -> list[dict]:
        candles = await self.get_time_series(symbol, interval, outputsize=5000)
        return [candle for candle in candles if candle["time"] > since_ts]


_provider: HyperliquidProvider | None = None


def get_hyperliquid_provider() -> HyperliquidProvider:
    global _provider
    if _provider is None:
        _provider = HyperliquidProvider()
    return _provider
