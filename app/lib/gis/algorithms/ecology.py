"""生态算法域包（Goal 07 Science V6 新域）。

``ecology.habitat_suitability``（HSI 响应曲线评分）与
``ecology.landscape_metrics``（FRAGSTATS 同族分类栅格指标）。
数值实现在 ``app/lib/geo_analysis/ecology.py``。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import (
    AlgorithmDescriptor,
    ResourceEnvelope,
)
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [
    AlgorithmDescriptor(
        id="ecology.habitat_suitability", name="生境适宜度指数（HSI）",
        category="ecology",
        capabilities=["habitat_suitability"],
        input_artifact_types=["polygon_feature_set", "point_feature_set",
                              "grid_aggregate", "admin_aggregate_table"],
        output_artifact_type="feature_collection",
        tool_candidates=["habitat_suitability_analysis"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="THREAD", priority=20,
        algorithm_family="ecology_habitat",
        method_references=["usfws1981"],
        assumptions=[
            "USFWS 1981 HSI 程序：逐变量响应曲线评分（trapezoid 四参数 / "
            "gaussian 最优幅适），0-1 归一",
            "聚合 arithmetic=加权平均；geometric=限制因子语义"
            "（任一变量 0 → 整体 0）",
            "权重归一化后进 meta；缺失/非有限属性按 0 分计并披露",
            "属性驱动评分：不做几何运算（CRS 无关）",
        ],
        limitations=[
            "响应曲线参数是建模输入（非本算法估计）——须有生态学依据",
            "变量间相关性不做校正（共线性会重复计权）",
            "0-1 分级切点（<0.25 不适宜 … ≥0.75 最优）是缺省披露口径，"
            "可按物种生物学重定义",
        ],
        crs_class="CRS_AGNOSTIC",
        uncertainty_outputs=[],
        random_seed_policy="deterministic",
        numerical_tolerance="闭合式响应曲线：同输入逐位一致",
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(bytes_per_feature=48,
                                           notes="逐要素逐变量得分数组"),
        cancellation_profile="none",
        conformance_tests=[
            "tests/unit/lib/test_ecology_v6.py::"
            "test_hsi_trapezoid_closed_form_anchors",
            "tests/unit/lib/test_ecology_v6.py::"
            "test_hsi_geometric_limiting_factor",
            "tests/unit/lib/test_ecology_v6.py::"
            "test_hsi_adversarial_inputs",
        ],
        parameter_contract_ref="habitat_suitability_analysis",
    ),

    AlgorithmDescriptor(
        id="ecology.landscape_metrics", name="景观格局指标（FRAGSTATS 族）",
        category="ecology",
        capabilities=["landscape_metrics"],
        input_artifact_types=["raster_surface", "terrain_surface"],
        output_artifact_type="stats_table",
        tool_candidates=["landscape_metrics_analysis"],
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD", priority=20,
        algorithm_family="landscape_metrics",
        method_references=["mcgarigal_marks1995"],
        assumptions=[
            "FRAGSTATS 缺省 4 邻接连通（8 邻接会高估连通性）",
            "类级：PLAND / NP / PD（每 100ha）/ LPI / ED（米每公顷）；"
            "景观级：ED / SHDI / SIDI / PR",
            "nodata 像元不计面积；类 vs nodata/边界的对比边计入边缘",
            "类别上限 256（先重分类再算指标）",
        ],
        limitations=[
            "连通性对栅格分辨率敏感（跨分辨率比较需同化像元尺寸）",
            "逐类边缘密度按类面积归一（与景观级 ED 口径不同）",
        ],
        crs_class="RASTER_GRID",
        scientific_preconditions=["raster_band_required:1"],
        uncertainty_outputs=[],
        random_seed_policy="deterministic",
        numerical_tolerance="SHDI/SIDI/PLAND 闭合式：同输入逐位一致",
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_cell=24, hard_max_cells=50000000,
            notes="掩膜+标签数组 3×float64/int 系；50M 像元硬顶"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/lib/test_ecology_v6.py::"
            "test_landscape_uniform_and_diversity_anchors",
            "tests/unit/lib/test_ecology_v6.py::"
            "test_landscape_patches_and_edge_density",
            "tests/unit/lib/test_ecology_v6.py::"
            "test_landscape_adversarial_inputs",
        ],
        parameter_contract_ref="landscape_metrics_analysis",
    ),
]

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="habitat_suitability_analysis", version=1,
        description="生境适宜度：变量响应曲线 JSON / 聚合方式。",
        parameters=[
            ParameterSpec(
                name="variables_json", type="string", required=True,
                description="变量定义 JSON 数组：[{field, curve: trapezoid|"
                            "gaussian, params: [a,b,c,d]|[mu,sigma], weight}]",
            ),
            ParameterSpec(
                name="aggregation", type="enum", default="arithmetic",
                enum_values=["arithmetic", "geometric"],
                description="聚合：arithmetic 加权平均 / geometric 限制因子",
            ),
        ],
    ),
    ParameterContract(
        id="landscape_metrics_analysis", version=1,
        description="景观格局指标：分类栅格 / 像元尺寸 / nodata。",
        parameters=[
            ParameterSpec(
                name="raster_path", type="string", required=True,
                description="分类栅格 GeoTIFF 路径（data_dir 内）",
            ),
            ParameterSpec(
                name="nodata", type="number", default=0,
                description="nodata 覆盖值（0=用栅格自带 nodata）",
            ),
        ],
    ),
]
