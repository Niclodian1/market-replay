"""
滚动相关性矩阵：基于对数收益率的 Pearson 相关系数。
"""
import math
from typing import Optional


def compute_log_returns(closes: list[float]) -> list[float]:
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            returns.append(math.log(closes[i] / closes[i - 1]))
        else:
            returns.append(0.0)
    return returns


def pearson_correlation(x: list[float], y: list[float]) -> Optional[float]:
    n = min(len(x), len(y))
    if n < 5:
        return None

    x = x[-n:]
    y = y[-n:]

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
    std_x = (sum((xi - mean_x) ** 2 for xi in x) / n) ** 0.5
    std_y = (sum((yi - mean_y) ** 2 for yi in y) / n) ** 0.5

    if std_x == 0 or std_y == 0:
        return None

    return cov / (std_x * std_y)


def compute_correlation_matrix(
    symbol_closes: dict[str, list[dict]],
    window: int = 60,
) -> dict:
    """
    输入 { symbol: [{ time, close }, ...] }，
    返回 { symbols, matrix }。
    """
    symbols = sorted(symbol_closes.keys())
    n = len(symbols)

    returns_map: dict[str, dict[int, float]] = {}
    for sym in symbols:
        data = symbol_closes[sym]
        for i in range(1, len(data)):
            prev_close = data[i - 1]["close"]
            curr_close = data[i]["close"]
            if prev_close > 0 and curr_close > 0:
                if sym not in returns_map:
                    returns_map[sym] = {}
                returns_map[sym][data[i]["time"]] = math.log(curr_close / prev_close)

    common_times = None
    for sym in symbols:
        times = set(returns_map.get(sym, {}).keys())
        common_times = times if common_times is None else common_times & times
    common_times = sorted(common_times or [])

    if window > 0:
        common_times = common_times[-window:]

    matrix: list[list[Optional[float]]] = []
    for i, sym_i in enumerate(symbols):
        row: list[Optional[float]] = []
        ri = [returns_map.get(sym_i, {}).get(t, 0.0) for t in common_times]
        for j, sym_j in enumerate(symbols):
            if i == j:
                row.append(1.0)
            elif j < i:
                row.append(matrix[j][i])
            else:
                rj = [returns_map.get(sym_j, {}).get(t, 0.0) for t in common_times]
                row.append(pearson_correlation(ri, rj))
            row[-1] = round(row[-1], 4) if row[-1] is not None else None
        matrix.append(row)

    return {"symbols": symbols, "matrix": matrix}
