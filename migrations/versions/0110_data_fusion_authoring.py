"""融合工作台: data_fusion_authoring —— 多源空间实体融合存储面

Revision ID: 0110_data_fusion_authoring
Revises: 0109_data_join_runs_job_id_bigint
Create Date: 2026-11-25

三张 additive 新表（约定照抄 0107_data_join_authoring 先例：create_all
coexistence guard 可重入 DDL；downgrade 反序回滚）：

- data_fusion_plans / data_fusion_runs / data_fusion_decisions

CHECK 词表与 app/models/data_fusion_authoring.py 一字不差（wiring
drift 闸 tests/test_deploy_migration_wiring.py / tests/test_alembic_metadata.py
依赖）。完整输出内容不入行 —— 走 DATA_DIR/data-fusion 终态包 +
DataObject CAS（outputs.py 单一可写副本纪律）。决定挂 plan 级
（pair 稳定键唯一约束）：增量刷新新 run 自动继承锁定决定。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0110_data_fusion_authoring"
down_revision: Union[str, Sequence[str], None] = "0109_data_join_runs_job_id_bigint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if not _table_exists("data_fusion_plans"):
        op.create_table(
            "data_fusion_plans",
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
            sa.Column("fingerprint_a", sa.String(length=128), nullable=True),
            sa.Column("fingerprint_b", sa.String(length=128), nullable=True),
            sa.Column("preview", sa.JSON(), nullable=True),
            sa.Column("preview_digest", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("revision >= 1",
                               name="ck_data_fusion_plan_revision"),
            sa.CheckConstraint(
                "status IN ('draft','archived')",
                name="ck_data_fusion_plan_status"),
        )
        op.create_index(
            "idx_data_fusion_plans_session", "data_fusion_plans",
            ["session_id", "created_at"],
        )

    if not _table_exists("data_fusion_runs"):
        op.create_table(
            "data_fusion_runs",
            sa.Column("id", sa.String(length=40), primary_key=True),
            sa.Column(
                "plan_id", sa.String(length=36),
                sa.ForeignKey("data_fusion_plans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("plan_revision", sa.Integer(), nullable=False),
            sa.Column("plan_digest", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("job_id", sa.BigInteger(), nullable=True),
            sa.Column("idempotency_key", sa.String(length=255), nullable=True),
            sa.Column("base_run_id", sa.String(length=40), nullable=True),
            sa.Column("receipt", sa.JSON(), nullable=True),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("output_ref", sa.String(length=255), nullable=True),
            sa.Column("output_sha256", sa.String(length=128), nullable=True),
            sa.Column("output_features", sa.Integer(), nullable=True),
            sa.Column("commit_receipt", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending','running','completed','failed','cancelled')",
                name="ck_data_fusion_run_status",
            ),
            sa.CheckConstraint("plan_revision >= 1",
                               name="ck_data_fusion_run_plan_rev"),
        )
        op.create_index(
            "idx_data_fusion_runs_idem", "data_fusion_runs",
            ["idempotency_key", "created_at"],
        )
        op.create_index(
            "idx_data_fusion_runs_session", "data_fusion_runs",
            ["session_id", "created_at"],
        )
        op.create_index(
            "idx_data_fusion_runs_plan", "data_fusion_runs",
            ["plan_id", "created_at"],
        )

    if not _table_exists("data_fusion_decisions"):
        op.create_table(
            "data_fusion_decisions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "plan_id", sa.String(length=36),
                sa.ForeignKey("data_fusion_plans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("session_id", sa.String(length=255), nullable=False),
            sa.Column("pair_key", sa.String(length=160), nullable=False),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("field_values", sa.JSON(), nullable=True),
            sa.Column("geometry_side", sa.String(length=4), nullable=True),
            sa.Column("decision_run_id", sa.String(length=40), nullable=True),
            sa.Column("idx_a", sa.Integer(), nullable=True),
            sa.Column("idx_b", sa.Integer(), nullable=True),
            sa.Column("reviewer", sa.String(length=255), nullable=True),
            sa.Column("note", sa.String(length=500), nullable=True),
            sa.Column("locked", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("action IN ('accept','reject')",
                               name="ck_data_fusion_decision_action"),
            sa.CheckConstraint("revision >= 1",
                               name="ck_data_fusion_decision_revision"),
            sa.CheckConstraint(
                "geometry_side IS NULL OR geometry_side IN ('a','b')",
                name="ck_data_fusion_decision_gside"),
            sa.UniqueConstraint("plan_id", "pair_key",
                                name="uq_data_fusion_decision_pair"),
        )
        op.create_index(
            "idx_data_fusion_decisions_plan", "data_fusion_decisions",
            ["plan_id", "created_at"],
        )


def downgrade() -> None:
    op.drop_table("data_fusion_decisions")
    op.drop_table("data_fusion_runs")
    op.drop_table("data_fusion_plans")
