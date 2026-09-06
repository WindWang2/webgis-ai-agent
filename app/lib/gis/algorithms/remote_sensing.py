"""遥感 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 remote_sensing 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：本包为光谱指数族 / 双时相变化检测 / SAR 补齐科学
元数据（出处 / CRS 类 / 前置条件 / 不确定性 / conformance 节点）与参数
契约。实现位于 app/lib/geo_analysis/{spectral,raster_change,sar_temporal,
sar_filter,sar_calibration,glcm,raster_pca,tasseled_cap}.py，
工具层（app/tools/remote_sensing.py）只做 validate → 调实现 → 挂证据块。

Foundation V2（A6）：sar.speckle_filter / sar.radiometric_calibration 由
planned 翻转为 native（实现落地 + 工具候选 + 完整契约 + conformance）；
原 planned 诚实披露**替换**为实现级披露（LUT / 热噪声 / MAP 变体近似），
不是删除诚实边界。新增 sar.glcm_texture / remote.pca / remote.tasseled_cap /
sar.temporal_composite。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="remote.ndvi", name="NDVI 植被指数", category="remote_sensing",
            capabilities=["ndvi"],
            input_artifact_types=["raster_surface", "terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["compute_ndvi", "compute_vegetation_index"],
            cpu_cost="medium", memory_cost="high", io_cost="medium",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=10,
            algorithm_family="spectral_index",
            method_references=["rouse1974"],
            assumptions=[
                "反射率需 0-1 定标；零分母→NaN（nodata 像元不稀释统计）",
                "在线路径按 STAC 波段语义取 B04/B08（显式角色映射，非位置猜测）",
            ],
            limitations=[
                "比值指数对线性缩放不变，但对云影/气溶胶/定标漂移敏感",
                "无大气校正补偿，跨期可比性依赖同一 L2A 产品线",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_spectral_engine.py::test_compute_index_array_ndvi",
                "tests/unit/test_spectral_engine.py::test_compute_ndvi_coverage_excludes_nodata",
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_family_hand_cases_exact",
            ],
        ),

        AlgorithmDescriptor(
            id="remote.change.raster", name="双时相栅格变化检测", category="remote_sensing",
            capabilities=["raster_change_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_raster_change"],
            cpu_cost="high", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD",
            compatible_map_models=["raster_surface"], priority=10,
            version="1.0",
            algorithm_family="change_detection",
            assumptions=[
                "A（T1）网格为基准，B 经 WarpedVRT 对齐；对齐事实进质量证据",
                "有效像元 = 双方都有效（任一 nodata → nodata）",
            ],
            limitations=[
                "差值法对配准/辐射差异敏感，无语义分类（变化≠地类转移）",
                "normalized_difference 零分母 → nodata（不产 inf）",
            ],
            crs_class="RASTER_GRID",
        ),

        # ── VNext：类型化光谱指数 / CVA / 比值变化 ─────────────────────
        AlgorithmDescriptor(
            id="remote.spectral_index", name="类型化光谱指数（11 公式族）", category="remote_sensing",
            capabilities=["spectral_index"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["compute_spectral_index"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="spectral_index",
            method_references=["rouse1974", "huete1988", "gao1996", "xu2006",
                               "zha_woodcock2003", "key_benson2006", "mcfeeters1996"],
            assumptions=[
                "波段按语义角色显式命名（band_map），绝不按波段位置猜测",
                "线性定标先于公式（DN/10000→反射率）；零分母→NaN",
                "超理论值域只报告不钳制（out_of_range_fraction）",
            ],
            limitations=[
                "公式出处逐指数声明（gndvi/msavi/ndmi 无词表出处，诚实留空）",
                "EVI/EVI2 常数项只在反射率单位下成立（#382）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["band_semantics_required"],
            numerical_tolerance="conformance fixture 上与手算精确一致（float64）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_family_hand_cases_exact",
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_zero_denominator_and_scale",
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_missing_role_typed_error",
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_dn_input_out_of_range_reported",
                "tests/unit/lib/test_tasseled_cap.py::test_spectral_evi2_hand_golden",
            ],
            parameter_contract_ref="spectral_index_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.cva", name="变化向量分析（CVA）", category="remote_sensing",
            capabilities=["raster_change_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_change_cva"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="change_detection",
            method_references=["malila1980"],
            assumptions=[
                "两景波段按语义角色对齐（缺角色拒绝，不按位置猜测）",
                "幅度=全角色欧氏范数；角度=固定角色序前两分量 atan2（弧度）",
                "同一像元任一角色任一期无效 → 输出 NaN",
            ],
            limitations=[
                "CVA 只给幅度/方向，不构成土地覆盖语义变化",
                "方向角依赖角色序约定——跨研究比较需披露所用角色序",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["band_semantics_required"],
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_change_detection_science.py::test_cva_two_band_hand_computed_exact",
                "tests/unit/lib/test_change_detection_science.py::test_cva_identical_scenes_zero_and_nodata",
                "tests/unit/lib/test_change_detection_science.py::test_cva_role_order_documented_and_asserted",
            ],
        ),

        AlgorithmDescriptor(
            id="remote.ratio_change", name="双时相比值变化", category="remote_sensing",
            capabilities=["raster_change_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_ratio_change"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="change_detection",
            assumptions=[
                "比值法适用于 SAR 后向散射/强度（同量纲输入）",
                "ratio：a/b，零分母→NaN；log_ratio：log(a)−log(b)（对数域对称）",
            ],
            limitations=[
                "比值不区分变化原因（物候/几何/定标漂移同权混合）",
                "log_ratio 输入须为正（线性强度或 dB）",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_change_detection_science.py::test_ratio_and_log_ratio_hand_exact",
                "tests/unit/lib/test_change_detection_science.py::test_log_ratio_symmetry_and_zeros",
            ],
            parameter_contract_ref="ratio_change_analysis",
        ),

        # ── VNext：SAR 域（时序/极化）─────────────────────────────────
        AlgorithmDescriptor(
            id="sar.temporal_stats", name="SAR 时序栈统计", category="remote_sensing",
            capabilities=["sar_analysis"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_temporal_stats"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="sar_temporal_statistics",
            assumptions=[
                "输入假定已几何校正并对齐；std 为总体标准差（ddof=0）",
                "nodata/NaN 逐切片剔除，剩余有效切片上统计（部分有效像元披露）",
                "CV=std/mean（可选）：|mean|≤1e-12 → NaN；dB 域 CV 无物理量纲（披露）",
                "percentiles（可选）=np.nanpercentile 线性插值，≤5 个 [0,100]",
            ],
            limitations=[
                "本工具无滤波/定标隐式前置——独立原生算法见 "
                "sar.speckle_filter/sar.radiometric_calibration",
                "栈深 ≤24、H·W ≤4096×4096，超限 ResourceScaleMismatch 先拒绝",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_science_vnext.py::test_sar_stack_statistics_hand_exact",
                "tests/unit/test_temporal_science_vnext.py::test_sar_stack_scale_guard",
                "tests/unit/lib/test_sar_filters_v2.py::test_sar_stats_cv_and_percentiles",
                "tests/unit/lib/test_sar_filters_v2.py::test_sar_stats_defaults_preserve_v1_shape",
            ],
            parameter_contract_ref="sar_temporal_stats_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.vh_ratio", name="SAR VV/VH 极化比", category="remote_sensing",
            capabilities=["sar_analysis"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_vh_ratio"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="sar_polarimetry",
            assumptions=[
                "VV/VH：线性域为比值、dB 域为 dB 差（VV−VH）；VH=0 → NaN",
                "同景双极化（如 Sentinel-1 VV+VH）",
            ],
            limitations=[
                "无辐射定标假定下仅作结构对比代理，非物理量",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_science_vnext.py::test_sar_vh_ratio_and_log_ratio_exact",
            ],
        ),

        AlgorithmDescriptor(
            id="sar.log_ratio_change", name="SAR 双时相对数比值变化", category="remote_sensing",
            capabilities=["sar_analysis"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_ratio_change"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=20,
            algorithm_family="change_detection",
            assumptions=[
                "log(a)−log(b)：对数域对称（增强=衰减镜像），SAR 双期惯用量",
                "经 detect_ratio_change 工具 method=log_ratio 参数执行",
            ],
            limitations=[
                "比值不区分变化原因；输入须为正（线性强度或 dB）",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_change_detection_science.py::test_log_ratio_symmetry_and_zeros",
            ],
        ),

        # ── Foundation V2（A6）：斑点滤波 / 辐射定标 planned→native ────
        AlgorithmDescriptor(
            id="sar.speckle_filter",
            name="SAR 斑点噪声滤波（Lee/Refined-Lee/Frost）",
            category="remote_sensing",
            capabilities=["sar_speckle_filtering"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_speckle_filter"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="sar_speckle_filtering",
            method_references=["lee1980", "lee1981", "lopes1990", "frost1982"],
            assumptions=[
                "斑点为乘性噪声（x=R·n）；输入须线性强度（非负，dB 被拒绝）",
                "ENL 显式参数优先；缺省整图矩估计 ENL=mean²/var（均匀假设，披露）",
                "窗口 ∈ {3,5,7}；窗口统计 nodata 感知（全无效窗口 → NaN）",
                "Frost：k=D·(CV·√ENL)²，w=exp(−k·d)，d=城市块距离（对称）",
            ],
            limitations=[
                "refined_lee 子窗选择为 MSE 代理（方差+中心偏差²）——非 Lopes 1990 完整 MAP 变体",
                "斑点抑制同时平滑真实纹理；不恢复被斑点淹没的像元信息",
                "边界：lee/refined_lee 窗口统计 reflect 补齐（frost 有效集归一）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1",
                                      "band_semantics_required"],
            numerical_tolerance="均匀合成斑点块 std(after)<std(before)；Lee 窗口手算精确一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_filters_v2.py::test_lee_hand_window_exact",
                "tests/unit/lib/test_sar_filters_v2.py::test_lee_noise_reduction_on_homogeneous_patch",
                "tests/unit/lib/test_sar_filters_v2.py::test_refined_lee_preserves_step_edge_better_than_lee",
                "tests/unit/lib/test_sar_filters_v2.py::test_speckle_scale_guard_enl_estimation_and_errors",
            ],
            parameter_contract_ref="sar_speckle_filter_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.radiometric_calibration",
            name="SAR 辐射定标（β⁰/σ⁰/γ⁰ 常数定标）",
            category="remote_sensing",
            capabilities=["sar_radiometric_calibration"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_calibrate"],
            cpu_cost="medium", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="sar_calibration",
            method_references=["oliver_quegan1998"],
            assumptions=[
                "标准定标关系：β⁰=I/K、σ⁰=β⁰·sin(θᵢ)、γ⁰=β⁰·tan(θᵢ)，I=DN²（振幅域）",
                "calibration_constant（K，如 Sentinel-1 A²/AUT）显式必需——缺失拒绝",
                "入射角：标量或逐像元平面（与网格同形），(0,90) 开区间（度）",
            ],
            limitations=[
                "逐像元定标 LUT 未实现（仅常数定标）——LUT 场景精度受限",
                "热噪声去除未实现（Sentinel-1 GRD 噪声底未扣，弱信号偏乐观）",
                "不修正地形起伏（无地形辐射校正/局部入射角模型）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1",
                                      "band_semantics_required"],
            numerical_tolerance="手算黄金值精确一致（amplitude 2, K=1 → β⁰=4；θ=30° → σ⁰=2.0）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_calibration_v2.py::test_calibration_hand_golden",
                "tests/unit/lib/test_sar_calibration_v2.py::test_calibration_intensity_db_and_all_products",
                "tests/unit/lib/test_sar_calibration_v2.py::test_calibration_missing_constant_typed_error",
                "tests/unit/lib/test_sar_calibration_v2.py::test_calibration_negative_dn_and_incidence_guards",
            ],
            parameter_contract_ref="sar_calibration_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.glcm_texture",
            name="GLCM 纹理特征（Haralick 窗口化）",
            category="remote_sensing",
            capabilities=["sar_texture"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_glcm_texture"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="glcm_texture",
            method_references=["haralick1973"],
            assumptions=[
                "量化：有效像元 2-98 分位线性拉伸到 levels 档（越界钳端点）",
                "对称约定 P+Pᵀ（±d 同线）；d=1；多方向=逐方向属性 NaN 感知均值",
                "entropy 为自然对数；纯 numpy 手工实现（scikit-image 非声明依赖）",
            ],
            limitations=[
                "零方差/无有效对窗口 → NaN（correlation 不伪造）",
                "操作规模 H·W·window²·n_dir ≤ 64M 估算上界，超限先拒绝",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1",
                                      "band_semantics_required"],
            numerical_tolerance="手算 4×4 单窗 GLCM 计数与属性精确一致（float64）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_glcm_texture.py::test_glcm_hand_window_counts_golden",
                "tests/unit/lib/test_glcm_texture.py::test_glcm_constant_window_correlation_nan",
                "tests/unit/lib/test_glcm_texture.py::test_glcm_symmetry_determinism_and_directions",
                "tests/unit/lib/test_glcm_texture.py::test_glcm_scale_guard_and_quantization",
            ],
            parameter_contract_ref="sar_glcm_texture_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.pca", name="波段栈 PCA（SVD 降维）", category="remote_sensing",
            capabilities=["raster_dimensionality_reduction"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["raster_pca"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="dimensionality_reduction",
            assumptions=[
                "标准 SVD/PCA 无单一经典出处声明——method_references 诚实留空",
                "公共有效掩膜：任一波段无效 → 整行剔除（非 pairwise-complete）",
                "standardize=False 协方差 PCA / True 相关矩阵 PCA（方差 ddof=1）",
            ],
            limitations=[
                "无流式实现：n_bands·H·W ≤ 16M 像元，超限先拒绝（不假装可扩展）",
                "载荷符号不唯一（SVD 符号约定）——跨运行比较需固定实现版本",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "min_numeric_samples:8"],
            numerical_tolerance="秩 1 栈 explained_variance_ratio=[1,0,0]（1e-12）；载荷与 numpy eig 一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_raster_pca_v2.py::test_pca_rank1_explained_variance_exact",
                "tests/unit/lib/test_raster_pca_v2.py::test_pca_loadings_match_numpy_eig_reference",
                "tests/unit/lib/test_raster_pca_v2.py::test_pca_orthogonality_and_reconstruction",
                "tests/unit/lib/test_raster_pca_v2.py::test_pca_common_mask_and_scale_guard",
            ],
            parameter_contract_ref="raster_pca_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.tasseled_cap",
            name="Tasseled Cap 冠层变换（传感器系数注册表）",
            category="remote_sensing",
            capabilities=["tasseled_cap_transformation"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["tasseled_cap"],
            cpu_cost="low", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="spectral_transform",
            method_references=["crist_cicone1984", "baig2014", "shi_xu2019"],
            assumptions=[
                "系数行按传感器显式注册（landsat5_tm/landsat8_oli/sentinel2）",
                "波段按六语义角色显式映射（blue/green/red/nir/swir1/swir2）",
                "reflectance_domain 仅披露（baig2014/shi_xu2019 于 at-satellite 推导）",
            ],
            limitations=[
                "线性变换不改信息总量（3 轴是 6 波段旋转投影，非独立观测）",
                "未注册传感器显式拒绝（不默认套用他传感器系数）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["band_semantics_required",
                                      "raster_band_required:6"],
            numerical_tolerance="单像元手算 Σ coef·band 与实现精确一致（1e-12）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_tasseled_cap.py::test_tasseled_cap_hand_golden_all_sensors",
                "tests/unit/lib/test_tasseled_cap.py::test_tasseled_cap_unknown_sensor_and_missing_role",
            ],
            parameter_contract_ref="tasseled_cap_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.temporal_composite",
            name="SAR 时序栈合成（mean/median/percentile）",
            category="remote_sensing",
            capabilities=["sar_analysis"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_temporal_composite"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="sar_temporal_statistics",
            method_references=["oliver_quegan1998"],
            assumptions=[
                "时间维聚合为描述性合成（median 为斑点拖尾下的鲁棒惯用）",
                "nodata/NaN 逐切片剔除；全切片无效像元 → NaN（披露）",
            ],
            limitations=[
                "无滤波/定标隐式前置（独立原生算法见 sar.speckle_filter 等）",
                "栈深 ≤24、H·W ≤4096×4096，超限 ResourceScaleMismatch 先拒绝",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_filters_v2.py::test_sar_temporal_composite_median_exact",
                "tests/unit/lib/test_sar_filters_v2.py::test_sar_temporal_composite_methods_and_guards",
            ],
            parameter_contract_ref="sar_temporal_composite_analysis",
        ),
]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="spectral_index_analysis", version=2,
        description="类型化光谱指数：指数 id（INDEX_FAMILY 11 成员；波段按角色显式命名）。",
        parameters=[
            ParameterSpec(
                name="index_id", type="enum", required=True,
                enum_values=["ndvi", "gndvi", "savi", "msavi", "ndwi",
                             "mndwi", "ndbi", "ndmi", "nbr", "evi", "evi2"],
                description="光谱指数 id（公式出处随结果披露）",
            ),
        ],
    ),
    ParameterContract(
        id="ratio_change_analysis", version=1,
        description="双时相比值变化：ratio（a/b）或 log_ratio（SAR 惯用）。",
        parameters=[
            ParameterSpec(
                name="method", type="enum", default="ratio",
                enum_values=["ratio", "log_ratio"],
                description="ratio=a/b（零分母→NaN）；log_ratio=log(a)−log(b)（对数域对称）",
            ),
        ],
    ),
    ParameterContract(
        # v2（Foundation V2 · A6 additive）：可选 include_cv / percentiles
        # ——默认关闭（历史输出形状不变）；percentiles 以逗号分隔字符串
        # 过 JSON 通道（≤5 个、0-100）。
        id="sar_temporal_stats_analysis", version=2,
        description="SAR 时序栈统计量（时间维聚合；规模守卫 T≤24、H·W≤4096²）。",
        parameters=[
            ParameterSpec(
                name="product", type="enum", default="mean",
                enum_values=["mean", "std", "min", "max", "range"],
                description="统计量（std 为总体标准差 ddof=0）",
            ),
            ParameterSpec(
                name="include_cv", type="boolean", default=False,
                description="追加 CV=std/mean（|mean|≤1e-12 → NaN；描述性披露）",
            ),
            ParameterSpec(
                name="percentiles", type="string", default="",
                description="逗号分隔分位数（如 '10,50,90'；≤5 个，0-100；空=不计算）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_speckle_filter_analysis", version=1,
        description="SAR 斑点滤波：Lee/Refined-Lee/Frost 局部统计 MMSE（ENL 显式优先，缺省矩估计披露）。",
        parameters=[
            ParameterSpec(
                name="filter", type="enum", default="lee",
                enum_values=["lee", "refined_lee", "frost"],
                description="滤波器（公式随结果披露）",
            ),
            ParameterSpec(
                name="window", type="enum", default="3",
                enum_values=["3", "5", "7"],
                description="奇数窗口（像元）",
            ),
            ParameterSpec(
                name="enl", type="number", unit="ratio",
                description="等效视数（>0；缺省整图矩估计 ENL=mean²/var）",
            ),
            ParameterSpec(
                name="damping", type="number", default=1.0, minimum=0.5, maximum=5.0,
                description="Frost 阻尼参数 D（仅 frost 使用）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_calibration_analysis", version=1,
        description="SAR 辐射定标：DN→β⁰/σ⁰/γ⁰ 常数定标（定标常数显式必需；LUT/热噪声未实现披露）。",
        parameters=[
            ParameterSpec(
                name="calibration_constant", type="number", required=True,
                unit="unitless",
                description="定标常数 K（如 Sentinel-1 A²/AUT）——绝不虚构",
            ),
            ParameterSpec(
                name="incidence_deg", type="number", unit="degrees",
                description="标量入射角（度，0-90 开区间；σ⁰/γ⁰ 必需）",
            ),
            ParameterSpec(
                name="input_domain", type="enum", default="dn_intensity",
                enum_values=["dn_amplitude", "dn_intensity"],
                description="dn_amplitude 先平方为强度（披露）",
            ),
            ParameterSpec(
                name="output_product", type="enum", default="sigma0",
                enum_values=["sigma0", "beta0", "gamma0", "all"],
                description="输出产品（all=三产品全出）",
            ),
            ParameterSpec(
                name="to_db", type="boolean", default=False,
                description="输出 10·log₁₀（强度量纲惯例）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_glcm_texture_analysis", version=1,
        description="GLCM 窗口纹理（Haralick 1973；量化 2-98 分位；P+Pᵀ 对称；多方向均值）。",
        parameters=[
            ParameterSpec(
                name="window", type="enum", default="3",
                enum_values=["3", "5", "7"],
                description="奇数窗口（像元）",
            ),
            ParameterSpec(
                name="levels", type="enum", default="16",
                enum_values=["8", "16", "32", "64"],
                description="量化档数",
            ),
            ParameterSpec(
                name="directions", type="enum", default="all4",
                enum_values=["all4", "0", "45", "90", "135"],
                description="d=1 偏移方向集（all4=四方向均值）",
            ),
            ParameterSpec(
                name="properties", type="string", default="all",
                description="逗号分隔属性子集或 'all'（contrast,dissimilarity,homogeneity,asm,energy,entropy,mean,variance,correlation）",
            ),
        ],
    ),
    ParameterContract(
        id="raster_pca_analysis", version=1,
        description="波段栈 PCA（SVD；公共有效掩膜；协方差/相关 PCA 披露）。",
        parameters=[
            ParameterSpec(
                name="standardize", type="boolean", default=False,
                description="True=相关矩阵 PCA（逐波段 z-score）；False=协方差 PCA",
            ),
            ParameterSpec(
                name="n_components", type="integer", minimum=1,
                description="输出分量栅格数 k（≤ n_bands；缺省 = n_bands）",
            ),
        ],
    ),
    ParameterContract(
        id="tasseled_cap_analysis", version=1,
        description="Tasseled Cap 冠层变换（传感器系数注册表；六语义角色显式映射）。",
        parameters=[
            ParameterSpec(
                name="sensor", type="enum", required=True,
                enum_values=["landsat5_tm", "landsat8_oli", "sentinel2"],
                description="传感器（系数行出处随结果披露）",
            ),
            ParameterSpec(
                name="reflectance_domain", type="enum", default="at_satellite",
                enum_values=["surface", "at_satellite"],
                description="反射率域披露（系数推导域错配不静默）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_temporal_composite_analysis", version=1,
        description="SAR 时序栈合成（mean/median/percentile；nodata 感知时间维聚合）。",
        parameters=[
            ParameterSpec(
                name="method", type="enum", default="mean",
                enum_values=["mean", "median", "percentile"],
                description="合成方法（percentile 需显式 percentile 参数）",
            ),
            ParameterSpec(
                name="percentile", type="number", minimum=0.0, maximum=100.0,
                description="分位数（method=percentile 时必需）",
            ),
        ],
    ),
]
