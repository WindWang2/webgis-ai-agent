"""Persist the server-owned evidence session for workflow continuation."""
from alembic import op
import sqlalchemy as sa

revision = "0103_workflow_run_session"
down_revision = "0102_owner_token_hashes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("session_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_runs", "session_id")
