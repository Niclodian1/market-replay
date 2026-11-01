"""指标统计 API（R3）。

当前提供「即时统计」：传 K 线 → 事件 → 逐次结果 → 汇总。
不依赖数据库，因此可以先上线、先验证口径；后续 DB 版（indicator_events 回填）
复用同一套 ``label_events`` / ``summarize``，保证线上线下同口径。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_store
from app.core.indicator_provider import get_registry
from app.db.session import get_db
from app.indicator_core import UnknownIndicator
from app.indicator_core.outcomes import label_events
from app.indicator_core.stats import equity_curve, group_summaries, summarize

router = APIRouter()
events_router = APIRouter()


class CandleIn(BaseModel):
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class StatsRequest(BaseModel):
    symbol: str
    timeframe: str = "1day"
    candles: list[CandleIn] = Field(min_length=1)
    indicator_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    horizon: int = Field(default=20, ge=1, le=200)
    target_r: float = Field(default=2.0, gt=0, le=10)
    stop_lookback: int = Field(default=20, ge=2, le=200)
    atr_period: int = Field(default=14, ge=2, le=200)
    include_censored: bool = True
    min_sample: int = Field(default=30, ge=1)
    group_by: list[Literal["year", "direction", "symbol", "timeframe"]] = Field(
        default_factory=lambda: ["year", "direction"]
    )


@router.post("/preview")
def stats_preview(req: StatsRequest) -> dict[str, Any]:
    registry = get_registry()
    try:
        registry.get(req.indicator_id)
    except UnknownIndicator as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    bars = [candle.model_dump() for candle in req.candles]
    try:
        events = registry.run(
            req.indicator_id, bars, req.symbol, req.timeframe, req.params
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    outcomes = label_events(
        events,
        bars,
        horizon=req.horizon,
        target_r=req.target_r,
        stop_lookback=req.stop_lookback,
        atr_period=req.atr_period,
        include_censored=True,
    )
    visible = (
        outcomes
        if req.include_censored
        else [item for item in outcomes if not item.censored]
    )
    summary = summarize(visible, exclude_censored=True, min_sample=req.min_sample)

    groups: dict[str, list[dict[str, Any]]] = {}
    for name in req.group_by:
        if name == "year":
            groups["year"] = group_summaries(
                visible, lambda item: str(_year(item.entry_time))
            )
        elif name == "direction":
            groups["direction"] = group_summaries(visible, lambda item: item.direction)
        elif name == "symbol":
            groups["symbol"] = group_summaries(visible, lambda item: item.symbol)
        elif name == "timeframe":
            groups["timeframe"] = group_summaries(visible, lambda item: item.timeframe)

    return {
        "symbol": req.symbol.upper(),
        "timeframe": req.timeframe,
        "indicator_id": req.indicator_id,
        "params": req.params,
        "n_events": len(events),
        "n_outcomes": len(outcomes),
        "summary": summary.to_dict(),
        "groups": groups,
        "equity": equity_curve(visible),
        "outcomes": [item.to_dict() for item in outcomes],
    }


def _year(timestamp: int) -> int:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, timezone.utc).year


class BackfillRequest(BaseModel):
    symbol: str
    timeframe: str = "1day"
    indicator_ids: list[str] | None = None
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    label: bool = True
    horizon: int = Field(default=20, ge=1, le=200)
    target_r: float = Field(default=2.0, gt=0, le=10)
    stop_lookback: int = Field(default=20, ge=2, le=200)


@router.get("/indicators")
async def stats_overview(
    horizon: Annotated[int, Query(ge=1, le=200)] = 20,
    target_r: Annotated[float, Query(gt=0, le=10)] = 2.0,
    stop_lookback: Annotated[int, Query(ge=2, le=200)] = 20,
    min_sample: Annotated[int, Query(ge=1)] = 30,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """指标总览（DB 版）：每个指标一行，n / 胜率 / 期望 R / Wilson。"""
    rows = await event_store.load_outcomes(
        db, horizon=horizon, target_r=target_r, stop_lookback=stop_lookback
    )
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(row["indicator_id"], []).append(row)
    indicators = [
        {
            "indicator_id": indicator_id,
            **summarize(items, min_sample=min_sample).to_dict(),
        }
        for indicator_id, items in sorted(buckets.items())
    ]
    return {
        "horizon": horizon,
        "target_r": target_r,
        "stop_lookback": stop_lookback,
        "count": len(indicators),
        "indicators": indicators,
    }


@router.get("/indicators/{indicator_id}")
async def stats_detail(
    indicator_id: str,
    symbol: Annotated[str | None, Query()] = None,
    timeframe: Annotated[str | None, Query()] = None,
    horizon: Annotated[int, Query(ge=1, le=200)] = 20,
    target_r: Annotated[float, Query(gt=0, le=10)] = 2.0,
    stop_lookback: Annotated[int, Query(ge=2, le=200)] = 20,
    min_sample: Annotated[int, Query(ge=1)] = 30,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = await event_store.load_outcomes(
        db,
        indicator_id=indicator_id,
        symbol=symbol,
        timeframe=timeframe,
        horizon=horizon,
        target_r=target_r,
        stop_lookback=stop_lookback,
    )
    summary = summarize(rows, min_sample=min_sample)
    return {
        "indicator_id": indicator_id,
        "symbol": symbol.upper() if symbol else None,
        "timeframe": timeframe,
        "horizon": horizon,
        "target_r": target_r,
        "stop_lookback": stop_lookback,
        "summary": summary.to_dict(),
        "by_year": group_summaries(rows, lambda item: str(_year(item["entry_time"]))),
        "by_direction": group_summaries(rows, lambda item: str(item["direction"])),
        "equity": equity_curve(rows),
        "outcomes": rows[:limit],
    }


@events_router.get("")
async def list_events(
    symbol: Annotated[str | None, Query()] = None,
    timeframe: Annotated[str | None, Query()] = None,
    indicator_id: Annotated[str | None, Query()] = None,
    start: Annotated[int | None, Query()] = None,
    end: Annotated[int | None, Query()] = None,
    confirmed_before: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    with_outcomes: Annotated[bool, Query()] = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = await event_store.load_events(
        db,
        symbol=symbol,
        timeframe=timeframe,
        indicator_id=indicator_id,
        start=start,
        end=end,
        confirmed_before=confirmed_before,
        limit=limit,
    )
    payload = [event_store.event_to_dict(row) for row in rows]
    if with_outcomes and payload:
        outcome_map = await event_store.outcomes_by_uid(db, [row["uid"] for row in payload])
        for row in payload:
            row["outcome"] = outcome_map.get(row["uid"])
    return {"count": len(rows), "events": payload}


@events_router.post("/backfill")
async def backfill_events(
    req: BackfillRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await event_store.backfill(
        db,
        req.symbol,
        req.timeframe,
        indicator_ids=req.indicator_ids,
        params=req.params or None,
        label=req.label,
        horizon=req.horizon,
        target_r=req.target_r,
        stop_lookback=req.stop_lookback,
    )
