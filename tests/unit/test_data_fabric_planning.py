"""ads-v1 DS3 planning tests (ADR-0173).

Covers: plan compilation across ≥5 source types, pushdown honesty (declared
capabilities decide what is pushed), budget choice with downgrade
suggestions (never a hard failure), deterministic explain (snapshot),
replay consistency (same plan + pin → same hash), and the cost model's
P50 deviation ≤ 30% against the fixture source (provisional calibration).
"""
from __future__ import annotations

import json

import pytest

from app.services.data_fabric.contracts import AcquisitionBudget
from app.services.data_fabric.planning import (
    PlanRequest,
    choose_plan,
    estimate_cost,
    explain_plan,
    facts_from_source_definition,
    replay,
)
from app.services.data_fabric.planning.compiler import PlanCompiler, _SourceFacts
from app.services.data_fabric.planning.cost_model import bbox_selectivity


# ── helpers ──────────────────────────────────────────────────────────────────


def _facts(protocol: str, source_id: str = "src", **kw) -> _SourceFacts:
    base = dict(
        source_id=source_id,
        source_name=f"Test {protocol}",
        protocol=protocol,
        title="Test dataset",
        fields=["name", "value"],
        data_type="point",
        feature_count=10_000,
        bbox=[100.0, 20.0, 130.0, 50.0],
        license="cc-by-4.0",
    )
    base.update(kw)
    return _SourceFacts(**base)


def _req(**kw) -> PlanRequest:
    base = dict(dataset_key="src/ds", bbox=[110.0, 30.0, 120.0, 40.0], columns=["name"], version="latest")
    base.update(kw)
    return PlanRequest(**base)


# ── plan compilation across source types (≥5) ────────────────────────────────


@pytest.mark.parametrize(
    "protocol,pushdown",
    [
        ("ogc_api", {"bbox": True, "time_filter": True, "projection": True, "pagination": True}),
        ("postgis", {"bbox": True, "aggregation": True, "projection": True}),
        ("arcgis", {"bbox": True, "projection": True}),
        ("stac", {"bbox": True, "time_filter": True}),
        ("geopackage", {"bbox": True, "projection": True}),
        ("stats_api", {"projection": True}),
        ("local_file", {"aggregation": True, "projection": True}),
    ],
)
def test_plan_generation_across_source_types(protocol, pushdown):
    facts = _facts(protocol, pushdown=pushdown)
    plan = PlanCompiler().compile(_req(), facts)
    step_types = [s.step_type for s in plan.steps]
    # execution order contract: source_select first, filters before projection
    assert step_types[0] == "source_select"
    assert plan.plan_id.startswith("plan-")
    assert plan.dataset_key == "src/ds"
    assert plan.cost_estimate.rows is not None
    assert facts.source_name.split()[-1] in plan.explain  # source named in explain
    if pushdown.get("bbox"):
        clip = next(s for s in plan.steps if s.step_type == "bbox_clip")
        assert clip.params["pushed_down"] is True
    else:
        clip = next((s for s in plan.steps if s.step_type == "bbox_clip"), None)
        if clip:
            assert clip.params["pushed_down"] is False  # honesty: local filter


def test_plan_id_deterministic_and_structure_sensitive():
    p1 = PlanCompiler().compile(_req(), _facts("ogc_api"))
    p2 = PlanCompiler().compile(_req(), _facts("ogc_api"))
    assert p1.plan_id == p2.plan_id
    p3 = PlanCompiler().compile(_req(bbox=[100.0, 20.0, 110.0, 30.0]), _facts("ogc_api"))
    assert p3.plan_id != p1.plan_id


def test_version_pin_step_when_not_latest():
    plan = PlanCompiler().compile(_req(version="rev-2024-06"), _facts("ogc_api"))
    pin = next((s for s in plan.steps if s.step_type == "version_pin"), None)
    assert pin is not None and pin.params["pin"] == "rev-2024-06"


def test_aggregate_and_sampling_steps():
    plan = PlanCompiler().compile(
        _req(aggregate="count", group_by="district", sample_rate=0.25),
        _facts("postgis", pushdown={"bbox": True, "aggregation": True}),
    )
    types = {s.step_type for s in plan.steps}
    assert "aggregate_pushdown" in types and "sampling" in types


# ── budget-aware choice with downgrade suggestions ───────────────────────────


def test_over_budget_returns_suggestions_not_failure():
    # tight budget: raw plan needs ~10k rows
    plan, suggestions = choose_plan(
        _req(budget=AcquisitionBudget(max_rows=500, max_bytes=200_000)),
        _facts("ogc_api", pushdown={"bbox": True, "pagination": True}),
    )
    assert plan is not None and suggestions, "over-budget must yield suggestions"
    assert any("聚合" in s or "抽样" in s or "范围" in s for s in suggestions)


def test_budget_fitting_variant_is_chosen():
    plan, suggestions = choose_plan(
        _req(budget=AcquisitionBudget(max_rows=500)),
        _facts("postgis", pushdown={"bbox": True, "aggregation": True}),
    )
    # the aggregated variant fits; suggestions may be empty if it was chosen
    assert plan.cost_estimate.rows is not None
    assert plan.cost_estimate.rows <= 500 or suggestions


def test_no_budget_returns_raw_plan_no_suggestions():
    plan, suggestions = choose_plan(_req(), _facts("ogc_api"))
    assert plan and suggestions == []


# ── explain snapshot (deterministic) ─────────────────────────────────────────


def test_explain_is_deterministic_snapshot():
    plan = PlanCompiler().compile(_req(), _facts("ogc_api", pushdown={"bbox": True, "projection": True}))
    lines1 = explain_plan(plan)
    lines2 = explain_plan(plan)
    assert lines1 == lines2
    assert any("下推" in ln for ln in lines1)
    assert any("代价估算" in ln and "provisional" in ln for ln in lines1)


def test_unpushed_steps_declared_as_local_in_explain():
    plan = PlanCompiler().compile(_req(), _facts("stats_api", pushdown={"projection": True}))
    lines = explain_plan(plan)
    assert any("本地" in ln for ln in lines), "unpushed bbox must be visible as local work"


# ── cost model deviation (provisional ≤30%) ──────────────────────────────────


def test_bbox_selectivity_grid_math():
    cov = [100.0, 20.0, 130.0, 50.0]
    assert bbox_selectivity(cov, cov) == pytest.approx(1.0)
    assert bbox_selectivity([100.0, 20.0, 115.0, 35.0], cov) == pytest.approx(0.25)
    assert bbox_selectivity(None, cov) == 1.0
    assert bbox_selectivity([0.0, 0.0, 1.0, 1.0], None) == 1.0  # unknown → upper bound


def test_cost_estimate_p50_deviation_within_30pct(patched_safe_sessions, fake_source_server):
    """Estimated rows/bytes vs actual on the fixture source (grid points).

    The OGC adapter applies the bbox locally after fetch (fake returns the
    full collection), so actual = grid points inside the window — exactly the
    selectivity model's prediction for a uniform grid.
    """
    from tests.data.fabric_fixtures import grid_features
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter

    feats = grid_features(10)  # 100 points on a 10×10 grid over the coverage bbox
    fake_source_server.add_ogc_api(features=feats)
    patched_safe_sessions(fake_source_server)
    adapter = OGCAPIAdapter(
        ConnectionProfile(source_type="ogc_api", endpoint_url=fake_source_server.base_url + "/ogc", name="cost_test")
    )

    cov = [100.0, 20.0, 130.0, 50.0]
    windows = [
        [100.0, 20.0, 115.0, 35.0],   # quarter
        [100.0, 20.0, 130.0, 35.0],   # half height
        [100.0, 20.0, 130.0, 50.0],   # full
        [107.5, 27.5, 122.5, 42.5],   # half
    ]
    deviations = []
    for w in windows:
        est = estimate_cost(
            feature_count=len(feats), fields=2, geometry_type="point",
            protocol="ogc_api", request_bbox=w, coverage_bbox=cov, limit=None,
        )
        result = adapter.query("lake_depth", QuerySpec(bbox=w, limit=10_000))
        actual_rows = len(result.features)
        actual_bytes = len(json.dumps(result.features, ensure_ascii=False).encode())
        row_dev = abs(est.rows - actual_rows) / max(1, actual_rows)
        byte_dev = abs(est.bytes - actual_bytes) / max(1, actual_bytes)
        deviations.append(row_dev)
        deviations.append(byte_dev)
    deviations.sort()
    p50 = deviations[len(deviations) // 2]
    assert p50 <= 0.30, f"cost estimate P50 deviation {p50:.2%} > 30%"


# ── replay consistency ───────────────────────────────────────────────────────


def test_replay_same_plan_same_hash(patched_safe_sessions, fake_source_server):
    from tests.data.fabric_fixtures import grid_features
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.adapters.ogc_api_adapter import OGCAPIAdapter

    fake_source_server.add_ogc_api(features=grid_features(4))
    patched_safe_sessions(fake_source_server)
    adapter = OGCAPIAdapter(
        ConnectionProfile(source_type="ogc_api", endpoint_url=fake_source_server.base_url + "/ogc", name="replay_test")
    )

    req = _req(version="rev-42", limit=16)
    plan = PlanCompiler().compile(req, _facts("ogc_api", pushdown={"bbox": True}))
    r1 = replay(plan, adapter=adapter)
    r2 = replay(plan, adapter=adapter)
    assert r1["hash"] == r2["hash"]
    # 4×4 grid over [100,20,130,50]: the clip window [110,30,120,40] contains
    # exactly 4 grid points (the fake source now filters server-side)
    assert r1["rows"] == r2["rows"] == 4

    # a different version pin yields a different hash (version participates)
    other = PlanCompiler().compile(_req(version="rev-43", limit=16), _facts("ogc_api", pushdown={"bbox": True}))
    r3 = replay(other, adapter=adapter)
    assert r3["hash"] != r1["hash"]


def test_facts_from_registry_source():
    facts = facts_from_source_definition("local_osm", "roads")
    assert facts.local is True and facts.protocol == "geopackage"
    plan = PlanCompiler().compile(_req(dataset_key="local_osm/roads"), facts)
    assert plan.steps[0].source_id == "local_osm"
    assert plan.cost_estimate.latency_ms is not None
