"""Candle helpers shared across alerting / monitoring / reporting."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def _market_close(symbol: str, candle_time: int) -> Optional[datetime]:
    """Return the close instant for a daily candle, or None for unknown markets."""
    upper = symbol.upper().replace("/", "-")
    if (
        upper in {"XAUUSD", "XAU/USD"}
        or upper.startswith(("HL:", "BINANCE:"))
        or upper.endswith(("-USD", "-USDT", "-BTC", "-ETH", "-BNB"))
    ):
        opened = datetime.fromtimestamp(candle_time, timezone.utc)
        # Crypto/local feeds may anchor their daily bar at UTC+8 midnight
        # (16:00 UTC). A daily candle closes 24h after its actual open, not at
        # the next UTC calendar midnight.
        return opened + timedelta(days=1)

    markets = {
        ".SS": ("Asia/Shanghai", time(15, 0)),
        ".SZ": ("Asia/Shanghai", time(15, 0)),
        ".BJ": ("Asia/Shanghai", time(15, 0)),
        ".HK": ("Asia/Hong_Kong", time(16, 0)),
        ".TW": ("Asia/Taipei", time(13, 30)),
        ".TWO": ("Asia/Taipei", time(13, 30)),
    }
    match = next((value for suffix, value in markets.items() if upper.endswith(suffix)), None)
    if match is None:
        # Symbols without an exchange suffix (plus Yahoo index symbols) use US hours.
        if "." in upper and not upper.startswith("^"):
            return None
        match = ("America/New_York", time(16, 0))

    tz_name, close_at = match
    market_tz = ZoneInfo(tz_name)
    session_date = datetime.fromtimestamp(candle_time, market_tz).date()
    return datetime.combine(session_date, close_at, market_tz).astimezone(timezone.utc)


def completed_daily_candles(
    symbol: str,
    candles: list[dict],
    now: Optional[datetime] = None,
) -> list[dict]:
    """Drop the latest daily candle when its market session has not closed yet."""
    if not candles:
        return []
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    # Explicit source metadata is more authoritative than a market-hours guess.
    # This prevents a live Binance/Hyperliquid daily bar from being treated as
    # a completed US-equity session merely because its underlying is a stock.
    if candles[-1].get("is_closed") is False:
        return candles[:-1]

    # Daily-report fallback feeds (Binance/Hyperliquid) keep the original
    # requested symbol for rendering but annotate the actual session clock.
    candle_symbol = str(candles[-1].get("_report_source_symbol") or symbol)
    close_at = _market_close(candle_symbol, candles[-1]["time"])
    if close_at is None or current < close_at:
        return candles[:-1]
    return candles
