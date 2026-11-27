"""04 线·交互式只读地图发布快照表（map_publications）

Revision ID: g5202_map_publications
Revises: aa356ce2fcdc
Create Date: 2026-11-28 00:00:00.000000

additive 新表：无数据回填；downgrade 整表 drop（发布快照由作者显式重建）。
命名/约束惯例对齐 g51_map_series（ck_*/uq_*/idx_*；batch 兼容 sqlite）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g5202_map_publications"  # type: ignore[assignment]
down_revision: Union[str, Sequence[str], None] = "aa356ce2fcdc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "map_publications"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("access_policy", sa.String(length=20), nullable=False, server_default="private"),
        sa.Column("share_code", sa.String(length=64), nullable=True),
        sa.Column("share_expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("spec_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("spec_revision", sa.Integer(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("materialized", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("creator_id", sa.String(length=255), nullable=True),
        sa.Column("org_id", sa.String(length=64), nullable=True),
        sa.Column("owner_token", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'revoked')",
            name="ck_map_publication_status",
        ),
        sa.CheckConstraint(
            "access_policy IN ('private', 'link')",
            name="ck_map_publication_access",
        ),
        sa.CheckConstraint("version >= 1", name="ck_map_publication_version"),
        sa.UniqueConstraint("product_id", "version", name="uq_map_publication_version"),
        sa.UniqueConstraint("share_code", name="uq_map_publication_share_code"),
    )
    op.create_index(
        "idx_map_publication_product", _TABLE, ["product_id", "version"]
    )
    op.create_index("idx_map_publication_session", _TABLE, ["session_id"])
    op.create_index("idx_map_publication_status", _TABLE, ["status"])


def downgrade() -> None:
    # 反序回滚：索引随表 drop 一并移除（与 g51 同款惯例）。
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    if _TABLE in existing:
        op.drop_table(_TABLE)
