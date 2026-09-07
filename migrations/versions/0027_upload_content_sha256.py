"""Wave 3 ingest V4: uploads.content_sha256 (upload content identity)

Revision ID: 0027_upload_content_sha256
Revises: 0026_artifact_revisions
Create Date: 2026-09-07

目标 D Wave 3（audit 03-ingest-gaps.md §7 R2）：上传路径此前没有任何内容
身份 —— 同一文件传两次 = 两个 uuid 目录 + 两行 DB 记录，无去重、无血缘键。
新增可空列 ``uploads.content_sha256``（上传字节 sha256 hex）：

- **可空**：存量行与 dedup 关闭的上传没有指纹，绝不虚构（与 V3 摄入管线
  的 ``ingest_content_sha256`` 载荷元数据同语义，两侧键收敛为一族）；
- **非唯一**：内容身份按会话幂等（WHERE session_id = ? AND
  content_sha256 = ?），跨会话不共享 —— 全局唯一会把内容变成跨租户
  能力令牌（S42 纪律）；
- 探测索引 ``ix_uploads_session_content``（session_id, content_sha256）
  服务幂等再导入探测；NULL 键不参与去重。

纯新增（一列 + 一索引），无数据改写；create_all-coexistence guard 保护
（repo convention，0022/0024/0026 同款）；SQLite 与 PostgreSQL 兼容。
downgrade 反序删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0027_upload_content_sha256"
down_revision: Union[str, Sequence[str], None] = "0026_artifact_revisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "uploads"
_COLUMN = "content_sha256"
_INDEX = "ix_uploads_session_content"


def _column_exists(table: str, column: str) -> bool:
    """create_all-coexistence guard（repo convention，0022/0024 同款）。"""
    return column in {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)
    }


def _index_exists(table: str, index: str) -> bool:
    return index in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def upgrade() -> None:
    if not _column_exists(_TABLE, _COLUMN):
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(length=64), nullable=True),
        )
    if not _index_exists(_TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ["session_id", _COLUMN])


def downgrade() -> None:
    if _index_exists(_TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _column_exists(_TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
