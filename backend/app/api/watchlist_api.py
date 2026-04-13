from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.db.session import get_db
from app.models.models import Watchlist, User
from app.schemas.notes import WatchlistCreate, WatchlistItem, WatchlistResponse

router = APIRouter()

# 单用户应用：固定使用 user_id=1，启动时自动创建


async def _ensure_default_user(db: AsyncSession) -> int:
    result = await db.execute(select(User).where(User.id == 1))
    user = result.scalar_one_or_none()
    if not user:
        user = User(id=1, username="default", password_hash="unused")
        db.add(user)
        await db.commit()
    return 1


@router.get("", response_model=WatchlistResponse)
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    result = await db.execute(
        select(Watchlist)
        .where(Watchlist.user_id == user_id)
        .order_by(Watchlist.created_at.desc())
    )
    items = result.scalars().all()
    return WatchlistResponse(items=items)


@router.post("", response_model=WatchlistItem, status_code=201)
async def add_watchlist(body: WatchlistCreate, db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    # 避免重复
    existing = await db.execute(
        select(Watchlist).where(Watchlist.user_id == user_id, Watchlist.symbol == body.symbol)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Symbol already in watchlist")
    item = Watchlist(user_id=user_id, symbol=body.symbol, name=body.name, asset_type=body.asset_type)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204)
async def delete_watchlist(item_id: int, db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    result = await db.execute(
        select(Watchlist).where(Watchlist.id == item_id, Watchlist.user_id == user_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await db.delete(item)
    await db.commit()
