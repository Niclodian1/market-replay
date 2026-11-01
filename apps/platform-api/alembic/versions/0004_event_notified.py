"""指标事件投递状态

Revision ID: 0004_event_notified
Revises: 0003_plan_indicator_subs
Create Date: 2026-10-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_event_notified"
down_revision = "0003_plan_indicator_subs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("indicator_events", sa.Column("notified_at", sa.BigInteger(), nullable=True))
    op.create_index("ix_indicator_events_notified_at", "indicator_events", ["notified_at"])


def downgrade() -> None:
    op.drop_index("ix_indicator_events_notified_at", table_name="indicator_events")
    op.drop_column("indicator_events", "notified_at")
