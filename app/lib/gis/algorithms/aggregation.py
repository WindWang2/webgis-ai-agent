"""空间聚合 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 aggregation 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：登记显式分母聚合（率/密度）语义。反目标（骨架 §10）：
count 永不冒充 rate/density —— 分母显式（字段/面积/计数），零分母策略
披露（rate=None，从不编造 0/inf）。库层实现
``app/lib/geo_analysis/aggregation.py::aggregate_with_denominator``；
工具 spatial_aggregate 的参数面尚未暴露分母通道（中央接线待编排方完成，
见 spatial.aggregate.rates 的 limitation 披露）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="spatial.aggregate.admin", name="点落入面聚合（行政区统计）",
            capabilities=["admin_aggregation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="admin_aggregate_table",
            geometry_requirements=["point"],
            complexity="O(N·M) 点×面",
            tool_candidates=["spatial_aggregate"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["administrative_choropleth", "administrative_aggregation", "extrusion_3d"],
            priority=10,
            algorithm_family="zonal_aggregation",
            assumptions=[
                "空间连接谓词 intersects（边界点计入其贴边多边形，聚合约定）",
                "无点多边形 count=0 且 has_data=False；真 0 与无数据显式区分（#693）",
                "点/面 CRS 不一致时先统一到 UTM 工作帧再连接",
            ],
            limitations=[
                "count 聚合非密度/率——归一化需显式分母（见 spatial.aggregate.rates）",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/gis/test_golden_gis_numerics.py::test_g3_aggregate_exact_counts",
                "tests/unit/gis/test_golden_gis_numerics.py::test_g3_aggregate_value_field_sum",
                "tests/unit/lib/test_spatial_aggregate_693.py::test_within_vs_intersects_boundary_point",
                "tests/unit/lib/test_spatial_aggregate_693.py::test_null_vs_zero_empty_polygon",
                "tests/unit/lib/test_aggregation_semantics_vnext.py::test_count_denominator_matches_plain_count",
            ],
        ),

        # ── VNext：显式分母聚合（率/密度）。EXPERIMENTAL 是诚实声明：库层
        # 已实现并有语义测试，但工具参数面尚未暴露分母通道。────────────
        AlgorithmDescriptor(
            id="spatial.aggregate.rates", name="显式分母聚合（率/密度）",
            capabilities=["rate_aggregation"],
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "polygon_feature_set", "admin_boundary_set"],
            output_artifact_type="admin_aggregate_table",
            complexity="O(N·M) 点×面 + O(M) 面积/字段分母",
            tool_candidates=["spatial_aggregate"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["administrative_choropleth", "administrative_aggregation"],
            priority=15,
            algorithm_family="zonal_aggregation",
            parameter_contract_ref="aggregate_with_denominator",
            assumptions=[
                "分子 = 分子字段按区求和（NaN 值剔除并披露）或缺省的要素计数",
                "分母三种口径：区分母字段（field）/ 区真实面积 m²（area）/ 要素计数（count）",
                "率 = 分子 ÷ 分母；面积分母在 UTM/极方位度量 CRS 下计算（Web Mercator 不可信）",
                "空间连接谓词 intersects（与 spatial.aggregate.admin 同约定）",
            ],
            limitations=[
                "分母通道已接入 spatial_aggregate 工具（denominator_kind/numerator_field/denominator）——需中央接线 numerator_field/"
                "denominator_kind/denominator_field 三个参数",
                "count 分母的输出是比值（count_ratio_not_rate），不是率/密度",
                "分母缺失/≤0 的区 rate=None（JSON null）——从不编造 0 或 inf",
                "无支撑区（无要素）rate 仍可计算（分子 0），用 has_support 区分真 0 与无数据",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="EXPERIMENTAL",
            conformance_tests=[
                "tests/unit/lib/test_aggregation_semantics_vnext.py::test_field_denominator_rates_exact",
                "tests/unit/lib/test_aggregation_semantics_vnext.py::test_area_denominator_density_exact",
                "tests/unit/lib/test_aggregation_semantics_vnext.py::test_zero_and_negative_denominator_rate_none",
                "tests/unit/lib/test_aggregation_semantics_vnext.py::test_nan_numerator_excluded_and_disclosed",
            ],
        ),

        AlgorithmDescriptor(
            id="spatial.grid.h3", name="H3 六边形聚合",
            capabilities=["grid_binning"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="grid_aggregate",
            geometry_requirements=["point"],
            complexity="O(N) H3 索引",
            tool_candidates=["h3_binning"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["aggregate_grid"],
            fallback_algorithms=["spatial.grid.fishnet"],
            priority=10,
        fallback_semantics={"spatial.grid.fishnet": "approximation"},
        ),

        AlgorithmDescriptor(
            id="spatial.grid.fishnet", name="渔网格网聚合",
            capabilities=["grid_binning"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="grid_aggregate",
            geometry_requirements=["point"],
            complexity="O(N·M) 点×格",
            tool_candidates=["fishnet_grid"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["aggregate_grid"],
            fallback_algorithms=["spatial.grid.h3"],
            priority=20,
        fallback_semantics={"spatial.grid.h3": "approximation"},
        ),
]

# ── 参数契约（§12；为 spatial_aggregate 工具的分母通道预注册）────────
# 注意：契约无 required 参数 —— parity 门（validate_algorithm_tool_
# parameter_parity）只校验 required 参数在候选工具 schema 中的存在性，
# 因此工具侧尚未暴露这些参数时门保持绿色；接线后工具签名自然满足。
# 参数名与库层 aggregate_with_denominator 签名逐字对齐（接线规格见
# spatial.aggregate.rates limitation：numerator_field / denominator_kind /
# denominator_field，缺省 numerator_field=None、denominator_kind="count"）。

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="aggregate_with_denominator", version=1,
        description="显式分母聚合：分子（字段求和/计数）× 分母（字段/面积/计数）→ 率或密度。",
        parameters=[
            ParameterSpec(
                name="numerator_field", type="string",
                description="分子数值字段名（按区求和；缺省 = 要素计数）",
            ),
            ParameterSpec(
                name="denominator_kind", type="enum", default="count",
                enum_values=["field", "area", "count"],
                description="分母口径：区分母字段/区真实面积 m²/要素计数（count 输出是比值非率）",
            ),
            ParameterSpec(
                name="denominator", type="string",
                description="denominator_kind=field 时的区分母列名（如人口/暴露面积）",
            ),
        ],
    ),
]
