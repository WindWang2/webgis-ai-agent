"""V9 安全与多租户（ADR-0139）：geocompute_* 9 表 org_id 落库

P1（foundation/security-tenancy-v9）：

- **默认组织**：确保 ``organizations`` 存在 ``slug='default'`` 行 —— 匿名
  owner_token 会话与无法解析归属的存量行统一归入该隔离桶（effective org）。
- **租户数据面**（geocompute_runs / run_events / node_results /
  run_evidence / artifacts）：加 ``org_id VARCHAR(255)``（0033 纪律：
  存 organizations.id 字符串原文、无 FK、跨库可移植）→ 回填 → NOT NULL
  → 索引 (org_id, created_at)（artifacts 用 stored_at，同语义）。
- **控制面信任域**（workers / worker_cache / resource_usage /
  task_quarantine）：仅加 nullable 列（NULL = 服务全部租户）——worker
  行不随 org 分裂；resource_usage/task_quarantine 的 scope 键是单向哈希
  不可逆推，故不回填。信任域边界论证见 docs/dev/security-tenancy-recon.md
  §3.2 与 ADR-0139。

回填顺序：runs（creator → user.org，兜底 default）先行，events/evidence/
artifacts 按 run_id 继承；node_results 按 session → conversation.owner →
user.org，兜底 default。

downgrade 完整可逆：租户数据面回 nullable（SQLite batch recreate）、
控制面与派生面直接 DROP COLUMN（artifacts/events/evidence/node_results
回 0035 无 org 形态；runs 回 0033 的 nullable 形态）。

Revision ID: 0036_security_v9_geocompute_org
Revises: c1e2f3a4b5c6
Create Date: 2026-09-11
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0036_security_v9_geocompute_org'
down_revision: Union[str, Sequence[str], None] = 'c1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (表名, 是否租户数据面[回填+NOT NULL+索引], 时间列名[索引用])
TENANT_TABLES = [
    ('geocompute_runs', True, 'created_at'),
    ('geocompute_run_events', True, 'created_at'),
    ('geocompute_node_results', True, 'created_at'),
    ('geocompute_run_evidence', True, 'created_at'),
    ('geocompute_artifacts', True, 'stored_at'),
]
CONTROL_TABLES = [
    'geocompute_workers',
    'geocompute_worker_cache',
    'geocompute_resource_usage',
    'geocompute_task_quarantine',
]

#: 默认组织 id 的字符串原文（organizations.id 自增；slug 唯一保证单行）。
DEFAULT_ORG_EXPR = (
    "(SELECT CAST(id AS VARCHAR) FROM organizations WHERE slug = 'default')"
)

INDEXES = [
    ('idx_gc_run_org_created', 'geocompute_runs', ['org_id', 'created_at']),
    ('idx_gc_event_org_created', 'geocompute_run_events', ['org_id', 'created_at']),
    ('idx_gc_node_result_org_created', 'geocompute_node_results', ['org_id', 'created_at']),
    ('idx_gc_run_evidence_org_created', 'geocompute_run_evidence', ['org_id', 'created_at']),
    ('idx_gc_artifact_org', 'geocompute_artifacts', ['org_id', 'stored_at']),
]


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def _ensure_default_org() -> None:
    """organizations 存在 slug='default' 行（幂等；TRUE/now 跨方言安全）。"""
    op.execute(
        "INSERT INTO organizations (name, slug, is_active, created_at, updated_at) "
        "SELECT 'Default Organization', 'default', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "WHERE NOT EXISTS (SELECT 1 FROM organizations WHERE slug = 'default')"
    )


def _add_columns() -> None:
    for table, _tenant, _ts in TENANT_TABLES:
        if table == 'geocompute_runs':
            continue  # 0033 已建列，本迁移只做回填+NOT NULL
        op.add_column(table, sa.Column('org_id', sa.String(length=255), nullable=True))
    for table in CONTROL_TABLES:
        op.add_column(table, sa.Column('org_id', sa.String(length=255), nullable=True))


def _backfill() -> None:
    # 1) runs：creator → user.org；匿名/无主 → default
    op.execute(
        "UPDATE geocompute_runs SET org_id = ("
        "  SELECT CAST(u.org_id AS VARCHAR) FROM users u"
        "  WHERE u.id = geocompute_runs.creator_id AND u.org_id IS NOT NULL"
        ") WHERE org_id IS NULL"
    )
    op.execute(
        f"UPDATE geocompute_runs SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL"
    )
    # 2) 事件/证据/artifact：按 run 继承；孤儿（run 已被 purge）→ default
    for child, run_id_col in (
        ('geocompute_run_events', 'run_id'),
        ('geocompute_run_evidence', 'run_id'),
        ('geocompute_artifacts', 'run_id'),
    ):
        op.execute(
            f"UPDATE {child} SET org_id = ("
            f"  SELECT r.org_id FROM geocompute_runs r"
            f"  WHERE r.run_id = {child}.{run_id_col}"
            ") WHERE org_id IS NULL"
        )
        op.execute(f"UPDATE {child} SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL")
    # 3) 节点复用索引：session → conversation 归属人 → user.org；兜底 default
    op.execute(
        "UPDATE geocompute_node_results SET org_id = ("
        "  SELECT CAST(u.org_id AS VARCHAR) FROM conversations c"
        "  JOIN users u ON u.id = c.user_id"
        "  WHERE c.id = geocompute_node_results.session_id AND u.org_id IS NOT NULL"
        ") WHERE org_id IS NULL"
    )
    op.execute(
        f"UPDATE geocompute_node_results SET org_id = {DEFAULT_ORG_EXPR} WHERE org_id IS NULL"
    )


def _enforce_not_null_and_indexes() -> None:
    for index_name, table, cols in INDEXES:
        col_sql = ", ".join(cols)
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                if table != 'geocompute_artifacts':
                    batch_op.create_index(index_name, cols, unique=False)
        else:
            op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({col_sql})")

    for table, tenant, _ts in TENANT_TABLES:
        if not tenant:
            continue
        if table == 'geocompute_artifacts':
            continue  # 索引已建；NOT NULL 见下（batch 分支统一处理）
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.alter_column('org_id', existing_type=sa.String(length=255), nullable=False)
        else:
            op.execute(f"ALTER TABLE {table} ALTER COLUMN org_id SET NOT NULL")

    # artifacts 的 NOT NULL 与索引顺序：batch recreate 一次完成两者，避免二次重建
    if _is_sqlite():
        with op.batch_alter_table('geocompute_artifacts', schema=None) as batch_op:
            batch_op.create_index('idx_gc_artifact_org', ['org_id', 'stored_at'], unique=False)
            batch_op.alter_column('org_id', existing_type=sa.String(length=255), nullable=False)
    else:
        op.execute("ALTER TABLE geocompute_artifacts ALTER COLUMN org_id SET NOT NULL")


def upgrade() -> None:
    _ensure_default_org()
    _add_columns()
    _backfill()
    _enforce_not_null_and_indexes()


def downgrade() -> None:
    # 回滚顺序与 upgrade 相逆：先索引、再 NOT NULL（租户面）/DROP（控制面）。
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

    for table, _tenant, _ts in reversed(
        [t for t in TENANT_TABLES if t[0] != 'geocompute_runs']
    ):
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_column('org_id')
        else:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS org_id")

    # runs 回 0033 形态（nullable，不删列——列是 0033 引入的）
    if _is_sqlite():
        with op.batch_alter_table('geocompute_runs', schema=None) as batch_op:
            batch_op.alter_column('org_id', existing_type=sa.String(length=255), nullable=True)
    else:
        op.execute("ALTER TABLE geocompute_runs ALTER COLUMN org_id DROP NOT NULL")
