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
    """Merge revision: joins branch heads into a single lineage tip.

    Carries no DDL of its own — the schema changes live in the merged
    branch revisions. See downgrade() for the rollback contract.
    """
    pass


def downgrade() -> None:
    """deliberate no-op: merge points carry no DDL, so there is nothing to
    undo at this revision.

    A merge revision only collapses multiple branch heads into one lineage
    tip; every schema effect belongs to the branch revisions it joins and is
    rolled back by *their* downgrades when the chain walks below the branch
    tips. Reverting the merge point itself is a lineage operation, not a
    schema operation, and is performed by downgrading those branch tips (or
    re-branching), never by emitting DDL here. This mirrors the declared
    no-op contract enforced by
    tests/integration/test_migration_lifecycle.py::test_every_migration_has_real_downgrade.
    """
    pass
