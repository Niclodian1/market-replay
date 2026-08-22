"""M3B 影子运行时 CLI（运维入口）。

命令：
  status            输出汇总：心跳 / 30 天与 100 唯一 eligible 进度 / 事件计数
                    （绝不输出原始 payload/秘密）
  run-once          执行一轮完整 cycle（摄取新 4H 事件 + 标签到期更新，写库）
  dry-run-event ID  对既有 V1 alert_events id 模拟处理（决策 + 可选标签预览，
                    不写任何表；ID 必须是 4h 事件）

运行示例（backend 容器内）：
  python -m app.tasks.m3_shadow_cli status
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.m3_shadow_runtime import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_DELAY_SECONDS,
    HEARTBEAT_ID,
    ShadowCore,
    SourceEvent,
    SqlShadowRepo,
    compute_decision_record,
    compute_label_record,
    compute_progress,
    production_market_loader,
    run_cycle_safe,
    run_production_cycle,
)
from app.db.session import AsyncSessionLocal
from app.models.models import AlertEvent
from app.schemas.m3_shadow_runtime import (
    HeartbeatOut,
    ShadowCountsOut,
    ShadowEventRow,
    ShadowStatusOut,
    canonical_json,
    sanitize_error,
)

def _ts(epoch: int | None) -> str:
    if epoch is None:
        return "-"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


async def _status() -> int:
    core = ShadowCore.from_settings()
    async with AsyncSessionLocal() as session:
        repo = SqlShadowRepo(session, core=core)
        hb = await repo.get_heartbeat()
        counts = await repo.counts()
    now = int(__import__("time").time())
    progress = compute_progress(hb.started_at, counts, now) if hb else {
        "days_elapsed": 0.0, "days_remaining_30": 30.0,
        "unique_eligible_digests": 0, "unique_eligible_remaining": 100,
        "milestone_reached": False,
    }
    out = ShadowStatusOut(
        enabled=core.enabled,
        symbols=sorted(core.symbols),
        timeframe=core.timeframe,
        max_bars=core.max_bars,
        heartbeat=HeartbeatOut(
            exists=hb is not None,
            started_at=hb.started_at if hb else None,
            last_run_at=hb.last_run_at if hb else None,
            last_run_ok=hb.last_run_ok if hb else None,
            last_error=hb.last_error if hb else None,
        ),
        counts=ShadowCountsOut(
            total=counts.total, eligible=counts.eligible, rejected=counts.rejected,
            pending=counts.pending, labeled=counts.labeled, completed=counts.completed,
            failed=counts.failed,
            decision_failed=counts.decision_failed, label_failed=counts.label_failed,
            unique_eligible_digests=counts.unique_eligible_digests,
        ),
        days_elapsed=progress["days_elapsed"],
        days_remaining_30=progress["days_remaining_30"],
        unique_eligible_digests=progress["unique_eligible_digests"],
        unique_eligible_remaining=progress["unique_eligible_remaining"],
        milestone_reached=progress["milestone_reached"],
    )
    print("M3B 影子运行时状态")
    print(f"  enabled        : {out.enabled}")
    print(f"  allowlist      : {', '.join(out.symbols) or '(空)'}")
    print(f"  timeframe      : {out.timeframe}（M2 as_of = signal_time + 14400）")
    print(f"  max_bars       : {out.max_bars}")
    print("  心跳:")
    print(f"    started_at   : {_ts(out.heartbeat.started_at)}" + ("（未启用：DB 心跳尚未创建）" if not out.heartbeat.exists else ""))
    print(f"    last_run_at  : {_ts(out.heartbeat.last_run_at)}")
    print(f"    last_run_ok  : {out.heartbeat.last_run_ok}")
    if out.heartbeat.last_error:
        print(f"    last_error   : {out.heartbeat.last_error}")
    print("  事件计数:")
    c = out.counts
    print(f"    total={c.total} eligible={c.eligible} rejected={c.rejected} "
          f"pending={c.pending} labeled={c.labeled} completed={c.completed} "
          f"failed={c.failed}（decision={c.decision_failed} label={c.label_failed}）")
    print(f"    唯一 eligible decision_digest = {out.unique_eligible_digests}")
    print("  进度（M3B 完成门槛，本轮不伪造）:")
    print(f"    30 天       : 已 {out.days_elapsed:.1f} 天，剩 {out.days_remaining_30:.1f} 天（started_at 起算）")
    print(f"    ≥100 唯一 eligible：剩 {out.unique_eligible_remaining}")
    print(f"    milestone_reached = {out.milestone_reached}（两者同时满足才为 True）")
    return 0


async def _run_once() -> int:
    async with AsyncSessionLocal() as session:
        result = await run_production_cycle(session)
    print("M3B 影子运行时 run-once 完成")
    if not result.get("enabled", True):
        print("  enabled=False：本轮未执行任何操作（零 DB/零网络）")
        return 0
    if not result.get("ok", True):
        print(f"  cycle 失败（已隔离，仅写 heartbeat）: {result.get('error')}")
        return 2
    print(f"  候选事件={result.get('candidates_seen', 0)} 摄取={result.get('ingested', 0)} "
          f"标签更新={result.get('labeled', 0)} 失败={result.get('failed', 0)}")
    print(f"  行情取数调用（同周期已复用）={result.get('load_calls', 0)}")
    return 0


async def _dry_run_event(event_id: int) -> int:
    core = ShadowCore.from_settings()
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(AlertEvent).where(AlertEvent.id == event_id)
        )).scalar_one_or_none()
        if row is None:
            print(f"错误: alert_events 中不存在 id={event_id}", file=sys.stderr)
            return 2
        if row.timeframe != core.timeframe:
            print(f"错误: id={event_id} 的 timeframe={row.timeframe}，dry-run 只支持 {core.timeframe}", file=sys.stderr)
            return 2
        try:
            payload = json.loads(row.payload or "{}")
        except (TypeError, ValueError):
            payload = {}
        source = SourceEvent(
            dedupe_key=row.dedupe_key, id=int(row.id), symbol=row.symbol,
            timeframe=row.timeframe, strategy=row.strategy, event_type=row.event_type,
            direction=row.direction, severity=row.severity,
            signal_time=int(row.signal_time), reference_price=float(row.reference_price),
            payload=payload if isinstance(payload, dict) else {},
            created_at_epoch=int(row.created_at.timestamp()),
        )
        now = int(__import__("time").time())
        as_of = source.signal_time + core.step_4h
        candles_by_tf: dict[str, list[dict]] = {}
        for tf in ("1week", "1day", core.timeframe):
            candles_by_tf[tf] = await production_market_loader(
                source.symbol, tf, as_of, core.max_bars,
            )
        rec = compute_decision_record(core, source, candles_by_tf, as_of)
        print(f"dry-run-event id={event_id}（不写任何表）")
        print(f"  symbol={rec.symbol} signal_time={_ts(rec.signal_time)} as_of={_ts(as_of)}")
        print(f"  event_type={source.event_type} direction={source.direction} "
              f"reference={source.reference_price}")
        print(f"  eligible={rec.eligible} digest={rec.decision_digest}")
        reasons = json.loads(rec.reasons)
        print(f"  reason_codes   : {reasons.get('codes')}")
        print(f"  config_hash    : {json.loads(rec.config_hash)}")
        if not rec.eligible:
            print("  → 拒绝决策（不排 M3A 标签，仍会持久化）")
            return 0
        boundary = core.next_label_boundary(rec, now)
        if boundary is None:
            print(f"  → eligible；标签将在 5 窗口边界 {_ts(core.window_boundary(rec.signal_time, 5))} 到期后生成")
            return 0
        label_as_of, w = boundary
        bars4h = await production_market_loader(rec.symbol, core.timeframe, label_as_of, core.max_bars)
        label_json, status, mw, next_at = compute_label_record(core, rec, bars4h, label_as_of)
        label = json.loads(label_json)
        windows = {x["window"]: {
            "matured": x["matured"], "censor_code": x["censor_code"],
            "first_touch": x["first_touch"], "fake_breakout": x["fake_breakout"],
        } for x in label["windows"]}
        print(f"  label（预览）  : label_as_of={_ts(label_as_of)} status→{status} "
              f"maturity_window={mw} next_label_at={_ts(next_at)}")
        print(f"  label windows  : {json.dumps(windows, ensure_ascii=False)}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="M3B 影子运行时 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="输出汇总/心跳/30 天与 100 唯一 eligible 进度")
    sub.add_parser("run-once", help="执行一轮完整 cycle（写库）")
    dry = sub.add_parser("dry-run-event", help="对既有 4h AlertEvent 模拟处理（不写库）")
    dry.add_argument("event_id", type=int)
    args = parser.parse_args()
    if args.cmd == "status":
        return asyncio.run(_status())
    if args.cmd == "run-once":
        return asyncio.run(_run_once())
    if args.cmd == "dry-run-event":
        return asyncio.run(_dry_run_event(args.event_id))
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
