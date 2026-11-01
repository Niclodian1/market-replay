"""让 `import app...` 与 `import scripts...` 在从任意目录运行 pytest 时都能工作。"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parents[1]
# 本地测试依赖（pip install --target apps/platform-api/.testdeps），CI 里由 requirements-dev 安装
TESTDEPS_DIR = BACKEND_DIR / ".testdeps"
for p in (str(BACKEND_DIR), str(REPO_ROOT), str(TESTDEPS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
