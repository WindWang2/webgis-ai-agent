"""时序分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 temporal 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：temporal.trend 补齐非参数方法族科学元数据
（method 参数：ols_sen 缺省逐位不变 / mann_kendall / seasonal_mann_kendall），
新增 temporal.changepoint（CUSUM 均值变点，固定种子 bootstrap）。
实现位于 app/services/temporal/trend.py，工具层只做薄包装 + 证据块。

science-v3（审计 03 §8 R1/R9）：temporal.hotspot 语义修正为 ST-DBSCAN
真实实现（审计 F1），新增 temporal.emerging_hotspot（Emerging Hot Spot
Analysis：逐期 Gi* + 逐箱 MK → ESRI 17+1 演化分类，实现位于
app/lib/geo_analysis/spatiotemporal_eha.py）；temporal.aggregate 补
科学元数据；temporal.changepoint 补 method_references（page1954）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import (
    AlgorithmDescriptor,
    NumericalTolerance,
    ResourceEnvelope,
)
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="temporal.profile", name="时间画像", category="temporal_analysis",
            capabilities=["temporal_profile"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["temporal_profile"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=10,
            algorithm_family="temporal_descriptive",
            assumptions=["时间字段解析 NaT 剔除并披露（与 ST-DBSCAN 同约定）",
                         "画像/聚合为描述性统计，不做趋势推断"],
            limitations=["无时区归一（时间戳语义由输入披露决定）",
                         "空时间维度 → 类型化错误（不伪造空统计）"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_gis_runtime.py::test_temporal_profiler_auto_detect",
            ]
        ),

        AlgorithmDescriptor(
            id="temporal.aggregate", name="时间聚合", category="temporal_analysis",
            capabilities=["temporal_aggregate"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["temporal_aggregate"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="temporal_descriptive",
            assumptions=["按时间粒度分组聚合（描述性）；NaT 剔除并披露",
                         "count=分组计数；sum/mean/min/max 作用于显式 metric_fields"],
            limitations=["分组键时区语义不归一（诚实披露）",
                         "空分组/全 NaT 不伪造统计（类型化空结果）"],
            crs_class="CRS_AGNOSTIC",
            scientific_preconditions=["temporal_field_required"],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_gis_runtime.py::test_temporal_aggregation_daily",
                "tests/unit/test_temporal_gis_runtime.py::test_temporal_aggregation_monthly",
            ],
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
            method_references=["page1954"],
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

        # ── Foundation V3：经典季节分解（centered MA；非 STL）──────────
        AlgorithmDescriptor(
            id="temporal.seasonal_decompose", name="经典季节分解",
            category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["stats_table"],
            output_artifact_type="stats_table",
            tool_candidates=["temporal_seasonal_decompose"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="seasonal_decomposition",
            method_references=["makridakis1998"],
            assumptions=[
                "经典 MA 分解：趋势=奇数窗口（period）中心滑动平均",
                "季节指数=去趋势值按相位 t mod period 的组均值；additive 归一化 Σs=0",
                "余项 additive = y−trend−seasonal；multiplicative = y/(trend·seasonal)",
                "至少 2 个完整周期（n ≥ 2×period，否则拒绝）；首尾 (period−1)/2 "
                "个位置趋势/余项无定义（None）",
            ],
            limitations=[
                "经典 MA 分解不是 STL——无迭代稳健拟合、无季节平滑，对离群值敏感",
                "period 必须为奇数（偶数窗口的中心 MA 需 2×m 复合平均，显式拒绝）",
                "multiplicative 要求序列严格为正",
            ],
            crs_class="CRS_AGNOSTIC",
            scientific_preconditions=[
                "min_temporal_observations:6",
                "temporal_field_required",
            ],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="线性斜坡+固定季节构造下：趋势=斜坡的中心 MA、"
                                "季节指数=构造季节、余项=0（1e-9）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_completeness_v3.py::test_seasonal_decompose_sine_plus_linear_exact",
                "tests/unit/lib/test_completeness_v3.py::test_seasonal_decompose_additive_reconstruction",
                "tests/unit/lib/test_completeness_v3.py::test_seasonal_decompose_typed_rejections",
            ],
            parameter_contract_ref="seasonal_decompose_analysis",
        ),

        AlgorithmDescriptor(
            id="temporal.change", name="时序变化", category="temporal_analysis",
            capabilities=["change_detection"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="change_set", tool_candidates=["temporal_change"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="temporal_descriptive",
            assumptions=["双期快照对比（描述性集合差：新增/消失/保持）"],
            limitations=["无匹配容差语义（同键精确匹配）"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_gis_runtime.py::test_temporal_change_engine_multi_snapshot",
            ]
        ),

        # 审计 F1 修正：本条目的真实语义是 ST-DBSCAN 时空密度聚类
        # （引擎 SpatiotemporalClusterEngine → st_dbscan 同核），不再是
        # 失实的「时间片×空间箱计数」；箱计数×逐期 Gi*×MK 的热点演化
        # 分析见 temporal.emerging_hotspot。
        AlgorithmDescriptor(
            id="temporal.hotspot", name="时空热点簇（ST-DBSCAN）",
            category="temporal_analysis",
            capabilities=["spatiotemporal_clustering"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="hotspot_result", tool_candidates=["spatiotemporal_hotspot"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="spatiotemporal_clustering",
            method_references=["ester_kriegel1996"],
            assumptions=["ST-DBSCAN 时空密度聚类：eps_spatial_m（米）/ "
                         "eps_temporal_days（天）/ min_samples 参数语义",
                         "输出为时空簇计数与成员要素（描述性密度聚类）；"
                         "不是逐期 Gi*、不做 Emerging Hotspot 演化分类"
                         "（后者见 temporal.emerging_hotspot）",
                         "时间字段解析 NaT 剔除并披露（与 temporal.profile 同约定）"],
            limitations=["无自动带宽：eps 需调用方给定，结果对 eps/min_samples 敏感（参数披露）",
                         "簇计数输出无显著性检验语义（密度聚类的诚实边界）"],
            crs_class="GEOGRAPHIC_OK",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_gis_runtime.py::test_spatiotemporal_cluster_engine",
            ],
                ),

        # science-v3 R1（审计 03 §8）：Emerging Hot Spot Analysis——
        # 把 temporal.hotspot 原先失实宣称的「箱计数矩阵」语义真正落地。
        AlgorithmDescriptor(
            id="temporal.emerging_hotspot", name="时空热点演化（EHA）",
            category="temporal_analysis",
            capabilities=["emerging_hotspot_analysis"],
            input_artifact_types=["grid_aggregate", "admin_aggregate_table",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            tool_candidates=["emerging_hotspot_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="spatiotemporal_statistics",
            method_references=["getis_ord1992", "mann1945", "kendall1975", "esri_eha"],
            assumptions=[
                "输入为已聚合的空间箱 × 时间期计数量矩阵（space-time cube，"
                "H3/格网聚合由调用方完成；某期缺失的箱按 0 计入并披露）",
                "逐期对全箱计算 Getis-Ord Gi* z（距离段二值权重、含自身 "
                "w_ii=1），双侧解析 p 经 BH-FDR 校正后判显著（q<alpha）",
                "对每个箱的 Gi* z 值时序跑 Mann-Kendall（tie 校正方差 + "
                "连续性校正）；按 ESRI Emerging Hot Spot Analysis 决策树"
                "输出 17 类 + none（互斥完备；类别码 ±1..±8/0）",
                "≥90% 期显著才进入 intensifying/persistent/diminishing/"
                "historical 分支；MK 需 n_periods ≥ 4，否则趋势不可得、"
                "分类退化为形态学规则并披露",
            ],
            limitations=[
                "逐期 Gi* 用纯空间邻域（非时空 lag 邻域）——与 ArcGIS 实现同口径，"
                "但对期数少、箱数少的立方显著性偏保守",
                "空间箱 <8 或期数 <8 时正态近似偏保守（仅描述性解读，警告在场）",
                "某期各箱计数全同（零方差）时该期无空间对比，z 置 0 并披露",
                "分类对 binning 粒度与 distance_band 敏感（band=0 自动取平均 "
                "8-NN 距离，自动值在输出中披露）",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "min_numeric_samples:3",
                "min_temporal_observations:2",
            ],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="deterministic",
            numerical_tolerance="解析公式（无模拟/置换）：同输入逐位可复现；"
                                "Gi*/MK 手算锚点见 conformance 测试",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_emerging_hotspot.py::test_gistar_hand_anchor_and_fdr",
                "tests/unit/lib/test_emerging_hotspot.py::test_categories_new_persistent_sporadic_consecutive_historical",
                "tests/unit/lib/test_emerging_hotspot.py::test_classification_mutually_exclusive_and_complete",
            ],
        ),

        AlgorithmDescriptor(
            id="temporal.raster_ts", name="时序栅格", category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface", tool_candidates=["temporal_raster"],
            cpu_cost="medium", memory_cost="medium", io_cost="high",
            preferred_execution_policy="THREAD", priority=30,
            algorithm_family="temporal_descriptive",
            assumptions=["时序栅格切片统计（逐期描述性统计）"],
            limitations=["栈深与格网规模守卫在实现层（ResourceScaleMismatch）"],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_gis_runtime.py::test_temporal_raster_engine_mock",
            ]
        ),

        # ── science-v5（W8）：时空立方体 / 物候 / 时间异常 ──────────────

        AlgorithmDescriptor(
            id="temporal.cube_stats", name="时空立方体统计", category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="stats_table", runtime_status="native",
            parameter_contract_ref="science_temporal_analysis",
            tool_candidates=["temporal_cube_stats"],
            cpu_cost="medium", memory_cost="medium", io_cost="high",
            preferred_execution_policy="THREAD", priority=31,
            algorithm_family="temporal_descriptive",
            complexity="O(T·H·W)（nan-aware 逐切片统计；T≤512 硬顶）",
            approximation_class="exact",
            assumptions=[
                "统一时间轴：栈 (T,H,W) + 秒制时间戳（升序）；SAR/光学共用容器",
                "缺口诚实：无效像元-切片计数披露，不静默插值",
            ],
            limitations=[
                "单位一致性（SAR 强度/分贝、光学反射率）由调用方保证",
                "疑似缺失切片判据 = 间距 > 中位距 1.5×（启发式，已披露）",
            ],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            resource_envelope=ResourceEnvelope(
                hard_max_cells=8 * 1024 * 1024, bytes_per_cell=8,
                notes="T≤512 切片 + T·H·W≤8,388,608 元素（CUBE_MAX_ELEMENTS 类型化守卫）"),
            cancellation_profile="coarse",
            tolerance=NumericalTolerance(rtol=1e-12, atol=1e-12, policy="conformance"),
            conformance_tests=[
                "tests/unit/lib/test_temporal_cube_v5.py::TestTemporalCube::test_slice_spacing_report_missing_slices",
                "tests/unit/lib/test_temporal_cube_v5.py::TestBuildCube::test_nodata_and_quality_masking",
            ],
        ),

        AlgorithmDescriptor(
            id="temporal.phenology", name="物候特征", category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface", runtime_status="native",
            parameter_contract_ref="science_temporal_analysis",
            tool_candidates=["phenology_features"],
            cpu_cost="high", memory_cost="medium", io_cost="high",
            preferred_execution_policy="THREAD", priority=32,
            algorithm_family="phenology_descriptors",
            complexity="O(T·N) 填充/平滑 + O(T·N_ok·6) 联合 LS（N=像元，n_ok=完整序列）",
            approximation_class="approximate",
            approximate=True,
            assumptions=[
                "双谐波联合 LS [1, t, sin/cos ωt, sin/cos 2ωt]（趋势与谐波联合估计）",
                "阈值法物候期：thr = min + frac·(max−min)，切片索引制",
                "短缺口（run ≤ max_gap）线性插值；长缺口保持 NaN（不外推）",
            ],
            limitations=[
                "物候期为切片索引制——非真实日期反演（诚实边界）",
                "SG 平滑只作用于填充后完整序列；被排除像元计数披露",
                "高斯谐波近似——非正弦物候轨迹的 SOS/EOS 有系统偏差",
            ],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            resource_envelope=ResourceEnvelope(
                hard_max_cells=8 * 1024 * 1024, bytes_per_cell=8,
                notes="工作数组 O(3T·N)；入参经 build_cube 的 T≤512 与 T·H·W≤8M 元素守卫"),
            cancellation_profile="coarse",
            tolerance=NumericalTolerance(rtol=1e-9, atol=1e-9, policy="conformance"),
            conformance_tests=[
                "tests/unit/lib/test_phenology_v5.py::TestPhenology::test_synthetic_sine_sos_eos_anchor",
                "tests/unit/lib/test_phenology_v5.py::TestPhenology::test_long_gap_excluded_not_extrapolated",
                "tests/unit/lib/test_phenology_v5.py::TestPhenology::test_gap_filled_count_disclosed",
            ],
        ),

        AlgorithmDescriptor(
            id="temporal.anomaly", name="时间异常/变化", category="temporal_analysis",
            capabilities=["temporal_trend"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface", runtime_status="native",
            parameter_contract_ref="science_temporal_analysis",
            tool_candidates=["temporal_anomaly"],
            cpu_cost="medium", memory_cost="medium", io_cost="high",
            preferred_execution_policy="THREAD", priority=33,
            algorithm_family="temporal_change",
            complexity="O(T·H·W)（气候态 + 分段 Welch 近似）",
            approximation_class="approximate",
            approximate=True,
            assumptions=[
                "异常 = 最后切片 z-score（全期气候态，std ddof=1）",
                "变化 = 后半段均值 − 前半段均值；z 为 Welch 近似",
            ],
            limitations=[
                "change_z 是效应量近似——非正式显著性检验（无自由度校正）",
                "z 分母为零/样本 <2 的像元 → NaN（诚实缺省）",
            ],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            resource_envelope=ResourceEnvelope(
                hard_max_cells=8 * 1024 * 1024, bytes_per_cell=8,
                notes="工作数组 O(4T·N)；入参经 build_cube 的 T≤512 与 T·H·W≤8M 元素守卫"),
            cancellation_profile="coarse",
            tolerance=NumericalTolerance(rtol=1e-9, atol=1e-9, policy="conformance"),
            conformance_tests=[
                "tests/unit/lib/test_phenology_v5.py::TestAnomaly::test_anomaly_z_hand_anchor",
                "tests/unit/lib/test_phenology_v5.py::TestAnomaly::test_change_direction_and_nan_guard",
            ],
        ),

        AlgorithmDescriptor(
            id="temporal.smooth_gapfill", name="时序平滑与缺口填补",
            category="temporal_analysis",
            capabilities=["temporal_smoothing"],
            input_artifact_types=["stats_table"],
            output_artifact_type="stats_table", tool_candidates=["ts_smooth_gapfill"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="temporal_descriptive",
            method_references=["savitzky_golay1964", "cochran1977"],
            assumptions=[
                "savgol（Savitzky-Golay 卷积平滑）/ moving_average（中心滑均）/"
                "none（只填补）三种方法；偶数窗口自动 +1 取奇并披露",
                "缺口填补是显式步骤（linear 时间插值 / nearest 最近有效值 / "
                "none 保持 NaN），填补位置由 filled_mask 标记",
                "缺口占比 ≥90% 或序列 <3 观测 → 类型化拒绝（缺口主导的"
                "平滑是伪像）",
                "无随机成分（deterministic）",
            ],
            limitations=[
                "与 temporal.phenology 内嵌的 SG 平滑互补：本算法是独立"
                "预处理（输出平滑序列），物候是特征提取（输出 SOS/EOS 指标）",
                "滑动平均对趋势起点/终点有边缘偏差（edge 填充口径）",
                "线性插值填补长缺口会抹平真实突变（连续缺口 >3 建议人工复核）",
            ],
            crs_class="CRS_AGNOSTIC",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="多项式再现性：线性信号平滑误差 <1e-12（闭合式锚）",
            scientific_status="VALIDATED",
            resource_envelope=ResourceEnvelope(
                bytes_per_cell=24,
                notes="等长输入/输出/掩膜数组（时间序列逐像元/逐要素复用）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-12, atol=1e-12,
                                         policy="polynomial_reproduction"),
            conformance_tests=[
                "tests/unit/lib/test_ts_smoothing_v6.py::"
                "test_savgol_linear_identity_and_noise_suppression",
                "tests/unit/lib/test_ts_smoothing_v6.py::"
                "test_gap_fill_semantics",
                "tests/unit/lib/test_ts_smoothing_v6.py::"
                "test_savgol_even_window_autocorrected",
                "tests/unit/lib/test_ts_smoothing_v6.py::"
                "test_smooth_gapfill_determinism",
                "tests/unit/lib/test_ts_smoothing_v6.py::"
                "test_smooth_gapfill_adversarial_inputs",
            ],
            parameter_contract_ref="ts_smooth_gapfill_analysis",
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
    # Foundation V3：经典季节分解契约（period 奇数；至少 2 个完整周期）。
    # values 序列本身是数据输入（同 geojson），不进契约。
    ParameterContract(
        id="seasonal_decompose_analysis", version=1,
        description="经典季节分解：周期（奇数）+ 分解模型（additive/multiplicative）。",
        parameters=[
            ParameterSpec(
                name="period", type="integer", required=True, minimum=3,
                unit="count",
                description="季节周期长度（必须为奇数；n ≥ 2×period 才可分解）",
            ),
            ParameterSpec(
                name="model", type="enum", default="additive",
                enum_values=["additive", "multiplicative"],
                description="additive=加法（默认）；multiplicative=乘法"
                            "（要求序列严格为正）",
            ),
        ],
    ),
    # science-v5 W8：物候/立方体/异常共用契约（工具签名与参数名一致——
    # parity 门校验）。
    ParameterContract(
        id="science_temporal_analysis", version=1,
        description="science-v5 时空立方体族：平滑/缺口/基线参数。",
        parameters=[
            ParameterSpec(
                name="window", type="integer", default=5,
                minimum=3, maximum=31, unit="count",
                description="Savitzky-Golay 窗口（奇数；偶数自动 +1 并披露）",
            ),
            ParameterSpec(
                name="polyorder", type="integer", default=2,
                minimum=1, maximum=3, unit="count",
                description="SG 多项式阶（须 < 窗口）",
            ),
            ParameterSpec(
                name="max_gap", type="integer", default=2,
                minimum=0, maximum=8, unit="count",
                description="线性插值填补的最大连续缺口（更长缺口保持 NaN）",
            ),
            ParameterSpec(
                name="threshold_frac", type="number", default=0.5,
                minimum=0.01, maximum=0.99,
                description="SOS/EOS 阈值分数（thr = min + frac·amplitude）",
            ),
            ParameterSpec(
                name="baseline_slices", type="integer", default=0,
                minimum=0, maximum=511, unit="count",
                description="时间异常基线段长（0 = 默认对半分）",
            ),
        ],
    ),
    ParameterContract(
        id="ts_smooth_gapfill_analysis", version=1,
        description="时序平滑/缺口填补：方法 / 窗口 / 多项式阶 / 填补方式。",
        parameters=[
            ParameterSpec(
                name="method", type="enum", default="savgol",
                enum_values=["savgol", "moving_average", "none"],
                description="平滑方法（none = 只填补）",
            ),
            ParameterSpec(
                name="window_length", type="integer", default=5,
                minimum=1, maximum=99, unit="count",
                description="滑动窗口（savgol 自动取奇）",
            ),
            ParameterSpec(
                name="polyorder", type="integer", default=2,
                minimum=1, maximum=9, unit="count",
                description="savgol 多项式阶（须 < 窗口）",
            ),
            ParameterSpec(
                name="fill", type="enum", default="linear",
                enum_values=["linear", "nearest", "none"],
                description="缺口填补：linear 时间插值 / nearest 最近有效 / "
                            "none 保持 NaN",
            ),
        ],
    ),
]
