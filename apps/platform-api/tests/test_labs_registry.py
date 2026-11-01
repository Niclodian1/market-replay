"""实验登记表契约：registry.yaml 与 experiments/ 目录、脚本路径保持一致。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY = REPO_ROOT / "labs" / "experiments" / "registry.yaml"
EXPERIMENTS_DIR = REPO_ROOT / "experiments"
VALID_STATUS = {"active", "promoted", "archived"}
REQUIRED_FIELDS = {"id", "title", "hypothesis", "status", "data", "command", "artifacts"}


def _entries() -> list[dict]:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    return list(data["experiments"])


def test_registry_parses_and_has_required_fields() -> None:
    entries = _entries()
    assert entries, "登记表不能为空"
    for entry in entries:
        missing = REQUIRED_FIELDS - set(entry)
        assert not missing, f"{entry.get('id')} 缺字段 {missing}"
        assert entry["status"] in VALID_STATUS, entry["id"]
        assert isinstance(entry["data"], list) and entry["data"], entry["id"]
        if entry["status"] in {"promoted", "archived"}:
            assert entry.get("conclusion_date"), f"{entry['id']} 已定稿但没写 conclusion_date"
            assert entry.get("conclusion"), f"{entry['id']} 已定稿但没写 conclusion"


def test_registry_ids_unique() -> None:
    ids = [entry["id"] for entry in _entries()]
    assert len(ids) == len(set(ids)), f"重复 id: {ids}"


def test_every_experiment_is_registered() -> None:
    """experiments/ 下的每个目录与顶层脚本都必须有登记（不许有结论不明的实验漂在外面）。"""
    registered = " ".join(entry["command"] for entry in _entries())
    artifact_suffixes = {".xlsx", ".png", ".csv", ".json", ".md", ".parquet", ".html"}
    for path in sorted(EXPERIMENTS_DIR.iterdir()):
        if path.name in {"specs", "__pycache__"} or path.name.startswith("."):
            continue
        if path.is_file() and path.suffix in artifact_suffixes:
            continue  # 产物/说明文件，不是实验
        assert path.name in registered, f"{path.name} 没有登记到 labs/experiments/registry.yaml"


def test_registry_commands_point_to_existing_files() -> None:
    for entry in _entries():
        parts = entry["command"].split()
        assert parts[0].startswith("python"), entry["id"]
        target = REPO_ROOT / parts[1]
        assert target.exists(), f"{entry['id']} 命令指向的文件不存在: {parts[1]}"


def test_artifacts_paths_are_declared_or_null() -> None:
    for entry in _entries():
        artifacts = entry["artifacts"]
        if artifacts is None:
            continue
        assert isinstance(artifacts, str) and artifacts
