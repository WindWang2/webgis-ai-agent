"""Spatial Events 单元测试共享 fixtures（hermetic SQLite）。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
import app.models.mission  # noqa: F401 — register tables
import app.models.spatial_events  # noqa: F401 — register tables
from app.services.mission_runtime.service import MissionRuntimeService
from app.services.mission_runtime.store import MissionStore
from app.services.spatial_events.ledger import SpatialEventLedger


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db_factory(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)


@pytest.fixture()
def ledger(db_factory, event_env) -> SpatialEventLedger:
    return SpatialEventLedger(factory=db_factory)


@pytest.fixture()
def mission_runtime(db_factory):
    return MissionRuntimeService(store=MissionStore(factory=db_factory))


@pytest.fixture()
def event_env(monkeypatch):
    """默认开启本分支全部 flag（测试显式控制；生产默认全关）。"""
    monkeypatch.setenv("GIS_SPATIAL_EVENT_RUNTIME", "1")
    monkeypatch.setenv("GIS_SPATIAL_EVENT_MISSION_BRIDGE", "1")
    monkeypatch.setenv("GIS_SPATIAL_EVENT_INVALIDATION", "1")
    monkeypatch.setenv("GIS_SPATIAL_EVENT_GOVERNOR_GATE", "0")
    monkeypatch.setenv("GIS_SPATIAL_EVENT_WORKER_INTERVAL_S", "3600")
    monkeypatch.setenv("GIS_SPATIAL_EVENT_BATCH", "32")
    monkeypatch.delenv("GIS_SPATIAL_EVENT_WEBHOOK_SECRET", raising=False)
