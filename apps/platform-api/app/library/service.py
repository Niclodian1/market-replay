"""指标库服务：创建、发版、晋升、归档、导出/导入。

三层概念：
- primitive：代码里的指标实现（``indicator_core`` 注册表），不可删；
- indicator：用户命名的一份配置（指向 primitive + params + 周期）；
- version：不可变快照；``promote`` 后成为正式环境使用的版本。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Sequence
from urllib.parse import urlencode, urlparse

import httpx

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.indicator_provider import get_registry
from app.indicator_core.spec import params_hash
from app.models.models import (
    Experiment,
    ExperimentRun,
    Indicator,
    IndicatorEvent,
    IndicatorVersion,
    MonitorPlan,
    PromotionAudit,
)

BUNDLE_SCHEMA = "indicator-bundle/1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _audit(
    db: AsyncSession,
    indicator_id: int,
    action: str,
    *,
    version_id: int | None = None,
    actor: str = "",
    note: str = "",
) -> None:
    db.add(
        PromotionAudit(
            indicator_id=indicator_id,
            version_id=version_id,
            action=action,
            actor=actor,
            note=note,
        )
    )


def _version_spec(primitive_id: str, params: dict[str, Any], timeframes: list[str], schedule: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "primitive_id": primitive_id,
        "params": dict(params),
        "timeframes": list(timeframes),
        "schedule": dict(schedule or {}),
    }


def version_dict(version: IndicatorVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "indicator_id": version.indicator_id,
        "version_no": version.version_no,
        "spec": version.spec_json,
        "params_hash": version.params_hash,
        "engine_version": version.engine_version,
        "evidence_run_ids": version.evidence_run_ids or [],
        "metrics_snapshot": version.metrics_snapshot_json or {},
        "promoted_at": version.promoted_at.isoformat() if version.promoted_at else None,
        "promoted_by": version.promoted_by,
        "demoted_at": version.demoted_at.isoformat() if version.demoted_at else None,
        "note": version.note,
        "created_at": version.created_at.isoformat(),
    }


def indicator_dict(
    indicator: Indicator,
    *,
    versions: Sequence[IndicatorVersion] | None = None,
    audit: Sequence[PromotionAudit] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": indicator.id,
        "name": indicator.name,
        "primitive_id": indicator.primitive_id,
        "status": indicator.status,
        "description": indicator.description,
        "current_version_id": indicator.current_version_id,
        "created_at": indicator.created_at.isoformat(),
        "updated_at": indicator.updated_at.isoformat(),
        "archived_at": indicator.archived_at.isoformat() if indicator.archived_at else None,
        "deleted_at": indicator.deleted_at.isoformat() if indicator.deleted_at else None,
        "deleted_reason": indicator.deleted_reason,
    }
    if versions is not None:
        payload["versions"] = [version_dict(version) for version in versions]
    if audit is not None:
        payload["audit"] = [
            {
                "id": row.id,
                "action": row.action,
                "version_id": row.version_id,
                "actor": row.actor,
                "note": row.note,
                "created_at": row.created_at.isoformat(),
            }
            for row in audit
        ]
    return payload


def list_primitives(usage: dict[str, int] | None = None) -> list[dict[str, Any]]:
    usage = usage or {}
    return [
        {
            **spec.to_dict(),
            "deletable": False,
            "indicator_count": usage.get(spec.id, 0),
        }
        for spec in get_registry().specs
    ]


async def get_indicator(db: AsyncSession, indicator_id: int) -> Indicator:
    indicator = await db.get(Indicator, indicator_id)
    if indicator is None:
        raise HTTPException(status_code=404, detail="指标不存在")
    return indicator


async def usage_counts(db: AsyncSession) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Indicator.primitive_id, func.count(Indicator.id))
            .where(Indicator.deleted_at.is_(None))
            .group_by(Indicator.primitive_id)
        )
    ).all()
    return {primitive_id: int(count) for primitive_id, count in rows}


async def create_indicator(
    db: AsyncSession,
    *,
    name: str,
    primitive_id: str,
    params: dict[str, Any],
    timeframes: list[str],
    description: str = "",
    actor: str = "",
) -> Indicator:
    registry = get_registry()
    if primitive_id not in registry.names:
        raise HTTPException(status_code=400, detail=f"未注册的 primitive: {primitive_id}")
    existing = await db.execute(select(Indicator).where(Indicator.name == name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="指标名已存在")

    indicator = Indicator(
        name=name,
        primitive_id=primitive_id,
        status="draft",
        description=description,
    )
    db.add(indicator)
    await db.flush()
    version = IndicatorVersion(
        indicator_id=indicator.id,
        version_no=1,
        spec_json=_version_spec(primitive_id, params, timeframes),
        params_hash=params_hash(params),
        engine_version="ev1",
        note="initial",
    )
    db.add(version)
    await db.flush()
    indicator.current_version_id = version.id
    _audit(db, indicator.id, "create", version_id=version.id, actor=actor)
    await db.commit()
    await db.refresh(indicator)
    return indicator


async def add_version(
    db: AsyncSession,
    indicator: Indicator,
    *,
    params: dict[str, Any],
    timeframes: list[str] | None = None,
    note: str = "",
    actor: str = "",
) -> IndicatorVersion:
    if indicator.deleted_at is not None:
        raise HTTPException(status_code=409, detail="指标已删除，请先恢复")
    versions = list(
        (
            await db.execute(
                select(IndicatorVersion).where(IndicatorVersion.indicator_id == indicator.id)
            )
        ).scalars().all()
    )
    next_no = max((version.version_no for version in versions), default=0) + 1
    spec = _version_spec(
        indicator.primitive_id,
        params,
        timeframes or (versions[-1].spec_json.get("timeframes", []) if versions else []),
    )
    version = IndicatorVersion(
        indicator_id=indicator.id,
        version_no=next_no,
        spec_json=spec,
        params_hash=params_hash(params),
        engine_version="ev1",
        note=note,
    )
    db.add(version)
    await db.flush()
    _audit(db, indicator.id, "version", version_id=version.id, actor=actor, note=note)
    await db.commit()
    await db.refresh(version)
    return version


def _run_uses_version(
    spec_raw: dict[str, Any], primitive_id: str, params: dict[str, Any]
) -> bool:
    entry = spec_raw.get("entry") or {}
    for block in ("all", "any"):
        for condition in entry.get(block) or []:
            if condition.get("indicator") == primitive_id and dict(
                condition.get("params") or {}
            ) == dict(params):
                return True
    return False


async def promote(
    db: AsyncSession,
    indicator: Indicator,
    version: IndicatorVersion,
    *,
    experiment_run_ids: list[int],
    actor: str = "",
    note: str = "",
    min_trades: int = 30,
) -> dict[str, Any]:
    if indicator.deleted_at is not None:
        raise HTTPException(status_code=409, detail="指标已删除，不能晋升")
    if version.indicator_id != indicator.id:
        raise HTTPException(status_code=400, detail="版本不属于该指标")
    if not experiment_run_ids:
        raise HTTPException(status_code=400, detail="晋升需要至少一次实验运行作为证据")

    runs = list(
        (
            await db.execute(
                select(ExperimentRun).where(
                    ExperimentRun.id.in_(experiment_run_ids),
                    ExperimentRun.status == "succeeded",
                )
            )
        ).scalars().all()
    )
    if not runs:
        raise HTTPException(status_code=400, detail="没有成功的实验运行")

    params = version.spec_json.get("params") or {}
    matched: list[ExperimentRun] = []
    for run in runs:
        experiment = await db.get(Experiment, run.experiment_id)
        if experiment and _run_uses_version(
            experiment.spec_json, indicator.primitive_id, params
        ):
            matched.append(run)
    if not matched:
        raise HTTPException(status_code=400, detail="证据运行没有使用该指标/参数，无法晋升")

    best = max(matched, key=lambda run: int((run.metrics_json or {}).get("n", 0)))
    metrics = best.metrics_json or {}
    if int(metrics.get("n", 0)) < min_trades:
        raise HTTPException(
            status_code=400,
            detail=f"样本不足：n={metrics.get('n', 0)} < {min_trades}，先补数据再晋升",
        )

    if indicator.current_version_id != version.id:
        indicator.current_version_id = version.id
    indicator.status = "promoted"
    version.promoted_at = _utcnow()
    version.promoted_by = actor or "local"
    version.evidence_run_ids = [run.id for run in matched]
    version.metrics_snapshot_json = metrics
    version.demoted_at = None
    _audit(
        db,
        indicator.id,
        "promote",
        version_id=version.id,
        actor=actor,
        note=note or f"n={metrics.get('n')}",
    )
    await db.commit()
    await db.refresh(indicator)
    await db.refresh(version)
    return {"indicator": indicator_dict(indicator, versions=[version]), "metrics": metrics}


async def demote(
    db: AsyncSession, indicator: Indicator, version: IndicatorVersion, *, actor: str = "", note: str = ""
) -> None:
    if version.indicator_id != indicator.id:
        raise HTTPException(status_code=400, detail="版本不属于该指标")
    version.demoted_at = _utcnow()
    if indicator.current_version_id == version.id:
        indicator.current_version_id = None
    indicator.status = "testing"
    _audit(db, indicator.id, "demote", version_id=version.id, actor=actor, note=note)
    await db.commit()


async def archive(db: AsyncSession, indicator: Indicator, *, reason: str = "", actor: str = "") -> None:
    indicator.archived_at = _utcnow()
    indicator.status = "archived"
    indicator.deleted_reason = reason or indicator.deleted_reason
    _audit(db, indicator.id, "archive", actor=actor, note=reason)
    await db.commit()


async def restore(db: AsyncSession, indicator: Indicator, *, actor: str = "") -> None:
    indicator.archived_at = None
    indicator.deleted_at = None
    indicator.status = "testing" if indicator.current_version_id else "draft"
    _audit(db, indicator.id, "restore", actor=actor)
    await db.commit()


async def purge(db: AsyncSession, indicator: Indicator, *, actor: str = "") -> None:
    """物理删除：指标行 + 版本 + 审计。历史事件（事实）保留，按 indicator_id 字符串可追溯。"""
    indicator_id = indicator.id
    await db.execute(
        PromotionAudit.__table__.delete().where(PromotionAudit.indicator_id == indicator_id)
    )
    await db.execute(
        IndicatorVersion.__table__.delete().where(IndicatorVersion.indicator_id == indicator_id)
    )
    await db.execute(Indicator.__table__.delete().where(Indicator.id == indicator_id))
    await db.commit()


def _spec_indicator_ids(spec: dict[str, Any] | None) -> set[str]:
    """从实验 spec 里取出所有引用的指标 id（entry.all / exit.any）。"""
    if not isinstance(spec, dict):
        return set()
    found: set[str] = set()
    for group, key in (("entry", "all"), ("exit", "any")):
        rules = (spec.get(group) or {}).get(key) or []
        for rule in rules:
            indicator = (rule or {}).get("indicator")
            if indicator:
                found.add(str(indicator))
    return found


async def indicator_impact(db: AsyncSession, indicator: Indicator) -> dict[str, Any]:
    """删除/下线前的影响面：事件、订阅计划、实验各引用多少。

    事件是历史事实（purge 也保留），这里只用于提示；计划/实验会随指标消失而失效。
    """
    versions = list(
        (
            await db.execute(
                select(IndicatorVersion)
                .where(IndicatorVersion.indicator_id == indicator.id)
                .order_by(IndicatorVersion.version_no)
            )
        ).scalars().all()
    )
    version_ids = {version.id for version in versions}

    events_by_version = int(
        await db.scalar(
            select(func.count())
            .select_from(IndicatorEvent)
            .where(IndicatorEvent.indicator_version_id.in_(version_ids))
        )
        or 0
    ) if version_ids else 0
    events_by_primitive = int(
        await db.scalar(
            select(func.count())
            .select_from(IndicatorEvent)
            .where(IndicatorEvent.indicator_id == indicator.primitive_id)
        )
        or 0
    )

    plan_ids: list[int] = []
    for plan in (await db.execute(select(MonitorPlan))).scalars().all():
        raw = plan.indicator_version_ids
        if not raw:
            continue
        try:
            subscribed = {int(item) for item in json.loads(raw)}
        except (TypeError, ValueError):
            continue
        if subscribed & version_ids:
            plan_ids.append(plan.id)

    experiment_ids: list[int] = []
    for experiment in (
        await db.execute(select(Experiment).where(Experiment.deleted_at.is_(None)))
    ).scalars().all():
        referenced = _spec_indicator_ids(experiment.spec_json)
        blob = json.dumps(experiment.spec_json or {}, ensure_ascii=False)
        if indicator.primitive_id in referenced or any(str(vid) in blob for vid in version_ids):
            experiment_ids.append(experiment.id)

    return {
        "indicator_id": indicator.id,
        "name": indicator.name,
        "primitive_id": indicator.primitive_id,
        "status": indicator.status,
        "versions": [
            {"id": version.id, "version_no": version.version_no, "params_hash": version.params_hash}
            for version in versions
        ],
        "events": {
            "by_version": events_by_version,
            "by_primitive": events_by_primitive,
            "total": events_by_version + events_by_primitive,
        },
        "plans": {"count": len(plan_ids), "ids": plan_ids},
        "experiments": {"count": len(experiment_ids), "ids": experiment_ids},
        "has_references": bool(plan_ids or experiment_ids),
    }


async def export_bundle(db: AsyncSession, indicator_ids: list[int] | None = None) -> dict[str, Any]:
    stmt = select(Indicator).where(Indicator.deleted_at.is_(None))
    if indicator_ids:
        stmt = stmt.where(Indicator.id.in_(indicator_ids))
    indicators = list((await db.execute(stmt)).scalars().all())
    payload: list[dict[str, Any]] = []
    for indicator in indicators:
        versions = list(
            (
                await db.execute(
                    select(IndicatorVersion)
                    .where(IndicatorVersion.indicator_id == indicator.id)
                    .order_by(IndicatorVersion.version_no)
                )
            ).scalars().all()
        )
        payload.append(
            {
                "name": indicator.name,
                "primitive_id": indicator.primitive_id,
                "description": indicator.description,
                "status": indicator.status,
                "versions": [
                    {
                        "version_no": version.version_no,
                        "spec": version.spec_json,
                        "params_hash": version.params_hash,
                        "note": version.note,
                    }
                    for version in versions
                ],
                "current_version_no": next(
                    (
                        version.version_no
                        for version in versions
                        if version.id == indicator.current_version_id
                    ),
                    None,
                ),
            }
        )
    return {
        "schema": BUNDLE_SCHEMA,
        "exported_at": _utcnow().isoformat(),
        "indicators": payload,
    }


async def import_bundle(
    db: AsyncSession, bundle: dict[str, Any], *, actor: str = ""
) -> dict[str, Any]:
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise HTTPException(status_code=400, detail=f"bundle schema 必须是 {BUNDLE_SCHEMA}")
    registry = get_registry()
    created, skipped = [], []
    for item in bundle.get("indicators") or []:
        primitive_id = item.get("primitive_id")
        if primitive_id not in registry.names:
            skipped.append({"name": item.get("name"), "reason": f"primitive 不存在: {primitive_id}"})
            continue
        name = item.get("name") or primitive_id
        exists = (await db.execute(select(Indicator).where(Indicator.name == name))).scalar_one_or_none()
        if exists is not None:
            skipped.append({"name": name, "reason": "同名指标已存在"})
            continue
        indicator = Indicator(
            name=name,
            primitive_id=primitive_id,
            status="draft",
            description=item.get("description") or "",
        )
        db.add(indicator)
        await db.flush()
        for version_item in item.get("versions") or []:
            spec = version_item.get("spec") or {}
            version = IndicatorVersion(
                indicator_id=indicator.id,
                version_no=int(version_item["version_no"]),
                spec_json=spec,
                params_hash=version_item.get("params_hash") or params_hash(spec.get("params") or {}),
                engine_version="ev1",
                note=version_item.get("note") or "",
            )
            db.add(version)
            await db.flush()
            if version_item.get("version_no") == item.get("current_version_no"):
                indicator.current_version_id = version.id
        _audit(db, indicator.id, "import", actor=actor)
        created.append({"id": indicator.id, "name": indicator.name})
    await db.commit()
    return {"created": created, "skipped": skipped}


def _remote_base(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="远端地址必须是 http(s)://host[:port]")
    return url.rstrip("/")


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"远端返回 {response.status_code}: {response.text[:200]}")
    return response.json()


async def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url, headers=headers)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"远端返回 {response.status_code}: {response.text[:200]}")
    return response.json()


async def push_bundle(
    db: AsyncSession,
    *,
    target_url: str,
    indicator_ids: list[int] | None = None,
    token: str = "",
    actor: str = "local",
) -> dict[str, Any]:
    """实验环境 → 正式环境：导出本地 bundle 并 POST 到远端 /api/library/import。"""
    target = _remote_base(target_url)
    bundle = await export_bundle(db, indicator_ids)
    if not bundle["indicators"]:
        raise HTTPException(status_code=400, detail="没有可推送的指标")
    remote = await _post_json(
        f"{target}/api/library/import", bundle, _auth_headers(token)
    )
    for item in bundle["indicators"]:
        indicator = (
            await db.execute(select(Indicator).where(Indicator.name == item["name"]))
        ).scalar_one_or_none()
        if indicator is not None:
            _audit(db, indicator.id, "push", actor=actor, note=target)
    await db.commit()
    return {"pushed": len(bundle["indicators"]), "target": target, "remote": remote}


async def pull_bundle(
    db: AsyncSession,
    *,
    source_url: str,
    indicator_ids: list[int] | None = None,
    token: str = "",
    actor: str = "local",
) -> dict[str, Any]:
    """正式环境 → 实验环境：从远端 /api/library/export 拉 bundle 并导入。"""
    source = _remote_base(source_url)
    query = ""
    if indicator_ids:
        query = "?" + urlencode({"ids": ",".join(str(item) for item in indicator_ids)})
    bundle = await _get_json(f"{source}/api/library/export{query}", _auth_headers(token))
    result = await import_bundle(db, bundle, actor=actor)
    return {"pulled": len(bundle.get("indicators") or []), "source": source, **result}
