"""Add CHECK constraint on AnalysisTask.progress (0-100)

Revision ID: a1b2c3d4e5f6
Revises: e46935cd5dd1
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op, context


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e46935cd5dd1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    """Add CHECK constraint: progress must be between 0 and 100."""
    if _is_sqlite():
        # SQLite: CHECK constraints added via batch_alter_table
        with op.batch_alter_table("analysis_tasks", schema=None) as batch_op:
            batch_op.create_check_constraint(
                "ck_task_progress",
                "progress >= 0 AND progress <= 100",
            )
    else:
        # PostgreSQL
        op.execute("""
            ALTER TABLE analysis_tasks
                ADD CONSTRAINT ck_task_progress
                CHECK (progress >= 0 AND progress <= 100)
        """)


def downgrade() -> None:
    """Remove CHECK constraint on progress."""
    if _is_sqlite():
        with op.batch_alter_table("analysis_tasks", schema=None) as batch_op:
            batch_op.drop_constraint("ck_task_progress", type_="check")
    else:
        op.execute("ALTER TABLE analysis_tasks DROP CONSTRAINT IF EXISTS ck_task_progress")
