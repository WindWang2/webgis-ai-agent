"""merge heads g3001_skill_job + 0105_topology_workbench

Revision ID: e6fe5ade5010
Revises: 0105_topology_workbench, g3001_skill_job
Create Date: 2026-11-13 13:18:10.806474

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6fe5ade5010'
down_revision: Union[str, Sequence[str], None] = ('0105_topology_workbench', 'g3001_skill_job')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
