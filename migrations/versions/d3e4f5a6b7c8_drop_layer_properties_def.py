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


def _column_exists(table: str, column: str) -> bool:
    return column in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    if _is_sqlite():
        # create_all 共存守卫：模型已删该列，create_all 库上无守卫的
        # batch drop_column 对不存在列抛 KeyError（PG 分支已有 IF EXISTS）。
        if _column_exists('layers', 'properties_def'):
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
