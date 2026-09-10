"""GeoCompute V8 distributed spatial compute fabric: ledger dims / artifacts / quarantine

Revision ID: 0035_geocompute_v8_fabric
Revises: c0d8322aa2cb
Create Date: 2026-09-11

全部 additive（可空/带缺省列 + 新表），无数据改写（repo convention，0034
同款 create_all-coexistence guard + 可重入 DDL）：

1. ``geocompute_runs.reserved_mem_mb / reserved_gpu``（NOT NULL DEFAULT 0）
   — 账本内存/GPU 维度预留的**精确归还**依据（与 reserved_rows/bytes/units
   同一纪律：认领时 reserve，reclaim/finish 同事务归还）。
2. ``geocompute_resource_usage.usage_mem_mb / limit_mem_mb / usage_gpu /
   limit_gpu`` — 账本 V8 两维：enforcing reserve 以内存估计做 OOM 预防
   （防「先启动再 OOM」）、以卡数计数防 GPU 池超卖。0/NULL 缺省 = V7
   行为逐字节兼容。
3. ``geocompute_artifacts`` — artifact exchange 元数据（内容寻址 BlobStore
   的登记投影；载荷字节真相仍在 BlobStore，本表只服务 cleanup/可观测）。
4. ``geocompute_task_quarantine`` — poison task 隔离登记（owner 域 +
   节点语义指纹复合主键；窗口过期查询侧惰性解封）。

downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0035_geocompute_v8_fabric"
down_revision: Union[str, Sequence[str], None] = "c0d8322aa2cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUN_TABLE = "geocompute_runs"
_USAGE_TABLE = "geocompute_resource_usage"
_ARTIFACT_TABLE = "geocompute_artifacts"
_QUARANTINE_TABLE = "geocompute_task_quarantine"

_ARTIFACT_INDEXES = {
    "idx_gc_artifact_run": ["run_id", "id"],
    "idx_gc_artifact_expiry": ["expires_at"],
}
_QUARANTINE_INDEXES = {
    "idx_gc_quarantine_until": ["quarantined_until"],
}


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _column_exists(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # 1) run 行：内存/GPU 预留归还依据（NOT NULL DEFAULT 0 = 旧行兼容）
    if _table_exists(_RUN_TABLE) and not _column_exists(_RUN_TABLE, "reserved_mem_mb"):
        op.add_column(_RUN_TABLE, sa.Column(
            "reserved_mem_mb",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False, server_default="0"))
    if _table_exists(_RUN_TABLE) and not _column_exists(_RUN_TABLE, "reserved_gpu"):
        op.add_column(_RUN_TABLE, sa.Column("reserved_gpu", sa.Integer(),
                                            nullable=False, server_default="0"))

    # 2) 账本：mem/gpu 两维（usage NOT NULL DEFAULT 0；limit NULL = 不设限）
    if _table_exists(_USAGE_TABLE) and not _column_exists(_USAGE_TABLE, "usage_mem_mb"):
        op.add_column(_USAGE_TABLE, sa.Column(
            "usage_mem_mb",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False, server_default="0"))
    if _table_exists(_USAGE_TABLE) and not _column_exists(_USAGE_TABLE, "limit_mem_mb"):
        op.add_column(_USAGE_TABLE, sa.Column(
            "limit_mem_mb",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=True))
    if _table_exists(_USAGE_TABLE) and not _column_exists(_USAGE_TABLE, "usage_gpu"):
        op.add_column(_USAGE_TABLE, sa.Column("usage_gpu", sa.Integer(),
                                              nullable=False, server_default="0"))
    if _table_exists(_USAGE_TABLE) and not _column_exists(_USAGE_TABLE, "limit_gpu"):
        op.add_column(_USAGE_TABLE, sa.Column("limit_gpu", sa.Integer(),
                                              nullable=True))

    # 3) geocompute_artifacts（V8 artifact exchange 元数据）
    if not _table_exists(_ARTIFACT_TABLE):
        op.create_table(
            _ARTIFACT_TABLE,
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      autoincrement=True, primary_key=True),
            sa.Column("artifact_key", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=True),
            sa.Column("owner_scope", sa.String(length=40), nullable=True),
            sa.Column("kind", sa.String(length=20), nullable=False,
                      server_default="payload"),
            sa.Column("codec", sa.String(length=10), nullable=False,
                      server_default="raw"),
            sa.Column("size_bytes",
                      sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      nullable=False, server_default="0"),
            sa.Column("stored_at", sa.DateTime(), nullable=False),
            sa.Column("last_hit_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("artifact_key", name="uq_gc_artifact_key"),
        )
    for name, cols in _ARTIFACT_INDEXES.items():
        if _table_exists(_ARTIFACT_TABLE) and not _index_exists(_ARTIFACT_TABLE, name):
            op.create_index(name, _ARTIFACT_TABLE, cols)

    # 4) geocompute_task_quarantine（V8 poison 隔离登记）
    if not _table_exists(_QUARANTINE_TABLE):
        op.create_table(
            _QUARANTINE_TABLE,
            sa.Column("owner_scope", sa.String(length=40), primary_key=True),
            sa.Column("task_fingerprint", sa.String(length=64), primary_key=True),
            sa.Column("failure_count", sa.Integer(), nullable=False,
                      server_default="0"),
            sa.Column("last_error_code", sa.String(length=64), nullable=True),
            sa.Column("last_run_id", sa.String(length=64), nullable=True),
            sa.Column("quarantined_until", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    for name, cols in _QUARANTINE_INDEXES.items():
        if _table_exists(_QUARANTINE_TABLE) and not _index_exists(_QUARANTINE_TABLE, name):
            op.create_index(name, _QUARANTINE_TABLE, cols)


def downgrade() -> None:
    for name in _QUARANTINE_INDEXES:
        if _table_exists(_QUARANTINE_TABLE) and _index_exists(_QUARANTINE_TABLE, name):
            op.drop_index(name, table_name=_QUARANTINE_TABLE)
    if _table_exists(_QUARANTINE_TABLE):
        op.drop_table(_QUARANTINE_TABLE)
    for name in _ARTIFACT_INDEXES:
        if _table_exists(_ARTIFACT_TABLE) and _index_exists(_ARTIFACT_TABLE, name):
            op.drop_index(name, table_name=_ARTIFACT_TABLE)
    if _table_exists(_ARTIFACT_TABLE):
        op.drop_table(_ARTIFACT_TABLE)
    if _table_exists(_USAGE_TABLE) and _column_exists(_USAGE_TABLE, "limit_gpu"):
        op.drop_column(_USAGE_TABLE, "limit_gpu")
    if _table_exists(_USAGE_TABLE) and _column_exists(_USAGE_TABLE, "usage_gpu"):
        op.drop_column(_USAGE_TABLE, "usage_gpu")
    if _table_exists(_USAGE_TABLE) and _column_exists(_USAGE_TABLE, "limit_mem_mb"):
        op.drop_column(_USAGE_TABLE, "limit_mem_mb")
    if _table_exists(_USAGE_TABLE) and _column_exists(_USAGE_TABLE, "usage_mem_mb"):
        op.drop_column(_USAGE_TABLE, "usage_mem_mb")
    if _table_exists(_RUN_TABLE) and _column_exists(_RUN_TABLE, "reserved_gpu"):
        op.drop_column(_RUN_TABLE, "reserved_gpu")
    if _table_exists(_RUN_TABLE) and _column_exists(_RUN_TABLE, "reserved_mem_mb"):
        op.drop_column(_RUN_TABLE, "reserved_mem_mb")
