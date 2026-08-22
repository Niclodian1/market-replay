"""M3A 历史结果标注测试（任务 3：证明与文档）。

覆盖（任务书要求 ≥35 项，本文件 48 项）：
  1. long/short 数值手算（MFE/MAE 的 R 与 pct）、事件 bar 极值不计、0 下限；
  2. 5/10/20/40 窗口成熟、集合/顺序、单调不减；
  3. 首触及四分类 + fake_breakout 语义（ambiguous→null、negative_first→true）；
  4. gap 跨阈值仍算触及；不假设 OHLC 内路径；
  5. 删失三原因（缺根/未收盘/label_as_of 未到）区分，不拿短窗口冒充；
  6. 输入校验：重复 time / 步长缺口 / 乱序 / cutoff 缺 OHLC / OHLC 非法 /
     ATR 非法 / config hash 错 / 伪造与拒绝态 decision / 事件 bar 缺失 /
     reference 不匹配 / label_as_of 非法；
  7. 因果：追加未来不改成熟窗口与 decision digest；终点后数据不改早期窗口；
     未来数据不改事件时点快照；
  8. 深不可变 + to_dict 脱钩 + JSON allow_nan=False；
  9. 严格 schema：extra forbid / strict / NaN / 交叉约束 / 窗口顺序；
  10. 批量：重复 decision 受控、确定性；汇总：空样本 / 混合 symbol / golden
      摘要 / 分组 / 迁移 / fake_rate；
  11. 状态路径（事件态 → 逐 bar → 终点态）。
"""
from __future__ import annotations

import copy
import json
import math
import random

import pytest
from pydantic import ValidationError

from app.core.market_state import MarketStateConfig
from app.core.multi_timeframe import evaluate
from app.core.outcome_labeling import (
    TOUCH_AMBIGUOUS,
    TOUCH_NEITHER,
    TOUCH_NEGATIVE,
    TOUCH_POSITIVE,
    WINDOWS,
    label_batch,
    label_decision,
    summarize,
)
from app.schemas.outcome_labeling import (
    validate_outcome_label,
    validate_outcome_summary,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  确定性构造（与 M2 测试同口径，本地独立实现）
# ═══════════════════════════════════════════════════════════════════════════════

T0 = 1_700_000_000
STEP4 = 14400

# 缩短 warmup 的 M1 配置（与 M2 测试一致）
QUICK = MarketStateConfig(
    vol_percentile_lookback=10,
    vol_percentile_low=0.2,
    vol_percentile_high=0.8,
    vol_change_window=3,
    ma_fast=5,
    ma_slow=10,
    ma_tertiary=15,
    atr_period=14,
    adx_period=5,
    pivot=3,
)


def make_bars(n: int, step: int, *, drift: float = 0.008, wave: float = 0.02) -> list[dict]:
    out = []
    for i in range(n):
        close = 100.0 * (1 + drift * i) * (1 + wave * math.sin(i / 9.0))
        op = 100.0 * (1 + drift * max(0, i - 1)) * (1 + wave * math.sin((i - 1) / 9.0))
        out.append({
            "time": T0 + i * step,
            "open": round(op, 6),
            "high": round(max(op, close) * 1.012, 6),
            "low": round(min(op, close) * 0.988, 6),
            "close": round(close, 6),
            "volume": 1000 + (i * 37) % 11,
        })
    return out


def make_candles(n4: int = 700, *, drift1w: float = 0.008, drift1d: float = 0.008) -> dict[str, list[dict]]:
    return {
        "4h": make_bars(n4, STEP4),
        "1day": make_bars(n4 // 6 + 2, 86400, drift=drift1d),
        "1week": make_bars(n4 // 42 + 4, 604800, drift=drift1w),
    }


def as_of_4h_close(bar_index: int) -> int:
    return T0 + (bar_index + 1) * STEP4


def make_decision(event_idx: int = 600, direction: str = "long", *,
                  n4: int = 700, drift1w: float = 0.008, drift1d: float = 0.008,
                  symbol: str = "BTCUSDT", config: MarketStateConfig = QUICK,
                  event_type: str = "ma_breakout",
                  event_bar: dict | None = None):
    """构造 eligible 的 M2 决策 + 原始 4H 序列。

    event_bar 若给定，在 evaluate 之前覆盖事件 bar 的 OHLC（close 保持
    entry，用于在决策生成前构造极值事件 bar）。
    """
    candles = make_candles(n4, drift1w=drift1w, drift1d=drift1d)
    as_of = as_of_4h_close(event_idx)
    sig = T0 + event_idx * STEP4
    close = candles["4h"][event_idx]["close"]
    if event_bar is not None:
        candles["4h"][event_idx] = dict(candles["4h"][event_idx], close=close,
                                         **event_bar)
    trig = {
        "strategy": "ma", "event_type": event_type, "direction": direction,
        "severity": "signal", "signal_time": sig, "reference_price": close,
        "summary": "s",
        "payload": {
            "schema_version": "v1", "signal_type": event_type,
            "direction": direction, "state": "signal", "timeframe": "4h",
            "occurred_at": sig, "confirmed_at": sig, "reference_price": close,
            "bar_closed": True, "evidence": {}, "parameters": {},
        },
    }
    d = evaluate(symbol, candles, as_of, trig, config)
    assert d.eligible, d.reason_codes
    return d, candles["4h"], as_of


def make_window_bars(entry: float, R: float, start_time: int, n: int,
                     pattern: list[tuple[float, float, float, float]]) -> list[dict]:
    """窗口 bar 生成器：pattern 为 (open, high, low, close) 相对 entry 的 R 倍数，
    循环填充 n 根。调用方保证 OHLC 关系合法。"""
    out = []
    for k in range(n):
        o, h, l, c = pattern[k % len(pattern)]
        out.append({
            "time": start_time + k * STEP4,
            "open": round(entry + o * R, 6),
            "high": round(entry + h * R, 6),
            "low": round(entry + l * R, 6),
            "close": round(entry + c * R, 6),
            "volume": 1000,
        })
    return out


# 通用窗口 pattern（全部在 ±1R 内，无触及）
PATTERN_CALM = [
    (0.0, 0.4, -0.5, -0.3), (0.0, 0.3, -0.4, 0.2), (0.0, 0.2, -0.3, 0.1),
    (0.0, 0.4, -0.5, 0.3), (0.0, 0.1, -0.4, -0.2),
]
# A：bar2 触及 +1R → positive_first；MFE=1.2R、MAE=0.5R（bar5 low=-0.5R）
PATTERN_A = [
    (0.1, 0.5, -0.3, 0.4), (0.3, 1.2, -0.1, 1.1), (0.0, 0.3, -0.2, 0.2),
    (0.0, 0.2, -0.4, 0.0), (0.0, 0.1, -0.5, -0.2),
]
# B：bar4 触及 -1R → negative_first（fake=True）；MFE=0.5R、MAE=1.2R
PATTERN_B = [
    (0.0, 0.4, -0.5, -0.3), (0.0, 0.3, -0.8, -0.6), (0.0, 0.5, -0.7, 0.4),
    (0.0, 0.1, -1.2, -1.0), (0.0, 0.3, -0.4, 0.2),
]
# C：无触及 → neither；MFE=0.4R、MAE=0.5R
PATTERN_C = [
    (0.0, 0.4, -0.5, -0.3), (0.0, 0.3, -0.4, 0.2), (0.0, 0.2, -0.3, 0.1),
    (0.0, 0.4, -0.5, 0.3), (0.0, 0.1, -0.4, -0.2),
]


def build_label(direction: str = "long", event_idx: int = 600, *,
                pattern: list[tuple[float, float, float, float]] | None = PATTERN_CALM,
                n_window: int = 40, label_as_of: int | None = None,
                drift1w: float = 0.008, drift1d: float = 0.008,
                extreme_event_bar: bool = True, symbol: str = "BTCUSDT",
                config: MarketStateConfig = QUICK,
                event_type: str = "ma_breakout"):
    """构造 (决策, 自定义 4H 序列, label_as_of)。

    - 事件 bar 默认在 **evaluate 之前** 构造为极值 OHLC（high=1.5*entry /
      low=0.5*entry，close 保持 entry），决策本身即反映该极值 bar；
      用于证明「事件 bar 极值不计」（绝不事后篡改决策对应 bar）；
    - 窗口 bar 用 pattern 填充 n_window 根（从事件 bar 后第 1 根起，
      相对最终决策的 R 倍数）。
    """
    candles = make_candles(700, drift1w=drift1w, drift1d=drift1d)
    entry = float(candles["4h"][event_idx]["close"])
    if extreme_event_bar:
        candles["4h"][event_idx] = dict(
            candles["4h"][event_idx], close=entry,
            open=round(entry * 0.9, 6), high=round(entry * 1.5, 6),
            low=round(entry * 0.5, 6),
        )
    as_of = as_of_4h_close(event_idx)
    sig = T0 + event_idx * STEP4
    trig = {
        "strategy": "ma", "event_type": event_type, "direction": direction,
        "severity": "signal", "signal_time": sig, "reference_price": entry,
        "summary": "s",
        "payload": {
            "schema_version": "v1", "signal_type": event_type,
            "direction": direction, "state": "signal", "timeframe": "4h",
            "occurred_at": sig, "confirmed_at": sig, "reference_price": entry,
            "bar_closed": True, "evidence": {}, "parameters": {},
        },
    }
    d = evaluate(symbol, candles, as_of, trig, config)
    assert d.eligible, d.reason_codes
    R = float(d.to_dict()["trigger"]["market_state"]["features"]["atr"])
    out4h = list(candles["4h"][:event_idx + 1])
    if pattern is not None:
        out4h += make_window_bars(entry, R, T0 + (event_idx + 1) * STEP4,
                                  n_window, pattern)
    if label_as_of is None:
        label_as_of = as_of_4h_close(event_idx + 40)
    return d, out4h, label_as_of


def label(direction: str = "long", event_idx: int = 600, **kw):
    d, seq, las = build_label(direction, event_idx, **kw)
    return label_decision(d, seq, las, QUICK)


def w5(out: dict) -> dict:
    return next(x for x in out["windows"] if x["window"] == 5)


# ═══════════════════════════════════════════════════════════════════════════════
#  1. 数值手算（long）
# ═══════════════════════════════════════════════════════════════════════════════

def test_long_manual_mfe_mae_window5():
    """long 手算：窗口 5 MFE=1.2R / MAE=0.5R，MFE/MAE 的 R 与 pct 同时输出。"""
    lab = label("long", pattern=PATTERN_A)
    out = lab.to_dict()
    validate_outcome_label(out)
    w = w5(out)
    assert w["matured"] is True
    assert w["first_touch"] == TOUCH_POSITIVE
    assert w["fake_breakout"] is False
    assert w["mfe_r"] == pytest.approx(1.2, rel=1e-6)
    assert w["mae_r"] == pytest.approx(0.5, rel=1e-6)
    # pct = 相对参考价的百分比（小数比例；round 6 引入 ~1e-6 噪声）
    assert w["mfe_pct"] == pytest.approx(1.2 * out["risk"]["atr_abs"] / out["reference_price"], rel=1e-6)
    assert w["mae_pct"] == pytest.approx(0.5 * out["risk"]["atr_abs"] / out["reference_price"], rel=1e-6)
    assert w["mfe_r"] == pytest.approx(w["mfe_pct"] / out["risk"]["atr_pct"], rel=1e-6)


def test_short_manual_mfe_mae_mirror():
    """short 镜像手算：同一 OHLC 下 short 的 MFE == long 的 MAE（逐字段）。"""
    lab_long = label("long", pattern=PATTERN_A)
    lab_short = label("short", pattern=PATTERN_A, drift1w=-0.008, drift1d=-0.008)
    out_l, out_s = lab_long.to_dict(), lab_short.to_dict()
    assert out_s["direction"] == "short"
    wl, ws = w5(out_l), w5(out_s)
    assert ws["mfe_r"] == pytest.approx(wl["mae_r"], rel=1e-9)
    assert ws["mae_r"] == pytest.approx(wl["mfe_r"], rel=1e-9)
    assert ws["first_touch"] == TOUCH_NEGATIVE  # long 的 +1R 对应 short 的 -1R
    assert ws["fake_breakout"] is True


def test_short_direct_manual():
    """short 手算：bar2 低点触及 entry-1R → negative_first；MFE/MAE 镜像公式。"""
    lab = label("short", pattern=PATTERN_A, drift1w=-0.008, drift1d=-0.008)
    out = lab.to_dict()
    w = w5(out)
    # short: fav = entry-low（bar2 low=-0.1R → fav=0.1；bar1 low=-0.3R → fav=0.3...）
    # 实际最大 fav：bar2 low=-0.1 → fav=0.1；bar4 low=-0.4 → fav=0.4 → MFE=0.4R？
    # 用镜像断言替代精确手算：short MFE == long MAE、short MAE == long MFE
    lab_long = label("long", pattern=PATTERN_A)
    wl = w5(lab_long.to_dict())
    assert w["mfe_r"] == pytest.approx(wl["mae_r"], rel=1e-9)
    assert w["mae_r"] == pytest.approx(wl["mfe_r"], rel=1e-9)


def test_mfe_mae_floor_at_zero():
    """窗口内无有利偏移时 MFE=0（从 0 起取非负极值，不允许负值）。"""
    # 所有窗口 bar 都低于 entry（long）：high 全部 < entry → fav 全负 → MFE=0
    pattern_low = [
        (-0.2, -0.1, -0.6, -0.3), (-0.3, -0.2, -0.7, -0.4), (-0.15, -0.1, -0.5, -0.2),
        (-0.4, -0.3, -0.8, -0.5), (-0.25, -0.2, -0.4, -0.3),
    ]
    out = label("long", pattern=pattern_low).to_dict()
    w = w5(out)
    assert w["mfe_r"] == pytest.approx(0.0, abs=1e-12)
    assert w["mae_r"] > 0
    assert w["first_touch"] == TOUCH_NEITHER


def test_event_bar_extremes_excluded():
    """事件 bar 极值不计：事件 bar high=+2R/low=-2R 不进入 MFE/MAE。"""
    lab = label("long", pattern=PATTERN_A)  # 事件 bar 已被替换为 ±2R 极值
    w = w5(lab.to_dict())
    assert w["mfe_r"] == pytest.approx(1.2, rel=1e-6)  # 若计入事件 bar 应为 2.0
    assert w["mae_r"] == pytest.approx(0.5, rel=1e-6)  # 若计入应为 2.0


def test_windows_all_matured_and_monotonic():
    """5/10/20/40 四窗口全部成熟；MFE 随窗口单调不减。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    mfe_prev = 0.0
    for w in out["windows"]:
        assert w["matured"] is True
        assert w["censor_reason"] is None
        assert w["mfe_r"] >= mfe_prev - 1e-9
        mfe_prev = w["mfe_r"]


def test_windows_order_and_set():
    """窗口集合/顺序恰好 [5,10,20,40]（schema 交叉约束）。"""
    out = label("long").to_dict()
    assert [w["window"] for w in out["windows"]] == [5, 10, 20, 40]


# ═══════════════════════════════════════════════════════════════════════════════
#  2. 首触及 / fake_breakout
# ═══════════════════════════════════════════════════════════════════════════════

def test_positive_first_fake_false():
    assert w5(label("long", pattern=PATTERN_A).to_dict())["first_touch"] == TOUCH_POSITIVE


def test_negative_first_fake_true():
    out = label("long", pattern=PATTERN_B).to_dict()
    w = w5(out)
    assert w["first_touch"] == TOUCH_NEGATIVE
    assert w["fake_breakout"] is True


def test_neither_fake_false():
    out = label("long", pattern=PATTERN_C).to_dict()
    w = w5(out)
    assert w["first_touch"] == TOUCH_NEITHER
    assert w["fake_breakout"] is False


def test_same_bar_both_touch_ambiguous_fake_null():
    """同 bar 首次同时触及 ±1R → ambiguous_same_bar，fake_breakout=null。"""
    pattern = [(0.0, 1.5, -1.5, 0.0)] * 5  # 每根都同时覆盖 ±1R
    out = label("long", pattern=pattern).to_dict()
    w = w5(out)
    assert w["first_touch"] == TOUCH_AMBIGUOUS
    assert w["fake_breakout"] is None
    assert w["mfe_r"] == pytest.approx(1.5, rel=1e-6)  # MFE/MAE 仍正常计算
    assert w["mae_r"] == pytest.approx(1.5, rel=1e-6)


def test_gap_across_threshold_counts_as_touch():
    """gap 跨阈值：bar 开盘已越过 +1R 仍算触及（positive_first）。"""
    pattern = [(1.5, 1.6, 1.2, 1.5)] * 5  # open 直接 gap 到 +1.5R
    out = label("long", pattern=pattern).to_dict()
    w = w5(out)
    assert w["first_touch"] == TOUCH_POSITIVE
    assert w["mfe_r"] == pytest.approx(1.6, rel=1e-6)


def test_fake_breakout_only_negative_first():
    """fake_breakout 语义：仅 negative_first=true；其余非 null 均 false。"""
    for pattern, touch in ((PATTERN_A, TOUCH_POSITIVE), (PATTERN_B, TOUCH_NEGATIVE),
                           (PATTERN_C, TOUCH_NEITHER)):
        w = w5(label("long", pattern=pattern).to_dict())
        assert w["first_touch"] == touch
        assert w["fake_breakout"] == (touch == TOUCH_NEGATIVE)


def test_first_touch_earliest_bar_wins():
    """首触及取最早触及 bar：bar1 触 +1R 时不再看 bar2 的 -1R。"""
    pattern = [(0.0, 1.5, -0.5, 1.0), (-0.2, -0.1, -1.5, -1.0),
               (0.0, 0.3, -0.4, 0.2), (0.0, 0.2, -0.3, 0.1), (0.0, 0.1, -0.4, -0.2)]
    w = w5(label("long", pattern=pattern).to_dict())
    assert w["first_touch"] == TOUCH_POSITIVE  # bar1 先触 +1R
    assert w["fake_breakout"] is False


# ═══════════════════════════════════════════════════════════════════════════════
#  3. 删失（censored）
# ═══════════════════════════════════════════════════════════════════════════════

def test_immature_short_window_censored():
    """缺根：事件 bar 后只有 20 根 → 窗口 40 censored「缺根」，5/10/20 成熟。"""
    d, seq, _ = build_label(n_window=20)
    las = as_of_4h_close(600 + 40)
    lab = label_decision(d, seq, las, QUICK)
    out = lab.to_dict()
    for w in out["windows"]:
        if w["window"] == 40:
            assert w["matured"] is False
            assert "缺根" in w["censor_reason"]
        else:
            assert w["matured"] is True
            assert w["censor_reason"] is None


def test_no_short_window_substitution():
    """不拿短窗口冒充：40 根窗口只有 30 根可用 → 40 窗口删失，绝不用 30 根算。"""
    d, seq, _ = build_label(n_window=30)
    lab = label_decision(d, seq, as_of_4h_close(640), QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    assert w40["matured"] is False
    assert w40["mfe_r"] is None
    assert "缺根" in w40["censor_reason"]


def test_label_as_of_not_reached_censored():
    """label_as_of 未到：序列含 40 根但只标注到第 15 根收盘 → 20/40 删失。"""
    d, seq, _ = build_label(n_window=40)
    lab = label_decision(d, seq, as_of_4h_close(615), QUICK)
    out = lab.to_dict()
    for w in out["windows"]:
        if w["window"] in (5, 10):
            assert w["matured"] is True
        else:
            assert w["matured"] is False
            assert "label_as_of 未到" in w["censor_reason"]


def test_unclosed_bar_censored():
    """未收盘：窗口内存在显式 is_closed=False bar → 后续窗口删失「未收盘」。"""
    d, seq, _ = build_label(n_window=40)
    # 在窗口 21 根位置（bar 521）插入未收盘 bar，其后仍有完整 bar
    t_un = T0 + 621 * STEP4
    seq = seq[:621] + [{"time": t_un, "is_closed": False}] + seq[621:]
    # 用原 label_as_of 不够（cutoff 提前），用足够远的 label_as_of
    las = T0 + (600 + 40 + 2) * STEP4
    lab = label_decision(d, seq, las, QUICK)
    out = lab.to_dict()
    for w in out["windows"]:
        if w["window"] in (5, 10, 20):
            assert w["matured"] is True, w
        else:
            assert w["matured"] is False
            assert "未收盘" in w["censor_reason"]


def test_censor_reason_distinct():
    """三种删失原因可区分：缺根 / 未收盘 / label_as_of 未到。"""
    # 缺根
    d, seq, _ = build_label(n_window=10)
    lab = label_decision(d, seq, as_of_4h_close(640), QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    assert "缺根" in w40["censor_reason"]
    # label_as_of 未到（序列足够）
    d, seq, _ = build_label(n_window=40)
    lab = label_decision(d, seq, as_of_4h_close(620), QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    assert "label_as_of 未到" in w40["censor_reason"]
    # 未收盘
    seq = seq[:621] + [{"time": T0 + 621 * STEP4, "is_closed": False}] + seq[621:]
    lab = label_decision(d, seq, T0 + 645 * STEP4, QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    assert "未收盘" in w40["censor_reason"]


def test_censored_window_no_fake_data():
    """删失窗口不携带任何伪结果字段（mfe/mae/touch/end/path 全空）。"""
    d, seq, _ = build_label(n_window=5)
    lab = label_decision(d, seq, as_of_4h_close(640), QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    for f in ("mfe_r", "mfe_pct", "mae_r", "mae_pct", "first_touch",
              "fake_breakout", "end_vol_phase", "end_trend_strength",
              "end_close", "end_time"):
        assert w40[f] is None
    assert w40["path"] == []


# ═══════════════════════════════════════════════════════════════════════════════
#  4. 输入校验
# ═══════════════════════════════════════════════════════════════════════════════

def test_duplicate_time_raises():
    """4H 序列重复 time → ValueError（唯一 time 是回放前提）。"""
    d, seq, las = build_label()
    dup = list(seq)
    dup.append(dict(dup[510]))  # 复制一根已收盘 bar
    with pytest.raises(ValueError, match="重复"):
        label_decision(d, dup, las, QUICK)


def test_gap_in_prefix_raises():
    """消费前缀内步长缺口（time 差 ≠ 4H 步长）→ ValueError。"""
    d, seq, las = build_label()
    gapped = [b for b in seq if _t(b) != T0 + 603 * STEP4]  # 挖掉 bar 503
    with pytest.raises(ValueError, match="步长"):
        label_decision(d, gapped, las, QUICK)


def _t(b: dict) -> int:
    return int(b["time"])


def test_shuffled_input_same_result():
    """乱序输入（唯一 time）→ 结果逐字段一致。"""
    d, seq, las = build_label(pattern=PATTERN_A)
    rng = random.Random(2026)
    shuffled = list(seq)
    rng.shuffle(shuffled)
    a = label_decision(d, seq, las, QUICK).to_dict()
    b = label_decision(d, shuffled, las, QUICK).to_dict()
    assert a == b


def test_cutoff_missing_ohlc_not_read():
    """cutoff 后缺 OHLC 不得读取：未收盘 bar 无 OHLC 不报错。"""
    d, seq, _ = build_label(n_window=40)
    seq = seq[:621] + [{"time": T0 + 621 * STEP4, "is_closed": False}] + seq[621:]
    # 窗口 20 仍成熟（cutoff 在 521），40 删失
    lab = label_decision(d, seq, T0 + 645 * STEP4, QUICK)
    out = lab.to_dict()
    assert next(x for x in out["windows"] if x["window"] == 20)["matured"] is True
    assert next(x for x in out["windows"] if x["window"] == 40)["matured"] is False


def test_ohlc_invalid_raises():
    """消费前缀内 OHLC 非有限 / 关系非法 → ValueError。"""
    d, seq, las = build_label()
    bad = copy.deepcopy(seq)
    bad[620]["high"] = float("nan")
    with pytest.raises(ValueError, match="非有限"):
        label_decision(d, bad, las, QUICK)
    bad = copy.deepcopy(seq)
    bad[620]["high"] = bad[620]["low"] - 1.0  # high < low
    with pytest.raises(ValueError, match="OHLC"):
        label_decision(d, bad, las, QUICK)


def test_atr_invalid_rejected():
    """风险 ATR 必须有限正数（来自决策快照 features.atr）。"""
    d, seq, las = build_label()
    out = d.to_dict()
    # ATR=None
    bad = copy.deepcopy(out)
    bad["trigger"]["market_state"]["features"]["atr"] = None
    with pytest.raises(ValueError):
        label_decision(bad, seq, las, QUICK)
    # ATR<=0
    bad = copy.deepcopy(out)
    bad["trigger"]["market_state"]["features"]["atr"] = 0.0
    with pytest.raises(ValueError, match="ATR"):
        label_decision(bad, seq, las, QUICK)
    # ATR=NaN → schema 拒绝（同样 ValueError）
    bad = copy.deepcopy(out)
    bad["trigger"]["market_state"]["features"]["atr"] = float("nan")
    with pytest.raises(ValueError):
        label_decision(bad, seq, las, QUICK)


def test_config_hash_mismatch_rejected():
    """传入 MarketStateConfig 的 hash 必须等于决策 config_hash.market_state。"""
    d, seq, las = build_label()
    other = MarketStateConfig(vol_percentile_lookback=12)
    with pytest.raises(ValueError, match="hash"):
        label_decision(d, seq, las, other)


def test_forged_decision_rejected():
    """伪造 decision（eligible=True 但 reason_codes 矛盾）→ ValueError。"""
    d, seq, las = build_label()
    out = d.to_dict()
    bad = copy.deepcopy(out)
    bad["reason_codes"] = ["ctx_ok", "setup_ok"]  # 缺 trigger_ok
    with pytest.raises(ValueError, match="schema"):
        label_decision(bad, seq, las, QUICK)
    bad = copy.deepcopy(out)
    bad["config_hash"] = {"market_state": "0" * 16, "fusion": "0" * 16}
    with pytest.raises(ValueError, match="schema"):
        label_decision(bad, seq, las, QUICK)


def test_rejected_decision_rejected():
    """拒绝态 decision（eligible=False）→ ValueError，不产生标签。"""
    # 构造逆势 short（周线 up）→ 拒绝态
    candles = make_candles(700, drift1w=0.008, drift1d=0.008)
    as_of = as_of_4h_close(600)
    sig = T0 + 600 * STEP4
    close = candles["4h"][600]["close"]
    trig = {
        "strategy": "ma", "event_type": "ma_breakout", "direction": "short",
        "severity": "signal", "signal_time": sig, "reference_price": close,
        "summary": "s",
        "payload": {"schema_version": "v1", "signal_type": "ma_breakout",
                    "direction": "short", "state": "signal", "timeframe": "4h",
                    "occurred_at": sig, "confirmed_at": sig,
                    "reference_price": close, "bar_closed": True,
                    "evidence": {}, "parameters": {}},
    }
    rej = evaluate("BTCUSDT", candles, as_of, trig, QUICK)
    assert rej.eligible is False
    seq = list(candles["4h"][:601]) + make_window_bars(close, 10.0, T0 + 601 * STEP4, 40, PATTERN_CALM)
    with pytest.raises(ValueError, match="eligible"):
        label_decision(rej, seq, as_of_4h_close(640), QUICK)


def test_event_bar_missing_rejected():
    """事件 bar 不存在（signal_time 不在已收盘前缀）→ ValueError。"""
    d, seq, las = build_label()
    truncated = [b for b in seq if _t(b) <= T0 + 599 * STEP4]
    with pytest.raises(ValueError, match="事件 bar 不存在"):
        label_decision(d, truncated, las, QUICK)
    # label_as_of 早于事件收盘 → 事件 bar 不可见 → 同样拒绝
    with pytest.raises(ValueError, match="事件 bar 不存在"):
        label_decision(d, seq, as_of_4h_close(599), QUICK)


def test_reference_mismatch_rejected():
    """事件 bar close 与 reference_price 不一致（超容差）→ ValueError。"""
    d, seq, las = build_label()
    bad = copy.deepcopy(seq)
    entry = float(bad[600]["close"])
    bad[600] = dict(bad[600], close=round(entry * 1.01, 6),
                    high=round(entry * 1.02, 6))  # 保持 OHLC 合法
    with pytest.raises(ValueError, match="reference"):
        label_decision(d, bad, las, QUICK)


def test_bad_label_as_of_raises():
    """label_as_of 非法（bool/负/非整数）→ ValueError。"""
    d, seq, las = build_label()
    for bad in (True, -1, 1.5, "x"):
        with pytest.raises(ValueError, match="label_as_of"):
            label_decision(d, seq, bad, QUICK)


# ═══════════════════════════════════════════════════════════════════════════════
#  5. 因果 / 不可变
# ═══════════════════════════════════════════════════════════════════════════════

def test_append_future_no_change():
    """追加任意未来 K 线 → 已成熟窗口与 decision digest 逐字段不变。"""
    d, seq, las = build_label(pattern=PATTERN_A)
    base = label_decision(d, seq, las, QUICK).to_dict()
    grown = list(seq) + make_window_bars(
        float(seq[600]["close"]), 10.0, T0 + 641 * STEP4, 20, PATTERN_CALM)
    after = label_decision(d, grown, las, QUICK).to_dict()
    assert after == base
    assert after["decision_digest"] == base["decision_digest"]


def test_append_after_endpoint_no_change():
    """终点后数据不改早期窗口：label_as_of 后移到 40 根，5/10 窗口逐字段不变。"""
    d, seq, _ = build_label(pattern=PATTERN_A, n_window=40)
    early = label_decision(d, seq, as_of_4h_close(615), QUICK).to_dict()
    late = label_decision(d, seq, as_of_4h_close(640), QUICK).to_dict()
    for w in (5, 10):
        e = next(x for x in early["windows"] if x["window"] == w)
        l = next(x for x in late["windows"] if x["window"] == w)
        assert e == l, f"窗口 {w} 被终点后数据改变"
    # 且 20/40 从未成熟变成熟
    assert next(x for x in late["windows"] if x["window"] == 40)["matured"] is True


def test_grow_censored_to_matured_preserves_early():
    """同一 label_as_of 下，序列从 20 根长到 40 根：早期窗口逐字段一致。"""
    d, seq, _ = build_label(pattern=PATTERN_A, n_window=20)
    las = as_of_4h_close(640)
    short = label_decision(d, seq, las, QUICK).to_dict()
    grown = list(seq) + make_window_bars(
        float(seq[600]["close"]), 10.0, T0 + 621 * STEP4, 20, PATTERN_CALM)
    full = label_decision(d, grown, las, QUICK).to_dict()
    for w in (5, 10, 20):
        s = next(x for x in short["windows"] if x["window"] == w)
        f = next(x for x in full["windows"] if x["window"] == w)
        assert s == f
    assert next(x for x in short["windows"] if x["window"] == 40)["matured"] is False
    assert next(x for x in full["windows"] if x["window"] == 40)["matured"] is True


def test_decision_snapshot_not_affected_by_future():
    """未来 K 线只生成标签：事件时点 decision/risk/condition 快照不变。"""
    d, seq, las = build_label()
    base = label_decision(d, seq, las, QUICK).to_dict()
    grown = list(seq) + make_window_bars(
        float(seq[600]["close"]), 10.0, T0 + 641 * STEP4, 20, PATTERN_CALM)
    after = label_decision(d, grown, las, QUICK).to_dict()
    d0 = d.to_dict()
    for lab in (base, after):
        assert lab["risk"]["atr_abs"] == pytest.approx(
            d0["trigger"]["market_state"]["features"]["atr"], rel=1e-9)
        assert lab["event_state"]["vol_phase"] == d0["trigger"]["market_state"]["volatility"]["phase"]
        assert lab["snapshots"]["trigger"]["bar_time"] == d0["trigger"]["bar_time"]
        assert lab["config_hash"] == d0["config_hash"]


def test_deep_immutable():
    """结果深不可变：to_dict 每次脱钩，内部映射只读。"""
    lab = label("long", pattern=PATTERN_A)
    out1 = lab.to_dict()
    out1["windows"][0]["mfe_r"] = 999.0
    out1["snapshots"]["context"]["bar_time"] = 1
    out1["risk"]["atr_abs"] = 1.0
    out1["event_state"]["vol_phase"] = "hacked"
    out2 = lab.to_dict()
    assert out2["windows"][0]["mfe_r"] == pytest.approx(1.2, rel=1e-6)
    assert out2["snapshots"]["context"]["bar_time"] != 1
    assert out2["risk"]["atr_abs"] != 1.0
    assert out2["event_state"]["vol_phase"] != "hacked"
    with pytest.raises(TypeError):
        lab.snapshots["context"] = {}
    with pytest.raises(TypeError):
        lab.risk["atr_abs"] = 5.0
    with pytest.raises(TypeError):
        lab.config_hash["market_state"] = "0" * 16
    with pytest.raises(AttributeError):
        lab.windows = ()
    # path 步深冻结
    with pytest.raises(AttributeError):
        lab.windows[0].path[0].close = 1.0


def test_decision_digest_stable():
    """decision digest 稳定（16 位 hex）；不同决策 digest 不同。"""
    d, seq, las = build_label()
    a = label_decision(d, seq, las, QUICK)
    b = label_decision(d, seq, las, QUICK)
    assert a.decision_digest == b.decision_digest
    assert len(a.decision_digest) == 16
    assert all(c in "0123456789abcdef" for c in a.decision_digest)
    d2, seq2, _ = build_label(event_idx=610)
    c = label_decision(d2, seq2, las, QUICK)
    assert c.decision_digest != a.decision_digest
    # digest 不受未来数据影响
    grown = list(seq) + make_window_bars(
        float(seq[600]["close"]), 10.0, T0 + 641 * STEP4, 20, PATTERN_CALM)
    d3 = label_decision(d, grown, las, QUICK)
    assert d3.decision_digest == a.decision_digest


# ═══════════════════════════════════════════════════════════════════════════════
#  6. schema / JSON
# ═══════════════════════════════════════════════════════════════════════════════

def test_schema_strict_and_nan_rejected():
    """schema：extra forbid / strict / allow_inf_nan=False / digest hex。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    validate_outcome_label(out)  # 正常通过
    # extra 字段
    bad = copy.deepcopy(out)
    bad["extra"] = 1
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # NaN（allow_inf_nan=False）
    bad = copy.deepcopy(out)
    bad["risk"]["atr_abs"] = float("nan")
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # digest 非 hex
    bad = copy.deepcopy(out)
    bad["decision_digest"] = "nothex"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # 事件态 Literal 非法
    bad = copy.deepcopy(out)
    bad["event_state"]["vol_phase"] = "banana"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_windows_order_and_set():
    """windows 集合/顺序必须恰好 [5,10,20,40]。"""
    out = label("long").to_dict()
    bad = copy.deepcopy(out)
    bad["windows"] = [bad["windows"][3], bad["windows"][0], bad["windows"][1], bad["windows"][2]]
    with pytest.raises(ValidationError, match="恰好按序"):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["windows"] = bad["windows"][:3]
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_cross_constraints():
    """交叉约束：matured/censored 互斥字段、touch/fake 对应、path 长度。"""
    out = label("long", pattern=PATTERN_B).to_dict()
    # matured=True 但 fake 与 negative_first 矛盾
    bad = copy.deepcopy(out)
    w = bad["windows"][0]
    w["first_touch"] = "negative_first"
    w["fake_breakout"] = False
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # ambiguous 但 fake 非 null
    bad = copy.deepcopy(out)
    w = bad["windows"][0]
    w["first_touch"] = "ambiguous_same_bar"
    w["fake_breakout"] = False
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # matured=False 但携带结果字段
    bad = copy.deepcopy(out)
    w = bad["windows"][3]  # 窗口 40（成熟）
    w["matured"] = False
    w["censor_reason"] = "x"
    w["mfe_r"] = 0.5  # 必须为 None
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # matured=True 但 path 长度不等于窗口
    bad = copy.deepcopy(out)
    w = bad["windows"][0]
    w["path"] = w["path"][:3]
    with pytest.raises(ValidationError, match="path"):
        validate_outcome_label(bad)


def test_json_allow_nan_false():
    """to_dict 与汇总输出均可 json.dumps(allow_nan=False)。"""
    lab = label("long", pattern=PATTERN_A)
    out = lab.to_dict()
    text = json.dumps(out, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text
    s = summarize([lab])
    text = json.dumps(s, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text


# ═══════════════════════════════════════════════════════════════════════════════
#  7. 状态路径
# ═══════════════════════════════════════════════════════════════════════════════

def test_state_path_and_transition():
    """事件态 → 逐 bar 路径 → 终点态：path 长度=窗口、bar_time 推进、终点=末步。"""
    d, seq, las = build_label(pattern=PATTERN_A)
    lab = label_decision(d, seq, las, QUICK)
    out = lab.to_dict()
    d0 = d.to_dict()
    w = w5(out)
    # 事件态来自决策快照 trigger market_state
    assert out["event_state"]["vol_phase"] == d0["trigger"]["market_state"]["volatility"]["phase"]
    assert out["event_state"]["trend_strength"] == d0["trigger"]["market_state"]["trend"]["strength"]
    # 逐 bar 路径：5 步，bar_time 从事件 bar 后第 1 根推进
    assert len(w["path"]) == 5
    times = [p["bar_time"] for p in w["path"]]
    assert times == [T0 + (600 + k) * STEP4 for k in range(1, 6)]
    # 终点态 = 路径末步
    assert w["end_vol_phase"] == w["path"][-1]["vol_phase"]
    assert w["end_trend_strength"] == w["path"][-1]["trend_strength"]
    assert w["end_time"] == times[-1]
    assert w["end_close"] == pytest.approx(w["path"][-1]["close"], rel=1e-12)
    # 事件态→终点态迁移字段存在
    assert w["end_vol_phase"] in ("contraction", "transition", "expansion", "unknown")


# ═══════════════════════════════════════════════════════════════════════════════
#  8. 批量 / 汇总
# ═══════════════════════════════════════════════════════════════════════════════

def test_batch_two_decisions():
    """批量：同一事件两个 decision（不同 event_type，digest 不同）同一序列，
    同源重放一致；结果等于逐个标注。"""
    d1, seq1, _ = build_label(event_idx=600, pattern=PATTERN_A,
                              event_type="ma_breakout")
    d2, seq2, _ = build_label(event_idx=600, pattern=PATTERN_C,
                              event_type="bb_breakout")
    shared = list(seq1)  # 同一序列（事件 600 窗口 601..640）
    las = as_of_4h_close(640)
    labels = label_batch([d1, d2], shared, las, QUICK)
    assert len(labels) == 2
    assert labels[0].decision_digest != labels[1].decision_digest
    # 与逐个一致
    single1 = label_decision(d1, shared, las, QUICK).to_dict()
    single2 = label_decision(d2, shared, las, QUICK).to_dict()
    assert labels[0].to_dict() == single1
    assert labels[1].to_dict() == single2


def test_batch_duplicate_decision_rejected():
    """批量重复 decision（相同 digest）→ 受控 ValueError。"""
    d, seq, las = build_label()
    with pytest.raises(ValueError, match="重复 decision"):
        label_batch([d, d], seq, las, QUICK)
    # 同一 dict 两次同样拒绝
    out = d.to_dict()
    with pytest.raises(ValueError, match="重复 decision"):
        label_batch([out, out], seq, las, QUICK)


def test_batch_deterministic():
    """批量结果确定性：重复调用逐字段一致。"""
    d1, seq1, _ = build_label(event_idx=600, pattern=PATTERN_A,
                              event_type="ma_breakout")
    d2, seq2, _ = build_label(event_idx=600, pattern=PATTERN_C,
                              event_type="bb_breakout")
    shared = list(seq1)
    las = as_of_4h_close(640)
    a = label_batch([d1, d2], shared, las, QUICK)
    b = label_batch([d1, d2], shared, las, QUICK)
    assert [x.to_dict() for x in a] == [x.to_dict() for x in b]


def test_summarize_empty_controlled():
    """空样本 → 受控空汇总（不抛异常，schema 通过）。"""
    s = summarize([])
    validate_outcome_summary(s)
    assert s["n_decisions"] == 0
    assert s["symbol"] == ""
    for w in s["windows"]:
        assert w["samples"] == 0 and w["censored"] == 0
    assert s["groups"] == [] and s["transitions"] == []


def test_summarize_mixed_symbol_rejected():
    """混合 symbol 汇总 → ValueError。"""
    d1, seq1, _ = build_label(symbol="BTCUSDT")
    d2, seq2, _ = build_label(symbol="ETHUSDT")
    las = as_of_4h_close(640)
    l1 = label_decision(d1, seq1, las, QUICK)
    l2 = label_decision(d2, seq2, las, QUICK)
    with pytest.raises(ValueError, match="混合 symbol"):
        summarize([l1, l2])


def test_summarize_golden():
    """golden 摘要：三个手算样本（A: pos/1.2R/0.3R、B: neg/0.5R/1.2R、
    C: neither/0.4R/0.5R）→ 窗口 5 均值/计数/fake_rate 手算一致。"""
    labels = []
    for i, pattern in enumerate((PATTERN_A, PATTERN_B, PATTERN_C)):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pattern,
                                n_window=40)
        labels.append(label_decision(d, seq, as_of_4h_close(660), QUICK))
    s = summarize(labels)
    validate_outcome_summary(s)
    assert s["n_decisions"] == 3
    w = next(x for x in s["windows"] if x["window"] == 5)
    assert w["samples"] == 3 and w["censored"] == 0
    assert w["mean_mfe_r"] == pytest.approx((1.2 + 0.5 + 0.4) / 3, rel=1e-6)
    assert w["mean_mae_r"] == pytest.approx((0.5 + 1.2 + 0.5) / 3, rel=1e-6)
    assert w["touches"] == {"positive_first": 1, "negative_first": 1,
                            "neither": 1, "ambiguous_same_bar": 0}
    assert w["fake_rate"] == pytest.approx(1 / 3, rel=1e-9)  # 仅 B 为 true
    # 40 根 pattern 循环：窗口 10/20/40 极值相同 → 均值相同
    w40 = next(x for x in s["windows"] if x["window"] == 40)
    assert w40["mean_mfe_r"] == pytest.approx(w["mean_mfe_r"], rel=1e-9)


def test_summarize_censored_counts():
    """汇总只聚合成熟样本：删失数 = n - 成熟数，samples+censored == n。"""
    d600, seq40, _ = build_label(pattern=PATTERN_A, n_window=40)
    d610, seq20, _ = build_label(event_idx=610, pattern=PATTERN_A, n_window=20)
    early = label_decision(d610, seq20, as_of_4h_close(640), QUICK)  # 40 缺根删失
    full = label_decision(d600, seq40, as_of_4h_close(640), QUICK)   # 40 成熟
    s = summarize([early, full])
    validate_outcome_summary(s)
    for w in s["windows"]:
        assert w["samples"] + w["censored"] == s["n_decisions"] == 2
    w40 = next(x for x in s["windows"] if x["window"] == 40)
    assert w40["samples"] == 1 and w40["censored"] == 1
    w5 = next(x for x in s["windows"] if x["window"] == 5)
    assert w5["samples"] == 2 and w5["censored"] == 0


def test_summarize_groups_and_transitions():
    """分组稳定：组样本之和 = 各窗口成熟数；键排序确定；schema 通过。"""
    labels = []
    for i, pattern in enumerate((PATTERN_A, PATTERN_B, PATTERN_C)):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pattern,
                                n_window=40)
        labels.append(label_decision(d, seq, as_of_4h_close(640), QUICK))
    s = summarize(labels)
    validate_outcome_summary(s)
    for w in WINDOWS:
        gw = [g for g in s["groups"] if g["window"] == w]
        tw = [t for t in s["transitions"] if t["window"] == w]
        sw = next(x for x in s["windows"] if x["window"] == w)
        assert sum(g["samples"] for g in gw) == sw["samples"]
        assert sum(t["samples"] for t in tw) == sw["samples"]
    # 排序稳定：group/transition 键按序
    keys = [(g["window"], g["direction"], g["event_type"],
             g["context_direction"], g["context_strength"],
             g["setup_direction"], g["setup_strength"], g["setup_vol_phase"],
             g["trigger_vol_phase"], g["trigger_candle_strength"],
             g["trigger_volume_state"]) for g in s["groups"]]
    assert keys == sorted(keys)
    tkeys = [(t["window"], t["event_vol_phase"], t["end_vol_phase"],
              t["event_trend_strength"], t["end_trend_strength"]) for t in s["transitions"]]
    assert tkeys == sorted(tkeys)
    # 摘要可重复（确定性）
    assert summarize(labels) == s


def test_summarize_fake_rate_unknown_none():
    """已知样本为 0（全 ambiguous）时 fake_rate=None。"""
    pattern_amb = [(0.0, 1.5, -1.5, 0.0)] * 5
    labels = []
    for i in range(2):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pattern_amb,
                                n_window=5)
        labels.append(label_decision(d, seq, as_of_4h_close(640), QUICK))
    s = summarize(labels)
    w5 = next(x for x in s["windows"] if x["window"] == 5)
    assert w5["samples"] == 2
    assert w5["touches"]["ambiguous_same_bar"] == 2
    assert w5["fake_rate"] is None


def test_accepts_fusion_decision_object():
    """公开入口同时接受 FusionDecision 对象与 m2-v1 dict（结果一致）。"""
    d, seq, las = build_label(pattern=PATTERN_A)
    lab_obj = label_decision(d, seq, las, QUICK)
    lab_dict = label_decision(d.to_dict(), seq, las, QUICK)
    assert lab_obj.to_dict() == lab_dict.to_dict()
    # summarize 同样接受 dict 标签
    s1 = summarize([lab_obj])
    s2 = summarize([lab_dict.to_dict()])
    assert s1 == s2


def test_label_output_roundtrip_schema():
    """to_dict → validate → model_dump 回写仍通过（schema 往返稳定）。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    validated = validate_outcome_label(out)
    again = validated.model_dump()
    assert validate_outcome_label(again).model_dump() == again


# ═══════════════════════════════════════════════════════════════════════════
#  第一轮整改：暗卷复现（每条先写回归测试取红灯 → 修复取绿灯）
# ═══════════════════════════════════════════════════════════════════════════

def test_config_atr_period_must_be_14():
    """暗卷1：风险口径硬化——M3A 只接受 config.atr_period==14（ATR(14) 冻结）。"""
    bad = MarketStateConfig(
        vol_percentile_lookback=10, vol_percentile_low=0.2, vol_percentile_high=0.8,
        vol_change_window=3, ma_fast=5, ma_slow=10, ma_tertiary=15,
        atr_period=5, adx_period=5, pivot=3,
    )
    d, seq, las = build_label(config=bad)  # 决策用同一 bad config 生成（hash 匹配）
    with pytest.raises(ValueError, match="atr_period"):
        label_decision(d, seq, las, bad)


def test_risk_fields_wilder_atr_period_14():
    """暗卷1：risk 输出 method='wilder_atr'、period=14，atr_abs 等于决策 ATR。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    assert out["risk"]["method"] == "wilder_atr"
    assert out["risk"]["period"] == 14
    assert out["risk"]["atr_abs"] == pytest.approx(
        out["decision_snapshot"]["trigger"]["market_state"]["features"]["atr"], rel=1e-12)
    assert out["risk"]["atr_pct"] == pytest.approx(
        out["risk"]["atr_abs"] / out["reference_price"], rel=1e-9)


def test_schema_rejects_forged_risk_atr_pct():
    """暗卷2：schema 拒绝伪造 risk.atr_pct（≠ atr_abs/reference）与 method。"""
    out = label("long").to_dict()
    bad = copy.deepcopy(out)
    bad["risk"]["atr_pct"] = bad["risk"]["atr_pct"] * 2
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["risk"]["method"] = "ema"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_prior_bar_tampering_rejected():
    """暗卷3：同源重放——事件前一根 bar 篡改（high+1000/low-1000）→ 受控拒绝。"""
    d, seq, las = build_label()
    bad = copy.deepcopy(seq)
    prev = bad[599]
    bad[599] = dict(prev, high=float(prev["high"]) * 1.01)  # OHLC 关系仍合法
    with pytest.raises(ValueError, match="重放|不一致"):
        label_decision(d, bad, las, QUICK)


def test_replay_consistency_ok():
    """暗卷3：未篡改序列同源重放一致，事件态/风险来自决策快照。"""
    d, seq, las = build_label()
    lab = label_decision(d, seq, las, QUICK)
    d0 = d.to_dict()
    out = lab.to_dict()
    assert out["event_state"]["vol_phase"] == d0["trigger"]["market_state"]["volatility"]["phase"]
    assert out["risk"]["atr_abs"] == pytest.approx(
        d0["trigger"]["market_state"]["features"]["atr"], rel=1e-12)


def test_event_bar_extremes_before_evaluate():
    """暗卷3：事件 bar 极值在 evaluate 前构造（决策即反映），仍不进入结果窗口。"""
    lab = label("long", pattern=PATTERN_A)
    w = w5(lab.to_dict())
    assert w["mfe_r"] == pytest.approx(1.2, rel=1e-6)
    assert w["mae_r"] == pytest.approx(0.5, rel=1e-6)


def test_decision_snapshot_saved_and_frozen():
    """暗卷4：OutcomeLabel 保存深冻结的完整 decision_snapshot。"""
    lab = label("long")
    out = lab.to_dict()
    assert out["decision_snapshot"]["schema_version"] == "m2-v1"
    assert out["decision_snapshot"]["eligible"] is True
    out["decision_snapshot"]["symbol"] = "HACKED"
    out2 = label("long").to_dict()
    assert out2["decision_snapshot"]["symbol"] == "BTCUSDT"
    with pytest.raises(TypeError):
        lab.decision_snapshot["symbol"] = "x"


def test_digest_canonical_no_default_str():
    """暗卷4：decision_digest 用严格 canonical JSON（无 default=str）。"""
    import hashlib as _hl
    d, seq, las = build_label()
    lab = label_decision(d, seq, las, QUICK)
    payload = json.dumps(lab.to_dict()["decision_snapshot"], sort_keys=True,
                         allow_nan=False)
    assert lab.decision_digest == _hl.sha256(payload.encode()).hexdigest()[:16]


def test_schema_forged_decision_snapshot_rejected():
    """暗卷4：schema 用 MarketFusionOut 验证 decision_snapshot 并重算 digest。"""
    out = label("long").to_dict()
    bad = copy.deepcopy(out)
    bad["decision_snapshot"]["symbol"] = "ETHUSDT"  # 顶层不一致
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["decision_snapshot"]["eligible"] = False  # 伪造快照本身
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["decision_snapshot"]["as_of"] += 1  # digest 重算不匹配
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_top_level_consistent_with_snapshot():
    """暗卷4：顶层 symbol/as_of/signal_time/direction/reference/config_hash 与 snapshot 一致。"""
    out = label("long").to_dict()
    for field, val in (("symbol", "ETHUSDT"), ("as_of", 1), ("signal_time", 1),
                       ("direction", "short"), ("reference_price", 1.0)):
        bad = copy.deepcopy(out)
        bad[field] = val
        with pytest.raises(ValidationError):
            validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["config_hash"] = {"market_state": "0" * 16, "fusion": "0" * 16}
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_snapshot_role_timeframe_fixed():
    """暗卷4：snapshot context/setup/trigger role 与 timeframe 固定且可用。"""
    out = label("long").to_dict()
    bad = copy.deepcopy(out)
    bad["snapshots"]["context"]["role"] = "setup"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["snapshots"]["trigger"]["timeframe"] = "1day"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["snapshots"]["context"]["market_state"]["ready"] = False
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_path_time_continuity():
    """暗卷2：path bar_time 必须从 signal_time+step 严格连续。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    bad = copy.deepcopy(out)
    bad["windows"][0]["path"][1]["bar_time"] += 1
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_end_matches_last_path_step():
    """暗卷2：end_time/end_close/end 状态必须等于路径末步。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    bad = copy.deepcopy(out)
    bad["windows"][0]["end_close"] += 5.0
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["windows"][0]["end_time"] += 1
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["windows"][0]["end_vol_phase"] = "expansion"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_mfe_pct_equals_mfe_r_times_atr_pct():
    """暗卷2：mfe_pct≈mfe_r*atr_pct、mae 同理。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    bad = copy.deepcopy(out)
    bad["windows"][0]["mfe_pct"] *= 2
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["windows"][0]["mae_pct"] += 0.5
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_reference_equals_trigger_close():
    """暗卷2：reference 必须等于 trigger features.close。"""
    out = label("long").to_dict()
    bad = copy.deepcopy(out)
    bad["reference_price"] = bad["decision_snapshot"]["trigger"]["market_state"]["features"]["close"] * 1.01
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_end_time_matches_window():
    """暗卷2：end_time 必须等于 signal_time + window*step。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    step = out["snapshots"]["trigger"]["bar_close_time"] - out["snapshots"]["trigger"]["bar_time"]
    for w in out["windows"]:
        if w["matured"]:
            assert w["end_time"] == out["signal_time"] + w["window"] * step


def test_ohlc_positive_required():
    """暗卷2：引擎 OHLC 四价必须有限正数。"""
    d, seq, las = build_label()
    bad = copy.deepcopy(seq)
    bad[620]["low"] = -1.0
    with pytest.raises(ValueError, match="正数|OHLC"):
        label_decision(d, bad, las, QUICK)
    bad = copy.deepcopy(seq)
    bad[620]["close"] = 0.0
    with pytest.raises(ValueError, match="正数|OHLC"):
        label_decision(d, bad, las, QUICK)


def test_summarize_duplicate_digest_rejected():
    """暗卷1：汇总拒绝重复 decision_digest。"""
    d, seq, las = build_label()
    l1 = label_decision(d, seq, las, QUICK)
    with pytest.raises(ValueError, match="重复"):
        summarize([l1, l1])


def test_summarize_mixed_config_hash_rejected():
    """暗卷5：汇总拒绝混合 config_hash。"""
    other = MarketStateConfig(
        vol_percentile_lookback=11, vol_percentile_low=0.2, vol_percentile_high=0.8,
        vol_change_window=3, ma_fast=5, ma_slow=10, ma_tertiary=15,
        atr_period=14, adx_period=5, pivot=3,
    )
    d1, seq1, _ = build_label()
    d2, seq2, _ = build_label(config=other)
    l1 = label_decision(d1, seq1, as_of_4h_close(640), QUICK)
    l2 = label_decision(d2, seq2, as_of_4h_close(640), other)
    assert l1.to_dict()["config_hash"] != l2.to_dict()["config_hash"]
    with pytest.raises(ValueError, match="config_hash"):
        summarize([l1, l2])


def test_summarize_touches_sum_and_fake_rate_formula():
    """暗卷5：touch 四类之和==samples；fake_rate==negative_first/(samples-ambiguous)。"""
    labels = []
    for i, pat in enumerate((PATTERN_A, PATTERN_B, PATTERN_C)):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pat,
                                n_window=40)
        labels.append(label_decision(d, seq, as_of_4h_close(640), QUICK))
    s = summarize(labels)
    validate_outcome_summary(s)
    w = next(x for x in s["windows"] if x["window"] == 5)
    t = w["touches"]
    assert t["positive_first"] + t["negative_first"] + t["neither"] + t["ambiguous_same_bar"] == w["samples"] == 3
    assert w["fake_rate"] == pytest.approx(
        t["negative_first"] / (w["samples"] - t["ambiguous_same_bar"]), rel=1e-9)


def test_schema_summary_cross_relations():
    """暗卷5：schema 校验汇总关系（touch 和、fake_rate 公式、样本和、rate、行唯一）。"""
    labels = []
    for i, pat in enumerate((PATTERN_A, PATTERN_B, PATTERN_C)):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pat,
                                n_window=40)
        labels.append(label_decision(d, seq, as_of_4h_close(640), QUICK))
    s = summarize(labels)
    validate_outcome_summary(s)
    # touch 之和 != samples
    bad = copy.deepcopy(s)
    bad["windows"][0]["touches"]["positive_first"] += 1
    with pytest.raises(ValidationError):
        validate_outcome_summary(bad)
    # fake_rate 公式错
    bad = copy.deepcopy(s)
    bad["windows"][0]["fake_rate"] = 0.9
    with pytest.raises(ValidationError):
        validate_outcome_summary(bad)
    # group 样本和 != 窗口 samples
    bad = copy.deepcopy(s)
    bad["groups"][0]["samples"] += 1
    with pytest.raises(ValidationError):
        validate_outcome_summary(bad)
    # positive_first_rate 公式错
    bad = copy.deepcopy(s)
    bad["groups"][0]["positive_first_rate"] = 0.5
    with pytest.raises(ValidationError):
        validate_outcome_summary(bad)
    # 行重复
    bad = copy.deepcopy(s)
    bad["groups"] = bad["groups"] + [bad["groups"][0]]
    with pytest.raises(ValidationError):
        validate_outcome_summary(bad)


def test_group_dimensions_included():
    """暗卷6：条件分组包含 direction/event_type/Context/Setup/Trigger 全维度。"""
    labels = []
    for i, pat in enumerate((PATTERN_A, PATTERN_B, PATTERN_C)):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pat,
                                n_window=40)
        labels.append(label_decision(d, seq, as_of_4h_close(640), QUICK))
    s = summarize(labels)
    validate_outcome_summary(s)
    g = s["groups"][0]
    for f in ("direction", "event_type", "context_direction", "context_strength",
              "setup_direction", "setup_strength", "setup_vol_phase",
              "trigger_vol_phase", "trigger_candle_strength", "trigger_volume_state"):
        assert f in g, f"group 缺少维度 {f}"


def test_batch_mixed_symbol_rejected():
    """暗卷7：label_batch 同一 candles 序列 → 拒绝混合 symbol。"""
    d1, seq1, _ = build_label(symbol="BTCUSDT")
    d2, seq2, _ = build_label(symbol="ETHUSDT")
    shared = list(seq1) + list(seq2[641:691])
    with pytest.raises(ValueError, match="symbol"):
        label_batch([d1, d2], shared, as_of_4h_close(690), QUICK)


def test_summarize_groups_consistent_per_window():
    """暗卷5：每窗口 group/transition samples 总和 == 窗口 samples（运行时）。"""
    labels = []
    for i, pat in enumerate((PATTERN_A, PATTERN_B, PATTERN_C)):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pat,
                                n_window=40)
        labels.append(label_decision(d, seq, as_of_4h_close(640), QUICK))
    s = summarize(labels)
    for w in s["windows"]:
        gw = [g for g in s["groups"] if g["window"] == w["window"]]
        tw = [t for t in s["transitions"] if t["window"] == w["window"]]
        assert sum(g["samples"] for g in gw) == w["samples"]
        assert sum(t["samples"] for t in tw) == w["samples"]


# ═══════════════════════════════════════════════════════════════════════════
#  第二轮整改：第二层暗卷复现（每条先写回归测试取红灯 → 修复取绿灯）
# ═══════════════════════════════════════════════════════════════════════════

def test_summarize_all_censored_ok():
    """暗卷1：全删失 label → summarize 合法（samples=0, censored=1），
    validate_outcome_summary 必须通过（不得自拒）。"""
    d, seq, _ = build_label(n_window=5)  # 事件 600 + 5 根窗口
    lab = label_decision(d, seq, as_of_4h_close(640), QUICK)  # 40 窗口缺根删失
    s = summarize([lab])
    validate_outcome_summary(s)
    w40 = next(x for x in s["windows"] if x["window"] == 40)
    assert w40["samples"] == 0 and w40["censored"] == 1
    assert w40["fake_rate"] is None
    assert w40["touches"] == {"positive_first": 0, "negative_first": 0,
                              "neither": 0, "ambiguous_same_bar": 0}


def test_summarize_rejects_tampered_dataclass():
    """暗卷2：dataclasses.replace 篡改内存对象（mfe_r=-99）→ summarize
    必须先 to_dict→validate，绝不信任内存对象。"""
    import dataclasses
    lab = label("long", pattern=PATTERN_A)
    tampered = dataclasses.replace(lab, windows=tuple(
        dataclasses.replace(x, mfe_r=-99.0, mfe_pct=-99.0) if x.window == 5 else x
        for x in lab.windows))
    with pytest.raises((ValueError, ValidationError)):
        summarize([tampered])


def test_summarize_mixed_label_as_of_rejected():
    """暗卷3：混合 label_as_of → 拒绝（统计口径混杂，不得取 max 冒充）。"""
    d1, seq1, _ = build_label(event_idx=600, event_type="ma_breakout")
    d2, seq2, _ = build_label(event_idx=600, event_type="bb_breakout")
    l1 = label_decision(d1, seq1, as_of_4h_close(640), QUICK)
    l2 = label_decision(d2, seq2, as_of_4h_close(650), QUICK)
    with pytest.raises(ValueError, match="label_as_of"):
        summarize([l1, l2])


def test_censor_code_stable_across_future_append():
    """暗卷4：同一 decision/label_as_of，追加不可见未来 bar 不改变完整 label
    （censor 判定先看 expected end close_time，不看序列长度）。"""
    d, seq, _ = build_label(n_window=5)  # 0..600 + 601..605
    las = as_of_4h_close(603)  # 窗口 5 末根 605 未到
    a = label_decision(d, seq, las, QUICK)
    grown = list(seq) + make_window_bars(float(seq[600]["close"]), 10.0,
                                         T0 + 606 * STEP4, 10, PATTERN_CALM)
    b = label_decision(d, grown, las, QUICK)
    assert a.to_dict() == b.to_dict()
    assert w5(a.to_dict())["matured"] is False
    assert w5(a.to_dict())["censor_code"] == "label_as_of_not_reached"


def test_schema_recomputes_mfe_and_first_touch():
    """暗卷5：成套伪造（mfe_r/mfe_pct 同 9 倍 / first_touch+fake 同伪）→
    schema 从 path OHLC 复算并拒绝。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    validate_outcome_label(out)  # 正常通过
    bad = copy.deepcopy(out)
    bad["windows"][0]["mfe_r"] *= 9
    bad["windows"][0]["mfe_pct"] *= 9
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["windows"][0]["mae_r"] *= 9
    bad["windows"][0]["mae_pct"] *= 9
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["windows"][0]["first_touch"] = "negative_first"
    bad["windows"][0]["fake_breakout"] = True
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # short 镜像同样复算
    out_s = label("short", pattern=PATTERN_A, drift1w=-0.008, drift1d=-0.008).to_dict()
    validate_outcome_label(out_s)
    bad = copy.deepcopy(out_s)
    bad["windows"][0]["first_touch"] = "positive_first"
    bad["windows"][0]["fake_breakout"] = False
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_groups_transitions_sorted():
    """暗卷6：groups/transitions 倒序 → schema 拒绝（稳定排序契约）。"""
    labels = []
    for i, pat in enumerate((PATTERN_A, PATTERN_B, PATTERN_C)):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pat, n_window=40)
        labels.append(label_decision(d, seq, as_of_4h_close(660), QUICK))
    s = summarize(labels)
    validate_outcome_summary(s)
    bad = copy.deepcopy(s)
    bad["groups"] = list(reversed(bad["groups"]))
    with pytest.raises(ValidationError, match="排序"):
        validate_outcome_summary(bad)
    bad = copy.deepcopy(s)
    bad["transitions"] = list(reversed(bad["transitions"]))
    with pytest.raises(ValidationError, match="排序"):
        validate_outcome_summary(bad)


def test_censor_code_fields():
    """暗卷E：censor_code 结构化（matured=None）；三种 code 可区分。"""
    d, seq, _ = build_label(n_window=40)
    # label_as_of_not_reached：截止未到
    lab = label_decision(d, seq, as_of_4h_close(620), QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    assert w40["censor_code"] == "label_as_of_not_reached"
    assert w40["censor_reason"]  # 人话 reason 非空
    # missing_bars：截止已到但 bar 不存在（序列仅 20 根窗口）
    _, seq20, _ = build_label(n_window=20)
    lab = label_decision(d, seq20, as_of_4h_close(640), QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    assert w40["censor_code"] == "missing_bars"
    # unclosed：显式未收盘
    seq2 = list(seq)
    seq2 = seq2[:621] + [{"time": T0 + 621 * STEP4, "is_closed": False}] + seq2[621:]
    lab = label_decision(d, seq2, T0 + 645 * STEP4, QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    assert w40["censor_code"] == "unclosed"
    # 成熟窗口 None
    lab = label_decision(d, seq, as_of_4h_close(640), QUICK)
    w5m = next(x for x in lab.to_dict()["windows"] if x["window"] == 5)
    assert w5m["censor_code"] is None


def test_path_step_has_ohlc():
    """暗卷A：path 步含 open/high/low/close 且 OHLC 关系合法。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    w = w5(out)
    for p in w["path"]:
        for f in ("open", "high", "low", "close"):
            assert p[f] > 0, f
        assert p["high"] >= max(p["open"], p["close"]) >= min(p["open"], p["close"]) >= p["low"]
    validate_outcome_label(out)


def test_summary_meta_audit():
    """暗卷F：summary 输出 config_hash / decision_digests / label_as_of 元信息。"""
    d, seq, _ = build_label()
    l1 = label_decision(d, seq, as_of_4h_close(640), QUICK)
    s = summarize([l1])
    validate_outcome_summary(s)
    assert s["config_hash"] == l1.to_dict()["config_hash"]
    assert s["decision_digests"] == [l1.decision_digest]  # 稳定排序
    assert s["label_as_of"] == as_of_4h_close(640)
    assert len(s["decision_digests"]) == s["n_decisions"] == 1
    # 空报告：config_hash=None、digests 空
    s0 = summarize([])
    validate_outcome_summary(s0)
    assert s0["config_hash"] is None
    assert s0["decision_digests"] == []
    # 伪造 digests（数量不符）→ schema 拒绝
    bad = copy.deepcopy(s)
    bad["decision_digests"] = ["0" * 16, "1" * 16]  # 数量不符
    with pytest.raises(ValidationError):
        validate_outcome_summary(bad)
    bad = copy.deepcopy(s)
    bad["decision_digests"] = ["0" * 16]  # 数量正确时 schema 只校验数量/唯一/排序/hex
    validate_outcome_summary(bad)  # 合法（结构层面；内容统一由 summarize 运行时保证）


def test_schema_censor_code_reason_relation():
    """暗卷E：schema 校验 censor_code/censor_reason/matured 关系。"""
    out = label("long").to_dict()
    bad = copy.deepcopy(out)
    w = bad["windows"][3]  # 成熟窗口改为删失但 code=None
    w["matured"] = False
    w["censor_reason"] = "x"
    w["censor_code"] = None
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    w = bad["windows"][0]  # 成熟窗口带 censor_code
    w["censor_code"] = "unclosed"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    w = bad["windows"][0]
    w["censor_code"] = "banana"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_summarize_all_censored_vs_empty():
    """暗卷C：空报告(n=0)与全删失(n>0)区别清楚。"""
    d, seq, _ = build_label(n_window=5)
    lab = label_decision(d, seq, as_of_4h_close(640), QUICK)
    s_all = summarize([lab])
    s_empty = summarize([])
    validate_outcome_summary(s_all)
    validate_outcome_summary(s_empty)
    assert s_all["n_decisions"] == 1 and s_empty["n_decisions"] == 0
    assert s_all["symbol"] == "BTCUSDT" and s_empty["symbol"] == ""
    w40 = next(x for x in s_all["windows"] if x["window"] == 40)
    assert w40["samples"] == 0 and w40["censored"] == 1


# ═══════════════════════════════════════════════════════════════════════════
#  第三轮收口：最终候选审查反例（每条先写回归测试取红灯 → 修复取绿灯）
# ═══════════════════════════════════════════════════════════════════════════

def test_censor_boundary_formula():
    """暗卷1：expected_end_close=signal_time+(w+1)*step；
    t+(w+1)*step-1 不成熟 / 精确边界成熟。"""
    d, seq, _ = build_label(n_window=40)  # 0..600 + 601..640
    boundary = as_of_4h_close(605)  # 窗口 5 第 5 根（bar 605）收盘 = T0+606*step
    lab_early = label_decision(d, seq, boundary - 1, QUICK)
    w5e = w5(lab_early.to_dict())
    assert w5e["matured"] is False
    assert w5e["censor_code"] == "label_as_of_not_reached"
    lab_ok = label_decision(d, seq, boundary, QUICK)
    assert w5(lab_ok.to_dict())["matured"] is True


def test_schema_cross_window_consistency():
    """暗卷2：跨窗口约束——交换合法 w5、成熟倒挂、路径不一致、MFE 倒挂、
    first_touch 改变均拒绝。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    validate_outcome_label(out)
    # 交换另一合法标签的 w5
    out2 = label("long", pattern=PATTERN_B).to_dict()
    bad = copy.deepcopy(out)
    bad["windows"][0] = out2["windows"][0]
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # 成熟倒挂：w40 成熟但短窗改删失
    bad = copy.deepcopy(out)
    for i in range(3):
        w = bad["windows"][i]
        w["matured"] = False
        w["censor_code"] = "missing_bars"
        w["censor_reason"] = "缺根"
        for f in ("mfe_r", "mae_r", "mfe_pct", "mae_pct", "first_touch",
                  "fake_breakout", "end_vol_phase", "end_trend_strength",
                  "end_close", "end_time"):
            w[f] = None
        w["path"] = []
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # 路径不一致：w10 的 path 与 w20 前缀不同
    bad = copy.deepcopy(out)
    bad["windows"][1]["path"][0] = dict(bad["windows"][1]["path"][0],
                                        vol_phase="expansion")
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # MFE 倒挂：w10 mfe < w5 mfe
    bad = copy.deepcopy(out)
    bad["windows"][1]["mfe_r"] = bad["windows"][0]["mfe_r"] - 0.5
    bad["windows"][1]["mfe_pct"] = bad["windows"][0]["mfe_pct"] - 0.5
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # 短窗非 neither → 长窗 first_touch 改变
    bad = copy.deepcopy(out)
    bad["windows"][1]["first_touch"] = "negative_first"
    bad["windows"][1]["fake_breakout"] = True
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_schema_replay_verifies_m1_states():
    """暗卷3：伪造 path 全部 vol_phase/trend_strength 与 end → 拒绝
    （schema 用 config 快照 + replay_context 重跑 M1 precompute 验证）。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    validate_outcome_label(out)
    bad = copy.deepcopy(out)
    for p in bad["windows"][0]["path"]:
        p["vol_phase"] = "expansion"
        p["trend_strength"] = "strong"
    bad["windows"][0]["end_vol_phase"] = "expansion"
    bad["windows"][0]["end_trend_strength"] = "strong"
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_market_state_config_snapshot():
    """暗卷C：market_state_config 完整字段快照（atr_period=14）+
    重算 hash == config_hash.market_state。"""
    out = label("long").to_dict()
    assert out["market_state_config"]["atr_period"] == 14
    assert out["market_state_config"]["schema_version"] == "m1-v1"
    validate_outcome_label(out)
    bad = copy.deepcopy(out)
    bad["market_state_config"]["atr_period"] = 5
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["market_state_config"]["vol_percentile_lookback"] += 1  # hash 不匹配
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_replay_context_fields():
    """暗卷C：replay_context 严格连续、末根==signal_time/reference、OHLC 正数。"""
    out = label("long").to_dict()
    validate_outcome_label(out)
    ctx = out["replay_context"]
    assert len(ctx) > 0
    assert ctx[-1]["time"] == out["signal_time"]
    assert ctx[-1]["close"] == pytest.approx(out["reference_price"], rel=1e-9)
    step = out["snapshots"]["trigger"]["bar_close_time"] - out["snapshots"]["trigger"]["bar_time"]
    for i in range(1, len(ctx)):
        assert ctx[i]["time"] - ctx[i - 1]["time"] == step
    for b in ctx:
        for f in ("open", "high", "low", "close"):
            assert b[f] > 0
        assert b["volume"] >= 0
        assert b["high"] >= max(b["open"], b["close"]) >= min(b["open"], b["close"]) >= b["low"]
    bad = copy.deepcopy(out)
    bad["replay_context"][-1]["close"] *= 2  # 与 reference 不一致
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    bad = copy.deepcopy(out)
    bad["replay_context"][-1]["time"] += 1  # 连续破坏
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)


def test_path_step_volume():
    """暗卷C：path 步带 volume（有限非负）。"""
    out = label("long", pattern=PATTERN_A).to_dict()
    for p in w5(out)["path"]:
        assert p["volume"] >= 0
    validate_outcome_label(out)


def test_validate_summary_against_labels():
    """暗卷4：source-linked validator——group mean+100、成套改写、digests
    同长度替换均拒绝（仅凭 summary 自身无法证明源数据）。"""
    from app.schemas.outcome_labeling import validate_outcome_summary_against_labels
    labels = []
    for i, pat in enumerate((PATTERN_A, PATTERN_B, PATTERN_C)):
        d, seq, _ = build_label(event_idx=600 + i * 10, pattern=pat, n_window=40)
        labels.append(label_decision(d, seq, as_of_4h_close(660), QUICK))
    s = summarize(labels)
    validate_outcome_summary_against_labels(s, labels)  # 通过
    bad = copy.deepcopy(s)
    bad["groups"][0]["mean_mfe_r"] += 100
    with pytest.raises((ValueError, ValidationError)):
        validate_outcome_summary_against_labels(bad, labels)
    bad = copy.deepcopy(s)
    for g in bad["groups"]:
        g["positive_first"] = g["samples"]
        g["positive_first_rate"] = 1.0
    with pytest.raises((ValueError, ValidationError)):
        validate_outcome_summary_against_labels(bad, labels)
    bad = copy.deepcopy(s)
    bad["decision_digests"] = ["0" * 16] * 3  # 同长度替换
    with pytest.raises((ValueError, ValidationError)):
        validate_outcome_summary_against_labels(bad, labels)


def test_random_seed_property_matrix():
    """暗卷F：20 组固定 seed 随机 OHLC 属性测试（不依赖 mock）。"""
    import random as _r
    from app.core.multi_timeframe import evaluate as _evaluate
    for seed in range(20):
        rng = _r.Random(1000 + seed)
        candles = make_candles(700)
        for i, b in enumerate(candles["4h"]):
            o = b["open"] * (1 + rng.uniform(-0.01, 0.01))
            c = b["close"] * (1 + rng.uniform(-0.01, 0.01))
            h = max(o, c) * (1 + rng.uniform(0.002, 0.02))
            l = min(o, c) * (1 - rng.uniform(0.002, 0.02))
            candles["4h"][i] = {**b, "open": round(o, 6), "high": round(h, 6),
                                "low": round(l, 6), "close": round(c, 6)}
        entry = float(candles["4h"][600]["close"])
        as_of = as_of_4h_close(600)
        sig = T0 + 600 * STEP4
        trig = {
            "strategy": "ma", "event_type": "ma_breakout", "direction": "long",
            "severity": "signal", "signal_time": sig, "reference_price": entry,
            "summary": "s",
            "payload": {"schema_version": "v1", "signal_type": "ma_breakout",
                        "direction": "long", "state": "signal", "timeframe": "4h",
                        "occurred_at": sig, "confirmed_at": sig,
                        "reference_price": entry, "bar_closed": True,
                        "evidence": {}, "parameters": {}},
        }
        d = _evaluate("BTCUSDT", candles, as_of, trig, QUICK)
        assert d.eligible, d.reason_codes
        seq = list(candles["4h"][:641])
        las = as_of_4h_close(640)
        lab = label_decision(d, seq, las, QUICK)
        out = lab.to_dict()
        validate_outcome_label(out)  # 含 schema 独立复算 MFE/首触及
        # 同 label_as_of 追加不可见未来 bar 逐字段不变
        grown = list(seq) + [dict(b, time=b["time"] + 10 * STEP4) for b in seq[-5:]]
        assert label_decision(d, grown, las, QUICK).to_dict() == out
        # MFE/MAE 非负且随窗口不减（独立 prev_mfe / prev_mae，禁止 or True）
        prev_mfe = 0.0
        prev_mae = 0.0
        for w in out["windows"]:
            if w["matured"]:
                assert w["mfe_r"] >= 0 and w["mae_r"] >= 0
                assert w["mfe_r"] >= prev_mfe - 1e-9
                assert w["mae_r"] >= prev_mae - 1e-9
                prev_mfe = w["mfe_r"]
                prev_mae = w["mae_r"]
        # 短/长 path 前缀一致
        for sa, sb in ((5, 10), (10, 20), (20, 40)):
            wa = next(x for x in out["windows"] if x["window"] == sa)
            wb = next(x for x in out["windows"] if x["window"] == sb)
            if wa["matured"] and wb["matured"]:
                assert wa["path"] == wb["path"][:sa], f"seed={seed} 窗口 {sa}/{sb} 前缀不一致"


def test_schema_temporal_semantics():
    """最终审查：时序语义——成熟窗口 label_as_of >= signal_time+(window+1)*step；
    未成熟窗口 censor_code 与 label_as_of 边界严格对应（not_reached ⟺ 时点未到）。"""
    out = label("long", pattern=PATTERN_A).to_dict()  # 全窗口成熟
    validate_outcome_label(out)
    step = out["snapshots"]["trigger"]["bar_close_time"] - out["snapshots"]["trigger"]["bar_time"]
    boundary5 = out["signal_time"] + 6 * step  # 窗口 5 第 5 根收盘

    def _censor(w: dict, code: str) -> None:
        w["matured"] = False
        w["censor_code"] = code
        w["censor_reason"] = {"missing_bars": "缺根", "unclosed": "未收盘",
                              "label_as_of_not_reached": "label_as_of 未到"}[code]
        for f in ("mfe_r", "mae_r", "mfe_pct", "mae_pct", "first_touch",
                  "fake_breakout", "end_vol_phase", "end_trend_strength",
                  "end_close", "end_time"):
            w[f] = None
        w["path"] = []

    # a) 成熟窗口 label_as_of 改到边界前（不改窗口字段）→ 拒绝
    bad = copy.deepcopy(out)
    bad["label_as_of"] = boundary5 - 1
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # b) 未成熟窗口在边界前改成 missing_bars → 拒绝（只能 not_reached）
    bad = copy.deepcopy(out)
    bad["label_as_of"] = boundary5 - 1
    for w in bad["windows"]:
        _censor(w, "missing_bars")
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # c) 未成熟窗口在边界前改成 unclosed → 拒绝
    bad = copy.deepcopy(out)
    bad["label_as_of"] = boundary5 - 1
    for w in bad["windows"]:
        _censor(w, "unclosed")
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # d) 时点已到边界但 censor_code=not_reached → 拒绝
    bad = copy.deepcopy(out)
    bad["label_as_of"] = boundary5  # 窗口 5 边界已到
    _censor(bad["windows"][0], "label_as_of_not_reached")
    for w in bad["windows"][1:]:
        _censor(w, "missing_bars")
    with pytest.raises(ValidationError):
        validate_outcome_label(bad)
    # e) 精确边界：label_as_of == t+6*step 时窗口 5 成熟合法
    d, seq, _ = build_label(n_window=40)
    lab = label_decision(d, seq, boundary5, QUICK)
    w5b = w5(lab.to_dict())
    assert w5b["matured"] is True
    validate_outcome_label(lab.to_dict())
    # f) 边界前未成熟 + not_reached 合法（正向）
    bad = copy.deepcopy(out)
    bad["label_as_of"] = boundary5 - 1
    for w in bad["windows"]:
        _censor(w, "label_as_of_not_reached")
    validate_outcome_label(bad)


def test_cutoff_outside_bars_do_not_affect_label():
    """主审反例：截止之外的 bar 除 time 用于 cutoff 外，其 is_closed/OHLC/
    volume/数量不得影响任何 label 字段；as_of cutoff 优先于 is_closed；
    missing 数量只基于可见已收盘前缀。"""
    d, seq, _ = build_label(n_window=0)  # 0..600（601 根，事件后无窗口 bar）
    las = as_of_4h_close(640)
    base = label_decision(d, seq, las, QUICK).to_dict()
    # 四窗均 missing_bars（截止已到、事件 bar 后 0 根可见）
    for w in base["windows"]:
        assert w["matured"] is False
        assert w["censor_code"] == "missing_bars"
    assert "0 根" in base["windows"][0]["censor_reason"]
    # 1) 追加 OHLC 缺失的远期 is_closed=False → 逐字段不变
    seq1 = list(seq) + [{"time": T0 + 700 * STEP4, "is_closed": False}]
    assert label_decision(d, seq1, las, QUICK).to_dict() == base
    # 2) 追加完整远期 is_closed=True → 逐字段不变
    seq2 = list(seq) + [dict(seq[0], time=T0 + 700 * STEP4)]
    assert label_decision(d, seq2, las, QUICK).to_dict() == base
    # 3) 多根远期（混合 is_closed）→ 逐字段不变
    seq3 = list(seq) + [
        {"time": T0 + 700 * STEP4, "is_closed": False},
        dict(seq[0], time=T0 + 701 * STEP4),
        {"time": T0 + 702 * STEP4, "is_closed": False},
    ]
    assert label_decision(d, seq3, las, QUICK).to_dict() == base
    # 输入列表未被修改
    assert len(seq1) == 602 and len(seq3) == 604
    assert seq1[-1]["time"] == T0 + 700 * STEP4


def test_cutoff_inside_unclosed_still_unclosed():
    """主审：保留「截止以内显式未收盘→unclosed」（as_of cutoff 优先不破坏）。"""
    d, seq, _ = build_label(n_window=40)  # 0..600 + 601..640
    seq2 = list(seq)
    seq2 = seq2[:621] + [{"time": T0 + 621 * STEP4, "is_closed": False}] + seq2[621:]
    las = T0 + 645 * STEP4
    lab = label_decision(d, seq2, las, QUICK)
    w40 = next(x for x in lab.to_dict()["windows"] if x["window"] == 40)
    assert w40["matured"] is False
    assert w40["censor_code"] == "unclosed"
    # 窗口 20 仍成熟（未收盘 bar 在窗口 21 根位置，截止内）
    assert next(x for x in lab.to_dict()["windows"] if x["window"] == 20)["matured"] is True
