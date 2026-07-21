"""Add composite indexes for Layer and AnalysisTask

Revision ID: f123456789ab
Revises: a1b2c3d4e5f6
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op, context


# revision identifiers, used by Alembic.
revision: str = 'f123456789ab'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    """Add composite indexes for common multi-column query patterns."""
    if _is_sqlite():
        # SQLite: use batch_alter_table
        with op.batch_alter_table("layers", schema=None) as batch_op:
            batch_op.create_index("idx_layer_org_status", ["org_id", "status"])
            batch_op.create_index("idx_layer_org_category_status", ["org_id", "category", "status"])

        with op.batch_alter_table("analysis_tasks", schema=None) as batch_op:
            batch_op.create_index("idx_task_org_status", ["org_id", "status"])
            batch_op.create_index("idx_task_org_type_status", ["org_id", "task_type", "status"])
    else:
        # PostgreSQL
        op.execute("""
            CREATE INDEX idx_layer_org_status ON layers (org_id, status)
        """)
        op.execute("""
            CREATE INDEX idx_layer_org_category_status ON layers (org_id, category, status)
        """)
        op.execute("""
            CREATE INDEX idx_task_org_status ON analysis_tasks (org_id, status)
        """)
        op.execute("""
            CREATE INDEX idx_task_org_type_status ON analysis_tasks (org_id, task_type, status)
        """)


def downgrade() -> None:
    """Remove composite indexes."""
    if _is_sqlite():
        with op.batch_alter_table("layers", schema=None) as batch_op:
            batch_op.drop_index("idx_layer_org_status")
            batch_op.drop_index("idx_layer_org_category_status")

        with op.batch_alter_table("analysis_tasks", schema=None) as batch_op:
            batch_op.drop_index("idx_task_org_status")
            batch_op.drop_index("idx_task_org_type_status")
    else:
        op.execute("DROP INDEX IF EXISTS idx_layer_org_status")
        op.execute("DROP INDEX IF EXISTS idx_layer_org_category_status")
        op.execute("DROP INDEX IF EXISTS idx_task_org_status")
        op.execute("DROP INDEX IF EXISTS idx_task_org_type_status")
