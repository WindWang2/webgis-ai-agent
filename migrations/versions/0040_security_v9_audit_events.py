"""V9 安全与多租户（ADR-0139 P7）：audit_events 组织审计事件表

admin 动作 / 配额越限 / 安全事件的 durable 台账：actor / org / action /
target / trace_id（W3C traceparent 关联键）。fail-open：写入失败只记
结构化日志，绝不阻断主流程。

Revision ID: 0040_security_v9_audit_events
Revises: 0039_security_v9_org_quotas
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0040_security_v9_audit_events'
down_revision: Union[str, Sequence[str], None] = '0039_security_v9_org_quotas'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'audit_events'


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    columns = [
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  primary_key=True, autoincrement=True),
        sa.Column('org_id', sa.String(length=255), nullable=True),
        sa.Column('actor_id', sa.String(length=255), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('target_type', sa.String(length=40), nullable=True),
        sa.Column('target_id', sa.String(length=255), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column('trace_id', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
    ]
    op.create_table(TABLE, *columns)
    op.create_index('idx_audit_org_created', TABLE, ['org_id', 'created_at'])
    op.create_index('idx_audit_actor_created', TABLE, ['actor_id', 'created_at'])
    op.create_index('idx_audit_action', TABLE, ['action'])


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.drop_index('idx_audit_action')
            batch_op.drop_index('idx_audit_actor_created')
            batch_op.drop_index('idx_audit_org_created')
    else:
        op.execute("DROP INDEX IF EXISTS idx_audit_action")
        op.execute("DROP INDEX IF EXISTS idx_audit_actor_created")
        op.execute("DROP INDEX IF EXISTS idx_audit_org_created")
    op.drop_table(TABLE)
