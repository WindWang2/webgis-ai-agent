"""P3 逐行审查修复回归（app/services/data_fabric）。

每个测试对应 fixresults ``P3-app_services_data_fabric-00`` 清单中的一条
行为性缺陷；断言在未修复代码上必须失败。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QuerySpec,
)


# ── FlatGeobuf cache / probe ────────────────────────────────────────────────


class _FgbResp:
    def __init__(self, body: bytes = b"") -> None:
        self._body = body
        self.headers = {}
        self.ok = True
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        yield self._body

    def close(self) -> None:
        return None


class _FgbSession:
    def __init__(self, body: bytes = b"fgb-data") -> None:
        self._body = body

    def head(self, url, timeout=None, allow_redirects=True):
        return _FgbResp()

    def get(self, url, stream=None, timeout=None, **kw):
        return _FgbResp(self._body)


def test_fgb_cache_filename_is_stable_sha256(monkeypatch):
    from app.services.data_fabric.adapters import flatgeobuf_adapter as fgb

    url = "https://fgb.example.com/stable-name.fgb"
    path = fgb._cached_remote_flatgeobuf(_FgbSession(), url)
    expected = f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}.fgb"
    assert os.path.basename(path) == expected


def test_fgb_download_locks_are_released_after_use():
    from app.services.data_fabric.adapters import flatgeobuf_adapter as fgb

    fgb._cached_remote_flatgeobuf(_FgbSession(), "https://fgb.example.com/locks.fgb")
    # 引用计数归零后条目必须移除（不再有 URL 级锁字典泄漏）。
    assert fgb._FGB_DL_LOCKS == {}


def test_fgb_probe_requires_full_magic_not_three_byte_prefix(monkeypatch, tmp_path):
    from app.services.data_fabric.adapters.flatgeobuf_adapter import FlatGeobufAdapter

    bad = tmp_path / "bad.fgb"
    bad.write_bytes(b"fgb-not-a-real-header")
    adapter = FlatGeobufAdapter(ConnectionProfile(
        id="fgb", source_type="flatgeobuf", endpoint="https://fgb.example.com/x.fgb"))
    monkeypatch.setattr(adapter, "_readable_path", lambda: str(bad))
    assert adapter.probe() is False

    good = tmp_path / "good.fgb"
    good.write_bytes(b"fgb\x03fgb\x00" + b"\x00" * 8)
    monkeypatch.setattr(adapter, "_readable_path", lambda: str(good))
    assert adapter.probe() is True


# ── GeoJSON URL adapter ─────────────────────────────────────────────────────


class _GeoJsonResp:
    def __init__(self, body: bytes, status_code: int = 200, headers=None) -> None:
        self._body = body
        self.status_code = status_code
        self.headers = headers or {"Content-Type": "application/geo+json"}
        self.closed = False
        self.iter_calls = 0

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 65536):
        self.iter_calls += 1
        yield self._body

    def close(self) -> None:
        self.closed = True


class _NoReadGeoJsonResp(_GeoJsonResp):
    def iter_content(self, chunk_size: int = 65536):
        raise AssertionError("health() must not download the whole GeoJSON body")


def _geojson_adapter(monkeypatch, resp):
    from app.services.data_fabric.adapters.geojson_url_adapter import GeojsonUrlAdapter

    adapter = GeojsonUrlAdapter(ConnectionProfile(
        source_type="geojson_url", name="gj",
        endpoint_url="https://data.example.com/fc.geojson",
    ))
    monkeypatch.setattr(adapter.session, "get", lambda *a, **kw: resp)
    return adapter


def test_geojson_cache_returns_deep_copies(monkeypatch):
    doc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [1, 2]},
            "properties": {"n": 1},
        }],
    }
    adapter = _geojson_adapter(monkeypatch, _GeoJsonResp(json.dumps(doc).encode()))
    first = adapter._fetch_fc()
    first["features"][0]["properties"]["n"] = 999
    second = adapter._fetch_fc()
    assert first is not second
    assert second["features"][0]["properties"]["n"] == 1


def test_geojson_health_does_not_download_body(monkeypatch):
    resp = _NoReadGeoJsonResp(b"")
    adapter = _geojson_adapter(monkeypatch, resp)
    health = adapter.health()
    assert health.status == "healthy"
    assert resp.closed is True


# ── GeoPackage bounded page window ──────────────────────────────────────────


def test_geopackage_page_window_beyond_budget_is_typed(monkeypatch, tmp_path):
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point

    from app.services.data_fabric.adapters import geopackage_adapter as gpkg_mod
    from app.services.data_fabric.adapters.geopackage_adapter import GeoPackageAdapter
    from app.services.data_fabric.errors import QueryBudgetExceededError

    path = tmp_path / "small.gpkg"
    geopandas.GeoDataFrame(
        {"name": ["a", "b"]}, geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:4326"
    ).to_file(path, driver="GPKG", layer="pois")

    monkeypatch.setattr(gpkg_mod, "_MAX_QUERY_FEATURES", 10)
    adapter = GeoPackageAdapter(ConnectionProfile(
        id="gpkg", source_type="geopackage", endpoint_url=str(path)))
    adapter._path = path
    with pytest.raises(QueryBudgetExceededError):
        adapter.query("pois", QuerySpec(offset=8, limit=5))


# ── GeoParquet demo bbox validation ─────────────────────────────────────────


def test_geoparquet_demo_rejects_non_bbox_spatial_predicate():
    from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter
    from app.services.data_fabric.errors import InvalidQueryError

    adapter = GeoParquetAdapter(ConnectionProfile(
        id="gp", source_type="geoparquet", endpoint_url=""))
    with pytest.raises(InvalidQueryError):
        adapter.query("us_states_geoparquet", QuerySpec(
            spatial={"op": "intersects", "geometry": {"type": "Point", "coordinates": [1, 2]}},
        ))


# ── Local file FeatureCollection shape ──────────────────────────────────────


def test_local_file_rejects_non_list_features_as_typed_error(tmp_path):
    from app.services.data_fabric.adapters.local_file_adapter import LocalFileAdapter
    from app.services.data_fabric.errors import SourceBadResponseError

    path = tmp_path / "bad.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": {"x": 1}}))
    adapter = LocalFileAdapter(ConnectionProfile(
        id="lf", source_type="local_file", endpoint_url=str(path)))
    adapter._path = path
    with pytest.raises(SourceBadResponseError):
        adapter.describe("bad")


# ── OGC Coverages metadata/health ───────────────────────────────────────────


def _coverage_adapter(monkeypatch):
    from app.services.data_fabric.adapters.ogc_coverages_adapter import OGCCoveragesAdapter

    adapter = OGCCoveragesAdapter(ConnectionProfile(
        id="cov", source_type="ogc_api_coverages",
        endpoint_url="https://cov.example.com",
    ))
    monkeypatch.setattr(adapter, "_declares_coverages", lambda: False)
    monkeypatch.setattr(adapter, "_collection_doc", lambda ds: {
        "id": ds,
        "links": [{"rel": "coverage", "href": "https://cov.example.com/coverage"}],
    })
    return adapter


@pytest.mark.parametrize("referencing", [
    {"bad": "shape"},
    "not-a-list",
    [None, {"system": "EPSG:4326"}, {"system": {"id": None}}],
])
def test_ogc_coverages_grid_crs_tolerates_malformed_referencing(monkeypatch, referencing):
    adapter = _coverage_adapter(monkeypatch)

    def fake_get_json(url, timeout=10.0):
        if url.endswith("/domainset"):
            return {"referencing": referencing}
        return {}

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)
    desc = adapter.describe("dem")
    assert desc.metadata["grid_crs"] is None


def test_ogc_coverages_health_does_not_buffer_body(monkeypatch):
    adapter = _coverage_adapter(monkeypatch)
    resp = _NoReadGeoJsonResp(b"")
    seen = {}

    def fake_get(url, *a, **kw):
        seen.update(kw)
        return resp

    monkeypatch.setattr(adapter.session, "get", fake_get)
    health = adapter.health()
    assert health.status == "healthy"
    assert seen.get("stream") is True
    assert resp.closed is True


# ── OGC Tiles zoom bound / PMTiles health ───────────────────────────────────


def test_ogc_tiles_rejects_huge_zoom_before_shift(monkeypatch):
    from app.services.data_fabric.adapters.ogc_tiles_adapter import OGCTilesAdapter
    from app.services.data_fabric.errors import InvalidQueryError

    adapter = OGCTilesAdapter(ConnectionProfile(
        id="t", source_type="ogc_api_tiles", endpoint_url="https://tiles.example.com"))
    monkeypatch.setattr(adapter, "describe", lambda ds: DatasetDescriptor(
        id=ds, title="t", source_type="ogc_api_tiles",
        metadata={"display_ready": True, "minzoom": None, "maxzoom": None},
    ))
    with pytest.raises(InvalidQueryError):
        adapter.fetch_tile("ds", 100_000, 0, 0)


def test_pmtiles_health_reports_security_block_instead_of_raising(monkeypatch):
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter
    from app.services.data_fabric.errors import SecurityBlockedError

    adapter = PMTilesAdapter(ConnectionProfile(
        id="pm", source_type="pmtiles", endpoint_url=""))
    monkeypatch.setattr(adapter, "probe", lambda: (_ for _ in ()).throw(
        SecurityBlockedError("blocked")))
    health = adapter.health()
    assert health.status == "unreachable"


# ── S3 bare dataset id uses configured bucket ───────────────────────────────


def test_s3_describe_bare_dataset_uses_configured_bucket(monkeypatch):
    from app.services.data_fabric.adapters.s3_storage_seam import S3StorageSeam

    adapter = S3StorageSeam(ConnectionProfile(
        id="s3", source_type="s3", endpoint="https://s3.example.com",
        options={"bucket": "custom-bucket"},
    ))
    seen = {}

    def fake_head(**kwargs):
        seen.update(kwargs)
        return {"ContentLength": 10, "ContentType": "application/octet-stream"}

    monkeypatch.setattr(adapter, "_s3_client", lambda: SimpleNamespace(head_object=fake_head))
    adapter.describe("vectors/x.fgb")
    assert seen["Bucket"] == "custom-bucket"


# ── Arrow ops malformed geo metadata ────────────────────────────────────────


def test_covering_bbox_columns_tolerates_columns_not_dict():
    from app.services.data_fabric.arrow_ops import covering_bbox_columns

    assert covering_bbox_columns({"columns": [{"bbox": [0, 0, 1, 1]}]}) is None
    assert covering_bbox_columns({"columns": "bad"}) is None


def test_arrow_bbox_filter_malformed_columns_is_typed():
    pa = pytest.importorskip("pyarrow")
    from app.services.data_fabric.arrow_ops import (
        ArrowPredicateUnsupported,
        arrow_bbox_filter,
    )

    table = pa.table({"x": [1]}).replace_schema_metadata(
        {b"geo": json.dumps({"columns": "bad"}).encode()})
    with pytest.raises(ArrowPredicateUnsupported):
        arrow_bbox_filter(table, [0.0, 0.0, 1.0, 1.0])


# ── Connection manager dispose outside lock ─────────────────────────────────


def test_disconnect_disposes_pool_outside_manager_lock(monkeypatch):
    import app.services.data_fabric.connection_manager as cm

    mgr = cm.DataFabricConnectionManager()
    profile = ConnectionProfile(id="p1", source_type="postgis", url="")
    key = mgr._key(profile.id, None)
    with mgr._lock:
        mgr._profiles[key] = profile
        mgr._adapters[key] = object()
        mgr._last_access[key] = mgr._clock()

    entered = threading.Event()
    release = threading.Event()

    def slow_dispose(_profile):
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(cm, "_dispose_managed_postgis_pool", slow_dispose)
    worker = threading.Thread(target=lambda: mgr.disconnect("p1"))
    worker.start()
    assert entered.wait(2)
    acquired = mgr._lock.acquire(timeout=0.5)
    try:
        assert acquired, "manager lock must be free while the pool is disposed"
    finally:
        if acquired:
            mgr._lock.release()
        release.set()
        worker.join(5)


# ── coverage_read typed validation ─────────────────────────────────────────


def _coverage_desc():
    return DatasetDescriptor(
        id="dem", title="dem", source_type="ogc_api_coverages",
        metadata={
            "subset_read_ready": True,
            "capability": {"state": "supported", "reasons": []},
            "axes": {
                "x": {"count": 2, "first": 0.0, "last": 1.0},
                "y": {"count": 2, "first": 0.0, "last": 1.0},
            },
            "bands": [],
            "formats_supported": ["application/prs.coverage+json"],
            "grid_crs": "EPSG:4326",
        },
    )


def test_coverage_subset_short_window_is_typed():
    from app.services.data_fabric.coverage_read import (
        CoverageReadError, read_coverage_subset,
    )

    fake = SimpleNamespace(describe=lambda ds: _coverage_desc(), url="https://cov.example.com")
    with pytest.raises(CoverageReadError):
        asyncio.run(read_coverage_subset(
            fake, "dem", session_id="s", subsets={"x": (1.0,)}))


def test_coverage_bbox_wrong_length_is_typed():
    from app.services.data_fabric.coverage_read import (
        CoverageReadError, read_coverage_subset,
    )

    fake = SimpleNamespace(describe=lambda ds: _coverage_desc(), url="https://cov.example.com")
    with pytest.raises(CoverageReadError):
        asyncio.run(read_coverage_subset(
            fake, "dem", session_id="s", bbox=[1.0, 2.0, 3.0]))


def test_coverage_non_numeric_range_is_not_budget_error():
    from app.services.data_fabric.coverage_read import (
        CoverageReadError,
        RC_CAPABILITY_NOT_DECLARED,
        _parse_covjson,
    )

    doc = {
        "domain": {"axes": {
            "x": {"values": [0, 1]},
            "y": {"values": [0, 1]},
        }},
        "ranges": {"band1": {
            "axisNames": ["y", "x"], "shape": [2, 2],
            "values": [0, 1, 2, "not-a-number"],
        }},
    }
    with pytest.raises(CoverageReadError) as ei:
        _parse_covjson(json.dumps(doc).encode(), bands=None, bands_declared=[])
    assert ei.value.reason_code == RC_CAPABILITY_NOT_DECLARED


# ── probing copies ─────────────────────────────────────────────────────────


def test_probe_cache_hits_return_copies(monkeypatch):
    from app.services.data_fabric.fabric.probing import CapabilityProbeService
    from app.services.data_fabric.query.capabilities import get_capabilities

    class _Adapter:
        source_type = "arcgis"

        def capabilities_v2(self):
            return get_capabilities("arcgis")

    svc = CapabilityProbeService(ttl_s=60.0, max_entries=4)
    profile = ConnectionProfile(id="p", source_type="arcgis", url="https://example.com")
    r1 = svc.probe(_Adapter(), profile, "scope", "rev")
    r2 = svc.probe(_Adapter(), profile, "scope", "rev")
    assert r1 == r2
    assert r1 is not r2
    got = svc.get("p", "scope", "rev")
    assert got == r1 and got is not r1


def test_expired_cache_record_is_not_mutated_in_place(monkeypatch):
    from app.services.data_fabric.fabric.probing import CapabilityProbeService
    from app.services.data_fabric.query.capabilities import get_capabilities

    class _Adapter:
        source_type = "arcgis"

        def capabilities_v2(self):
            return get_capabilities("arcgis")

    svc = CapabilityProbeService(ttl_s=0.0, max_entries=4)
    profile = ConnectionProfile(id="p", source_type="arcgis", url="https://example.com")
    svc.probe(_Adapter(), profile, "scope", "rev")
    stored = svc._entries[("scope", "p", "rev")]
    assert stored.caps_basis == "probed"
    stale = svc.get("p", "scope", "rev")
    assert stale.caps_basis == "stale"
    assert stored.caps_basis == "probed"


# ── runtime adapter rebuild fallback ───────────────────────────────────────


def test_runtime_rebuild_failure_falls_back_to_legacy(monkeypatch):
    import app.services.data_fabric.fabric.connection_registry as reg_mod
    from app.services.data_fabric.fabric.runtime import FabricRuntime

    class _Reg:
        def sweep(self):
            return None

        def resolve(self, pid, scope):
            return None

        def peek(self, pid, scope):
            return SimpleNamespace(source_type="generic", scope_key="s", revision="r")

        def ensure_adapter(self, record):
            raise RuntimeError("rebuild failed")

    monkeypatch.setattr(reg_mod, "get_connection_registry", lambda: _Reg())
    rt = FabricRuntime()
    adapter = SimpleNamespace(profile=SimpleNamespace(source_type="generic"))
    monkeypatch.setattr(
        rt, "_legacy_adapter",
        lambda pid, owner=None: (adapter, SimpleNamespace(source_type="generic"), None),
    )
    resolved = rt.resolve("p1")
    assert resolved is not None and resolved.adapter is adapter


# ── source facts durable freshness ─────────────────────────────────────────


class _Durable:
    def __init__(self, record):
        self._record = record

    def load(self, scope_key, fingerprint):
        return self._record

    def save(self, facts):
        return True


def test_source_facts_ignores_durable_row_older_than_service_ttl():
    from app.services.data_fabric.fabric.source_facts import (
        SourceFactsRecord,
        SourceFactsService,
    )

    rec = SourceFactsRecord(
        dataset_fingerprint="fp", scope_key="s", profile_id="d",
        row_count=5, freshness_collected_at=time.time() - 3600,
    )
    svc = SourceFactsService(durable=_Durable(rec), ttl_s=300.0)
    assert svc.get(scope_key="s", fingerprint="fp") is None


def test_source_facts_accepts_fresh_durable_row():
    from app.services.data_fabric.fabric.source_facts import (
        SourceFactsRecord,
        SourceFactsService,
    )

    rec = SourceFactsRecord(
        dataset_fingerprint="fp", scope_key="s", profile_id="d",
        row_count=5, freshness_collected_at=time.time(),
    )
    svc = SourceFactsService(durable=_Durable(rec), ttl_s=300.0)
    facts = svc.get(scope_key="s", fingerprint="fp")
    assert facts is not None and facts.row_count == 5


# ── facts store lazy init lock ─────────────────────────────────────────────


def test_get_facts_store_is_single_instance_under_race(monkeypatch):
    import app.services.data_fabric.facts as facts_mod

    created = []
    original = facts_mod.FactsStore

    class _SlowStore(original):
        def __init__(self):
            created.append(1)
            time.sleep(0.05)
            super().__init__()

    monkeypatch.setattr(facts_mod, "FactsStore", _SlowStore)
    monkeypatch.setattr(facts_mod, "_STORE", None)
    first_entered = threading.Event()

    class _Signalling(_SlowStore):
        def __init__(self):
            first_entered.set()
            super().__init__()

    monkeypatch.setattr(facts_mod, "FactsStore", _Signalling)
    out = []
    t1 = threading.Thread(target=lambda: out.append(facts_mod.get_facts_store()))
    t1.start()
    assert first_entered.wait(2)
    t2 = threading.Thread(target=lambda: out.append(facts_mod.get_facts_store()))
    t2.start()
    t1.join(5)
    t2.join(5)
    assert len(created) == 1
    assert out[0] is out[1]


# ── health cache key ───────────────────────────────────────────────────────


def test_adapter_health_key_does_not_use_instance_identity():
    from app.services.data_fabric.health import adapter_health_key

    profile = ConnectionProfile(id="same", source_type="generic", url="")
    a1 = SimpleNamespace(profile=profile)
    a2 = SimpleNamespace(profile=profile)
    assert adapter_health_key(a1) == adapter_health_key(a2)


# ── local index cards ─────────────────────────────────────────────────────


def test_assets_to_cards_skips_fields_without_name():
    from app.services.data_fabric.local_index import LocalAsset, assets_to_cards

    asset = LocalAsset(
        asset_id="local/a", library="local", title="t", description="d",
        path="", data_type="vector",
        meta={"fields": [{"name": "ok"}, {"type": "string"}]},
    )
    cards = assets_to_cards([asset])
    assert cards[0].fields == ["ok"]


# ── planning compiler pagination ──────────────────────────────────────────


def test_compiler_emits_pagination_for_limited_request():
    from app.services.data_fabric.planning.compiler import (
        PlanCompiler, PlanRequest, _SourceFacts,
    )

    facts = _SourceFacts(
        source_id="src", protocol="ogc_api", feature_count=100_000,
        fields=["a"], data_type="point", bbox=[0, 0, 1, 1],
        requests_per_minute=60, pushdown={"pagination": True},
    )
    plan = PlanCompiler().compile(
        PlanRequest(dataset_key="src/ds", limit=5000, columns=["a"]), facts)
    assert any(s.step_type == "pagination" for s in plan.steps)


# ── query compilers typed errors ──────────────────────────────────────────


def test_compile_bbox_cql2_rejects_wrong_length_as_predicate_error():
    from app.services.data_fabric.query.compilers import compile_bbox_cql2
    from app.services.data_fabric.query.predicates import PredicateError

    with pytest.raises(PredicateError):
        compile_bbox_cql2([1.0, 2.0, 3.0])


def test_geojson_geometry_collection_depth_is_bounded():
    from app.services.data_fabric.query.compilers import geojson_to_wkt
    from app.services.data_fabric.query.predicates import PredicateError

    geom = {"type": "GeometryCollection", "geometries": []}
    for _ in range(100):
        geom = {"type": "GeometryCollection", "geometries": [geom]}
    with pytest.raises(PredicateError):
        geojson_to_wkt(geom)


# ── federated enumerator ──────────────────────────────────────────────────


def _enum_ctx(n=3):
    from app.services.data_fabric.query.federated.enumerator import (
        EnumerationContext, JoinEdge, SourceFacts,
    )

    sids = [chr(ord("a") + i) for i in range(n)]
    sources = [SourceFacts(source_id=s, dataset_id=f"ds_{s}", estimated_rows=1000) for s in sids]
    joins = [
        JoinEdge(left_source_id=sids[i], right_source_id=sids[i + 1],
                 kind="attribute_join", join_field_left="k", join_field_right="k")
        for i in range(n - 1)
    ]
    return EnumerationContext(sources=sources, joins=joins, limit=1000)


def test_enumerator_visits_each_subset_partition_once(monkeypatch):
    from app.services.data_fabric.query.federated import enumerator as en

    calls = {"n": 0}
    original = en._combine

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(en, "_combine", counting)
    en.enumerate_federation(_enum_ctx(3))
    # 3 源：2-bit masks（3×1 划分）+ 3-bit mask（3 划分），每划分两个方向。
    assert calls["n"] == 12


def test_fixed_chain_counts_right_scan_request_cost():
    from app.services.data_fabric.query.federated import enumerator as en

    ctx = _enum_ctx(2)
    a, b = ctx.sources
    plan = en._enumerate_fixed_chain(ctx, {"a": a, "b": b}, ["a", "b"], positional=True)
    a_bytes = en._scan_transfer_rows(a) * en._per_feature_bytes(a.fields)
    b_bytes = en._scan_transfer_rows(b) * en._per_feature_bytes(b.fields)
    a_cost, _ = en._scan_cost(a, bytes_cost=a_bytes * en._W_BYTES_TRANSFERRED)
    b_cost, _ = en._scan_cost(b, bytes_cost=b_bytes * en._W_BYTES_TRANSFERRED)
    assert plan.cost >= a_cost + en._W_REMOTE_REQUEST + b_cost + en._W_REMOTE_REQUEST


# ── federated executor / physical ─────────────────────────────────────────


def test_bushy_replan_propagates_cancelled_error(monkeypatch):
    from app.services.data_fabric.query.execution import StreamingBudget
    from app.services.data_fabric.query.federated.executor import PhysicalExecutor
    from app.services.data_fabric.query.federated.logical import LogicalScan
    from app.services.data_fabric.query.federated.physical import CancelledError

    budget = SimpleNamespace(max_rows=100, max_bytes=10_000, max_vertices=1_000,
                             deadline_s=60.0)
    tree = LogicalScan(source_id="a", dataset_id="da", estimated_rows=10, fetch_limit=100)
    new_tree = LogicalScan(source_id="b", dataset_id="db", estimated_rows=10, fetch_limit=100)
    new_plan = SimpleNamespace(tree=new_tree, previous_cost=100.0, cost=50.0)
    ex = PhysicalExecutor(
        adapter_factory=lambda sid: None, budget=budget, limit=10,
        replan_fn=lambda actuals: new_plan,
    )
    ex.trace.per_source_rows = {"a": 1000}
    ex._streaming = StreamingBudget(max_rows=100, max_bytes=10_000, max_vertices=1_000)
    monkeypatch.setattr(
        ex, "_eval",
        lambda *a, **kw: (_ for _ in ()).throw(CancelledError("cancelled")),
    )
    with pytest.raises(CancelledError):
        ex._maybe_bushy_replan(tree, [], 0)


def test_cancel_token_zero_deadline_is_expired():
    from app.services.data_fabric.errors import QueryBudgetExceededError
    from app.services.data_fabric.query.federated.physical import CancelToken

    with pytest.raises(QueryBudgetExceededError):
        CancelToken(deadline_s=0).check()
    CancelToken(deadline_s=None).check()


# ── OGC tiles proxy negative cache ────────────────────────────────────────


def test_ogc_tiles_proxy_caches_negative_result():
    from app.services.data_fabric.ogc_tiles_proxy import OgcTilesProxyService

    calls = {"n": 0}

    class _Adapter:
        def fetch_tile(self, dataset_id, z, x, y):
            calls["n"] += 1
            return None

    proxy = OgcTilesProxyService()
    proxy._resolve_governed_adapter = lambda ds_model: _Adapter()
    item = SimpleNamespace(id="ogc-tiles-neg-test", name="tms", fingerprint="fp-neg")
    ds = SimpleNamespace(org_id=None, owner_id=None)
    r1 = asyncio.run(proxy.serve_tile(item, ds, 1, 0, 0))
    r2 = asyncio.run(proxy.serve_tile(item, ds, 1, 0, 0))
    assert r1.body is None and r2.body is None
    assert calls["n"] == 1
