"""Hermetic gis_context fixtures (in-memory SQLite, ADR-0206)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
import app.models.gis_context  # noqa: F401 — register tables
import app.models.mission  # noqa: F401 — register tables
from app.services.gis_context.store import WorkingContextStore
from app.services.gis_context.working_context import (
    BasisDataset,
    GISWorkingContext,
    WorkingBasis,
)


@pytest.fixture()
def wc_store():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return WorkingContextStore(factory=factory)


@pytest.fixture()
def wc():
    """A working context with an accepted basis (AOI + dataset + measure)."""
    return GISWorkingContext(
        mission_id="msn-test0001",
        org_id="org-1",
        project_id="prj-1",
        user_id="u-1",
        basis=WorkingBasis(
            aoi_bbox=[102.9, 30.5, 104.5, 31.5],
            aoi_name="成都市",
            time_period="2024",
            crs="EPSG:4326",
            measure_field="school_count",
            measure_statistic="sum",
            datasets=[BasisDataset(
                ref_id="ref:schools", alias="schools",
                content_revision="rev-1", role="source",
            )],
            recipe_id="choropleth_v1",
        ),
    )


@pytest.fixture()
def mission_runtime():
    """Mission runtime on the same in-memory DB family (separate engine)."""
    from app.services.mission_runtime.service import MissionRuntimeService
    from app.services.mission_runtime.store import MissionStore

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return MissionRuntimeService(store=MissionStore(factory=factory))


@pytest.fixture(autouse=True)
def _isolated_process_state():
    """Process-local hotpath state and sampled log counter reset per test."""
    from app.services.gis_context import hotpath as hp
    from app.services.gis_harness.hotpath_convergence.session_ctx import (
        reset_turn_context,
    )

    reset_turn_context()
    hp._log_counter["n"] = 0
    yield
    reset_turn_context()
