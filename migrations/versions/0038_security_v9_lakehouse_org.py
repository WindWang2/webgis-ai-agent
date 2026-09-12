"""V9 安全与多租户（ADR-0139）：lakehouse 4 表 org_id 落库

P1（foundation/security-tenancy-v9）：

- **租户数据面**（lakehouse_datasets / dataset_versions / dataset_refs /
  catalog_items）：加 ``org_id VARCHAR(255)`` → 回填 → NOT NULL → 索引
  (org_id, created_at)。
- lakehouse 无控制面表（GC/catalog 投影均按 owner 域强制隔离）。

回填映射：
1. datasets / catalog_items：owner_type='project' → projects.org_id；
   owner_type='session' → conversations.user_id → users.org_id；
   均不可解析 → default 组织（0036 已 ensure）；
2. dataset_versions / dataset_refs：dataset_row_id → datasets.org_id；
   孤儿行 → default。

Revision ID: 0038_security_v9_lakehouse_org
Revises: 0037_security_v9_workflow_runtime_org
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0038_security_v9_lakehouse_org'
down_revision: Union[str, Sequence[str], None] = '0037_security_v9_workflow_runtime_org'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = [
    'lakehouse_datasets',
    'lakehouse_dataset_versions',
    'lakehouse_dataset_refs',
    'lakehouse_catalog_items',
]

DEFAULT_ORG_EXPR = (
    "(SELECT CAST(id AS VARCHAR) FROM organizations WHERE slug = 'default')"
)

INDEXES = [
    ('idx_lh_ds_org_created', 'lakehouse_datasets', ['org_id', 'created_at']),
    ('idx_lh_dsv_org_created', 'lakehouse_dataset_versions', ['org_id', 'created_at']),
    ('idx_lh_dsr_org', 'lakehouse_dataset_refs', ['org_id', 'created_at']),
    ('idx_lh_cat_org_created', 'lakehouse_catalog_items', ['org_id', 'created_at']),
]


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def _owner_backfill_sql(table: str) -> None:
    """datasets / catalog：owner_type+owner_id → org。"""
    op.execute(
        f"UPDATE {table} SET org_id = ("
        f"  SELECT CAST(p.org_id AS VARCHAR) FROM projects p"
        f"  WHERE p.id = {table}.owner_id AND p.org_id IS NOT NULL"
        f") WHERE org_id IS NULL AND owner_type = 'project'"
    )
    op.execute(
        f"UPDATE {table} SET org_id = ("
        f"  SELECT CAST(u.org_id AS VARCHAR) FROM conversations c"
        f"  JOIN users u ON u.id = c.user_id"
        f"  WHERE c.id = {table}.owner_id AND u.org_id IS NOT NULL"
        f") WHERE org_id IS NULL AND owner_type = 'session'"
    )
    op.execute(
        f"UPDATE {table} SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL"
    )


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.add_column(table, sa.Column('org_id', sa.String(length=255), nullable=True))

    _owner_backfill_sql('lakehouse_datasets')
    _owner_backfill_sql('lakehouse_catalog_items')

    # versions / refs：随 dataset 行继承
    for child in ('lakehouse_dataset_versions', 'lakehouse_dataset_refs'):
        op.execute(
            f"UPDATE {child} SET org_id = ("
            f"  SELECT d.org_id FROM lakehouse_datasets d"
            f"  WHERE d.id = {child}.dataset_row_id"
            ") WHERE org_id IS NULL"
        )
        op.execute(
            f"UPDATE {child} SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL"
        )

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


def downgrade() -> None:
    for index_name, table, _cols in reversed(INDEXES):
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_index(index_name)
        else:
            op.execute(f"DROP INDEX IF EXISTS {index_name}")

    for table in reversed(TENANT_TABLES):
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column('org_id')
        else:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS org_id")
