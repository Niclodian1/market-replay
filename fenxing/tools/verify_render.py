#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Headless Chrome 渲染验证: 缠论多级别联动前端。

检查项:
  - 三周期(1D/4H/1H)加载: 0 console 报错 + 蜡烛已绘制。
  - 卖点标记(三卖 #9c27b0)像素存在(?layers=buys,sells)。
  - ?detail=1 详情面板(产生说明/次级别确认)加载无报错。
  - 原始/合并、线段/笔中枢切换路径(带 ?view / ?pivot 参数)。
  - ?fast=1 抢先背驰买点(#26a69a)。

要点: 本机 Chrome headless `--dump-dom` 会因 autoSize 挂起, 用截图+像素分析;
      stderr 必须落盘(不能 PIPE 不读, 日志写满管道会卡死 Chrome);
      脚本自带临时 HTTP 服务(分钟级分片 fetch 在 file:// 下会被 CORS 拦, 属预期)。
"""
import functools
import http.server
import os
import shutil
import signal
import socketserver
import subprocess
import tempfile
import threading
import time

from PIL import Image

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # fenxing/ 根目录
SHOT = os.path.join(HERE, "tools", "render_check.png")

# 暗色主题标记色(像素检测目标, TradingView 色板); 容差收紧到 10, 避免与蜡烛涨跌色混淆。
# 注: 检测色须为当前数据实际出现的信号, 且不与 MA/图层默认色撞色(故用三卖紫/背驰买绿)。
SELL3 = (156, 39, 176)    # 三卖 #9c27b0
FAST_BUY = (38, 166, 154) # 背驰买点 #26a69a (抢先层)
UP = (8, 153, 129)        # 涨 #089981
DOWN = (242, 54, 69)      # 跌 #f23645
MARKER_TOL = 10


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server():
    handler = functools.partial(QuietHandler, directory=HERE)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def shot(url, prof):
    log = SHOT + ".log"
    if os.path.exists(SHOT):
        os.remove(SHOT)
    with open(log, "w") as f:
        p = subprocess.Popen(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--enable-logging=stderr", "--user-data-dir=" + prof,
             "--window-size=1680,1000", "--screenshot=" + SHOT, url],
            stdout=subprocess.DEVNULL, stderr=f, text=True,
        )
        deadline = time.time() + 45
        while time.time() < deadline:
            if os.path.exists(SHOT) and os.path.getsize(SHOT) > 0:
                break
            time.sleep(0.3)
        time.sleep(4.0)
        p.send_signal(signal.SIGKILL)
        try:
            p.wait(timeout=5)
        except Exception:
            pass
    with open(log, errors="ignore") as f:
        err = f.read()
    return SHOT, err


def candles_painted(img):
    W, H = img.size
    g = r = 0
    for x in range(0, W, 3):
        for y in range(0, H, 3):
            c = img.getpixel((x, y))
            if all(abs(a - b) <= 28 for a, b in zip(c, UP)):
                g += 1
            if all(abs(a - b) <= 28 for a, b in zip(c, DOWN)):
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
        n = find_color(img, tc, MARKER_TOL)
        good = n > 5
        ok = ok and good
        print("  [%s] %-12s %6d px" % ("PASS" if good else "FAIL", name, n))
    shutil.rmtree(prof, ignore_errors=True)
    return ok


def main():
    httpd, port = start_server()
    base = "http://127.0.0.1:%d/index.html" % port
    last = []
    for attempt in range(3):  # autoSize 首帧延迟重试(每次用全新 profile, 避免缓存旧脚本)
        run = tempfile.mkdtemp(prefix="chrome_vr_")
        # 1D/4H/1H: 蜡烛 + 买/卖标记(?layers 深链直达结构图层)
        for tf, tag in (("1D", "1d"), ("4H", "4h"), ("1H", "1h")):
            last.append(load(
                base + "?tf=%s&layers=buys,sells" % tf, os.path.join(run, tag),
                need_markers=[("三卖 marker", SELL3)]))
        last.append(load(base + "?tf=1D&detail=1", os.path.join(run, "det"),
                         expect_candles=False))
        last.append(load(base + "?tf=4H&view=merged&pivot=stroke", os.path.join(run, "vm"),
                         expect_candles=False))
        last.append(load(base + "?tf=1D&fast=1&layers=fast", os.path.join(run, "fa"),
                         need_markers=[("背驰买 marker", FAST_BUY)]))
        if all(last):
            break
        last = []
        time.sleep(1)
    httpd.shutdown()
    print("=== " + ("PASS" if all(last) else "FAIL") + " ===")
    return 0 if all(last) else 1


if __name__ == "__main__":
    raise SystemExit(main())
