"""兼容 shim：真实实现已迁到 ``packages/indicator-core/indicator_core``。

保留 ``app.indicator_core`` 这个导入路径，是为了让后端、脚本、测试在迁移期
可以继续工作（Docker、CI、旧脚本都无需立刻改 import）。新代码请直接
``from indicator_core import ...``；等所有调用方切完再删这个 shim。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _find_package_dir() -> Path | None:
    override = os.environ.get("INDICATOR_CORE_PATH")
    candidates = []
    if override:
        candidates.append(Path(override))
    repo_root = os.environ.get("REPO_ROOT")
    if repo_root:
        candidates.append(Path(repo_root) / "packages" / "indicator-core")
    for parent in Path(__file__).resolve().parents:
        candidates.append(parent / "packages" / "indicator-core")
    for candidate in candidates:
        if (candidate / "indicator_core" / "__init__.py").exists():
            return candidate
    return None


_PACKAGE_DIR = _find_package_dir()
if _PACKAGE_DIR is not None and str(_PACKAGE_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_DIR))

_impl = importlib.import_module("indicator_core")

_SUBMODULES = (
    "spec",
    "registry",
    "series",
    "outcomes",
    "stats",
    "adapters",
    "adapters.chanlun",
    "adapters.ma",
    "adapters.macd",
)
for _name in _SUBMODULES:
    try:
        _module = importlib.import_module(f"indicator_core.{_name}")
    except ModuleNotFoundError:
        continue
    sys.modules[f"{__name__}.{_name}"] = _module

for _name in getattr(_impl, "__all__", []):
    globals()[_name] = getattr(_impl, _name)

__all__ = list(getattr(_impl, "__all__", []))
__doc__ = _impl.__doc__
