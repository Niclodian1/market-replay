#!/usr/bin/env python3
"""缠论引擎因果性审计：前缀重算 vs 全量按 ``confirmedT`` 过滤。

判定口径（引擎级，与 ``scripts/audit_causality.py`` 的指标层审计互补）：
- 前缀重算 ``analyze(bars[:T+1])`` = 「实盘在 T 时刻真能看到的东西」；
- 全量结果里取 ``confirmedT <= epochs[T]`` 的点 = 「历史注释按确认时间过滤」；
- 两者必须逐个相等：多出来的是**泄漏**（用了未来 K 线），少的是**漏报**。

审计数组：买卖点/背驰/盘整背驰（笔级 + 线段级，新笔链）。
同时打印 ``confirmedT - t`` 的确认延迟分布——延迟为 0 的点只允许是
「弱速率背驰」（定义即当根收盘入场）。

用法::

    python3 scripts/audit_chanlun_causality.py                     # 1day/4h/1h 默认切点
    python3 scripts/audit_chanlun_causality.py --tf 4h --cuts 8
    python3 scripts/audit_chanlun_causality.py --symbol ETH --tf 1day --cuts 4

有泄漏/漏报时返回非零退出码，可直接进 CI / nightly。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packages" / "chanlun-core"))

import pandas as pd  # noqa: E402

from chanlun_core import chanlun as engine  # noqa: E402

ARRAYS = (
    "buyPointsStrokeRaw",
    "sellPointsStrokeRaw",
    "buyPointsSegmentRaw",
    "sellPointsSegmentRaw",
    "beichiPointsRaw",
    "beichiPointsSegmentRaw",
    "panbeiPointsRaw",
    "panbeiPointsSegmentRaw",
)
TF_SECONDS = {"1day": 86_400, "4h": 14_400, "1h": 3_600}
DEFAULT_CUTS = {"1day": 8, "4h": 8, "1h": 2}


def _load(symbol: str, tf: str, data_dir: Path) -> tuple[list[dict], list[int]]:
    path = data_dir / f"{symbol}_USD_{tf}.parquet"
    if not path.exists():
        raise SystemExit(f"找不到行情文件: {path}")
    frame = pd.read_parquet(path).sort_values("time")
    bars = [
        {
            "idx": i,
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for i, row in enumerate(frame.itertuples())
    ]
    return bars, [int(t) for t in frame["time"].tolist()]


def _key(name: str, point: dict) -> tuple:
    return (
        name,
        point.get("k"),
        int(point["t"]),
        int(point.get("confirmedT") or 0),
        round(float(point["p"]), 4),
    )


def audit(symbol: str, tf: str, cuts: int, data_dir: Path) -> tuple[int, int]:
    bars, epochs = _load(symbol, tf, data_dir)
    n = len(bars)
    full = engine.analyze(bars, epochs, tf)

    points: dict[tuple, dict] = {}
    delays: dict[str, Counter] = {}
    zero_delay: list[tuple] = []
    for name in ARRAYS:
        for point in full.get(name) or []:
            points[_key(name, point)] = point
            lag = (int(point.get("confirmedT") or 0) - int(point["t"])) // TF_SECONDS[tf]
            delays.setdefault(name, Counter())[lag] += 1
            if lag <= 0:
                zero_delay.append((name, point.get("k"), point.get("trigger"), point.get("state")))

    print(f"\n### {symbol} {tf}: bars={n} 全量因果点={len(points)}", flush=True)
    for name in ARRAYS:
        if name in delays:
            print(f"    {name}: n={sum(delays[name].values())} 确认延迟(bar)={dict(sorted(delays[name].items()))}")
    if zero_delay:
        kinds = Counter((d[0], d[1]) for d in zero_delay)
        print(f"    延迟=0 的点 {len(zero_delay)} 个（定义即当根收盘入场）: {dict(kinds)}")

    leak_total = omit_total = 0
    for i in range(1, cuts + 1):
        cut = int(n * i / (cuts + 1))
        prefix = engine.analyze(bars[: cut + 1], epochs[: cut + 1], tf)
        got = {_key(name, p) for name in ARRAYS for p in (prefix.get(name) or [])}
        expect = {k for k, p in points.items() if int(p.get("confirmedT") or 0) <= epochs[cut]}
        leak, omit = got - expect, expect - got
        leak_total += len(leak)
        omit_total += len(omit)
        mark = "✗" if (leak or omit) else "✓"
        detail = ""
        if leak or omit:
            detail = f" 泄漏样例={sorted(leak)[:1]} 漏报样例={sorted(omit)[:1]}"
        print(
            f"  {mark} T={cut:>6} {str(pd.to_datetime(epochs[cut], unit='s'))[:16]}"
            f" 前缀点={len(got):>4} 应知点={len(expect):>4}"
            f" 泄漏={len(leak)} 漏报={len(omit)}{detail}",
            flush=True,
        )
    print(f"### {symbol} {tf} 合计: 泄漏={leak_total} 漏报={omit_total}", flush=True)
    return leak_total, omit_total


def main() -> int:
    parser = argparse.ArgumentParser(description="缠论引擎因果性审计")
    parser.add_argument("--symbol", default="BTC", help="行情文件前缀，默认 BTC")
    parser.add_argument("--tf", nargs="+", default=list(DEFAULT_CUTS), choices=list(TF_SECONDS))
    parser.add_argument("--cuts", type=int, default=0, help="每个周期的切点数，默认按周期取 8/8/2")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "data" / "crypto",
        help="行情 parquet 目录（默认 data/crypto）",
    )
    args = parser.parse_args()

    total_leak = total_omit = 0
    for tf in args.tf:
        leak, omit = audit(args.symbol, tf, args.cuts or DEFAULT_CUTS[tf], args.data_dir)
        total_leak += leak
        total_omit += omit
    print(f"\n汇总: 泄漏={total_leak} 漏报={total_omit}", flush=True)
    if total_leak or total_omit:
        print("✗ 因果性审计未通过", file=sys.stderr)
        return 1
    print("✓ 因果性审计通过（0 泄漏 / 0 漏报）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
