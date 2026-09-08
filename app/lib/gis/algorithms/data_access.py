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

        # ── 数据控制平面（Data Control Plane V4 / GeoCompute V5）──────────

        AlgorithmDescriptor(
            id="data.ingest.pipeline", name="会话数据摄入管线",
            capabilities=["dataset_ingest"],
            output_artifact_type="feature_collection",
            tool_candidates=["ingest_dataset"],
            cpu_cost="medium", memory_cost="high", io_cost="medium",
            preferred_execution_policy="ASYNC",
            algorithm_family="data_access",
            assumptions=["内容指纹去重可重复触发（同载荷幂等返回既有 ref）"],
            limitations=["内联载荷 ≤8MB；更大文件走 POST /upload 通道"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
            priority=10,
        ),

        AlgorithmDescriptor(
            id="data.federated.chain", name="多源链式联邦查询",
            capabilities=["federated_dataset_query"],
            output_artifact_type="stats_table",
            tool_candidates=["query_federated_chain"],
            cpu_cost="medium", memory_cost="medium", io_cost="high",
            preferred_execution_policy="CELERY",
            algorithm_family="data_access",
            assumptions=["左深链计划；半连接右表约减在预算内"],
            limitations=["逐跳预算 fail-fast；绝不拉全量大表"],
            crs_class="PROJECTED_REQUIRED",
            random_seed_policy="deterministic",
            priority=10,
        ),

        AlgorithmDescriptor(
            id="workspace.snapshot.durable", name="工作空间快照（保存/恢复）",
            capabilities=["workspace_snapshot"],
            tool_candidates=["save_workspace_snapshot", "restore_workspace_snapshot"],
            cpu_cost="low", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="ASYNC",
            algorithm_family="data_access",
            assumptions=["快照元数据始终落盘；materialize=claimed 时载荷物化到持久内容库"],
            limitations=["materialize=none 的快照在会话过期后仅元数据可读"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
            priority=10,
        ),

        AlgorithmDescriptor(
            id="workspace.inspection.readonly", name="工作空间状态检视（只读）",
            capabilities=["workspace_state_inspection"],
            tool_candidates=["describe_workspace", "list_workspace_snapshots"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="LOCAL",
            algorithm_family="data_access",
            assumptions=["只读投影，绝不改工作空间状态"],
            crs_class="CRS_AGNOSTIC",
            random_seed_policy="deterministic",
            priority=10,
        ),
    ]
