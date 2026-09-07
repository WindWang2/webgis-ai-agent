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
            # V3：为无元数据描述符补齐诚实披露（A5）——实现为
            # raster_change.py 差值族（窗口化写入 + 对齐证据）。
            assumptions=[
                "A（T1）网格为基准，B 经 WarpedVRT 对齐；对齐事实进质量证据",
                "有效像元 = 双方都有效（任一 nodata → nodata）",
                "差值/绝对差为逐像元辐射差，不构成语义分类",
            ],
            limitations=[
                "差值法对配准/辐射差异敏感，无语义分类（变化≠地类转移）",
                "normalized_difference 零分母 → nodata（不产 inf）",
                "无云/阴影 QC（跨期云污染进入差值，见 remote.cloud_qc 基础）",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_change_detection_pixel.py::test_partial_nodata_masked_per_pixel",
                "tests/unit/lib/test_change_detection_science.py::test_change_science_determinism",
            ],
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
        # ── Foundation V3（additive）：+gamma_map/kuan（契约 v2）────────
        AlgorithmDescriptor(
            id="sar.speckle_filter",
            name="SAR 斑点噪声滤波（Lee/Refined-Lee/Frost/Gamma MAP/Kuan）",
            category="remote_sensing",
            capabilities=["sar_speckle_filtering"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_speckle_filter"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="sar_speckle_filtering",
            method_references=["lee1980", "lee1981", "lopes1990", "frost1982",
                               "kuan1985", "lee_jurkevich1994"],
            assumptions=[
                "斑点为乘性噪声（x=R·n）；输入须线性强度（非负，dB 被拒绝）",
                "ENL 显式参数优先；缺省整图矩估计 ENL=mean²/var（均匀假设，披露）",
                "窗口 ∈ {3,5,7}；窗口统计 nodata 感知（全无效窗口 → NaN）",
                "Frost：k=D·(CV·√ENL)²，w=exp(−k·d)，d=城市块距离（对称）",
                "gamma_map 三分支（均匀→m / 点目标→x / MAP 方程）+ Newton 迭代"
                "（闭式正根初始化，上限 max_iterations，iterations_used 披露）",
                "kuan 闭式 MMSE：k=(1−Cu²/Cv²)/(1+Cu²)，Cu²=1/ENL（均匀窗 → m）",
            ],
            limitations=[
                "refined_lee 子窗选择为 MSE 代理（方差+中心偏差²）——非 Lopes 1990 完整 MAP 变体",
                "gamma_map 为 Lopes 1990 / Lee & Jurkevich 1994 滤波核实现——"
                "不含完整先验结构比模型；发散像元冻结上一迭代（确定性披露）",
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
                "tests/unit/lib/test_sar_v3.py::test_gamma_map_newton_edge_and_noise",
                "tests/unit/lib/test_sar_v3.py::test_kuan_closed_form_hand_exact",
            ],
            parameter_contract_ref="sar_speckle_filter_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.radiometric_calibration",
            name="SAR 辐射定标（β⁰/σ⁰/γ⁰；标量 K + 入射角 LUT）",
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
                "入射角：标量或逐像元 2D LUT（与网格同形，(0,90) 开区间；"
                "lut_pixels 披露；V3 additive）",
            ],
            limitations=[
                "定标常数 K 为标量——σ⁰ 逐像元定标 LUT（SAFE annotation XML）"
                "不解析（入射角 LUT 已支持）",
                "热噪声去除为独立算法 sar.thermal_noise_removal"
                "（本工具不做隐式前置/后置）",
                "不修正地形起伏（地形辐射校正见独立算法 sar.rtc）",
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
                "tests/unit/lib/test_sar_v3.py::test_calibration_incidence_lut_and_v2_disclosure",
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

        # ── Foundation V3：遥感 V3 批次（波段栈科学算法族）────────────
        AlgorithmDescriptor(
            id="remote.mnf", name="最小噪声分数变换（MNF）", category="remote_sensing",
            capabilities=["mnf_transform"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["mnf_transform"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="dimensionality_reduction",
            method_references=["green1988"],
            assumptions=[
                "噪声协方差由水平/垂直一阶差分估计（(C_h+C_v)/4，差分加倍校正披露）",
                "白化空间噪声方差=1，SNR_i = λ_i − 1（λ 为白化 PCA 特征值 ddof=1）",
                "公共有效掩膜：任一波段无效 → 整行剔除（非 pairwise-complete）",
            ],
            limitations=[
                "无流式实现：n_bands·H·W ≤ 16M 像元，超限先拒绝",
                "常量/共线波段使噪声协方差奇异 → DegenerateData（诚实拒绝）",
                "分量/载荷代数符号依 LAPACK 约定（同一构建内稳定）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "min_numeric_samples:8"],
            numerical_tolerance="全分量逆变换重建误差 ≤1e-9；秩 1 确定性栈 → DegenerateData",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_mnf_signal_dominates_first_component",
                "tests/unit/lib/test_rs_v3.py::test_mnf_rank1_deterministic_degenerate_data",
                "tests/unit/lib/test_rs_v3.py::test_mnf_inverse_reconstruction_and_denoise",
                "tests/unit/lib/test_rs_v3.py::test_mnf_common_mask_nodata_and_determinism",
            ],
            parameter_contract_ref="mnf_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.ica", name="独立成分分析（FastICA）", category="remote_sensing",
            capabilities=["ica_transform"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["ica_transform"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="dimensionality_reduction",
            method_references=["hyvarinen1999"],
            assumptions=[
                "源信号统计独立且非高斯（FastICA 负熵代理）；whiten=unit-variance",
                "random_state=42 固定（fixed_seed）；收敛性显式披露不静默",
                "公共有效掩膜；n_valid ≥ max(8, k+2)（whiten 数值下限）",
            ],
            limitations=[
                "分量序与符号不唯一（ICA 固有）——跨运行比较需固定实现版本",
                "未收敛（max_iter 内）→ converged=false 披露，分量非稳定估计",
            ],
            crs_class="RASTER_GRID",
            random_seed_policy="fixed_seed",
            scientific_preconditions=["raster_band_required:2",
                                      "min_numeric_samples:8"],
            numerical_tolerance="固定种子重放逐位一致；两源线性混合恢复 |corr|>0.99",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_ica_recovers_mixed_sources",
                "tests/unit/lib/test_rs_v3.py::test_ica_convergence_disclosure_and_determinism",
            ],
            parameter_contract_ref="ica_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.sam", name="光谱角制图（SAM）", category="remote_sensing",
            capabilities=["spectral_target_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["spectral_angle_mapper"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="spectral_target_detection",
            method_references=["kruse1993"],
            assumptions=[
                "θ=arccos(⟨x,e⟩/(‖x‖·‖e‖))（弧度缺省，degrees 可选）",
                "端元向量与波段序逐波段对齐（band_order 披露，不按位置猜测）",
                "零范数像元（无亮度）→ NaN；零范数端元全 NaN 并披露",
            ],
            limitations=[
                "只度量光谱形状（对亮度增益不变），不区分亮度差异",
                "argmin 类别仅在有有限角度的端元上取（全 NaN → NaN）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "band_semantics_required"],
            numerical_tolerance="像元==端元 → 0 角（1e-7，arccos 近 1 条件数披露）；正交 → π/2",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_sam_hand_pixel_zero_angle",
                "tests/unit/lib/test_rs_v3.py::test_sam_zero_norm_nan_and_input_guards",
            ],
            parameter_contract_ref="sam_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.sid", name="光谱信息散度（SID）", category="remote_sensing",
            capabilities=["spectral_target_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["spectral_information_divergence"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="spectral_target_detection",
            method_references=["chang2000"],
            assumptions=[
                "对称形式 D(x,e)=Σp·ln(p/q)+Σq·ln(q/p)，p=x/Σx、q=e/Σe",
                "像元/端元出现非正分量或非正和 → NaN（熵在非正测度无定义）",
                "要求反射率类正值输入（SAR dB 等不适用，披露）",
            ],
            limitations=[
                "非正分量占比仅报告（nonpositive_fraction），不做钳制",
                "SID 比 SAM 对分布差异更敏感，但对定标噪声同样敏感",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "band_semantics_required"],
            numerical_tolerance="手算 2 波段 x=[1,1], e=[1,3] → D≈0.274653（rel 1e-12）；D(x,x)=0",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_sid_hand_two_band_exact",
                "tests/unit/lib/test_rs_v3.py::test_sid_symmetry_and_nonpositive_nan",
            ],
            parameter_contract_ref="sid_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.matched_filter", name="匹配滤波目标检测", category="remote_sensing",
            capabilities=["spectral_target_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["matched_filter"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="spectral_target_detection",
            method_references=["boardman1995"],
            assumptions=[
                "score = tᵀΣ⁻¹(x−μ)/(tᵀΣ⁻¹t)；μ/Σ 由全场景公共有效像元估计",
                "纯目标像元得分≈1、背景≈0（丰度式解读）",
                "目标向量与波段序逐波段对齐（band_order 披露）",
            ],
            limitations=[
                "单高斯背景假设——强背景结构会污染白化统计",
                "零方差波段剔除（dropped_bands 披露）；pinv 伪逆数值稳定",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "band_semantics_required",
                                      "min_numeric_samples:8"],
            numerical_tolerance="嵌入幅值 A 的目标像元得分≈A（rel 0.2）；固定数据重放一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_matched_filter_embedded_target",
                "tests/unit/lib/test_rs_v3.py::test_matched_filter_guards",
            ],
            parameter_contract_ref="matched_filter_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.rx_anomaly", name="RX 全局异常检测", category="remote_sensing",
            capabilities=["rx_anomaly_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["rx_anomaly"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="anomaly_detection",
            method_references=["reed1990"],
            assumptions=[
                "δ(x)=√((x−μ)ᵀΣ_r⁻¹(x−μ))；Σ_r = Σ + regularize·(tr Σ/k)·I",
                "单高斯全局背景假设（局部 RX/核 RX 未实现，披露）",
                "阈值 mean(δ)+k·σ(δ) 为启发式建议（k 显式参数，非假设检验）",
            ],
            limitations=[
                "≥3 波段推荐（2 波段可运行但背景估计弱）；常量场 → δ=0 披露",
                "异常≠语义目标——δ 高只说明偏离全局统计",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "min_numeric_samples:8"],
            numerical_tolerance="注入异常像元 δ > 阈值；常量场 δ≡0 且无异常（披露路径）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_rx_anomaly_outlier_above_threshold",
                "tests/unit/lib/test_rs_v3.py::test_rx_uniform_and_degenerate_paths",
            ],
            parameter_contract_ref="rx_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.mad_change", name="MAD / IR-MAD 变化检测", category="remote_sensing",
            capabilities=["raster_change_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["mad_change"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="change_detection",
            method_references=["nielsen1998"],
            assumptions=[
                "两期栈各自标准化 → SVD-CCA → MAD_i = a_i·X − b_i·Y（ρ 升序）",
                "χ² 栅格自由度按 2k 约定披露；ρ 钳制 ≤1−1e-12（恒等场景防 0/0）",
                "IR-MAD 权重 w=1/χ²（均值归一 + 下限 1e-4），固定点迭代 ≤10",
            ],
            limitations=[
                "对逐波段线性辐射偏移/增益不变（标准化吸收）——检测结构变化",
                "完整 IR-MAD 的 no-change 概率优化未实现（简化重加权披露）",
                "波段共线/常量 → DegenerateData（CCA 要求满秩场景协方差）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "min_numeric_samples:8"],
            numerical_tolerance="恒等两期 χ²<1e-9；局部变化块 χ² 显著高于未变像元（固定种子）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_mad_identical_stacks_zero_chi2",
                "tests/unit/lib/test_rs_v3.py::test_mad_localized_change_detected",
                "tests/unit/lib/test_rs_v3.py::test_mad_irmad_iterations_and_guards",
            ],
            parameter_contract_ref="mad_change_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.segmentation", name="图像分割（k-means 基座）", category="remote_sensing",
            capabilities=["image_segmentation"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["segment_image"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="image_segmentation",
            method_references=["lloyd1982"],
            assumptions=[
                "特征 = 标准化光谱波段 + 归一化坐标·spatial_weight·compactness",
                "KMeans(random_state=42, n_init=10)（确定性；Lloyd 1982 惯用法）",
                "段数 > 有效像元数 → 钳制并披露（realized < requested）",
            ],
            limitations=[
                "flat-color k-means 基座，非 SLIC 超像素：compactness 只是空间特征"
                "权重乘子（无几何紧致约束）、无 watershed 精化（诚实边界）",
                "常量波段不进特征（剔除披露）；全常量 → 仅按坐标分割",
            ],
            crs_class="RASTER_GRID",
            random_seed_policy="fixed_seed",
            scientific_preconditions=["raster_band_required:1",
                                      "min_numeric_samples:8"],
            numerical_tolerance="双块图像 2 段分离两半；固定种子重放逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_segment_two_blocks_and_determinism",
                "tests/unit/lib/test_rs_v3.py::test_segment_clamp_and_disclosure",
            ],
            parameter_contract_ref="segmentation_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.endmember_vca", name="端元提取（VCA）", category="remote_sensing",
            capabilities=["endmember_extraction"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["extract_endmembers_vca"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=20,
            algorithm_family="endmember_extraction",
            method_references=["nascimento2005"],
            assumptions=[
                "纯像元假设——恢复端元 = 原始像元光谱；随机投影固定 seed",
                "SVD 降维（均值正交补取前 m−1 维）+ 逐顶点随机投影选择",
                "末顶点用已选顶点仿射包法向（带符号极值距离比较）",
            ],
            limitations=[
                "EXPERIMENTAL：论文完整实现的简化确定性变体（披露），结果需人工核验",
                "守卫：2 ≤ n_endmembers < n_bands（降维到 m−1 维的实现约定）",
                "无纯像元的场景（强混合）恢复端元为近似（凸包顶点，非真实端元）",
            ],
            crs_class="RASTER_GRID",
            random_seed_policy="fixed_seed",
            scientific_preconditions=["raster_band_required:2",
                                      "min_numeric_samples:8"],
            numerical_tolerance="纯像元合成恢复端元（归一化后）与真值距离 <1e-6；seed 重放逐位一致",
            scientific_status="EXPERIMENTAL",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_vca_pure_pixels_recovered",
                "tests/unit/lib/test_rs_v3.py::test_vca_guards_and_determinism",
            ],
            parameter_contract_ref="endmember_vca_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.band_correlation", name="波段×波段相关表", category="remote_sensing",
            capabilities=["band_statistics"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="stats_table",
            tool_candidates=["band_correlation_table"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="band_statistics",
            assumptions=[
                "Pearson 相关（ddof=1 协方差）；公共有效掩膜（非 pairwise-complete）",
                "逐对样本数恒等于公共有效像元数（约定披露）",
                "standardize 不改变 Pearson r（线性不变性，诚实披露非双结果）",
            ],
            limitations=[
                "线性相关不捕获非线性关联；零方差波段行列 NaN（不伪造）",
                "公共掩膜 vs 逐对完整计算的差异在低重叠场景显著（披露）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "min_numeric_samples:8"],
            numerical_tolerance="完全相关对 r=1.0、反相 r=−1.0（1e-12）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_band_correlation_perfect_and_anti",
            ],
            parameter_contract_ref="band_correlation_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.temporal_features", name="时序特征提取", category="remote_sensing",
            capabilities=["temporal_feature_extraction"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["temporal_features"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="temporal_statistics",
            assumptions=[
                "栈第 0 轴 = 时间序；std 为总体标准差（ddof=0，披露）",
                "谐波 = [1, t, sin(2πt), cos(2πt)] 联合 LS（单周期 = 栈跨度）",
                "谐波要求完整序列 + 满秩设计（T≥4），否则 NaN（披露）",
            ],
            limitations=[
                "无物候模型拟合（无双谐波/SG 滤波/物候期提取）——诚实边界",
                "first−last 对首尾无效像元 → NaN；min/max/mean 对有限切片 nan-aware",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2"],
            numerical_tolerance="单调斜坡 amplitude/first−last/mean/std 手算精确（1e-12）；T=4 正弦幅值≈1",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_temporal_features_ramp_golden",
                "tests/unit/lib/test_rs_v3.py::test_temporal_features_harmonic_sin",
            ],
            parameter_contract_ref="temporal_features_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.robust_normalize", name="稳健波段/场景归一化", category="remote_sensing",
            capabilities=["radiometric_normalization"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["robust_normalize"],
            cpu_cost="low", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="radiometric_normalization",
            assumptions=[
                "percentile_stretch：逐波段 [2,98] 分位（可调）线性拉伸到 [0,1]",
                "percentile_match：源分位拉伸后重缩放到参考栈同序波段分位区间",
                "NaN-aware 分位（np.nanpercentile）；逐波段所用分位完整披露",
            ],
            limitations=[
                "对线性增益/偏移不变——非线性辐射差异（直方图形状）不校正",
                "常量波段（分位区间 0）→ DegenerateData；绝对辐射语义不保留",
                "相对归一化：非伪不变目标（PIF）/直方图匹配全量实现",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            numerical_tolerance="B=3A+10 经 match(ref=A) 与 A 自匹配逐位一致（1e-12）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_robust_normalize_linear_invariance",
                "tests/unit/lib/test_rs_v3.py::test_robust_normalize_stretch_and_guards",
            ],
            parameter_contract_ref="robust_normalize_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.cloud_qc", name="云 QC 基础咨询（亮度阈值）", category="remote_sensing",
            capabilities=["cloud_qc_advisory"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["cloud_qc_basic"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=20,
            algorithm_family="cloud_masking",
            assumptions=[
                "brightness=(red+nir)/2；阈值=显式绝对值或缺省场景 97.5 百分位",
                "可选 |NDVI| ≤ ndvi_max_abs 条件（云光谱平坦）；零分母不进条件",
                "qc_mask=True=疑似云（advisory）——非云概率产品",
            ],
            limitations=[
                "EXPERIMENTAL：非 Fmask/cloud-probability——无热红外、卷云、"
                "视差/多时相检验（强披露）",
                "亮地物（屋顶/沙地/雪/旱地）误报；云影不检测",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2",
                                      "band_semantics_required"],
            numerical_tolerance="注入亮块全标记；清洁场景 suspect_fraction<0.05（固定种子）",
            scientific_status="EXPERIMENTAL",
            conformance_tests=[
                "tests/unit/lib/test_rs_v3.py::test_cloud_qc_bright_block_and_clear_scene",
            ],
            parameter_contract_ref="cloud_qc_analysis",
        ),

        # ── Foundation V3：SAR 批次（热噪声 / 量纲换算 / MT-Lee / ───────
        # ── 相干性（EXPERIMENTAL）/ RTC / 叠掩阴影 / ENL 图）────────────
        AlgorithmDescriptor(
            id="sar.thermal_noise_removal",
            name="SAR 热噪声去除（噪声底/LUT 相减）",
            category="remote_sensing",
            capabilities=["sar_radiometric_calibration"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_remove_thermal_noise"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=20,
            algorithm_family="sar_calibration",
            method_references=["oliver_quegan1998"],
            assumptions=[
                "I_dn = max(I − N, 0)：噪声项 N 为标量噪声底或同形逐像元 LUT（互斥）",
                "输入须线性强度（非负；dB 输入被拒绝）",
                "去噪后负值钳 0（clamped_pixels 计数披露）",
            ],
            limitations=[
                "不解析 Sentinel-1 SAFE annotation XML（denoising 需逐 swath "
                "插值）——仅接收已提取的噪声底/LUT",
                "钳 0 使弱信号像元强度统计右偏（正偏披露，不静默）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1",
                                      "band_semantics_required"],
            numerical_tolerance="标量噪声底相减手算精确一致（float64）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_v3.py::test_thermal_noise_floor_removal_exact",
                "tests/unit/lib/test_sar_v3.py::test_thermal_noise_guards",
            ],
            parameter_contract_ref="sar_thermal_noise_removal_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.log_scaling",
            name="SAR 量纲换算（振幅/强度/dB 恒等式）",
            category="remote_sensing",
            capabilities=["sar_radiometric_calibration"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_log_scale"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=20,
            algorithm_family="sar_calibration",
            assumptions=[
                "纯代数恒等式：I=A²、A=√I、dB=10·log₁₀(x)、x=10^(dB/10)",
                "round-trip 精确（float64 恒等；测试锁定）",
                "线性→dB 对 ≤0 钳 ε=1e-12 下限（计数披露，非静默）",
            ],
            limitations=[
                "无定标语义（量纲假定由调用方负责）——只做换算",
                "振幅/强度域负值物理无意义 → NaN（计数披露）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            numerical_tolerance="round-trip 恒等（1e-12）；A=2 → I=4 → A=2 逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_v3.py::test_log_scale_round_trip_identities",
                "tests/unit/lib/test_sar_v3.py::test_log_scale_guards_and_epsilon",
            ],
            parameter_contract_ref="sar_log_scaling_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.multitemporal_speckle",
            name="多时相斑点抑制（强度域 MT-Lee）",
            category="remote_sensing",
            capabilities=["sar_speckle_filtering"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_multitemporal_speckle"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="sar_speckle_filtering",
            method_references=["lee1980", "oliver_quegan1998"],
            assumptions=[
                "逐切片：时序均值与空域 Lee 估计的逐像元逆方差加权（确定性）",
                "权重 σ²：空域=Lee 残差代理 k²·Var；时序=Var_temp/n_t（n_t<2 回退空域）",
                "栈已配准对齐；ENL 显式优先（缺省整图矩估计，披露）",
            ],
            limitations=[
                "非 Quegan 谱域多时相滤波（需 SLC 复数相干分解）——强度栈近似，披露",
                "时序方差计入真实地物变化 → 权重保守偏向空域估计",
                "栈深 ≥3 且 ≤24、H·W ≤4096²（超限 ResourceScaleMismatch）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            numerical_tolerance="固定数据重放逐位一致；常数栈（显式 ENL）恒等映射；合成斑点 std 下降",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_v3.py::test_multitemporal_speckle_noise_reduction_and_determinism",
                "tests/unit/lib/test_sar_v3.py::test_multitemporal_speckle_guards",
            ],
            parameter_contract_ref="sar_multitemporal_speckle_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.coherence",
            name="复数相干性估计（窗口化）",
            category="remote_sensing",
            capabilities=["sar_coherence"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_coherence_estimate"],
            cpu_cost="medium", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="sar_interferometry",
            method_references=["oliver_quegan1998"],
            assumptions=[
                "γ = |Σ a·b*| / √(Σ|a|²·Σ|b|²)（窗口化，nodata 感知累加）",
                "输入为双通道复 SLC（(re, im) 二元组或 complex）——两历元同网格",
                "分母为 0 的窗口 → NaN；γ 钳 [0,1]（超 1 像元计数披露）",
            ],
            limitations=[
                "EXPERIMENTAL：无轨道元数据/配准质量输入——窗口估计有偏差，需人工核验",
                "强度-only 输入（纯实数/虚部全零）被类型化拒绝（相位不可虚构）",
                "不输出干涉相位/解缠（仅相干性幅度）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:2"],
            numerical_tolerance="a==b → γ=1（窗口内 1e-9）；随机相位平移 → γ<1",
            scientific_status="EXPERIMENTAL",
            conformance_tests=[
                "tests/unit/lib/test_sar_v3.py::test_coherence_self_is_one_and_decorrelation",
                "tests/unit/lib/test_sar_v3.py::test_coherence_guards",
            ],
            parameter_contract_ref="sar_coherence_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.rtc",
            name="SAR 地形辐射校正 RTC（gamma 平坦化）",
            category="remote_sensing",
            capabilities=["sar_terrain_geometry_correction"],
            input_artifact_types=["raster_surface", "terrain_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_radiometric_terrain_correction"],
            cpu_cost="medium", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD", priority=15,
            algorithm_family="sar_terrain_correction",
            method_references=["small2011", "horn1981"],
            assumptions=[
                "γ_flat = σ⁰·cosθi/cosθl（Small 2011 gamma 平坦化）",
                "本地入射角：cos θl = cosθi·cosα + sinθi·sinα·cos(β−β_r)"
                "（α=坡度、β=下坡方位角、β_r=雷达视线方位角）",
                "DEM Horn 3×3 梯度；北朝上网格；方位角顺时针自北",
                "cell_size 与 radar_range_azimuth 显式必需（不虚构默认）",
            ],
            limitations=[
                "cell_size 必须为米制单位：度网格 DEM 的像元尺寸需调用方先换算（地形域的 cos(lat) 自动换算不在本域内）",
                "range-only 几何简化：无轨道元数据/传感器位置/方位向分量（披露）",
                "叠掩（面坡且 α>θi）与阴影（cosθl≤0）→ nodata（计数披露）",
                "DEM 无效像元及其 1 像元 Horn 梯度裙边同样 nodata",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            numerical_tolerance="平地 θl=θi → γ_flat=σ⁰ 恒等；斜坡 γ_flat 手算 cos 比精确一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_v3.py::test_rtc_flattens_synthetic_slope",
                "tests/unit/lib/test_sar_v3.py::test_rtc_layover_guard_and_lut",
            ],
            parameter_contract_ref="sar_rtc_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.layover_shadow",
            name="SAR 叠掩/阴影几何分类",
            category="remote_sensing",
            capabilities=["sar_terrain_geometry_correction"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_layover_shadow_mask"],
            cpu_cost="low", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="sar_terrain_correction",
            method_references=["small2011", "horn1981"],
            assumptions=[
                "分类 {0=normal,1=layover,2=shadow,3=nodata} + 占比",
                "layover = 面坡（cos(β−β_r)>0）且坡度陡于入射角（α > θi）",
                "shadow = 本地入射角余弦 ≤ 0（背坡超掠射角）",
            ],
            limitations=[
                "cell_size 必须为米制单位：度网格 DEM 的像元尺寸需调用方先换算（地形域的 cos(lat) 自动换算不在本域内）",
                "range-only 几何简化：无轨道元数据/传感器位置（传感器位置无关近似，披露）",
                "不含视线遮蔽（ray-casting cast shadow）——单像元几何判定",
                "reflect 边界的 Horn 梯度在 2 像元裙边内欠估（边界类判定保守）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            numerical_tolerance="楔形 DEM（平地/面坡/背坡）三类占比与逐像元类别精确一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_v3.py::test_layover_shadow_wedge_classification",
                "tests/unit/lib/test_sar_v3.py::test_layover_shadow_guards",
            ],
            parameter_contract_ref="sar_layover_shadow_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.enl_map",
            name="滑窗 ENL 估计图",
            category="remote_sensing",
            capabilities=["sar_speckle_filtering"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_enl_map"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=20,
            algorithm_family="sar_speckle_filtering",
            method_references=["oliver_quegan1998"],
            assumptions=[
                "ENL = mean²/var（滑窗、总体方差 ddof=0、nan 感知）",
                "全局 ENL 由整图有效像元估计（均匀假设）",
                "退化窗口（方差 ≤ ε）→ NaN（计数披露）",
            ],
            limitations=[
                "非均匀窗口把纹理方差计入 → ENL 被低估（估计偏差，披露）",
                "dB 输入被拒绝（矩估计仅线性强度有意义）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            numerical_tolerance="合成 4 视斑点（gamma）全局 ENL≈4（3-5.5）；常数场 → 类型化拒绝",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_sar_v3.py::test_enl_map_recovers_synthetic_enl",
                "tests/unit/lib/test_sar_v3.py::test_enl_map_guards",
            ],
            parameter_contract_ref="sar_enl_map_analysis",
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
        # v2（Foundation V3 additive）：+gamma_map/kuan（Gamma MAP 三分支 +
        # Newton 迭代上限 max_iterations；Kuan 1985 闭式 MMSE）——既有
        # lee/refined_lee/frost 行为不变（默认 filter=lee）。
        id="sar_speckle_filter_analysis", version=2,
        description="SAR 斑点滤波：Lee/Refined-Lee/Frost/Gamma MAP（Newton 迭代）/Kuan"
                    "（ENL 显式优先，缺省矩估计披露）。",
        parameters=[
            ParameterSpec(
                name="filter", type="enum", default="lee",
                enum_values=["lee", "refined_lee", "frost", "gamma_map", "kuan"],
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
            ParameterSpec(
                name="max_iterations", type="integer", default=10,
                minimum=1, maximum=50,
                description="Gamma MAP Newton 迭代上限（仅 gamma_map；"
                            "iterations_used 披露）",
            ),
        ],
    ),
    ParameterContract(
        # v2（Foundation V3 additive）：入射角支持标量或逐像元 2D LUT
        # （工具参数 incidence_lut / incidence_map，与网格同形，(0,90) 开
        # 区间；lut_pixels 披露）。数组不经 JSON 契约词表（无 array 类型）
        # ——工具签名形状校验 + 实现层守卫；本条目为文档位。
        # 热噪声去除为独立契约 sar_thermal_noise_removal_analysis。
        id="sar_calibration_analysis", version=2,
        description="SAR 辐射定标：DN→β⁰/σ⁰/γ⁰ 常数定标（定标常数显式必需；"
                    "v2：入射角标量或逐像元 2D LUT 互斥；热噪声去除独立算法）。",
        parameters=[
            ParameterSpec(
                name="calibration_constant", type="number", required=True,
                unit="unitless",
                description="定标常数 K（如 Sentinel-1 A²/AUT）——绝不虚构",
            ),
            ParameterSpec(
                name="incidence_deg", type="number", unit="degrees",
                description="标量入射角（度，0-90 开区间；σ⁰/γ⁰ 必需；"
                            "与 incidence_lut 互斥）",
            ),
            ParameterSpec(
                name="incidence_lut", type="string",
                description="逐像元入射角 LUT 文档位：2D 嵌套数组经内联 JSON "
                            "通道传入（与网格同形、(0,90) 开区间；词表无 array "
                            "类型——形状/区间校验在工具签名与实现层；lut_pixels 披露）",
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

    # ── Foundation V3：遥感 V3 批次参数契约 ────────────────────────────
    ParameterContract(
        id="mnf_analysis", version=1,
        description="MNF 变换（Green 1988：局部差分噪声白化 + 白化空间 PCA；SNR=λ−1）。",
        parameters=[
            ParameterSpec(
                name="n_components", type="integer", minimum=1,
                description="输出分量栅格数 k（≤ n_bands；缺省 = n_bands）",
            ),
            ParameterSpec(
                name="noise_estimation", type="enum", default="local_diff",
                enum_values=["local_diff"],
                description="噪声估计量（水平/垂直一阶差分，公式随结果披露）",
            ),
            ParameterSpec(
                name="standardize", type="boolean", default=False,
                description="True=逐波段 z-score 后变换（相关矩阵语义）",
            ),
        ],
    ),
    ParameterContract(
        id="ica_analysis", version=1,
        description="FastICA（Hyvärinen 1999；random_state=42，whiten=unit-variance，收敛性披露）。",
        parameters=[
            ParameterSpec(
                name="n_components", type="integer", minimum=1,
                description="分量数 k（≤ n_bands；缺省 = n_bands）",
            ),
            ParameterSpec(
                name="max_iter", type="integer", default=200, minimum=1,
                maximum=10000,
                description="固定点迭代上限（未收敛 → converged=false 披露）",
            ),
            ParameterSpec(
                name="tol", type="number", default=1e-4, minimum=1e-10,
                description="收敛容差（负熵增量）",
            ),
        ],
    ),
    ParameterContract(
        id="sam_analysis", version=1,
        description="光谱角制图（Kruse 1993：θ=arccos(⟨x,e⟩/(‖x‖‖e‖))；零范数 → NaN）。",
        parameters=[
            ParameterSpec(
                name="angle_unit", type="enum", default="radians",
                enum_values=["radians", "degrees"],
                description="角度单位（缺省弧度；degrees=arccos 结果 ×180/π）",
            ),
        ],
    ),
    ParameterContract(
        id="sid_analysis", version=1,
        description="光谱信息散度（Chang 2000 对称形式；非正分量/非正和 → NaN 披露）。",
        parameters=[
            ParameterSpec(
                name="divergence", type="enum", default="symmetric",
                enum_values=["symmetric"],
                description="散度约定（实现为对称形式 Σp·ln(p/q)+Σq·ln(q/p)）",
            ),
        ],
    ),
    ParameterContract(
        id="matched_filter_analysis", version=1,
        description="匹配滤波（Boardman 1995：全局协方差白化目标投影；丰度式得分）。",
        parameters=[
            ParameterSpec(
                name="center", type="boolean", default=True,
                description="True=均值中心化（score 用 x−μ）；False=不中心化",
            ),
        ],
    ),
    ParameterContract(
        id="rx_analysis", version=1,
        description="RX 异常检测（Reed & Xiaoli 1990；尺度不变岭正则 + mean+kσ 启发式阈值）。",
        parameters=[
            ParameterSpec(
                name="regularize", type="number", default=1e-6, minimum=0.0,
                unit="ratio",
                description="岭正则系数（Σ_r = Σ + regularize·(tr Σ/k)·I）",
            ),
            ParameterSpec(
                name="threshold_sigma", type="number", default=3.0,
                minimum=0.5, maximum=10.0,
                description="阈值倍数 k（threshold = mean(δ)+k·σ(δ)，启发式）",
            ),
        ],
    ),
    ParameterContract(
        id="mad_change_analysis", version=1,
        description="MAD / IR-MAD（Nielsen 1998；SVD-CCA，ρ 升序；IR-MAD 固定点迭代 ≤10）。",
        parameters=[
            ParameterSpec(
                name="n_iterms", type="integer", default=0, minimum=0,
                maximum=10,
                description="IR-MAD 重加权迭代次数（0=一次性 MAD；>10 拒绝）",
            ),
        ],
    ),
    ParameterContract(
        id="segmentation_analysis", version=1,
        description="k-means 分割基座（Lloyd 1982；非 SLIC——compactness 仅作空间权重乘子）。",
        parameters=[
            ParameterSpec(
                name="n_segments", type="integer", default=50, minimum=2,
                description="目标段数（> 有效像元数时钳制并披露）",
            ),
            ParameterSpec(
                name="spatial_weight", type="number", default=0.5,
                minimum=0.0, maximum=1.0, unit="ratio",
                description="空间坐标特征权重（0=纯光谱）",
            ),
            ParameterSpec(
                name="compactness", type="number", default=0.5,
                minimum=0.0, maximum=1.0, unit="ratio",
                description="空间权重乘子（非 SLIC 几何紧致约束，披露）",
            ),
        ],
    ),
    ParameterContract(
        id="endmember_vca_analysis", version=1,
        description="VCA 端元提取（EXPERIMENTAL；纯像元假设；n_endmembers < n_bands 守卫）。",
        parameters=[
            ParameterSpec(
                name="n_endmembers", type="integer", required=True,
                minimum=2,
                description="端元数 m（必须 < 波段数；EXPERIMENTAL 实现约定）",
            ),
            ParameterSpec(
                name="seed", type="integer", default=42, minimum=0,
                description="随机投影种子（固定可复现）",
            ),
        ],
    ),
    ParameterContract(
        id="band_correlation_analysis", version=1,
        description="波段×波段 Pearson 相关表（公共有效掩膜；逐对 n 恒等披露）。",
        parameters=[
            ParameterSpec(
                name="standardize", type="boolean", default=False,
                description="逐波段 z-score（Pearson 线性不变——r 不变，披露）",
            ),
        ],
    ),
    ParameterContract(
        id="temporal_features_analysis", version=1,
        description="时序特征（min/max/mean/std/amplitude/first−last + 单周期谐波；披露边界）。",
        parameters=[
            ParameterSpec(
                name="features", type="string", default="all",
                description="逗号分隔特征子集或 'all'（min,max,mean,std,amplitude,first_last_diff,harmonic_amplitude,harmonic_phase）",
            ),
        ],
    ),
    ParameterContract(
        id="robust_normalize_analysis", version=1,
        description="稳健归一化（2-98 分位拉伸 / 参考场景分位匹配；逐波段分位披露）。",
        parameters=[
            ParameterSpec(
                name="method", type="enum", default="percentile_match",
                enum_values=["percentile_stretch", "percentile_match"],
                description="percentile_match 需 reference 参考栈；stretch 输出 [0,1]",
            ),
            ParameterSpec(
                name="lower_percentile", type="number", default=2.0,
                minimum=0.0, maximum=50.0,
                description="下分位（NaN-aware，逐波段披露）",
            ),
            ParameterSpec(
                name="upper_percentile", type="number", default=98.0,
                minimum=50.0, maximum=100.0,
                description="上分位（NaN-aware，逐波段披露）",
            ),
        ],
    ),
    ParameterContract(
        id="cloud_qc_analysis", version=1,
        description="云 QC 基础咨询（EXPERIMENTAL；亮度阈值 + 可选 NDVI 近零；非 Fmask 强披露）。",
        parameters=[
            ParameterSpec(
                name="brightness_percentile", type="number", default=97.5,
                minimum=50.0, maximum=100.0,
                description="亮度分位阈值（缺省 97.5；显式 brightness_threshold 优先）",
            ),
            ParameterSpec(
                name="brightness_threshold", type="number", minimum=0.0,
                description="显式亮度绝对阈值（给定则跳过分位阈值）",
            ),
            ParameterSpec(
                name="ndvi_max_abs", type="number", minimum=0.0, maximum=1.0,
                description="可选 |NDVI| ≤ 阈值条件（云光谱平坦；零分母不进条件）",
            ),
        ],
    ),

    # ── Foundation V3：SAR 批次参数契约 ────────────────────────────────
    ParameterContract(
        id="sar_thermal_noise_removal_analysis", version=1,
        description="SAR 热噪声去除：I_dn=max(I−N,0)（标量噪声底/逐像元 LUT 互斥；"
                    "钳 0 计数披露；不解析 SAFE XML）。",
        parameters=[
            ParameterSpec(
                name="noise_floor", type="number", minimum=0.0,
                unit="unitless",
                description="标量噪声底（线性强度域；与 noise_lut 互斥）",
            ),
            ParameterSpec(
                name="noise_lut", type="string",
                description="逐像元噪声 LUT 文档位：2D 嵌套数组经内联 JSON 通道"
                            "（与网格同形；形状校验在工具签名与实现层）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_log_scaling_analysis", version=1,
        description="SAR 量纲换算（纯代数恒等式；round-trip 精确；dB 换算 ε 下限披露）。",
        parameters=[
            ParameterSpec(
                name="mode", type="enum", required=True,
                enum_values=["amplitude_to_intensity", "intensity_to_amplitude",
                             "linear_to_db", "db_to_linear"],
                description="换算方向（I=A² / A=√I / dB=10·log₁₀ / x=10^(dB/10)）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_multitemporal_speckle_analysis", version=1,
        description="多时相 MT-Lee 斑点抑制（时序均值 + 空域 Lee 逆方差加权；"
                    "非 Quegan 谱域近似披露；T≥3、T≤24）。",
        parameters=[
            ParameterSpec(
                name="window", type="enum", default="3",
                enum_values=["3", "5", "7"],
                description="空域 Lee 窗口（像元）",
            ),
            ParameterSpec(
                name="enl", type="number", unit="ratio",
                description="等效视数（>0；缺省整图矩估计）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_coherence_analysis", version=1,
        description="复数相干性 γ（EXPERIMENTAL）：|Σ a·b*|/√(Σ|a|²Σ|b|²) 窗口估计；"
                    "仅复 SLC（re/im 双通道）——强度-only 类型化拒绝。",
        parameters=[
            ParameterSpec(
                name="window", type="enum", default="5",
                enum_values=["3", "5", "7"],
                description="估计窗口（像元）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_rtc_analysis", version=1,
        description="RTC 地形辐射校正（Small 2011）：γ_flat=σ⁰·cosθi/cosθl；Horn 坡度坡向；"
                    "叠掩/阴影 → nodata（计数披露）；range-only 简化披露。",
        parameters=[
            ParameterSpec(
                name="cell_size", type="number", required=True,
                minimum=1e-6, unit="meters",
                description="DEM 像元大小（米；与 DEM 网格一致）",
            ),
            ParameterSpec(
                name="radar_range_azimuth", type="number", required=True,
                minimum=0.0, maximum=360.0, unit="degrees",
                description="雷达视线方位角（度 [0,360)，顺时针自北、地面指向传感器；"
                            "如降轨 IW≈270°）",
            ),
            ParameterSpec(
                name="incidence_deg", type="number", unit="degrees",
                description="标量入射角（度，0-90 开区间；与 incidence_lut 互斥）",
            ),
            ParameterSpec(
                name="incidence_lut", type="string",
                description="逐像元入射角 LUT 文档位：2D 嵌套数组经内联 JSON 通道"
                            "（与网格同形；形状/区间校验在工具签名与实现层）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_layover_shadow_analysis", version=1,
        description="叠掩/阴影几何分类（{0=normal,1=layover,2=shadow,3=nodata} + 占比）；"
                    "range-only 无轨道元数据简化披露；不含 ray-casting 遮蔽。",
        parameters=[
            ParameterSpec(
                name="cell_size", type="number", required=True,
                minimum=1e-6, unit="meters",
                description="DEM 像元大小（米；与 DEM 网格一致）",
            ),
            ParameterSpec(
                name="radar_range_azimuth", type="number", required=True,
                minimum=0.0, maximum=360.0, unit="degrees",
                description="雷达视线方位角（度 [0,360)，顺时针自北、地面指向传感器）",
            ),
            ParameterSpec(
                name="incidence_deg", type="number", unit="degrees",
                description="标量入射角（度，0-90 开区间；与 incidence_lut 互斥）",
            ),
            ParameterSpec(
                name="incidence_lut", type="string",
                description="逐像元入射角 LUT 文档位：2D 嵌套数组经内联 JSON 通道"
                            "（与网格同形；形状/区间校验在工具签名与实现层）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_enl_map_analysis", version=1,
        description="滑窗 ENL 估计图（ENL=mean²/var，nan 感知）+ 全局 ENL；"
                    "非均匀窗口低估偏差披露。",
        parameters=[
            ParameterSpec(
                name="window", type="enum", default="7",
                enum_values=["3", "5", "7"],
                description="滑窗（像元）",
            ),
        ],
    ),
]
