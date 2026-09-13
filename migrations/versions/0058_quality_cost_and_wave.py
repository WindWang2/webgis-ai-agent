"""Cartography Quality Cost & Wave: C4 扩展字段（cartography_quality_runs
增 wave / contract_version / map_type / cost_tokens / cost_ms；metrics 增 wave）

Revision ID: 0058_quality_cost_and_wave
Revises: 0057_cartography_intent_learning
Create Date: 2026-09-13

任务书 adaptive-cartography/v11 W8（ADR-0168）：「C4 扩展字段（``wave`` /
``contract_version`` / ``cost_tokens`` / ``cost_ms``）后，ratchet 按
「图型 × 检查项 × 波次」聚合；首轮为 ``provisional``」。

- 全部为**只增列**（nullable，既有字段零改动）：
  - runs: ``wave``（W0–W9）、``contract_version``（契约版本号）、
    ``map_type``（17 图型 id —— 原 scene_id 承载场景名）、``cost_tokens`` /
    ``cost_ms``（成本治理观测）；
  - metrics: ``wave``（聚合维度直接可查，免 join）；
- downgrade 反序回滚（drop 增列）。
- 领号 0058（migrations/.alloc.json，adaptive-cartography 段）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0058_quality_cost_and_wave"
down_revision: Union[str, Sequence[str], None] = "0057_cartography_intent_learning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUNS = "cartography_quality_runs"
_METRICS = "cartography_quality_metrics"

_RUN_COLUMNS = (
    ("wave", sa.String(length=8)),
    ("contract_version", sa.String(length=16)),
    ("map_type", sa.String(length=64)),
    ("cost_tokens", sa.Integer()),
    ("cost_ms", sa.Integer()),
)


def _columns_of(name: str) -> set:
    bind = op.get_bind()
    try:
        rows = bind.execute(sa.text(f"PRAGMA table_info({name})")).fetchall()
        if rows:
            return {r[1] for r in rows}
    except Exception:  # noqa: BLE001 —— 非 sqlite 走 information_schema
        pass
    try:
        rows = bind.execute(
            sa.text("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :n"),
            {"n": name},
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:  # noqa: BLE001
        return set()


def upgrade() -> None:
    run_cols = _columns_of(_RUNS)
    if run_cols:
        for name, type_ in _RUN_COLUMNS:
            if name not in run_cols:
                op.add_column(_RUNS, sa.Column(name, type_, nullable=True))
    metric_cols = _columns_of(_METRICS)
    if metric_cols and "wave" not in metric_cols:
        op.add_column(_METRICS, sa.Column("wave", sa.String(length=8), nullable=True))


def downgrade() -> None:
    metric_cols = _columns_of(_METRICS)
    if metric_cols and "wave" in metric_cols:
        op.drop_column(_METRICS, "wave")
    run_cols = _columns_of(_RUNS)
    for name, _type in reversed(_RUN_COLUMNS):
        if run_cols and name in run_cols:
            op.drop_column(_RUNS, name)
