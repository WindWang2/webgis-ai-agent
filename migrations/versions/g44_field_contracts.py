"""G44: field_contracts（业务字段合同 + 完整评估收据）

Revision ID: g44_field_contracts
Revises: g40_cartographic_symbol_assets（当前唯一头）
Create Date: 2026-11-15

owner 声明的业务字段合同（版本化不可变内容行 + active 部分唯一指针）与完整
评估收据（verified 数据指纹/规则 digest/counts + findings 终态包指针）。
additive 新表（_table_exists 共存 guard 可重入 DDL）；downgrade 反序回滚。
既有表零改动。约定照抄 0107_data_join_authoring。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g44_field_contracts"
down_revision: Union[str, Sequence[str], None] = "g40_cartographic_symbol_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return insp.has_table(name)


def _index_exists(table: str, name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return any(ix.get("name") == name for ix in insp.get_indexes(table))


def upgrade() -> None:
    if not _table_exists("field_contracts"):
        op.create_table(
            "field_contracts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("contract_key", sa.String(length=36), nullable=False),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("ref_id", sa.String(length=128), nullable=False),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("rules", sa.JSON(), nullable=False),
            sa.Column("normalized_digest", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("binding", sa.JSON(), nullable=False),
            sa.Column("activation", sa.JSON(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "status IN ('draft','active','retired','superseded')",
                name="ck_field_contracts_status",
            ),
        )
    if not _index_exists("field_contracts", "ix_field_contracts_session_ref"):
        op.create_index(
            "ix_field_contracts_session_ref", "field_contracts",
            ["session_id", "ref_id"],
        )
    if not _index_exists("field_contracts", "ix_field_contracts_key_rev"):
        op.create_index(
            "ix_field_contracts_key_rev", "field_contracts",
            ["contract_key", "revision"], unique=True,
        )
    if not _index_exists("field_contracts", "uq_field_contracts_active_per_binding"):
        op.create_index(
            "uq_field_contracts_active_per_binding", "field_contracts",
            ["session_id", "ref_id"], unique=True,
            sqlite_where=sa.text("status = 'active'"),
            postgresql_where=sa.text("status = 'active'"),
        )

    if not _table_exists("field_contract_assessments"):
        op.create_table(
            "field_contract_assessments",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("contract_id", sa.String(length=36), nullable=False),
            sa.Column("contract_revision", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("ref_id", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
            sa.Column("job_id", sa.String(length=64), nullable=True),
            sa.Column("verified", sa.JSON(), nullable=False),
            sa.Column("counts", sa.JSON(), nullable=False),
            sa.Column("findings_path", sa.String(length=512), nullable=True),
            sa.Column("findings_digest", sa.String(length=64), nullable=True),
            sa.Column("sampled", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True), nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "status IN ('queued','running','completed','cancelled',"
                "'failed','incomplete')",
                name="ck_field_contract_assessments_status",
            ),
        )
    if not _index_exists("field_contract_assessments", "ix_fca_contract"):
        op.create_index(
            "ix_fca_contract", "field_contract_assessments",
            ["contract_id", "created_at"],
        )
    if not _index_exists("field_contract_assessments", "ix_fca_session_ref"):
        op.create_index(
            "ix_fca_session_ref", "field_contract_assessments",
            ["session_id", "ref_id"],
        )


def downgrade() -> None:
    if _table_exists("field_contract_assessments"):
        op.drop_table("field_contract_assessments")
    if _table_exists("field_contracts"):
        op.drop_table("field_contracts")
