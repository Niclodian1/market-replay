"""
告警条件评估引擎：复用回测入场信号函数，检查最新K线是否触发信号。
"""
import logging
import time
from datetime import datetime, timezone

from app.core.indicators import _sma, _rsi, _macd, _bollinger
from app.core.price_action import detect_pinbar, detect_engulfing, detect_w_bottom
from app.core.sr_levels import signals_sr_breakout
from app.core.discord_webhook import send_discord_alert
from app.core.symbol_branding import resolve_symbol_branding
from app.core.divergence_detectors import (
    macd_extreme_divergence_events,
    _format_event,
)
from app.schemas.alerts import CONDITION_LABELS
from app.core.candle_utils import completed_daily_candles
from app.core.signals.monitor_signals import detect_ma_events
from app.core.signals.evidence import (
    format_volume_evidence,
    volume_evidence,
)

logger = logging.getLogger(__name__)

_TIMEFRAME_SECONDS = {
    "15min": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1day": 24 * 60 * 60,
    "1week": 7 * 24 * 60 * 60,
}


def completed_indicator_candles(
    candles: list[dict], timeframe: str, now_ts: int | None = None
) -> list[dict]:
    """排除行情源返回的尚未收盘 K 线，防止盘中 MACD/成交量重绘。"""
    duration = _TIMEFRAME_SECONDS.get(timeframe)
    if duration is None:
        return [c for c in candles if c.get("is_closed", True)]
    now = int(time.time()) if now_ts is None else now_ts
    return [
        {**c, "is_closed": True}
        for c in candles
        if c.get("is_closed", True) and int(c["time"]) + duration <= now
    ]


def _signals_ma_cross(closes: list[float], fast: int = 20, slow: int = 60) -> list[int]:
    ma_f = _sma(closes, fast)
    ma_s = _sma(closes, slow)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if ma_f[i] is None or ma_s[i] is None or ma_f[i - 1] is None or ma_s[i - 1] is None:
            continue
        prev = ma_f[i - 1] - ma_s[i - 1]
        curr = ma_f[i] - ma_s[i]
        if prev <= 0 and curr > 0:
            signals[i] = 1
        elif prev >= 0 and curr < 0:
            signals[i] = -1
    return signals


def _signals_rsi(closes: list[float], oversold: float = 30, overbought: float = 70) -> list[int]:
    rsi = _rsi(closes, 14)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if rsi[i] is None or rsi[i - 1] is None:
            continue
        if rsi[i - 1] >= oversold and rsi[i] < oversold:
            signals[i] = 1
        elif rsi[i - 1] <= overbought and rsi[i] > overbought:
            signals[i] = -1
    return signals


def _signals_macd(closes: list[float]) -> list[int]:
    macd_line, sig_line, _ = _macd(closes)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if macd_line[i] is None or sig_line[i] is None or macd_line[i - 1] is None or sig_line[i - 1] is None:
            continue
        prev = macd_line[i - 1] - sig_line[i - 1]
        curr = macd_line[i] - sig_line[i]
        if prev <= 0 and curr > 0:
            signals[i] = 1
        elif prev >= 0 and curr < 0:
            signals[i] = -1
    return signals


def _signals_ma_breakout(closes: list[float], period: int = 20) -> list[int]:
    ma = _sma(closes, period)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if ma[i] is None or ma[i - 1] is None:
            continue
        prev_above = closes[i - 1] > ma[i - 1]
        curr_above = closes[i] > ma[i]
        if not prev_above and curr_above:
            signals[i] = 1
        elif prev_above and not curr_above:
            signals[i] = -1
    return signals


def _ma_slope_text(ma_series: list, period: int, window: int | None = None, direction: str | None = None) -> str:
    """MA 斜率描述（ATR 归一化，供 legacy 告警正文）。

    direction 为告警方向（long/short），斜率符号与方向不一致时追加
    「斜率指标不一致」标记。
    """
    from app.core.signals.evidence import _ma_slope

    window = window or period
    index = len(ma_series) - 1
    slope = _ma_slope(ma_series, index, window)
    if slope is None:
        return ""
    closes = [v for v in ma_series[index - window + 1: index + 1] if v is not None]
    span = max(closes) - min(closes) if len(closes) >= 2 else 0.0
    norm = slope / span if span > 0 else 0.0
    text = f"，MA{period} 斜率 {slope:+.4f}/根（{norm * 100:+.1f}%/区间）"
    consistent = (
        (direction == "long" and slope >= 0)
        or (direction == "short" and slope <= 0)
        or direction not in ("long", "short")
    )
    if not consistent:
        text += "⚠斜率指标不一致"
    return text


def _signals_bollinger(closes: list[float], period: int = 20, num_std: float = 2.0) -> list[int]:
    upper, _, lower = _bollinger(closes, period, num_std)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if lower[i] is None or lower[i - 1] is None or upper[i] is None or upper[i - 1] is None:
            continue
        if closes[i - 1] >= lower[i - 1] and closes[i] < lower[i]:
            signals[i] = 1
        elif closes[i - 1] <= upper[i - 1] and closes[i] > upper[i]:
            signals[i] = -1
    return signals


_SIGNAL_DESC = {
    1: "买入",
    -1: "卖出",
}


def _alert_presentation(condition_type: str, detail: str) -> tuple[str, int]:
    if condition_type == "price_above" or "买入方向" in detail:
        return "🟢", 0x22C55E
    if condition_type == "price_below" or "卖出方向" in detail:
        return "🔴", 0xEF4444
    return "🔔", 0x3B82F6


async def evaluate_alert(alert, provider) -> tuple[bool, str]:
    """
    Evaluate a single alert. Returns (triggered, detail_message).
    """
    try:
        ctype = alert.condition_type

        if ctype in ("price_above", "price_below"):
            quote = await provider.get_quote(alert.symbol)
            price = quote.get("price", 0)
            duration = _TIMEFRAME_SECONDS.get(alert.timeframe, 60)
            alert._evaluated_signal_time = int(time.time()) // duration * duration
            if ctype == "price_above" and price > alert.threshold:
                return True, f"当前价格 **{price:.4f}** 高于阈值 **{alert.threshold:.4f}**"
            if ctype == "price_below" and price < alert.threshold:
                return True, f"当前价格 **{price:.4f}** 低于阈值 **{alert.threshold:.4f}**"
            return False, ""

        candle_size = 200
        if ctype in ("macd_divergence", "sr_breakout"):
            candle_size = 300
        candles = await provider.get_time_series(alert.symbol, alert.timeframe, outputsize=candle_size)
        if ctype == "strategy_a_breakout":
            candles = completed_daily_candles(alert.symbol, candles)
        else:
            candles = completed_indicator_candles(candles, alert.timeframe)
        if not candles:
            return False, ""
        alert._evaluated_signal_time = int(candles[-1]["time"])
        closes = [c["close"] for c in candles]

        sig = None
        detail_extra = ""

        if ctype == "ma_cross":
            sigs = _signals_ma_cross(closes)
            sig = sigs[-1] if sigs else 0
            if sig == 1:
                detail_extra = "MA20 上穿 MA60，**金叉**信号"
            elif sig == -1:
                detail_extra = "MA20 下穿 MA60，**死叉**信号"

        elif ctype == "rsi":
            oversold = 30.0
            overbought = 70.0
            if alert.threshold:
                overbought = alert.threshold
                oversold = 100 - alert.threshold
            sigs = _signals_rsi(closes, oversold, overbought)
            sig = sigs[-1] if sigs else 0
            rsi_vals = _rsi(closes, 14)
            latest_rsi = next((v for v in reversed(rsi_vals) if v is not None), None)
            rsi_str = f"{latest_rsi:.2f}" if latest_rsi is not None else "N/A"
            if sig == 1:
                detail_extra = f"RSI({rsi_str}) 跌破超卖线 {oversold:.0f}，**超卖反弹**信号"
            elif sig == -1:
                detail_extra = f"RSI({rsi_str}) 突破超买线 {overbought:.0f}，**超买回落**信号"

        elif ctype == "macd":
            sigs = _signals_macd(closes)
            sig = sigs[-1] if sigs else 0
            if sig == 1:
                detail_extra = "MACD 线上穿信号线，**MACD 金叉**"
            elif sig == -1:
                detail_extra = "MACD 线下穿信号线，**MACD 死叉**"

        elif ctype == "ma_breakout":
            period = alert.ma_period or 20
            sigs = _signals_ma_breakout(closes, period)
            sig = sigs[-1] if sigs else 0
            ma_vals = _sma(closes, period)
            latest_ma = next((v for v in reversed(ma_vals) if v is not None), None)
            ma_str = f"{latest_ma:.4f}" if latest_ma is not None else "N/A"
            slope_str = _ma_slope_text(ma_vals, period, direction="long" if sig == 1 else "short")
            if sig == 1:
                volume_text = format_volume_evidence(volume_evidence(candles))
                detail_extra = f"收盘价 **{closes[-1]:.4f}** 上穿 MA{period}（{ma_str}）{slope_str}；{volume_text}"
            elif sig == -1:
                volume_text = format_volume_evidence(volume_evidence(candles))
                detail_extra = f"收盘价 **{closes[-1]:.4f}** 下穿 MA{period}（{ma_str}）{slope_str}；{volume_text}"

        elif ctype == "strategy_a_breakout":
            # 复用监控计划 MA 突破检测器（口径统一：MA 30/60/99/120 ATR 归一化突破）
            period = alert.ma_period or 30
            events = detect_ma_events(candles, alert.timeframe, periods=(period,))
            match = next(
                (ev for ev in events if ev["direction"] == "long"),
                None,
            )
            sig = 1 if match else 0
            if sig:
                ma_value = _sma(closes, period)[-1]
                body_rise = (candles[-1]["close"] / candles[-1]["open"] - 1) * 100
                volume_text = format_volume_evidence(volume_evidence(candles))
                detail_extra = (
                    f"日线收盘 **{closes[-1]:.4f}** 上穿 MA{period}（{ma_value:.4f}），"
                    f"当日实体上涨 **{body_rise:.2f}%**，满足策略A入场条件；{volume_text}"
                )

        elif ctype == "macd_divergence":
            events = macd_extreme_divergence_events(candles)
            sig = 0
            if events:
                latest = events[-1]
                # 仅当最新事件落在当前 bar（或最近一根已完成 bar）才触发，
                # 避免历史背离在每次扫描重复报警。
                if int(latest["t"]) >= int(candles[-1]["time"]) - (
                    _TIMEFRAME_SECONDS.get(alert.timeframe, 0) or 0
                ):
                    sig = 1 if latest["side"] == "buy" else -1
                    detail_extra = _format_event(latest)

        elif ctype == "bollinger":
            sigs = _signals_bollinger(closes)
            sig = sigs[-1] if sigs else 0
            upper, _, lower = _bollinger(closes)
            u = next((v for v in reversed(upper) if v is not None), None)
            l = next((v for v in reversed(lower) if v is not None), None)
            if sig == 1:
                detail_extra = f"收盘价跌破布林下轨（{l:.4f}），**超卖**信号"
            elif sig == -1:
                detail_extra = f"收盘价突破布林上轨（{u:.4f}），**超买**信号"

        elif ctype == "sr_breakout":
            sigs = signals_sr_breakout(candles)
            sig = sigs[-1] if sigs else 0
            if sig == 1:
                detail_extra = f"收盘价 **{closes[-1]:.4f}** 向上突破阻力位"
            elif sig == -1:
                detail_extra = f"收盘价 **{closes[-1]:.4f}** 向下跌破支撑位"

        elif ctype == "pinbar":
            sigs = detect_pinbar(candles)
            sig = sigs[-1] if sigs else 0
            if sig == 1:
                detail_extra = "检测到**看涨 Pinbar**（锤子线）"
            elif sig == -1:
                detail_extra = "检测到**看跌 Pinbar**（射击之星）"

        elif ctype == "engulfing":
            sigs = detect_engulfing(candles)
            sig = sigs[-1] if sigs else 0
            if sig == 1:
                detail_extra = "检测到**看涨吞没形态**"
            elif sig == -1:
                detail_extra = "检测到**看跌吞没形态**"

        elif ctype == "w_bottom":
            sigs = detect_w_bottom(candles)
            sig = sigs[-1] if sigs else 0
            if sig == 1:
                detail_extra = "检测到**W底（双底）形态**"
            elif sig == -1:
                detail_extra = "检测到**M顶（双顶）形态**"

        if sig and sig != 0:
            direction = _SIGNAL_DESC.get(sig, "")
            return True, f"{detail_extra}（{direction}方向）"

    except Exception as e:
        logger.error("Alert %d (%s %s) evaluation error: %s", alert.id, alert.symbol, alert.condition_type, e)

    return False, ""


async def run_alert_checks(db_session, provider) -> None:
    """Fetch all active, non-triggered alerts and evaluate them."""
    from sqlalchemy import select
    from app.models.models import Alert

    try:
        result = await db_session.execute(
            select(Alert).where(Alert.is_active == True, Alert.is_triggered == False)  # noqa: E712
        )
        alerts = result.scalars().all()
    except Exception as e:
        logger.error("Failed to load alerts: %s", e)
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for alert in alerts:
        from app.providers.local_crypto import pick_quote_provider, pick_series_provider
        if alert.condition_type in ("price_above", "price_below"):
            alert_provider = pick_quote_provider(alert.symbol)
        else:
            alert_provider = pick_series_provider(alert.symbol, alert.timeframe)
        triggered, detail = await evaluate_alert(alert, alert_provider)
        if triggered:
            signal_time = getattr(alert, "_evaluated_signal_time", None)
            if (
                alert.repeating
                and signal_time is not None
                and alert.last_triggered_signal_time == signal_time
            ):
                continue
            cond_label = CONDITION_LABELS.get(alert.condition_type, alert.condition_type)
            title = f"{alert.symbol} — {alert.name}"
            description = (
                f"**标的**: {alert.symbol}\n"
                f"**策略**: {cond_label}\n"
                f"**详情**: {detail}\n"
                f"**时间框架**: {alert.timeframe}\n"
                f"**触发时间**: {now.strftime('%Y-%m-%d %H:%M UTC')}"
            )
            title_icon, color = _alert_presentation(alert.condition_type, detail)
            branding = resolve_symbol_branding(alert.symbol)
            ok = await send_discord_alert(
                alert.discord_webhook_url,
                title,
                description,
                color=color,
                thumbnail_url=branding.logo_url,
                title_icon=title_icon,
            )
            if ok:
                logger.info("Alert %d triggered and Discord notified", alert.id)
                # A delivery failure must never consume a one-shot rule. The
                # next scheduler pass will retry the same completed signal.
                alert.triggered_at = now
                alert.last_triggered_signal_time = signal_time
                if not alert.repeating:
                    alert.is_triggered = True
                db_session.add(alert)
            else:
                logger.warning("Alert %d triggered but delivery failed; left active for retry", alert.id)

    try:
        await db_session.commit()
    except Exception as e:
        logger.error("Failed to commit alert state: %s", e)
        await db_session.rollback()
