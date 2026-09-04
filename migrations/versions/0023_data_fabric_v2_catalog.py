"""Enterprise Geospatial Data Fabric V2: catalog availability semantics (ADR-0094)

Revision ID: 0023_data_fabric_v2_catalog
Revises: g1109_legacy_owner
Create Date: 2026-09-03

ADR-0094 §9（Catalog V2）：
- ``spatial_catalog_items.availability``：available | unavailable —— 增量同步
  检测到数据集从源消失时保留元数据并标记 unavailable（stale 检索语义），
  不再静默保留也不物理删除；source 不可达时条目保留（STABLE_METADATA_-
  STALE_SOURCE 由 sync 汇总报告，不落行级状态）。
- ``materializations.query_fingerprint``：物化审计行记录 V2 查询指纹，
  DatasetVersion + QueryEvidence 供 ADR-0092 lineage 消费。

全部为可空/带默认新列，向后兼容既有行（SQLite batch + PG ADD COLUMN IF NOT EXISTS）。
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


revision: str = "0023_data_fabric_v2_catalog"
down_revision: Union[str, Sequence[str], None] = "g1109_legacy_owner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """create_all-coexistence guard（repo 约定：0021/0022 同款）。"""
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _index_exists(table: str, index: str) -> bool:
    bind = op.get_bind()
    return index in {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    is_sqlite = context.get_context().dialect.name == "sqlite"

    if not _column_exists("spatial_catalog_items", "availability"):
        if is_sqlite:
            with op.batch_alter_table("spatial_catalog_items") as batch:
                batch.add_column(
                    sa.Column("availability", sa.String(32), nullable=False,
                              server_default="available")
                )
        else:
            op.execute(
                "ALTER TABLE spatial_catalog_items "
                "ADD COLUMN IF NOT EXISTS availability VARCHAR(32) NOT NULL DEFAULT 'available'"
            )
    if not _index_exists("spatial_catalog_items", "idx_catalog_availability"):
        op.create_index(
            "idx_catalog_availability", "spatial_catalog_items", ["availability"]
        )

    if not _column_exists("materializations", "query_fingerprint"):
        if is_sqlite:
            with op.batch_alter_table("materializations") as batch:
                batch.add_column(sa.Column("query_fingerprint", sa.String(64), nullable=True))
        else:
            op.execute(
                "ALTER TABLE materializations "
                "ADD COLUMN IF NOT EXISTS query_fingerprint VARCHAR(64)"
            )
    if not _column_exists("materializations", "result_mode"):
        if is_sqlite:
            with op.batch_alter_table("materializations") as batch:
                batch.add_column(sa.Column("result_mode", sa.String(32), nullable=True))
        else:
            op.execute(
                "ALTER TABLE materializations "
                "ADD COLUMN IF NOT EXISTS result_mode VARCHAR(32)"
            )


def downgrade() -> None:
    is_sqlite = context.get_context().dialect.name == "sqlite"
    if _index_exists("spatial_catalog_items", "idx_catalog_availability"):
        op.drop_index("idx_catalog_availability", table_name="spatial_catalog_items")
    if _column_exists("spatial_catalog_items", "availability"):
        if is_sqlite:
            with op.batch_alter_table("spatial_catalog_items") as batch:
                batch.drop_column("availability")
        else:
            op.execute("ALTER TABLE spatial_catalog_items DROP COLUMN IF EXISTS availability")
    for col in ("query_fingerprint", "result_mode"):
        if _column_exists("materializations", col):
            if is_sqlite:
                with op.batch_alter_table("materializations") as batch:
                    batch.drop_column(col)
            else:
                op.execute(f"ALTER TABLE materializations DROP COLUMN IF EXISTS {col}")
