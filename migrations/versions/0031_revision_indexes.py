"""artifact_revisions: GC/retention scan covering indexes

Revision ID: 0031_revision_indexes
Revises: 0030_revision_no_unique
Create Date: 2026-09-07

Round-1 review fix（PERF MAJOR-2 / MINOR-3）：保留与引用计数清扫此前在
无索引列上全表扫描 ——

- ``content_location``：``referencing_counts`` 的 IN 引用计数扫描
  （promotion GC 快照把整个 blob store 的 location 传进来）；
- ``created_at``：``plan_retention_cleanup`` 的超龄 cutoff 谓词
  （``ArtifactRevision.created_at < cutoff``，裸列无函数包裹，可直接命中）；
- ``pinned_at``：``pinned_content_sha256s`` 的 pin 保护扫描（PG 用部分索引
  ``WHERE pinned_at IS NOT NULL`` 把索引收缩到只有 pin 行；SQLite 退化普通
  索引 —— 漂移守卫 tests/test_deploy_migration_wiring.py 按列元组比对，
  两侧列元组一致）。

纯 additive，无数据改写；存在性守卫保证重入。create_all-coexistence 纪律
同 0022-0030：索引已在 ``app/models/project.ArtifactRevision.__table_args__``
声明，create_all 与本迁移产出按列元组一致。downgrade 只删索引。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0031_revision_indexes"
down_revision: Union[str, Sequence[str], None] = "0030_revision_no_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "artifact_revisions"

#: (index name, columns, partial where or None)。PG 支持部分索引 → pinned_at
#: 只索引被 pin 的行；SQLite 退化普通索引（列元组不变）。
_INDEXES = (
    ("idx_artifact_revision_content_location", ["content_location"], None),
    ("idx_artifact_revision_created_at", ["created_at"], None),
    (
        "idx_artifact_revision_pinned_at",
        ["pinned_at"],
        "pinned_at IS NOT NULL",
    ),
)


def _existing_indexes(table: str) -> set:
    return {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    existing = _existing_indexes(_TABLE)
    for name, cols, where in _INDEXES:
        if name in existing:
            continue
        if where and is_pg:
            op.create_index(
                name, _TABLE, cols,
                postgresql_where=sa.text(where),
            )
        else:
            op.create_index(name, _TABLE, cols)


def downgrade() -> None:
    existing = _existing_indexes(_TABLE)
    for name, _cols, _where in reversed(_INDEXES):
        if name in existing:
            op.drop_index(name, table_name=_TABLE)
