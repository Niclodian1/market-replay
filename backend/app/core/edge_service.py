import hashlib
import json
import math
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import EdgeStat, RecommenderThreshold


DEFAULT_THRESHOLDS = {
    "min_winrate": 0.55,
    "min_sample": 30,
    "min_score": 0.6,
    "max_open_per_symbol": 3,
}

_thresholds = DEFAULT_THRESHOLDS.copy()


def combo_hash(signals: Iterable[str], combine_mode: str = "AND") -> str:
    payload = {"signals": sorted(set(signals)), "mode": combine_mode.upper()}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def wilson_lower_bound(wins: int, sample_size: int, z: float = 1.96) -> float:
    if sample_size <= 0:
        return 0.0
    phat = wins / sample_size
    denom = 1 + z * z / sample_size
    centre = phat + z * z / (2 * sample_size)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * sample_size)) / sample_size)
    return max(0.0, (centre - margin) / denom)


def get_thresholds() -> dict[str, float | int]:
    return _thresholds.copy()


def update_thresholds(values: dict[str, Any]) -> dict[str, float | int]:
    for key, default in DEFAULT_THRESHOLDS.items():
        if key in values and values[key] is not None:
            _thresholds[key] = type(default)(values[key])
    _thresholds["min_winrate"] = min(max(float(_thresholds["min_winrate"]), 0.0), 1.0)
    _thresholds["min_score"] = min(max(float(_thresholds["min_score"]), 0.0), 1.0)
    _thresholds["min_sample"] = max(int(_thresholds["min_sample"]), 1)
    _thresholds["max_open_per_symbol"] = max(int(_thresholds["max_open_per_symbol"]), 1)
    return get_thresholds()


async def get_thresholds_db(db: AsyncSession) -> dict[str, float | int]:
    result = await db.execute(select(RecommenderThreshold).where(RecommenderThreshold.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = RecommenderThreshold(id=1, **DEFAULT_THRESHOLDS)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    values = {
        "min_winrate": row.min_winrate,
        "min_sample": row.min_sample,
        "min_score": row.min_score,
        "max_open_per_symbol": row.max_open_per_symbol,
    }
    update_thresholds(values)
    return values


async def update_thresholds_db(db: AsyncSession, values: dict[str, Any]) -> dict[str, float | int]:
    normalized = update_thresholds(values)
    result = await db.execute(select(RecommenderThreshold).where(RecommenderThreshold.id == 1))
    row = result.scalar_one_or_none()
    if row is None:
        row = RecommenderThreshold(id=1)
        db.add(row)
    row.min_winrate = float(normalized["min_winrate"])
    row.min_sample = int(normalized["min_sample"])
    row.min_score = float(normalized["min_score"])
    row.max_open_per_symbol = int(normalized["max_open_per_symbol"])
    await db.commit()
    await db.refresh(row)
    return await get_thresholds_db(db)


async def get_edge(
    db: AsyncSession,
    symbol: str,
    timeframe: str,
    hash_value: str,
    direction: str,
) -> Optional[EdgeStat]:
    result = await db.execute(
        select(EdgeStat).where(
            EdgeStat.symbol == symbol.upper(),
            EdgeStat.timeframe == timeframe,
            EdgeStat.combo_hash == hash_value,
            EdgeStat.direction == direction,
        )
    )
    return result.scalar_one_or_none()


async def upsert_edge_stat(
    db: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    signals: list[str],
    direction: str,
    sample_size: int,
    wins: int,
    avg_r: float,
    expectancy: float,
    combine_mode: str = "AND",
    window_start_ts: int = 0,
    window_end_ts: int = 0,
) -> EdgeStat:
    hash_value = combo_hash(signals, combine_mode)
    edge = await get_edge(db, symbol, timeframe, hash_value, direction)
    win_rate = wins / sample_size if sample_size > 0 else 0.0
    wilson = wilson_lower_bound(wins, sample_size)
    if edge is None:
        edge = EdgeStat(
            symbol=symbol.upper(),
            timeframe=timeframe,
            combo_hash=hash_value,
            combo_spec={"entry": sorted(signals), "exit": [], "mode": combine_mode.upper()},
            direction=direction,
        )
        db.add(edge)
    edge.sample_size = sample_size
    edge.wins = wins
    edge.win_rate = win_rate
    edge.avg_r = avg_r
    edge.expectancy = expectancy
    edge.sharpe_like = expectancy / max(abs(avg_r), 1e-9)
    edge.wilson_lower = wilson
    edge.window_start_ts = window_start_ts
    edge.window_end_ts = window_end_ts
    await db.commit()
    await db.refresh(edge)
    return edge
