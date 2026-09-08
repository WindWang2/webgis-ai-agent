"""GeoCompute V6 cluster runtime: runs / workers / resource usage

Revision ID: 0032_geocompute_v6_cluster
Revises: 0031_revision_indexes
Create Date: 2026-09-08

Wave 2（.agent-work/geocompute-v6/02-plan.md）：cluster 控制面的三个
additive 新表，无数据改写：

1. ``geocompute_runs`` — run 级持久生命周期（此前 run 注册表是进程内存态，
   多副本下 cancel/读取只见本进程、进程崩溃后在飞 run 无记录）。本表只管
   **生命周期**：状态机/lease epoch fencing/心跳/取消旗标/优先级/attempt
   计数/plan 快照（≤256KB 写入侧上界）。节点 job 真相仍= analysis_tasks，
   终态证据仍= geocompute_run_evidence，载荷= session ref —— 三个既有
   真相域零复制。
2. ``geocompute_workers`` — worker/coordinator 注册（心跳 + profile 能力 +
   leadership epoch）。coordinator 行的 lease epoch CAS 就是 leadership
   仲裁（任一时刻仅一个 coordinator 持有调度权）。
3. ``geocompute_resource_usage`` — tenant/project/global 资源账本（run 粒度
   预留/归还，条件 UPDATE 记账）。集群层准入防 N coordinator 各自 L1
   governor 叠加成 N 倍全局限额；进程内 L1 树仍是执行进程的权威。

全部 create_all-coexistence guard 保护的**可重入** DDL（repo convention，
0022-0029 同款）；索引按模型声明逐一建出（漂移守卫
tests/test_deploy_migration_wiring.py 按列元组比对）。downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0032_geocompute_v6_cluster"
down_revision: Union[str, Sequence[str], None] = "0031_revision_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUN_TABLE = "geocompute_runs"
_WORKER_TABLE = "geocompute_workers"
_USAGE_TABLE = "geocompute_resource_usage"

_RUN_INDEXES = {
    "idx_gc_run_owner_id": ["owner_scope", "id"],
    "idx_gc_run_status_priority": ["status", "priority"],
    "idx_gc_run_lease_expiry": ["status", "lease_expires_at"],
    "idx_gc_run_tenant_dispatch": ["tenant_key", "dispatch_seq"],
}
_WORKER_INDEXES = {
    "idx_gc_worker_role_heartbeat": ["role", "heartbeat_at"],
}
_RUN_CHECKS = (
    ("ck_gc_run_status",
     "status IN ('queued','leased','running','completed','failed',"
     "'cancelled','preempted')"),
    ("ck_gc_run_priority", "priority IN (0,5,10)"),
)
_WORKER_CHECKS = (
    ("ck_gc_worker_role", "role IN ('coordinator','worker')"),
)


def _table_exists(table: str) -> bool:
    """create_all-coexistence guard（repo convention，0022-0031 同款）。"""
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _constraint_exists(table: str, name: str) -> bool:
    return name in {
        ck["name"]
        for ck in sa.inspect(op.get_bind()).get_check_constraints(table)
    }


def upgrade() -> None:
    if not _table_exists(_RUN_TABLE):
        op.create_table(
            _RUN_TABLE,
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      autoincrement=True, primary_key=True),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("owner_scope", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
            sa.Column("plan_fingerprint", sa.String(length=32), nullable=False),
            sa.Column("plan_snapshot", sa.JSON(), nullable=False),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("creator_id", sa.String(length=255), nullable=True),
            sa.Column("org_id", sa.String(length=255), nullable=True),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("tenant_key", sa.String(length=40), nullable=True),
            sa.Column("project_key", sa.String(length=40), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("preempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("coordinator_id", sa.String(length=128), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("cancel_requested_at", sa.DateTime(), nullable=True),
            sa.Column("yield_requested_at", sa.DateTime(), nullable=True),
            sa.Column("dispatch_seq", sa.Integer(), nullable=True),
            sa.Column("reserved_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reserved_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reserved_units", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("required_profiles", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("terminal_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("run_id", name="uq_gc_run_run_id"),
            sa.CheckConstraint(
                "status IN ('queued','leased','running','completed','failed',"
                "'cancelled','preempted')", name="ck_gc_run_status"),
            sa.CheckConstraint("priority IN (0,5,10)",
                               name="ck_gc_run_priority"),
        )
    for name, cols in _RUN_INDEXES.items():
        if not _index_exists(_RUN_TABLE, name):
            op.create_index(name, _RUN_TABLE, cols)
    # CHECK 约束（模型声明；repo 惯例 —— 词表兜底必须随迁移建出，
    # round1 M2：drift guard 不比约束，这里缺失即生产库裸奔）。
    # create_all 预存在表（无约束）→ SQLite 走 batch（表重建），PG 直改。
    if context.get_context().dialect.name == "sqlite":
        if _RUN_CHECKS and not all(
            _constraint_exists(_RUN_TABLE, n) for n, _e in _RUN_CHECKS
        ):
            with op.batch_alter_table(_RUN_TABLE, schema=None) as batch_op:
                for name, expr in _RUN_CHECKS:
                    if not _constraint_exists(_RUN_TABLE, name):
                        batch_op.create_check_constraint(name, _RUN_TABLE, expr)
    else:
        for name, expr in _RUN_CHECKS:
            if not _constraint_exists(_RUN_TABLE, name):
                op.create_check_constraint(name, _RUN_TABLE, expr)

    if not _table_exists(_WORKER_TABLE):
        op.create_table(
            _WORKER_TABLE,
            sa.Column("worker_id", sa.String(length=128), primary_key=True),
            sa.Column("role", sa.String(length=20), nullable=False, server_default="worker"),
            sa.Column("profiles", sa.JSON(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
            sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("info", sa.JSON(), nullable=True),
            sa.CheckConstraint("role IN ('coordinator','worker')",
                               name="ck_gc_worker_role"),
        )
    for name, cols in _WORKER_INDEXES.items():
        if not _index_exists(_WORKER_TABLE, name):
            op.create_index(name, _WORKER_TABLE, cols)
    if context.get_context().dialect.name == "sqlite":
        if not _constraint_exists(_WORKER_TABLE, "ck_gc_worker_role"):
            with op.batch_alter_table(_WORKER_TABLE, schema=None) as batch_op:
                batch_op.create_check_constraint(
                    "ck_gc_worker_role", _WORKER_TABLE,
                    "role IN ('coordinator','worker')")
    else:
        for name, expr in _WORKER_CHECKS:
            if not _constraint_exists(_WORKER_TABLE, name):
                op.create_check_constraint(name, _WORKER_TABLE, expr)

    if not _table_exists(_USAGE_TABLE):
        op.create_table(
            _USAGE_TABLE,
            sa.Column("scope_key", sa.String(length=80), primary_key=True),
            sa.Column("usage_rows", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("usage_bytes",
                      sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      nullable=False, server_default="0"),
            sa.Column("usage_units", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("limit_rows",
                      sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
            sa.Column("limit_bytes",
                      sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
            sa.Column("limit_units", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    if _table_exists(_WORKER_TABLE):
        if context.get_context().dialect.name == "sqlite":
            if _constraint_exists(_WORKER_TABLE, "ck_gc_worker_role"):
                with op.batch_alter_table(_WORKER_TABLE, schema=None) as b:
                    b.drop_constraint("ck_gc_worker_role", type_="check")
        else:
            for name, _expr in _WORKER_CHECKS:
                if _constraint_exists(_WORKER_TABLE, name):
                    op.drop_constraint(name, _WORKER_TABLE, type_="check")
    for name in _WORKER_INDEXES:
        if _table_exists(_WORKER_TABLE) and _index_exists(_WORKER_TABLE, name):
            op.drop_index(name, table_name=_WORKER_TABLE)
    if _table_exists(_WORKER_TABLE):
        op.drop_table(_WORKER_TABLE)
    if _table_exists(_RUN_TABLE):
        if context.get_context().dialect.name == "sqlite":
            with op.batch_alter_table(_RUN_TABLE, schema=None) as batch_op:
                for name, _expr in _RUN_CHECKS:
                    if _constraint_exists(_RUN_TABLE, name):
                        batch_op.drop_constraint(name, type_="check")
        else:
            for name, _expr in _RUN_CHECKS:
                if _constraint_exists(_RUN_TABLE, name):
                    op.drop_constraint(name, _RUN_TABLE, type_="check")
        for name in _RUN_INDEXES:
            if _index_exists(_RUN_TABLE, name):
                op.drop_index(name, table_name=_RUN_TABLE)
        op.drop_table(_RUN_TABLE)
    if _table_exists(_USAGE_TABLE):
        op.drop_table(_USAGE_TABLE)
