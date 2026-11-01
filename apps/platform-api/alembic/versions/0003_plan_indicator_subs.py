"""监控计划订阅指标版本

Revision ID: 0003_plan_indicator_subs
Revises: 0002_indicator_layer
Create Date: 2026-10-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_plan_indicator_subs"
down_revision = "0002_indicator_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("monitor_plans", sa.Column("indicator_version_ids", sa.Text(), nullable=True))
    op.add_column("monitor_plans", sa.Column("indicator_cursor", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("monitor_plans", "indicator_cursor")
    op.drop_column("monitor_plans", "indicator_version_ids")
