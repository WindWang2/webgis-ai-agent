"""Lakehouse V8 dataset registry: lakehouse_datasets / _versions / _refs

Revision ID: 0035_lakehouse_dataset_versions
Revises: c0d8322aa2cb
Create Date: 2026-09-10

ADR-0130（Versioned Geospatial Lakehouse）：数据集级版本层的 durable
台账（事实源 = BlobStore manifest —— dataset 描述符与版本 commit record
都是内容寻址 manifest；本三表为台账，可由 manifest + 台账重建投影）。

- ``lakehouse_datasets``：数据集注册行（描述符 manifest id = dataset_id）；
- ``lakehouse_dataset_versions``：append-only 版本账本（version_id =
  commit manifest id；parent 链 = 版本 DAG；幂等键
  (dataset_row_id, version_id)）；
- ``lakehouse_dataset_refs``：命名指针 —— branch 可变（generation
  乐观并发）、tag 不可变（唯一约束拒绝改写）。

additive 新表（repo convention，0034 同款 create_all-coexistence guard
的可重入 DDL）；downgrade 反序回滚（drop 三表）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0035_lakehouse_dataset_versions"
down_revision: Union[str, Sequence[str], None] = "c0d8322aa2cb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("lakehouse_dataset_refs", "lakehouse_dataset_versions",
           "lakehouse_datasets")

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


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
    if not _table_exists("lakehouse_datasets"):
        op.create_table(
            "lakehouse_datasets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("dataset_id", sa.String(length=64), nullable=False),
            sa.Column("owner_type", sa.String(length=20), nullable=False),
            sa.Column("owner_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("description", sa.String(length=512), nullable=True),
            sa.Column("default_branch", sa.String(length=128), nullable=False,
                      server_default="main"),
            sa.Column("cube_contract", _JSON, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("dataset_id", name="uq_lh_dataset_descriptor"),
            sa.CheckConstraint("owner_type IN ('session','project')",
                               name="ck_lh_ds_owner_type"),
        )
    if not _index_exists("lakehouse_datasets", "idx_lh_ds_owner_created"):
        op.create_index("idx_lh_ds_owner_created", "lakehouse_datasets",
                        ["owner_type", "owner_id", "created_at"])

    if not _table_exists("lakehouse_dataset_versions"):
        op.create_table(
            "lakehouse_dataset_versions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("dataset_row_id", sa.String(length=36),
                      sa.ForeignKey("lakehouse_datasets.id"), nullable=False),
            sa.Column("version_id", sa.String(length=64), nullable=False),
            sa.Column("parent_version_id", sa.String(length=64), nullable=True),
            sa.Column("data_object_id", sa.String(length=64), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("byte_size", sa.BigInteger().with_variant(
                sa.Integer(), "sqlite"), nullable=False, server_default="0"),
            sa.Column("branch", sa.String(length=128), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False,
                      server_default="commit"),
            sa.Column("provenance_json", _JSON, nullable=False),
            sa.Column("workflow_run_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("dataset_row_id", "version_id",
                                name="uq_lh_dsv_dataset_version"),
            sa.CheckConstraint(
                "action IN ('commit','rollback','delta','import',"
                "'workflow_publish')",
                name="ck_lh_dsv_action",
            ),
        )
    for name, cols in (
        ("idx_lh_dsv_dataset_created", ["dataset_row_id", "created_at"]),
        ("idx_lh_dsv_run", ["workflow_run_id"]),
        ("idx_lh_dsv_object", ["data_object_id"]),
    ):
        if not _index_exists("lakehouse_dataset_versions", name):
            op.create_index(name, "lakehouse_dataset_versions", cols)

    if not _table_exists("lakehouse_dataset_refs"):
        op.create_table(
            "lakehouse_dataset_refs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("dataset_row_id", sa.String(length=36),
                      sa.ForeignKey("lakehouse_datasets.id"), nullable=False),
            sa.Column("ref_type", sa.String(length=8), nullable=False),
            sa.Column("ref_name", sa.String(length=128), nullable=False),
            sa.Column("version_id", sa.String(length=64), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False,
                      server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("dataset_row_id", "ref_type", "ref_name",
                                name="uq_lh_dsr_dataset_ref"),
            sa.CheckConstraint("ref_type IN ('branch','tag')",
                               name="ck_lh_dsr_type"),
        )
    if not _index_exists("lakehouse_dataset_refs", "idx_lh_dsr_dataset"):
        op.create_index("idx_lh_dsr_dataset", "lakehouse_dataset_refs",
                        ["dataset_row_id", "ref_type"])


def downgrade() -> None:
    for table in _TABLES:
        if _table_exists(table):
            op.drop_table(table)
