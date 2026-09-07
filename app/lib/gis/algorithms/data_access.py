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
            algorithm_family="data_access",
            assumptions=["本地数据优先（本地索引），不做几何改写"],
            limitations=["查询结果依赖本地数据完整性（元数据披露来源）"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
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
            algorithm_family="data_access",
            assumptions=["本地 SHP 边界面获取（不做几何改写）"],
            limitations=["边界现势性依赖本地数据版本（来源披露）"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
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
            algorithm_family="data_access",
            assumptions=["DEM 拉取（Copernicus 30m）经 STAC/非交互通道"],
            limitations=["在线数据源：可用性与产品版本不受本库控制（external）"],
            crs_class="RASTER_GRID",
            random_seed_policy="deterministic",
            scientific_status="EXPERIMENTAL",
            compatible_map_models=["raster_surface"],
            priority=10,
        ),

        AlgorithmDescriptor(
            id="admin.boundary_lookup", name="行政区边界获取", category="data_access",
            deterministic=False,
            capabilities=["admin_boundary_query"],
            output_artifact_type="polygon_feature_set",
            tool_candidates=["get_admin_division"],
            cpu_cost="low", memory_cost="low", io_cost="medium",
            preferred_execution_policy="THREAD", priority=10,
            algorithm_family="data_access",
            assumptions=["行政区边界检索（本地优先，在线兜底）"],
            limitations=["在线兜底结果可变（deterministic=False 已声明）"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="unseeded",
            scientific_status="EXPERIMENTAL",
        ),

        AlgorithmDescriptor(
            id="poi.area_search", name="区域 POI 检索", category="data_access",
            deterministic=False,
            capabilities=["poi_query"],
            output_artifact_type="poi_feature_set",
            tool_candidates=["search_poi_around", "search_poi_polygon"],
            cpu_cost="low", memory_cost="low", io_cost="medium",
            preferred_execution_policy="ASYNC", priority=20,
            algorithm_family="data_access",
            assumptions=["范围检索（在线 POI 服务）——external API 客户端",
                         "结果内容/排序由服务方决定（本库不重排）"],
            limitations=["deterministic=False：在线结果可变（已声明）",
                         "服务配额/风控可能拒绝（结构化错误返回）"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="unseeded",
            scientific_status="EXPERIMENTAL",
        ),
]
