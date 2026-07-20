"""user token_version column

Revision ID: 6c68ec475cfa
Revises: 85e4939d7e07
Create Date: 2026-07-21 00:00:00.000000

S41 - token refresh + logout: add an integer column `token_version` to
`users` so that bumping it (on logout / password change) invalidates all
outstanding access AND refresh tokens that carry the old `ver` claim.

Defaults to 0 (existing rows + new rows) -- back-compat with the
pre-S41 tokens that simply omit the `ver` claim (treated as ver=0).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c68ec475cfa'
down_revision: Union[str, Sequence[str], None] = '85e4939d7e07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add token_version column to users."""
    # batch_alter_table is required for SQLite (render_as_batch=True in env.py)
    # and is a no-op wrapper on Postgres/MySQL.
    with op.batch_alter_table('users', schema=None) as batch_op:
        # server_default='0' back-fills existing rows; nullable=False so the
        # column can never be NULL (defense against bugs that forget to set it).
        batch_op.add_column(
            sa.Column('token_version', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    """Drop token_version column from users."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('token_version')
