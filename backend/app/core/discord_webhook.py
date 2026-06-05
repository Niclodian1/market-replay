import httpx
import logging
from typing import Any

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


async def send_opportunity(webhook_url: str, opportunity: dict[str, Any], trade_plan: dict[str, Any] | None = None) -> bool:
    edge = opportunity.get("edge") or opportunity.get("edge_stat") or {}
    plan = trade_plan or opportunity.get("trade_plan") or {}
    symbol = opportunity.get("symbol", "")
    timeframe = opportunity.get("timeframe", "")
    direction = opportunity.get("direction", "")
    score = float(opportunity.get("score", 0)) * 100
    signals = opportunity.get("signals") or opportunity.get("confluence_payload", {}).get("signals", [])
    signal_text = ", ".join(str(item.get("name", item)) for item in signals) or "n/a"
    payload = {
        "embeds": [
            {
                "title": f"[NEW] {symbol} {timeframe} {direction.upper()} opportunity",
                "description": f"Confidence {score:.0f} · Signals: {signal_text}",
                "color": 0x26D07C if direction == "long" else 0xFF5F6D,
                "fields": [
                    {
                        "name": "Edge",
                        "value": (
                            f"WR {float(edge.get('win_rate', 0)) * 100:.1f}% · "
                            f"EXP {float(edge.get('expectancy', 0)):.2f}R · "
                            f"N {edge.get('sample_size', 0)}"
                        ),
                        "inline": False,
                    },
                    {
                        "name": "Trade Plan",
                        "value": (
                            f"Entry {plan.get('entry', '-')} / "
                            f"SL {plan.get('stop_loss', '-')} / "
                            f"TP {plan.get('take_profit', '-')} / "
                            f"RR {plan.get('rr_ratio', '-')}"
                        ),
                        "inline": False,
                    },
                ],
                "footer": {"text": "Market Replay Opportunity Pipeline"},
            }
        ]
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code in (200, 204):
                return True
            logger.warning("Discord opportunity webhook responded %s: %s", resp.status_code, resp.text)
            return False
    except Exception as e:
        logger.error("Discord opportunity webhook error: %s", e)
        return False
