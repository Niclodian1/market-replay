"""缠论 adapter：把 fenxing 引擎输出归一化成 ``Event``。

这里只做「结构 → 契约」的翻译，不重算任何缠论逻辑。
引擎调用通过 ``ChanlunProvider`` 注入，避免本包反向依赖 backend。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from ..spec import Event, IndicatorSpec, params_hash

# 引擎周期名由 provider 负责映射，这里只认后端周期名
_SUPPORTED_TFS = ("1day", "4h", "1h")
_TF_SECONDS = {"1day": 86_400, "4h": 14_400, "1h": 3_600}

_PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["stroke", "seg", "both"],
            "default": "both",
            "title": "笔/线段口径",
            "description": "stroke=笔级（灵敏）, seg=线段级（稳）, both=两级别合并去重（默认）",
        },
        "include_forming": {
            "type": "boolean",
            "default": False,
            "title": "包含在途信号",
            "description": "开启后包含尚未确认（forming）的背驰/买卖点，仅供观察，不应用于统计",
        },
    },
    "additionalProperties": False,
}


class ChanlunProvider(Protocol):
    """由宿主（backend）实现：给定 K 线，返回一个级别的缠论结构。"""

    def analyze(
        self, symbol: str, timeframe: str, bars: Sequence[dict[str, Any]]
    ) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class _PointSource:
    indicator_id: str
    label: str
    direction: str
    k: str
    # mode -> 引擎输出里的数组名
    arrays: dict[str, str]


_POINT_SOURCES: tuple[_PointSource, ...] = (
    _PointSource("chanlun.buy1", "一买", "long", "一买",
                 {"stroke": "buyPointsStrokeRaw", "seg": "buyPointsSegmentRaw"}),
    _PointSource("chanlun.buy2", "二买", "long", "二买",
                 {"stroke": "buyPointsStrokeRaw", "seg": "buyPointsSegmentRaw"}),
    _PointSource("chanlun.buy3", "三买", "long", "三买",
                 {"stroke": "buyPointsStrokeRaw", "seg": "buyPointsSegmentRaw"}),
    _PointSource("chanlun.sell1", "一卖", "short", "一卖",
                 {"stroke": "sellPointsStrokeRaw", "seg": "sellPointsSegmentRaw"}),
    _PointSource("chanlun.sell2", "二卖", "short", "二卖",
                 {"stroke": "sellPointsStrokeRaw", "seg": "sellPointsSegmentRaw"}),
    _PointSource("chanlun.sell3", "三卖", "short", "三卖",
                 {"stroke": "sellPointsStrokeRaw", "seg": "sellPointsSegmentRaw"}),
    _PointSource("chanlun.beichi.buy", "背驰买", "long", "背驰买",
                 {"stroke": "beichiPointsRaw", "seg": "beichiPointsSegmentRaw"}),
    _PointSource("chanlun.beichi.sell", "背驰卖", "short", "背驰卖",
                 {"stroke": "beichiPointsRaw", "seg": "beichiPointsSegmentRaw"}),
    _PointSource("chanlun.panbei.buy", "盘整背驰买", "long", "盘整背驰买",
                 {"stroke": "panbeiPointsRaw", "seg": "panbeiPointsSegmentRaw"}),
    _PointSource("chanlun.panbei.sell", "盘整背驰卖", "short", "盘整背驰卖",
                 {"stroke": "panbeiPointsRaw", "seg": "panbeiPointsSegmentRaw"}),
)


class _ChanlunDetector:
    def __init__(self, provider: ChanlunProvider, source: _PointSource) -> None:
        self._provider = provider
        self._source = source
        self.spec = IndicatorSpec(
            id=source.indicator_id,
            label=source.label,
            group="chanlun",
            timeframes=_SUPPORTED_TFS,
            warmup_bars=120,
            direction_support=(source.direction,),  # type: ignore[arg-type]
            description=f"缠论{source.label}（引擎能力，不可删除，只能停用）",
            logic="由 fenxing/chanlun.py 产出；事件带 confirmed_at、why、sub、upper、ratio。",
            params_schema=_PARAMS_SCHEMA,
        )

    def detect(
        self,
        bars: Sequence[dict[str, Any]],
        symbol: str,
        timeframe: str,
        params: dict[str, Any],
    ) -> list[Event]:
        mode = str(params.get("mode") or "both")
        if mode not in ("stroke", "seg", "both"):
            raise ValueError(f"mode 必须是 stroke/seg/both，收到 {mode!r}")
        include_forming = bool(params.get("include_forming", False))
        if len(bars) < self.spec.warmup_bars:
            return []

        level = self._provider.analyze(symbol, timeframe, bars)
        points = self._collect(level, mode, timeframe)
        p_hash = params_hash({**params, "mode": mode, "include_forming": include_forming})

        events: list[Event] = []
        for point in points:
            state = str(point.get("state") or "confirmed")
            if state != "confirmed" and not include_forming:
                continue
            bar_time = point.get("t")
            confirmed_at = point.get("confirmedT") or point.get("alertT") or bar_time
            price = point.get("p")
            if not bar_time or price is None:
                continue
            ratio = point.get("ratio")
            strength = None
            if isinstance(ratio, (int, float)):
                strength = round(max(0.0, min(1.0, 1.0 - float(ratio))), 4)
            events.append(
                Event(
                    indicator_id=self.spec.id,
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    bar_time=int(bar_time),
                    confirmed_at=int(confirmed_at),
                    direction=self._source.direction,  # type: ignore[arg-type]
                    price=float(price),
                    params_hash=p_hash,
                    state=state,
                    strength=strength,
                    title=f"{self._source.label} · {timeframe}",
                    evidence={
                        "k": point.get("k"),
                        "mode": point.get("mode") or mode,
                        "tier": point.get("tier"),
                        "ratio": ratio,
                        "trigger": point.get("trigger"),
                        "why": point.get("why") or "",
                        "sub": point.get("sub") or "",
                        "upper": point.get("upper") or "",
                    },
                )
            )
        return events

    def _collect(
        self, level: dict[str, Any], mode: str, timeframe: str
    ) -> list[dict[str, Any]]:
        modes = ("stroke", "seg") if mode == "both" else (mode,)
        picked: list[tuple[str, dict[str, Any]]] = []
        for m in modes:
            array_name = self._source.arrays[m]
            for point in level.get(array_name) or []:
                if point.get("k") != self._source.k:
                    continue
                picked.append((m, point))
        if mode != "both":
            return [p for _, p in picked]
        # both：同一 k + 位置相近（±3 bar）时保留线段级（更稳），与 chanlun_api 口径一致
        window = 3 * _TF_SECONDS.get(timeframe, 86_400)
        seg_times = [
            int(p["t"]) for m, p in picked if m == "seg" and p.get("t") is not None
        ]
        merged: list[dict[str, Any]] = []
        for m, point in picked:
            t = point.get("t")
            if m == "stroke" and t is not None and any(abs(int(t) - st) <= window for st in seg_times):
                continue
            merged.append(point)
        return merged


def build_chanlun_detectors(provider: ChanlunProvider) -> list[_ChanlunDetector]:
    """构建全部缠论指标（10 个），交给 ``IndicatorRegistry`` 注册。"""
    return [_ChanlunDetector(provider, source) for source in _POINT_SOURCES]
