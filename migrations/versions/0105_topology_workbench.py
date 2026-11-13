"""G31: topology_workbench —— 跨图层拓扑质量工作台存储面

Revision ID: 0105_topology_workbench
Revises: 0104_map_briefs
Create Date: 2026-11-12

四张 additive 新表（约定照抄 0099/0104 先例：create_all coexistence
guard 可重入 DDL；downgrade 反序回滚）：

- topology_rulesets / topology_runs / topology_findings / topology_exceptions

CHECK 词表与 app/models/topology_workbench.py 一字不差（wiring drift 闸
tests/test_deploy_migration_wiring.py 依赖）。完整几何证据不入行 ——
witness 集合走 BlobStore 内容寻址（witness_key + witness_sha256 指针）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0105_topology_workbench"
down_revision: Union[str, Sequence[str], None] = "0104_map_briefs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "topology_rulesets",
    "topology_runs",
    "topology_findings",
    "topology_exceptions",
)


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if not _table_exists("topology_rulesets"):
        op.create_table(
            "topology_rulesets",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("current_version", sa.Integer(), nullable=False),
            sa.Column("rules", sa.JSON(), nullable=False),
            sa.Column("digest", sa.String(length=64), nullable=False),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint("current_version >= 1", name="ck_topology_ruleset_version"),
            sa.CheckConstraint("length(name) > 0", name="ck_topology_ruleset_name"),
        )
        op.create_index(
            "idx_topology_ruleset_project", "topology_rulesets",
            ["project_id", "created_at"],
        )
        op.create_index(
            "idx_topology_ruleset_session", "topology_rulesets",
            ["session_id", "created_at"],
        )

    if not _table_exists("topology_runs"):
        op.create_table(
            "topology_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column(
                "ruleset_id", sa.String(length=36),
                sa.ForeignKey("topology_rulesets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("ruleset_digest", sa.String(length=64), nullable=False),
            sa.Column("ruleset_version", sa.Integer(), nullable=False),
            sa.Column("rules_snapshot", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("bindings", sa.JSON(), nullable=True),
            sa.Column("input_digest", sa.String(length=64), nullable=False),
            sa.Column("computation_crs", sa.String(length=64), nullable=True),
            sa.Column("method_version", sa.String(length=32), nullable=False),
            sa.Column("rule_results", sa.JSON(), nullable=True),
            sa.Column("completeness", sa.JSON(), nullable=True),
            sa.Column("findings_total", sa.Integer(), nullable=False),
            sa.Column("findings_open", sa.Integer(), nullable=False),
            sa.Column("witness_key", sa.String(length=64), nullable=True),
            sa.Column("witness_sha256", sa.String(length=64), nullable=True),
            sa.Column("witness_feature_count", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.String(length=64), nullable=True),
            sa.Column("recheck_of_run_id", sa.String(length=36), nullable=True),
            sa.Column("project_id", sa.String(length=255), nullable=True),
            sa.Column("session_id", sa.String(length=255), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending','running','completed','failed','cancelled')",
                name="ck_topology_run_status",
            ),
        )
        op.create_index(
            "idx_topology_run_ruleset", "topology_runs",
            ["ruleset_id", "created_at"],
        )
        op.create_index(
            "idx_topology_run_project", "topology_runs",
            ["project_id", "created_at"],
        )
        op.create_index(
            "idx_topology_run_session", "topology_runs",
            ["session_id", "created_at"],
        )
        op.create_index(
            "idx_topology_run_input", "topology_runs",
            ["input_digest", "ruleset_digest"],
        )

    if not _table_exists("topology_findings"):
        op.create_table(
            "topology_findings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "run_id", sa.String(length=36),
                sa.ForeignKey("topology_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("rule_id", sa.String(length=64), nullable=False),
            sa.Column("rule_type", sa.String(length=48), nullable=False),
            sa.Column("finding_key", sa.String(length=64), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("metric", sa.JSON(), nullable=True),
            sa.Column("locators", sa.JSON(), nullable=True),
            sa.Column("witness_index", sa.Integer(), nullable=False),
            sa.Column("witness_digest", sa.String(length=64), nullable=False),
            sa.Column("bbox_w", sa.Float(), nullable=False),
            sa.Column("bbox_s", sa.Float(), nullable=False),
            sa.Column("bbox_e", sa.Float(), nullable=False),
            sa.Column("bbox_n", sa.Float(), nullable=False),
            sa.Column("geometry_type", sa.String(length=24), nullable=False),
            sa.Column("message", sa.String(length=500), nullable=False),
            sa.Column("resolved_by_run_id", sa.String(length=36), nullable=True),
            sa.Column("exception_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "severity IN ('info','warn','error')",
                name="ck_topology_finding_severity",
            ),
            sa.CheckConstraint(
                "status IN ('open','accepted_exception','resolved','dismissed')",
                name="ck_topology_finding_status",
            ),
            sa.UniqueConstraint("run_id", "finding_key", name="uq_topology_finding_run_key"),
        )
        op.create_index(
            "idx_topology_finding_run", "topology_findings",
            ["run_id", "severity", "status"],
        )
        op.create_index(
            "idx_topology_finding_key", "topology_findings", ["finding_key"],
        )

    if not _table_exists("topology_exceptions"):
        op.create_table(
            "topology_exceptions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column(
                "ruleset_id", sa.String(length=36),
                sa.ForeignKey("topology_rulesets.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("rule_id", sa.String(length=64), nullable=False),
            sa.Column("finding_key", sa.String(length=64), nullable=False),
            sa.Column("anchor", sa.JSON(), nullable=True),
            sa.Column("reason", sa.String(length=2000), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.CheckConstraint(
                "status IN ('active','revoked','stale')",
                name="ck_topology_exception_status",
            ),
            sa.CheckConstraint("length(reason) > 0", name="ck_topology_exception_reason"),
        )
        op.create_index(
            "idx_topology_exception_ruleset", "topology_exceptions",
            ["ruleset_id", "status"],
        )
        op.create_index(
            "idx_topology_exception_key", "topology_exceptions",
            ["finding_key", "status"],
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        if _table_exists(table):
            op.drop_table(table)
