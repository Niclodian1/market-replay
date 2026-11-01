from pydantic import BaseModel


class SwingPoint(BaseModel):
    time: int
    price: float
    type: str
    label: str


class StructureBreak(BaseModel):
    time: int
    price: float
    direction: str
    broken_level: float


class SRLevel(BaseModel):
    price: float
    strength: float
    touch_count: int
    type: str


class StructureResponse(BaseModel):
    symbol: str
    timeframe: str
    trend: str
    swing_points: list[SwingPoint]
    structure_breaks: list[StructureBreak]
    sr_levels: list[SRLevel]


class SRResponse(BaseModel):
    symbol: str
    timeframe: str
    levels: list[SRLevel]
