"""Orchestration, persistence and retry logic for the 08:00 market report."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.daily_report_analysis import (
    aggregate_hot_products,
    analyze_instrument,
    detect_report_opportunities,
    render_html,
    render_text,
)
from app.core.daily_report_delivery import (
    email_is_configured,
    environment_email_config,
    send_report_discord,
    send_report_email,
)
from app.core.daily_report_sources import DailyReportSources
from app.core.candle_utils import completed_daily_candles
from app.core.notification_channels import decode_channel_config
from app.models.models import DailyReport, HotProductSnapshot, NotificationChannel, Watchlist
from app.schemas.daily_report import (
    CalendarEventItem,
    ChanlunChapter,
    DailyReportPayload,
    DailyReportPreview,
    InstrumentAnalysis,
    MarketSection,
    OpportunityItem,
)

logger = logging.getLogger(__name__)
MAX_DELIVERY_ATTEMPTS = 6


@dataclass
class InstrumentSpec:
    symbol: str
    name: str
    tags: list[str] = field(default_factory=list)
    opportunity_eligible: bool = False
    volume_applicable: bool = True


@dataclass(frozen=True)
class MarketSpec:
    key: str
    title: str
    session_timezone: str
    fixed: tuple[InstrumentSpec, ...]


MARKETS = (
    MarketSpec(
        "crypto", "加密货币", "UTC",
        (InstrumentSpec("BTC-USD", "比特币", ["市场基准"], True),),
    ),
    MarketSpec(
        "a_share", "A股交易前瞻", "Asia/Shanghai",
        (
            InstrumentSpec("000001.SS", "上证指数", ["指数"], False, False),
            InstrumentSpec("399006.SZ", "创业板指数", ["指数"], False, False),
            InstrumentSpec("000688.SS", "科创50", ["指数"], False, False),
        ),
    ),
    MarketSpec(
        "us_stock", "美股", "America/New_York",
        (
            InstrumentSpec("^IXIC", "纳斯达克综合指数", ["指数"], False, False),
            InstrumentSpec("^GSPC", "标普500", ["指数"], False, False),
        ),
    ),
    MarketSpec(
        "commodity", "商品", "America/New_York",
        (
            InstrumentSpec("GC=F", "黄金", ["商品"]),
            InstrumentSpec("SI=F", "白银", ["商品"]),
            InstrumentSpec("CL=F", "WTI原油", ["商品"]),
        ),
    ),
)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _daily_report_discord_webhook(session: AsyncSession) -> str | None:
    configured = str(settings.DISCORD_WEBHOOK_URL or "").strip()
    if configured:
        return configured
    result = await session.execute(
        select(NotificationChannel)
        .where(
            NotificationChannel.kind == "discord",
            NotificationChannel.is_active.is_(True),
        )
        .order_by(NotificationChannel.is_default.desc(), NotificationChannel.id)
        .limit(1)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        return None
    return str(decode_channel_config(channel.config).get("webhook_url") or "").strip() or None


async def _daily_report_email_config(session: AsyncSession) -> dict | None:
    result = await session.execute(
        select(NotificationChannel)
        .where(
            NotificationChannel.kind == "email",
            NotificationChannel.is_active.is_(True),
        )
        .order_by(NotificationChannel.is_default.desc(), NotificationChannel.id)
        .limit(1)
    )
    channel = result.scalar_one_or_none()
    if channel is not None:
        config = decode_channel_config(channel.config)
        if email_is_configured(config):
            return config
    fallback = environment_email_config()
    return fallback if email_is_configured(fallback) else None


def _belongs_to_market(symbol: str, market: str) -> bool:
    upper = symbol.upper()
    if market == "a_share":
        return upper.endswith((".SS", ".SZ", ".BJ")) or (upper.isdigit() and len(upper) == 6)
    if market == "us_stock":
        return (
            not upper.startswith("^")
            and not upper.endswith((".SS", ".SZ", ".BJ", ".HK", ".TW", ".TWO"))
            and "-USD" not in upper
            and "=" not in upper
        )
    return False


def _merge_specs(specs: Iterable[InstrumentSpec]) -> list[InstrumentSpec]:
    merged: dict[str, InstrumentSpec] = {}
    for item in specs:
        current = merged.get(item.symbol)
        if current is None:
            merged[item.symbol] = InstrumentSpec(
                symbol=item.symbol,
                name=item.name or item.symbol,
                tags=list(dict.fromkeys(item.tags)),
                opportunity_eligible=item.opportunity_eligible,
                volume_applicable=item.volume_applicable,
            )
            continue
        current.tags = list(dict.fromkeys([*current.tags, *item.tags]))
        current.opportunity_eligible = current.opportunity_eligible or item.opportunity_eligible
        current.volume_applicable = current.volume_applicable and item.volume_applicable
        if (not current.name or current.name == current.symbol) and item.name:
            current.name = item.name
    return list(merged.values())


async def _previous_market_dates(session: AsyncSession, report_date: str) -> dict[str, str]:
    result = await session.execute(
        select(DailyReport.market_dates)
        .where(DailyReport.report_date < report_date)
        .order_by(DailyReport.report_date.desc())
        .limit(14)
    )
    output: dict[str, str] = {}
    for raw in result.scalars().all():
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        for market, session_date in value.items():
            if market not in output and session_date:
                output[str(market)] = str(session_date)
        if len(output) == len(MARKETS):
            break
    return output


async def _watchlist_specs(session: AsyncSession, market: str) -> list[InstrumentSpec]:
    result = await session.execute(select(Watchlist).order_by(Watchlist.created_at))
    output = []
    for row in result.scalars().all():
        if str(row.asset_type).lower() not in {"stock", "equity"}:
            continue
        if _belongs_to_market(row.symbol, market):
            output.append(InstrumentSpec(row.symbol, row.name or row.symbol, ["自选"], True))
    return output


async def _save_hot_snapshot(
    session: AsyncSession,
    market: str,
    snapshot_date: str,
    candidates: list[dict],
) -> None:
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "")
        if not symbol:
            continue
        result = await session.execute(
            select(HotProductSnapshot).where(
                HotProductSnapshot.market == market,
                HotProductSnapshot.snapshot_date == snapshot_date,
                HotProductSnapshot.symbol == symbol,
            )
        )
        row = result.scalar_one_or_none()
        values = {
            "name": str(candidate.get("name") or symbol),
            "attention_score": float(candidate.get("attention_score") or 0),
            "turnover": float(candidate.get("turnover") or 0),
            "raw_payload": json.dumps(candidate, ensure_ascii=False, separators=(",", ":")),
        }
        if row is None:
            session.add(HotProductSnapshot(
                market=market,
                snapshot_date=snapshot_date,
                symbol=symbol,
                **values,
            ))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    await session.flush()


async def _rolling_hot_specs(
    session: AsyncSession,
    market: str,
    report_day: date,
    current_candidates: list[dict],
) -> list[InstrumentSpec]:
    await _save_hot_snapshot(session, market, report_day.isoformat(), current_candidates)
    first_day = (report_day - timedelta(days=2)).isoformat()
    result = await session.execute(
        select(HotProductSnapshot)
        .where(
            HotProductSnapshot.market == market,
            HotProductSnapshot.snapshot_date >= first_day,
            HotProductSnapshot.snapshot_date <= report_day.isoformat(),
        )
        .order_by(HotProductSnapshot.snapshot_date, HotProductSnapshot.attention_score.desc())
    )
    snapshots = [{
        "symbol": row.symbol,
        "name": row.name,
        "snapshot_date": row.snapshot_date,
        "attention_score": row.attention_score,
        "turnover": row.turnover,
    } for row in result.scalars().all()]
    # Keep reserve candidates because a trending token can be unsupported by
    # the configured candle venue. The first three with complete daily data
    # are selected below.
    hot = aggregate_hot_products(snapshots, limit=10)
    return [
        InstrumentSpec(row["symbol"], row["name"], ["热门"], True)
        for row in hot
    ]


async def _completed_candles(
    sources: DailyReportSources,
    symbol: str,
    now: datetime,
) -> list[dict]:
    historical_cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    before = int(now.timestamp()) if now < historical_cutoff else None
    candles = await sources.get_candles(symbol, 180, before=before)
    return completed_daily_candles(symbol, candles, now=now)


async def _available_hot_specs(
    sources: DailyReportSources,
    specs: list[InstrumentSpec],
    now: datetime,
    limit: int = 3,
) -> list[InstrumentSpec]:
    semaphore = asyncio.Semaphore(5)

    async def _available(spec: InstrumentSpec) -> bool:
        async with semaphore:
            try:
                return len(await _completed_candles(sources, spec.symbol, now)) >= 2
            except Exception:
                return False

    availability = await asyncio.gather(*(_available(spec) for spec in specs))
    return [spec for spec, valid in zip(specs, availability) if valid][:limit]


async def _analyze_specs(
    sources: DailyReportSources,
    market: MarketSpec,
    specs: list[InstrumentSpec],
    now: datetime,
) -> tuple[list[InstrumentAnalysis], list[OpportunityItem], list[ChanlunChapter]]:
    semaphore = asyncio.Semaphore(5)

    async def _chanlun_pass(
        symbol: str, name: str, candles: list[dict], *, opportunity_eligible: bool
    ) -> tuple[Any | None, list[OpportunityItem]]:
        """跑一次缠论，产出章节摘要 +（仅机会标的）机会项。失败返回 (None, [])。"""
        try:
            from app.core import chanlun_service

            if not chanlun_service.chanlun_available():
                return None, []
            levels = chanlun_service.analyze_symbol({"1day": candles})
            entry = chanlun_service.summarize_symbol(symbol, name, "1day", levels)
            opportunities = (
                chanlun_service.detect_chanlun_opportunities(
                    market.key, symbol, name, levels=levels
                )
                if opportunity_eligible else []
            )
            return entry, opportunities
        except Exception as exc:
            logger.warning("Chanlun section skipped %s: %s", symbol, exc)
            return None, []

    async def _one(spec: InstrumentSpec):
        async with semaphore:
            try:
                candles = await _completed_candles(sources, spec.symbol, now)
                analysis_tags = list(spec.tags)
                source_symbols = {
                    str(row.get("_report_source_symbol") or "")
                    for row in candles
                    if row.get("_report_source_symbol")
                }
                if source_symbols:
                    proxy_tag = (
                        "Binance代理"
                        if any(value.upper().startswith("BINANCE:") for value in source_symbols)
                        else "Hyperliquid代理"
                    )
                    if proxy_tag not in analysis_tags:
                        analysis_tags.append(proxy_tag)
                analysis = analyze_instrument(
                    spec.symbol,
                    spec.name,
                    analysis_tags,
                    candles,
                    session_timezone=market.session_timezone,
                    volume_applicable=spec.volume_applicable,
                )
                opportunities = (
                    detect_report_opportunities(market.key, spec.symbol, spec.name, candles)
                    if spec.opportunity_eligible else []
                )
                chanlun_entry, chanlun_opps = await _chanlun_pass(
                    spec.symbol, spec.name, candles,
                    opportunity_eligible=spec.opportunity_eligible,
                )
                return analysis, opportunities, chanlun_entry, chanlun_opps
            except Exception as exc:
                logger.warning("Daily report skipped %s: %s", spec.symbol, exc)
                return None, [], None, []

    rows = await asyncio.gather(*(_one(spec) for spec in specs))
    analyses = [row[0] for row in rows if row[0] is not None]
    opportunities = [item for row in rows for item in row[1]]
    chanlun_entries = [row[2] for row in rows if row[2] is not None]
    chanlun_opps = [item for row in rows for item in row[3]]
    return analyses, opportunities + chanlun_opps, chanlun_entries


async def build_daily_report(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    force_all_markets: bool = False,
    sources: DailyReportSources | None = None,
) -> DailyReportPreview:
    local_tz = ZoneInfo(settings.ALERT_TIMEZONE)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=local_tz).astimezone(timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    local_now = current.astimezone(local_tz)
    report_day = local_now.date()
    report_date = report_day.isoformat()
    previous_dates = {} if force_all_markets else await _previous_market_dates(session, report_date)
    source_adapter = sources or DailyReportSources()
    sections: list[MarketSection] = []
    all_opportunities: list[OpportunityItem] = []
    chanlun_entries: list[Any] = []

    for market in MARKETS:
        try:
            primary_candles = await _completed_candles(source_adapter, market.fixed[0].symbol, current)
            primary = analyze_instrument(
                market.fixed[0].symbol,
                market.fixed[0].name,
                market.fixed[0].tags,
                primary_candles,
                session_timezone=market.session_timezone,
                volume_applicable=market.fixed[0].volume_applicable,
            )
            session_date = primary.session_date
            if not force_all_markets and previous_dates.get(market.key) == session_date:
                continue

            additions: list[InstrumentSpec] = []
            hot_symbols: list[str] = []
            if market.key in {"a_share", "us_stock"}:
                top_rows = await source_adapter.top_turnover(market.key, 5)
                watchlist = await _watchlist_specs(session, market.key)
                additions.extend(
                    InstrumentSpec(str(row["symbol"]), str(row.get("name") or row["symbol"]), ["成交额前五"], True)
                    for row in top_rows
                )
                additions.extend(watchlist)
            if market.key in {"crypto", "a_share", "us_stock"}:
                hot_candidates = await source_adapter.hot_candidates(market.key, 30)
                hot_candidates_specs = await _rolling_hot_specs(
                    session, market.key, report_day, hot_candidates
                )
                hot_specs = await _available_hot_specs(
                    source_adapter, hot_candidates_specs, current
                )
                hot_symbols = [item.symbol for item in hot_specs]
                additions.extend(hot_specs)

            specs = _merge_specs([*market.fixed, *additions])
            analyses, opportunities, market_chanlun = await _analyze_specs(source_adapter, market, specs, current)
            if not analyses:
                raise ValueError("没有可用的完整日线")
            chanlun_entries.extend(market_chanlun)
            # Keep configured instrument order; primary was fetched once for session
            # detection but is intentionally re-fetched through the shared cache path.
            sections.append(MarketSection(
                market=market.key,
                title=market.title,
                session_date=session_date,
                instruments=analyses,
                hot_symbols=hot_symbols,
            ))
            all_opportunities.extend(opportunities)
        except Exception as exc:
            logger.exception("Daily report market failed: %s", market.key)
            sections.append(MarketSection(
                market=market.key,
                title=market.title,
                status="unavailable",
                error=str(exc)[:500],
            ))

    raw_events = await source_adapter.calendar_events(report_day, 7)
    events = []
    for row in raw_events:
        starts_at = datetime.fromisoformat(str(row["starts_at"]))
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=local_tz)
        display_time = starts_at.astimezone(local_tz).strftime("%Y-%m-%d %H:%M")
        events.append(CalendarEventItem(
            category=row["category"],
            name=row["name"],
            starts_at=display_time,
            timing=row.get("timing"),
        ))

    payload = DailyReportPayload(
        report_date=report_date,
        timezone=settings.ALERT_TIMEZONE,
        generated_at=local_now.strftime("%Y-%m-%d %H:%M"),
        markets=sections,
        opportunities=sorted(all_opportunities, key=lambda row: (-row.stars, row.symbol)),
        events=events,
        chanlun=ChanlunChapter(entries=chanlun_entries) if chanlun_entries else None,
    )
    subject = f"每日市场日报｜{report_date}"
    return DailyReportPreview(
        subject=subject,
        text=render_text(payload),
        html=render_html(payload),
        # Discord receives the same complete plain-text content as Email.
        # Delivery splits it into Discord-safe chunks when necessary.
        discord_summary=render_text(payload),
        payload=payload,
    )


async def _deliver_record(session: AsyncSession, row: DailyReport) -> None:
    email_config = await _daily_report_email_config(session)
    if row.email_status in {"pending", "failed", "disabled"} and row.email_attempts < MAX_DELIVERY_ATTEMPTS:
        if not email_config:
            row.email_status = "disabled"
            row.email_error = "日报邮件未配置"
        else:
            row.email_status = "pending"
            row.email_attempts += 1
            try:
                await send_report_email(
                    row.email_subject,
                    row.email_text,
                    row.email_html,
                    config=email_config,
                )
                row.email_status = "sent"
                row.email_sent_at = _utcnow_naive()
                row.email_error = None
            except Exception as exc:
                row.email_status = "failed"
                row.email_error = str(exc)[:1000]
                logger.exception("Daily report email delivery failed")
        await session.commit()

    # Older rows stored a short Discord-only summary. Upgrade them lazily so
    # retries and the next scheduled run use the exact email content.
    if row.email_text and row.discord_summary != row.email_text:
        row.discord_summary = row.email_text
        if row.discord_status == "sent":
            row.discord_status = "pending"
            row.discord_attempts = 0
            row.discord_sent_at = None
            row.discord_error = None

    discord_webhook = await _daily_report_discord_webhook(session)
    if row.discord_status in {"pending", "failed", "disabled"} and row.discord_attempts < MAX_DELIVERY_ATTEMPTS:
        if not discord_webhook:
            row.discord_status = "disabled"
            row.discord_error = "Discord Webhook 未配置"
        else:
            row.discord_status = "pending"
            row.discord_attempts += 1
            try:
                await send_report_discord(
                    row.report_date,
                    row.discord_summary,
                    webhook_url=discord_webhook,
                )
                row.discord_status = "sent"
                row.discord_sent_at = _utcnow_naive()
                row.discord_error = None
            except Exception as exc:
                row.discord_status = "failed"
                row.discord_error = str(exc)[:1000]
                logger.exception("Daily report Discord delivery failed")
        await session.commit()


async def generate_and_deliver_daily_report(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    force_all_markets: bool = False,
    sources: DailyReportSources | None = None,
) -> DailyReport:
    local_tz = ZoneInfo(settings.ALERT_TIMEZONE)
    requested_now = now or datetime.now(timezone.utc)
    if requested_now.tzinfo is None:
        requested_now = requested_now.replace(tzinfo=local_tz)
    local_now = requested_now.astimezone(local_tz)
    report_date = local_now.date().isoformat()
    existing_result = await session.execute(
        select(DailyReport).where(
            DailyReport.report_date == report_date,
            DailyReport.timezone == settings.ALERT_TIMEZONE,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing is not None:
        await _deliver_record(session, existing)
        return existing

    preview = await build_daily_report(
        session,
        now=now,
        force_all_markets=force_all_markets,
        sources=sources,
    )
    market_dates = {
        section.market: section.session_date
        for section in preview.payload.markets
        if section.status == "ok" and section.session_date
    }
    email_config = await _daily_report_email_config(session)
    discord_webhook = await _daily_report_discord_webhook(session)
    row = DailyReport(
        report_date=preview.payload.report_date,
        timezone=preview.payload.timezone,
        market_dates=json.dumps(market_dates, ensure_ascii=False),
        payload=preview.payload.model_dump_json(),
        email_subject=preview.subject,
        email_text=preview.text,
        email_html=preview.html,
        discord_summary=preview.discord_summary,
        email_status="pending" if email_config else "disabled",
        discord_status="pending" if discord_webhook else "disabled",
        email_error=None if email_config else "日报邮件未配置",
        discord_error=None if discord_webhook else "Discord Webhook 未配置",
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await _deliver_record(session, row)
    return row


async def retry_daily_report_deliveries(session: AsyncSession) -> int:
    result = await session.execute(
        select(DailyReport)
        .where(or_(
            and_(
                DailyReport.email_status == "failed",
                DailyReport.email_attempts < MAX_DELIVERY_ATTEMPTS,
            ),
            and_(
                DailyReport.discord_status == "failed",
                DailyReport.discord_attempts < MAX_DELIVERY_ATTEMPTS,
            ),
        ))
        .order_by(DailyReport.created_at)
        .limit(20)
    )
    rows = list(result.scalars().all())
    for row in rows:
        await _deliver_record(session, row)
    return len(rows)
