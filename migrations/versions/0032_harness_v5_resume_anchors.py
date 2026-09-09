"""Harness V5：workflow_resume_anchors 表（ADR-0118 决策 D8）。

独立 revision（0032）—— 与其他并行 Epic 的 migration 无交叉。
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0032_harness_v5_resume_anchors"
down_revision = "0031_revision_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_resume_anchors",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=True),
        sa.Column("project_id", sa.String(length=255), nullable=True),
        sa.Column("anchor", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_resume_anchor_session", "workflow_resume_anchors", ["session_id"]
    )
    op.create_index(
        "idx_resume_anchor_user", "workflow_resume_anchors", ["user_id"]
    )
    op.create_index(
        "idx_resume_anchor_project", "workflow_resume_anchors", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "idx_resume_anchor_project", table_name="workflow_resume_anchors"
    )
    op.drop_index("idx_resume_anchor_user", table_name="workflow_resume_anchors")
    op.drop_index(
        "idx_resume_anchor_session", table_name="workflow_resume_anchors"
    )
    op.drop_table("workflow_resume_anchors")
