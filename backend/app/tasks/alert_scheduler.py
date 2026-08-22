"""Schedules legacy rules, reliable retries, and recurring scans."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ImportError:  # A lightweight built-in loop keeps the backend available.
    AsyncIOScheduler = None  # type: ignore[assignment,misc]

from app.core.alert_engine import run_alert_checks
from app.core.config import settings
from app.core.daily_report_service import (
    generate_and_deliver_daily_report,
    retry_daily_report_deliveries,
)
from app.core.monitor_engine import retry_monitor_deliveries, run_monitor_checks
from app.core import chanlun_service
from app.db.session import AsyncSessionLocal
from app.providers.yahoo_finance import YahooFinanceProvider

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler(timezone="UTC") if AsyncIOScheduler is not None else None
_provider = YahooFinanceProvider()
_fallback_task: asyncio.Task | None = None
_daily_report_startup_task: asyncio.Task | None = None
_chanlun_startup_task: asyncio.Task | None = None


async def _check_alerts_job() -> None:
    async with AsyncSessionLocal() as session:
        await run_alert_checks(session, _provider)
        await run_monitor_checks(session)


async def _retry_delivery_job() -> None:
    async with AsyncSessionLocal() as session:
        retried = await retry_monitor_deliveries(session)
        retried += await retry_daily_report_deliveries(session)
        retried += await chanlun_service.retry_chanlun_deliveries(session)
        if retried:
            logger.info("Retried %d notification deliveries", retried)


async def _daily_report_job() -> None:
    if not settings.DAILY_REPORT_ENABLED:
        return
    async with AsyncSessionLocal() as session:
        row = await generate_and_deliver_daily_report(session)
        logger.info(
            "Daily report %s completed (email=%s discord=%s)",
            row.report_date,
            row.email_status,
            row.discord_status,
        )


async def _daily_report_catchup_if_due() -> None:
    local_now = datetime.now(ZoneInfo(settings.ALERT_TIMEZONE))
    if (
        settings.DAILY_REPORT_ENABLED
        and (local_now.hour, local_now.minute)
        >= (settings.DAILY_REPORT_HOUR, settings.DAILY_REPORT_MINUTE)
    ):
        await _daily_report_job()


async def _chanlun_scan_job() -> None:
    if not settings.CHANLUN_ALERTS_ENABLED or not chanlun_service.chanlun_available():
        return
    symbols = [s.strip() for s in (settings.CHANLUN_SYMBOLS or "").split(",") if s.strip()]
    if not symbols:
        return
    async with AsyncSessionLocal() as session:
        results = await chanlun_service.scan_symbols(session, symbols)
        logger.info("Chanlun scan completed: %s", results)


async def _shadow_job() -> None:
    """M3B 影子运行时独立 job：默认关闭时零 DB/零网络（直接 return）。

    异常隔离：shadow 的任何异常只写 shadow heartbeat/日志，绝不冒泡影响
    _check_alerts_job / V1 提交 / 发送；shadow 模块禁止导入任何 sender。
    """
    from app.core.m3_shadow_runtime import run_production_cycle

    if not settings.M3_SHADOW_ENABLED:
        return
    try:
        async with AsyncSessionLocal() as session:
            result = await run_production_cycle(session)
        logger.info("M3 shadow cycle: %s", result)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("M3 shadow cycle crashed (isolated; V1 unaffected)")
        # run_production_cycle 已通过 run_cycle_safe 写 heartbeat；此处双保险
        # （主审整改 4：兜底写 heartbeat 后必须 commit，否则写不进去）
        try:
            from app.core.m3_shadow_runtime import ShadowCore, SqlShadowRepo
            from app.db.session import AsyncSessionLocal as _ASL
            import time as _time
            async with _ASL() as session:
                repo = SqlShadowRepo(session, core=ShadowCore.from_settings())
                core = ShadowCore.from_settings()
                if core.enabled:
                    await repo.touch_heartbeat(
                        int(_time.time()), ok=False, error="shadow job crashed",
                    )
                    await session.commit()
        except Exception:
            logger.exception("M3 shadow heartbeat error write failed")


def _fallback_due(now: datetime, symbol: str, hour: int, minute: int, weekdays_only: bool) -> bool:
    if weekdays_only and now.weekday() >= 5:
        return False
    return (now.hour, now.minute) >= (hour, minute)


async def _fallback_loop() -> None:
    last_report_date: str | None = None
    last_chanlun_scan: datetime | None = None
    iteration = 0
    while True:
        try:
            await _check_alerts_job()
            if iteration % 5 == 0:
                await _retry_delivery_job()
            report_now = datetime.now(ZoneInfo(settings.ALERT_TIMEZONE))
            report_key = report_now.date().isoformat()
            if (
                settings.DAILY_REPORT_ENABLED
                and last_report_date != report_key
                and (report_now.hour, report_now.minute)
                >= (settings.DAILY_REPORT_HOUR, settings.DAILY_REPORT_MINUTE)
            ):
                await _daily_report_job()
                last_report_date = report_key
            if settings.CHANLUN_ALERTS_ENABLED and (
                last_chanlun_scan is None
                or (datetime.now(timezone.utc) - last_chanlun_scan).total_seconds()
                >= settings.CHANLUN_SCAN_INTERVAL_MIN * 60
            ):
                await _chanlun_scan_job()
                last_chanlun_scan = datetime.now(timezone.utc)
            if settings.M3_SHADOW_ENABLED:
                await _shadow_job()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Fallback alert scheduler job failed")
        iteration += 1
        await asyncio.sleep(60)


def start_scheduler() -> None:
    global _fallback_task, _daily_report_startup_task, _chanlun_startup_task
    if _scheduler is not None:
        _scheduler.add_job(
            _check_alerts_job,
            trigger="interval",
            minutes=1,
            id="alert_check",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
        )
        _scheduler.add_job(
            _retry_delivery_job,
            trigger="interval",
            minutes=1,
            id="delivery_retry",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
        )
        if settings.CHANLUN_ALERTS_ENABLED:
            _scheduler.add_job(
                _chanlun_scan_job,
                trigger="interval",
                minutes=settings.CHANLUN_SCAN_INTERVAL_MIN,
                id="chanlun_scan",
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=3600,
            )
        # M3B 影子运行时独立 job：默认关闭时零 DB/零网络；异常隔离不冒泡
        _scheduler.add_job(
            _shadow_job,
            trigger="interval",
            minutes=max(1, int(settings.M3_SHADOW_INTERVAL_MIN)),
            id="m3_shadow",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        _scheduler.add_job(
            _daily_report_job,
            trigger="cron",
            hour=settings.DAILY_REPORT_HOUR,
            minute=settings.DAILY_REPORT_MINUTE,
            timezone=settings.ALERT_TIMEZONE,
            id="daily_market_report",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=6 * 3600,
        )
        _scheduler.start()
        logger.info("Alert scheduler started")
    else:
        if _fallback_task is None or _fallback_task.done():
            _fallback_task = asyncio.create_task(_fallback_loop(), name="alert-scheduler-fallback")
        logger.warning("APScheduler unavailable; using built-in market-close loop")

    # Catch up after downtime. Dedupe keys make this safe on every restart.
    if settings.CHANLUN_ALERTS_ENABLED and chanlun_service.chanlun_available() and (
        _chanlun_startup_task is None or _chanlun_startup_task.done()
    ):
        _chanlun_startup_task = asyncio.create_task(_chanlun_scan_job(), name="chanlun-startup-scan")
    if _daily_report_startup_task is None or _daily_report_startup_task.done():
        _daily_report_startup_task = asyncio.create_task(
            _daily_report_catchup_if_due(),
            name="daily-report-startup-catchup",
        )


def stop_scheduler() -> None:
    global _fallback_task, _daily_report_startup_task, _chanlun_startup_task
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Alert scheduler stopped")
    for task in (_fallback_task, _daily_report_startup_task, _chanlun_startup_task):
        if task is not None:
            task.cancel()
    _fallback_task = None
    _daily_report_startup_task = None
    _chanlun_startup_task = None
