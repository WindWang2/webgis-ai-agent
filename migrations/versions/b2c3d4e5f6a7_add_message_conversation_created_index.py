"""Add composite index on messages (conversation_id, created_at)

Revision ID: b2c3d4e5f6a7
Revises: 6ef479051297
Create Date: 2026-07-24

BUG-15 - Message.created_at had no index. Loading a conversation's message
history runs `WHERE conversation_id = ? ORDER BY created_at`, which without
a covering index degrades to a full table scan + filesort on large
conversations. This adds a composite index on (conversation_id, created_at)
so that filter + sort are both served from the index.
"""
from typing import Sequence, Union

from alembic import op, context


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = '6ef479051297'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    """Add index on messages(conversation_id, created_at)."""
    if _is_sqlite():
        # SQLite: use batch_alter_table (render_as_batch=True in env.py).
        with op.batch_alter_table("messages", schema=None) as batch_op:
            batch_op.create_index(
                "idx_message_conversation_created",
                ["conversation_id", "created_at"],
            )
    else:
        # PostgreSQL
        # IF NOT EXISTS: Base.metadata.create_all() 在启动时已按模型定义建好同名索引，
        # alembic upgrade 会与此碰撞 -> "relation already exists"。幂等化以共存。
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_message_conversation_created
                ON messages (conversation_id, created_at)
        """)


def downgrade() -> None:
    """Remove the messages composite index."""
    if _is_sqlite():
        with op.batch_alter_table("messages", schema=None) as batch_op:
            batch_op.drop_index("idx_message_conversation_created")
    else:
        op.execute("DROP INDEX IF EXISTS idx_message_conversation_created")
