"""0111: model_drafts —— 模型作者草稿表（可视化 GIS 模型构建器域）

Revision ID: m0111_model_drafts
Revises: 0109_data_join_runs_job_id_bigint
Create Date: 2026-11-26

单张 additive 新表（约定照抄 g46_linked_exploration_views / 0107 先例：
`_table_exists` coexistence guard 可重入 DDL；downgrade 反序回滚）。

CHECK 词表与 app/models/model_authoring.py 一字不差（wiring drift 闸
tests/test_deploy_migration_wiring.py / tests/test_alembic_metadata.py 依赖）。

编号说明：master 当前 head 为 0109；开放 PR #2273 另占 0110（并行分支头）。
本迁移从 0109 分支，交付合并前按最新 head 重挂（rebase down_revision），
不预占 0110 编号。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "m0111_model_drafts"
down_revision: Union[str, Sequence[str], None] = "0109_data_join_runs_job_id_bigint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if not _table_exists("model_drafts"):
        op.create_table(
            "model_drafts",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "project_id",
                sa.String(length=255),
                sa.ForeignKey("projects.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("model_slug", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("document", sa.JSON(), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("document_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("source_kind", sa.String(length=20), nullable=False),
            sa.Column("source_ref", sa.String(length=255), nullable=True),
            sa.Column("template_id", sa.String(length=120), nullable=True),
            sa.Column("latest_published_package_id", sa.String(length=64), nullable=True),
            sa.Column("latest_published_version", sa.String(length=16), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "project_id", "model_slug", name="uq_model_draft_project_slug"
            ),
            sa.CheckConstraint("revision_no >= 1", name="ck_model_draft_revision"),
            sa.CheckConstraint(
                "source_kind IN ('manual','project_workflow','session_plan','template')",
                name="ck_model_draft_source_kind",
            ),
        )
        op.create_index(
            "idx_model_draft_project_updated",
            "model_drafts",
            ["project_id", "updated_at"],
        )
        op.create_index(
            "idx_model_draft_published_pkg",
            "model_drafts",
            ["latest_published_package_id"],
        )


def downgrade() -> None:
    if _table_exists("model_drafts"):
        op.drop_index("idx_model_draft_published_pkg", table_name="model_drafts")
        op.drop_index("idx_model_draft_project_updated", table_name="model_drafts")
        op.drop_table("model_drafts")
