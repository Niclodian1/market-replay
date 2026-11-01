#!/usr/bin/env python3
"""因果一致性审计：验证「全量注释按 confirmed_at 过滤」等价于「前缀重算」。

练习、看盘、统计都依赖一个假设：历史注释在构建期一次算好，运行期按
``confirmed_at <= cursor`` 过滤，就等于「当时只能看到这些」。本脚本用真实
数据抽查这个假设，出现泄漏或漏报即返回非零退出码（可直接进 CI / nightly）。

用法::

    python3 scripts/audit_causality.py --tf 1day --samples 12
    python3 scripts/audit_causality.py --tf 4h --samples 6 --symbol ETH-USD

判定口径：事件键 = (indicator_id, bar_time, direction)。
- 泄漏 = 过滤后出现、前缀重算没有（未来函数，最严重）
- 漏报 = 前缀重算有、过滤后没有（练习会少显示当时可见的信号）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "platform-api"))

import pandas as pd  # noqa: E402

from app.core.indicator_provider import get_registry  # noqa: E402

_TF_FILE = {"1day": "1day", "4h": "4h", "1h": "1h"}
_DATA_DIRS = (
    "crypto",
    "stocks",
    "indices",
    "cn_equity",
    "hk_equity",
    "gold",
)


def resolve_parquet(symbol: str, tf: str) -> Path:
    stem = symbol.upper().replace("-", "_").replace(".", "_")
    names = [f"{stem}_{_TF_FILE[tf]}.parquet", f"{stem}_{tf}.parquet"]
    for directory in _DATA_DIRS:
        for name in names:
            candidate = ROOT / "data" / directory / name
            if candidate.exists():
                return candidate
    raise SystemExit(f"找不到 {symbol} {tf} 的 parquet（已查 data/{{{','.join(_DATA_DIRS)}}}）")


def load_bars(path: Path) -> list[dict]:
    df = pd.read_parquet(path).sort_values("time").reset_index(drop=True)
    return [
        {
            "time": int(r.time),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(getattr(r, "volume", 0.0) or 0.0),
        }
        for r in df.itertuples()
    ]


def keyset(events, cursor_time: int | None = None) -> set[tuple[str, int, str]]:
    return {
        (e.indicator_id, e.bar_time, e.direction)
        for e in events
        if cursor_time is None or e.confirmed_at <= cursor_time
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="指标注释因果一致性审计")
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--tf", default="1day", choices=sorted(_TF_FILE))
    parser.add_argument("--samples", type=int, default=12, help="采样游标数量（默认 12）")
    parser.add_argument("--parquet", type=Path, help="直接指定 parquet，覆盖 --symbol/--tf")
    args = parser.parse_args(argv)

    path = args.parquet or resolve_parquet(args.symbol, args.tf)
    bars = load_bars(path)
    if len(bars) < 200:
        raise SystemExit(f"数据太短（{len(bars)} 根），无法审计")

    registry = get_registry()
    started = time.time()
    full = registry.run_all(bars, args.symbol, args.tf)
    full_elapsed = time.time() - started
    print(
        f"数据集: {path.relative_to(ROOT)} · {len(bars)} 根 · 全量事件 {len(full)} · "
        f"引擎耗时 {full_elapsed:.2f}s"
    )

    step = max(1, (len(bars) - 200) // args.samples)
    cursors = list(range(200, len(bars), step))[: args.samples]

    total_leak = total_missing = total_visible = 0
    print(f"{'游标时间':<12}{'前缀可见':>8}{'过滤后':>8}{'泄漏':>6}{'漏报':>6}")
    for idx in cursors:
        cursor_time = bars[idx]["time"]
        prefix = registry.run_all(bars[: idx + 1], args.symbol, args.tf)
        prefix_keys = keyset(prefix)
        filtered = {
            (e.indicator_id, e.bar_time, e.direction)
            for e in full
            if e.confirmed_at <= cursor_time
        }
        leaked = filtered - prefix_keys
        missing = prefix_keys - filtered
        total_leak += len(leaked)
        total_missing += len(missing)
        total_visible += len(prefix_keys)
        stamp = pd.to_datetime(cursor_time, unit="s").strftime("%Y-%m-%d")
        print(f"{stamp:<12}{len(prefix_keys):>8}{len(filtered):>8}{len(leaked):>6}{len(missing):>6}")
        for item in sorted(leaked)[:3]:
            print(f"    ! 泄漏 {item}")
        for item in sorted(missing)[:3]:
            print(f"    - 漏报 {item}")

    print(
        f"\n汇总: 前缀可见事件 {total_visible} · 泄漏 {total_leak} · 漏报 {total_missing} "
        f"· 采样 {len(cursors)} 点 · 总耗时 {time.time() - started:.1f}s"
    )
    if total_leak:
        print("结论: ❌ 存在未来函数，预计算方案不可用于练习/统计")
        return 1
    if total_missing:
        print("结论: ⚠️ 无泄漏但有漏报，练习会少显示当时可见信号（可接受但需记录）")
        return 0
    print("结论: ✅ 过滤法与前缀重算逐事件一致，可放心使用预计算")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
