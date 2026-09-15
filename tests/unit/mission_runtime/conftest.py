"""Hermetic Mission Runtime fixtures (in-memory SQLite)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
import app.models.mission  # noqa: F401 — register tables
from app.services.mission_runtime.service import MissionRuntimeService
from app.services.mission_runtime.store import MissionStore


@pytest.fixture()
def mission_store():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return MissionStore(factory=factory)


@pytest.fixture()
def runtime(mission_store):
    return MissionRuntimeService(store=mission_store)
