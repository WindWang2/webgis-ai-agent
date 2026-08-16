"""Close model↔migration drift: owner_token & missing single-column indexes

#497 集成重做：原分支的 0015 与已合并的 0015_uploads_session_upload_index /
0016_knowledge_documents_owner（PR #505）编号与内容冲突——knowledge_documents
的 org_id/creator_id 两列、FK 与 idx_document_org 已由 0016 覆盖。本迁移只
保留仍然缺失的部分，链到 0016 之上：

  1. analysis_tasks.owner_token 缺索引：任务中心归属谓词（jobs/store.py
     _ownership_predicate 的 OR 分支 `owner_token == :token`）在匿名会话下高频
     全表扫描。补 idx_task_owner_token（与模型 db_model.py 的显式 Index 对齐）。
  2. 补齐 autogenerate 期望、但迁移链从未创建的单列索引：
     ix_cartography_templates_kind / _name / _is_builtin（c1d2e3f4a5b6 只建了
     idx_template_* 同列旧名索引）、ix_data_sources_status（0011 只建了
     idx_datasource_status）。

env.py 补全 model import（project/data_fabric 显式注册）由本分支另一处提交
覆盖；漏 import 任何一个 model 模块，autogenerate 都会把该模块的表当作
「metadata 里不存在」→ 对已迁移库生成 drop_table（数据丢失风险）。

既有库执行 upgrade 即对齐；downgrade 对称回退。
"""
from typing import Sequence, Union

from alembic import op, context


# revision identifiers, used by Alembic.
revision: str = '0017_close_model_migration_drift'
down_revision: Union[str, Sequence[str], None] = '0016_knowledge_documents_owner'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: 单列补齐索引：(索引名, 表名, 列名)。
_MISSING_INDEXES: tuple[tuple[str, str, str], ...] = (
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
    for name, table, column in _MISSING_INDEXES:
        if _is_sqlite():
            op.create_index(name, table, [column])
        else:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")


def downgrade() -> None:
    for name, table, _column in reversed(_MISSING_INDEXES):
        if _is_sqlite():
            op.drop_index(name, table_name=table)
        else:
            op.execute(f"DROP INDEX IF EXISTS {name}")
