"""merge_0035_multi_epic_heads

Revision ID: c1e2f3a4b5c6
Revises: 0035_geocompute_v8_fabric, 0035_lakehouse_dataset_versions, 0035_workflow_v6_durable
Create Date: 2026-09-11

Unifies the three independent 0035 branch heads landed by parallel epics
(GeoCompute V8 / Lakehouse V8 / Workflow V6) after the #1187–#1196 merge wave.
"""
from typing import Sequence, Union


revision: str = "c1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = (
    "0035_geocompute_v8_fabric",
    "0035_lakehouse_dataset_versions",
    "0035_workflow_v6_durable",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Deliberate no-op: merge revision for unifying independent 0035 branch heads."""
    pass
