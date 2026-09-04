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
                "tests/unit/lib/test_point_pattern_science.py::test_ripley_k_deterministic",
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
                "地理输入自动投影到局部 UTM",
            ],
            limitations=[
                "R 阈值无显著性检验（p 值未实现）",
                "bbox 面积作 CSR 期望，窗形偏离矩形时期望偏",
            ],
            crs_class="GEOGRAPHIC_OK",
            random_seed_policy="deterministic",
            scientific_status="EXPERIMENTAL",
            conformance_tests=[
                "tests/unit/lib/test_nearest_contract.py::test_nearest_contract_keys",
                "tests/unit/lib/test_nearest_contract.py::test_nearest_coincident_points_clustered",
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
]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────
# 已知中央缺陷（见 statistics 域包 ensure_parameter_contracts_registered
# 的注释）：iter_contract_packs 聚合不到域契约 —— descriptor 暂不挂
# 契约经 iter_contract_packs 自动聚合。

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="ripley_k_analysis", version=1,
        description="Ripley's K：r 网格步数与最大半径比例。",
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
]
