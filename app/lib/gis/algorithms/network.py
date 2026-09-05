"""网络分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 network 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：为网络族补齐科学元数据（方法出处 / CRS 类 / 假设 /
局限 / conformance 节点 / fallback 语义），并登记外部服务商网络服务
（plan_route / search_transit_route / get_traffic_status —— 诚实声明外部
依赖与不可复现性），新增 network_shortest_path / network_od_matrix /
network_service_area 参数契约（工具签名对齐 —— parity 门校验）。

距离语义（反目标「不得静默用欧氏替代路网」）：
- 本地路网族（shortest_path / od_matrix / service_area.multi /
  closest_facility）边权 = haversine 长度或其派生时间 → crs_class=GEODESIC；
- network.service_area.simple 是速度表×时间的**直线缓冲** —— 路网等时圈的
  接近性代理（proxy），非近似（approximation），fallback 语义如实声明；
- 外部路径规划（plan_route 等）是服务商语义，与本地路网族互为独立实现，
  不构成 fallback 关系。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="network.isochrone", name="网络等时圈",
            capabilities=["service_area"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="service_area",
            tool_candidates=["isochrone_analysis"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"],
            priority=10,
            algorithm_family="isochrone",
            assumptions=[
                "外部高德路径规划 API 沿路网采样近似等时圈",
                "mode 速度表：walking 80 / cycling 250 / driving 667 / transit 417 m/min",
            ],
            limitations=[
                "依赖外部 AMAP_API_KEY 与服务商可用性（结果含 fetched_at 戳）；"
                "本地路网等时圈用 network.service_area.multi（network_service_area / isochrone_network）",
                "等时圈形态由服务商语义决定，与本地路网构图结果可不同",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="EXPERIMENTAL",
        ),

        AlgorithmDescriptor(
            id="network.service_area.simple", name="简化服务区（速度表缓冲）",
            capabilities=["service_area"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="service_area",
            approximate=True,
            tool_candidates=["service_area_simple"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"],
            fallback_algorithms=["network.isochrone"],
            priority=20,
            algorithm_family="proximity_proxy",
            assumptions=[
                "距离 = 速度表[mode] × travel_time_min 的直线（欧氏）缓冲：walking 5 / cycling 15 / driving 40 km/h",
                "不做路网构图、不解析拓扑 —— 输出是设施点的等距圆，非沿路可达范围",
            ],
            limitations=[
                "接近性代理（proxy）：速度表×时间的直线（欧氏）缓冲，忽略路网拓扑/单行线/障碍/河流分隔，实际路网可达范围可显著小于缓冲圈",
                "跨水系/高架隔断的区域会严重高估覆盖（用 network.isochrone / network.service_area.multi 做真实路网等时圈）",
                "速度为模式级常数，不含拥堵与路况",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="EXPERIMENTAL",
        fallback_semantics={"network.isochrone": "proxy"},
        ),

        AlgorithmDescriptor(
            id="network.shortest_path", name="最短路径", category="network_analysis",
            capabilities=["shortest_path"],
            output_artifact_type="line_feature_set", tool_candidates=["network_shortest_path"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC", priority=10,
            algorithm_family="shortest_path",
            method_references=["dijkstra1959"],
            assumptions=[
                "边权 = length_m（haversine 测段长）或 travel_time_s（长度/属性速度），Dijkstra/A* 在有向图上最优",
                "A* 启发式 = haversine直线距 × 图内最小每米成本（对任意阻抗可采，#447）",
                "坐标端点自动捕捉到最近边并插入虚拟节点（GIS-01），路线真正起止于捕捉点",
                "转向惩罚（travel_time_s 阻抗 + penalty>0）经边状态搜索只在实际转向处计（#455）",
            ],
            limitations=[
                "端点捕捉容差默认 500 m：超容差捕捉 confidence=0 并在结果警告中披露（不拒绝请求）",
                "图不连通时返回 total_cost=inf 的空路线（origin/destination 保留），不静默以欧氏距离替代路网距离",
                "边长为 haversine（测地）近似，无高程/坡度阻抗",
            ],
            crs_class="GEODESIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_network_issue447_astar_optimal.py::TestAStarAdmissibleHeuristic::test_astar_matches_dijkstra_on_express_detour",
                "tests/unit/test_network_issue447_astar_optimal.py::TestAStarDifferentialProperty::test_astar_cost_equals_dijkstra_cost_randomized",
                "tests/unit/test_network_issue455_turn_penalty.py::TestPenaltyChargedOnlyAtActualTurns::test_single_90_degree_turn_accrues_one_penalty",
                "tests/unit/test_network_analyst.py::TestNetworkRoutingService::test_shortest_path_dijkstra",
                "tests/unit/test_network_science_vnext.py::test_shortest_path_six_node_chain_exact_cost",
            ],
            parameter_contract_ref="network_shortest_path",
        ),

        AlgorithmDescriptor(
            id="network.closest_facility", name="最近设施", category="network_analysis",
            capabilities=["closest_facility"],
            output_artifact_type="line_feature_set",
            tool_candidates=["network_closest_facility", "nearest_facility"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC", priority=10,
            algorithm_family="closest_facility",
            method_references=["dijkstra1959"],
            assumptions=[
                "所有 需求×设施 对的代价来自同一棵逐起点 Dijkstra 最短路树（#489），选 K 近仅在代价上排序",
                "travel_direction 决定方向性：incident_to_facility（需求→设施）或 facility_to_incident",
                "零代价匹配（需求点恰在设施处）是合法匹配（#456）",
            ],
            limitations=[
                "网络不连通/超出 cutoff 的需求点不产路线，逐一点列入 summary.unmatched_demand_ids（不静默丢弃）",
                "OD 树代价不含转向惩罚（树无路径上下文，#455 跨工具语义）",
            ],
            crs_class="GEODESIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_network_analyst.py::TestNetworkClosestFacilityService::test_closest_facility",
                "tests/unit/test_network_issue453_od_snapping_consistency.py::TestClosestFacilityConsistency::test_closest_facility_cost_matches_routing",
                "tests/unit/test_network_issue456_zero_distance_facility.py::TestZeroDistanceMatch::test_zero_distance_wins_over_nonzero_competitor",
                "tests/unit/test_network_issue540_perf_fixes.py::test_closest_facility_selection_matches_naive_all_pairs",
            ],
            fallback_algorithms=["network.shortest_path"],
        fallback_semantics={"network.shortest_path": "approximation"},
        ),

        AlgorithmDescriptor(
            id="network.od_matrix", name="OD 成本矩阵", category="network_analysis",
            capabilities=["od_matrix"],
            output_artifact_type="od_matrix",
            tool_candidates=["network_od_matrix", "distance_matrix_cn"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["flow_od_arc"], priority=10,
            algorithm_family="od_matrix",
            method_references=["dijkstra1959"],
            assumptions=[
                "每个唯一起点一趟累积式 Dijkstra（#449），距离/时间沿同一最短路树累积（GIS-19）",
                "cutoff_s 以活动阻抗为单位（秒/米）；超出预算的对以 reachable=False + inf 返回，绝不静默缺行",
                "有向图语义：单行路网下 OD(A→B) ≠ OD(B→A)",
            ],
            limitations=[
                "OD 树代价不含转向惩罚（树无路径上下文，#455 跨工具语义）",
                "起点/终点捕捉在 500 m 容差内静默吸附最近边；捕捉距离在结果 snap_evidence 中逐端点披露",
            ],
            crs_class="GEODESIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_od_matrix_correctness.py::test_od_matrix_matches_direct_dijkstra_lengths",
                "tests/unit/test_od_matrix_correctness.py::test_od_matrix_dist_time_consistent_with_path",
                "tests/unit/test_network_issue449_od_cutoff.py::TestCutoffPlumbing::test_cutoff_never_returns_cells_beyond_it",
                "tests/unit/test_network_issue449_od_cutoff.py::TestAdversarial::test_disconnected_graph_unreachable",
            ],
            parameter_contract_ref="network_od_matrix",
        ),

        AlgorithmDescriptor(
            # ADR-0092 D：OD 边 → 有界带权流向线要素（flow_od_arc 渲染输入）。
            id="flow.od_arc_build", name="OD 流向构建", category="flow_analysis",
            capabilities=["od_flow_mapping"],
            input_artifact_type="od_table",
            output_artifact_type="line_feature_set",
            tool_candidates=["od_flow_edges"],
            cpu_cost="medium", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["flow_od_arc"], priority=10,
        ),

        AlgorithmDescriptor(
            id="network.service_area.multi", name="多断点服务区", category="network_analysis",
            capabilities=["service_area"],
            output_artifact_type="service_area", tool_candidates=["network_service_area"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"], priority=25,
            algorithm_family="isochrone",
            method_references=["dijkstra1959"],
            assumptions=[
                "有向图 Dijkstra 可达集（respect 单行线/障碍），逐 break 分类可达边并按剩余预算截断部分边（#618-20）",
                "break 单位 minutes/meters/seconds（km 为米别名）；minutes 断点按墙钟时间换算（#618-20/#706）",
                "边界多边形 = 可达边在局部 UTM 的固定米半径缓冲并集（GIS-08/09，不桥接不可达缝隙）",
            ],
            limitations=[
                "等时圈多边形是可达路网的 150 m 平滑缓冲包络，不是精确步行/车行边界",
                "设施捕捉节点不在图内时该设施不产出服务区，id 在结果 summary.unreachable_facility_ids 中披露",
                "无投影（极区）退化为纬度校正的点缓冲 fallback（GIS-08）",
            ],
            crs_class="GEODESIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_service_area_correctness.py::test_isochrone_polygon_is_valid_and_geojson",
                "tests/unit/test_service_area_correctness.py::test_isochrone_does_not_bridge_disconnected_gap",
                "tests/unit/test_network_issue540_perf_fixes.py::test_service_area_one_copy_when_mid_edge_and_same_result",
            ],
            parameter_contract_ref="network_service_area",
        ),

        AlgorithmDescriptor(
            # VNext（scientific-honesty pack）：isochrone_network（app/tools/
            # advanced_spatial.py → calculate_isochrones）是「输入路网线要素 →
            # 本地构图 → Dijkstra 可达集」的真路网等时圈，此前注册在
            # ToolRegistry 却无 capability/算法绑定（network_tool_orphan）。
            # 独立成 descriptor 而非并入既有 service_area 算法：两处的
            # tool_candidates 为 legacy 精确契约（tests/unit/gis
            # test_network_family_registered_native / 顺序契约），不可扩展。
            # priority=30 落在 service_area.multi（25）之后，保持解析顺序。
            id="network.isochrone.local", name="本地路网等时圈", category="network_analysis",
            capabilities=["service_area"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="service_area",
            tool_candidates=["isochrone_network"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"], priority=30,
            algorithm_family="isochrone",
            assumptions=[
                "输入路网线要素（调用方提供）建无向 MultiGraph，按 mode 速度×时间预算做 Dijkstra 可达集",
                "边长在局部 UTM 度量（to_utm_gdf 自动投影，GIS-02 同源语义）；设施投影到最近边后从两端点种子",
                "mode 速度表：walking 80 / cycling 250 / driving 667 / transit 417 m/min",
            ],
            limitations=[
                "无向图语义：单行线/转向限制不生效（需有向语义用 network.service_area.multi）",
                "单一时间断点（travel_time），不支持多 break 嵌套输出",
                "路网数据需调用方提供；空路网返回结构化失败（不静默空圈）",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="可达边计数与逐边全扫描参考一致（isochrone_443 等价测试）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_geo_analysis_isochrone_443.py::TestIsochroneEquivalence::test_reachable_counts_match_reference_scan",
                "tests/unit/test_geo_analysis_isochrone_443.py::TestIsochroneEquivalence::test_larger_cutoff_reaches_strictly_more",
                "tests/unit/test_geo_analysis_isochrone_443.py::TestAdversarialTopologies::test_disconnected_graph_stays_within_own_component",
                "tests/test_gis_audit_fixes.py::test_P3_4_multilinestring_edges_are_not_dropped",
            ],
        ),

        AlgorithmDescriptor(
            id="network.accessibility", name="网络可达性", category="network_analysis",
            capabilities=["accessibility"],
            output_artifact_type="service_area", tool_candidates=["network_accessibility"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["proximity_overlay"], priority=10,
            algorithm_family="accessibility",
            method_references=["radke_mu2010"],
            assumptions=[
                "2SFCA: 供给/需求两步浮动捕获 —— 第一步 R_j=容量_j/catchment 内需求权重和，第二步 A_i=Σ(cutoff 内 R_j)",
                "15min_circle 法：需求点在 cutoff 内可达任一设施即计入 served（0/1 覆盖，非 2SFCA）",
                "可达性以路网行程时间（分钟）度量，cutoff_minutes 为浮动捕获半径",
            ],
            limitations=[
                "容量/需求比值代理；E2SFCA 距离衰减未实现（cutoff 内等权）",
                "容量缺省 1.0：未提供 capacity 字段时 R_j 退化为供需计数比",
                "供需完全不可达的需求点计入 unserved（显式），score=0 的解释依赖供需总量披露",
            ],
            crs_class="GEODESIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_network_science_vnext.py::test_2sfca_single_facility_hand_computed_ratio",
                "tests/unit/test_network_science_vnext.py::test_2sfca_island_facility_ratio_and_unreachable_demand",
            ],
        ),

        AlgorithmDescriptor(
            id="network.route_optimization", name="路线优化", category="network_analysis",
            capabilities=["route_optimization"],
            output_artifact_type="line_feature_set", tool_candidates=["optimize_route"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC", priority=10,
            algorithm_family="route_optimization",
            assumptions=[
                "最近邻初始巡游 + 2-opt 局部搜索改进（有向代价矩阵，方向翻转计价 #540）",
                "leg 代价 = 活动阻抗下的路网最短路（OD 树重建，无逐 leg 重复寻路）",
            ],
            limitations=[
                "NN+2-opt 启发式非精确 TSP：解无最优性保证（迭代上限 100）",
                "不可达 leg 计 1e9 代价（巡游仍连贯，总代价如实累加 inf leg）",
            ],
            crs_class="GEODESIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_network_issue540_perf_fixes.py::test_two_opt_delta_matches_naive",
                "tests/unit/test_network_issue540_perf_fixes.py::test_two_opt_ladder_matches_naive",
                "tests/unit/test_network_issue540_perf_fixes.py::test_vrp_optimize_route_still_correct_end_to_end",
                "tests/unit/test_network_analyst.py::TestNetworkRouteOptimizationService::test_optimize_route_tsp",
            ],
        ),

        AlgorithmDescriptor(
            id="network.location_allocation", name="区位配置", category="network_analysis",
            capabilities=["location_allocation"],
            output_artifact_type="point_feature_set",
            tool_candidates=["location_allocation"],
            cpu_cost="high", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="ASYNC", priority=30,
            algorithm_family="location_allocation",
            method_references=["teitz_bart1968"],
            assumptions=[
                "p_median 目标 = 最小化 Σ w_i·min_{j∈S} C_ij；max_coverage = 最大化 cutoff 内覆盖需求权重",
                "代价矩阵 = 路网 OD 行程时间（不可达 = inf，参与目标时按 1e9 惩罚）",
            ],
            limitations=[
                "启发式 >20k 组合；exact ≤20k —— C(m,p) 枚举在预算内给出精确最优，超出切 Teitz-Bart 顶点替换 / 贪婪覆盖（近优非最优，summary.solver 披露）",
                "不可达需求点列入 summary.unassigned_ids（不参与选址目标）",
                "Teitz-Bart 收敛依赖初始化（前 p 个候选），无多起点重启",
            ],
            crs_class="GEODESIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_allocation_scaling.py::test_small_exact_still_exact",
                "tests/unit/test_allocation_scaling.py::test_heuristic_selects_reasonable_sites",
                "tests/unit/test_allocation_scaling.py::test_large_p_median_uses_heuristic_and_terminates",
                "tests/unit/test_quality_dedup.py::test_allocation_unassigned",
            ],
        ),

        AlgorithmDescriptor(
            id="network.optimize_route", name="路线优化（VRP）", category="network_analysis",
            capabilities=["route_optimization"],
            output_artifact_type="line_feature_set",
            tool_candidates=["optimize_route"],
            cpu_cost="high", memory_cost="low", io_cost="medium",
            preferred_execution_policy="ASYNC", priority=30,
            algorithm_family="route_optimization",
            assumptions=[
                "最近邻初始巡游 + 2-opt 局部搜索改进（有向代价矩阵，方向翻转计价 #540）",
                "leg 代价 = 活动阻抗下的路网最短路（OD 树重建）",
            ],
            limitations=[
                "NN+2-opt 启发式非精确 TSP：解无最优性保证（迭代上限 100）",
                "stops 上限 200（工具层显式拒绝超限，2-opt 超线性）",
            ],
            crs_class="GEODESIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_network_issue540_perf_fixes.py::test_two_opt_delta_matches_naive",
                "tests/unit/test_network_issue540_perf_fixes.py::test_vrp_optimize_route_still_correct_end_to_end",
            ],
        ),

        # ── VNext（ADR-0099）：外部服务商网络服务（消除 network_tool_orphan）──
        # 诚实登记：这些工具是高德/百度 API 的薄客户端，不是本地路网分析。
        # deterministic=False（服务商实时数据）；外部依赖进 limitations。
        AlgorithmDescriptor(
            id="network.route_external_api", name="外部路径规划（高德/百度）",
            category="network_analysis",
            capabilities=["external_route_planning"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="line_feature_set",
            tool_candidates=["plan_route"],
            cpu_cost="low", memory_cost="low", io_cost="high",
            preferred_execution_policy="ASYNC", priority=40,
            algorithm_family="external_routing",
            assumptions=[
                "路线/距离/耗时完全由服务商（高德或百度）路径规划 API 给出，本地不做路网构图",
                "输入为 WGS84 [lng,lat]，由服务商做坐标与路况语义解释",
            ],
            limitations=[
                "外部依赖：需 AMAP_API_KEY 或 BAIDU_API_KEY；配额/可达性/口径随服务商",
                "结果含 fetched_at 戳：实时路况敏感，逐次调用不可复现（deterministic=False）",
                "与服务商计费口径一致的路线不与本地路网分析（network.shortest_path）互相 fallback",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=[],
            random_seed_policy="none",
            deterministic=False,
            scientific_status="EXPERIMENTAL",
        ),

        AlgorithmDescriptor(
            id="network.transit_route_external", name="公交路径规划（高德）",
            category="network_analysis",
            capabilities=["transit_routing"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="line_feature_set",
            tool_candidates=["search_transit_route"],
            cpu_cost="low", memory_cost="low", io_cost="high",
            preferred_execution_policy="ASYNC", priority=40,
            algorithm_family="external_routing",
            assumptions=[
                "公交/地铁换乘方案（步行段+乘车段、换乘次数、总耗时、票价）完全由高德 API 给出",
                "city（起点城市）必填；跨城公交需 city_d",
            ],
            limitations=[
                "外部依赖：仅支持高德（需 AMAP_API_KEY）；策略 0=最快捷/1=最经济/2=最少换乘/3=最少步行/5=不乘地铁",
                "结果含 fetched_at 戳：班次时刻敏感，逐次调用不可复现（deterministic=False）",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=[],
            random_seed_policy="none",
            deterministic=False,
            scientific_status="EXPERIMENTAL",
        ),

        AlgorithmDescriptor(
            id="network.traffic_status_external", name="实时路况（高德）",
            category="network_analysis",
            capabilities=["traffic_status"],
            output_artifact_type="stats_table",
            tool_candidates=["get_traffic_status"],
            cpu_cost="low", memory_cost="low", io_cost="high",
            preferred_execution_policy="ASYNC", priority=40,
            algorithm_family="traffic_status",
            assumptions=[
                "道路名+拥堵等级+路段长度由高德实时路况 API 给出；矩形或圆形查询范围",
                "拥堵等级：1=畅通 2=缓行 3=拥堵 4=严重拥堵（0=全部）",
            ],
            limitations=[
                "外部依赖：仅支持高德（需 AMAP_API_KEY）；采样时刻的路况，查询即过期",
                "实时语义显式不缓存（#702）——缓存即错误信息；逐次调用不可复现（deterministic=False）",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=[],
            random_seed_policy="none",
            deterministic=False,
            scientific_status="EXPERIMENTAL",
        ),
]

# ── 参数契约（§12；参数名与工具签名逐字对齐 —— parity 门校验）────────
# 只为工具签名里真实存在的参数立契约（不虚构）：
# - network_shortest_path 没有 snap_tolerance / algorithm 参数（捕捉容差固定
#   500 m 并经 snap_evidence 披露；算法由实现内部按惩罚/阻抗选择）→ 不立；
# - network_od_matrix 没有 snap_tolerance 参数（固定 1e-5 度节点合并 +
#   500 m 端点捕捉，均经 snap_evidence 披露）→ 只立 cutoff_s；
# - network_service_area 的模式参数名是 profile（无 travel_mode）；
#   breaks 是 JSON 数组（契约类型词表无 array，按 terrain.extract_contours
#   的 levels 先例以 string+JSON 语义声明）。
# origin/destination 是多态输入（坐标数组/GeoJSON/地址串，schema 层 Any），
# 契约类型词表无对应类型：以 string + 描述声明其 required 语义（parity 门
# 只校验 required 名），运行时 apply_contract 不对它们做类型收敛（多态由
# 引擎 _parse_point 负责）—— 与 moran_i 契约外直传 distance_band 同款先例。

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="network_shortest_path", version=1,
        description="网络最短路径：起终点（必填）+ 阻抗/模式枚举；捕捉容差与警告在结果证据中披露。",
        parameters=[
            ParameterSpec(
                name="origin", type="string", required=True,
                description="起点（多态：[lng,lat] 数组 / GeoJSON Point / 地址串；引擎 _parse_point 解析）",
            ),
            ParameterSpec(
                name="destination", type="string", required=True,
                description="终点（多态：[lng,lat] 数组 / GeoJSON Point / 地址串）",
            ),
            ParameterSpec(
                name="impedance", type="enum", default="travel_time_s",
                enum_values=["length_m", "travel_time_s"],
                description="阻抗字段：行程时间（秒）或长度（米）",
            ),
            ParameterSpec(
                name="profile", type="enum", default="driving",
                enum_values=["walking", "driving", "cycling", "custom"],
                description="出行模式（决定默认速度与单行严格性）",
            ),
        ],
    ),
    ParameterContract(
        id="network_od_matrix", version=1,
        description="OD 成本矩阵：cutoff 上限（活动阻抗单位）；超预算的对以 reachable=False 显式返回。",
        parameters=[
            ParameterSpec(
                name="cutoff_s", type="number", minimum=0, unit="seconds",
                description="行程时间上限（秒）；None=不设限。超出预算的 OD 对返回 reachable=False + inf，绝不静默缺行",
            ),
        ],
    ),
    ParameterContract(
        id="network_service_area", version=1,
        description="多断点服务区：breaks 断点列表 + 出行模式；断点单位为分钟（工具签名缺省 [5,10,15]）。",
        parameters=[
            ParameterSpec(
                name="breaks", type="string",
                description="服务区断点（JSON 数组语义，如 [5,10,15]；工具 schema 为 array<number>，缺省 [5,10,15]）",
            ),
            ParameterSpec(
                name="profile", type="enum", default="driving",
                enum_values=["walking", "driving", "cycling", "custom"],
                description="出行模式（决定默认速度）",
            ),
        ],
    ),
]
