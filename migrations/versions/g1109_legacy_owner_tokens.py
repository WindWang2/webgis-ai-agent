"""#1109: mint random owner_token for legacy anonymous conversations

Legacy NULL/NULL conversations (user_id IS NULL AND owner_token IS NULL)
were world-accessible to any authenticated caller who knew/guessed the
session_id — an enumerable IDOR on uploads and session data (SEC-08 follow-up).

This migration mints a random 32-byte URL-safe owner_token for every such
row. Callers cannot know these tokens (the legitimate anonymous owners never
held credentials), so access is effectively closed while the data remains
recoverable by an operator. The authorization predicates
(AsyncHistoryService._authorize / authorize_session_write) fail closed on
any residual NULL/NULL row regardless of this migration.

Revision ID: g1109_legacy_owner
Revises: 0022_reproducible_gis_runtime
Create Date: 2026-09-02
"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g1109_legacy_owner"
down_revision: Union[str, Sequence[str], None] = "0022_reproducible_gis_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _mint_legacy_tokens(bind) -> int:
    """Mint a random owner_token for every legacy anonymous conversation.

    Row-by-row with Python-generated tokens: portable across PostgreSQL and
    SQLite (no gen_random_bytes dependency), and the row count of legacy
    anonymous conversations is expected to be modest. Returns the number of
    minted rows (also unit-tested directly).
    """
    rows = bind.execute(
        sa.text(
            "SELECT id FROM conversations "
            "WHERE user_id IS NULL AND owner_token IS NULL"
        )
    ).fetchall()
    for (conv_id,) in rows:
        bind.execute(
            sa.text(
                "UPDATE conversations SET owner_token = :token WHERE id = :cid"
            ),
            {"token": secrets.token_urlsafe(32), "cid": conv_id},
        )
    return len(rows)


def upgrade() -> None:
    """Backfill owner_token for every legacy anonymous conversation."""
    _mint_legacy_tokens(op.get_bind())


def downgrade() -> None:
    """Deliberate no-op.

    We cannot reconstruct which rows were NULL versus minted-and-later-set,
    and resetting tokens to NULL would re-open the enumerable IDOR this
    migration closes. Leaving the minted tokens in place is fail-closed under
    BOTH code versions: the old grandfather only applied to owner_token IS
    NULL, and a random token nobody holds denies access. A no-op also keeps
    chain downgrades (e.g. tests walking to an earlier revision) functional.
    """
    pass
