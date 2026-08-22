"""缠论 (Chan theory) 信号接入层 — 导入 fenxing 管线并归一化为后端可投递信号。

fenxing 为单一代码源（sys.path 指向仓库根 /fenxing），此处只 import 不复制。
缺失时优雅降级：chanlun_available() 返回 False，日报缠论段显示数据暂不可用，
告警扫描跳过。

对外接口：
- analyze_symbol(candles_by_tf)            多周期分析 -> levels_out（sub/upper 已填）
- extract_signals(symbol, levels_out)      归一化买卖点/背驰/抢先告警
- fetch_closed / scan_symbol / scan_symbols 实时扫描（闭合 K 线 + 去重 + 投递）
- retry_chanlun_deliveries                失败/站内信号的投递重试
- detect_chanlun_opportunities            日报「日线机会」映射
- summarize_symbol                        日报「缠论结构」章节摘要
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.discord_webhook import send_discord_alert
from app.core.notification_channels import send_to_all_channels
from app.core.signal_strength import evaluate_signal_strength, signal_type_label
from app.models.models import ChanlunSignal, NotificationChannel

_DEFAULT_USER_ID = 1
from app.schemas.daily_report import (
    ChanlunSignalSummary,
    ChanlunSymbolSummary,
    OpportunityItem,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(os.environ.get("REPO_ROOT", str(Path(__file__).resolve().parents[3])))
_FENXING_DIR = _REPO_ROOT / "fenxing"

# 后端周期名 -> fenxing 周期名 / 反向
_TF_FENXING = {"1day": "1D", "4h": "4H", "1h": "1H"}
_TF_BACKEND = {"1D": "1day", "4H": "4h", "1H": "1h"}
_TIMEFRAME_SECONDS = {"1day": 86_400, "4h": 14_400, "1h": 3_600}
# 镜像 chanlun.py:43 的级别关系（仅用于单周期时填默认 sub）
_SUB_RELATION = {"1D": "4H", "4H": "1H"}
_FULL_SET = {"1D", "4H", "1H"}

MAX_DELIVERY_ATTEMPTS = 6

# 缠论 k 标签 -> (kind, direction)；告警的 side 单独映射
_KIND_DIRECTION = {
    "一买": ("buy", "long"),
    "二买": ("buy2", "long"),
    "三买": ("buy3", "long"),
    "一卖": ("sell", "short"),
    "二卖": ("sell2", "short"),
    "三卖": ("sell3", "short"),
    "背驰买": ("beichi_buy", "long"),
    "背驰卖": ("beichi_sell", "short"),
}
_ALERT_DIRECTION = {"买": "long", "卖": "short"}

# 缠论事件类型（日报日线机会用）
_CHANLUN_EVENT_TYPE = {
    "一买": "chanlun_buy1",
    "二买": "chanlun_buy2",
    "三买": "chanlun_buy3",
    "一卖": "chanlun_sell1",
    "二卖": "chanlun_sell2",
    "三卖": "chanlun_sell3",
    "背驰买": "chanlun_beichi_buy",
    "背驰卖": "chanlun_beichi_sell",
}


class ChanlunUnavailable(RuntimeError):
    """fenxing 缠论模块不可用。"""


# --------------------------------------------------------------------------- #
# 导入桥                                                                        #
# --------------------------------------------------------------------------- #
_FENXING_AVAILABLE = False
try:
    if str(_FENXING_DIR) not in sys.path:
        sys.path.insert(0, str(_FENXING_DIR))
    import chanlun as _chanlun  # noqa: PLC0415  fenxing/chanlun.py
    from fractals import (  # noqa: F401, PLC0415  被 chanlun 依赖，一并暴露
        process_inclusions,
    )

    _FENXING_AVAILABLE = True
except Exception as exc:  # pragma: no cover - 依赖缺失时的降级路径
    logger.warning("fenxing 缠论模块不可用，缠论功能降级: %s", exc)


def chanlun_available() -> bool:
    return _FENXING_AVAILABLE


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(
        ZoneInfo(settings.ALERT_TIMEZONE)
    ).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- #
# 多周期分析                                                                    #
# --------------------------------------------------------------------------- #
def analyze_symbol(candles_by_tf: dict[str, list[dict]]) -> dict[str, Any]:
    """对一组周期蜡烛跑缠论全链路。

    candles_by_tf: 后端周期名("1day"/"4h"/"1h") -> 蜡烛列表 {time,open,high,low,close,volume}。
    返回 levels_out（fenxing 周期名 "1D"/"4H"/"1H"），sub/upper 已填充：
      - 含完整 {1D,4H,1H} 时调用 link_levels（次级别联动）；
      - 仅部分周期时按 chanlun.py:742-750 填默认 sub/upper。
    """
    if not _FENXING_AVAILABLE:
        raise ChanlunUnavailable("fenxing 缠论模块不可用")
    levels_out: dict[str, Any] = {}
    for tf, candles in candles_by_tf.items():
        f_tf = _TF_FENXING[tf]
        epochs = [int(c["time"]) for c in candles]
        bars = [
            {
                "idx": i,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume") or 0),
            }
            for i, c in enumerate(candles)
        ]
        levels_out[f_tf] = _chanlun.analyze(bars, epochs, f_tf)
    if set(levels_out) == _FULL_SET:
        _chanlun.link_levels(levels_out)
    else:
        _fill_default_sub_upper(levels_out)
    return levels_out


def _fill_default_sub_upper(levels_out: dict[str, Any]) -> None:
    """镜像 chanlun.py:742-750：单周期/部分周期时填默认 sub/upper。"""
    for tf, lvl in levels_out.items():
        for mode in ("stroke", "seg"):
            for p in lvl["_internal"]["buys"][mode] + lvl["_internal"]["sells"][mode]:
                if not p.get("sub"):
                    p["sub"] = (
                        "最小级别(%s), 无次级别可确认" % tf
                        if tf not in _SUB_RELATION
                        else "次级别无同向趋势背驰 ✗"
                    )
                if not p.get("upper"):
                    p["upper"] = "—"


# --------------------------------------------------------------------------- #
# 信号归一化                                                                    #
# --------------------------------------------------------------------------- #
def extract_signals(symbol: str, levels_out: dict[str, Any]) -> list[dict]:
    """把 analyze_symbol 输出归一化为可投递/可存库的信号列表。

    覆盖：笔级(stroke) + 线段级(seg) 一/二/三类买卖点（同位置去重，优先线段级）
          + 抢先层背驰买/卖（含 forming 在途）。告警层已移除。
    """
    out: list[dict] = []

    def _point_signal(p: dict, mode: str) -> dict:
        kind, direction = _KIND_DIRECTION.get(p.get("k"), (p.get("k"), "long"))
        return {
            "symbol": symbol,
            "timeframe": tf,
            "mode": mode,
            "kind": kind,
            "k": p["k"],
            "direction": direction,
            "state": p.get("state"),
            "tier": p.get("tier"),
            "t": p.get("t"),
            "confirmedT": p.get("confirmedT"),
            "price": p.get("p"),
            "why": p.get("why") or "",
            "sub": p.get("sub") or "",
            "upper": p.get("upper") or "",
            "ratio": p.get("ratio"),
            "trigger": None,
            "alertT": None,
        }

    for f_tf, lvl in levels_out.items():
        tf = _TF_BACKEND[f_tf]
        # 线段级（更稳，优先保留）
        seg_points = [
            _point_signal(p, "seg")
            for p in lvl.get("buyPointsSegmentRaw", []) + lvl.get("sellPointsSegmentRaw", [])
        ]
        out.extend(seg_points)
        # 笔级（更灵敏），与线段级同 k + 方向 + 位置相近去重
        stroke_points = [
            _point_signal(p, "stroke")
            for p in lvl.get("buyPointsStrokeRaw", []) + lvl.get("sellPointsStrokeRaw", [])
        ]
        if stroke_points and seg_points:
            dedupe_win = 3 * _TIMEFRAME_SECONDS.get(tf, 86_400)  # 位置容差 ±3 bar
            seg_times: dict[tuple[str, str], list[int]] = {}
            for p in seg_points:
                seg_times.setdefault((p["k"], p["direction"]), []).append(p["t"] or 0)
            stroke_points = [
                p for p in stroke_points
                if not any(
                    abs((p["t"] or 0) - t) <= dedupe_win
                    for t in seg_times.get((p["k"], p["direction"]), [])
                )
            ]
        out.extend(stroke_points)
        for a in lvl.get("beichiAlertsRaw", []):
            out.append({
                "symbol": symbol,
                "timeframe": tf,
                "mode": "fast",
                "kind": "alert",
                "k": "告警",
                "direction": _ALERT_DIRECTION.get(a.get("side"), "long"),
                "state": None,
                "tier": None,
                "t": a.get("t"),
                "confirmedT": None,
                "price": a.get("p"),
                "why": a.get("why") or "",
                "sub": "",
                "upper": "",
                "ratio": None,
                "trigger": None,
                "alertT": None,
            })
        for e in lvl.get("beichiPointsRaw", []):
            kind, direction = _KIND_DIRECTION.get(e.get("k"), (e.get("k"), "long"))
            out.append({
                "symbol": symbol,
                "timeframe": tf,
                "mode": "fast",
                "kind": kind,
                "k": e["k"],
                "direction": direction,
                "state": e.get("state"),
                "tier": None,
                "t": e.get("t"),
                "confirmedT": e.get("confirmedT"),
                "price": e.get("p"),
                "why": e.get("why") or "",
                "sub": "",
                "upper": "",
                "ratio": e.get("ratio"),
                "trigger": e.get("trigger"),
                "alertT": e.get("alertT"),
            })
    out.sort(key=lambda x: x["t"] or 0)
    return out


def _dedupe_key(sig: dict) -> str:
    # 不含 state：买卖点端点 + 方向 + 时间即可唯一标识，避免
    # forming→confirmed 状态跃迁造成同一买卖点重复推送。
    return ":".join((sig["symbol"], sig["timeframe"], sig["mode"], sig["k"],
                     str(sig["t"])))


# --------------------------------------------------------------------------- #
# 闭合蜡烛获取                                                                  #
# --------------------------------------------------------------------------- #
async def fetch_closed(symbol: str, timeframe: str) -> list[dict]:
    """拉取指定周期仅含已闭合 K 线的蜡烛序列（供缠论消费，避免盘中重绘）。

    统一走 DailyReportSources 多源回退（日线 A股 akShare、美股 Yahoo、
    crypto Binance spot 为主源，失败后兜底 Binance 永续 / Hyperliquid），
    与监控/日报共用同一取数通道。
    """
    from app.core.candle_utils import completed_daily_candles
    from app.core.daily_report_sources import DailyReportSources

    candles = await DailyReportSources().get_candles(
        symbol, outputsize=500 if timeframe == "1day" else 1000, timeframe=timeframe
    )
    if timeframe == "1day":
        return completed_daily_candles(symbol, candles)
    from app.core.alert_engine import completed_indicator_candles

    return completed_indicator_candles(candles, timeframe)


def _tfs_for(symbol: str) -> tuple[str, ...]:
    return ("1day", "4h", "1h")


# --------------------------------------------------------------------------- #
# 投递（Discord，一条 embed / (symbol, timeframe)）                              #
# --------------------------------------------------------------------------- #
# 缠论 k 标签 -> 给盯盘人的一句话转折结论（语义 + 失效参考）
_POINT_MEANING = {
    "一买": ("下跌趋势背驰，趋势可能反转，第一类买点", "跌破该买点低点则信号失效"),
    "二买": ("一买后回抽不创新低，反转确认，第二类买点", "跌破一买低点则转势失败"),
    "三买": ("向上离开中枢后回抽不跌回，新趋势启动，第三类买点", "跌回中枢上沿则信号失效"),
    "一卖": ("上涨趋势背驰，趋势可能反转，第一类卖点", "升破该卖点高点则信号失效"),
    "二卖": ("一卖后反抽不创新高，反转确认，第二类卖点", "升破一卖高点则转势失败"),
    "三卖": ("向下离开中枢后反抽不升回，新跌势启动，第三类卖点", "升回中枢下沿则信号失效"),
    "背驰买": ("下跌动能衰竭，抢先预判的买入信号", "动能回升越过参考段则背驰消失"),
    "背驰卖": ("上涨动能衰竭，抢先预判的卖出信号", "动能回升越过参考段则背驰消失"),
}
_TF_LABEL = {"1day": "日线", "4h": "4小时", "1h": "1小时"}


def _current_price(candles: list[dict] | None) -> float | None:
    """取最新一根已闭合K线的收盘价作为当前价格。"""
    if not candles:
        return None
    return float(candles[-1].get("close") or 0) or None


def _signal_line(sig: dict) -> str:
    icon = "🟢" if sig["direction"] == "long" else "🔴"
    meaning, invalid = _POINT_MEANING.get(
        sig["k"], (f"{sig['k']}信号", "结构未确认")
    )
    tier = f"（{sig['tier']}）" if sig.get("tier") else ""
    confirmed = sig.get("confirmedT") or sig.get("t")
    head = (
        f"{icon} **{sig['k']}**{tier} · {_TF_LABEL.get(sig['timeframe'], sig['timeframe'])} · {meaning}"
    )
    lines = [head]
    lines.append(
        f"　🕒 确认 {_fmt_ts(confirmed)}　极值 {_fmt_ts(sig.get('t'))}　参考 {_number(sig['price'])}"
    )
    if sig.get("sub") or sig.get("upper"):
        lines.append(f"　📐 联动：{sig['sub']} · {sig['upper']}")
    if sig.get("why"):
        lines.append(f"　🔬 依据：{sig['why']}")
    lines.append(f"　⚠️ 失效参考：{invalid}")
    if sig.get("kind") in ("beichi_buy", "beichi_sell") and sig.get("ratio") is not None:
        lines.append(
            f"　⚡ 比率 {sig['ratio']} · 告警 {_fmt_ts(sig.get('alertT'))}"
        )
    return "\n".join(lines)


def _batch_color(items: list[dict]) -> tuple[str, int]:
    dirs = {sig["direction"] for sig in items}
    if dirs == {"long"}:
        return "🟢", 0x22C55E
    if dirs == {"short"}:
        return "🔴", 0xEF4444
    return "🟠", 0xF59E0B


def _build_message(
    symbol: str,
    tf: str,
    items: list[dict],
    *,
    current_price: float | None = None,
) -> tuple[str, str, int]:
    icon, color = _batch_color(items)
    tf_label = _TF_LABEL.get(tf, tf)
    title = f"{icon} 缠论转折｜{symbol}｜{tf_label}"
    price_line = f"　💰 当前价格：{_number(current_price)}" if current_price else ""
    lines = [f"**{len(items)} 条缠论转折信号**（{settings.ALERT_TIMEZONE}，确认即推送）{price_line}"]
    for sig in sorted(items, key=lambda x: x.get("confirmedT") or x.get("t") or 0, reverse=True):
        lines.append(_signal_line(sig))
    return title, "\n".join(lines), color


async def _resolve_default_channels(db: AsyncSession) -> list[NotificationChannel]:
    """Return every active channel for the default user (Discord/飞书/email).

    与监控引擎一致：不再限定单一 Discord，支持多渠道同步推送。
    """
    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.user_id == _DEFAULT_USER_ID,
            NotificationChannel.is_active.is_(True),
        ).order_by(NotificationChannel.is_default.desc(), NotificationChannel.id.asc())
    )
    return list(result.scalars().all())


async def _send_chunk(
    *,
    channels: list[NotificationChannel],
    title: str,
    description: str,
    color: int,
    thumbnail_url: str | None,
    image_payloads: list[dict] | None = None,
) -> bool:
    """Deliver one chunk to every active channel; any success wins."""
    if channels:
        return await send_to_all_channels(
            channels, title, description, color=color,
            thumbnail_url=thumbnail_url, title_icon=title[0],
            image_payloads=image_payloads,
        )
    url = (settings.DISCORD_WEBHOOK_URL or "").strip()
    if not url:
        return False
    return await send_discord_alert(
        url, title, description, color=color,
        thumbnail_url=thumbnail_url, title_icon=title[0],
        image_payloads=image_payloads,
    )


def _chart_payloads(
    symbol: str,
    candles_by_tf: dict[str, list[dict]],
    levels_out: dict[str, Any],
    current_price: float | None = None,
    max_bars: int = 250,
) -> list[dict]:
    """为每个周期生成 PNG 走势图字节（含该周期买卖点标记）。

    仅保留最新 max_bars 根K线，避免图中K线过于密集。买卖点标记只保留
    时间戳落在截断窗口内（或紧邻窗口左侧）的点。
    返回 [{filename, data, mime}]，按周期从大到小排序。
    """
    from app.core.chanlun_chart import render_chanlun_png

    payloads: list[dict] = []
    for tf in ("1day", "4h", "1h"):
        all_candles = candles_by_tf.get(tf)
        if not all_candles:
            continue
        candles = all_candles[-max_bars:]
        window_start = int(all_candles[0]["time"]) if all_candles else 0
        if candles:
            window_start = int(candles[0]["time"])
        f_tf = _TF_FENXING[tf]
        lvl = levels_out.get(f_tf)
        markers: list[dict] = []
        pivots: list[dict] = []
        if lvl:
            for level, src in (
                ("seg", lvl.get("buyPointsSegmentRaw", []) + lvl.get("sellPointsSegmentRaw", [])),
                ("stroke", lvl.get("buyPointsStrokeRaw", []) + lvl.get("sellPointsStrokeRaw", [])),
            ):
                for p in src:
                    if p.get("t") and int(p["t"]) + 7 * 3600 < window_start:
                        continue
                    direction = _KIND_DIRECTION.get(p.get("k"), (p.get("k"), "long"))[1]
                    markers.append({
                        "t": p.get("t"),
                        "p": p.get("p"),
                        "k": p.get("k"),
                        "direction": direction,
                        "level": level,
                    })
            # 中枢：线段级（更稳，宽）+ 笔级（更灵敏，窄），两层都画
            for p in lvl.get("pivotsSegmentRaw", []):
                if len(p) >= 6 and int(p[1]) + 7 * 3600 >= window_start:
                    pivots.append({
                        "start": int(p[0]),
                        "end": int(p[1]),
                        "zd": float(p[2]),
                        "zg": float(p[3]),
                        "gg": float(p[4]),
                        "dd": float(p[5]),
                        "level": "seg",
                    })
            for p in lvl.get("pivotsStrokeRaw", []):
                if len(p) >= 6 and int(p[1]) + 7 * 3600 >= window_start:
                    pivots.append({
                        "start": int(p[0]),
                        "end": int(p[1]),
                        "zd": float(p[2]),
                        "zg": float(p[3]),
                        "gg": float(p[4]),
                        "dd": float(p[5]),
                        "level": "stroke",
                    })
        png = render_chanlun_png(
            symbol, _TF_LABEL.get(tf, tf), candles,
            markers=markers, pivots=pivots, current_price=current_price,
        )
        payloads.append({
            "filename": f"{symbol}_{tf}.png",
            "data": png,
            "mime": "image/png",
        })
    return payloads


async def _deliver_message(
    db: AsyncSession,
    signals: list[dict],
    events: list[ChanlunSignal],
    title: str,
    description: str,
    color: int,
    *,
    candles_by_tf: dict[str, list[dict]] | None = None,
    levels_out: dict[str, Any] | None = None,
    current_price: float | None = None,
) -> None:
    """Send one embed per (symbol, timeframe) batch.

    Discord embeds cap description at 4096 chars; large scans (esp. 4h/1h)
    can exceed that, so the description is split into multiple messages the
    same way the daily report splits long text (title gets a (1/N) suffix).
    """
    from app.core.daily_report_delivery import split_discord_text

    channels = await _resolve_default_channels(db)
    now = _utcnow()
    if not channels and not (settings.DISCORD_WEBHOOK_URL or "").strip():
        for event in events:
            event.status = "in_app_only"
            event.last_error = "未配置通知渠道；站内信号已保存"
        await db.commit()
        return
    from app.core.symbol_branding import resolve_symbol_branding

    thumbnail_url = resolve_symbol_branding(signals[0]["symbol"]).logo_url
    symbol = signals[0]["symbol"]
    image_payloads = None
    if candles_by_tf and levels_out:
        image_payloads = _chart_payloads(symbol, candles_by_tf, levels_out, current_price)
    chunks = split_discord_text(description)
    ok = True
    for index, chunk in enumerate(chunks):
        suffix = "" if len(chunks) == 1 else f"（{index + 1}/{len(chunks)}）"
        sent = await _send_chunk(
            channels=channels,
            title=f"{title}{suffix}",
            description=chunk,
            color=color,
            thumbnail_url=thumbnail_url,
            image_payloads=image_payloads,
        )
        if not sent:
            ok = False
            break
    for event in events:
        event.attempts += 1
        event.sent_at = now if ok else None
        if ok:
            event.status = "sent"
            event.next_retry_at = None
            event.last_error = None
        else:
            event.status = "failed"
            delay_minutes = min(5 * (3 ** max(0, event.attempts - 1)), 360)
            event.next_retry_at = now + timedelta(minutes=delay_minutes)
            event.last_error = "通知渠道投递失败，将在约 %d 分钟后重试" % delay_minutes
    await db.commit()


# --------------------------------------------------------------------------- #
# 实时扫描                                                                      #
# --------------------------------------------------------------------------- #
async def _store_signal(db: AsyncSession, sig: dict) -> ChanlunSignal | None:
    title = f"缠论 {sig['k']}｜{sig['symbol']}｜{sig['timeframe']}"
    event = ChanlunSignal(
        dedupe_key=_dedupe_key(sig),
        symbol=sig["symbol"],
        timeframe=sig["timeframe"],
        mode=sig["mode"],
        kind=sig["kind"],
        k=sig["k"],
        direction=sig["direction"],
        state=sig.get("state"),
        tier=sig.get("tier"),
        signal_time=int(sig["t"] or 0),
        confirmed_time=sig.get("confirmedT"),
        price=float(sig["price"] or 0),
        title=title,
        description=_signal_line(sig),
        payload=json_dumps(sig),
        status="pending",
    )
    db.add(event)
    try:
        await db.flush()
        return event
    except IntegrityError:
        await db.rollback()
        return None


async def scan_symbol(db: AsyncSession, symbol: str) -> dict:
    symbol = symbol.upper()
    tfs = _tfs_for(symbol)
    candles_by_tf: dict[str, list[dict]] = {}
    errors: list[str] = []
    for tf in tfs:
        try:
            candles = await fetch_closed(symbol, tf)
            if len(candles) >= settings.CHANLUN_MIN_BARS:
                candles_by_tf[tf] = candles
            else:
                errors.append(f"{tf} 有效K线不足")
        except Exception as exc:
            errors.append(f"{tf} 拉取失败: {str(exc)[:100]}")
    if not candles_by_tf:
        return {"symbol": symbol, "triggered": False, "error": "；".join(errors)}
    levels_out = analyze_symbol(candles_by_tf)
    # 增量游标：只推送「确认时间」晚于该 symbol 已推送最大确认点的信号，
    # 不再用固定 72h 窗口回捞历史买卖点（缠论买卖点是时点信号，非时段状态）。
    cursor_result = await db.execute(
        select(func.max(ChanlunSignal.confirmed_time)).where(
            ChanlunSignal.symbol == symbol,
            ChanlunSignal.confirmed_time.is_not(None),
        )
    )
    cursor = cursor_result.scalar() or 0
    signals = [
        sig for sig in extract_signals(symbol, levels_out)
        if sig.get("confirmedT") and sig["confirmedT"] > cursor
    ]
    new: list[tuple[dict, ChanlunSignal]] = []
    for sig in signals:
        event = await _store_signal(db, sig)
        if event is not None:
            new.append((sig, event))
    if new:
        await db.commit()
        for tf in tfs:
            group = [pair for pair in new if pair[0]["timeframe"] == tf]
            if not group:
                continue
            current_price = _current_price(candles_by_tf.get(tf))
            title, description, color = _build_message(
                symbol, tf, [pair[0] for pair in group],
                current_price=current_price,
            )
            await _deliver_message(
                db, [pair[0] for pair in group], [pair[1] for pair in group],
                title, description, color,
                candles_by_tf=candles_by_tf,
                levels_out=levels_out,
                current_price=current_price,
            )
    return {"symbol": symbol, "created": len(new), "timeframes": list(tfs)}


async def scan_symbols(db: AsyncSession, symbols: Iterable[str]) -> list[dict]:
    results: list[dict] = []
    for symbol in symbols:
        try:
            results.append(await scan_symbol(db, symbol))
        except Exception as exc:
            logger.exception("Chanlun scan failed for %s", symbol)
            results.append({"symbol": symbol, "created": 0, "error": str(exc)})
    return results


async def retry_chanlun_deliveries(db: AsyncSession) -> int:
    channels = await _resolve_default_channels(db)
    if not channels and not (settings.DISCORD_WEBHOOK_URL or "").strip():
        return 0
    now = _utcnow()
    result = await db.execute(
        select(ChanlunSignal).where(
            ChanlunSignal.status.in_(["failed", "in_app_only"]),
            ChanlunSignal.attempts < MAX_DELIVERY_ATTEMPTS,
            (ChanlunSignal.next_retry_at.is_(None)) | (ChanlunSignal.next_retry_at <= now),
        ).order_by(ChanlunSignal.created_at.asc()).limit(20)
    )
    events = list(result.scalars().all())
    for event in events:
        await _deliver_one_event(db, event)
    return len(events)


async def _deliver_one_event(db: AsyncSession, event: ChanlunSignal) -> None:
    """按预渲染 title/description 重发单条信号（失败/站内重试用）。"""
    channels = await _resolve_default_channels(db)
    now = _utcnow()
    ok: bool
    if channels:
        from app.core.symbol_branding import resolve_symbol_branding

        thumbnail_url = resolve_symbol_branding(event.symbol).logo_url
        ok = await send_to_all_channels(
            channels, event.title, event.description, color=_event_color(event),
            thumbnail_url=thumbnail_url, title_icon=event.title[0] if event.title else "🔔",
        )
    else:
        url = (settings.DISCORD_WEBHOOK_URL or "").strip()
        if not url:
            event.status = "in_app_only"
            event.next_retry_at = None
            event.last_error = "未配置通知渠道；站内信号已保存"
            await db.commit()
            return
        from app.core.symbol_branding import resolve_symbol_branding

        thumbnail_url = resolve_symbol_branding(event.symbol).logo_url
        ok = await send_discord_alert(
            url, event.title, event.description, color=_event_color(event),
            thumbnail_url=thumbnail_url, title_icon=event.title[0] if event.title else "🔔",
        )
    event.attempts += 1
    event.sent_at = now if ok else None
    if ok:
        event.status = "sent"
        event.next_retry_at = None
        event.last_error = None
    else:
        event.status = "failed"
        delay_minutes = min(5 * (3 ** max(0, event.attempts - 1)), 360)
        event.next_retry_at = now + timedelta(minutes=delay_minutes)
        event.last_error = "通知渠道投递失败，将在约 %d 分钟后重试" % delay_minutes
    await db.commit()


def _event_color(event: ChanlunSignal) -> int:
    if event.kind == "alert":
        return 0xF59E0B
    return 0x22C55E if event.direction == "long" else 0xEF4444


# --------------------------------------------------------------------------- #
# 日报映射                                                                      #
# --------------------------------------------------------------------------- #
def detect_chanlun_opportunities(
    market: str,
    symbol: str,
    name: str,
    candles: list[dict] | None = None,
    levels: dict[str, Any] | None = None,
) -> list[OpportunityItem]:
    """把最近确认的买卖点/背驰映射为日报机会项。

    任何失败返回 []（日报优雅降级）。仅收录 confirmed（在途 forming 不列入
    机会模块，只在缠论章节体现）。candles 与 levels 二选一：
    - 独立调用时传 candles（内部跑一次 1D 缠论）；
    - 日报路径已算过 levels 时直接传 levels，避免重复计算。
    """
    if not _FENXING_AVAILABLE:
        return []
    if levels is None:
        if not candles:
            return []
        try:
            levels = analyze_symbol({"1day": candles})
        except Exception as exc:
            logger.warning("Chanlun opportunities skipped %s: %s", symbol, exc)
            return []
    now = int(time.time())
    lookback_days = int(settings.CHANLUN_REPORT_LOOKBACK_DAYS) * 86_400
    signals = [sig for sig in extract_signals(symbol, levels)
               if sig.get("state") == "confirmed"
               and sig.get("t") and now - sig["t"] <= lookback_days]
    output: list[OpportunityItem] = []
    for sig in signals:
        event_type = _CHANLUN_EVENT_TYPE.get(sig["k"])
        if not event_type or sig["direction"] not in {"long", "short"}:
            continue
        strength = evaluate_signal_strength(event_type, "1day")
        output.append(OpportunityItem(
            market=market,
            symbol=symbol,
            name=name,
            direction=sig["direction"],
            event_type=event_type,
            signal_name=signal_type_label(event_type, "chanlun"),
            stars=strength.stars,
            strength_label=strength.label,
            summary=f"缠论 {sig['k']} @ {_fmt_ts(sig['t'])}｜{sig['why']}",
            signal_time=int(sig["t"]),
        ))
    return output


def summarize_symbol(
    symbol: str,
    name: str,
    timeframe: str,
    levels: dict[str, Any],
) -> ChanlunSymbolSummary:
    """日报「缠论结构」章节：最近确认/在途信号 + 末端结构摘要。"""
    signals = extract_signals(symbol, levels)
    recent = [sig for sig in signals if sig.get("state") in ("confirmed", "forming")]
    recent.sort(key=lambda x: x["t"] or 0, reverse=True)
    summaries = [
        ChanlunSignalSummary(
            k=sig["k"],
            direction=sig["direction"],
            state=sig.get("state"),
            tier=sig.get("tier"),
            t=int(sig["t"] or 0),
            price=float(sig["price"] or 0),
            why=sig["why"],
            sub=sig.get("sub") or "",
            upper=sig.get("upper") or "",
        )
        for sig in recent[:4]
    ]
    edge = ""
    f_tf = _TF_FENXING[timeframe]
    lvl = levels.get(f_tf)
    if lvl and lvl.get("meta", {}).get("edge"):
        meta_edge = lvl["meta"]["edge"]
        parts = []
        if meta_edge.get("lastStroke"):
            parts.append(meta_edge["lastStroke"])
        if meta_edge.get("pending"):
            parts.append(meta_edge["pending"])
        if meta_edge.get("live"):
            live = meta_edge["live"]
            if live.get("alert"):
                parts.append("抢先live: %s" % live["alert"])
            elif live.get("note"):
                parts.append("抢先live: %s" % live["note"])
        edge = "；".join(parts)
    return ChanlunSymbolSummary(
        symbol=symbol,
        name=name,
        timeframe=timeframe,
        signals=summaries,
        edge=edge,
    )


def _number(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)
