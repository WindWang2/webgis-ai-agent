"""Add composite index on uploads (session_id, upload_time)

Issue #429: GET /api/v1/uploads (list_uploads) 查询
``WHERE session_id = ? ORDER BY upload_time DESC`` + COUNT，每次面板打开执行。
uploads 表之前只有主键索引 —— 按会话列出的代价是全表扫描 + 排序，随全局
上传行数线性增长（每个会话的列出请求都要扫所有其他会话的行）。

本迁移为存量库补建模型（app/models/upload.py）已声明的复合索引
ix_uploads_session_time，使过滤 + 排序都走索引扫描。

Revision ID: 0015_uploads_session_upload_index
Revises: 0014_workflow_provenance_revisions
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op, context


# revision identifiers, used by Alembic.
revision: str = '0015_uploads_session_upload_index'
down_revision: Union[str, Sequence[str], None] = '0014_workflow_provenance_revisions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = 'ix_uploads_session_time'


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    """Create the composite index for per-session upload listing."""
    if _is_sqlite():
        with op.batch_alter_table("uploads", schema=None) as batch_op:
            batch_op.create_index(INDEX_NAME, ["session_id", "upload_time"])
    else:
        # PostgreSQL: IF NOT EXISTS 与启动期 create_all 幂等共存（新库由
        # create_all 按模型建出同名索引，alembic upgrade 不得碰撞报错）。
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} ON uploads (session_id, upload_time)"
        )


def downgrade() -> None:
    """Drop the composite index (data untouched)."""
    if _is_sqlite():
        with op.batch_alter_table("uploads", schema=None) as batch_op:
            batch_op.drop_index(INDEX_NAME)
    else:
        op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
