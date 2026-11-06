"""Real behavioral counterexamples for the medium data/scientific audit."""
from types import SimpleNamespace

import networkx as nx
import pytest


def feature(value=1, **properties):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116, 39]},
            "properties": {"v": value, **properties}}


def test_nonglobal_and_embedded_private_ips_are_blocked():
    from app.services.data_fabric.security import _is_blocked_ip

    for value in ["100.64.0.1", "198.18.0.1", "224.0.0.1", "240.0.0.1", "64:ff9b::a00:1", "2002:a00:1::1"]:
        assert _is_blocked_ip(value), value
    assert not _is_blocked_ip("93.184.216.34")


def test_local_empty_roots_deny_and_server_budget_is_clamped(tmp_path):
    from app.services.data_fabric.security import resolve_safe_local_path, DataFabricSecurityError
    from app.services.data_fabric.query.normalize import normalize_query_spec
    from app.schemas.data_fabric_schema import QuerySpec

    path = tmp_path / "data.txt"
    path.write_text("x")
    with pytest.raises(DataFabricSecurityError, match="nonempty"):
        resolve_safe_local_path(str(path), [])
    spec = normalize_query_spec(QuerySpec(max_bytes=2**60, max_vertices=2**60))
    assert spec.execution.max_bytes == 512 * 1024 * 1024
    assert spec.execution.max_vertices == 50_000_000


def test_in_parser_quotes_trailing_garbage_and_case():
    from app.services.data_fabric.query.normalize import parse_legacy_where
    from app.services.data_fabric.query.predicates import evaluate_predicate
    from app.services.data_fabric.errors import InvalidQueryError

    predicate = parse_legacy_where("a IN ('x)', 'y') and b=2")
    assert evaluate_predicate(predicate, {"a": "x)", "b": 2})
    with pytest.raises(InvalidQueryError):
        parse_legacy_where("a IN (1) ignored")
    assert evaluate_predicate(parse_legacy_where("a NOT IN (1)"), {"a": 2})


def test_nested_distinct_and_group_values_and_finite_aggregates():
    from app.services.data_fabric.query.execution import compute_aggregates

    rows = [{"g": [1], "v": {"tags": ["a"]}, "x": "nan"},
            {"g": [1], "v": {"tags": ["a"]}, "x": 3}]
    result = compute_aggregates(rows, [{"func": "distinct_count", "field": "v"}, {"func": "sum", "field": "x"}], ["g"])
    assert result == [{"g": [1], "distinct_count_v": 1, "sum_x": 3}]


def test_missing_rule_conditions_and_unknown_goal_directions_fail_honestly():
    from app.services.spatial_decision.rule_pack import DomainRulePackRegistry
    from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine
    from app.services.spatial_decision.baseline_resolver import BaselineResolver

    assert not DomainRulePackRegistry()._evaluate_applicability({"growth_pct": {"min": 2}}, {})
    goals = ScenarioComparisonEngine()._infer_optimization_goals({"noise_level": {}, "subway_access_min": {}, "pop_density": {}})
    assert goals == {"noise_level": "minimize", "subway_access_min": "minimize"}
    assert BaselineResolver()._aggregate_spatial_features([feature(99)], SimpleNamespace(geometry={"type": "Bogus"})) == {}


def test_sensitivity_reuses_method_and_penalty_scores():
    from app.services.spatial_decision.sensitivity import analyze_weight_sensitivity

    result = analyze_weight_sensitivity({"c": {"a": 1, "b": 0}}, {"c": 1}, {"a": True, "b": True},
        n_perturbations=10, score_fn=lambda weights: {"a": 0, "b": 1})
    assert result.rank_stability["b"] == 100


def test_repair_preserves_bad_features_identifiers_and_finds_planned_outlier():
    from app.services.spatial_repair_pipeline import SpatialRepairPipeline

    bad = {"type": "Feature", "geometry": {"type": "Bogus"}, "properties": {}}
    fc = {"type": "FeatureCollection", "features": [bad, "malformed"]}
    cleaned, _, evidence, _ = SpatialRepairPipeline._repair_impl(fc, ops=["make_valid"])
    assert cleaned["features"] == fc["features"]
    assert evidence[0]["failed_count"] == 2
    fc = {"type": "FeatureCollection", "features": [feature(1, zip="01234", idc="110101199003071234") for _ in range(60)] + [feature(1_000_000)]}
    cleaned, _, evidence, _ = SpatialRepairPipeline._repair_impl(fc, ops=["attribute_type_normalization", "drop_outliers_or_flag"],
        op_params={"drop_outliers_or_flag": {"fields": [{"field": "v", "suggested_clip": [0, 10]}]}})
    assert cleaned["features"][0]["properties"]["zip"] == "01234"
    assert cleaned["features"][0]["properties"]["idc"] == "110101199003071234"
    assert any(row["op"] == "drop_outliers_or_flag" and row["features_affected"] == 1 for row in evidence)


def test_graph_cache_observes_mutation_speed_strings_and_grade_separation():
    from app.services.network.graph_builder import NetworkGraphBuilder

    roads = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[116, 39], [116.01, 39.01]]}, "properties": {"speed_kmh": "20"}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[116, 39.01], [116.01, 39]]}, "properties": {"bridge": "yes", "layer": "1"}},
    ]}
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(roads)
    assert nx.number_weakly_connected_components(graph) == 2
    roads["features"][0]["properties"]["speed_kmh"] = "30"
    graph2, dataset2 = builder.build_graph(roads)
    assert graph2 is not graph and dataset2.dataset_id != dataset.dataset_id


@pytest.mark.parametrize("source_type,uri", [("geoparquet", "s3://bucket/object.parquet"), ("geoparquet", "gs://bucket/object.parquet"), ("s3", "s3://bucket")])
def test_untrusted_provider_sdk_never_invoked(monkeypatch, source_type, uri):
    import sys
    from app.schemas.data_fabric_schema import ConnectionProfile
    from app.services.data_fabric.adapters.geoparquet_adapter import GeoParquetAdapter
    from app.services.data_fabric.adapters.s3_storage_seam import S3ObjectStorageSeam
    from app.services.data_fabric.errors import SecurityBlockedError

    def forbidden(*args, **kwargs):
        pytest.fail("untrusted provider SDK was invoked")
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=forbidden))
    profile = ConnectionProfile(source_type=source_type, url=uri, allow_private=False)
    if source_type == "s3":
        adapter = S3ObjectStorageSeam(profile)
        with pytest.raises(SecurityBlockedError, match="guarded HTTPS"):
            adapter._s3_client()
    else:
        adapter = GeoParquetAdapter(profile)
        with pytest.raises(SecurityBlockedError, match="guarded HTTPS"):
            adapter._open_source()


def test_node_result_measure_accounts_for_large_late_geometry():
    from app.services.geocompute.executor import NodeResultStore
    from app.services.geocompute.cluster.object_cache import PayloadCache

    payload = {"features": [feature()] * 64 + [feature(text="x" * 1_000_000)]}
    assert NodeResultStore._measure(payload) > 1_000_000
    assert PayloadCache._measure(payload) > 1_000_000


def test_incremental_numeric_moments_ignore_null_and_text_and_merge_h3():
    import h3
    from app.services.data_profile.incremental import new_state, update_state, merge_states, state_summary
    from app.services.data_profile.distribution import spatial_distribution

    first = update_state(new_state(resolution=8), [feature(0), feature(None), feature('x')])
    second = update_state(new_state(resolution=6), [feature(4)])
    summary = state_summary(merge_states(first, second))
    assert summary['fields']['v']['mean'] == 2
    assert summary['fields']['v']['std'] == 2
    merged = merge_states(first, second)
    assert all(h3.get_resolution(cell) == 6 for cell in merged['h3'])
    assert sum(merged['h3'].values()) == 4
    assert not spatial_distribution([feature()], crs='EPSG:3857')['available']
    assert not update_state(new_state(), [feature()], crs='EPSG:3857')['h3']


def test_guardrails_generic_codes_are_advisory_and_point_work_is_bounded(monkeypatch):
    from app.services.spatial_guardrails.guardrail_middleware import SpatialGuardrails
    import app.services.spatial_guardrails.guardrail_middleware as module

    gateway = SpatialGuardrails()
    assert gateway.check_tool_args('demo', {'city_code': 'NYC'}).passed
    assert gateway.check_tool_args('demo', {'adcode': '110101001001'}).passed
    assert not gateway.check_tool_args('demo', {'adcode': 'invalid'}).passed
    calls = []
    original = module.detect_pair
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'detect_pair', counted)
    verdict = gateway.check_tool_args('demo', {'type': 'FeatureCollection', 'features': [feature()] * 2000})
    assert len(calls) <= 500
    assert len(verdict.issues) <= 200
    assert any(issue.code == 'GUARDRAIL_SCAN_TRUNCATED' for issue in verdict.issues)


def test_parallel_edges_minimum_cost_and_reverse_dataset_roundtrip():
    from app.services.network.graph_builder import NetworkGraphBuilder
    roads = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': [[116,39],[116.01,39]]}, 'properties': {'oneway': '-1', 'speed_kmh': speed}} for speed in [20, 40]]}
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(roads)
    assert len(dataset.edges) == 1
    edge = dataset.edges[0]
    assert edge.speed_kmh == 40
    assert graph.nodes[edge.u]['x'] > graph.nodes[edge.v]['x']
    _, restored = builder.build_graph(dataset)
    assert len(restored.edges) == 1
    assert restored.edges[0].geometry['coordinates'][0][0] > restored.edges[0].geometry['coordinates'][-1][0]


def test_eigenvector_periodic_graph_converges_and_turn_labels_have_correct_handedness():
    from app.services.network.centrality import eigenvector_centrality
    from app.services.network.routing import NetworkRoutingService
    graph = nx.DiGraph([(0,1),(1,0),(1,2),(2,1)])
    scores, meta = eigenvector_centrality(graph)
    assert meta['converged'] and scores[1] > scores[0]
    graph = nx.DiGraph()
    graph.add_node('a', x=116, y=39)
    graph.add_node('b', x=116.01, y=39)
    graph.add_node('c', x=116.01, y=39.01)
    assert NetworkRoutingService()._calculate_turn_type(graph, 'a','b','c') == 'Turn left'


def test_milp_solver_failure_is_typed(monkeypatch):
    from app.services.network import allocation
    from app.lib.gis.scientific_errors import UnsupportedMethod
    monkeypatch.setattr(allocation, 'milp', lambda **kwargs: SimpleNamespace(success=False, x=None))
    with pytest.raises(UnsupportedMethod, match='optimal'):
        allocation.solve_p_median_milp([[1.]], [1.], 1)


def test_temporal_local_day_aliases_partial_date_and_order():
    from app.services.temporal.aggregation import TemporalAggregationEngine
    from app.services.temporal.filter import TemporalFilterEngine
    from app.services.temporal.trend import TemporalTrendEngine, seasonal_decompose_narrated
    from app.services.temporal.raster import TemporalRasterEngine

    rows = [{'t': '2026-01-02T00:30:00+08:00', 'v': 2}, {'t': '2026-01-02T23:30:00+08:00', 'v': 4}]
    buckets = TemporalAggregationEngine().aggregate(rows, time_field='t', metrics=['avg','std'], target_fields=['v'])
    assert len(buckets) == 1 and buckets[0]['period'] == '2026-01-02'
    assert buckets[0]['v_mean'] == 3
    selected = TemporalFilterEngine().filter_dataset([{'t': '2026-01-02T18:00:00Z'}, {'t': '2026-01-03T00:00:00Z'}], time_field='t', start='2026-01-02', end='2026-01-02')
    assert len(selected) == 1
    analyzer = TemporalTrendEngine()
    trend = analyzer.analyze_trend([0.101, 0.1, 0.1005], timestamps=['2026-01-01','2024-01-01','2025-01-01'])
    assert trend.direction == 'increasing' and trend.values == [0.1,0.1005,0.101]
    with pytest.raises(ValueError, match='every phase'):
        seasonal_decompose_narrated([1, float('nan'), 2, 3, 4, 5], period=2)
    raster = TemporalRasterEngine()
    slices = raster.select_time_slice([{'timestamp':'2025-01-01'}, {}, {'timestamp':'invalid'}, {'timestamp':'2024-01-01'}], '2024-01-01', '2026-01-01')
    assert [item['timestamp'] for item in slices] == ['2024-01-01','2025-01-01']
    assert raster._empty_difference()['status'] == 'unavailable'
    assert raster._empty_difference()['mean_difference'] is None


def test_raster_reader_lifetimes_do_not_retain_thread_environment(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import numpy as np
    import rasterio
    from affine import Affine
    from app.lib.geo_raster.reader import RasterReader
    path = tmp_path / 'small.tif'
    with rasterio.open(path, 'w', driver='GTiff', width=4, height=4, count=1, dtype='uint8', crs='EPSG:4326', transform=Affine(1,0,100,0,-1,40)) as ds:
        ds.write(np.ones((1,4,4), dtype='uint8'))
    first = RasterReader.open(str(path))
    second = RasterReader.open(str(path))
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(first.close).result()
        assert executor.submit(second.read_mask).result().shape == (4,4)
        executor.submit(second.close).result()
    assert not rasterio.env.hasenv()


def test_welford_profile_keeps_small_variance_at_large_offsets_and_prime_meridian_geometry():
    from app.services.data_profile.incremental import new_state, update_state, merge_states, state_summary
    from app.lib.gis.longitude import normalize_geojson_geometry
    from shapely.geometry import shape
    left = update_state(new_state(), [feature(1e12-1), feature(1e12)])
    right = update_state(new_state(), [feature(1e12+1)])
    stats = state_summary(merge_states(left, right))['fields']['v']
    assert stats['std'] == pytest.approx((2/3)**0.5, abs=1e-6)
    polygon = {'type':'Polygon','coordinates':[[[359,0],[361,0],[361,2],[359,2],[359,0]]]}
    result = normalize_geojson_geometry(polygon)
    assert shape(result).area == 4 and shape(result).bounds == (-1,0,1,2)
    collection = normalize_geojson_geometry({'type':'GeometryCollection','geometries':[polygon]})
    assert shape(collection).area == 4


def test_reuse_paths_without_authorized_roots_do_not_touch_files(monkeypatch):
    from app.lib.gis import analysis_reuse
    monkeypatch.setattr(analysis_reuse, '_raster_path_fingerprint', lambda path: pytest.fail('unauthorized file was inspected'))
    assert analysis_reuse.snapshot_raster_fingerprints({'source': '/etc/private.tif'}, allowed_roots=[]) == {'/etc/private.tif':'unverifiable'}
    assert analysis_reuse.snapshot_raster_fingerprints({'source': '/etc/private.tif'}, allowed_roots=['/data/session']) == {'/etc/private.tif':'unverifiable'}


def test_coverage_geotiff_preserves_integer_precision(tmp_path):
    import numpy as np
    import rasterio
    from affine import Affine
    from app.services.data_fabric.coverage_read import _write_gtiff
    path = tmp_path / 'int.tif'
    values = np.full((1,16,16), 16777217, dtype=np.int32)
    _write_gtiff(values, Affine(1,0,100,0,-1,40), 'EPSG:4326', None, path)
    with rasterio.open(path) as dataset:
        assert dataset.read(1)[0,0] == 16777217
        assert dataset.dtypes[0] == 'int32'


def test_quality_crs_inference_and_total_issue_budget():
    from app.services.spatial_quality_gate import _resolve_projected_zone
    from app.services.spatial_quality_service import SpatialQualityEngine
    code, _, _ = _resolve_projected_zone('utm_or_gk', 114, 30, '')
    assert code == 'EPSG:4547'
    projected = {'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[12900000,4800000]},'properties':{}}]}
    report = SpatialQualityEngine.audit_dataset(projected, crs=None)
    assert not any(issue.code == 'IMPOSSIBLE_LAT_LON' for issue in report.issues)
    assert report.overall_status == 'warning'
    bad = {'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[200+i,100]},'properties':{}} for i in range(260)]}
    report = SpatialQualityEngine.audit_dataset(bad, crs='EPSG:4326')
    assert len(report.issues) <= 2000 and report.truncated
    assert report.truncated_count > 0 and report.issue_summary['blocking'] >= 260


def test_routing_overlay_does_not_copy_or_mutate_base(monkeypatch):
    from app.services.network.routing_overlay import RoutingGraphOverlay
    graph = nx.DiGraph()
    graph.add_edge('a','b', cost=1)
    graph.add_edge('b','c', cost=1)
    monkeypatch.setattr(graph, 'copy', lambda *args, **kwargs: pytest.fail('full graph copied'))
    overlay = RoutingGraphOverlay(graph)
    overlay.remove_edge('a','b')
    overlay.add_edge('a','x', cost=.25)
    overlay.add_edge('x','b', cost=.75)
    assert nx.shortest_path(overlay, 'a','c', weight='cost') == ['a','x','b','c']
    assert graph.has_edge('a','b') and not graph.has_node('x')
    assert nx.shortest_path(overlay.reverse(copy=False), 'c','a', weight='cost') == ['c','b','x','a']


def test_cube_source_fingerprint_observes_middle_second_band_change(tmp_path):
    import os
    import numpy as np
    import rasterio
    from affine import Affine
    from app.services.lakehouse.cube_service import _raster_fingerprint
    path = tmp_path / 'bands.tif'
    with rasterio.open(path, 'w', driver='GTiff', width=128, height=128, count=2, dtype='uint8', crs='EPSG:4326', transform=Affine(1,0,100,0,-1,40)) as ds:
        ds.write(np.ones((2,128,128),dtype='uint8'))
    previous_stat = path.stat()
    first = _raster_fingerprint(path)
    with rasterio.open(path, 'r+') as ds:
        ds.write(np.array([[2]],dtype='uint8'), 2, window=rasterio.windows.Window(64,64,1,1))
    os.utime(path, ns=(previous_stat.st_atime_ns,previous_stat.st_mtime_ns))
    assert _raster_fingerprint(path) != first


def test_admin_names_prefer_full_prefecture_and_keep_long_autonomous_stem():
    from app.services.spatial_guardrails.admin_division_verifier import resolve_name

    assert resolve_name("吉林市") == "220200"
    assert resolve_name("吉林省") == "220000"
    assert resolve_name("博尔塔拉") == "652700"
    assert resolve_name("克孜勒苏") == "653000"
    assert resolve_name("鄂") is None


def test_redline_invalid_configuration_falls_back_and_inline_json_works(tmp_path):
    from app.services.spatial_guardrails.redlines import RedlineRegistry

    assert RedlineRegistry.from_json(str(tmp_path / "missing.json")).zones == []
    assert RedlineRegistry.from_json('{"zones": "bad"}').zones == []
    registry = RedlineRegistry.from_json(
        '{"zones":[{"name":"z","ring":[[0,0],[1,0],[0,1]]}]}'
    )
    assert len(registry.zones) == 1


def test_longitude_envelope_distinguishes_global_and_dateline_samples():
    from app.lib.gis.longitude import minimal_longitude_interval
    from app.lib.geo_analysis.interpolation import _target_cells_for_samples
    from app.lib.gis.scientific_errors import UnsupportedMethod
    import numpy as np
    import pytest

    assert minimal_longitude_interval([-170, 0, 170]) == (-170.0, 170.0)
    assert minimal_longitude_interval([-179, -170, 170, 179]) == (170.0, 190.0)
    assert minimal_longitude_interval([-1, 0, 1]) == (-1.0, 1.0)
    with pytest.raises(UnsupportedMethod, match="hemisphere"):
        _target_cells_for_samples(np.array([[-170, 0], [0, 0], [170, 0]]), 2)


def test_spatial_baseline_extensive_sum_and_boolean_omission():
    from app.services.spatial_decision.baseline_resolver import BaselineResolver
    target = SimpleNamespace(geometry={"type": "Polygon", "coordinates": [[[115, 38], [117, 38], [117, 40], [115, 40], [115, 38]]]})
    result = BaselineResolver()._aggregate_spatial_features(
        [feature(population=10, area_km2=2, price=100, flag=True), feature(population=30, area_km2=3, price=200, flag=False)], target
    )
    assert result["population"] == 40 and result["area_km2"] == 5
    assert result["price"] == 150 and "flag" not in result


def test_decision_numeric_constraint_bad_attribute_fails_and_equality_positive():
    from app.services.spatial_decision.constraints import evaluate_alternative_constraints, evaluate_numeric_constraint
    from app.services.spatial_decision.models_v3 import Constraint, Alternative
    constraint = Constraint(id="c", name="eq", metric_key="v", operator="==", threshold=3)
    assert evaluate_numeric_constraint("a", 3, constraint).margin > 0
    alternative = Alternative(id="a", name="a", attributes={"v": "N/A"})
    feasible, failures, _ = evaluate_alternative_constraints(alternative, [constraint], {})
    assert not feasible and len(failures) == 1


def test_impact_collection_union_and_empirical_missing_is_not_fake_zero():
    from app.services.spatial_decision.impact_engine import SpatialImpactEngine
    from app.services.spatial_decision.models_v3 import UncertainParameter
    from app.services.spatial_decision.uncertainty import sample_parameter_distribution
    import numpy as np
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    multi = {"type": "MultiPolygon", "coordinates": [polygon["coordinates"]]}
    result = SpatialImpactEngine().extract_geometry({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": multi}, {"type": "Feature", "geometry": {"type": "Point", "coordinates": [3, 3]}}]})
    assert result.geom_type == "GeometryCollection"
    parameter = UncertainParameter(param_id="p", name="p", distribution="empirical", params={})
    with pytest.raises(ValueError, match="nonempty"):
        sample_parameter_distribution(parameter, 5, np.random.default_rng(1))
    parameter.params = {"values": [2, 4]}
    assert set(sample_parameter_distribution(parameter, 10, np.random.default_rng(1))) <= {2, 4}


def test_quality_checks_raw_rings_and_preserves_real_origin_centered_polygon():
    from app.services.spatial_quality_service import SpatialQualityEngine
    from app.services.spatial_repair_pipeline import SpatialRepairPipeline
    polygon = {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]]}}
    fc = {"type": "FeatureCollection", "features": [polygon]}
    report = SpatialQualityEngine.audit_dataset(fc)
    assert not any(issue.code == "NULL_ISLAND" for issue in report.issues)
    cleaned, _, _, _ = SpatialRepairPipeline._repair_impl(fc, ops=["remove_empty"], op_params={"remove_empty": {"drop_zero_coordinates": True}})
    assert len(cleaned["features"]) == 1
    polygon["geometry"]["coordinates"][0].pop()
    report = SpatialQualityEngine.audit_dataset(fc)
    assert any(issue.code == "RING_CHECK_FAILED" for issue in report.issues)


def test_temporal_first_last_sort_by_epoch_not_input_order():
    from app.services.temporal.aggregation import TemporalAggregationEngine
    out = TemporalAggregationEngine().aggregate([
        {"time": "2026-01-01T12:00:00Z", "v": 12},
        {"time": "2026-01-01T01:00:00Z", "v": 1},
    ], time_field="time", target_fields=["v"], metrics=["first", "last"])
    assert out[0]["metrics"]["v"]["first"] == 1
    assert out[0]["metrics"]["v"]["last"] == 12


async def test_comparison_rejects_duplicate_ids_before_overwrite():
    from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine
    result = SimpleNamespace(scenario=SimpleNamespace(scenario_id="same"))
    with pytest.raises(ValueError, match="unique"):
        await ScenarioComparisonEngine().compare_scenarios([result, result])


async def test_rule_evidence_propagates_explicit_tenant():
    from app.services.spatial_decision.rule_pack import build_evidence_chain
    from app.services.rag.engine import TenantContext
    seen = []
    class Knowledge:
        async def search(self, *, query, tenant, top_k):
            seen.append(tenant)
            return []
    tenant = TenantContext(user_id="owner", org_id="org")
    await build_evidence_chain("unknown", tenant=tenant, knowledge_engine=Knowledge())
    assert seen == [tenant]


def test_wrapped_bbox_and_nonpolygon_target_are_truthful():
    from app.services.spatial_decision.target_resolver import TargetAreaResolver
    result = TargetAreaResolver()._try_parse_bbox("170,-5,-170,5")
    assert result.geometry["type"] == "MultiPolygon"
    assert abs(result.center[0]) == 180


def test_epoch_t_alias_is_explicit_but_arbitrary_numeric_id_stays_ambiguous():
    from app.services.temporal.profiler import profile_field
    values = [1672531200000, 1672534800000, 1704067200000]
    assert profile_field("t", values).confidence_score >= 0.4
    assert profile_field("object_id", values).confidence_score < 0.4


def test_wms_public_query_encodes_layer_and_refuses_credential_stripped_urls(monkeypatch):
    from app.services.data_fabric.adapters.wms_wmts_adapter import WMSWMTSAdapter
    from app.services.data_fabric.errors import QueryUnsupportedError
    from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
    adapter = WMSWMTSAdapter(ConnectionProfile(source_type="wms", url="https://wms.example/service?map=base", allow_private=True))
    monkeypatch.setattr(adapter, "describe", lambda dataset: SimpleNamespace(metadata={"service_version": "1.3.0"}, bbox=[0, 0, 1, 1], srs="EPSG:4326"))
    result = adapter.query('layer&injected=yes', QuerySpec())
    from urllib.parse import parse_qs, urlsplit
    query = parse_qs(urlsplit(result.metadata["getmap_url"]).query)
    assert query["LAYERS"] == ['layer&injected=yes'] and "injected" not in query
    assert query["map"] == ["base"]
    adapter.profile.credentials = {"api_key": "private"}
    with pytest.raises(QueryUnsupportedError, match="governed proxy"):
        adapter.query("layer", QuerySpec())
