"""
MACD 背离检测器（后端告警用）。

柱极值背离 · 动能极值相对减弱（双向：底背离做多 / 顶背离做空）。
从 fenxing/build_divergence_data.py 移植，去除前端展示字段，聚焦可投递信号。
"""
from __future__ import annotations

from typing import Any

from app.core.indicators import _macd


def macd_extreme_divergence_events(
    candles: list[dict[str, Any]],
    threshold: float = 0.0,
) -> list[dict[str, Any]]:
    """柱极值背离 · 动能极值相对减弱（双向）。

    规则（用户定义）:
      1. 当前极值 hist[i] —— 当前同向柱段内 |MACD柱| 最大的 K 线（买侧: 红柱段最深谷;
         卖侧: 绿柱段最高峰）。极值在段内自然成立, 不依赖全局窗口。
      2. 参照极值 hist[pre] —— 向前穿过 ≥3 根反向柱后, 前一段同向柱区间内 |MACD柱|
         最大的 K 线。
      3. 背离强度 strength = 1 − hist[i]/hist[pre]（同号比值, 动能相对减弱越多越接近1）;
         所有强度>0（动能确实减弱）即标记为背离, 分级:
         ≥0.7 强 / ≥0.4 中 / 其余(0,0.4) 弱。
      4. 告警时点 alertT —— 当前动能段内第一次动能减弱（|MACD柱| 首次缩短）的 K 线。
      5. 两段式确认 —— 极值日 ±3 窗口走完（极值+3 收盘）才确认, 否则 state=pending。

    返回升序事件列表: {t, side("buy"/"sell"), state, confirm_t, alertT,
                       prev_extreme, height_ratio, strength, strength_tier}。
    """
    n = len(candles)
    if n < 60:
        return []
    closes = [float(c["close"]) for c in candles]
    _, _, hist = _macd(closes)
    out: list[dict[str, Any]] = []

    # 1) 切同向柱段（正段 / 负段），并取每段 |hist| 最大为段内极值
    segments: list[tuple[int, int, int, int]] = []  # (sign, start, end, extreme_idx)
    start, sign = 0, 0
    for i, h in enumerate(list(hist) + [None]):
        now = 0 if h is None else (1 if h > 0 else -1)
        if i < n and now == sign:
            continue
        if sign != 0 and i > start:
            seg = hist[start:i]
            ext_i = start + max(range(len(seg)), key=lambda k: abs(seg[k]))
            segments.append((sign, start, i - 1, ext_i))
        sign = now
        start = i

    # 2) 逐段: 找参照极值（穿过≥3反向柱的前一同向段极值）+ 首衰日
    for si in range(1, len(segments)):
        sign, s0, s1, i = segments[si]
        side = "buy" if sign < 0 else "sell"
        if hist[i] is None:
            continue
        # 首衰日: 当前段内 |hist| 首次缩短（相对前一根，含转向前后的比较）
        alert_i = None
        for k in range(s0 + 1, s1 + 1):
            if hist[k] is not None and hist[k - 1] is not None and abs(hist[k]) < abs(hist[k - 1]):
                alert_i = k
                break
        if alert_i is None:
            continue
        # 参照极值: 从当前段往前找, 中间须隔 ≥3 根反向柱
        prev_seg = None
        for pj in range(si - 1, -1, -1):
            psign, ps0, ps1, pidx = segments[pj]
            if psign != sign:
                continue
            gap = s0 - ps1 - 1  # 两同向段之间反向柱根数
            if gap >= 3:
                prev_seg = segments[pj]
                break
        if prev_seg is None:
            continue
        prev = prev_seg[3]
        hist_prev = hist[prev]
        if hist_prev is None or hist_prev == 0:
            continue
        # 3) 背离强度
        ratio = hist[i] / hist_prev  # 同号相除, 正比
        strength = round(1 - ratio, 3)
        if strength <= 0:
            continue  # 动能未减弱(≥0), 不是背离
        tier = "强" if strength >= 0.7 else ("中" if strength >= 0.4 else "弱")
        confirm_i = i + 3  # 极值日 +3 收盘确认
        out.append({
            "t": candles[i]["time"], "side": side,
            "state": "confirmed" if confirm_i < n else "pending",
            "confirm_t": candles[min(confirm_i, n - 1)]["time"],
            "alertT": candles[alert_i]["time"],
            "prev_extreme": candles[prev]["time"],
            "height_ratio": ratio,
            "strength": strength, "strength_tier": tier,
        })
    out.sort(key=lambda x: x["t"])
    return out


def macd_extreme_divergence_array(candles: list[dict[str, Any]]) -> list[int]:
    """柱极值背离事件的逐根信号数组（1=做多, -1=做空, 0=无）。

    供回测/批量信号框架消费，与 macd_extreme_divergence_events 同一逻辑。
    """
    by_ts = {int(c["time"]): i for i, c in enumerate(candles)}
    n = len(candles)
    signals = [0] * n
    for ev in macd_extreme_divergence_events(candles):
        idx = by_ts.get(int(ev["t"]))
        if idx is not None:
            signals[idx] = 1 if ev["side"] == "buy" else -1
    return signals


def _format_event(ev: dict[str, Any]) -> str:
    from datetime import datetime, timezone

    def _day(ts):
        if ts is None:
            return "—"
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")

    side = "多头" if ev["side"] == "buy" else "空头"
    confirm = "已确认" if ev["state"] == "confirmed" else "待确认(±3 窗口未完)"
    return (
        f"**{side}背离**（{ev['strength_tier']}·强度{ev['strength']}）"
        f"参照柱极值 {_day(ev['prev_extreme'])} · 首衰告警 {_day(ev['alertT'])} · {confirm}"
    )
