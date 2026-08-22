"""Extensible server-side notification channel adapters."""
from __future__ import annotations

import asyncio
import json
import smtplib
from email.message import EmailMessage
from typing import Any, Protocol

from app.core.discord_webhook import send_discord_alert
from app.core.feishu_webhook import send_feishu_alert


# Common public mailbox SMTP presets. Custom domains can still provide their
# own SMTP host and transport settings in the channel config.
EMAIL_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gmail.com": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    },
    "googlemail.com": {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    },
    "outlook.com": {
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    },
    "hotmail.com": {
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    },
    "live.com": {
        "smtp_host": "smtp-mail.outlook.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    },
    "qq.com": {
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_use_tls": False,
        "smtp_use_ssl": True,
    },
    "foxmail.com": {
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_use_tls": False,
        "smtp_use_ssl": True,
    },
    "163.com": {
        "smtp_host": "smtp.163.com",
        "smtp_port": 465,
        "smtp_use_tls": False,
        "smtp_use_ssl": True,
    },
    "126.com": {
        "smtp_host": "smtp.126.com",
        "smtp_port": 465,
        "smtp_use_tls": False,
        "smtp_use_ssl": True,
    },
    "yeah.net": {
        "smtp_host": "smtp.yeah.net",
        "smtp_port": 465,
        "smtp_use_tls": False,
        "smtp_use_ssl": True,
    },
    "yahoo.com": {
        "smtp_host": "smtp.mail.yahoo.com",
        "smtp_port": 465,
        "smtp_use_tls": False,
        "smtp_use_ssl": True,
    },
    "icloud.com": {
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    },
    "me.com": {
        "smtp_host": "smtp.mail.me.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
    },
    "zoho.com": {
        "smtp_host": "smtp.zoho.com",
        "smtp_port": 465,
        "smtp_use_tls": False,
        "smtp_use_ssl": True,
    },
}


def email_provider_preset(email: str) -> dict[str, Any] | None:
    """Return an SMTP preset for a recognized mailbox domain."""
    value = str(email or "").strip().lower()
    if value.count("@") != 1:
        return None
    domain = value.rsplit("@", 1)[1]
    preset = EMAIL_PROVIDER_PRESETS.get(domain)
    return dict(preset) if preset else None


def apply_email_provider_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """Fill an email channel from a mailbox address without overwriting overrides."""
    value = dict(config)
    email = str(
        value.get("email_address")
        or value.get("email")
        or value.get("from_address")
        or value.get("smtp_username")
        or ""
    ).strip()
    if not email:
        return value

    def fill(key: str, fallback: Any) -> None:
        if value.get(key) in (None, ""):
            value[key] = fallback

    fill("from_address", email)
    fill("smtp_username", email)
    fill("to_addresses", [email])
    preset = email_provider_preset(email)
    if preset:
        for key, preset_value in preset.items():
            fill(key, preset_value)
    return value


class ChannelSender(Protocol):
    async def send(
        self,
        config: dict[str, Any],
        title: str,
        description: str,
        *,
        color: int | None = None,
        thumbnail_url: str | None = None,
        title_icon: str = "🔔",
        image_payloads: list[dict] | None = None,
    ) -> bool: ...


class DiscordSender:
    async def send(
        self,
        config: dict[str, Any],
        title: str,
        description: str,
        *,
        color: int | None = None,
        thumbnail_url: str | None = None,
        title_icon: str = "🔔",
        image_payloads: list[dict] | None = None,
    ) -> bool:
        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url:
            return False
        return await send_discord_alert(
            webhook_url,
            title,
            description,
            color=color if color is not None else 0x3B82F6,
            thumbnail_url=thumbnail_url,
            title_icon=title_icon,
            image_payloads=image_payloads,
        )


class FeishuSender:
    async def send(
        self,
        config: dict[str, Any],
        title: str,
        description: str,
        *,
        color: int | None = None,
        thumbnail_url: str | None = None,
        title_icon: str = "🔔",
        image_payloads: list[dict] | None = None,
    ) -> bool:
        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url:
            return False
        del thumbnail_url
        return await send_feishu_alert(
            webhook_url,
            title,
            description,
            color=color,
            title_icon=title_icon,
            image_payloads=image_payloads,
        )


def email_recipients(config: dict[str, Any]) -> list[str]:
    raw = config.get("to_addresses") or config.get("address") or ""
    values = raw if isinstance(raw, list) else str(raw).split(",")
    return [str(item).strip() for item in values if str(item).strip()]


def email_config_is_complete(config: dict[str, Any]) -> bool:
    return bool(
        str(config.get("smtp_host") or "").strip()
        and str(config.get("from_address") or "").strip()
        and email_recipients(config)
    )


def smtp_login_password(config: dict[str, Any]) -> str:
    """Return the SMTP password in the form expected by the provider."""
    password = str(config.get("smtp_password") or "")
    host = str(config.get("smtp_host") or "").strip().lower()
    # Google displays app passwords in four-character groups. Accept both the
    # displayed form and the 16-character form users paste into SMTP clients.
    if host == "smtp.gmail.com":
        return password.replace(" ", "")
    return password


def email_error_hint(exc: Exception) -> str:
    """Map low-level SMTP failures to actionable UI text."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "SMTP 认证失败，请检查用户名、密码或应用专用密码"
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, OSError)):
        return "服务器无法连接 SMTP，请检查服务器出网策略、SMTP 主机和端口"
    if isinstance(exc, smtplib.SMTPException):
        return "SMTP 服务器拒绝了本次发送，请检查邮箱服务商设置"
    return "邮件发送失败，请检查邮件配置"


async def send_email_message(
    config: dict[str, Any],
    subject: str,
    text: str,
    html: str | None = None,
) -> None:
    if not email_config_is_complete(config):
        raise RuntimeError("邮件渠道缺少 SMTP 主机、发件人或收件人")

    use_ssl = bool(config.get("smtp_use_ssl", False))
    use_tls = bool(config.get("smtp_use_tls", True))
    if use_ssl and use_tls:
        raise RuntimeError("SMTP SSL 与 STARTTLS 不能同时启用")

    recipients = email_recipients(config)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = str(config["from_address"]).strip()
    message["To"] = ", ".join(recipients)
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    def _send() -> None:
        smtp_type = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_type(
            str(config["smtp_host"]).strip(),
            int(config.get("smtp_port") or (465 if use_ssl else 587)),
            timeout=20,
        ) as client:
            if use_tls:
                client.starttls()
            username = str(config.get("smtp_username") or "").strip()
            if username:
                client.login(username, smtp_login_password(config))
            client.send_message(message, to_addrs=recipients)

    await asyncio.to_thread(_send)


class EmailSender:
    async def send(
        self,
        config: dict[str, Any],
        title: str,
        description: str,
        *,
        color: int | None = None,
        thumbnail_url: str | None = None,
        title_icon: str = "🔔",
        image_payloads: list[dict] | None = None,
    ) -> bool:
        del color, thumbnail_url, image_payloads
        try:
            await send_email_message(config, f"{title_icon} {title}", description)
            return True
        except Exception:
            return False


SENDERS: dict[str, ChannelSender] = {
    "discord": DiscordSender(),
    "feishu": FeishuSender(),
    "email": EmailSender(),
}


def decode_channel_config(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def encode_channel_config(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def mask_endpoint(kind: str, config: dict[str, Any]) -> str:
    if kind == "email":
        recipients = email_recipients(config)
        value = recipients[0] if recipients else ""
    else:
        value = str(config.get("webhook_url") or config.get("address") or "")
    if not value:
        return "未配置"
    if kind in {"discord", "feishu"}:
        return f"{value[:28]}…{value[-6:]}" if len(value) > 40 else "••••••"
    if "@" in value:
        name, domain = value.split("@", 1)
        return f"{name[:2]}***@{domain}"
    return "••••••"


def safe_channel_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """Return fields needed by the edit form without ever exposing credentials."""
    if kind != "email":
        return {}
    return {
        "smtp_host": str(config.get("smtp_host") or ""),
        "smtp_port": int(config.get("smtp_port") or 587),
        "smtp_username": str(config.get("smtp_username") or ""),
        "smtp_use_tls": bool(config.get("smtp_use_tls", True)),
        "smtp_use_ssl": bool(config.get("smtp_use_ssl", False)),
        "from_address": str(config.get("from_address") or ""),
        "to_addresses": ", ".join(email_recipients(config)),
        "has_password": bool(config.get("smtp_password")),
    }


async def send_to_channel(
    channel,
    title: str,
    description: str,
    *,
    color: int | None = None,
    thumbnail_url: str | None = None,
    title_icon: str = "🔔",
    image_payloads: list[dict] | None = None,
) -> bool:
    sender = SENDERS.get(channel.kind)
    if sender is None or not channel.is_active:
        return False
    return await sender.send(
        decode_channel_config(channel.config),
        title,
        description,
        color=color,
        thumbnail_url=thumbnail_url,
        title_icon=title_icon,
        image_payloads=image_payloads,
    )


async def send_to_all_channels(
    channels,
    title: str,
    description: str,
    *,
    color: int | None = None,
    thumbnail_url: str | None = None,
    title_icon: str = "🔔",
    image_payloads: list[dict] | None = None,
) -> bool:
    """Deliver to every active channel (Discord/飞书/email) in parallel.

    任一渠道发送成功即返回 True；全部失败才返回 False。
    """
    if not channels:
        return False
    results = await asyncio.gather(
        *(
            send_to_channel(
                channel,
                title,
                description,
                color=color,
                thumbnail_url=thumbnail_url,
                title_icon=title_icon,
                image_payloads=image_payloads,
            )
            for channel in channels
        ),
        return_exceptions=True,
    )
    return any(result is True for result in results)
