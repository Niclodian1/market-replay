"""MTF-BTC-V1 回测数据管线 CLI —— fetch / verify / run（2026-08-16）。

唯一产品：Binance USD-M **BTCUSDT**，4H klines，官方 data.binance.vision
（https://data.binance.vision/data/futures/um/...）。

命令：
  fetch   拉取 2020-01..2026-07 月度 zip + 2026-08-01..14 每日 zip；
          HTTP 非 200 只记录、绝不交 zip 解析；全部 zip 解析后按 time
          去重（相同内容幂等；同 time 内容冲突 → ValueError），排序后
          严格 14400 连续且全部已收盘校验；**首末必须精确匹配正式边界**
          EXPECTED_FIRST_TIME=2020-01-01T00:00:00Z /
          EXPECTED_LAST_TIME=2026-08-14T20:00:00Z（缺头/缺尾即使内部连续
          也拒绝；可传 expected_first_time/expected_last_time 显式覆盖，
          测试小样本必须显式给出其边界）。再聚合 1day/1week。
          输出确定性三份 gzip + source_manifest.json + MANIFEST.sha256
          （fetch_metadata.json 单独存放 fetched_at，不进清单）。
  verify  重算每个文件 sha256、解 gzip、重跑连续性 / 聚合一致性；
          任何篡改（gzip 字节 / 清单 / 聚合不一致）必然失败。
  run     先 verify（失败即拒绝）；verify 后仍校验产品/边界（verify 只验
          哈希/连续性/聚合，不校验语义）：source_manifest.product==BTCUSDT、
          interval==4h、raw 非空、raw 首末精确匹配 expected_first_time /
          expected_last_time（CLI 正式 run 不传覆盖 → 严格正式边界）、
          manifest raw_4h count/first/last 与 raw 一致，任一不符 ValueError；
          只读 raw_4h.json.gz（不联网）跑固定回测
          run_backtest_pipeline(raw, ["ma","structure","macd"], {},
          DEFAULT_MARKET_STATE_CONFIG)，写 events.csv / decisions.csv /
          summary.json（仍只从 events.csv 聚合）+ run_manifest.json +
          RUN_MANIFEST.sha256；manifest 确定性、无时钟/绝对路径。

fetch/verify 输出目录（--output-dir，默认 reports/mtf-btc-backtest-v1/data）：
  raw_4h.json.gz            确定性 gzip JSON（sort_keys / 紧凑 / mtime=0）
  derived_1day.json.gz      同 4H 聚合 1day
  derived_1week.json.gz     同 4H 聚合 1week
  source_manifest.json      确定性清单（每 URL/status/content_sha256、
                            requested_cutoff、首末 bar、count、各文件 sha256）
  MANIFEST.sha256           source_manifest.json 的 sha256（sha256sum 格式）
  fetch_metadata.json       fetched_at 等非确定性信息（不进清单）

run 输出目录（--output-dir）：events.csv / decisions.csv / summary.json /
run_manifest.json / RUN_MANIFEST.sha256（五份字节确定性，同输入一致）。

网络层复用到 app.core.mtf_historical_backtest 的 http_get /
fetch_binance_zip（HTTP 非 200 只记录、不读作 zip），纯函数
parse_binance_kline_csv / validate_closed_4h / aggregate_utc 原样使用。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from app.core.market_state import DEFAULT_MARKET_STATE_CONFIG
from app.core.mtf_historical_backtest import (
    FetchError,
    aggregate_utc,
    fetch_binance_zip,
    merge_4h_batches,
    read_gzip_json,
    run_backtest_pipeline,
    validate_closed_4h,
    write_gzip_json_deterministic,
)

PRODUCT = "BTCUSDT"
INTERVAL = "4h"
BASE_URL = "https://data.binance.vision/data/futures/um"
MONTH_START = (2020, 1)
MONTH_END = (2026, 7)
DAILY_START = (2026, 8, 1)
DAILY_END = (2026, 8, 14)
REQUESTED_CUTOFF = "2026-08-16T00:00:00Z"
SCHEMA = "mtf-btc-backtest-v1.source-manifest.v1"

# 正式数据边界（UTC epoch 秒）：2020-01-01T00:00:00Z .. 2026-08-14T20:00:00Z
EXPECTED_FIRST_TIME = 1_577_836_800  # 2020-01-01T00:00:00Z（首根 4H 开盘）
EXPECTED_LAST_TIME = 1_786_737_600  # 2026-08-14T20:00:00Z（末根 4H 开盘）
EXPECTED_FIRST_ISO = "2020-01-01T00:00:00Z"
EXPECTED_LAST_ISO = "2026-08-14T20:00:00Z"

RAW_4H = "raw_4h.json.gz"
DERIVED_1DAY = "derived_1day.json.gz"
DERIVED_1WEEK = "derived_1week.json.gz"
SOURCE_MANIFEST = "source_manifest.json"
SHA_MANIFEST = "MANIFEST.sha256"
FETCH_METADATA = "fetch_metadata.json"

# run 固定策略参数 / 产物
RUN_SCHEMA = "mtf-btc-backtest-v1.run-manifest.v1"
RUN_STRATEGIES = ["ma", "structure", "macd"]
RUN_PARAMETERS: dict[str, Any] = {}
RUN_OUTCOME_WINDOWS = [5, 10, 20, 40]
RUN_EVENTS = "events.csv"
RUN_DECISIONS = "decisions.csv"
RUN_SUMMARY = "summary.json"
RUN_MANIFEST = "run_manifest.json"
RUN_SHA = "RUN_MANIFEST.sha256"

DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[3] / "reports" / "mtf-btc-backtest-v1" / "data"
)


# ═══════════════════════════════════════════════════════════════════════════════
#  URL 生成（官方 data.binance.vision futures/um 4h）
# ═══════════════════════════════════════════════════════════════════════════════

def monthly_url(year: int, month: int) -> str:
    return (
        f"{BASE_URL}/monthly/klines/{PRODUCT}/{INTERVAL}/"
        f"{PRODUCT}-{INTERVAL}-{year:04d}-{month:02d}.zip"
    )


def daily_url(year: int, month: int, day: int) -> str:
    return (
        f"{BASE_URL}/daily/klines/{PRODUCT}/{INTERVAL}/"
        f"{PRODUCT}-{INTERVAL}-{year:04d}-{month:02d}-{day:02d}.zip"
    )


def all_urls() -> list[str]:
    """官方数据 URL 列表：月度 2020-01..2026-07 + 每日 2026-08-01..14。"""
    urls: list[str] = []
    y, m = MONTH_START
    while (y, m) <= MONTH_END:
        urls.append(monthly_url(y, m))
        m += 1
        if m > 12:
            y += 1
            m = 1
    day = datetime.date(*DAILY_START)
    end = datetime.date(*DAILY_END)
    while day <= end:
        urls.append(daily_url(day.year, day.month, day.day))
        day += datetime.timedelta(days=1)
    return urls


# ═══════════════════════════════════════════════════════════════════════════════
#  fetch
# ═══════════════════════════════════════════════════════════════════════════════

def collect_sources(
    urls: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """下载（默认全部）URL → (records, batches)。

    - urls=None → all_urls() 全量范围（生产默认）；传入时只拉给定 URL；
    - records：每 URL 一条确定性记录（url / http_status / ok；
      200 时含 content_sha256，非 200 时含 error），绝不携带原始 rows；
    - batches：HTTP 200 且 zip/CSV 解析成功的 4H 批次列表。
    - HTTP 非 200 只记录（error="HTTP xxx"），绝不把响应体交 zip 解析。
    - 网络级错误（连接/超时/证书）→ FetchError 上抛。
    """
    records: list[dict[str, Any]] = []
    batches: list[list[dict[str, Any]]] = []
    for url in (all_urls() if urls is None else urls):
        rec = fetch_binance_zip(url)
        record: dict[str, Any] = {
            "url": url,
            "http_status": rec["http_status"],
            "ok": rec["ok"],
        }
        if rec["ok"]:
            record["content_sha256"] = rec["content_sha256"]
            batches.append(rec["rows"])
        else:
            record["error"] = rec["error"]
        records.append(record)
    return records, batches


def _bar_slim(bar: dict[str, Any]) -> dict[str, Any]:
    return {k: bar[k] for k in ("time", "open", "high", "low", "close", "volume")}


def _fmt_utc(ts: int) -> str:
    return datetime.datetime.fromtimestamp(
        int(ts), tz=datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(
    output_dir: Path | str,
    urls: list[str] | None = None,
    *,
    expected_first_time: int = EXPECTED_FIRST_TIME,
    expected_last_time: int = EXPECTED_LAST_TIME,
) -> dict[str, Any]:
    """拉取（默认全部 URL）→ 去重/冲突/连续/闭合/首末边界校验 → 写确定性产物。

    urls=None → all_urls() 全量范围（生产默认）；传入时只拉给定 URL，
    清单 sources 与返回 source_urls 都反映实际传入的 URL。

    expected_first_time/expected_last_time：合并后首末 4H bar 开盘秒必须
    精确匹配（默认正式边界 2020-01-01T00:00:00Z .. 2026-08-14T20:00:00Z）。
    缺头/缺尾即使内部严格 14400 连续也必须 ValueError 拒绝（测试小样本
    必须显式传其边界）。

    可重复执行：同一数据源下覆盖生成字节级相同的三份 gzip、
    source_manifest.json 与 MANIFEST.sha256（fetch_metadata.json 除外，
    其 fetched_at 每次不同且不进清单）。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    records, batches = collect_sources(urls)
    # 去重（相同内容幂等；同 time 内容冲突 ValueError）、排序、
    # 严格 14400 连续 + 全部已收盘校验（任何缺口/未收盘 → ValueError）
    merged = merge_4h_batches(batches)
    # 正式边界：首末必须精确匹配（缺头/缺尾即使内部连续也拒绝）
    if not merged:
        raise ValueError(
            f"缺头缺尾：无任何 4H bar（期望首根 "
            f"{_fmt_utc(expected_first_time)}，末根 {_fmt_utc(expected_last_time)}）"
        )
    if int(merged[0]["time"]) != int(expected_first_time):
        raise ValueError(
            f"缺头：首根 {_fmt_utc(merged[0]['time'])} != 期望 "
            f"{_fmt_utc(expected_first_time)}（即使内部连续也拒绝）"
        )
    if int(merged[-1]["time"]) != int(expected_last_time):
        raise ValueError(
            f"缺尾：末根 {_fmt_utc(merged[-1]['time'])} != 期望 "
            f"{_fmt_utc(expected_last_time)}（即使内部连续也拒绝）"
        )

    raw_sha = write_gzip_json_deterministic(output_dir / RAW_4H, merged)
    day_bars = aggregate_utc(merged, "1day")
    day_sha = write_gzip_json_deterministic(output_dir / DERIVED_1DAY, day_bars)
    week_bars = aggregate_utc(merged, "1week")
    week_sha = write_gzip_json_deterministic(output_dir / DERIVED_1WEEK, week_bars)

    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "product": PRODUCT,
        "interval": INTERVAL,
        "requested_cutoff": REQUESTED_CUTOFF,
        "sources": records,
        "files": {
            RAW_4H: raw_sha,
            DERIVED_1DAY: day_sha,
            DERIVED_1WEEK: week_sha,
        },
        "raw_4h": {
            "count": len(merged),
            "first": _bar_slim(merged[0]) if merged else None,
            "last": _bar_slim(merged[-1]) if merged else None,
        },
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False) + "\n"
    ).encode("utf-8")
    (output_dir / SOURCE_MANIFEST).write_bytes(manifest_bytes)
    (output_dir / SHA_MANIFEST).write_bytes(
        (hashlib.sha256(manifest_bytes).hexdigest()
         + "  " + SOURCE_MANIFEST + "\n").encode("ascii")
    )
    # fetched_at 单独放 fetch_metadata.json，不进入确定性清单
    (output_dir / FETCH_METADATA).write_bytes(
        (
            json.dumps(
                {
                    "fetched_at": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(timespec="seconds"),
                    "product": PRODUCT,
                    "interval": INTERVAL,
                    "requested_cutoff": REQUESTED_CUTOFF,
                    "output_dir": str(output_dir),
                },
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ) + "\n"
        ).encode("utf-8")
    )
    return {
        "command": "fetch",
        "product": PRODUCT,
        "interval": INTERVAL,
        "output_dir": str(output_dir),
        "expected_first_time": int(expected_first_time),
        "expected_last_time": int(expected_last_time),
        "source_urls": len(records),
        "ok_sources": sum(1 for r in records if r["ok"]),
        "bar_count": len(merged),
        "day_count": len(day_bars),
        "week_count": len(week_bars),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  verify
# ═══════════════════════════════════════════════════════════════════════════════

def verify(output_dir: Path | str) -> dict[str, Any]:
    """重算每个文件 sha256、解 gzip、重跑连续性 / 聚合一致性。

    任一失败 → {"valid": False, "errors": [...]}（篡改必然失败）：
      - MANIFEST.sha256 != sha256(source_manifest.json) → 清单被改；
      - files 中任一 gzip 重算 sha256 不一致 → 数据文件被改；
      - raw_4h 解 gzip 后 validate_closed_4h 失败 → 连续性/闭合被破坏；
      - derived_1day/1week 与 aggregate_utc(raw_4h) 不一致 → 聚合被改。
    """
    output_dir = Path(output_dir)
    errors: list[str] = []
    sha_path = output_dir / SHA_MANIFEST
    man_path = output_dir / SOURCE_MANIFEST
    if not sha_path.exists() or not man_path.exists():
        return {"valid": False,
                "errors": [f"缺少 {SHA_MANIFEST} 或 {SOURCE_MANIFEST}"]}
    try:
        expected = sha_path.read_text(encoding="ascii").strip().split()[0]
    except (OSError, IndexError) as exc:
        return {"valid": False, "errors": [f"{SHA_MANIFEST} 不可读: {exc}"]}
    try:
        manifest = json.loads(man_path.read_bytes().decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False,
                "errors": [f"{SOURCE_MANIFEST} 损坏或不可读: {exc}"]}

    actual = hashlib.sha256(man_path.read_bytes()).hexdigest()
    if actual != expected:
        errors.append(
            f"{SHA_MANIFEST} 与 {SOURCE_MANIFEST} 不一致"
            f"（期望 {expected[:12]}…，实际 {actual[:12]}…）"
        )

    files = manifest.get("files")
    if not isinstance(files, dict):
        errors.append(f"{SOURCE_MANIFEST} 缺少 files 字典")
        files = {}
    for name, want_sha in files.items():
        fpath = output_dir / name
        if not fpath.exists():
            errors.append(f"缺少 {name}")
            continue
        if not isinstance(want_sha, str) or len(want_sha) != 64:
            errors.append(f"{SOURCE_MANIFEST} 中 {name} 的 sha256 摘要非法")
            continue
        got = hashlib.sha256(fpath.read_bytes()).hexdigest()
        if got != want_sha:
            errors.append(
                f"{name} sha256 不一致（期望 {want_sha[:12]}…，"
                f"实际 {got[:12]}…）"
            )

    try:
        raw = read_gzip_json(output_dir / RAW_4H)
    except Exception as exc:  # noqa: BLE001 —— 任何解压/解析失败都要报
        errors.append(f"{RAW_4H} 解压/解析失败: {exc}")
        raw = None
    if raw is not None:
        if not isinstance(raw, list):
            errors.append(f"{RAW_4H} 内容必须是 4H bar 列表")
        else:
            try:
                validate_closed_4h(raw)
            except ValueError as exc:
                errors.append(f"{RAW_4H} 连续性/闭合校验失败: {exc}")
            expected_count = manifest.get("raw_4h", {}).get("count")
            if isinstance(expected_count, int) and expected_count != len(raw):
                errors.append(
                    f"{RAW_4H} count({len(raw)}) 与清单({expected_count})不一致"
                )
            for tf, fname in (("1day", DERIVED_1DAY), ("1week", DERIVED_1WEEK)):
                try:
                    derived = read_gzip_json(output_dir / fname)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{fname} 解压/解析失败: {exc}")
                    continue
                recomputed = aggregate_utc(raw, tf)
                if derived != recomputed:
                    errors.append(
                        f"{fname} 与 {RAW_4H} 的 {tf} 聚合不一致"
                    )
    return {"valid": not errors, "errors": errors}


# ═══════════════════════════════════════════════════════════════════════════════
#  run
# ═══════════════════════════════════════════════════════════════════════════════

def _file_sha(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _config_fields(config: Any) -> dict[str, Any]:
    return {
        k: v for k, v in config.__dict__.items() if not k.startswith("_")
    }


RUN_MARKET_STATE_CONFIG = DEFAULT_MARKET_STATE_CONFIG


def run(
    data_dir: Path | str,
    output_dir: Path | str,
    *,
    expected_first_time: int = EXPECTED_FIRST_TIME,
    expected_last_time: int = EXPECTED_LAST_TIME,
) -> dict[str, Any]:
    """verify 通过后：只读 raw_4h（不联网）跑固定回测 → 写五份确定性产物。

    - 先 verify(data_dir)，任何失败 → ValueError（拒绝执行）；
    - verify 后仍须通过 run 自身产品/边界校验（verify 只验哈希/连续性/
      聚合，不校验产品/边界语义）：source_manifest.product == BTCUSDT、
      interval == 4h、raw 非空、raw 首末精确匹配 expected_first_time /
      expected_last_time、manifest raw_4h count/first/last 与 raw 一致；
      任一不满足 → ValueError。CLI 正式 run 不传覆盖 → 严格正式边界；
      测试小样本必须显式传其边界（expected_first_time/expected_last_time）。
    - 只读 raw_4h.json.gz，跑固定管线
      run_backtest_pipeline(raw, ["ma","structure","macd"], {},
      DEFAULT_MARKET_STATE_CONFIG)；summary.json 仍只从 events.csv 聚合；
    - 写 events.csv / decisions.csv / summary.json / run_manifest.json /
      RUN_MANIFEST.sha256；manifest 确定性（无时钟、无绝对路径）；
      同输入重复执行五份产物字节级一致。
    """
    data_dir = Path(data_dir)
    output_dir = Path(output_dir)
    v = verify(data_dir)
    if not v["valid"]:
        raise ValueError(
            "verify 失败，拒绝 run: " + "; ".join(v["errors"])
        )
    raw = read_gzip_json(data_dir / RAW_4H)
    src_manifest = json.loads(
        (data_dir / SOURCE_MANIFEST).read_bytes().decode("utf-8")
    )
    errors: list[str] = []
    if src_manifest.get("product") != PRODUCT:
        errors.append(
            f"source_manifest.product={src_manifest.get('product')!r} "
            f"!= {PRODUCT!r}"
        )
    if src_manifest.get("interval") != INTERVAL:
        errors.append(
            f"source_manifest.interval={src_manifest.get('interval')!r} "
            f"!= {INTERVAL!r}"
        )
    if not raw:
        errors.append(f"{RAW_4H} 为空")
    else:
        if int(raw[0]["time"]) != int(expected_first_time):
            errors.append(
                f"缺头：首根 {_fmt_utc(raw[0]['time'])} != 期望 "
                f"{_fmt_utc(expected_first_time)}（run 正式边界）"
            )
        if int(raw[-1]["time"]) != int(expected_last_time):
            errors.append(
                f"缺尾：末根 {_fmt_utc(raw[-1]['time'])} != 期望 "
                f"{_fmt_utc(expected_last_time)}（run 正式边界）"
            )
        raw_info = src_manifest.get("raw_4h")
        if not isinstance(raw_info, dict):
            errors.append(f"{SOURCE_MANIFEST} 缺少 raw_4h 块")
        else:
            if raw_info.get("count") != len(raw):
                errors.append(
                    f"清单 raw_4h.count={raw_info.get('count')!r} != "
                    f"raw 实际 {len(raw)}"
                )
            for key, want in (("first", raw[0]), ("last", raw[-1])):
                got = (raw_info.get(key) or {}).get("time")
                if got != int(want["time"]):
                    errors.append(
                        f"清单 raw_4h.{key}.time={got!r} != raw 实际 "
                        f"{int(want['time'])}"
                    )
    if errors:
        raise ValueError("run 产品/边界校验失败，拒绝执行: " + "; ".join(errors))
    output_dir.mkdir(parents=True, exist_ok=True)
    result = run_backtest_pipeline(
        raw,
        RUN_STRATEGIES,
        RUN_PARAMETERS,
        output_dir / RUN_EVENTS,
        output_dir / RUN_SUMMARY,
        output_dir / RUN_DECISIONS,
        RUN_MARKET_STATE_CONFIG,
    )
    cfg = RUN_MARKET_STATE_CONFIG
    first = raw[0]
    last = raw[-1]
    manifest: dict[str, Any] = {
        "schema": RUN_SCHEMA,
        "product": "BINANCE:BTCUSDT",
        "market": "Binance USD-M perpetual",
        "interval": INTERVAL,
        "data": {
            "source_manifest": SOURCE_MANIFEST,
            "source_manifest_sha256": _file_sha(data_dir / SOURCE_MANIFEST),
            "raw_file": RAW_4H,
            "raw_sha256": _file_sha(data_dir / RAW_4H),
            "bar_count": len(raw),
            "first_bar": _bar_slim(first),
            "last_bar": _bar_slim(last),
            "end_exclusive_time": int(last["time"]) + 14400,
            "entry_basis": "next_4h_open_price",
            "expected_bounds": {
                "first_time": int(expected_first_time),
                "last_time": int(expected_last_time),
            },
        },
        "strategies": RUN_STRATEGIES,
        "parameters": RUN_PARAMETERS,
        "market_state_config": _config_fields(cfg),
        "market_state_config_hash": cfg.config_hash(),
        "policy_A": {
            "label": "A（周日同向）",
            "human": "周线与日线都必须与事件方向同向（周/日均 ready+closed，"
                     "周线方向明确同向）",
            "rule": "week.ready AND week.is_closed AND day.ready AND "
                     "day.is_closed AND trigger.ready AND trigger.is_closed AND "
                     "day.trend == event_direction AND week.trend == event_direction",
        },
        "policy_B": {
            "label": "B（日线同向，周线仅 ready+closed）",
            "human": "日线必须与事件方向同向；周线只须 ready+closed，方向不限",
            "rule": "week.ready AND week.is_closed AND day.ready AND "
                     "day.is_closed AND trigger.ready AND trigger.is_closed AND "
                     "day.trend == event_direction",
        },
        "costs": {"fees": 0.0, "slippage": 0.0},
        "methodology": "event_study",
        "pnl_note": "事件研究：逐事件窗口收益（next_open→end_close），"
                     "非组合 PnL；不含手续费/滑点",
        "outcome_windows": RUN_OUTCOME_WINDOWS,
        "outcome_window_unit": "completed_4h_bars",
        "outputs": {
            RUN_EVENTS: _file_sha(output_dir / RUN_EVENTS),
            RUN_DECISIONS: _file_sha(output_dir / RUN_DECISIONS),
            RUN_SUMMARY: _file_sha(output_dir / RUN_SUMMARY),
        },
        "counts": {
            "event_count": result["event_count"],
            "events_rows": result["events_rows"],
            "decision_count": result["decision_count"],
        },
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False) + "\n"
    ).encode("utf-8")
    (output_dir / RUN_MANIFEST).write_bytes(manifest_bytes)
    (output_dir / RUN_SHA).write_bytes(
        (hashlib.sha256(manifest_bytes).hexdigest()
         + "  " + RUN_MANIFEST + "\n").encode("ascii")
    )
    return {
        "command": "run",
        "data_dir": str(data_dir),
        "output_dir": str(output_dir),
        "bar_count": len(raw),
        "event_count": result["event_count"],
        "events_rows": result["events_rows"],
        "decision_count": result["decision_count"],
        "events_sha256": result["events_sha256"],
        "decisions_sha256": result["decisions_sha256"],
        "summary_sha256": result["summary_sha256"],
        "run_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MTF-BTC-V1 回测数据管线 CLI（fetch / verify / run）"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("fetch", "拉取官方 4H zip → 确定性 gzip + 清单"),
        ("verify", "重算 sha / 解 gzip / 连续性 / 聚合一致性（防篡改）"),
        ("run", "verify 通过后：只读 raw_4h 跑固定回测 → 五份确定性产物"),
    ):
        p = sub.add_parser(name, help=help_text)
        if name == "run":
            p.add_argument(
                "--data-dir",
                required=True,
                help="fetch/verify 输出目录（含 raw_4h.json.gz 与 source 清单）",
            )
            p.add_argument(
                "--output-dir",
                default=str(DEFAULT_OUTPUT_DIR),
                help="回测输出目录（默认 reports/mtf-btc-backtest-v1/data）",
            )
        else:
            p.add_argument(
                "--output-dir",
                default=str(DEFAULT_OUTPUT_DIR),
                help="输出目录（默认 reports/mtf-btc-backtest-v1/data）",
            )
    args = parser.parse_args(argv)
    out_dir = Path(args.output_dir)
    try:
        if args.command == "fetch":
            result = fetch(out_dir)
        elif args.command == "run":
            result = run(args.data_dir, out_dir)
        else:
            result = verify(out_dir)
    except (FetchError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.command == "verify":
        return 0 if result.get("valid") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
