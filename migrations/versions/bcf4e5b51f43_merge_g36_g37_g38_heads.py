"""merge parallel feature heads: g36 visual context, g37 session model, g38 data join

Revision ID: bcf4e5b51f43
Revises: 0107_data_join_authoring, g36_visual_context, g3701_session_model
Create Date: 2026-11-15 14:20:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'bcf4e5b51f43'
down_revision: Union[str, Sequence[str], None] = ('0107_data_join_authoring', 'g36_visual_context', 'g3701_session_model')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Deliberate no-op: merge revision for unifying independent feature heads."""


def downgrade() -> None:
    """Deliberate no-op: merge revision for unifying independent feature heads."""
