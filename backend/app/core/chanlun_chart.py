"""用 matplotlib 生成缠论 K 线图 PNG（高质量，支持中文）。

容器需安装 matplotlib + Noto Sans CJK 字体（Dockerfile 已配置）。
输入标准蜡烛列表 + 可选买卖点标记，输出 PNG 字节。
"""
from __future__ import annotations

import io
import math
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager as fm  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

_UP = "#26a69a"
_DOWN = "#ef5350"
_GRID = "#2a2e39"
_TEXT = "#d8dce4"
_BUY = "#22c55e"
_SELL = "#ef4444"
_CURRENT = "#f59e0b"
_BG = "#1b1e27"
_DIF = "#60a5fa"
_DEA = "#f59e0b"
_PIVOT_FILL = "#3b82f6"
_PIVOT_EDGE = "#60a5fa"
_PIVOT_STROKE_FILL = "#ef4444"
_PIVOT_STROKE_EDGE = "#ef4444"
_MA_COLORS = ["#f59e0b", "#60a5fa", "#c084fc", "#34d399", "#fb7185", "#38bdf8"]


def _init_font() -> None:
    """确保中文字体可用（幂等）。"""
    try:
        names = {f.name for f in fm.fontManager.ttflist}
        if "Noto Sans CJK SC" not in names:
            import glob

            for ttc in glob.glob("/usr/share/fonts/opentype/noto/*.ttc"):
                try:
                    fm.fontManager.addfont(ttc)
                except Exception:
                    continue
    except Exception:
        pass
    plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * k + result[-1] * (1 - k))
    return result


def macd_series(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float], list[float], list[float]]:
    """标准 MACD：返回 (dif, dea, hist)，hist = dif - dea。"""
    if not closes:
        return [], [], []
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    hist = [d - s for d, s in zip(dif, dea)]
    return dif, dea, hist


def render_chanlun_png(
    symbol: str,
    timeframe: str,
    candles: list[dict],
    *,
    markers: list[dict] | None = None,
    pivots: list[dict] | None = None,
    current_price: float | None = None,
    ma_lines: list[dict] | None = None,
    title: str | None = None,
    width: int = 1100,
    height: int = 760,
) -> bytes:
    """Render a PNG candlestick chart with MACD panel, pivots and markers."""
    if not candles:
        return b""
    _init_font()
    markers = markers or []
    pivots = pivots or []
    ma_lines = ma_lines or []
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]

    fig = plt.figure(figsize=(width / 100, height / 100), dpi=110, facecolor=_BG)
    gs = fig.add_gridspec(3, 1, height_ratios=[3, 1, 1], hspace=0.35,
                          left=0.06, right=0.97, top=0.93, bottom=0.05)
    ax_price = fig.add_subplot(gs[0, 0])
    ax_macd = fig.add_subplot(gs[1, 0], sharex=ax_price)
    ax_extra = fig.add_subplot(gs[2, 0], sharex=ax_price)
    ax_extra.axis("off")

    xs = list(range(len(candles)))

    # ---- K线 ----
    ax_price.set_facecolor(_BG)
    body_w = max(0.5, min(1.0, 40.0 / len(candles)))

    # 中枢矩形（底层背景，半透明，画在K线之前）
    # seg=线段级（蓝，更稳）、stroke=笔级（青，更灵敏）
    for pv in pivots:
        x0 = _index_for_time(candles, pv.get("start"))
        x1 = _index_for_time(candles, pv.get("end"))
        if x1 < 0 or x0 > len(candles) - 1:
            continue
        x0 = max(0, x0)
        x1 = min(len(candles) - 1, x1)
        zd, zg = float(pv["zd"]), float(pv["zg"])
        is_stroke = pv.get("level") == "stroke"
        fill = _PIVOT_STROKE_FILL if is_stroke else _PIVOT_FILL
        edge = _PIVOT_STROKE_EDGE if is_stroke else _PIVOT_EDGE
        alpha = 0.22 if is_stroke else 0.35
        rect = Rectangle((x0, zd), x1 - x0, zg - zd,
                         facecolor=fill, edgecolor=edge,
                         lw=0.8, alpha=alpha, zorder=1)
        ax_price.add_patch(rect)
        ax_price.text(x0 + 1, zg, "笔中枢" if is_stroke else "中枢",
                      color=edge, fontsize=9, va="bottom", ha="left", zorder=2)

    for i, c in enumerate(candles):
        up = c["close"] >= c["open"]
        color = _UP if up else _DOWN
        ax_price.plot([i, i], [c["low"], c["high"]], color=color, lw=0.8)
        rect = Rectangle((i - body_w / 2, min(c["open"], c["close"])),
                         body_w, max(0.6, abs(c["close"] - c["open"])),
                         facecolor=color, edgecolor=color, lw=0.4, zorder=3)
        ax_price.add_patch(rect)

    # MA 均线（可选，供 MA 突破告警图使用）
    for index, ma in enumerate(ma_lines):
        values = ma.get("values") or []
        if len(values) < len(candles):
            values = [None] * (len(candles) - len(values)) + list(values)
        color = _MA_COLORS[index % len(_MA_COLORS)]
        period = ma.get("period") or ""
        valid_x = [i for i, v in enumerate(values) if v is not None]
        valid_y = [values[i] for i in valid_x]
        if valid_x:
            ax_price.plot(valid_x, valid_y, color=color, lw=1.1, zorder=4,
                          label=f"MA{period}")
    if ma_lines:
        ax_price.legend(loc="upper left", fontsize=8, labelcolor=_TEXT,
                        frameon=False)

    # 当前价格线
    if current_price is not None:
        ax_price.axhline(current_price, color=_CURRENT, lw=1.0, ls="--", alpha=0.9)
        ax_price.text(0, current_price, f" 现价 {_format_price(current_price)}",
                      color=_CURRENT, fontsize=10, va="bottom", fontweight="bold")

    # 买卖点标记（中文标签）
    # 线段级（seg，对应线段中枢）：实心圆点 + 中文标签
    # 笔级（stroke，对应笔中枢）：空心方块 + 前缀「笔」的标签
    for m in markers:
        if m.get("p") is None:
            continue
        idx = _index_for_time(candles, m.get("t"))
        is_buy = str(m.get("direction", "")).lower() in ("long", "buy")
        color = _BUY if is_buy else _SELL
        is_stroke = m.get("level") == "stroke"
        if is_stroke:
            ax_price.scatter(idx, float(m["p"]), s=70, facecolors="none",
                             edgecolors=color, linewidths=1.6, zorder=5)
            label = f"笔{str(m.get('k') or '')}"
            fontsize = 11
        else:
            ax_price.scatter(idx, float(m["p"]), s=60, color=color, zorder=5)
            label = str(m.get("k") or "")
            fontsize = 12
        va = "bottom" if not is_buy else "top"
        dy = 12 if not is_buy else -12
        ax_price.annotate(
            label, (idx, float(m["p"])), xytext=(6, dy),
            textcoords="offset points", color=color, fontsize=fontsize,
            fontweight="bold", va=va,
        )

    ax_price.set_facecolor(_BG)
    for spine in ax_price.spines.values():
        spine.set_color(_GRID)
    ax_price.tick_params(colors=_TEXT, labelsize=9)
    ax_price.grid(True, color=_GRID, lw=0.6, alpha=0.7)
    ax_price.set_title(title or f"{symbol} · {timeframe} · 缠论结构", color=_TEXT,
                       fontsize=14, fontweight="bold", pad=8)
    ax_price.set_xticklabels([])

    # ---- MACD ----
    dif, dea, hist = macd_series(closes)
    ax_macd.set_facecolor(_BG)
    for i, h in enumerate(hist):
        ax_macd.bar(i, h, color=_UP if h >= 0 else _DOWN, width=body_w, lw=0)
    ax_macd.plot(xs, dif, color=_DIF, lw=0.9, label="DIF")
    ax_macd.plot(xs, dea, color=_DEA, lw=0.9, label="DEA")
    ax_macd.axhline(0, color=_GRID, lw=0.7)
    ax_macd.legend(loc="upper left", fontsize=8, labelcolor=_TEXT, frameon=False)
    for spine in ax_macd.spines.values():
        spine.set_color(_GRID)
    ax_macd.tick_params(colors=_TEXT, labelsize=8)
    ax_macd.grid(True, color=_GRID, lw=0.5, alpha=0.6)
    ax_macd.set_ylabel("MACD", color=_TEXT, fontsize=10)
    ax_macd.set_xticklabels([])

    # ---- 时间轴 ----
    n = len(candles)
    tick_step = max(1, n // 6)
    tick_pos = list(range(0, n, tick_step))
    if tick_pos and tick_pos[-1] != n - 1:
        tick_pos.append(n - 1)
    labels = []
    for i in tick_pos:
        ts = int(candles[i]["time"])
        labels.append(_time_label(ts, timeframe))
    ax_extra.set_xticks(tick_pos)
    ax_extra.set_xticklabels(labels, color=_TEXT, fontsize=8)
    ax_extra.tick_params(colors=_TEXT, labelsize=8)
    for spine in ax_extra.spines.values():
        spine.set_color(_GRID)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _format_price(value: float) -> str:
    if value >= 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    return f"{value:.6f}"


def _time_label(ts: int, timeframe: str) -> str:
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp(ts, timezone.utc)
    if timeframe.lower().startswith("1d"):
        return dt.strftime("%m-%d")
    return dt.strftime("%m-%d %H:%M")


def _index_for_time(candles: list[dict], ts: Any) -> int:
    if ts is None:
        return len(candles) - 1
    target = int(ts)
    best, best_dist = 0, math.inf
    for i, c in enumerate(candles):
        dist = abs(int(c["time"]) - target)
        if dist < best_dist:
            best, best_dist = i, dist
    return best
