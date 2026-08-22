import asyncio
from typing import Optional
from fastapi import APIRouter, Query, HTTPException, WebSocket, WebSocketDisconnect
from app.core.hyperliquid_stream import hyperliquid_stream
from app.providers.yahoo_finance import get_provider
from app.providers.local_crypto import pick_series_provider
from app.providers.hyperliquid import get_hyperliquid_provider, is_hyperliquid_symbol
from app.providers.dukascopy import get_xau_provider, is_xauusd_symbol
from app.providers.binance import (
    get_binance_futures_provider,
    get_binance_spot_provider,
    is_binance_futures_symbol,
    is_crypto_symbol,
    to_binance_futures_symbol,
)
from app.schemas.market import SearchResponse, QuoteResponse, CandlesResponse, BatchQuoteResponse

router = APIRouter()

VALID_INTERVALS = {"15min", "1h", "4h", "1day", "1week"}
VALID_SOURCES = {
    "auto", "hyperliquid", "yahoo", "dukascopy",
    "binance_spot", "binance_futures",
}


def _normalize_source(source: str) -> str:
    # Route functions are also called directly by a few unit tests, where a
    # FastAPI Query default is not resolved into a string.
    normalized = source.lower() if isinstance(source, str) else "auto"
    if normalized not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"source 必须是 {VALID_SOURCES} 之一",
        )
    return normalized


async def _resolve_hyperliquid_stock_aliases(symbols: list[str]) -> dict[str, str]:
    try:
        return await asyncio.wait_for(
            get_hyperliquid_provider().resolve_stock_aliases(symbols),
            timeout=8,
        )
    except Exception:
        # Discovery is best-effort: Yahoo remains the fallback when the HIP-3
        # catalog is temporarily unavailable.
        return {}


def _hyperliquid_quote_for_request(requested_symbol: str, provider_symbol: str, quote: dict) -> dict:
    return {
        **quote,
        "symbol": requested_symbol,
        "provider_symbol": provider_symbol,
    }


def _yahoo_quote_for_request(requested_symbol: str, quote: dict) -> dict:
    return {
        **quote,
        "symbol": requested_symbol,
        "display_symbol": quote.get("symbol", requested_symbol),
        "source": "yahoo",
        "source_label": "Yahoo Finance",
    }


@router.websocket("/stream/hyperliquid")
async def hyperliquid_market_stream(websocket: WebSocket):
    await websocket.accept()
    requested = {
        symbol.strip().upper()
        for symbol in websocket.query_params.get("symbols", "").split(",")
        if is_hyperliquid_symbol(symbol.strip())
    }
    queue = hyperliquid_stream.subscribe()
    try:
        while True:
            payload = await queue.get()
            mids = payload.get("mids", {})
            if requested:
                mids = {symbol: price for symbol, price in mids.items() if symbol in requested}
            if mids:
                await websocket.send_json({**payload, "mids": mids})
    except WebSocketDisconnect:
        pass
    finally:
        hyperliquid_stream.unsubscribe(queue)


@router.get("/search", response_model=SearchResponse)
async def search_symbols(q: str = Query(..., min_length=1, description="搜索关键词")):
    try:
        yahoo_result, hyperliquid_result, binance_result = await asyncio.gather(
            get_provider().search(q),
            get_hyperliquid_provider().search(q),
            get_binance_futures_provider().search(q),
            return_exceptions=True,
        )
        if all(isinstance(item, Exception) for item in (yahoo_result, hyperliquid_result, binance_result)):
            raise yahoo_result
        yahoo_items = [] if isinstance(yahoo_result, Exception) else [
            {
                **item,
                "display_symbol": item.get("symbol"),
                "source": "yahoo",
                "source_label": "Yahoo Finance",
            }
            for item in yahoo_result
        ]
        hyperliquid_items = [] if isinstance(hyperliquid_result, Exception) else hyperliquid_result
        binance_items = [] if isinstance(binance_result, Exception) else binance_result
        # Company-name searches (for example "Apple" or "Circle") are resolved by
        # Yahoo first, then their tickers are used to expose matching HIP-3 perps.
        if yahoo_items:
            alias_symbols = [str(item.get("symbol", "")) for item in yahoo_items if item.get("symbol")]
            if not isinstance(hyperliquid_result, Exception):
                try:
                    hyperliquid_items = await get_hyperliquid_provider().search(q, aliases=alias_symbols)
                except Exception:
                    pass
            try:
                binance_items = await get_binance_futures_provider().search(q, aliases=alias_symbols)
            except Exception:
                pass
        xau_items = []
        if any(token in q.strip().lower() for token in ("xau", "gold", "黄金", "黃金")):
            xau_items.append({
                "symbol": "XAUUSD", "display_symbol": "XAUUSD", "name": "现货黄金",
                "type": "commodity", "exchange": "OTC", "currency": "USD",
                "source": "dukascopy", "source_label": "Dukascopy",
            })
        results = [*xau_items, *binance_items, *hyperliquid_items, *yahoo_items]
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/capabilities")
async def get_market_capabilities():
    """Machine-readable routing contract used by alert configuration UIs."""
    return {
        "timeframes": sorted(VALID_INTERVALS),
        "sources": [
            {
                "id": "binance_spot",
                "label": "Binance Spot",
                "asset_types": ["crypto_spot"],
                "timeframes": ["15min", "1h", "4h", "1day", "1week"],
                "closed_bar_signals": True,
                "symbol_example": "BTC-USD",
            },
            {
                "id": "binance_futures",
                "label": "Binance USD-M / TradFi Perpetual",
                "asset_types": ["crypto_perpetual", "equity_perpetual"],
                "timeframes": ["15min", "1h", "4h", "1day", "1week"],
                "closed_bar_signals": True,
                "symbol_example": "BINANCE:AAPLUSDT",
            },
            {
                "id": "hyperliquid",
                "label": "Hyperliquid",
                "asset_types": ["crypto_perpetual", "equity_perpetual"],
                "timeframes": ["15min", "1h", "4h", "1day", "1week"],
                "closed_bar_signals": True,
                "symbol_example": "HL:XYZ:AAPL",
            },
            {
                "id": "yahoo",
                "label": "Yahoo Finance",
                "asset_types": ["stock", "etf", "index"],
                "timeframes": ["15min", "1h", "4h", "1day", "1week"],
                "intraday_history_note": "15 分钟等日内历史受上游窗口限制",
                "closed_bar_signals": True,
                "symbol_example": "AAPL",
            },
            {
                "id": "dukascopy",
                "label": "Dukascopy",
                "asset_types": ["spot_gold"],
                "timeframes": ["1day"],
                "closed_bar_signals": True,
                "symbol_example": "XAUUSD",
            },
        ],
    }


@router.get("/quote", response_model=QuoteResponse)
async def get_quote(
    symbol: str = Query(..., description="标的代码，如 AAPL 或 BTC/USD"),
    source: str = Query(default="auto", description="数据源：auto/hyperliquid/yahoo"),
):
    requested_source = _normalize_source(source)
    try:
        explicit_binance_futures = is_binance_futures_symbol(symbol)
        if explicit_binance_futures:
            if requested_source not in {"auto", "binance_futures"}:
                raise ValueError(f"{symbol} 是 Binance USD-M 永续代码，数据源必须使用 Binance Futures")
            return await asyncio.wait_for(get_binance_futures_provider().get_quote(symbol), timeout=12)
        if requested_source == "binance_futures":
            raise ValueError(f"Binance 永续标的必须使用 BINANCE: 前缀，例如 BINANCE:BTCUSDT")
        if requested_source == "binance_spot":
            if not is_crypto_symbol(symbol):
                raise ValueError("Binance Spot 仅支持加密现货代码，例如 BTC-USD")
            return await asyncio.wait_for(get_binance_spot_provider().get_quote(symbol), timeout=12)
        if requested_source == "auto" and is_crypto_symbol(symbol):
            return await asyncio.wait_for(get_binance_spot_provider().get_quote(symbol), timeout=12)
        if is_xauusd_symbol(symbol):
            if requested_source in {"hyperliquid", "yahoo", "binance_spot"}:
                raise ValueError("XAUUSD 第一阶段使用 Dukascopy 现货黄金数据")
            return await asyncio.wait_for(get_xau_provider().get_quote(symbol), timeout=12)
        if requested_source == "dukascopy":
            raise ValueError("Dukascopy 第一阶段仅支持 XAUUSD")
        explicit_hyperliquid = is_hyperliquid_symbol(symbol)
        if explicit_hyperliquid and requested_source == "yahoo":
            raise ValueError(f"{symbol} 是 Hyperliquid 专用代码，不能使用 Yahoo 数据源")

        # Plain stock symbols remain stocks. Perpetuals require either an HL:
        # product identity or an explicit Hyperliquid source selection.
        should_try_hyperliquid = explicit_hyperliquid or requested_source == "hyperliquid"
        aliases = (
            {}
            if explicit_hyperliquid or not should_try_hyperliquid
            else await _resolve_hyperliquid_stock_aliases([symbol])
        )
        provider_symbol = symbol if explicit_hyperliquid else aliases.get(symbol)
        if should_try_hyperliquid and provider_symbol:
            try:
                quote = await asyncio.wait_for(
                    get_hyperliquid_provider().get_quote(provider_symbol),
                    timeout=8,
                )
                return _hyperliquid_quote_for_request(symbol, provider_symbol, quote)
            except Exception:
                if explicit_hyperliquid or requested_source == "hyperliquid":
                    raise
        if requested_source == "hyperliquid":
            raise ValueError(f"Hyperliquid 未找到 {symbol} 对应的市场")

        quotes = await asyncio.wait_for(get_provider().get_quotes_batch([symbol]), timeout=7)
        if not quotes or quotes[0] is None:
            raise ValueError(f"无法获取 {symbol} 的报价数据")
        return _yahoo_quote_for_request(symbol, quotes[0])
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"获取 {symbol} 报价超时")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quotes")
async def get_quotes(symbols: str = Query(..., description="逗号分隔的标的代码，如 AAPL,MSFT,BTC-USD")):
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="至少需要一个标的代码")
    if len(symbol_list) > 30:
        raise HTTPException(status_code=400, detail="单次最多查询 30 个标的")

    results: list[Optional[dict]] = [None] * len(symbol_list)
    futures_positions = [
        (index, symbol) for index, symbol in enumerate(symbol_list)
        if is_binance_futures_symbol(symbol)
    ]
    spot_positions = [
        (index, symbol) for index, symbol in enumerate(symbol_list)
        if is_crypto_symbol(symbol)
    ]
    xau_positions = [
        (index, symbol) for index, symbol in enumerate(symbol_list)
        if is_xauusd_symbol(symbol) and not is_binance_futures_symbol(symbol)
    ]
    aliases: dict[str, str] = {}
    hyperliquid_positions = [
        (index, symbol, symbol)
        for index, symbol in enumerate(symbol_list)
        if is_hyperliquid_symbol(symbol)
        if not is_xauusd_symbol(symbol) and not is_binance_futures_symbol(symbol)
    ]
    yahoo_positions = [
        (index, symbol)
        for index, symbol in enumerate(symbol_list)
        if not is_hyperliquid_symbol(symbol) and symbol not in aliases
        and not is_xauusd_symbol(symbol) and not is_binance_futures_symbol(symbol)
        and not is_crypto_symbol(symbol)
    ]

    async def bounded(coro):
        # One degraded market-data source must not hold the entire dashboard.
        return await asyncio.wait_for(coro, timeout=8)

    tasks: list[tuple[str, list[tuple], object]] = []
    if futures_positions:
        tasks.append(("binance", futures_positions, bounded(
            get_binance_futures_provider().get_quotes_batch([symbol for _, symbol in futures_positions])
        )))
    if spot_positions:
        tasks.append(("binance", spot_positions, bounded(
            get_binance_spot_provider().get_quotes_batch([symbol for _, symbol in spot_positions])
        )))
    if xau_positions:
        tasks.append(("xau", xau_positions, bounded(asyncio.gather(*(get_xau_provider().get_quote(symbol) for _, symbol in xau_positions)))))
    if yahoo_positions:
        tasks.append(("yahoo", yahoo_positions, bounded(get_provider().get_quotes_batch([symbol for _, symbol in yahoo_positions]))))
    if hyperliquid_positions:
        tasks.append(("hyperliquid", hyperliquid_positions, bounded(get_hyperliquid_provider().get_quotes_batch([provider_symbol for _, _, provider_symbol in hyperliquid_positions]))))
    batches = await asyncio.gather(*(task for _, _, task in tasks), return_exceptions=True)
    yahoo_fallback: list[tuple[int, str]] = []
    for (kind, positions, _), batch in zip(tasks, batches):
        if isinstance(batch, Exception):
            if kind == "hyperliquid":
                for position in positions:
                    if not is_hyperliquid_symbol(position[1]):
                        yahoo_fallback.append((position[0], position[1]))
            continue
        for position_info, quote in zip(positions, batch):
            position, symbol = position_info[0], position_info[1]
            if kind == "hyperliquid":
                if quote is not None:
                    results[position] = _hyperliquid_quote_for_request(symbol, position_info[2], quote)
                elif not is_hyperliquid_symbol(symbol):
                    yahoo_fallback.append((position[0], position[1]))
            elif quote is not None:
                results[position] = _yahoo_quote_for_request(symbol, quote) if kind == "yahoo" else quote

    if yahoo_fallback:
        fallback_batch = await asyncio.gather(
            bounded(get_provider().get_quotes_batch([symbol for _, symbol in yahoo_fallback])),
            return_exceptions=True,
        )
        fallback_quotes = fallback_batch[0]
        if not isinstance(fallback_quotes, Exception):
            for (position, symbol), quote in zip(yahoo_fallback, fallback_quotes):
                if quote is not None:
                    results[position] = _yahoo_quote_for_request(symbol, quote)
    return {"quotes": results}


@router.get("/earnings")
async def get_earnings(symbols: str = Query(..., description="逗号分隔的标的代码")):
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="至少需要一个标的代码")
    if len(symbol_list) > 20:
        raise HTTPException(status_code=400, detail="单次最多查询 20 个标的")
    provider = get_provider()
    result = await provider.get_earnings_dates(symbol_list)
    return {"earnings": result}


@router.get("/candles", response_model=CandlesResponse)
async def get_candles(
    symbol: str = Query(..., description="标的代码"),
    timeframe: str = Query(default="1day", description="周期：15min/1h/4h/1day/1week"),
    outputsize: int = Query(default=500, ge=10, le=5000, description="K线数量"),
    before: Optional[int] = Query(default=None, description="Unix 时间戳，仅返回此时间之前的数据（用于动态加载历史）"),
    source: str = Query(default="auto", description="数据源：auto/hyperliquid/yahoo"),
):
    if timeframe not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"timeframe 必须是 {VALID_INTERVALS} 之一")
    requested_source = _normalize_source(source)
    try:
        explicit_binance_futures = is_binance_futures_symbol(symbol)
        if explicit_binance_futures:
            if requested_source not in {"auto", "binance_futures"}:
                raise ValueError(f"{symbol} 是 Binance USD-M 永续代码，数据源必须使用 Binance Futures")
            candles = await asyncio.wait_for(
                get_binance_futures_provider().get_time_series(
                    symbol, timeframe, outputsize, before=before,
                ),
                timeout=20,
            )
            return {
                "symbol": symbol,
                "provider_symbol": to_binance_futures_symbol(symbol),
                "source": "binance_futures",
                "timeframe": timeframe,
                "candles": candles,
            }
        if requested_source == "binance_futures":
            raise ValueError("Binance 永续标的必须使用 BINANCE: 前缀，例如 BINANCE:BTCUSDT")
        if requested_source == "binance_spot" and not is_crypto_symbol(symbol):
            raise ValueError("Binance Spot 仅支持加密现货代码，例如 BTC-USD")
        if is_xauusd_symbol(symbol):
            if requested_source in {"hyperliquid", "yahoo", "binance_spot"}:
                raise ValueError("XAUUSD 第一阶段使用 Dukascopy 现货黄金数据")
            candles = await asyncio.wait_for(
                get_xau_provider().get_time_series(symbol, timeframe, outputsize, before=before),
                timeout=20,
            )
            return {"symbol": "XAUUSD", "source": "dukascopy", "timeframe": timeframe, "candles": candles}
        if requested_source == "dukascopy":
            raise ValueError("Dukascopy 第一阶段仅支持 XAUUSD")
        explicit_hyperliquid = is_hyperliquid_symbol(symbol)
        if explicit_hyperliquid and requested_source == "yahoo":
            raise ValueError(f"{symbol} 是 Hyperliquid 专用代码，不能使用 Yahoo 数据源")

        should_try_hyperliquid = explicit_hyperliquid or requested_source == "hyperliquid"
        aliases = (
            {}
            if explicit_hyperliquid or not should_try_hyperliquid
            else await _resolve_hyperliquid_stock_aliases([symbol])
        )
        provider_symbol = symbol if explicit_hyperliquid else aliases.get(symbol)
        if should_try_hyperliquid and provider_symbol:
            try:
                candles = await asyncio.wait_for(
                    get_hyperliquid_provider().get_time_series(
                        provider_symbol, timeframe, outputsize, before=before,
                    ),
                    timeout=12,
                )
                return {
                    "symbol": symbol,
                    "provider_symbol": provider_symbol,
                    "source": "hyperliquid",
                    "timeframe": timeframe,
                    "candles": candles,
                }
            except Exception:
                if explicit_hyperliquid or requested_source == "hyperliquid":
                    raise
        if requested_source == "hyperliquid":
            raise ValueError(f"Hyperliquid 未找到 {symbol} 对应的市场")

        # Crypto with local parquet uses the hybrid local source; remaining
        # plain symbols fall back to Yahoo Finance.
        provider = (
            get_provider()
            if requested_source == "yahoo"
            else get_binance_spot_provider()
            if requested_source == "binance_spot"
            else pick_series_provider(symbol, timeframe)
        )
        candles = await asyncio.wait_for(
            provider.get_time_series(symbol, timeframe, outputsize, before=before),
            timeout=12,
        )
        return {
            "symbol": symbol,
            "source": getattr(provider, "source", "yahoo"),
            "timeframe": timeframe,
            "candles": candles,
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"获取 {symbol} K 线超时")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
