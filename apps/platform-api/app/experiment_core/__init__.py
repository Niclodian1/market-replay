"""实验内核：声明式 spec → 撮合 → 交易/净值/指标。"""
from .runner import ExperimentResult, Trade, run_experiment
from .spec import SPEC_SCHEMA, SPEC_SCHEMA_VERSION, ExperimentSpec, parse_spec, validate_spec

__all__ = [
    "SPEC_SCHEMA",
    "SPEC_SCHEMA_VERSION",
    "ExperimentResult",
    "ExperimentSpec",
    "Trade",
    "parse_spec",
    "run_experiment",
    "validate_spec",
]
