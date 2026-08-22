"""Pure calculations and deterministic renderers for the 08:00 daily report."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from typing import Iterable
from zoneinfo import ZoneInfo

from app.core.indicators import _atr, _macd, _sma
from app.core.signal_strength import evaluate_signal_strength, signal_type_label
from app.core.signals.monitor_signals import detect_ma_events, detect_macd_events
from app.schemas.daily_report import (
    CalendarEventItem,
    ChanlunChapter,
    ChanlunSymbolSummary,
    DailyReportPayload,
    InstrumentAnalysis,
    MarketSection,
    OpportunityItem,
)

MA_PERIODS = (20, 60, 120)
MA_POSITION_ATR_THRESHOLD = 0.10
MA_SLOPE_ATR_THRESHOLD = 0.05
MACD_HISTOGRAM_ATR_THRESHOLD = 0.02
MACD_DELTA_ATR_THRESHOLD = 0.01
ALLOWED_OPPORTUNITY_EVENTS = {"ma_breakout", "macd_extreme_divergence"}
MARKET_TITLES = {
    "crypto": "加密货币",
    "a_share": "A股交易前瞻",
    "us_stock": "美股",
    "commodity": "商品",
}


def _pct(current: float, previous: float) -> float:
    return (current / previous - 1.0) * 100 if previous else 0.0


def _session_date(timestamp: int, timezone_name: str) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ZoneInfo(timezone_name)).date().isoformat()


def _relative_state(value: float, reference: float | None, atr: float | None) -> str:
    if reference is None or atr is None or atr <= 0:
        return "unknown"
    distance = (value - reference) / atr
    if distance > MA_POSITION_ATR_THRESHOLD:
        return "above"
    if distance < -MA_POSITION_ATR_THRESHOLD:
        return "below"
    return "nearby"


def _slope_state(values: list[float | None], atr: float | None) -> str:
    if len(values) < 3 or values[-1] is None or values[-3] is None or atr is None or atr <= 0:
        return "unknown"
    slope = (float(values[-1]) - float(values[-3])) / atr
    if slope > MA_SLOPE_ATR_THRESHOLD:
        return "up"
    if slope < -MA_SLOPE_ATR_THRESHOLD:
        return "down"
    return "flat"


def _cn_join(values: list[str]) -> str:
    if len(values) <= 1:
        return values[0] if values else ""
    return "、".join(values[:-1]) + "和" + values[-1]


def _ma_structure_text(positions: dict[str, str], slopes: dict[str, str]) -> str:
    position_labels = {"above": "上方", "below": "下方", "nearby": "附近", "unknown": "关系不明"}
    slope_labels = {"up": "向上", "down": "向下", "flat": "基本走平", "unknown": "方向不明"}

    position_groups: dict[str, list[str]] = {}
    slope_groups: dict[str, list[str]] = {}
    for period in MA_PERIODS:
        name = f"MA{period}"
        position_groups.setdefault(positions.get(f"ma{period}", "unknown"), []).append(name)
        slope_groups.setdefault(slopes.get(f"ma{period}", "unknown"), []).append(name)

    position_bits = [
        f"价格位于{_cn_join(names)}{position_labels[state]}"
        for state, names in position_groups.items()
    ]
    slope_bits = [
        f"{_cn_join(names)}斜率{slope_labels[state]}"
        for state, names in slope_groups.items()
    ]
    return "，".join(position_bits) + "；" + "，".join(slope_bits) + "。"


def _market_regime(
    close: float,
    latest_ma: dict[int, float | None],
    positions: dict[str, str],
    slopes: dict[str, str],
) -> tuple[str, str, str]:
    ma20, ma60, ma120 = latest_ma[20], latest_ma[60], latest_ma[120]
    if any(value is None for value in (ma20, ma60, ma120)):
        return "数据不足", "unclear", "均线历史不足，暂时无法判断市场趋势。"

    bullish = (
        (
            positions["ma120"] == "above"
            and float(ma60) > float(ma120)
            and slopes["ma60"] == "up"
            and slopes["ma120"] in {"up", "flat"}
        )
        or (
            all(positions[f"ma{period}"] == "above" for period in MA_PERIODS)
            and all(slopes[f"ma{period}"] == "up" for period in MA_PERIODS)
        )
    )
    bearish = (
        (
            positions["ma120"] == "below"
            and float(ma60) < float(ma120)
            and slopes["ma60"] == "down"
            and slopes["ma120"] in {"down", "flat"}
        )
        or (
            all(positions[f"ma{period}"] == "below" for period in MA_PERIODS)
            and all(slopes[f"ma{period}"] == "down" for period in MA_PERIODS)
        )
    )
    strong_bullish = (
        close > float(ma20) > float(ma60) > float(ma120)
        and all(slopes[f"ma{period}"] == "up" for period in MA_PERIODS)
    )
    strong_bearish = (
        close < float(ma20) < float(ma60) < float(ma120)
        and all(slopes[f"ma{period}"] == "down" for period in MA_PERIODS)
    )

    if bullish:
        pullback = positions["ma20"] != "above" or slopes["ma20"] == "down"
        if strong_bullish:
            return "上涨趋势", "continuation", "市场处于强劲上涨趋势，三条均线形成多头排列且方向向上。"
        if pullback:
            return "上涨趋势", "pullback", "中长期上涨结构尚未改变，但市场正在经历短期回调。"
        return "上涨趋势", "continuation", "市场处于上涨趋势，当前结构仍然偏多。"

    if bearish:
        rebound = positions["ma20"] != "below" or slopes["ma20"] == "up"
        if strong_bearish:
            return "下跌趋势", "continuation", "市场处于强劲下跌趋势，三条均线形成空头排列且方向向下。"
        if rebound:
            return "下跌趋势", "rebound", "中长期下跌结构尚未改变，当前属于下跌趋势中的短期反弹。"
        return "下跌趋势", "continuation", "市场处于下跌趋势，当前结构仍然偏空。"

    return "趋势不明确", "unclear", "均线结构交错，市场方向尚不明确。"


def _momentum_analysis(candles: list[dict]) -> tuple[str, str]:
    closes = [float(row["close"]) for row in candles]
    highs = [float(row["high"]) for row in candles]
    lows = [float(row["low"]) for row in candles]
    dif, _, histogram = _macd(closes)
    atr_values = _atr(highs, lows, closes, 14)
    if len(candles) < 2 or histogram[-1] is None or histogram[-2] is None or not atr_values[-1]:
        return "unknown", "MACD历史不足，暂时无法判断动能变化。"

    atr_now = float(atr_values[-1])
    hist_now = float(histogram[-1]) / atr_now
    hist_delta = (float(histogram[-1]) - float(histogram[-2])) / atr_now
    dif_now = float(dif[-1]) if dif[-1] is not None else 0.0
    regime = "零轴上方" if dif_now > 0 else "零轴下方" if dif_now < 0 else "零轴附近"

    if hist_now > MACD_HISTOGRAM_ATR_THRESHOLD:
        if hist_delta > MACD_DELTA_ATR_THRESHOLD:
            return "bullish_accelerating", f"MACD位于{regime}，上涨动能继续增强。"
        if hist_delta < -MACD_DELTA_ATR_THRESHOLD:
            return "bullish_decelerating", f"MACD位于{regime}，上涨动能正在减弱。"
        return "bullish_stable", f"MACD位于{regime}，上涨动能大致稳定。"
    if hist_now < -MACD_HISTOGRAM_ATR_THRESHOLD:
        if hist_delta < -MACD_DELTA_ATR_THRESHOLD:
            return "bearish_accelerating", f"MACD位于{regime}，下跌动能继续增强。"
        if hist_delta > MACD_DELTA_ATR_THRESHOLD:
            return "bearish_decelerating", f"MACD位于{regime}，下跌动能正在减弱。"
        return "bearish_stable", f"MACD位于{regime}，下跌动能大致稳定。"
    return "balanced", f"MACD位于{regime}，多空动能接近平衡。"


def _trading_guidance(stage: str, tactical_phase: str) -> tuple[str, str]:
    if stage == "上涨趋势":
        if tactical_phase == "pullback":
            return (
                "中长期上涨结构没有改变。已有多头仓位重点管理风险，未持仓者等待日线止跌并重新出现动能增强后寻找顺势多头机会。",
                "若价格进一步跌破MA60且MA60转为向下，上涨趋势的可靠性将明显下降。",
            )
        return (
            "主策略禁止逆势做空。已有多头仓位可以继续持有；未持仓者避免追高，等待日线回调并重新转强后寻找建仓机会。",
            "若价格跌破MA20且MA20转为向下，市场可能进入短期回调；中长期上涨趋势暂未被破坏。",
        )
    if stage == "下跌趋势":
        if tactical_phase == "rebound":
            return (
                "短期反弹不等于趋势反转，不追涨、不提前抄底。可做空品种等待反弹承压后寻找空头机会，不能做空的现货品种以减仓或观望为主。",
                "若价格进一步站上MA60且MA60转为向上，中长期空头结构才开始弱化。",
            )
        return (
            "主策略禁止逆势抄底。已有多头仓位应控制风险；可做空品种等待反弹承压后寻找空头机会，不能做空的现货品种以减仓或观望为主。",
            "若价格站上MA20且MA20转为向上，市场可能进入短期反弹；中长期下跌趋势暂未改变。",
        )
    if stage == "数据不足":
        return (
            "历史数据不足，不根据均线建立方向性仓位。",
            "等待MA20、MA60和MA120均具备完整历史后再判断。",
        )
    return (
        "当前不适合使用趋势跟随策略，建议降低仓位并等待方向确认。",
        "关注价格与三条均线能否形成一致方向，以及MA60和MA120是否同步转向。",
    )


def analyze_instrument(
    symbol: str,
    name: str,
    tags: Iterable[str],
    candles: list[dict],
    *,
    session_timezone: str,
    volume_applicable: bool = True,
) -> InstrumentAnalysis:
    """Calculate the shared instrument template from ascending, completed daily bars."""
    if len(candles) < 2:
        raise ValueError(f"{symbol} 至少需要两根完整日线")
    if not candles[-1].get("is_closed", True):
        # 未收盘末根：数据未确认，不产生日报状态（调用方捕获后跳过该品种）
        raise ValueError(f"{symbol} 末根K线未收盘（is_closed=False），不产生日报状态")
    # M1 整改：cutoff 预检必须在读取任何 OHLC 之前。任意位置出现显式未收盘
    # （is_closed=False）即数据未确认——即使该 bar 之后又有完整 K 线也不能绕过
    # cutoff；发现即抛受控 ValueError（而非 KeyError）。字段缺省视为已收盘。
    for i, row in enumerate(candles):
        if not row.get("is_closed", True):
            raise ValueError(
                f"{symbol} 存在未收盘K线（is_closed=False，index={i}），"
                f"cutoff 之后的数据未确认，不产生日报状态"
            )

    current, previous = candles[-1], candles[-2]
    close = float(current["close"])
    previous_close = float(previous["close"])
    low = float(current["low"])
    high = float(current["high"])
    volume = float(current.get("volume") or 0)
    previous_volume = float(previous.get("volume") or 0)
    closes = [float(row["close"]) for row in candles]
    ma_values = {period: _sma(closes, period) for period in MA_PERIODS}
    latest_ma = {
        period: (float(values[-1]) if values and values[-1] is not None else None)
        for period, values in ma_values.items()
    }
    distances = {
        f"ma{period}": (_pct(close, value) if value else None)
        for period, value in latest_ma.items()
    }

    highs = [float(row["high"]) for row in candles]
    lows = [float(row["low"]) for row in candles]
    atr_values = _atr(highs, lows, closes, 14)
    atr_now = float(atr_values[-1]) if atr_values and atr_values[-1] else None
    positions = {
        f"ma{period}": _relative_state(close, latest_ma[period], atr_now)
        for period in MA_PERIODS
    }
    slopes = {
        f"ma{period}": _slope_state(ma_values[period], atr_now)
        for period in MA_PERIODS
    }
    ma_structure = _ma_structure_text(positions, slopes)
    # M1：stage / tactical_phase / conclusion 改从统一 MarketState 适配器获得，
    # 输出与原 _market_regime 逐字段一致（legacy parity 测试锁定）。
    from app.core.market_state import precompute_market_states
    from app.core.market_state_adapters import daily_stage_from_state

    states = precompute_market_states(candles, volume_applicable=volume_applicable)
    stage, tactical_phase, conclusion = daily_stage_from_state(states[-1])
    momentum_state, momentum_summary = _momentum_analysis(candles)
    trade_advice, risk_focus = _trading_guidance(stage, tactical_phase)

    change_percent = _pct(close, previous_close)
    amplitude = (high - low) / previous_close * 100 if previous_close else 0.0
    volume_change = (
        _pct(volume, previous_volume)
        if volume_applicable and volume > 0 and previous_volume > 0
        else None
    )
    return InstrumentAnalysis(
        symbol=symbol,
        name=name,
        tags=list(dict.fromkeys(tags)),
        session_date=_session_date(int(current["time"]), session_timezone),
        close=close,
        change_percent=change_percent,
        low=low,
        high=high,
        amplitude_percent=amplitude,
        volume_change_percent=volume_change,
        volume_applicable=volume_applicable,
        ma20=latest_ma[20],
        ma60=latest_ma[60],
        ma120=latest_ma[120],
        ma_distance_percent=distances,
        ma_positions=positions,
        ma_slopes=slopes,
        ma_structure=ma_structure,
        stage=stage,
        tactical_phase=tactical_phase,
        momentum_state=momentum_state,
        momentum_summary=momentum_summary,
        trade_advice=trade_advice,
        risk_focus=risk_focus,
        conclusion=conclusion,
    )


def aggregate_hot_products(
    snapshots: Iterable[dict],
    *,
    limit: int = 3,
) -> list[dict]:
    """Aggregate up to three days of normalized attention and turnover scores."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in snapshots:
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            grouped[symbol].append(row)
    if not grouped:
        return []
    snapshot_days = {
        str(row.get("snapshot_date"))
        for rows in grouped.values()
        for row in rows
        if row.get("snapshot_date")
    }

    average_turnover = {
        symbol: sum(max(0.0, float(row.get("turnover") or 0)) for row in rows) / len(rows)
        for symbol, rows in grouped.items()
    }
    ordered_turnover = sorted(set(average_turnover.values()))

    def turnover_percentile(value: float) -> float:
        if len(ordered_turnover) <= 1:
            return 1.0 if value > 0 else 0.0
        return ordered_turnover.index(value) / (len(ordered_turnover) - 1)

    ranked: list[dict] = []
    for symbol, rows in grouped.items():
        attention_denominator = len(snapshot_days) or len(rows)
        attention = (
            sum(max(0.0, min(1.0, float(row.get("attention_score") or 0))) for row in rows)
            / attention_denominator
        )
        activity = turnover_percentile(average_turnover[symbol])
        ranked.append({
            "symbol": symbol,
            "name": next((str(row.get("name") or "") for row in reversed(rows) if row.get("name")), symbol),
            "attention_score": attention,
            "turnover_score": activity,
            "heat_score": attention * 0.7 + activity * 0.3,
        })
    return sorted(ranked, key=lambda row: (-row["heat_score"], row["symbol"]))[:max(1, limit)]


def detect_report_opportunities(
    market: str,
    symbol: str,
    name: str,
    candles: list[dict],
) -> list[OpportunityItem]:
    events = [
        *detect_ma_events(candles, "1day", periods=MA_PERIODS),
        *detect_macd_events(candles, "1day"),
    ]
    output: list[OpportunityItem] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        event_type = str(event.get("event_type") or "")
        direction = str(event.get("direction") or "")
        if event_type not in ALLOWED_OPPORTUNITY_EVENTS or direction not in {"long", "short"}:
            continue
        key = (event_type, direction)
        if key in seen:
            continue
        seen.add(key)
        strength = evaluate_signal_strength(event_type, "1day", str(event.get("severity") or ""))
        output.append(OpportunityItem(
            market=market,
            symbol=symbol,
            name=name,
            direction=direction,
            event_type=event_type,
            signal_name=signal_type_label(event_type, str(event.get("strategy") or "")),
            stars=strength.stars,
            strength_label=strength.label,
            summary=str(event.get("summary") or ""),
            signal_time=int(event["signal_time"]),
        ))
    return output


def _number(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _chanlun_signal_line(sig, *, timezone_name: str) -> str:
    icon = "🟢" if sig.direction == "long" else "🔴"
    state = f"（{sig.tier}·已确认）" if sig.tier else "（已确认）" if sig.state == "confirmed" else "（在途）" if sig.state == "forming" else ""
    when = _session_date(int(sig.t), timezone_name) if sig.t else "—"
    parts = [f"{icon} **{sig.k}**{state} @{when}　参考 {_number(sig.price, 4)}"]
    if sig.why:
        parts.append(f"　依据：{sig.why}")
    if sig.sub or sig.upper:
        parts.append(f"　联动：{sig.sub} · {sig.upper}")
    return "\n".join(parts)


def _chanlun_symbol_text(entry: ChanlunSymbolSummary, *, timezone_name: str) -> list[str]:
    label = f"{entry.name}（{entry.symbol}）" if entry.name and entry.name != entry.symbol else entry.symbol
    lines = [f"· {label}（{entry.timeframe}）"]
    for sig in entry.signals:
        lines.extend(f"  {line}" for line in _chanlun_signal_line(sig, timezone_name=timezone_name).split("\n"))
    if entry.edge:
        lines.append(f"  末端结构：{entry.edge}")
    return lines


def _chanlun_section_text(chanlun: ChanlunChapter | None, *, timezone_name: str) -> list[str]:
    if chanlun is None or not chanlun.entries:
        return []
    lines = ["【缠论结构】"]
    for entry in chanlun.entries:
        if not entry.signals and not entry.edge:
            continue
        lines.extend(_chanlun_symbol_text(entry, timezone_name=timezone_name))
        lines.append("")
    if len(lines) == 1:
        return []
    return lines


def _instrument_text(item: InstrumentAnalysis) -> list[str]:
    label = f"{item.name}（{item.symbol}）" if item.name and item.name != item.symbol else item.symbol
    tags = f" [{'/'.join(item.tags)}]" if item.tags else ""
    volume = (
        f"{item.volume_change_percent:+.2f}%"
        if item.volume_applicable and item.volume_change_percent is not None
        else "不适用"
    )
    return [
        f"{label}{tags}",
        f"  收盘 {_number(item.close, 4)}｜涨跌 {item.change_percent:+.2f}%｜区间 {_number(item.low, 4)}–{_number(item.high, 4)}｜振幅 {item.amplitude_percent:.2f}%",
        f"  成交量较前日 {volume}",
        f"  市场状态：{item.conclusion}",
        f"  均线结构：{item.ma_structure}",
        f"  动能变化：{item.momentum_summary}",
        f"  交易建议：{item.trade_advice}",
        f"  风险关注：{item.risk_focus}",
    ]


def render_text(payload: DailyReportPayload) -> str:
    lines = [f"每日市场日报｜{payload.report_date}", ""]
    for section in payload.markets:
        lines.append(f"【{section.title}】")
        if section.status == "unavailable":
            lines.extend(["数据暂不可用", ""])
            continue
        if section.hot_symbols:
            lines.append(f"热门产品：{'、'.join(section.hot_symbols)}")
        for item in section.instruments:
            lines.extend(_instrument_text(item))
        lines.append("")

    lines.extend(_chanlun_section_text(payload.chanlun, timezone_name=payload.timezone))
    lines.append("【日线机会】")
    if not payload.opportunities:
        lines.append("暂无日线级别机会")
    else:
        for item in sorted(payload.opportunities, key=lambda row: (-row.stars, row.symbol)):
            side = "做多" if item.direction == "long" else "做空"
            lines.append(f"{'★' * item.stars}{'☆' * (5 - item.stars)} {item.symbol}｜{side}｜{item.signal_name}｜{item.summary}")
    lines.extend(["", "【未来7日重点事件】"])
    if not payload.events:
        lines.append("未来7日无重点事件")
    else:
        for item in payload.events:
            timing = f"｜{item.timing}" if item.timing else ""
            lines.append(f"{item.starts_at}｜{item.name}{timing}")
    return "\n".join(lines).strip()


def _market_tone(item: InstrumentAnalysis) -> tuple[str, str]:
    if item.stage == "上涨趋势":
        return "bull", "看多"
    if item.stage == "下跌趋势":
        return "bear", "看空"
    return "neutral", "等待确认"


def _momentum_tone(state: str) -> str:
    if state.startswith("bullish"):
        return "bull"
    if state.startswith("bearish"):
        return "bear"
    return "neutral"


def _chanlun_symbol_html(entry: ChanlunSymbolSummary, *, timezone_name: str) -> str:
    label = f"{entry.name}（{entry.symbol}）" if entry.name and entry.name != entry.symbol else entry.symbol
    signal_html = "".join(
        f"<p class='chanlun-sig chanlun-{'long' if sig.direction == 'long' else 'short'}'>"
        f"<strong>{escape(sig.k)}</strong>"
        f"{escape('（' + (sig.tier or '已确认') + '）') if sig.tier or sig.state == 'confirmed' else escape('（在途）') if sig.state == 'forming' else ''}"
        f" @ {escape(_session_date(int(sig.t), timezone_name) if sig.t else '—')}"
        f"｜参考 {_number(sig.price, 4)}</p>"
        + (f"<p class='chanlun-detail'>依据：{escape(sig.why)}</p>" if sig.why else "")
        + (f"<p class='chanlun-detail'>联动：{escape(sig.sub)} · {escape(sig.upper)}</p>" if sig.sub or sig.upper else "")
        for sig in entry.signals
    )
    edge_html = f"<p class='chanlun-edge'>末端结构：{escape(entry.edge)}</p>" if entry.edge else ""
    return (
        f"<div class='chanlun-card'><h3>{escape(label)}"
        f"<span class='muted'>{escape(entry.timeframe)}</span></h3>"
        f"{signal_html}{edge_html}</div>"
    )


def _chanlun_section_html(chanlun: ChanlunChapter | None, *, timezone_name: str) -> str:
    if chanlun is None or not chanlun.entries:
        return ""
    cards = "".join(
        _chanlun_symbol_html(entry, timezone_name=timezone_name)
        for entry in chanlun.entries
        if entry.signals or entry.edge
    )
    if not cards:
        return ""
    return f"<h2>缠论结构</h2>{cards}"


def render_html(payload: DailyReportPayload) -> str:
    section_html: list[str] = []
    for section in payload.markets:
        body: list[str] = [f"<h2>{escape(section.title)}</h2>"]
        if section.status == "unavailable":
            body.append("<p>数据暂不可用</p>")
        else:
            if section.hot_symbols:
                body.append(f"<p><strong>热门产品：</strong>{escape('、'.join(section.hot_symbols))}</p>")
            for item in section.instruments:
                label = f"{item.name}（{item.symbol}）" if item.name and item.name != item.symbol else item.symbol
                tags = f" · {' / '.join(item.tags)}" if item.tags else ""
                tone, tone_label = _market_tone(item)
                momentum_tone = _momentum_tone(item.momentum_state)
                change_tone = "bull" if item.change_percent > 0 else "bear" if item.change_percent < 0 else "neutral"
                volume = (
                    f"{item.volume_change_percent:+.2f}%"
                    if item.volume_applicable and item.volume_change_percent is not None
                    else "不适用"
                )
                body.append(
                    f"<div class='instrument tone-{tone}'>"
                    f"<h3>{escape(label + tags)}"
                    f"<span class='badge badge-{tone}'>{tone_label}</span></h3>"
                    f"<p class='metrics'>收盘 {_number(item.close, 4)}｜"
                    f"涨跌 <span class='text-{change_tone}'>{item.change_percent:+.2f}%</span>｜"
                    f"区间 {_number(item.low, 4)}–{_number(item.high, 4)}｜振幅 {item.amplitude_percent:.2f}%</p>"
                    f"<p class='volume'>成交量较前日 {volume}</p>"
                    f"<div class='state-box state-{tone}'><strong>市场状态</strong>"
                    f"<p>{escape(item.conclusion)}</p></div>"
                    f"<p><strong>均线结构：</strong>{escape(item.ma_structure)}</p>"
                    f"<div class='momentum momentum-{momentum_tone}'><strong>动能变化</strong>"
                    f"<p>{escape(item.momentum_summary)}</p></div>"
                    f"<div class='advice advice-{tone}'><strong>交易建议</strong>"
                    f"<p>{escape(item.trade_advice)}</p></div>"
                    f"<p class='risk'><strong>风险关注：</strong>{escape(item.risk_focus)}</p></div>"
                )
        section_html.append("".join(body))

    if payload.opportunities:
        opportunity_html = "".join(
            f"<li class='opportunity opportunity-{'bull' if item.direction == 'long' else 'bear'}'>"
            f"{'★' * item.stars}{'☆' * (5 - item.stars)} "
            f"{escape(item.symbol)}｜"
            f"<strong>{'做多' if item.direction == 'long' else '做空'}</strong>｜"
            f"{escape(item.signal_name)}｜{escape(item.summary)}</li>"
            for item in sorted(payload.opportunities, key=lambda row: (-row.stars, row.symbol))
        )
    else:
        opportunity_html = "<li>暂无日线级别机会</li>"
    event_html = "".join(
        f"<li>{escape(item.starts_at)}｜{escape(item.name)}"
        f"{'｜' + escape(item.timing) if item.timing else ''}</li>"
        for item in payload.events
    ) or "<li>未来7日无重点事件</li>"

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;background:#f4f6f8;color:#172033;line-height:1.65;margin:0;padding:24px}}
.report{{max-width:920px;margin:auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;padding:28px}}
h1{{font-size:27px;line-height:1.25;margin:0 0 8px;color:#111827;letter-spacing:-.3px}}
h2{{font-size:20px;margin:32px 0 12px;border-bottom:2px solid #e5e7eb;padding-bottom:8px;color:#111827}}
h3{{font-size:17px;line-height:1.4;margin:0 0 8px;color:#111827}}p{{margin:6px 0}}
.instrument{{margin:12px 0;padding:16px 18px;border:1px solid #e5e7eb;border-left-width:5px;border-radius:10px;background:#ffffff}}
.tone-bull{{border-left-color:#16a34a}}.tone-bear{{border-left-color:#dc2626}}.tone-neutral{{border-left-color:#d97706}}
.badge{{display:inline-block;margin-left:9px;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:700;vertical-align:2px}}
.badge-bull{{color:#166534;background:#dcfce7}}.badge-bear{{color:#991b1b;background:#fee2e2}}.badge-neutral{{color:#92400e;background:#fef3c7}}
.metrics{{font-variant-numeric:tabular-nums;color:#374151}}.volume{{font-size:14px;color:#6b7280}}
.text-bull{{color:#15803d;font-weight:700}}.text-bear{{color:#b91c1c;font-weight:700}}.text-neutral{{color:#6b7280;font-weight:700}}
.state-box,.momentum,.advice{{margin:10px 0;padding:10px 12px;border-radius:8px}}.state-box p,.momentum p,.advice p{{margin:3px 0 0}}
.state-bull,.advice-bull{{color:#14532d;background:#f0fdf4}}.state-bear,.advice-bear{{color:#7f1d1d;background:#fef2f2}}
.state-neutral,.advice-neutral{{color:#78350f;background:#fffbeb}}
.momentum-bull{{color:#166534;background:#f0fdf4;border:1px solid #bbf7d0}}
.momentum-bear{{color:#991b1b;background:#fef2f2;border:1px solid #fecaca}}
.momentum-neutral{{color:#92400e;background:#fffbeb;border:1px solid #fde68a}}
.risk{{font-size:14px;color:#4b5563}}.opportunity{{margin:8px 0;padding:8px 10px;border-radius:7px}}
.opportunity-bull{{color:#166534;background:#f0fdf4}}.opportunity-bear{{color:#991b1b;background:#fef2f2}}
.chanlun-card{{margin:12px 0;padding:12px 16px;border:1px solid #e5e7eb;border-left:5px solid #7c3aed;border-radius:10px;background:#fdfcff}}
.chanlun-card h3{{margin-bottom:6px}}.chanlun-card h3 .muted{{margin-left:8px;font-weight:400}}
.chanlun-sig{{margin:6px 0;padding:6px 10px;border-radius:7px;font-size:14px}}
.chanlun-long{{color:#166534;background:#f0fdf4}}.chanlun-short{{color:#991b1b;background:#fef2f2}}
.chanlun-detail{{margin:4px 0 4px 10px;font-size:13px;color:#4b5563}}
.chanlun-edge{{margin:8px 0 0;font-size:13px;color:#6d28d9}}
li{{margin:7px 0}}.muted{{color:#6b7280;font-size:13px}}
@media(max-width:640px){{body{{padding:8px}}.report{{padding:18px 14px;border-radius:0}}.instrument{{padding:14px 12px}}}}
</style></head>
<body><div class="report"><h1>每日市场日报｜{escape(payload.report_date)}</h1>
<p class="muted">生成时间：{escape(payload.generated_at)}（{escape(payload.timezone)}）</p>
{''.join(section_html)}
{_chanlun_section_html(payload.chanlun, timezone_name=payload.timezone)}
<h2>日线机会</h2><ul>{opportunity_html}</ul>
<h2>未来7日重点事件</h2><ul>{event_html}</ul>
</div></body></html>"""


def render_discord_summary(payload: DailyReportPayload) -> str:
    lines: list[str] = []
    for section in payload.markets:
        if section.status == "unavailable":
            lines.append(f"**{section.title}**：数据暂不可用")
            continue
        representative = section.instruments[0] if section.instruments else None
        core = (
            f"{representative.stage}｜{representative.symbol} {representative.change_percent:+.2f}%"
            if representative else "暂无有效标的"
        )
        lines.append(f"**{section.title}**：{core}")
        if section.hot_symbols:
            lines.append(f"热门：{'、'.join(section.hot_symbols)}")
    lines.append("")
    lines.append("**日线机会**")
    ranked = sorted(payload.opportunities, key=lambda row: (-row.stars, row.symbol))[:3]
    if not ranked:
        lines.append("暂无日线级别机会")
    for item in ranked:
        lines.append(f"{'★' * item.stars}{'☆' * (5 - item.stars)} {item.symbol}｜{'多' if item.direction == 'long' else '空'}｜{item.signal_name}")
    chanlun_lines = _chanlun_section_text(payload.chanlun, timezone_name=payload.timezone)
    if chanlun_lines:
        lines.append("")
        lines.extend(chanlun_lines)
    lines.append("")
    lines.append("**未来7日事件**")
    if payload.events:
        for item in payload.events[:3]:
            lines.append(f"{item.starts_at}｜{item.name}")
    else:
        lines.append("未来7日无重点事件")
    return "\n".join(lines)[:4000]
