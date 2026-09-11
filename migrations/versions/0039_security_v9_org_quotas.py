"""V9 安全与多租户（ADR-0139 P5）：org_quotas 配额配置表

组织配额三资源（存储字节 / 并发任务数 / 速率）的 per-org 覆盖配置。
NULL 列 = 落 env 默认值（跟随全局默认，非无限）。

Revision ID: 0039_security_v9_org_quotas
Revises: 0038_security_v9_lakehouse_org
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0039_security_v9_org_quotas'
down_revision: Union[str, Sequence[str], None] = '0038_security_v9_lakehouse_org'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'org_quotas'


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    columns = [
        sa.Column('org_id', sa.String(length=255), primary_key=True),
        sa.Column('max_storage_bytes', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  nullable=True),
        sa.Column('max_concurrent_tasks', sa.Integer(), nullable=True),
        sa.Column('rate_limit_per_min', sa.Integer(), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
    ]
    if _is_sqlite():
        op.create_table(TABLE, *columns)
    else:
        op.create_table(TABLE, *columns)


def downgrade() -> None:
    op.drop_table(TABLE)
