"""实验服务：执行、落库、写产物、删除。

「实验 = spec + run + artifacts」：spec 在 DB，run 记录环境与指标，
产物落在唯一根目录 ``data/experiments/<experiment_id>/<run_id>/``（可整体删除）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.indicator_provider import get_registry
from app.core.market_data import read_local_bars
from app.experiment_core.runner import run_experiment
from app.experiment_core.spec import ExperimentSpec, parse_spec
from app.models.models import Experiment, ExperimentRun, ExperimentTrade

REPO_ROOT = Path(__file__).resolve().parents[4]


def artifact_root() -> Path:
    override = os.environ.get("EXPERIMENT_ARTIFACT_ROOT")
    root = Path(override) if override else REPO_ROOT / "data" / "experiments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_dir(experiment_id: int, run_id: int) -> Path:
    return artifact_root() / str(int(experiment_id)) / str(int(run_id))


def _epoch(value: str | None) -> int | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def load_data(spec: ExperimentSpec) -> dict[tuple[str, str], list[dict[str, Any]]]:
    start, end = _epoch(spec.raw.get("period", {}).get("from")), _epoch(
        spec.raw.get("period", {}).get("to")
    )
    data: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for symbol in spec.symbols:
        for timeframe in spec.timeframes:
            bars = [
                bar
                for bar in read_local_bars(symbol, timeframe)
                if (start is None or bar["time"] >= start) and (end is None or bar["time"] <= end)
            ]
            data[(symbol, timeframe)] = bars
    return data


# 后台运行的 asyncio.Task（run_id -> task），取消时用来中断
_RUN_TASKS: dict[int, asyncio.Task] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_run(db: AsyncSession, experiment: Experiment) -> ExperimentRun:
    """先建一条 queued run 并返回，便于后台执行时立刻拿到 run_id。"""
    run = ExperimentRun(experiment_id=experiment.id, status="queued")
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


def spawn_background(experiment_id: int, run_id: int) -> None:
    """把执行放到 asyncio 任务里（进程内队列）；同一 run 只会有一个任务。"""

    async def _runner() -> None:
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            experiment = await session.get(Experiment, experiment_id)
            run = await session.get(ExperimentRun, run_id)
            if experiment is None or run is None:
                return
            await execute(session, experiment, run=run)

    task = asyncio.create_task(_runner())
    _RUN_TASKS[run_id] = task
    task.add_done_callback(lambda _task: _RUN_TASKS.pop(run_id, None))


async def cancel(db: AsyncSession, run: ExperimentRun) -> ExperimentRun:
    """取消排队中/运行中的 run；已结束的返回 409。"""
    if run.status not in ("queued", "running"):
        raise HTTPException(status_code=409, detail="运行已结束，无法取消")
    run.status = "cancelled"
    run.error = "用户取消"
    run.finished_at = _now()
    await db.commit()
    task = _RUN_TASKS.get(run.id)
    if task is not None and not task.done():
        task.cancel()
    await db.refresh(run)
    return run


async def execute(
    db: AsyncSession,
    experiment: Experiment,
    *,
    run: ExperimentRun | None = None,
    git_sha: str = "",
    data_version: str = "",
    timeout_seconds: int | None = None,
) -> ExperimentRun:
    spec = parse_spec(experiment.spec_json)
    if run is None:
        run = ExperimentRun(
            experiment_id=experiment.id,
            status="running",
            started_at=_now(),
            git_sha=git_sha,
            data_version=data_version,
        )
        db.add(run)
        await db.flush()
    else:
        run.status = "running"
        run.started_at = run.started_at or _now()
        run.git_sha = git_sha or run.git_sha
        run.data_version = data_version or run.data_version

    timeout = timeout_seconds if timeout_seconds is not None else settings.EXPERIMENT_RUN_TIMEOUT_SEC
    try:
        data = load_data(spec)
        # 引擎是同步 CPU/IO 代码：丢到线程里跑，主事件循环不被阻塞，超时也能兜住
        result = await asyncio.wait_for(
            asyncio.to_thread(run_experiment, spec, data, get_registry()),
            timeout=max(1, int(timeout)),
        )
        run.status = "succeeded"
        run.metrics_json = result.metrics
        run.finished_at = _now()

        directory = run_dir(experiment.id, run.id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "spec.json").write_text(
            json.dumps(spec.raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (directory / "metrics.json").write_text(
            json.dumps(result.metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (directory / "trades.json").write_text(
            json.dumps([trade.to_dict() for trade in result.trades], ensure_ascii=False),
            encoding="utf-8",
        )
        (directory / "equity.json").write_text(
            json.dumps(result.equity, ensure_ascii=False), encoding="utf-8"
        )
        run.artifact_dir = str(directory.relative_to(artifact_root().parent))

        for trade in result.trades:
            db.add(
                ExperimentTrade(
                    run_id=run.id,
                    symbol=trade.symbol,
                    timeframe=trade.timeframe,
                    direction=trade.direction,
                    entry_time=trade.entry_time,
                    entry_price=trade.entry_price,
                    exit_time=trade.exit_time,
                    exit_price=trade.exit_price,
                    r_multiple=trade.r_multiple,
                    pnl_pct=trade.pnl * 100,
                    exit_reason=trade.exit_reason,
                    entry_event_uid=trade.entry_event_uid or None,
                    exit_event_uid=trade.exit_event_uid or None,
                )
            )
        experiment.status = "tested"
    except asyncio.TimeoutError:
        run.status = "failed"
        run.error = f"运行超时(>{max(1, int(timeout))}s)"
        run.finished_at = _now()
    except asyncio.CancelledError:
        run.status = "cancelled"
        run.error = run.error or "已取消"
        run.finished_at = _now()
        await db.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - 失败要落库，方便排查
        run.status = "failed"
        run.error = str(exc)[:2000]
        run.finished_at = _now()
    await db.commit()
    await db.refresh(run)
    return run


async def latest_run(db: AsyncSession, experiment_id: int) -> ExperimentRun | None:
    result = await db.execute(
        select(ExperimentRun)
        .where(ExperimentRun.experiment_id == experiment_id)
        .order_by(ExperimentRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def purge_artifacts(experiment_id: int, run_id: int | None = None) -> None:
    """只删 ``data/experiments`` 下的目录，路径由整数 id 拼成，不接受用户输入。"""
    root = artifact_root().resolve()
    target = (run_dir(experiment_id, run_id) if run_id is not None else root / str(int(experiment_id))).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"拒绝删除实验目录之外的路径: {target}")
    if target.exists():
        shutil.rmtree(target)

