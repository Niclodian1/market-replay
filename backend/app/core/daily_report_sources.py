"""Free public-source adapters used by the daily report.

All adapters return normalized symbols and keep disk fallbacks outside the
rendering layer. Fallback status is exposed only as a compact instrument tag.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.providers.binance import (
    BinanceFuturesProvider,
    get_binance_futures_provider,
    is_crypto_symbol,
)
from app.providers.hyperliquid import HyperliquidProvider, get_hyperliquid_provider
from app.providers.local_crypto import pick_series_provider
from app.providers.yahoo_finance import YahooFinanceProvider

logger = logging.getLogger(__name__)

MAGNIFICENT_SEVEN = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA")
A_SHARE_INDEX_SYMBOLS = {
    "000001.SS": "sh000001",
    "399006.SZ": "sz399006",
    "000688.SS": "sh000688",
}
_REPO_ROOT = Path(os.environ.get("REPO_ROOT", str(Path(__file__).resolve().parents[3])))
_CACHE_DIR = _REPO_ROOT / "data" / "market" / "daily_report_cache"
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MarketReplay/1.0)",
    "Accept": "application/json,text/html,text/calendar,*/*",
}
_MONTHS = {
    name: index for index, name in enumerate(
        ("January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"),
        1,
    )
}

# Yahoo index symbols do not exist as native Binance/Hyperliquid products. Use
# liquid US equity proxies so the report can still calculate trend structure
# during a Yahoo outage. Individual stocks prefer the matching TradFi perpetual.
_US_INDEX_FALLBACKS = {
    "^IXIC": ("QQQUSDT", ("HL:SPX", "HL:KM:US500", "HL:MKTS:US500")),
    "^GSPC": ("SPYUSDT", ("HL:SPX", "HL:KM:US500", "HL:MKTS:US500")),
}
_US_STOCK_TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,11}$")

# Commodities with native perpetual coverage on Binance (TradFi) and Hyperliquid.
# Key is the normalized product symbol with slashes stripped (XAUUSD, XAUUSD=X).
# Binance target must exist in the perpetual catalog; Hyperliquid targets are
# raw coins (PAXG) that resolve via HL:{coin}.
_COMMODITY_FALLBACKS = {
    "XAUUSD": ("XAUUSDT", ("PAXG",)),  # spot gold
    "XAGUSD": ("XAGUSDT", ()),          # spot silver
}


def _normalize_commodity(symbol: str) -> str:
    upper = str(symbol or "").upper().replace("/", "")
    return upper.removesuffix("=X")


def _money(value: Any) -> float:
    text = str(value or "").strip().replace("$", "").replace(",", "")
    multiplier = 1.0
    if text.endswith(("K", "M", "B", "T")):
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1]]
        text = text[:-1]
    try:
        return float(text) * multiplier
    except (TypeError, ValueError):
        return 0.0


def normalize_a_share_symbol(code: str) -> str:
    value = str(code or "").strip().upper()
    for prefix, suffix in (("SH", ".SS"), ("SZ", ".SZ"), ("BJ", ".BJ")):
        if value.startswith(prefix) and value[len(prefix):].isdigit():
            return f"{value[len(prefix):].zfill(6)}{suffix}"
    value = value.zfill(6)
    if value.startswith("6"):
        return f"{value}.SS"
    if value.startswith(("0", "3")):
        return f"{value}.SZ"
    if value.startswith(("4", "8")):
        return f"{value}.BJ"
    return value


def _attention_score(rank: int, count: int) -> float:
    if count <= 1:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (rank - 1) / (count - 1)))


class DailyReportSources:
    def __init__(
        self,
        yahoo: YahooFinanceProvider | None = None,
        *,
        binance_futures: BinanceFuturesProvider | None = None,
        hyperliquid: HyperliquidProvider | None = None,
    ) -> None:
        self.yahoo = yahoo or YahooFinanceProvider()
        # Keep derivative clients lazy in production; tests can inject small
        # fakes without touching the public-source routing behavior.
        self.binance_futures = binance_futures
        self.hyperliquid = hyperliquid

    @staticmethod
    def _is_fallback_target(symbol: str) -> bool:
        upper = str(symbol or "").strip().upper()
        # Commodities first: XAU/USD would otherwise match the crypto suffix check.
        if _normalize_commodity(upper) in _COMMODITY_FALLBACKS:
            return True
        if is_crypto_symbol(upper):
            return True
        return upper in _US_INDEX_FALLBACKS or bool(_US_STOCK_TICKER.fullmatch(upper))

    async def _fallback_candidates(self, symbol: str) -> list[tuple[str, str]]:
        """Return (provider, symbol) fallbacks for an arbitrary market symbol.

        Covers commodities (Dukascopy 主源 → Binance TradFi 永续 / Hyperliquid),
        crypto (Binance spot 主源 → Binance USD-M 永续 / Hyperliquid) and
        US stocks/indices (Yahoo 主源 → Binance TradFi 永续 / Hyperliquid).
        """
        upper = str(symbol or "").strip().upper()
        if not self._is_fallback_target(upper):
            return []

        candidates: list[tuple[str, str]] = []
        binance = self.binance_futures or get_binance_futures_provider()
        hyperliquid = self.hyperliquid or get_hyperliquid_provider()

        commodity = _normalize_commodity(upper)
        if commodity in _COMMODITY_FALLBACKS:
            binance_target, hl_coins = _COMMODITY_FALLBACKS[commodity]
            try:
                markets = await binance.get_exchange_info(ttl=300)
            except Exception:
                markets = {}
            if not markets or binance_target in markets:
                candidates.append(("binance", f"BINANCE:{binance_target}"))
            candidates.extend(("hyperliquid", f"HL:{coin}") for coin in hl_coins)
            return candidates

        if is_crypto_symbol(upper):
            coin = upper.split("-")[0] if "-" in upper else upper.replace("USDT", "").replace("USD", "")
            binance_target = f"{coin}USDT"
            try:
                markets = await binance.get_exchange_info(ttl=300)
            except Exception:
                markets = {}
            if not markets or binance_target in markets:
                candidates.append(("binance", f"BINANCE:{binance_target}"))
            candidates.append(("hyperliquid", f"HL:{coin}"))
            return candidates

        if upper in _US_INDEX_FALLBACKS:
            binance_target, hyperliquid_targets = _US_INDEX_FALLBACKS[upper]
        else:
            binance_target = f"{upper}USDT"
            hyperliquid_targets = ()

        try:
            markets = await binance.get_exchange_info(ttl=300)
        except Exception:
            # The catalog can fail independently from klines, so retain the
            # canonical candidate and let the kline request make the decision.
            markets = {}
        meta = markets.get(binance_target, {})
        if not markets or str(meta.get("contractType") or "").upper() == "TRADIFI_PERPETUAL":
            candidates.append(("binance", f"BINANCE:{binance_target}"))

        if upper not in _US_INDEX_FALLBACKS:
            try:
                aliases = await hyperliquid.resolve_stock_aliases([upper])
            except Exception:
                aliases = {}
            provider_symbol = aliases.get(upper)
            if provider_symbol:
                hyperliquid_targets = (provider_symbol,)

        candidates.extend(("hyperliquid", target) for target in hyperliquid_targets)
        return candidates

    async def _fallback_candles(
        self,
        symbol: str,
        timeframe: str,
        outputsize: int,
        before: int | None,
        primary_error: Exception,
    ) -> list[dict]:
        errors: list[str] = []
        for provider_name, provider_symbol in await self._fallback_candidates(symbol):
            try:
                if provider_name == "binance":
                    provider = self.binance_futures or get_binance_futures_provider()
                else:
                    provider = self.hyperliquid or get_hyperliquid_provider()
                rows = await provider.get_time_series(
                    provider_symbol,
                    timeframe,
                    outputsize,
                    before=before,
                )
                if len(rows) < 2:
                    raise ValueError("备用数据不足两根K线")
                logger.warning(
                    "market data fallback: %s %s -> %s (%s)",
                    symbol,
                    timeframe,
                    provider_symbol,
                    provider_name,
                )
                return [
                    {**row, "_report_source_symbol": provider_symbol}
                    for row in rows
                ]
            except Exception as exc:
                errors.append(f"{provider_symbol}: {exc}")

        if errors:
            raise ValueError(
                f"{symbol} 主数据源不可用，Binance/Hyperliquid 备用数据也不可用："
                + "；".join(errors)[:450]
            ) from primary_error
        raise primary_error

    @staticmethod
    def _read_cache(name: str, max_age_hours: int) -> Any | None:
        path = _CACHE_DIR / name
        try:
            if datetime.now(timezone.utc).timestamp() - path.stat().st_mtime > max_age_hours * 3600:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write_cache(name: str, value: Any) -> None:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path = _CACHE_DIR / name
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            logger.warning("Unable to persist daily-report cache %s", name)

    async def get_candles(
        self,
        symbol: str,
        outputsize: int = 180,
        before: int | None = None,
        timeframe: str = "1day",
    ) -> list[dict]:
        if timeframe == "1day" and symbol.upper() in A_SHARE_INDEX_SYMBOLS:
            return await self._a_share_index_candles(symbol.upper(), outputsize, before=before)
        provider = pick_series_provider(symbol, timeframe)
        try:
            rows = await provider.get_time_series(symbol, timeframe, outputsize, before=before)
            if any(row.get("_yahoo_stale") for row in rows):
                raise ValueError("Yahoo Finance 仅返回过期缓存")
            return rows
        except Exception as exc:
            if not self._is_fallback_target(symbol):
                raise
            return await self._fallback_candles(
                symbol,
                timeframe,
                outputsize,
                before,
                exc,
            )

    @staticmethod
    def _index_frame_to_candles(frame: Any, before: int | None = None) -> list[dict]:
        rows: list[dict] = []
        if frame is None or getattr(frame, "empty", True):
            return rows
        for raw in frame.to_dict("records"):
            raw_date = raw.get("date") or raw.get("日期")
            try:
                if isinstance(raw_date, datetime):
                    session_date = raw_date.date()
                elif isinstance(raw_date, date):
                    session_date = raw_date
                else:
                    session_date = date.fromisoformat(str(raw_date)[:10])
            except (TypeError, ValueError):
                continue
            timestamp = int(datetime.combine(
                session_date,
                time.min,
                ZoneInfo("Asia/Shanghai"),
            ).timestamp())
            if before is not None and timestamp >= before:
                continue
            open_price = _money(raw.get("open") or raw.get("开盘"))
            high = _money(raw.get("high") or raw.get("最高"))
            low = _money(raw.get("low") or raw.get("最低"))
            close = _money(raw.get("close") or raw.get("收盘"))
            if min(open_price, high, low, close) <= 0:
                continue
            rows.append({
                "time": timestamp,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": _money(
                    raw.get("volume")
                    or raw.get("amount")
                    or raw.get("成交量")
                    or raw.get("成交额")
                ),
            })
        return sorted(rows, key=lambda row: row["time"])

    async def _a_share_index_candles(
        self,
        symbol: str,
        outputsize: int,
        *,
        before: int | None = None,
    ) -> list[dict]:
        vendor_symbol = A_SHARE_INDEX_SYMBOLS[symbol]
        cache_name = f"a_share_index_{vendor_symbol}.json"
        cached = self._read_cache(cache_name, 24 * 7)

        try:
            def _load_sina():
                import akshare as ak
                return ak.stock_zh_index_daily(symbol=vendor_symbol)

            rows = self._index_frame_to_candles(
                await asyncio.to_thread(_load_sina),
                before,
            )
            if len(rows) >= 2:
                self._write_cache(cache_name, rows)
                return rows[-outputsize:]
        except Exception as exc:
            logger.warning("Sina A-share index history unavailable for %s: %s", symbol, exc)

        try:
            def _load_tencent():
                import akshare as ak
                return ak.stock_zh_index_daily_tx(symbol=vendor_symbol)

            rows = self._index_frame_to_candles(
                await asyncio.to_thread(_load_tencent),
                before,
            )
            if len(rows) >= 2:
                self._write_cache(cache_name, rows)
                return rows[-outputsize:]
        except Exception as exc:
            logger.warning("Tencent A-share index history unavailable for %s: %s", symbol, exc)

        try:
            rows = await self.yahoo.get_time_series(
                symbol,
                "1day",
                outputsize,
                before=before,
            )
            if len(rows) >= 2:
                return rows[-outputsize:]
        except Exception as exc:
            logger.warning("Yahoo A-share index history unavailable for %s: %s", symbol, exc)

        if cached:
            rows = [
                row for row in cached
                if before is None or int(row.get("time") or 0) < before
            ]
            if rows:
                return rows[-outputsize:]
        raise ValueError(f"{symbol} 没有可用的A股指数日线")

    async def _a_share_spot(self) -> list[dict]:
        cached = self._read_cache("a_share_spot.json", 18)
        try:
            def _load() -> list[dict]:
                import akshare as ak  # Lazy so the backend can still start during optional-source outages.

                frame = ak.stock_zh_a_spot_em()
                rows: list[dict] = []
                for row in frame.to_dict("records"):
                    code = str(row.get("代码") or "")
                    if not code or not code[0] in "03468":
                        continue
                    rows.append({
                        "symbol": normalize_a_share_symbol(code),
                        "name": str(row.get("名称") or code),
                        "price": _money(row.get("最新价")),
                        "volume": _money(row.get("成交量")),
                        "turnover": _money(row.get("成交额")),
                    })
                return rows

            rows = await asyncio.to_thread(_load)
            if rows:
                self._write_cache("a_share_spot.json", rows)
                return rows
        except Exception as exc:
            logger.warning("A-share snapshot unavailable: %s", exc)
        return cached or []

    async def top_turnover(self, market: str, limit: int = 5) -> list[dict]:
        if market == "a_share":
            rows = await self._a_share_spot()
            return sorted(rows, key=lambda row: float(row.get("turnover") or 0), reverse=True)[:limit]
        if market == "us_stock":
            rows = await self._us_screener()
            return sorted(rows, key=lambda row: float(row.get("turnover") or 0), reverse=True)[:limit]
        return []

    async def _us_screener(self) -> list[dict]:
        cached = self._read_cache("us_stock_screener.json", 18)
        url = "https://api.nasdaq.com/api/screener/stocks"
        try:
            async with httpx.AsyncClient(timeout=20, headers=_HTTP_HEADERS, follow_redirects=True) as client:
                response = await client.get(url, params={
                    "tableonly": "true", "limit": 10000, "offset": 0, "download": "true",
                })
                response.raise_for_status()
                raw_rows = response.json().get("data", {}).get("rows") or []
            rows: list[dict] = []
            for row in raw_rows:
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol or any(mark in symbol for mark in ("^", "/", "+", "=")):
                    continue
                price = _money(row.get("lastsale"))
                volume = _money(row.get("volume"))
                if price <= 0 or volume <= 0:
                    continue
                rows.append({
                    "symbol": symbol,
                    "name": str(row.get("name") or symbol),
                    "price": price,
                    "volume": volume,
                    "turnover": price * volume,
                })
            if rows:
                self._write_cache("us_stock_screener.json", rows)
                return rows
        except Exception as exc:
            logger.warning("US screener unavailable: %s", exc)
        return cached or []

    async def hot_candidates(self, market: str, limit: int = 30) -> list[dict]:
        if market == "crypto":
            return await self._crypto_hot(limit)
        if market == "a_share":
            return await self._a_share_hot(limit)
        if market == "us_stock":
            return await self._us_hot(limit)
        return []

    async def _crypto_hot(self, limit: int) -> list[dict]:
        cached = self._read_cache("crypto_hot.json", 72)
        try:
            async with httpx.AsyncClient(timeout=15, headers=_HTTP_HEADERS, follow_redirects=True) as client:
                response = await client.get("https://api.coingecko.com/api/v3/search/trending")
                response.raise_for_status()
                coins = response.json().get("coins") or []
            rows: list[dict] = []
            excluded = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "WETH", "WBTC"}
            for rank, entry in enumerate(coins[:limit], 1):
                item = entry.get("item") or {}
                ticker = str(item.get("symbol") or "").upper()
                if not ticker or ticker in excluded or "WRAPPED" in str(item.get("name") or "").upper():
                    continue
                data = item.get("data") or {}
                rows.append({
                    "symbol": f"{ticker}-USD",
                    "name": str(item.get("name") or ticker),
                    "attention_score": _attention_score(rank, max(2, len(coins[:limit]))),
                    "turnover": _money(data.get("total_volume")),
                    "source_rank": rank,
                })
            if rows:
                self._write_cache("crypto_hot.json", rows)
                return rows
        except Exception as exc:
            logger.warning("Crypto hot list unavailable: %s", exc)
        return cached or []

    async def _a_share_hot(self, limit: int) -> list[dict]:
        cached = self._read_cache("a_share_hot.json", 72)
        try:
            def _load() -> list[dict]:
                import akshare as ak

                frame = ak.stock_hot_rank_em()
                output = []
                records = frame.head(limit).to_dict("records")
                for rank, row in enumerate(records, 1):
                    code = str(row.get("代码") or row.get("股票代码") or "")
                    if not code:
                        continue
                    output.append({
                        "symbol": normalize_a_share_symbol(code),
                        "name": str(row.get("股票名称") or row.get("名称") or code),
                        "attention_score": _attention_score(rank, max(2, len(records))),
                        "turnover": 0,
                        "source_rank": rank,
                    })
                return output

            hot = await asyncio.to_thread(_load)
            spot = {row["symbol"]: row for row in await self._a_share_spot()}
            for row in hot:
                row["turnover"] = float(spot.get(row["symbol"], {}).get("turnover") or 0)
            if hot:
                self._write_cache("a_share_hot.json", hot)
                return hot
        except Exception as exc:
            logger.warning("A-share hot list unavailable: %s", exc)
        return cached or []

    async def _us_hot(self, limit: int) -> list[dict]:
        cached = self._read_cache("us_stock_hot.json", 72)
        try:
            url = "https://query1.finance.yahoo.com/v1/finance/trending/US"
            async with httpx.AsyncClient(timeout=15, headers=_HTTP_HEADERS, follow_redirects=True) as client:
                response = await client.get(url, params={"count": limit})
                response.raise_for_status()
                quotes = (response.json().get("finance", {}).get("result") or [{}])[0].get("quotes") or []
            symbols = [
                str(row.get("symbol") or "").strip().upper()
                for row in quotes if row.get("symbol")
            ][:limit]
            screen = {row["symbol"]: row for row in await self._us_screener()}
            rows = []
            for rank, symbol in enumerate(symbols, 1):
                market_row = screen.get(symbol, {})
                if not market_row:
                    # The stocks screener acts as the product-type allowlist;
                    # trending ETFs, funds and other instruments stay excluded.
                    continue
                rows.append({
                    "symbol": symbol,
                    "name": str(market_row.get("name") or symbol),
                    "attention_score": _attention_score(rank, max(2, len(symbols))),
                    "turnover": float(market_row.get("turnover") or 0),
                    "source_rank": rank,
                })
            if rows:
                self._write_cache("us_stock_hot.json", rows)
                return rows
        except Exception as exc:
            logger.warning("US hot list unavailable: %s", exc)
        return cached or []

    async def calendar_events(self, start: date, days: int = 7) -> list[dict]:
        end = start + timedelta(days=days)
        events: list[dict] = []
        fetched = await asyncio.gather(
            self._fomc_events(start, end),
            self._bls_events(start, end),
            self._earnings_events(start, end),
            return_exceptions=True,
        )
        for result in fetched:
            if isinstance(result, list):
                events.extend(result)
            elif isinstance(result, Exception):
                logger.warning("Calendar source unavailable: %s", result)
        return sorted(events, key=lambda row: row["starts_at"])

    async def _fomc_events(self, start: date, end: date) -> list[dict]:
        cached = self._read_cache("fomc_calendar.json", 24 * 14)
        events: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=15, headers=_HTTP_HEADERS, follow_redirects=True) as client:
                response = await client.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
                response.raise_for_status()
                html = response.text
            for panel in re.finditer(
                r"(?P<year>20\d{2}) FOMC Meetings.*?(?=(?:20\d{2} FOMC Meetings)|$)",
                html,
                re.DOTALL,
            ):
                year = int(panel.group("year"))
                for match in re.finditer(
                    r"fomc-meeting__month[^>]*><strong>(?P<month>[A-Za-z]+)</strong>.*?"
                    r"fomc-meeting__date[^>]*>(?P<days>[^<]+)",
                    panel.group(0),
                    re.DOTALL,
                ):
                    month = _MONTHS.get(match.group("month"))
                    day_values = re.findall(r"\d+", match.group("days"))
                    if not month or not day_values:
                        continue
                    decision_day = int(day_values[-1])
                    eastern = datetime.combine(date(year, month, decision_day), time(14, 0), ZoneInfo("America/New_York"))
                    local = eastern.astimezone(ZoneInfo("Asia/Taipei"))
                    events.append({
                        "category": "FOMC",
                        "name": "FOMC 利率决议",
                        "starts_at": local.isoformat(timespec="minutes"),
                    })
            if events:
                self._write_cache("fomc_calendar.json", events)
        except Exception as exc:
            if cached is None:
                raise
            logger.warning("Using cached FOMC calendar: %s", exc)
            events = cached
        return [row for row in events if start <= datetime.fromisoformat(row["starts_at"]).date() < end]

    async def _bls_events(self, start: date, end: date) -> list[dict]:
        cached = self._read_cache("bls_calendar.json", 24 * 14)
        events: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=15, headers=_HTTP_HEADERS, follow_redirects=True) as client:
                response = await client.get("https://www.bls.gov/schedule/news_release/bls.ics")
                response.raise_for_status()
                text = response.text
            if "BEGIN:VCALENDAR" not in text:
                raise ValueError("BLS calendar response is not iCalendar")
            unfolded = re.sub(r"\r?\n[ \t]", "", text)
            for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.DOTALL):
                summary_match = re.search(r"\nSUMMARY:(.+)", block)
                start_match = re.search(r"\nDTSTART(?:;[^:]*)?:(\d{8}T\d{6})", block)
                if not summary_match or not start_match:
                    continue
                summary = summary_match.group(1).strip()
                if "Consumer Price Index" in summary:
                    category, name = "CPI", "美国 CPI"
                elif "Employment Situation" in summary:
                    category, name = "NFP", "美国非农就业数据"
                else:
                    continue
                eastern = datetime.strptime(start_match.group(1), "%Y%m%dT%H%M%S").replace(
                    tzinfo=ZoneInfo("America/New_York")
                )
                local = eastern.astimezone(ZoneInfo("Asia/Taipei"))
                events.append({"category": category, "name": name, "starts_at": local.isoformat(timespec="minutes")})
            if events:
                self._write_cache("bls_calendar.json", events)
        except Exception as exc:
            if cached is None:
                raise
            logger.warning("Using cached BLS calendar: %s", exc)
            events = cached
        return [row for row in events if start <= datetime.fromisoformat(row["starts_at"]).date() < end]

    async def _earnings_events(self, start: date, end: date) -> list[dict]:
        earnings = await self.yahoo.get_earnings_dates(list(MAGNIFICENT_SEVEN))
        output = []
        for symbol, date_text in earnings.items():
            try:
                earnings_date = date.fromisoformat(str(date_text))
            except (TypeError, ValueError):
                continue
            if start <= earnings_date < end:
                local = datetime.combine(earnings_date, time(0, 0), ZoneInfo("Asia/Taipei"))
                output.append({
                    "category": "EARNINGS",
                    "name": f"{symbol} 财报",
                    "starts_at": local.isoformat(timespec="minutes"),
                    "timing": "时间待确认",
                })
        return output
