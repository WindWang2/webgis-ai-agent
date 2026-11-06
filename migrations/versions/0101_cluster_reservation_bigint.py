"""Match cluster reservation counters to signed 64-bit resource limits.

Revision ID: 0101_cluster_reservation_bigint
Revises: 0100_spatial_watch_consistency
"""
from alembic import op
import sqlalchemy as sa

revision = "0101_cluster_reservation_bigint"
down_revision = "0100_spatial_watch_consistency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, columns in (("geocompute_runs", ("reserved_rows", "reserved_bytes", "reserved_units")),
                           ("geocompute_resource_usage", ("usage_rows", "usage_units"))):
        with op.batch_alter_table(table) as batch:
            for name in columns:
                batch.alter_column(name, existing_type=sa.Integer(), type_=sa.BigInteger().with_variant(sa.Integer(), "sqlite"), existing_nullable=False)


def downgrade() -> None:
    # PostgreSQL rejects narrowing counters that no longer fit INT4; preserve
    # accounting evidence rather than silently truncating active reservations.
    for table, columns in (("geocompute_runs", ("reserved_rows", "reserved_bytes", "reserved_units")),
                           ("geocompute_resource_usage", ("usage_rows", "usage_units"))):
        with op.batch_alter_table(table) as batch:
            for name in columns:
                batch.alter_column(name, existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
