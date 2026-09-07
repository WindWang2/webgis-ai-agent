"""插值 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 interpolation 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：IDW / 克里金补齐科学元数据（出处 / CRS 类 / 不确定性 /
conformance 节点 / backend 变体）；新增 interpolation.rbf（scipy
RBFInterpolator）与 interpolation.universal_kriging（线性漂移 UK）。
实现位于 app/lib/geo_analysis/{interpolation,kriging,rbf_interpolation}.py，
工具层（app/tools/advanced_spatial.py）只做 validate → 调实现 → 挂证据块。

CRS 类核实记录：
- interpolation.idw / interpolation.rbf：实现内部经 estimate_utm_crs 自动
  投影到米制 CRS 后才算距离 —— 度数输入结果正确，crs_class=GEOGRAPHIC_OK；
  跨带自动 UTM 的投影失真记入 limitations。
- interpolation.kriging / interpolation.universal_kriging：方法的度量假设
  是投影坐标（driver 的声明 CRS 契约只接受 4326/4490/3857/UTM，度数输入
  在 driver 内强制重投影，不支持的 CRS 结构化拒绝），crs_class=
  PROJECTED_REQUIRED；EPSG:3857 被接受为工作 CRS 但含 Web Mercator 尺度
  畸变（非真实地面距离），记入 limitations。

（V3 备注：中央 kriging_interpolation 契约现已含 method 枚举
（ordinary/universal，见 parameter_contracts），本文件此前所称"中央契约
缺 method 参数"的缺口已消除。）
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor, BackendVariant
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="interpolation.idw", name="IDW 插值", category="interpolation",
            capabilities=["spatial_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", unit_requirements="meters",
            parameter_contract_ref="idw_interpolation", tool_candidates=["idw_interpolation"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            fallback_algorithms=["interpolation.kriging"], priority=10,
            complexity="O(n log n + m·k)（cKDTree 邻域 k=5）",
        # F3（science-v3 审计）：kriging 带 nugget 时非精确插值器、CRS 语义
        # 不同（PROJECTED_REQUIRED vs GEOGRAPHIC_OK）、min_features 8 vs 1——
        # "equivalent" 过强，诚实降级为 approximation。
        fallback_semantics={"interpolation.kriging": "approximation"},
        # ── VNext（ADR-0099）─────────────────────────────────────────────
        algorithm_family="deterministic_interpolation",
        method_references=["shepard1968"],
        assumptions=[
            "精确插值器（过样本点）；无理论方差——不确定性以 LOOCV 残差证据呈现",
            "米制距离：地理输入经 estimate_utm_crs 自动投影（极区用极方位立体投影）",
            "k=5 最近邻截断（与主路径一致）；重复坐标先按均值聚合（确定性）",
            "幂次守卫 0 < power ≤ 5（>5 时权重退化为最近邻）",
        ],
        limitations=[
            "跨带数据自动 UTM 有投影失真（单带处理，无跨带拆分）",
            "LOOCV 残差分位数是样本内证据，不外推为置信区间",
            "样本凸包外的外推由幂次主导，远端值趋向邻域均值",
        ],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=["validation_metrics"],
        random_seed_policy="deterministic",
        numerical_tolerance="向量化解与标量参考一致（<1e-6）；精确命中阈值 1e-9 m",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/lib/test_idw_interpolation.py::test_idw_cell_centered_on_input_point_is_exact",
            "tests/unit/lib/test_idw_interpolation.py::test_idw_matches_independent_metric_reference",
            "tests/unit/lib/test_idw_interpolation.py::test_idw_uses_metric_not_degree_distance",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_idw_exact_hit_at_samples",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_idw_loocv_matches_independent_reference",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_idw_tool_evidence_blocks",
        ],
        ),

        AlgorithmDescriptor(
            id="interpolation.kriging", name="普通克里金插值", category="interpolation",
            capabilities=["spatial_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 与核心 MIN_SAMPLES 对齐（resolver 侧同值镜像；去重后 <8 点克里金无意义）
            min_features=8,
            parameter_contract_ref="kriging_interpolation",
            tool_candidates=["kriging_interpolation"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            fallback_algorithms=["interpolation.idw"], priority=20,
            fallback_semantics={"interpolation.idw": "approximation"},
            complexity="拟合 O(N_fit²)（N_fit≤2000）+ 预测 O(m·(k+1)³)（k≤24, chunk 1024）",
            approximation_class="exact",
        # ── VNext（ADR-0099）─────────────────────────────────────────────
        algorithm_family="geostatistical_interpolation",
        method_references=["matheron1963"],
        assumptions=[
            "二阶平稳性假设：变异函数从数据估计（加权 RSS 最低的模型胜出）",
            "规范半方差构造（Isaaks & Srivastava）：nugget 进所有 h>0 项与 γ₀，对角为零",
            "k 邻域（≤24）系统分批求解；高斯模型加 ridge 稳定化，退化逐格计数",
            "5 折 CV 确定性分折（索引取模，无 RNG），折内重拟合变异函数",
            "V2 opt-in：matern/wave/cubic 家族、几何各向异性、spatial_block CV、显式求解后端（默认=历史行为）",
        ],
        limitations=[
            "EPSG:3857 被接受为工作 CRS 但含 Web Mercator 尺度畸变（高纬非真实地面距离）",
            "趋势明显的场 OK 有系统偏差——改用 interpolation.universal_kriging",
            "变异函数拟合失败 / 滞后 bin 不足时结构化拒绝（不静默降级）",
            "驱动层 variogram_model 词表固定为 auto/3 族（生产一致性套件钉死）；matern/wave/cubic 经 fit_variogram 库级启用",
        ],
        crs_class="PROJECTED_REQUIRED",
        uncertainty_outputs=["raster_uncertainty", "validation_metrics"],
        random_seed_policy="deterministic",
        numerical_tolerance="批式求解 chunk 1024；ridge 1e-6·sill（gaussian 1e-2·sill）；预测钳制 ±3√sill",
        scientific_status="PRODUCTION",
        conformance_tests=[
            "tests/unit/lib/test_kriging_interpolation.py::test_ok_exact_interpolation_at_samples",
            "tests/unit/lib/test_kriging_interpolation.py::test_ok_accuracy_beats_naive_mean_on_stationary_field",
            "tests/unit/lib/test_kriging_interpolation.py::test_ok_survives_nugget_dominated_data",
            "tests/unit/gis_harness/test_kriging_vertical_slice.py::test_kriging_beats_or_matches_idw_on_stationary_field",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_kriging_driver_deterministic_repeat",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_kriging_tool_structured_validation_block",
        ],
        # Wave 8：declared uncertainty → producer test 机器可查闭环。
        uncertainty_producer_tests={
            "raster_uncertainty": "tests/unit/lib/test_kriging_interpolation.py::test_ok_uncertainty_grows_away_from_samples",
            "validation_metrics": "tests/unit/lib/test_interpolation_science_vnext.py::test_kriging_tool_structured_validation_block",
        },
        backend_variants=[
            BackendVariant(id="numpy_batched", backend="numpy", deterministic=True,
                           min_features=8, max_features=100_000,
                           approximation_class="exact",
                           notes="批式 np.linalg.solve（chunk 1024），中小规模主路径"),
            BackendVariant(id="scipy_linalg", backend="scipy", deterministic=True,
                           min_features=100_001, max_features=500_000,
                           approximation_class="exact",
                           notes="scipy cKDTree 邻域 + 逐行 LAPACK 退化路径（同一 conformance 套件；大规模窗口）"),
        ],
        ),

        # ── VNext 插值科学新算法 ─────────────────────────────────────────

        AlgorithmDescriptor(
            id="interpolation.rbf", name="RBF 径向基插值", category="interpolation",
            capabilities=["spatial_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # LOOCV 留一后仍需 ≥2 点求解，<3 点 RBF 系统无意义
            min_features=3,
            parameter_contract_ref="rbf_interpolation",
            tool_candidates=["rbf_interpolation"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            fallback_algorithms=["interpolation.idw"], priority=15,
        fallback_semantics={"interpolation.idw": "approximation"},
        complexity="O(n³) 系统分解 + O(m·n) 求值（RBF_HARD_CAP 类型化拒绝）",
        approximation_class="exact",
        algorithm_family="deterministic_interpolation",
        method_references=["duchon1977"],
        assumptions=[
            "scipy RBFInterpolator：核薄板样条默认，smoothing=0 时精确过样本点",
            "米制距离：地理输入经 estimate_utm_crs 自动投影（与 IDW 同一 CRS 政策）",
            "局部 RBF（neighbors ≤64）：超样本数时按 KdTree 最近邻截断",
            "无理论方差——不确定性以 LOOCV 残差证据呈现（与 IDW 同口径）",
        ],
        limitations=[
            "多二次/高斯类核在大数据集上病态（本实现未含 gaussian 核）",
            ">2 万点确定性行距抽稀（metadata.disclosures 披露），>10 万点拒绝",
            "外推区域行为由核多项式项主导，远端可能发散（无钳制）",
        ],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=["validation_metrics"],
        random_seed_policy="deterministic",
        numerical_tolerance="smoothing=0 时节点精确复现（<1e-9 相对误差，conformance 固定）",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/lib/test_interpolation_science_vnext.py::test_rbf_exactness_at_nodes_smoothing_zero",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_rbf_loocv_present_and_finite",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_rbf_invalid_kernel_rejected",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_rbf_driver_deterministic_repeat",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_rbf_default_kernel_thin_plate_spline",
        ],
        ),

        AlgorithmDescriptor(
            id="interpolation.universal_kriging", name="泛克里金插值", category="interpolation",
            capabilities=["spatial_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 线性漂移 [1,x,y] 至少需要 12 点约束（与实现 UK_MIN_SAMPLES 对齐）
            min_features=12,
            parameter_contract_ref="kriging_interpolation",
            tool_candidates=["kriging_interpolation"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            complexity="OLS 趋势 O(n·d²) + 残差 OK（同 kriging 窗口）",
            approximation_class="exact",
            fallback_algorithms=["interpolation.kriging"], priority=21,
        fallback_semantics={"interpolation.kriging": "approximation"},
        algorithm_family="geostatistical_interpolation",
        method_references=["matheron1963"],
        assumptions=[
            "线性漂移 E[Z(x)]=b0+b1·x+b2·y；变异函数在 OLS 去趋势残差上拟合",
            "UK 系统带趋势约束 Lagrange 乘子；方差 = wᵗγ₀ + mᵗf0",
            "零残差退化（数据严格线性）→ 精确趋势预测、方差 0、披露 zero_residual_variance",
            "CV 每折重拟合趋势 + 残差变异函数（折间无泄漏）",
        ],
        limitations=[
            "漂移阶数固定为线性（二次及以上趋势未实现）",
            "EPSG:3857 被接受为工作 CRS 但含 Web Mercator 尺度畸变（与 OK 同）",
            "样本 <12 拒绝（InsufficientSamples）；普通克里金 ≥8 即可",
        ],
        crs_class="PROJECTED_REQUIRED",
        scientific_preconditions=["min_numeric_samples:12"],
        uncertainty_outputs=["raster_uncertainty", "validation_metrics"],
        random_seed_policy="deterministic",
        numerical_tolerance="与 OK 同（批式 chunk 1024；ridge 1e-6·sill；钳制 ±3√sill）；零残差退化方差精确为 0",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/lib/test_interpolation_science_vnext.py::test_universal_kriging_exact_plane_zero_residual_variance",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_universal_kriging_insufficient_samples_typed",
            "tests/unit/lib/test_interpolation_science_vnext.py::test_ok_uk_loocv_on_trended_field",
        ],
        ),

        # ── Foundation V2 · A2（插值 V2 新算法）──────────────────────────

        AlgorithmDescriptor(
            id="interpolation.tin", name="TIN 三角网插值", category="interpolation",
            capabilities=["triangulation_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 三角剖分至少需要 3 个非共线点（LOOCV 留一后仍需 ≥3）
            min_features=3,
            parameter_contract_ref="tin_interpolation",
            tool_candidates=["tin_interpolation"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            complexity="Delaunay O(n log n) + 逐格重心定位（>20 万样本类型化拒绝）",
            approximation_class="exact",
            fallback_algorithms=["interpolation.idw"], priority=16,
            fallback_semantics={"interpolation.idw": "approximation"},
            algorithm_family="deterministic_interpolation",
            method_references=["watson1981", "clough_tocher1966"],
            assumptions=[
                "Delaunay 三角剖分上的分段插值：linear=C⁰ 重心插值，clough_tocher=C¹ 三次",
                "凸包外诚实空缺（fill_value=NaN）：TIN 不外推，格网外的缺失进 metadata",
                "米制坐标下剖分：地理输入经 estimate_utm_crs 自动投影（与 IDW 同 CRS 政策）",
                "精确插值器（过样本点）；无理论方差——不确定性以 LOOCV 残差证据呈现",
            ],
            limitations=[
                "样本共线/退化构型结构化拒绝（DegenerateData，附修正提示）",
                ">20 万样本拒绝（Qhull 内存有界但超限先抽稀）",
                "凸包外格网无值——需要全域覆盖时改用 IDW/趋势面（会外推）",
            ],
            crs_class="PROJECTED_REQUIRED",
            uncertainty_outputs=["validation_metrics"],
            random_seed_policy="deterministic",
            numerical_tolerance="linear 在样本格心精确复现采样值（<1e-9，conformance 固定）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_tin_interpolation.py::test_tin_linear_exact_at_samples",
                "tests/unit/lib/test_tin_interpolation.py::test_tin_nan_outside_hull_no_extrapolation",
                "tests/unit/lib/test_tin_interpolation.py::test_tin_collinear_rejected_degenerate_data",
                "tests/unit/lib/test_tin_interpolation.py::test_tin_scale_guard_resource_mismatch",
                "tests/unit/lib/test_tin_interpolation.py::test_tin_loocv_bounded_and_finite",
                "tests/unit/lib/test_tin_interpolation.py::test_tin_driver_deterministic_repeat",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.trend_surface", name="趋势面分析", category="interpolation",
            capabilities=["trend_surface"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # order=1 的项增长守卫（order·(order+3)/2 ≤ n/3）需要 ≥6 点
            min_features=6,
            parameter_contract_ref="trend_surface_analysis",
            tool_candidates=["trend_surface"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", compatible_map_models=["raster_surface"],
            complexity="O(n·d²)（单位盒缩放 OLS，d=多项式项数）",
            approximation_class="exact",
            fallback_algorithms=["interpolation.kriging"], priority=17,
            fallback_semantics={"interpolation.kriging": "approximation"},
            algorithm_family="global_polynomial_regression",
            method_references=["webster_oliver2007"],
            assumptions=[
                "全局多项式 OLS：z ~ u^i·v^j（i+j≤order），坐标缩放至单位盒（条件数稳定，已披露）",
                "OLS 残差方差 σ̂²=SS_res/(n−p) 是有效的模型方差证据（区别于克里金逐点方差）",
                "bbox 外评估照常输出但逐格标记 extrapolated（趋势模型本就全局外推）",
                "项增长守卫：order·(order+3)/2 > n/3 结构化拒绝（每项 ≥3 样本支撑）",
            ],
            limitations=[
                "全局多项式只能表达大尺度趋势——局地结构交给克里金/TIN/RBF",
                "高阶多项式边缘振荡（Runge 现象）：order≤3 硬限制",
                "坐标零跨度/设计矩阵不满秩结构化拒绝（DegenerateData）",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=["validation_metrics"],
            random_seed_policy="deterministic",
            numerical_tolerance="单位盒缩放下平面场精确恢复（残差 ≤1e-9·量级，conformance 固定）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_trend_surface.py::test_trend_plane_recovered_exactly_order1",
                "tests/unit/lib/test_trend_surface.py::test_trend_extrapolation_flagged_outside_bbox",
                "tests/unit/lib/test_trend_surface.py::test_trend_order_guard_insufficient_samples",
                "tests/unit/lib/test_trend_surface.py::test_trend_order2_beats_order1_on_quadratic_field",
                "tests/unit/lib/test_trend_surface.py::test_trend_zero_span_degenerate",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.regression_kriging", name="回归克里金", category="interpolation",
            capabilities=["regression_kriging"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 与普通克里金同底（去重后 ≥8 点）+ ≥2 个协变量字段
            min_features=8,
            parameter_contract_ref="regression_kriging_analysis",
            tool_candidates=["regression_kriging"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            complexity="OLS 趋势 + 残差 OK + 协变量 IDW 近似（目标栅格通道）",
            approximation_class="approximate",
            fallback_algorithms=["interpolation.kriging"], priority=22,
            fallback_semantics={"interpolation.kriging": "approximation"},
            algorithm_family="hybrid_regression_kriging",
            method_references=["odeh1995", "matheron1963"],
            assumptions=[
                "RK = OLS 趋势（z ~ 协变量）+ 残差普通克里金（auto 变异函数）",
                "目标处协变量值由样本协变量经 IDW（k=5, power=2）近似——approximate 语义",
                "rk_variance 仅含残差克里金方差；趋势系数不确定性未传播（如实披露）",
                "零残差退化（z 严格线性于协变量）→ 精确趋势预测、方差 0、披露披露位",
            ],
            limitations=[
                "协变量场在目标处不可知——IDW 近似误差进入趋势项（approximate=True）",
                "常量协变量（零方差）结构化拒绝（DegenerateData）；至少 2 个协变量",
                "EPSG:3857 工作 CRS 的 Web Mercator 尺度畸变（与克里金同）",
            ],
            crs_class="PROJECTED_REQUIRED",
            approximate=True,
            scientific_preconditions=["min_numeric_samples:8", "nonzero_variance_required"],
            uncertainty_outputs=["raster_uncertainty", "validation_metrics"],
            random_seed_policy="deterministic",
            numerical_tolerance="平面协变量精确恢复（残差克里金方差精确为 0）；LOOCV 有界 200 点",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_regression_kriging.py::test_rk_plane_covariates_exact_zero_residual",
                "tests/unit/lib/test_regression_kriging.py::test_rk_variance_is_residual_kriging_variance_only",
                "tests/unit/lib/test_regression_kriging.py::test_rk_constant_covariate_rejected",
                "tests/unit/lib/test_regression_kriging.py::test_rk_requires_two_covariates",
                "tests/unit/lib/test_regression_kriging.py::test_rk_loocv_finite_and_deterministic",
                "tests/unit/lib/test_regression_kriging.py::test_rk_driver_deterministic_repeat",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.model_compare", name="插值模型比较", category="interpolation",
            capabilities=["interpolation_model_selection"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", runtime_status="native",
            # 所有方法的 CV 证据都不可得时推荐为空——仍输出诚实跳过表
            min_features=2,
            parameter_contract_ref="interpolation_model_compare",
            tool_candidates=["interpolation_model_compare"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=[],
            complexity="Σ 方法 CV 预算走查（cv_budget 上限；固定确定性顺序）",
            fallback_algorithms=["interpolation.idw"], priority=18,
            fallback_semantics={"interpolation.idw": "approximation"},
            algorithm_family="model_selection",
            method_references=["shepard1968", "matheron1963"],
            assumptions=[
                "以各方法库级 LOOCV/CV 证据（RMSE）排名——方法选择基于证据而非惯例",
                "固定方法序 + 预算序贯走查：完全确定性（同输入必同表）",
                "样本下限：idw≥2/tin≥4/trend≥6/rbf≥3/kriging≥20（各方法 CV 下限）",
                "平局按方法名打破（确定性推荐）",
            ],
            limitations=[
                "CV 证据是样本内泛化估计——极端外推场景不外推结论",
                "cv_budget 预算走查按固定方法序跳过后续方法（跳过原因逐行披露）",
                "比较运行于样本（无表面输出）；最终表面仍需调用对应插值工具",
            ],
            crs_class="GEOGRAPHIC_OK",
            uncertainty_outputs=["validation_metrics"],
            random_seed_policy="deterministic",
            numerical_tolerance="同输入逐字节一致的比较表；各方法指标与其库级实现逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_interpolation_compare.py::test_compare_deterministic_repeat",
                "tests/unit/lib/test_interpolation_compare.py::test_compare_recommendation_beats_idw_on_stationary_field",
                "tests/unit/lib/test_interpolation_compare.py::test_compare_sample_floor_skips_honest",
                "tests/unit/lib/test_interpolation_compare.py::test_compare_budget_cap_skips_disclosed",
                "tests/unit/lib/test_interpolation_compare.py::test_compare_rows_carry_validation_evidence",
            ],
            ),

        # ── Foundation V3（Geostatistics/Interpolation 批次）────────────

        AlgorithmDescriptor(
            id="interpolation.directional_variogram", name="方向变异函数", category="interpolation",
            capabilities=["variogram_analysis"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", runtime_status="native",
            # 单个滞后 bin 至少要有样本对支撑（≥8 点经验上才可读）
            min_features=8,
            parameter_contract_ref="directional_variogram_analysis",
            tool_candidates=["directional_variogram_analysis"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", compatible_map_models=[],
            complexity="O(pair_budget)（行步幅 20 万对上限）",
            algorithm_family="variography",
            method_references=["webster_oliver2007", "isaaks_srivastava1989"],
            assumptions=[
                "轴向（双向）配对过滤：方位角 +180° 属同一条轴，曲线逐位一致",
                "方位角为数学约定：0°=东(+x)、逆时针（与 anisotropy_angle 一致，非罗盘）",
                "滞后 bin 与全向 empirical_variogram 同一 span/edges 约定（tolerance=90° 时两者一致）",
                "配对行走沿用行步幅 max_pairs 预算（确定性）",
            ],
            limitations=[
                "单轴单次调用：完整各向异性椭圆需多方位角扫描（本工具不自动拟合椭圆）",
                "带宽过滤为 GSLIB band 语义近似（配对中点到轴线垂距）",
                "统计表输出（无表面）：结果供变异函数建模与各向异性诊断使用",
            ],
            crs_class="GEOGRAPHIC_OK",
            unit_requirements="meters",
            random_seed_policy="deterministic",
            numerical_tolerance="tolerance=90° 时与全向 empirical_variogram 逐位一致（conformance 固定）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geostat_v3.py::test_directional_variogram_anisotropy_discriminant",
                "tests/unit/lib/test_geostat_v3.py::test_directional_variogram_axis_bidirectional_and_omnidirectional_parity",
                "tests/unit/lib/test_geostat_v3.py::test_directional_variogram_input_guards",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.variogram_selection", name="变异函数模型选择", category="interpolation",
            capabilities=["variogram_analysis"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", runtime_status="native",
            # ≥4 个有效滞后 bin 才能支撑 3 参数拟合的模型比较
            min_features=8,
            parameter_contract_ref="variogram_selection_analysis",
            tool_candidates=["variogram_model_selection"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", compatible_map_models=[],
            complexity="6 家族 × 有界拟合 + AICc 排名（样本 ≤2000）",
            algorithm_family="model_selection",
            method_references=["webster_oliver2007", "matern1986"],
            assumptions=[
                "6 家族（spherical/exponential/gaussian/matern/wave/cubic）在同一经验变异函数上同台",
                "加权 RSS 即 fit_variogram 的拟合目标（样本对计数 σ-权重）——与 auto 选型同源",
                "AICc 自由度 k=3（sill/range/nugget）；matern k=4（固定平滑度 ν 计入，已披露）",
                "完全确定性：无随机重启，复用有界网格回退；平局按模型名打破",
            ],
            limitations=[
                "AICc 基于加权残差而非严格极大似然（信息准则是近似的，已披露）",
                "滞后 bin 数 n ≤ k+2 时 AICc 诚实取 inf（不伪造小样本准则）",
                "统计表输出（无表面）；选中模型需再传入 kriging 工具出表面",
            ],
            crs_class="GEOGRAPHIC_OK",
            unit_requirements="meters",
            random_seed_policy="deterministic",
            numerical_tolerance="同输入排名逐字节一致；各家族 RSS 与 fit_variogram 显式拟合逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geostat_v3.py::test_variogram_selection_ranks_deterministic_and_complete",
                "tests/unit/lib/test_geostat_v3.py::test_variogram_selection_aicc_disclosure_and_matern_k4",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.indicator_kriging", name="指示克里金", category="interpolation",
            capabilities=["indicator_kriging"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 与普通克里金同底（去重后 ≥8 点）
            min_features=8,
            parameter_contract_ref="indicator_kriging_analysis",
            tool_candidates=["indicator_kriging_surface"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            complexity="T × OK（T=阈值数 ≤20；概率面 T×H×W 内存线性放大）",
            approximation_class="exact",
            fallback_algorithms=["interpolation.kriging"], priority=23,
            fallback_semantics={"interpolation.kriging": "approximation"},
            algorithm_family="indicator_geostatistics",
            method_references=["journel1983", "matheron1963"],
            assumptions=[
                "逐阈值指示变换 I=1[z≤t] → 各自经验变异函数 + 拟合 → 指示场普通克里金",
                "variogram_model=auto 时逐阈值在全部 6 家族里按加权 RSS 选型（逐阈值披露）",
                "概率钳制 [0,1]：被钳制格数逐格计数（绝不静默）",
                "p50 阈值=每格首个 p≥0.5 的阈值；E-type 为离散中值近似（类代表值=阈值，保守）",
            ],
            limitations=[
                "逐阈值独立克里金不保证概率面在阈值间单调（P(Z≤t) 单调性未强制，已披露）",
                "常量指示场（阈值在样本值域之外）输出常量概率（无变异函数拟合）",
                "E-type 类内分布未建模——不是分位数中值的精确期望",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["min_numeric_samples:8"],
            uncertainty_outputs=["raster_uncertainty"],
            random_seed_policy="deterministic",
            numerical_tolerance="概率面 ∈ [0,1]（钳制计数披露）；同输入概率面逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geostat_v3.py::test_indicator_kriging_probability_bounds_and_p50_semantics",
                "tests/unit/lib/test_geostat_v3.py::test_indicator_kriging_constant_threshold_disclosed",
                "tests/unit/lib/test_geostat_v3.py::test_indicator_kriging_etype_and_guards",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.cokriging", name="协同克里金", category="interpolation",
            capabilities=["cokriging"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 与普通克里金同底 ≥8 主变量样本 + ≥2 次变量样本（ρ 估计）
            min_features=8,
            parameter_contract_ref="cokriging_analysis",
            tool_candidates=["cokriging_surface"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            complexity="协同定位系统 O(m·(k+2)³)（MM1 近似）",
            approximation_class="approximate",
            fallback_algorithms=["interpolation.kriging"], priority=24,
            fallback_semantics={"interpolation.kriging": "approximation"},
            approximate=True,
            algorithm_family="geostatistical_interpolation",
            method_references=["journel_huijbregts1978", "matheron1963"],
            assumptions=[
                "Markov Model 1 近似核化：交叉协方差 C_sy(h)=ρ·C_pp(h)（全交叉协方差未建模）",
                "协同定位近似：次变量仅在目标格点以单一数值进入克里金系统",
                "次变量缺失时取最近次变量值（精确协同定位格数披露）；C_ss(0)=主变量先验方差（标准化假设）",
                "ρ 缺省按最近配对 Pearson 估计；|ρ|<0.2 类型化拒绝（弱相关不会优于 OK，不输出）",
            ],
            limitations=[
                "次变量自身变异函数未拟合（MM1 缩放假设）",
                "次变量须与主变量共享同一工作 CRS；非协同定位部分由最近邻补格（近似）",
                "近似语义（approximate=True）：协同克里金理论收益依赖 MM1 假设成立",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["min_numeric_samples:8"],
            uncertainty_outputs=["raster_uncertainty"],
            random_seed_policy="deterministic",
            numerical_tolerance="系统批式求解（同 OK 稳定化策略）；方差钳 ≥0（负值计数披露）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geostat_v3.py::test_cokriging_beats_ok_loocv_with_correlated_secondary",
                "tests/unit/lib/test_geostat_v3.py::test_cokriging_weak_correlation_typed_error",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.nearest_neighbor", name="最近邻插值", category="interpolation",
            capabilities=["spatial_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            min_features=1,
            parameter_contract_ref="nearest_neighbor_analysis",
            tool_candidates=["nearest_neighbor_surface"],
            cpu_cost="low", memory_cost="medium", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            complexity="O(n log n + m)（cKDTree k=1 分段常值场）",
            approximation_class="exact",
            fallback_algorithms=["interpolation.idw"], priority=11,
            fallback_semantics={"interpolation.idw": "approximation"},
            algorithm_family="deterministic_interpolation",
            method_references=["thiessen1911"],
            assumptions=[
                "每个格点取最近样本值（cKDTree k=1）：输出为样本的 Voronoi（泰森）分段常值场",
                "无平滑：表面在单元边界处不连续（跳变是方法语义，非缺陷）",
                "全域有值：凸包外为最近样本外推（已披露，无不确定性声明）",
                "米制距离：地理输入经 estimate_utm_crs 自动投影（与 IDW 同 CRS 政策）",
            ],
            limitations=[
                "无理论方差，无残差验证证据（跳变场 LOOCV 无意义）",
                ">20 万样本 / >400 万目标格点类型化拒绝（先拒绝不 OOM）",
                "需要连续平滑表面时改用 IDW / kriging / 自然邻域",
            ],
            crs_class="GEOGRAPHIC_OK",
            random_seed_policy="deterministic",
            numerical_tolerance="每格值精确等于其最近样本值（无近似）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geostat_v3.py::test_nearest_neighbor_voronoi_semantics_exact",
                "tests/unit/lib/test_geostat_v3.py::test_nearest_neighbor_scale_guards_typed",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.natural_neighbor", name="自然邻域插值", category="interpolation",
            capabilities=["triangulation_interpolation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 三角剖分至少需要 3 个非共线点
            min_features=3,
            parameter_contract_ref="natural_neighbor_analysis",
            tool_candidates=["natural_neighbor_surface"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            complexity="Delaunay O(n log n) + Sibson 面积裁剪/ Watson walk 逐格",
            approximation_class="exact",
            fallback_algorithms=["interpolation.tin"], priority=19,
            fallback_semantics={"interpolation.tin": "approximation"},
            algorithm_family="deterministic_interpolation",
            method_references=["sibson1981", "watson1981"],
            assumptions=[
                "Sibson (1981) 坐标：权重=插入点窃取的 Voronoi 面积比例（精确多边形裁剪面积）",
                "Watson (1981) 阶梯 walk：外接圆包含格点的单形集合 = 自然邻域（邻接 walk 收集）",
                "精确插值器：过样本点（重合格点直接返回样本值，float64 精确）",
                "精确再现线性函数（平面场内部复现 ≤1e-6，conformance 固定）",
            ],
            limitations=[
                "凸包外 NaN——不外推（需要全域覆盖时改用 IDW/趋势面）",
                "近共线构型下 Sibson 权重几何呈长条：外墙自适应外扩保证面积精度（次数披露）",
                ">20 万样本 / >400 万目标格点类型化拒绝；逐格点 Python 裁剪成本高",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:3"],
            uncertainty_outputs=["validation_metrics"],
            random_seed_policy="deterministic",
            numerical_tolerance="样本点精确复现（float64）；平面场内部复现 ≤1e-6（conformance 固定）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geostat_v3.py::test_natural_neighbor_exact_at_samples",
                "tests/unit/lib/test_geostat_v3.py::test_natural_neighbor_plane_reproduction_and_nan_outside_hull",
                "tests/unit/lib/test_geostat_v3.py::test_natural_neighbor_scale_and_degenerate_guards",
            ],
            ),

        AlgorithmDescriptor(
            id="interpolation.block_kriging", name="块克里金", category="interpolation",
            capabilities=["block_kriging"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="terrain_surface", runtime_status="native",
            # 与普通克里金同底（去重后 ≥8 点）
            min_features=8,
            parameter_contract_ref="block_kriging_analysis",
            tool_candidates=["block_kriging_surface"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY", compatible_map_models=["raster_surface"],
            complexity="块离散化 2×2 + OK 系统（−γ̄(B,B) 修正）",
            approximation_class="approximate",
            fallback_algorithms=["interpolation.kriging"], priority=25,
            fallback_semantics={"interpolation.kriging": "approximation"},
            approximate=True,
            algorithm_family="geostatistical_interpolation",
            method_references=["isaaks_srivastava1989", "matheron1963"],
            assumptions=[
                "2×2 子点离散化近似块均值协方差（Isaaks & Srivastava 1989 惯例，近似已披露）",
                "LHS 保持点支撑样本-样本 γ；块支撑经 RHS γ̄(x,B) 与方差修正 −γ̄(B,B) 进入",
                "块尺寸→0 时收敛到点克里金（rtol 1e-3，conformance 固定）",
                "block_size=0 时按 H3 分辨率平均六边形边长自动取值（全局平均近似）",
            ],
            limitations=[
                "块尺寸相对变程越大，2×2 离散化近似误差越大（更高密度离散化未实现）",
                "块边界取矩形（H3 单元为六边形——以等面积方形近似，已披露）",
                "块方差 ≤ 点方差仅在平均意义上成立（个别格点可反超）",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["min_numeric_samples:8"],
            uncertainty_outputs=["raster_uncertainty"],
            random_seed_policy="deterministic",
            numerical_tolerance="小块（1e-9 m）与点克里金一致 rtol 1e-3；方差钳 ≥0（负值计数披露）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_geostat_v3.py::test_block_kriging_small_blocks_converge_to_point_kriging",
                "tests/unit/lib/test_geostat_v3.py::test_block_kriging_variance_not_larger_than_point",
            ],
            ),
]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────
# rbf_interpolation 契约随域包聚合（iter_contract_packs）；idw/kriging 契约
# 仍在中央 _SEED_CONTRACTS（历史种子，对本包只读）。
# Foundation V2 · A2：tin/trend_surface/regression_kriging/model_compare 契约
# 随域包新增；中央 kriging 契约由 v2 → v3（可选项 matern_smoothness /
# anisotropy_angle / anisotropy_ratio / cv_scheme / solve_backend，全默认值
# 零破坏）。

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="rbf_interpolation", version=1,
        description="径向基函数（RBF）插值：核 / 平滑 / 局部邻域 / H3 分辨率（米制投影下执行）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="插值数值字段名",
            ),
            ParameterSpec(
                name="kernel", type="enum", default="thin_plate_spline",
                enum_values=["thin_plate_spline", "linear", "cubic",
                             "quintic"],
                description="RBF 核（薄板样条默认；无 gaussian 核——病态风险）",
            ),
            ParameterSpec(
                name="smoothing", type="number", default=0.0,
                minimum=0.0, maximum=10.0,
                description="平滑系数；0=精确过样本点（精确插值器）",
            ),
            ParameterSpec(
                name="neighbors", type="integer", default=32,
                minimum=1, maximum=64, unit="count",
                description="局部 RBF 邻域样本数（KdTree 最近邻截断）",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
        ],
    ),
    ParameterContract(
        id="tin_interpolation", version=1,
        description="TIN 三角网插值：Delaunay 剖分上的 linear（C⁰）/clough_tocher（C¹），凸包外不外推。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="插值数值字段名",
            ),
            ParameterSpec(
                name="method", type="enum", default="linear",
                enum_values=["linear", "clough_tocher"],
                description="三角网插值方法：linear=重心线性（C⁰）；clough_tocher=三次（C¹）",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
            ParameterSpec(
                name="cross_validate", type="boolean", default=True,
                description="是否计算 LOOCV 验证指标（有界 500 点）",
            ),
        ],
    ),
    ParameterContract(
        id="trend_surface_analysis", version=1,
        description="全局多项式趋势面：OLS 拟合（单位盒缩放），R²/残差方差证据，bbox 外逐格标记外推。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="插值数值字段名",
            ),
            ParameterSpec(
                name="order", type="enum", default="1",
                enum_values=["1", "2", "3"],
                description="趋势面多项式阶数（1=平面 / 2=二次 / 3=三次；项增长守卫 order·(order+3)/2 ≤ n/3）",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
            ParameterSpec(
                name="cross_validate", type="boolean", default=True,
                description="是否计算 LOOCV 验证指标（有界 500 点）",
            ),
        ],
    ),
    ParameterContract(
        id="regression_kriging_analysis", version=1,
        description="回归克里金：OLS 趋势（协变量）+ 残差普通克里金；目标协变量由 IDW 近似（approximate 语义）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="插值数值字段名",
            ),
            ParameterSpec(
                name="explanatory_fields", type="string", required=True,
                description="协变量字段名（同一 FC 的属性；≥2 个，逗号分隔）",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
            ParameterSpec(
                name="variogram_model", type="enum", default="auto",
                enum_values=["auto", "spherical", "exponential", "gaussian", "matern"],
                data_dependent_default="variogram_least_rss",
                description="残差变异函数模型",
            ),
            ParameterSpec(
                name="neighbors", type="integer", default=12,
                minimum=1, maximum=24,
                description="残差克里金邻域样本数上限",
            ),
            ParameterSpec(
                name="cross_validate", type="boolean", default=True,
                description="是否计算全流程 LOOCV（有界 200 点）",
            ),
        ],
    ),
    ParameterContract(
        id="interpolation_model_compare", version=1,
        description="插值模型比较：各方法库级 LOOCV/CV 证据（RMSE 排名，确定性平局打破），跳过逐行披露。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="插值数值字段名",
            ),
            ParameterSpec(
                name="cv_budget", type="integer", default=2500,
                minimum=10, unit="count",
                description="总 CV 残差评估预算（超出按固定方法序跳过并披露）",
            ),
        ],
    ),
    # ── Foundation V3（Geostatistics/Interpolation 批次）────────────────
    ParameterContract(
        id="directional_variogram_analysis", version=1,
        description="方向变异函数：单轴（双向）角度过滤 + 可选带宽的经验半方差；方位角为数学约定（0°=东、逆时针）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="数值字段名",
            ),
            ParameterSpec(
                name="azimuth_deg", type="number", default=0.0,
                unit="degrees",
                description="轴方位角（数学约定 0°=东(+x)、逆时针；轴向双向，+180° 同轴）",
            ),
            ParameterSpec(
                name="tolerance_deg", type="number", default=22.5,
                minimum=1.0, maximum=90.0, unit="degrees",
                description="轴向半角（90=全向退化）",
            ),
            ParameterSpec(
                name="band_width", type="number", default=0.0,
                minimum=0.0, unit="meters",
                description="带宽：配对中点到轴线垂距上限（米；0=不限）",
            ),
            ParameterSpec(
                name="n_lags", type="integer", default=12,
                minimum=4, maximum=64, unit="count",
                description="滞后 bin 数",
            ),
        ],
    ),
    ParameterContract(
        id="variogram_selection_analysis", version=1,
        description="变异函数模型选择：6 家族同台拟合，加权 RSS 排名 + AICc（k=3；matern k=4，已披露）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="数值字段名",
            ),
            ParameterSpec(
                name="n_lags", type="integer", default=12,
                minimum=4, maximum=64, unit="count",
                description="滞后 bin 数",
            ),
            ParameterSpec(
                name="matern_smoothness", type="number", default=0.5,
                minimum=0.1, maximum=5.0, unit="ratio",
                description="Matérn 平滑度 ν（仅 matern 家族使用）",
            ),
        ],
    ),
    ParameterContract(
        id="indicator_kriging_analysis", version=1,
        description="指示克里金：逐阈值指示变换 + 独立变异函数 + OK，输出 P(Z≤t) 概率面与 p50 阈值面。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="数值字段名",
            ),
            ParameterSpec(
                name="thresholds", type="string", required=True,
                description="阈值列表（逗号分隔，自动排序去重）",
            ),
            ParameterSpec(
                name="variogram_model", type="enum", default="auto",
                enum_values=["auto", "spherical", "exponential", "gaussian"],
                data_dependent_default="variogram_least_rss",
                description="指示变异函数模型（auto=逐阈值 6 家族加权 RSS 选型）",
            ),
            ParameterSpec(
                name="k_neighbors", type="integer", default=16,
                minimum=2, maximum=24, unit="count",
                description="指示克里金邻域样本数",
            ),
            ParameterSpec(
                name="n_lags", type="integer", default=12,
                minimum=4, maximum=64, unit="count",
                description="经验变异函数 bin 数",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
            ParameterSpec(
                name="etype", type="boolean", default=False,
                description="是否输出 E-type 估计（类代表值=阈值本身，保守离散近似）",
            ),
        ],
    ),
    ParameterContract(
        id="cokriging_analysis", version=1,
        description="协同定位协同克里金（MM1 近似）：交叉结构=ρ×主变量结构，次变量仅在目标协同定位点进入系统。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="主变量数值字段名",
            ),
            ParameterSpec(
                name="secondary_field", type="string", required=True,
                description="次变量数值字段名（次要素集的属性）",
            ),
            ParameterSpec(
                name="correlation_rho", type="number", default=0.0,
                minimum=-1.0, maximum=1.0,
                description="主/次相关系数 ρ；0=由最近配对 Pearson 自动估计（|ρ|<0.2 结构化拒绝）",
            ),
            ParameterSpec(
                name="neighbors", type="integer", default=12,
                minimum=2, maximum=24, unit="count",
                description="主变量克里金邻域样本数",
            ),
            ParameterSpec(
                name="variogram_model", type="enum", default="auto",
                enum_values=["auto", "spherical", "exponential", "gaussian"],
                data_dependent_default="variogram_least_rss",
                description="主变量变异函数模型",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
        ],
    ),
    ParameterContract(
        id="nearest_neighbor_analysis", version=1,
        description="最近邻插值：Voronoi（泰森）分段常值场，无平滑、不连续；全域有值（凸包外为最近样本外推）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="数值字段名",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=8,
                minimum=6, maximum=9,
                description="H3 分辨率",
            ),
        ],
    ),
    ParameterContract(
        id="natural_neighbor_analysis", version=1,
        description="自然邻域插值（Sibson 坐标）：权重=被窃取 Voronoi 面积比例；凸包外不外推。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="数值字段名",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
        ],
    ),
    ParameterContract(
        id="block_kriging_analysis", version=1,
        description="块克里金：2×2 块离散化（Isaaks & Srivastava 1989），RHS 点-块平均 γ + 方差块内修正。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="数值字段名",
            ),
            ParameterSpec(
                name="block_size", type="number", default=0.0,
                minimum=0.0, unit="meters",
                description="块尺寸（米）；0=按 H3 分辨率平均六边形边长自动取值",
            ),
            ParameterSpec(
                name="neighbors", type="integer", default=12,
                minimum=2, maximum=24, unit="count",
                description="克里金邻域样本数",
            ),
            ParameterSpec(
                name="variogram_model", type="enum", default="auto",
                enum_values=["auto", "spherical", "exponential", "gaussian"],
                data_dependent_default="variogram_least_rss",
                description="变异函数模型",
            ),
            ParameterSpec(
                name="resolution", type="integer", default=7,
                minimum=5, maximum=9,
                description="H3 分辨率",
            ),
        ],
    ),
]
