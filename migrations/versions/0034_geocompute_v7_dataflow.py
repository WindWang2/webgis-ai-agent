"""GeoCompute V7 distributed dataflow: capability / events / worker cache

Revision ID: 0034_geocompute_v7_dataflow
Revises: 0033_geocompute_v6_cluster
Create Date: 2026-09-09

全部 additive（可空列 + 新表），无数据改写（01-architecture.md §1）：

1. ``geocompute_runs.resource_request``（JSON 可空）— run 级资源 envelope
   （ResourceRequest 投影，≤1KB 写入侧钳制）。可空 = V6 行为不变。
2. ``geocompute_workers.capability``（JSON 可空）— 能力剖面
   （WorkerCapabilityProfile 投影，≤4KB 写入侧钳制）。可空 = placement
   退化为 V6 的 profiles 匹配。
3. ``geocompute_run_events`` — 有界 observability trace（终态证据 of record
   仍是 geocompute_run_evidence；本表随 run retention 级联删除 + 独立 TTL）。
4. ``geocompute_worker_cache`` — worker 对象缓存的位置**声明**注册表
   （缓存本体在 worker 本地盘；一切不一致失败方向 = miss → 重物化）。

create_all-coexistence guard 保护的**可重入** DDL（repo convention，
0033 同款）。downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0034_geocompute_v7_dataflow"
down_revision: Union[str, Sequence[str], None] = "0033_geocompute_v6_cluster"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUN_TABLE = "geocompute_runs"
_WORKER_TABLE = "geocompute_workers"
_EVENT_TABLE = "geocompute_run_events"
_CACHE_TABLE = "geocompute_worker_cache"

_EVENT_INDEXES = {
    "idx_gc_event_run_id": ["run_id", "id"],
    "idx_gc_event_created": ["created_at"],
}
_CACHE_INDEXES = {
    "idx_gc_wcache_worker_hit": ["worker_id", "last_hit_at"],
    "idx_gc_wcache_cached_at": ["cached_at"],
}


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _column_exists(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # 1) additive 列（幂等：列已存在即跳过 —— create_all 共存库重入安全）
    if _table_exists(_RUN_TABLE) and not _column_exists(_RUN_TABLE, "resource_request"):
        op.add_column(_RUN_TABLE, sa.Column("resource_request", sa.JSON(), nullable=True))
    if _table_exists(_WORKER_TABLE) and not _column_exists(_WORKER_TABLE, "capability"):
        op.add_column(_WORKER_TABLE, sa.Column("capability", sa.JSON(), nullable=True))

    # 2) geocompute_run_events
    if not _table_exists(_EVENT_TABLE):
        op.create_table(
            _EVENT_TABLE,
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      autoincrement=True, primary_key=True),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("event", sa.String(length=32), nullable=False),
            sa.Column("node_id", sa.String(length=128), nullable=True),
            sa.Column("worker_id", sa.String(length=128), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=True),
            sa.Column("rows", sa.Integer(), nullable=True),
            sa.Column("bytes", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    for name, cols in _EVENT_INDEXES.items():
        if _table_exists(_EVENT_TABLE) and not _index_exists(_EVENT_TABLE, name):
            op.create_index(name, _EVENT_TABLE, cols)

    # 3) geocompute_worker_cache
    if not _table_exists(_CACHE_TABLE):
        op.create_table(
            _CACHE_TABLE,
            sa.Column("worker_id", sa.String(length=128), primary_key=True),
            sa.Column("cache_key", sa.String(length=64), primary_key=True),
            sa.Column("owner_scope", sa.String(length=40), nullable=False),
            sa.Column("size_bytes",
                      sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      nullable=False, server_default="0"),
            sa.Column("cached_at", sa.DateTime(), nullable=False),
            sa.Column("last_hit_at", sa.DateTime(), nullable=False),
        )
    for name, cols in _CACHE_INDEXES.items():
        if _table_exists(_CACHE_TABLE) and not _index_exists(_CACHE_TABLE, name):
            op.create_index(name, _CACHE_TABLE, cols)


def downgrade() -> None:
    for name in _CACHE_INDEXES:
        if _table_exists(_CACHE_TABLE) and _index_exists(_CACHE_TABLE, name):
            op.drop_index(name, table_name=_CACHE_TABLE)
    if _table_exists(_CACHE_TABLE):
        op.drop_table(_CACHE_TABLE)
    for name in _EVENT_INDEXES:
        if _table_exists(_EVENT_TABLE) and _index_exists(_EVENT_TABLE, name):
            op.drop_index(name, table_name=_EVENT_TABLE)
    if _table_exists(_EVENT_TABLE):
        op.drop_table(_EVENT_TABLE)
    if _table_exists(_WORKER_TABLE) and _column_exists(_WORKER_TABLE, "capability"):
        op.drop_column(_WORKER_TABLE, "capability")
    if _table_exists(_RUN_TABLE) and _column_exists(_RUN_TABLE, "resource_request"):
        op.drop_column(_RUN_TABLE, "resource_request")
