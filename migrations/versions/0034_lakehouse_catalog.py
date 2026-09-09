"""Spatial Lakehouse V7 catalog projection: lakehouse_catalog_items

Revision ID: 0034_lakehouse_catalog
Revises: 0033_geocompute_v6_cluster
Create Date: 2026-09-09

ADR-0119 Scope F：lakehouse 对象的**可检索投影表**（事实源 =
BlobStore manifest + artifact 台账；本表可由事实源重建 —— reindex 语义）。

additive 新表（repo convention，0022-0033 同款 create_all-coexistence
guard 的可重入 DDL）：

- 代理 PK（uuid）—— 同一 data_object 可发布进多个 owner 域
  （评审 R0-7）；唯一性在 (owner_type, owner_id, content_sha256)；
- bbox 用 (minx,maxx)/(miny,maxy) 复合索引（范围谓词走索引）；
- tags GIN **仅 PostgreSQL**（json 列无 GIN opclass —— R0-25；
  JSONB variant；SQLite 方言跳过 —— 查询侧退化为有界行集内存过滤）。

downgrade 反序回滚（drop 索引 + drop 表）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0034_lakehouse_catalog"
down_revision: Union[str, Sequence[str], None] = "0033_geocompute_v6_cluster"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "lakehouse_catalog_items"

_INDEXES = {
    "idx_lh_cat_owner_created": ["owner_type", "owner_id", "created_at"],
    "idx_lh_cat_kind": ["kind"],
    "idx_lh_cat_status": ["status"],
    "idx_lh_cat_time_start": ["time_start"],
    "idx_lh_cat_time_end": ["time_end"],
    "idx_lh_cat_bbox_x": ["minx", "maxx"],
    "idx_lh_cat_bbox_y": ["miny", "maxy"],
    "idx_lh_cat_object": ["object_id"],
}
_GIN_INDEX = "idx_lh_cat_tags_gin"
_CHECKS = (
    ("ck_lh_cat_status", "status IN ('active','revoked')"),
    ("ck_lh_cat_owner_type", "owner_type IN ('session','project')"),
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
            sa.Column("object_id", sa.String(length=255), nullable=False),
            sa.Column("owner_type", sa.String(length=20), nullable=False),
            sa.Column("owner_id", sa.String(length=128), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("producer_capability", sa.String(length=128), nullable=True),
            sa.Column("producer_tool", sa.String(length=128), nullable=True),
            sa.Column("workflow_run_id", sa.String(length=64), nullable=True),
            sa.Column(
                "tags_json",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=False,
            ),
            sa.Column(
                "descriptor_json",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=False,
            ),
            sa.Column("minx", sa.Float(), nullable=True),
            sa.Column("miny", sa.Float(), nullable=True),
            sa.Column("maxx", sa.Float(), nullable=True),
            sa.Column("maxy", sa.Float(), nullable=True),
            sa.Column("time_start", sa.DateTime(), nullable=True),
            sa.Column("time_end", sa.DateTime(), nullable=True),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column(
                "byte_size",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                nullable=False,
            ),
            sa.Column("status", sa.String(length=20), nullable=False,
                      server_default="active"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("owner_type", "owner_id", "content_sha256",
                                name="uq_lh_cat_owner_content"),
            sa.CheckConstraint(_CHECKS[0][1], name=_CHECKS[0][0]),
            sa.CheckConstraint(_CHECKS[1][1], name=_CHECKS[1][0]),
        )
    for name, cols in _INDEXES.items():
        if not _index_exists(_TABLE, name):
            op.create_index(name, _TABLE, cols)
    if op.get_bind().dialect.name == "postgresql":
        if not _index_exists(_TABLE, _GIN_INDEX):
            op.create_index(
                _GIN_INDEX, _TABLE, ["tags_json"], postgresql_using="gin",
            )
    elif not _index_exists(_TABLE, _GIN_INDEX):
        # SQLite 等方言：普通索引占位同名（JSON 列 B-tree 无 GIN 语义，
        # 仅满足模型↔迁移漂移守卫的列元组比对 —— 查询侧 tags 过滤本就
        # 退化为有界行集内存过滤）。
        op.create_index(_GIN_INDEX, _TABLE, ["tags_json"])


def downgrade() -> None:
    if _table_exists(_TABLE):
        if _index_exists(_TABLE, _GIN_INDEX):
            op.drop_index(_GIN_INDEX, table_name=_TABLE)
        for name in _INDEXES:
            if _index_exists(_TABLE, name):
                op.drop_index(name, table_name=_TABLE)
        op.drop_table(_TABLE)
