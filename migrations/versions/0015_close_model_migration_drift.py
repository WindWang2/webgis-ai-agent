"""Close model↔migration drift: knowledge_documents org/creator, owner_token & missing indexes

env.py 补全 model import 后，`alembic check` 暴露出迁移链与模型的 additive 漂移。
本迁移只加不删（不 drop 任何列/表/既有索引）：

  1. knowledge_documents 缺模型定义的 org_id / creator_id 两列（含 FK：
     org_id → organizations ON DELETE CASCADE；creator_id → users ON DELETE SET NULL）
     与 idx_document_org 索引 —— 自 85e4939d7e07 建表以来迁移链从未补过。
  2. analysis_tasks.owner_token 缺索引：任务中心归属谓词（jobs/store.py
     _ownership_predicate 的 OR 分支 `owner_token == :token`）在匿名会话下高频
     全表扫描。补 idx_task_owner_token（与模型 db_model.py 的显式 Index 对齐）。
  3. 补齐 autogenerate 期望、但迁移链从未创建的单列索引：
     ix_cartography_templates_kind / _name / _is_builtin（c1d2e3f4a5b6 只建了
     idx_template_* 同列旧名索引）、ix_data_sources_status（0011 只建了
     idx_datasource_status）。

既有库执行 upgrade 即对齐；downgrade 对称回退。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0015_close_model_migration_drift"
down_revision: Union[str, Sequence[str], None] = "0014_workflow_provenance_revisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 单列补齐索引：(索引名, 表名, 列名)。
_MISSING_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_document_org", "knowledge_documents", "org_id"),
    ("idx_task_owner_token", "analysis_tasks", "owner_token"),
    ("ix_cartography_templates_kind", "cartography_templates", "kind"),
    ("ix_cartography_templates_name", "cartography_templates", "name"),
    ("ix_cartography_templates_is_builtin", "cartography_templates", "is_builtin"),
    ("ix_data_sources_status", "data_sources", "status"),
)


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        _upgrade_sqlite()
    else:
        _upgrade_postgres()


def _upgrade_sqlite() -> None:
    # SQLite 无法 ADD CONSTRAINT，用 batch 重建表把新列与 FK 一次带上。
    # knowledge_documents 没有 CHECK 约束，重建无丢失风险。
    with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("org_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("creator_id", sa.String(length=255), nullable=True))
        batch_op.create_foreign_key(
            "fk_knowledge_documents_org_id_organizations",
            "organizations", ["org_id"], ["id"], ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_knowledge_documents_creator_id_users",
            "users", ["creator_id"], ["id"], ondelete="SET NULL",
        )
    for name, table, column in _MISSING_INDEXES:
        op.create_index(name, table, [column])


def _upgrade_postgres() -> None:
    op.execute("ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS org_id INTEGER")
    op.execute("ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS creator_id VARCHAR(255)")
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS fk_knowledge_documents_org_id_organizations")
    op.execute(
        "ALTER TABLE knowledge_documents ADD CONSTRAINT fk_knowledge_documents_org_id_organizations "
        "FOREIGN KEY (org_id) REFERENCES organizations(id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS fk_knowledge_documents_creator_id_users")
    op.execute(
        "ALTER TABLE knowledge_documents ADD CONSTRAINT fk_knowledge_documents_creator_id_users "
        "FOREIGN KEY (creator_id) REFERENCES users(id) ON DELETE SET NULL"
    )
    for name, table, column in _MISSING_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")


def downgrade() -> None:
    if _is_sqlite():
        _downgrade_sqlite()
    else:
        _downgrade_postgres()


def _downgrade_sqlite() -> None:
    for name, table, _column in reversed(_MISSING_INDEXES):
        op.drop_index(name, table_name=table)
    with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
        batch_op.drop_constraint("fk_knowledge_documents_creator_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_knowledge_documents_org_id_organizations", type_="foreignkey")
        batch_op.drop_column("creator_id")
        batch_op.drop_column("org_id")


def _downgrade_postgres() -> None:
    for name, table, _column in reversed(_MISSING_INDEXES):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS fk_knowledge_documents_creator_id_users")
    op.execute("ALTER TABLE knowledge_documents DROP CONSTRAINT IF EXISTS fk_knowledge_documents_org_id_organizations")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS creator_id")
    op.execute("ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS org_id")
