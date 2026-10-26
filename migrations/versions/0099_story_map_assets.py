"""G3D05: story_map_assets（StoryMap 会话资产存储面）

Revision ID: 0099_story_map_assets
Revises: 0098_composition_template_assets
Create Date: 2026-10-27

ADR-0275 D6：StoryMap「可恢复的项目资产」缺口 —— 会话作用域单行
（conversation 唯一）保存最新 StoryMapSpec 快照 + 单调 revision；所有权
随会话（require_owned_session），删除级联。约定照抄 0098 先例：additive
新表 + create_all-coexistence guard 可重入 DDL；downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0099_story_map_assets"
down_revision: Union[str, Sequence[str], None] = "0098_composition_template_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "story_map_assets"

_COLUMNS = (
    sa.Column("id", sa.String(length=64), primary_key=True),
    sa.Column(
        "conversation_id", sa.String(length=255),
        sa.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("title", sa.String(length=255), nullable=False),
    sa.Column("spec", sa.Text(), nullable=False),
    sa.Column("revision", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=True),
    sa.Column("updated_at", sa.DateTime(), nullable=True),
)

_INDEXES: dict = {}  # 唯一性由内联 UNIQUE 约束承担（SQLite ALTER 限制，0048 先例）


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def upgrade() -> None:
    if not _table_exists(_TABLE):
        op.create_table(
            _TABLE,
            *_COLUMNS,
            sa.UniqueConstraint(
                "conversation_id", name="uq_sma_conversation"),
            sa.CheckConstraint("revision >= 1", name="ck_sma_revision"),
        )
    for iname, cols in _INDEXES.items():
        if not _index_exists(_TABLE, iname):
            op.create_index(iname, _TABLE, cols, unique=True)


def downgrade() -> None:
    if not _table_exists(_TABLE):
        return
    for iname in _INDEXES:
        if _index_exists(_TABLE, iname):
            op.drop_index(iname, table_name=_TABLE)
    op.drop_table(_TABLE)
