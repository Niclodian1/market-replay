import asyncio
import json

from app.core.discord_webhook import send_discord_alert


class _Response:
    status_code = 204
    text = ""


class _Client:
    def __init__(self, captured, *args, **kwargs):
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json=None, **kwargs):
        self.captured.update(url=url, payload=json)
        self.captured.update(**kwargs)
        return _Response()


def test_discord_embed_contains_direction_style_logo_and_safe_mentions(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.core.discord_webhook.httpx.AsyncClient",
        lambda *args, **kwargs: _Client(captured, *args, **kwargs),
    )

    ok = asyncio.run(send_discord_alert(
        "https://discord.example/webhook",
        "多头机会｜BTC-PERP｜4H",
        "测试正文",
        color=0x22C55E,
        thumbnail_url="https://example.com/btc.png",
        title_icon="🟢",
    ))

    assert ok
    assert captured["payload"]["allowed_mentions"] == {"parse": []}
    embed = captured["payload"]["embeds"][0]
    assert embed["title"].startswith("🟢 多头机会")
    assert embed["color"] == 0x22C55E
    assert embed["thumbnail"] == {"url": "https://example.com/btc.png"}
    assert embed["timestamp"].endswith("+00:00")


def test_discord_attachment_uses_multipart_and_embed_image(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.core.discord_webhook.httpx.AsyncClient",
        lambda *args, **kwargs: _Client(captured, *args, **kwargs),
    )

    svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    ok = asyncio.run(send_discord_alert(
        "https://discord.example/webhook",
        "缠论转折｜BTC｜1H",
        "信号正文",
        image_payloads=[
            {"filename": "BTC_1h.svg", "data": svg, "mime": "image/svg+xml"},
        ],
    ))

    assert ok
    assert captured["payload"] is None  # 附件走 multipart
    embed = json.loads(captured["data"]["payload_json"])["embeds"][0]
    assert embed["image"] == {"url": "attachment://BTC_1h.svg"}
    assert captured["files"] == [("file", ("BTC_1h.svg", svg, "image/svg+xml"))]
