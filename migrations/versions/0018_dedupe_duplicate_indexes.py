"""Deduplicate duplicate indexes & close model-migration constraint drift (#547)

Revision ID: 0018_dedupe_duplicate_indexes
Revises: 0017_close_model_migration_drift
Create Date: 2026-08-16

#547 根因修复 —— 模型↔迁移收敛（db_model.analysis_tasks / report 已定型
的同一缺陷的两处漏网 + 两处约束漂移）：

  1. knowledge_chunks.document_id 两份同列索引：模型 index=True 生成的
     ix_knowledge_chunks_document_id + __table_args__ 遗留的 idx_chunk_document
     （initial 迁移两个都建了）。保留 ix_*（create_all 也只生成它），删
     idx_chunk_document。模型侧已移除对应 Index 声明。
  2. data_sources.status 两份同列索引：0011 建 idx_datasource_status，0017
     又补 ix_data_sources_status（模型 index=True 出生名）。保留 ix_*，删
     idx_datasource_status。模型侧已移除对应 Index 声明。
  3. reports 的迁移侧同款重复（sibling）：initial 同时建了 idx_report_session/
     ix_reports_session_id、idx_report_status/ix_reports_status，而模型只声明
     idx_report_* —— 删迁移侧的 ix_reports_session_id / ix_reports_status。
  4. layers.creator_id 只放松了 PG（e46935）；SQLite 链保持 NOT NULL 与模型
     nullable=True 漂移。补 SQLite 分支（batch 重建）。
  5. uq_datasource_org_name 是 0011 在两个方言都建了、但模型 __table_args__
     一直缺漏的唯一约束 —— 模型补上后 autogenerate 不再反复想 drop 它。

既有库 upgrade 即收敛；downgrade 对称回退。
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0018_dedupe_duplicate_indexes'
down_revision: Union[str, Sequence[str], None] = '0017_close_model_migration_drift'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


#: 要删除的重复单列索引：(索引名, 表名, 列名)。
_DUP_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_chunk_document", "knowledge_chunks", "document_id"),
    ("idx_datasource_status", "data_sources", "status"),
    ("ix_reports_session_id", "reports", "session_id"),
    ("ix_reports_status", "reports", "status"),
)


def upgrade() -> None:
    if _is_sqlite():
        for name, table, _column in _DUP_INDEXES:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_index(name)
        # layers.creator_id 放松为 nullable（与模型及 PG 分支对齐）。
        with op.batch_alter_table('layers', schema=None) as batch_op:
            batch_op.alter_column(
                'creator_id',
                existing_type=sa.String(length=255),
                nullable=True,
            )
    else:
        for name, _table, _column in _DUP_INDEXES:
            op.execute(f"DROP INDEX IF EXISTS {name}")
        # layers.creator_id 已于 e46935（PG 分支）放松为 nullable —— 无需再动。


def downgrade() -> None:
    if _is_sqlite():
        for name, table, column in _DUP_INDEXES:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.create_index(name, [column])
        with op.batch_alter_table('layers', schema=None) as batch_op:
            batch_op.alter_column(
                'creator_id',
                existing_type=sa.String(length=255),
                nullable=False,
            )
    else:
        for name, table, column in _DUP_INDEXES:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")
        # PG 分支的 0018 upgrade 对 layers 不做任何修改（e46935 早已把
        # creator_id 放松为 nullable 并建立 ON DELETE SET NULL 的 FK），因此
        # PG downgrade 也绝不能碰 layers —— 否则一次 0018 downgrade 会把 FK
        # 换成无 ON DELETE SET NULL 的版本并重新 SET NOT NULL，永久丢失
        # 用户删除级联语义（review B1 / #547）。