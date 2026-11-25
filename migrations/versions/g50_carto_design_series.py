"""WP1: cartography design specs（制图设计规范权威存储面）

Revision ID: g50_carto_design_series
Revises: 0109_data_join_runs_job_id_bigint
Create Date: 2026-11-26

goal-carto-design-series-20261125 WP1。additive 新表两张（create_all
coexistence guard 可重入 DDL；照 0048/0098 先例）：

- ``cartography_design_specs`` —— 规范主表（payload 恒指向当前 effective
  版本；draft/published/retired 状态机；scope = builtin ∪ creator ∪ org）；
- ``cartography_design_spec_versions`` —— 不可变版本快照（同规范内
  parent_version_id 继承链 + 引用校验投影 + 结构 digest + 软废弃）。

规范是「设计 token / 模板组合的引用与约束文档」，不建第二 MapSpec /
组件库 / 资产库。既有表零改动。编号 g50：g47–g49 留空避让在途分支；
主线 0110 已被并行开放 PR（data fusion）占用，不预占同一号段。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g50_carto_design_series"
down_revision: Union[str, Sequence[str], None] = "0109_data_join_runs_job_id_bigint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SPECS_TABLE = "cartography_design_specs"
_VERSIONS_TABLE = "cartography_design_spec_versions"


def upgrade() -> None:
    # create_all-coexistence guard：表已存在（测试 create_all 先建）时幂等跳过。
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())

    if _SPECS_TABLE not in existing:
        op.create_table(
            _SPECS_TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_builtin", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("creator_id", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("structural_digest", sa.String(length=80), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.Column("retirement_note", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "status IN ('draft', 'published', 'retired')",
                name="ck_design_spec_status",
            ),
            sa.UniqueConstraint(
                "org_id", "creator_id", "name", name="uq_design_spec_scope_name"
            ),
        )
        op.create_index(
            "idx_design_spec_creator", _SPECS_TABLE, ["creator_id"]
        )
        op.create_index("idx_design_spec_status", _SPECS_TABLE, ["status"])

    if _VERSIONS_TABLE not in existing:
        op.create_table(
            _VERSIONS_TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column(
                "spec_id", sa.String(length=36),
                sa.ForeignKey(f"{_SPECS_TABLE}.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column(
                "parent_version_id", sa.String(length=36),
                sa.ForeignKey(f"{_VERSIONS_TABLE}.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("reference_violations", sa.JSON(), nullable=True),
            sa.Column("reference_warnings", sa.JSON(), nullable=True),
            sa.Column("structural_digest", sa.String(length=80), nullable=True),
            sa.Column("deprecated_at", sa.DateTime(), nullable=True),
            sa.Column("deprecation_note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("version >= 1", name="ck_design_spec_version_no"),
            sa.UniqueConstraint("spec_id", "version", name="uq_design_spec_version"),
        )
        op.create_index(
            "idx_design_spec_version_spec", _VERSIONS_TABLE, ["spec_id", "deprecated_at"]
        )


def downgrade() -> None:
    # 反序回滚：先版本表（FK 子表）再主表。索引随表 drop 一并移除。
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())
    if _VERSIONS_TABLE in existing:
        op.drop_table(_VERSIONS_TABLE)
    if _SPECS_TABLE in existing:
        op.drop_table(_SPECS_TABLE)
