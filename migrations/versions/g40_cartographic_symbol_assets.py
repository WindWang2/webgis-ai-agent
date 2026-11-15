"""G40: cartographic_symbol_assets（制图符号资产存储面）

Revision ID: g40_cartographic_symbol_assets
Revises: bcf4e5b51f43 (merge head after #2122)
Create Date: 2026-11-15

可复用点符号/面纹理资产（cartoasset.*）+ 不可变版本快照。additive 新表
（create_all-coexistence guard 可重入 DDL；FK 指向既有
organizations/users/projects）；downgrade 反序回滚。既有表零改动。
约定照抄 0098_composition_template_assets。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g40_cartographic_symbol_assets"
down_revision: Union[str, Sequence[str], None] = "bcf4e5b51f43"  # 合流后唯一头（#2122 收拢 G36/G37/G38）
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    {
        "name": "cartographic_symbol_assets",
        "columns": [
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "org_id", sa.Integer(),
                sa.ForeignKey("organizations.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "creator_id", sa.String(length=255),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("visibility", sa.String(length=16), nullable=False),
            sa.Column(
                "project_id", sa.String(length=255),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("latest_revision", sa.Integer(), nullable=False),
            sa.Column("current_revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        ],
        "checks": (
            ("ck_csa_visibility",
             "visibility IN ('private', 'org', 'project')"),
            ("ck_csa_status", "status IN ('draft', 'published', 'retired')"),
            ("ck_csa_kind", "kind IN ('point_symbol', 'area_texture')"),
            ("ck_csa_latest_rev", "latest_revision >= 0"),
            ("ck_csa_current_rev", "current_revision >= 0"),
        ),
        "indexes": {
            "idx_csa_scope": ["org_id", "creator_id", "status"],
            "idx_csa_project": ["project_id", "status"],
            "idx_csa_kind": ["kind", "status"],
        },
    },
    {
        "name": "cartographic_symbol_asset_versions",
        "columns": [
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "asset_id", sa.String(length=64),
                sa.ForeignKey(
                    "cartographic_symbol_assets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("document", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("content_digest", sa.String(length=96), nullable=False),
            sa.Column("blob_key", sa.String(length=64), nullable=False),
            sa.Column("source_note", sa.Text(), nullable=True),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.Column("retired_note", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        ],
        "checks": (
            ("ck_csa_revision_no", "revision >= 1"),
            ("ck_csa_version_status",
             "status IN ('draft', 'published', 'retired')"),
        ),
        "uniques": (
            ("uq_csa_revision", ["asset_id", "revision"]),
        ),
        "indexes": {
            "idx_csa_version_asset": ["asset_id", "status", "revision"],
        },
    },
)


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def upgrade() -> None:
    for spec in _TABLES:
        name = spec["name"]
        if not _table_exists(name):
            uniques = [
                sa.UniqueConstraint(*cols, name=cname)
                for cname, cols in spec.get("uniques", ())
            ]
            checks = [
                sa.CheckConstraint(expr, name=cname)
                for cname, expr in spec["checks"]
            ]
            # CHECK/UNIQUE 内联进建表（SQLite 不支持 ALTER ADD
            # CONSTRAINT；0048/0098 先例）。幂等由 _table_exists 守卫保证。
            op.create_table(name, *spec["columns"], *uniques, *checks)
        for iname, cols in spec["indexes"].items():
            if not _index_exists(name, iname):
                op.create_index(iname, name, cols)


def downgrade() -> None:
    # 反序：先版本表（FK 依赖主表）再主表
    for spec in reversed(_TABLES):
        name = spec["name"]
        if not _table_exists(name):
            continue
        for iname in spec["indexes"]:
            if _index_exists(name, iname):
                op.drop_index(iname, table_name=name)
        op.drop_table(name)
