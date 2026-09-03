"""生成 MACD 背离对比查看器数据 divergence_data.js。

在 BTC 日线上跑两套背离检测器（柱极值·动能极值相对减弱 / 缠论中枢背驰·C/A面积比），
归一化为 window.DIVERGENCE_DATA 供 divergence_viewer.html 使用。
纯本地计算，无 DB、无网络。

用法:
  cd fenxing && python3 build_divergence_data.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_FENXING_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _FENXING_DIR.parent

sys.path.insert(0, str(_REPO_ROOT / "backend"))
sys.path.insert(0, str(_FENXING_DIR))

SYMBOL = "BTC-USD"
TIMEFRAME = "1day"
PARQUET = _REPO_ROOT / "data" / "crypto" / "BTC_USD_1day.parquet"
OUT_JS = _FENXING_DIR / "divergence_data.js"


def _load_candles() -> list[dict]:
    df = pd.read_parquet(PARQUET)
    df = df[["time", "open", "high", "low", "close", "volume"]].dropna()
    df = df.sort_values("time")
    candles = [
        {
            "time": int(r.time),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
        }
        for r in df.itertuples()
    ]
    return candles


def _round(v):
    return None if v is None else round(float(v), 2)


def _macd_divergence_strong(candles: list[dict], threshold: float = 0.0) -> list[dict]:
    """柱极值背离 · 动能极值相对减弱（双向：底背离做多 / 顶背离做空）。

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
    """
    from app.core.indicators import _macd

    n = len(candles)
    if n < 60:
        return []
    closes = [c["close"] for c in candles]
    _, _, hist = _macd(closes)
    out: list[dict] = []

    # 1) 切同向柱段（正段 / 负段），并取每段 |hist| 最大为段内极值
    segments = []  # (sign, start, end, extreme_idx)
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
        ratio = hist[i] / hist_prev          # 同号相除, 正比
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
            "height_ratio": _round(ratio),
            "strength": strength, "strength_tier": tier,
        })
    out.sort(key=lambda x: x["t"])
    return out


def main() -> None:
    candles = _load_candles()
    if len(candles) < 200:
        raise SystemExit(f"K线不足: {len(candles)}")

    closes = [c["close"] for c in candles]

    # MACD 柱（供前端渲染，与 candles 对齐）
    from app.core.indicators import _macd
    _, _, hist = _macd(closes)

    detectors: list[dict] = []

    # 1) 柱极值背离 · 动能极值相对减弱（双向：底背离做多 / 顶背离做空）
    swing_strong = _macd_divergence_strong(candles)
    detectors.append({
        "key": "swing_strong", "label": "柱极值背离 · 动能极值相对减弱",
        "desc": "双向动能背离: 当前同向段极值 hist[i] vs 穿过≥3根反向柱的前一同向段极值 "
                "hist[pre]; 背离强度=1−hist[i]/hist[pre], 所有动能减弱(强度>0)均标记, "
                "≥0.7强 / ≥0.4中 / 其余弱; 告警时点=段内首次动能减弱; 极值日±3 两段式确认。",
    })

    # 2) 缠论背驰（chanlun_service → fenxing）
    from app.core import chanlun_service
    chanlun_ok = chanlun_service.chanlun_available()
    chanlun = []
    if chanlun_ok:
        try:
            levels = chanlun_service.analyze_symbol({TIMEFRAME: candles})
            for sig in chanlun_service.extract_signals(SYMBOL, levels):
                chanlun.append({
                    "t": sig["t"],
                    "side": "buy" if sig["direction"] == "long" else "sell",
                    "mode": sig["mode"],
                    "k": sig["k"],
                    "state": sig["state"],
                    "tier": sig["tier"],
                    "price": _round(sig["price"]),
                    "why": sig["why"],
                    "ratio": _round(sig["ratio"]),
                    "trigger": sig["trigger"],
                    "alertT": sig["alertT"],
                    "sub": sig["sub"],
                    "upper": sig["upper"],
                })
        except Exception as exc:  # 缠论失败仅降级该组
            print(f"[warn] 缠论分析失败，该组留空: {exc}")
            chanlun = []
    detectors.append({
        "key": "chanlun", "label": "缠论中枢背驰 · C/A面积比",
        "desc": "中枢内 C离开段 vs A进入段 MACD柱面积比 c<a*0.9 → 一买/一卖趋势背驰；"
                "ratio<0.7 为明显；抢先层(笔级)有背驰买/卖，含在途 forming。",
    })

    for name in ("swing_strong", "chanlun"):
        signals = locals()[name]
        signals.sort(key=lambda x: x["t"])

    payload = {
        "meta": {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "source": PARQUET.name,
            "nBars": len(candles),
            "firstDate": datetime.fromtimestamp(candles[0]["time"], tz=timezone.utc)
                .strftime("%Y-%m-%d"),
            "lastDate": datetime.fromtimestamp(candles[-1]["time"], tz=timezone.utc)
                .strftime("%Y-%m-%d"),
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "macdParams": {"fast": 12, "slow": 26, "signal": 9},
            "detectors": detectors,
            "chanlunAvailable": chanlun_ok,
        },
        "candles": [[c["time"], c["open"], c["high"], c["low"], c["close"]]
                    for c in candles],
        "hist": [[candles[i]["time"], hist[i]] for i in range(len(candles))],
        "signals": {
            "swing_strong": swing_strong,
            "chanlun": chanlun,
        },
    }

    body = "window.DIVERGENCE_DATA = %s;\n" % json.dumps(payload, ensure_ascii=False)
    OUT_JS.write_text(body, encoding="utf-8")

    print(f"已写入 {OUT_JS.name}  ({len(body) / 1024:.0f} KB)")
    print(f"  数据: {len(candles)} 根 {payload['meta']['firstDate']} ~ "
          f"{payload['meta']['lastDate']} | MACD {payload['meta']['macdParams']}")
    for d in detectors:
        print(f"  {d['label']:<40} {len(payload['signals'][d['key']]):>4} 条")


if __name__ == "__main__":
    main()
