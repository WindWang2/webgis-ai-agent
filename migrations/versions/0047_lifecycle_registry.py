"""Data Lifecycle V9: lifecycle_objects + lifecycle_policies + gc_plans

Revision ID: 0047_lifecycle_registry
Revises: 0046_quality_reports
Create Date: 2026-09-11

任务书 P3/P5（ADR-0140 核心）：统一生命周期策略引擎的落库事实面。

- 三张 additive 新表（create_all-coexistence guard 可重入 DDL）；
- kind/action/status CHECK 词表与 app/models/data_lifecycle.py 一字不差；
- org_id 为 B 线占位列（nullable 无 FK）；
- downgrade 反序回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0047_lifecycle_registry"
down_revision: Union[str, Sequence[str], None] = "0046_quality_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OBJECTS = "lifecycle_objects"
_POLICIES = "lifecycle_policies"
_PLANS = "gc_plans"

_KIND_CHECK = (
    "kind IN ('lakehouse_dataset','fabric_materialization',"
    "'artifact_cache','cog_output','worker_cache')"
)

_OBJECT_INDEXES = {
    "idx_lifecycle_object_kind_tier": ["kind", "tier"],
    "idx_lifecycle_object_last_used": ["kind", "last_used_at"],
}

_POLICY_CHECKS = (
    ("ck_lifecycle_policy_kind", _KIND_CHECK),
    ("ck_lifecycle_policy_action",
     "action IN ('observe','stage_delete','delete')"),
)
_POLICY_INDEXES = {
    "idx_lifecycle_policy_kind": ["kind", "enabled"],
}

_PLAN_CHECKS = (
    ("ck_gc_plan_status",
     "status IN ('draft','pending_approval','approved','executing',"
     "'done','failed','rolled_back','rejected','cancelled')"),
)
_PLAN_INDEXES = {
    "idx_gc_plan_status": ["status", "created_at"],
    "idx_gc_plan_digest": ["plan_digest"],
}


def _table_exists(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return index in {
        i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)
    }


def _checks_of(checks):
    return [sa.CheckConstraint(expr, name=name) for name, expr in checks]


def upgrade() -> None:
    if not _table_exists(_OBJECTS):
        op.create_table(
            _OBJECTS,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("object_id", sa.String(length=255), nullable=False),
            sa.Column("owner_scope", sa.String(length=128), nullable=False,
                      server_default=""),
            sa.Column("byte_size",
                      sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      nullable=False, server_default="0"),
            sa.Column("tier", sa.String(length=8), nullable=False,
                      server_default="hot"),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("info", sa.JSON(), nullable=True),
            sa.UniqueConstraint("kind", "object_id",
                                name="uq_lifecycle_object_natural"),
            sa.CheckConstraint(_KIND_CHECK, name="ck_lifecycle_object_kind"),
            sa.CheckConstraint("tier IN ('hot','warm','cold')",
                               name="ck_lifecycle_object_tier"),
        )
    for name, cols in _OBJECT_INDEXES.items():
        if not _index_exists(_OBJECTS, name):
            op.create_index(name, _OBJECTS, cols)

    if not _table_exists(_POLICIES):
        op.create_table(
            _POLICIES,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("action", sa.String(length=16), nullable=False,
                      server_default="observe"),
            sa.Column("tier_thresholds", sa.JSON(), nullable=True),
            sa.Column("staging_hours", sa.Integer(), nullable=False,
                      server_default="72"),
            sa.Column("enabled", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("params", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("name", name="uq_lifecycle_policy_name"),
            *_checks_of(_POLICY_CHECKS),
        )
    for name, cols in _POLICY_INDEXES.items():
        if not _index_exists(_POLICIES, name):
            op.create_index(name, _POLICIES, cols)

    if not _table_exists(_PLANS):
        op.create_table(
            _PLANS,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("approved_by", sa.String(length=255), nullable=True),
            sa.Column("executed_by", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False,
                      server_default="draft"),
            sa.Column("scope", sa.JSON(), nullable=True),
            sa.Column("plan_tree", sa.JSON(), nullable=True),
            sa.Column("candidate_count", sa.Integer(), nullable=False,
                      server_default="0"),
            sa.Column("candidate_bytes",
                      sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                      nullable=False, server_default="0"),
            sa.Column("staging_expires_at", sa.DateTime(), nullable=True),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("job_id", sa.String(length=64), nullable=True),
            sa.Column("plan_digest", sa.String(length=64), nullable=False,
                      server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            *_checks_of(_PLAN_CHECKS),
        )
    for name, cols in _PLAN_INDEXES.items():
        if not _index_exists(_PLANS, name):
            op.create_index(name, _PLANS, cols)


def downgrade() -> None:
    if _table_exists(_PLANS):
        for name in _PLAN_INDEXES:
            if _index_exists(_PLANS, name):
                op.drop_index(name, table_name=_PLANS)
        op.drop_table(_PLANS)
    if _table_exists(_POLICIES):
        for name in _POLICY_INDEXES:
            if _index_exists(_POLICIES, name):
                op.drop_index(name, table_name=_POLICIES)
        op.drop_table(_POLICIES)
    if _table_exists(_OBJECTS):
        for name in _OBJECT_INDEXES:
            if _index_exists(_OBJECTS, name):
                op.drop_index(name, table_name=_OBJECTS)
        op.drop_table(_OBJECTS)
