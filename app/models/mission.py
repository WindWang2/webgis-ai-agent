"""GIS Mission Runtime ORM models (ADR-0197 / Direction 01).

``gis_missions`` is the durable ownership/lifecycle envelope above SessionPlan,
Workflow Runtime, and Specialist Swarm. Checkpoints and swarm-run ledgers are
separate bounded tables. Payloads are refs only (Zero Big Data in Agent Context).
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

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GISMissionRow(Base):
    """Durable Mission row of record (lease_epoch fencing + revision CAS)."""

    __tablename__ = "gis_missions"

    mission_id = Column(String(64), primary_key=True)
    org_id = Column(String(255), nullable=False)
    user_id = Column(String(255), nullable=False, default="")
    project_id = Column(String(255), nullable=True)
    owner_scope = Column(String(64), nullable=False, default="")
    root_goal = Column(Text, nullable=False, default="")
    goal_revision = Column(Integer, nullable=False, default=1)
    state = Column(String(32), nullable=False, default="created")
    revision = Column(Integer, nullable=False, default=1)
    schema_version = Column(String(16), nullable=False, default="mission.v1")

    #: Bounded ref inventories (JSON lists of opaque tickets / ref:… strings).
    refs = Column(JSON, nullable=False, default=dict)
    frontier = Column(JSON, nullable=False, default=dict)
    resource_budget = Column(JSON, nullable=False, default=dict)
    failure_state = Column(JSON, nullable=False, default=dict)
    recovery_state = Column(JSON, nullable=False, default=dict)

    #: Distributed ownership (GeoCompute-style epoch fencing).
    lease_owner = Column(String(128), nullable=True)
    lease_epoch = Column(Integer, nullable=False, default=0)
    lease_expires_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)
    terminal_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "state IN ('created','planning','running','waiting_dependency',"
            "'partially_complete','suspended','recovering','complete',"
            "'failed','cancelled')",
            name="ck_gis_mission_state",
        ),
        Index("idx_gis_mission_org_created", "org_id", "created_at"),
        Index("idx_gis_mission_org_state", "org_id", "state"),
        Index("idx_gis_mission_owner_scope", "owner_scope", "mission_id"),
        Index("idx_gis_mission_lease_expiry", "state", "lease_expires_at"),
        Index("idx_gis_mission_project", "project_id"),
    )


class GISMissionCheckpointRow(Base):
    """Bounded checkpoint ring entries for a Mission (refs only, ≤16KB)."""

    __tablename__ = "gis_mission_checkpoints"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    checkpoint_id = Column(String(96), nullable=False)
    mission_id = Column(String(64), nullable=False)
    org_id = Column(String(255), nullable=False)
    mission_revision = Column(Integer, nullable=False, default=1)
    goal_revision = Column(Integer, nullable=False, default=1)
    state = Column(String(32), nullable=False, default="running")
    snapshot = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("checkpoint_id", name="uq_gis_mission_cp_id"),
        Index("idx_gis_mission_cp_mission_created", "mission_id", "created_at"),
        Index("idx_gis_mission_cp_org", "org_id", "created_at"),
    )


class GISMissionSwarmRunRow(Base):
    """Durable swarm run ledger under a Mission (closes ADR-0187 process-local gap)."""

    __tablename__ = "gis_mission_swarm_runs"

    swarm_run_id = Column(String(64), primary_key=True)
    mission_id = Column(String(64), nullable=False)
    org_id = Column(String(255), nullable=False)
    goal_slice = Column(Text, nullable=False, default="")
    state = Column(String(24), nullable=False, default="running")
    #: {task_id: SwarmTaskReceipt dict} — bounded; receipts are refs/summaries only.
    tasks = Column(JSON, nullable=False, default=dict)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)
    terminal_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "state IN ('running','succeeded','failed','cancelled','partial')",
            name="ck_gis_mission_swarm_state",
        ),
        Index("idx_gis_mission_swarm_mission", "mission_id", "state"),
        Index("idx_gis_mission_swarm_org", "org_id", "created_at"),
    )


__all__ = [
    "GISMissionRow",
    "GISMissionCheckpointRow",
    "GISMissionSwarmRunRow",
]
