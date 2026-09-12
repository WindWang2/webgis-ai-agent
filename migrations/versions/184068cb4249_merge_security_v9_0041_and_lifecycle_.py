"""merge security-v9 (0041) and lifecycle/template-v9 (0048) heads

Revision ID: 184068cb4249
Revises: 0041_security_v9_refresh_families, 0048_template_versions
Create Date: 2026-09-12 21:04:33.808518

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '184068cb4249'
down_revision: Union[str, Sequence[str], None] = ('0041_security_v9_refresh_families', '0048_template_versions')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Deliberate no-op: merge revision for unifying independent V9 branch heads."""
    pass


def downgrade() -> None:
    """Deliberate no-op: merge revision for unifying independent V9 branch heads."""
    pass
