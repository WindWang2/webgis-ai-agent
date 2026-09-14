"""Close model↔migration index drift (post-merge integration)

Revision ID: 0090_close_model_index_drift
Revises: 0080_gis_spatial_memories
Create Date: 2026-09-14

多线并行合并（harness 系列 #1274-#1285 + #1273）后的模型↔迁移索引漂移
收敛（drift gate 实测两条）：

  1. carto_feedback_signals.target —— 模型 ``index=True``
     （app/models/intent_learning.py → ix_carto_feedback_signals_target），
     迁移 0057 建表时未建该索引。
  2. gis_spatial_memories.status —— 模型 ``index=True``
     （app/models/spatial_memory.py → ix_gis_spatial_memories_status），
     迁移 0080 建表时未建该索引。

既有库 upgrade 即收敛；downgrade 对称回退。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0090_close_model_index_drift'
down_revision: Union[str, Sequence[str], None] = '0080_gis_spatial_memories'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (索引名, 表名, 列) —— 与模型 ``index=True`` 生成的索引名对齐。
_MISSING_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_carto_feedback_signals_target", "carto_feedback_signals", "target"),
    ("ix_gis_spatial_memories_status", "gis_spatial_memories", "status"),
)


def upgrade() -> None:
    for name, table, column in _MISSING_INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, _table, _column in reversed(_MISSING_INDEXES):
        op.drop_index(name, table_name=_table)
