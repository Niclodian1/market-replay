import json
import math
import os
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from app.schemas.fundamentals import CompanyProfile, CompanyProfileCard, RevenueSegment


PROFILE_PATH = Path(__file__).resolve().parents[2] / "data" / "fundamentals" / "company_profiles.xlsx"


class CompanyProfileLoader:
    def __init__(self, path: Path = PROFILE_PATH) -> None:
        self.path = path
        self._mtime: Optional[float] = None
        self._profiles: dict[str, CompanyProfile] = {}

    def list_profiles(self, market: Optional[str] = None, q: Optional[str] = None) -> list[CompanyProfileCard]:
        profiles = list(self._load().values())
        if market:
            profiles = [p for p in profiles if p.market.upper() == market.upper()]
        if q:
            needle = q.strip().lower()
            profiles = [
                p for p in profiles
                if needle in p.symbol.lower()
                or needle in (p.name_cn or "").lower()
                or needle in (p.name_en or "").lower()
                or needle in (p.sector or "").lower()
                or needle in (p.industry or "").lower()
            ]
        return [
            CompanyProfileCard(
                symbol=p.symbol,
                market=p.market,
                name_cn=p.name_cn,
                name_en=p.name_en,
                sector=p.sector,
                industry=p.industry,
                business_overview=p.business_overview,
                reviewed=p.reviewed,
            )
            for p in sorted(profiles, key=lambda item: (item.market, item.symbol))
        ]

    def get_profile(self, symbol: str) -> Optional[CompanyProfile]:
        return self._load().get(_symbol_key(symbol))

    def reload(self) -> int:
        self._mtime = None
        return len(self._load(force=True))

    def _load(self, force: bool = False) -> dict[str, CompanyProfile]:
        if not self.path.exists():
            self._profiles = {}
            self._mtime = None
            return self._profiles

        mtime = os.path.getmtime(self.path)
        if not force and self._mtime == mtime:
            return self._profiles

        df = pd.read_excel(self.path)
        profiles: dict[str, CompanyProfile] = {}
        for _, row in df.iterrows():
            data = {col: _clean(row.get(col)) for col in df.columns}
            symbol = str(data.get("symbol") or "").strip()
            if not symbol:
                continue
            profile = CompanyProfile(
                symbol=symbol,
                market=str(data.get("market") or "").strip().upper(),
                name_cn=data.get("name_cn"),
                name_en=data.get("name_en"),
                sector=data.get("sector"),
                industry=data.get("industry"),
                business_overview=data.get("business_overview"),
                equity_story=data.get("equity_story"),
                revenue_model=data.get("revenue_model"),
                revenue_breakdown=_parse_revenue_breakdown(data.get("revenue_breakdown")),
                moat=_split_points(data.get("moat")),
                key_risks=_split_points(data.get("key_risks")),
                updated_by=data.get("updated_by"),
                updated_at=str(data.get("updated_at")) if data.get("updated_at") is not None else None,
                reviewed=_parse_bool(data.get("reviewed")),
            )
            profiles[_symbol_key(symbol)] = profile

        self._profiles = profiles
        self._mtime = mtime
        return self._profiles


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _symbol_key(symbol: str) -> str:
    return symbol.strip().upper()


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def _split_points(value: Any) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in str(value).replace("；", ";").split(";") if part.strip()]


def _parse_revenue_breakdown(value: Any) -> list[RevenueSegment]:
    if value is None:
        return []
    try:
        raw = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    segments = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        segment = str(item.get("segment") or "").strip()
        pct = item.get("pct")
        if not segment or pct is None:
            continue
        try:
            segments.append(RevenueSegment(segment=segment, pct=float(pct)))
        except (TypeError, ValueError):
            continue
    return segments


profile_loader = CompanyProfileLoader()
