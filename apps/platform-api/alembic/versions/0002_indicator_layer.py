"""indicator layer: events / outcomes / experiments / indicator library

Revision ID: 0002_indicator_layer
Revises: 0001_baseline
Create Date: 2026-10-22

只创建新增的 8 张表；既有 13 张表由 0001 baseline 负责。
"""
from typing import Sequence, Union

from alembic import op

from app.db.base import Base
import app.models  # noqa: F401

revision: str = "0002_indicator_layer"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "indicator_events",
    "indicator_outcomes",
    "experiments",
    "experiment_runs",
    "experiment_trades",
    "indicators",
    "indicator_versions",
    "promotion_audit",
)


def _tables():
    return [Base.metadata.tables[name] for name in _TABLES]


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), tables=_tables())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=list(reversed(_tables())))
