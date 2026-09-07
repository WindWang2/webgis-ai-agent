"""Wave 1 durable artifact store: artifact_revisions (append-only content ledger)

Revision ID: 0026_artifact_revisions
Revises: 0025_data_fabric_durable_stats
Create Date: 2026-09-07

目标 D Wave 1（audit 01-artifact-authority-map.md §7.3）：晋升内容此前只以
``Artifact.metadata_json.content_location`` 单指针存在（覆写式、无历史、
无引用计数）。新表 ``artifact_revisions`` 为每次物化记一行不可变修订：

- content_sha256 = 载荷摘要（= BlobStore 键，内容寻址、跨 artifact 去重）；
- (artifact_id, content_sha256) 唯一 —— 重晋升同内容幂等复用同一行；
- workflow_run_id 可空索引（无 FK：修订是持久证据，run 删除不连带销毁）；
- pinned_at 用户 pin（GC 绝不删除被任何修订引用的 blob，与层级无关）。

纯新增（新表 + 新索引），无数据改写；create_all-coexistence guard 保护
（repo convention，0022/0025 同款）；SQLite 与 PostgreSQL 兼容。
downgrade 反序删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0026_artifact_revisions"
down_revision: Union[str, Sequence[str], None] = "0025_data_fabric_durable_stats"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "artifact_revisions"


def _table_exists(table: str) -> bool:
    """create_all-coexistence guard（repo convention，0021-0025 同款）。"""
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if not _table_exists(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=255), primary_key=True),
            sa.Column(
                "artifact_id",
                sa.String(length=255),
                sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            # server_default="1" mirrors the ORM default (0022 convention:
            # raw-SQL inserts still get a valid revision number).
            sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("content_location", sa.String(length=500), nullable=False),
            sa.Column("content_type", sa.String(length=20), nullable=False,
                      server_default="json"),
            sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("workflow_run_id", sa.String(length=255), nullable=True),
            sa.Column("pinned_at", sa.DateTime(), nullable=True),
            # Sibling convention (0022/0024): created_at is nullable with a
            # client-side UTC default — no server default (CURRENT_TIMESTAMP
            # in TIMESTAMP WITHOUT TIME ZONE stores session-local wall time
            # on PG, which diverges from the ORM's UTC values).
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.CheckConstraint("revision_no >= 1", name="ck_artifact_revision_no_pos"),
            sa.CheckConstraint(
                "content_type IN ('json', 'binary')",
                name="ck_artifact_revision_content_type",
            ),
        )
    # 内容身份幂等（唯一索引同时服务 artifact 内历史扫描 —— 0020 左前缀
    # 去重约定：不再为 artifact_id 单独建冗余索引）。guards：已存在的
    # create_all 自举库不再重复创建。
    if not _index_exists(_TABLE, "uq_artifact_revision_content"):
        op.create_index(
            "uq_artifact_revision_content", _TABLE,
            ["artifact_id", "content_sha256"], unique=True,
        )
    if not _index_exists(_TABLE, "idx_artifact_revision_sha"):
        op.create_index("idx_artifact_revision_sha", _TABLE, ["content_sha256"])
    if not _index_exists(_TABLE, "idx_artifact_revision_run"):
        op.create_index("idx_artifact_revision_run", _TABLE, ["workflow_run_id"])


def downgrade() -> None:
    if _index_exists(_TABLE, "idx_artifact_revision_run"):
        op.drop_index("idx_artifact_revision_run", table_name=_TABLE)
    if _index_exists(_TABLE, "idx_artifact_revision_sha"):
        op.drop_index("idx_artifact_revision_sha", table_name=_TABLE)
    if _index_exists(_TABLE, "uq_artifact_revision_content"):
        op.drop_index("uq_artifact_revision_content", table_name=_TABLE)
    if _table_exists(_TABLE):
        op.drop_table(_TABLE)
