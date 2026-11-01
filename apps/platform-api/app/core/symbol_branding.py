"""Presentation-only symbol identity used by notification adapters."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class SymbolBranding:
    ticker: str
    display_name: str
    logo_url: str


_CRYPTO_TICKERS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT",
    "LINK", "LTC", "BCH", "TRX", "TON", "UNI", "ATOM", "NEAR", "SUI",
}

_COMPANY_DOMAINS = {
    "AAPL": "apple.com",
    "AMZN": "amazon.com",
    "CRCL": "circle.com",
    "GOOG": "google.com",
    "GOOGL": "google.com",
    "META": "meta.com",
    "MSFT": "microsoft.com",
    "NFLX": "netflix.com",
    "NVDA": "nvidia.com",
    "TSLA": "tesla.com",
}


def _ticker(symbol: str) -> str:
    upper = symbol.strip().upper().replace("/", "-")
    if upper.startswith("BINANCE:"):
        upper = upper.split(":", 1)[1]
        if upper.startswith("PERP:"):
            upper = upper.split(":", 1)[1]
    elif upper.startswith("HL:"):
        upper = upper.rsplit(":", 1)[-1]
    if "-" in upper:
        left, right = upper.rsplit("-", 1)
        if right in {"USD", "USDT", "USDC", "BTC", "ETH", "BNB", "PERP"}:
            upper = left
    else:
        for suffix in ("USDT", "USDC"):
            if upper.endswith(suffix) and len(upper) > len(suffix):
                upper = upper[:-len(suffix)]
                break
    return upper or "MARKET"


def _fallback_badge(ticker: str) -> str:
    color = hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:6].upper()
    label = quote(ticker[:8], safe="")
    return (
        f"https://ui-avatars.com/api/?name={label}&background={color}"
        "&color=fff&bold=true&format=png&size=128"
    )


def resolve_symbol_branding(symbol: str) -> SymbolBranding:
    ticker = _ticker(symbol)
    upper = symbol.strip().upper()
    if upper.startswith(("BINANCE:", "HL:")):
        # Provider routing is an internal implementation detail. Alerts should
        # identify the instrument itself, regardless of where its candles came
        # from.
        display_name = f"{ticker}-PERP"
    else:
        display_name = ticker if ticker != symbol.upper() else symbol.upper()

    if ticker in _CRYPTO_TICKERS:
        logo_url = (
            "https://cdn.jsdelivr.net/gh/atomiclabs/cryptocurrency-icons@master/"
            f"128/color/{ticker.lower()}.png"
        )
    elif ticker in _COMPANY_DOMAINS:
        domain = _COMPANY_DOMAINS[ticker]
        logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    else:
        logo_url = _fallback_badge(ticker)
    return SymbolBranding(ticker=ticker, display_name=display_name, logo_url=logo_url)
