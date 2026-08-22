"""Execute dynamic watchlist monitor plans and durably deliver signal events."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.core.alert_engine import completed_indicator_candles
from app.core.candle_utils import completed_daily_candles
from app.core.daily_report_sources import DailyReportSources
from app.core.indicators import _sma
from app.core.notification_channels import send_to_all_channels
from app.core.signal_strength import (
    evaluate_signal_strength,
    signal_type_label,
)
from app.core.signals.monitor_signals import run_monitor_detectors
from app.core.symbol_branding import resolve_symbol_branding
from app.core.config import settings
from app.models.models import AlertEvent, MonitorPlan, NotificationChannel, Watchlist


logger = logging.getLogger(__name__)
VALID_MONITOR_TIMEFRAMES = {"1h", "4h", "1day"}


async def fetch_monitor_candles(symbol: str, timeframe: str, outputsize: int = 360) -> list[dict]:
    """Fetch closed candles for a monitor group with Binance/HL multi-source fallback.

    全部走 DailyReportSources 多源回退通道（日线 A股指数 akShare、美股 Yahoo、
    crypto Binance spot 为主源，失败后兜底 Binance 永续 / Hyperliquid），
    与缠论/日报共用同一取数通道。
    """
    candles = await DailyReportSources().get_candles(
        symbol,
        outputsize=outputsize,
        timeframe=timeframe,
    )
    if timeframe == "1day":
        return completed_daily_candles(symbol, candles)
    return completed_indicator_candles(candles, timeframe)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode(raw: str, fallback):
    try:
        value = json.loads(raw)
        return value
    except (TypeError, ValueError):
        return fallback


def _plan_timeframes(plan: MonitorPlan) -> list[str]:
    """Return valid selected timeframes, falling back to legacy single rows."""
    decoded = _decode(plan.timeframes, None) if plan.timeframes else None
    candidates = decoded if isinstance(decoded, list) else [plan.timeframe]
    return list(dict.fromkeys(
        str(item).lower() for item in candidates if str(item).lower() in VALID_MONITOR_TIMEFRAMES
    )) or ["4h"]


async def _resolve_active_channels(db, plan: MonitorPlan) -> list[NotificationChannel]:
    """Return every active channel that should receive a plan's alerts.

    若计划绑定 channel_id 则优先包含该渠道；同时包含默认用户的所有活跃
    渠道（Discord/飞书等），实现多渠道同步推送，按 id 去重。
    """
    by_id: dict[int, NotificationChannel] = {}
    if plan.channel_id is not None:
        channel = await db.get(NotificationChannel, plan.channel_id)
        if channel is not None and channel.is_active:
            by_id[channel.id] = channel
    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.user_id == plan.user_id,
            NotificationChannel.is_active == True,  # noqa: E712
        )
    )
    for channel in result.scalars().all():
        by_id[channel.id] = channel
    return sorted(by_id.values(), key=lambda c: (not c.is_default, c.id))


def _direction_presentation(directions) -> tuple[str, str, int]:
    unique = {str(value) for value in directions}
    if unique == {"long"}:
        return "多头机会", "🟢", 0x22C55E
    if unique == {"short"}:
        return "空头机会", "🔴", 0xEF4444
    return "多空信号并存", "🟠", 0xF59E0B


def _timeframe_presentation(timeframe: str) -> str:
    return {
        "1h": "1H",
        "4h": "4H",
        "1day": "1D",
    }.get(timeframe.lower(), timeframe.upper())


def _price_presentation(value: float) -> str:
    price = float(value)
    decimals = 2 if abs(price) >= 1 else 4 if abs(price) >= 0.01 else 6
    return f"{price:,.{decimals}f}".rstrip("0").rstrip(".")


def _ma_period(item: dict) -> int | None:
    try:
        return int(item.get("payload", {}).get("parameters", {}).get("period"))
    except (TypeError, ValueError):
        return None


def _merge_signal_summary(event_type: str, direction: str, items: list[dict]) -> str:
    if len(items) == 1:
        return str(items[0]["summary"])

    periods = sorted({period for item in items if (period := _ma_period(item)) is not None})
    ma_text = "、".join(f"MA{period}" for period in periods)
    if event_type == "ma_breakout" and ma_text:
        first_summary = str(items[0]["summary"])
        evidence = first_summary.split("，", 1)
        suffix = f"，{evidence[1]}" if len(evidence) == 2 else ""
        verb = "上穿" if direction == "long" else "下穿"
        return f"收盘同时{verb} {ma_text}{suffix}"
    if event_type == "ma_retest" and ma_text:
        return f"{ma_text} 均完成突破后回踩确认"

    summaries = list(dict.fromkeys(str(item["summary"]) for item in items))
    return "；".join(summaries)


def _ma_chart_payloads(
    symbol: str,
    timeframe: str,
    candles: list[dict],
    items: list[dict],
    current_price: float | None = None,
    max_bars: int = 250,
) -> list[dict]:
    """生成 MA 突破告警的走势图 PNG（K线 + 告警涉及的 MA 均线 + 突破点标记）。

    突破点标记用 item 的 signal_time / reference_price，方向色：买绿/卖红。
    返回 [{filename, data, mime}]。
    """
    from app.core.chanlun_chart import render_chanlun_png

    if not candles:
        return []
    chart_candles = candles[-max_bars:]
    closes = [float(c["close"]) for c in chart_candles]
    periods = sorted({_ma_period(item) for item in items if _ma_period(item) is not None}) or [20]
    ma_lines = [
        {"period": period, "values": _sma(closes, period)}
        for period in periods
    ]
    markers = []
    for item in items:
        direction = str(item.get("direction") or "")
        markers.append({
            "t": item.get("signal_time"),
            "p": item.get("reference_price"),
            "k": "上穿" if direction == "long" else "下穿",
            "direction": direction,
        })
    png = render_chanlun_png(
        symbol,
        _timeframe_presentation(timeframe),
        chart_candles,
        markers=markers,
        ma_lines=ma_lines,
        current_price=current_price,
        title=f"{symbol} · {_timeframe_presentation(timeframe)} · MA 突破",
    )
    if not png:
        return []
    return [{"filename": f"{symbol}_{timeframe}_ma.png", "data": png, "mime": "image/png"}]


def _signal_rows(events: list[dict], timeframe: str) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in events:
        event_type = str(item.get("event_type") or item.get("strategy") or "signal")
        key = (str(item.get("direction") or ""), event_type)
        grouped.setdefault(key, []).append(item)

    rows = []
    for (direction, event_type), items in grouped.items():
        strength = evaluate_signal_strength(
            event_type,
            timeframe,
            str(items[0].get("severity") or ""),
        )
        rows.append({
            "direction": direction,
            "label": signal_type_label(event_type, str(items[0].get("strategy") or "")),
            "summary": _merge_signal_summary(event_type, direction, items),
            "strength": strength,
        })
    return sorted(rows, key=lambda row: row["strength"].stars, reverse=True)


def _format_batch(
    plan: MonitorPlan,
    symbol: str,
    timeframe: str,
    events: list[dict],
) -> tuple[str, str, dict]:
    direction_icon = {"long": "🟢", "short": "🔴"}
    direction_name, title_icon, color = _direction_presentation(item["direction"] for item in events)
    branding = resolve_symbol_branding(symbol)
    signal_time = datetime.fromtimestamp(
        int(events[0]["signal_time"]), timezone.utc
    ).astimezone(ZoneInfo(settings.ALERT_TIMEZONE))
    title = f"{direction_name}｜{branding.display_name}｜{_timeframe_presentation(timeframe)}"
    lines = ["**核心结论**"]
    for row in _signal_rows(events, timeframe):
        lines.append(
            f"• {direction_icon.get(row['direction'], '⚪')} {row['strength'].glyphs} "
            f"**{row['label']}**：{row['summary']}"
        )
    lines.extend((
        "",
        f"**信号时间**：{signal_time.strftime('%Y-%m-%d %H:%M')} ({settings.ALERT_TIMEZONE})",
        f"**参考价格**：{_price_presentation(events[0]['reference_price'])}",
    ))
    return title, "\n".join(lines), {
        "color": color,
        "thumbnail_url": branding.logo_url,
        "title_icon": title_icon,
    }


async def _store_new_event(db, plan: MonitorPlan, symbol: str, timeframe: str, item: dict) -> AlertEvent | None:
    strength = evaluate_signal_strength(item["event_type"], timeframe, item["severity"])
    dedupe_key = ":".join((
        str(plan.id), symbol, timeframe, item["strategy"], item["event_type"],
        item["direction"], str(item["signal_time"]),
    ))
    event = AlertEvent(
        dedupe_key=dedupe_key,
        plan_id=plan.id,
        symbol=symbol,
        timeframe=timeframe,
        strategy=item["strategy"],
        event_type=item["event_type"],
        direction=item["direction"],
        severity=item["severity"],
        signal_time=item["signal_time"],
        reference_price=item["reference_price"],
        payload=json.dumps({
            **item["payload"],
            "summary": item["summary"],
            "strength": strength.stars,
            "strength_label": strength.label,
        }, ensure_ascii=False),
    )
    try:
        async with db.begin_nested():
            db.add(event)
            await db.flush()
        return event
    except IntegrityError:
        return None


async def _deliver_batch(
    db,
    plan: MonitorPlan,
    symbol: str,
    timeframe: str,
    items: list[dict],
    events: list[AlertEvent],
    candles: list[dict] | None = None,
) -> None:
    channels = await _resolve_active_channels(db, plan)
    now = _utcnow()
    if not channels:
        for event in events:
            event.status = "in_app_only"
            event.last_error = "未配置可用的通知渠道；站内信号已保存"
        return
    title, description, presentation = _format_batch(plan, symbol, timeframe, items)
    image_payloads = _ma_chart_payloads(
        symbol, timeframe, candles or [], items,
        current_price=float(items[0].get("reference_price")) if items else None,
    ) if candles else None
    ok = await send_to_all_channels(
        channels, title, description, image_payloads=image_payloads, **presentation
    )
    for event in events:
        event.attempts += 1
        if ok:
            event.status = "sent"
            event.sent_at = now
            event.next_retry_at = None
            event.last_error = None
        else:
            event.status = "failed"
            event.next_retry_at = now + timedelta(minutes=5)
            event.last_error = "全部通知渠道投递失败，将自动重试"


async def run_monitor_checks(db) -> dict:
    """Fetch each symbol/timeframe once, then run selected detectors independently."""
    plan_result = await db.execute(
        select(MonitorPlan).where(MonitorPlan.is_active == True)  # noqa: E712
    )
    plans = list(plan_result.scalars().all())
    if not plans:
        return {"plans": 0, "groups": 0, "events": 0}

    watchlists_by_user: dict[int, list[str]] = {}
    for user_id in {plan.user_id for plan in plans}:
        result = await db.execute(
            select(Watchlist.symbol).where(Watchlist.user_id == user_id).order_by(Watchlist.created_at)
        )
        watchlists_by_user[user_id] = list(dict.fromkeys(str(value).upper() for value in result.scalars().all()))

    groups: dict[tuple[str, str], list[MonitorPlan]] = defaultdict(list)
    plan_errors: dict[int, list[str]] = defaultdict(list)
    for plan in plans:
        symbols = watchlists_by_user.get(plan.user_id, [])
        if not symbols:
            plan_errors[plan.id].append("自选列表为空")
        for symbol in symbols:
            for timeframe in _plan_timeframes(plan):
                groups[(symbol, timeframe)].append(plan)

    created_count = 0
    for (symbol, timeframe), group_plans in groups.items():
        try:
            candles = await fetch_monitor_candles(symbol, timeframe, outputsize=360)
            if len(candles) < 50:
                raise ValueError(f"已完成K线不足：{len(candles)} 根")
        except Exception as exc:
            message = str(exc)[:160] or "数据源暂不可用"
            for plan in group_plans:
                plan_errors[plan.id].append(f"{symbol} {timeframe}: {message}")
            continue

        for plan in group_plans:
            strategies = _decode(plan.strategies, [])
            parameters = _decode(plan.parameters, {})
            try:
                items = run_monitor_detectors(candles, timeframe, strategies, parameters)
            except Exception as exc:
                plan_errors[plan.id].append(f"策略参数错误: {str(exc)[:120]}")
                continue
            new_items: list[dict] = []
            new_events: list[AlertEvent] = []
            for item in items:
                event = await _store_new_event(db, plan, symbol, timeframe, item)
                if event is not None:
                    new_items.append(item)
                    new_events.append(event)
            if new_events:
                # Durable outbox boundary: commit the dedupe records before the
                # external side effect. A crash leaves pending events retryable.
                await db.commit()
                await _deliver_batch(db, plan, symbol, timeframe, new_items, new_events, candles=candles)
                created_count += len(new_events)

    now = _utcnow()
    for plan in plans:
        plan.last_run_at = now
        errors = plan_errors.get(plan.id, [])
        plan.last_error = "；".join(errors[:8]) if errors else None
    await db.commit()
    return {"plans": len(plans), "groups": len(groups), "events": created_count}


async def retry_monitor_deliveries(db) -> int:
    now = _utcnow()
    result = await db.execute(
        select(AlertEvent).where(
            AlertEvent.status.in_(("pending", "failed")),
            AlertEvent.attempts < 6,
            or_(AlertEvent.next_retry_at.is_(None), AlertEvent.next_retry_at <= now),
        ).limit(50)
    )
    events = list(result.scalars().all())
    retried = 0
    for event in events:
        plan = await db.get(MonitorPlan, event.plan_id)
        if plan is None:
            continue
        channels = await _resolve_active_channels(db, plan)
        if not channels:
            event.status = "in_app_only"
            event.next_retry_at = None
            event.last_error = "通知渠道不可用；站内信号已保留"
            continue
        payload = _decode(event.payload, {})
        summary = payload.get("summary") or event.event_type
        direction_name, title_icon, color = _direction_presentation([event.direction])
        branding = resolve_symbol_branding(event.symbol)
        signal_time = datetime.fromtimestamp(
            int(event.signal_time), timezone.utc
        ).astimezone(ZoneInfo(settings.ALERT_TIMEZONE))
        direction_icon = {"long": "🟢", "short": "🔴"}
        strength = evaluate_signal_strength(
            event.event_type,
            event.timeframe,
            event.severity,
        )
        event_label = signal_type_label(event.event_type, event.strategy)
        image_payloads = None
        if event.event_type in ("ma_breakout", "ma_retest"):
            try:
                candles = await fetch_monitor_candles(event.symbol, event.timeframe, outputsize=360)
                payload_item = {
                    "direction": event.direction,
                    "signal_time": event.signal_time,
                    "reference_price": event.reference_price,
                    "payload": payload,
                }
                image_payloads = _ma_chart_payloads(
                    event.symbol, event.timeframe, candles, [payload_item],
                    current_price=float(event.reference_price),
                )
            except Exception:
                image_payloads = None
        ok = await send_to_all_channels(
            channels,
            f"{direction_name}｜{branding.display_name}｜"
            f"{_timeframe_presentation(event.timeframe)} · 重试",
            (
                "**核心结论**\n"
                f"• {direction_icon.get(event.direction, '⚪')} "
                f"{strength.glyphs} **{event_label}**：{summary}\n"
                "\n"
                f"**信号时间**：{signal_time.strftime('%Y-%m-%d %H:%M')} "
                f"({settings.ALERT_TIMEZONE})\n"
                f"**参考价格**：{_price_presentation(event.reference_price)}"
            ),
            color=color,
            thumbnail_url=branding.logo_url,
            title_icon=title_icon,
            image_payloads=image_payloads,
        )
        event.attempts += 1
        retried += 1
        if ok:
            event.status = "sent"
            event.sent_at = now
            event.next_retry_at = None
            event.last_error = None
        else:
            delay = min(5 * (3 ** max(0, event.attempts - 1)), 360)
            event.next_retry_at = now + timedelta(minutes=delay)
            event.last_error = f"投递失败，将在约 {delay} 分钟后重试"
    await db.commit()
    return retried
