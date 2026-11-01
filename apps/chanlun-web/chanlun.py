"""兼容 shim：真实引擎已迁到 ``packages/chanlun-core/chanlun_core``。

``apps/chanlun-web`` 下的旧脚本、后端 ``chanlun_service`` 仍可 ``import chanlun``。
新代码请 ``from chanlun_core import analyze, link_levels``。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _package_dir() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "packages" / "chanlun-core"
        if (candidate / "chanlun_core" / "__init__.py").exists():
            return candidate
    return None


_PKG = _package_dir()
if _PKG is not None and str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

_impl = importlib.import_module("chanlun_core.chanlun")
for _name, _value in vars(_impl).items():
    if not _name.startswith("__"):
        globals()[_name] = _value
sys.modules[__name__ + ".fractals"] = importlib.import_module("chanlun_core.fractals")
