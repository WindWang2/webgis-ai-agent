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

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
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
            output_artifact_type="hotspot_result", tool_candidates=["h3_lisa"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["hotspot_overlay"], priority=15,
            algorithm_family="spatial_autocorrelation",
            method_references=["getis_ord1992", "benjamini_hochberg1995"],
            assumptions=[
                "Gi* 含 w_ii=1（distance band 内二值权重，含自身）",
                "p 值为正态近似（非置换）",
                "q_value_fdr 为 BH-FDR 校正（G-6/#870）",
                "距离阈值缺省按 8 近邻平均距离自动（E-7 规则）",
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
]
