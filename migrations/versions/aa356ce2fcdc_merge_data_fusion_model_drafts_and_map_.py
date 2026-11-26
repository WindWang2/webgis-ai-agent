"""merge data fusion, model drafts and map series heads

Revision ID: aa356ce2fcdc
Revises: 0110_data_fusion_authoring, m0111_model_drafts, g51_map_series
Create Date: 2026-11-26 18:37:32.774816

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'aa356ce2fcdc'
down_revision: Union[str, Sequence[str], None] = ('0110_data_fusion_authoring', 'm0111_model_drafts', 'g51_map_series')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join the three existing migration histories without changing the schema."""
    pass


def downgrade() -> None:
    """Deliberate no-op. The parent revisions own their schema changes."""
    pass
