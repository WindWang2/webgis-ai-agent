"""Repair drift: knowledge_documents.org_id / creator_id missing from migration chain

Issue #476 的 drift check（tests/test_deploy_migration_wiring.py::
test_migrated_schema_matches_models）发现：Document 模型（app/models/
knowledge_base.py）声明的 org_id / creator_id 两列（A2 资源所有权改造时
加入模型）从未进过任何迁移 —— create_all 引导的库有这两列，而 Alembic
迁移链建出的库没有。把部署切到 Alembic 后该漂移会变成运行期
"column does not exist"。

本迁移补齐两列 + idx_document_org 索引（幂等：存量列/索引已存在时跳过）。

Revision ID: 0016_knowledge_documents_owner
Revises: 0015_uploads_session_upload_index
Create Date: 2026-08-16
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0016_knowledge_documents_owner'
down_revision: Union[str, Sequence[str], None] = '0015_uploads_session_upload_index'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'knowledge_documents'


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.add_column(sa.Column('org_id', sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column('creator_id', sa.String(length=255), nullable=True))
            batch_op.create_index('idx_document_org', ['org_id'])
    else:
        # PostgreSQL：IF NOT EXISTS 与 create_all 幂等共存（create_all 引导的
        # 存量库已有这两列 —— 见迁移头注释的 drift 背景）。
        op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS org_id INTEGER")
        op.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS creator_id VARCHAR(255)")
        op.execute(f"CREATE INDEX IF NOT EXISTS idx_document_org ON {TABLE} (org_id)")
        # FK 约束无 IF NOT EXISTS；DO 块按约束名幂等（与模型声明的 ondelete 对齐）
        op.execute(f"""
            DO $$ BEGIN
                ALTER TABLE {TABLE} ADD CONSTRAINT fk_knowledge_documents_org
                    FOREIGN KEY (org_id) REFERENCES organizations (id) ON DELETE CASCADE;
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)
        op.execute(f"""
            DO $$ BEGIN
                ALTER TABLE {TABLE} ADD CONSTRAINT fk_knowledge_documents_creator
                    FOREIGN KEY (creator_id) REFERENCES users (id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """)


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.drop_index('idx_document_org')
            batch_op.drop_column('creator_id')
            batch_op.drop_column('org_id')
    else:
        op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS fk_knowledge_documents_creator")
        op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS fk_knowledge_documents_org")
        op.execute("DROP INDEX IF EXISTS idx_document_org")
        op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS creator_id")
        op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS org_id")
