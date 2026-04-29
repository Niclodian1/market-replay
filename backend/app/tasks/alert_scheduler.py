"""
APScheduler 后台任务：每 5 分钟检查一次活跃告警。
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db.session import AsyncSessionLocal
from app.providers.yahoo_finance import YahooFinanceProvider
from app.core.alert_engine import run_alert_checks

logger = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler(timezone="UTC")
_provider = YahooFinanceProvider()


async def _check_alerts_job() -> None:
    async with AsyncSessionLocal() as session:
        await run_alert_checks(session, _provider)


def start_scheduler() -> None:
    _scheduler.add_job(
        _check_alerts_job,
        trigger="interval",
        minutes=5,
        id="alert_check",
        replace_existing=True,
        misfire_grace_time=60,
    )
    _scheduler.start()
    logger.info("Alert scheduler started (interval: 5 min)")


def stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Alert scheduler stopped")
