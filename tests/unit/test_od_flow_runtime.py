"""OD Flow vertical-slice tests (ADR-0092 Phase D).

Covers the D1/D2/D3 data contract, the D4 native flow_od_arc rendering
chain (converter + dispatch seam → MapSpec), and the §11 large-OD
performance contract (≥50k edges, O(N log N), bounded output).
"""
import json
import time
import uuid

import pytest

from app.evaluation.fixtures import od_edges, od_edges_50k
from app.services.session_data import session_data_manager
from app.tools import init_tools
from app.tools.registry import ToolRegistry
from app.tools.flow_tools import OD_FLOW_MAX_EDGES, _aggregate_rows


@pytest.fixture
async def registry():
    reg = ToolRegistry()
    init_tools(reg)
    assert "od_flow_edges" in set(reg.list_tools()), "flow tool must register"
    return reg


@pytest.fixture
async def flow_session():
    sid = f"flow-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


async def _store_od(sid: str, doc: dict, alias: str) -> str:
    ref = await session_data_manager.store(sid, doc, prefix="bench")
    await session_data_manager.set_alias(sid, ref, alias)
    return alias


# ── D1/D2: data contract + tool behaviors ─────────────────────────────────


async def test_od_flow_edges_top_n_bounded(registry, flow_session):
    alias = await _store_od(flow_session, od_edges(2000), "odt")
    res = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": alias, "top_n": 200},
        session_id=flow_session,
    )
    assert isinstance(res, dict) and res.get("type") == "FeatureCollection"
    assert len(res["features"]) == 200, "top-N must bound the output"
    # Weight-descending selection.
    weights = [f["properties"]["weight"] for f in res["features"]]
    assert weights == sorted(weights, reverse=True)
    # D1 contract fields on every feature.
    f0 = res["features"][0]["properties"]
    assert f0["origin_lng"] and f0["origin_lat"]
    assert f0["destination_lng"] and f0["destination_lat"]
    assert "weight" in f0 and "weight_norm" in f0
    # Stable ids for selection binding (D5).
    assert res["features"][0]["id"] == f0["id"]
    assert "->" in f0["id"]


async def test_od_flow_edges_hard_cap(registry, flow_session):
    alias = await _store_od(flow_session, od_edges(2000), "odt")
    res = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": alias, "top_n": 10_000_000},
        session_id=flow_session,
    )
    assert len(res["features"]) <= OD_FLOW_MAX_EDGES, "output cap is absolute"


async def test_od_flow_edges_bidirectional_aggregation(registry, flow_session):
    rows = {"type": "od_table", "rows": [
        {"origin_id": "a", "destination_id": "b",
         "origin_lng": 104.0, "origin_lat": 30.5,
         "destination_lng": 104.1, "destination_lat": 30.6, "weight": 5},
        {"origin_id": "b", "destination_id": "a",
         "origin_lng": 104.1, "origin_lat": 30.6,
         "destination_lng": 104.0, "destination_lat": 30.5, "weight": 3},
        {"origin_id": "a", "destination_id": "b",
         "origin_lng": 104.0, "origin_lat": 30.5,
         "destination_lng": 104.1, "destination_lat": 30.6, "weight": 2},
    ]}
    alias = await _store_od(flow_session, rows, "odb")
    res = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": alias, "top_n": 10, "aggregate": "bidirectional"},
        session_id=flow_session,
    )
    flows = res["features"]
    assert len(flows) == 1, "A→B and B→A must merge into one flow"
    assert flows[0]["properties"]["weight"] == 10


async def test_od_flow_edges_threshold_and_validation(registry, flow_session):
    alias = await _store_od(flow_session, od_edges(2000), "odt")
    res = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": alias, "top_n": 50, "min_weight": 10},
        session_id=flow_session,
    )
    # Non-vacuous: the fixture has weights ≥10, so an empty result would mean
    # the filter (or the test) regressed — all() alone passes on [].
    assert res["features"], "threshold must not empty the result for this fixture"
    assert all(f["properties"]["weight"] >= 10 for f in res["features"])
    # Unknown aggregation mode is a bounded, honest error.
    bad = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": alias, "aggregate": "nonsense"},
        session_id=flow_session,
    )
    assert bad.get("success") is False
    # Missing ref is a bounded error, not a crash.
    missing = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": "nope-alias"}, session_id=flow_session,
    )
    assert missing.get("success") is False


def test_aggregation_rejects_bad_rows():
    rows = [
        {"origin_lng": 104.0, "origin_lat": 30.5, "destination_lng": 104.1,
         "destination_lat": 30.6, "weight": 2},
        {"origin_lng": "garbage", "origin_lat": 30.5, "destination_lng": 104.1,
         "destination_lat": 30.6, "weight": 2},
        {"origin_lng": 200.0, "origin_lat": 30.5, "destination_lng": 104.1,
         "destination_lat": 30.6, "weight": 2},
    ]
    flows, skipped = _aggregate_rows(
        rows,
        ("origin_lng", "origin_lat", "destination_lng", "destination_lat"),
        "weight", "none", 0.0,
    )
    assert len(flows) == 1
    assert skipped == 2


# ── §11 Scenario E: 50k edges, O(N log N), bounded ────────────────────────


async def test_od_flow_edges_50k_edges_bounded_and_fast(registry, flow_session):
    doc = od_edges_50k()
    assert len(doc["rows"]) >= 50_000
    alias = await _store_od(flow_session, doc, "odt50k")
    started = time.monotonic()
    res = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": alias, "top_n": 1000, "min_weight": 2},
        session_id=flow_session,
    )
    elapsed = time.monotonic() - started
    assert res.get("type") == "FeatureCollection"
    assert len(res["features"]) <= 1000, "bounded output"
    # O(N log N) on 50k rows completes well under a wall-clock sanity bound;
    # an accidental O(N²) pair expansion would blow past this by orders of
    # magnitude.
    assert elapsed < 20, f"50k OD selection took {elapsed:.1f}s — complexity regression?"
    meta = res["metadata"]
    assert meta["total_edges"] >= 50_000
    assert meta["kept"] <= 1000


# ── D4: native flow_od_arc rendering chain (dispatch seam → MapSpec) ──────


async def test_flow_layer_authored_via_dispatch_seam(registry, flow_session):
    alias = await _store_od(flow_session, od_edges(2000), "odt")
    from app.services.tool_dispatch_service import ToolDispatchService

    service = ToolDispatchService(registry=registry)
    tc = {
        "id": "call-flow-seam",
        "function": {
            "name": "od_flow_edges",
            "arguments": json.dumps({"od_table_ref": alias, "top_n": 100}),
        },
    }
    out = await service.dispatch(tc, session_id=flow_session, executed_tools=set())
    assert out.status.value == "ok" if hasattr(out.status, "value") else out.status == "ok"

    from app.services.mapspec.store import mapspec_store_instance

    spec = await mapspec_store_instance.get_mapspec(flow_session)
    assert spec, "flow tool must author a MapSpec layer"
    line_layers = [ly for ly in spec.get("layers", []) if ly.get("type") == "line"]
    assert line_layers, "flow_od_arc layer must be a line layer"
    paint = line_layers[-1].get("paint") or {}
    # Width channel ← weight (data-driven interpolate, never constant).
    width = paint.get("width")
    assert isinstance(width, dict) and width.get("method") == "interpolate"
    assert width.get("field") == "weight"
    # Color channel ← weight via continuous Plasma legend.
    color = paint.get("color")
    assert isinstance(color, dict) and color.get("method") == "interpolate"


async def test_flow_selection_ids_survive_parking(registry, flow_session):
    alias = await _store_od(flow_session, od_edges(2000), "odt")
    res = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": alias, "top_n": 20},
        session_id=flow_session,
    )
    ref = await session_data_manager.store(flow_session, res, prefix="geojson")
    parked = await session_data_manager.get(flow_session, ref)
    ids = [f["id"] for f in parked["features"]]
    assert all("->" in i for i in ids), "selection ids must survive the ref round-trip"
    assert len(set(ids)) == len(ids), "flow ids must be unique for selection binding"


async def test_od_flow_edges_reports_skipped_rows(registry, flow_session):
    """Tool-level disclosure contract: invalid rows are counted, not silent."""
    rows = {"type": "od_table", "rows": [
        {"origin_id": "a", "destination_id": "b", "origin_lng": 104.0,
         "origin_lat": 30.5, "destination_lng": 104.1, "destination_lat": 30.6,
         "weight": 5},
        {"origin_lng": "garbage", "origin_lat": 30.5, "destination_lng": 104.1,
         "destination_lat": 30.6, "weight": 2},
        {"origin_lng": 200.0, "origin_lat": 30.5, "destination_lng": 104.1,
         "destination_lat": 30.6, "weight": 2},
    ]}
    alias = await _store_od(flow_session, rows, "odskip")
    res = await registry.dispatch(
        "od_flow_edges", {"od_table_ref": alias, "top_n": 10},
        session_id=flow_session,
    )
    assert res["metadata"]["total_edges"] == 3
    assert res["metadata"]["skipped_rows"] == 2
    assert len(res["features"]) == 1
