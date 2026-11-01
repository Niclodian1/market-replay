"""仓库分区：指标实现位于 packages/indicator-core，app.indicator_core 只是 shim。"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_indicator_core_lives_in_packages() -> None:
    package = REPO_ROOT / "packages" / "indicator-core" / "indicator_core" / "__init__.py"
    assert package.exists()
    assert (REPO_ROOT / "packages" / "indicator-core" / "pyproject.toml").exists()


def test_shim_points_at_the_same_objects() -> None:
    sys.path.insert(0, str(REPO_ROOT / "packages" / "indicator-core"))
    canonical = importlib.import_module("indicator_core")
    shim = importlib.import_module("app.indicator_core")
    assert shim.Event is canonical.Event
    assert shim.IndicatorRegistry is canonical.IndicatorRegistry
    assert shim.params_hash is canonical.params_hash

    canonical_series = importlib.import_module("indicator_core.series")
    shim_series = importlib.import_module("app.indicator_core.series")
    assert shim_series.compute_series is canonical_series.compute_series
