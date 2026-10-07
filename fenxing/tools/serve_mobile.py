#!/usr/bin/env python3
"""Serve the phone practice PWA on the LAN (no Xcode)."""
from __future__ import annotations

import http.server
import os
import socket
import sys
from functools import partial
from pathlib import Path

PORT = int(os.environ.get("PORT", "8766"))


def lan_ips() -> list[str]:
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    host = socket.gethostname()
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".json": "application/json",
        ".webmanifest": "application/manifest+json",
        ".js": "text/javascript",
    }

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "mobile"
    data = root / "data" / "catalog.json"
    if not data.exists():
        print("mobile/data 尚未生成。请先运行:\n  python3 tools/build_mobile_data.py", file=sys.stderr)
        return 1
    os.chdir(root)
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=str(root)))
    print(f"盘感练习 PWA  ->  http://127.0.0.1:{PORT}/", flush=True)
    for ip in lan_ips():
        print(f"iPhone 同一 Wi-Fi 打开  ->  http://{ip}:{PORT}/", flush=True)
    print("本机 HTTP 不能注册 Service Worker（iOS 要求 HTTPS）。", flush=True)
    print("真离线：把 mobile/ 推到 GitHub Pages / Cloudflare Pages 后再「添加到主屏幕」。", flush=True)
    print("Ctrl+C 停止", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
