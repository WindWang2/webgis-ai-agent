"""Map Product lifecycle V2: snapshot + lineage columns (ADR-0099)

Revision ID: 0024_map_product_lifecycle
Revises: 0023_data_fabric_v2_catalog
Create Date: 2026-09-04

ADR-0099：版本生命周期操作（open / restore / fork / rerun / merge /
auto-record）需要版本行携带：
- ``mapspec_snapshot`` —— 该版本定稿时的 MapSpec 文档快照（有界、可空；
  旧版本无快照 = 诚实的 open 降级：可对比不可回放）；
- ``label`` / ``actor`` —— 人可读标记与操作者（审计）；
- ``parent_version_no`` + ``lineage_kind`` —— fork/restore/merge/rerun 的
  谱系边。版本行仍然不可变：所有生命周期操作都是**新增行**（append-only
  证据），绝不改写历史。

全部为可空新增列，向后兼容既有行（旧行 parent/kind 为 NULL = linear）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0024_map_product_lifecycle"
down_revision: Union[str, Sequence[str], None] = "0023_data_fabric_v2_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """create_all-coexistence guard（repo convention，见 0022）。"""
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _column_exists("map_products", "mapspec_snapshot"):
        op.add_column(
            "map_products",
            sa.Column("mapspec_snapshot", sa.JSON(), nullable=True),
        )
    if not _column_exists("map_products", "label"):
        op.add_column(
            "map_products",
            sa.Column("label", sa.String(length=200), nullable=True),
        )
    if not _column_exists("map_products", "actor"):
        op.add_column(
            "map_products",
            sa.Column("actor", sa.String(length=255), nullable=True),
        )
    if not _column_exists("map_products", "parent_version_no"):
        op.add_column(
            "map_products",
            sa.Column("parent_version_no", sa.Integer(), nullable=True),
        )
    if not _column_exists("map_products", "lineage_kind"):
        op.add_column(
            "map_products",
            sa.Column("lineage_kind", sa.String(length=20), nullable=True),
        )


def downgrade() -> None:
    for column in (
        "lineage_kind", "parent_version_no", "actor", "label", "mapspec_snapshot",
    ):
        if _column_exists("map_products", column):
            op.drop_column("map_products", column)
