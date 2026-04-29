"""
告警条件评估引擎：复用回测入场信号函数，检查最新K线是否触发信号。
"""
import logging
from datetime import datetime, timezone

from app.core.indicators import _sma, _rsi, _ema, _macd, _bollinger
from app.core.price_action import detect_pinbar, detect_engulfing
from app.core.sr_levels import signals_sr_breakout
from app.core.discord_webhook import send_discord_alert
from app.schemas.alerts import CONDITION_LABELS

logger = logging.getLogger(__name__)


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


def _signals_macd_divergence(closes: list[float], lookback: int = 5) -> list[int]:
    _, _, hist = _macd(closes)
    n = len(closes)
    signals = [0] * n

    def _find_extremes(series, lb):
        nn = len(series)
        is_peak = [False] * nn
        is_trough = [False] * nn
        for i in range(lb, nn - lb):
            window = series[i - lb: i + lb + 1]
            if series[i] == max(window):
                is_peak[i] = True
            if series[i] == min(window):
                is_trough[i] = True
        return is_peak, is_trough

    is_peak, is_trough = _find_extremes(closes, lookback)
    prev_trough_idx = None
    prev_peak_idx = None

    for i in range(lookback, n - lookback):
        if hist[i] is None:
            continue
        if is_trough[i]:
            if prev_trough_idx is not None and hist[prev_trough_idx] is not None:
                if closes[i] < closes[prev_trough_idx] and hist[i] > hist[prev_trough_idx]:
                    if i + 1 < n:
                        signals[i + 1] = 1
            prev_trough_idx = i
        if is_peak[i]:
            if prev_peak_idx is not None and hist[prev_peak_idx] is not None:
                if closes[i] > closes[prev_peak_idx] and hist[i] < hist[prev_peak_idx]:
                    if i + 1 < n:
                        signals[i + 1] = -1
            prev_peak_idx = i

    return signals


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


async def evaluate_alert(alert, provider) -> tuple[bool, str]:
    """
    Evaluate a single alert. Returns (triggered, detail_message).
    """
    try:
        ctype = alert.condition_type

        if ctype in ("price_above", "price_below"):
            quote = await provider.get_quote(alert.symbol)
            price = quote.get("price", 0)
            if ctype == "price_above" and price > alert.threshold:
                return True, f"当前价格 **{price:.4f}** 高于阈值 **{alert.threshold:.4f}**"
            if ctype == "price_below" and price < alert.threshold:
                return True, f"当前价格 **{price:.4f}** 低于阈值 **{alert.threshold:.4f}**"
            return False, ""

        candle_size = 200
        if ctype in ("macd_divergence", "sr_breakout"):
            candle_size = 300
        candles = await provider.get_time_series(alert.symbol, alert.timeframe, outputsize=candle_size)
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
            if sig == 1:
                detail_extra = f"收盘价 **{closes[-1]:.4f}** 上穿 MA{period}（{ma_str}）"
            elif sig == -1:
                detail_extra = f"收盘价 **{closes[-1]:.4f}** 下穿 MA{period}（{ma_str}）"

        elif ctype == "macd_divergence":
            sigs = _signals_macd_divergence(closes)
            sig = sigs[-1] if sigs else 0
            if sig == 1:
                detail_extra = "价格创新低但 MACD 柱状图抬高，**多头背离**信号"
            elif sig == -1:
                detail_extra = "价格创新高但 MACD 柱状图降低，**空头背离**信号"

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
        triggered, detail = await evaluate_alert(alert, provider)
        if triggered:
            cond_label = CONDITION_LABELS.get(alert.condition_type, alert.condition_type)
            title = f"{alert.symbol} — {alert.name}"
            description = (
                f"**标的**: {alert.symbol}\n"
                f"**策略**: {cond_label}\n"
                f"**详情**: {detail}\n"
                f"**时间框架**: {alert.timeframe}\n"
                f"**触发时间**: {now.strftime('%Y-%m-%d %H:%M UTC')}"
            )
            ok = await send_discord_alert(alert.discord_webhook_url, title, description)
            if ok:
                logger.info("Alert %d triggered and Discord notified", alert.id)
            alert.triggered_at = now
            if alert.repeating:
                pass
            else:
                alert.is_triggered = True
            db_session.add(alert)

    try:
        await db_session.commit()
    except Exception as e:
        logger.error("Failed to commit alert state: %s", e)
        await db_session.rollback()
