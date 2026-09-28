"""Harness Turn Journal: durable turn event ledger + workflow turn link (H04 / ADR-0216)

Revision ID: 0096_harness_turn_journal
Revises: 0095_gis_working_contexts
Create Date: 2026-09-29

Tables:
  - turn_events            session-scoped append-only turn event ledger
                           (event_id UNIQUE dedup; envelope stays the live
                           authority — this table is crash forensics,
                           causal query and retention only).
  - workflow_instances     +nullable turn_id / run_id (turn↔workflow causal
                           link captured at instance creation from the
                           ambient RuntimeContext; purely additive).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0096_harness_turn_journal"
down_revision: Union[str, Sequence[str], None] = "0095_gis_working_contexts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "turn_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(length=160), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("step_id", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("host", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("causal_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("note", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("payload_ref", sa.String(length=160), nullable=True),
        sa.Column("mutation_revision", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="recorded"),
        sa.CheckConstraint(
            "status IN ('recorded','compacted')",
            name="ck_turn_event_status",
        ),
        sa.UniqueConstraint("event_id", name="uq_turn_event_id"),
    )
    op.create_index("idx_turn_event_session", "turn_events", ["session_id", "id"])
    op.create_index("idx_turn_event_turn", "turn_events", ["turn_id", "id"])
    op.create_index("idx_turn_event_sweep", "turn_events", ["status", "occurred_at"])
    op.create_index("idx_turn_event_kind", "turn_events", ["session_id", "kind", "id"])

    with op.batch_alter_table("workflow_instances") as batch:
        batch.add_column(
            sa.Column("turn_id", sa.String(length=80), nullable=True))
        batch.add_column(
            sa.Column("run_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workflow_instances") as batch:
        batch.drop_column("run_id")
        batch.drop_column("turn_id")
    op.drop_index("idx_turn_event_kind", table_name="turn_events")
    op.drop_index("idx_turn_event_sweep", table_name="turn_events")
    op.drop_index("idx_turn_event_turn", table_name="turn_events")
    op.drop_index("idx_turn_event_session", table_name="turn_events")
    op.drop_table("turn_events")
