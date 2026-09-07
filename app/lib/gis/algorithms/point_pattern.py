"""point_pattern 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 point_pattern 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：登记 Ripley K / 样方 χ² 两个新实现（lib：
app/lib/geo_analysis/point_pattern.py；工具：app/tools/spatial_stats.py），
并为既有实现补 NNI（nearest_neighbor 工具）与 DBSCAN（spatial_cluster
工具）的描述符。crs_class=GEOGRAPHIC_OK：工具层经 to_utm_gdf 自动投影
到局部 UTM，度数输入结果正确；度量失真披露在 limitations。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="point_pattern.ripley_k", name="Ripley's K 函数", category="point_pattern",
            capabilities=["point_pattern_analysis"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["ripley_k_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="point_pattern_second_order",
            method_references=["ripley1976"],
            assumptions=[
                "同质（CSR 可作参考）二阶结构；各向同性边缘校正（矩形窗）",
                "K(r)=A/(n(n-1))·Σ I(d≤r)/w_ij，w_ij 为圆周入窗比例",
                "r_max=max_distance_ratio×min(窗宽,窗高)，≤0.5 保边缘校正可信",
                "地理输入自动投影到局部 UTM（米制距离是方法学前提）",
            ],
            limitations=[
                "描述性输出（无显著性 p 值）；显著性需固定种子 CSR 模拟包络",
                "O(n²) 成对统计，上限 2 万点（超出诚实拒绝）",
                "非矩形研究域的边缘校正按外接矩形近似",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:10"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="CSR fixture 的 K(r) 落在固定种子模拟包络内；规则格网 K(r)≤πr²",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_science.py::test_ripley_k_csr_within_simulation_envelope",
                "tests/unit/lib/test_point_pattern_science.py::test_ripley_k_regular_grid_below_csr",
                "tests/unit/lib/test_point_pattern_science.py::test_ripley_k_rejects_geographic_degrees",
                "tests/unit/lib/test_point_pattern_science.py::test_ripley_k_deterministic_and_bounded",
            ],
            parameter_contract_ref="ripley_k_analysis",
            # （statistics 域包的 ensure_parameter_contracts_registered 注释）。
        ),

        AlgorithmDescriptor(
            id="point_pattern.quadrat_test", name="样方 χ² 离散检验",
            category="point_pattern",
            capabilities=["point_pattern_analysis"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["quadrat_analysis"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=10,
            algorithm_family="point_pattern_quadrat",
            assumptions=[
                "期望频数 N/(mn)；χ² 检验 df=mn-1",
                "样方划分覆盖数据 bbox（工具层自动 UTM 投影后划分）",
                "VMR（方差/均值比）>1 聚集、<1 均匀",
            ],
            limitations=[
                "对网格粒度敏感（粒度变→结论可变），建议多粒度对照",
                "期望频数<5 时 χ² 近似变差（结果内 chi2_approx_warning 披露）",
                "bbox 自适应窗口会把『集中在一角』归一化掉（lib 支持 fixed window）",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:4"],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_science.py::test_quadrat_single_quadrant_rejects_csr",
                "tests/unit/lib/test_point_pattern_science.py::test_quadrat_csr_not_significant",
            ],
            parameter_contract_ref="quadrat_analysis",
        ),

        # 既有实现（app/lib/geo_analysis/statistics.calculate_nearest）的
        # 描述符登记 —— 工具 nearest_neighbor 在 app/tools/spatial.py。
        # Foundation V2 (A3)：Clark-Evans 正态近似 z/p 落地后升级 VALIDATED。
        AlgorithmDescriptor(
            id="point_pattern.nni", name="最近邻指数（NNI）", category="point_pattern",
            capabilities=["point_pattern_analysis"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["nearest_neighbor"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="point_pattern_first_order",
            method_references=["clark_evans1954"],
            assumptions=[
                "R=观测最近邻均值/CSR 期望（0.5·√(A/N)，A 取 bbox）",
                "R<0.7 聚集 / >1.3 分散的阈值为经验分档（非检验）",
                "z=(R̄−E)/SE，SE=√((4−π)/(4πNρ))，ρ=N/A（Clark-Evans 1954）",
                "z 检验为正态近似，双侧 p 经 erfc；地理输入自动投影 UTM",
            ],
            limitations=[
                "正态近似 p 在小样本/边缘效应下有偏（无蒙特卡洛包络）",
                "bbox 面积作 CSR 期望，窗形偏离矩形时期望偏",
                "零面积 bbox（全重合点）下 z 检验不可用（nni_test_note 披露）",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:2"],
            uncertainty_outputs=["statistical_significance"],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_nearest_contract.py::test_nearest_contract_keys",
                "tests/unit/lib/test_nearest_contract.py::test_nearest_coincident_points_clustered",
                "tests/unit/lib/test_point_pattern_v2.py::test_nni_uniform_random_not_significant",
                "tests/unit/lib/test_point_pattern_v2.py::test_nni_clustered_jitter_z_large_negative",
                "tests/unit/lib/test_point_pattern_v2.py::test_nni_hand_computed_square_grid",
            ],
        ),

        # 既有实现（cluster_narrated, method=dbscan）的描述符登记 ——
        # 工具 spatial_cluster 在 app/tools/spatial_stats.py。
        AlgorithmDescriptor(
            id="point_pattern.dbscan", name="DBSCAN 密度聚类", category="point_pattern",
            capabilities=["point_pattern_analysis"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="hotspot_result",
            geometry_requirements=["point"],
            tool_candidates=["spatial_cluster"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="density_clustering",
            method_references=["ester_kriegel1996"],
            assumptions=[
                "eps（米）/min_samples 定义密度可达；地理输入自动投影 UTM",
                "无值维时纯空间聚类；value_field 时值维按坐标 σ 缩放（#867）",
            ],
            limitations=[
                "eps 对结果高度敏感且无自动选择",
                "密度不均的数据单一 eps 会把稀疏簇判为噪声",
            ],
            crs_class="GEOGRAPHIC_OK",
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_spatial_stats.py::test_spatial_cluster_dbscan",
                "tests/unit/test_spatial_stats.py::test_spatial_cluster_insufficient_points",
            ],
        ),

        # ── Foundation V2 (A3)：G/F/J、pcf、cross-K、Knox、K 包络 ────────

        AlgorithmDescriptor(
            id="point_pattern.g_f_j", name="G/F/J 距离函数", category="point_pattern",
            capabilities=["nearest_neighbor_functions"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["g_f_j_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="point_pattern_second_order",
            method_references=["diggle1983", "van_lieshout_baddeley1996", "ripley1976"],
            assumptions=[
                "G(r)=最近邻距离 CDF；F(r)=空空间函数（确定性低差异查询格）",
                "J(r)=(1−G)/(1−F)，CSR 下 J≡1（van Lieshout–Baddeley 1996）",
                "F 查询格：default_rng(42) 均匀点，n_f=min(4n, 2000)，观测/模拟共用",
                "包络零假设：同 n、同窗的同质 Poisson（CSR），固定种子 42",
            ],
            limitations=[
                "edge_correction=none（缺省）为原始估计——边界点低估 G/F；"
                "V3 起可选 border（reduced-sample）/isotropic（Ohser 加权）",
                "border 校正要求焦点/查询点到四边距离 > r_max（内点不足时诚实拒绝）",
                "J 在 F(r)→1 时分母退化记 NaN（j_undefined_from 披露）",
                "p 值来自秩检验（+1 校正），分辨率 1/(envelopes+1)，上限 499",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:10", "point_support_required"],
            uncertainty_outputs=["monte_carlo_summary", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="CSR fixture 的 G/F 落在固定种子包络内；规则格网 G 低于 CSR 于第一壳层内",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_v2.py::test_gfj_csr_within_fixed_seed_envelope",
                "tests/unit/lib/test_point_pattern_v2.py::test_gfj_regular_lattice_g_below_csr_first_shell",
                "tests/unit/lib/test_point_pattern_v2.py::test_gfj_clustered_g_above_csr_and_significant",
                "tests/unit/lib/test_point_pattern_v2.py::test_gfj_typed_errors_and_scale_guard",
            ],
            parameter_contract_ref="g_f_j_analysis",
        ),

        AlgorithmDescriptor(
            id="point_pattern.pcf", name="成对相关函数 g(r)", category="point_pattern",
            capabilities=["pair_correlation_function"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["pcf_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="point_pattern_second_order",
            method_references=["illian2008", "ripley1976"],
            assumptions=[
                "g(r)=K′(r)/(2πr)：由各向同性校正 K 的离散导数 + Epanechnikov 平滑",
                "bandwidth（米）缺省 0=一个 r 步宽（自动值在输出披露）",
                "CSR 参考 g≡1；g>1 聚集 / g<1 规则",
                "包络零假设：同质 Poisson（固定种子 42），sup|g−1| 秩检验",
            ],
            limitations=[
                "g 由 K 的离散导数间接估计，r 网格粒度限制分辨率",
                "Epanechnikov 平滑带宽敏感：小带宽噪声大、大带宽抹平峰值",
                "O(n²) 成对统计，上限 2 万点（超出诚实拒绝）",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:10", "point_support_required"],
            uncertainty_outputs=["monte_carlo_summary", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="CSR fixture 的 g(r) 落在固定种子包络内且均值≈1（±0.2）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_v2.py::test_pcf_csr_near_one_within_band",
                "tests/unit/lib/test_point_pattern_v2.py::test_pcf_clustered_peak_above_one",
                "tests/unit/lib/test_point_pattern_v2.py::test_pcf_envelopes_sup_statistic",
            ],
            parameter_contract_ref="pcf_analysis",
        ),

        AlgorithmDescriptor(
            id="point_pattern.cross_k", name="双变量交叉 K 函数", category="point_pattern",
            capabilities=["cross_k_function"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["cross_k_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="point_pattern_second_order",
            method_references=["besag1977", "ripley1976"],
            assumptions=[
                "K12(r)=A/(n1·n2)·Σ_{i∈1,j∈2} I(d≤r)/w_ij，w_ij 各向同性逐对校正",
                "随机标记（random labelling）零假设：类型标签在固定位置间置换",
                "置换从池化成对表重抽（非仅观测跨类对），固定种子 42",
                "type_field 必须恰有 2 个取值，每类 ≥5 点",
            ],
            limitations=[
                "随机标记只检验『给定位置下的类型关联』，不检验位置格局本身",
                "p 值来自 max|K12−πr²| 秩（+1 校正），分辨率 1/(permutations+1)",
                "O(n²) 成对统计，上限 2 万点（超出诚实拒绝）",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:10", "point_support_required"],
            uncertainty_outputs=["monte_carlo_summary", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="随机标记零假设下 p>0.05；类型空间分离 fixture p<0.05",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_v2.py::test_cross_k_requires_exactly_two_types",
                "tests/unit/lib/test_point_pattern_v2.py::test_cross_k_random_labelling_null_not_significant",
                "tests/unit/lib/test_point_pattern_v2.py::test_cross_k_segregation_below_envelope",
            ],
            parameter_contract_ref="cross_k_analysis",
        ),

        AlgorithmDescriptor(
            id="spatiotemporal.knox", name="Knox 时空交互检验", category="point_pattern",
            capabilities=["space_time_interaction"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["knox_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="space_time_interaction",
            method_references=["knox1964"],
            assumptions=[
                "观测=同时落在 critical_distance（米）与 critical_time（秒）内的点对数",
                "独立零假设期望 E=2·S·T/(n(n−1))；时间置换（固定种子 42）给单侧 p",
                "critical_distance=0 → 自动取中位最近邻距离（输出披露）",
                "时间戳解析与 ST-DBSCAN 同约定（ISO-8601/Epoch，utc）",
            ],
            limitations=[
                "阈值（距离/时间）敏感且结果随阈值变化——建议多阈值对照",
                "时间置换保边际分布，不校正时空趋势（Mantel 类检验更合适）",
                "空间邻近对经 query_pairs 稀疏化，预算超限诚实拒绝",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:4", "point_support_required"],
            uncertainty_outputs=["monte_carlo_summary", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="4 点 2×2 手算例：观测/期望/空间对/时间对精确匹配",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_space_time_interaction.py::test_knox_hand_computed_4point_example",
                "tests/unit/lib/test_space_time_interaction.py::test_knox_spacetime_clustered_significant",
                "tests/unit/lib/test_space_time_interaction.py::test_knox_independent_not_significant",
                "tests/unit/lib/test_space_time_interaction.py::test_knox_insufficient_and_scale_guards",
                "tests/unit/lib/test_space_time_interaction.py::test_knox_nan_times_dropped_with_disclosure",
                "tests/unit/lib/test_space_time_interaction.py::test_knox_auto_critical_distance",
                "tests/unit/lib/test_space_time_interaction.py::test_knox_duplicate_timestamp_ties_disclosed",
            ],
            parameter_contract_ref="knox_analysis",
        ),

        AlgorithmDescriptor(
            id="point_pattern.ripley_k_env", name="Ripley's K + CSR 模拟包络",
            category="point_pattern",
            capabilities=["point_pattern_analysis"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["ripley_k_envelope_analysis"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="point_pattern_second_order",
            method_references=["ripley1976"],
            assumptions=[
                "与 point_pattern.ripley_k 同一估计器（isotropic 边缘校正）",
                "包络：envelopes 次同 n、同窗同质 Poisson 模拟（固定种子 42）",
                "逐半径秩双侧 p 值（+1 校正）；观测 K 与包络同估计器可比",
            ],
            limitations=[
                "p 值分辨率 1/(envelopes+1)，上限 499",
                "模拟重跑 K 估计器：envelopes 大 × n 大时计算量线性放大",
                "非矩形研究域的边缘校正按外接矩形近似",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:10"],
            uncertainty_outputs=["monte_carlo_summary", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="CSR fixture 的 K(r) 落在自身固定种子包络内（p>0.05）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_v2.py::test_ripley_k_envelopes_csr_honest_and_additive_keys",
                "tests/unit/lib/test_point_pattern_v2.py::test_ripley_k_default_call_unchanged_keys",
            ],
            parameter_contract_ref="ripley_k_envelope_analysis",
        ),

        # ── Foundation V3：时空 K / Mantel / 双变量 g12 ────────────────
        AlgorithmDescriptor(
            id="point_pattern.space_time_k", name="时空 K 函数 K_st(r,t)",
            category="point_pattern",
            capabilities=["space_time_k_function"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["space_time_k_analysis"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="point_pattern_space_time",
            method_references=["diggle1995", "ripley1976"],
            assumptions=[
                "K_st(r,t)=|W|·T/(n(n−1))·Σ_{i≠j} I(d≤r)I(|Δt|≤t)/w_ij"
                "（有序对双向计入；w_ij 与单变量 K 同款各向同性校正）",
                "独立零假设参考 K_st=πr²·2t（K_s=πr² 与 K_t=2t 之积）",
                "显著性：时间标签置换（固定种子 42），sup(K_st−ref) 单侧 greater",
            ],
            limitations=[
                "时间维无边缘校正：观测窗端点附近 Δt 分布被截断，"
                "结论对窗长敏感（meta 中 temporal_edge_note 披露）",
                "O(n²) 成对统计：空间对稀疏化 + 配对预算先估后分配，"
                "上限 2 万点",
                "p 值分辨率 1/(permutations+1)，上限 499",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:8", "point_support_required"],
            uncertainty_outputs=["monte_carlo_summary", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="构造时空聚集 fixture 的 sup(K−ref)>0 且置换 p<0.05；"
                                "时间标签洗牌后 p>0.05",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_v3.py::test_space_time_k_clusters_and_shuffled",
                "tests/unit/lib/test_point_pattern_v3.py::test_space_time_k_determinism_and_guards",
            ],
            parameter_contract_ref="space_time_k_analysis",
        ),

        AlgorithmDescriptor(
            id="point_pattern.mantel", name="Mantel 时空距离相关检验",
            category="point_pattern",
            capabilities=["mantel_test"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["mantel_test_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="point_pattern_space_time",
            method_references=["mantel1967"],
            assumptions=[
                "标准化 Mantel r = Pearson(上三角空间距离, 上三角时间距离)",
                "时间标签置换（固定种子 42）构成零假设分布；"
                "alternative=greater（聚集方向，缺省）/ two-sided",
            ],
            limitations=[
                "Mantel 把全部点对当独立样本（距离矩阵非独立），"
                "对空间自相关敏感——meta 中 disclosure 披露",
                "密集 n×n 距离矩阵：n ≤ 2000 诚实上限（超限结构化拒绝）",
                "p 值分辨率 1/(permutations+1)，上限 999",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:6", "point_support_required"],
            uncertainty_outputs=["monte_carlo_summary", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="构造关联 fixture r>0.5 且置换 p<0.05；"
                                "时间洗牌后 |r|≈0 且 p>0.05；同输入重放逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_v3.py::test_mantel_association_and_shuffle",
                "tests/unit/lib/test_point_pattern_v3.py::test_mantel_determinism_and_guards",
            ],
            parameter_contract_ref="mantel_analysis",
        ),

        AlgorithmDescriptor(
            id="point_pattern.cross_pcf", name="双变量成对相关函数 g12(r)",
            category="point_pattern",
            capabilities=["cross_pair_correlation"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="stats_table",
            geometry_requirements=["point"],
            tool_candidates=["cross_pcf_analysis"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", priority=20,
            algorithm_family="point_pattern_second_order",
            method_references=["illian2008", "besag1977"],
            assumptions=[
                "g12(r)=K12′(r)/(2πr)：交叉 K12（各向同性校正）的离散导数"
                " + Epanechnikov 平滑（与单变量 pcf 同款后处理）",
                "random-labelling 参考 g12≡1；g12>1 两类吸引/共现，g12<1 相斥",
                "bandwidth（米）缺省 0=一个 r 步宽（自动值在输出披露）",
            ],
            limitations=[
                "g12 由 K12 的离散导数间接估计，r 网格粒度限制分辨率",
                "每类 ≥5 点（否则诚实拒绝）；O(n²) 成对统计上限 2 万点",
                "p 值来自 sup|g12−1| 秩检验（+1 校正），上限 499",
            ],
            crs_class="GEOGRAPHIC_OK",
            scientific_preconditions=["min_numeric_samples:10", "point_support_required"],
            uncertainty_outputs=["monte_carlo_summary", "statistical_significance"],
            random_seed_policy="fixed_seed",
            numerical_tolerance="完全空间随机 + 随机标记下 g12 包络覆盖 1；"
                                "同输入重放逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_point_pattern_v3.py::test_cross_pcf_runs_and_labels",
                "tests/unit/lib/test_point_pattern_v3.py::test_cross_pcf_determinism_and_guards",
            ],
            parameter_contract_ref="cross_pcf_analysis",
        ),
]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────
# 已知中央缺陷（见 statistics 域包 ensure_parameter_contracts_registered
# 的注释）：iter_contract_packs 聚合不到域契约 —— descriptor 暂不挂
# 契约经 iter_contract_packs 自动聚合。

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        # v2（Foundation V2 A3）：可选 envelopes（0=关，输出键不变）；
        # additive —— 旧工具按名取参不受新默认键影响，parity 只查必填参数。
        id="ripley_k_analysis", version=2,
        description="Ripley's K：r 网格步数、最大半径比例与可选 CSR 包络。",
        parameters=[
            ParameterSpec(
                name="n_steps", type="integer", default=10, minimum=4, maximum=32,
                unit="count",
                description="r 网格步数（r_max/n_steps 到 r_max 等距）",
            ),
            ParameterSpec(
                name="max_distance_ratio", type="number", default=0.25,
                minimum=0.05, maximum=0.5, unit="ratio",
                description="r_max = 比例 × min(窗宽,窗高)；上限 0.5（半窗）",
            ),
            ParameterSpec(
                name="envelopes", type="integer", default=0, minimum=0, maximum=499,
                unit="count",
                description="CSR 模拟包络次数（固定种子 42）；0=关（输出键不变）",
            ),
        ],
    ),
    ParameterContract(
        # v2（Foundation V3 additive）：可选 edge_correction
        # （none=历史缺省行为不变；border/isotropic 为新增校正）。
        id="g_f_j_analysis", version=2,
        description="G/F/J 距离函数：r 网格、最大半径比例、CSR 包络与边缘校正。",
        parameters=[
            ParameterSpec(
                name="n_steps", type="integer", default=10, minimum=4, maximum=32,
                unit="count",
                description="r 网格步数（r_max/n_steps 到 r_max 等距）",
            ),
            ParameterSpec(
                name="max_distance_ratio", type="number", default=0.25,
                minimum=0.05, maximum=0.5, unit="ratio",
                description="r_max = 比例 × min(窗宽,窗高)；上限 0.5（半窗）",
            ),
            ParameterSpec(
                name="envelopes", type="integer", default=0, minimum=0, maximum=499,
                unit="count",
                description="同质 Poisson 模拟包络次数（固定种子 42）；0=关",
            ),
            ParameterSpec(
                name="edge_correction", type="enum", default="none",
                enum_values=["none", "border", "isotropic"],
                description="边缘校正：none=原始估计（历史缺省）；"
                            "border=reduced-sample（内点）；isotropic=Ohser 加权",
            ),
        ],
    ),
    ParameterContract(
        id="pcf_analysis", version=1,
        description="成对相关函数 g(r)：r 网格、平滑带宽与 CSR 包络。",
        parameters=[
            ParameterSpec(
                name="n_steps", type="integer", default=10, minimum=4, maximum=32,
                unit="count",
                description="r 网格步数（r_max/n_steps 到 r_max 等距）",
            ),
            ParameterSpec(
                name="max_distance_ratio", type="number", default=0.25,
                minimum=0.05, maximum=0.5, unit="ratio",
                description="r_max = 比例 × min(窗宽,窗高)；上限 0.5（半窗）",
            ),
            ParameterSpec(
                name="bandwidth", type="number", default=0, minimum=0,
                unit="meters",
                description="Epanechnikov 平滑带宽（米，r 单位）；0=自动（一个 r 步宽）",
            ),
            ParameterSpec(
                name="envelopes", type="integer", default=0, minimum=0, maximum=499,
                unit="count",
                description="同质 Poisson 模拟包络次数（固定种子 42）；0=关",
            ),
        ],
    ),
    ParameterContract(
        id="cross_k_analysis", version=1,
        description="双变量交叉 K：类型字段、r 网格与随机标记置换。",
        parameters=[
            ParameterSpec(
                name="type_field", type="string", required=True,
                description="类型字段名（必须恰有 2 个不同取值，每类 ≥5 点）",
            ),
            ParameterSpec(
                name="n_steps", type="integer", default=10, minimum=4, maximum=32,
                unit="count",
                description="r 网格步数（r_max/n_steps 到 r_max 等距）",
            ),
            ParameterSpec(
                name="max_distance_ratio", type="number", default=0.25,
                minimum=0.05, maximum=0.5, unit="ratio",
                description="r_max = 比例 × min(窗宽,窗高)；上限 0.5（半窗）",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="199",
                enum_values=["99", "199", "499"],
                description="随机标记置换次数（固定种子 42）",
            ),
        ],
    ),
    ParameterContract(
        id="knox_analysis", version=1,
        description="Knox 时空交互：时间字段、空间/时间阈值与时间置换。",
        parameters=[
            ParameterSpec(
                name="time_field", type="string", required=True,
                description="时间戳字段名（ISO-8601 或 Epoch；NaT 行剔除并披露）",
            ),
            ParameterSpec(
                name="critical_distance", type="number", default=0,
                minimum=0, unit="meters",
                description="空间阈值（米）；0=自动取中位最近邻距离（披露）",
            ),
            ParameterSpec(
                name="critical_time", type="number", required=True,
                minimum=0.001, unit="seconds",
                description="时间阈值（秒，必须为正）",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="199",
                enum_values=["99", "199", "499", "999"],
                description="时间置换次数（固定种子 42）",
            ),
        ],
    ),
    ParameterContract(
        id="ripley_k_envelope_analysis", version=1,
        description="Ripley's K + CSR 包络：r 网格、比例与包络次数（≥1）。",
        parameters=[
            ParameterSpec(
                name="n_steps", type="integer", default=10, minimum=4, maximum=32,
                unit="count",
                description="r 网格步数（r_max/n_steps 到 r_max 等距）",
            ),
            ParameterSpec(
                name="max_distance_ratio", type="number", default=0.25,
                minimum=0.05, maximum=0.5, unit="ratio",
                description="r_max = 比例 × min(窗宽,窗高)；上限 0.5（半窗）",
            ),
            ParameterSpec(
                name="envelopes", type="integer", default=99, minimum=1, maximum=499,
                unit="count",
                description="CSR 模拟包络次数（固定种子 42）",
            ),
        ],
    ),
    ParameterContract(
        id="quadrat_analysis", version=1,
        description="样方 χ² 检验：网格行×列。",
        parameters=[
            ParameterSpec(
                name="grid_rows", type="integer", default=4, minimum=2, maximum=10,
                unit="count",
                description="样方行数",
            ),
            ParameterSpec(
                name="grid_cols", type="integer", default=4, minimum=2, maximum=10,
                unit="count",
                description="样方列数",
            ),
        ],
    ),
    # ── Foundation V3：时空 K / Mantel / 双变量 g12 ─────────────────
    ParameterContract(
        id="space_time_k_analysis", version=1,
        description="时空 K 函数 K_st(r,t)：r/t 网格、空间半径比例与时间置换。",
        parameters=[
            ParameterSpec(
                name="n_steps_r", type="integer", default=8, minimum=4, maximum=24,
                unit="count",
                description="r 网格步数（r_max/n_steps_r 到 r_max 等距）",
            ),
            ParameterSpec(
                name="n_steps_t", type="integer", default=8, minimum=4, maximum=24,
                unit="count",
                description="t 网格步数（t_max/n_steps_t 到 t_max 等距）",
            ),
            ParameterSpec(
                name="max_distance_ratio", type="number", default=0.25,
                minimum=0.05, maximum=0.5, unit="ratio",
                description="r_max = 比例 × min(窗宽,窗高)；t_max = 时间跨度的一半",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="199",
                enum_values=["0", "99", "199", "499"],
                description="时间标签置换次数（固定种子 42）；0=关",
            ),
        ],
    ),
    ParameterContract(
        id="mantel_analysis", version=1,
        description="Mantel 时空检验：标准化 r + 时间标签置换。",
        parameters=[
            ParameterSpec(
                name="permutations", type="enum", default="499",
                enum_values=["0", "199", "499", "999"],
                description="时间标签置换次数（固定种子 42）；0=关",
            ),
            ParameterSpec(
                name="alternative", type="enum", default="greater",
                enum_values=["greater", "two-sided"],
                description="greater=时空聚集方向（缺省）；two-sided=双侧",
            ),
        ],
    ),
    ParameterContract(
        id="cross_pcf_analysis", version=1,
        description="双变量 g12(r)：类型字段、r 网格、平滑带宽与随机标记置换。",
        parameters=[
            ParameterSpec(
                name="type_field", type="string", required=True,
                description="类型字段名（必须恰有 2 个不同取值，每类 ≥5 点）",
            ),
            ParameterSpec(
                name="n_steps", type="integer", default=10, minimum=4, maximum=32,
                unit="count",
                description="r 网格步数（r_max/n_steps 到 r_max 等距）",
            ),
            ParameterSpec(
                name="max_distance_ratio", type="number", default=0.25,
                minimum=0.05, maximum=0.5, unit="ratio",
                description="r_max = 比例 × min(窗宽,窗高)；上限 0.5（半窗）",
            ),
            ParameterSpec(
                name="bandwidth", type="number", default=0, minimum=0,
                unit="meters",
                description="Epanechnikov 平滑带宽（米，r 单位）；0=自动（一个 r 步宽）",
            ),
            ParameterSpec(
                name="permutations", type="enum", default="199",
                enum_values=["0", "99", "199", "499"],
                description="随机标记置换次数（固定种子 42）；0=关",
            ),
        ],
    ),
]
