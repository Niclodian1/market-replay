from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.db.session import get_db
from app.models.models import ReplayNote, User
from app.schemas.notes import ReplayNoteCreate, ReplayNoteOut, ReplayNotesResponse

router = APIRouter()


def _parse_iso(s: str) -> datetime:
    """解析 ISO 字符串为不带时区的 UTC datetime（存入 DB）。"""
    if not s:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


async def _ensure_default_user(db: AsyncSession) -> int:
    result = await db.execute(select(User).where(User.id == 1))
    user = result.scalar_one_or_none()
    if not user:
        user = User(id=1, username="default", password_hash="unused")
        db.add(user)
        await db.commit()
    return 1


@router.get("", response_model=ReplayNotesResponse)
async def list_notes(
    symbol: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    user_id = await _ensure_default_user(db)
    q = select(ReplayNote).where(ReplayNote.user_id == user_id)
    if symbol:
        q = q.where(ReplayNote.symbol == symbol)
    q = q.order_by(ReplayNote.created_at.desc()).offset(skip).limit(limit)

    count_q = select(func.count()).select_from(ReplayNote).where(ReplayNote.user_id == user_id)
    if symbol:
        count_q = count_q.where(ReplayNote.symbol == symbol)

    result = await db.execute(q)
    total_result = await db.execute(count_q)
    items = result.scalars().all()
    total = total_result.scalar_one()
    return ReplayNotesResponse(items=items, total=total)


@router.post("", response_model=ReplayNoteOut, status_code=201)
async def create_note(body: ReplayNoteCreate, db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    note = ReplayNote(
        user_id=user_id,
        symbol=body.symbol,
        timeframe=body.timeframe,
        replay_start_time=_parse_iso(body.replay_start_time),
        note_time=_parse_iso(body.note_time),
        trend_view=body.trend_view,
        support_level=body.support_level,
        resistance_level=body.resistance_level,
        entry_idea=body.entry_idea,
        stop_loss=body.stop_loss,
        take_profit=body.take_profit,
        summary=body.summary,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


@router.get("/{note_id}", response_model=ReplayNoteOut)
async def get_note(note_id: int, db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    result = await db.execute(
        select(ReplayNote).where(ReplayNote.id == note_id, ReplayNote.user_id == user_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.delete("/{note_id}", status_code=204)
async def delete_note(note_id: int, db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    result = await db.execute(
        select(ReplayNote).where(ReplayNote.id == note_id, ReplayNote.user_id == user_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    await db.delete(note)
    await db.commit()
