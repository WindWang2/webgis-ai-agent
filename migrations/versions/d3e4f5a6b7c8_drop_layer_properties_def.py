"""Drop dormant properties_def column from layers table

Revision ID: d3e4f5a6b7c8
Revises: c1d2e3f4a5b6
Create Date: 2026-07-30

Reconciles the ORM model with the DB schema. The `Layer` model (app/models/db_model.py)
removed `properties_def` (it was dormant — no app/frontend/test code reads it) and repurposed
`style_config` as the current-template pointer. DBs created via Alembic still carry the
orphaned `properties_def` column; this migration drops it so model and schema agree.
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table('layers', schema=None) as batch_op:
            batch_op.drop_column('properties_def')
    else:
        op.execute("ALTER TABLE layers DROP COLUMN IF EXISTS properties_def;")


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table('layers', schema=None) as batch_op:
            batch_op.add_column(sa.Column('properties_def', sa.JSON(), nullable=True))
    else:
        op.execute("ALTER TABLE layers ADD COLUMN IF NOT EXISTS properties_def JSON;")
