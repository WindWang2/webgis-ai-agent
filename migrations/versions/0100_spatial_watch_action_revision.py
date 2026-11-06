"""Scope watch fires per action and use an integer state revision.

Revision ID: 0100_spatial_watch_consistency
Revises: 0099_story_map_assets
"""
from alembic import op
import sqlalchemy as sa

revision = "0100_spatial_watch_consistency"
down_revision = "0099_story_map_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("spatial_events") as batch:
        batch.alter_column("session_id", existing_type=sa.String(64), type_=sa.String(128), existing_nullable=True)
        batch.drop_constraint("uq_spatial_event_id", type_="unique")
        batch.create_unique_constraint("uq_spatial_event_id", ["org_id", "event_id"])
    with op.batch_alter_table("spatial_watches") as batch:
        batch.alter_column("session_id", existing_type=sa.String(64), type_=sa.String(128), existing_nullable=True)
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("spatial_watch_fires") as batch:
        batch.drop_constraint("uq_spatial_watch_fire", type_="unique")
        batch.create_unique_constraint("uq_spatial_watch_fire", ["watch_id", "event_id", "action"])


def downgrade() -> None:
    # The old constraints cannot represent every valid current row. Refuse
    # before any DDL, rather than leaving a partially rebuilt SQLite schema.
    connection = op.get_bind()
    metadata = sa.MetaData()
    events = sa.Table("spatial_events", metadata, autoload_with=connection)
    watches = sa.Table("spatial_watches", metadata, autoload_with=connection)
    fires = sa.Table("spatial_watch_fires", metadata, autoload_with=connection)
    duplicate_events = sa.select(events.c.event_id).group_by(events.c.event_id).having(sa.func.count() > 1)
    duplicate_fires = sa.select(fires.c.watch_id, fires.c.event_id).group_by(
        fires.c.watch_id, fires.c.event_id
    ).having(sa.func.count() > 1)
    too_long = sa.union_all(
        sa.select(events.c.session_id).where(sa.func.length(events.c.session_id) > 64),
        sa.select(watches.c.session_id).where(sa.func.length(watches.c.session_id) > 64),
    )
    for query in (duplicate_events, duplicate_fires, too_long):
        if connection.execute(query.limit(1)).first() is not None:
            raise RuntimeError(
                "0100 downgrade refused before schema changes: current event IDs, "
                "watch actions or session IDs cannot fit the old constraints. "
                "Back up and explicitly reconcile those rows before retrying; no rows were deleted."
            )
    with op.batch_alter_table("spatial_events") as batch:
        batch.alter_column("session_id", existing_type=sa.String(128), type_=sa.String(64), existing_nullable=True)
        batch.drop_constraint("uq_spatial_event_id", type_="unique")
        batch.create_unique_constraint("uq_spatial_event_id", ["event_id"])
    with op.batch_alter_table("spatial_watch_fires") as batch:
        batch.drop_constraint("uq_spatial_watch_fire", type_="unique")
        batch.create_unique_constraint("uq_spatial_watch_fire", ["watch_id", "event_id"])
    with op.batch_alter_table("spatial_watches") as batch:
        batch.alter_column("session_id", existing_type=sa.String(128), type_=sa.String(64), existing_nullable=True)
        batch.drop_column("revision")
