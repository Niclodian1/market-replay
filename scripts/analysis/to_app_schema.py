#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「分析用」的买点文件转成前端可导入的 chanlun.ann/2 格式。

前端 `annImportData()` 认的字段是 `decision` / `manual` / `features.time` / `source`；
只带 `time/entry/stop` 的文件导进去会变成「无 manual、无判定」的空记录，
在「我的买点」源里一条都看不见——所以这里补齐，同时保留原有分析字段。

用法：python3 scripts/analysis/to_app_schema.py [--check]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / "annotations-BTC-1D-manual.json"

# ManualEvidence.PATTERNS 里允许的值；没有区间锚点的一律降级成 free，否则再保存会报错
ALLOWED = {"free", "contraction", "range", "pullback", "rebound", "other"}


def convert(it: dict) -> dict:
    pattern = it.get("pattern") if it.get("pattern") in ALLOWED else "free"
    if pattern in ("range", "contraction") and not (it.get("rangeStart") and it.get("rangeEnd")):
        pattern = "free"
    out = dict(it)
    out.setdefault("source", "manual_buy_v1")
    out.setdefault("sourceName", "我的买点 · 不限指标")
    out["decision"] = "yes"
    out.setdefault("tag", "我的买点")
    out.setdefault("kind", "manual_buy")
    out.setdefault("reasons", [])
    out.setdefault("note", "")
    out.setdefault("features", {"time": int(it["ts"]), "price": float(it["entry"])})
    out.setdefault("manual", {
        "schema": "manual-buy/1",
        "reason": (it.get("reason") or "").strip(),
        "pattern": pattern,
        "rangeStart": "",
        "rangeEnd": "",
        "collectionMode": "historical_review",
        "selectedAt": "2026-11-03T00:00:00.000Z",
        "execution": {"basis": "selected_bar_close", "price": float(it["entry"])},
        "archived": False,
    })
    if it.get("reference"):
        out["manual"].setdefault("reference", it["reference"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只报告，不写文件")
    args = ap.parse_args()

    data = json.loads(FILE.read_text())
    items = [convert(x) for x in data["items"]]
    ready = [x for x in items if x.get("manual") and x.get("features", {}).get("time") and x.get("decision") == "yes"]
    print(f"共 {len(items)} 条，可被前端识别为买点的 {len(ready)} 条")
    if len(ready) != len(items):
        bad = [x["time"] for x in items if x not in ready]
        print("  缺字段:", bad[:10])
    if args.check:
        return

    data["schema"] = "chanlun.ann/2"
    data["analysisSchema"] = "manual-buy/1"
    data["unmarkedPolicy"] = "unknown"
    data["items"] = items
    data["count"] = len(items)
    FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"写出 {FILE.relative_to(ROOT)}（{len(items)} 条，含前端所需字段）")


if __name__ == "__main__":
    main()
