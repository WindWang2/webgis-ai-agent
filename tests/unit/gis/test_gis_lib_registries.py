"""app/lib/gis —— artifact / capability / algorithm 注册表与 resolver 单测。"""

import pytest
from pydantic import ValidationError


class TestArtifactTypes:
    def test_seed_types_cover_required_list(self):
        from app.lib.gis.artifacts import get_artifact_type_registry

        required = {
            "point_feature_set", "line_feature_set", "polygon_feature_set",
            "poi_feature_set", "admin_boundary_set", "admin_aggregate_table",
            "density_surface", "grid_aggregate", "hotspot_result",
            "proximity_zone", "service_area", "raster_surface",
            "terrain_surface", "remote_sensing_index", "change_set",
            "od_matrix", "network_graph",
        }
        assert required <= set(get_artifact_type_registry().all_ids)

    def test_lookup_o1_and_missing(self):
        from app.lib.gis.artifacts import get_artifact_type_registry

        reg = get_artifact_type_registry()
        assert reg.get("poi_feature_set") is not None
        assert reg.get("nope") is None
        assert not reg.has("nope")

    def test_duplicate_registration_rejected(self):
        from app.lib.gis.artifacts import (
            ArtifactTypeDescriptor, ArtifactTypeRegistry,
        )

        reg = ArtifactTypeRegistry()
        reg.register(ArtifactTypeDescriptor(
            id="x", name_zh="x", geometry_kind="point"))
        with pytest.raises(ValueError, match="duplicate"):
            reg.register(ArtifactTypeDescriptor(
                id="x", name_zh="x", geometry_kind="point"))

    def test_descriptor_rejects_unregistered_type(self):
        from app.lib.gis.artifacts import ArtifactDescriptor

        with pytest.raises(ValidationError):
            ArtifactDescriptor(artifact_type="not_a_type")

    def test_descriptor_bounds(self):
        from app.lib.gis.artifacts import ArtifactDescriptor

        with pytest.raises(ValidationError):
            ArtifactDescriptor(artifact_type="poi_feature_set",
                               fields=[f"f{i}" for i in range(65)])
        with pytest.raises(ValidationError):
            ArtifactDescriptor(artifact_type="poi_feature_set",
                               lineage=[f"l{i}" for i in range(17)])

    def test_artifact_from_profile_bounded_no_payload_copy(self):
        from app.lib.gis.artifacts import artifact_from_profile

        profile = {
            "geometryTypes": ["Point"], "featureCount": 1260,
            "fields": {f"attr_{i}": {"type": "number"} for i in range(100)},
            "crs": "EPSG:4326",
        }
        art = artifact_from_profile(
            "poi_feature_set", profile, data_ref="ref:geojson/x",
            source_capability="poi_query", producer_algorithm="poi.query.local",
        )
        assert art.artifact_type == "poi_feature_set"
        assert art.geometry_kind == "point"
        assert art.feature_count == 1260
        assert len(art.fields) == 64          # 截断到上界
        assert "geojson" not in art.model_dump()  # 不复制大 payload
        assert art.data_ref == "ref:geojson/x"

    def test_artifact_from_ref_descriptor(self):
        from app.lib.gis.artifacts import artifact_from_ref_descriptor

        art = artifact_from_ref_descriptor(
            "grid_aggregate",
            {"ref_id": "ref:geojson/g1", "feature_count": 40,
             "geometry_types": ["Polygon"], "filterable_fields": {"count": 1, "cell": 2}},
            source_capability="grid_binning",
        )
        assert art.geometry_kind == "polygon"
        assert art.feature_count == 40
        assert art.fields == ["count", "cell"]


class TestCapabilityRegistry:
    def test_seed_capabilities_cover_harness_vocabulary(self):
        from app.lib.gis.capability_registry import get_capability_registry

        required = {
            "poi_query", "admin_boundary_query", "admin_aggregation",
            "point_profile", "density_surface", "kde_density", "hotspot",
            "category_breakdown", "proximity_buffer", "service_area",
            "raster_source", "grid_binning", "analytical_density",
        }
        reg = get_capability_registry()
        assert required <= set(reg.all_ids)

    def test_purpose_templates_match_planner_vocabulary(self):
        from app.lib.gis.capability_registry import get_capability_registry

        reg = get_capability_registry()
        assert reg.purpose_for("poi_query", "小学") == "小学 要素获取"
        assert reg.purpose_for("poi_query") == "主体 要素获取"
        assert reg.purpose_for("admin_aggregation") == "按行政区聚合统计"
        assert reg.purpose_for("unknown_cap") == "unknown_cap"

    def test_duplicate_registration_rejected(self):
        from app.lib.gis.capability_registry import (
            CapabilityDescriptor, CapabilityRegistry,
        )

        reg = CapabilityRegistry()
        reg.register(CapabilityDescriptor(id="c", name="c"))
        with pytest.raises(ValueError, match="duplicate"):
            reg.register(CapabilityDescriptor(id="c", name="c"))

    def test_artifact_refs_valid(self):
        from app.lib.gis.capability_registry import get_capability_registry

        assert get_capability_registry().validate() == []

    def test_fallback_capability_dangling_detected(self):
        from app.lib.gis.capability_registry import (
            CapabilityDescriptor, CapabilityRegistry,
        )

        reg = CapabilityRegistry()
        reg.register(CapabilityDescriptor(
            id="c1", name="c1", fallback_capabilities=["ghost"]))
        issues = reg.validate()
        assert any("ghost" in i for i in issues)


class TestAlgorithmRegistry:
    def test_seed_algorithm_count_and_coverage(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        reg = get_algorithm_registry()
        # 每个种子 capability 至少一个 native 算法
        from app.lib.gis.capability_registry import get_capability_registry
        for cap in get_capability_registry().all_ids:
            assert reg.algorithms_for_capability(cap), f"{cap} has no algorithm"

    def test_capability_ordering_stable(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        reg = get_algorithm_registry()
        ids = [a.id for a in reg.algorithms_for_capability("grid_binning")]
        assert ids == ["spatial.grid.h3", "spatial.grid.fishnet"]
        ids2 = [a.id for a in reg.algorithms_for_capability("kde_density")]
        assert ids2 == ["spatial.kde.contours", "spatial.kde.surface"]

    def test_duplicate_registration_rejected(self):
        from app.lib.gis.algorithm_registry import (
            AlgorithmDescriptor, AlgorithmRegistry,
        )

        reg = AlgorithmRegistry()
        reg.register(AlgorithmDescriptor(id="a", name="a", capabilities=["poi_query"]))
        with pytest.raises(ValueError, match="duplicate"):
            reg.register(AlgorithmDescriptor(id="a", name="a", capabilities=["poi_query"]))

    def test_derived_capability_tool_map_matches_legacy_order(self):
        """派生视图与旧 CAPABILITY_TOOLS 手写表逐项一致（兼容承诺）。"""
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        legacy_expected = {
            "poi_query": ["query_local_poi", "search_poi", "query_osm_poi"],
            "admin_boundary_query": ["get_local_admin_boundary"],
            "admin_aggregation": ["spatial_aggregate"],
            "point_profile": ["spatial_stats", "webgis_source_profile"],
            "density_surface": ["heatmap_data"],
            "kde_density": ["kde_contours", "kde_surface"],
            "hotspot": ["hotspot_analysis"],
            "category_breakdown": ["spatial_stats"],
            "proximity_buffer": ["buffer_analysis"],
            "service_area": ["isochrone_analysis", "service_area_simple"],
            "raster_source": ["fetch_dem"],
            "grid_binning": ["h3_binning", "fishnet_grid"],
            "analytical_density": ["kde_contours", "heatmap_data", "spatial_aggregate"],
        }
        actual = get_algorithm_registry().capability_tool_map()
        for cap, tools in legacy_expected.items():
            assert actual.get(cap)[:len(tools)] == tools, f"{cap}: {actual.get(cap)} != {tools} (prefix)"

    def test_native_requires_tool_candidates(self):
        from app.lib.gis.algorithm_registry import (
            AlgorithmDescriptor, AlgorithmRegistry,
        )

        reg = AlgorithmRegistry()
        reg.register(AlgorithmDescriptor(
            id="bad", name="bad", capabilities=["poi_query"],
            runtime_status="native"))
        issues = reg.validate()
        assert any("no tool candidates" in i for i in issues)

    def test_tool_to_capability_derivation(self):
        """全量锁定派生 tool → capability 表（含首选工具归属规则）。

        首选工具（tool_candidates[0]）必须归给最早声明的专有算法：
        kde_contours → kde_density（而非把它列为首选候选的混合路径
        density.analytical.mixed）。"""
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        expected = {
            "query_local_poi": "poi_query",
            "search_poi": "poi_query",
            "query_osm_poi": "poi_query",
            "get_local_admin_boundary": "admin_boundary_query",
            "fetch_dem": "raster_source",
            "spatial_stats": "point_profile",
            "webgis_source_profile": "point_profile",
            "spatial_aggregate": "admin_aggregation",
            "h3_binning": "grid_binning",
            "fishnet_grid": "grid_binning",
            "heatmap_data": "density_surface",
            "kde_contours": "kde_density",
            "kde_surface": "kde_density",
            "hotspot_analysis": "hotspot",
            "buffer_analysis": "proximity_buffer",
            "isochrone_analysis": "service_area",
            "service_area_simple": "service_area",
        }
        mapping = get_algorithm_registry().tool_to_capability()
        for tool, cap in expected.items():
            assert mapping.get(tool) == cap, (
                f"{tool} -> {mapping.get(tool)} (expected {cap})")
        # new capabilities may add tools — not required to be absent


class TestAlgorithmResolver:
    def test_first_candidate_preferred_without_view(self):
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver

        r = get_algorithm_resolver()
        res = r.resolve("grid_binning")
        assert res.status == "resolved"
        assert res.algorithm == "spatial.grid.h3"
        assert res.tool == "h3_binning"

    def test_h3_unavailable_falls_back_to_fishnet(self):
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver

        r = get_algorithm_resolver()
        res = r.resolve("grid_binning", available_tools={"fishnet_grid"})
        assert res.status == "resolved"
        assert res.algorithm == "spatial.grid.fishnet"
        assert res.tool == "fishnet_grid"
        assert any("tool_unavailable:spatial.grid.h3" in x for x in res.rejected)

    def test_all_tools_missing_marks_unavailable(self):
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver

        res = get_algorithm_resolver().resolve(
            "grid_binning", available_tools=set())
        assert res.status == "unavailable"
        assert res.tool == ""

    def test_unknown_capability(self):
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver

        res = get_algorithm_resolver().resolve("no_such_capability")
        assert res.status == "unavailable"
        assert res.reason == "capability_not_registered"

    def test_geometry_mismatch_rejects(self):
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver

        res = get_algorithm_resolver().resolve(
            "kde_density",
            profile={"geometryTypes": ["Polygon"], "featureCount": 100},
            available_tools={"kde_contours"},
        )
        assert res.status == "unavailable"
        assert any("geometry_mismatch" in x for x in res.rejected)

    def test_min_features_rejects_small_sample(self):
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver

        res = get_algorithm_resolver().resolve(
            "density_surface",
            profile={"geometryTypes": ["Point"], "featureCount": 7},
            available_tools={"heatmap_data"},
        )
        assert res.status == "unavailable"
        assert res.rejected == [
            "insufficient_features:density.visual.heatmap:7<10"]

    def test_unknown_profile_facts_do_not_reject(self):
        """descriptor 派生画像 fields/几何未知 —— 未知 ≠ 不满足。"""
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver

        res = get_algorithm_resolver().resolve(
            "density_surface", profile={"featureCount": 100},
            available_tools={"heatmap_data"},
        )
        assert res.status == "resolved"
        assert "feature_count=100" in res.reason

    def test_required_fields_checked_when_known(self):
        from app.lib.gis.algorithm_registry import (
            AlgorithmDescriptor, AlgorithmRegistry,
        )
        from app.lib.gis.algorithm_resolver import AlgorithmResolver

        algos = AlgorithmRegistry()
        algos.register(AlgorithmDescriptor(
            id="x.field.required", name="x", capabilities=["point_profile"],
            required_fields=["magnitude"], tool_candidates=["spatial_stats"],
        ))
        resolver = AlgorithmResolver(algorithms=algos)
        ok = resolver.resolve(
            "point_profile",
            profile={"geometryTypes": ["Point"], "featureCount": 5,
                     "fields": {"magnitude": {"type": "number"}}},
        )
        assert ok.status == "resolved"
        missing = resolver.resolve(
            "point_profile",
            profile={"geometryTypes": ["Point"], "featureCount": 5,
                     "fields": {"other": {"type": "number"}}},
        )
        assert missing.status == "unavailable"
        assert any("missing_fields" in x for x in missing.rejected)

    def test_deterministic_repeated_resolution(self):
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver

        r = get_algorithm_resolver()
        a = r.resolve("poi_query", available_tools={"query_local_poi", "search_poi"})
        b = r.resolve("poi_query", available_tools={"query_local_poi", "search_poi"})
        assert a.model_dump() == b.model_dump()
