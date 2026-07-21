"""Add ForeignKey ondelete clauses and CHECK constraints

Revision ID: e46935cd5dd1
Revises: 6c68ec475cfa
Create Date: 2026-07-21
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e46935cd5dd1'
down_revision: Union[str, Sequence[str], None] = '6c68ec475cfa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    is_sqlite = _is_sqlite()

    if is_sqlite:
        # SQLite: use batch_alter_table which recreates the table behind the scenes.
        # Constraints must be named explicitly so they can be dropped/recreated.
        _upgrade_sqlite()
    else:
        # PostgreSQL: use ALTER TABLE with named constraints.
        _upgrade_postgres()


def _upgrade_sqlite() -> None:
    """SQLite-compatible migration.

    SQLite does not enforce FOREIGN KEY constraints by default and has limited
    ALTER TABLE support. We only create the missing knowledge_chunks table here;
    ON DELETE clauses and CHECK constraints are enforced on PostgreSQL.
    """
    op.execute(
        "CREATE TABLE IF NOT EXISTS knowledge_chunks ("
        "id VARCHAR(36) NOT NULL, "
        "document_id VARCHAR(36) NOT NULL, "
        "content TEXT NOT NULL, "
        "chunk_index INTEGER NOT NULL, "
        "start_char INTEGER, "
        "end_char INTEGER, "
        "PRIMARY KEY (id), "
        "FOREIGN KEY(document_id) REFERENCES knowledge_documents (id) ON DELETE CASCADE"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunk_document "
        "ON knowledge_chunks (document_id)"
    )


def _upgrade_postgres() -> None:
    """PostgreSQL migration using ALTER TABLE."""
    op.execute("""
        ALTER TABLE users
            DROP CONSTRAINT IF EXISTS users_org_id_fkey,
            ADD CONSTRAINT users_org_id_fkey
                FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
            ALTER COLUMN token_version SET NOT NULL
    """)

    op.execute("""
        ALTER TABLE layers
            DROP CONSTRAINT IF EXISTS layers_creator_id_fkey,
            ADD CONSTRAINT layers_creator_id_fkey
                FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE SET NULL,
            ALTER COLUMN creator_id DROP NOT NULL
    """)

    op.execute("""
        ALTER TABLE analysis_tasks
            DROP CONSTRAINT IF EXISTS analysis_tasks_org_id_fkey,
            ADD CONSTRAINT analysis_tasks_org_id_fkey
                FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE,
            DROP CONSTRAINT IF EXISTS analysis_tasks_creator_id_fkey,
            ADD CONSTRAINT analysis_tasks_creator_id_fkey
                FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE SET NULL,
            DROP CONSTRAINT IF EXISTS analysis_tasks_layer_id_fkey,
            ADD CONSTRAINT analysis_tasks_layer_id_fkey
                FOREIGN KEY (layer_id) REFERENCES layers(id) ON DELETE SET NULL,
            DROP CONSTRAINT IF EXISTS analysis_tasks_result_layer_id_fkey,
            ADD CONSTRAINT analysis_tasks_result_layer_id_fkey
                FOREIGN KEY (result_layer_id) REFERENCES layers(id) ON DELETE SET NULL,
            ALTER COLUMN creator_id DROP NOT NULL
    """)

    op.execute("DROP INDEX IF EXISTS idx_task_celery")
    op.execute("DROP INDEX IF EXISTS ix_analysis_tasks_celery_task_id")
    op.execute("""
        CREATE UNIQUE INDEX uq_analysis_tasks_celery_task_id
            ON analysis_tasks(celery_task_id)
    """)

    op.execute("DROP INDEX IF EXISTS idx_report_share")

    op.execute("""
        ALTER TABLE layer_permissions
            DROP CONSTRAINT IF EXISTS layer_permissions_layer_id_fkey,
            ADD CONSTRAINT layer_permissions_layer_id_fkey
                FOREIGN KEY (layer_id) REFERENCES layers(id) ON DELETE CASCADE,
            DROP CONSTRAINT IF EXISTS layer_permissions_user_id_fkey,
            ADD CONSTRAINT layer_permissions_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            DROP CONSTRAINT IF EXISTS layer_permissions_granted_by_fkey,
            ADD CONSTRAINT layer_permissions_granted_by_fkey
                FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE SET NULL
    """)

    op.execute("""
        ALTER TABLE messages
            DROP CONSTRAINT IF EXISTS messages_conversation_id_fkey,
            ADD CONSTRAINT messages_conversation_id_fkey
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    """)

    op.execute("""
        ALTER TABLE conversations
            ADD CONSTRAINT conversations_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    """)

    # knowledge_chunks table (detected as missing from initial schema)
    op.execute("CREATE TABLE IF NOT EXISTS knowledge_chunks (id VARCHAR(36) NOT NULL, document_id VARCHAR(36) NOT NULL, content TEXT NOT NULL, chunk_index INTEGER NOT NULL, start_char INTEGER, end_char INTEGER, PRIMARY KEY (id), FOREIGN KEY(document_id) REFERENCES knowledge_documents (id) ON DELETE CASCADE)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_chunk_document ON knowledge_chunks (document_id)")


def downgrade() -> None:
    """Downgrade schema."""
    is_sqlite = _is_sqlite()

    if is_sqlite:
        _downgrade_sqlite()
    else:
        _downgrade_postgres()


def _downgrade_sqlite() -> None:
    """SQLite-compatible downgrade — no-op (FK/CHECK constraints are application-level)."""
    pass


def _downgrade_postgres() -> None:
    """PostgreSQL downgrade."""
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")

    op.execute("""
        ALTER TABLE conversations
            DROP CONSTRAINT IF EXISTS conversations_user_id_fkey
    """)

    op.execute("""
        ALTER TABLE messages
            DROP CONSTRAINT IF EXISTS messages_conversation_id_fkey,
            ADD CONSTRAINT messages_conversation_id_fkey
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    """)

    op.execute("""
        ALTER TABLE layer_permissions
            DROP CONSTRAINT IF EXISTS layer_permissions_layer_id_fkey,
            ADD CONSTRAINT layer_permissions_layer_id_fkey
                FOREIGN KEY (layer_id) REFERENCES layers(id),
            DROP CONSTRAINT IF EXISTS layer_permissions_user_id_fkey,
            ADD CONSTRAINT layer_permissions_user_id_fkey
                FOREIGN KEY (user_id) REFERENCES users(id),
            DROP CONSTRAINT IF EXISTS layer_permissions_granted_by_fkey,
            ADD CONSTRAINT layer_permissions_granted_by_fkey
                FOREIGN KEY (granted_by) REFERENCES users(id)
    """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_report_share ON reports(share_code)")

    op.execute("DROP INDEX IF EXISTS uq_analysis_tasks_celery_task_id")
    op.execute("CREATE INDEX ix_analysis_tasks_celery_task_id ON analysis_tasks(celery_task_id)")
    op.execute("CREATE INDEX idx_task_celery ON analysis_tasks(celery_task_id)")

    op.execute("""
        ALTER TABLE analysis_tasks
            DROP CONSTRAINT IF EXISTS analysis_tasks_org_id_fkey,
            ADD CONSTRAINT analysis_tasks_org_id_fkey
                FOREIGN KEY (org_id) REFERENCES organizations(id),
            DROP CONSTRAINT IF EXISTS analysis_tasks_creator_id_fkey,
            ADD CONSTRAINT analysis_tasks_creator_id_fkey
                FOREIGN KEY (creator_id) REFERENCES users(id),
            DROP CONSTRAINT IF EXISTS analysis_tasks_layer_id_fkey,
            ADD CONSTRAINT analysis_tasks_layer_id_fkey
                FOREIGN KEY (layer_id) REFERENCES layers(id),
            DROP CONSTRAINT IF EXISTS analysis_tasks_result_layer_id_fkey,
            ADD CONSTRAINT analysis_tasks_result_layer_id_fkey
                FOREIGN KEY (result_layer_id) REFERENCES layers(id),
            ALTER COLUMN creator_id SET NOT NULL
    """)

    op.execute("""
        ALTER TABLE layers
            DROP CONSTRAINT IF EXISTS layers_creator_id_fkey,
            ADD CONSTRAINT layers_creator_id_fkey
                FOREIGN KEY (creator_id) REFERENCES users(id),
            ALTER COLUMN creator_id SET NOT NULL
    """)

    # Note: token_version column is dropped by migration 6c68ec475cfa downgrade,
    # not here, to avoid double-drop conflicts.
