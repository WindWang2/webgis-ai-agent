"""Workflow V6 durable: events journal + node lease/heartbeat/cancel/retry-gate

Revision ID: 0035_workflow_v6_durable
Revises: c0d8322aa2cb
Create Date: 2026-09-10

Epic workflow-v6（durable distributed runtime）Phase B —— additive DDL，
无数据改写：

1. ``workflow_events`` — append-only 事件日志（与状态转移同事务写入；
   replay/inspect/recovery 的完整历史真相。节点行内嵌 ``transitions`` 环
   只是调度热路径快照，两者互补不重复）。
2. ``workflow_workers`` — worker/driver 能力注册表（CPU/内存/GPU/
   profile 槽位/IO/后端 + 心跳活性；workflow 调度域自有事实，与
   geocompute cluster worker 表互不重复）。
3. ``workflow_instance_nodes`` 新列（Workflow V6 两级租约与严格取消/重试）：
   - ``lease_expires_at`` / ``heartbeat_at`` — 节点级租约（执行者 claim 时
     写入；worker 死亡 → 租约过期 → 孤儿接管。与 run 级租约独立：coordinator
     存活 ≠ worker 存活）；
   - ``cancel_requested`` — 节点级取消旗标（后代取消/分布式取消的持久事实）；
   - ``next_ready_at`` — 重试退避门（FAILED→READY 重排队的最早时刻）。

全部 create_all-coexistence guard 保护的**可重入** DDL（repo convention，
0022-0035 同款）。downgrade 反序回滚（新列可安全 DROP；journal 表整表 DROP
—— 事件日志丢弃可接受：它是观测面，不是状态机真相）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0035_workflow_v6_durable"
down_revision: Union[str, Sequence[str], None] = "c0d8322aa2cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NODE_TABLE = "workflow_instance_nodes"
_EVENT_TABLE = "workflow_events"
_WORKER_TABLE = "workflow_workers"

_WORKER_CHECKS = (
    ("ck_wf_worker_role", "role IN ('driver','worker')"),
    ("ck_wf_worker_status", "status IN ('active','stale','retired')"),
)
_WORKER_INDEXES = {
    "idx_wf_worker_status_hb": ["status", "last_heartbeat_at"],
}

_NODE_NEW_COLUMNS = (
    ("lease_expires_at", sa.DateTime(), True, None),
    ("heartbeat_at", sa.DateTime(), True, None),
    ("cancel_requested", sa.Boolean(), False, sa.false()),
    ("next_ready_at", sa.DateTime(), True, None),
)

_EVENT_INDEXES = {
    "idx_wf_event_inst_id": ["instance_id", "id"],
    "idx_wf_event_inst_kind": ["instance_id", "kind"],
}
_NODE_INDEXES = {
    "idx_wf_node_lease": ["instance_id", "state", "lease_expires_at"],
}


def _table_exists(table: str) -> bool:
    """create_all-coexistence guard（repo convention）。"""
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def _column_exists(table: str, column: str) -> bool:
    return column in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)
    }


def _create_table(table: str, *columns, checks=(), indexes=None) -> None:
    """建表（CHECK 内联 —— SQLite 不可 ALTER 约束；0034 同款守卫）。"""
    if _table_exists(table):
        return
    args = list(columns) + [
        sa.CheckConstraint(expr, name=name) for name, expr in checks
    ]
    op.create_table(table, *args)
    for name, cols in (indexes or {}).items():
        if not _index_exists(table, name):
            op.create_index(name, table, cols)


def upgrade() -> None:
    # 0) worker 注册表（additive）
    _create_table(
        _WORKER_TABLE,
        sa.Column("worker_id", sa.String(length=64), primary_key=True),
        sa.Column("role", sa.String(length=16), nullable=False,
                  server_default="worker"),
        sa.Column("status", sa.String(length=16), nullable=False,
                  server_default="active"),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("load", sa.JSON(), nullable=False),
        sa.Column("runtime", sa.String(length=24), nullable=False,
                  server_default="inprocess"),
        sa.Column("locality", sa.JSON(), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        checks=_WORKER_CHECKS,
        indexes=_WORKER_INDEXES,
    )

    # 1) journal 表（additive）
    if not _table_exists(_EVENT_TABLE):
        op.create_table(
            _EVENT_TABLE,
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      autoincrement=True, primary_key=True),
            sa.Column("instance_id", sa.String(length=64), nullable=False),
            sa.Column("node_id", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("from_state", sa.String(length=16), nullable=False,
                      server_default=""),
            sa.Column("to_state", sa.String(length=16), nullable=False,
                      server_default=""),
            sa.Column("reason", sa.String(length=96), nullable=False,
                      server_default=""),
            sa.Column("actor", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("attempt", sa.Integer(), nullable=False,
                      server_default=sa.text("0")),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    for name, cols in _EVENT_INDEXES.items():
        if not _index_exists(_EVENT_TABLE, name):
            op.create_index(name, _EVENT_TABLE, cols)

    # 2) 节点表新列（SQLite ADD COLUMN 安全：全部带 NULL 或 server_default）
    for name, col_type, nullable, server_default in _NODE_NEW_COLUMNS:
        if _column_exists(_NODE_TABLE, name):
            continue
        column = sa.Column(name, col_type, nullable=nullable)
        if server_default is not None:
            column.server_default = server_default
        op.add_column(_NODE_TABLE, column)
    for name, cols in _NODE_INDEXES.items():
        if not _index_exists(_NODE_TABLE, name):
            op.create_index(name, _NODE_TABLE, cols)


def downgrade() -> None:
    if _table_exists(_WORKER_TABLE):
        op.drop_table(_WORKER_TABLE)
    for name in _NODE_INDEXES:
        if _index_exists(_NODE_TABLE, name):
            op.drop_index(name, table_name=_NODE_TABLE)
    for name, _t, _n, _d in reversed(_NODE_NEW_COLUMNS):
        if _column_exists(_NODE_TABLE, name):
            op.drop_column(_NODE_TABLE, name)
    for name in _EVENT_INDEXES:
        if _index_exists(_EVENT_TABLE, name):
            op.drop_index(name, table_name=_EVENT_TABLE)
    if _table_exists(_EVENT_TABLE):
        op.drop_table(_EVENT_TABLE)
