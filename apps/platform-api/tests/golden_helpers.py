"""golden / 特征测试共用工具。

确定性合成 K 线 + 稳定摘要,用于在重构期间锁住回测引擎的行为:
任何改动若改变了输出,摘要就会变,测试立即报警。
"""
import hashlib
import json
import math


def make_candles(n: int = 240, start_ts: int = 1_600_000_000, step: int = 3600, base: float = 100.0):
    """趋势 + 双正弦波动的确定性 K 线(无随机数,跨机器/Python 版本稳定)。"""
    out = []
    for i in range(n):
        drift = 1 + 0.0008 * i
        wave = 1 + 0.06 * math.sin(i / 9.0) + 0.025 * math.sin(i / 3.3)
        close = base * drift * wave
        if i:
            openp = base * drift * (1 + 0.06 * math.sin((i - 1) / 9.0) + 0.025 * math.sin((i - 1) / 3.3))
        else:
            openp = close
        out.append({
            "time": start_ts + i * step,
            "open": round(openp, 6),
            "high": round(max(openp, close) * 1.012, 6),
            "low": round(min(openp, close) * 0.988, 6),
            "close": round(close, 6),
            "volume": 1000 + 50 * ((i * 37) % 11),
        })
    return out


def digest(obj) -> str:
    """对任意可 JSON 序列化对象取稳定短摘要。"""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]
