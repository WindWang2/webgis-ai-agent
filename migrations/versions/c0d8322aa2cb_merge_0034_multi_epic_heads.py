"""merge_0034_multi_epic_heads

Revision ID: c0d8322aa2cb
Revises: 0034_data_fabric_v7_facts_feedback, 0034_geocompute_v7_dataflow, 0034_lakehouse_catalog, 0034_workflow_v5_runtime
Create Date: 2026-09-10 15:57:25.945431

"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = 'c0d8322aa2cb'
down_revision: Union[str, Sequence[str], None] = ('0034_data_fabric_v7_facts_feedback', '0034_geocompute_v7_dataflow', '0034_lakehouse_catalog', '0034_workflow_v5_runtime')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Deliberate no-op: merge revision for unifying independent 0034 branch heads."""
    pass
