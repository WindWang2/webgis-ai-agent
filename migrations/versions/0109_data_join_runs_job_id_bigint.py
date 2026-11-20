"""#2208: data_join_runs.job_id → BigInteger（64 位 durable job id）。

data_join_runs.job_id 引用 analysis_tasks.id（BigInteger 主键，ADR-0052
统一 durable job 表）。32 位 Integer 在 PostgreSQL 上超 2^31-1 溢出 —
— 与 0101_cluster_reservation_bigint 同一修复模式（PG 升 BIGINT，
sqlite 变体保持 Integer）。

Revision ID: 0109_data_join_runs_job_id_bigint
Revises: 0108_layers_org_fk_ondelete
"""
from alembic import op
import sqlalchemy as sa

revision = "0109_data_join_runs_job_id_bigint"
down_revision = "0108_layers_org_fk_ondelete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("data_join_runs") as batch:
        batch.alter_column(
            "job_id",
            existing_type=sa.Integer(),
            type_=sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            existing_nullable=True,
        )


def downgrade() -> None:
    # PostgreSQL 拒绝缩窄已超 INT4 的 job id；保留运行证据而非静默截断。
    with op.batch_alter_table("data_join_runs") as batch:
        batch.alter_column(
            "job_id",
            existing_type=sa.BigInteger(),
            type_=sa.Integer().with_variant(sa.Integer(), "sqlite"),
            existing_nullable=True,
        )
