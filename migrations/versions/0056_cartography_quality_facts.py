"""Cartography Quality Facts: cartography_quality_runs + cartography_quality_metrics

Revision ID: 0056_cartography_quality_facts
Revises: 184068cb4249
Create Date: 2026-09-13

任务书 adaptive-cartography/10 P1（ADR-0159）：制图质量事实库。

- ``cartography_quality_runs``     —— 一次制图质量评审 run（lane 阶段 × 场景）；
- ``cartography_quality_metrics``  —— run 内逐检查项观测行（check_id × value ×
  evidence_class × verdict）；

- 代理 PK（uuid hex）—— run 是领域对象（quality_reports 同款）；
- 逐检查行走 BigInteger sqlite 变体自增（quality_rule_results 同款）；
- lane / verdict CHECK 词表与 app/models/cartography_quality.py 一字不差；
- additive 新表（repo convention：create_all-coexistence guard 可重入 DDL）；
  downgrade 反序回滚，不触碰既有表（本线是 10 线中唯一允许新建迁移的线，
  0036–0055 为 v9 保留段，本线从 0056 起编号 —— 领号见 migrations/.alloc.json）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0056_cartography_quality_facts"
down_revision: Union[str, Sequence[str], None] = "184068cb4249"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RUNS = "cartography_quality_runs"
_METRICS = "cartography_quality_metrics"
_BASELINES = "cartography_quality_baselines"
_WAIVERS = "cartography_quality_waivers"

_RUN_CHECKS = (
    ("ck_carto_quality_run_lane",
     "lane IN ('desired_state','runtime','eval','adaptive','golden')"),
)
_RUN_INDEXES = {
    "idx_carto_quality_run_ts": ["ts"],
    "idx_carto_quality_run_scene_ts": ["scene_id", "ts"],
    "idx_carto_quality_run_lane_ts": ["lane", "ts"],
}
_METRIC_CHECKS = (
    ("ck_carto_quality_metric_verdict",
     "verdict IN ('pass','fail','warning','not_evaluated')"),
)
_METRIC_INDEXES = {
    "idx_carto_quality_metric_run": ["run_id"],
    "idx_carto_quality_metric_check": ["check_id"],
}
_BASELINE_CHECKS = (
    ("ck_carto_quality_baseline_status",
     "status IN ('provisional','active')"),
    ("ck_carto_quality_baseline_direction",
     "direction IN ('high_bad','low_bad')"),
)
_WAIVER_INDEXES = {
    "idx_carto_quality_waiver_scope": ["scene_id", "check_id"],
    "idx_carto_quality_waiver_expires": ["expires_at"],
}


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def _constraint_exists(table: str, name: str) -> bool:
    return name in {
        ck["name"]
        for ck in sa.inspect(op.get_bind()).get_check_constraints(table)
    }


def _checks_of(defs) -> tuple:
    """CHECK 内联进 CREATE TABLE（SQLite 不支持 ALTER ADD CONSTRAINT）。"""
    return tuple(sa.CheckConstraint(cond, name=name) for name, cond in defs)


def upgrade() -> None:
    if not _table_exists(_RUNS):
        op.create_table(
            _RUNS,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("ts", sa.DateTime(), nullable=False),
            sa.Column("app_version", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("scene_id", sa.String(length=128), nullable=True),
            sa.Column("lane", sa.String(length=20), nullable=False,
                      server_default="runtime"),
            sa.Column("source", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("passed", sa.Boolean(), nullable=True),
            sa.Column("summary", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            *_checks_of(_RUN_CHECKS),
        )
    for name, cols in _RUN_INDEXES.items():
        if not _index_exists(_RUNS, name):
            op.create_index(name, _RUNS, cols)

    if not _table_exists(_METRICS):
        op.create_table(
            _METRICS,
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                      primary_key=True, autoincrement=True),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("check_id", sa.String(length=128), nullable=False),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("evidence_class", sa.String(length=16), nullable=False,
                      server_default="deterministic"),
            sa.Column("verdict", sa.String(length=16), nullable=False,
                      server_default="not_evaluated"),
            sa.ForeignKeyConstraint(["run_id"], [f"{_RUNS}.id"],
                                    ondelete="CASCADE"),
            *_checks_of(_METRIC_CHECKS),
        )
    for name, cols in _METRIC_INDEXES.items():
        if not _index_exists(_METRICS, name):
            op.create_index(name, _METRICS, cols)

    if not _table_exists(_BASELINES):
        op.create_table(
            _BASELINES,
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                      primary_key=True, autoincrement=True),
            sa.Column("scene_id", sa.String(length=128), nullable=False,
                      server_default="*"),
            sa.Column("check_id", sa.String(length=128), nullable=False),
            sa.Column("value", sa.Float(), nullable=False),
            sa.Column("quantile", sa.Float(), nullable=False),
            sa.Column("direction", sa.String(length=8), nullable=False,
                      server_default="high_bad"),
            sa.Column("tolerance_pct", sa.Float(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False,
                      server_default="provisional"),
            sa.Column("source", sa.String(length=24), nullable=False,
                      server_default="first_run"),
            sa.Column("sample_n", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            *_checks_of(_BASELINE_CHECKS),
        )
    if not _index_exists(_BASELINES, "uq_carto_quality_baseline_scope"):
        op.create_index(
            "uq_carto_quality_baseline_scope", _BASELINES,
            ["scene_id", "check_id"], unique=True,
        )

    if not _table_exists(_WAIVERS):
        op.create_table(
            _WAIVERS,
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                      primary_key=True, autoincrement=True),
            sa.Column("scene_id", sa.String(length=128), nullable=False,
                      server_default="*"),
            sa.Column("check_id", sa.String(length=128), nullable=False),
            sa.Column("reason", sa.String(length=500), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=False,
                      server_default=""),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
    for name, cols in _WAIVER_INDEXES.items():
        if not _index_exists(_WAIVERS, name):
            op.create_index(name, _WAIVERS, cols)


def downgrade() -> None:
    # 与 0046 同款：SQLite 不支持 DROP CONSTRAINT，CHECK 随表删除即消失，
    # 只需反序 drop 索引与表（additive 表的 downgrade 不触碰既有数据）。
    if _table_exists(_WAIVERS):
        for name in _WAIVER_INDEXES:
            if _index_exists(_WAIVERS, name):
                op.drop_index(name, table_name=_WAIVERS)
        op.drop_table(_WAIVERS)
    if _table_exists(_BASELINES):
        if _index_exists(_BASELINES, "uq_carto_quality_baseline_scope"):
            op.drop_index(
                "uq_carto_quality_baseline_scope", table_name=_BASELINES
            )
        op.drop_table(_BASELINES)
    if _table_exists(_METRICS):
        for name in _METRIC_INDEXES:
            if _index_exists(_METRICS, name):
                op.drop_index(name, table_name=_METRICS)
        op.drop_table(_METRICS)
    if _table_exists(_RUNS):
        for name in _RUN_INDEXES:
            if _index_exists(_RUNS, name):
                op.drop_index(name, table_name=_RUNS)
        op.drop_table(_RUNS)
