"""Add carto_project_facts (project-scoped cartographic memory, ADR-0069)

Revision ID: 0021_add_carto_project_facts
Revises: 0020_drop_data_fabric_prefix_indexes
Create Date: 2026-08-23

specs/cartographic-quality-rules-and-memory-spec.md Phase 2：项目级制图事实
账本。记忆是先验而非证据（ADR-0069 决策 2），作用域严格是 project（决策 1）。

表设计要点：
- ``(project_id, kind, subject)`` 唯一 —— 写入是 upsert，事实不随 turn 数增长；
- ``idx_carto_fact_project_status`` 服务唯一的注入查询形态（project+status+kind）；
- ``idx_carto_fact_project_verified`` 服务 LRU 淘汰（按 last_verified_at 取最旧）；
- CHECK 约束把 kind/status 的合法取值钉在库层，防止后续代码写入未定义状态。

FK ``projects.id`` ON DELETE CASCADE：项目删除即记忆消失（无孤儿记忆）。
SQLite 在 create_table 内联 FK/CHECK；Postgres 同构。
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0021_add_carto_project_facts"
down_revision: Union[str, Sequence[str], None] = "0020_drop_data_fabric_prefix_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "carto_project_facts"


def _is_sqlite() -> bool:
    return context.get_context().dialect.name == "sqlite"


def _table_exists() -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(_TABLE)


def upgrade() -> None:
    if _table_exists():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("project_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("validity_tier", sa.String(length=32), nullable=True),
        sa.Column("evidence_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE",
            name="fk_carto_fact_project",
        ),
        sa.UniqueConstraint(
            "project_id", "kind", "subject", name="uq_carto_fact_identity",
        ),
        sa.CheckConstraint(
            "kind IN ('preference', 'recipe_outcome', 'data_profile', "
            "'shared_classification')",
            name="ck_carto_fact_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'stale', 'conflicted', 'retired')",
            name="ck_carto_fact_status",
        ),
    )
    op.create_index(
        "idx_carto_fact_project_status", _TABLE, ["project_id", "status", "kind"],
    )
    op.create_index(
        "idx_carto_fact_project_verified", _TABLE, ["project_id", "last_verified_at"],
    )


def downgrade() -> None:
    if not _table_exists():
        return
    # SQLite 的 drop_table 连带索引；Postgres 显式先删索引更干净。
    if not _is_sqlite():
        op.drop_index("idx_carto_fact_project_verified", table_name=_TABLE)
        op.drop_index("idx_carto_fact_project_status", table_name=_TABLE)
    op.drop_table(_TABLE)
