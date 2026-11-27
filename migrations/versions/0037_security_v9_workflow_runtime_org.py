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


#: PG 回填分批大小（#1306：避免单事务全表 UPDATE 长时间锁热表）。
_BACKFILL_BATCH = 5000


def _progress_guard(set_clause: str) -> str:
    """``col = expr`` 的「值真的会变」判据（PG ``IS DISTINCT FROM``）。

    project_id 为空、project/users 无 org、instance_id/source_instance_id
    孤儿时子查询返回 NULL，SET 后 org_id 仍为 NULL、行仍满足 where ——
    rowcount 恒 >0，循环打到 1_000_000 硬帽（每批还是一个独立 autocommit
    事务，PG 升级事实挂死）。把批选区收紧到「值会变」的行后，rowcount 即
    本轮真实改行数，循环自然收敛；解析不出的行留给随后的兜底回填写入。
    非 ``col = expr`` 形态时返回空串（退回原语义）。
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



def _column_exists(table: str, column: str) -> bool:
    """inspect 守卫：#2207 —— 幂等/可重试（同 0036）。半途失败后重试若再
    add_column 即 DuplicateColumn；列已存在则跳过。"""
    insp = sa.inspect(op.get_bind())
    if table not in set(insp.get_table_names()):
        return False
    return any(c.get("name") == column for c in insp.get_columns(table))


def _add_columns() -> None:
    for table in TENANT_TABLES:
        if _column_exists(table, 'org_id'):
            continue  # 重试幂等（#2207）：列已落库则跳过
        op.add_column(table, sa.Column('org_id', sa.String(length=255), nullable=True))
    for table in CONTROL_TABLES:
        if _column_exists(table, 'org_id'):
            continue  # 重试幂等（#2207）：列已落库则跳过
        op.add_column(table, sa.Column('org_id', sa.String(length=255), nullable=True))


def _backfill() -> None:
    # instances：project → project.org（#1306 分批）
    _exec_update_batched(
        "workflow_instances",
        "org_id = ("
        "  SELECT CAST(p.org_id AS VARCHAR) FROM projects p"
        "  WHERE p.id = workflow_instances.project_id AND p.org_id IS NOT NULL"
        ")",
        "org_id IS NULL",
    )
    # instances：session → 会话归属人 org
    _exec_update_batched(
        "workflow_instances",
        "org_id = ("
        "  SELECT CAST(u.org_id AS VARCHAR) FROM conversations c"
        "  JOIN users u ON u.id = c.user_id"
        "  WHERE c.id = workflow_instances.session_id AND u.org_id IS NOT NULL"
        ")",
        "org_id IS NULL",
    )
    _exec_update_batched(
        "workflow_instances",
        f"org_id = {DEFAULT_ORG_EXPR}",
        "org_id IS NULL",
    )
    # packages：project → project.org；其余 default
    _exec_update_batched(
        "workflow_packages",
        "org_id = ("
        "  SELECT CAST(p.org_id AS VARCHAR) FROM projects p"
        "  WHERE p.id = workflow_packages.project_id AND p.org_id IS NOT NULL"
        ")",
        "org_id IS NULL",
    )
    _exec_update_batched(
        "workflow_packages",
        f"org_id = {DEFAULT_ORG_EXPR}",
        "org_id IS NULL",
    )
    # nodes / events：随 instance 继承；孤儿 default
    for child in ('workflow_instance_nodes', 'workflow_events'):
        _exec_update_batched(
            child,
            f"org_id = ("
            f"  SELECT i.org_id FROM workflow_instances i"
            f"  WHERE i.instance_id = {child}.instance_id"
            ")",
            "org_id IS NULL",
        )
        _exec_update_batched(child, f"org_id = {DEFAULT_ORG_EXPR}", "org_id IS NULL")
    # node_reuse：source_instance → instance org；artifact 会话归属人；default
    _exec_update_batched(
        "workflow_node_reuse",
        "org_id = ("
        "  SELECT i.org_id FROM workflow_instances i"
        "  WHERE i.instance_id = workflow_node_reuse.source_instance_id"
        ")",
        "org_id IS NULL",
    )
    _exec_update_batched(
        "workflow_node_reuse",
        "org_id = ("
        "  SELECT CAST(u.org_id AS VARCHAR) FROM conversations c"
        "  JOIN users u ON u.id = c.user_id"
        "  WHERE c.id = workflow_node_reuse.artifact_session_id AND u.org_id IS NOT NULL"
        ")",
        "org_id IS NULL",
    )
    _exec_update_batched(
        "workflow_node_reuse",
        f"org_id = {DEFAULT_ORG_EXPR}",
        "org_id IS NULL",
    )


def _enforce_not_null_and_indexes() -> None:
    for index_name, table, cols in INDEXES:
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.create_index(index_name, cols, unique=False)
                batch_op.alter_column('org_id', existing_type=sa.String(length=255), nullable=False)
        else:
            _pg_create_index_concurrently(index_name, table, cols)
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
