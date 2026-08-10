"""Add composite indexes declared in ProjectDataset/WorkflowRun models

Revision ID: 0012_add_composite_indexes_pd_wr
Revises: 0011_enterprise_geospatial_data_fabric
Create Date: 2026-08-10

Audit DATA-09: migration 0010 created the single-column indexes declared in
``app/models/project.py`` but dropped the two COMPOSITE ones:

- ``idx_project_dataset_pid_created`` (project_id, created_at) — supports
  "datasets of a project ordered by creation" list queries; the single-column
  ``project_id`` index filters but cannot serve the sort.
- ``idx_workflow_run_wid_created`` (workflow_id, created_at) — same pattern for
  the workflow-runs history list.

New installs get them via ``Base.metadata.create_all()``, but existing
databases upgraded via alembic never would — this migration closes the gap.
Follows the idempotent pattern of ``f123456789ab`` (IF NOT EXISTS on Postgres
so it coexists with create_all).
"""
from typing import Sequence, Union

from alembic import op, context


# revision identifiers, used by Alembic.
revision: str = '0012_add_composite_indexes_pd_wr'
down_revision: Union[str, Sequence[str], None] = '0011_enterprise_geospatial_data_fabric'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    """Add composite indexes for project_datasets and workflow_runs."""
    if _is_sqlite():
        with op.batch_alter_table("project_datasets", schema=None) as batch_op:
            batch_op.create_index("idx_project_dataset_pid_created", ["project_id", "created_at"])

        with op.batch_alter_table("workflow_runs", schema=None) as batch_op:
            batch_op.create_index("idx_workflow_run_wid_created", ["workflow_id", "created_at"])
    else:
        # PostgreSQL: IF NOT EXISTS 与 Base.metadata.create_all() 启动建索引共存
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_project_dataset_pid_created
            ON project_datasets (project_id, created_at)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_workflow_run_wid_created
            ON workflow_runs (workflow_id, created_at)
        """)


def downgrade() -> None:
    """Remove composite indexes."""
    if _is_sqlite():
        with op.batch_alter_table("project_datasets", schema=None) as batch_op:
            batch_op.drop_index("idx_project_dataset_pid_created")

        with op.batch_alter_table("workflow_runs", schema=None) as batch_op:
            batch_op.drop_index("idx_workflow_run_wid_created")
    else:
        op.execute("DROP INDEX IF EXISTS idx_project_dataset_pid_created")
        op.execute("DROP INDEX IF EXISTS idx_workflow_run_wid_created")
