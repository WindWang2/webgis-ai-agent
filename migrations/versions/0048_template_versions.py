"""Templates V9: template_versions（版本化/继承/失效）

Revision ID: 0048_template_versions
Revises: 0047_lifecycle_registry
Create Date: 2026-09-11

任务书 P4（foundation/data-lifecycle-v9）：模板版本化实体。additive 新表
（create_all-coexistence guard 可重入 DDL；FK 指向既有 cartography_templates
与自引用继承链）；downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0048_template_versions"
down_revision: Union[str, Sequence[str], None] = "0047_lifecycle_registry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "template_versions"

_INDEXES = {
    "idx_template_version_template": ["template_id", "deprecated_at"],
}
_CHECKS = (
    ("ck_template_version_no", "version >= 1"),
)


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def _constraint_exists(table: str, name: str) -> bool:
    return name in {
        ck["name"]
        for ck in sa.inspect(op.get_bind()).get_check_constraints(table)
    }


def upgrade() -> None:
    if not _table_exists(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column(
                "template_id", sa.String(length=255),
                sa.ForeignKey("cartography_templates.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column(
                "parent_version_id", sa.String(length=36),
                sa.ForeignKey("template_versions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("component_refs", sa.JSON(), nullable=True),
            sa.Column("component_violations", sa.JSON(), nullable=True),
            sa.Column("deprecated_at", sa.DateTime(), nullable=True),
            sa.Column("deprecation_note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("template_id", "version",
                                name="uq_template_version"),
            sa.CheckConstraint(_CHECKS[0][1], name=_CHECKS[0][0]),
        )
    for name, cols in _INDEXES.items():
        if not _index_exists(_TABLE, name):
            op.create_index(name, _TABLE, cols)


def downgrade() -> None:
    if _table_exists(_TABLE):
        for name in _INDEXES:
            if _index_exists(_TABLE, name):
                op.drop_index(name, table_name=_TABLE)
        op.drop_table(_TABLE)
