"""Golden benchmark scenarios (ADR-0092 B2).

Twelve representative scenarios covering the professional-GIS contract:
distribution, honest simplicity (no forced KDE), network service areas,
large-data bounds, raster goldens, repair semantics, and OD flow. OD cases
execute only once the flow tooling is registered — the runner reports them
as ``skipped`` otherwise (graceful degradation, never a false pass).
"""
from __future__ import annotations

from app.evaluation.case import GISBenchmarkCase, NumericAssertion, ScriptStep

GOLDEN_CASES: list[GISBenchmarkCase] = [
    # ── G1 成都小学空间分布（全链路分布产品）────────────────────────────
    GISBenchmarkCase(
        id="G1",
        name="成都小学分布：POI + 区县聚合 + 图表 + 地图产品",
        group="poi",
        query="分析成都各区小学的空间分布情况",
        expected_task="distribution_overview",
        expected_capabilities=["poi_query", "admin_aggregation"],
        # recipe poi_distribution_overview 的 optional_analysis —— 计划期
        # 一并解析、资格复检可禁用，不计入 precision 惩罚。
        optional_capabilities=["admin_boundary_query", "point_profile", "kde_density", "hotspot"],
        allowed_algorithms=["poi.query", "spatial.aggregate", "spatial.kde", "admin.boundary", "profile.spatial", "spatial.hotspot"],
        forbidden_algorithms=["remote.ndvi", "network.od_matrix"],
        expected_recipe="poi_distribution_overview",
        expected_product_facets=["chart"],
        max_tool_calls=8,
        fixture_aliases=["chengdu_schools"],
        script=[
            ScriptStep(tool="webgis_map_product", args={
                "primary_ref": "fixture:chengdu_schools",
                "query": "分析成都各区小学的空间分布情况",
            }),
            ScriptStep(tool="generate_chart", args={
                "chart_type": "bar", "title": "成都各区学校数量",
                # Live runs pass the aggregate result; the script pins a
                # deterministic aggregate so the panel binding is checkable.
                "data": [{"name": d, "value": v} for d, v in
                         [("锦江区", 10), ("青羊区", 10), ("金牛区", 10),
                          ("武侯区", 10), ("成华区", 10), ("龙泉驿区", 10)]],
                "attach_to_map": True,
            }),
        ],
        component_assertions=["title", "north_arrow", "scale_bar", "attribution"],
        numeric_assertions=[
            NumericAssertion(source="fixture", path="chengdu_schools.features",
                             agg="len", op="==", value=60, label="fixture size"),
        ],
    ),
    # ── G2 简单点图（不得无意义 KDE）────────────────────────────────────
    GISBenchmarkCase(
        id="G2",
        name="简单 POI 点图：simple_view 不强制 KDE",
        group="poi",
        query="在地图上显示成都的咖啡店",
        expected_task="simple_view",
        expected_capabilities=["poi_query"],
        optional_capabilities=["point_profile"],
        allowed_algorithms=["poi.query", "profile.spatial"],
        forbidden_algorithms=["spatial.kde", "density.analytical", "stats.h3_lisa"],
        max_tool_calls=3,
    ),
    # ── G3 学校服务区（真实 network capability）─────────────────────────
    GISBenchmarkCase(
        id="G3",
        name="学校服务区：network service-area capability",
        group="network",
        query="计算成都各小学步行15分钟的服务区范围",
        expected_capabilities=["service_area"],
        optional_capabilities=["poi_query", "admin_boundary_query"],
        allowed_algorithms=["network.service_area", "network.isochrone", "poi.query", "admin.boundary", "profile.spatial"],
        forbidden_algorithms=["spatial.buffer.proximity", "geometry.buffer"],
    ),
    # ── G4 超大 POI（150k 不得进 LLM 上下文）────────────────────────────
    GISBenchmarkCase(
        id="G4",
        name="150k POI：fetch-on-demand 描述符，不内联载荷",
        group="poi",
        query="在地图上展示成都全部小学",
        expected_task="simple_view",
        expected_capabilities=["poi_query"],
        optional_capabilities=["point_profile"],
        # 大数据契约：轻量展示任务不得因数据量大而升级为重分析计划。
        max_tool_calls=3,
        fixture_aliases=["chengdu_schools_large"],
        script=[
            ScriptStep(tool="webgis_map_product", args={
                "primary_ref": "fixture:chengdu_schools_large",
                "query": "在地图上展示成都全部小学",
            }),
        ],
        numeric_assertions=[
            # Large-data contract: the LLM-facing result stays bounded
            # (descriptor/projection only) and inlines zero features.
            NumericAssertion(source="step_result_bytes", step=0,
                             op="<=", value=20000, label="bounded LLM payload (bytes)"),
            NumericAssertion(source="step_result", step=0, path="features",
                             agg="len", op="<=", value=0, label="no inline features"),
            NumericAssertion(source="step_result", step=0, path="data",
                             agg="len", op="<=", value=0, label="no inline data payload"),
        ],
    ),
    # ── G5 NDVI（数值 golden，确定性 lib 级）────────────────────────────
    GISBenchmarkCase(
        id="G5",
        name="NDVI 数值 golden：remote.ndvi capability + 已知均值",
        group="raster",
        query="计算这片区域的 NDVI 植被指数",
        expected_task="vegetation_index",
        expected_capabilities=["ndvi"],
        optional_capabilities=["raster_source", "point_profile"],
        allowed_algorithms=["remote.ndvi", "raster.source", "profile.spatial"],
        forbidden_algorithms=["spatial.kde", "interpolation.idw"],
        fixture_aliases=["ndvi_pair"],
        plan_only=False,
        numeric_assertions=[
            NumericAssertion(source="quantity", quantity="ndvi_mean",
                             op="approx", value=0.25, tol=1e-9, label="ndvi golden mean"),
        ],
    ),
    # ── G6 双时相 raster change detection ───────────────────────────────
    GISBenchmarkCase(
        id="G6",
        name="双时相变化检测：change_detection capability",
        group="raster",
        query="对比这个区域两个月前后的遥感影像变化",
        expected_capabilities=["raster_change_detection"],
        optional_capabilities=["raster_source", "ndvi"],
        allowed_algorithms=["remote.change", "raster.source", "profile.spatial"],
        forbidden_algorithms=["spatial.kde", "interpolation.idw"],
    ),
    # ── G7 行政区统计图（分类、legend、title 完整）───────────────────────
    GISBenchmarkCase(
        id="G7",
        name="行政区统计：聚合 + 统计图 facet 完整",
        group="poi",
        query="统计成都各区小学数量并生成对比柱状图",
        expected_task="administrative_statistic",
        expected_capabilities=["poi_query", "admin_aggregation"],
        optional_capabilities=["admin_boundary_query"],
        expected_product_facets=["chart", "legend"],
        max_tool_calls=8,
    ),
    # ── G8 用户隐藏图层后 finalize（user-wins）──────────────────────────
    GISBenchmarkCase(
        id="G8",
        name="用户隐藏图层：repair 不得强制恢复",
        group="repair",
        query="成都小学分布地图",
        plan_only=False,
        expected_interaction_semantics=["user-wins"],
    ),
    # ── G9 artifact expired（执行债而非 remount）─────────────────────────
    GISBenchmarkCase(
        id="G9",
        name="artifact 过期：重跑 producer，不 remount 死 ref",
        group="repair",
        query="成都小学分布地图",
        plan_only=False,
        expected_interaction_semantics=["artifact-expired-no-remount"],
    ),
    # ── G10 chart 缺失但 stats 存活（facet 定向补齐）────────────────────
    GISBenchmarkCase(
        id="G10",
        name="图表缺失统计健在：facet 契约只补 chart",
        group="repair",
        query="成都各区小学数量统计与图表",
        expected_capabilities=["poi_query", "admin_aggregation"],
        optional_capabilities=["admin_boundary_query"],
        expected_product_facets=["chart"],
        max_tool_calls=8,
    ),
    # ── G11 OD 流向图小样本（D vertical slice）──────────────────────────
    GISBenchmarkCase(
        id="G11",
        name="OD 流向图：OD 边 → flow arc 产品",
        group="od",
        query="展示成都各区之间的通勤出行流",
        expected_task="mobility_flow",
        expected_capabilities=["od_matrix", "od_flow_mapping"],
        optional_capabilities=["admin_boundary_query"],
        allowed_algorithms=["network.od_matrix", "flow.", "profile.spatial", "admin.boundary"],
        forbidden_algorithms=["spatial.kde"],
        fixture_aliases=["od_edges"],
        script=[
            ScriptStep(tool="od_flow_edges", args={
                "od_table_ref": "fixture:od_edges",
                "top_n": 500,
            }),
        ],
        numeric_assertions=[
            NumericAssertion(source="step_result", step=0, path="features",
                             agg="len", op="==", value=500, label="top-N bounded flows"),
            NumericAssertion(source="step_result", step=0, path="metadata.total_pairs",
                             agg="value", op=">=", value=500, label="enough unique pairs"),
        ],
    ),
    # ── G12 大规模 OD（≥50k edges，无 O(N²)、有界输出）──────────────────
    GISBenchmarkCase(
        id="G12",
        name="50k OD 边：有界 top-N 输出",
        group="od",
        query="分析全市五万条通勤流的 major flows",
        expected_task="mobility_flow",
        expected_capabilities=["od_matrix", "od_flow_mapping"],
        optional_capabilities=["admin_boundary_query"],
        fixture_aliases=["od_edges_50k"],
        script=[
            ScriptStep(tool="od_flow_edges", args={
                "od_table_ref": "fixture:od_edges_50k",
                "top_n": 1000,
                "min_weight": 2,
            }),
        ],
        numeric_assertions=[
            NumericAssertion(source="step_result", step=0, path="features",
                             agg="len", op="<=", value=1000, label="bounded flow features"),
        ],
        max_tool_calls=5,
    ),
]


def get_all_cases() -> list[GISBenchmarkCase]:
    return list(GOLDEN_CASES)
