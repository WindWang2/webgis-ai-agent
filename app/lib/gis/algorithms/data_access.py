"""数据获取 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 data_access 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="poi.query.local", name="POI 查询（本地优先）",
            capabilities=["poi_query"],
            input_artifact_types=[],
            output_artifact_type="poi_feature_set",
            geometry_requirements=[],
            tool_candidates=["query_local_poi", "search_poi", "query_osm_poi"],
            cpu_cost="low", memory_cost="low", io_cost="medium",
            preferred_execution_policy="ASYNC",
            priority=10,
        ),

        AlgorithmDescriptor(
            id="admin.boundary.local", name="行政区边界获取（本地 SHP）",
            capabilities=["admin_boundary_query"],
            output_artifact_type="admin_boundary_set",
            geometry_requirements=["polygon"],
            tool_candidates=["get_local_admin_boundary"],
            cpu_cost="low", memory_cost="low", io_cost="medium",
            preferred_execution_policy="ASYNC",
            priority=10,
        ),

        AlgorithmDescriptor(
            id="raster.source.dem", name="DEM 栅格获取",
            capabilities=["raster_source"],
            output_artifact_type="terrain_surface",
            geometry_requirements=["raster"],
            tool_candidates=["fetch_dem"],
            cpu_cost="low", memory_cost="medium", io_cost="high",
            preferred_execution_policy="ASYNC",
            compatible_map_models=["raster_surface"],
            priority=10,
        ),

        AlgorithmDescriptor(
            id="admin.boundary_lookup", name="行政区边界获取", category="data_access",
            capabilities=["admin_boundary_query"],
            output_artifact_type="polygon_feature_set",
            tool_candidates=["get_admin_division"],
            cpu_cost="low", memory_cost="low", io_cost="medium",
            preferred_execution_policy="THREAD", priority=10,
        ),

        AlgorithmDescriptor(
            id="poi.area_search", name="区域 POI 检索", category="data_access",
            capabilities=["poi_query"],
            output_artifact_type="poi_feature_set",
            tool_candidates=["search_poi_around", "search_poi_polygon"],
            cpu_cost="low", memory_cost="low", io_cost="medium",
            preferred_execution_policy="ASYNC", priority=20,
        ),
]
