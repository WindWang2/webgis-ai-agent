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


#: PG 回填分批大小（#1306：避免单事务全表 UPDATE 长时间锁热表）。
_BACKFILL_BATCH = 5000


def _progress_guard(set_clause: str) -> str:
    """``col = expr`` 的「值真的会变」判据（PG ``IS DISTINCT FROM``）。

    owner_type='project'/'session' 但 owner_id 无对应行、projects.org_id /
    users.org_id 为 NULL、dataset_row_id 孤儿时子查询返回 NULL，SET 后
    org_id 仍为 NULL、行仍满足 where —— rowcount 恒 >0，循环打到
    1_000_000 硬帽（每批还是一个独立 autocommit 事务，PG 升级事实挂死）。
    把批选区收紧到「值会变」的行后，rowcount 即本轮真实改行数，循环自然
    收敛；解析不出的行留给随后的兜底回填写入。非 ``col = expr`` 形态时
    返回空串（退回原语义）。
    """
    column, sep, expression = set_clause.partition("=")
    column = column.strip()
    if not sep or not column.isidentifier():
        return ""
    return f" AND ({column} IS DISTINCT FROM {expression.strip()})"


def _exec_update_batched(table: str, set_clause: str, where_clause: str) -> None:
    """PostgreSQL 按 ctid 分批 UPDATE；SQLite 全量一次。

    每批 ``autocommit_block``，缩短锁持有窗口（#1306）。
    """
    if _is_sqlite():
        op.execute(f"UPDATE {table} SET {set_clause} WHERE {where_clause}")
        return
    select_where = f"({where_clause}){_progress_guard(set_clause)}"
    sql = (
        f"UPDATE {table} SET {set_clause} "
        f"WHERE ({where_clause}) AND ctid IN ("
        f"  SELECT ctid FROM {table} WHERE {select_where} LIMIT {_BACKFILL_BATCH}"
        f")"
    )
    bind = op.get_bind()
    # 硬帽：防止驱动 rowcount 异常导致死循环
    for _ in range(1_000_000):
        with op.get_context().autocommit_block():
            rc = bind.execute(sa.text(sql)).rowcount or 0
        if rc <= 0:
            break


def _pg_create_index_concurrently(index_name: str, table: str, cols: list) -> None:
    """CREATE INDEX CONCURRENTLY（事务外；#1306）。"""
    col_sql = ", ".join(cols)
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
            f"ON {table} ({col_sql})"
        )



def _owner_backfill_sql(table: str) -> None:
    """datasets / catalog：owner_type+owner_id → org（#1306 分批）。"""
    _exec_update_batched(
        table,
        f"org_id = ("
        f"  SELECT CAST(p.org_id AS VARCHAR) FROM projects p"
        f"  WHERE p.id = {table}.owner_id AND p.org_id IS NOT NULL"
        f")",
        "org_id IS NULL AND owner_type = 'project'",
    )
    _exec_update_batched(
        table,
        f"org_id = ("
        f"  SELECT CAST(u.org_id AS VARCHAR) FROM conversations c"
        f"  JOIN users u ON u.id = c.user_id"
        f"  WHERE c.id = {table}.owner_id AND u.org_id IS NOT NULL"
        f")",
        "org_id IS NULL AND owner_type = 'session'",
    )
    _exec_update_batched(table, f"org_id = {DEFAULT_ORG_EXPR}", "org_id IS NULL")


def _column_exists(table: str, column: str) -> bool:
    """inspect 守卫：#2207 —— 幂等/可重试（同 0036/0037）。半途失败后重试
    若再 add_column 即 DuplicateColumn；列已存在则跳过。"""
    insp = sa.inspect(op.get_bind())
    if table not in set(insp.get_table_names()):
        return False
    return any(c.get("name") == column for c in insp.get_columns(table))


def upgrade() -> None:
    for table in TENANT_TABLES:
        if _column_exists(table, 'org_id'):
            continue  # 重试幂等（#2207）：列已落库则跳过
        op.add_column(table, sa.Column('org_id', sa.String(length=255), nullable=True))

    _owner_backfill_sql('lakehouse_datasets')
    _owner_backfill_sql('lakehouse_catalog_items')

    # versions / refs：随 dataset 行继承
    for child in ('lakehouse_dataset_versions', 'lakehouse_dataset_refs'):
        _exec_update_batched(
            child,
            f"org_id = ("
            f"  SELECT d.org_id FROM lakehouse_datasets d"
            f"  WHERE d.id = {child}.dataset_row_id"
            ")",
            "org_id IS NULL",
        )
        _exec_update_batched(child, f"org_id = {DEFAULT_ORG_EXPR}", "org_id IS NULL")

    for index_name, table, cols in INDEXES:
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.create_index(index_name, cols, unique=False)
                batch_op.alter_column('org_id', existing_type=sa.String(length=255), nullable=False)
        else:
            _pg_create_index_concurrently(index_name, table, cols)
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
