"""MACD 柱形图三段顶/底背离（半木夏）— 逐 K 线因果状态机。

规则来源: 《MACD柱形图三段顶背离和三段底背离-半木夏》(2025-11-09)

设计要点（零未来函数）
----------------------
* 引擎是一个逐根推进的状态机 ``MACD3SegDivergence.update(i)``，第 i 根收盘时
  只读取 ``index <= i`` 的数组切片；不使用任何全局极值搜索。
* MACD 用 ``ewm(adjust=False)`` 递推，前缀稳定（截断数据与全量数据在 i 处数值完全相同）。
* “段”的划分全部由已收盘的柱值在线确定：
    1. 柱值变色（穿零轴）→ 一定结束当前段（文中“出现相反颜色的 MACD 柱”）。
    2. 同色柱回落到“非常接近零轴”(|h| <= near_zero_frac × 本段已出现峰值) → 记为“触零”，
       之后若新的柱群峰值达到 confirm_frac × 前段峰值，才确认拆分为两段；
       否则视为同一段内的小回撤并入原段（文中“非常接近出现”）。
    3. 已完成的段若峰值 < noise_frac × 近 scale_window 根 |柱| 最大值，记为“噪声段”，
       只作为隔断使用，不参与 S1/S2 比较。
* 第三段（S3）为“当前正在运行的段”；S1、S2 为之前最近两个同向、非噪声的已完成段。
* 入场: S3 内价格已创出比 S2 更极端的价格，且 S3 柱峰值短于 S2，同时当根柱子相对
  前一根缩短（底: h[T] > h[T-1]；顶: h[T] < h[T-1]，含当根直接变色）→ T 收盘价入场。
* 止损: 硬止损 = S3 影线极值（底 L3 / 顶 P3）；次日动能止损 = T+1 收盘柱子没有继续缩短。
* 二次入场: 止损后 S3 若继续创出新的价格极值（严格超越止损时的 L3/P3）且柱峰值仍
  短于 S2，再次出现缩短即再次入场。若止损发生在变色当根且相反色柱只是噪声级小柱
  （如 2023-06-06 的 +2.4 绿柱），相反色柱被视为 S3 内部的噪声，S3 继续延续。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

ENGINE_VERSION = "macd_3seg_divergence/1.0.0"


# --------------------------------------------------------------------------- 参数
@dataclass(frozen=True)
class Params:
    fast: int = 12
    slow: int = 26
    signal: int = 9
    # 段价格极值口径: 'either' = 影线极值或收盘价极值满足其一即可（兼容偶发单针长影线，如 SPX 2022-02-24），'wick' = 最高/最低价，'close' = 收盘价
    price_basis: str = "either"
    # “非常接近零轴”: |h| <= near_zero_frac × 本段已出现的 |柱| 峰值
    near_zero_frac: float = 0.10
    # 触零后新柱群峰值须达到 confirm_frac × 前段峰值才确认拆段，否则并回原段
    confirm_frac: float = 0.30
    # 噪声段: 段峰值 < noise_frac × 近 scale_window 根 |柱| 最大值
    noise_frac: float = 0.20
    scale_window: int = 100
    # 次日止损后，若相反色柱是噪声级且不超过该根数，S3 延续（二次入场所需）
    merge_blip_max_bars: int = 3
    # 第三段柱峰值下限 = s3_min_frac × |H2|，排除只有 1 根噪声小柱的“伪第三段”
    s3_min_frac: float = 0.05
    # 柱子“缩短”的最小相对幅度。原文最小的被认可缩短为 52.8→51.5（2.5%）；
    # 低于 2% 的差异落在不同交易所数据源的误差范围内，不视为有效缩短。
    min_shrink_frac: float = 0.02
    # 预热根数（MACD 收敛）
    warmup: int = 35
    # 持仓跟踪（只用于统计；原文“效用时长 15-50 根”）
    max_hold: int = 50


# --------------------------------------------------------------------------- 数据结构
@dataclass
class Segment:
    sign: int                 # +1 绿柱(多头段) / -1 红柱(空头段)
    start: int
    end: int
    noise: bool = False
    # 触零拆段的临时状态
    trough: int | None = None
    # 第三段交易状态: idle / open / consumed / stopped
    state: str = "idle"
    rearm_ref: float | None = None
    stop_bar: int | None = None
    n_signals: int = 0
    absorbed_blips: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class SignalEvent:
    kind: str                 # 'buy' | 'sell' | 'stop_next' | 'stop_hard' | 'expire'
    label: str                # '3段底背离买' / '3段顶背离卖' / '次日止损' / '硬止损' / '持有到期'
    bar: int
    time: int                 # 该 K 线开盘时间 (epoch 秒)
    date: str
    price: float
    side: str                 # 'long' | 'short'
    trade_id: int
    attempt: int = 1          # 同一 S3 内第几次入场（2 = 二次入场）
    stop_price: float | None = None
    segments: list[dict[str, Any]] | None = None
    hist: float | None = None
    note: str = ""


# --------------------------------------------------------------------------- 指标
def macd_hist(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """递推 EMA (adjust=False)；前缀稳定，可用于因果截断校验。"""
    s = pd.Series(close, dtype="float64")
    dif = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif.to_numpy(), dea.to_numpy(), (dif - dea).to_numpy()


# --------------------------------------------------------------------------- 引擎
class MACD3SegDivergence:
    """逐 K 线推进的三段背离识别器。"""

    def __init__(self, df: pd.DataFrame, params: Params | None = None):
        self.p = params or Params()
        df = df.reset_index(drop=True)
        self.t = _epoch_seconds(df)
        self.o = df["open"].to_numpy(float)
        self.h_ = df["high"].to_numpy(float)
        self.l_ = df["low"].to_numpy(float)
        self.c = df["close"].to_numpy(float)
        self.dif, self.dea, self.hist = macd_hist(self.c, self.p.fast, self.p.slow, self.p.signal)
        self.n = len(df)
        self.segments: list[Segment] = []
        self.live: Segment | None = None
        self.events: list[SignalEvent] = []
        self.trades: list[dict[str, Any]] = []
        self._open: dict[str, Any] | None = None      # 等待 T+1 检查的仓位
        self._holding: list[dict[str, Any]] = []      # 通过次日检查、仍在跟踪硬止损的仓位
        self._trade_seq = 0
        self.i = -1

    # ------------------------------------------------------------------ 工具
    def _px(self, sign: int) -> np.ndarray:
        if self.p.price_basis == "wick":
            return self.h_ if sign > 0 else self.l_
        return self.c

    def _scale(self, i: int) -> float:
        a = max(0, i - self.p.scale_window + 1)
        return float(np.max(np.abs(self.hist[a:i + 1]))) if i >= 0 else 0.0

    def seg_stats(self, seg: Segment, upto: int | None = None) -> dict[str, Any]:
        """段统计（只读 seg.start..min(seg.end, upto)）。"""
        e = seg.end if upto is None else min(seg.end, upto)
        s = seg.start
        hh = self.hist[s:e + 1]
        px = self._px(seg.sign)[s:e + 1]
        wick = (self.h_ if seg.sign > 0 else self.l_)[s:e + 1]
        cls = self.c[s:e + 1]
        if seg.sign > 0:
            k, kp, kw, kc = int(np.argmax(hh)), int(np.argmax(px)), int(np.argmax(wick)), int(np.argmax(cls))
        else:
            k, kp, kw, kc = int(np.argmin(hh)), int(np.argmin(px)), int(np.argmin(wick)), int(np.argmin(cls))
        return {
            "sign": seg.sign, "start": s, "end": e,
            "peak": float(hh[k]), "peak_bar": s + k, "peak_abs": float(abs(hh[k])),
            "px": float(px[kp]), "px_bar": s + kp,
            "wick": float(wick[kw]), "wick_bar": s + kw,
            "close_ext": float(cls[kc]), "close_bar": s + kc,
        }

    def _seg_dict(self, seg: Segment, upto: int | None = None, tag: str = "") -> dict[str, Any]:
        st = self.seg_stats(seg, upto)
        return {
            "tag": tag, "sign": st["sign"],
            "start_time": int(self.t[st["start"]]), "end_time": int(self.t[st["end"]]),
            "start_date": _d(self.t[st["start"]]), "end_date": _d(self.t[st["end"]]),
            "hist_peak": round(st["peak"], 4), "hist_time": int(self.t[st["peak_bar"]]), "hist_date": _d(self.t[st["peak_bar"]]),
            "price": round(st["px"], 8), "price_time": int(self.t[st["px_bar"]]), "price_date": _d(self.t[st["px_bar"]]),
            "wick": round(st["wick"], 8), "wick_time": int(self.t[st["wick_bar"]]),
        }

    def _finish(self, seg: Segment, end: int) -> None:
        seg.end = end
        seg.trough = None
        st = self.seg_stats(seg)
        seg.noise = st["peak_abs"] < self.p.noise_frac * self._scale(end)
        self.segments.append(seg)

    def _refs(self, s3: Segment) -> tuple[Segment, Segment] | None:
        same = [s for s in self.segments if s.sign == s3.sign and not s.noise and s.end < s3.start]
        if len(same) < 2:
            return None
        return same[-2], same[-1]

    # ------------------------------------------------------------------ 段维护
    def _update_segments(self, i: int) -> None:
        h = self.hist[i]
        sgn = 1 if h > 0 else (-1 if h < 0 else (self.live.sign if self.live else 1))
        live = self.live
        if live is None:
            self.live = Segment(sign=sgn, start=i, end=i)
            return
        if sgn != live.sign:
            # 变色: 结束当前段
            self._finish(live, i - 1)
            # 噪声小柱 + S3 已止损 → S3 延续（二次入场机制）
            blip = self.segments[-1]
            if len(self.segments) >= 2:
                prev = self.segments[-2]
                if (blip.noise and prev.sign == sgn and prev.state == "stopped"
                        and prev.stop_bar is not None and prev.stop_bar >= blip.start
                        and blip.end - blip.start + 1 <= self.p.merge_blip_max_bars
                        and prev.end == blip.start - 1):
                    self.segments.pop()          # 移除噪声小柱
                    self.segments.pop()          # 取回 S3
                    prev.absorbed_blips.append((blip.start, blip.end))
                    prev.end = i
                    prev.noise = False
                    self.live = prev
                    return
            self.live = Segment(sign=sgn, start=i, end=i)
            return
        # 同色延续
        live.end = i
        ah = abs(h)
        if live.trough is None:
            ref = float(np.max(np.abs(self.hist[live.start:i])) if i > live.start else 0.0)
            if ref > 0 and ref >= self.p.noise_frac * self._scale(i) and ah <= self.p.near_zero_frac * ref:
                live.trough = i
            return
        # 已触零: 维护谷底，并检查新柱群是否确认
        t = live.trough
        if ah <= abs(self.hist[t]):
            live.trough = i
            return
        ref_peak = float(np.max(np.abs(self.hist[live.start:t + 1])))
        tent_peak = float(np.max(np.abs(self.hist[t + 1:i + 1])))
        if tent_peak >= self.p.confirm_frac * ref_peak:
            # 确认拆段: 旧段到谷底为止，新段从谷底后一根开始（只改当前及以前的归属，不改历史信号）
            old_state = live.state
            self._finish(live, t)
            new = Segment(sign=live.sign, start=t + 1, end=i)
            self.live = new
            if old_state == "open":
                pass

    # ------------------------------------------------------------------ 交易状态
    def _emit(self, **kw) -> SignalEvent:
        ev = SignalEvent(**kw)
        self.events.append(ev)
        return ev

    def _check_open(self, i: int) -> None:
        tr = self._open
        if tr is None or tr["entry_bar"] != i - 1:
            return
        self._open = None
        seg: Segment = tr["seg"]
        long = tr["side"] == "long"
        h, hp = self.hist[i], self.hist[i - 1]
        hard = (self.l_[i] <= tr["stop"]) if long else (self.h_[i] >= tr["stop"])
        momentum_fail = (h <= hp) if long else (h >= hp)
        if hard or momentum_fail:
            if hard:
                px, kind, label, note = tr["stop"], "stop_hard", "硬止损", "T+1 盘中触及第三段极值"
                if momentum_fail:
                    note += "，且收盘柱子未继续缩短"
            else:
                px, kind, label, note = float(self.c[i]), "stop_next", "次日止损", "T+1 收盘柱子未继续缩短"
            self._close_trade(tr, i, px, kind, label, note)
            seg.state = "stopped"
            seg.stop_bar = i
            seg.rearm_ref = tr["stop"]          # 第三段影线极值（含入场当根）
        else:
            seg.state = "consumed"
            self._holding.append(tr)

    def _check_holding(self, i: int) -> None:
        keep = []
        for tr in self._holding:
            long = tr["side"] == "long"
            if (long and self.l_[i] <= tr["stop"]) or (not long and self.h_[i] >= tr["stop"]):
                self._close_trade(tr, i, tr["stop"], "stop_hard", "硬止损", "持仓期间触及第三段极值")
                seg: Segment = tr["seg"]
                if seg is self.live:
                    seg.state = "stopped"
                    seg.stop_bar = i
                    seg.rearm_ref = tr["stop"]
            elif i - tr["entry_bar"] >= self.p.max_hold:
                self._close_trade(tr, i, float(self.c[i]), "expire", "持有到期", f"{self.p.max_hold} 根到期统计")
            else:
                keep.append(tr)
        self._holding = keep

    def _close_trade(self, tr: dict, i: int, px: float, kind: str, label: str, note: str) -> None:
        long = tr["side"] == "long"
        ret = (px / tr["entry"] - 1) if long else (1 - px / tr["entry"])
        tr.update(exit_bar=i, exit_time=int(self.t[i]), exit_date=_d(self.t[i]), exit_price=float(px),
                  exit_kind=kind, exit_label=label, ret=float(ret), bars=i - tr["entry_bar"])
        self._emit(kind=kind, label=label, bar=i, time=int(self.t[i]), date=_d(self.t[i]), price=float(px),
                   side=tr["side"], trade_id=tr["id"], attempt=tr["attempt"], stop_price=tr["stop"],
                   hist=float(self.hist[i]), note=note)

    # ------------------------------------------------------------------ 触发
    def _try_trigger(self, i: int) -> None:
        if i < max(self.p.warmup, 1) or self._open is not None:
            return
        # S3 = 包含 T-1 的段
        if self.live is not None and self.live.start <= i - 1:
            s3 = self.live
        elif self.segments and self.segments[-1].end == i - 1:
            s3 = self.segments[-1]
        else:
            return
        h, hp = self.hist[i], self.hist[i - 1]
        sign = s3.sign
        if sign < 0 and not (hp < 0 and h > hp):
            return
        if sign > 0 and not (hp > 0 and h < hp):
            return
        if s3.state in ("open", "consumed"):
            return
        refs = self._refs(s3)
        if refs is None:
            return
        s1, s2 = refs
        a, b = self.seg_stats(s1), self.seg_stats(s2)
        # S3 统计: 柱子用 T-1 及以前（S3 色柱），价格含当根（若当根仍为 S3 色）
        # 价格含触发当根（即使当根柱子已变色，如 2019-12-18 当根影线 6435 才是第三个低点）
        upto_px = i
        c3 = self.seg_stats(Segment(sign=sign, start=s3.start, end=upto_px))
        c3h = self.seg_stats(s3, i - 1)
        if sign < 0:
            wick_ok = a["wick"] > b["wick"] > c3["wick"]
            close_ok = a["close_ext"] > b["close_ext"] > c3["close_ext"]
        else:
            wick_ok = a["wick"] < b["wick"] < c3["wick"]
            close_ok = a["close_ext"] < b["close_ext"] < c3["close_ext"]
        if self.p.price_basis == "wick":
            price_ok = wick_ok
        elif self.p.price_basis == "close":
            price_ok = close_ok
        else:
            price_ok = wick_ok or close_ok
        k = 1.0 - self.p.min_shrink_frac
        hist_ok = (b["peak_abs"] < k * a["peak_abs"] and c3h["peak_abs"] < k * b["peak_abs"]
                   and c3h["peak_abs"] >= self.p.s3_min_frac * b["peak_abs"])
        if not (price_ok and hist_ok):
            return
        if s3.state == "stopped":
            # 二次入场: 止损之后须严格创出新的价格低点/高点（按影线，与硬止损口径一致）
            if s3.stop_bar is None or upto_px <= s3.stop_bar:
                return
            wk = self.l_ if sign < 0 else self.h_
            seg_after = wk[s3.stop_bar + 1:upto_px + 1]
            ext = float(np.min(seg_after) if sign < 0 else np.max(seg_after))
            if not ((ext < s3.rearm_ref) if sign < 0 else (ext > s3.rearm_ref)):
                return
        # 入场
        self._trade_seq += 1
        s3.n_signals += 1
        s3.state = "open"
        wick_upto = i  # 硬止损含当根影线
        wick_arr = self.l_ if sign < 0 else self.h_
        stop = float(np.min(wick_arr[s3.start:wick_upto + 1]) if sign < 0 else np.max(wick_arr[s3.start:wick_upto + 1]))
        side = "long" if sign < 0 else "short"
        segs = [self._seg_dict(s1, tag="S1"), self._seg_dict(s2, tag="S2"),
                self._seg_dict(Segment(sign=sign, start=s3.start, end=upto_px), tag="S3")]
        segs[2]["hist_peak"] = round(c3h["peak"], 4)
        segs[2]["hist_time"] = int(self.t[c3h["peak_bar"]])
        segs[2]["hist_date"] = _d(self.t[c3h["peak_bar"]])
        tr = {"id": self._trade_seq, "side": side, "entry_bar": i, "entry_time": int(self.t[i]), "entry_date": _d(self.t[i]),
              "entry": float(self.c[i]), "stop": stop, "attempt": s3.n_signals, "seg": s3, "segments": segs}
        self._open = tr
        self.trades.append(tr)
        self._emit(kind="buy" if side == "long" else "sell", label="3段底背离买" if side == "long" else "3段顶背离卖",
                   bar=i, time=int(self.t[i]), date=_d(self.t[i]), price=float(self.c[i]), side=side,
                   trade_id=tr["id"], attempt=s3.n_signals, stop_price=stop, segments=segs, hist=float(h),
                   note=("二次入场" if s3.n_signals > 1 else "首次入场") + ("（柱子已变色）" if np.sign(h) != sign else ""))

    # ------------------------------------------------------------------ 主循环
    def update(self, i: int) -> None:
        assert i == self.i + 1, "必须逐根推进"
        self.i = i
        self._check_open(i)
        self._check_holding(i)
        self._update_segments(i)
        self._try_trigger(i)

    def run(self) -> "MACD3SegDivergence":
        for i in range(self.n):
            self.update(i)
        return self

    # ------------------------------------------------------------------ 输出
    def result(self) -> dict[str, Any]:
        trades = []
        for tr in self.trades:
            d = {k: v for k, v in tr.items() if k != "seg"}
            i = tr["entry_bar"]
            long = tr["side"] == "long"
            fw = {}
            for k in (15, 30, 50):
                j = min(self.n - 1, i + k)
                if j > i:
                    r = self.c[j] / tr["entry"] - 1
                    fw[f"ret{k}"] = float(r if long else -r)
            end = min(self.n - 1, i + self.p.max_hold)
            if end > i:
                if long:
                    fw["mfe50"] = float(np.max(self.h_[i + 1:end + 1]) / tr["entry"] - 1)
                    fw["mae50"] = float(np.min(self.l_[i + 1:end + 1]) / tr["entry"] - 1)
                else:
                    fw["mfe50"] = float(1 - np.min(self.l_[i + 1:end + 1]) / tr["entry"])
                    fw["mae50"] = float(1 - np.max(self.h_[i + 1:end + 1]) / tr["entry"])
            d["forward"] = fw
            trades.append(d)
        return {
            "engine": ENGINE_VERSION,
            "params": asdict(self.p),
            "events": [asdict(e) for e in self.events],
            "trades": trades,
        }


# --------------------------------------------------------------------------- 便捷函数
def detect(df: pd.DataFrame, params: Params | None = None) -> dict[str, Any]:
    return MACD3SegDivergence(df, params).run().result()


def _epoch_seconds(df: pd.DataFrame) -> np.ndarray:
    if "time" in df.columns:
        t = df["time"]
        if np.issubdtype(t.dtype, np.number):
            return t.to_numpy(dtype="int64")
        return (pd.to_datetime(t, utc=True).astype("int64") // 10**9).to_numpy()
    if "open_time" in df.columns:
        return (pd.to_datetime(df["open_time"], utc=True).astype("int64") // 10**9).to_numpy()
    raise KeyError("需要 time 或 open_time 列")


def _d(ts: int) -> str:
    return pd.Timestamp(int(ts), unit="s", tz="UTC").strftime("%Y-%m-%d %H:%M")[:16].replace(" 00:00", "")
