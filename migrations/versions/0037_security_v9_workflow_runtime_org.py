"""V9 安全与多租户（ADR-0139）：workflow runtime 6 表 org_id 落库

P1（foundation/security-tenancy-v9）：

- **租户数据面**（workflow_packages / instances / instance_nodes /
  events / node_reuse）：加 ``org_id VARCHAR(255)`` → 回填 → NOT NULL →
  索引（org_id, created_at；nodes 用 updated_at，无 created_at 列）。
- **控制面信任域**（workflow_workers）：仅加 nullable 列（NULL = 服务
  全部租户；与 geocompute worker 表同纪律）。

回填映射（projects/conversations/users 的 id 均为字符串，无 CAST 歧义）：
1. instances/packages：project_id → projects.org_id；
2. instances：session_id → conversations.user_id → users.org_id；
3. instance_nodes / events：instance_id → workflow_instances.org_id；
4. node_reuse：source_instance_id → instance org，fallback
   artifact_session_id → 会话归属人 org，再 fallback default；
5. 其余（哈希 owner_scope-only 行）→ default 组织（0036 已 ensure）。

Revision ID: 0037_security_v9_workflow_runtime_org
Revises: 0036_security_v9_geocompute_org
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0037_security_v9_workflow_runtime_org'
down_revision: Union[str, Sequence[str], None] = '0036_security_v9_geocompute_org'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = {
    'workflow_packages': 'created_at',
    'workflow_instances': 'created_at',
    'workflow_instance_nodes': 'updated_at',
    'workflow_events': 'created_at',
    'workflow_node_reuse': 'created_at',
}
CONTROL_TABLES = ['workflow_workers']

DEFAULT_ORG_EXPR = (
    "(SELECT CAST(id AS VARCHAR) FROM organizations WHERE slug = 'default')"
)

INDEXES = [
    ('idx_wf_pkg_org_created', 'workflow_packages', ['org_id', 'created_at']),
    ('idx_wf_inst_org_created', 'workflow_instances', ['org_id', 'created_at']),
    ('idx_wf_node_org_updated', 'workflow_instance_nodes', ['org_id', 'updated_at']),
    ('idx_wf_event_org_created', 'workflow_events', ['org_id', 'created_at']),
    ('idx_wf_reuse_org_created', 'workflow_node_reuse', ['org_id', 'created_at']),
]


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def _add_columns() -> None:
    for table in TENANT_TABLES:
        op.add_column(table, sa.Column('org_id', sa.String(length=255), nullable=True))
    for table in CONTROL_TABLES:
        op.add_column(table, sa.Column('org_id', sa.String(length=255), nullable=True))


def _backfill() -> None:
    # instances：project → project.org
    op.execute(
        "UPDATE workflow_instances SET org_id = ("
        "  SELECT CAST(p.org_id AS VARCHAR) FROM projects p"
        "  WHERE p.id = workflow_instances.project_id AND p.org_id IS NOT NULL"
        ") WHERE org_id IS NULL"
    )
    # instances：session → 会话归属人 org
    op.execute(
        "UPDATE workflow_instances SET org_id = ("
        "  SELECT CAST(u.org_id AS VARCHAR) FROM conversations c"
        "  JOIN users u ON u.id = c.user_id"
        "  WHERE c.id = workflow_instances.session_id AND u.org_id IS NOT NULL"
        ") WHERE org_id IS NULL"
    )
    op.execute(
        f"UPDATE workflow_instances SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL"
    )
    # packages：project → project.org；其余 default
    op.execute(
        "UPDATE workflow_packages SET org_id = ("
        "  SELECT CAST(p.org_id AS VARCHAR) FROM projects p"
        "  WHERE p.id = workflow_packages.project_id AND p.org_id IS NOT NULL"
        ") WHERE org_id IS NULL"
    )
    op.execute(
        f"UPDATE workflow_packages SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL"
    )
    # nodes / events：随 instance 继承；孤儿 default
    for child in ('workflow_instance_nodes', 'workflow_events'):
        op.execute(
            f"UPDATE {child} SET org_id = ("
            f"  SELECT i.org_id FROM workflow_instances i"
            f"  WHERE i.instance_id = {child}.instance_id"
            ") WHERE org_id IS NULL"
        )
        op.execute(f"UPDATE {child} SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL")
    # node_reuse：source_instance → instance org；artifact 会话归属人；default
    op.execute(
        "UPDATE workflow_node_reuse SET org_id = ("
        "  SELECT i.org_id FROM workflow_instances i"
        "  WHERE i.instance_id = workflow_node_reuse.source_instance_id"
        ") WHERE org_id IS NULL"
    )
    op.execute(
        "UPDATE workflow_node_reuse SET org_id = ("
        "  SELECT CAST(u.org_id AS VARCHAR) FROM conversations c"
        "  JOIN users u ON u.id = c.user_id"
        "  WHERE c.id = workflow_node_reuse.artifact_session_id AND u.org_id IS NOT NULL"
        ") WHERE org_id IS NULL"
    )
    op.execute(
        f"UPDATE workflow_node_reuse SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL"
    )


def _enforce_not_null_and_indexes() -> None:
    for index_name, table, cols in INDEXES:
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.create_index(index_name, cols, unique=False)
                batch_op.alter_column('org_id', existing_type=sa.String(length=255), nullable=False)
        else:
            op.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({', '.join(cols)})"
            )
            op.execute(f"ALTER TABLE {table} ALTER COLUMN org_id SET NOT NULL")


def upgrade() -> None:
    _add_columns()
    _backfill()
    _enforce_not_null_and_indexes()


def downgrade() -> None:
    for index_name, table, _cols in reversed(INDEXES):
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_index(index_name)
        else:
            op.execute(f"DROP INDEX IF EXISTS {index_name}")

    for table in CONTROL_TABLES:
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column('org_id')
        else:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS org_id")

    for table in reversed(list(TENANT_TABLES)):
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column('org_id')
        else:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS org_id")
