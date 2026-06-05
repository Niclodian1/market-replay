"""
本地 K 线数据服务：同步、加载、查询可用范围。
所有操作基于 market_candles 表（PostgreSQL）。

数据来源路由策略：
  加密货币 (BTC-USD 等) + 本地有 parquet → LocalCrypto（parquet 历史 + OKX 增量）
  加密货币 + 1h/4h（无本地）             → Binance/OKX（历史可达 8 年）
  其他所有情况                           → Yahoo Finance
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.models import MarketCandle
from app.providers.yahoo_finance import get_provider as get_yf_provider
from app.providers.binance import (
    BinanceProvider,
    is_crypto_symbol,
    get_full_history_days as binance_full_days,
)
from app.providers.local_crypto import LocalCryptoProvider, has_local_data

NewCandleCallback = Callable[[str, str, int], Awaitable[None]]
_new_candle_callbacks: list[NewCandleCallback] = []


def register_on_new_candle_callback(callback: NewCandleCallback) -> None:
    if callback not in _new_candle_callbacks:
        _new_candle_callbacks.append(callback)

# yfinance 各周期实际能提供的最大历史天数
_YF_FULL_HISTORY_DAYS: dict[str, int] = {
    "1h":    730,
    "4h":    730,
    "1day":  365 * 30,
    "1week": 365 * 30,
}


def _select_provider(symbol: str, timeframe: str):
    """
    根据标的和周期选择数据提供商。
    加密货币 + 本地有 parquet → LocalCryptoProvider（parquet 历史 + OKX 增量）；
    加密货币 + 1h/4h（无本地） → BinanceProvider；其余 → YahooFinanceProvider。
    返回 (provider, expected_full_history_days)。
    """
    if is_crypto_symbol(symbol):
        if has_local_data(symbol, timeframe):
            return LocalCryptoProvider(), binance_full_days(timeframe)
        if timeframe in ("1h", "4h"):
            return BinanceProvider(), binance_full_days(timeframe)
    return get_yf_provider(), _YF_FULL_HISTORY_DAYS.get(timeframe, 730)


async def get_local_range(
    symbol: str,
    timeframe: str,
    db: AsyncSession,
) -> tuple[int | None, int | None]:
    """返回本地已存储的 (min_time, max_time)，无数据返回 (None, None)。"""
    result = await db.execute(
        select(
            func.min(MarketCandle.time),
            func.max(MarketCandle.time),
        ).where(
            MarketCandle.symbol == symbol,
            MarketCandle.timeframe == timeframe,
        )
    )
    row = result.one()
    return row[0], row[1]


def _needs_full_resync(min_ts: int, expected_full_days: int) -> bool:
    """
    判断本地最早数据是否不完整。
    若本地最早时间晚于理论起点的 80%，则需要重拉全量。
    """
    threshold_ts = _time.time() - expected_full_days * 86400 * 0.8
    return min_ts > threshold_ts


async def sync_candles(
    symbol: str,
    timeframe: str,
    db: AsyncSession,
) -> int:
    """
    智能增量同步：
    - 无本地数据       → 全量拉取
    - 本地数据不完整   → 清空后重拉全量（自动补历史）
    - 本地数据完整     → 只拉最新时间戳之后的增量
    返回本次新写入的 K 线数量。
    """
    provider, full_history_days = _select_provider(symbol, timeframe)
    min_ts, last_ts = await get_local_range(symbol, timeframe, db)

    if last_ts is None:
        candles = await provider.get_full_history(symbol, timeframe)

    elif min_ts is not None and _needs_full_resync(min_ts, full_history_days):
        # 历史不完整（可能是旧版或换了更长历史的数据源），清空重拉
        await db.execute(
            delete(MarketCandle).where(
                MarketCandle.symbol == symbol,
                MarketCandle.timeframe == timeframe,
            )
        )
        await db.commit()
        candles = await provider.get_full_history(symbol, timeframe)

    else:
        candles = await provider.get_time_series_since(symbol, timeframe, last_ts)

    if not candles:
        return 0

    rows = [
        {
            "symbol":    symbol,
            "timeframe": timeframe,
            "time":      c["time"],
            "open":      c["open"],
            "high":      c["high"],
            "low":       c["low"],
            "close":     c["close"],
            "volume":    c["volume"],
        }
        for c in candles
    ]

    # asyncpg 单次 INSERT 参数上限 32767，每行 8 列，批次上限 4000 行
    BATCH_SIZE = 4000
    total_inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        stmt = pg_insert(MarketCandle).values(batch)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["symbol", "timeframe", "time"]
        )
        result = await db.execute(stmt)
        total_inserted += result.rowcount or len(batch)
    await db.commit()
    if total_inserted and _new_candle_callbacks:
        latest_ts = max(int(c["time"]) for c in candles)
        for callback in list(_new_candle_callbacks):
            await callback(symbol, timeframe, latest_ts)
    return total_inserted


async def load_candles(
    symbol: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
    db: AsyncSession,
) -> list[dict]:
    """从本地 DB 读取指定时间范围的 K 线，按时间升序返回。"""
    result = await db.execute(
        select(MarketCandle)
        .where(
            MarketCandle.symbol == symbol,
            MarketCandle.timeframe == timeframe,
            MarketCandle.time >= start_ts,
            MarketCandle.time <= end_ts,
        )
        .order_by(MarketCandle.time)
    )
    rows = result.scalars().all()
    return [
        {
            "time":   r.time,
            "open":   r.open,
            "high":   r.high,
            "low":    r.low,
            "close":  r.close,
            "volume": r.volume,
        }
        for r in rows
    ]
