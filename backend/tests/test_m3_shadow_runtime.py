"""M3B 安全影子运行时测试（任务 3：证明与文档）。

覆盖（任务书要求 ≥35 项，本文件 46 项）：
  1. flag/allowlist：默认 disabled、allowlist 解析/大小写归一、非名单不摄取；
  2. heartbeat：首次 enabled cycle 原子创建、started_at 固定、last_run 更新；
  3. 摄取边界：created_at>=started_at、4H 过滤、同源已处理跳过、as_of 口径；
  4. 决策持久化：canonical JSON + 16 位 digest、严格 validator、拒绝也持久化、
     只读 source 快照；
  5. eligible 标签分期：5/10/20/40 边界到期更新、40 完成停止、到期前不生成；
  6. 幂等/重试/崩溃恢复：重跑不重复计数、label 失败退避重试、completed 不改写、
     同 bar 多事件唯一 digest 计数、attempts 上限停试；
  7. 同周期取数复用：同 (symbol, signal_time) 不重复拉行情；
  8. 未来追加不变：decision digest / 成熟 label / rejected 均不被未来 bar 改写；
  9. 错误脱敏：webhook/token 打码、截断；
  10. V1 隔离与无发送：run_cycle_safe 不冒泡、heartbeat 记录错误、
      模块不导入任何 sender；
  11. CLI 汇总：ShadowStatusOut 不含 payload、进度计算诚实（不伪造门槛）；
  12. 反向红→绿三组证据（坏实现红 → 好实现绿，见文档）：
      a. 影子异常若冒泡会打断 V1（run_cycle 红 vs run_cycle_safe 绿）；
      b. 同源重复若产生两行（无唯一键 repo 红 vs 唯一键绿）；
      c. 未来 bar 若改写决策（直接重算红 vs 快照 digest 不变绿）。

不得 mock M1/M2/M3A 被测核心：evaluate / label_decision / validate_decision /
validate_outcome_label 全部真实调用；只注入确定性行情 loader / 时钟（now）/
内存 repo。
"""
from __future__ import annotations

import asyncio
import json
import math
import re

import pytest

from app.core.config import Settings
from app.core.m3_shadow_runtime import (
    DEFAULT_MAX_ATTEMPTS,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_LABELED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Heartbeat,
    MemoryShadowRepo,
    ShadowCore,
    ShadowCounts,
    SourceEvent,
    compute_decision_record,
    compute_label_record,
    compute_progress,
    decision_digest as _digest,
    run_cycle,
    run_cycle_safe,
)
from app.core.market_state import MarketStateConfig
from app.core.multi_timeframe import TIMEFRAME_DURATIONS, evaluate
from app.core.outcome_labeling import label_decision
from app.schemas.m3_shadow_runtime import (
    ShadowStatusOut,
    canonical_json,
    sanitize_error,
)
from app.schemas.market_fusion import validate_decision
from app.schemas.outcome_labeling import validate_outcome_label

# ═══════════════════════════════════════════════════════════════════════════════
#  确定性构造
# ═══════════════════════════════════════════════════════════════════════════════

T0 = 1_700_000_000
STEP4 = TIMEFRAME_DURATIONS["4h"]
STEP1D = TIMEFRAME_DURATIONS["1day"]
STEP1W = TIMEFRAME_DURATIONS["1week"]
EVENT_IDX = 620
SIG = T0 + EVENT_IDX * STEP4
AS_OF = SIG + STEP4  # = signal_time + 14400
W5 = SIG + 6 * STEP4   # 5 窗口边界
W10 = SIG + 11 * STEP4
W20 = SIG + 21 * STEP4
W40 = SIG + 41 * STEP4


def make_bars(n: int, step: int, *, drift: float = 0.008, wave: float = 0.02,
              start_off: int = 0) -> list[dict]:
    out = []
    for i in range(n):
        close = 100.0 * (1 + drift * i) * (1 + wave * math.sin(i / 9.0))
        op = 100.0 * (1 + drift * max(0, i - 1)) * (1 + wave * math.sin((i - 1) / 9.0))
        out.append({
            "time": T0 + (i - start_off) * step,
            "open": round(op, 6),
            "high": round(max(op, close) * 1.012, 6),
            "low": round(min(op, close) * 0.988, 6),
            "close": round(close, 6),
            "volume": 1000 + (i * 37) % 11,
        })
    return out


def make_candles(n4: int = 700) -> dict[str, list[dict]]:
    """4H 序列从 T0 起（事件 bar 前有 620 根历史）；日/周从事件前 700 根起，
    保证事件 as_of 处 M1 DEFAULT 配置（vol lookback=100）warmup 就绪。"""
    return {
        "4h": make_bars(n4, STEP4),
        "1day": make_bars(800, STEP1D, start_off=700),
        "1week": make_bars(800, STEP1W, start_off=700),
    }


_BARS = make_candles()


def _payload(event_idx: int = EVENT_IDX, direction: str = "long",
             event_type: str = "ma_breakout") -> dict:
    return {
        "schema_version": 1, "signal_type": event_type, "direction": direction,
        "state": "signal", "timeframe": "4h", "occurred_at": SIG, "confirmed_at": SIG,
        "reference_price": _BARS["4h"][event_idx]["close"], "bar_closed": True,
        "evidence": {}, "parameters": {},
    }


def make_source(event_idx: int = EVENT_IDX, *, dedupe_key: str = "k1",
                symbol: str = "BINANCE:BTCUSDT", timeframe: str = "4h",
                direction: str = "long", event_type: str = "ma_breakout",
                strategy: str = "ma", created_at: int | None = None,
                reference_price: float | None = None,
                signal_time: int | None = None) -> SourceEvent:
    sig = signal_time if signal_time is not None else T0 + event_idx * STEP4
    ref = reference_price if reference_price is not None else _BARS["4h"][event_idx]["close"]
    return SourceEvent(
        dedupe_key=dedupe_key, id=abs(hash(dedupe_key)) % 10_000_000 + 1,
        symbol=symbol, timeframe=timeframe, strategy=strategy,
        event_type=event_type, direction=direction, severity="signal",
        signal_time=sig, reference_price=ref,
        payload=_payload(event_idx, direction, event_type),
        created_at_epoch=created_at if created_at is not None else 0,  # 0=启用后（run_cycle_once 填 now）
    )


def make_loader(bars_map: dict[str, list[dict]] | None = None, *,
                fail_label: bool = False) -> tuple[object, list[str]]:
    """确定性 async loader：按 close_time<=end_ts 过滤并截断；统计调用 key。"""
    bars = bars_map or _BARS
    calls: list[str] = []

    async def loader(symbol: str, timeframe: str, end_ts: int, max_bars: int) -> list[dict]:
        calls.append(f"{symbol}|{timeframe}|{end_ts}")
        if fail_label and timeframe == "4h" and end_ts > AS_OF:
            raise RuntimeError("行情源不可用（label 取数失败注入）")
        dur = TIMEFRAME_DURATIONS[timeframe]
        return [b for b in bars[timeframe] if b["time"] + dur <= end_ts][-max_bars:]

    return loader, calls


def run_cycle_once(core: ShadowCore, sources: list[SourceEvent], now: int,
                   *, loader=None, repo=None):
    """同步测试入口：asyncio.run 包一层；created_at=0 的事件视为启用后创建。"""
    if repo is None:
        repo = MemoryShadowRepo(core=core)
    seeds = []
    for s in sources:
        if s.created_at_epoch == 0:
            s = SourceEvent(
                dedupe_key=s.dedupe_key, id=s.id, symbol=s.symbol,
                timeframe=s.timeframe, strategy=s.strategy, event_type=s.event_type,
                direction=s.direction, severity=s.severity, signal_time=s.signal_time,
                reference_price=s.reference_price, payload=s.payload,
                created_at_epoch=now,
            )
        seeds.append(s)
    repo.seed_source(seeds)
    if loader is None:
        loader, _calls = make_loader()
    return asyncio.run(run_cycle(core, repo, loader, now)), repo


def core(symbols: frozenset[str] = frozenset({"BINANCE:BTCUSDT"}),
         **kw) -> ShadowCore:
    return ShadowCore(enabled=True, symbols=symbols, **kw)


# ═══════════════════════════════════════════════════════════════════════════════
#  1. flag / allowlist
# ═══════════════════════════════════════════════════════════════════════════════

def test_core_from_settings_default_disabled():
    cfg = Settings(M3_SHADOW_ENABLED=False, M3_SHADOW_SYMBOLS="")
    c = ShadowCore.from_settings(cfg)
    assert c.enabled is False
    assert c.symbols == frozenset()


def test_core_from_settings_enabled_with_symbols():
    cfg = Settings(M3_SHADOW_ENABLED=True, M3_SHADOW_SYMBOLS=" BINANCE:BTCUSDT, binance:ethusdt ")
    c = ShadowCore.from_settings(cfg)
    assert c.enabled is True
    assert c.symbols == frozenset({"BINANCE:BTCUSDT", "BINANCE:ETHUSDT"})  # 大小写归一
    assert c.max_bars == 700 and c.timeframe == "4h"


def test_allowlist_filters_out_non_whitelist_symbol():
    src = make_source(dedupe_key="tsla", symbol="TSLA")
    result, repo = run_cycle_once(core(), [src], W5 + 60)
    assert result["ingested"] == 0
    assert (asyncio.run(repo.counts())).total == 0


def test_allowlist_matches_uppercase():
    src = make_source(dedupe_key="k-lower", symbol="binance:btcusdt")
    result, repo = run_cycle_once(core(), [src], W5 + 60)
    assert result["ingested"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
#  2. heartbeat
# ═══════════════════════════════════════════════════════════════════════════════

def test_heartbeat_started_at_fixed_on_first_cycle():
    repo = MemoryShadowRepo(core=core())
    hb1 = asyncio.run(repo.ensure_heartbeat(now=1000))
    hb2 = asyncio.run(repo.ensure_heartbeat(now=9999))  # 并发/重跑
    assert hb1.started_at == 1000
    assert hb2.started_at == 1000  # 只首次设置
    got = asyncio.run(repo.get_heartbeat())
    assert got.started_at == 1000


def test_heartbeat_touch_updates_last_run():
    repo = MemoryShadowRepo(core=core())
    asyncio.run(repo.ensure_heartbeat(now=1000))
    asyncio.run(repo.touch_heartbeat(now=2000, ok=True, error=None))
    hb = asyncio.run(repo.get_heartbeat())
    assert hb.last_run_at == 2000 and hb.last_run_ok is True
    asyncio.run(repo.touch_heartbeat(now=3000, ok=False, error="boom"))
    hb = asyncio.run(repo.get_heartbeat())
    assert hb.last_run_at == 3000 and hb.last_run_ok is False and hb.last_error == "boom"


def test_heartbeat_started_at_is_first_enabled_cycle():
    """started_at 不得早于启用时刻（now 注入 = 启用时刻）。"""
    result, repo = run_cycle_once(core(), [make_source()], now=W5 + 60)
    hb = asyncio.run(repo.get_heartbeat())
    assert hb.started_at == W5 + 60
    assert hb.started_at >= W5 + 60


# ═══════════════════════════════════════════════════════════════════════════════
#  3. 摄取边界
# ═══════════════════════════════════════════════════════════════════════════════

def test_ingest_only_created_at_after_started_at():
    early = make_source(dedupe_key="early", created_at=W5 - 10_000)  # 启用前
    late = make_source(dedupe_key="late", created_at=W5 + 60)  # 显式启用后
    result, repo = run_cycle_once(core(), [early, late], now=W5 + 60)
    assert result["ingested"] == 1
    assert asyncio.run(repo.get_by_key("early")) is None
    assert asyncio.run(repo.get_by_key("late")) is not None


def test_ingest_only_4h_timeframe():
    d1 = make_source(dedupe_key="d1", timeframe="1day")
    h1 = make_source(dedupe_key="h1", timeframe="1h")
    result, repo = run_cycle_once(core(), [d1, h1], W5 + 60)
    assert result["ingested"] == 0


def test_existing_source_skipped_on_rerun():
    src = make_source()
    result1, repo = run_cycle_once(core(), [src], W5 + 60)
    assert result1["ingested"] == 1
    # 重跑（崩溃后重启）：同源不重复计数
    result2, _ = run_cycle_once(core(), [src], W5 + 60, repo=repo)
    assert result2["ingested"] == 0
    counts = asyncio.run(repo.counts())
    assert counts.total == 1


def test_as_of_is_signal_time_plus_14400():
    loader, calls = make_loader()
    result, repo = run_cycle_once(core(), [make_source()], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    decision = json.loads(rec.decision_json)
    assert decision["as_of"] == SIG + STEP4
    # 取数以 as_of 为截止
    assert any(c.endswith(f"|{AS_OF}") for c in calls)


def test_same_cycle_reuses_market_data():
    """同 symbol+signal_time 多事件：决策行情只拉一次（3 周期），不翻倍。"""
    srcs = [
        make_source(dedupe_key="k-a", strategy="ma", event_type="ma_breakout"),
        make_source(dedupe_key="k-b", strategy="macd", event_type="macd_divergence"),
        make_source(dedupe_key="k-c", strategy="structure", event_type="structure_break"),
    ]
    loader, calls = make_loader()
    result, repo = run_cycle_once(core(), srcs, W5 + 60, loader=loader)
    assert result["ingested"] == 3
    decision_calls = [c for c in calls if c.endswith(f"|{AS_OF}")]
    # 三事件同 as_of：决策取数 3 次（1week/1day/4h 各一），label 另计
    assert decision_calls == [
        "BINANCE:BTCUSDT|1week|%d" % AS_OF,
        "BINANCE:BTCUSDT|1day|%d" % AS_OF,
        "BINANCE:BTCUSDT|4h|%d" % AS_OF,
    ]
    assert len(decision_calls) == 3


# ═══════════════════════════════════════════════════════════════════════════════
#  4. 决策持久化
# ═══════════════════════════════════════════════════════════════════════════════

def test_decision_persisted_canonical_digest_validated():
    result, repo = run_cycle_once(core(), [make_source()], W5 + 60)
    assert result["ingested"] == 1
    rec = asyncio.run(repo.get_by_key("k1"))
    decision = json.loads(rec.decision_json)
    assert re.fullmatch(r"[0-9a-f]{16}", rec.decision_digest)
    assert rec.decision_digest == _digest(decision)
    # 读回同样过严格 validator
    validated = validate_decision(decision)
    assert validated.eligible is rec.eligible


def test_rejected_decision_persisted_not_labeled():
    # 构造必然拒绝的：事件 payload 周期声明 1h → M2 trigger_wrong_period
    src = make_source(dedupe_key="rej2")
    src = SourceEvent(
        dedupe_key="rej2", id=9, symbol=src.symbol, timeframe=src.timeframe,
        strategy=src.strategy, event_type=src.event_type, direction=src.direction,
        severity=src.severity, signal_time=src.signal_time,
        reference_price=src.reference_price, payload={**src.payload, "timeframe": "1h"},
        created_at_epoch=src.created_at_epoch,
    )
    result, repo = run_cycle_once(core(), [src], W5 + 60)
    assert result["ingested"] == 1
    rec = asyncio.run(repo.get_by_key("rej2"))
    assert rec.eligible is False
    assert rec.status == STATUS_REJECTED
    assert rec.label_json is None
    assert rec.maturity_window is None
    reasons = json.loads(rec.reasons)
    assert "trigger_wrong_period" in reasons["codes"]


def test_source_snapshot_preserved_readonly():
    result, repo = run_cycle_once(core(), [make_source()], W5 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    snap = json.loads(rec.source_snapshot)
    assert snap["dedupe_key"] == "k1"
    assert snap["payload"]["signal_type"] == "ma_breakout"
    assert snap["reference_price"] == _BARS["4h"][EVENT_IDX]["close"]


def test_decision_json_no_nan():
    """canonical JSON allow_nan=False：含 NaN 的值必须被拒绝而非序列化。"""
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
    with pytest.raises(ValueError):
        canonical_json({"x": float("inf")})


# ═══════════════════════════════════════════════════════════════════════════════
#  5. eligible 标签分期
# ═══════════════════════════════════════════════════════════════════════════════

def test_label_not_before_first_boundary():
    result, repo = run_cycle_once(core(), [make_source()], now=W5 - 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_PENDING
    assert rec.label_json is None
    assert rec.next_label_at is None


def test_label_generated_at_w5_boundary():
    result, repo = run_cycle_once(core(), [make_source()], now=W5 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_LABELED
    assert rec.maturity_window == 5
    assert rec.next_label_at == W10
    assert rec.label_as_of == W5
    lab = json.loads(rec.label_json)
    validate_outcome_label(lab)  # 读回过严格 validator
    w5 = next(x for x in lab["windows"] if x["window"] == 5)
    assert w5["matured"] is True
    w10 = next(x for x in lab["windows"] if x["window"] == 10)
    assert w10["matured"] is False and w10["censor_code"] == "label_as_of_not_reached"


def test_label_progresses_to_w10():
    result, repo = run_cycle_once(core(), [make_source()], now=W10 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_LABELED
    assert rec.maturity_window == 10
    assert rec.next_label_at == W20
    assert rec.label_as_of == W10
    lab = json.loads(rec.label_json)
    assert next(x for x in lab["windows"] if x["window"] == 10)["matured"] is True
    assert next(x for x in lab["windows"] if x["window"] == 20)["censor_code"] == "label_as_of_not_reached"


def test_label_skips_multiple_boundaries_when_late():
    """崩溃跳过多个边界：now 已到 20 边界 → 一次推进到 20（label_as_of=20 边界）。"""
    result, repo = run_cycle_once(core(), [make_source()], now=W20 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_LABELED
    assert rec.maturity_window == 20
    assert rec.next_label_at == W40
    assert rec.label_as_of == W20


def test_label_completed_at_w40():
    result, repo = run_cycle_once(core(), [make_source()], now=W40 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_COMPLETED
    assert rec.maturity_window == 40
    assert rec.next_label_at is None
    lab = json.loads(rec.label_json)
    assert all(x["matured"] for x in lab["windows"])


def test_completed_not_rewritten_on_rerun():
    src = make_source()
    result, repo = run_cycle_once(core(), [src], now=W40 + 60)
    rec1 = asyncio.run(repo.get_by_key("k1"))
    label1 = rec1.label_json
    result2, _ = run_cycle_once(core(), [src], now=W40 + 60 + 86_400, repo=repo)
    rec2 = asyncio.run(repo.get_by_key("k1"))
    assert rec2.status == STATUS_COMPLETED
    assert rec2.label_json == label1  # 已完成不改写
    assert result2["labeled"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  6. 幂等 / 重试 / 崩溃恢复
# ═══════════════════════════════════════════════════════════════════════════════

def test_rerun_no_duplicate_counting():
    src = make_source()
    result1, repo = run_cycle_once(core(), [src], W5 + 60)
    result2, _ = run_cycle_once(core(), [src], W5 + 60, repo=repo)
    result3, _ = run_cycle_once(core(), [src], W5 + 60, repo=repo)
    counts = asyncio.run(repo.counts())
    assert counts.total == 1 and counts.unique_eligible_digests == 1
    assert result2["ingested"] == 0 and result3["ingested"] == 0


def test_label_failure_backoff_then_retry_success():
    """label 阶段行情失败 → failed + attempts+1 + backoff；到期重试成功。"""
    core0 = core(retry_delay_seconds=300)
    src = make_source()
    loader_fail, _ = make_loader(fail_label=True)
    result1, repo = run_cycle_once(core0, [src], W5 + 60, loader=loader_fail)
    assert result1["failed"] == 1
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_FAILED
    assert rec.attempts == 1
    assert rec.next_retry_at == W5 + 60 + 300
    assert rec.last_error is not None
    # 未到 backoff：不再重试
    loader_ok, _ = make_loader()
    result2, _ = run_cycle_once(core0, [src], W5 + 60 + 100, repo=repo, loader=loader_ok)
    assert result2["labeled"] == 0
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_FAILED
    # 到 backoff：重试成功
    result3, _ = run_cycle_once(core0, [src], W5 + 60 + 301, repo=repo, loader=loader_ok)
    assert result3["labeled"] == 1
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_LABELED
    assert rec.attempts == 0 and rec.last_error is None


def test_attempts_cap_stops_retry():
    core0 = core(max_attempts=2, retry_delay_seconds=0)
    src = make_source()
    loader_fail, _ = make_loader(fail_label=True)
    result1, repo = run_cycle_once(core0, [src], W5 + 60, loader=loader_fail)
    assert result1["failed"] == 1
    result2, _ = run_cycle_once(core0, [src], W5 + 60 + 1, repo=repo, loader=loader_fail)
    assert result2["failed"] == 1
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.attempts == 2
    # 达上限：不再列入重试
    result3, _ = run_cycle_once(core0, [src], W5 + 60 + 2, repo=repo, loader=loader_fail)
    assert result3["failed"] == 0 and result3["labeled"] == 0
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_FAILED and rec.attempts == 2


def test_same_bar_multi_events_unique_digest_count():
    """同 bar（同 signal_time）多事件：各自成行、digest 不同、唯一计数正确。"""
    srcs = [
        make_source(dedupe_key="k-a", strategy="ma", event_type="ma_breakout"),
        make_source(dedupe_key="k-b", strategy="macd", event_type="macd_divergence_confirmed"),
    ]
    result, repo = run_cycle_once(core(), srcs, W5 + 60)
    assert result["ingested"] == 2
    counts = asyncio.run(repo.counts())
    assert counts.total == 2
    assert counts.unique_eligible_digests == 2
    ra = asyncio.run(repo.get_by_key("k-a"))
    rb = asyncio.run(repo.get_by_key("k-b"))
    assert ra.decision_digest != rb.decision_digest
    assert ra.signal_time == rb.signal_time  # 同 bar


def test_ingest_after_partial_cycle_completes():
    """部分处理后崩溃：新事件下一轮补齐，已处理不重复。"""
    src_a = make_source(dedupe_key="k-a", strategy="ma")
    src_b = make_source(dedupe_key="k-b", strategy="macd", event_type="macd_divergence")
    result1, repo = run_cycle_once(core(), [src_a, src_b], W5 + 60)
    assert result1["ingested"] == 2
    # 模拟：仅 a 已处理（b 丢失），重跑
    repo2 = MemoryShadowRepo(core=core())
    repo2._events = {"k-a": asyncio.run(repo.get_by_key("k-a"))}
    repo2.seed_source([src_a, src_b])
    result2, _ = run_cycle_once(core(), [src_a, src_b], W5 + 60, repo=repo2)
    assert result2["ingested"] == 1  # 只补 b
    counts = asyncio.run(repo2.counts())
    assert counts.total == 2


# ═══════════════════════════════════════════════════════════════════════════════
#  7. 未来追加不变（因果）
# ═══════════════════════════════════════════════════════════════════════════════

def test_decision_digest_immutable_append_future():
    src = make_source()
    loader1, _ = make_loader()
    result, repo = run_cycle_once(core(), [src], W5 + 60, loader=loader1)
    rec = asyncio.run(repo.get_by_key("k1"))
    digest_before = rec.decision_digest
    # 追加大量未来 bar（更长 4H 序列）重新计算：digest 必须不变
    extended = dict(_BARS)
    extended["4h"] = make_bars(900, STEP4)
    loader2, _ = make_loader(extended)
    rec2 = compute_decision_record(
        core(), src,
        {tf: asyncio.run(loader2(src.symbol, tf, AS_OF, 700)) for tf in ("1week", "1day", "4h")},
        AS_OF,
    )
    assert rec2.decision_digest == digest_before
    assert rec2.decision_json == rec.decision_json


def test_matured_label_immutable_append_future():
    src = make_source()
    result, repo = run_cycle_once(core(), [src], W40 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    label_before = rec.label_json
    # 追加更长序列重新算 label：成熟窗口逐字段不变
    extended = dict(_BARS)
    extended["4h"] = make_bars(900, STEP4)
    loader2, _ = make_loader(extended)
    bars = asyncio.run(loader2(src.symbol, "4h", W40, 700))
    decision = json.loads(rec.decision_json)
    lab = label_decision(decision, bars, W40, core().market_state_config)
    assert canonical_json(lab.to_dict()) == label_before


def test_rejected_decision_immutable_append_future():
    src = make_source(dedupe_key="rej2")
    src = SourceEvent(
        dedupe_key="rej2", id=9, symbol=src.symbol, timeframe=src.timeframe,
        strategy=src.strategy, event_type=src.event_type, direction=src.direction,
        severity=src.severity, signal_time=src.signal_time,
        reference_price=src.reference_price, payload={**src.payload, "timeframe": "1h"},
        created_at_epoch=src.created_at_epoch,
    )
    loader1, _ = make_loader()
    result, repo = run_cycle_once(core(), [src], W5 + 60, loader=loader1)
    rec = asyncio.run(repo.get_by_key("rej2"))
    digest_before = rec.decision_digest
    extended = dict(_BARS)
    extended["4h"] = make_bars(900, STEP4)
    loader2, _ = make_loader(extended)
    rec2 = compute_decision_record(
        core(), src,
        {tf: asyncio.run(loader2(src.symbol, tf, AS_OF, 700)) for tf in ("1week", "1day", "4h")},
        AS_OF,
    )
    assert rec2.decision_digest == digest_before
    assert rec2.eligible is False


# ═══════════════════════════════════════════════════════════════════════════════
#  8. 错误脱敏
# ═══════════════════════════════════════════════════════════════════════════════

def test_sanitize_redacts_discord_webhook():
    msg = "投递失败 https://discord.com/api/webhooks/123/secret-token 重试中"
    out = sanitize_error(msg)
    assert "discord.com" not in out
    assert "<redacted>" in out


def test_sanitize_redacts_token_and_secret():
    msg = 'auth failed token=abcdef1234567890 secret="xyz"'
    out = sanitize_error(msg)
    assert "abcdef1234567890" not in out
    assert "xyz" not in out
    assert "<redacted>" in out


def test_sanitize_truncates_long_error():
    msg = "x" * 5000
    out = sanitize_error(msg)
    assert len(out) <= 300 + 2
    assert out.endswith("…")


def test_sanitize_none_returns_none():
    assert sanitize_error(None) is None


# ═══════════════════════════════════════════════════════════════════════════════
#  9. V1 隔离与无发送
# ═══════════════════════════════════════════════════════════════════════════════

class _BoomRepo(MemoryShadowRepo):
    """注入崩溃：fetch_candidates 抛异常（模拟 DB 故障）。"""

    async def fetch_candidates(self, *args, **kwargs):
        raise RuntimeError("DB connection lost")


def test_cycle_safe_swallows_exception():
    repo = _BoomRepo(core=core())
    loader, _ = make_loader()
    out = asyncio.run(run_cycle_safe(core(), repo, loader, W5 + 60))
    assert out["ok"] is False
    assert "DB connection lost" in out["error"]


def test_cycle_safe_writes_heartbeat_error():
    repo = _BoomRepo(core=core())
    loader, _ = make_loader()
    asyncio.run(run_cycle_safe(core(), repo, loader, W5 + 60))
    hb = asyncio.run(repo.get_heartbeat())
    assert hb is not None
    assert hb.last_run_ok is False
    assert hb.last_error and "DB connection lost" in hb.last_error


def test_cycle_unsafe_raises_for_contrast():
    """红→绿证据 a：坏路径（直接 run_cycle 无隔离）会抛出——若 scheduler
    不做隔离将冒泡打断 V1 job；好路径 run_cycle_safe 不抛。"""
    repo = _BoomRepo(core=core())
    loader, _ = make_loader()
    with pytest.raises(RuntimeError):
        asyncio.run(run_cycle(core(), repo, loader, W5 + 60))


def test_module_imports_no_sender():
    """shadow 模块禁止导入任何 sender（discord/feishu/notification）。"""
    import ast
    import inspect
    import app.core.m3_shadow_runtime as mod
    tree = ast.parse(inspect.getsource(mod))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for banned in ("notification_channels", "discord_webhook", "feishu_webhook",
                   "monitor_engine", "alert_engine"):
        assert not any(banned in name for name in imported), f"shadow 模块不得导入 {banned}"


def test_scheduler_shadow_job_skips_when_disabled():
    """默认 disabled：shadow job 零 DB/零网络（不触发 AsyncSessionLocal）。"""
    import app.tasks.alert_scheduler as sched
    from unittest.mock import patch

    sched.settings.M3_SHADOW_ENABLED = False
    with patch.object(sched, "AsyncSessionLocal", side_effect=AssertionError("disabled 不得碰 DB")):
        asyncio.run(sched._shadow_job())
    sched.settings.M3_SHADOW_ENABLED = True  # 还原
    sched.settings.M3_SHADOW_ENABLED = False


# ═══════════════════════════════════════════════════════════════════════════════
#  10. CLI 汇总与进度
# ═══════════════════════════════════════════════════════════════════════════════

def test_progress_honest_not_faked():
    p = compute_progress(started_at=W5, counts=ShadowCounts(unique_eligible_digests=7), now=W5)
    assert p["days_elapsed"] == 0.0
    assert p["days_remaining_30"] == 30.0
    assert p["unique_eligible_remaining"] == 93
    assert p["milestone_reached"] is False  # 本轮不伪造门槛


def test_progress_milestone_requires_both():
    p = compute_progress(
        started_at=W5 - 31 * 86400,
        counts=ShadowCounts(unique_eligible_digests=99),
        now=W5,
    )
    assert p["milestone_reached"] is False  # 30 天满足但 100 未满
    p2 = compute_progress(
        started_at=W5 - 31 * 86400,
        counts=ShadowCounts(unique_eligible_digests=100),
        now=W5,
    )
    assert p2["milestone_reached"] is True  # 两者同时满足


def test_status_out_model_strict_no_payload():
    """ShadowStatusOut 字段白名单：绝无 payload/source_snapshot/决策细节。"""
    import pydantic
    out = ShadowStatusOut(
        enabled=True, symbols=["BINANCE:BTCUSDT"], timeframe="4h", max_bars=700,
        heartbeat={"exists": True, "started_at": 123, "last_run_at": 124,
                   "last_run_ok": True, "last_error": None},
        counts={"total": 3, "eligible": 2, "rejected": 1, "pending": 0, "labeled": 0,
                "completed": 0, "failed": 0, "decision_failed": 0, "label_failed": 0,
                "unique_eligible_digests": 2},
        days_elapsed=0.0, days_remaining_30=30.0,
        unique_eligible_digests=2, unique_eligible_remaining=98,
        milestone_reached=False,
    )
    data = out.model_dump()
    for banned in ("payload", "source_snapshot", "decision_json", "label_json",
                   "webhook", "token", "secret"):
        assert banned not in json.dumps(data), f"status 不得输出 {banned}"
    # extra forbid：多传字段必须拒绝
    with pytest.raises(pydantic.ValidationError):
        ShadowStatusOut.model_validate({**data, "hacked": 1})


# ═══════════════════════════════════════════════════════════════════════════════
#  11. 反向红→绿三组（含在测试内对比；文档另附实际红→绿运行证据）
# ═══════════════════════════════════════════════════════════════════════════════

class _NoUniqueRepo(MemoryShadowRepo):
    """红→绿证据 b：坏实现——fetch 不排除已处理 + 无唯一键，同源重复产生两行。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rows: list = []

    async def fetch_candidates(
        self, started_at: int, timeframe: str, symbols: frozenset[str], limit: int
    ) -> list:
        # 坏实现：不排除已处理源（分页/去重都坏）
        return [
            r for r in self._source_rows
            if r.created_at_epoch >= started_at and r.timeframe == timeframe
        ][:limit]

    async def existing_keys(self, keys: list[str]) -> set[str]:
        return set()  # 坏实现：永远认为未处理

    async def insert_event(self, rec) -> bool:
        self._rows.append(rec)  # 坏实现：每行都插，无唯一键
        self._events[rec.source_dedupe_key] = rec  # 兼容基类 update/mark_failed
        return True

    async def counts(self) -> ShadowCounts:
        return ShadowCounts(total=len(self._rows), eligible=sum(1 for r in self._rows if r.eligible))

    async def get_by_key(self, key: str):
        return next((r for r in reversed(self._rows) if r.source_dedupe_key == key), None)

    async def list_label_due(self, now: int, limit: int) -> list:
        return []  # 坏实现与本测试无关（只数行）


def test_r2g_duplicate_without_unique_key_red():
    """坏 repo：同源重跑产生两行（counts.total=2）→ 红。"""
    src = make_source()
    result1, repo = run_cycle_once(core(), [src], W5 + 60, repo=_NoUniqueRepo(core=core()))
    result2, _ = run_cycle_once(core(), [src], W5 + 60, repo=repo)
    counts = asyncio.run(repo.counts())
    assert counts.total == 2  # 红：同源重复产生两行


def test_r2g_duplicate_with_unique_key_green():
    """好 repo（MemoryShadowRepo 模拟唯一键）：同源重跑只一行 → 绿。"""
    src = make_source()
    result1, repo = run_cycle_once(core(), [src], W5 + 60)
    result2, _ = run_cycle_once(core(), [src], W5 + 60, repo=repo)
    counts = asyncio.run(repo.counts())
    assert counts.total == 1  # 绿


def test_r2g_future_bar_rewrite_red():
    """红→绿证据 c：坏评估——as_of 用未来时刻（W5，事件 bar 尚未可见），
    未来数据进入决策 → digest 必然改变（红）；真实实现按 as_of=signal_time
    +14400 截断，digest 不变（绿，见下）。"""
    src = make_source()
    result, repo = run_cycle_once(core(), [src], W5 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    digest_original = rec.decision_digest
    trigger = {
        "strategy": src.strategy, "event_type": src.event_type,
        "direction": src.direction, "severity": src.severity,
        "signal_time": src.signal_time, "reference_price": src.reference_price,
        "summary": "", "payload": src.payload,
    }
    # 坏评估：as_of 推后到未来边界 W5（事件 bar 620 尚未收盘于该 as_of 前）
    d_future = evaluate(src.symbol, {
        "1week": make_bars(800, STEP1W, start_off=700),
        "1day": make_bars(800, STEP1D, start_off=700),
        "4h": make_bars(900, STEP4),
    }, W5, trigger, core().market_state_config, core().fusion_config)
    assert _digest(d_future.to_dict()) != digest_original  # 红：未来 as_of 改写决策


def test_r2g_future_bar_snapshot_green():
    """绿：按 as_of 截断且与运行时相同的 max_bars 截断后，digest 与持久化
    快照一致（未来不参与、取数窗口语义一致）。"""
    src = make_source()
    result, repo = run_cycle_once(core(), [src], W5 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    digest_original = rec.decision_digest
    rec2 = compute_decision_record(
        core(), src,
        {tf: [b for b in _BARS[tf] if b["time"] + TIMEFRAME_DURATIONS[tf] <= AS_OF][-700:]
         for tf in ("1week", "1day", "4h")},
        AS_OF,
    )
    assert rec2.decision_digest == digest_original  # 绿


# ═══════════════════════════════════════════════════════════════════════════════
#  12. 补充：核心纯函数与边界
# ═══════════════════════════════════════════════════════════════════════════════

def test_window_boundary_formula():
    c = core()
    assert c.window_boundary(SIG, 5) == SIG + 6 * STEP4
    assert c.window_boundary(SIG, 40) == SIG + 41 * STEP4
    with pytest.raises(ValueError):
        c.window_boundary(SIG, 7)


def test_next_label_boundary_states():
    c = core()
    from app.core.m3_shadow_runtime import ShadowRecord
    rec = ShadowRecord(
        source_dedupe_key="x", source_event_id=1, symbol="S", timeframe="4h",
        signal_time=SIG, source_snapshot="{}", decision_json="{}",
        decision_digest="0" * 16, eligible=True, reasons="[]", config_hash="{}",
        status=STATUS_PENDING,
    )
    assert c.next_label_boundary(rec, W5 - 1) is None
    las, w = c.next_label_boundary(rec, W5 + 1)
    assert (las, w) == (W5, 5)
    rec.status = STATUS_LABELED
    rec.next_label_at = W10
    las, w = c.next_label_boundary(rec, W40 + 1)
    assert (las, w) == (W40, 40)  # 跳过多边界一次推进
    rec.status = STATUS_COMPLETED
    assert c.next_label_boundary(rec, W40 + 1) is None


def test_compute_label_record_roundtrip_validated():
    """compute_label_record 输出 label_json 可过 M3A 严格 validator。"""
    src = make_source()
    result, repo = run_cycle_once(core(), [src], W5 + 60)
    rec = asyncio.run(repo.get_by_key("k1"))
    lab = json.loads(rec.label_json)
    validated = validate_outcome_label(lab)
    assert validated.decision_digest == rec.decision_digest
    assert validated.signal_time == SIG


# ═══════════════════════════════════════════════════════════════════════════════
#  14. 主审整改反例（先红后绿锁死；见 PROGRESS 红→绿证据）
# ═══════════════════════════════════════════════════════════════════════════════

def make_loader_v2(bars_map: dict[str, list[dict]] | None = None, *,
                   fail_symbol: str | None = None, fail_decision: bool = False,
                   fail_first: bool = False) -> tuple[object, list[str]]:
    """loader v2：支持按 symbol 注入 decision 取数失败 / 首调用失败；统计调用。"""
    bars = bars_map or _BARS
    calls: list[str] = []
    state = {"count": 0}

    async def loader(symbol: str, timeframe: str, end_ts: int, max_bars: int) -> list[dict]:
        calls.append(f"{symbol}|{timeframe}|{end_ts}")
        state["count"] += 1
        if fail_decision and fail_symbol is not None and symbol.upper() == fail_symbol.upper():
            raise RuntimeError("decision 行情取数失败注入")
        if fail_first and state["count"] == 1:
            raise RuntimeError("首调用失败注入")
        dur = TIMEFRAME_DURATIONS[timeframe]
        return [b for b in bars[timeframe] if b["time"] + dur <= end_ts][-max_bars:]

    return loader, calls


# ── 整改 1：分页饥饿（NOT EXISTS/anti-join，LIMIT 前排除已处理）──────────────

def test_pagination_no_starvation_second_page():
    """>limit 两轮：第二轮必须吃下一页，不能永远第一页。"""
    core0 = core(ingest_limit=10)
    srcs = [make_source(dedupe_key=f"p{i:03d}", event_type="ma_breakout",
                        direction="long" if i % 2 == 0 else "short")
            for i in range(25)]
    result1, repo = run_cycle_once(core0, srcs, W5 + 60)
    assert result1["ingested"] == 10
    assert asyncio.run(repo.counts()).total == 10
    result2, _ = run_cycle_once(core0, srcs, W5 + 60, repo=repo)
    assert result2["ingested"] == 10  # 第二轮必须吃下一页（10~19）
    assert asyncio.run(repo.counts()).total == 20
    result3, _ = run_cycle_once(core0, srcs, W5 + 60, repo=repo)
    assert result3["ingested"] == 5
    assert asyncio.run(repo.counts()).total == 25


def test_fetch_candidates_sql_anti_join_structure():
    """SQL 结构证据：fetch_candidates 的查询必须在 LIMIT 前用 NOT EXISTS 排除
    已处理源（不能先 LIMIT 再在 Python 侧跳过）。"""
    from app.core.m3_shadow_runtime import fetch_candidates_stmt
    stmt = fetch_candidates_stmt(started_at=W5, timeframe="4h",
                                 symbols=frozenset({"BINANCE:BTCUSDT"}), limit=7)
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True})).upper()
    exists_pos = sql.find("EXISTS")
    limit_pos = sql.rfind("LIMIT")
    assert exists_pos != -1 and limit_pos != -1
    # NOT EXISTS 必须出现在 LIMIT 之前（子查询内排除已处理源）
    assert exists_pos < limit_pos
    not_pos = sql.rfind("NOT", 0, exists_pos)
    assert not_pos != -1 and not_pos < exists_pos  # NOT(EXISTS...) 语义
    # 顶层不允许 OFFSET 形态（即不存在「先取 limit 行再跳过」）
    assert "OFFSET" not in sql


# ── 整改 2：decision 失败单事件隔离 + 持久化重试（failure_stage）──────────────

def test_decision_failure_persists_failed_row():
    """决策/取数失败必须落库（唯一行）：source 快照 + 脱敏错误 + attempts + backoff。"""
    bad = make_source(dedupe_key="bad")
    loader, _calls = make_loader_v2(fail_symbol="BINANCE:BTCUSDT", fail_decision=True)
    result, repo = run_cycle_once(core(), [bad], W5 + 60, loader=loader)
    assert result["ingested"] == 0
    assert result["decision_failed"] == 1
    rec = asyncio.run(repo.get_by_key("bad"))
    assert rec is not None
    assert rec.status == STATUS_FAILED
    assert rec.failure_stage == "decision"
    assert rec.attempts == 1
    assert rec.next_retry_at == W5 + 60 + 300
    assert rec.last_error and "行情取数失败注入" in rec.last_error
    assert rec.decision_json is None  # decision 失败态允许决策字段为空
    assert json.loads(rec.source_snapshot)["dedupe_key"] == "bad"  # 源快照仍在


def test_first_bad_then_good_ingest():
    """一个坏源不阻塞同轮后续好源：首坏次好仍摄取好源。"""
    bad = make_source(dedupe_key="bad")
    good = make_source(dedupe_key="good")
    loader, _calls = make_loader_v2(fail_first=True)
    result, repo = run_cycle_once(core(), [bad, good], W5 + 60, loader=loader)
    assert result["decision_failed"] == 1
    assert result["ingested"] == 1  # 好源正常摄取
    bad_rec = asyncio.run(repo.get_by_key("bad"))
    assert bad_rec is not None and bad_rec.status == STATUS_FAILED
    good_rec = asyncio.run(repo.get_by_key("good"))
    assert good_rec is not None and good_rec.decision_digest is not None


def test_decision_retry_backoff_then_promote_same_row():
    """backoff 未到期不重试（不拉行情）；到期后成功 → 原行升级，无重复行。"""
    core0 = core(retry_delay_seconds=300)
    bad = make_source(dedupe_key="bad")
    loader_fail, calls1 = make_loader_v2(fail_symbol="BINANCE:BTCUSDT", fail_decision=True)
    result1, repo = run_cycle_once(core0, [bad], W5 + 60, loader=loader_fail)
    assert result1["decision_failed"] == 1
    # backoff 内：不重试、不拉取
    loader_ok, calls2 = make_loader_v2()
    result2, _ = run_cycle_once(core0, [bad], W5 + 60 + 100, repo=repo, loader=loader_ok)
    assert result2["ingested"] == 0 and result2["decision_failed"] == 0
    assert len(calls2) == 0  # backoff 前不拉取
    # 到期：重试成功 → 原行升级（无重复行）；eligible 且 5 边界已到会立即 label
    result3, _ = run_cycle_once(core0, [bad], W5 + 60 + 301, repo=repo, loader=loader_ok)
    rec = asyncio.run(repo.get_by_key("bad"))
    assert rec.status in (STATUS_PENDING, STATUS_REJECTED, STATUS_LABELED)
    assert rec.failure_stage is None
    assert rec.decision_digest and re.fullmatch(r"[0-9a-f]{16}", rec.decision_digest)
    assert rec.attempts == 0
    assert asyncio.run(repo.counts()).total == 1  # 无重复行


def test_decision_failure_max_attempts_stops():
    core0 = core(max_attempts=2, retry_delay_seconds=0)
    bad = make_source(dedupe_key="bad")
    loader_fail, _ = make_loader_v2(fail_symbol="BINANCE:BTCUSDT", fail_decision=True)
    result1, repo = run_cycle_once(core0, [bad], W5 + 60, loader=loader_fail)
    assert result1["decision_failed"] == 1
    result2, _ = run_cycle_once(core0, [bad], W5 + 60 + 1, repo=repo, loader=loader_fail)
    assert asyncio.run(repo.get_by_key("bad")).attempts == 2
    result3, _ = run_cycle_once(core0, [bad], W5 + 60 + 2, repo=repo, loader=loader_fail)
    assert result3["decision_failed"] == 0  # 达上限停止
    assert asyncio.run(repo.get_by_key("bad")).attempts == 2


def test_decision_failure_survives_whole_cycle():
    """坏源不使整轮退出：一轮内既处理 decision 失败，也完成 label 更新与心跳。"""
    bad = make_source(dedupe_key="bad")
    good = make_source(dedupe_key="good")
    loader, _ = make_loader_v2(fail_first=True)
    result, repo = run_cycle_once(core(), [bad, good], W5 + 60, loader=loader)
    hb = asyncio.run(repo.get_heartbeat())
    assert hb is not None and hb.last_run_ok is True
    assert result["decision_failed"] == 1 and result["ingested"] == 1


def test_label_failure_keeps_decision():
    """标签失败（failure_stage=label）不得丢 decision 字段。"""
    bad = make_source(dedupe_key="k1")
    loader, _ = make_loader_v2(fail_symbol="BINANCE:BTCUSDT", fail_decision=False)
    # 让 label 取数失败：fail_first 在第 4 次调用（label 4H 取数）抛
    state = {"count": 0}
    bars = _BARS
    async def loader2(symbol, timeframe, end_ts, max_bars):
        state["count"] += 1
        if state["count"] == 4:  # decision 3 次取数后 = label 取数
            raise RuntimeError("label 取数失败注入")
        dur = TIMEFRAME_DURATIONS[timeframe]
        return [b for b in bars[timeframe] if b["time"] + dur <= end_ts][-max_bars:]
    result, repo = run_cycle_once(core(), [bad], W5 + 60, loader=loader2)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_FAILED
    assert rec.failure_stage == "label"
    assert rec.decision_digest is not None and rec.decision_json is not None  # decision 未丢
    assert rec.eligible is True


# ── 整改 3：production_market_loader 把 end_ts 作为 provider before 传入 ──────

def test_loader_passes_before_to_provider_and_filters_future():
    """spy 反例：end_ts 必须作为 provider 的 before 透传（不是拉最新再后过滤）；
    provider 返回追加未来数据不改变结果（二次过滤 + 截断）。"""
    import app.core.daily_report_sources as drs_mod
    from app.core.m3_shadow_runtime import production_market_loader
    from unittest import mock

    captured: dict = {}

    class FakeSources:
        def __init__(self) -> None:
            pass

        async def get_candles(self, symbol, outputsize=None, before=None, timeframe=None):
            captured["before"] = before
            captured["outputsize"] = outputsize
            captured["timeframe"] = timeframe
            dur = TIMEFRAME_DURATIONS[timeframe]
            return [dict(b) for b in make_bars(800, dur, start_off=700)]

    end_ts = AS_OF
    dur4 = TIMEFRAME_DURATIONS["4h"]
    with mock.patch.object(drs_mod, "DailyReportSources", FakeSources):
        out = asyncio.run(production_market_loader("BINANCE:BTCUSDT", "4h", end_ts, 700))
    assert captured["before"] == end_ts  # 精确 end_ts（不得 end_ts+duration）
    assert captured["outputsize"] >= 700
    assert captured["timeframe"] == "4h"
    assert all(b["time"] + dur4 <= end_ts for b in out)  # 未来被二次过滤
    assert len(out) <= 700  # 截断


# ── 整改 4：scheduler 兜底写 heartbeat 后必须 commit ─────────────────────────

def test_shadow_job_outer_heartbeat_commit_is_isolated():
    """scheduler 外层兜底写 heartbeat 后必须 commit；commit 失败也不冒泡。"""
    import app.tasks.alert_scheduler as sched
    from unittest import mock

    sched.settings.M3_SHADOW_ENABLED = True
    sched.settings.M3_SHADOW_SYMBOLS = "BINANCE:BTCUSDT"  # pytest 环境 .env 不可见，需显式
    events: list[str] = []
    commit_count = {"n": 0}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self, *a, **k):
            events.append("execute")
            class R:
                def scalar_one_or_none(self):
                    return None
            return R()

        async def commit(self):
            commit_count["n"] += 1
            raise RuntimeError("commit 失败注入")

    with mock.patch.object(sched, "AsyncSessionLocal", lambda: FakeSession()):
        asyncio.run(sched._shadow_job())
    assert "execute" in events  # 走了 DB 路径（含兜底写 heartbeat）
    assert commit_count["n"] >= 1  # 异常路径也尝试 commit
    sched.settings.M3_SHADOW_ENABLED = False
    sched.settings.M3_SHADOW_SYMBOLS = ""


# ── 整改 5：标签成熟真实性（maturity_window=实际成熟最大值，无则 None）────────

def test_label_missing_w5_no_fake_maturity():
    """5 窗口缺根（数据未到齐）：maturity_window=None、status 保持 pending，
    不伪设 5、不跳 10。"""
    thin = dict(_BARS)
    thin["4h"] = make_bars(EVENT_IDX + 1, STEP4)  # 只有到事件 bar，5 窗口缺根
    loader, _ = make_loader_v2(thin)
    result, repo = run_cycle_once(core(), [make_source()], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_PENDING
    assert rec.maturity_window is None
    assert rec.label_as_of is None or rec.label_as_of == W5


def test_label_partial_matured_target_missing():
    """5 成熟、10 边界到期但 10 缺根：maturity_window=5（实际值）、next 仍 10
    边界、不跳 20。"""
    thin = dict(_BARS)
    thin["4h"] = make_bars(EVENT_IDX + 1 + 5, STEP4)  # 事件 bar + 5 根（10 缺根）
    loader, _ = make_loader_v2(thin)
    result1, repo = run_cycle_once(core(), [make_source()], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_LABELED and rec.maturity_window == 5
    result2, _ = run_cycle_once(core(), [make_source()], W10 + 60, repo=repo, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_LABELED
    assert rec.maturity_window == 5
    assert rec.next_label_at == W10  # 不跳 20


def test_label_data_arrives_later_progresses():
    """缺根 → 数据到齐（backoff 到期后）→ 重试推进到 10。"""
    core0 = core(label_retry_delay_seconds=900)
    thin = dict(_BARS)
    thin["4h"] = make_bars(EVENT_IDX + 1 + 5, STEP4)
    loader_thin, _ = make_loader_v2(thin)
    result1, repo = run_cycle_once(core0, [make_source()], W10 + 60, loader=loader_thin)
    assert asyncio.run(repo.get_by_key("k1")).maturity_window == 5
    # backoff 内不重试（防热循环）：数据已到齐但未到期 → 不推进
    full_loader, _ = make_loader_v2()
    result2, _ = run_cycle_once(core0, [make_source()], W10 + 60 + 100, repo=repo, loader=full_loader)
    assert asyncio.run(repo.get_by_key("k1")).maturity_window == 5
    # backoff 到期 + 数据到齐 → 推进到 10
    result3, _ = run_cycle_once(core0, [make_source()], W10 + 60 + 901, repo=repo, loader=full_loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.maturity_window == 10
    assert rec.next_label_at == W20
    assert rec.next_retry_at is None  # 成功清空 backoff


# ── 整改 6：ShadowCore 参数语义校验；Sql repo 不硬编码 max_attempts ──────────

def test_core_rejects_bad_timeframe():
    with pytest.raises(ValueError):
        core(timeframe="1h")


def test_core_rejects_nonpositive_params():
    with pytest.raises(ValueError):
        core(max_bars=0)
    with pytest.raises(ValueError):
        core(ingest_limit=0)
    with pytest.raises(ValueError):
        core(label_limit=0)
    with pytest.raises(ValueError):
        core(max_attempts=0)
    with pytest.raises(ValueError):
        core(retry_delay_seconds=-1)


def test_sql_repo_uses_core_max_attempts():
    """Sql repo 不得硬编码 DEFAULT_MAX_ATTEMPTS，须用 core.max_attempts。"""
    from app.core.m3_shadow_runtime import SqlShadowRepo
    repo = SqlShadowRepo(session=object(), core=core(max_attempts=2))
    assert repo._max_attempts() == 2
    repo2 = SqlShadowRepo(session=object(), core=core(max_attempts=9))
    assert repo2._max_attempts() == 9


# ── 整改 7：Sql 读回重新验证（不信任脏行）────────────────────────────────────

def test_readback_validates_decision_digest_consistency():
    """读回行 decision 内容与 digest 不一致必须拒绝（不信任脏行）。"""
    from app.core.m3_shadow_runtime import validate_row_content
    good = make_source()
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [good], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    original_digest = rec.decision_digest
    # 篡改 digest → 重算不匹配 → 拒绝
    rec.decision_digest = "0" * 16
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # 篡改 decision_json（非法 JSON）→ 拒绝
    rec.decision_digest = original_digest
    rec.decision_json = "{not json"
    with pytest.raises(ValueError):
        validate_row_content(rec)


def test_readback_validates_label_content():
    """读回行 label 内容必须过 M3A 严格 validator。"""
    from app.core.m3_shadow_runtime import validate_row_content
    good = make_source()
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [good], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.label_json is not None
    validate_row_content(rec)  # 合法行不抛
    rec.label_json = "{}"  # 伪造 label → 拒绝
    with pytest.raises(ValueError):
        validate_row_content(rec)


# ── 整改 8：disabled 零 DB/网络、commit 与隔离仍成立（回归确认）───────────────

def test_disabled_production_cycle_zero_db():
    """disabled：run_production_cycle 不得碰 DB（不触发 AsyncSessionLocal）。"""
    from app.core.m3_shadow_runtime import run_production_cycle
    from unittest import mock
    cfg = Settings(M3_SHADOW_ENABLED=False, M3_SHADOW_SYMBOLS="")
    with mock.patch("app.core.m3_shadow_runtime.ShadowCore.from_settings",
                    return_value=ShadowCore.from_settings(cfg)):
        result = asyncio.run(run_production_cycle(session=object()))
    assert result["enabled"] is False


# ═══════════════════════════════════════════════════════════════════════════════
#  15. 第二轮主审反例（A–H，先红后绿；见 PROGRESS 红→绿证据）
# ═══════════════════════════════════════════════════════════════════════════════

# ── A：pending 的 censored label 合法；maturity 非空必须有 label ─────────────

def test_update_label_pending_censored_is_legal():
    """A：数据未到齐时 update_label(status=pending, label_json=非空 censored,
    label_as_of=W5, maturity_window=None) 必须合法（Memory + validate 层）。"""
    src = make_source()
    thin = dict(_BARS)
    thin["4h"] = make_bars(EVENT_IDX + 1, STEP4)  # 5 窗口缺根 → 全 censored
    loader, _ = make_loader_v2(thin)
    result, repo = run_cycle_once(core(), [src], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_PENDING
    assert rec.label_json is not None and rec.label_as_of == W5
    assert rec.maturity_window is None
    from app.core.m3_shadow_runtime import validate_row_content
    validate_row_content(rec)  # 读回验证不拒绝（Memory 与 Sql 同口径）


def test_maturity_requires_label_and_asof():
    """A：maturity 非空而 label_json/label_as_of 为空 → 非法（DB CHECK）。"""
    from app.core.m3_shadow_runtime import validate_row_content
    src = make_source()
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [src], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    # 篡改：maturity 非空但 label_json 为空 → 拒绝
    rec.label_json = None
    with pytest.raises(ValueError):
        validate_row_content(rec)


# ── B：首次 label 异常（failed/label + next_label_at NULL）backoff 后可重试 ──

def test_failed_label_null_next_label_retries_after_backoff():
    """B：eligible 新行在 W5 首次 label 抛错 → failed/label + next_label_at=NULL；
    backoff 未到不重试，到期后按 W5 语义重试（Memory 语义；SQL 同口径）。"""
    core0 = core(retry_delay_seconds=300, label_retry_delay_seconds=900)
    src = make_source(dedupe_key="k1")
    # 首次 label（第 4 次取数）抛错
    state = {"count": 0}
    bars = _BARS
    async def loader_bad(symbol, timeframe, end_ts, max_bars):
        state["count"] += 1
        if state["count"] == 4:
            raise RuntimeError("label 取数失败注入")
        dur = TIMEFRAME_DURATIONS[timeframe]
        return [b for b in bars[timeframe] if b["time"] + dur <= end_ts][-max_bars:]
    result1, repo = run_cycle_once(core0, [src], W5 + 60, loader=loader_bad)
    assert result1["failed"] == 1
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_FAILED and rec.failure_stage == "label"
    assert rec.next_label_at is None
    assert rec.next_retry_at == W5 + 60 + 300
    # backoff 内不重试
    loader_ok, _ = make_loader_v2()
    result2, _ = run_cycle_once(core0, [src], W5 + 60 + 100, repo=repo, loader=loader_ok)
    assert result2["failed"] == 0 and result2["labeled"] == 0
    # backoff 到期 → 按 W5 重试成功
    result3, _ = run_cycle_once(core0, [src], W5 + 60 + 301, repo=repo, loader=loader_ok)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status in (STATUS_LABELED, STATUS_PENDING)  # 进入 W5 重试（不再 failed）
    assert rec.failure_stage is None


def test_failed_label_null_next_label_sql_max_attempts_from_core():
    """B（旧占位，已改名避免同名覆盖）：Sql repo 的 max_attempts 来自 core
    （不硬编码）；真正的 due 分支精确锁定断言见第 16 节强化版
    test_failed_label_null_next_label_sql_condition。"""
    from app.core.m3_shadow_runtime import SqlShadowRepo
    repo = SqlShadowRepo(session=object(), core=core())
    assert repo._max_attempts() == 6
    core3 = core(max_attempts=3)
    repo3 = SqlShadowRepo(session=object(), core=core3)
    assert repo3._max_attempts() == 3


# ── C：scheduler 兜底 commit 完全隔离测试（不得访问真实 DB）──────────────────

def test_scheduler_fallback_commit_exactly_once_isolated_v1_legacy():
    """C（旧版 v1，已改名避免与第 16 节完整隔离版同名覆盖；完整版见
    test_scheduler_fallback_commit_exactly_once_isolated）：主路径强制抛异常 →
    兜底 touch_heartbeat 后 commit 恰好一次；patch app.db.session.AsyncSessionLocal
    （兜底 import 的同一模块），完全隔离真实 DB/网络；settings 用 try/finally 还原。"""
    import app.tasks.alert_scheduler as sched
    import app.core.m3_shadow_runtime as rt
    import app.db.session as db_session
    from unittest import mock

    old_enabled = sched.settings.M3_SHADOW_ENABLED
    old_symbols = sched.settings.M3_SHADOW_SYMBOLS
    sched.settings.M3_SHADOW_ENABLED = True
    sched.settings.M3_SHADOW_SYMBOLS = "BINANCE:BTCUSDT"
    try:
        events = []
        commit_count = {"n": 0}

        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def execute(self, *a, **k):
                events.append("execute")
                return FakeResult()

            async def commit(self):
                commit_count["n"] += 1
                events.append("commit")

        with mock.patch.object(rt, "run_production_cycle",
                               side_effect=RuntimeError("主路径强制失败注入")):
            with mock.patch.object(db_session, "AsyncSessionLocal",
                                   lambda: FakeSession()):
                asyncio.run(sched._shadow_job())
        assert "execute" in events  # 兜底 touch_heartbeat 走了 DB
        assert commit_count["n"] == 1  # 兜底 commit 恰好一次
    finally:
        sched.settings.M3_SHADOW_ENABLED = old_enabled
        sched.settings.M3_SHADOW_SYMBOLS = old_symbols


def test_scheduler_fallback_commit_failure_isolated_v1_legacy():
    """C（旧版 v1，已改名避免与第 16 节完整隔离版同名覆盖）：兜底 commit
    本身失败 → 不冒泡（隔离）。"""
    import app.tasks.alert_scheduler as sched
    import app.core.m3_shadow_runtime as rt
    import app.db.session as db_session
    from unittest import mock

    old_enabled = sched.settings.M3_SHADOW_ENABLED
    old_symbols = sched.settings.M3_SHADOW_SYMBOLS
    sched.settings.M3_SHADOW_ENABLED = True
    sched.settings.M3_SHADOW_SYMBOLS = "BINANCE:BTCUSDT"
    try:
        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def execute(self, *a, **k):
                return FakeResult()

            async def commit(self):
                raise RuntimeError("兜底 commit 失败注入")

        with mock.patch.object(rt, "run_production_cycle",
                               side_effect=RuntimeError("主路径失败")):
            with mock.patch.object(db_session, "AsyncSessionLocal",
                                   lambda: FakeSession()):
                asyncio.run(sched._shadow_job())  # 不冒泡
    finally:
        sched.settings.M3_SHADOW_ENABLED = old_enabled
        sched.settings.M3_SHADOW_SYMBOLS = old_symbols


# ── D：before 必须精确传 end_ts（不是 end_ts+duration）──────────────────────

def test_loader_before_is_exact_end_ts():
    """D：production_market_loader 必须把 end_ts 精确传给 provider.before
    （before 是开盘时点上界）；保留 outputsize padding 与 close_time 二次过滤。"""
    import app.core.daily_report_sources as drs_mod
    from app.core.m3_shadow_runtime import production_market_loader
    from unittest import mock

    captured = {}

    class FakeSources:
        def __init__(self) -> None:
            pass

        async def get_candles(self, symbol, outputsize=None, before=None, timeframe=None):
            captured["before"] = before
            captured["outputsize"] = outputsize
            captured["timeframe"] = timeframe
            dur = TIMEFRAME_DURATIONS[timeframe]
            # 返回截至 before 之前的 bars（含未来追加：before 之后的行）
            rows = []
            t = before - 800 * dur
            for i in range(800):
                close = 100.0 + i
                rows.append({
                    "time": t + i * dur, "open": close, "high": close + 1,
                    "low": close - 1, "close": close, "volume": 1000,
                })
            return rows

    end_ts = AS_OF
    with mock.patch.object(drs_mod, "DailyReportSources", FakeSources):
        out = asyncio.run(production_market_loader("BINANCE:BTCUSDT", "4h", end_ts, 700))
    assert captured["before"] == end_ts  # 精确 end_ts，不是 end_ts+duration
    assert captured["outputsize"] >= 700
    dur4 = TIMEFRAME_DURATIONS["4h"]
    assert all(b["time"] + dur4 <= end_ts for b in out)  # 未来被二次过滤
    assert len(out) <= 700


# ── G：from_settings 不吞 0 / 不静默绕过非法配置 ─────────────────────────────

def test_from_settings_zero_max_bars_rejected():
    """G：M3_SHADOW_MAX_BARS=0 必须抛 ValueError，不能被 or 700 静默替换。"""
    from app.core.config import Settings
    cfg = Settings(M3_SHADOW_ENABLED=True, M3_SHADOW_SYMBOLS="BINANCE:BTCUSDT",
                   M3_SHADOW_MAX_BARS=0)
    with pytest.raises(ValueError):
        ShadowCore.from_settings(cfg)


def test_from_settings_empty_timeframe_rejected():
    """G：M3_SHADOW_TIMEFRAME 空串不得静默改 4h（非法配置不绕过校验）。"""
    from app.core.config import Settings
    cfg = Settings(M3_SHADOW_ENABLED=True, M3_SHADOW_SYMBOLS="BINANCE:BTCUSDT",
                   M3_SHADOW_TIMEFRAME="")
    with pytest.raises(ValueError):
        ShadowCore.from_settings(cfg)


# ── H：缺数据 backoff（不热循环）+ >limit 多轮不饿死后排 ────────────────────

def test_missing_data_sets_backoff_not_hot_loop():
    """H：5 边界到期但数据缺根（未成熟）→ 设置 next_retry_at（backoff），
    backoff 内不重试（不每分钟热循环）；成功推进后清空。"""
    core0 = core(label_retry_delay_seconds=900)
    src = make_source()
    thin = dict(_BARS)
    thin["4h"] = make_bars(EVENT_IDX + 1, STEP4)
    loader_thin, calls1 = make_loader_v2(thin)
    result1, repo = run_cycle_once(core0, [src], W5 + 60, loader=loader_thin)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_PENDING and rec.maturity_window is None
    assert rec.next_retry_at == W5 + 60 + 900  # backoff 已设
    n_calls_after_first = len(calls1)
    # backoff 内：不再取数/label（热循环停止）
    result2, _ = run_cycle_once(core0, [src], W5 + 60 + 100, repo=repo, loader=loader_thin)
    assert result2["labeled"] == 0
    calls_after_2 = len(calls1)
    assert calls_after_2 == n_calls_after_first  # 未新增取数
    # backoff 到期 + 数据到齐 → 推进成功并清空 backoff
    loader_full, _ = make_loader_v2()
    result3, _ = run_cycle_once(core0, [src], W5 + 60 + 901, repo=repo, loader=loader_full)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_LABELED and rec.maturity_window == 5
    assert rec.next_retry_at is None  # 成功清空


def test_label_starvation_does_not_happen_over_limit():
    """H：分页公平性——先摄入（W5 到期前，不 label），到期后用 thin loader +
    label_limit=10 连跑三轮：每轮实际处理 10 条、三轮互不相交、合计覆盖 30 条。

    严格性：初次摄入（W5-60）不得直接完成标签（30 条全部 pending、无 label）；
    W5 到期后每轮 label_limit=10 + 缺数据 backoff 保证分页前进——已处理者
    下一轮被 backoff 排除，后排逐轮被处理，无人饿死、无人重复。
    """
    core0 = core(label_limit=10, label_retry_delay_seconds=900)
    ets = ["ma_breakout", "macd_divergence", "structure_break"]
    sts = ["ma", "macd", "structure"]
    srcs = [make_source(dedupe_key=f"s{i:02d}", direction="long",
                        strategy=sts[i % 3], event_type=ets[i % 3])
            for i in range(30)]
    thin = dict(_BARS)
    thin["4h"] = make_bars(EVENT_IDX + 1, STEP4)  # 5 窗口缺根（label 永不成熟）
    loader_thin, _ = make_loader_v2(thin)
    all_keys = {f"s{i:02d}" for i in range(30)}

    # 1) W5 到期前摄入：30 条全部 pending，不得直接完成标签
    result0, repo = run_cycle_once(core0, srcs, W5 - 60, loader=loader_thin)
    assert result0["ingested"] == 30 and result0["labeled"] == 0
    for i in range(30):
        rec = asyncio.run(repo.get_by_key(f"s{i:02d}"))
        assert rec is not None and rec.status == STATUS_PENDING
        assert rec.label_json is None and rec.label_as_of is None

    # 2) W5 到期 + thin loader + label_limit=10：连跑三轮（backoff 均未到期，
    #    每轮只可能处理 10 个新 key）
    per_round: list[set[str]] = []
    seen: set[str] = set()
    for t in (W5 + 60, W5 + 60 + 100, W5 + 60 + 200):
        result, _ = run_cycle_once(core0, srcs, t, repo=repo, loader=loader_thin)
        assert result["labeled"] == 10
        now_seen = {
            f"s{i:02d}" for i in range(30)
            if (r := asyncio.run(repo.get_by_key(f"s{i:02d}"))) is not None
            and r.label_json is not None
        }
        per_round.append(now_seen - seen)
        seen = now_seen

    # 3) 三轮各 10 条、互不相交、合计覆盖全部 30 条（分页公平性）
    a, b, c = per_round
    assert len(a) == 10 and len(b) == 10 and len(c) == 10
    assert a.isdisjoint(b) and a.isdisjoint(c) and b.isdisjoint(c)
    assert a | b | c == all_keys
    # 已处理者均为 pending censored（thin 缺根，未虚报成熟）且进入 backoff
    for key in seen:
        r = asyncio.run(repo.get_by_key(key))
        assert r.status == STATUS_PENDING and r.maturity_window is None
        assert r.next_retry_at is not None and r.next_retry_at > W5 + 60


# ── F：状态一致性（validate_row_content 交叉验证）────────────────────────────

def test_readback_cross_validates_label_digest_asof_maturity():
    """F：validate_row_content 交叉验证 label 与行字段一致：
    label.decision_digest == row.decision_digest、label_as_of、成熟最大值、
    reasons 与 decision reason_codes/explanations 一致；交换他行 label 也拒绝。"""
    from app.core.m3_shadow_runtime import validate_row_content, ShadowRecord
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    validate_row_content(rec)  # 合法
    lab = json.loads(rec.label_json)
    # label_as_of 不匹配 → 拒
    rec.label_as_of = lab["label_as_of"] + 1
    with pytest.raises(ValueError):
        validate_row_content(rec)
    rec.label_as_of = lab["label_as_of"]
    # maturity 不匹配 → 拒
    rec.maturity_window = 40
    with pytest.raises(ValueError):
        validate_row_content(rec)
    rec.maturity_window = lab["windows"][-1]["window"] if lab["windows"][-1]["matured"] else 5
    # label digest 不匹配（交换他行 label）→ 拒
    result2, repo2 = run_cycle_once(core(), [make_source(dedupe_key="k2",
                                                         event_type="macd_divergence")],
                                    W5 + 60, loader=make_loader_v2()[0])
    other = asyncio.run(repo2.get_by_key("k2"))
    rec.label_json = other.label_json
    with pytest.raises(ValueError):
        validate_row_content(rec)
    rec.label_json = lab_json_orig = json.dumps(lab, sort_keys=True)
    # 非 canonical 字符串 → 拒
    rec.label_json = json.dumps(lab)  # 无 sort_keys → 非 canonical
    if rec.label_json == lab_json_orig:
        rec.label_json = rec.label_json.replace('"', "'", 1)  # 强制非 canonical
    with pytest.raises(ValueError):
        validate_row_content(rec)


def test_readback_cross_validates_reasons():
    """F：reasons 必须与 decision 的 reason_codes/explanations 完全一致。"""
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    rec.reasons = canonical_json({"codes": ["ctx_ok"], "explanations": ["伪造"]})
    with pytest.raises(ValueError):
        validate_row_content(rec)


def test_status_consistency_violations_rejected():
    """F：状态机一致性——rejected 必须 eligible=false 且无 label；
    pending/labeled/completed 必须 eligible=true；labeled maturity<40；
    completed maturity=40；非 failed 无 attempts/last_error。"""
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    # rejected + eligible=true → 拒
    rec.status = STATUS_REJECTED
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # completed + maturity != 40 → 拒
    rec.status = STATUS_COMPLETED
    rec.eligible = True
    rec.maturity_window = 5
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # labeled + maturity=40 → 拒
    rec.status = STATUS_LABELED
    rec.maturity_window = 40
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # 非 failed 带 last_error → 拒
    rec.maturity_window = 5
    rec.last_error = "boom"
    with pytest.raises(ValueError):
        validate_row_content(rec)


# ═══════════════════════════════════════════════════════════════════════════════
#  16. 第二轮主审补充反例（任务书 1–4/6/8 定向；先红后绿）
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1：label_bound 三层一致（ShadowEventRow / validate_row_content）──────────

def test_shadow_event_row_pending_censored_label_legal():
    """1：pending censored（label_as_of 有、maturity NULL）在 schema 层合法。"""
    from app.schemas.m3_shadow_runtime import ShadowEventRow
    row = ShadowEventRow(
        id=1, source_dedupe_key="k1", source_event_id=1, symbol="BINANCE:BTCUSDT",
        timeframe="4h", signal_time=SIG, decision_digest="0" * 16, eligible=True,
        status=STATUS_PENDING, attempts=0, label_as_of=W5, maturity_window=None,
    )
    assert row.status == STATUS_PENDING and row.maturity_window is None


def test_shadow_event_row_maturity_requires_label_as_of():
    """1：maturity 非空而 label_as_of 为空 → schema 层拒绝。"""
    import pydantic
    from app.schemas.m3_shadow_runtime import ShadowEventRow
    with pytest.raises(pydantic.ValidationError):
        ShadowEventRow(
            id=1, source_dedupe_key="k1", source_event_id=1, symbol="BINANCE:BTCUSDT",
            timeframe="4h", signal_time=SIG, decision_digest="0" * 16, eligible=True,
            status=STATUS_LABELED, attempts=0, maturity_window=5, label_as_of=None,
        )


def test_validate_row_content_pending_censored_label_legal():
    """1：validate_row_content 接受 pending censored（真实 thin 数据产生的行）。"""
    from app.core.m3_shadow_runtime import validate_row_content
    src = make_source()
    thin = dict(_BARS)
    thin["4h"] = make_bars(EVENT_IDX + 1, STEP4)
    loader, _ = make_loader_v2(thin)
    result, repo = run_cycle_once(core(), [src], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_PENDING and rec.maturity_window is None
    assert rec.label_json is not None and rec.label_as_of == W5
    validate_row_content(rec)  # 不抛


def test_validate_row_content_label_bound_violations():
    """1：label_bound 关键非法行逐一拒绝（非仅 Memory 行为，validate 层）。"""
    import copy
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    base = asyncio.run(repo.get_by_key("k1"))
    # maturity 非空 + label_json 空 → 拒
    rec = copy.copy(base)
    rec.label_json = None
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # label_json 非空 + label_as_of 空 → 拒
    rec = copy.copy(base)
    rec.label_as_of = None
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # label_json 非空 + maturity 空 + 非 pending（labeled）→ 拒
    rec = copy.copy(base)
    rec.maturity_window = None
    with pytest.raises(ValueError):
        validate_row_content(rec)


# ── 2：Sql list_label_due 必须选 failed/label + next_label_at NULL（backoff 后）─

def test_list_label_due_sql_covers_failed_label_null_next_label():
    """2：Sql list_label_due SQL 结构必须覆盖 failed/label + next_label_at IS NULL
    （backoff 到期后按 W5 边界重试）、next_retry_at backoff、新鲜优先排序。"""
    from app.core.m3_shadow_runtime import SqlShadowRepo, list_label_due_stmt
    repo = SqlShadowRepo(session=object(), core=core())
    stmt = list_label_due_stmt(now=W5 + 60, limit=7, max_attempts=repo._max_attempts())
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    upper = sql.upper()
    assert "FAILED" in upper and "NEXT_LABEL_AT" in upper
    assert "IS NULL" in upper            # next_label_at IS NULL 分支（首次 W5 异常行）
    assert "IS NOT NULL" in upper        # next_label_at IS NOT NULL 分支
    assert "NEXT_RETRY_AT" in upper      # backoff 条件
    assert "ORDER BY" in upper           # 公平轮转排序
    # next_retry_at 排序出现（新鲜优先 + 最早到期优先）
    assert upper.count("NEXT_RETRY_AT") >= 3


def test_failed_label_null_next_label_sql_condition():
    """B（SQL 级强化）：list_label_due 的 due 分支必须**精确锁定**
    status=failed、failure_stage=label、next_label_at IS NULL、W5 已成熟
    （now >= signal_time + 86400）、next_retry_at 已到期、attempts<max；
    任一关键条件缺失，_assert_due_sql_branch 必须失败（证明测试能抓缺陷，
    不再只测 max_attempts）。"""
    from app.core.m3_shadow_runtime import (
        WINDOW_BOUNDARY_OFFSETS,
        SqlShadowRepo,
        list_label_due_stmt,
    )

    def _assert_due_sql_branch(sql: str) -> None:
        upper = sql.upper()
        # status=failed 分支（list 含 pending/labeled/failed 全状态）
        assert "FAILED" in upper and "PENDING" in upper and "LABELED" in upper
        # failure_stage=label（failed 才允许的阶段；W5 异常行重试路径）
        assert "FAILURE_STAGE" in upper and "'LABEL'" in upper
        # next_label_at IS NULL（首次 W5 异常行）与 IS NOT NULL（labeled 分支）并存
        assert "NEXT_LABEL_AT IS NULL" in upper
        assert "NEXT_LABEL_AT IS NOT NULL" in upper
        # W5 已成熟：now >= signal_time + 6*14400
        assert f"SIGNAL_TIME + {WINDOW_BOUNDARY_OFFSETS[5]}" in upper
        # next_retry_at 已到期：IS NULL OR <= now（backoff 尊重）
        assert "NEXT_RETRY_AT IS NULL" in upper
        assert "NEXT_RETRY_AT <=" in upper
        # attempts < max（来自 core，不硬编码）
        assert re.search(r"ATTEMPTS < \d+", upper)
        # 公平轮转排序
        assert "ORDER BY" in upper

    repo = SqlShadowRepo(session=object(), core=core())
    stmt = list_label_due_stmt(now=W5 + 60, limit=10, max_attempts=repo._max_attempts())
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    _assert_due_sql_branch(sql)  # 完整 SQL 必须通过

    # 移除任一关键条件 → _assert_due_sql_branch 必须失败（缺陷可被抓）
    upper = sql.upper()
    broken = {
        "status=failed": upper.replace("FAILED", ""),
        "failure_stage=label": upper.replace("FAILURE_STAGE = 'LABEL'", ""),
        "next_label_at IS NULL": upper.replace("NEXT_LABEL_AT IS NULL", ""),
        "W5 已成熟(+86400)": upper.replace(
            f"SIGNAL_TIME + {WINDOW_BOUNDARY_OFFSETS[5]}", "SIGNAL_TIME"),
        "next_retry_at 到期": upper.replace("NEXT_RETRY_AT IS NULL", "")
        .replace("NEXT_RETRY_AT <=", "NEXT_RETRY_AT >"),
        "attempts<max": re.sub(r"ATTEMPTS < \d+", "ATTEMPTS > 0", upper),
    }
    for name, broken_sql in broken.items():
        with pytest.raises(AssertionError):
            _assert_due_sql_branch(broken_sql)

    # max_attempts 来自 core（不硬编码）——补充但非唯一断言
    core3 = core(max_attempts=3)
    stmt3 = list_label_due_stmt(now=W5 + 60, limit=10, max_attempts=core3.max_attempts)
    sql3 = str(stmt3.compile(compile_kwargs={"literal_binds": True}))
    assert re.search(r"ATTEMPTS < 3", sql3.upper())


# ── 3：scheduler 兜底 commit 完全隔离（不碰真实 DB/网络；settings 自动还原）──

def test_scheduler_fallback_commit_exactly_once_isolated():
    """C：主路径强制抛异常 → 兜底 touch_heartbeat 后 commit 恰好一次；
    patch runtime.run_production_cycle（强制抛）+ patch sched.AsyncSessionLocal
    与 app.db.session.AsyncSessionLocal（兜底 import 的同一模块）为可观测 fake，
    任何路径都不产生真实 session/连接；settings 用 try/finally 还原。"""
    import app.tasks.alert_scheduler as sched
    import app.core.m3_shadow_runtime as rt
    import app.db.session as db_session
    from unittest import mock

    old_enabled = sched.settings.M3_SHADOW_ENABLED
    old_symbols = sched.settings.M3_SHADOW_SYMBOLS
    sched.settings.M3_SHADOW_ENABLED = True
    sched.settings.M3_SHADOW_SYMBOLS = "BINANCE:BTCUSDT"
    try:
        events: list[str] = []
        commit_count = {"n": 0}

        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def __aenter__(self):
                events.append("aenter")
                return self

            async def __aexit__(self, *a):
                return None

            async def execute(self, *a, **k):
                events.append("execute")
                return FakeResult()

            async def commit(self):
                commit_count["n"] += 1
                events.append("commit")

        with mock.patch.object(rt, "run_production_cycle",
                               side_effect=RuntimeError("主路径强制失败注入")):
            with mock.patch.object(sched, "AsyncSessionLocal", lambda: FakeSession()):
                with mock.patch.object(db_session, "AsyncSessionLocal", lambda: FakeSession()):
                    asyncio.run(sched._shadow_job())
        assert "aenter" in events          # 主路径 session 已进入（fake，非真实）
        assert events.count("aenter") >= 2  # 主路径 + 兜底各一个 fake session
        assert "execute" in events          # 兜底 touch_heartbeat 走了 DB（fake）
        assert commit_count["n"] == 1       # 兜底 commit 恰好一次
        assert "commit" in events
    finally:
        sched.settings.M3_SHADOW_ENABLED = old_enabled
        sched.settings.M3_SHADOW_SYMBOLS = old_symbols


def test_scheduler_fallback_commit_failure_isolated():
    """C：兜底 commit 本身失败 → 不冒泡（完全隔离；settings 自动还原）。"""
    import app.tasks.alert_scheduler as sched
    import app.core.m3_shadow_runtime as rt
    import app.db.session as db_session
    from unittest import mock

    old_enabled = sched.settings.M3_SHADOW_ENABLED
    old_symbols = sched.settings.M3_SHADOW_SYMBOLS
    sched.settings.M3_SHADOW_ENABLED = True
    sched.settings.M3_SHADOW_SYMBOLS = "BINANCE:BTCUSDT"
    try:
        class FakeResult:
            def scalar_one_or_none(self):
                return None

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def execute(self, *a, **k):
                return FakeResult()

            async def commit(self):
                raise RuntimeError("兜底 commit 失败注入")

        with mock.patch.object(rt, "run_production_cycle",
                               side_effect=RuntimeError("主路径失败")):
            with mock.patch.object(sched, "AsyncSessionLocal", lambda: FakeSession()):
                with mock.patch.object(db_session, "AsyncSessionLocal", lambda: FakeSession()):
                    asyncio.run(sched._shadow_job())  # 不冒泡
    finally:
        sched.settings.M3_SHADOW_ENABLED = old_enabled
        sched.settings.M3_SHADOW_SYMBOLS = old_symbols


# ── 4：before 精确 end_ts + 未来追加不变 ─────────────────────────────────────

def test_loader_before_exact_end_ts_and_future_append_unchanged():
    """D：production_market_loader 必须把 end_ts 精确传给 provider.before
    （不得 end_ts+duration）；provider 返回未来追加 bar 必须被二次过滤（结果不变）。"""
    import app.core.daily_report_sources as drs_mod
    from app.core.m3_shadow_runtime import production_market_loader
    from unittest import mock

    captured = {}

    class FakeSources:
        def __init__(self) -> None:
            pass

        async def get_candles(self, symbol, outputsize=None, before=None, timeframe=None):
            captured["before"] = before
            captured["outputsize"] = outputsize
            dur = TIMEFRAME_DURATIONS[timeframe]
            rows = []
            # 截至 before 的已收盘 bar（部分 close 恰在 end_ts 前）
            t0 = before - 720 * dur
            for i in range(720):
                close = 100.0 + i
                rows.append({
                    "time": t0 + i * dur, "open": close, "high": close + 1,
                    "low": close - 1, "close": close, "volume": 1000,
                })
            # 未来追加：before 之后的开盘 bar（close 更晚，必须被过滤）
            for i in range(50):
                rows.append({
                    "time": before + i * dur, "open": 200.0 + i, "high": 201 + i,
                    "low": 199 + i, "close": 200.5 + i, "volume": 999,
                })
            return rows

    end_ts = AS_OF
    dur4 = TIMEFRAME_DURATIONS["4h"]
    with mock.patch.object(drs_mod, "DailyReportSources", FakeSources):
        out = asyncio.run(production_market_loader("BINANCE:BTCUSDT", "4h", end_ts, 700))
    assert captured["before"] == end_ts  # 精确 end_ts，不是 end_ts+duration
    assert captured["outputsize"] >= 700  # padding 保留
    assert len(out) <= 700
    assert all(b["time"] + dur4 <= end_ts for b in out)  # 未来/边界外被过滤
    assert all(b["time"] < end_ts for b in out)  # 未来追加（time>=end_ts）绝不进入


# ── 6：状态一致性（validate_row_content 关键非法行逐一；ShadowEventRow 同层）─

def test_shadow_event_row_state_consistency_layers():
    """6：ShadowEventRow（schema 层）与 DB CHECK / validate_row_content 同语义。"""
    import pydantic
    from app.schemas.m3_shadow_runtime import ShadowEventRow

    def make(status="pending", eligible=True, digest="0" * 16, failure_stage=None,
             attempts=0, last_error=None, next_retry_at=None, label_as_of=None,
             maturity_window=None):
        return ShadowEventRow(
            id=1, source_dedupe_key="k1", source_event_id=1, symbol="BINANCE:BTCUSDT",
            timeframe="4h", signal_time=SIG, decision_digest=digest, eligible=eligible,
            status=status, failure_stage=failure_stage, attempts=attempts,
            next_retry_at=next_retry_at, last_error=last_error, label_as_of=label_as_of,
            maturity_window=maturity_window,
        )

    make()  # pending eligible=true 合法
    make(status="pending", label_as_of=W5, maturity_window=None)  # pending censored 合法
    with pytest.raises(pydantic.ValidationError):
        make(status="pending", eligible=False)  # pending 必须 eligible=true
    with pytest.raises(pydantic.ValidationError):
        make(status="rejected", eligible=True)  # rejected 必须 eligible=false
    with pytest.raises(pydantic.ValidationError):
        make(status="labeled", eligible=False)  # labeled 必须 eligible=true
    with pytest.raises(pydantic.ValidationError):
        make(status="labeled", label_as_of=W5, maturity_window=40)  # labeled maturity<40
    with pytest.raises(pydantic.ValidationError):
        make(status="completed", label_as_of=W5, maturity_window=5)  # completed=40
    with pytest.raises(pydantic.ValidationError):
        make(status="labeled", label_as_of=W5, maturity_window=5, attempts=1)  # 非 failed attempts=0
    with pytest.raises(pydantic.ValidationError):
        make(status="labeled", label_as_of=W5, maturity_window=5, last_error="boom")  # 非 failed error 空
    with pytest.raises(pydantic.ValidationError):
        make(status="completed", label_as_of=W5, maturity_window=40, next_retry_at=W5)  # completed 无 backoff
    with pytest.raises(pydantic.ValidationError):
        make(status="rejected", eligible=False, next_retry_at=W5)  # rejected 无 backoff
    # failed/decision：digest/eligible 必须空；failed/label 必须决策完整且 eligible=true
    # （failed 三层契约：attempts>=1、next_retry_at 非空、last_error 非空白）
    make(status="failed", failure_stage="decision", digest=None, eligible=None,
         attempts=1, next_retry_at=W5 + 60, last_error="boom")
    with pytest.raises(pydantic.ValidationError):
        make(status="failed", failure_stage="decision", digest="0" * 16, eligible=None,
             attempts=1, next_retry_at=W5 + 60, last_error="boom")
    make(status="failed", failure_stage="label", eligible=True,
         attempts=1, next_retry_at=W5 + 60, last_error="boom")  # failed/label 合法


def test_state_consistency_decision_failed_must_have_empty_decision_and_label():
    """6：failed/decision 要求 decision 五字段全 NULL 且 label 空。"""
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    # 改成 failed/decision 但 decision 字段残留 → 拒
    rec.status = STATUS_FAILED
    rec.failure_stage = "decision"
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # decision 全空但带 label → 拒
    rec.decision_json = None
    rec.decision_digest = None
    rec.eligible = None
    rec.reasons = None
    rec.config_hash = None
    rec.label_json = "{}"
    rec.label_as_of = W5
    rec.maturity_window = None
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # 干净的 failed/decision → 合法
    rec.label_json = None
    rec.label_as_of = None
    rec.attempts = 1
    rec.next_retry_at = W5 + 60
    rec.last_error = "boom"
    validate_row_content(rec)


def test_state_consistency_non_failed_retry_fields():
    """6：非 failed attempts=0、error 空；rejected/completed 不得有 next_retry_at。"""
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    rec.attempts = 1
    with pytest.raises(ValueError):
        validate_row_content(rec)
    rec.attempts = 0
    # completed：maturity=40 且 next_retry_at 非空 → 拒
    result2, repo2 = run_cycle_once(core(), [make_source(dedupe_key="c1")], W40 + 60, loader=loader)
    rec2 = asyncio.run(repo2.get_by_key("c1"))
    assert rec2.status == STATUS_COMPLETED and rec2.maturity_window == 40
    rec2.next_retry_at = W40
    with pytest.raises(ValueError):
        validate_row_content(rec2)
    rec2.next_retry_at = None
    validate_row_content(rec2)
    # rejected：eligible=false 且 next_retry_at 非空 → 拒
    src_rej = make_source(dedupe_key="r1")
    src_rej = SourceEvent(
        dedupe_key="r1", id=10, symbol=src_rej.symbol, timeframe=src_rej.timeframe,
        strategy=src_rej.strategy, event_type=src_rej.event_type, direction=src_rej.direction,
        severity=src_rej.severity, signal_time=src_rej.signal_time,
        reference_price=src_rej.reference_price,
        payload={**src_rej.payload, "timeframe": "1h"},
        created_at_epoch=src_rej.created_at_epoch,
    )
    result3, repo3 = run_cycle_once(core(), [src_rej], W5 + 60, loader=loader)
    rec3 = asyncio.run(repo3.get_by_key("r1"))
    assert rec3.status == STATUS_REJECTED and rec3.eligible is False
    rec3.next_retry_at = W5
    with pytest.raises(ValueError):
        validate_row_content(rec3)
    rec3.next_retry_at = None
    validate_row_content(rec3)


def test_state_consistency_eligible_and_status():
    """6：rejected 必须 eligible=false 且无 label；pending/labeled/completed/
    failed-label 必须 eligible=true；labeled 真实 maturity<40。"""
    import copy
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_LABELED and rec.maturity_window == 5
    # labeled + eligible=false → 拒
    rec = copy.copy(rec)
    rec.eligible = False
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # completed + eligible=false → 拒
    result2, repo2 = run_cycle_once(core(), [make_source(dedupe_key="c1")], W40 + 60, loader=loader)
    rec2 = asyncio.run(repo2.get_by_key("c1"))
    rec2 = copy.copy(rec2)
    rec2.eligible = False
    with pytest.raises(ValueError):
        validate_row_content(rec2)
    # pending + eligible=false → 拒（用真实 censored 行）
    thin = dict(_BARS)
    thin["4h"] = make_bars(EVENT_IDX + 1, STEP4)
    loader_thin, _ = make_loader_v2(thin)
    result3, repo3 = run_cycle_once(core(), [make_source(dedupe_key="p1")], W5 + 60, loader=loader_thin)
    rec3 = asyncio.run(repo3.get_by_key("p1"))
    assert rec3.status == STATUS_PENDING and rec3.maturity_window is None
    rec3 = copy.copy(rec3)
    rec3.eligible = False
    with pytest.raises(ValueError):
        validate_row_content(rec3)
    # 真实 pending censored（eligible=true）→ 合法
    rec3.eligible = True
    validate_row_content(rec3)


def test_validate_row_content_decision_json_canonical():
    """6：decision_json 非 canonical 字符串必须拒绝（canonical JSON 契约）。"""
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    d = json.loads(rec.decision_json)
    rec.decision_json = json.dumps(d)  # 无 sort_keys → 非 canonical
    with pytest.raises(ValueError):
        validate_row_content(rec)


# ═══════════════════════════════════════════════════════════════════════════════
#  17. M3B-A 最后四项严格性整改（2026-08-14）
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1：failed 三层契约（validate_row_content / ShadowEventRow / DB CHECK）────

def test_validate_row_content_failed_three_layer_contract():
    """任何 status=failed 必须 attempts>=1、last_error 非空白（strip）、
    next_retry_at 非空；合法 failed 通过、任一违反拒绝（validate 层）。"""
    import copy
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2(fail_symbol="BINANCE:BTCUSDT", fail_decision=True)
    result, repo = run_cycle_once(
        core(), [make_source(dedupe_key="bad")], W5 + 60, loader=loader)
    assert result["decision_failed"] == 1
    base = asyncio.run(repo.get_by_key("bad"))
    assert base.status == STATUS_FAILED and base.attempts == 1
    assert base.last_error and base.last_error.strip()
    assert base.next_retry_at is not None
    validate_row_content(base)  # 合法 failed 通过
    # attempts=0 → 拒
    rec = copy.copy(base)
    rec.attempts = 0
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # next_retry_at 空 → 拒
    rec = copy.copy(base)
    rec.next_retry_at = None
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # last_error 空 → 拒
    rec = copy.copy(base)
    rec.last_error = None
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # last_error 全空白（btrim 语义）→ 拒
    rec = copy.copy(base)
    rec.last_error = "   "
    with pytest.raises(ValueError):
        validate_row_content(rec)


def test_shadow_event_row_failed_three_layer_contract():
    """failed 三层契约：ShadowEventRow（schema 层）与 DB CHECK/validate 同语义。"""
    import pydantic
    from app.schemas.m3_shadow_runtime import ShadowEventRow

    def make(**kw):
        base = dict(
            id=1, source_dedupe_key="k1", source_event_id=1, symbol="BINANCE:BTCUSDT",
            timeframe="4h", signal_time=SIG, status="failed", failure_stage="decision",
            attempts=1, next_retry_at=W5 + 60, last_error="boom",
        )
        base.update(kw)
        return ShadowEventRow(**base)

    make()  # 合法 failed：attempts>=1 + next_retry_at 非空 + last_error 非空白
    with pytest.raises(pydantic.ValidationError):
        make(attempts=0)
    with pytest.raises(pydantic.ValidationError):
        make(next_retry_at=None)
    with pytest.raises(pydantic.ValidationError):
        make(last_error=None)
    with pytest.raises(pydantic.ValidationError):
        make(last_error="   ")  # 全空白（btrim 语义）


def test_label_failure_blank_error_sanitize_fallback():
    """mark_failed 错误文本必须 sanitize_error(exc) or 安全兜底：异常 str 为
    全空白（sanitize_error → None）时不得写入空 last_error（否则违反 failed
    三层契约），必须落兜底文本且读回三层契约通过。"""
    from app.core.m3_shadow_runtime import validate_row_content
    core0 = core(retry_delay_seconds=300)
    src = make_source(dedupe_key="k1")
    state = {"count": 0}
    bars = _BARS

    async def loader_blank(symbol, timeframe, end_ts, max_bars):
        state["count"] += 1
        if state["count"] == 4:  # decision 3 次取数后 = label 取数
            raise RuntimeError("   ")  # 全空白 → sanitize_error 返回 None
        dur = TIMEFRAME_DURATIONS[timeframe]
        return [b for b in bars[timeframe] if b["time"] + dur <= end_ts][-max_bars:]

    result, repo = run_cycle_once(core0, [src], W5 + 60, loader=loader_blank)
    assert result["failed"] == 1
    rec = asyncio.run(repo.get_by_key("k1"))
    assert rec.status == STATUS_FAILED and rec.failure_stage == "label"
    assert rec.last_error is not None and rec.last_error.strip()  # 兜底生效
    assert rec.attempts == 1 and rec.next_retry_at is not None
    validate_row_content(rec)  # 读回三层契约通过（get_by_key 已验，此处显式）


# ── 2：reasons canonical 契约（不得重新 canonicalize 后比较）────────────────

def test_readback_reasons_canonical_contract():
    """reasons 必须与期望 canonical 字符串**逐字节一致**：同内容但 key 顺序
    颠倒 / 缩进 / 分隔符不同的非规范序列化 → 拒绝（validate_row_content
    不得把输入重新 canonicalize 后比较）。"""
    from app.core.m3_shadow_runtime import validate_row_content
    loader, _ = make_loader_v2()
    result, repo = run_cycle_once(
        core(), [make_source(dedupe_key="k1")], W5 + 60, loader=loader)
    rec = asyncio.run(repo.get_by_key("k1"))
    d = json.loads(rec.decision_json)
    expected = canonical_json({
        "codes": d["reason_codes"],
        "explanations": d["explanations"],
    })
    assert rec.reasons == expected  # 正常写入即 canonical
    # 同内容：key 顺序颠倒（explanations 在前）——语义等价但字符串不同
    swapped = json.dumps(
        {"explanations": d["explanations"], "codes": d["reason_codes"]},
        sort_keys=False, ensure_ascii=False,
    )
    assert json.loads(swapped) == json.loads(expected)  # 同内容
    assert swapped != expected
    rec.reasons = swapped
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # 同内容：pretty 缩进序列化
    pretty = json.dumps(
        {"codes": d["reason_codes"], "explanations": d["explanations"]},
        sort_keys=True, indent=2, ensure_ascii=False,
    )
    assert json.loads(pretty) == json.loads(expected)
    rec.reasons = pretty
    with pytest.raises(ValueError):
        validate_row_content(rec)
    # 同内容：分隔符含空格（separators 不同）
    spaced = json.dumps(
        {"codes": d["reason_codes"], "explanations": d["explanations"]},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    assert json.loads(spaced) == json.loads(expected)
    rec.reasons = spaced
    with pytest.raises(ValueError):
        validate_row_content(rec)
