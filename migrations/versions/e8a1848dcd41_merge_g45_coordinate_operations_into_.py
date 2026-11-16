"""merge g45_coordinate_operations into main line (post #2240)

Revision ID: e8a1848dcd41
Revises: 862bcc649f52, g45_coordinate_operations
Create Date: 2026-11-16 22:28:11.102383

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8a1848dcd41'
down_revision: Union[str, Sequence[str], None] = ('862bcc649f52', 'g45_coordinate_operations')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
