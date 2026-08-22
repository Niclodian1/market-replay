import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.monitoring_api import (
    _validate_plan,
    _validated_channel_config,
    test_environment_email as send_test_environment_email,
)
from app.core.monitor_engine import _format_batch, _plan_timeframes, fetch_monitor_candles
from app.core.notification_channels import (
    apply_email_provider_defaults,
    email_provider_preset,
    email_error_hint,
    mask_endpoint,
    safe_channel_config,
    send_email_message,
    send_to_all_channels,
    send_to_channel,
    smtp_login_password,
)
from app.core.symbol_branding import resolve_symbol_branding


def test_monitor_plan_defaults_to_parallel_strategy_contract():
    assert _validate_plan(
        target_scope="watchlist",
        timeframes=["1h", "4h", "1day", "1h"],
        strategies=["ma", "macd", "structure", "macd"],
    ) == ["ma", "macd", "structure"]


def test_monitor_plan_rejects_weekly_and_unknown_strategy():
    with pytest.raises(HTTPException):
        _validate_plan(target_scope="watchlist", timeframes=["1week"], strategies=["ma"])
    with pytest.raises(HTTPException):
        _validate_plan(target_scope="watchlist", timeframes=[], strategies=["ma"])
    with pytest.raises(HTTPException):
        _validate_plan(target_scope="watchlist", timeframe="4h", strategies=["neural_magic"])


def test_channel_endpoint_is_masked_before_api_output():
    masked = mask_endpoint(
        "discord",
        {"webhook_url": "https://discord.com/api/webhooks/123456789/very-secret-token"},
    )
    assert "very-secret-token" not in masked
    assert masked.startswith("https://discord.com/api/web")


def test_feishu_channel_validation_and_delivery(monkeypatch):
    config = _validated_channel_config(
        "feishu",
        {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/abc-123"},
    )
    assert config == {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/abc-123"}
    masked = mask_endpoint("feishu", config)
    assert "abc-123" not in masked
    assert masked.startswith("https://open.feishu.cn")

    with pytest.raises(HTTPException, match="HTTPS"):
        _validated_channel_config("feishu", {"webhook_url": "http://insecure.example"})

    calls: dict = {}

    async def fake_send(webhook_url, title, description, color=None, title_icon="🔔", image_payloads=None):
        calls.update(webhook_url=webhook_url, title=title, description=description, color=color)
        return True

    monkeypatch.setattr("app.core.notification_channels.send_feishu_alert", fake_send)
    channel = SimpleNamespace(kind="feishu", is_active=True, config='{"webhook_url":"https://open.feishu.cn/open-apis/bot/v2/hook/abc-123"}')
    ok = asyncio.run(send_to_channel(channel, "测试标题", "测试正文", color=0x22C55E))
    assert ok is True
    assert calls == {
        "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/abc-123",
        "title": "测试标题",
        "description": "测试正文",
        "color": 0x22C55E,
    }


def test_send_to_all_channels_fan_out_and_any_success_wins(monkeypatch):
    calls: list[str] = []

    async def fake_send(channel, *_args, **_kwargs):
        calls.append(channel.kind)
        return channel.kind != "discord"

    monkeypatch.setattr("app.core.notification_channels.send_to_channel", fake_send)
    discord = SimpleNamespace(kind="discord", is_active=True, config="{}")
    feishu = SimpleNamespace(kind="feishu", is_active=True, config="{}")
    ok = asyncio.run(send_to_all_channels([discord, feishu], "标题", "正文"))
    assert sorted(calls) == ["discord", "feishu"]
    assert ok is True  # feishu 成功即整体成功

    ok = asyncio.run(send_to_all_channels([], "标题", "正文"))
    assert ok is False


def test_email_channel_validation_and_safe_output_preserve_password_on_edit():
    existing = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_username": "sender@example.com",
        "smtp_password": "existing-app-password",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "from_address": "sender@example.com",
        "to_addresses": ["one@example.com"],
    }
    updated = _validated_channel_config(
        "email",
        {
            **existing,
            "smtp_password": "",
            "to_addresses": "one@example.com, two@example.com",
        },
        existing=existing,
    )
    assert updated["smtp_password"] == "existing-app-password"
    assert updated["to_addresses"] == ["one@example.com", "two@example.com"]
    assert mask_endpoint("email", updated) == "on***@example.com"
    safe = safe_channel_config("email", updated)
    assert safe["has_password"] is True
    assert "smtp_password" not in safe


def test_email_provider_defaults_can_build_a_channel_from_address_and_password():
    config = _validated_channel_config(
        "email",
        {"email_address": "trader@gmail.com", "smtp_password": "app-password"},
    )
    assert config == {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_username": "trader@gmail.com",
        "smtp_password": "app-password",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "from_address": "trader@gmail.com",
        "to_addresses": ["trader@gmail.com"],
    }
    assert email_provider_preset("trader@gmail.com")["smtp_host"] == "smtp.gmail.com"
    assert email_provider_preset("trader@company.example") is None


def test_email_provider_defaults_do_not_overwrite_manual_smtp_settings():
    config = apply_email_provider_defaults(
        {
            "email_address": "trader@gmail.com",
            "smtp_host": "smtp.internal.example",
            "smtp_port": 2525,
            "smtp_use_tls": False,
            "smtp_use_ssl": False,
        }
    )
    assert config["smtp_host"] == "smtp.internal.example"
    assert config["smtp_port"] == 2525
    assert config["smtp_use_tls"] is False
    assert config["smtp_use_ssl"] is False


def test_environment_email_test_endpoint_sends_without_exposing_credentials(monkeypatch):
    calls: dict = {}

    monkeypatch.setattr(
        "app.api.monitoring_api.environment_email_config",
        lambda: {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_username": "sender@example.com",
            "smtp_password": "secret",
            "smtp_use_tls": True,
            "smtp_use_ssl": False,
            "from_address": "sender@example.com",
            "to_addresses": "receiver@example.com",
        },
    )

    async def fake_send(config, subject, text, html=None):
        calls.update(config=config, subject=subject, text=text, html=html)

    monkeypatch.setattr("app.api.monitoring_api.send_email_message", fake_send)
    result = asyncio.run(send_test_environment_email())

    assert result == {"success": True}
    assert calls["config"]["smtp_password"] == "secret"
    assert calls["subject"] == "[Market Replay] 邮件渠道测试"


def test_environment_email_test_endpoint_rejects_incomplete_configuration(monkeypatch):
    monkeypatch.setattr(
        "app.api.monitoring_api.environment_email_config",
        lambda: {"smtp_host": "smtp.example.com"},
    )
    with pytest.raises(HTTPException, match="配置不完整"):
        asyncio.run(send_test_environment_email())


def test_gmail_app_password_spaces_are_normalized_and_network_error_is_actionable():
    assert smtp_login_password({"smtp_host": "smtp.gmail.com", "smtp_password": "abcd efgh"}) == "abcdefgh"
    assert smtp_login_password({"smtp_host": "smtp.example.com", "smtp_password": "abcd efgh"}) == "abcd efgh"
    assert "出网策略" in email_error_hint(TimeoutError())


def test_email_channel_rejects_conflicting_transport_security():
    with pytest.raises(HTTPException, match="只能选择一种"):
        _validated_channel_config(
            "email",
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_use_tls": True,
                "smtp_use_ssl": True,
                "from_address": "sender@example.com",
                "to_addresses": "receiver@example.com",
            },
        )


def test_email_sender_uses_smtp_login_and_multiple_recipients(monkeypatch):
    calls: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.update(host=host, port=port, timeout=timeout)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self):
            calls["starttls"] = True

        def login(self, username, password):
            calls.update(username=username, password=password)

        def send_message(self, message, to_addrs):
            calls.update(subject=message["Subject"], to_addrs=to_addrs)

    monkeypatch.setattr("app.core.notification_channels.smtplib.SMTP", FakeSMTP)
    asyncio.run(send_email_message(
        {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_username": "sender@example.com",
            "smtp_password": "secret",
            "smtp_use_tls": True,
            "smtp_use_ssl": False,
            "from_address": "sender@example.com",
            "to_addresses": ["one@example.com", "two@example.com"],
        },
        "测试主题",
        "测试正文",
    ))
    assert calls == {
        "host": "smtp.example.com",
        "port": 587,
        "timeout": 20,
        "starttls": True,
        "username": "sender@example.com",
        "password": "secret",
        "subject": "测试主题",
        "to_addrs": ["one@example.com", "two@example.com"],
    }


def test_monitor_plan_timeframes_support_multiple_and_legacy_rows():
    plan = SimpleNamespace(timeframes='["15min", "1h", "4h", "1day", "1h"]', timeframe="4h")
    assert _plan_timeframes(plan) == ["1h", "4h", "1day"]

    legacy_plan = SimpleNamespace(timeframes=None, timeframe="1h")
    assert _plan_timeframes(legacy_plan) == ["1h"]


def _closed_daily_candles(count: int = 60) -> list[dict]:
    base = 1_700_000_000
    return [
        {
            "time": base + index * 86_400,
            "open": 100.0 + index * 0.1,
            "high": 101.0 + index * 0.1,
            "low": 99.0 + index * 0.1,
            "close": 100.5 + index * 0.1,
            "volume": 1000.0,
            "is_closed": True,
        }
        for index in range(count)
    ]


def test_monitor_daily_candles_use_multi_source_fallback(monkeypatch):
    """日线监控必须走 DailyReportSources（多源回退），而不是单一 provider。"""
    fallback_rows = [
        {**row, "_report_source_symbol": "BINANCE:AAPLUSDT"}
        for row in _closed_daily_candles()
    ]
    calls: dict = {}

    async def fake_get_candles(symbol, outputsize=180, before=None, timeframe="1day"):
        calls.update(symbol=symbol, outputsize=outputsize, before=before, timeframe=timeframe)
        return fallback_rows

    class FakeSources:
        async def get_candles(self, symbol, outputsize=180, before=None, timeframe="1day"):
            return await fake_get_candles(symbol, outputsize, before, timeframe)

    monkeypatch.setattr("app.core.monitor_engine.DailyReportSources", FakeSources)

    rows = asyncio.run(fetch_monitor_candles("AAPL", "1day", outputsize=360))
    assert calls["symbol"] == "AAPL"
    assert calls["outputsize"] == 360
    assert calls["timeframe"] == "1day"
    assert len(rows) == 60
    assert rows[-1]["_report_source_symbol"] == "BINANCE:AAPLUSDT"


def test_monitor_intraday_candles_also_use_fallback_channel(monkeypatch):
    """非日线周期同样走 DailyReportSources 多源回退通道，并保持收盘过滤。"""
    rows = _closed_daily_candles()
    calls: dict = {}

    async def fake_get_candles(symbol, outputsize=180, before=None, timeframe="1day"):
        calls.update(symbol=symbol, outputsize=outputsize, timeframe=timeframe)
        return rows

    class FakeSources:
        async def get_candles(self, symbol, outputsize=180, before=None, timeframe="1day"):
            return await fake_get_candles(symbol, outputsize, before, timeframe)

    monkeypatch.setattr("app.core.monitor_engine.DailyReportSources", FakeSources)
    monkeypatch.setattr("app.core.monitor_engine.completed_indicator_candles", lambda candles, tf: candles)

    got = asyncio.run(fetch_monitor_candles("BTC-USD", "4h", outputsize=360))
    assert calls["timeframe"] == "4h"
    assert calls["outputsize"] == 360
    assert got == rows


def _monitor_event(
    direction: str,
    *,
    event_type: str = "ma_breakout",
    strategy: str = "ma",
    severity: str = "signal",
    summary: str = "测试信号",
    period: int = 20,
) -> dict:
    return {
        "strategy": strategy,
        "event_type": event_type,
        "severity": severity,
        "direction": direction,
        "summary": summary,
        "signal_time": 1_700_000_000,
        "reference_price": 70.49,
        "payload": {"parameters": {"period": period}},
    }


def test_discord_batch_presentation_distinguishes_long_short_and_mixed():
    plan = SimpleNamespace(name="技术监控")
    long_title, long_description, long_style = _format_batch(
        plan, "BINANCE:CRCLUSDT", "1day", [_monitor_event("long")]
    )
    assert "多头机会" in long_title
    assert "CRCL-PERP" in long_title
    assert long_title.endswith("｜1D")
    assert "Binance" not in long_title
    assert "BINANCE:CRCLUSDT" not in long_description
    assert "🟢" in long_description
    assert "**核心结论**" in long_description
    assert "★★★★★ **MA突破**：测试信号" in long_description
    assert "**参考价格**：70.49" in long_description
    assert "监控计划" not in long_description
    assert "运行关系" not in long_description
    assert "**方向**" not in long_description
    assert "**周期**" not in long_description
    assert "**说明**" not in long_description
    assert long_style["color"] == 0x22C55E
    assert "circle.com" in long_style["thumbnail_url"]

    short_title, short_description, short_style = _format_batch(
        plan, "BINANCE:CRCLUSDT", "4h", [_monitor_event("short")]
    )
    assert "空头机会" in short_title
    assert "🔴" in short_description
    assert short_style["color"] == 0xEF4444

    mixed_title, _, mixed_style = _format_batch(
        plan,
        "BINANCE:CRCLUSDT",
        "1h",
        [_monitor_event("long"), _monitor_event("short")],
    )
    assert "多空信号并存" in mixed_title
    assert mixed_style["color"] == 0xF59E0B


def test_discord_batch_sorts_strength_and_merges_same_direction_ma_breakouts():
    plan = SimpleNamespace(name="技术监控")
    observation = _monitor_event(
        "long",
        event_type="macd_extreme_divergence",
        strategy="macd",
        severity="signal",
        summary="多头柱极值背离（强·强度0.8）：动能相对前一同向段极值减弱",
    )
    ma20 = _monitor_event(
        "long",
        summary="收盘上穿 MA20，实体 0.86 ATR，成交量状态 high",
        period=20,
    )
    ma60 = _monitor_event(
        "long",
        summary="收盘上穿 MA60，实体 0.86 ATR，成交量状态 high",
        period=60,
    )

    _, description, _ = _format_batch(
        plan,
        "BINANCE:CRCLUSDT",
        "1day",
        [observation, ma20, ma60],
    )

    assert description.count("★★★★★ **MA突破**") == 1
    assert "收盘同时上穿 MA20、MA60，实体 0.86 ATR，成交量状态 high" in description
    assert "**MACD柱极值背离**" in description
    assert description.index("★★★★★") < description.index("**MACD柱极值背离**")


def test_symbol_branding_uses_crypto_logo_and_safe_unknown_badge():
    btc = resolve_symbol_branding("BINANCE:BTCUSDT")
    assert btc.ticker == "BTC"
    assert btc.display_name == "BTC-PERP"
    assert "btc.png" in btc.logo_url

    crcl = resolve_symbol_branding("HL:XYZ:CRCL")
    assert crcl.display_name == "CRCL-PERP"
    assert "Hyperliquid" not in crcl.display_name

    unknown = resolve_symbol_branding("TEST.UNKNOWN")
    assert unknown.ticker == "TEST.UNKNOWN"
    assert unknown.logo_url.startswith("https://ui-avatars.com/api/")
