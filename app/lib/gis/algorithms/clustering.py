"""空间聚类 域算法包（method-experiment WP1 补缺）。

背景（Phase 0 证实缺口）：``spatial_cluster`` 工具与
``geo_analysis/statistics.py::cluster_narrated`` 实现早已存在，但
AlgorithmRegistry 从未为普通空间聚类注册描述符（此前只有 ST-DBSCAN
的 ``stats.st_dbscan``）——方法实验引擎引用算法 id 时悬空。

本模块把既有实现补入注册表（只补描述符，零实现复制）：

- ``spatial.cluster.kmeans``   —— K-Means（fixed seed 42，statistics.py 实现）
- ``spatial.cluster.dbscan``   —— DBSCAN（确定性；statistics.py 实现）

能力 ``spatial_clustering`` 在 capabilities/statistics.py 同批登记。
实现真相：``app/lib/geo_analysis/statistics.py::cluster_narrated``；
工具层：``app/tools/spatial_stats.py::spatial_cluster``。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [
    AlgorithmDescriptor(
        id="spatial.cluster.kmeans", name="K-Means 空间聚类",
        capabilities=["spatial_clustering"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="point_feature_set",
        geometry_requirements=["point"],
        tool_candidates=["spatial_cluster"],
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="INLINE",
        algorithm_family="spatial_clustering",
        assumptions=[
            "欧氏距离下的球形等方差簇假设（非凸簇形状不保证）",
            "必须显式给定 k（无自动选 k）",
        ],
        limitations=[
            "k 过大 → 过分割；k 过小 → 异质簇合并",
            "value_field 参与时值维缩放由 value_weight 控制（默认等权保守）",
        ],
        crs_class="PROJECTED_REQUIRED",
        min_features=3,
        random_seed_policy="fixed_seed",
        deterministic=True,
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/gis_harness/method_experiment/"
            "test_primitives.py::test_cluster_stability_three_gaussian_clusters",
            "tests/unit/gis_harness/method_experiment/"
            "test_perf_and_isolation.py::test_candidate_scaling_linear_dispatch_count",
        ],
        numerical_tolerance="1e-6",
        complexity="O(n·k·i)",
        priority=50,
    ),
    AlgorithmDescriptor(
        id="spatial.cluster.dbscan", name="DBSCAN 密度空间聚类",
        capabilities=["spatial_clustering"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="point_feature_set",
        geometry_requirements=["point"],
        tool_candidates=["spatial_cluster"],
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="INLINE",
        algorithm_family="spatial_clustering",
        assumptions=[
            "密度可达定义簇（eps 半径内 ≥ min_samples 邻居为核心点）",
            "eps 单位为米（metric 投影坐标上的真实距离）",
        ],
        limitations=[
            "eps/min_samples 过严 → 高噪声占比（诚实披露，非错误）",
            "密度差异大的数据单全局 eps 不适应（ST-DBSCAN/分层变体另行评估）",
        ],
        crs_class="PROJECTED_REQUIRED",
        min_features=3,
        random_seed_policy="deterministic",
        deterministic=True,
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/gis_harness/method_experiment/"
            "test_primitives.py::test_cluster_stability_dbscan_bootstrap",
        ],
        numerical_tolerance="1e-6",
        complexity="O(n log n)",
        priority=50,
    ),
]
