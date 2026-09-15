"""Durable GIS Mission Runtime ledger (ADR-0197 / Direction 01)

Revision ID: 0091_gis_mission_runtime
Revises: 0090_close_model_index_drift
Create Date: 2026-09-15

Tables:
  - gis_missions              mission ownership/lifecycle + lease_epoch fencing
  - gis_mission_checkpoints   bounded checkpoint ring (refs only)
  - gis_mission_swarm_runs    durable swarm task receipt ledger
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0091_gis_mission_runtime"
down_revision: Union[str, Sequence[str], None] = "0090_close_model_index_drift"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gis_missions",
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=True),
        sa.Column("owner_scope", sa.String(length=64), nullable=False),
        sa.Column("root_goal", sa.Text(), nullable=False),
        sa.Column("goal_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("refs", sa.JSON(), nullable=False),
        sa.Column("frontier", sa.JSON(), nullable=False),
        sa.Column("resource_budget", sa.JSON(), nullable=False),
        sa.Column("failure_state", sa.JSON(), nullable=False),
        sa.Column("recovery_state", sa.JSON(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_epoch", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("terminal_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('created','planning','running','waiting_dependency',"
            "'partially_complete','suspended','recovering','complete',"
            "'failed','cancelled')",
            name="ck_gis_mission_state",
        ),
        sa.PrimaryKeyConstraint("mission_id"),
    )
    op.create_index("idx_gis_mission_org_created", "gis_missions", ["org_id", "created_at"])
    op.create_index("idx_gis_mission_org_state", "gis_missions", ["org_id", "state"])
    op.create_index("idx_gis_mission_owner_scope", "gis_missions", ["owner_scope", "mission_id"])
    op.create_index("idx_gis_mission_lease_expiry", "gis_missions", ["state", "lease_expires_at"])
    op.create_index("idx_gis_mission_project", "gis_missions", ["project_id"])

    op.create_table(
        "gis_mission_checkpoints",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("checkpoint_id", sa.String(length=96), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("mission_revision", sa.Integer(), nullable=False),
        sa.Column("goal_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkpoint_id", name="uq_gis_mission_cp_id"),
    )
    op.create_index(
        "idx_gis_mission_cp_mission_created",
        "gis_mission_checkpoints",
        ["mission_id", "created_at"],
    )
    op.create_index(
        "idx_gis_mission_cp_org",
        "gis_mission_checkpoints",
        ["org_id", "created_at"],
    )

    op.create_table(
        "gis_mission_swarm_runs",
        sa.Column("swarm_run_id", sa.String(length=64), nullable=False),
        sa.Column("mission_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("goal_slice", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("tasks", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("terminal_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "state IN ('running','succeeded','failed','cancelled','partial')",
            name="ck_gis_mission_swarm_state",
        ),
        sa.PrimaryKeyConstraint("swarm_run_id"),
    )
    op.create_index(
        "idx_gis_mission_swarm_mission",
        "gis_mission_swarm_runs",
        ["mission_id", "state"],
    )
    op.create_index(
        "idx_gis_mission_swarm_org",
        "gis_mission_swarm_runs",
        ["org_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_gis_mission_swarm_org", table_name="gis_mission_swarm_runs")
    op.drop_index("idx_gis_mission_swarm_mission", table_name="gis_mission_swarm_runs")
    op.drop_table("gis_mission_swarm_runs")
    op.drop_index("idx_gis_mission_cp_org", table_name="gis_mission_checkpoints")
    op.drop_index("idx_gis_mission_cp_mission_created", table_name="gis_mission_checkpoints")
    op.drop_table("gis_mission_checkpoints")
    op.drop_index("idx_gis_mission_project", table_name="gis_missions")
    op.drop_index("idx_gis_mission_lease_expiry", table_name="gis_missions")
    op.drop_index("idx_gis_mission_owner_scope", table_name="gis_missions")
    op.drop_index("idx_gis_mission_org_state", table_name="gis_missions")
    op.drop_index("idx_gis_mission_org_created", table_name="gis_missions")
    op.drop_table("gis_missions")
