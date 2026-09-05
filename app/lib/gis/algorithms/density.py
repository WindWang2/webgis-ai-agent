"""密度分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 density 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="density.visual.heatmap", name="视觉热力（渲染态密度）",
            capabilities=["density_surface"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="density_surface",
            geometry_requirements=["point"],
            min_features=10,               # heatmap_data 工具硬门槛（HEATMAP_MIN_POINTS）
            # ADR-0083：原生渲染通道硬上限 —— 前端 ref-source-resolver 的
            # FETCH_FEATURE_CAP（20k）：超过该点数前端拒绝挂载 ref，视觉热力
            # 必须降级聚合/服务端通道（capability fallback → grid_binning）。
            max_features_hint=20_000,
            approximate=True, deterministic=False,
            complexity="O(N) GPU/渲染端",
            tool_candidates=["heatmap_data"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["visual_heatmap"],
            priority=10,
        random_seed_policy="none",
        ),

        AlgorithmDescriptor(
            id="spatial.kde.contours", name="核密度等值线",
            capabilities=["kde_density"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="density_surface",
            geometry_requirements=["point"],
            approximate=True, deterministic=False,
            complexity="O(N·grid)",
            tool_candidates=["kde_contours"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY",
            compatible_map_models=["visual_heatmap", "isoline_contour"],
            fallback_algorithms=["spatial.kde.surface"],
            priority=10,
        random_seed_policy="none",
        fallback_semantics={"spatial.kde.surface": "equivalent"},
        ),

        AlgorithmDescriptor(
            id="spatial.kde.surface", name="核密度全格网表面",
            capabilities=["kde_density"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="density_surface",
            geometry_requirements=["point"],
            approximate=True, deterministic=False,
            complexity="O(N·grid)",
            tool_candidates=["kde_surface"],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="CELERY",
            compatible_map_models=["visual_heatmap"],
            fallback_algorithms=["spatial.kde.contours"],
            priority=20,
        random_seed_policy="none",
        fallback_semantics={"spatial.kde.contours": "equivalent"},
        ),

        AlgorithmDescriptor(
            id="density.analytical.mixed", name="分析密度（KDE/聚合混合路径）",
            capabilities=["analytical_density"],
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_type="density_surface",
            geometry_requirements=["point"],
            approximate=True, deterministic=False,
            tool_candidates=["kde_contours", "heatmap_data", "spatial_aggregate"],
            cpu_cost="high", memory_cost="medium", io_cost="low",
            preferred_execution_policy="CELERY",
            compatible_map_models=["administrative_choropleth", "aggregate_grid"],
            # 混合聚合路径（非专有算法）：priority 低于专有算法，使
            # tool_to_capability 的首选工具归属正确（kde_contours →
            # spatial.kde.contours/kde_density，而非本混合路径）。
            priority=30,
        random_seed_policy="none",
        ),
]
