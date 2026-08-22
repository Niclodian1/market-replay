"""Manual daily-report preview/delivery command.

Examples:
  python -m app.tasks.daily_report_cli --dry-run
  python -m app.tasks.daily_report_cli --date 2026-07-24 --dry-run --html /tmp/report.html
  python -m app.tasks.daily_report_cli --send
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.daily_report_service import build_daily_report, generate_and_deliver_daily_report
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
import app.models  # noqa: F401


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成每日市场日报")
    parser.add_argument("--date", help="报告日期，格式 YYYY-MM-DD；默认今天")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不持久化、不投递")
    parser.add_argument("--send", action="store_true", help="持久化并投递")
    parser.add_argument("--html", type=Path, help="将 HTML 预览写入指定路径")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    if args.send and args.dry_run:
        raise SystemExit("--send 与 --dry-run 不能同时使用")
    if args.send and args.date:
        raise SystemExit("历史日期只支持预览，不能与 --send 同时使用")
    local_tz = ZoneInfo(settings.ALERT_TIMEZONE)
    now = (
        datetime.strptime(args.date, "%Y-%m-%d").replace(
            hour=settings.DAILY_REPORT_HOUR,
            minute=settings.DAILY_REPORT_MINUTE,
            tzinfo=local_tz,
        )
        if args.date else datetime.now(local_tz)
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        if args.send:
            row = await generate_and_deliver_daily_report(
                session, now=now, force_all_markets=True
            )
            print(
                f"{row.report_date} email={row.email_status} "
                f"discord={row.discord_status}"
            )
        else:
            preview = await build_daily_report(
                session, now=now, force_all_markets=True
            )
            print(preview.text)
            if args.html:
                args.html.write_text(preview.html, encoding="utf-8")
                print(f"\nHTML 已写入 {args.html}")
            await session.rollback()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run(_arguments()))
