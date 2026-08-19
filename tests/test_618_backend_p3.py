"""P3 audit items from GitHub issue #618 (backend only).

Covers items 6, 10, 11, 14, 16, 17, 18, 20. Each test fails on the pre-fix code.
"""
from __future__ import annotations

import math
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Layer, Organization
from app.models.project import Project, Workflow, WorkflowRevision
from app.models.pydantic_models import LayerCreate
from app.schemas.project_schema import WorkflowCreate, WorkflowGraphSpec, WorkflowStepSpec
from app.services.data_fabric import manager as df_manager
from app.services.data_fabric.manager import DataFabricManager
from app.services.layer_service import LayerService
from app.services.project_service import ProjectService
from app.services.session_data import MemorySessionStore
from app.services.rs.band_math import compute_raster_stats
from app.services.rs.spectral_engine import SpectralRasterEngine
from app.services.rs.stac_client import StacClientPrimitive, stac_primitive
from app.services.spatial_decision import mapspec_integration as mapspec_mod
from app.tools.osm import _geocode_bbox
from app.tools.what_if_simulate import _generate_simulation_geojson


# ── shared sqlite fixture ───────────────────────────────────────────────────


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_con, _):
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _seed_org(db, org_id=1, slug="org1"):
    org = Organization(id=org_id, name=f"org{org_id}", slug=slug)
    db.add(org)
    db.commit()
    return org


# ── item 6: materialize audit-fail must delete the stored ref ───────────────


def _qr_features():
    from app.schemas.data_fabric_schema import QueryResult

    return QueryResult(
        dataset_id="ds1",
        features=[{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            "properties": {"id": 1},
        }],
        total_count=1,
    )


@pytest.mark.asyncio
async def test_item6_audit_commit_failure_deletes_stored_ref(monkeypatch):
    """#618-6: if the audit row fails after store(), the session ref must not linger."""
    store = MemorySessionStore()
    monkeypatch.setattr(df_manager, "session_data_manager", store)

    item = MagicMock()
    item.id = "cat1"
    item.source_id = "src1"
    item.title = "T"
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = item
    db.commit.side_effect = RuntimeError("db gone")

    async def _async_query(cls, db, item_id, spec, cancel_token=None):
        return _qr_features()

    monkeypatch.setattr(
        DataFabricManager, "query_catalog_item_async", classmethod(_async_query)
    )

    res = await DataFabricManager.materialize_catalog_item(
        db=db, session_id="s-orphan", item_id="cat1"
    )

    assert res["success"] is False
    assert res["ref_id"] is None
    db.rollback.assert_called()
    # Compensation: nothing retrievable remains for this session.
    refs = await store.list_refs("s-orphan")
    assert refs == {}


# ── item 10: LayerService CHECK-legal status + org_id ───────────────────────


def test_item10_create_uses_ready_status_and_caller_org():
    added = []

    class _DB:
        def add(self, obj):
            added.append(obj)

        def commit(self):
            pass

        def refresh(self, obj):
            if getattr(obj, "id", None) is None:
                obj.id = 1

    svc = LayerService(_DB())
    layer = svc.create(
        LayerCreate(name="roads", layer_type="vector"),
        creator_id="u-layer",
        org_id=42,
    )
    assert layer.status == "ready"
    assert layer.org_id == 42
    assert layer.creator_id == "u-layer"
    assert added[0] is layer


def test_item10_list_all_filters_ready_not_active(db_session):
    org = _seed_org(db_session, org_id=7, slug="org7")
    db_session.add_all([
        Layer(id=1, org_id=org.id, name="ready-one", layer_type="vector", status="ready"),
        Layer(id=2, org_id=org.id, name="pending-one", layer_type="vector", status="pending"),
        Layer(id=3, org_id=org.id, name="error-one", layer_type="vector", status="error"),
    ])
    db_session.commit()

    svc = LayerService(db_session)
    layers, total = svc.list_all()
    names = {ly.name for ly in layers}
    assert names == {"ready-one"}
    assert total == 1


def test_item10_delete_removes_row(db_session):
    org = _seed_org(db_session, org_id=8, slug="org8")
    row = Layer(id=11, org_id=org.id, name="gone", layer_type="vector", status="ready")
    db_session.add(row)
    db_session.commit()

    svc = LayerService(db_session)
    assert svc.delete(11) is True
    assert svc.get_by_id(11) is None
    assert db_session.get(Layer, 11) is None


# ── item 11: workflow insert + revision in one transaction ──────────────────


def _wf_create(name="wf"):
    return WorkflowCreate(
        name=name,
        graph_spec=WorkflowGraphSpec(
            steps=[WorkflowStepSpec(step_id="s1", tool_name="t_a", dependencies=[])]
        ),
    )


def test_item11_save_workflow_rolls_back_when_revision_fails(db_session, monkeypatch):
    org = _seed_org(db_session)
    proj = Project(id=f"proj_{uuid.uuid4().hex[:8]}", name="p", org_id=org.id, status="active")
    db_session.add(proj)
    db_session.commit()

    def boom(*a, **k):
        raise RuntimeError("revision publish failed")

    monkeypatch.setattr(ProjectService, "_publish_revision", staticmethod(boom))

    with pytest.raises(RuntimeError, match="revision publish failed"):
        ProjectService.save_workflow(db_session, proj.id, _wf_create())

    assert db_session.execute(select(Workflow)).scalars().all() == []
    assert db_session.execute(select(WorkflowRevision)).scalars().all() == []


def test_item11_update_workflow_rolls_back_graph_when_revision_fails(db_session, monkeypatch):
    org = _seed_org(db_session)
    proj = Project(id=f"proj_{uuid.uuid4().hex[:8]}", name="p", org_id=org.id, status="active")
    db_session.add(proj)
    db_session.commit()

    wf = ProjectService.save_workflow(db_session, proj.id, _wf_create())
    old_graph = dict(wf.graph_spec)
    old_rev = wf.current_revision_id
    assert old_rev  # first save published a revision

    def boom(*a, **k):
        raise RuntimeError("revision publish failed")

    monkeypatch.setattr(ProjectService, "_publish_revision", staticmethod(boom))

    new_graph = {
        "steps": [
            {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
            {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
        ]
    }
    with pytest.raises(RuntimeError, match="revision publish failed"):
        ProjectService.update_workflow(db_session, proj.id, wf.id, graph_spec=new_graph)

    fresh = db_session.get(Workflow, wf.id)
    assert fresh.graph_spec == old_graph
    assert fresh.current_revision_id == old_rev
    assert len(ProjectService.list_workflow_revisions(db_session, proj.id, wf.id)) == 1


# ── item 14: geocode expand_km longitude uses cos(lat) ──────────────────────


@pytest.mark.asyncio
async def test_item14_geocode_bbox_lon_expand_uses_cos_lat():
    # Degenerate Nominatim bbox at 60°N: south=north=60, west=east=10.
    payload = [{
        "lat": "60.0",
        "lon": "10.0",
        "importance": "1.0",
        "boundingbox": ["60.0", "60.0", "10.0", "10.0"],
    }]
    with patch("app.tools.osm.tracked_provider_get", new=AsyncMock(return_value=payload)):
        bbox = await _geocode_bbox("Tromso", expand_km=111.0)

    south, west, north, east = [float(p) for p in bbox.split(",")]
    lat_delta = 111.0 / 111.0
    lon_delta = 111.0 / (111.0 * max(math.cos(math.radians(60.0)), 0.01))
    assert south == pytest.approx(60.0 - lat_delta, abs=1e-9)
    assert north == pytest.approx(60.0 + lat_delta, abs=1e-9)
    assert west == pytest.approx(10.0 - lon_delta, abs=1e-9)
    assert east == pytest.approx(10.0 + lon_delta, abs=1e-9)
    # Pre-fix used the same 1° for longitude; at 60°N it must be ~2°.
    assert (east - west) == pytest.approx(2.0 * lon_delta, abs=1e-9)
    assert (east - west) > (north - south)


# ── item 16: mapspec match field must be zone_type ──────────────────────────


@pytest.mark.asyncio
async def test_item16_decision_mapspec_match_field_is_zone_type(monkeypatch):
    captured = {}

    async def fake_upsert(*, session_id, layer, source_data):
        captured["layer"] = layer
        captured["source_data"] = source_data
        return {"success": True, "mapspec": {"layers": [layer]}}

    monkeypatch.setattr(mapspec_mod, "_upsert_decision_layer", fake_upsert)

    result = SimpleNamespace(
        decision_id="dec1",
        simulation_ref_id="ref:sim",
        scenario=SimpleNamespace(name="subway"),
        simulation_geojson={
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"zone_type": "direct"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                },
            }],
        },
    )
    await mapspec_mod.apply_decision_to_mapspec("sess-16", result)
    assert captured["layer"]["style"]["color"]["field"] == "zone_type"
    assert captured["source_data"]["features"][0]["properties"]["zone_type"] == "direct"


def test_item16_what_if_geojson_keeps_zone_property():
    """what_if_simulate uses a different GeoJSON path with property 'zone' — leave it."""
    geo = _generate_simulation_geojson("subway", (116.4, 39.9), {})
    assert geo["features"][0]["properties"]["zone"] == "direct"
    assert "zone_type" not in geo["features"][0]["properties"]


# ── item 17: aspect circular mean ───────────────────────────────────────────


def test_item17_circular_mean_of_wrapping_aspects():
    arr = np.array([350.0, 10.0])
    linear = compute_raster_stats(arr)
    circ = compute_raster_stats(arr, circular=True)
    assert linear["mean"] == pytest.approx(180.0, abs=0.01)
    # atan2(mean sin, mean cos) of 350° and 10° is 0°, not 180°.
    assert circ["mean"] == pytest.approx(0.0, abs=0.05)
    assert circ["min"] == pytest.approx(10.0)
    assert circ["max"] == pytest.approx(350.0)


@pytest.mark.asyncio
async def test_item17_terrain_aspect_stats_use_circular_mean(monkeypatch):
    import importlib

    se_mod = importlib.import_module("app.services.rs.spectral_engine")
    engine = SpectralRasterEngine()
    aspect = np.array([[350.0, 10.0], [350.0, 10.0]])

    async def fake_fetch(**kwargs):
        return {"bands": {"dem": np.ones((2, 2)) * 100.0}, "cell_size_m": 30.0}

    monkeypatch.setattr(engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    monkeypatch.setattr(se_mod, "compute_aspect", lambda *a, **k: aspect)

    res = await engine.compute_terrain([0.0, 0.0, 1.0, 1.0], products=["aspect"])
    mean = res.stats["terrain_products"]["aspect"]["mean"]
    assert mean == pytest.approx(0.0, abs=0.05)
    assert mean != pytest.approx(180.0, abs=1.0)


@pytest.mark.asyncio
async def test_item17_slope_stats_stay_linear(monkeypatch):
    import importlib

    se_mod = importlib.import_module("app.services.rs.spectral_engine")
    engine = SpectralRasterEngine()
    slope = np.array([[1.0, 3.0], [5.0, 7.0]])

    async def fake_fetch(**kwargs):
        return {"bands": {"dem": np.ones((2, 2)) * 100.0}, "cell_size_m": 30.0}

    monkeypatch.setattr(engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    monkeypatch.setattr(se_mod, "compute_slope", lambda *a, **k: slope)

    res = await engine.compute_terrain([0.0, 0.0, 1.0, 1.0], products=["slope"])
    assert res.stats["terrain_products"]["slope"]["mean"] == pytest.approx(4.0, abs=0.01)


# ── item 18: STAC search sorts by cloud cover ───────────────────────────────


class _CloudItem:
    def __init__(self, item_id, cloud_cover):
        self.id = item_id
        self.properties = {"eo:cloud_cover": cloud_cover}
        self.assets = {}
        self.datetime = None
        self.bbox = [0.0, 0.0, 1.0, 1.0]


class _CloudSearch:
    def __init__(self, items):
        self._items = items

    def items(self):
        return list(self._items)


@pytest.mark.asyncio
async def test_item18_stac_picks_least_cloudy_and_returns_cloud_cover(monkeypatch):
    captured = {}

    class _Catalog:
        def search(self, **kwargs):
            captured["kwargs"] = kwargs
            items = [
                _CloudItem("cloudy", 80.0),
                _CloudItem("clear", 2.0),
                _CloudItem("mid", 20.0),
            ]
            sortby = kwargs.get("sortby")
            if sortby:
                items = sorted(items, key=lambda i: i.properties["eo:cloud_cover"])
            max_items = kwargs.get("max_items") or len(items)
            return _CloudSearch(items[:max_items])

    monkeypatch.setattr(StacClientPrimitive, "_get_catalog", lambda self: _Catalog())
    res = await stac_primitive.fetch_stac_items_and_bands(
        collection="sentinel-2-l2a",
        bbox=[0.0, 0.0, 1.0, 1.0],
        max_items=1,
    )
    assert "error" not in res
    assert res["first_item"].id == "clear"
    assert res["cloud_cover"] == 2.0
    assert captured["kwargs"].get("sortby")


# ── item 20: isochrone mid-edge + minutes conversion ────────────────────────


def _long_edge_network():
    from app.services.network.graph_builder import NetworkGraphBuilder
    from app.services.network.models import TravelProfile

    # ~7.7 km east-west at 39°N, 60 km/h → ~7.7 minutes travel time.
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.0, 39.0], [116.09, 39.0]],
            },
        }],
    }
    return NetworkGraphBuilder().build_graph(geojson, profile=TravelProfile())


def test_item20_isochrone_includes_partial_edge_beyond_last_node():
    from shapely.geometry import shape

    from app.services.network.models import Facility, TravelProfile
    from app.services.network.service_area import NetworkServiceAreaService

    graph, dataset = _long_edge_network()
    svc = NetworkServiceAreaService()
    fac = Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.0, 39.0]})
    areas = svc.network_service_area(
        facilities=[fac],
        breaks=[3.0],  # minutes — destination node is ~7.7 min away
        break_unit="minutes",
        graph=graph,
        network_dataset=dataset,
        profile=TravelProfile(),
    )
    brk = areas[0].breaks[0]
    assert brk.reachable_edge_count >= 1
    assert brk.reachable_network_geometry is not None
    reachable = shape(brk.reachable_network_geometry)
    full = shape(graph.edges[list(graph.edges)[0]]["geometry"])
    assert reachable.length > 0
    assert reachable.length < full.length


def test_item20_minutes_convert_for_custom_seconds_impedance():
    from app.services.network.graph_builder import NetworkGraphBuilder
    from app.services.network.models import Facility, Impedance, TravelProfile
    from app.services.network.service_area import NetworkServiceAreaService

    # 10×10 grid, ~111 m cells at 60 km/h. 5 minutes ≈ 5 km ≫ one cell;
    # 5 seconds (forgotten ×60) barely leaves the origin.
    features = []
    for r in range(10):
        features.append({
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.0 + c * 0.001, 39.0 + r * 0.001] for c in range(10)],
            },
        })
    for c in range(10):
        features.append({
            "type": "Feature",
            "properties": {"speed_kmh": 60.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.0 + c * 0.001, 39.0 + r * 0.001] for r in range(10)],
            },
        })
    graph, dataset = NetworkGraphBuilder().build_graph(
        {"type": "FeatureCollection", "features": features},
        profile=TravelProfile(),
    )
    for _u, _v, data in graph.edges(data=True):
        data["custom_s"] = data["travel_time_s"]

    svc = NetworkServiceAreaService()
    fac = Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.005, 39.005]})
    kwargs = dict(
        facilities=[fac],
        breaks=[5.0],
        break_unit="minutes",
        graph=graph,
        network_dataset=dataset,
        profile=TravelProfile(),
    )
    default = svc.network_service_area(**kwargs)
    custom = svc.network_service_area(
        **kwargs, impedance=Impedance(name="custom_s", unit="seconds")
    )
    default_n = default[0].breaks[0].reachable_edge_count
    custom_n = custom[0].breaks[0].reachable_edge_count
    assert default_n == custom_n
    assert default_n > 10


def test_item20_minutes_impedance_unit_does_not_double_convert():
    from app.services.network.models import Facility, Impedance, TravelProfile
    from app.services.network.service_area import NetworkServiceAreaService

    graph, dataset = _long_edge_network()
    for _u, _v, data in graph.edges(data=True):
        data["t_min"] = data["travel_time_s"] / 60.0

    svc = NetworkServiceAreaService()
    fac = Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.0, 39.0]})
    # 3 minutes of budget against a ~7.7-minute edge stored in minutes.
    areas = svc.network_service_area(
        facilities=[fac],
        breaks=[3.0],
        break_unit="minutes",
        graph=graph,
        network_dataset=dataset,
        profile=TravelProfile(),
        impedance=Impedance(name="t_min", unit="minutes"),
    )
    brk = areas[0].breaks[0]
    # Destination node must stay unreachable (3 < 7.7). A mistaken *60 would
    # treat the cutoff as 180 minutes and include the far node as fully reached.
    dest_fully_reached = brk.reachable_edge_count >= 2  # both directions full
    # Partial inclusion of the outgoing edge is expected; both-endpoints-in is not.
    from shapely.geometry import shape
    reachable = shape(brk.reachable_network_geometry) if brk.reachable_network_geometry else None
    full = shape(graph.edges[list(graph.edges)[0]]["geometry"])
    assert reachable is not None
    assert reachable.length < full.length or not dest_fully_reached
