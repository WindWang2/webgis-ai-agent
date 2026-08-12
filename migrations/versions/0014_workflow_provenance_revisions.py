"""Workflow provenance: immutable revisions, run snapshots, dataset fingerprints

Additive 迁移（spec §31）。把 workflow/dataset/artifact/lineage 从「基本数据模型」
推进到可复现、可比较、可溯源的 provenance 子系统：

  * 新表 ``workflow_revisions`` —— append-only 不可变图快照（INV-REV1/2）。
  * ``workflow_runs`` 冻结执行时的 graph_snapshot / revision / 输入数据集指纹 /
    run_manifest / run_fingerprint / completed_steps（INV-SNAP1/2、INV-PART、
    INV-MAN）。新增 denormalized ``project_id``（修复 compare_runs 路由引用不存在
    列的 latent bug，并提供租户内 run 列表/比较的免 join 查询）。
  * ``project_datasets.detached_at`` —— 软 detach 墓碑，历史 lineage 不失真
    （INV-DEL1）。
  * ``artifacts.content_fingerprint`` 与 ``artifact_lineages.{source_dataset_id,
    source_dataset_fingerprint,content_fingerprint}`` —— 真实产物指纹与输入数据集
    溯源（INV-ART、INV-LIN4）。

全部新列 nullable，老行无需回填；不改写/不删既有列；不触碰任何 CHECK 约束。
SQLite 与 PostgreSQL 都用原生 ``ALTER TABLE ADD/DROP COLUMN``（SQLite 3.35+ 原生
支持且**保留 CHECK 约束**，无需 batch_alter 重建），因此不丢任何既有 CHECK。
PostgreSQL 全程 ``IF NOT EXISTS`` / ``IF EXISTS``，与 ``create_all`` 共存（仓库约定）。

Revision ID: 0014_workflow_provenance_revisions
Revises: 0013_unified_durable_job_runtime
Create Date: 2026-08-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0014_workflow_provenance_revisions"
down_revision: Union[str, Sequence[str], None] = "0013_unified_durable_job_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """Detect if the target database is SQLite."""
    return context.get_context().dialect.name == "sqlite"


# (table, column, sqlite-type, pg-type). All nullable, no default.
_NEW_COLUMNS: tuple[tuple[str, str, str, str], ...] = (
    ("project_datasets", "detached_at", "DATETIME", "TIMESTAMP"),
    ("workflows", "current_revision_id", "VARCHAR(255)", "VARCHAR(255)"),
    ("workflow_runs", "project_id", "VARCHAR(255)", "VARCHAR(255)"),
    ("workflow_runs", "workflow_revision_id", "VARCHAR(255)", "VARCHAR(255)"),
    ("workflow_runs", "graph_snapshot", "JSON", "JSON"),
    ("workflow_runs", "input_dataset_fingerprints", "JSON", "JSON"),
    ("workflow_runs", "completed_steps", "JSON", "JSON"),
    ("workflow_runs", "run_manifest", "JSON", "JSON"),
    ("workflow_runs", "run_fingerprint", "VARCHAR(64)", "VARCHAR(64)"),
    ("workflow_runs", "durable_job_id", "BIGINT", "BIGINT"),
    ("artifacts", "content_fingerprint", "VARCHAR(64)", "VARCHAR(64)"),
    ("artifact_lineages", "source_dataset_id", "VARCHAR(255)", "VARCHAR(255)"),
    ("artifact_lineages", "source_dataset_fingerprint", "VARCHAR(64)", "VARCHAR(64)"),
    ("artifact_lineages", "content_fingerprint", "VARCHAR(64)", "VARCHAR(64)"),
)

# (name, table, [columns], unique). All created with IF NOT EXISTS (PG) / plain (SQLite).
_NEW_INDEXES: tuple[tuple[str, str, list[str], bool], ...] = (
    ("idx_project_dataset_pid_detached", "project_datasets", ["project_id", "detached_at"], False),
    ("idx_workflow_current_revision", "workflows", ["current_revision_id"], False),
    ("idx_workflow_run_project_created", "workflow_runs", ["project_id", "created_at"], False),
    ("idx_workflow_run_fingerprint", "workflow_runs", ["run_fingerprint"], False),
    ("idx_artifact_content_fingerprint", "artifacts", ["content_fingerprint"], False),
    ("idx_lineage_source_dataset_id", "artifact_lineages", ["source_dataset_id"], False),
    ("idx_workflow_revision_wf_no", "workflow_revisions", ["workflow_id", "revision_no"], True),
    ("idx_workflow_revision_wf_created", "workflow_revisions", ["workflow_id", "created_at"], False),
    ("idx_workflow_revision_fingerprint", "workflow_revisions", ["graph_fingerprint"], False),
)


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table(table)


def upgrade() -> None:
    # 1. New table: workflow_revisions (immutable graph snapshots).
    if not _table_exists("workflow_revisions"):
        op.create_table(
            "workflow_revisions",
            sa.Column("id", sa.String(length=255), primary_key=True, nullable=False),
            sa.Column(
                "workflow_id", sa.String(length=255),
                sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("graph_spec", sa.JSON(), nullable=False),
            sa.Column("inputs_schema", sa.JSON(), nullable=True),
            sa.Column("graph_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )

    # 2. Additive columns (native ALTER preserves SQLite CHECK constraints).
    bind = op.get_bind()
    for table, col, sql_type, pg_type in _NEW_COLUMNS:
        if _is_sqlite():
            bind.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}"))
        else:
            bind.execute(
                sa.text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {pg_type}")
            )

    # 3. Indexes.
    for name, table, cols, unique in _NEW_INDEXES:
        cols_sql = ", ".join(cols)
        if _is_sqlite():
            # Fresh upgrade: index never exists yet. Guard cheaply via inspect.
            existing = {i["name"] for i in sa.inspect(bind).get_indexes(table)}
            if name not in existing:
                kind = "UNIQUE INDEX" if unique else "INDEX"
                bind.execute(sa.text(f"CREATE {kind} {name} ON {table} ({cols_sql})"))
        else:
            kind = "UNIQUE INDEX" if unique else "INDEX"
            bind.execute(sa.text(f"CREATE {kind} IF NOT EXISTS {name} ON {table} ({cols_sql})"))


def downgrade() -> None:
    bind = op.get_bind()
    # 1. Indexes.
    for name, table, _cols, _unique in reversed(_NEW_INDEXES):
        if _is_sqlite():
            existing = {i["name"] for i in sa.inspect(bind).get_indexes(table)}
            if name in existing:
                bind.execute(sa.text(f"DROP INDEX {name}"))
        else:
            bind.execute(sa.text(f"DROP INDEX IF EXISTS {name}"))

    # 2. Columns (native DROP preserves SQLite CHECK constraints).
    for table, col, _sql, _pg in reversed(_NEW_COLUMNS):
        if _is_sqlite():
            cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
            if col in cols:
                bind.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {col}"))
        else:
            bind.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}"))

    # 3. New table.
    if _table_exists("workflow_revisions"):
        op.drop_table("workflow_revisions")
