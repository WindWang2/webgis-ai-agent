"""GIS Working Context store (ADR-0204 / Direction 06)

Revision ID: 0095_gis_working_contexts
Revises: 0094_model_check_constraint_drift
Create Date: 2026-09-20

Tables:
  - gis_working_contexts  mission-scoped bounded GIS working state
    (accepted basis / decisions / findings / user edits; revision CAS;
    ≤16KB validated payload; refs only — Zero Big Data in Agent Context).
    One row per mission; purged (payload dropped) when the mission turns
    terminal. Chat turns write via revision CAS, never via mission lease.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0095_gis_working_contexts"
down_revision: Union[str, Sequence[str], None] = "0094_model_check_constraint_drift"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gis_working_contexts",
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=True),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("goal_revision_mirror", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "state IN ('active','purged')",
            name="ck_gis_working_context_state",
        ),
        sa.PrimaryKeyConstraint("mission_id"),
    )
    op.create_index(
        "idx_gis_working_context_org",
        "gis_working_contexts",
        ["org_id", "updated_at"],
    )
    op.create_index(
        "idx_gis_working_context_project",
        "gis_working_contexts",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_gis_working_context_project", table_name="gis_working_contexts")
    op.drop_index("idx_gis_working_context_org", table_name="gis_working_contexts")
    op.drop_table("gis_working_contexts")
