"""Unified durable job runtime: extend analysis_tasks into the durable job record

ADR-0052。additive 迁移：只加列/索引 + 放宽两处约束，不删列、不改写老数据行。
现有 analysis_tasks 行升级后依然合法（新列全部 nullable 或有 server_default）。

放宽的两处约束：
  1. org_id 由 NOT NULL 改为 nullable —— 匿名/个人会话产生的 job 没有组织归属，
     归属由 creator_id + owner_token + session_id 证明。
  2. ck_task_status 增加 'cancelling'（取消中，非终态）与 'stale'（worker 失联）。

SQLite 说明：SQLite 无法 ALTER 约束，必须重建表。这里用
``batch_alter_table(copy_from=<反射得到的表>)`` —— SQLAlchemy 不反射 SQLite 的 CHECK
约束，所以重建会隐式丢掉全部 CHECK，我们随后显式重建 ck_task_status（新定义）与
ck_task_progress（原定义）。PostgreSQL 走逐条 DDL + IF NOT EXISTS，可重复执行。

Revision ID: 0013_unified_durable_job_runtime
Revises: 0012_add_composite_indexes_pd_wr
Create Date: 2026-08-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0013_unified_durable_job_runtime"
down_revision: Union[str, Sequence[str], None] = "0012_add_composite_indexes_pd_wr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "analysis_tasks"


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


#: (column_name, 类型键, server_default)。全部 nullable，老行无需回填。
#: 类型键经 _TYPE_MAP / _PG_TYPES 按方言渲染 —— PostgreSQL 没有 DATETIME 类型，
#: 直接把 SQLite 的类型名拼进 DDL 会让整个 upgrade 失败。
NEW_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("job_kind", "VARCHAR(20)", "analysis"),
    ("display_name", "VARCHAR(200)", None),
    ("session_id", "VARCHAR(255)", None),
    ("owner_token", "VARCHAR(64)", None),
    ("project_id", "VARCHAR(255)", None),
    ("run_id", "VARCHAR(64)", None),
    ("turn_id", "VARCHAR(64)", None),
    ("tool_call_id", "VARCHAR(128)", None),
    ("agent_task_id", "VARCHAR(64)", None),
    ("agent_step_id", "VARCHAR(32)", None),
    ("idempotency_key", "VARCHAR(128)", None),
    ("attempt", "INTEGER", "1"),
    ("worker_id", "VARCHAR(128)", None),
    ("cancel_requested_at", "DATETIME", None),
    ("heartbeat_at", "DATETIME", None),
    ("result_ref", "VARCHAR(512)", None),
    ("dispatch_spec", "JSON", None),
)

#: PostgreSQL 的类型名映射（其余键与 SQLite 同名）。
_PG_TYPES: dict[str, str] = {
    "DATETIME": "TIMESTAMP",
    "JSON": "JSON",
}


def _pg_type(type_key: str) -> str:
    return _PG_TYPES.get(type_key, type_key)


_TYPE_MAP: dict[str, sa.types.TypeEngine] = {
    "VARCHAR(20)": sa.String(length=20),
    "VARCHAR(32)": sa.String(length=32),
    "VARCHAR(64)": sa.String(length=64),
    "VARCHAR(128)": sa.String(length=128),
    "VARCHAR(200)": sa.String(length=200),
    "VARCHAR(255)": sa.String(length=255),
    "VARCHAR(512)": sa.String(length=512),
    "INTEGER": sa.Integer(),
    "DATETIME": sa.DateTime(),
    "JSON": sa.JSON(),
}

#: 任务中心的查询路径 + stale 清扫路径。
NEW_INDEXES: tuple[tuple[str, list[str], bool], ...] = (
    ("idx_task_session_created", ["session_id", "created_at"], False),
    ("idx_task_creator_created", ["creator_id", "created_at"], False),
    ("idx_task_status_heartbeat", ["status", "heartbeat_at"], False),
    ("idx_task_agent_task", ["agent_task_id"], False),
    ("uq_analysis_tasks_idempotency_key", ["idempotency_key"], True),
)

OLD_STATUS_CHECK = "status IN ('pending', 'queued', 'running', 'completed', 'failed', 'cancelled')"
NEW_STATUS_CHECK = (
    "status IN ('pending', 'queued', 'running', 'cancelling', "
    "'completed', 'failed', 'cancelled', 'stale')"
)
PROGRESS_CHECK = "progress >= 0 AND progress <= 100"


def _reflected_table() -> sa.Table:
    """反射当前 analysis_tasks 结构，供 batch_alter_table 重建使用。"""
    meta = sa.MetaData()
    return sa.Table(TABLE, meta, autoload_with=op.get_bind())


def upgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table(TABLE, copy_from=_reflected_table(), schema=None) as batch_op:
            for name, type_key, default in NEW_COLUMNS:
                batch_op.add_column(
                    sa.Column(name, _TYPE_MAP[type_key], nullable=True, server_default=default)
                )
            batch_op.alter_column("org_id", existing_type=sa.Integer(), nullable=True)
            # e46935cd5dd1 只在 PostgreSQL 分支执行了 creator_id DROP NOT NULL，
            # SQLite 分支漏了 —— 模型声明 nullable=True 但本地 SQLite 仍是 NOT NULL。
            # 匿名会话的 job 没有 creator_id，这里把 SQLite 对齐到模型。
            batch_op.alter_column("creator_id", existing_type=sa.String(length=255), nullable=True)
            # SQLite 的 BIGINT 主键不是 rowid 别名（不自增）。durable job 现在是热
            # 路径，必须能自增插入 —— 重建时把 id 收敛为 INTEGER PRIMARY KEY。
            # PostgreSQL 分支不需要（BIGSERIAL 已有序列）。
            batch_op.alter_column(
                "id",
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=False,
                autoincrement=True,
            )
            # 重建被反射丢弃的 CHECK：status 用新定义，progress 保持原定义
            batch_op.create_check_constraint("ck_task_status", NEW_STATUS_CHECK)
            batch_op.create_check_constraint("ck_task_progress", PROGRESS_CHECK)
        for index_name, columns, unique in NEW_INDEXES:
            op.create_index(index_name, TABLE, columns, unique=unique)
        return

    # PostgreSQL
    for name, type_key, default in NEW_COLUMNS:
        default_sql = f" DEFAULT '{default}'" if default is not None else ""
        op.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {name} "
            f"{_pg_type(type_key)}{default_sql}"
        )

    op.execute(f"ALTER TABLE {TABLE} ALTER COLUMN org_id DROP NOT NULL")

    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS ck_task_status")
    op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_task_status CHECK ({NEW_STATUS_CHECK})")

    for index_name, columns, unique in NEW_INDEXES:
        cols = ", ".join(columns)
        kind = "UNIQUE INDEX" if unique else "INDEX"
        op.execute(f"CREATE {kind} IF NOT EXISTS {index_name} ON {TABLE} ({cols})")


def downgrade() -> None:
    """回滚到 0012。

    cancelling/stale 状态的行归一化为 failed —— 否则旧 ck_task_status 无法重建。
    org_id 的 NOT NULL 只在确实没有 NULL 行时恢复：期间产生过的匿名 job 不能被删除
    （规范 §39：迁移不得要求删除现有 task 数据）。
    """
    op.execute(f"UPDATE {TABLE} SET status = 'failed' WHERE status IN ('cancelling', 'stale')")

    # 先删依赖新列的索引，再删列
    for index_name, _columns, _unique in reversed(NEW_INDEXES):
        if _is_sqlite():
            op.drop_index(index_name, table_name=TABLE)
        else:
            op.execute(f"DROP INDEX IF EXISTS {index_name}")

    if _is_sqlite():
        with op.batch_alter_table(TABLE, copy_from=_reflected_table(), schema=None) as batch_op:
            for name, _type_key, _default in reversed(NEW_COLUMNS):
                batch_op.drop_column(name)
            batch_op.create_check_constraint("ck_task_status", OLD_STATUS_CHECK)
            batch_op.create_check_constraint("ck_task_progress", PROGRESS_CHECK)
        return

    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS ck_task_status")
    op.execute(f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_task_status CHECK ({OLD_STATUS_CHECK})")

    for name, _type_key, _default in reversed(NEW_COLUMNS):
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {name}")

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM analysis_tasks WHERE org_id IS NULL) THEN
                ALTER TABLE analysis_tasks ALTER COLUMN org_id SET NOT NULL;
            END IF;
        END $$;
        """
    )
