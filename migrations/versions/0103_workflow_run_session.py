"""Persist the server-owned evidence session for workflow continuation."""
from alembic import op
import sqlalchemy as sa

revision = "0103_workflow_run_session"
down_revision = "0102_owner_token_hashes"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    return column in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)
    }


def upgrade() -> None:
    # create_all 共存守卫：模型已声明 session_id，create_all 库上无守卫的
    # add_column 即 duplicate column。
    if not _column_exists("workflow_runs", "session_id"):
        op.add_column("workflow_runs", sa.Column("session_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_runs", "session_id")
