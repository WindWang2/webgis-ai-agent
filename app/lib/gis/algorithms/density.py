"""密度分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 density 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="density.visual.heatmap", name="视觉热力（渲染态密度）",
            capabilities=["density_surface"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="density_surface",
            geometry_requirements=["point"],
            min_features=10,               # heatmap_data 工具硬门槛（HEATMAP_MIN_POINTS）
            # ADR-0083：原生渲染通道硬上限 —— 前端 ref-source-resolver 的
            # FETCH_FEATURE_CAP（20k）：超过该点数前端拒绝挂载 ref，视觉热力
            # 必须降级聚合/服务端通道（capability fallback → grid_binning）。
            max_features_hint=20_000,
            approximate=True, deterministic=False,
            complexity="O(N) GPU/渲染端",
            tool_candidates=["heatmap_data"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="ASYNC",
            algorithm_family="density_visualization",
            assumptions=["渲染态密度（栅格化加核）——与解析 KDE 语义分离",
                         "（§10 硬规则：不以视觉热力冒充解析 KDE）"],
            limitations=["带宽/半径为渲染参数（非统计带宽选择器）"],
            crs_class="GEOGRAPHIC_OK",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_690_heatmap_guard.py::test_heatmap_tool_rejects_few_points",
            ],
            compatible_map_models=["visual_heatmap"],
            priority=10,
        random_seed_policy="none",
        ),

        AlgorithmDescriptor(
            id="spatial.kde.contours", name="核密度等值线",
            capabilities=["kde_density"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="density_surface",
            geometry_requirements=["point"],
            approximate=True, deterministic=False,
            complexity="O(N·grid)",
            tool_candidates=["kde_contours"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY",
            algorithm_family="density_estimation",
            assumptions=["KDE 表面的 marching-squares 等值线（matplotlib Agg）"],
            limitations=["等值线级别为渲染选择（非分位数语义）"],
            crs_class="GEOGRAPHIC_OK",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_density.py::test_kde_contours_returns_geoanalysisresult",
            ],
            compatible_map_models=["visual_heatmap", "isoline_contour"],
            fallback_algorithms=["spatial.kde.surface"],
            priority=10,
        random_seed_policy="none",
        fallback_semantics={"spatial.kde.surface": "equivalent"},
        ),

        AlgorithmDescriptor(
            id="spatial.kde.surface", name="核密度全格网表面",
            capabilities=["kde_density"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="density_surface",
            geometry_requirements=["point"],
            approximate=True, deterministic=False,
            complexity="O(N·grid)",
            tool_candidates=["kde_surface"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY",
            algorithm_family="density_estimation",
            method_references=["silverman1986", "abramson1982"],
            assumptions=["Silverman 规则或显式带宽（scipy gaussian_kde）",
                         "点数上限触发时降级披露",
                         "bandwidth_method=fixed（默认）：单一各向同性带宽，"
                         "行为与历史逐位一致",
                         "bandwidth_method=adaptive：Abramson 1982 平方根先导"
                         "律——先导密度来自固定带宽路径，h_i=h0·λ_i，评估为"
                         "逐点带宽核的直接求和（gaussian_kde 不支持逐点带宽）"],
            limitations=["高斯核假设；大规模点集走聚合通道（fallback 已声明）",
                         "adaptive 为一步先导近似（非迭代变带宽）；先导带宽与"
                         "λ 范围随结果披露",
                         "自适应评估与固定路径同阶 O(n·grid)，点数上限同 #384"],
            crs_class="GEOGRAPHIC_OK",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_completeness_v3.py::test_adaptive_kde_evaluator_integrates_to_one",
                "tests/unit/lib/test_completeness_v3.py::test_kde_surface_fixed_mode_byte_identical",
                "tests/unit/lib/test_completeness_v3.py::test_kde_surface_adaptive_determinism_and_disclosure",
            ],
            compatible_map_models=["visual_heatmap"],
            fallback_algorithms=["spatial.kde.contours"],
            priority=20,
        random_seed_policy="none",
        fallback_semantics={"spatial.kde.contours": "equivalent"},
        parameter_contract_ref="kde_surface_analysis",
        ),

        AlgorithmDescriptor(
            id="density.analytical.mixed", name="分析密度（KDE/聚合混合路径）",
            capabilities=["analytical_density"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="density_surface",
            geometry_requirements=["point"],
            approximate=True, deterministic=False,
            tool_candidates=["kde_contours", "heatmap_data", "spatial_aggregate"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="CELERY",
            algorithm_family="density_estimation",
            assumptions=["KDE/聚合混合路径按规模切换（切换语义披露）"],
            limitations=["路径切换以规模阈值为准（诊断进证据块）"],
            crs_class="GEOGRAPHIC_OK",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_density.py::test_kde_surface_has_grid_size_and_bandwidth",
            ],
            compatible_map_models=["administrative_choropleth", "aggregate_grid"],
            # 混合聚合路径（非专有算法）：priority 低于专有算法，使
            # tool_to_capability 的首选工具归属正确（kde_contours →
            # spatial.kde.contours/kde_density，而非本混合路径）。
            priority=30,
        random_seed_policy="none",
        ),

]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────
# Foundation V3：kde_surface 首次登记参数契约。bandwidth_method 为 additive
# 新参数（fixed=默认，历史行为不变；adaptive=Abramson 1982 平方根先导律）。
# 注意 parity 门只校验 required 参数，本契约无 required 参数。

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="kde_surface_analysis", version=1,
        description="KDE 表面：带宽/格元/权重字段/范围 + 带宽方法（fixed/adaptive）。",
        parameters=[
            ParameterSpec(
                name="bandwidth", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="bandwidth_scott",
                description="核带宽（米）；0=Scott 自动（kNN 尺度钳制）",
            ),
            ParameterSpec(
                name="bandwidth_method", type="enum", default="fixed",
                enum_values=["fixed", "adaptive"],
                description="fixed=单一各向同性带宽（默认，行为不变）；"
                            "adaptive=Abramson 1982 平方根先导律（逐点带宽）",
            ),
            ParameterSpec(
                name="cell_size", type="number", default=500, minimum=1,
                unit="meters",
                description="格网单元大小（米）",
            ),
            ParameterSpec(
                name="value_field", type="string",
                description="可选：作为点权重的数值字段名",
            ),
        ],
    ),
]
