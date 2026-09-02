"""
Geospatial Data Fabric: Unified AI Tools
7 Unified AI Tools for managing, inspecting, catalog searching, querying, materializing,
and health-monitoring Data Fabric geospatial data sources.
"""
import asyncio
import logging
from typing import Optional
from app.tools.registry import ToolRegistry, tool, ToolExecutionPolicy
from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    QuerySpec,
)
from app.services.data_fabric.security import DataFabricSecurity
from app.services.data_fabric.spatial_catalog import spatial_catalog_service
from app.services.data_fabric.fingerprint import dataset_fingerprint_service
from app.services.data_fabric.materialization_service import materialization_service
from app.services.data_fabric.health import data_fabric_health_check
from app.services.data_fabric.connection_manager import connection_manager
from app.services.data_fabric.registry import build_adapter, resolve_adapter_spec
from app.services.data_fabric.errors import (
    DATASET_NOT_FOUND,
    UNSUPPORTED_SOURCE,
    DataFabricError,
)

logger = logging.getLogger(__name__)


def _is_demo_source_type(source_type) -> bool:
    """#767: True iff source_type resolves to the explicit demo/sample adapter."""
    if not source_type or not isinstance(source_type, str):
        return False
    try:
        return bool(resolve_adapter_spec(source_type).is_demo)
    except DataFabricError:
        return False


def _cap_payload_for_context(res: dict, list_key: str, cap: int = 10) -> None:
    """E-3/E-11（#902）：LLM 上下文载荷上限保护。

    json.dumps 检查器自身失败（不可序列化对象等）时，旧行为 `except: pass`
    会让保护静默失效——超大 payload 原样进入上下文。失败分支改为保守裁剪
    + warning 日志（宁可误裁不可漏放）。
    """
    import json
    try:
        oversized = len(json.dumps(res)) > 40000
    except Exception as e:  # noqa: BLE001 检查失败 → 保守按超限处理
        logger.warning("[data_fabric] payload size check failed (%s); conservatively capping %s", e, list_key)
        oversized = True
    if oversized and isinstance(res.get(list_key), list):
        res[list_key] = res[list_key][:cap]
        res["_payload_notice"] = "Payload capped for context safety (>40,000 chars)."


def register_data_fabric_tools(registry: ToolRegistry):
    """
    Register the 7 unified Data Fabric AI tools into the ToolRegistry.
    """


    @tool(
        registry,
        tier=2, domains=["dataset"],
        name="connect_data_source",
        description=(
            "连接与注册地理空间数据源（PostGIS, OGC API, WFS, WMS, WMTS, ArcGIS, STAC, GeoParquet, PMTiles, S3 等；"
            "generic/mock/sample 为显式演示适配器，返回合成数据并以 is_demo 标注）。"
            "\n注意：未注册的类型（如 csv、geojson 远程 URL）会被拒绝并返回 UNSUPPORTED_SOURCE 错误——不会静默生成模拟数据。"
            "\n安全策略：自动执行 SSRF 拦截（禁止私有网段/loopback/元数据地址），对 url 与 host/port 连接路径均生效，并在返回结果中自动脱敏敏感凭据。"
            "\n返回：{status, connection_profile, health, datasets_count, is_demo}"
        ),
        param_descriptions={
            "profile_id": "数据源连接配置的唯一标识符（如 'pg-main', 'wfs-usgs'）",
            "source_type": "适配器协议类型 ('postgis', 'ogc_api', 'wfs', 'wms', 'wmts', 'arcgis', 'stac', 'geoparquet', 'flatgeobuf', 'pmtiles', 's3'; 演示: 'generic')",
            "url": "远程服务 API Endpoint URL（执行 SSRF 安全校验）",
            "host": "数据库主机地址（与 url 相同的 SSRF 安全校验）",
            "port": "数据库端口",
            "database": "数据库名称",
            "username": "认证用户名",
            "password": "认证密码",
            "options": "协议特定配置选项 key-value 字典",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
    )
    async def connect_data_source(
        profile_id: str,
        source_type: str,
        url: Optional[str] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        options: Optional[dict] = None,
    ) -> dict:
        """连接与注册数据源

        Security: the SSRF private-endpoint bypass (``allow_private``) is
        intentionally NOT exposed as a tool parameter — it was previously
        prompt-injectable. Private endpoints must be allow-listed server-side
        (mirrors the REST route in app/api/routes/data_fabric.py). See
        docs/research/deep-audit-performance-convergence.md SEC-01.

        Both ``url`` and host/port-only profiles are SSRF-validated in
        ``connection_manager.connect`` (#1107).
        """
        def _sync_run():
            profile = ConnectionProfile(
                id=profile_id,
                name=f"Connection {profile_id}",
                source_type=source_type,
                url=url,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                options=options or {},
                allow_private=False,
            )

            connected_profile, adapter = connection_manager.connect(profile)
            sanitized_profile = DataFabricSecurity.sanitize_profile_dict(connected_profile.model_dump())
            health = data_fabric_health_check.check_health(adapter)
            datasets = adapter.list_datasets()

            return {
                "status": "connected",
                "connection_profile": sanitized_profile,
                "health": health.model_dump(),
                "datasets_count": len(datasets),
                "discovered_datasets": [d.get("id") for d in datasets],
                # #767: demo/sample adapters serve synthetic features — label
                # the connection so the data is never mistaken for remote data.
                "is_demo": _is_demo_source_type(source_type),
            }

        return await asyncio.to_thread(_sync_run)

    @tool(
        registry,
        tier=2, domains=["dataset"],
      name="inspect_data_source",
        description=(
            "检查已连接数据源的诊断健康状态、协议能力标识（ capabilities ）以及可用的数据集/图层列表。"
            "\n返回：{status, profile_id, health, capabilities, datasets}"
        ),
        param_descriptions={
            "profile_id": "要检查的数据源连接 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC)
    async def inspect_data_source(profile_id: str) -> dict:
        """检查数据源健康度与能力清单"""
        def _sync_run():
            adapter = connection_manager.get_adapter(profile_id)
            if not adapter:
                profile = connection_manager.get_profile(profile_id)
                if not profile:
                    raise RuntimeError(f"Data source connection profile '{profile_id}' not found. Please connect first.")
                # Build the profile's real adapter via the canonical registry.
                # An unregistered source type raises UnsupportedSourceError rather
                # than silently producing mock data.
                adapter = build_adapter(profile)

            health = data_fabric_health_check.check_health(adapter)
            caps = adapter.capabilities()
            datasets = adapter.list_datasets()

            res = {
                "status": "inspected",
                "profile_id": profile_id,
                "health": health.model_dump(),
                "capabilities": caps,
                "datasets_count": len(datasets),
                "datasets": datasets,
            }
            _cap_payload_for_context(res, "datasets")
            return res

        return await asyncio.to_thread(_sync_run)

    @tool(
        registry,
        tier=2, domains=["dataset"],
      name="search_spatial_catalog",
        description=(
            "在 SpatialCatalog 中检索数据集。支持关键字、空间包围盒 (bbox)、空间参考系 (CRS/SRS)、标签 (tags) 和数据源类型综合过滤。"
            "\n返回：{total, items=[{id, title, description, geometry_type, srs, bbox, tags, ...}], limit, offset}"
        ),
        param_descriptions={
            "query": "关键字检索词（匹配 ID、标题、描述或标签）",
            "bbox": "空间包围盒 [minx, miny, maxx, maxy]",
            "crs": "空间参考系代码（如 'EPSG:4326' 或 '3857'）",
            "tags": "标签列表过滤",
            "source_type": "数据源协议类型 ('postgis', 'wfs', 'geojson' 等)",
            "limit": "返回最大数量限制，默认 50",
            "offset": "分页偏移量，默认 0",
        },
        execution_policy=ToolExecutionPolicy.INLINE)
    def search_spatial_catalog(
        query: Optional[str] = None,
        bbox: Optional[list[float]] = None,
        crs: Optional[str] = None,
        tags: Optional[list[str]] = None,
        source_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """空间目录综合检索"""
        res = spatial_catalog_service.search(
            query=query,
            bbox=bbox,
            crs=crs,
            tags=tags,
            source_type=source_type,
            limit=limit,
            offset=offset,
        )
        _cap_payload_for_context(res, "items")
        return res

    @tool(
        registry,
        tier=2, domains=["dataset"],
        name="describe_dataset",
        description=(
            "获取指定数据集的完整 DatasetDescriptor 属性元数据契约（Schema 字段、几何类型、SRS、FeatureCount、Extent 范围），"
            "并计算确定性 DatasetFingerprint 校验哈希。"
            "\n返回：{dataset_descriptor, fingerprint, profile_id}"
        ),
        param_descriptions={
            "dataset_id": "要描述的数据集/图层唯一 ID",
            "profile_id": "可选的数据源连接 profile_id",
        },
        # audit #826: catalog-miss falls back to adapter.describe() which does
        # SYNCHRONOUS network I/O (WFS GetCapabilities up to 10-15s) — that is
        # a THREAD contract, not INLINE (<5ms event-loop budget).
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    def describe_dataset(dataset_id: str, profile_id: Optional[str] = None) -> dict:
        """获取数据集 Schema 描述与 Fingerprint"""
        desc = spatial_catalog_service.get_dataset(dataset_id)
        pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id)

        if not desc and pid:
            adapter = connection_manager.get_adapter(pid)
            if adapter:
                desc = adapter.describe(dataset_id)

        if not desc:
            # #768: an unknown dataset id is a typed error — NEVER a fabricated
            # worldwide Polygon/EPSG:4326 descriptor with a deterministic
            # fingerprint (the agent would plan queries against metadata that
            # does not exist).
            return {
                "status": "error",
                "error_type": DATASET_NOT_FOUND,
                "error": (
                    f"Dataset '{dataset_id}' not found in the spatial catalog "
                    f"and no connected adapter can describe it"
                ),
                "dataset_id": dataset_id,
                "profile_id": pid,
            }

        fingerprint = dataset_fingerprint_service.calculate_descriptor_fingerprint(desc)

        return {
            "dataset_id": dataset_id,
            "descriptor": desc.model_dump(),
            "fingerprint": fingerprint,
            "profile_id": pid,
        }

    @tool(
        registry,
        tier=2, domains=["dataset"],
      name="query_dataset",
        description=(
            "针对数据集执行 QuerySpec 下推查询（支持 limit, offset, bbox 空间裁剪, where 属性过滤, fields 投影与 SRS 转换）。"
            "\n返回：{dataset_id, features, total_count, schema_info, metadata}"
        ),
        param_descriptions={
            "dataset_id": "目标数据集/图层 ID",
            "limit": "最大获取要素条数，默认 100",
            "offset": "分页偏移量，默认 0",
            "bbox": "空间裁剪包围盒 [minx, miny, maxx, maxy]",
            "where": "属性过滤表达式（如 \"type = 'commercial'\"）",
            "fields": "需要选择/投影的字段列表",
            "srs": "输出目标空间参考系，默认 'EPSG:4326'",
            "profile_id": "可选的数据源 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC)
    async def query_dataset(
        dataset_id: str,
        limit: int = 100,
        offset: int = 0,
        bbox: Optional[list[float]] = None,
        where: Optional[str] = None,
        fields: Optional[list[str]] = None,
        srs: Optional[str] = "EPSG:4326",
        profile_id: Optional[str] = None,
    ) -> dict:
        """执行 QuerySpec 下推查询"""
        def _sync_run():
            pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id)
            adapter = connection_manager.get_adapter(pid) if pid else None

            if not adapter:
                # Do NOT fabricate a geojson mock adapter — that would serve
                # synthetic features as if they were real remote data. Return a
                # typed, actionable error so the agent connects a source first.
                return {
                    "status": "error",
                    "error_type": UNSUPPORTED_SOURCE,
                    "error": (
                        f"No connected data source adapter for dataset '{dataset_id}' "
                        f"(profile_id={pid}). Connect a data source first."
                    ),
                    "dataset_id": dataset_id,
                    "features": [],
                }

            spec = QuerySpec(
                limit=limit,
                offset=offset,
                bbox=bbox,
                where=where,
                fields=fields,
                srs=srs or "EPSG:4326",
            )

            try:
                query_result = materialization_service.execute_query(adapter, dataset_id, spec)
            except DataFabricError as e:
                # #766: the remote fetch failed — typed error, never a silent
                # empty-but-successful feature list.
                return {
                    "status": "error",
                    "error_type": e.code,
                    "error": str(e),
                    "dataset_id": dataset_id,
                    "features": [],
                }
            result_dict = query_result.model_dump()
            result_dict["is_demo"] = _is_demo_source_type(
                getattr(adapter.profile, "source_type", None)
            )
            features = result_dict.get("features") or []
            if isinstance(features, list):
                from app.tools._utils import _feature_collection_bbox
                fc = {"type": "FeatureCollection", "features": features}
                result_dict["features"] = {
                    "feature_count": len(features),
                    "bbox": _feature_collection_bbox(fc),
                    "note": (
                        "Features omitted from tool_result (Fetch-on-Demand). Use "
                        "materialize_dataset to obtain a ref_id cursor for the rows."
                    ),
                }
            return result_dict

        return await asyncio.to_thread(_sync_run)

    @tool(
        registry,
        tier=2, domains=["dataset"], name="materialize_dataset",
        description=(
            "执行下推查询并将远程数据物理物化（Materialize）到本地 Session 存储，生成 unique ref_id 游标供后续 GIS 工具分析。"
            "\n返回：{status, ref_id, dataset_id, layer_name, feature_count, fingerprint}"
        ),
        param_descriptions={
            "dataset_id": "目标数据集/图层 ID",
            "session_id": "用户会话 ID，默认 'default'",
            "layer_name": "本地物化图层的显示名称",
            "limit": "物化要素数量限制，默认 100",
            "offset": "分页偏移量",
            "bbox": "空间裁剪包围盒 [minx, miny, maxx, maxy]",
            "where": "属性过滤表达式",
            "profile_id": "可选的数据源 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
    )
    async def materialize_dataset(
        dataset_id: str,
        session_id: str = "default",
        layer_name: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        bbox: Optional[list[float]] = None,
        where: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> dict:
        """数据下推查询与本地物化流水线 (生成 ref_id)"""
        pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id)
        adapter = connection_manager.get_adapter(pid) if pid else None

        if not adapter:
            # Do NOT fabricate a geojson mock adapter and materialize synthetic
            # features as real data. Return a typed error so the agent connects
            # a source first.
            return {
                "status": "error",
                "error_type": UNSUPPORTED_SOURCE,
                "error": (
                    f"No connected data source adapter for dataset '{dataset_id}' "
                    f"(profile_id={pid}). Connect a data source first."
                ),
                "dataset_id": dataset_id,
                "ref_id": None,
            }

        spec = QuerySpec(
            limit=limit,
            offset=offset,
            bbox=bbox,
            where=where,
        )

        return await materialization_service.materialize_dataset(
            adapter=adapter,
            dataset_id=dataset_id,
            query_spec=spec,
            session_id=session_id,
            layer_name=layer_name,
        )

    @tool(
        registry,
        tier=2, domains=["dataset"], name="refresh_data_source",
        description=(
            "刷新数据源元数据缓存、重新发现数据集/图层，重新触发健康度探测，并更新 SpatialCatalog 索引。"
            "\n返回：{status, profile_id, sync_details, health}"
        ),
        param_descriptions={
            "profile_id": "要刷新的数据源 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
    )
    async def refresh_data_source(profile_id: str) -> dict:
        """刷新数据源缓存与 Catalog 索引"""
        def _sync_run():
            adapter = connection_manager.get_adapter(profile_id)
            if not adapter:
                raise RuntimeError(f"Connection profile '{profile_id}' not found. Cannot refresh.")

            sync_details = adapter.sync()
            health = data_fabric_health_check.check_health(adapter)

            return {
                "status": "refreshed",
                "profile_id": profile_id,
                "sync_details": sync_details,
                "health": health.model_dump(),
            }

        return await asyncio.to_thread(_sync_run)
