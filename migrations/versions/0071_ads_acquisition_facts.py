"""ads-v1 Acquisition Facts: D4 fact store + cost budgets + ratchet (ADR-0178).

Revision ID: 0071_ads_acquisition_facts
Revises: 0070_ads_acquisition_snapshots
Create Date: 2026-09-13

任务书 adaptive-data-supply/v1 DS8：取数埋点全字段落库（D4 AcquisitionFact）、
分源/分请求型成本预算（超限告警）、ratchet 按「源 × 指标 × 波次」聚合。

- ``ads_acquisition_facts`` —— 每次取数一行（成功/降级/失败都记）；
- ``ads_cost_budgets``     —— (source_id × request_type) 预算行（超限=告警）；
- 表名 ``ads_*``（本线命名空间，与 V11 ``carto_*`` 表隔离，§8.1.7——
  仅展示层共享，不共享表）；
- additive 新表；downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0071_ads_acquisition_facts"
down_revision: Union[str, Sequence[str], None] = "0070_ads_acquisition_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FACTS = "ads_acquisition_facts"
_BUDGETS = "ads_cost_budgets"


def upgrade() -> None:
    op.create_table(
        _FACTS,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False, index=True),
        sa.Column("dataset_key", sa.String(256), nullable=False, index=True),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("fallback_json", sa.Text(), nullable=True),
        sa.Column("drift", sa.String(32), nullable=True),
        sa.Column("wave", sa.String(16), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('success','degraded','failed')", name="ck_ads_fact_outcome"
        ),
    )
    op.create_table(
        _BUDGETS,
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("source_id", sa.String(128), nullable=False, index=True),
        sa.Column("request_type", sa.String(64), nullable=False),
        sa.Column("max_rows", sa.Integer(), nullable=True),
        sa.Column("max_bytes", sa.BigInteger(), nullable=True),
        sa.Column("max_ms", sa.Float(), nullable=True),
        sa.Column("max_quota", sa.Float(), nullable=True),
        sa.Column("wave", sa.String(16), nullable=False),
        sa.UniqueConstraint("source_id", "request_type", "wave", name="uq_ads_budget_scope"),
    )


def downgrade() -> None:
    op.drop_table(_BUDGETS)
    op.drop_table(_FACTS)
