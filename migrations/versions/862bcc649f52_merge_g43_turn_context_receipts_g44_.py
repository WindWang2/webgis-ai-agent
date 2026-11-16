"""merge g43_turn_context_receipts + g44_field_contracts heads (parallel goal wave)

Revision ID: 862bcc649f52
Revises: g43_turn_context_receipts, g44_field_contracts
Create Date: 2026-11-16 22:24:20.923812

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '862bcc649f52'
down_revision: Union[str, Sequence[str], None] = ('g43_turn_context_receipts', 'g44_field_contracts')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
