"""
Geospatial Data Fabric: Unified AI Tools
7 Unified AI Tools for managing, inspecting, catalog searching, querying, materializing,
and health-monitoring Data Fabric geospatial data sources.
"""
import asyncio
import logging
import math
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
from app.services.data_fabric.registry import resolve_adapter_spec
from app.services.data_fabric.errors import (
    DATASET_NOT_FOUND,
    SOURCE_UNREACHABLE,
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


def _resolve_source_adapter(profile_id, session_id):
    """V8（ADR-0130）：工具层数据源解析的**单一入口**。

    顺序：ConnectionRegistry（治理：作用域/revision/健康/secret 分离）→
    legacy 会话（首次命中即注册进 registry，治理视图统一）→ None。
    此前全部 11 处调用点直接走 ``connection_manager.get_adapter`` —— 治理
    元数据对生产流量不可见（ADR-0120 披露的接线缺口）。Registry 层故障
    fail-open 回退 legacy 语义，工具错误契约（None → UNSUPPORTED_SOURCE）
    不变。
    """
    from app.services.data_fabric.fabric.runtime import get_fabric_runtime

    try:
        resolved = get_fabric_runtime().resolve(profile_id, owner=session_id)
    except DataFabricError:
        resolved = None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[data_fabric] runtime resolve failed for %s: %s", profile_id, exc)
        resolved = None
    return resolved.adapter if resolved is not None else None


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
        anti_examples=("上传新文件注册数据（用 ingest_dataset）",),
        capabilities=['data_source_pipeline'],
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
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=["数据源", "连接", "connection", "postgis", "数据接入", "ssrf"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=["network_error", "timeout", "invalid_args"],
    )
    async def connect_data_source(
        profile_id: str,
        source_type: str,
        session_id: Optional[str] = None,
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

            connected_profile, adapter = connection_manager.connect(profile, owner=session_id)
            # V8（ADR-0130）：连接即治理 —— 同一 adapter 实例注册进
            # ConnectionRegistry（secret 摘离、revision、生命周期归属 registry；
            # legacy 会话存储原样保留）。best-effort：治理注册失败不阻断连接。
            try:
                from app.services.data_fabric.fabric.connection_registry import (
                    TenantScope,
                    get_connection_registry,
                )

                get_connection_registry().attach(
                    connected_profile,
                    TenantScope(owner=session_id),
                    build_adapter=False,
                    prebuilt_adapter=adapter,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[data_fabric] registry attach skipped for %s: %s", profile_id, exc)
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
      capabilities=['data_source_pipeline'],
        description=(
            "检查已连接数据源的诊断健康状态、协议能力标识（ capabilities ）以及可用的数据集/图层列表。"
            "\n返回：{status, profile_id, health, capabilities, datasets}"
        ),
        param_descriptions={
            "profile_id": "要检查的数据源连接 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="cacheable_read",
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="medium",
        tags=["数据源", "诊断", "健康检查", "capabilities", "数据集列表", "inspect"],
        output_semantic_type="list",
        result_size_policy="bounded",
        required_context=["data_profile"],
        failure_modes=["network_error", "timeout", "missing_data"],
    )
    async def inspect_data_source(profile_id: str, session_id: Optional[str] = None) -> dict:
        """检查数据源健康度与能力清单"""
        def _sync_run():
            # V8：registry 优先的统一解析（含 legacy 会话回退与 profile-only
            # 补建语义 —— 补建的 adapter 会被注册进 registry 成为受治理条目）。
            adapter = _resolve_source_adapter(profile_id, session_id)
            if not adapter:
                raise RuntimeError(f"Data source connection profile '{profile_id}' not found. Please connect first.")

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
      capabilities=['data_source_pipeline'],
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
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="pure",
        network=False,
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="medium",
        tags=["目录", "检索", "catalog", "搜索", "数据集", "bbox", "标签"],
        output_semantic_type="list",
        result_size_policy="bounded",
        failure_modes=["empty_result"],
    )
    def search_spatial_catalog(
        session_id: Optional[str] = None,
        query: Optional[str] = None,
        bbox: Optional[list[float]] = None,
        crs: Optional[str] = None,
        tags: Optional[list[str]] = None,
        source_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """空间目录综合检索（owner 作用域，R3-M5）"""
        res = spatial_catalog_service.search(
            owner=session_id,
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
        capabilities=['data_source_pipeline'],
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
        side_effect="cacheable_read",
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=["数据集", "元数据", "schema", "describe", "指纹", "fingerprint"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=["network_error", "timeout", "missing_data"],
    )
    def describe_dataset(dataset_id: str, profile_id: Optional[str] = None, session_id: Optional[str] = None) -> dict:
        """获取数据集 Schema 描述与 Fingerprint"""
        desc = spatial_catalog_service.get_dataset(dataset_id, owner=session_id)
        pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id, owner=session_id)

        if not desc and pid:
            adapter = _resolve_source_adapter(pid, session_id)
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
      capabilities=['data_source_pipeline'],
        description=(
            "针对数据集执行 QuerySpec V2 下推查询（limit/offset/cursor 分页、bbox 空间裁剪、where 属性过滤、"
            "fields 投影、SRS 输出、聚合统计、确定性采样）。"
            "\n返回：{dataset_id, features(stub), total_count, total_matching, next_cursor, has_more, "
            "result_mode, metadata.query_plan, metadata.query_evidence}"
        ),
        param_descriptions={
            "dataset_id": "目标数据集/图层 ID",
            "limit": "最大获取要素条数，默认 100",
            "offset": "分页偏移量，默认 0（深分页建议用 cursor）",
            "bbox": "空间裁剪包围盒 [minx, miny, maxx, maxy]",
            "where": "属性过滤表达式（如 \"type = 'commercial' AND students > 500\"）",
            "fields": "需要选择/投影的字段列表",
            "srs": "输出目标空间参考系，默认 'EPSG:4326'",
            "aggregate": "聚合规格列表，如 [{func: count}] 或 [{func: sum, field: students}]（触发 STATISTICS 模式，零几何传输）",
            "group_by": "分组字段列表（配合 aggregate，如按区县统计）",
            "order_by": "排序规格，如 ['students desc']",
            "result_mode": "结果模式：features(默认) | statistics | sample | descriptor | vector_tile",
            "cursor": "上一页返回的 next_cursor 令牌（keyset 分页）",
            "sample_size": "sample 模式的采样大小（确定性，可复现）",
            "profile_id": "可选的数据源 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="cacheable_read",
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="large",
        tags=["查询", "query", "下推", "分页", "bbox", "过滤", "聚合"],
        output_semantic_type="stats",
        result_size_policy="bounded",
        required_context=["data_profile"],
        failure_modes=["network_error", "timeout", "missing_data"],
    )
    async def query_dataset(
        dataset_id: str,
        limit: int = 100,
        offset: int = 0,
        bbox: Optional[list[float]] = None,
        where: Optional[str] = None,
        fields: Optional[list[str]] = None,
        srs: Optional[str] = "EPSG:4326",
        aggregate: Optional[list[dict]] = None,
        group_by: Optional[list[str]] = None,
        order_by: Optional[list[str]] = None,
        result_mode: Optional[str] = None,
        cursor: Optional[str] = None,
        sample_size: Optional[int] = None,
        profile_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """执行 QuerySpec 下推查询"""
        def _sync_run():
            pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id, owner=session_id)
            adapter = _resolve_source_adapter(pid, session_id) if pid else None

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

            extras: dict = {"srs": srs or "EPSG:4326"}
            if aggregate:
                extras["aggregate"] = aggregate
            if group_by:
                extras["group_by"] = group_by
            if order_by:
                extras["order_by"] = order_by
            if result_mode:
                extras["result_mode"] = result_mode
            if sample_size:
                extras["sample_size"] = sample_size
            if cursor:
                extras["page_kind"] = "cursor"
                extras["cursor"] = cursor
            spec = QuerySpec(
                limit=limit,
                offset=offset,
                bbox=bbox,
                where=where,
                fields=fields,
                **extras,
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
            # R4-minor3：features 本就要被 stub 替换 —— 不序列化（省一次
            # 深拷贝 + 一次 bbox 全扫描；10k 特征时省百 ms 级）。
            features = query_result.features or []
            result_dict = query_result.model_dump(exclude={"features"})
            result_dict["is_demo"] = _is_demo_source_type(
                getattr(adapter.profile, "source_type", None)
            )
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
        capabilities=['dataset_ingest'],
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
        side_effect="state_mutation",
        data_mutations=["cache_write"],
        required_context=["data_profile"],
        produced_refs=["data"],
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=["物化", "materialize", "ref", "数据落地", "远程数据", "缓存"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        failure_modes=["network_error", "timeout", "missing_data"],
    )
    async def materialize_dataset(
        dataset_id: str,
        session_id: str = "default",
        layer_name: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        bbox: Optional[list[float]] = None,
        where: Optional[str] = None,
        fields: Optional[list[str]] = None,
        result_mode: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> dict:
        """数据下推查询与本地物化流水线 (生成 ref_id)"""
        pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id, owner=session_id)
        adapter = _resolve_source_adapter(pid, session_id) if pid else None

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

        extras: dict = {}
        if fields:
            extras["fields"] = fields
        if result_mode:
            extras["result_mode"] = result_mode
        spec = QuerySpec(
            limit=limit,
            offset=offset,
            bbox=bbox,
            where=where,
            **extras,
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
        capabilities=['data_source_pipeline'],
        description=(
            "刷新数据源元数据缓存、重新发现数据集/图层，重新触发健康度探测，并更新 SpatialCatalog 索引。"
            "\n返回：{status, profile_id, sync_details, health}"
        ),
        param_descriptions={
            "profile_id": "要刷新的数据源 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="cacheable_read",
        data_mutations=["cache_write"],
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=["刷新", "refresh", "重新发现", "缓存", "健康检查", "同步"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        required_context=["data_profile"],
        failure_modes=["network_error", "timeout", "missing_data"],
    )
    async def refresh_data_source(profile_id: str, session_id: Optional[str] = None) -> dict:
        """刷新数据源缓存与 Catalog 索引"""
        def _sync_run():
            adapter = _resolve_source_adapter(profile_id, session_id)
            if not adapter:
                raise RuntimeError(f"Connection profile '{profile_id}' not found. Cannot refresh.")

            sync_details = adapter.sync(owner=session_id)
            health = data_fabric_health_check.check_health(adapter)

            return {
                "status": "refreshed",
                "profile_id": profile_id,
                "sync_details": sync_details,
                "health": health.model_dump(),
            }

        return await asyncio.to_thread(_sync_run)

    # ── V2 高价值工具（ADR-0094 §11）：explain / aggregate / federated ──────

    @tool(
        registry,
        tier=2, domains=["dataset"], name="plan_data_query",
        capabilities=['data_source_pipeline'],
        description=(
            "dry-run 查询计划（explain）：不执行查询，返回 pushdown 划分、行数估算、"
            "分页策略、结果模式与警告——用于判断'为什么快/为什么慢/为什么只采样'。"
            "\n返回：{status, explain(lines), plan, capabilities, dataset}"
        ),
        param_descriptions={
            "dataset_id": "目标数据集 ID",
            "bbox": "可选空间裁剪 [minx, miny, maxx, maxy]",
            "where": "可选属性过滤表达式",
            "limit": "页大小（影响分页策略评估）",
            "result_mode": "可选结果模式（features/statistics/sample/...）",
            "profile_id": "可选数据源 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="cacheable_read",
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=["查询计划", "explain", "dry-run", "性能", "预算", "planner"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        required_context=["data_profile"],
        failure_modes=["missing_data", "invalid_args"],
    )
    async def plan_data_query(
        dataset_id: str,
        bbox: Optional[list[float]] = None,
        where: Optional[str] = None,
        limit: int = 100,
        result_mode: Optional[str] = None,
        profile_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """explain（dry-run）"""
        def _sync_run():
            pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id, owner=session_id)
            adapter = _resolve_source_adapter(pid, session_id) if pid else None
            if not adapter:
                return {
                    "status": "error",
                    "error_type": UNSUPPORTED_SOURCE,
                    "error": f"No connected data source adapter for dataset '{dataset_id}'.",
                }
            from app.services.data_fabric.query.capabilities import (
                AdapterCapabilitiesV2,
                get_capabilities,
            )
            from app.services.data_fabric.query.normalize import normalize_query_spec
            from app.services.data_fabric.query.planner import plan_query

            extras = {"srs": "EPSG:4326"}
            if result_mode:
                extras["result_mode"] = result_mode
            spec = QuerySpec(limit=limit, bbox=bbox, where=where, **extras)
            try:
                descriptor = adapter.describe(dataset_id)
            except DataFabricError as e:
                return {"status": "error", "error_type": e.code, "error": str(e)}
            except Exception as e:
                return {"status": "error", "error_type": SOURCE_UNREACHABLE, "error": str(e)}
            fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(descriptor)
            try:
                v2 = normalize_query_spec(spec)
                caps = None
                probe = getattr(adapter, "capabilities_v2", None)
                if probe is not None:
                    try:
                        caps = probe(descriptor)
                    except Exception:
                        caps = None
                if not isinstance(caps, AdapterCapabilitiesV2):
                    caps = getattr(adapter, "_caps", None)
                if not isinstance(caps, AdapterCapabilitiesV2):
                    caps = get_capabilities(
                        getattr(getattr(adapter, "profile", None), "source_type", None) or "generic"
                    )
                plan = plan_query(v2, descriptor, caps, source_id=str(pid), dataset_fingerprint=fp)
            except DataFabricError as e:
                return {"status": "error", "error_type": e.code, "error": str(e),
                        "details": e.details}
            return {
                "status": "success",
                "dataset_id": dataset_id,
                "dataset_fingerprint": fp,
                "explain": plan.summary_lines(),
                "plan": plan.model_dump(),
                "capabilities": caps.model_dump(),
            }

        return await asyncio.to_thread(_sync_run)

    @tool(
        registry,
        tier=2, domains=["dataset"], name="aggregate_dataset",
        capabilities=['data_source_pipeline'],
        description=(
            "数据集聚合统计（STATISTICS 模式，零几何传输）：count/sum/avg/min/max/stddev/"
            "distinct_count，支持 group_by。支持聚合下推的源（如 PostGIS）在服务器执行，"
            "只返回聚合结果——百万行也只传几十行统计。"
            "\n返回：{status, rows, result_mode:'statistics', query_evidence}"
        ),
        param_descriptions={
            "dataset_id": "目标数据集 ID",
            "aggregate": "聚合规格列表 [{func: 'count'} | {func: 'sum', field: 'students'}]",
            "group_by": "可选分组字段（如 ['district'] → 每区县统计）",
            "bbox": "可选空间裁剪",
            "where": "可选属性过滤",
            "profile_id": "可选数据源 profile_id",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="cacheable_read",
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="large",
        tags=["聚合", "统计", "aggregate", "分组统计", "count", "sum", "下推"],
        output_semantic_type="stats",
        result_size_policy="bounded",
        required_context=["data_profile"],
        failure_modes=["network_error", "timeout", "missing_data"],
    )
    async def aggregate_dataset(
        dataset_id: str,
        aggregate: list[dict],
        group_by: Optional[list[str]] = None,
        bbox: Optional[list[float]] = None,
        where: Optional[str] = None,
        profile_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """聚合统计（STATISTICS 结果模式）"""
        def _sync_run():
            pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id, owner=session_id)
            adapter = _resolve_source_adapter(pid, session_id) if pid else None
            if not adapter:
                return {
                    "status": "error", "error_type": UNSUPPORTED_SOURCE,
                    "error": f"No connected data source adapter for dataset '{dataset_id}'.",
                }
            extras: dict = {"aggregate": aggregate, "srs": "EPSG:4326"}
            if group_by:
                extras["group_by"] = group_by
            spec = QuerySpec(limit=1000, bbox=bbox, where=where, **extras)
            try:
                result = materialization_service.execute_query(adapter, dataset_id, spec)
            except DataFabricError as e:
                return {"status": "error", "error_type": e.code, "error": str(e)}
            evidence = result.metadata.get("query_evidence") or {}
            return {
                "status": "success",
                "dataset_id": dataset_id,
                "rows": result.data or [],
                "row_count": len(result.data or []),
                "result_mode": "statistics",
                "pushdown": bool(result.metadata.get("query_plan", {}).get("pushed_aggregation")),
                "query_evidence": evidence,
                "is_demo": bool(getattr(result, "is_demo", False)),
            }

        return await asyncio.to_thread(_sync_run)

    @tool(
        registry,
        tier=2, domains=["dataset"], name="query_federated_data",
        capabilities=['federated_dataset_query'],
        description=(
            "受控两源联邦查询（有硬预算）：属性等值连接 / 点面空间连接 / 聚合+连接。"
            "同源 PostGIS 自动优先 server-side join；跨源用 STRtree 本地连接。"
            "超预算返回 QUERY_BUDGET_EXCEEDED 与缩减建议——绝不拉全量大表。"
            "\n返回：{status, rows, row_count, plan, strategy, pushdown_ratio}"
        ),
        param_descriptions={
            "left_dataset_id": "左侧（事实/点）数据集 ID",
            "right_dataset_id": "右侧（维表/多边形）数据集 ID",
            "join_field_left": "属性连接：左侧字段",
            "join_field_right": "属性连接：右侧字段",
            "spatial_op": "空间连接操作：'within'（点在面内）| 'intersects'；与 join_field 二选一",
            "group_by_right": "右侧分组字段（聚合+连接，如 ['district_name']）",
            "aggregates": "聚合规格 [{func:'count'}]（需 group_by_right）",
            "bbox": "可选空间裁剪（应用到两侧）",
            "where_left": "可选左侧属性过滤",
            "where_right": "可选右侧属性过滤",
            "left_profile_id": "可选左侧数据源 ID",
            "right_profile_id": "可选右侧数据源 ID",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="cacheable_read",
        network=True,
        deterministic=False,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=["联邦查询", "join", "连接", "空间连接", "两源", "federated"],
        output_semantic_type="table",
        result_size_policy="bounded",
        required_context=["data_profile"],
        failure_modes=["network_error", "timeout", "partial_coverage"],
    )
    async def query_federated_data(
        left_dataset_id: str,
        right_dataset_id: str,
        join_field_left: Optional[str] = None,
        join_field_right: Optional[str] = None,
        spatial_op: Optional[str] = None,
        group_by_right: Optional[list[str]] = None,
        aggregates: Optional[list[dict]] = None,
        bbox: Optional[list[float]] = None,
        where_left: Optional[str] = None,
        where_right: Optional[str] = None,
        left_profile_id: Optional[str] = None,
        right_profile_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """受控联邦查询"""
        from app.services.data_fabric.query.federation import (
            FederatedExecutor,
            FederatedQueryRequest,
        )

        def _adapter_of(dataset_id: str, profile_id: Optional[str]):
            pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id, owner=session_id)
            return (pid, _resolve_source_adapter(pid, session_id)) if pid else (pid, None)

        def _sync_run():
            lp, left_adapter = _adapter_of(left_dataset_id, left_profile_id)
            rp, right_adapter = _adapter_of(right_dataset_id, right_profile_id)
            if not left_adapter or not right_adapter:
                missing = left_dataset_id if not left_adapter else right_dataset_id
                return {
                    "status": "error",
                    "error_type": UNSUPPORTED_SOURCE,
                    "error": f"No connected data source adapter for dataset '{missing}'.",
                }
            req = FederatedQueryRequest(
                left_source_id=lp or "left",
                left_dataset_id=left_dataset_id,
                right_source_id=rp or "right",
                right_dataset_id=right_dataset_id,
                join_field_left=join_field_left,
                join_field_right=join_field_right,
                spatial_op=spatial_op,
                group_by_right=group_by_right,
                aggregates=aggregates,
                bbox=bbox,
                left_where=where_left,
                right_where=where_right,
            )
            executor = FederatedExecutor(
                lambda sid: {lp: left_adapter, rp: right_adapter}.get(sid)
            )
            try:
                return executor.execute(req)
            except DataFabricError as e:
                return {"status": "error", "error_type": e.code, "error": str(e),
                        "details": e.details}

        return await asyncio.to_thread(_sync_run)

    @tool(
        registry,
        tier=2, domains=["dataset"], name="query_federated_chain",
        capabilities=["federated_dataset_query"],
        description=(
            "N 源（2..4）有界左深链式联邦查询：属性连接 / 点面空间连接 / 聚合+连接逐跳串联。"
            "成本排序（estimated_rows 提示，可选 stats 提示的有界枚举）、最小投影自动派生、"
            "半连接右表约减、逐跳预算 fail-fast——绝不拉全量大表。"
            "\n返回：{status, rows(有界内联), row_count, order, strategy, explain(lines), plans, "
            "per_source_rows, semi_join_reduction}"
        ),
        param_descriptions={
            "sources": (
                "链上数据集列表（2..4 个），每项 {dataset_id(必填), source_id(可选,默认 s{i}), "
                "profile_id(可选), where(可选属性过滤), fields(可选投影), "
                "estimated_rows(可选行数提示,驱动成本排序), srs(可选,混用 CRS 会计划期拒绝)}"
            ),
            "joins": (
                "连接列表（恰好 len(sources)-1 个），每项 {kind: attribute_join|spatial_join|"
                "aggregate_join, join_field_left, join_field_right, spatial_op: within|intersects, "
                "group_by_right, aggregates: [{func, field}], left_source_id, right_source_id "
                "(可选 id 寻址,建议声明以启用成本枚举)}"
            ),
            "bbox": "可选空间裁剪（应用到所有源）",
            "limit": "最终行数上限，默认 10000（超预算返回 QUERY_BUDGET_EXCEEDED）",
            "order_strategy": "链序策略：cost(默认,按 estimated_rows 提示) | given | cost_stats(需统计提示)",
            "derive_projection": (
                "是否自动派生每源最小投影（默认 true；空间跳端点永不被裁剪；"
                "where 为不可解析的自由字符串的源被排除在派生外 —— 该源按"
                "全列取数，只是多取，安全）"
            ),
            "engine": (
                "执行引擎：v6（默认，cost-based join 树枚举/流式批执行/Bloom 预滤/"
                "执行期基数观测，非 typed 异常自动回退 v5）| v5（左深链基线）。"
                "自适应尾重排仅在 join graph 存在替代有向链时触发"
            ),
            "use_cache": (
                "V7 结果缓存开关（默认开）：命中时结果带 result_cache 披露段"
                "（age/basis）；按源数据指纹 + TTL 失效；全局源不缓存"
            ),
            "session_id": "用户会话 ID",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="cacheable_read",
        network=True,
        deterministic=False,
        latency_class="slow",
        memory_class="heavy",
        scale_class="large",
        tags=["联邦查询", "链式", "多源", "join", "连接", "federated", "chain"],
        output_semantic_type="table",
        result_size_policy="bounded",
        required_context=["data_profile"],
        failure_modes=["network_error", "timeout", "partial_coverage"],
    )
    async def query_federated_chain(
        sources: list[dict],
        joins: list[dict],
        bbox: Optional[list[float]] = None,
        limit: int = 10_000,
        order_strategy: str = "cost",
        derive_projection: bool = True,
        engine: str = "v6",
        use_cache: bool = True,
        session_id: Optional[str] = None,
    ) -> dict:
        """N 源有界链式联邦查询（V6：cost-based 枚举 + 流式批执行；
        V5 路径保留为回退/对照）。"""
        from app.services.data_fabric.query.federation import (
            ChainJoin,
            ChainSource,
            FederatedChainRequest,
            FederatedExecutor,
            chain_explain_lines,
        )

        #: 工具内联行上限（上下文载荷保护；row_count 仍报告真实总量）。
        CHAIN_TOOL_ROW_CAP = 200

        def _adapter_of(dataset_id: str, profile_id: Optional[str]):
            pid = profile_id or spatial_catalog_service.get_profile_id(dataset_id, owner=session_id)
            return (pid, _resolve_source_adapter(pid, session_id)) if pid else (pid, None)

        def _sync_run():
            if not isinstance(sources, list) or not isinstance(joins, list):
                return {"status": "error", "error_type": "INVALID_QUERY",
                        "error": "sources and joins must be lists of objects"}
            chain_sources = []
            adapters_by_id: dict = {}
            for i, s in enumerate(sources):
                if not isinstance(s, dict) or not s.get("dataset_id"):
                    return {"status": "error", "error_type": "INVALID_QUERY",
                            "error": f"sources[{i}] must be an object with dataset_id"}
                sid = str(s.get("source_id") or f"s{i}")
                if sid in adapters_by_id:
                    return {"status": "error", "error_type": "INVALID_QUERY",
                            "error": f"duplicate source_id {sid!r} in chain"}
                pid, adapter = _adapter_of(str(s["dataset_id"]), s.get("profile_id"))
                if adapter is None:
                    return {
                        "status": "error",
                        "error_type": UNSUPPORTED_SOURCE,
                        "error": f"No connected data source adapter for dataset '{s['dataset_id']}'.",
                    }
                est = s.get("estimated_rows")
                # m3（审计 round1）：NaN/inf 估算不是合法的成本提示 ——
                # int(nan) raises ValueError / int(inf) raises OverflowError
                # 会令整个工具崩溃 → 如实降级为 None（无提示，排序回退）。
                est_ok = (
                    isinstance(est, (int, float))
                    and float(est) == float(est)  # NaN 检验
                    and math.isfinite(float(est))
                )
                chain_sources.append(ChainSource(
                    source_id=sid,
                    dataset_id=str(s["dataset_id"]),
                    where=s.get("where"),
                    fields=list(s["fields"]) if s.get("fields") else None,
                    estimated_rows=int(est) if est_ok else None,
                    srs=s.get("srs"),
                ))
                adapters_by_id[sid] = adapter
            chain_joins = []
            for i, j in enumerate(joins):
                if not isinstance(j, dict):
                    return {"status": "error", "error_type": "INVALID_QUERY",
                            "error": f"joins[{i}] must be an object"}
                try:
                    chain_joins.append(ChainJoin(
                        kind=str(j.get("kind", "attribute_join")),
                        join_field_left=j.get("join_field_left"),
                        join_field_right=j.get("join_field_right"),
                        spatial_op=j.get("spatial_op"),
                        group_by_right=list(j["group_by_right"]) if j.get("group_by_right") else None,
                        aggregates=list(j["aggregates"]) if j.get("aggregates") else None,
                        left_source_id=j.get("left_source_id"),
                        right_source_id=j.get("right_source_id"),
                    ))
                except (TypeError, ValueError) as e:
                    return {"status": "error", "error_type": "INVALID_QUERY",
                            "error": f"joins[{i}] invalid: {e}"}

            engine_norm = str(engine or "").strip().lower()
            if engine_norm not in ("v5", "v6"):
                return {"status": "error", "error_type": "INVALID_QUERY",
                        "error": f"engine must be 'v5' or 'v6' (got {engine!r})"}
            req = FederatedChainRequest(
                sources=chain_sources,
                joins=chain_joins,
                bbox=bbox,
                limit=limit,
                order_strategy=order_strategy,
                derive_projection=derive_projection,
                engine=engine_norm,
                session_owner=session_id,
                use_cache=bool(use_cache),
            )
            executor = FederatedExecutor(lambda src: adapters_by_id.get(src))
            try:
                result = executor.execute_chain(req)
            except DataFabricError as e:
                return {"status": "error", "error_type": e.code, "error": str(e),
                        "details": e.details}
            rows = result.get("rows") or []
            out = {
                "status": result.get("status"),
                "row_count": result.get("row_count"),
                "rows": rows[:CHAIN_TOOL_ROW_CAP],
                "order": result.get("order"),
                "strategy": result.get("strategy"),
                "explain": chain_explain_lines(result),
                "plans": result.get("plans"),
                "per_source_rows": result.get("per_source_rows"),
                "warnings": result.get("warnings"),
            }
            if result.get("semi_join_reduction"):
                out["semi_join_reduction"] = result["semi_join_reduction"]
            out["engine"] = result.get("engine", "v5")
            if result.get("explain_v6"):
                out["explain_v6"] = result["explain_v6"]
            # V7（ADR-0119）fabric 证据 additive
            if result.get("fabric"):
                out["fabric"] = result["fabric"]
            if result.get("result_cache"):
                out["result_cache"] = result["result_cache"]
            if result.get("bloom_reduction"):
                out["bloom_reduction"] = result["bloom_reduction"]
            if result.get("replans_used") is not None:
                out["replans_used"] = result["replans_used"]
            if len(rows) > CHAIN_TOOL_ROW_CAP:
                out["_payload_notice"] = (
                    f"rows capped for context safety ({len(rows)} total; use row_count / "
                    "aggregate / narrower filters for the rest)."
                )
            return out

        return await asyncio.to_thread(_sync_run)
