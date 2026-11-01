"""baseline: 全量建表（与 app.models 当前元数据一致）

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-22

基线迁移直接使用 Base.metadata.create_all：
- 新环境（空库）：`alembic upgrade head` 建出与模型完全一致的 13 张表
- 既有生产库：不要 upgrade，执行 `alembic stamp head` 标记为已迁移
"""
from typing import Sequence, Union

from alembic import op

from app.db.base import Base
import app.models  # noqa: F401

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
