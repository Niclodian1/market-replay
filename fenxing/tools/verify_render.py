#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Headless Chrome 渲染验证: 缠论多级别联动前端。

检查项:
  - 三周期(1D/4H/1H)加载: 0 console 报错 + 蜡烛已绘制。
  - 买点标记(一买 #d55181)与卖点标记(一卖 #0094a6)像素存在。
  - ?detail=1 详情面板(产生说明/次级别确认)加载无报错。
  - 原始/合并、线段/笔中枢切换路径(带 ?view / ?pivot 参数)。

要点: Chrome headless 在本机 `--dump-dom` 会因 autoSize 挂起, 用截图+像素分析;
      autoSize 首帧延迟用重试兜底。
"""
import os
import signal
import subprocess
import time

from PIL import Image

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = os.path.dirname(os.path.abspath(__file__))
URL = "file://" + HERE + "/index.html"
SHOT = os.path.join(HERE, "render_check.png")

# 暗色主题标记色(像素检测目标)
BUY1 = (213, 81, 129)     # 一买 #d55181
SELL1 = (0, 148, 166)     # 一卖 #0094a6
FAST_BUY = (15, 157, 92)  # 背驰买点 #0f9d5c (抢先层)


def shot(url, prof):
    p = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--enable-logging=stderr", "--user-data-dir=" + prof,
         "--window-size=1680,1000", "--screenshot=" + SHOT, url],
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
    return SHOT, err


def candles_painted(img):
    W, H = img.size
    g = r = 0
    for x in range(0, W, 3):
        for y in range(0, H, 3):
            c = img.getpixel((x, y))
            if abs(c[0] - 12) <= 28 and abs(c[1] - 163) <= 28 and abs(c[2] - 12) <= 28:
                g += 1
            if abs(c[0] - 230) <= 28 and abs(c[1] - 103) <= 28 and abs(c[2] - 103) <= 28:
                r += 1
    return g, r


def find_color(img, tc, tol):
    W, H = img.size
    return sum(
        1
        for x in range(0, W, 2)
        for y in range(0, H, 2)
        if all(abs(a - b) <= tol for a, b in zip(img.getpixel((x, y)), tc))
    )


def load(url, prof, expect_candles=True, need_markers=()):
    img_path, err = shot(url, prof)
    img = Image.open(img_path).convert("RGB")
    g, r = candles_painted(img)
    errs = [l for l in (err or "").splitlines() if "CONSOLE" in l]
    ok = (not errs) and (not expect_candles or g + r > 50)
    print("  [%s] %s  console=%d  candles_g=%d/r=%d" % (
        "PASS" if ok else "FAIL", url, len(errs), g, r))
    for l in errs[:6]:
        print("      ", l.strip())
    for name, tc in need_markers:
        n = find_color(img, tc, 30)
        good = n > 5
        ok = ok and good
        print("  [%s] %-12s %6d px" % ("PASS" if good else "FAIL", name, n))
    return ok


def main():
    results = []
    for attempt in range(3):  # autoSize 首帧延迟重试
        # 1D 默认: 蜡烛 + 买点 + 卖点
        ok1 = load(URL, "/tmp/chrome_vr_%d_1d" % attempt,
                   need_markers=[("一买 marker", BUY1), ("一卖 marker", SELL1)])
        ok2 = load(URL + "?tf=4H", "/tmp/chrome_vr_%d_4h" % attempt,
                   need_markers=[("一买 marker", BUY1), ("一卖 marker", SELL1)])
        ok3 = load(URL + "?tf=1H", "/tmp/chrome_vr_%d_1h" % attempt,
                   need_markers=[("一买 marker", BUY1), ("一卖 marker", SELL1)])
        ok4 = load(URL + "?tf=1D&detail=1", "/tmp/chrome_vr_%d_det" % attempt,
                   expect_candles=False)
        ok5 = load(URL + "?tf=4H&view=merged&pivot=stroke", "/tmp/chrome_vr_%d_vm" % attempt,
                   expect_candles=False)
        ok6 = load(URL + "?tf=1D&fast=1", "/tmp/chrome_vr_%d_fa" % attempt,
                   need_markers=[("背驰买 marker", FAST_BUY)])
        results = [ok1, ok2, ok3, ok4, ok5, ok6]
        if all(results):
            break
        time.sleep(1)
    print("=== " + ("PASS" if all(results) else "FAIL") + " ===")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
