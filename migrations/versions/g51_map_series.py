"""WP3: cartography map series（系列地图作者计划权威存储面）

Revision ID: g51_map_series
Revises: g50_carto_design_series
Create Date: 2026-11-26

goal-carto-design-series-20261125 WP3。additive 新表两张（create_all
coexistence guard 可重入 DDL；照 g50 先例）：

- ``cartography_map_series`` —— 系列主表（成员引用与计划、设计规范 pin、
  可比性契约、输出预算；draft/published/retired 状态机；
  scope = builtin ∪ creator ∪ org）；
- ``cartography_map_series_versions`` —— 不可变版本快照（同系列内
  parent_version_id 继承链 + 结构 digest + 软废弃）。

系列**不承载地图状态**：成员真实状态在各 session 的权威 MapSpec，本表
只存 session 引用与计划字段。``design_spec_id`` 为跨库引用（WP1 权威），
无 FK 约束，解析时点校验。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g51_map_series"
down_revision: Union[str, Sequence[str], None] = "g50_carto_design_series"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SERIES_TABLE = "cartography_map_series"
_VERSIONS_TABLE = "cartography_map_series_versions"


def upgrade() -> None:
    # create_all-coexistence guard：表已存在（测试 create_all 先建）时幂等跳过。
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())

    if _SERIES_TABLE not in existing:
        op.create_table(
            _SERIES_TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_builtin", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("creator_id", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            # 设计规范 pin（WP1 权威；跨库引用 —— 无 FK 约束）。
            sa.Column("design_spec_id", sa.String(length=36), nullable=True),
            sa.Column("spec_version", sa.Integer(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("structural_digest", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.Column("retirement_note", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "status IN ('draft', 'published', 'retired')",
                name="ck_map_series_status",
            ),
            sa.UniqueConstraint(
                "org_id", "creator_id", "name", name="uq_map_series_scope_name"
            ),
        )
        op.create_index("idx_map_series_creator", _SERIES_TABLE, ["creator_id"])
        op.create_index("idx_map_series_status", _SERIES_TABLE, ["status"])
        op.create_index("idx_map_series_spec_pin", _SERIES_TABLE, ["design_spec_id"])

    if _VERSIONS_TABLE not in existing:
        op.create_table(
            _VERSIONS_TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column(
                "series_id", sa.String(length=36),
                sa.ForeignKey(f"{_SERIES_TABLE}.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column(
                "parent_version_id", sa.String(length=36),
                sa.ForeignKey(f"{_VERSIONS_TABLE}.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("structural_digest", sa.String(length=80), nullable=True),
            sa.Column("deprecated_at", sa.DateTime(), nullable=True),
            sa.Column("deprecation_note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("version >= 1", name="ck_map_series_version_no"),
            sa.UniqueConstraint("series_id", "version", name="uq_map_series_version"),
        )
        op.create_index(
            "idx_map_series_version_series", _VERSIONS_TABLE, ["series_id", "deprecated_at"]
        )


def downgrade() -> None:
    # 反序回滚：先版本表（FK 子表）再主表。索引随表 drop 一并移除。
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    if _VERSIONS_TABLE in existing:
        op.drop_table(_VERSIONS_TABLE)
    if _SERIES_TABLE in existing:
        op.drop_table(_SERIES_TABLE)
