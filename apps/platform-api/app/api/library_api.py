"""指标库 API（R6）：primitive / indicator / version 三层 + 晋升与删除语义。"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.library import service
from app.models.models import Indicator, IndicatorVersion, PromotionAudit

router = APIRouter()


class IndicatorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    primitive_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    timeframes: list[str] = Field(default_factory=lambda: ["1day"])
    description: str = ""


class VersionCreate(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    timeframes: list[str] | None = None
    note: str = ""


class PromoteRequest(BaseModel):
    experiment_run_ids: list[int] = Field(default_factory=list)
    actor: str = "local"
    note: str = ""
    min_trades: int = Field(default=30, ge=1)


class PushRequest(BaseModel):
    target_url: str
    indicator_ids: list[int] = Field(default_factory=list)
    token: str = ""
    actor: str = "local"


class PullRequest(BaseModel):
    source_url: str
    indicator_ids: list[int] = Field(default_factory=list)
    token: str = ""
    actor: str = "local"


class NoteRequest(BaseModel):
    reason: str = ""
    actor: str = "local"


@router.get("/primitives")
async def list_primitives(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    usage = await service.usage_counts(db)
    primitives = service.list_primitives(usage)
    return {"count": len(primitives), "primitives": primitives}


@router.get("/promoted-versions")
async def list_promoted_versions(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """告警订阅用：所有已发布、未下线、未删除的指标版本（扁平列表）。"""
    rows = await db.execute(
        select(IndicatorVersion, Indicator)
        .join(Indicator, Indicator.id == IndicatorVersion.indicator_id)
        .where(
            IndicatorVersion.promoted_at.isnot(None),
            IndicatorVersion.demoted_at.is_(None),
            Indicator.deleted_at.is_(None),
        )
        .order_by(Indicator.name, IndicatorVersion.version_no.desc())
    )
    versions = [
        {
            "id": version.id,
            "indicator_id": indicator.id,
            "indicator_name": indicator.name,
            "primitive_id": indicator.primitive_id,
            "version_no": version.version_no,
            "params": dict((version.spec_json or {}).get("params") or {}),
            "params_hash": version.params_hash,
            "promoted_at": version.promoted_at.isoformat() if version.promoted_at else None,
        }
        for version, indicator in rows.all()
    ]
    return {"count": len(versions), "versions": versions}


@router.get("/indicators")
async def list_indicators(
    include_archived: Annotated[bool, Query()] = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Indicator).where(Indicator.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Indicator.archived_at.is_(None))
    indicators = list((await db.execute(stmt.order_by(Indicator.id.desc()))).scalars().all())
    return {
        "count": len(indicators),
        "indicators": [service.indicator_dict(item) for item in indicators],
    }


@router.post("/indicators")
async def create_indicator(
    payload: IndicatorCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    indicator = await service.create_indicator(
        db,
        name=payload.name,
        primitive_id=payload.primitive_id,
        params=payload.params,
        timeframes=payload.timeframes,
        description=payload.description,
    )
    return await get_indicator(indicator.id, db=db)


@router.get("/indicators/{indicator_id}")
async def get_indicator(indicator_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    indicator = await service.get_indicator(db, indicator_id)
    versions = list(
        (
            await db.execute(
                select(IndicatorVersion)
                .where(IndicatorVersion.indicator_id == indicator_id)
                .order_by(IndicatorVersion.version_no)
            )
        ).scalars().all()
    )
    audit = list(
        (
            await db.execute(
                select(PromotionAudit)
                .where(PromotionAudit.indicator_id == indicator_id)
                .order_by(PromotionAudit.id)
            )
        ).scalars().all()
    )
    return service.indicator_dict(indicator, versions=versions, audit=audit)


@router.get("/indicators/{indicator_id}/impact")
async def indicator_impact(indicator_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """删除/下线前的影响面：事件数、订阅计划、实验引用。"""
    indicator = await service.get_indicator(db, indicator_id)
    return await service.indicator_impact(db, indicator)


@router.post("/indicators/{indicator_id}/versions")
async def add_version(
    indicator_id: int, payload: VersionCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    indicator = await service.get_indicator(db, indicator_id)
    version = await service.add_version(
        db,
        indicator,
        params=payload.params,
        timeframes=payload.timeframes,
        note=payload.note,
    )
    return service.version_dict(version)


@router.post("/indicators/{indicator_id}/versions/{version_id}/promote")
async def promote_version(
    indicator_id: int,
    version_id: int,
    payload: PromoteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    indicator = await service.get_indicator(db, indicator_id)
    version = await db.get(IndicatorVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return await service.promote(
        db,
        indicator,
        version,
        experiment_run_ids=payload.experiment_run_ids,
        actor=payload.actor,
        note=payload.note,
        min_trades=payload.min_trades,
    )


@router.post("/indicators/{indicator_id}/versions/{version_id}/demote")
async def demote_version(
    indicator_id: int, version_id: int, payload: NoteRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    indicator = await service.get_indicator(db, indicator_id)
    version = await db.get(IndicatorVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    impact = await service.indicator_impact(db, indicator)
    await service.demote(db, indicator, version, actor=payload.actor, note=payload.reason)
    return {"id": indicator_id, "demoted": version_id, "impact": impact}


@router.post("/indicators/{indicator_id}/archive")
async def archive_indicator(
    indicator_id: int, payload: NoteRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    indicator = await service.get_indicator(db, indicator_id)
    await service.archive(db, indicator, reason=payload.reason, actor=payload.actor)
    return {"id": indicator_id, "archived": True}


@router.post("/indicators/{indicator_id}/restore")
async def restore_indicator(
    indicator_id: int, payload: NoteRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    indicator = await service.get_indicator(db, indicator_id)
    await service.restore(db, indicator, actor=payload.actor)
    return {"id": indicator_id, "restored": True}


@router.delete("/indicators/{indicator_id}")
async def delete_indicator(
    indicator_id: int,
    purge: Annotated[bool, Query()] = False,
    reason: Annotated[str, Query(max_length=255)] = "",
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    indicator = await service.get_indicator(db, indicator_id)
    if purge:
        impact = await service.indicator_impact(db, indicator)
        await service.purge(db, indicator)
        return {"id": indicator_id, "purged": True, "impact": impact}
    await service.archive(db, indicator, reason=reason)
    return {"id": indicator_id, "archived": True, "purged": False}


@router.post("/push")
async def push_indicators(payload: PushRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await service.push_bundle(
        db,
        target_url=payload.target_url,
        indicator_ids=payload.indicator_ids or None,
        token=payload.token,
        actor=payload.actor,
    )


@router.post("/pull")
async def pull_indicators(payload: PullRequest, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await service.pull_bundle(
        db,
        source_url=payload.source_url,
        indicator_ids=payload.indicator_ids or None,
        token=payload.token,
        actor=payload.actor,
    )


@router.get("/export")
async def export_bundle(
    ids: Annotated[str | None, Query()] = None, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    indicator_ids = [int(item) for item in ids.split(",") if item.strip().isdigit()] if ids else None
    return await service.export_bundle(db, indicator_ids)


@router.post("/import")
async def import_bundle(
    bundle: dict[str, Any], db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    return await service.import_bundle(db, bundle)
