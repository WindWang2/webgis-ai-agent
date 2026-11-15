"""G46: linked_exploration —— 多图联合筛选持久命名探索视图存储面

Revision ID: g46_linked_exploration_views
Revises: g40_cartographic_symbol_assets
Create Date: 2026-11-15

单张 additive 新表（约定照抄 0107_data_join_authoring 先例：create_all
coexistence guard 可重入 DDL；downgrade 反序回滚）：

- exploration_views

CHECK 词表与 app/models/linked_exploration.py 一字不差（wiring drift 闸
tests/test_deploy_migration_wiring.py / tests/test_alembic_metadata.py 依赖）。
完整匹配集不入行 —— 运行期由 G24 where-scope selection handle 承载。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g46_linked_exploration_views"
down_revision: Union[str, Sequence[str], None] = "g40_cartographic_symbol_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if not _table_exists("exploration_views"):
        op.create_table(
            "exploration_views",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("plan", sa.JSON(), nullable=False),
            sa.Column("normalized_digest", sa.String(length=64), nullable=False),
            sa.Column("source_ref", sa.String(length=255), nullable=False),
            sa.Column("source_revision", sa.Integer(), nullable=False),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("preview", sa.JSON(), nullable=True),
            sa.Column("preview_digest", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("revision >= 1", name="ck_exploration_view_revision"),
            sa.CheckConstraint(
                "status IN ('draft','archived')", name="ck_exploration_view_status"),
            sa.CheckConstraint(
                "source_revision >= 1", name="ck_exploration_view_src_rev"),
        )
        op.create_index(
            "idx_exploration_views_session", "exploration_views",
            ["session_id", "created_at"],
        )


def downgrade() -> None:
    if _table_exists("exploration_views"):
        op.drop_index("idx_exploration_views_session", table_name="exploration_views")
        op.drop_table("exploration_views")
