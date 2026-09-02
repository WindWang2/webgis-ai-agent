"""Reproducible professional GIS runtime: lineage semantics + map_products (ADR-0092)

Revision ID: 0022_reproducible_gis_runtime
Revises: 0021_add_carto_project_facts
Create Date: 2026-09-02

ADR-0092 Phase A：
- ``artifact_lineages`` 增加三个可空语义列（capability / algorithm /
  mapspec_fingerprint）——Dataset → Capability → Algorithm → Tool → Artifact
  链在**既有**血缘表上可表达，不建第二图；
- 新表 ``map_products``：项目地图产品版本账本（product fingerprint / 输入
  指纹 / workflow run / MapSpec 指纹 / artifact 集 / 五维 diff）。

全部列为可空/独立表，向后兼容既有行。
"""
from typing import Sequence, Union

from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0022_reproducible_gis_runtime"
down_revision: Union[str, Sequence[str], None] = "0021_add_carto_project_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """create_all-coexistence guard (repo convention: 0014 IF NOT EXISTS /
    0016 ADD COLUMN IF NOT EXISTS / 0021 _table_exists). init_db() runs
    Base.metadata.create_all on every dev DB, which already creates new
    tables/columns defined on the models — an unguarded upgrade would die on
    'duplicate column/table' there."""
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _column_exists("artifact_lineages", "producing_capability"):
        op.add_column(
            "artifact_lineages",
            sa.Column("producing_capability", sa.String(length=100), nullable=True),
        )
    if not _column_exists("artifact_lineages", "producing_algorithm"):
        op.add_column(
            "artifact_lineages",
            sa.Column("producing_algorithm", sa.String(length=100), nullable=True),
        )
    if not _column_exists("artifact_lineages", "mapspec_fingerprint"):
        op.add_column(
            "artifact_lineages",
            sa.Column("mapspec_fingerprint", sa.String(length=80), nullable=True),
        )

    if sa.inspect(op.get_bind()).has_table("map_products"):
        # create_all-bootstrapped DB already carries the table (0021 guard style).
        return
    op.create_table(
        "map_products",
        sa.Column("id", sa.String(length=255), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=255),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("product_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("input_dataset_fingerprints", sa.JSON(), nullable=True),
        sa.Column("compute_plan", sa.JSON(), nullable=True),
        sa.Column("output_fingerprints", sa.JSON(), nullable=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
        sa.Column("workflow_run_id", sa.String(length=255), nullable=True),
        sa.Column("mapspec_fingerprint", sa.String(length=80), nullable=True),
        sa.Column("mapspec_revision", sa.Integer(), nullable=True),
        sa.Column("recipe_id", sa.String(length=100), nullable=True),
        sa.Column("artifact_ids", sa.JSON(), nullable=True),
        sa.Column("diff_summary", sa.JSON(), nullable=True),
        # Sibling convention (0014/0021): created_at is nullable with a
        # client-side UTC default — no server default (CURRENT_TIMESTAMP in a
        # TIMESTAMP WITHOUT TIME ZONE stores session-local wall time on PG,
        # which diverges from the ORM's UTC values).
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "version_no", name="uq_map_product_version"),
        sa.CheckConstraint("version_no >= 1", name="ck_map_product_version_pos"),
    )
    # Index budget (0020 convention): the UNIQUE (project_id, version_no)
    # backing index already serves project-scoped range scans — no redundant
    # left-prefix duplicates. Only the run lookup gets its own index.
    op.create_index("idx_map_product_run", "map_products", ["workflow_run_id"])


def downgrade() -> None:
    op.drop_index("idx_map_product_run", table_name="map_products")
    op.drop_table("map_products")
    op.drop_column("artifact_lineages", "mapspec_fingerprint")
    op.drop_column("artifact_lineages", "producing_algorithm")
    op.drop_column("artifact_lineages", "producing_capability")
