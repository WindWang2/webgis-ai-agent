"""merge_0035_multi_epic_heads

Revision ID: e7a51c9d2f04
Revises: 0035_geocompute_v8_fabric, 0035_lakehouse_dataset_versions, 0035_workflow_v6_durable
Create Date: 2026-09-11 10:30:00.000000

Merge revision unifying the three independent 0035 branch heads produced by the
V7/V8 epic integration round (geocompute-v8 / lakehouse-v8 / workflow-v6).
Follows the c0d8322aa2cb (0034) precedent: per-branch migrations stay as-is,
this revision only converges the graph to a single head.
"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = 'e7a51c9d2f04'
down_revision: Union[str, Sequence[str], None] = ('0035_geocompute_v8_fabric', '0035_lakehouse_dataset_versions', '0035_workflow_v6_durable')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Deliberate no-op: merge revision for unifying independent 0035 branch heads."""
    pass
