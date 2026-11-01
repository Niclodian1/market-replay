"""实验台 API（R5）。

- spec 可读可删；run 记录环境与指标；产物落 `data/experiments/<exp>/<run>/`
- 默认删除 = 归档（可恢复）；`?purge=true` 才物理删除（DB cascade + 删目录）
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.indicator_provider import get_registry
from app.core.market_data import read_local_bars
from app.db.session import get_db
from app.experiment_core import service
from app.experiment_core.spec import parse_spec
from app.models.models import Experiment, ExperimentRun, ExperimentTrade

router = APIRouter()


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    spec: dict[str, Any]
    note: str = ""


class ExperimentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    note: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validate_indicators(spec_raw: dict[str, Any]) -> None:
    registry = get_registry()
    spec = parse_spec(spec_raw)
    used = [item[0] for item in spec.entry_all + spec.entry_any] + [
        item[0] for item in spec.exit_indicators
    ]
    unknown = sorted({item for item in used if item not in registry.names})
    if unknown:
        raise HTTPException(status_code=400, detail=f"未注册的指标: {unknown}")


def _run_dict(run: ExperimentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "experiment_id": run.experiment_id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
        "git_sha": run.git_sha,
        "data_version": run.data_version,
        "metrics": run.metrics_json or {},
        "artifact_dir": run.artifact_dir,
    }


def _experiment_dict(exp: Experiment, *, runs: list[ExperimentRun] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": exp.id,
        "name": exp.name,
        "spec": exp.spec_json,
        "params_hash": exp.params_hash,
        "status": exp.status,
        "note": exp.note,
        "created_at": exp.created_at.isoformat(),
        "updated_at": exp.updated_at.isoformat(),
        "archived_at": exp.archived_at.isoformat() if exp.archived_at else None,
    }
    if runs is not None:
        payload["runs"] = [_run_dict(run) for run in runs]
    return payload


async def _get_experiment(db: AsyncSession, experiment_id: int) -> Experiment:
    exp = await db.get(Experiment, experiment_id)
    if exp is None or exp.deleted_at is not None:
        raise HTTPException(status_code=404, detail="实验不存在")
    return exp


@router.get("")
async def list_experiments(
    include_archived: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(Experiment).where(Experiment.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Experiment.archived_at.is_(None))
    stmt = stmt.order_by(Experiment.id.desc()).limit(limit)
    experiments = list((await db.execute(stmt)).scalars().all())
    last_runs = {}
    if experiments:
        run_rows = (
            await db.execute(
                select(ExperimentRun)
                .where(ExperimentRun.experiment_id.in_([exp.id for exp in experiments]))
                .order_by(ExperimentRun.id.desc())
            )
        ).scalars().all()
        for run in run_rows:
            last_runs.setdefault(run.experiment_id, run)
    return {
        "count": len(experiments),
        "experiments": [
            {
                **_experiment_dict(exp),
                "last_run": _run_dict(last_runs[exp.id]) if exp.id in last_runs else None,
            }
            for exp in experiments
        ],
    }


@router.post("")
async def create_experiment(
    payload: ExperimentCreate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        _validate_indicators(payload.spec)
        spec = parse_spec(payload.spec)
    except HTTPException:
        raise
    except Exception as exc:  # jsonschema / ValueError
        raise HTTPException(status_code=400, detail=f"spec 非法: {exc}") from exc

    exp = Experiment(
        name=payload.name,
        spec_json=spec.raw,
        params_hash=spec.spec_hash,
        status="draft",
        note=payload.note,
    )
    db.add(exp)
    await db.commit()
    await db.refresh(exp)
    return _experiment_dict(exp, runs=[])


@router.get("/{experiment_id}")
async def get_experiment(
    experiment_id: int, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    exp = await _get_experiment(db, experiment_id)
    runs = list(
        (
            await db.execute(
                select(ExperimentRun)
                .where(ExperimentRun.experiment_id == experiment_id)
                .order_by(ExperimentRun.id.desc())
            )
        ).scalars().all()
    )
    return _experiment_dict(exp, runs=runs)


@router.patch("/{experiment_id}")
async def update_experiment(
    experiment_id: int, payload: ExperimentUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    exp = await _get_experiment(db, experiment_id)
    if payload.name is not None:
        exp.name = payload.name
    if payload.note is not None:
        exp.note = payload.note
    await db.commit()
    await db.refresh(exp)
    return _experiment_dict(exp)


@router.post("/{experiment_id}/fork")
async def fork_experiment(
    experiment_id: int, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    exp = await _get_experiment(db, experiment_id)
    clone = Experiment(
        name=f"{exp.name} (copy)",
        spec_json=exp.spec_json,
        params_hash=exp.params_hash,
        status="draft",
        note=exp.note,
    )
    db.add(clone)
    await db.commit()
    await db.refresh(clone)
    return _experiment_dict(clone, runs=[])


@router.post("/{experiment_id}/runs")
async def run_experiment(
    experiment_id: int,
    background: Annotated[bool, Query(description="true 时立即返回 queued run，后台执行")] = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    exp = await _get_experiment(db, experiment_id)
    if background:
        run = await service.create_run(db, exp)
        service.spawn_background(exp.id, run.id)
        return _run_dict(run)
    run = await service.execute(db, exp)
    if run.status == "failed":
        raise HTTPException(status_code=422, detail=run.error or "实验执行失败")
    return _run_dict(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """取消 queued/running 的 run；已结束返回 409。"""
    run = await db.get(ExperimentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    run = await service.cancel(db, run)
    return _run_dict(run)


@router.get("/{experiment_id}/runs/{run_id}")
async def get_run(
    experiment_id: int, run_id: int, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    run = await db.get(ExperimentRun, run_id)
    if run is None or run.experiment_id != experiment_id:
        raise HTTPException(status_code=404, detail="运行不存在")
    return _run_dict(run)


@router.get("/{experiment_id}/runs/{run_id}/trades")
async def get_run_trades(
    experiment_id: int,
    run_id: int,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    run = await db.get(ExperimentRun, run_id)
    if run is None or run.experiment_id != experiment_id:
        raise HTTPException(status_code=404, detail="运行不存在")
    rows = list(
        (
            await db.execute(
                select(ExperimentTrade)
                .where(ExperimentTrade.run_id == run_id)
                .order_by(ExperimentTrade.entry_time)
                .limit(limit)
            )
        ).scalars().all()
    )
    return {
        "count": len(rows),
        "trades": [
            {
                "id": row.id,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                "direction": row.direction,
                "entry_time": row.entry_time,
                "entry_price": row.entry_price,
                "exit_time": row.exit_time,
                "exit_price": row.exit_price,
                "r_multiple": row.r_multiple,
                "pnl_pct": row.pnl_pct,
                "exit_reason": row.exit_reason,
                "entry_event_uid": row.entry_event_uid,
                "exit_event_uid": row.exit_event_uid,
            }
            for row in rows
        ],
    }


@router.get("/{experiment_id}/runs/{run_id}/chart")
async def get_run_chart(
    experiment_id: int,
    run_id: int,
    symbol: Annotated[str | None, Query()] = None,
    timeframe: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=100, le=20000)] = 1500,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    run = await db.get(ExperimentRun, run_id)
    if run is None or run.experiment_id != experiment_id:
        raise HTTPException(status_code=404, detail="运行不存在")
    exp = await _get_experiment(db, experiment_id)
    spec = parse_spec(exp.spec_json)
    symbol = (symbol or spec.symbols[0]).upper()
    timeframe = timeframe or spec.timeframes[0]

    trades = list(
        (
            await db.execute(
                select(ExperimentTrade)
                .where(ExperimentTrade.run_id == run_id)
                .order_by(ExperimentTrade.entry_time)
            )
        ).scalars().all()
    )
    bars = list(read_local_bars(symbol, timeframe))[:limit]
    markers: list[dict[str, Any]] = []
    for trade in trades:
        if trade.symbol.upper() != symbol or trade.timeframe != timeframe:
            continue
        markers.append(
            {
                "trade_id": trade.id,
                "kind": "entry",
                "time": trade.entry_time,
                "price": trade.entry_price,
                "direction": trade.direction,
                "r_multiple": trade.r_multiple,
            }
        )
        if trade.exit_time is not None:
            markers.append(
                {
                    "trade_id": trade.id,
                    "kind": "exit",
                    "time": trade.exit_time,
                    "price": trade.exit_price,
                    "direction": trade.direction,
                    "reason": trade.exit_reason,
                    "r_multiple": trade.r_multiple,
                }
            )
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": bars,
        "markers": markers,
        "metrics": run.metrics_json or {},
    }


@router.post("/{experiment_id}/restore")
async def restore_experiment(
    experiment_id: int, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    exp = await _get_experiment(db, experiment_id)
    exp.archived_at = None
    await db.commit()
    await db.refresh(exp)
    return {"id": exp.id, "restored": True}


@router.delete("/{experiment_id}")
async def delete_experiment(
    experiment_id: int,
    purge: Annotated[bool, Query()] = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    exp = await _get_experiment(db, experiment_id)
    if not purge:
        exp.archived_at = _utcnow()
        await db.commit()
        return {"id": experiment_id, "archived": True, "purge": False}

    run_ids = list(
        (
            await db.execute(
                select(ExperimentRun.id).where(ExperimentRun.experiment_id == experiment_id)
            )
        ).scalars().all()
    )
    if run_ids:
        await db.execute(delete(ExperimentTrade).where(ExperimentTrade.run_id.in_(run_ids)))
        await db.execute(delete(ExperimentRun).where(ExperimentRun.id.in_(run_ids)))
    await db.execute(delete(Experiment).where(Experiment.id == experiment_id))
    await db.commit()
    service.purge_artifacts(experiment_id)
    return {"id": experiment_id, "purged": True, "runs_deleted": len(run_ids)}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    run = await db.get(ExperimentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    experiment_id = run.experiment_id
    await db.execute(delete(ExperimentTrade).where(ExperimentTrade.run_id == run_id))
    await db.execute(delete(ExperimentRun).where(ExperimentRun.id == run_id))
    await db.commit()
    service.purge_artifacts(experiment_id, run_id)
    return {"id": run_id, "deleted": True}


@router.post("/cleanup")
async def cleanup(
    scope: Annotated[str, Query(pattern="^(failed|stale|orphan)$")] = "failed",
    older_than_days: Annotated[int, Query(ge=0, le=365)] = 7,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if scope == "failed":
        runs = list(
            (
                await db.execute(select(ExperimentRun).where(ExperimentRun.status == "failed"))
            ).scalars().all()
        )
        for run in runs:
            await db.execute(delete(ExperimentTrade).where(ExperimentTrade.run_id == run.id))
            await db.execute(delete(ExperimentRun).where(ExperimentRun.id == run.id))
            service.purge_artifacts(run.experiment_id, run.id)
        await db.commit()
        return {"scope": scope, "deleted": len(runs)}

    if scope == "stale":
        cutoff = _utcnow() - timedelta(days=older_than_days)
        experiments = list(
            (
                await db.execute(
                    select(Experiment).where(
                        Experiment.archived_at.is_not(None), Experiment.archived_at <= cutoff
                    )
                )
            ).scalars().all()
        )
        purged = 0
        for exp in experiments:
            run_ids = list(
                (
                    await db.execute(
                        select(ExperimentRun.id).where(ExperimentRun.experiment_id == exp.id)
                    )
                ).scalars().all()
            )
            if run_ids:
                await db.execute(
                    delete(ExperimentTrade).where(ExperimentTrade.run_id.in_(run_ids))
                )
                await db.execute(delete(ExperimentRun).where(ExperimentRun.id.in_(run_ids)))
            await db.execute(delete(Experiment).where(Experiment.id == exp.id))
            service.purge_artifacts(exp.id)
            purged += 1
        await db.commit()
        return {"scope": scope, "deleted": purged}

    # orphan：磁盘上有目录、DB 里没有对应 run
    rows = (await db.execute(select(ExperimentRun.id, ExperimentRun.experiment_id))).all()
    known = {(int(exp_id), int(run_id)) for run_id, exp_id in rows}
    removed = 0
    root = service.artifact_root()
    for exp_dir in root.iterdir():
        if not exp_dir.is_dir() or not exp_dir.name.isdigit():
            continue
        for run_dir in exp_dir.iterdir():
            if run_dir.is_dir() and run_dir.name.isdigit():
                if (int(exp_dir.name), int(run_dir.name)) not in known:
                    shutil.rmtree(run_dir, ignore_errors=True)
                    removed += 1
    return {"scope": scope, "deleted": removed}
