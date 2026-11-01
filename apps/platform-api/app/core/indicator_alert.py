"""指标触发的飞书/多渠道通知：带历史统计摘要 + 回到当时的深链。

与旧告警的区别：消息里不只有「发生了什么」，还有「历史上这个信号值不值得看」。
"""
from __future__ import annotations

from typing import Any, Sequence

from app.core.notification_channels import send_to_all_channels, send_to_channel


def event_deep_link(base_url: str, event: Any) -> str:
    base = (base_url or "http://localhost:3000").rstrip("/")
    return (
        f"{base}/watch?symbol={event.symbol}&timeframe={event.timeframe}"
        f"&t={int(event.confirmed_at)}"
    )


def _format_event(event: Any, summary: dict[str, Any]) -> str:
    side = "买" if event.direction == "long" else "卖"
    bits = [
        f"**[{event.indicator_id}]** {side} @ {event.price:.4f}",
        f"时间 `{event.confirmed_at}`",
    ]
    n = int(summary.get("n") or 0)
    if n:
        bits.append(
            f"历史 n={n} · 胜率 {summary.get('win_rate', 0) * 100:.1f}% · "
            f"期望 {summary.get('expectancy_r', 0):.2f}R · Wilson 下界 "
            f"{summary.get('wilson_low', 0) * 100:.1f}%"
        )
    else:
        bits.append("历史样本不足")
    return " · ".join(bits)


def build_digest(
    *,
    symbol: str,
    timeframe: str,
    events: Sequence[Any],
    summaries: dict[str, dict[str, Any]],
    base_url: str = "http://localhost:3000",
) -> tuple[str, str]:
    """返回 (title, markdown)，发送与测试共用，避免两处拼字符串。"""
    title = f"{symbol.upper()} {timeframe} · {len(events)} 个指标触发"
    lines = [f"**{symbol.upper()} · {timeframe}**"]
    for event in events[:10]:
        lines.append(f"- {_format_event(event, summaries.get(event.indicator_id, {}))}")
    if len(events) > 10:
        lines.append(f"- … 另有 {len(events) - 10} 个")
    lines.append("")
    lines.append(f"[回到当时看盘]({event_deep_link(base_url, events[0])})")
    return title, "\n".join(lines)


async def send_indicator_digest(
    channel: Any,
    *,
    symbol: str,
    timeframe: str,
    events: Sequence[Any],
    summaries: dict[str, dict[str, Any]],
    base_url: str = "http://localhost:3000",
) -> bool:
    if not events:
        return False
    title, description = build_digest(
        symbol=symbol,
        timeframe=timeframe,
        events=events,
        summaries=summaries,
        base_url=base_url,
    )
    return await send_to_channel(channel, title, description, title_icon="📈")


async def send_indicator_digest_all(
    channels: Sequence[Any],
    *,
    symbol: str,
    timeframe: str,
    events: Sequence[Any],
    summaries: dict[str, dict[str, Any]],
    base_url: str = "http://localhost:3000",
) -> bool:
    if not events or not channels:
        return False
    title, description = build_digest(
        symbol=symbol,
        timeframe=timeframe,
        events=events,
        summaries=summaries,
        base_url=base_url,
    )
    return await send_to_all_channels(channels, title, description, title_icon="📈")
