"""G45: coordinate_operations（可审阅、可固定坐标操作的派生矢量重投影）

Revision ID: g45_coordinate_operations
Revises: g40_cartographic_symbol_assets (当前唯一 head)
Create Date: 2026-11-16

坐标操作计划 + 执行 run 两张 additive 新表（coexistence guard 可重入 DDL；
CHECK 词表与 app/models/coordinate_operations.py 一字不差 —— drift 闸依赖）。
既有表零改动；执行活性/取消/fencing 继续走既有 analysis_tasks。
约定照抄 g40_cartographic_symbol_assets / 0107_data_join_authoring。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g45_coordinate_operations"
down_revision: Union[str, Sequence[str], None] = "g40_cartographic_symbol_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    {
        "name": "coordinate_operation_plans",
        "columns": [
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("plan", sa.JSON(), nullable=False),
            sa.Column("normalized_digest", sa.String(length=64), nullable=False),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("selected_candidate_id", sa.String(length=64), nullable=True),
            sa.Column("operation", sa.Text(), nullable=True),
            sa.Column("preview", sa.JSON(), nullable=True),
            sa.Column("preview_digest", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        ],
        "checks": (
            ("ck_coord_op_plan_revision", "revision >= 1"),
            ("ck_coord_op_plan_status",
             "status IN ('draft','locked','archived')"),
        ),
        "indexes": (
            ("idx_coord_op_plans_session", ("session_id", "created_at")),
        ),
    },
    {
        "name": "coordinate_operation_runs",
        "columns": [
            sa.Column("id", sa.String(length=40), primary_key=True),
            sa.Column(
                "plan_id", sa.String(length=36),
                sa.ForeignKey(
                    "coordinate_operation_plans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("plan_revision", sa.Integer(), nullable=False),
            sa.Column("plan_digest", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            #: durable job 关联：analysis_tasks.id 是 BigInteger 主键 —— 32 位
            #: Integer 在 PG 上超 2^31-1 溢出；sqlite 变体保持 Integer（与 0109
            #: data_join_runs.job_id 同一修复模式）。
            sa.Column(
                "job_id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                nullable=True,
            ),
            sa.Column("idempotency_key", sa.String(length=255), nullable=True),
            sa.Column("receipt", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("output_ref", sa.String(length=255), nullable=True),
            sa.Column("output_sha256", sa.String(length=128), nullable=True),
            sa.Column("output_rows", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        ],
        "checks": (
            ("ck_coord_op_run_status",
             "status IN ('pending','running','completed','failed','cancelled')"),
            ("ck_coord_op_run_plan_rev", "plan_revision >= 1"),
        ),
        "indexes": (
            ("idx_coord_op_runs_idem", ("idempotency_key", "created_at")),
            ("idx_coord_op_runs_session", ("session_id", "created_at")),
            ("idx_coord_op_runs_plan", ("plan_id", "created_at")),
        ),
    },
)


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return insp.has_table(name)


def upgrade() -> None:
    for spec in _TABLES:
        if _table_exists(spec["name"]):
            continue  # coexistence guard（create_all 环境可重入）
        op.create_table(
            spec["name"],
            *spec["columns"],
            *[sa.CheckConstraint(c, name=n) for n, c in spec["checks"]],
        )
        for name, cols in spec["indexes"]:
            op.create_index(name, spec["name"], list(cols))


def downgrade() -> None:
    for spec in reversed(_TABLES):
        if not _table_exists(spec["name"]):
            continue
        for name, _cols in spec["indexes"]:
            op.drop_index(name, table_name=spec["name"])
        op.drop_table(spec["name"])
