"""空间统计 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 statistics 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：本包为全局/局部空间自相关族补齐科学元数据
（方法出处 / CRS 类 / 置换策略 / 不确定性 / conformance 节点）与
参数契约（moran_i_analysis / geary_c_analysis / general_g_analysis）。
实现位于 app/lib/geo_analysis/{statistics,spatial_weights}.py，工具层
（app/tools/spatial_stats.py）只做 validate → 调实现 → 挂证据块。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor, BackendVariant
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="profile.spatial.stats", name="空间数据画像",
            capabilities=["point_profile"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="point_feature_set",
            geometry_requirements=["point"],
            tool_candidates=["spatial_stats", "webgis_source_profile"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE",
            algorithm_family="spatial_descriptive",
            assumptions=["画像为描述性统计（计数/几何/字段元数据），不产出新几何"],
            limitations=["字段类型推断是启发式（数值/类别判定规则披露于工具层）"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                            "tests/unit/test_descriptor_derived_profile_688.py::test_derived_profile_shape_matches_full_profiler_contract",
                        ],
            priority=10,
        ),

        AlgorithmDescriptor(
            id="stats.category.breakdown", name="类别构成统计",
            capabilities=["category_breakdown"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            tool_candidates=["spatial_stats"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE",
            algorithm_family="spatial_descriptive",
            assumptions=["按类别字段 groupby 计数/占比（描述性）"],
            limitations=["类别基数过大时 top-k 截断披露（不聚合长尾）"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
            priority=10,
        ),

        AlgorithmDescriptor(
            id="spatial.hotspot.local", name="局部热点显著性（Getis-Ord Gi*）",
            capabilities=["hotspot"],
            input_artifact_types=["poi_feature_set", "point_feature_set", "grid_aggregate"],
            output_artifact_type="hotspot_result",
            geometry_requirements=["point"],
            tool_candidates=["hotspot_analysis"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["hotspot_overlay"],
            priority=10,
            # Foundation V3：significance_method 枚举参数（normal 默认与既有
            # 行为逐位一致；permutation=条件随机化，n≤5000 守卫）。
            parameter_contract_ref="gi_star_analysis",
            algorithm_family="spatial_autocorrelation",
            method_references=["getis_ord1992"],
            assumptions=[
                "Gi* 含 w_ii=1（distance band 内二值权重，含自身）",
                "significance_method=normal：解析正态 p（既有路径，输出键不变）",
                "significance_method=permutation：条件随机化置换 p（固定种子 42，"
                "双侧 (count+1)/(perms+1)；全局矩取观测值，邻域值随机重排）",
                "距离阈值缺省按 8 近邻平均距离自动（E-7 规则）",
            ],
            limitations=[
                "正态近似在小样本/偏态分布下 p 值偏乐观（置换路径可对照）",
                "置换路径 n>5000 拒绝；邻居样本为全多重集无放回抽取（与严格 "
                "y_{−i} 条件化差一项，Monte-Carlo 近似）",
                "逐格检验的多重比较问题由 BH-FDR 缓解而非消除",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[
                "numeric_field_required",
                "nonzero_variance_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["statistical_significance"],
            uncertainty_producer_tests={
                "statistical_significance":
                    "tests/unit/lib/test_spatial_stats_v3.py"
                    "::test_hotspot_uncertainty_evidence_block",
            },
            random_seed_policy="fixed_seed",
            numerical_tolerance="Gi* 与手算稀疏参考一致（atol 5e-5，既有 conformance）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hotspot_gistar.py::test_hotspot_gistar_includes_self",
                "tests/unit/lib/test_hotspot_gistar.py::test_hotspot_gistar_recomputed_reference",
                "tests/unit/lib/test_spatial_stats_v3.py::test_hotspot_permutation_significance_option",
                "tests/unit/lib/test_spatial_stats_v3.py::test_hotspot_permutation_scale_guard",
            ],
        ),

        # ── VNext：全局自相关族（Moran / Geary / General G）────────────
        # crs_class=GEOGRAPHIC_OK（核实过实现）：moran_i_narrated /
        # geary_c_narrated / general_g_narrated 内部经 to_utm_gdf 自动投影
        # 到局部 UTM 再建权重，度数输入结果正确；失真说明进 limitations。
        AlgorithmDescriptor(
            id="stats.morans_i", name="全局莫兰指数", category="spatial_statistics",
            capabilities=["global_morans_i"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_type="stats_table", tool_candidates=["moran_i"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["moran1950", "benjamini_hochberg1995"],
            assumptions=[
                "默认 KNN k=8 二值权重，对称并集 + 行标准化（#1002 语义）",
                "置换检验 99 次、固定种子 42、双侧 (count+1)/(perms+1)",
                "地理输入自动投影到局部 UTM 后建权重",
                "孤岛（无邻居）行权重为 0，不参与统计量",
            ],
            limitations=[
                "KNN 权重对面数据只是邻接的近似（queen/rook 更贴切）",
                "99 次置换的 p 值分辨率只有 1/100（可升 199/499/999）",
                "自动 UTM 对跨带数据有投影失真",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[
                "numeric_field_required",
                "nonzero_variance_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="Moran I 与 esda.Moran（同 Queen 行标准化权重）在 conformance fixture 上差 <1e-10",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_conformance.py::test_moran_checkerboard_rook_is_minus_one",
                "tests/unit/lib/test_spatial_stats_conformance.py::test_moran_matches_esda_queen_weights",
                "tests/unit/lib/test_spatial_stats_conformance.py::test_moran_deterministic_and_bounded_pvalue",
                "tests/unit/lib/test_statistics_vector.py::test_moran_i_pvalue_matches_seeded_scalar_reference",
                "tests/unit/lib/test_statistics_hardening.py::test_moran_rejects_constant_values",
            ],
            parameter_contract_ref="moran_i_analysis",
            # （见文件尾 PARAMETER_CONTRACTS 注释：iter_contract_packs 目前
            # 无法聚合域契约，挂 ref 会在 validate() 里成悬空引用）。
        ),

        AlgorithmDescriptor(
            id="stats.gearys_c", name="全局 Geary 指数", category="spatial_statistics",
            capabilities=["global_gearys_c"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_type="stats_table", tool_candidates=["geary_c"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["geary1954"],
            assumptions=[
                "C=(n-1)·Σw_ij(x_i-x_j)²/(2·S0·Σz²)，行标准化权重",
                "置换检验与 Moran 同策略：固定种子 42、双侧 +1 校正",
                "与 Moran 的 I 相比 C 对局部差异更敏感（成对差而非叉积）",
                "地理输入自动投影到局部 UTM 后建权重",
            ],
            limitations=[
                "checkerboard 完美负自相关的 C 上限是 2-2/n（非精确 2）",
                "99 次置换的 p 值分辨率只有 1/100",
                "解析方差（analytic_variance）依赖正态假设，偏态数据失真",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[
                "numeric_field_required",
                "nonzero_variance_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="C 与 esda.Geary（同 Queen 权重）差 <1e-10；checkerboard C=2-2/n 精确",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_conformance.py::test_geary_checkerboard_c_expected_value",
                "tests/unit/lib/test_spatial_stats_conformance.py::test_geary_matches_esda_queen_weights",
                "tests/unit/lib/test_spatial_stats_conformance.py::test_geary_constant_and_empty_inputs_typed_errors",
            ],
            parameter_contract_ref="geary_c_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.general_g", name="Getis-Ord General G（全局高值聚集）",
            category="spatial_statistics",
            capabilities=["general_g"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_type="stats_table", tool_candidates=["general_g"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["ord_getis1995"],
            assumptions=[
                "G=Σ_{i≠j} w_ij·x_i·x_j / Σ_{i≠j} x_i·x_j，二值距离阈值权重",
                "值必须非负（计数/强度语义）；负值拒绝",
                "距离阈值缺省按 8 近邻平均距离自动（E-7 规则）",
                "置换检验固定种子 42，双侧 min 侧翻倍",
            ],
            limitations=[
                "G 显著偏低=低值聚集（clustered-low），不是『高值聚集』的镜像陈述",
                "G 只检验高值聚集，不能定位热点（定位用 hotspot_analysis/h3_lisa）",
                "非负约束使 General G 不适用于中心化/标准化变量",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[
                "numeric_field_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="G 与 esda.G（同 DistanceBand 二值权重）差 <1e-10",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_conformance.py::test_general_g_clustered_high_significant",
                "tests/unit/lib/test_spatial_stats_conformance.py::test_general_g_csr_not_significant",
                "tests/unit/lib/test_spatial_stats_conformance.py::test_general_g_matches_esda_and_rejects_negative",
            ],
            parameter_contract_ref="general_g_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.h3_lisa", name="H3 LISA 局部自相关", category="spatial_statistics",
            capabilities=["local_morans_i"],
            input_artifact_types=["grid_aggregate", "admin_aggregate_table"],
            output_artifact_type="hotspot_result", tool_candidates=["h3_lisa"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["hotspot_overlay"], priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["anselin1995"],
            assumptions=[
                "esda.Moran_Local（Queen 邻接、行标准化、seed=42）",
                "孤岛格网给中性结果（p=1、q=0），保持行对齐（#927）",
                "输入为带数值字段的 H3 网格（如 h3_binning 产物）",
                "逐格 p_sim 附 BH-FDR q_value_fdr（审计 F-3 与 Gi* 路径同源）",
            ],
            limitations=[
                "逐格 p_sim<0.05 在随机数据下期望产出 ~0.05n 假显著（结果内披露期望数）",
                "H3 分辨率改变邻接结构，跨分辨率结果不可比",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[
                "numeric_field_required",
                "nonzero_variance_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["statistical_significance"],
            uncertainty_producer_tests={
                "statistical_significance":
                    "tests/unit/lib/test_statistics_hardening.py"
                    "::test_h3_lisa_statistical_significance_block",
            },
            random_seed_policy="fixed_seed",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_statistics_vector.py::test_h3_lisa_classification_matches_scalar_reference",
                "tests/unit/lib/test_statistics_hardening.py::test_h3_lisa_rejects_constant_values",
                "tests/unit/lib/test_statistics_hardening.py::test_h3_lisa_island_cells_neutral",
            ],
        ),

        AlgorithmDescriptor(
            id="stats.h3_hotspot", name="H3 Gi* 热点", category="spatial_statistics",
            capabilities=["getis_ord_gi_star"],
            input_artifact_types=["grid_aggregate", "admin_aggregate_table"],
            output_artifact_type="hotspot_result",
            # 审计 F-1（A2）：tool_candidates 曾指向 h3_lisa —— 那条路径只
            # 产 LISA 象限标签，不产 Gi* z/p/q_value_fdr，声明与接线错位。
            # 改指真实 Gi* 实现 hotspot_analysis（H3 网格以格心点输入同一
            # Gi* 路径，descriptor 宣称的输出因此真实可得）。
            tool_candidates=["hotspot_analysis"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["hotspot_overlay"], priority=15,
            algorithm_family="spatial_autocorrelation",
            method_references=["getis_ord1992", "benjamini_hochberg1995"],
            assumptions=[
                "Gi* 含 w_ii=1（distance band 内二值权重，含自身）",
                "p 值为正态近似（非置换）",
                "q_value_fdr 为 BH-FDR 校正（G-6/#870）",
                "距离阈值缺省按 8 近邻平均距离自动（E-7 规则）",
                "H3 网格以格心为点要素输入 hotspot_analysis 运行（与连续"
                "点同一 Gi* 实现）",
            ],
            limitations=[
                "正态近似在小样本/偏态分布下 p 值偏乐观",
                "逐格检验的多重比较问题由 BH-FDR 缓解而非消除",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=[
                "numeric_field_required",
                "nonzero_variance_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hotspot_gistar.py::test_hotspot_gistar_includes_self",
                "tests/unit/lib/test_hotspot_gistar.py::test_hotspot_gistar_recomputed_reference",
                "tests/unit/lib/test_statistics_vector.py::test_hotspot_classification_matches_scalar_reference",
            ],
        ),

        AlgorithmDescriptor(
            id="stats.st_dbscan", name="时空 DBSCAN 聚类", category="point_pattern",
            capabilities=["spatiotemporal_clustering"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="hotspot_result", tool_candidates=["st_dbscan", "spatial_cluster"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="spatiotemporal_clustering",
            method_references=["ester_kriegel1996"],
            assumptions=["ST-DBSCAN：空间 ε（米，自动投影 UTM）+ 时间 ετ 双阈值",
                         "时间字段解析 NaT 剔除并披露"],
            limitations=["minPts/ε 选择敏感（无自动带宽）；簇数为结果而非假设"],
            crs_class="GEOGRAPHIC_OK",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                            "tests/unit/test_st_dbscan.py::test_st_dbscan_narrated_basic",
                            "tests/unit/test_st_dbscan.py::test_st_dbscan_insufficient_data",
                        ],
        ),

        # ── Foundation V2（A1）：局部 Geary / Join Count / 双变量 Moran /
        #    地理探测器 / 空间回归族 / 权重敏感性 / MGWR（planned）──────
        # 距离型统计一律 PROJECTED_REQUIRED（度数上不正确；实现经 to_utm_gdf
        # 自动投影到局部 UTM，假设中披露）。
        AlgorithmDescriptor(
            id="stats.local_geary", name="局部 Geary's C（相似性/相异性）",
            category="spatial_statistics",
            capabilities=["local_gearys_c"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_type="hotspot_result", tool_candidates=["local_geary"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["hotspot_overlay"],
            priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["anselin1995", "geary1954", "holm1979",
                               "benjamini_hochberg1995"],
            assumptions=[
                "C_i=Σ_j w_ij(z_i-z_j)²，z 为总体方差标准化（esda.Geary_Local 同式）",
                "行标准化权重；置换检验固定种子 42、双侧 (count+1)/(perms+1)",
                "多重校正默认 BH-FDR（可 bonferroni/holm/none）",
                "地理输入自动投影到局部 UTM 后建权重",
            ],
            limitations=[
                "Local Geary 只判相似/相异，高-低方向配对用 LISA（h3_lisa）",
                "±1 二值场等离散取值下置换分布退化，p 分辨率受格子限制",
                "逐格校正后 α=0.05 判定在随机数据下仍有 ~0.05q 假显著期望",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="C_i 与 esda.Geary_Local（同 Queen 行标准化权重）差 <1e-8",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_local_geary_checkerboard_golden",
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_local_geary_matches_esda",
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_local_geary_clustered_classification_and_correction",
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_local_geary_adversarial_inputs",
            ],
            parameter_contract_ref="local_geary_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.join_count", name="Join Count（二值空间关联）",
            category="spatial_statistics",
            capabilities=["join_count_statistics"],
            input_artifact_types=["admin_aggregate_table"],
            output_artifact_type="stats_table", tool_candidates=["join_count"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["cliff_ord1973", "moran1950"],
            assumptions=[
                "字段必须 ⊆ {0,1}，含 0 与 1 两个类（违者 UnsupportedMethod）",
                "二值对称权重；n_BB/n_BW/n_WW 按无序连接计数",
                "期望/方差用 non-free sampling（Cliff-Ord 1973，条件于类别"
                "边际的解析矩），n≥4（审计 F-2：与实现口径同步）",
                "permutations>0 时附固定种子 42 的置换复核 p",
            ],
            limitations=[
                "non-free sampling 忽略权重结构细节（只含连接数 J）",
                "knn 权重是邻接的近似；queen/rook 需要面要素",
                "小 n 下解析 z 的正态近似偏乐观",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["binary_field_required", "min_numeric_samples:4"],
            uncertainty_outputs=["statistical_significance"],
            uncertainty_producer_tests={
                "statistical_significance":
                    "tests/unit/lib/test_local_spatial_stats_v2.py"
                    "::test_join_count_nonfree_sampling_wording_sync",
            },
            random_seed_policy="fixed_seed",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_join_count_checkerboard_golden",
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_join_count_rejects_non_binary",
            ],
            parameter_contract_ref="join_count_analysis",
        ),

        # ── Foundation V3：双色（双变量）Join Count / 经验贝叶斯率平滑 ──
        AlgorithmDescriptor(
            id="stats.bivariate_join_count", name="双色 Join Count（二类别空间关联）",
            category="spatial_statistics",
            capabilities=["join_count_statistics"],
            input_artifact_types=["admin_aggregate_table"],
            output_artifact_type="stats_table", tool_candidates=["bivariate_join_count"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["cliff_ord1973"],
            assumptions=[
                "字段恰好取两个值（任意数值类别，违者 UnsupportedMethod/"
                "DegenerateData）；按排序映射 B=较小值 / W=较大值",
                "二值对称权重；n_BB（同类）/n_BW（异类）/n_WW 按无序连接计数",
                "期望/方差用 non-free sampling（Cliff-Ord 1973，条件于类别"
                "边际的解析矩），n≥4；忽略权重结构细节（披露于结果）",
                "permutations>0 时附固定种子 42 的置换复核 p",
            ],
            limitations=[
                "non-free sampling 是零假设近似，不反映真实抽样设计",
                "knn 权重是邻接的近似；queen/rook 需要面要素",
                "小 n 下解析 z 的正态近似偏乐观（置换可对照）",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["binary_field_required", "min_numeric_samples:4"],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="连接计数为整数精确；置换 p 固定种子 42 可复现",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_completeness_v3.py::test_bivariate_join_count_hand_fixture",
                "tests/unit/lib/test_completeness_v3.py::test_bivariate_join_count_expectation_and_permutation_determinism",
                "tests/unit/lib/test_completeness_v3.py::test_bivariate_join_count_typed_rejections",
            ],
            parameter_contract_ref="bivariate_join_count_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.rate_smoothing", name="经验贝叶斯率平滑（Marshall 1991 MOM）",
            category="spatial_statistics",
            capabilities=["rate_smoothing"],
            input_artifact_types=["admin_boundary_set", "admin_aggregate_table",
                                  "polygon_feature_set"],
            output_artifact_type="admin_aggregate_table", tool_candidates=["rate_smoothing"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD",
            compatible_map_models=["administrative_choropleth"],
            priority=15,
            algorithm_family="rate_smoothing",
            method_references=["marshall1991"],
            assumptions=[
                "分子=观测计数、分母=风险人口；原始率 r_i=C_i/P_i",
                "先验均值/方差用矩估计（MOM，Marshall 1991）：假设计数近似 Poisson",
                "weights_scheme 给定时先验来自邻居（不含自身）的人口加权矩"
                "（局部 EB）；缺省全局 EB",
                "平滑率 = w·r_i+(1−w)·先验均值，w=σ²/(σ²+μ/P_i)；σ²≤0 钳零"
                "（收缩到先验均值）并披露",
            ],
            limitations=[
                "MOM 先验假设 Poisson 计数——小计数/超散布数据下收缩失真",
                "零人口区不产率值（类型化排除并披露），不是 0",
                "孤岛（无有效邻居）保留原始率并披露；极端收缩不等于因果调整",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["numeric_field_required",
                                      "min_numeric_samples:3"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="收缩权重/先验参数与手算 MOM 公式一致（fixture 精确）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_completeness_v3.py::test_eb_constant_rates_recover_raw_rates",
                "tests/unit/lib/test_completeness_v3.py::test_eb_heavy_population_shrinks_less",
                "tests/unit/lib/test_completeness_v3.py::test_eb_zero_population_typed_and_disclosed",
            ],
            parameter_contract_ref="rate_smoothing_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.bivariate_moran", name="双变量 Moran's I（x vs W·y）",
            category="spatial_statistics",
            capabilities=["bivariate_morans_i"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_type="stats_table", tool_candidates=["bivariate_moran"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["wartenberg1985", "moran1950"],
            assumptions=[
                "I=(n/S0)·Σ x_i(Wy)_i/(‖x-x̄‖·‖y-ȳ‖)，行标准化权重",
                "x=y 时与单变量 Moran 严格一致（属性测试钉住）",
                "置换只打乱 y（固定种子 42，双侧 (count+1)/(perms+1)）",
                "地理输入自动投影到局部 UTM 后建权重",
            ],
            limitations=[
                "共位相关 ≠ 因果/超前-滞后；方向解读需领域模型支撑",
                "x 与 y 量纲无关（分子分母同除范数），但受离群值影响",
                "与 esda 归一化对齐仅在无 island 权重时成立（S0=n）；含 island 发散 n/S0",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="x=y 时与 moran_i_narrated 的 I 差 <1e-12；与 esda.Moran_BV 差 <1e-9",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_bivariate_moran_equals_univariate_when_x_is_y",
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_bivariate_moran_matches_esda",
            ],
            parameter_contract_ref="bivariate_moran_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.geodetector", name="地理探测器（因子 q + 交互）",
            category="spatial_statistics",
            capabilities=["geographical_detector"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["geodetector"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_stratified_heterogeneity",
            method_references=["wang2010"],
            assumptions=[
                "q=1-Σ N_h σ_h²/(N σ²)（总体方差），q∈[0,1] 完全分层时 =1",
                "数值分层字段需显式分箱（bins≥2 分位数）；唯一值≤12 按类别",
                "F 检验解析 p；permutations>0 附固定种子 42 的置换 p 与分位",
                "交互按 Wang 2010 表分类（q1∩q2 相对 q1+q2 的位置）",
            ],
            limitations=[
                "类别交集是两分层的公共加细，q 加细单调不减——weakened 类只在"
                "分层被粗化时出现",
                "q 只度量分层解释力，不是因果证据",
                "分层过细（>n/2 层）时 q 退化为 1，被显式拒绝",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["numeric_field_required", "min_numeric_samples:10"],
            uncertainty_outputs=["statistical_significance", "monte_carlo_summary"],
            random_seed_policy="fixed_seed",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_geodetector_q_and_interaction_classes",
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_geodetector_adversarial_inputs",
            ],
            parameter_contract_ref="geodetector_analysis",
        ),

        AlgorithmDescriptor(
            id="spatial.ols_regression", name="OLS + 空间诊断",
            category="spatial_regression",
            capabilities=["spatial_regression"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["ols_regression"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_regression",
            method_references=["anselin1988", "jarque_bera1980",
                               "breusch_pagan1979", "moran1950",
                               "mackinnon_white1985"],
            assumptions=[
                "y~X（含截距）；lstsq 求解，se/t/p 由 (X'X)⁻¹σ² 给出",
                "cov_type=classic（默认）行为与历史逐位一致；HC0/HC1/HC3 附 "
                "MacKinnon-White 异方差稳健标准误列（系数不变）",
                "残差 Moran's I 固定种子 42 置换（双侧 +1）",
                "LM-lag/LM-error/稳健版与 spreg LMtests 逐式一致（Anselin 1988）",
                "BP 为 Koenker 学生化（对非正态残差稳健）",
            ],
            limitations=[
                "残差 Moran 显著时只给 SAR/SEM 建议文本，不替用户自动换模型",
                "n < 2p+2 拒绝（InsufficientSamples）",
                "VIF 在仅一个解释变量时不可得（诚实留空）",
                "稳健标准误不修正空间依赖——残差 Moran 显著时仍需空间模型",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:8",
            ],
            uncertainty_outputs=["validation_metrics", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="精确平面数据系数恢复到 1e-10；LM_err 与手算公式差 <1e-10；"
                                "HC0/HC3 标准误与闭式夹心公式差 <1e-12",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_regression_v2.py::test_ols_recovers_exact_plane",
                "tests/unit/lib/test_spatial_regression_v2.py::test_ols_lm_error_matches_hand_formula",
                "tests/unit/lib/test_spatial_regression_v2.py::test_ols_jb_bp_diagnostics_and_guards",
                "tests/unit/lib/test_completeness_v3.py::test_ols_hc0_matches_closed_form",
                "tests/unit/lib/test_completeness_v3.py::test_ols_hc3_matches_closed_form",
                "tests/unit/lib/test_completeness_v3.py::test_ols_default_cov_type_unchanged",
            ],
            parameter_contract_ref="ols_regression_analysis",
        ),

        AlgorithmDescriptor(
            id="spatial.sar_ml", name="空间滞后 ML（SAR）",
            category="spatial_regression",
            capabilities=["spatial_regression"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["sar_ml_regression"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            max_features_hint=4000,
            algorithm_family="spatial_regression",
            method_references=["ord1975", "anselin1988"],
            assumptions=[
                "y=ρWy+Xβ+ε；log|I-ρW|=Σ ln(1-ρκᵢ)（Ord 1975 特征值法）",
                "ρ 在平稳域 (1/κ_min,1/κ_max) 内有界 Brent 最大化（确定性）",
                "LR 检验 vs OLS（df=1）；伪 R²=1-SSE_SAR/SSE_OLS",
                "β 标准误为给定 ρ̂ 的条件渐近近似（不含 ρ 估计不确定性）",
            ],
            limitations=[
                "n>4000 拒绝（特征值 O(n³)，ResourceScaleMismatch 先于分配）",
                "仅支持相似对称权重（knn/queen/rook/distance_band 均满足）",
                "运行时不静默回退 OLS——fallback 声明只供规划层参考",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:8",
            ],
            uncertainty_outputs=["validation_metrics", "statistical_significance"],
            random_seed_policy="deterministic",
            numerical_tolerance="ρ→0 时与 OLS 一致；格网 ρ=0.6 恢复到 ±0.15",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_regression_v2.py::test_sar_ml_recovers_rho_on_grid",
                "tests/unit/lib/test_spatial_regression_v2.py::test_sar_ml_scale_guard_and_degenerate_inputs",
            ],
            fallback_algorithms=["spatial.ols_regression"],
            fallback_semantics={"spatial.ols_regression": "approximation"},
            backend_variants=[
                BackendVariant(
                    id="eigen_dense_symmetric", backend="numpy",
                    max_features=4000,
                    notes="对称相似变换后稠密 eigvalsh；n≤4000"),
            ],
            parameter_contract_ref="sar_ml_analysis",
        ),

        AlgorithmDescriptor(
            id="spatial.sem_ml", name="空间误差 ML（SEM）",
            category="spatial_regression",
            capabilities=["spatial_regression"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["sem_ml_regression"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            max_features_hint=4000,
            algorithm_family="spatial_regression",
            method_references=["ord1975", "anselin1988"],
            assumptions=[
                "y=Xβ+u，u=λWu+ε；Cy=CXβ+ε 的 GLS 剖面似然（C=I-λW）",
                "与 SAR 同一特征值机器；λ 有界 Brent 最大化（确定性）",
                "LR 检验 vs OLS（df=1，Burridge 1980 LM-error 的 ML 对应）",
            ],
            limitations=[
                "n>4000 拒绝（特征值 O(n³)）",
                "运行时不静默回退 OLS——fallback 声明只供规划层参考",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:8",
            ],
            uncertainty_outputs=["validation_metrics", "statistical_significance"],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_regression_v2.py::test_sem_ml_recovers_lambda",
                "tests/unit/lib/test_spatial_regression_v2.py::test_sar_ml_scale_guard_and_degenerate_inputs",
            ],
            fallback_algorithms=["spatial.ols_regression"],
            fallback_semantics={"spatial.ols_regression": "approximation"},
            parameter_contract_ref="sem_ml_analysis",
        ),

        AlgorithmDescriptor(
            id="spatial.slx", name="SLX（空间滞后 X 的 OLS）",
            category="spatial_regression",
            capabilities=["spatial_regression"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["slx_regression"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="spatial_regression",
            method_references=["anselin1988", "cliff_ord1973"],
            assumptions=[
                "y~[X, WX]；WX 为行标准化权重的空间滞后解释变量",
                "系数表含 WX 滞后项（邻居溢出的直接估计）",
                "孤岛观测的 WX 行为 0（披露于 weights 元数据）",
            ],
            limitations=[
                "直接/间接效应分解未做（需 SAR/SDM 类模型的偏导推导）",
                "参数量翻倍，n<2p+2 时拒绝",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:8",
            ],
            uncertainty_outputs=["validation_metrics"],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_regression_v2.py::test_slx_recovers_plane_with_lagged_terms",
            ],
            parameter_contract_ref="slx_analysis",
        ),

        AlgorithmDescriptor(
            id="spatial.gwr", name="地理加权回归（GWR）",
            category="spatial_regression",
            capabilities=["gwr"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["gwr_regression"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=30,
            algorithm_family="spatial_regression",
            method_references=["brunsdon1996", "fotheringham2002"],
            assumptions=[
                "自适应 bisquare 核，带宽=最近邻数 k（默认 30，钳制 [5,n/2]）",
                "bandwidth_selection=cv 时在有界网格上留一 CV（确定性穷举）",
                "fixed 路径产以 k 为中心的 3 点带宽敏感性摘要（ENP/R²，F-4）",
                "逐系数局地 SE/t：σ̂²=RSS/(n−tr(S)) 的局地 WLS sandwich"
                "（Fotheringham 2002 §2.6 局部 hat 近似）",
                "AIC/AICc 用帽矩阵迹 q=tr(S)+1 的高斯形式（常用近似）",
                "n≤2000 附逐观测系数面；超过只回摘要+披露旗标",
            ],
            limitations=[
                "局部共线性会让局部系数失真（全局 VIF 不代表局部）",
                "局地 SE/t 不含量化带宽选择与核形态的不确定性",
                "AICc 没有唯一公认公式——比较带宽/模型时保持同一实现",
                "CV 带宽选择在有界网格上，非连续优化",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:8",
            ],
            uncertainty_outputs=["validation_metrics", "field_uncertainty",
                                 "sensitivity_envelope"],
            uncertainty_producer_tests={
                "field_uncertainty":
                    "tests/unit/lib/test_gwr_local_inference.py"
                    "::test_gwr_local_se_field_uncertainty_block",
                "sensitivity_envelope":
                    "tests/unit/lib/test_gwr_local_inference.py"
                    "::test_gwr_fixed_bandwidth_sensitivity_envelope",
            },
            random_seed_policy="deterministic",
            numerical_tolerance="局地 WLS 与手算 bisquare 加权解差 <5e-7（载荷 6 位舍入下限）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_regression_v2.py::test_gwr_matches_hand_wls_and_bandwidth_cv",
                "tests/unit/lib/test_spatial_regression_v2.py::test_gwr_guards_typed_errors",
            ],
            parameter_contract_ref="gwr_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.weights_sensitivity", name="权重方案敏感性（Moran's I）",
            category="spatial_statistics",
            capabilities=["weights_sensitivity"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["weights_sensitivity"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["moran1950", "anselin1988"],
            assumptions=[
                "方案集固定：knn(k)/queen/rook/distance_band(auto 8nn)",
                "queen/rook 对点输入如实跳过并披露（不做静默替换）",
                "逐方案 Moran I + 固定种子 42 置换 p；判读多数一致性=稳定性",
            ],
            limitations=[
                "四方案是常见代表性集合，不是穷举权重空间",
                "稳定性=判读一致比例，不代表 I 的点估计置信区间",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:3",
            ],
            uncertainty_outputs=["sensitivity_envelope", "statistical_significance"],
            random_seed_policy="fixed_seed",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_local_spatial_stats_v2.py::test_weights_sensitivity_stability_and_disclosure",
            ],
            parameter_contract_ref="weights_sensitivity_analysis",
        ),

        # ── Foundation V3（A3）：MGWR 原生实现（反向拟合）──────────────
        # planned 条目翻转为 native：真实逐变量带宽反向拟合，等带宽一致
        # 性锚（与 GWR/WLS 联合解 rtol 1e-4）钉住「不是改名的 GWR」。
        AlgorithmDescriptor(
            id="spatial.mgwr", name="多尺度地理加权回归（MGWR）",
            category="spatial_regression",
            capabilities=["gwr"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["mgwr_regression"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=40,
            max_features_hint=2000,
            algorithm_family="spatial_regression",
            method_references=["fotheringham2017", "fotheringham2002",
                               "brunsdon1996"],
            assumptions=[
                "每个设计列（含截距项）独立带宽的 bisquare kNN 反向拟合",
                "联合 GWR 解热启动；逐项部分残差 + LOO-CV 带宽搜索（≤20 候选）",
                "ENP=逐项帽矩阵对角迹之和；AICc 用 q=ENP+1 高斯近似",
                "逐系数局地 SE/t：过原点单列 WLS sandwich，σ̂²=RSS/(n−ENP)"
                "（条件于收敛解；自由度交叉项未计入）",
                "n≤2000 输出逐观测系数面；超过先抛 ResourceScaleMismatch",
            ],
            limitations=[
                "反向拟合是不动点迭代：收敛到局部最优，不保证全局最优",
                "带宽为有界网格穷举而非连续优化；等带宽锚在精确可表示表"
                "面上逐位成立，噪声数据的等带宽解与 GWR 有平滑交互偏差",
                "局部共线性会让局部系数失真；AICc 无唯一公认公式",
                "局地 SE/t 不含量化带宽搜索与反向拟合迭代的不确定性",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=[
                "numeric_field_required", "nonzero_variance_required",
                "min_numeric_samples:8",
            ],
            uncertainty_outputs=["validation_metrics", "field_uncertainty",
                                 "sensitivity_envelope"],
            uncertainty_producer_tests={
                "field_uncertainty":
                    "tests/unit/lib/test_gwr_local_inference.py"
                    "::test_mgwr_local_se_field_uncertainty_block",
            },
            random_seed_policy="deterministic",
            numerical_tolerance="等带宽反向拟合在精确平面上逐位恢复 GWR 解"
                                "（conformance 锚 rtol 1e-4）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_v3.py::test_mgwr_equal_bandwidth_matches_gwr_anchor",
                "tests/unit/lib/test_spatial_stats_v3.py::test_mgwr_different_bandwidths_change_surfaces",
                "tests/unit/lib/test_spatial_stats_v3.py::test_mgwr_guards_typed_errors",
            ],
            parameter_contract_ref="mgwr_analysis",
        ),

        # ── Foundation V3：地理探测器生态/风险探测器（Wang 2010 族）────
        AlgorithmDescriptor(
            id="stats.geodetector_ecological", name="地理探测器·生态探测器",
            category="spatial_statistics",
            capabilities=["geographical_detector"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["geodetector_ecological"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_stratified_heterogeneity",
            method_references=["wang2010"],
            assumptions=[
                "SSW_j=Σ_h Σ_{i∈h}(y_i−ȳ_h)²（分层的未解释变异）",
                "t=[SSW₁/(n−m₁)−SSW₂/(n−m₂)]/sqrt(速率方差合成)，Wang 2010 族",
                "双侧 p 用 Student t、df=n−2（保守可复核的 df 选择，meta 披露）",
                "SSW 显著更小的一侧=解释力显著占优（p<0.05 才判 dominant）",
            ],
            limitations=[
                "df=n−2 是保守选择：分层自由度的精确合成需 Behrens-Fisher 类近似",
                "SSW 只度量分层解释力，不是因果证据",
                "两分层必须行对齐（任一分层字段为空的行整行丢弃并披露计数）",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["numeric_field_required",
                                      "min_numeric_samples:10"],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="deterministic",
            numerical_tolerance="SSW/t 与手算黄金值逐位一致（10×2 分层 fixture）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_v3.py::test_geodetector_ecological_hand_fixture",
                "tests/unit/lib/test_spatial_stats_v3.py::test_geodetector_ecological_adversarial_inputs",
            ],
            parameter_contract_ref="geodetector_ecological_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.geodetector_risk", name="地理探测器·风险探测器",
            category="spatial_statistics",
            capabilities=["geographical_detector"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["geodetector_risk"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="spatial_stratified_heterogeneity",
            method_references=["wang2010"],
            assumptions=[
                "逐分层对均值差的 Welch t 检验（equal_var=False，方差不等稳健）",
                "permutations>0 附固定种子 42 标签置换双侧 p（(count+1)/(perms+1)）",
                "方向判定 p<0.05 才给 higher/lower，否则 not_significant",
                "输出对列表 + 方向矩阵两种形式；分层数<2 的对诚实留空",
            ],
            limitations=[
                "两分层的均值差不构成因果证据",
                "分层数<2 时该对的 t/p/方向不可得（not_significant + note）",
                "多重比较未校正：对数随分层数平方增长，解读需谨慎",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["numeric_field_required",
                                      "min_numeric_samples:10"],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="Welch t/p 与 scipy.stats.ttest_ind 一致（同实现）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_v3.py::test_geodetector_risk_pairwise_and_matrix",
                "tests/unit/lib/test_spatial_stats_v3.py::test_geodetector_risk_permutation_determinism",
            ],
            parameter_contract_ref="geodetector_risk_analysis",
        ),

        # ── Foundation V3：局部 Join Count / 双变量局部 Moran / 权重诊断
        AlgorithmDescriptor(
            id="stats.local_join_count", name="局部 Join Count（二值共位簇）",
            category="spatial_statistics",
            capabilities=["local_join_count"],
            input_artifact_types=["admin_aggregate_table"],
            output_artifact_type="hotspot_result", tool_candidates=["local_join_count"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["hotspot_overlay"],
            priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["anselin_li2019", "sokal1998",
                               "benjamini_hochberg1995"],
            assumptions=[
                "y ⊆ {0,1}（违者 UnsupportedMethod）；二值对称权重（无自环）",
                "LJC_i=Σ_j w_ij·I(y_i=1)·I(y_j=1)；y=0 位置 LJC≡0、p≡1",
                "条件置换推断（保持 1 的总数），单侧上尾 (count+1)/(perms+1)",
                "多重校正默认 BH-FDR（在全部 n 个位置上校正，偏保守）",
            ],
            limitations=[
                "只检测 y=1 的共位聚集；y=0 的聚集用 0/1 翻转后再检",
                "BH 在全 n 位置上校正（含 y=0 的 p≡1），对稀疏 1 偏保守",
                "knn/distance_band 权重是邻接的近似；queen/rook 需要面要素",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["binary_field_required",
                                      "min_numeric_samples:4"],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="LJC 计数为整数精确；置换 p 固定种子 42 可复现",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_v3.py::test_local_join_count_hand_fixture_and_clustering",
                "tests/unit/lib/test_spatial_stats_v3.py::test_local_join_count_rejects_non_binary",
                "tests/unit/lib/test_spatial_stats_v3.py::test_local_join_count_permutation_determinism",
            ],
            parameter_contract_ref="local_join_count_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.bivariate_local_moran", name="双变量局部 Moran（LISA）",
            category="spatial_statistics",
            capabilities=["bivariate_local_moran"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate"],
            output_artifact_type="hotspot_result", tool_candidates=["bivariate_local_moran"],
            cpu_cost="medium", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["hotspot_overlay"],
            priority=10,
            algorithm_family="spatial_autocorrelation",
            method_references=["anselin1995", "wartenberg1985",
                               "benjamini_hochberg1995"],
            assumptions=[
                "esda.Moran_Local_BV 委托（行标准化权重、固定种子 42 条件随机化）",
                "I_i=z(x1)_i·Σ_j w_ij z(x2)_j；标签 HH/LH/LL/HL 取 p_sim<0.05",
                "BH q 值随要素输出；孤岛位置贡献为 0、结果中性",
            ],
            limitations=[
                "共位相关 ≠ 因果/超前-滞后；方向解读需领域模型支撑",
                "孤岛权重处置与 esda 归一化的对齐仅在无 island 权重时严格成立",
                "p_sim<0.05 的逐点判定在随机数据下期望产出 ~0.05n 假显著",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["numeric_field_required",
                                      "nonzero_variance_required",
                                      "min_numeric_samples:8"],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="I_i 与 esda.Moran_Local_BV（同权重）逐位一致（委托）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_v3.py::test_bivariate_local_moran_labels_and_determinism",
            ],
            parameter_contract_ref="bivariate_local_moran_analysis",
        ),

        AlgorithmDescriptor(
            id="stats.weights_diagnostics", name="空间权重诊断",
            category="spatial_statistics",
            capabilities=["spatial_weights_diagnostics"],
            input_artifact_types=["admin_aggregate_table", "grid_aggregate",
                                  "poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table", tool_candidates=["weights_diagnostics"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=10,
            algorithm_family="spatial_weights",
            method_references=["anselin1988"],
            assumptions=[
                "诊断对象=既有空间权重构造器（knn/queen/rook/distance_band）产物",
                "对称性分别检查存储矩阵与二值邻接（行标准化矩阵一般不对称）",
                "连通分量在二值邻接的无向图上计算（networkx）",
                "确定性、零随机成分；孤岛/不对称/多分量给结构警告",
            ],
            limitations=[
                "诊断只覆盖权重结构，不覆盖权重方案的选择恰当性",
                "连通分量是无向近似：有向 kNN 的互邻关系按无向边处理",
            ],
            crs_class="PROJECTED_REQUIRED",
            scientific_preconditions=["numeric_field_required",
                                      "min_numeric_samples:3"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="计数类输出为整数精确；稀疏度为精确比值",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spatial_stats_v3.py::test_weights_diagnostics_island_detection",
            ],
            parameter_contract_ref="weights_diagnostics_analysis",
        ),
]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────
# permutations 用 enum（"99"/"199"/"499"/"999"）而非开放 integer：方法学
# 上置换数是离散档位，开放整型会让 (count+1)/(perms+1) 的分辨率声明失真。
# 契约经 iter_contract_packs 自动聚合（中央缺陷已修复：属性从域模块读）。

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="moran_i_analysis", version=1,
        description="全局 Moran's I：权重方案 / kNN 邻居数 / 距离阈值 / 置换数。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="待检验的数值字段名",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="空间权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="99",
                enum_values=["99", "199", "499", "999"],
                description="置换次数（固定种子 42；p 值分辨率 1/(n+1)）",
            ),
        ],
    ),
    ParameterContract(
        id="geary_c_analysis", version=1,
        description="全局 Geary's C：权重方案 / kNN 邻居数 / 距离阈值 / 置换数。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="待检验的数值字段名",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="空间权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="99",
                enum_values=["99", "199", "499", "999"],
                description="置换次数（固定种子 42；p 值分辨率 1/(n+1)）",
            ),
        ],
    ),
    ParameterContract(
        id="general_g_analysis", version=1,
        description="Getis-Ord General G：距离阈值 + 置换数（值须非负）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="非负数值字段名（计数/强度）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                description="二值权重距离阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="99",
                enum_values=["99", "199", "499", "999"],
                description="置换次数（固定种子 42）",
            ),
        ],
    ),
    ParameterContract(
        id="local_geary_analysis", version=1,
        description="局部 Geary's C_i：权重方案 / k / 距离阈值 / 置换数 / 多重校正。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="待检验的数值字段名",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="空间权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="99",
                enum_values=["99", "199", "499", "999"],
                description="置换次数（固定种子 42；p 值分辨率 1/(n+1)）",
            ),
            ParameterSpec(
                name="correction", type="enum", default="bh",
                enum_values=["bh", "bonferroni", "holm", "none"],
                description="逐格 p 的多重校正方法",
            ),
        ],
    ),
    ParameterContract(
        id="join_count_analysis", version=2,
        description="Join Count：二值字段 / 权重方案 / 解析推断 + 可选置换。",
        parameters=[
            ParameterSpec(
                name="binary_field", type="string", required=True,
                description="二值（0/1）字段名；含其他值会被拒绝",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="二值权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="0",
                enum_values=["0", "99", "199", "499", "999"],
                description="置换复核次数；0=只用 non-free sampling 解析 z 检验",
            ),
        ],
    ),
    ParameterContract(
        id="bivariate_moran_analysis", version=1,
        description="双变量 Moran's I：x 字段 / 滞后字段 / 权重方案 / 置换数。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="x 的数值字段名",
            ),
            ParameterSpec(
                name="lag_field", type="string", required=True,
                description="y 的数值字段名（取其空间滞后 W·y）",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="空间权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="99",
                enum_values=["99", "199", "499", "999"],
                description="置换次数（只打乱 y；固定种子 42）",
            ),
        ],
    ),
    ParameterContract(
        id="geodetector_analysis", version=1,
        description="地理探测器：值字段 / 分层字段 / 可选交互字段 / 分箱 / 置换。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="被解释的数值字段名",
            ),
            ParameterSpec(
                name="strata_field", type="string", required=True,
                description="分层字段名（类别，或数值+分箱）",
            ),
            ParameterSpec(
                name="interaction_field", type="string", default="",
                description="第二分层字段（可选）：计算 q(X1∩X2) 并按 Wang 2010 分类",
            ),
            ParameterSpec(
                name="bins", type="integer", default=0, minimum=0, maximum=20,
                unit="count",
                description="数值分层字段的分位数分箱数；0=按原值类别（≤12 唯一值）",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="99",
                enum_values=["0", "99", "199", "499", "999"],
                description="分层标签置换次数；0=只用 F 检验解析 p",
            ),
        ],
    ),
    # Foundation V3 additive bump（v1→v2）：cov_type 可选枚举参数。
    # classic 默认 → 工具与实现行为逐位不变；HC0/HC1/HC3 = MacKinnon-White
    # 异方差稳健协方差（输出附加 robust_std_error 列 + 方法披露）。
    ParameterContract(
        id="ols_regression_analysis", version=2,
        description="OLS + 空间诊断：目标/解释字段 + 权重方案 + 残差 Moran 置换"
                    " + 可选异方差稳健协方差。",
        parameters=[
            ParameterSpec(
                name="target_field", type="string", required=True,
                description="因变量 y 的数值字段名",
            ),
            ParameterSpec(
                name="explanatory_fields", type="string", required=True,
                description="自变量字段名列表（逗号分隔）",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="残差 Moran/LM 诊断用的空间权重方案",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="99",
                enum_values=["99", "199", "499", "999"],
                description="残差 Moran's I 的置换次数（固定种子 42）",
            ),
            ParameterSpec(
                name="cov_type", type="enum", default="classic",
                enum_values=["classic", "HC0", "HC1", "HC3"],
                description="系数协方差：classic=经典 (X'X)⁻¹σ²（默认，行为"
                            "不变）；HC0/HC1/HC3=MacKinnon-White 稳健标准误"
                            "（附加列，不改系数）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_ml_analysis", version=1,
        description="SAR-ML：目标/解释字段 + 权重方案（特征值 log-det 路径）。",
        parameters=[
            ParameterSpec(
                name="target_field", type="string", required=True,
                description="因变量 y 的数值字段名",
            ),
            ParameterSpec(
                name="explanatory_fields", type="string", required=True,
                description="自变量字段名列表（逗号分隔）",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="空间权重方案（须相似对称——四种均满足）",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
        ],
    ),
    ParameterContract(
        id="sem_ml_analysis", version=1,
        description="SEM-ML：目标/解释字段 + 权重方案（与 SAR 同一特征值机器）。",
        parameters=[
            ParameterSpec(
                name="target_field", type="string", required=True,
                description="因变量 y 的数值字段名",
            ),
            ParameterSpec(
                name="explanatory_fields", type="string", required=True,
                description="自变量字段名列表（逗号分隔）",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="空间权重方案（须相似对称——四种均满足）",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
        ],
    ),
    ParameterContract(
        id="slx_analysis", version=1,
        description="SLX：目标/解释字段 + 权重方案（W·X 滞后项进设计阵）。",
        parameters=[
            ParameterSpec(
                name="target_field", type="string", required=True,
                description="因变量 y 的数值字段名",
            ),
            ParameterSpec(
                name="explanatory_fields", type="string", required=True,
                description="自变量字段名列表（逗号分隔）",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="W·X 滞后的空间权重方案",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
        ],
    ),
    ParameterContract(
        id="gwr_analysis", version=1,
        description="GWR：目标/解释字段 + 自适应 bisquare 带宽（kNN 计数）。",
        parameters=[
            ParameterSpec(
                name="target_field", type="string", required=True,
                description="因变量 y 的数值字段名",
            ),
            ParameterSpec(
                name="explanatory_fields", type="string", required=True,
                description="自变量字段名列表（逗号分隔）",
            ),
            ParameterSpec(
                name="bandwidth", type="integer", default=30, minimum=5,
                maximum=500, unit="count",
                description="带宽 = 最近邻数（含自身）；运行时钳制到 [5, n/2]",
            ),
            ParameterSpec(
                name="bandwidth_selection", type="enum", default="fixed",
                enum_values=["fixed", "cv"],
                description="fixed=用 bandwidth；cv=在有界网格上留一交叉验证选带宽",
            ),
        ],
    ),
    ParameterContract(
        id="weights_sensitivity_analysis", version=1,
        description="权重敏感性：值字段 + kNN k + 距离阈值（方案集固定）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="待检验的数值字段名",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="knn 方案的邻居数",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="99",
                enum_values=["99", "199", "499", "999"],
                description="逐方案 Moran I 的置换次数（固定种子 42）",
            ),
        ],
    ),

    # ── Foundation V3 契约（MGWR / 生态·风险探测器 / 局部 Join Count /
    #    双变量局部 Moran / 权重诊断 / Gi* 显著性方法）──────────────────
    ParameterContract(
        id="mgwr_analysis", version=1,
        description="MGWR：目标/解释字段 + 全局带宽初值（逐项带宽运行时搜索）。",
        parameters=[
            ParameterSpec(
                name="target_field", type="string", required=True,
                description="因变量 y 的数值字段名",
            ),
            ParameterSpec(
                name="explanatory_fields", type="string", required=True,
                description="自变量字段名列表（逗号分隔）",
            ),
            ParameterSpec(
                name="bandwidth", type="integer", default=30, minimum=5,
                maximum=500, unit="count",
                description="全局带宽初值 = 最近邻数（含自身）；逐项带宽由"
                            "反向拟合的 LOO-CV 在有界网格上确定",
            ),
        ],
    ),
    ParameterContract(
        id="geodetector_ecological_analysis", version=1,
        description="生态探测器：值字段 + 两个分层字段的 SSW 比较 t 检验。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="被解释的数值字段名",
            ),
            ParameterSpec(
                name="strata_field_1", type="string", required=True,
                description="第一分层字段名（其 SSW 显著更小=解释占优）",
            ),
            ParameterSpec(
                name="strata_field_2", type="string", required=True,
                description="第二分层字段名",
            ),
            ParameterSpec(
                name="bins", type="integer", default=0, minimum=0, maximum=20,
                unit="count",
                description="数值分层字段的分位数分箱数；0=按原值类别"
                            "（≤12 唯一值）",
            ),
        ],
    ),
    ParameterContract(
        id="geodetector_risk_analysis", version=1,
        description="风险探测器：值字段 + 分层字段的逐对均值差检验。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="被解释的数值字段名",
            ),
            ParameterSpec(
                name="strata_field", type="string", required=True,
                description="分层字段名（类别，或数值+分箱）",
            ),
            ParameterSpec(
                name="bins", type="integer", default=0, minimum=0, maximum=20,
                unit="count",
                description="数值分层字段的分位数分箱数；0=按原值类别"
                            "（≤12 唯一值）",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="0",
                enum_values=["0", "99", "199", "499", "999"],
                description="逐对标签置换复核次数；0=只用 Welch t 解析 p",
            ),
        ],
    ),
    ParameterContract(
        id="local_join_count_analysis", version=1,
        description="局部 Join Count：二值字段 / 权重方案 / 条件置换 + 校正。",
        parameters=[
            ParameterSpec(
                name="binary_field", type="string", required=True,
                description="二值（0/1）字段名；含其他值会被拒绝",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="二值对称权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="999",
                enum_values=["99", "199", "499", "999"],
                description="条件置换次数（固定种子 42；单侧上尾）",
            ),
            ParameterSpec(
                name="correction", type="enum", default="bh",
                enum_values=["bh", "bonferroni", "holm", "none"],
                description="逐位置 p 的多重校正方法",
            ),
        ],
    ),
    ParameterContract(
        id="bivariate_local_moran_analysis", version=1,
        description="双变量局部 Moran：x / 滞后字段 / 权重方案 / 置换数。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="x 的数值字段名",
            ),
            ParameterSpec(
                name="lag_field", type="string", required=True,
                description="y 的数值字段名（取其空间滞后 W·y）",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="空间权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="999",
                enum_values=["99", "199", "499", "999"],
                description="条件随机化次数（固定种子 42）",
            ),
        ],
    ),
    ParameterContract(
        id="weights_diagnostics_analysis", version=1,
        description="权重诊断：权重方案 / k / 距离阈值（无必填统计字段）。",
        parameters=[
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="待诊断的空间权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
        ],
    ),
    ParameterContract(
        id="gi_star_analysis", version=1,
        description="Getis-Ord Gi*：值字段 + 距离阈值 + 显著性方法（解析/置换）。",
        parameters=[
            ParameterSpec(
                name="value_field", type="string", required=True,
                description="待分析的数值字段名",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="二值权重距离阈值（米，含自身）；0=按 8 近邻平均"
                            "距离自动",
            ),
            ParameterSpec(
                name="significance_method", type="enum", default="normal",
                enum_values=["normal", "permutation"],
                description="显著性方法：normal=解析正态 p（默认，既有行为）；"
                            "permutation=条件随机化置换 p（固定种子 42）",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="999",
                enum_values=["99", "199", "499", "999"],
                description="置换次数（仅 significance_method=permutation）",
            ),
        ],
    ),
    # ── Foundation V3（completeness batch）：双色 Join Count / EB 率平滑 ──
    ParameterContract(
        id="bivariate_join_count_analysis", version=2,
        description="双色 Join Count：二类别字段 / 权重方案 / 解析推断 + 可选置换。",
        parameters=[
            ParameterSpec(
                name="binary_field", type="string", required=True,
                description="恰好取两个值的类别字段名（按排序映射 B/W）；"
                            "其他取值数会被拒绝",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="knn",
                enum_values=["knn", "queen", "rook", "distance_band"],
                description="二值权重方案；queen/rook 需要面要素",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="0",
                enum_values=["0", "99", "199", "499", "999"],
                description="置换复核次数；0=只用 non-free sampling 解析 z 检验",
            ),
        ],
    ),
    ParameterContract(
        id="rate_smoothing_analysis", version=1,
        description="经验贝叶斯率平滑：计数/人口字段 + 可选邻居权重（Marshall 1991 MOM）。",
        parameters=[
            ParameterSpec(
                name="count_field", type="string", required=True,
                description="分子：观测计数数值字段名",
            ),
            ParameterSpec(
                name="population_field", type="string", required=True,
                description="分母：风险人口数值字段名（≤0/缺失的区不产率值）",
            ),
            ParameterSpec(
                name="weights_scheme", type="enum", default="none",
                enum_values=["none", "knn", "queen", "rook", "distance_band"],
                description="none=全局 EB 先验（默认）；其余=邻居先验（局部 EB）",
            ),
            ParameterSpec(
                name="k", type="integer", default=8, minimum=2, maximum=16,
                unit="count",
                description="kNN 邻居数（仅 weights_scheme=knn）",
            ),
            ParameterSpec(
                name="distance_band", type="number", default=0, minimum=0,
                unit="meters",
                data_dependent_default="distance_band_8nn",
                description="distance_band 权重阈值（米）；0=按 8 近邻平均距离自动",
            ),
        ],
    ),
]
