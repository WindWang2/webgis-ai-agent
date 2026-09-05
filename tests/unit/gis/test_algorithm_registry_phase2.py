"""Algorithm Registry Phase-2 —— 诚实化修复 + 网络族/时序族补齐的锁定测试.

覆盖（对照 .scratch/component-library-audit.md §1.13-1.15）：
- 修复项：temporal.trend 死代码 hack / network.shortest_path 错指工具 /
  geometry.spatial_join、stats.st_dbscan、remote.zonal_stats capability 错挂 /
  stats.h3_lisa 双 capability 过宽；
- 补齐项：网络族 7 工具、时序族 5 工具进入 registry（native + 真实工具）；
- resolver 行为：新 capability 可解析到真实路网工具；工具不可用时回退。
"""
import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def registry_names():
    from app.tools.registry import ToolRegistry
    from app.tools import init_tools

    reg = ToolRegistry()
    init_tools(reg)
    return set(reg.list_tools())


class TestAlgorithmHonestyFixes:
    def test_temporal_trend_is_native_with_proper_capability(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        algo = get_algorithm_registry().get("temporal.trend")
        assert algo is not None
        assert algo.capabilities == ["temporal_trend"]
        assert algo.runtime_status == "native"
        assert algo.tool_candidates == ["temporal_trend"]

    def test_shortest_path_points_to_real_network_tool(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        algo = get_algorithm_registry().get("network.shortest_path")
        assert algo.tool_candidates == ["network_shortest_path"]
        assert "isochrone_network" not in algo.tool_candidates

    def test_capability_rehangs_fixed(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        reg = get_algorithm_registry()
        assert reg.get("geometry.spatial_join").capabilities == ["spatial_join"]
        assert reg.get("stats.st_dbscan").capabilities == ["spatiotemporal_clustering"]
        assert reg.get("remote.zonal_stats").capabilities == ["zonal_statistics"]
        assert reg.get("network.closest_facility").capabilities == ["closest_facility"]
        # h3_lisa 双声明拆分：LISA 与 Gi* 各归其位
        assert reg.get("stats.h3_lisa").capabilities == ["local_morans_i"]
        assert reg.get("stats.h3_hotspot").capabilities == ["getis_ord_gi_star"]
        assert reg.get("stats.h3_hotspot").tool_candidates == ["h3_lisa"]

    def test_no_dead_code_capability_hacks(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        for algo in get_algorithm_registry().all_ids:
            algo = get_algorithm_registry().get(algo)
            assert algo is not None
            assert algo.capabilities, f"{algo.id} declares no capability"
            for cap in algo.capabilities:
                assert "interpolation" not in cap or algo.id.startswith(("interpolation",)), (
                    f"{algo.id} suspiciously re-hangs {cap}")


class TestNetworkAndTemporalFamilies:
    def test_network_family_registered_native(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        reg = get_algorithm_registry()
        expected = {
            "network.od_matrix": ["network_od_matrix", "distance_matrix_cn"],
            "network.service_area.multi": ["network_service_area"],
            "network.accessibility": ["network_accessibility"],
            "network.location_allocation": ["location_allocation"],
            "network.route_optimization": ["optimize_route"],
            "network.closest_facility": ["network_closest_facility", "nearest_facility"],
        }
        for algo_id, tools in expected.items():
            algo = reg.get(algo_id)
            assert algo is not None, algo_id
            assert algo.runtime_status == "native", algo_id
            assert algo.tool_candidates == tools, algo_id

    def test_temporal_family_registered_native(self):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        reg = get_algorithm_registry()
        expected = {
            "temporal.profile": "temporal_profile",
            "temporal.aggregate": "temporal_aggregate",
            "temporal.trend": "temporal_trend",
            "temporal.change": "temporal_change",
            "temporal.hotspot": "spatiotemporal_hotspot",
        }
        for algo_id, tool in expected.items():
            algo = reg.get(algo_id)
            assert algo is not None, algo_id
            assert algo.runtime_status == "native", algo_id
            assert tool in algo.tool_candidates, algo_id

    def test_new_tool_candidates_are_real_tools(self, registry_names):
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        reg = get_algorithm_registry()
        for algo_id in reg.all_ids:
            algo = reg.get(algo_id)
            assert algo is not None
            if algo.runtime_status != "native":
                continue
            for tool in algo.tool_candidates:
                assert tool in registry_names, f"{algo.id} → ghost tool {tool}"

    def test_new_capabilities_resolve_to_real_tools(self, registry_names):
        from app.lib.gis.algorithm_resolver import AlgorithmResolver

        resolver = AlgorithmResolver()
        for cap in (
            "shortest_path", "closest_facility", "od_matrix", "accessibility",
            "location_allocation", "route_optimization",
            "temporal_trend", "temporal_profile", "temporal_aggregate",
            "change_detection", "spatiotemporal_clustering",
            "spatial_join", "zonal_statistics",
        ):
            decision = resolver.resolve(cap, available_tools=registry_names)
            assert decision.status == "resolved", (
                f"{cap}: {decision.reason_code}")
            assert decision.tool in registry_names

    def test_service_area_default_resolution_unchanged(self, registry_names):
        """真路网工具是附加候选：默认解析仍是 isochrone_analysis（前缀兼容承诺）."""
        from app.lib.gis.algorithm_resolver import AlgorithmResolver

        decision = AlgorithmResolver().resolve(
            "service_area", available_tools=registry_names)
        assert decision.status == "resolved"
        assert decision.tool == "isochrone_analysis"

    def test_service_area_falls_back_to_real_network_tool(self, registry_names):
        """简化工具不可用时，真路网多断点工具作为后续候选接管."""
        from app.lib.gis.algorithm_resolver import AlgorithmResolver

        reduced = registry_names - {"isochrone_analysis", "service_area_simple"}
        decision = AlgorithmResolver().resolve("service_area", available_tools=reduced)
        assert decision.status == "resolved"
        assert decision.tool == "network_service_area"
