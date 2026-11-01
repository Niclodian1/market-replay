import asyncio
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.daily_report_analysis import (
    _market_regime,
    aggregate_hot_products,
    analyze_instrument,
    detect_report_opportunities,
    render_discord_summary,
    render_html,
    render_text,
)
from app.core.daily_report_delivery import send_report_discord, split_discord_text
from app.schemas.daily_report import (
    ChanlunChapter,
    ChanlunSignalSummary,
    ChanlunSymbolSummary,
    DailyReportPayload,
    MarketSection,
)
from app.core.daily_report_sources import DailyReportSources, normalize_a_share_symbol


def _candles(closes: list[float], *, start: int = 1_700_000_000) -> list[dict]:
    return [{
        "time": start + index * 86_400,
        "open": close - 0.2,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1000 + index * 10,
    } for index, close in enumerate(closes)]


def test_analysis_uses_two_session_ma_slope_and_shared_formulas():
    candles = _candles([float(value) for value in range(1, 131)])
    item = analyze_instrument(
        "TEST",
        "测试",
        ["热门"],
        candles,
        session_timezone="UTC",
    )

    assert item.stage == "上涨趋势"
    assert item.change_percent == (130 / 129 - 1) * 100
    assert item.amplitude_percent == 2 / 129 * 100
    assert item.ma20 is not None
    assert item.ma60 is not None
    assert item.ma120 is not None
    assert item.volume_change_percent is not None


def test_analysis_marks_index_volume_not_applicable():
    item = analyze_instrument(
        "^TEST",
        "测试指数",
        ["指数"],
        _candles([100.0] * 130),
        session_timezone="UTC",
        volume_applicable=False,
    )

    assert item.stage == "趋势不明确"
    assert item.volume_applicable is False
    assert item.volume_change_percent is None


def test_analysis_does_not_force_stage_without_ma120_history():
    item = analyze_instrument(
        "SHORT",
        "短历史",
        [],
        _candles([100.0] * 121),
        session_timezone="UTC",
    )
    assert item.stage == "数据不足"


def test_ma_regime_keeps_only_three_actionable_states_and_tactical_phase():
    bullish = analyze_instrument(
        "UP",
        "上涨",
        [],
        _candles([float(value) for value in range(1, 131)]),
        session_timezone="UTC",
    )
    assert bullish.stage == "上涨趋势"
    assert bullish.tactical_phase == "continuation"
    assert "强劲上涨趋势" in bullish.conclusion
    assert "禁止逆势做空" in bullish.trade_advice
    assert bullish.ma_structure == (
        "价格位于MA20、MA60和MA120上方；MA20、MA60和MA120斜率向上。"
    )

    rebound_closes = [float(value) for value in range(220, 95, -1)] + [100, 105, 110, 115, 120]
    rebound = analyze_instrument(
        "REBOUND",
        "反弹",
        [],
        _candles(rebound_closes),
        session_timezone="UTC",
    )
    assert rebound.stage == "下跌趋势"
    assert rebound.tactical_phase == "rebound"
    assert "下跌趋势中的短期反弹" in rebound.conclusion
    assert "价格位于MA20上方" in rebound.ma_structure
    assert "MA60和MA120斜率向下" in rebound.ma_structure


def test_all_mas_above_price_and_falling_is_downtrend_before_strict_bear_order():
    stage, phase, conclusion = _market_regime(
        3814.198,
        {20: 3954.506, 60: 4055.240, 120: 4054.357},
        {"ma20": "below", "ma60": "below", "ma120": "below"},
        {"ma20": "down", "ma60": "down", "ma120": "down"},
    )
    assert stage == "下跌趋势"
    assert phase == "continuation"
    assert "结构仍然偏空" in conclusion
    assert "强劲" not in conclusion


def test_daily_macd_momentum_uses_histogram_delta():
    # 连续下跌走势：MACD 柱为负，且近期跌幅收窄 → bearish_decelerating
    closes = [float(value) for value in range(200, 70, -1)]
    item = analyze_instrument(
        "DOWN",
        "下跌",
        [],
        _candles(closes),
        session_timezone="UTC",
    )
    assert item.momentum_state in {
        "bearish_decelerating", "bearish_stable", "bearish_accelerating", "balanced",
    }
    assert "MACD" in item.momentum_summary
    assert "★" not in item.momentum_summary


def test_hot_products_use_three_day_attention_and_turnover_weights():
    rows = [
        {"symbol": "AAA", "name": "A", "attention_score": 1.0, "turnover": 10},
        {"symbol": "AAA", "name": "A", "attention_score": 1.0, "turnover": 10},
        {"symbol": "BBB", "name": "B", "attention_score": 0.5, "turnover": 100},
        {"symbol": "CCC", "name": "C", "attention_score": 0.6, "turnover": 50},
    ]
    ranked = aggregate_hot_products(rows, limit=3)

    assert [row["symbol"] for row in ranked] == ["AAA", "BBB", "CCC"]
    assert ranked[0]["heat_score"] == 0.7
    assert ranked[1]["heat_score"] == pytest.approx(0.65)


def test_hot_products_penalize_symbols_missing_from_other_snapshot_days():
    rows = [
        {"snapshot_date": "2026-07-22", "symbol": "AAA", "attention_score": 0.8, "turnover": 10},
        {"snapshot_date": "2026-07-23", "symbol": "AAA", "attention_score": 0.8, "turnover": 10},
        {"snapshot_date": "2026-07-24", "symbol": "AAA", "attention_score": 0.8, "turnover": 10},
        {"snapshot_date": "2026-07-24", "symbol": "BBB", "attention_score": 1.0, "turnover": 10},
    ]
    ranked = aggregate_hot_products(rows, limit=2)
    assert [row["symbol"] for row in ranked] == ["AAA", "BBB"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519.SS"),
        ("SZ000001", "000001.SZ"),
        ("BJ430047", "430047.BJ"),
    ],
)
def test_a_share_symbol_normalization(raw, expected):
    assert normalize_a_share_symbol(raw) == expected


def test_a_share_fixed_index_uses_dedicated_history_before_yahoo(monkeypatch):
    class FakeFrame:
        empty = False

        def to_dict(self, _orientation):
            return [
                {
                    "date": date(2026, 7, 23),
                    "open": 3400,
                    "high": 3450,
                    "low": 3380,
                    "close": 3430,
                    "volume": 100,
                },
                {
                    "date": date(2026, 7, 24),
                    "open": 3430,
                    "high": 3480,
                    "low": 3410,
                    "close": 3460,
                    "volume": 120,
                },
            ]

    calls = []
    fake_akshare = SimpleNamespace(
        stock_zh_index_daily=lambda symbol: calls.append(symbol) or FakeFrame(),
    )

    class UnexpectedYahoo:
        async def get_time_series(self, *_args, **_kwargs):
            raise AssertionError("dedicated A-share index history should run first")

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setattr("app.core.daily_report_sources.asyncio.to_thread", inline)
    monkeypatch.setattr(DailyReportSources, "_read_cache", staticmethod(lambda *_args: None))
    monkeypatch.setattr(DailyReportSources, "_write_cache", staticmethod(lambda *_args: None))

    rows = asyncio.run(
        DailyReportSources(yahoo=UnexpectedYahoo()).get_candles("399006.SZ", 180)
    )
    assert calls == ["sz399006"]
    assert len(rows) == 2
    assert rows[-1]["close"] == 3460


def test_us_stock_falls_back_to_binance_tradfi_when_yahoo_is_empty(monkeypatch):
    class FailingYahoo:
        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Yahoo unavailable")

    class FakeBinance:
        async def get_exchange_info(self, ttl=300):
            return {"QQQUSDT": {"contractType": "TRADIFI_PERPETUAL"}}

        async def get_time_series(self, symbol, interval, outputsize, before=None):
            assert (symbol, interval, outputsize, before) == (
                "BINANCE:QQQUSDT", "1day", 180, None,
            )
            return [{
                "time": 1_700_000_000 + index * 86_400,
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 100.0 + index,
                "volume": 10.0,
                "is_closed": True,
            } for index in range(3)]

    monkeypatch.setattr(
        "app.core.daily_report_sources.pick_series_provider",
        lambda *_args: FailingYahoo(),
    )
    rows = asyncio.run(DailyReportSources(
        yahoo=FailingYahoo(),
        binance_futures=FakeBinance(),
    ).get_candles("^IXIC", 180))

    assert len(rows) == 3
    assert rows[0]["_report_source_symbol"] == "BINANCE:QQQUSDT"


def test_us_stock_falls_back_to_hyperliquid_alias_after_binance_failure(monkeypatch):
    class FailingYahoo:
        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Yahoo unavailable")

    class FakeBinance:
        async def get_exchange_info(self, ttl=300):
            return {}

        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Binance unavailable")

    class FakeHyperliquid:
        async def resolve_stock_aliases(self, symbols):
            assert symbols == ["AAPL"]
            return {"AAPL": "HL:XYZ:AAPL"}

        async def get_time_series(self, symbol, interval, outputsize, before=None):
            assert (symbol, interval, outputsize, before) == (
                "HL:XYZ:AAPL", "1day", 180, None,
            )
            return [{
                "time": 1_700_000_000 + index * 86_400,
                "open": 200.0,
                "high": 205.0,
                "low": 195.0,
                "close": 200.0 + index,
                "volume": 10.0,
            } for index in range(3)]

    monkeypatch.setattr(
        "app.core.daily_report_sources.pick_series_provider",
        lambda *_args: FailingYahoo(),
    )
    rows = asyncio.run(DailyReportSources(
        yahoo=FailingYahoo(),
        binance_futures=FakeBinance(),
        hyperliquid=FakeHyperliquid(),
    ).get_candles("AAPL", 180))

    assert len(rows) == 3
    assert rows[0]["_report_source_symbol"] == "HL:XYZ:AAPL"


def test_crypto_falls_back_to_binance_futures_when_spot_fails(monkeypatch):
    class FailingSpot:
        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Binance spot unavailable")

    class FakeBinance:
        async def get_exchange_info(self, ttl=300):
            return {"BTCUSDT": {"contractType": "PERPETUAL"}}

        async def get_time_series(self, symbol, interval, outputsize, before=None):
            assert (symbol, interval, outputsize) == ("BINANCE:BTCUSDT", "1day", 180)
            return [{
                "time": 1_700_000_000 + index * 86_400,
                "open": 30000.0,
                "high": 31000.0,
                "low": 29000.0,
                "close": 30000.0 + index,
                "volume": 10.0,
            } for index in range(3)]

    monkeypatch.setattr(
        "app.core.daily_report_sources.pick_series_provider",
        lambda *_args: FailingSpot(),
    )
    rows = asyncio.run(DailyReportSources(
        binance_futures=FakeBinance(),
    ).get_candles("BTC-USD", 180))

    assert len(rows) == 3
    assert rows[0]["_report_source_symbol"] == "BINANCE:BTCUSDT"


def test_crypto_falls_back_to_hyperliquid_when_binance_missing(monkeypatch):
    class FailingSpot:
        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Binance spot unavailable")

    class EmptyBinance:
        async def get_exchange_info(self, ttl=300):
            return {"BTCUSDT": {"contractType": "PERPETUAL"}}

        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Binance futures unavailable")

    class FakeHyperliquid:
        async def get_time_series(self, symbol, interval, outputsize, before=None):
            assert (symbol, interval, outputsize) == ("HL:BTC", "1day", 180)
            return [{
                "time": 1_700_000_000 + index * 86_400,
                "open": 30000.0,
                "high": 31000.0,
                "low": 29000.0,
                "close": 30000.0 + index,
                "volume": 10.0,
            } for index in range(3)]

    monkeypatch.setattr(
        "app.core.daily_report_sources.pick_series_provider",
        lambda *_args: FailingSpot(),
    )
    rows = asyncio.run(DailyReportSources(
        binance_futures=EmptyBinance(),
        hyperliquid=FakeHyperliquid(),
    ).get_candles("BTC-USD", 180))

    assert len(rows) == 3
    assert rows[0]["_report_source_symbol"] == "HL:BTC"


def test_gold_falls_back_to_binance_xau_when_dukascopy_fails(monkeypatch):
    class FailingDukascopy:
        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Dukascopy unavailable")

    class FakeBinance:
        async def get_exchange_info(self, ttl=300):
            return {"XAUUSDT": {"contractType": "TRADIFI_PERPETUAL"}}

        async def get_time_series(self, symbol, interval, outputsize, before=None):
            assert (symbol, interval, outputsize) == ("BINANCE:XAUUSDT", "1day", 180)
            return [{
                "time": 1_700_000_000 + index * 86_400,
                "open": 1900.0,
                "high": 1920.0,
                "low": 1890.0,
                "close": 1905.0 + index,
                "volume": 10.0,
            } for index in range(3)]

    monkeypatch.setattr(
        "app.core.daily_report_sources.pick_series_provider",
        lambda *_args: FailingDukascopy(),
    )
    rows = asyncio.run(DailyReportSources(
        binance_futures=FakeBinance(),
    ).get_candles("XAUUSD", 180))

    assert len(rows) == 3
    assert rows[0]["_report_source_symbol"] == "BINANCE:XAUUSDT"


def test_gold_falls_back_to_hyperliquid_paxg(monkeypatch):
    class FailingDukascopy:
        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Dukascopy unavailable")

    class EmptyBinance:
        async def get_exchange_info(self, ttl=300):
            return {}

        async def get_time_series(self, *_args, **_kwargs):
            raise ValueError("Binance XAU unavailable")

    class FakeHyperliquid:
        async def get_time_series(self, symbol, interval, outputsize, before=None):
            assert (symbol, interval, outputsize) == ("HL:PAXG", "1day", 180)
            return [{
                "time": 1_700_000_000 + index * 86_400,
                "open": 1900.0,
                "high": 1920.0,
                "low": 1890.0,
                "close": 1905.0 + index,
                "volume": 10.0,
            } for index in range(3)]

    monkeypatch.setattr(
        "app.core.daily_report_sources.pick_series_provider",
        lambda *_args: FailingDukascopy(),
    )
    rows = asyncio.run(DailyReportSources(
        binance_futures=EmptyBinance(),
        hyperliquid=FakeHyperliquid(),
    ).get_candles("XAUUSD=X", 180))

    assert len(rows) == 3
    assert rows[0]["_report_source_symbol"] == "HL:PAXG"


def test_opportunity_module_allows_only_ma_breakout_and_confirmed_divergence():
    events = [
        {
            "event_type": "ma_breakout", "direction": "long", "severity": "signal",
            "strategy": "ma", "summary": "突破", "signal_time": 1,
        },
        {
            "event_type": "ma_retest", "direction": "long", "severity": "signal",
            "strategy": "ma", "summary": "回踩", "signal_time": 1,
        },
    ]
    macd_events = [
        {
            "event_type": "macd_extreme_divergence", "direction": "short", "severity": "signal",
            "strategy": "macd", "summary": "背离", "signal_time": 1,
        },
    ]
    with (
        patch("app.core.daily_report_analysis.detect_ma_events", return_value=events),
        patch("app.core.daily_report_analysis.detect_macd_events", return_value=macd_events),
    ):
        output = detect_report_opportunities("us_stock", "AAPL", "Apple", _candles([100.0] * 130))

    assert {item.event_type for item in output} == {
        "ma_breakout",
        "macd_extreme_divergence",
    }
    assert all(item.stars == 5 for item in output)


def test_renderers_are_focused_and_do_not_leak_sources_or_runtime_details():
    instrument = analyze_instrument(
        "BTC-USD",
        "比特币",
        ["市场基准", "热门"],
        _candles([float(value) for value in range(1, 131)]),
        session_timezone="UTC",
    )
    payload = DailyReportPayload(
        report_date="2026-07-24",
        timezone="Asia/Taipei",
        generated_at="2026-07-24 08:00",
        markets=[MarketSection(
            market="crypto",
            title="加密货币",
            session_date=instrument.session_date,
            instruments=[instrument],
            hot_symbols=["BTC-USD"],
        )],
    )

    text = render_text(payload)
    html = render_html(payload)
    discord = render_discord_summary(payload)
    for rendered in (text, html, discord):
        assert "BTC-USD" in rendered
        assert "数据源" not in rendered
        assert "监控计划" not in rendered
        assert "运行关系" not in rendered
    for rendered in (text, html):
        assert "均线结构" in rendered
        assert "动能变化" in rendered
        assert "交易建议" in rendered
        assert "风险关注" in rendered
        assert f"MA20 {instrument.ma20:,.2f}" not in rendered
        assert f"MA60 {instrument.ma60:,.2f}" not in rendered
        assert f"MA120 {instrument.ma120:,.2f}" not in rendered


def test_renderers_include_chanlun_chapter_without_leaking_sources():
    payload = DailyReportPayload(
        report_date="2026-08-07",
        timezone="Asia/Taipei",
        generated_at="2026-08-07 08:00",
        markets=[],
        chanlun=ChanlunChapter(entries=[
            ChanlunSymbolSummary(
                symbol="BTC-USD",
                name="比特币",
                timeframe="1day",
                signals=[
                    ChanlunSignalSummary(
                        k="一买", direction="long", state="confirmed", tier="明显",
                        t=1_752_000_000, price=60_000.0,
                        why="C段背驰", sub="次级别无同向趋势背驰 ✗", upper="—",
                    ),
                ],
                edge="线段向上延伸中",
            ),
        ]),
    )

    text = render_text(payload)
    html = render_html(payload)
    discord = render_discord_summary(payload)
    for rendered in (text, html, discord):
        assert "缠论结构" in rendered
        assert "一买" in rendered
        assert "比特币" in rendered
        assert "数据源" not in rendered
        assert "监控计划" not in rendered
        assert "运行关系" not in rendered
    assert "chanlun-card" in html
    assert "C段背驰" in text


def test_renderers_skip_empty_chanlun_chapter():
    payload = DailyReportPayload(
        report_date="2026-08-07",
        timezone="Asia/Taipei",
        generated_at="2026-08-07 08:00",
        markets=[],
        chanlun=ChanlunChapter(entries=[]),
    )
    text = render_text(payload)
    html = render_html(payload)
    assert "缠论结构" not in text
    assert "<h2>缠论结构</h2>" not in html
    assert "class='chanlun-card'" not in html


def test_html_email_uses_chinese_font_stack_and_directional_highlights():
    bullish = analyze_instrument(
        "BULL",
        "多头示例",
        [],
        _candles([float(value) for value in range(1, 131)]),
        session_timezone="UTC",
    )
    bearish = analyze_instrument(
        "BEAR",
        "空头示例",
        [],
        _candles([float(value) for value in range(200, 70, -1)]),
        session_timezone="UTC",
    )
    payload = DailyReportPayload(
        report_date="2026-07-26",
        timezone="Asia/Taipei",
        generated_at="2026-07-26 08:00",
        markets=[MarketSection(
            market="us_stock",
            title="美股",
            session_date=bullish.session_date,
            instruments=[bullish, bearish],
        )],
    )
    html = render_html(payload)

    assert '"PingFang SC"' in html
    assert '"Microsoft YaHei"' in html
    assert "tone-bull" in html
    assert "tone-bear" in html
    assert "badge-bull'>看多" in html
    assert "badge-bear'>看空" in html
    assert "advice-bull" in html
    assert "advice-bear" in html
    assert "#16a34a" in html
    assert "#dc2626" in html


def test_discord_report_splits_full_email_content_without_losing_text(monkeypatch):
    source = "每日市场日报｜2026-08-03\n\n" + "市场分析内容。\n" * 700
    chunks = split_discord_text(source)
    assert len(chunks) > 1
    assert all(len(chunk) <= 3900 for chunk in chunks)
    assert "".join(chunks) == source

    calls = []

    async def fake_send(_url, title, description, **_kwargs):
        calls.append((title, description))
        return True

    monkeypatch.setattr("app.core.daily_report_delivery.send_discord_alert", fake_send)
    asyncio.run(send_report_discord("2026-08-03", source, webhook_url="https://discord.example/webhook"))
    assert len(calls) == len(chunks)
    assert calls[0][0] == "每日市场日报｜2026-08-03（1/2）"
    assert "".join(description for _title, description in calls) == source
