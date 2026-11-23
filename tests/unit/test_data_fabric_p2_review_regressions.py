"""P2 review-wave regression tests for ``app/services/data_fabric``.

Each test targets one reviewed defect and must FAIL on the pre-fix code.
Pure-unit where possible; adapters are exercised via minimal fakes /
monkeypatching so no live service is required.
"""
from __future__ import annotations

import pytest
import requests


# ── cost_model.py: bbox_selectivity antimeridian (finding cost_model:75) ──────


def test_bbox_selectivity_antimeridian_not_zero():
    from app.services.data_fabric.planning.cost_model import bbox_selectivity

    # request crosses the antimeridian; coverage is the whole world.
    sel = bbox_selectivity([170.0, -10.0, -170.0, 10.0], [-180.0, -90.0, 180.0, 90.0])
    assert sel > 0.0, "antimeridian bbox must not collapse selectivity to 0 (cost/budget lies)"


# ── planner.py: _desc_bbox_area antimeridian (finding planner:83) ────────────


def test_desc_bbox_area_antimeridian_wraps():
    from app.services.data_fabric.query.planner import _desc_bbox_area

    # [170,-10,-170,10]: wrapped lon width = 360 + (-170-170) = 20; height = 20 → 400.
    assert _desc_bbox_area([170.0, -10.0, -170.0, 10.0]) == pytest.approx(400.0)
    # a normal bbox is unchanged.
    assert _desc_bbox_area([0.0, 0.0, 10.0, 5.0]) == pytest.approx(50.0)


# ── compilers.py: MultiPoint [[]] must be typed-rejected (finding compilers:77)


def test_multipoint_empty_coord_raises_predicate_error():
    from app.services.data_fabric.query.compilers import geojson_to_wkt
    from app.services.data_fabric.query.predicates import PredicateError

    with pytest.raises(PredicateError):
        geojson_to_wkt({"type": "MultiPoint", "coordinates": [[]]})


# ── connection_registry.py: empty-string scope is global (finding conn_reg:88)


def test_tenant_scope_empty_string_is_global():
    from app.services.data_fabric.fabric.connection_registry import (
        _GLOBAL_SCOPE_KEY,
        TenantScope,
    )

    for scope in (TenantScope(owner=""), TenantScope(project_id="")):
        assert scope.is_global is True
        assert scope.scope_key() == _GLOBAL_SCOPE_KEY
    # a real owner is still tenant-scoped.
    assert TenantScope(owner="alice").is_global is False


# ── engine_breaker.py: replan in same context must not leak HALF_OPEN trial ──


def test_engine_breaker_replan_same_context_releases_trial():
    from app.services.data_fabric.fabric.engine_breaker import EngineFallbackBreaker

    clock = [0.0]
    b = EngineFallbackBreaker(failure_threshold=1, cool_down_s=10.0, clock=lambda: clock[0])
    b.record_v6_crash(RuntimeError("boom"))  # → OPEN
    clock[0] = 100.0  # past cool-down
    assert b.allow_v6() is True   # OPEN→HALF_OPEN, acquire the single trial
    assert b.allow_v6() is False  # replan in same context: trial already inflight
    b.release_trial()
    # trial slot must be free for reuse (bug: leaked → next allow_v6() returns False)
    assert b.allow_v6() is True


# ── fallback.py: resolve_chain includes ALL fallback rules (finding fallback:114)


def test_resolve_chain_includes_all_fallback_rules():
    from app.services.data_fabric.fallback import resolve_chain
    from app.services.data_fabric.source_registry import (
        FallbackRule,
        SourceRegistryError,
    )

    class _FakeDef:
        def __init__(self, rules):
            self._rules = rules

        def normalized_fallbacks(self):
            return self._rules

    class _FakeService:
        def __init__(self, defs):
            self._defs = defs

        def get(self, source_id):
            d = self._defs.get(source_id)
            if d is None:
                raise SourceRegistryError(f"no {source_id}")
            return d

    svc = _FakeService({
        "src_a": _FakeDef([
            FallbackRule(source_id="src_b", on=["timeout"]),
            FallbackRule(source_id="src_c", on=["empty_result"]),
        ]),
        "src_b": _FakeDef([]),
        "src_c": _FakeDef([]),
    })
    ids = [h[0] for h in resolve_chain("src_a", service=svc)]
    assert "src_b" in ids and "src_c" in ids, "every declared fallback rule must enter the chain"
    # empty_result trigger preserved on the second rule.
    triggers = {h[0]: h[1] for h in resolve_chain("src_a", service=svc)}
    assert "empty_result" in triggers["src_c"]


# ── result_cache.py: constant de-dup (result_cache:225) → NONE (no behavior change)


# ── source_facts.py: observe_unfiltered_count honours _max (source_facts:405)


def test_source_facts_observe_count_bounded():
    from app.services.data_fabric.fabric.source_facts import SourceFactsService

    svc = SourceFactsService(max_entries=3)
    for i in range(10):
        svc.observe_unfiltered_count(
            scope_key="org:_|owner:o|proj:_",
            fingerprint=f"fp-{i}",
            profile_id="p",
            source_type="ogc_api",
            count=i,
        )
    # the in-memory LRU must stay bounded by max_entries.
    with svc._lock:
        assert len(svc._entries) <= 3


# ── replay.py: sampling honours the requested rate (replay:104) ──────────────


def test_replay_sampling_honors_rate():
    from app.services.data_fabric.contracts import AcquisitionPlan, AcquisitionStep
    from app.services.data_fabric.planning.replay import replay
    from app.schemas.data_fabric_schema import QueryResult

    plan = AcquisitionPlan(
        plan_id="p1", dataset_key="src/ds", version="latest",
        steps=[
            AcquisitionStep(step_type="source_select", source_id="src"),
            AcquisitionStep(step_type="sampling", source_id="src", params={"rate": 0.6}),
        ],
    )

    class _FakeAdapter:
        def query(self, dataset_id, spec):
            feats = [
                {"type": "Feature", "id": i, "geometry": None, "properties": {"i": i}}
                for i in range(100)
            ]
            return QueryResult(dataset_id=dataset_id, features=feats,
                               result_mode="features", total_count=100)

    res = replay(plan, adapter=_FakeAdapter())
    assert res["rows"] == 60, f"rate=0.6 of 100 must keep 60 rows, got {res['rows']}"


# ── planner.py: input CRS consistency for non-bbox spatial (planner:161) ─────


def test_planner_nonbbox_crs_mismatch_is_typed():
    from app.services.data_fabric.query.capabilities import get_capabilities
    from app.services.data_fabric.query.models import QuerySpecV2
    from app.services.data_fabric.errors import CrsInvalidError
    from app.schemas.data_fabric_schema import DatasetDescriptor
    from app.services.data_fabric.query.planner import plan_query

    caps = get_capabilities("postgis").model_copy(update={"server_reprojection": False})
    spec = QuerySpecV2(spatial={
        "op": "intersects",
        "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
        "crs": "EPSG:3857",
    })
    descriptor = DatasetDescriptor(id="ds", source_type="postgis", srs="EPSG:4326")
    with pytest.raises(CrsInvalidError):
        plan_query(spec, descriptor, caps, source_id="pg")


# ── stats_api.py: cross-page id uniqueness (stats_api:142) ───────────────────


def _stats_adapter(datasets):
    from app.services.data_fabric.adapters.stats_api_adapter import StatsApiAdapter
    from app.schemas.data_fabric_schema import ConnectionProfile

    profile = ConnectionProfile(
        source_type="stats_api",
        endpoint_url="https://stats.test/api",
        options={"datasets": datasets},
    )
    return StatsApiAdapter(profile)


def test_stats_api_ids_unique_across_pages(monkeypatch):
    from app.schemas.data_fabric_schema import QuerySpec

    adapter = _stats_adapter([{
        "dataset_id": "ds", "path": "things", "page_param": "page",
        "page_size": 2, "page_size_param": "per_page",
    }])
    # page_size==row_limit==2, so a page only continues when it is "full" yet the
    # row cap is not hit — achieved here by a skipped (non-dict) row on page 1.
    pages = {
        1: [{"v": "a"}, "not-a-dict"],
        2: [{"v": "b"}, {"v": "c"}],
    }

    def fake_fetch(url, params=None):
        return pages.get((params or {}).get("page", 1), [])

    monkeypatch.setattr(adapter, "_fetch_json", fake_fetch)
    res = adapter.query("ds", QuerySpec(limit=0))
    ids = [f["id"] for f in res.features]
    assert len(ids) >= 2, f"pagination must fetch >1 page, got {ids}"
    assert len(ids) == len(set(ids)), f"cross-page ids must be unique, got {ids}"


# ── stats_api.py: string total must not crash / leak (stats_api:235) ──────────


def test_stats_api_string_total_is_normalized(monkeypatch):
    from app.schemas.data_fabric_schema import QuerySpec

    adapter = _stats_adapter([{
        "dataset_id": "ds", "path": "things", "items_path": "payload",
    }])

    def fake_fetch(url, params=None):
        return {"payload": {"total": "1234", "results": [{"v": "a"}, {"v": "b"}]}}

    monkeypatch.setattr(adapter, "_fetch_json", fake_fetch)
    res = adapter.query("ds", QuerySpec(limit=10))
    assert res.total_matching == 1234
    assert isinstance(res.total_matching, int)


# ── pmtiles.py: network failure in tile branch is typed (pmtiles:811) ────────


def test_pmtiles_tile_network_failure_is_typed(monkeypatch):
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter
    from app.services.data_fabric.errors import SourceUnreachableError
    from app.schemas.data_fabric_schema import (
        ConnectionProfile,
        DatasetDescriptor,
        QuerySpec,
    )

    profile = ConnectionProfile(
        source_type="pmtiles",
        endpoint_url="https://tiles.test/archive.pmtiles",
        name="pmt",
    )
    adapter = PMTilesAdapter(profile)
    assert adapter.endpoint, "endpoint must be set so is_demo is False"
    monkeypatch.setattr(
        adapter, "describe",
        lambda ds: DatasetDescriptor(
            id=ds, source_type="pmtiles", source_id="pmt",
            metadata={"min_zoom": 0, "tile_type": "MVT"},
        ),
    )

    def boom(z, x, y):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(adapter, "read_tile_bytes", boom)
    spec = QuerySpec(tile_coords={"z": 0, "x": 0, "y": 0})
    with pytest.raises(SourceUnreachableError):
        adapter.query("ds", spec)


# ── flatgeobuf.py: SAMPLE mode honest truncation (flatgeobuf:574) ────────────


def test_flatgeobuf_sample_mode_honest_truncation(monkeypatch, tmp_path):
    import geopandas as gpd
    from shapely.geometry import Point

    from app.services.data_fabric.adapters import flatgeobuf_adapter as fgb_mod
    from app.services.data_fabric.adapters.flatgeobuf_adapter import FlatGeobufAdapter
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec

    monkeypatch.setattr(fgb_mod, "_local_file_roots_from_settings", lambda: [str(tmp_path)])
    fgb = tmp_path / "t.fgb"
    gdf = gpd.GeoDataFrame(
        {"id": list(range(20)), "v": [f"x{i}" for i in range(20)]},
        geometry=[Point(float(i), float(i)) for i in range(20)], crs="EPSG:4326",
    )
    gdf.to_file(str(fgb), driver="FlatGeobuf")

    adapter = FlatGeobufAdapter(ConnectionProfile(
        source_type="flatgeobuf", endpoint_url=str(fgb), name="fgb",
    ))
    res = adapter.query("t", QuerySpec(limit=5, result_mode="sample", sample_size=7))
    assert res.result_mode == "sample"
    assert res.truncated is True, "a bounded-window sample cannot claim an exhaustive total"
    assert res.total_matching is None, "must not report the bounded read-prefix as total_matching"


# ── federated/executor.py: hop_estimates follow hops after tail reorder ──────


def test_executor_hop_estimates_reordered_with_hops(monkeypatch):
    import app.services.data_fabric.query.federated.executor as ex_mod
    from app.services.data_fabric.query.federated.executor import PhysicalExecutor
    from app.services.data_fabric.query.federated.logical import LogicalJoin, LogicalScan
    from app.services.data_fabric.query.models import ExecutionBudget

    budget = ExecutionBudget(
        max_rows=1_000_000, max_bytes=536_870_912, max_vertices=50_000_000,
        max_pages=100, deadline_s=60.0,
    )
    ex = PhysicalExecutor(adapter_factory=lambda sid: None, budget=budget, limit=1000)

    class _Spec:
        kind = "attribute_join"
        join_field_left = "k"
        join_field_right = "k"
        spatial_op = None
        group_by_right = None
        aggregates = None

    spec = _Spec()
    # edges needed for the rebuilt tail: A→D and D→C.
    edge_specs = {("A", "D"): spec, ("D", "C"): spec}

    base = LogicalScan(source_id="A", dataset_id="A")

    def _join(sid):
        return LogicalJoin(
            join_kind="attribute_join", left=base,
            right=LogicalScan(source_id=sid, dataset_id=sid),
            join_field_left="k", join_field_right="k",
        )

    hops = [_join("B"), _join("C"), _join("D")]  # original order B, C, D
    hop_estimates = [100, 50, 30]  # B→100, C→50, D→30

    # isolate the reorder logic from real evaluation / join / lift work.
    monkeypatch.setattr(ex, "_eval", lambda node, lift_key=None: ([{"k": 1}], None))
    monkeypatch.setattr(ex, "_apply_hop", lambda hop, acc, right, hop_pos=0: [{}] * 5)
    monkeypatch.setattr(ex, "_lift", lambda rows, key: None)
    ex.trace.per_source_rows = {"A": 1, "B": 1}
    ex._ndv_hints = {}
    # force a single tail reorder that swaps the tail to [D, C].
    monkeypatch.setattr(ex_mod, "pick_tail_order", lambda **kw: (["D", "C"], True))

    class _FakeAdaptive:
        def __init__(self):
            self.observations = []
            self.replans_used = 0
            self.notes = []

        def observe(self, *, hop, estimated_rows, actual_rows):
            self.observations.append({"hop": hop, "estimated_rows": estimated_rows})
            return hop == 0  # trigger exactly one replan after the first hop

        def can_replan(self):
            return True

    fake = _FakeAdaptive()
    ex.adaptive = fake

    ex._execute_chain_adaptive(base, list(hops), hop_estimates=list(hop_estimates), edge_specs=edge_specs)

    by_hop = {o["hop"]: o["estimated_rows"] for o in fake.observations}
    # After the reorder, hop 1 executes source D (original estimate 30) and hop 2
    # executes source C (original estimate 50). If hop_estimates were NOT reordered
    # alongside hops, hop 1 would carry C's stale estimate (50).
    assert by_hop.get(1) == 30, f"hop-1 (now D) must carry D's estimate 30, got {by_hop.get(1)}"
    assert by_hop.get(2) == 50, f"hop-2 (now C) must carry C's estimate 50, got {by_hop.get(2)}"


# ── wfs.py: non-2xx POST response is closed (wfs:491) ────────────────────────


def test_wfs_post_non2xx_closes_response(monkeypatch):
    from app.services.data_fabric.adapters.wfs_adapter import WFSAdapter
    from app.services.data_fabric.security import DataFabricSecurity
    from app.schemas.data_fabric_schema import (
        ConnectionProfile,
        DatasetDescriptor,
        QuerySpec,
    )

    # bypass SSRF hostname resolution (no live network in unit tests).
    monkeypatch.setattr(DataFabricSecurity, "validate_url", staticmethod(lambda url, **kw: url))
    profile = ConnectionProfile(source_type="wfs", url="https://wfs.test/wfs", name="wfs")
    adapter = WFSAdapter(profile)
    # bypass the GetCapabilities HTTP probe — we only exercise the POST GetFeature
    # response handling.
    monkeypatch.setattr(
        adapter, "describe",
        lambda ds: DatasetDescriptor(id=ds, source_type="wfs", title=ds),
    )

    class _FakeResp:
        def __init__(self):
            self.closed = False

        def raise_for_status(self):
            raise requests.exceptions.HTTPError("500 Server Error")

        def iter_content(self, chunk_size):  # pragma: no cover - never reached
            return iter([])

        def close(self):
            self.closed = True

    resp = _FakeResp()

    class _FakeSession:
        def post(self, *a, **k):
            return resp

    monkeypatch.setattr(adapter, "session", _FakeSession())
    # A filter predicate forces the POST GetFeature path (post_xml is built).
    spec = QuerySpec(filter_expr={"op": "eq", "field": "name", "value": "x"})
    with pytest.raises(Exception):
        adapter.query("lyr", spec)
    assert resp.closed, "streamed response must be closed on non-2xx (pool exhaustion guard)"
