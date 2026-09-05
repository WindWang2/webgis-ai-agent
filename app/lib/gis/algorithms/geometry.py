"""几何处理 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 geometry 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：为几何处理族补齐科学元数据。核实结论（crs_class）：
- geometry.buffer：buffer_smart 内部经 to_utm_gdf 自动投影（地理输入 OK），
  crs_class=GEOGRAPHIC_OK；实现 app/lib/geo_processor/geometry.py。
- geometry.overlay：overlay_smart 在 WGS84 工作帧做 GEOS 拓扑叠加（纯拓扑，
  不量度），crs_class=CRS_AGNOSTIC；实现 app/lib/geo_processor/overlay.py。
- geometry.convex_hull / geometry.voronoi / geometry.multi_ring_buffer：
  geometry_ops 内部经 to_utm_gdf 自动投影，GEOGRAPHIC_OK；
  实现 app/lib/geo_analysis/geometry_ops.py。
- geometry.center_statistics：spatial_stats 的 centroid 在 UTM 下计算后回
  WGS84（地理输入 OK），GEOGRAPHIC_OK。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="spatial.buffer.proximity", name="距离缓冲区",
            capabilities=["proximity_buffer"],
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "line_feature_set", "polygon_feature_set"],
            output_artifact_type="proximity_zone",
            tool_candidates=["buffer_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["proximity_overlay"],
            priority=10,
        ),

        # ── VNext：几何缓冲科学元数据（crs_class 核实：buffer_smart 内部
        # to_utm_gdf 自动投影，地理/投影输入都正确处理 → GEOGRAPHIC_OK）──
        AlgorithmDescriptor(
            id="geometry.buffer", name="几何缓冲", category="geometry_processing",
            capabilities=["geometry_buffer"],
            input_artifact_types=["poi_feature_set", "point_feature_set", "line_feature_set", "polygon_feature_set"],
            output_artifact_type="proximity_zone", unit_requirements="meters",
            parameter_contract_ref="buffer_analysis", tool_candidates=["buffer_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="distance_buffer",
            assumptions=[
                "UTM 自动投影后米制缓冲，结果回 WGS84",
                "缓冲距离按输入 unit（m/km）换算为米后在 UTM 平面应用",
                "已投影输入保持原 CRS：非米制线性单位（英尺等）按轴因子换算（#524/#588）",
            ],
            limitations=[
                "UTM 带内大地测量尺度误差 <0.1%（跨带/大范围数据失真增大）",
                "quad_segs 圆弧离散化使点缓冲面积略小于 πr²（~0.16%，golden G1 容差 1%）",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="点缓冲面积与 πr² 差 <1%（UTM 带内，含圆弧离散化）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/gis/test_golden_gis_numerics.py::test_g1_point_buffer_area_matches_pi_r2",
                "tests/unit/gis/test_golden_gis_numerics.py::test_g1_km_unit_scales_quadratically",
                "tests/unit/lib/test_buffer_unit.py::test_buffer_unit_invalid_fails",
                "tests/unit/lib/test_geometry_science_vnext.py::test_buffer_monotonicity_seeded",
                "tests/unit/lib/test_geometry_science_vnext.py::test_buffer_geographic_area_accuracy_60n",
            ],
        ),

        AlgorithmDescriptor(
            id="geometry.clip", name="几何裁剪", category="geometry_processing",
            capabilities=["geometry_clip"],
            input_artifact_types=["poi_feature_set", "polygon_feature_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["clip_layer"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="geometry.dissolve", name="融合溶解", category="geometry_processing",
            capabilities=["geometry_dissolve"],
            input_artifact_types=["polygon_feature_set", "admin_boundary_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["dissolve_layer"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="geometry.spatial_join", name="空间连接", category="spatial_relationship",
            capabilities=["spatial_join"],
            input_artifact_types=["poi_feature_set", "polygon_feature_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["spatial_join"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
        ),

        # ── VNext：几何叠加/构造族（实现核实：overlay_smart 纯拓扑在 WGS84
        # 工作帧执行，不量度 → CRS_AGNOSTIC；hull/voronoi/multi_ring 经
        # to_utm_gdf 自动投影 → GEOGRAPHIC_OK）─────────────────────────
        AlgorithmDescriptor(
            id="geometry.overlay", name="几何叠加", category="geometry_processing",
            capabilities=["geometry_overlay"],
            input_artifact_types=["polygon_feature_set", "line_feature_set",
                                  "point_feature_set", "poi_feature_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["overlay_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="geometric_overlay",
            assumptions=[
                "GEOS 精确拓扑叠加（intersection/union/difference/symmetric_difference/identity）",
                "叠加在 WGS84 工作帧执行：图层 CRS 不一致时先对齐到 layer_a",
                "结果属性 = 两图层属性列的并集（gpd.overlay 语义）",
            ],
            limitations=[
                "纯拓扑运算：叠加输出坐标仍是度，叠加面积须另投影后量测",
                "输入几何经 make_valid 修复（无效多边形可能改变边界形状）",
                "面×点叠加结果是点集（输出按 polygon_feature_set 声明以面×面为主）",
            ],
            crs_class="CRS_AGNOSTIC",
            scientific_preconditions=[],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geo_processor.py::test_overlay_smart",
                "tests/unit/lib/test_geo_processor.py::test_overlay_smart_honors_declared_crs_member",
                "tests/unit/lib/test_geometry_science_vnext.py::test_overlay_intersection_area_exact",
                "tests/unit/lib/test_geometry_science_vnext.py::test_overlay_disjoint_honest_empty",
            ],
        ),

        AlgorithmDescriptor(
            id="geometry.convex_hull", name="凸包", category="geometry_processing",
            capabilities=["convex_hull"],
            input_artifact_types=["poi_feature_set", "point_feature_set", "polygon_feature_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["convex_hull"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=10,
            algorithm_family="hull_construction",
            method_references=[],
            assumptions=[
                "UTM 投影平面上的最小凸包（GEOS convex_hull），结果回 WGS84",
                "group_by 给定时按属性分组各建一个凸包",
            ],
            limitations=[
                "<3 个非共线要素的组/集合退化为 Point/LineString —— 诚实拒绝不产出假多边形",
                "度空间共线的点在 UTM 投影后可变成极薄三角形（投影非仿射），不保证仍失败",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geometry_ops.py::test_convex_hull_contains_all_points",
                "tests/unit/lib/test_geometry_ops.py::test_convex_hull_group_by",
                "tests/unit/lib/test_geometry_science_vnext.py::test_convex_hull_square_fixture_exact",
                "tests/unit/lib/test_geometry_science_vnext.py::test_convex_hull_degenerate_honest_failure",
            ],
        ),

        AlgorithmDescriptor(
            id="geometry.voronoi", name="Voronoi（Thiessen）剖分", category="geometry_processing",
            capabilities=["voronoi_tessellation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="polygon_feature_set", tool_candidates=["voronoi_polygons"],
            geometry_requirements=["point"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="tessellation",
            method_references=[],
            assumptions=[
                "scipy Voronoi + 4 轴镜像点外推使边界点获得有限区域",
                "输出按数据范围 +50% 边距裁剪（可用 clip_bounds 显式指定）",
                "每个点格的质心作为剖分种子（非点输入取质心）",
            ],
            limitations=[
                "边界镜像外推：裁剪框外的区域形状依赖镜像几何，非真实边界",
                "重复点行为：Qhull 退化时诚实报错（QH6154）；一般重复点各得一份相同区域（不去重）",
                "无单元格的退化区域被静默跳过（count < 输入点数）",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geometry_ops.py::test_voronoi_produces_cells_covering_inputs",
                "tests/unit/lib/test_geometry_ops.py::test_voronoi_too_few_points",
                "tests/unit/lib/test_geometry_science_vnext.py::test_voronoi_triangle_three_cells",
                "tests/unit/lib/test_geometry_science_vnext.py::test_voronoi_duplicates_honest_behavior",
            ],
        ),

        AlgorithmDescriptor(
            id="geometry.multi_ring_buffer", name="多环缓冲", category="geometry_processing",
            capabilities=["multi_ring_buffer"],
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "line_feature_set", "polygon_feature_set"],
            output_artifact_type="proximity_zone", tool_candidates=["multi_ring_buffer"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="distance_buffer",
            assumptions=[
                "UTM 投影平面米制缓冲；升序距离环，merge_rings=True 时内环被外环差集扣除",
                "环带宽度 = 相邻距离差（band i 覆盖 (d_{i-1}, d_i]）",
            ],
            limitations=[
                "UTM 带内大地测量尺度误差 <0.1%（同 geometry.buffer）",
                "quad_segs=32 圆弧离散化使环面积与解析环差 ~0.1%",
                "非米制已投影输入按轴因子换算（#588），极小负/零距离拒绝",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geometry_ops.py::test_multi_ring_buffer_merged_rings_are_annular",
                "tests/unit/lib/test_geometry_ops.py::test_multi_ring_buffer_state_plane_feet_distance_in_meters",
                "tests/unit/lib/test_geometry_science_vnext.py::test_multi_ring_bands_disjoint_cover_widths",
            ],
        ),

        AlgorithmDescriptor(
            id="geometry.center_statistics", name="几何中心统计", category="geometry_processing",
            capabilities=["geometry_centroid"],
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "line_feature_set", "polygon_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["spatial_stats", "central_feature"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=30,
            algorithm_family="centroid_statistics",
            assumptions=[
                "spatial_stats 的 centroid = 并集几何质心（UTM 下计算后回 WGS84）",
                "点集时等价于无权平均中心；面集时近似面积加权质心",
                "central_feature 提供显式 mean_center / central_feature 两种口径",
            ],
            limitations=[
                "并集质心不是加权平均中心：需要显式加权中心用 central_feature",
                "spatial_stats 是量纲摘要（total_area_m2/total_length_m/bbox/centroid），"
                "不是中心趋势的显著性描述（离散度用 standard_deviational_ellipse）",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="EXPERIMENTAL",
            conformance_tests=[],
        ),
]
