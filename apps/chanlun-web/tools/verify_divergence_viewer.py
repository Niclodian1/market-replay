#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""divergence_viewer.html 渲染冒烟: headless Chrome 截图 + 像素分析。
检查: 0 console 报错, K线/柱已绘, 2 组检测器 marker 颜色均出现在默认视图。"""
import os
import signal
import subprocess
import time

from PIL import Image

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = os.path.dirname(os.path.abspath(__file__))
URL = "file://" + HERE + "/divergence_viewer.html"
SHOT = "/tmp/dvg_check.png"

UP = (12, 163, 12)       # #0ca30c 涨
DOWN = (230, 103, 103)   # #e66767 跌
FAMILIES = [  # (名称, RGB) 对应 DETECTORS 颜色
    ("强 swing_strong-强", (190, 24, 93)),
    ("强 swing_strong-中", (236, 72, 153)),
    ("强 swing_strong-弱", (249, 168, 212)),
    ("缠 chanlun", (234, 88, 12)),
]


def shot():
    p = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--enable-logging=stderr", "--user-data-dir=/tmp/dvg_chrome",
         "--window-size=1680,1000", "--screenshot=" + SHOT, URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 45
    while not os.path.exists(SHOT) and time.time() < deadline:
        time.sleep(0.3)
    time.sleep(4.0)
    p.send_signal(signal.SIGKILL)
    try:
        _, err = p.communicate(timeout=5)
    except Exception:
        err = ""
    return err


def count_color(img, tc, tol, step=2):
    W, H = img.size
    return sum(
        1
        for x in range(0, W, step)
        for y in range(0, H, step)
        if all(abs(a - b) <= tol for a, b in zip(img.getpixel((x, y)), tc))
    )


def main():
    err = shot()
    img = Image.open(SHOT).convert("RGB")
    errs = [l for l in (err or "").splitlines() if "CONSOLE" in l]
    results = []
    results.append(("console 0 报错", len(errs) == 0))
    print("console lines:", len(errs))
    for l in errs[:6]:
        print("   ", l.strip())
    g = count_color(img, UP, 28)
    r = count_color(img, DOWN, 28)
    results.append(("K线/柱 涨色 %d" % g, g > 20))
    results.append(("K线/柱 跌色 %d" % r, r > 20))
    for name, tc in FAMILIES:
        n = count_color(img, tc, 32)
        results.append(("marker %-16s %5d px" % (name, n), n > 5))
    all_ok = True
    for label, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", label))
        all_ok = all_ok and ok
    print("=== " + ("PASS" if all_ok else "FAIL") + " ===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
