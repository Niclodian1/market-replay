import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


async def send_discord_alert(
    webhook_url: str,
    title: str,
    description: str,
    color: int = 0x3B82F6,
    *,
    thumbnail_url: str | None = None,
    title_icon: str = "🔔",
    image_payloads: list[dict] | None = None,
) -> bool:
    embed = {
        "title": f"{title_icon} {title}".strip(),
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Market Replay · 行情辅助信号，不构成交易建议"},
    }
    if thumbnail_url:
        embed["thumbnail"] = {"url": thumbnail_url}
    if image_payloads:
        embed["image"] = {"url": f"attachment://{image_payloads[0]['filename']}"}
    payload = {
        "allowed_mentions": {"parse": []},
        "embeds": [embed],
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if image_payloads:
                files = [
                    ("file", (p["filename"], p["data"], p.get("mime", "image/svg+xml")))
                    for p in image_payloads
                ]
                resp = await client.post(webhook_url, data={"payload_json": _json_dumps(payload)}, files=files)
            else:
                resp = await client.post(webhook_url, json=payload)
            if resp.status_code in (200, 204):
                return True
            logger.warning("Discord webhook responded %s: %s", resp.status_code, resp.text)
            return False
    except Exception as e:
        logger.error("Discord webhook error: %s", e)
        return False


def _json_dumps(value: dict) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
