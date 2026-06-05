from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.models import Opportunity
from app.schemas.opportunities import OpportunitiesResponse, OpportunityOut

router = APIRouter()


@router.get("", response_model=OpportunitiesResponse)
async def list_opportunities(
    status: str | None = None,
    min_score: float | None = None,
    symbol: str | None = None,
    limit: int = 30,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Opportunity).options(
        selectinload(Opportunity.edge_stat),
        selectinload(Opportunity.trade_plan),
    )
    count_stmt = select(func.count()).select_from(Opportunity)
    filters = []
    if status:
        filters.append(Opportunity.status == status)
    if min_score is not None:
        filters.append(Opportunity.score >= min_score)
    if symbol:
        filters.append(Opportunity.symbol == symbol.upper())
    for item in filters:
        stmt = stmt.where(item)
        count_stmt = count_stmt.where(item)
    stmt = stmt.order_by(Opportunity.created_at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 100))
    result = await db.execute(stmt)
    total_result = await db.execute(count_stmt)
    return OpportunitiesResponse(items=list(result.scalars().all()), total=total_result.scalar_one())


@router.get("/{opportunity_id}", response_model=OpportunityOut)
async def get_opportunity(opportunity_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Opportunity)
        .where(Opportunity.id == opportunity_id)
        .options(selectinload(Opportunity.edge_stat), selectinload(Opportunity.trade_plan))
    )
    opportunity = result.scalar_one_or_none()
    if opportunity is None:
        raise HTTPException(status_code=404, detail="机会不存在")
    return opportunity


@router.post("/{opportunity_id}/dismiss", response_model=OpportunityOut)
async def dismiss_opportunity(opportunity_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Opportunity)
        .where(Opportunity.id == opportunity_id)
        .options(selectinload(Opportunity.edge_stat), selectinload(Opportunity.trade_plan))
    )
    opportunity = result.scalar_one_or_none()
    if opportunity is None:
        raise HTTPException(status_code=404, detail="机会不存在")
    opportunity.status = "closed"
    opportunity.closed_at = datetime.utcnow()
    await db.commit()
    result = await db.execute(
        select(Opportunity)
        .where(Opportunity.id == opportunity_id)
        .options(selectinload(Opportunity.edge_stat), selectinload(Opportunity.trade_plan))
    )
    return result.scalar_one()
