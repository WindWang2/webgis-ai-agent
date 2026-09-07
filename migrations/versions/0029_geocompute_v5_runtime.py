"""GeoCompute V5 runtime: cross-process reuse index + run evidence snapshots

Revision ID: 0029_geocompute_v5_runtime
Revises: 0028_lineage_repair_evidence
Create Date: 2026-09-07

Wave 7（audit 06-geocompute-gaps.md §6.1 steps 2-3）：异构调度的两个
**纯缓存/证据** 持久化事实，均为 additive 新表，无数据改写：

1. ``geocompute_node_results`` — durable 节点的跨进程 checkpoint 复用索引：
   {owner_scope, node_fingerprint, result_ref, session_id,
   upstream_fingerprints, created_at}。(owner_scope, node_fingerprint) 唯一
   （重写即刷新），每 owner 由应用层在写入时按 created_at LRU 剪枝到 64 条。
   这是缓存索引，不是第二任务状态机 —— job 真相仍在 analysis_tasks。
2. ``geocompute_run_evidence`` — run 终态的有界（≤16KB）证据快照
   （owner 域隔离；append-once/upsert-on-terminal），进程重启后 ``get_run``
   内存未命中时按 owner 校验回放，读取不再 404。

两表都是 create_all-coexistence guard 保护的**可重入** DDL（repo convention，
0022/0024/0025/0027/0028 同款）；索引按模型声明逐一建出（漂移守卫
tests/test_deploy_migration_wiring.py §6 按列元组比对）。downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0029_geocompute_v5_runtime"
down_revision: Union[str, Sequence[str], None] = "0028_lineage_repair_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RESULT_TABLE = "geocompute_node_results"
_RUN_TABLE = "geocompute_run_evidence"

_RESULT_INDEXES = {
    "uq_gc_node_result_owner_fp": ["owner_scope", "node_fingerprint"],
    "idx_gc_node_result_owner_created": ["owner_scope", "created_at"],
}
_RUN_INDEXES = {
    "idx_gc_run_evidence_owner": ["owner_scope"],
}


def _table_exists(table: str) -> bool:
    """create_all-coexistence guard（repo convention，0022-0028 同款）。"""
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if not _table_exists(_RESULT_TABLE):
        op.create_table(
            _RESULT_TABLE,
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      autoincrement=True, primary_key=True),
            sa.Column("owner_scope", sa.String(length=40), nullable=False),
            sa.Column("node_fingerprint", sa.String(length=32), nullable=False),
            sa.Column("result_ref", sa.String(length=512), nullable=False),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("upstream_fingerprints", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    for name, cols in _RESULT_INDEXES.items():
        if not _index_exists(_RESULT_TABLE, name):
            op.create_index(name, _RESULT_TABLE, cols)

    if not _table_exists(_RUN_TABLE):
        op.create_table(
            _RUN_TABLE,
            sa.Column("run_id", sa.String(length=64), primary_key=True),
            sa.Column("owner_scope", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("snapshot", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    for name, cols in _RUN_INDEXES.items():
        if not _index_exists(_RUN_TABLE, name):
            op.create_index(name, _RUN_TABLE, cols)


def downgrade() -> None:
    for name in _RUN_INDEXES:
        if _index_exists(_RUN_TABLE, name):
            op.drop_index(name, table_name=_RUN_TABLE)
    if _table_exists(_RUN_TABLE):
        op.drop_table(_RUN_TABLE)
    for name in _RESULT_INDEXES:
        if _index_exists(_RESULT_TABLE, name):
            op.drop_index(name, table_name=_RESULT_TABLE)
    if _table_exists(_RESULT_TABLE):
        op.drop_table(_RESULT_TABLE)
