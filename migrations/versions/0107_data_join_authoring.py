"""G38: data_join_authoring —— 数据关联与空间富集作者工作台存储面

Revision ID: 0107_data_join_authoring
Revises: 0106_lifecycle_policy_org_unique
Create Date: 2026-11-14

两张 additive 新表（约定照抄 0105_topology_workbench 先例：create_all
coexistence guard 可重入 DDL；downgrade 反序回滚）：

- data_join_plans / data_join_runs

CHECK 词表与 app/models/data_join_authoring.py 一字不差（wiring drift 闸
tests/test_deploy_migration_wiring.py / tests/test_alembic_metadata.py 依赖）。
完整输出内容不入行 —— 走 DATA_DIR/data-join 终态包 + DataObject CAS
（outputs.py 单一可写副本纪律）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0107_data_join_authoring"
down_revision: Union[str, Sequence[str], None] = "0106_lifecycle_policy_org_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "data_join_runs",
    "data_join_plans",
)


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if not _table_exists("data_join_plans"):
        op.create_table(
            "data_join_plans",
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
            sa.Column("left_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("right_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("preview", sa.JSON(), nullable=True),
            sa.Column("preview_digest", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("revision >= 1", name="ck_data_join_plan_revision"),
            sa.CheckConstraint(
                "status IN ('draft','archived')", name="ck_data_join_plan_status"),
        )
        op.create_index(
            "idx_data_join_plans_session", "data_join_plans",
            ["session_id", "created_at"],
        )

    if not _table_exists("data_join_runs"):
        op.create_table(
            "data_join_runs",
            sa.Column("id", sa.String(length=40), primary_key=True),
            sa.Column(
                "plan_id", sa.String(length=36),
                sa.ForeignKey("data_join_plans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("plan_revision", sa.Integer(), nullable=False),
            sa.Column("plan_digest", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=True),
            sa.Column("idempotency_key", sa.String(length=255), nullable=True),
            sa.Column("receipt", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("output_ref", sa.String(length=255), nullable=True),
            sa.Column("output_sha256", sa.String(length=128), nullable=True),
            sa.Column("output_rows", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending','running','completed','failed','cancelled')",
                name="ck_data_join_run_status",
            ),
            sa.CheckConstraint("plan_revision >= 1", name="ck_data_join_run_plan_rev"),
        )
        op.create_index(
            "idx_data_join_runs_idem", "data_join_runs",
            ["idempotency_key", "created_at"],
        )
        op.create_index(
            "idx_data_join_runs_session", "data_join_runs",
            ["session_id", "created_at"],
        )
        op.create_index(
            "idx_data_join_runs_plan", "data_join_runs",
            ["plan_id", "created_at"],
        )


def downgrade() -> None:
    op.drop_table("data_join_runs")
    op.drop_table("data_join_plans")
