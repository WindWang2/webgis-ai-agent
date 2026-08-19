"""Drop leftover data-fabric prefix indexes & add knowledge creator_id (#618)

审计 #618（P3 收口，item 4 + 5）：

  4. 0011 在复合索引之外又建了同列左前缀单列索引，纯写放大：
     - ix_spatial_catalog_items_source_id ⊂ idx_catalog_source_name(source_id, name)
     - ix_spatial_catalog_items_geometry_type ⊂ idx_catalog_geom_feature(geometry_type, feature_type)
     - ix_materializations_dataset_id ⊂ idx_mat_dataset_ref(dataset_id, ref_id)
     仅当 inspector 确认存在以该列为左前缀的复合索引时才 DROP IF EXISTS；
     没有覆盖复合索引则跳过，避免误删唯一可用索引。
  5. knowledge_documents.creator_id 是 list_documents 的主过滤列，0016 只补了
     idx_document_org —— 补建 ix_knowledge_documents_creator_id。

既有库 upgrade 即收敛；downgrade 对称回退。
"""
from typing import Sequence, Union

from alembic import op, context
from sqlalchemy import inspect as sa_inspect


# revision identifiers, used by Alembic.
revision: str = "0020_drop_data_fabric_prefix_indexes"
down_revision: Union[str, Sequence[str], None] = "0019_drop_leftover_duplicate_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


#: 被复合索引左前缀覆盖的迁移侧单列索引：(索引名, 表名, 列名)。
_PREFIX_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_spatial_catalog_items_source_id", "spatial_catalog_items", "source_id"),
    ("ix_spatial_catalog_items_geometry_type", "spatial_catalog_items", "geometry_type"),
    ("ix_materializations_dataset_id", "materializations", "dataset_id"),
)

_CREATOR_INDEX = "ix_knowledge_documents_creator_id"
_CREATOR_TABLE = "knowledge_documents"
_CREATOR_COLUMN = "creator_id"


def _has_left_prefix_composite(indexes: list[dict], column: str) -> bool:
    """True if some index on this table starts with ``column`` and has ≥2 cols."""
    for ix in indexes:
        cols = list(ix.get("column_names") or [])
        if len(cols) >= 2 and cols[0] == column:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    for name, table, column in _PREFIX_INDEXES:
        if not _has_left_prefix_composite(insp.get_indexes(table), column):
            continue
        if _is_sqlite():
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_index(name, if_exists=True)
        else:
            op.execute(f"DROP INDEX IF EXISTS {name}")

    if _is_sqlite():
        op.create_index(_CREATOR_INDEX, _CREATOR_TABLE, [_CREATOR_COLUMN])
    else:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {_CREATOR_INDEX} "
            f"ON {_CREATOR_TABLE} ({_CREATOR_COLUMN})"
        )


def downgrade() -> None:
    if _is_sqlite():
        op.drop_index(_CREATOR_INDEX, table_name=_CREATOR_TABLE)
        for name, table, column in _PREFIX_INDEXES:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.create_index(name, [column])
    else:
        op.execute(f"DROP INDEX IF EXISTS {_CREATOR_INDEX}")
        for name, table, column in _PREFIX_INDEXES:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
