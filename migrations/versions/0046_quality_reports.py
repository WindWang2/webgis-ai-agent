"""Data Quality V9: quality_reports + quality_rule_results

Revision ID: 0046_quality_reports
Revises: c1e2f3a4b5c6
Create Date: 2026-09-11

任务书 P1（foundation/data-lifecycle-v9）：QualityReport 实体落库。

- 代理 PK（uuid hex）—— 报告是领域对象（project.py 领域表同款）；
- 逐规则行走 BigInteger sqlite 变体自增（analysis_tasks 同款）；
- status/overall/target_kind CHECK 词表与 app/models/data_quality.py 一字不差；
- org_id 为 B 线（security-tenancy-v9）占位列：nullable 无 FK，B 合入后再补约束；
- additive 新表（repo convention：create_all-coexistence guard 可重入 DDL）；
  downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0046_quality_reports"
down_revision: Union[str, Sequence[str], None] = "c1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REPORTS = "quality_reports"
_RESULTS = "quality_rule_results"

_REPORT_CHECKS = (
    ("ck_quality_report_status",
     "status IN ('pending','running','completed','failed')"),
    ("ck_quality_report_overall",
     "overall_status IN ('pass','warn','fail')"),
    ("ck_quality_report_target_kind",
     "target_kind IN ('vector','raster','table')"),
)
_REPORT_INDEXES = {
    "idx_quality_report_project_created": ["project_id", "created_at"],
    "idx_quality_report_session_created": ["session_id", "created_at"],
    "idx_quality_report_identity": ["dataset_identity", "ruleset_digest"],
}

_RESULT_CHECKS = (
    ("ck_quality_rule_result_status",
     "status IN ('pass','warn','fail','skipped','error')"),
    ("ck_quality_rule_result_severity",
     "severity IN ('info','warn','error')"),
)
_RESULT_INDEXES = {
    "idx_quality_rule_result_report": ["report_id"],
    "idx_quality_rule_result_rule": ["rule_id", "status"],
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


def upgrade() -> None:
    if not _table_exists(_REPORTS):
        op.create_table(
            _REPORTS,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("target_ref", sa.String(length=255), nullable=False,
                      server_default=""),
            sa.Column("target_kind", sa.String(length=16), nullable=False,
                      server_default="vector"),
            sa.Column("dataset_identity", sa.String(length=128), nullable=False,
                      server_default=""),
            sa.Column("ruleset_digest", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("status", sa.String(length=20), nullable=False,
                      server_default="pending"),
            sa.Column("overall_status", sa.String(length=16), nullable=False,
                      server_default="pass"),
            sa.Column("rule_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("warn_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("summary", sa.JSON(), nullable=True),
            sa.Column("diagnostics", sa.JSON(), nullable=True),
            sa.Column("job_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            *_CHECKS_OF(_REPORT_CHECKS),
        )
    for name, cols in _REPORT_INDEXES.items():
        if not _index_exists(_REPORTS, name):
            op.create_index(name, _REPORTS, cols)

    if not _table_exists(_RESULTS):
        op.create_table(
            _RESULTS,
            sa.Column(
                "id",
                sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                primary_key=True,
                autoincrement=True,
            ),
            sa.Column("report_id", sa.String(length=36),
                      sa.ForeignKey("quality_reports.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("rule_id", sa.String(length=64), nullable=False),
            sa.Column("rule_type", sa.String(length=48), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False,
                      server_default="warn"),
            sa.Column("status", sa.String(length=16), nullable=False,
                      server_default="pass"),
            sa.Column("message", sa.String(length=500), nullable=False,
                      server_default=""),
            sa.Column("affected_count", sa.Integer(), nullable=False,
                      server_default="0"),
            sa.Column("duration_ms", sa.Integer(), nullable=False,
                      server_default="0"),
            sa.Column("metric", sa.JSON(), nullable=True),
            sa.Column("autofixable", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("fix_operations", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            *_CHECKS_OF(_RESULT_CHECKS),
        )
    for name, cols in _RESULT_INDEXES.items():
        if not _index_exists(_RESULTS, name):
            op.create_index(name, _RESULTS, cols)


def _CHECKS_OF(checks):
    return [sa.CheckConstraint(expr, name=name) for name, expr in checks]


def downgrade() -> None:
    if _table_exists(_RESULTS):
        for name in _RESULT_INDEXES:
            if _index_exists(_RESULTS, name):
                op.drop_index(name, table_name=_RESULTS)
        op.drop_table(_RESULTS)
    if _table_exists(_REPORTS):
        for name in _REPORT_INDEXES:
            if _index_exists(_REPORTS, name):
                op.drop_index(name, table_name=_REPORTS)
        op.drop_table(_REPORTS)
