"""Project Knowledge Projection ledger (Direction: project knowledge & cross-mission reuse)

Revision ID: 0093_project_knowledge_entries
Revises: 0091_gis_mission_runtime
Create Date: 2026-09-17

Tables:
  - project_knowledge_entries  project-scoped knowledge projection/index
    (borrowed identity → authoritative stores; bounded; invalidatable).

Note: 0092 is reserved by the parallel branch `spatial_events` (PR #1355);
this migration intentionally also revises 0091 — whichever lands second gets
an alembic merge node (repo convention, cf. merge_0034_multi_epic_heads).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0093_project_knowledge_entries"
down_revision: Union[str, Sequence[str], None] = "0091_gis_mission_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_knowledge_entries",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("entity_kind", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("authority_store", sa.String(length=48), nullable=False),
        sa.Column("authority_id", sa.String(length=255), nullable=False),
        sa.Column("version_token", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("temporal_label", sa.String(length=64), nullable=True),
        sa.Column("method_key", sa.String(length=200), nullable=True),
        sa.Column("refs", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("invalidation_rule", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=64), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "entity_kind IN ('dataset_version','artifact','map_product','workflow',"
            "'method','mission','failure_pattern','place','preference')",
            name="ck_pkx_entity_kind",
        ),
        sa.CheckConstraint(
            "authority_store IN ('project_dataset','artifact','map_product','workflow',"
            "'mission','gis_memory','carto_fact','session_ref')",
            name="ck_pkx_authority_store",
        ),
        sa.CheckConstraint(
            "status IN ('active','stale','superseded','invalidated')",
            name="ck_pkx_status",
        ),
        sa.CheckConstraint(
            "invalidation_rule IN ('','version_bump','head_changed','claim_lost',"
            "'manual','scope_gone')",
            name="ck_pkx_invalidation_rule",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_pkx_org_project_kind_status",
        "project_knowledge_entries",
        ["org_id", "project_id", "entity_kind", "status"],
    )
    op.create_index(
        "idx_pkx_org_project_authority",
        "project_knowledge_entries",
        ["org_id", "project_id", "authority_store", "authority_id"],
    )
    # model 列级 index=True 的对应物（防 model/migration 漂移，0090 先例）。
    op.create_index(
        "ix_project_knowledge_entries_org_id",
        "project_knowledge_entries",
        ["org_id"],
    )
    op.create_index(
        "ix_project_knowledge_entries_status",
        "project_knowledge_entries",
        ["status"],
    )
    op.create_index(
        "uq_pkx_active_key",
        "project_knowledge_entries",
        ["org_id", "project_id", "entity_kind", "authority_store", "authority_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_pkx_active_key", table_name="project_knowledge_entries")
    op.drop_index("ix_project_knowledge_entries_status", table_name="project_knowledge_entries")
    op.drop_index("ix_project_knowledge_entries_org_id", table_name="project_knowledge_entries")
    op.drop_index("idx_pkx_org_project_authority", table_name="project_knowledge_entries")
    op.drop_index("idx_pkx_org_project_kind_status", table_name="project_knowledge_entries")
    op.drop_table("project_knowledge_entries")
