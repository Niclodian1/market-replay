#!/usr/bin/env python3
"""Export @baoshen23761792 posts to a chronological markdown file and dated images."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
V2 = ROOT / "v2"
OUT_MD = ROOT / "timeline.md"
IMG_DIR = ROOT / "images"
USER = "baoshen23761792"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

TYPE_LABEL = {
    "post": "原创",
    "quote": "引用",
    "reply": "回复",
    "reply_or_thread_context": "回复/线程",
}

# Found via X search; not present in the nitter corpus.
X_SEARCH_EXTRA = [
    {
        "id": "1473238129004134401",
        "author": USER,
        "url": "https://x.com/baoshen23761792/status/1473238129004134401",
        "text": "@shawn_diu 边走边看吧，49000附近我平了1/3。 51000有机会。",
        "display_date": "Dec 21, 2021 · 10:25 AM UTC",
        "created_at_from_id": "2021-12-21T10:25:16.052000+00:00",
        "replying_to": ["@shawn_diu"],
        "quote_url": None,
        "quoted_text": None,
        "photo_urls": [],
        "quote_photo_urls": [],
        "links": [],
        "has_video": False,
        "post_type": "reply",
        "conversation_id": "1473148538641338370",
        "source": "x_search",
    }
]


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def x_status_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"/status/(\d+)", url)
    if m:
        return f"https://x.com/{USER}/status/{m.group(1)}"
    return url


def load_posts() -> list[dict]:
    posts = {}
    for line in (V2 / "corpus.jsonl").read_text().splitlines():
        row = json.loads(line)
        posts[row["id"]] = row

    verify_path = V2 / "fulltext_verification.json"
    if verify_path.exists():
        for item in json.loads(verify_path.read_text()):
            if item.get("status") != "checked":
                continue
            api_text = item.get("api_text") or ""
            row = posts.get(item["id"])
            if row and len(api_text) > len(row.get("text") or ""):
                row["text"] = api_text
                row["text_chars"] = len(api_text)
                row["possible_truncation"] = False
                row["fulltext_source"] = "fxtwitter"

    for extra in X_SEARCH_EXTRA:
        posts.setdefault(extra["id"], extra)

    rows = list(posts.values())
    rows.sort(key=lambda r: (parse_dt(r["created_at_from_id"]), r["id"]))
    return rows


def media_basename(url: str) -> str:
    return Path(urlparse(url.split("?")[0]).path).name


def candidate_urls(url: str) -> list[str]:
    clean = url.split("?")[0]
    stem = clean.rsplit(".", 1)[0]
    name = Path(urlparse(clean).path).stem
    out = [
        clean + "?name=orig",
        clean + "?format=jpg&name=orig",
        clean + "?format=png&name=orig",
        f"https://pbs.twimg.com/media/{name}?format=jpg&name=orig",
        f"https://pbs.twimg.com/media/{name}?format=png&name=orig",
        stem + ".png?name=orig",
        stem + ".jpg?name=orig",
        clean,
    ]
    seen, uniq = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def curl_download(url: str, dest: Path) -> bool:
    temp = dest.with_suffix(dest.suffix + ".part")
    cmd = [
        "curl", "--fail", "--silent", "--show-error", "--location",
        "--max-time", "40", "--retry", "2", "--retry-delay", "1",
        "-A", UA, url, "--output", str(temp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode or not temp.exists() or temp.stat().st_size < 32:
        if temp.exists():
            temp.unlink()
        return False
    head = temp.read_bytes()[:16]
    if head.startswith(b"<!DOCTYPE") or head.startswith(b"<html"):
        temp.unlink()
        return False
    temp.replace(dest)
    return True


def dated_name(dt: datetime, tweet_id: str, index: int, total: int, ext: str) -> str:
    stamp = dt.strftime("%Y-%m-%d_%H%M%S")
    if total == 1:
        return f"{stamp}_{tweet_id}{ext}"
    return f"{stamp}_{tweet_id}_{index:02d}{ext}"


def collect_images(posts: list[dict]) -> list[dict]:
    existing = {}
    manifest_path = V2 / "media_manifest.json"
    if manifest_path.exists():
        for item in json.loads(manifest_path.read_text()):
            existing[item["url"].split("?")[0]] = item

    IMG_DIR.mkdir(exist_ok=True)
    used_names: set[str] = set()
    jobs = []
    for post in posts:
        dt = parse_dt(post["created_at_from_id"])
        urls = list(post.get("photo_urls") or [])
        for i, url in enumerate(urls, 1):
            key = url.split("?")[0]
            ext = Path(urlparse(key).path).suffix.lower() or ".jpg"
            name = dated_name(dt, post["id"], i, len(urls), ext)
            while name in used_names:
                name = dated_name(dt, post["id"], i, len(urls), f"_{len(used_names)}{ext}")
            used_names.add(name)
            jobs.append({
                "post_id": post["id"],
                "url": url,
                "key": key,
                "dest": IMG_DIR / name,
                "rel": f"images/{name}",
                "index": i,
                "created_at": dt.isoformat(),
            })

    def valid_file(path: Path | None) -> bool:
        return bool(path and path.exists() and path.stat().st_size > 32)

    def resolve(job: dict) -> dict:
        dest: Path = job["dest"]
        if valid_file(dest):
            return {**job, "status": "exists", "bytes": dest.stat().st_size, "dest": str(dest)}
        item = existing.get(job["key"], {})
        src = None
        if item.get("status") == "downloaded":
            for candidate in (V2 / item.get("file", ""), ROOT / item.get("path", "")):
                if valid_file(candidate):
                    src = candidate
                    break
        if src:
            shutil.copy2(src, dest)
            return {**job, "status": "copied", "bytes": dest.stat().st_size, "dest": str(dest)}
        for url in candidate_urls(job["url"]):
            if curl_download(url, dest):
                return {**job, "status": "downloaded", "bytes": dest.stat().st_size, "from": url, "dest": str(dest)}
        return {**job, "status": "failed", "dest": str(dest)}

    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(resolve, job) for job in jobs]
        for n, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if n % 50 == 0:
                print(f"images {n}/{len(jobs)}", flush=True)
    results.sort(key=lambda r: (r["created_at"], r["post_id"], r["index"]))
    (ROOT / "images_manifest.json").write_text(
        json.dumps([{k: v for k, v in r.items() if k != "dest"} | {"file": r["rel"]}
                    for r in results], ensure_ascii=False, indent=2) + "\n"
    )
    return results


def md_escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(posts: list[dict], images: list[dict]) -> str:
    by_post = defaultdict(list)
    for item in images:
        if item["status"] != "failed":
            by_post[item["post_id"]].append(item)
        else:
            by_post[item["post_id"]].append(item)

    months = Counter(parse_dt(p["created_at_from_id"]).strftime("%Y-%m") for p in posts)
    types = Counter(TYPE_LABEL.get(p.get("post_type"), p.get("post_type") or "未知") for p in posts)
    ok_images = sum(1 for i in images if i["status"] != "failed")
    failed_images = [i for i in images if i["status"] == "failed"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# @{USER} 时间线",
        "",
        f"- 账号：BlackJack（[@{USER}](https://x.com/{USER})）",
        f"- 导出时间：{now}",
        f"- 帖子数：**{len(posts)}**（按创作时间升序）",
        f"- 配图：成功 {ok_images} / 尝试 {len(images)}，目录 [`images/`](images/)",
        "- 时间均为 UTC。图片文件名：`YYYY-MM-DD_HHMMSS_<推文ID>[_序号].ext`",
        "",
        "账号公开资料约为 1565 条、461 条媒体。本文件合并 nitter 公开时间线、X 搜索抽检与 FxTwitter 长文校对。",
        "已删除、仅自己可见、未编入公开索引的内容无法补全。转推他人的帖子未计入。",
        "",
        "## 按类型",
        "",
        "| 类型 | 条数 |",
        "| --- | ---: |",
    ]
    for name, count in types.most_common():
        lines.append(f"| {name} | {count} |")
    lines += ["", "## 按月", "", "| 月份 | 条数 |", "| --- | ---: |"]
    for month, count in sorted(months.items()):
        lines.append(f"| {month} | {count} |")
    lines += ["", "---", ""]

    current_month = None
    for post in posts:
        dt = parse_dt(post["created_at_from_id"])
        month = dt.strftime("%Y-%m")
        if month != current_month:
            current_month = month
            lines += [f"## {month}", ""]
        kind = TYPE_LABEL.get(post.get("post_type"), post.get("post_type") or "帖子")
        stamp = dt.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"### {stamp} UTC · {kind}")
        lines.append("")
        lines.append(f"- 链接：https://x.com/{USER}/status/{post['id']}")
        replies = post.get("replying_to") or []
        if replies:
            who = ", ".join(replies) if isinstance(replies, list) else str(replies)
            lines.append(f"- 回复：{who}")
        quote_url = x_status_url(post.get("quote_url"))
        if quote_url:
            lines.append(f"- 引用：{quote_url}")
        lines.append("")
        quoted = (post.get("quoted_text") or "").strip()
        if quoted:
            for qline in quoted.splitlines() or [quoted]:
                lines.append(f"> {qline}")
            lines.append("")
        text = (post.get("text") or "").strip() or "（无文字）"
        lines.append(text)
        lines.append("")
        attached = by_post.get(post["id"], [])
        for item in attached:
            if item["status"] == "failed":
                lines.append(f"- 配图下载失败：{item['url']}")
            else:
                alt = f"{stamp} UTC 配图"
                lines.append(f"![{alt}]({item['rel']})")
            lines.append("")
        if post.get("has_video"):
            lines.append("- 本条含视频，未下载。")
            lines.append("")
        lines.append("---")
        lines.append("")

    if failed_images:
        lines += ["## 未下载成功的配图", ""]
        for item in failed_images:
            lines.append(f"- {item['created_at']} · {item['post_id']} · {item['url']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    posts = load_posts()
    print(f"posts {len(posts)}", flush=True)
    images = collect_images(posts)
    ok = sum(i["status"] != "failed" for i in images)
    print(json.dumps({
        "posts": len(posts),
        "images_ok": ok,
        "images_failed": sum(i["status"] == "failed" for i in images),
        "oldest": posts[0]["created_at_from_id"] if posts else None,
        "newest": posts[-1]["created_at_from_id"] if posts else None,
    }, ensure_ascii=False), flush=True)
    OUT_MD.write_text(render(posts, images), encoding="utf-8")
    print(f"wrote {OUT_MD} ({OUT_MD.stat().st_size} bytes)", flush=True)


if __name__ == "__main__":
    main()
