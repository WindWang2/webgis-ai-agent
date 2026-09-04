"""时序分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 temporal 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：temporal.trend 补齐非参数方法族科学元数据
（method 参数：ols_sen 缺省逐位不变 / mann_kendall / seasonal_mann_kendall），
新增 temporal.changepoint（CUSUM 均值变点，固定种子 bootstrap）。
实现位于 app/services/temporal/trend.py，工具层只做薄包装 + 证据块。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="temporal.profile", name="时间画像", category="temporal_analysis",
            capabilities=["temporal_profile"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["temporal_profile"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=10,
        ),

        AlgorithmDescriptor(
            id="temporal.aggregate", name="时间聚合", category="temporal_analysis",
            capabilities=["temporal_aggregate"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["temporal_aggregate"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="temporal.trend", name="时序趋势", category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["stats_table"],
            output_artifact_type="stats_table", tool_candidates=["temporal_trend"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=10,
            algorithm_family="trend_analysis",
            method_references=["sen1968", "mann1945", "kendall1975"],
            assumptions=[
                "缺省 ols_sen：Sen 中位斜率 + OLS，行为与历史逐位一致",
                "MK 族：tie 校正方差 + 连续性校正正态 z + 双侧 p",
                "显著性证据仅在 mann_kendall/seasonal 分支产出（ols_sen 无 p 值）",
                "携带可解析时间戳时 x 轴归一化为年（#594，斜率 per_year）",
            ],
            limitations=[
                "序列相关（lag-1 秩自相关超限）会夸大 MK 显著性——结果内警告",
                "季节 MK 无预白化（prewhitening 未实现）；观测 <3 的季节跳过并披露",
                "两时间点无法定义趋势统计量（n=2 拒绝，非降级描述）",
            ],
            crs_class="CRS_AGNOSTIC",
            scientific_preconditions=[
                "min_temporal_observations:8",
                "temporal_field_required",
            ],
            uncertainty_outputs=["statistical_significance"],
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_gis_runtime.py::test_temporal_trend_engine_linear_and_anomalies",
                "tests/unit/test_temporal_gis_runtime.py::test_trend_single_nan_matches_cleaned_series",
                "tests/unit/test_temporal_gis_runtime.py::test_analyze_trend_engine_real_time_axis_vs_index",
                "tests/unit/test_temporal_science_vnext.py::test_mk_monotone_increasing_and_white_noise",
                "tests/unit/test_temporal_science_vnext.py::test_analyze_trend_method_branches",
            ],
            parameter_contract_ref="temporal_trend_analysis",
        ),

        # ── VNext：CUSUM 均值变点 ─────────────────────────────────────
        AlgorithmDescriptor(
            id="temporal.changepoint", name="CUSUM 均值变点", category="temporal_analysis",
            capabilities=["temporal_change_point"],
            input_artifact_types=["stats_table"],
            output_artifact_type="stats_table",
            tool_candidates=["temporal_changepoint"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="change_point",
            assumptions=[
                "单均值漂移假设：变点 = argmax|Σ(x−x̄)|（k 取 1..n−1）",
                "显著性 = 无变化零假设下固定种子 bootstrap 的 max-CUSUM 分布",
                "p ≥ alpha 时不给 change_point_index（candidate 恒给）",
            ],
            limitations=[
                "多变点/方差变化不在模型内；n<10 变点定位不稳定（警告）",
                "bootstrap p 分辨率 1/(draws+1)",
            ],
            crs_class="CRS_AGNOSTIC",
            scientific_preconditions=["temporal_field_required"],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="同 seed 同输入逐位可复现（default_rng）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_science_vnext.py::test_cusum_shift_detected_and_deterministic",
                "tests/unit/test_temporal_science_vnext.py::test_cusum_no_shift_and_insufficient",
            ],
            parameter_contract_ref="temporal_changepoint_analysis",
        ),

        AlgorithmDescriptor(
            id="temporal.change", name="时序变化", category="temporal_analysis",
            capabilities=["change_detection"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="change_set", tool_candidates=["temporal_change"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="temporal.hotspot", name="时空热点", category="temporal_analysis",
            capabilities=["spatiotemporal_clustering"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="hotspot_result", tool_candidates=["spatiotemporal_hotspot"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
        ),

        AlgorithmDescriptor(
            id="temporal.raster_ts", name="时序栅格", category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface", tool_candidates=["temporal_raster"],
            cpu_cost="medium", memory_cost="medium", io_cost="high",
            preferred_execution_policy="THREAD", priority=30,
        ),
]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="temporal_trend_analysis", version=1,
        description="时序趋势方法选择（ols_sen 缺省 = 历史行为，逐位不变）。",
        parameters=[
            ParameterSpec(
                name="method", type="enum", default="ols_sen",
                enum_values=["ols_sen", "mann_kendall", "seasonal_mann_kendall"],
                description="seasonal_mann_kendall 需要逐点可解析日期",
            ),
        ],
    ),
    ParameterContract(
        id="temporal_changepoint_analysis", version=1,
        description="CUSUM 均值变点：bootstrap 重排次数与随机种子（固定种子策略）。",
        parameters=[
            ParameterSpec(
                name="bootstrap_draws", type="integer", default=200,
                minimum=100, maximum=1000, unit="count",
                description="无变化零假设下的重排次数（p 分辨率 1/(n+1)）",
            ),
            ParameterSpec(
                name="seed", type="integer", default=42,
                description="bootstrap 随机种子（可复现）",
            ),
        ],
    ),
]
