"""SMTP and Discord delivery for persisted daily reports."""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.discord_webhook import send_discord_alert
from app.core.notification_channels import email_config_is_complete, send_email_message


DISCORD_DESCRIPTION_LIMIT = 3900


def split_discord_text(text: str, limit: int = DISCORD_DESCRIPTION_LIMIT) -> list[str]:
    """Split a full email-style report into Discord-safe descriptions."""
    source = str(text or "")
    if limit < 1:
        raise ValueError("Discord 文本长度限制必须大于 0")
    if len(source) <= limit:
        return [source]

    chunks: list[str] = []
    current = ""
    for line in source.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks or [""]


def environment_email_config() -> dict[str, Any]:
    return {
        "smtp_host": settings.SMTP_HOST,
        "smtp_port": settings.SMTP_PORT,
        "smtp_username": settings.SMTP_USERNAME,
        "smtp_password": settings.SMTP_PASSWORD,
        "smtp_use_tls": settings.SMTP_USE_TLS,
        "smtp_use_ssl": settings.SMTP_USE_SSL,
        "from_address": settings.REPORT_EMAIL_FROM,
        "to_addresses": settings.REPORT_EMAIL_TO,
    }


def email_is_configured(config: dict[str, Any] | None = None) -> bool:
    return email_config_is_complete(config or environment_email_config())


def discord_is_configured() -> bool:
    return bool(settings.DISCORD_WEBHOOK_URL)


async def send_report_email(
    subject: str,
    text: str,
    html: str,
    *,
    config: dict[str, Any] | None = None,
) -> None:
    target = config or environment_email_config()
    if not email_is_configured(target):
        raise RuntimeError("日报邮件未配置")
    await send_email_message(target, subject, text, html)


async def send_report_discord(
    report_date: str,
    summary: str,
    *,
    webhook_url: str | None = None,
) -> None:
    target_url = str(webhook_url or settings.DISCORD_WEBHOOK_URL or "").strip()
    if not target_url:
        raise RuntimeError("Discord Webhook 未配置")
    chunks = split_discord_text(summary)
    for index, chunk in enumerate(chunks):
        suffix = "" if len(chunks) == 1 else f"（{index + 1}/{len(chunks)}）"
        sent = await send_discord_alert(
            target_url,
            f"每日市场日报｜{report_date}{suffix}",
            chunk,
            color=0x2563EB,
            title_icon="📊",
        )
        if not sent:
            raise RuntimeError(f"Discord Webhook 投递失败（第 {index + 1}/{len(chunks)} 段）")
