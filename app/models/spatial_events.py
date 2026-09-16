"""Spatial Event Control Plane ORM models（事件驱动空间操作控制平面）。

表：
- ``spatial_events``：durable append-only 事件账本。自增 ``id`` = 因果序；
  ``event_id`` 唯一 ⇒ 投递去重；``status`` 条件更新 ⇒ worker CAS 抢批与
  崩溃恢复（at-least-once + 副作用幂等 ⇒ 恰好一次可观察效果）。
- ``spatial_event_cursors``：durable 消费游标（重启恢复：不丢/不双跑）。
- ``spatial_watches``：SpatialWatch/TriggerRule 定义 + 求值状态（重启续算）。
- ``spatial_watch_fires``：触发事实账本，(watch_id, event_id) 唯一 ⇒ 触发幂等。

载荷纪律：payload 为有界 JSON（≤2KB canonical）；大内容只存 payload_ref。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Text as SAText

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SpatialEventRow(Base):
    """事件账本行（append-only；status 生命周期见 ledger.py）。"""

    __tablename__ = "spatial_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"),
                primary_key=True, autoincrement=True)
    event_id = Column(String(64), nullable=False)
    org_id = Column(String(255), nullable=False)
    kind = Column(String(64), nullable=False)
    source = Column(String(32), nullable=False, default="internal")
    subject_type = Column(String(32), nullable=False)
    subject_key = Column(String(128), nullable=False)
    session_id = Column(String(64), nullable=True)
    project_id = Column(String(64), nullable=True)
    occurred_at = Column(DateTime, nullable=False)
    ingested_at = Column(DateTime, default=_utcnow, nullable=False)
    priority = Column(String(16), nullable=False, default="normal")
    dedupe_key = Column(String(128), nullable=True)
    correlation_id = Column(String(64), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    payload_ref = Column(String(160), nullable=True)

    #: pending → processing → processed | failed | coalesced | skipped
    status = Column(String(16), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    collapsed_count = Column(Integer, nullable=False, default=0)
    claimed_by = Column(String(128), nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    error_code = Column(String(128), nullable=True)
    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_spatial_event_id"),
        CheckConstraint(
            "status IN ('pending','processing','processed','failed',"
            "'coalesced','skipped','duplicate')",
            name="ck_spatial_event_status",
        ),
        Index("idx_spatial_event_org_id", "org_id", "id"),
        Index("idx_spatial_event_status", "status", "next_attempt_at"),
        Index("idx_spatial_event_subject", "org_id", "subject_key", "id"),
        Index("idx_spatial_event_project", "project_id", "id"),
    )


class SpatialEventCursorRow(Base):
    """durable 消费游标（per consumer name；重启恢复基准）。"""

    __tablename__ = "spatial_event_cursors"

    name = Column(String(64), primary_key=True)
    last_event_id = Column(BigInteger().with_variant(Integer, "sqlite"),
                           nullable=False, default=0)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)


class SpatialWatchRow(Base):
    """SpatialWatch 定义 + 有界求值状态（JSON）。"""

    __tablename__ = "spatial_watches"

    watch_id = Column(String(64), primary_key=True)
    org_id = Column(String(255), nullable=False)
    name = Column(String(128), nullable=False)
    enabled = Column(Integer, nullable=False, default=1)
    kinds = Column(JSON, nullable=False, default=list)
    subject_key_prefix = Column(String(128), nullable=True)
    session_id = Column(String(64), nullable=True)
    project_id = Column(String(64), nullable=True)
    condition = Column(JSON, nullable=False, default=dict)
    actions = Column(JSON, nullable=False, default=list)
    cooldown_s = Column(Float, nullable=False, default=60.0)
    mission_goal_template = Column(Text, nullable=True)
    mission_project_id = Column(String(64), nullable=True)
    state = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        Index("idx_spatial_watch_org", "org_id", "enabled"),
    )


class SpatialWatchFireRow(Base):
    """触发事实（(watch_id, event_id) 唯一 ⇒ 同事件同 watch 至多触发一次）。"""

    __tablename__ = "spatial_watch_fires"

    id = Column(BigInteger().with_variant(Integer, "sqlite"),
                primary_key=True, autoincrement=True)
    watch_id = Column(String(64), nullable=False)
    org_id = Column(String(255), nullable=False)
    event_id = Column(String(64), nullable=False)
    action = Column(String(32), nullable=False)
    outcome = Column(String(16), nullable=False)
    fired_at = Column(DateTime, default=_utcnow, nullable=False)
    #: executing 认领时间（多副本重试防双写；stale 由恢复清扫复位）
    claimed_at = Column(DateTime, nullable=True)
    detail = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("watch_id", "event_id", name="uq_spatial_watch_fire"),
        Index("idx_spatial_watch_fire_org", "org_id", "fired_at"),
        Index("idx_spatial_watch_fire_watch", "watch_id", "fired_at"),
    )


# SAText re-exported to keep flake8 happy if future columns need Text.
_ = SAText
