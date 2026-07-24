"""conversation owner_token column

Revision ID: 6ef479051297
Revises: f123456789ab
Create Date: 2026-07-24

SEC-08 - Anonymous session ownership:
Anonymous conversations (user_id IS NULL) were world-readable: anyone who
knew the session_id could read chat history, uploads, reports, and layer data.

Add a server-issued `owner_token` to the `conversations` table. New anonymous
sessions get a token generated via `secrets.token_urlsafe(32)`; access to such
a session requires presenting the matching token via the `X-Session-Token`
header.

Back-compat:
- The column is nullable. Existing rows (anonymous or not) have NULL.
- A NULL owner_token is grandfathered: anonymous sessions created before this
  deploy keep working without a token (knowing session_id remains a capability).
- Authenticated sessions (user_id IS NOT NULL) are never gated by owner_token.
- Only NEW anonymous sessions (created after this deploy) have the token set
  and require it for anonymous access.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ef479051297'
down_revision: Union[str, Sequence[str], None] = 'f123456789ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add owner_token column to conversations."""
    # batch_alter_table is required for SQLite (render_as_batch=True in env.py)
    # and is a no-op wrapper on Postgres/MySQL.
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        # nullable=True: grandfather existing rows (NULL = no token required).
        # 64 chars comfortably holds secrets.token_urlsafe(32) (~43 chars).
        batch_op.add_column(
            sa.Column('owner_token', sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    """Drop owner_token column from conversations."""
    with op.batch_alter_table('conversations', schema=None) as batch_op:
        batch_op.drop_column('owner_token')
