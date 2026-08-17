"""Drop leftover duplicate single-column indexes & add missing model index (#618)

审计 #618（P3 收口，「#547 sibling 族」）在 0017/0018 之后仍遗留的索引收敛：

  1. cartography_templates 重复索引 + 缺失索引：
     - c1d2e3f4a5b6 建了 idx_template_kind/name/category/builtin，0017 又补建
       同列 ix_cartography_templates_kind/name/is_builtin —— 迁移侧两套并存。
       模型（db_model.CartographyTemplate）只声明 ix_cartography_templates_*
       （index=True）+ 两个复合索引 idx_template_builtin_kind/org_kind。
       故删迁移侧的 idx_template_kind/name/category/builtin。
     - 模型 category=index=True 的出生名 ix_cartography_templates_category
       从未进过迁移链（0017 只补了 kind/name/is_builtin）——补建。
  2. analysis_tasks.status 两份单列索引：initial 同时建了 idx_task_status
     （模型 __table_args__ 声明名）与 ix_analysis_tasks_status（模型没有）——
     删 ix_analysis_tasks_status。
  3. e46935 只在 PG 删了 idx_task_celery / idx_report_share：SQLite 链残留
     与 ix_analysis_tasks_celery_task_id / ix_reports_share_code 同列的重复
     索引 —— SQLite 分支补删（PG IF EXISTS 幂等 no-op），两方言 schema 对齐。

既有库 upgrade 即收敛；downgrade 对称回退。
"""
from typing import Sequence, Union

from alembic import op, context


# revision identifiers, used by Alembic.
revision: str = '0019_drop_leftover_duplicate_indexes'
down_revision: Union[str, Sequence[str], None] = '0018_dedupe_duplicate_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


#: 要删除的迁移侧重复索引：(索引名, 表名, 列名（供 downgrade 重建）)。
#: PG 侧若已被早期迁移删除（idx_task_celery / idx_report_share），DROP IF
#: EXISTS 幂等 no-op。
_DUP_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_template_kind", "cartography_templates", "kind"),
    ("idx_template_name", "cartography_templates", "name"),
    ("idx_template_category", "cartography_templates", "category"),
    ("idx_template_builtin", "cartography_templates", "is_builtin"),
    ("ix_analysis_tasks_status", "analysis_tasks", "status"),
    ("idx_task_celery", "analysis_tasks", "celery_task_id"),
    ("idx_report_share", "reports", "share_code"),
)


def upgrade() -> None:
    if _is_sqlite():
        for name, table, _column in _DUP_INDEXES:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.drop_index(name)
        op.create_index(
            "ix_cartography_templates_category", "cartography_templates", ["category"]
        )
    else:
        for name, _table, _column in _DUP_INDEXES:
            op.execute(f"DROP INDEX IF EXISTS {name}")
        # 0017 只补了 kind/name/is_builtin；category 的模型出生名索引此处补齐。
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_cartography_templates_category "
            "ON cartography_templates (category)"
        )


def downgrade() -> None:
    if _is_sqlite():
        op.drop_index("ix_cartography_templates_category", table_name="cartography_templates")
        for name, table, column in _DUP_INDEXES:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.create_index(name, [column])
    else:
        op.execute("DROP INDEX IF EXISTS ix_cartography_templates_category")
        for name, table, column in _DUP_INDEXES:
            op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})")