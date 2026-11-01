#!/usr/bin/env python3
"""指标告警扫描（cron 可用）：算事件 → 附历史统计 → 推送飞书/Discord/邮件。

示例：
    python scripts/indicator_alert_cli.py --symbol BTC-USD --timeframe 4h \
        --channel 1 --since-hours 8 --base-url https://your-frontend

先用 ``python scripts/backfill_indicator_events.py`` 回填历史，统计摘要才有样本。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "platform-api"))

from app.core import event_store  # noqa: E402
from app.core.indicator_alert import event_deep_link, send_indicator_digest  # noqa: E402
from app.core.indicator_provider import get_registry  # noqa: E402
from app.core.market_data import read_local_bars  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.indicator_core.stats import summarize  # noqa: E402
from app.models.models import NotificationChannel  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    bars = list(read_local_bars(args.symbol, args.timeframe))
    if not bars:
        print(f"没有本地行情：{args.symbol} {args.timeframe}", file=sys.stderr)
        return 2
    registry = get_registry()
    wanted = args.indicators or registry.names
    unknown = [item for item in wanted if item not in registry.names]
    if unknown:
        print(f"未注册指标：{unknown}", file=sys.stderr)
        return 2

    events = registry.run_many(wanted, bars, args.symbol, args.timeframe, {})
    since = int(time.time()) - args.since_hours * 3600
    events = [event for event in events if event.confirmed_at >= since][-args.limit :]
    if not events:
        print("最近窗口没有触发")
        return 0

    async with AsyncSessionLocal() as db:
        outcomes = await event_store.load_outcomes(
            db, symbol=args.symbol, timeframe=args.timeframe
        )
        by_indicator: dict[str, list[dict]] = {}
        for row in outcomes:
            by_indicator.setdefault(row["indicator_id"], []).append(row)
        summaries = {key: summarize(rows).to_dict() for key, rows in by_indicator.items()}

        print(f"{len(events)} 个事件：")
        for event in events:
            print(f"  {event.indicator_id} {event.direction} @ {event.price} "
                  f"{event_deep_link(args.base_url, event)}")
        if args.dry_run:
            return 0

        channel = await db.get(NotificationChannel, args.channel)
        if channel is None or not channel.is_active:
            print("通知渠道不存在或未启用", file=sys.stderr)
            return 2
        sent = await send_indicator_digest(
            channel,
            symbol=args.symbol,
            timeframe=args.timeframe,
            events=events,
            summaries=summaries,
            base_url=args.base_url,
        )
        print("已发送" if sent else "发送失败")
        return 0 if sent else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--channel", type=int, required=False, default=0)
    parser.add_argument("--indicators", nargs="*", default=None)
    parser.add_argument("--since-hours", type=int, default=8)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--base-url", default="http://localhost:3000")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.channel:
        parser.error("发送时必须提供 --channel")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
