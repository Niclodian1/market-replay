from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.edge_service import combo_hash, get_edge, get_thresholds_db, update_thresholds_db
from app.core.recommender import explain_plan
from app.db.session import get_db
from app.schemas.edge import ExplainRequest, ExplainResponse, Thresholds

router = APIRouter()


@router.post("/explain", response_model=ExplainResponse)
async def explain(body: ExplainRequest, db: AsyncSession = Depends(get_db)):
    hash_value = combo_hash(body.signals)
    edge = await get_edge(db, body.symbol, body.timeframe, hash_value, body.direction)
    result = explain_plan(
        symbol=body.symbol,
        timeframe=body.timeframe,
        direction=body.direction,
        signals=body.signals,
        entry=body.entry,
        atr=body.atr,
        edge=edge,
    )
    return result


@router.get("/thresholds", response_model=Thresholds)
async def read_thresholds(db: AsyncSession = Depends(get_db)):
    return await get_thresholds_db(db)


@router.put("/thresholds", response_model=Thresholds)
async def write_thresholds(body: Thresholds, db: AsyncSession = Depends(get_db)):
    return await update_thresholds_db(db, body.model_dump())
