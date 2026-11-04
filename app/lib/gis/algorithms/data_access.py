"""数据获取 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 data_access 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor, BackendVariant

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="poi.query.local", name="POI 查询（本地优先）",
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_query_local_poi_db_missing_typed_contract",
                "tests/unit/gis/test_algo_conformance_v2.py::test_query_local_poi_polygon_format_typed_contract",
            ],
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
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_admin_boundary_local_level_and_identifier_contract",
            ],
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
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_fetch_dem_bbox_typed",
                "tests/unit/gis/test_algo_conformance_v2.py::test_fetch_dem_item_mapping_oracle",
            ],
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
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_admin_boundary_lookup_local_first_hit",
                "tests/unit/gis/test_algo_conformance_v2.py::test_admin_boundary_lookup_typed_token_error_when_unconfigured",
            ],
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
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_poi_around_typed_geometry_contract",
                "tests/unit/gis/test_algo_conformance_v2.py::test_poi_polygon_extraction_typed_contract",
            ],
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
            backend_variants=[
                BackendVariant(
                    id="pure_python_inline", backend="pure_python", deterministic=True,
                    min_features=1, max_features=50000,
                    notes="内联工具路径 ≤8MB 载荷（_MAX_PAYLOAD_BYTES，约数万要素，保守取 5 万）；更大走 /upload 文件通道"),
            ],
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_ingest_pipeline_fingerprint_dedup_and_bounded_profile",
                "tests/unit/gis/test_algo_conformance_v2.py::test_ingest_pipeline_tool_typed_payload_rejection",
            ],
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
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_federated_chain_two_source_attribute_join_oracle",
                "tests/unit/gis/test_algo_conformance_v2.py::test_federated_chain_typed_errors",
            ],
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
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_workspace_snapshot_durable_roundtrip",
            ],
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
            conformance_tests=[
                "tests/unit/gis/test_algo_conformance_v2.py::test_workspace_inspection_readonly_contract",
            ],
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
        AlgorithmDescriptor(
            id="data.prepare", priority=10, name="数据集准备", category="data_access",
            capabilities=["data_preparation"], tool_candidates=["prepare_dataset"],
            deterministic=False, preferred_execution_policy="ASYNC",
            crs_class="CRS_AGNOSTIC", algorithm_family="data_access",
            random_seed_policy="none",
            assumptions=["输入为会话内 source_ref；准备计划保留原始数据并产新引用"],
            limitations=["需确认的字段/坐标歧义以 requires_input 或 blocked 返回"],
            conformance_tests=["tests/unit/gis/test_native_surface_bindings.py::test_native_surface_binding_contract",
                "tests/unit/test_data_preparation_tools_v1.py::TestDispatchRealChain::test_prepared_state_full_auto"],
        ),
        AlgorithmDescriptor(
            id="layer.features.page", priority=10, name="图层要素分页", category="data_access",
            capabilities=["layer_browse"], tool_candidates=["webgis_layer_features_page"],
            deterministic=True, preferred_execution_policy="ASYNC",
            crs_class="CRS_AGNOSTIC", algorithm_family="data_access",
            random_seed_policy="deterministic",
            assumptions=["有界分页、字段投影和 typed 过滤，不下载整包"],
            limitations=["返回 total_matching/is_complete/truncated，未知总数不得当全量"],
            conformance_tests=["tests/unit/gis/test_native_surface_bindings.py::test_native_surface_binding_contract",
                "tests/unit/test_layer_browse_tools.py::test_features_page_honest_metadata"],
        ),
        AlgorithmDescriptor(
            id="layer.aggregate", priority=20, name="图层窗口聚合", category="data_access",
            capabilities=["layer_browse"], tool_candidates=["webgis_layer_aggregate"],
            deterministic=True, preferred_execution_policy="ASYNC",
            crs_class="CRS_AGNOSTIC", algorithm_family="data_access",
            random_seed_policy="deterministic",
            assumptions=["全扫过滤窗口的分组统计，原始数据只读"],
            limitations=["分组溢出和非数值计数显式披露，不补造分母"],
            conformance_tests=["tests/unit/gis/test_native_surface_bindings.py::test_native_surface_binding_contract",
                "tests/unit/test_layer_browse_tools.py::test_aggregate_window_stats"],
        ),
        AlgorithmDescriptor(
            id="layer.feature.detail", priority=30, name="单要素详情", category="data_access",
            capabilities=["layer_browse"], tool_candidates=["webgis_layer_feature_detail"],
            deterministic=True, preferred_execution_policy="ASYNC",
            crs_class="CRS_AGNOSTIC", algorithm_family="data_access",
            random_seed_policy="deterministic",
            assumptions=["按稳定 id 获取单要素，原始数据只读"],
            limitations=["positional id 仅对当前数据版本稳定"],
            conformance_tests=["tests/unit/gis/test_native_surface_bindings.py::test_native_surface_binding_contract",
                "tests/unit/test_layer_browse_tools.py::test_feature_detail_native_and_positional_namespaces"],
        ),
        AlgorithmDescriptor(
            id="spatial.semantics.inspect", priority=10, name="空间语义契约检视", category="data_access",
            capabilities=["spatial_semantics"], tool_candidates=["inspect_spatial_semantics"],
            deterministic=True, preferred_execution_policy="LOCAL",
            crs_class="CRS_AGNOSTIC", algorithm_family="data_access",
            random_seed_policy="deterministic",
            assumptions=["检视 CRS 来源、轴序与计算空间，不改写数据"],
            limitations=["未知 CRS 如实报告 unknown，不猜测坐标系"],
            conformance_tests=["tests/unit/gis/test_native_surface_bindings.py::test_native_surface_binding_contract",
                "tests/unit/gis_harness/spatial_semantics/test_tool_adapters.py::test_inspect_spatial_semantics_tool_discoverable"],
        ),
    ]
