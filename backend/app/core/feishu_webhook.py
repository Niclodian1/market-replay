import json
import logging
from datetime import datetime, timezone

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# 飞书卡片模板颜色 → 标题背景色
_TEMPLATE_COLOR = {
    0x22C55E: "green",
    0xEF4444: "red",
    0xF59E0B: "orange",
    0x3B82F6: "blue",
}


def _template(color: int | None) -> str:
    return _TEMPLATE_COLOR.get(color, "blue")


async def _tenant_access_token(client: httpx.AsyncClient) -> str | None:
    """换 tenant_access_token（自建应用凭证），失败返回 None。"""
    if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
        return None
    try:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": settings.FEISHU_APP_ID,
                "app_secret": settings.FEISHU_APP_SECRET,
            },
        )
        body = resp.json()
        if body.get("code") != 0:
            logger.warning("Feishu token error: %s", body.get("msg"))
            return None
        return body.get("tenant_access_token")
    except Exception as exc:
        logger.error("Feishu token request error: %s", exc)
        return None


async def _upload_image(client: httpx.AsyncClient, token: str, payload: dict) -> str | None:
    """上传图片，返回 image_key；失败返回 None。"""
    try:
        resp = await client.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": (payload["filename"], payload["data"], payload.get("mime", "image/svg+xml"))},
            timeout=20.0,
        )
        body = resp.json()
        if body.get("code") != 0:
            logger.warning("Feishu image upload error: %s", body.get("msg"))
            return None
        return body.get("data", {}).get("image_key")
    except Exception as exc:
        logger.error("Feishu image upload request error: %s", exc)
        return None


async def send_feishu_alert(
    webhook_url: str,
    title: str,
    description: str,
    color: int | None = None,
    *,
    title_icon: str = "🔔",
    image_payloads: list[dict] | None = None,
) -> bool:
    """Send an interactive-card message to a Feishu custom bot webhook.

    若配置了 FEISHU_APP_ID/SECRET 且提供了图片，会先上传图片换取 image_key，
    再以卡片 img 元素插入；否则发送纯文本卡片。
    """
    template = _template(color)
    elements: list[dict] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": description}},
    ]
    image_keys: list[str] = []
    if image_payloads:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await _tenant_access_token(client)
            if token:
                for payload in image_payloads:
                    key = await _upload_image(client, token, payload)
                    if key:
                        image_keys.append(key)
    for key in image_keys:
        elements.append({"tag": "img", "img_key": key})
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": "Market Replay · 行情辅助信号，不构成交易建议"}],
    })
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": f"{title_icon} {title}".strip()},
        },
        "elements": elements,
    }
    payload = {"msg_type": "interactive", "card": card}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code == 200 and body.get("code") in (None, 0):
                return True
            logger.warning("Feishu webhook responded %s: %s", resp.status_code, resp.text)
            return False
    except Exception as e:
        logger.error("Feishu webhook error: %s", e)
        return False
