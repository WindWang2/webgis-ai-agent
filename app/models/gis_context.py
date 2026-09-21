"""GIS Working Context ORM model (ADR-0204 / Direction 06).

``gis_working_contexts`` is the mission-scoped GIS working state (accepted
basis / decisions / findings / user edits) that spans sessions under one
Mission. One bounded row per mission; payload is refs and short decision
records only (Zero Big Data in Agent Context). Writes are revision-CAS —
chat turns never touch the mission lease (epoch bumping would fence out
live swarm workers).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
)

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GISWorkingContextRow(Base):
    """Durable mission-scoped working context (revision CAS, ≤16KB payload)."""

    __tablename__ = "gis_working_contexts"

    mission_id = Column(String(64), primary_key=True)
    org_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    user_id = Column(String(255), nullable=False, default="")
    state = Column(String(16), nullable=False, default="active")
    revision = Column(Integer, nullable=False, default=1)
    goal_revision_mirror = Column(Integer, nullable=False, default=0)
    schema_version = Column(String(32), nullable=False, default="gis_working_context.v1")
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "state IN ('active','purged')",
            name="ck_gis_working_context_state",
        ),
        Index("idx_gis_working_context_org", "org_id", "updated_at"),
        Index("idx_gis_working_context_project", "project_id"),
    )


__all__ = ["GISWorkingContextRow"]
