"""GIS Spatial Reasoning Memory: gis_spatial_memories（方向 9，ADR-0183）

Revision ID: 0080_gis_spatial_memories
Revises: 0071_ads_acquisition_facts
Create Date: 2026-09-14

任务书方向 9：跨会话/会话/用户三作用域的 GIS 空间记忆（resolved_place /
dataset_semantics / field_role / crs_resolution / analysis_artifact /
successful_strategy / provider_failure / product_decision …）。

- additive 新表（create_all-coexistence guard 可重入 DDL，与 0057 同款）；
- downgrade 直接 drop table；
- 领号 0080（本分支自有段 0080-0089，migrations/.alloc.json 登记；
  越段用号禁令见 docs/dev/migration-protocol.md §2——0070-0079 已被
  adaptive-data-supply 预留；
  down_revision = 0071_ads_acquisition_facts）；
- tenancy：org_id 恒非空（调用方烙印），scoped_query 参与租户过滤（ADR-0139）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0080_gis_spatial_memories"
down_revision: Union[str, Sequence[str], None] = "0071_ads_acquisition_facts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "gis_spatial_memories"


def _table_exists(name: str) -> bool:
    """按方言探测表存在性：sqlite 走 sqlite_master，其余走 information_schema。

    （迁移门禁修复：原实现无条件先查 sqlite_master —— PG 上直接
    UndefinedTable，alembic upgrade head 中断。）
    """
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        rows = bind.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND :n = name"),
            {"n": name},
        ).fetchall()
        return bool(rows)
    try:
        rows = bind.execute(
            sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :n"),
            {"n": name},
        ).fetchall()
    except Exception:  # noqa: BLE001 —— 非 information_schema 方言（保守跳过）
        return False
    return bool(rows)


def upgrade() -> None:
    if _table_exists(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("org_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("user_id", sa.String(length=255), nullable=True, index=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("refs", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=64), nullable=True),
        sa.Column("sensitive", sa.Boolean(), nullable=False),
        sa.Column("invalidation_rule", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "scope IN ('session','project','user')", name="ck_gis_mem_scope"
        ),
        sa.CheckConstraint(
            "status IN ('active','superseded','invalidated')",
            name="ck_gis_mem_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_gis_mem_confidence"
        ),
        sa.CheckConstraint(
            "invalidation_rule IN ('ttl','dataset_version','manual','scope_gone')",
            name="ck_gis_mem_invalidation",
        ),
    )
    op.create_index(
        "idx_gis_mem_key",
        _TABLE,
        ["org_id", "scope", "scope_id", "kind", "subject"],
    )
    op.create_index("idx_gis_mem_scope_id", _TABLE, ["scope", "scope_id"])
    op.create_index("idx_gis_mem_expires", _TABLE, ["expires_at"])
    # 并发双写兜底（review F6）：同 key 至多一条 active 行（partial unique，
    # 双后端谓词）；supersede 竞态败方吃 IntegrityError，由 store 重试消解。
    op.create_index(
        "uq_gis_mem_active_key",
        _TABLE,
        ["org_id", "scope", "scope_id", "kind", "subject"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    if not _table_exists(_TABLE):
        return
    op.drop_index("uq_gis_mem_active_key", table_name=_TABLE)
    op.drop_index("idx_gis_mem_expires", table_name=_TABLE)
    op.drop_index("idx_gis_mem_scope_id", table_name=_TABLE)
    op.drop_index("idx_gis_mem_key", table_name=_TABLE)
    op.drop_table(_TABLE)
