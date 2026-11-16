"""merge g46_linked_exploration_views into main line (post #2168)

Revision ID: dc0b99ededbd
Revises: e8a1848dcd41, g46_linked_exploration_views
Create Date: 2026-11-16 22:34:11.079246

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dc0b99ededbd'
down_revision: Union[str, Sequence[str], None] = ('e8a1848dcd41', 'g46_linked_exploration_views')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
