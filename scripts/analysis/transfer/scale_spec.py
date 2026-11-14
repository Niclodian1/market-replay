"""AI-MA20 参数缩放规格（ScaleSpec）。

把 ai-ma20 v4 规则里写死的常数按「参数五分类」参数化，用于跨周期 / 跨品种迁移实验：

  A 指标平滑/回看窗口   W_n = W_20 · k            （MACD、共振均线、趋势线回看、均量窗口）
  B 事件持续时间阈值    T_n = T_20 · k^alpha      （盘整根数、冷却、MACD 比较间隔、摆动点、标签视窗、持有期）
  B' 假突破窗口        reclaim = 3 · k^reclaim_exp（默认不缩放：whip 率与周期无关）
  C 信号K当根幅度      以 ATR 为单位保持不变       （amp_mode="atr"）或 v4 固定百分比（amp_mode="pct"）
  D 多根累积幅度       × k^H                     （缩放止损 stop_mode="scaled"）

其中 k = n / 20。k=1 + amp_mode="pct" + stop_mode="low" 与 v4 冻结规则逐根一致（见 test_parity.py）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal


@dataclass(frozen=True)
class ScaleSpec:
    n: int = 20                                   # 主均线周期
    alpha: float = 1.0                            # B 类持续时间缩放指数
    H: float = 0.5                                # D 类多根幅度缩放指数
    reclaim_exp: float = 0.0                      # B' 类：reclaimBars 的缩放指数
    amp_mode: Literal["pct", "atr"] = "pct"       # C 类幅度阈值口径
    stop_mode: Literal["low", "scaled"] = "low"   # 1R 止损口径
    trig_bars: int = 1                            # 触发K聚合根数（1=单根K线；>1 用最近 m 根合成一根判断阳线/实体）
    s20: float = 0.85                             # BTC MA20 信号上 (C-L)/ATR 的中位数（scaled 止损用，见 run_experiments 标定）
    label: str = field(default="", compare=False)

    # ---- v4 原始常数（MA20 基准）----
    BASE_RECLAIM: int = 3
    BASE_BARS_BELOW: int = 5
    BASE_PIVOT_LB: int = 3
    BASE_PIVOT_WIN: int = 60
    BASE_PIVOT_GAP: int = 3
    BASE_HIST_LAG: int = 3
    BASE_COOLDOWN: int = 8
    BASE_HORIZON: int = 60
    BASE_HOLD: int = 120
    BASE_AV_WIN: int = 20
    BASE_WARMUP: int = 121
    MACD: tuple[int, int, int] = (12, 26, 9)

    # ---- 派生 ----
    @property
    def k(self) -> float:
        return self.n / 20.0

    def lin(self, w: int) -> int:
        return max(1, int(round(w * self.k)))

    def dur(self, w: int) -> int:
        return max(1, int(round(w * self.k ** self.alpha)))

    @property
    def ma_hi(self) -> int:          # 共振均线：MA20→MA60，MA60→MA180，MA120→MA360
        return 3 * self.n

    @property
    def macd(self) -> tuple[int, int, int]:
        return tuple(self.lin(x) for x in self.MACD)  # type: ignore[return-value]

    @property
    def reclaim(self) -> int:
        return max(1, int(round(self.BASE_RECLAIM * self.k ** self.reclaim_exp)))

    @property
    def bars_below(self) -> int:
        return self.dur(self.BASE_BARS_BELOW)

    @property
    def pivot_lb(self) -> int:
        return self.dur(self.BASE_PIVOT_LB)

    @property
    def pivot_win(self) -> int:
        return self.lin(self.BASE_PIVOT_WIN)

    @property
    def pivot_gap(self) -> int:
        return self.dur(self.BASE_PIVOT_GAP)

    @property
    def hist_lag(self) -> int:
        return self.dur(self.BASE_HIST_LAG)

    @property
    def cooldown(self) -> int:
        return self.dur(self.BASE_COOLDOWN)

    @property
    def horizon(self) -> int:
        return self.dur(self.BASE_HORIZON)

    @property
    def hold(self) -> int:
        return self.dur(self.BASE_HOLD)

    @property
    def av_win(self) -> int:
        return self.lin(self.BASE_AV_WIN)

    @property
    def warmup(self) -> int:
        return self.lin(self.BASE_WARMUP)

    @property
    def amp_k(self) -> float:        # D 类幅度系数
        return self.k ** self.H

    def describe(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if not k.startswith(("BASE_", "MACD"))}
        d.update(k=round(self.k, 3), ma_hi=self.ma_hi, macd=self.macd, reclaim=self.reclaim,
                 bars_below=self.bars_below, pivot_lb=self.pivot_lb, pivot_win=self.pivot_win,
                 pivot_gap=self.pivot_gap, hist_lag=self.hist_lag, cooldown=self.cooldown,
                 horizon=self.horizon, hold=self.hold, av_win=self.av_win, warmup=self.warmup,
                 amp_k=round(self.amp_k, 3))
        return d


V4_IDENTITY = ScaleSpec(label="v4-identity")


def scaled(n: int, alpha: float = 0.65, H: float = 0.69, **kw) -> ScaleSpec:
    """ATR 口径 + 缩放止损的推荐配置。"""
    kw.setdefault("amp_mode", "atr")
    kw.setdefault("stop_mode", "scaled")
    kw.setdefault("label", f"MA{n} a={alpha} H={H}")
    return ScaleSpec(n=n, alpha=alpha, H=H, **kw)


def naive(n: int, **kw) -> ScaleSpec:
    """「直接套用」基线：只换均线周期，其余参数全部沿用 MA20 版本（alpha=0, 不缩放窗口/止损）。"""
    kw.setdefault("label", f"MA{n} naive")
    return _NaiveSpec(n=n, alpha=0.0, H=0.0, **kw)


@dataclass(frozen=True)
class _NaiveSpec(ScaleSpec):
    """A 类窗口也不缩放（k 只作用于主均线），共振均线仍为 MA60。"""

    def lin(self, w: int) -> int:  # noqa: D401
        return w

    @property
    def ma_hi(self) -> int:
        return 60
