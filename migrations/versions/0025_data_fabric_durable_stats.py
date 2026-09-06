"""GeoCompute & Data Fabric V4: durable advisory dataset statistics (ADR-0101 D6)

Revision ID: 0025_data_fabric_durable_stats
Revises: 0024_map_product_lifecycle
Create Date: 2026-09-06

ADR-0101 D6：V3 的统计是进程内 TTL store（重启即失，诚实但每次冷启动都要
重新采集）。V4 增加 **advisory** 的 DB 持久层：按 dataset 指纹寻址、显式
collected_at/expires_at（stale 语义）、有界保留。统计仍是性能提示 ——
查询正确性永不依赖它；所有读取方 fail-open（DB 故障回退进程内采集）。

新表 + 新索引，均为 create_all-coexistence guard 保护的可重入 DDL；
无数据改写，downgrade 反序删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0025_data_fabric_durable_stats"
down_revision: Union[str, Sequence[str], None] = "0024_map_product_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "dataset_statistics"


def _table_exists(table: str) -> bool:
    """create_all-coexistence guard（repo convention，0021-0024 同款）。"""
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if not _table_exists(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("dataset_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("source_type", sa.String(length=50), nullable=True),
            sa.Column("collector", sa.String(length=32), nullable=False,
                      server_default="descriptor"),
            sa.Column("confidence", sa.String(length=16), nullable=False,
                      server_default="assumption"),
            sa.Column("revision_strength", sa.String(length=16), nullable=False,
                      server_default="weak"),
            sa.Column("stats_json", sa.JSON(), nullable=False),
            sa.Column("collected_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
        )
    if not _index_exists(_TABLE, "idx_dataset_stats_fp_collected"):
        op.create_index(
            "idx_dataset_stats_fp_collected", _TABLE,
            ["dataset_fingerprint", "collected_at"],
        )


def downgrade() -> None:
    if _index_exists(_TABLE, "idx_dataset_stats_fp_collected"):
        op.drop_index("idx_dataset_stats_fp_collected", table_name=_TABLE)
    if _table_exists(_TABLE):
        op.drop_table(_TABLE)
