import httpx
import logging

logger = logging.getLogger(__name__)


async def send_discord_alert(webhook_url: str, title: str, description: str, color: int = 0xef4444) -> bool:
    payload = {
        "embeds": [
            {
                "title": f"🔔 {title}",
                "description": description,
                "color": color,
                "footer": {"text": "Market Replay 告警系统"},
            }
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code in (200, 204):
                return True
            logger.warning("Discord webhook responded %s: %s", resp.status_code, resp.text)
            return False
    except Exception as e:
        logger.error("Discord webhook error: %s", e)
        return False
