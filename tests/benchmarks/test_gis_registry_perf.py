"""GIS registry 编排性能不变量（§31）。

目标不是微基准，而是锁『不退化成 全 registry × 全 tool × 全 template
扫描』：capability lookup / algorithm resolution / map model lookup /
template selection 均为 O(1)/有界操作，中位数在毫秒级以下量级。
"""

import statistics

from app.services.gis_harness.intent import resolve_map_request_intent


def _median_ms(fn, n=50):
    import time
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


class TestRegistryLookupPerf:
    def test_capability_lookup_1000(self):
        from app.lib.gis.capability_registry import get_capability_registry
        reg = get_capability_registry()
        med = _median_ms(lambda: [reg.get("grid_binning") for _ in range(1000)])
        assert med < 50.0, f"capability lookup too slow: {med:.3f}ms/1000"

    def test_algorithm_resolution_1000(self):
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver
        resolver = get_algorithm_resolver()
        tools = {"query_local_poi", "h3_binning", "fishnet_grid", "heatmap_data",
                 "spatial_aggregate", "kde_contours"}
        med = _median_ms(
            lambda: [resolver.resolve("grid_binning", available_tools=tools)
                     for _ in range(1000)])
        assert med < 100.0, f"algorithm resolution too slow: {med:.3f}ms/1000"

    def test_map_model_lookup_1000(self):
        from app.lib.cartography.model_library import get_map_model_registry
        models = get_map_model_registry()
        med = _median_ms(
            lambda: [models.resolve("density_overview") for _ in range(1000)])
        assert med < 50.0, f"map model lookup too slow: {med:.3f}ms/1000"

    def test_template_selection_100(self):
        from app.services.gis_harness.template_selector import TemplateSelector
        intent = resolve_map_request_intent("成都小学的分布情况")
        selector = TemplateSelector()
        med = _median_ms(
            lambda: [selector.select_product(
                intent=intent, recipe_id="poi_distribution_overview")
                for _ in range(100)])
        assert med < 100.0, f"template selection too slow: {med:.3f}ms/100"

    def test_planner_plan_from_intent_100(self):
        from app.services.gis_harness.planner import MapProductPlanner
        intent = resolve_map_request_intent("成都小学的分布情况")
        planner = MapProductPlanner()
        med = _median_ms(
            lambda: [planner.plan_from_intent(intent) for _ in range(100)])
        assert med < 200.0, f"plan_from_intent too slow: {med:.3f}ms/100"

    def test_no_full_geojson_scan_in_resolution(self):
        """resolver 只读 profile 摘要字段，不触碰大 payload。"""
        from app.lib.gis.algorithm_resolver import get_algorithm_resolver
        big = {
            "geometryTypes": ["Point"], "featureCount": 200000,
            "fields": {f"f{i}": {"type": "number"} for i in range(60)},
            "features": ["poison"] * 3,  # 若有全量扫描会在这里炸
        }
        res = get_algorithm_resolver().resolve(
            "poi_query", profile=big, available_tools={"query_local_poi"})
        assert res.status == "resolved"
