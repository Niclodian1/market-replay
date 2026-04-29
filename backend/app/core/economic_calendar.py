"""
经济日历：只保留三大宏观事件（非农、CPI、FOMC），日期显示北京时间。
"""
from datetime import datetime, timedelta


KNOWN_FOMC_2025 = [
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-17",
]

KNOWN_FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]

# FOMC 决议通常 UTC 18:00 公布 → 北京时间次日 02:00
FOMC_BJT_HOUR = "02:00"

# 非农就业通常 UTC 12:30 公布 → 北京时间 20:30
NFP_BJT_HOUR = "20:30"

# CPI 通常 UTC 12:30 公布 → 北京时间 20:30
CPI_BJT_HOUR = "20:30"


def _first_friday(year: int, month: int) -> str:
    d = datetime(year, month, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _fomc_bjt_date(utc_date_str: str) -> str:
    """FOMC UTC 18:00 → 北京时间次日 02:00，日期+1"""
    d = datetime.strptime(utc_date_str, "%Y-%m-%d")
    bjt_d = d + timedelta(days=1)
    return bjt_d.strftime("%Y-%m-%d")


def _mid_month_tuesday(year: int, month: int) -> str:
    """CPI 通常在每月 10-14 日左右发布，取第二周的周二近似"""
    d = datetime(year, month, 10)
    while d.weekday() != 1:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def generate_calendar(days_back: int = 7, days_ahead: int = 60) -> list[dict]:
    today = datetime.utcnow().date()
    start = today - timedelta(days=days_back)
    end = today + timedelta(days=days_ahead)

    events = []

    # FOMC
    for fomc_date in KNOWN_FOMC_2025 + KNOWN_FOMC_2026:
        d = datetime.strptime(fomc_date, "%Y-%m-%d").date()
        bjt_date = _fomc_bjt_date(fomc_date)
        bjt_d = datetime.strptime(bjt_date, "%Y-%m-%d").date()
        if start <= bjt_d <= end:
            events.append({
                "date": bjt_date,
                "time_bjt": FOMC_BJT_HOUR,
                "name": "FOMC 利率决议",
                "importance": "high",
            })

    # 非农 & CPI
    for year in range(start.year, end.year + 1):
        for month in range(1, 13):
            # 非农：每月第一个周五
            nfp_date = _first_friday(year, month)
            nfp_d = datetime.strptime(nfp_date, "%Y-%m-%d").date()
            if start <= nfp_d <= end:
                events.append({
                    "date": nfp_date,
                    "time_bjt": NFP_BJT_HOUR,
                    "name": "非农就业数据",
                    "importance": "high",
                })

            # CPI：每月中旬
            cpi_date = _mid_month_tuesday(year, month)
            cpi_d = datetime.strptime(cpi_date, "%Y-%m-%d").date()
            if start <= cpi_d <= end:
                events.append({
                    "date": cpi_date,
                    "time_bjt": CPI_BJT_HOUR,
                    "name": "CPI 消费者物价指数",
                    "importance": "high",
                })

    events.sort(key=lambda e: (e["date"], e["time_bjt"]))
    return events
