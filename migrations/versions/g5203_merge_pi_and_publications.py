"""merge Pi turn-attempt and map-publication heads

Revision ID: g5203_merge_pi_and_publications
Revises: g5201_turn_attempts, g5202_map_publications
Create Date: 2026-11-28 12:00:00.000000

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "g5203_merge_pi_and_publications"
down_revision: Union[str, Sequence[str], None] = (
    "g5201_turn_attempts",
    "g5202_map_publications",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Join the two existing migration histories without changing the schema."""
    pass


def downgrade() -> None:
    """Deliberate no-op. The parent revisions own their schema changes."""
    pass
