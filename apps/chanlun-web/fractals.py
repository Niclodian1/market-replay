"""兼容 shim：真实实现已迁到 ``packages/chanlun-core/chanlun_core/fractals.py``。"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    _candidate = _parent / "packages" / "chanlun-core"
    if (_candidate / "chanlun_core" / "__init__.py").exists():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

_impl = importlib.import_module("chanlun_core.fractals")
for _name, _value in vars(_impl).items():
    if not _name.startswith("__"):
        globals()[_name] = _value
