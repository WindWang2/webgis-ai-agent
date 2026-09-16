"""Spatial Event Control Plane ledger（事件驱动空间操作控制平面）

Revision ID: 0092_spatial_events
Revises: 0091_gis_mission_runtime
Create Date: 2026-09-16

Tables:
  - spatial_events           durable append-only 事件账本（event_id UQ 去重；
                            status 条件更新 = worker CAS；id 自增 = 因果序）
  - spatial_event_cursors    durable 消费游标（重启恢复：不丢/不双跑）
  - spatial_watches          SpatialWatch/TriggerRule 定义 + 求值状态
  - spatial_watch_fires      触发事实账本（(watch_id,event_id) UQ ⇒ 幂等）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0092_spatial_events"
down_revision: Union[str, Sequence[str], None] = "0091_gis_mission_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "spatial_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                  autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_key", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_ref", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("collapsed_count", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','processing','processed','failed',"
            "'coalesced','skipped','duplicate')",
            name="ck_spatial_event_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_spatial_event_id"),
    )
    op.create_index("idx_spatial_event_org_id", "spatial_events", ["org_id", "id"])
    op.create_index("idx_spatial_event_status", "spatial_events",
                    ["status", "next_attempt_at"])
    op.create_index("idx_spatial_event_subject", "spatial_events",
                    ["org_id", "subject_key", "id"])
    op.create_index("idx_spatial_event_project", "spatial_events",
                    ["project_id", "id"])

    op.create_table(
        "spatial_event_cursors",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("last_event_id",
                  sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "spatial_watches",
        sa.Column("watch_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("kinds", sa.JSON(), nullable=False),
        sa.Column("subject_key_prefix", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("condition", sa.JSON(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("cooldown_s", sa.Float(), nullable=False),
        sa.Column("mission_goal_template", sa.Text(), nullable=True),
        sa.Column("mission_project_id", sa.String(length=64), nullable=True),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("watch_id"),
    )
    op.create_index("idx_spatial_watch_org", "spatial_watches",
                    ["org_id", "enabled"])

    op.create_table(
        "spatial_watch_fires",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
                  autoincrement=True, nullable=False),
        sa.Column("watch_id", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=255), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("fired_at", sa.DateTime(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("watch_id", "event_id", name="uq_spatial_watch_fire"),
    )
    op.create_index("idx_spatial_watch_fire_org", "spatial_watch_fires",
                    ["org_id", "fired_at"])
    op.create_index("idx_spatial_watch_fire_watch", "spatial_watch_fires",
                    ["watch_id", "fired_at"])


def downgrade() -> None:
    op.drop_index("idx_spatial_watch_fire_watch", table_name="spatial_watch_fires")
    op.drop_index("idx_spatial_watch_fire_org", table_name="spatial_watch_fires")
    op.drop_table("spatial_watch_fires")
    op.drop_index("idx_spatial_watch_org", table_name="spatial_watches")
    op.drop_table("spatial_watches")
    op.drop_table("spatial_event_cursors")
    op.drop_index("idx_spatial_event_project", table_name="spatial_events")
    op.drop_index("idx_spatial_event_subject", table_name="spatial_events")
    op.drop_index("idx_spatial_event_status", table_name="spatial_events")
    op.drop_index("idx_spatial_event_org_id", table_name="spatial_events")
    op.drop_table("spatial_events")
