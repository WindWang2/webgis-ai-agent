"""Data Fabric V7 federated data plane: source facts + federated feedback

Revision ID: 0034_data_fabric_v7_facts_feedback
Revises: 0033_geocompute_v6_cluster
Create Date: 2026-09-09

ADR-0119 W4/W11 的两个 additive advisory 新表，无既有表改写、无数据迁移：

1. ``data_fabric_source_facts`` — SourceFacts 作用域化持久层（scope_key +
   dataset_fingerprint 寻址；provenance/采样标注随 payload JSON）。跨租户
   事实互不可见。仍是性能提示不是正确性真相（fail-open 文化延续）。
2. ``data_fabric_federated_feedback`` — 联邦链执行的 per-source 观测持久层
   （plan_hash + scope 寻址；行数/字节/时延/错误类/限流，绝不含 secret）。

全部 create_all-coexistence guard 保护的**可重入** DDL（repo convention，
0022-0033 同款）；downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0034_data_fabric_v7_facts_feedback"
down_revision: Union[str, Sequence[str], None] = "0033_geocompute_v6_cluster"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FACTS_TABLE = "data_fabric_source_facts"
_FEEDBACK_TABLE = "data_fabric_federated_feedback"

_FACTS_INDEXES = {
    "idx_df_source_facts_scope_fp": ["scope_key", "dataset_fingerprint", "collected_at"],
}
_FEEDBACK_INDEXES = {
    "idx_df_feedback_scope_plan": ["scope_key", "plan_hash", "created_at"],
    "idx_df_feedback_created": ["created_at"],
}


def _table_exists(table: str) -> bool:
    """create_all-coexistence guard（repo convention，0022-0033 同款）。"""
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if not _table_exists(_FACTS_TABLE):
        op.create_table(
            _FACTS_TABLE,
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("scope_key", sa.String(length=128), nullable=False),
            sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("profile_id", sa.String(length=64), nullable=True),
            sa.Column("collector", sa.String(length=32), nullable=False,
                      server_default="descriptor"),
            sa.Column("facts_json", sa.JSON(), nullable=False),
            sa.Column("collected_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
        )
    for name, cols in _FACTS_INDEXES.items():
        if not _index_exists(_FACTS_TABLE, name):
            op.create_index(name, _FACTS_TABLE, cols)

    if not _table_exists(_FEEDBACK_TABLE):
        op.create_table(
            _FEEDBACK_TABLE,
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("plan_hash", sa.String(length=64), nullable=False),
            sa.Column("scope_key", sa.String(length=128), nullable=False),
            sa.Column("engine", sa.String(length=8), nullable=False,
                      server_default="v6"),
            sa.Column("outcome", sa.String(length=16), nullable=False,
                      server_default="ok"),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
        )
    for name, cols in _FEEDBACK_INDEXES.items():
        if not _index_exists(_FEEDBACK_TABLE, name):
            op.create_index(name, _FEEDBACK_TABLE, cols)


def downgrade() -> None:
    if _table_exists(_FEEDBACK_TABLE):
        op.drop_table(_FEEDBACK_TABLE)
    if _table_exists(_FACTS_TABLE):
        op.drop_table(_FACTS_TABLE)
