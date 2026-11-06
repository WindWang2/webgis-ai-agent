"""Behavior regressions for the November data/scientific correctness audit."""
from functools import partial
import socket
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.mark.parametrize("is_verified", [True, False])
def test_http_connection_pins_address_preserves_port_and_global_resolver(monkeypatch, is_verified):
    import urllib3.connection
    from app.services.data_fabric.security import _safe_pool_classes

    public_ip = "93.184.216.34"
    def resolve(host, port, *args):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (public_ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    seen = []
    sock = object()
    def connect(address, *args, **kwargs):
        assert socket.getaddrinfo is resolve
        seen.append(address)
        return sock

    monkeypatch.setattr(urllib3.connection.connection, "create_connection", connect)
    connection = _safe_pool_classes()["https"].ConnectionCls("public.example", port=8443)
    assert connection._new_conn() is sock
    assert seen == [(public_ip, 8443)]
    assert connection._dns_host == "public.example"
    assert socket.getaddrinfo is resolve
    tls_arguments = {}
    def wrap_tls(**kwargs):
        tls_arguments.update(kwargs)
        # Use urllib3's real return type: 2.8 unpacks the pair, while older
        # releases read its attributes. SimpleNamespace only supports the latter.
        return urllib3.connection._WrappedAndVerifiedSocket(socket=sock, is_verified=is_verified)

    monkeypatch.setattr(urllib3.connection, "_ssl_wrap_socket_and_match_hostname", wrap_tls)
    connection.connect()
    assert tls_arguments["server_hostname"] == "public.example"
    assert connection.sock is sock
    assert connection.is_verified is is_verified
    assert seen == [(public_ip, 8443), (public_ip, 8443)]
    assert connection._dns_host == "public.example"
    assert socket.getaddrinfo is resolve


def test_http_dns_failure_and_connect_time_rebinding_fail_closed(monkeypatch):
    from app.services.data_fabric.security import DataFabricSecurity, DataFabricSecurityError, _safe_pool_classes

    monkeypatch.setattr(DataFabricSecurity, "_resolve_all", staticmethod(lambda host: []))
    with pytest.raises(DataFabricSecurityError, match="unable to resolve"):
        DataFabricSecurity.validate_url("https://unresolved.example/data")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
    ])
    connection = _safe_pool_classes()["https"].ConnectionCls("rebound.example", port=443)
    with pytest.raises(DataFabricSecurityError, match="blocked DNS"):
        connection._new_conn()


def test_safe_http_transport_rejects_uninspectable_proxy_dns():
    from app.services.data_fabric.security import DataFabricSecurityError, SSRFSafeHTTPAdapter

    adapter = SSRFSafeHTTPAdapter()
    with pytest.raises(DataFabricSecurityError, match="proxy-side DNS"):
        adapter.send(SimpleNamespace(url="https://data.example/file"), proxies={"https": "http://proxy.example:8080"})


def test_geoparquet_http_never_delegates_redirects_to_unguarded_transport(monkeypatch):
    import fsspec
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter
    from app.services.data_fabric.security import DataFabricSecurity, safe_fsspec_http_client

    monkeypatch.setattr(DataFabricSecurity, "validate_url", lambda url, **kw: url)
    observed = {}
    handle = object()
    def open_file(url, mode, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(open=lambda: handle)

    monkeypatch.setattr(fsspec, "open", open_file)
    adapter = GeoParquetAdapter(ConnectionProfile(id="gp", source_type="geoparquet", url="https://data.example/x.parquet"))
    assert adapter._open_source() == (handle, handle)
    assert observed["allow_redirects"] is False
    assert observed["get_client"] is safe_fsspec_http_client


@pytest.mark.asyncio
async def test_fsspec_checks_dns_answers_at_connect(monkeypatch):
    import aiohttp
    from app.services.data_fabric.security import DataFabricSecurityError, safe_fsspec_http_client

    async def resolve(self, host, port=0, family=socket.AF_INET):
        return [{"hostname": host, "host": "127.0.0.1", "port": port,
                 "family": family, "proto": 0, "flags": 0}]

    monkeypatch.setattr(aiohttp.resolver.DefaultResolver, "resolve", resolve)
    async with await safe_fsspec_http_client() as client:
        with pytest.raises(DataFabricSecurityError):
            await client.connector._resolver.resolve("rebound.example", 443)


@pytest.mark.parametrize("scheme", ["postgresql", "postgres", "s3", "minio", "gs"])
def test_data_protocols_obey_air_gap_allowlist(scheme):
    from app.core.egress import EgressPolicy

    policy = EgressPolicy.from_params(profile="air_gapped", mode="allowlist", allow_private=False)
    assert not policy.decide(f"{scheme}://public.example/data").allowed
    assert not policy.decide(f"{scheme}:/missing-host").allowed


def test_chunk_identity_tracks_constants_closures_defaults_and_partial():
    from app.lib.geo_raster.chunk import fn_fingerprint

    def make(threshold):
        return lambda value: value > threshold

    assert fn_fingerprint(make(1)) != fn_fingerprint(make(2))
    assert fn_fingerprint(lambda value: value > 0.5) != fn_fingerprint(lambda value: value > 0.9)
    assert fn_fingerprint(partial(pow, exp=2)) != fn_fingerprint(partial(pow, exp=3))
    f = make(1)
    assert fn_fingerprint(f) == fn_fingerprint(f)


def test_pmtiles_runs_and_leaf_directory_ranges():
    from app.services.data_fabric.adapters.pmtiles_adapter import PMTilesAdapter

    entries = [(3, 100, 10, 4), (9, 200, 20, 0), (20, 300, 30, 1)]
    assert PMTilesAdapter._find_entry(entries, 2) is None
    assert PMTilesAdapter._find_entry(entries, 5) == entries[0]
    assert PMTilesAdapter._find_entry(entries, 7) is None
    assert PMTilesAdapter._find_entry(entries, 19) == entries[1]
    assert PMTilesAdapter._find_entry(entries, 21) is None


def test_scoped_raster_path_rejects_other_owner_symlink_and_vsi(tmp_path):
    from app.services.scoped_raster_paths import resolve_scoped_raster_path
    from app.services.data_fabric.security import DataFabricSecurityError

    alice, bob = tmp_path / "alice", tmp_path / "bob"
    alice.mkdir()
    bob.mkdir()
    owned, foreign = alice / "a.tif", bob / "b.tif"
    owned.touch()
    foreign.touch()
    (alice / "escape.tif").symlink_to(foreign)
    assert resolve_scoped_raster_path(str(owned), roots=[alice]) == str(owned)
    for path in (str(foreign), str(alice / "escape.tif"), "/vsicurl/https://data.example/a.tif"):
        with pytest.raises(DataFabricSecurityError):
            resolve_scoped_raster_path(path, roots=[alice])


def test_raster_math_accepts_numeric_ast_and_rejects_object_access():
    from app.lib.geo_analysis.raster_math import _validate_raster_expression

    _validate_raster_expression("where(A > 0, (A-B)/(A+B), 0)")
    for expression in ("A.mean()", "A[0]", "unknown(A)", "'not numeric'", "[A, B]"):
        with pytest.raises(ValueError):
            _validate_raster_expression(expression)


def test_inline_temporal_rasters_are_bounded_before_conversion(monkeypatch):
    import app.services.temporal.raster as raster

    monkeypatch.setattr(raster, "_MAX_INLINE_CELLS", 4)
    np.testing.assert_array_equal(raster._bounded_inline_array([[1, 2], [3, 4]]), [[1, 2], [3, 4]])
    with pytest.raises(ValueError, match="budget"):
        raster._bounded_inline_array([[1, 2, 3], [4, 5, 6]])


def test_coarse_landmask_and_long_lines_do_not_reject_valid_islands_or_od():
    from app.services.spatial_guardrails import SpatialGuardrails
    from app.services.spatial_guardrails.landmask_validator import validate_facility_point
    from app.services.spatial_guardrails.topology_checks import validate_linestring

    for lng, lat in [(112.33, 16.83), (127.68, 26.21), (144.79, 13.44)]:
        assert all(issue.mode.value != "BLOCK" for issue in validate_facility_point(lng, lat, "airport"))
    assert validate_linestring([[179.5, -17], [-179.5, -17]]) == []
    assert validate_linestring([[116.4, 39.9], [121.5, 31.2]])[0].mode.value == "WARN_DEGRADE"
    gateway = SpatialGuardrails()
    assert gateway.check_tool_args("zonal_stats", {"extent": [73, 18, 135, 54]}).passed
    assert not gateway.check_tool_args("external_fetch", {"bbox": [73, 18, 135, 54]}, network_fetch=True).passed
    nested = gateway.check_tool_args("tool", {"geometry": {"type": "Point", "coordinates": [116.4, 39.9]}})
    assert not any(issue.code == "GUARDRAIL_INTERNAL_ERROR" for issue in nested.issues)


def test_slanted_network_intersections_create_connected_junction():
    import networkx as nx
    from app.services.network.graph_builder import NetworkGraphBuilder

    lines = [[(116.1, 39.9), (116.4337, 39.9771)], [(116.2, 40.1), (116.3313, 39.8123)]]
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": line}}
        for line in lines
    ]}
    graph, _ = NetworkGraphBuilder().build_graph(fc, use_cache=False)
    assert len(graph) == 5
    assert graph.number_of_edges() == 8
    assert nx.is_weakly_connected(graph)


def test_single_feature_runs_spatial_audit():
    from app.services.spatial_quality_service import SpatialQualityEngine

    report = SpatialQualityEngine.audit_dataset({"type": "Feature", "properties": {}, "geometry": None})
    assert report.total_features == 1
    assert report.issues


def test_instance_polygon_georeferencing_includes_roi_transform():
    from affine import Affine
    from app.lib.modelops.stitching import _polygonize

    fc = _polygonize(np.array([[1, 1], [1, 1]], dtype="int32"),
                     transform=Affine(10, 0, 1000, 0, -10, 2000) * Affine.translation(3, 4),
                     crs="EPSG:3857")
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert min(x for x, y in ring) == 1030
    assert max(y for x, y in ring) == 1960
    assert fc["crs"]["properties"]["name"] == "EPSG:3857"


def test_flatgeobuf_filter_is_applied_before_page_and_count_uses_real_metadata(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import Point
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.flatgeobuf_adapter import FlatGeobufAdapter

    path = tmp_path / "rows.fgb"
    gpd.GeoDataFrame({"value": list(range(20)), "label": [str(i) for i in range(20)]}, geometry=[Point(x, 0) for x in range(20)], crs="EPSG:4326").to_file(path, driver="FlatGeobuf")
    adapter = FlatGeobufAdapter(ConnectionProfile(id="fgb", source_type="flatgeobuf", url=str(path)))
    monkeypatch.setattr(adapter, "_readable_path", lambda: str(path))
    assert adapter.describe(path.name).feature_count == 20
    result = adapter.query(path.name, QuerySpec(limit=2, where={"op": "ge", "field": "value", "value": 15}))
    assert len(result.features) == 2
    assert all(feature["properties"]["value"] >= 15 for feature in result.features)
    assert result.has_more
    projected = adapter.query(path.name, QuerySpec(limit=10, fields=["label"], where={"op": "ge", "field": "value", "value": 15}))
    assert len(projected.features) == 5
    assert all(set(feature["properties"]) == {"label"} for feature in projected.features)
    count = adapter.query(path.name, QuerySpec(aggregate=[{"func": "count"}]))
    assert count.data == [{"count": 20}]


def test_heat_matrix_svg_and_embedded_pdf_font_are_renderable():
    import base64
    from app.lib.cartography.svg_charts import render_chart_panel
    from app.services.publication_export import _deny_url_fetcher

    svg, status, _ = render_chart_panel(0, 0, {"type": "heat_matrix", "series": [
        {"name": "row", "data": [{"name": "a", "value": 1}, {"name": "b", "value": 2}]},
    ]})
    assert status == "drawn" and "<rect" in svg
    resource = _deny_url_fetcher("data:font/ttf;base64," + base64.b64encode(b"font-bytes").decode())
    assert resource["string"] == b"font-bytes"
    with pytest.raises(ValueError):
        _deny_url_fetcher("https://external.example/font.ttf")


def test_pubsub_client_has_no_read_timeout_and_does_not_replace_publisher(monkeypatch):
    import redis.asyncio as redis
    from app.core.config import settings
    from app.services.collab.bus import CollabBus

    monkeypatch.setattr(settings, "USE_REDIS", True)
    clients = []
    def make_client(url, **options):
        client = SimpleNamespace(options=options)
        clients.append(client)
        return client

    monkeypatch.setattr(redis, "from_url", make_client)
    bus = CollabBus()
    publisher = bus._redis_client()
    subscriber = bus._redis_subscriber()
    assert publisher is not subscriber
    assert publisher.options["socket_timeout"] == 2
    assert subscriber.options["socket_timeout"] is None
    assert subscriber.options["health_check_interval"] == 30
    assert bus._redis_client() is publisher


def test_collab_bus_rejects_nonfinite_wire_json():
    from app.services.collab.bus import CollabBus, bound_payload, build_envelope

    bounded = bound_payload(build_envelope("presence", "s", {"x": float("nan")}))
    assert bounded["truncated"] and bounded["data"] == {}
    bus = CollabBus()
    captured = []
    bus._dispatch_local = captured.append
    bus._apply_raw('{"v":1,"kind":"presence","sid":"s","data":{"x":NaN}}')
    assert not captured


def test_federated_build_side_paginates_past_adapter_page_cap_and_retains_attributes():
    from app.schemas.data_fabric_schema import QueryResult
    from app.services.data_fabric.query.federation import FederatedExecutor, FederatedQueryRequest

    class Adapter:
        def __init__(self):
            self.pages = []

        def query(self, dataset_id, spec):
            self.pages.append((dataset_id, spec.offset, spec.limit))
            rows = ([{"type": "Feature", "geometry": None, "properties": {"key": 14999, "left_value": 10}}]
                    if dataset_id == "left" else [
                        {"type": "Feature", "geometry": None, "properties": {"key": i, "right_value": i * 2}}
                        for i in range(15000)
                    ])
            page = rows[spec.offset:spec.offset + min(10000, spec.limit)]
            return QueryResult(dataset_id=dataset_id, features=page, total_count=len(page),
                               has_more=spec.offset + len(page) < len(rows))

        def server_spatial_join(self, *args, **kwargs):
            raise AssertionError("attribute joins must not use the spatial-count shortcut")

    adapter = Adapter()
    request = FederatedQueryRequest("same", "left", "same", "right", join_field_left="key", join_field_right="key")
    result = FederatedExecutor(lambda source: adapter).execute(request)
    assert result["row_count"] == 1
    assert result["rows"][0]["left_value"] == 10
    assert result["rows"][0]["__right__"]["right_value"] == 29998
    assert any(dataset == "right" and offset == 10000 for dataset, offset, limit in adapter.pages)


def test_topsis_missing_cost_never_becomes_ideal():
    from app.services.spatial_decision.mcda import MultiCriteriaDecisionEngine
    from app.services.spatial_decision.models_v3 import Criterion, CriterionDirection

    criterion = Criterion(id="cost", name="cost", direction=CriterionDirection.MINIMIZE)
    scores = MultiCriteriaDecisionEngine().evaluate_topsis(
        {"cost": {"cheap": 10.0, "expensive": 20.0, "missing": None}},
        [criterion], {"cost": 1.0}, {"cheap": True, "expensive": True, "missing": True},
    )
    assert scores["cheap"] > scores["missing"]
    assert scores["missing"] == 0.0


def test_result_cache_backend_io_does_not_hold_local_mutex(monkeypatch):
    from app.services.data_fabric.fabric.result_cache import FederatedResultCache

    cache = FederatedResultCache()
    def remote_get(key):
        acquired = cache._lock.acquire(blocking=False)
        assert acquired, "slow Redis reads must not block unrelated local cache work"
        cache._lock.release()
        return None

    monkeypatch.setattr(cache, "_backend_get", remote_get)
    assert cache.get("missing") is None


def test_sample_seed_and_output_budget_change_query_identity():
    from app.services.data_fabric.query.models import QuerySpecV2, OutputSpec, SampleSpec, query_fingerprint

    first = QuerySpecV2(sample=SampleSpec(size=5, seed=1))
    second = first.model_copy(update={"sample": SampleSpec(size=5, seed=2)})
    assert query_fingerprint(first) != query_fingerprint(second)
    third = first.model_copy(update={"output": OutputSpec(max_features=10)})
    assert query_fingerprint(first) != query_fingerprint(third)


def test_bloom_memory_cap_discloses_saturation_without_false_negatives():
    from app.services.data_fabric.query.federated.bloom import BloomFilter

    bloom = BloomFilter.with_capacity(2_000_000)
    assert bloom.saturated
    for key in range(10):
        bloom.add(key)
        assert key in bloom


def test_postgis_explicit_order_retains_unique_tiebreaker():
    from app.schemas.data_fabric_schema import QuerySpec
    from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter, _TableMeta
    from app.services.data_fabric.query.normalize import normalize_query_spec

    meta = _TableMeta()
    meta.field_names = ["value", "id"]
    meta.pk_col = "id"
    adapter = PostGISAdapter.__new__(PostGISAdapter)
    spec = normalize_query_spec(QuerySpec(order_by=[{"field": "value", "direction": "desc"}]))
    columns = adapter._order_columns(spec, meta)
    assert columns == [('"value" DESC', "value"), ('"id" DESC', "id")]


def test_ogc_collection_identifier_encodes_one_segment_and_rejects_dot_segments():
    from app.services.data_fabric.adapters.ogc_api_adapter import _quote_collection_id
    from app.services.data_fabric.errors import InvalidQueryError

    assert _quote_collection_id("a/b?x#frag") == "a%2Fb%3Fx%23frag"
    for value in ("", ".", ".."):
        with pytest.raises(InvalidQueryError):
            _quote_collection_id(value)


def test_stac_post_body_is_bounded_and_closed(monkeypatch):
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    from app.services.data_fabric.adapters.stac_adapter import STACAdapter
    from app.services.data_fabric.query.normalize import normalize_query_spec
    from app.services.data_fabric.security import DataFabricSecurity
    from app.services.data_fabric.errors import InvalidQueryError, QueryBudgetExceededError

    monkeypatch.setattr(DataFabricSecurity, "validate_url", lambda url, **kw: url)
    adapter = STACAdapter(ConnectionProfile(source_type="stac", url="https://catalog.example/stac"))
    seen = {}
    response = SimpleNamespace(status_code=200, headers={},
        iter_content=lambda **kw: iter([b"x" * 2048]), close=lambda: seen.update(closed=True))
    def post(url, **kw):
        seen.update(kw)
        return response
    adapter.session = SimpleNamespace(post=post)
    spec = normalize_query_spec(QuerySpec(max_bytes=1024))
    with pytest.raises(QueryBudgetExceededError):
        adapter._search_remote("ds", spec, SimpleNamespace(), 0)
    assert seen["stream"] and seen["closed"]
    with pytest.raises(InvalidQueryError, match="offset"):
        adapter._search_remote("ds", normalize_query_spec(QuerySpec(offset=1)), SimpleNamespace(), 0)


def test_coverage_cis_axes_and_real_tiff_read_budget():
    from app.services.data_fabric.adapters.ogc_coverages_adapter import OGCCoveragesAdapter
    from app.services.data_fabric.coverage_read import _parse_geotiff, CoverageReadError
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin

    axes = OGCCoveragesAdapter._axes_summary({"generalGrid": {"axis": [
        {"axisLabel": "x", "lowerBound": 0, "upperBound": 20, "resolution": 10},
        {"axisLabel": "y", "lowerBound": 20, "upperBound": 0, "resolution": -10},
    ]}})
    assert axes["x"]["count"] == axes["y"]["count"] == 3
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", width=2, height=2, count=2, dtype="uint8", crs="EPSG:4326", transform=from_origin(0, 2, 1, 1)) as ds:
            ds.write(np.ones((2, 2, 2), dtype="uint8"))
            ds.set_band_description(1, "red")
            ds.set_band_description(2, "nir")
        body = mem.read()
    with pytest.raises(CoverageReadError, match="pixel budget"):
        _parse_geotiff(body, bands=None, max_cells=4)
    arr, _, _, _, names, _ = _parse_geotiff(body, bands=["nir"], max_cells=4)
    assert arr.shape == (1, 2, 2) and names == ["nir"]


def test_fallback_keeps_valid_empty_answer_and_ignores_numeric_substrings(monkeypatch):
    from app.services.data_fabric import fallback as fb
    from app.services.data_fabric.circuit_breaker import CircuitBreakerRegistry

    monkeypatch.setattr(fb, "breaker_registry", CircuitBreakerRegistry())
    def run(source):
        if source == "primary":
            return [], {"empty_result": True}
        raise RuntimeError("unavailable")
    result = fb.execute_fallback_chain("primary", run, chain=[("backup", ["empty_result"], True)], max_attempts=1)
    assert result.ok and result.source_used == "primary" and result.fact.degraded
    assert fb.classify_failure(RuntimeError("limit 5000, port 5020")) == "other"


def test_fes20_wraps_filter_and_uses_value_reference_and_gml32():
    from app.services.data_fabric.adapters.wfs_adapter import WFSAdapter
    from app.services.data_fabric.query.compilers import compile_bbox_fes
    from app.schemas.data_fabric_schema import ConnectionProfile
    from xml.etree.ElementTree import fromstring

    adapter = WFSAdapter(ConnectionProfile(source_type="wfs", url="https://wfs.example", options={"version": "2.0.0"}))
    adapter.version = "2.0.0"
    xml = adapter._build_get_feature_xml("roads", 10, 0, compile_bbox_fes([1, 2, 3, 4]))
    root = fromstring(xml)
    assert root.find(".//{http://www.opengis.net/fes/2.0}Filter") is not None
    assert root.find(".//{http://www.opengis.net/gml/3.2}Envelope") is not None
    assert not root.findall(".//{http://www.opengis.net/ogc}PropertyName")


def test_spatial_index_query_failure_has_cumulative_fallback_budget():
    from app.services.data_fabric.query.federation import _LocalSpatialIndex
    from app.services.data_fabric.errors import QueryBudgetExceededError

    index = _LocalSpatialIndex([])
    index._geoms = [None] * 1001
    class Broken:
        def query(self, geom):
            raise RuntimeError("index failed")
    index._tree = Broken()
    for _ in range(999):
        assert len(index.candidates(None)) == 1001
    with pytest.raises(QueryBudgetExceededError):
        index.candidates(None)
