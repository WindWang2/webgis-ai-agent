"""GOAL-02 / M5：OGC 开放服务与远程资产的 AI 工具面。

四个工具（全部经 ToolRegistry 注册、ToolDispatchService 统一执行 ——
前端/agent 不直连第三方 URL）：

- ``inspect_data_service``：任意服务 URL 的只读发现 + 三态能力报告 +
  数据资格（G01 双轨）。≠既有 ``inspect_data_source``（后者检查**已连接**
  源；本工具对**任意 URL** 做发现/资格判断）。
- ``read_coverage_subset``：OGC Coverage 受控 subset 读取（M3 入口；
  reason code 封闭）。
- ``materialize_remote_raster_asset``：远程 COG 受控物化（M4 入口；幂等
  /预算/取消/脱敏）。
- ``add_tile_service_layer``：把已治理的 OGC Tiles tileset 桥接为
  MapSpec 图层（受控瓦片代理 URL —— 绝不把第三方 URL 直塞前端）。

命名查重（registry grep，2026-11-01）：inspect_data_source/
materialize_dataset/prepare_dataset 已存在且语义不同；本模块四个名字
全仓唯一。
"""
import logging
from typing import Any, Dict, List, Optional

from app.tools.registry import ToolRegistry, ToolExecutionPolicy, tool

logger = logging.getLogger(__name__)


def register_ogc_service_tools(registry: ToolRegistry) -> None:
    """注册 GOAL-02 工具面（data_access/inspection 归类见 categories.py）。"""

    # ── inspect_data_service ─────────────────────────────────────────
    @tool(
        registry,
        tier=2, domains=["dataset"], name="inspect_data_service",
        capabilities=["data_source_pipeline"],  # CapabilityRegistry 既有项（drift 门 BLOCKER 面）
        description=(
            "对任意 OGC 开放服务 URL 做只读发现与资格判断（不发探测性写请求）："
            "识别 OGC API - Tiles / Coverages（也可检查既有协议），返回三态能力"
            "（supported/unsupported/unknown + reason codes）、数据集 descriptor、"
            "以及数据资格（display_only 瓦片显示 / numeric_raster 可计算栅格 —— "
            "仅能显示的服务不能做栅格统计）。"
            "\n返回：{status, service_type, capability, datasets[], qualification}"
        ),
        param_descriptions={
            "url": "服务根 URL（如 https://example.org/ogc；SSRF 校验）",
            "service_type": "显式协议（'ogc_api_tiles'|'ogc_api_coverages'；缺省自动探测）",
            "session_id": "会话 ID（目录发现归属）",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="cacheable_read",
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=["服务发现", "OGC", "tiles", "coverage", "能力", "三态", "资格"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=["network_error", "timeout", "unsupported_service"],
    )
    async def inspect_data_service(
        url: str,
        session_id: str = "default",
        service_type: Optional[str] = None,
    ) -> dict:
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )
        from app.services.data_fabric.adapters.ogc_tiles_adapter import OGCTilesAdapter
        from app.services.data_fabric.service_qualification import (
            qualification_for_descriptor,
        )
        from app.services.data_fabric.errors import DataFabricError

        if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return {"status": "error", "error_type": "invalid_args",
                    "error": "url must be an http(s) service root"}

        candidates: List[str] = (
            [service_type] if service_type else ["ogc_api_tiles", "ogc_api_coverages"]
        )
        attempts: List[Dict[str, Any]] = []
        for st in candidates:
            profile = ConnectionProfile(
                id=f"inspect-{st}", source_type=st,
                endpoint_url=url, name=f"inspect {st}",
            )
            adapter = (
                OGCTilesAdapter(profile) if st == "ogc_api_tiles" else OGCCoveragesAdapter(profile)
            )
            try:
                if not adapter.probe():
                    attempts.append({"service_type": st, "probe": False})
                    continue
                datasets = adapter.list_datasets()
                descriptors = []
                for d in datasets[:10]:
                    try:
                        desc = adapter.describe(d["id"])
                        descriptors.append({
                            "id": desc.id,
                            "title": desc.title,
                            "srs": desc.srs,
                            "bbox": desc.bbox,
                            "metadata": _bounded_metadata(desc.metadata),
                            "qualification": qualification_for_descriptor(desc),
                        })
                    except DataFabricError as e:
                        descriptors.append({"id": d.get("id"), "describe_error": str(e)[:200]})
                return {
                    "status": "success",
                    "service_type": st,
                    "capability": {
                        "declares_conformance": (
                            {"tiles": adapter._declares_tiles()}
                            if st == "ogc_api_tiles"
                            else {"coverages": adapter._declares_coverages()}
                        ),
                    },
                    "datasets": descriptors,
                    "probes": attempts + [{"service_type": st, "probe": True}],
                    "url_redacted": _redact(url),
                }
            except DataFabricError as e:
                attempts.append({"service_type": st, "error": str(e)[:200]})
                continue
            except Exception as e:  # noqa: BLE001
                attempts.append({"service_type": st, "error": str(e)[:200]})
                continue
        return {
            "status": "unreachable_or_unsupported",
            "url_redacted": _redact(url),
            "probes": attempts,
            "hint": "服务不可达或不声明 Tiles/Coverages 能力（诚实三态：不做猜测性连接）",
        }

    # ── read_coverage_subset ─────────────────────────────────────────
    @tool(
        registry,
        tier=2, domains=["dataset"], name="read_coverage_subset",
        capabilities=["dataset_ingest"],  # CapabilityRegistry 既有项
        description=(
            "OGC API - Coverages 受控 subset 读取：按服务声明的轴（bbox 仅在可识别"
            "经纬轴时接受）与预算（max_cells/响应字节帽）读取 coverage 子集并物化"
            "为 COG ref（ref:fabric-cog/…，携带 fingerprint/provenance/预算账本）。"
            "大面积/超预算/格式未声明 → typed reason code（budget_exceeded 等），"
            "绝不静默降采样或下载整幅。"
            "\n返回：{status, ref, grid_shape, crs, nodata, budget, provenance}"
        ),
        param_descriptions={
            "url": "Coverages 服务根 URL",
            "dataset_id": "coverage 集合 ID（inspect_data_service 发现）",
            "bbox": "[minx,miny,maxx,maxy]（EPSG:4326；轴不可识别时改用 subsets）",
            "subsets": "显式轴子集 {轴名: [min,max]}（轴名以服务 domainset 为准）",
            "bands": "波段名列表（缺省全部）",
            "max_cells": "像元预算上限（默认 4,000,000）",
            "session_id": "会话 ID（ref 归属）",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="state_mutation",
        data_mutations=["cache_write", "session_state"],
        produced_refs=["data"],
        network=True,
        deterministic=False,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=["coverage", "subset", "栅格", "预算", "OGC", "ref"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        failure_modes=["network_error", "budget_exceeded", "capability_not_declared",
                       "axis_not_identifiable", "timeout"],
    )
    async def read_coverage_subset(
        url: str,
        dataset_id: str,
        session_id: str = "default",
        bbox: Optional[List[float]] = None,
        subsets: Optional[Dict[str, List[float]]] = None,
        bands: Optional[List[str]] = None,
        max_cells: int = 4_000_000,
    ) -> dict:
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )
        from app.services.data_fabric.coverage_read import (
            CoverageReadError,
            read_coverage_subset as _read,
        )

        adapter = OGCCoveragesAdapter(ConnectionProfile(
            id="coverage-read", source_type="ogc_api_coverages",
            endpoint_url=url, name="coverage read",
        ))
        try:
            result = await _read(
                adapter, dataset_id,
                session_id=session_id,
                bbox=bbox,
                subsets={k: tuple(v) for k, v in (subsets or {}).items()} or None,
                bands=bands,
                max_cells=int(max_cells),
            )
        except CoverageReadError as e:
            return {
                "status": "error",
                "error_type": e.reason_code,
                "error": str(e),
                "details": e.details,
                "ref": None,
            }
        return _bounded_receipt(result)

    # ── materialize_remote_raster_asset ──────────────────────────────
    @tool(
        registry,
        tier=2, domains=["dataset"], name="materialize_remote_raster_asset",
        capabilities=["dataset_ingest"],  # CapabilityRegistry 既有项
        description=(
            "远程 COG/GeoTIFF 资产受控物化：SSRF 门 → 头读预检 → AOI/bands/"
            "overview 显式窗口读（请求/字节预算 + 可取消）→ 本地 COG ref"
            "（ref:fabric-cog/… + sha256 指纹 + CAS DataObject + provenance；"
            "签名 URL 脱敏）。绝不整幅下载；超预算 → budget_exceeded（带窗口"
            "事实）。同参数幂等（内容寻址去重）；资产变化 → 新指纹新身份。"
            "STAC 发现用 search_spatial_catalog/query_federated_data（stac 源）。"
            "\n返回：{status, ref, content_sha256, data_object_id, ledger, provenance}"
        ),
        param_descriptions={
            "href": "远程 COG/GeoTIFF URL（SSRF 校验；签名 URL 全程脱敏）",
            "bbox": "AOI [minx,miny,maxx,maxy]（EPSG:4326；缺省=整幅但受预算门）",
            "bands": "波段索引列表（1-based；缺省全部）",
            "overview_level": "overview 层级（0=全分辨率；N=按 2^N 抽样）",
            "max_output_bytes": "窗口读输出字节预算（默认 256MiB）",
            "session_id": "会话 ID（ref 归属）",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="state_mutation",
        data_mutations=["cache_write", "session_state"],
        produced_refs=["data"],
        network=True,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="large",
        tags=["COG", "物化", "远程资产", "STAC", "预算", "幂等", "ref"],
        output_semantic_type="ref",
        result_size_policy="ref_offload",
        failure_modes=["network_error", "budget_exceeded", "ssrf_blocked",
                       "not_a_raster", "cancelled", "timeout"],
    )
    async def materialize_remote_raster_asset(
        href: str,
        session_id: str = "default",
        bbox: Optional[List[float]] = None,
        bands: Optional[List[int]] = None,
        overview_level: int = 0,
        max_output_bytes: int = 256 * 1024 * 1024,
    ) -> dict:
        from app.services.data_fabric.cog_materialization import (
            CogMaterializationError,
            materialize_remote_cog_asset,
        )

        try:
            result = await materialize_remote_cog_asset(
                href,
                session_id=session_id,
                bands=bands,
                bbox=bbox,
                overview_level=overview_level,
                max_output_bytes=int(max_output_bytes),
            )
        except CogMaterializationError as e:
            return {
                "status": "error",
                "error_type": e.reason_code,
                "error": str(e),
                "details": e.details,
                "ref": None,
            }
        return _bounded_receipt(result)

    # ── add_tile_service_layer ───────────────────────────────────────
    @tool(
        registry,
        tier=2, domains=["cartography"], name="add_tile_service_layer",
        capabilities=["thematic_cartography"],
        description=(
            "把已治理的 OGC API - Tiles tileset 作为图层加入当前地图（MapSpec 受控"
            "事务）：矢量瓦片集 → MapSpec vector 源（tiles 模板 + min/maxzoom），"
            "栅格瓦片集 → data_fabric url 模板 —— 瓦片经本服务受控代理（SSRF/缓存/"
            "ETag/归属治理），前端绝不直连第三方 URL。tileset 资格未过门（非 "
            "WebMercatorQuad/未声明媒体类型等）→ 诚实拒绝 + reason codes。"
            "\n返回：{status, layer_id, source, mapspec_fingerprint}"
        ),
        param_descriptions={
            "catalog_item_id": "目录条目 ID（cid:tilematrixset，connect+sync 后获得）",
            "layer_id": "图层 ID（缺省由 tileset 名派生）",
            "layer_type": "图层类型（vector tileset 默认 line；raster 为 raster）",
            "session_id": "会话 ID（MapSpec 归属 + 瓦片代理鉴权）",
        },
        execution_policy=ToolExecutionPolicy.ASYNC,
        side_effect="state_mutation",
        data_mutations=["session_state"],
        map_mutations=("add_layer",),
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=["瓦片服务", "底图", "MapSpec", "图层", "tiles", "受控代理"],
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "tileset_not_display_ready", "catalog_not_found"],
    )
    async def add_tile_service_layer(
        catalog_item_id: str,
        session_id: str = "default",
        layer_id: Optional[str] = None,
        layer_type: Optional[str] = None,
    ) -> dict:
        from app.services.data_fabric.errors import DataFabricError, UnsupportedSourceError
        from app.services.data_fabric.ogc_tiles_proxy import build_mapspec_source
        from app.services.mapspec_store import mapspec_store
        from app.services.data_fabric.spatial_catalog import spatial_catalog_service

        descriptor = spatial_catalog_service.get_dataset(catalog_item_id, owner=session_id)
        if descriptor is None:
            return {
                "status": "error",
                "error_type": "catalog_not_found",
                "error": f"catalog item '{catalog_item_id}' not found (owner={session_id})",
            }
        metadata = descriptor.metadata or {}
        try:
            source = build_mapspec_source(
                metadata, item_id=catalog_item_id, session_id=session_id
            )
        except UnsupportedSourceError as e:
            return {
                "status": "error",
                "error_type": "tileset_not_display_ready",
                "error": str(e),
                "capability": metadata.get("capability"),
            }
        media_kind = metadata.get("media_kind")
        lid = layer_id or f"ogc-tiles-{catalog_item_id.replace(':', '-').lower()}"
        ltype = layer_type or ("raster" if media_kind == "raster" else "line")
        layer: Dict[str, Any] = {"id": lid, "type": ltype, "source": lid}
        if ltype == "raster":
            layer["paint"] = {}
        try:
            res = await mapspec_store.layer_upsert(session_id, layer, source)
        except DataFabricError as e:
            return {"status": "error", "error_type": "mapspec_error", "error": str(e)}
        if not res.get("success"):
            return {"status": "error", "error_type": "mapspec_error",
                    "error": res.get("message") or "layer upsert failed"}
        return {
            "status": "success",
            "layer_id": lid,
            "source": source,
            "mapspec": res.get("mapspec"),
            "capability": metadata.get("capability"),
        }


def _redact(url: str) -> str:
    from app.services.data_fabric.security import DataFabricSecurity

    return DataFabricSecurity.redact_url(url) or url


def _bounded_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """descriptor.metadata 的 LLM 出仓裁剪（不搬瓦片模板原文等大字段）。"""
    if not isinstance(metadata, dict):
        return {}
    keep = (
        "descriptor_version", "protocol", "tile_matrix_set_id", "minzoom",
        "maxzoom", "media_kind", "media_types_declared", "capability",
        "display_ready", "subset_read_ready", "axes", "bands",
        "formats_declared", "formats_supported", "formats_state",
        "has_coverage_endpoint", "zoom_levels",
    )
    return {k: metadata[k] for k in keep if k in metadata}


def _bounded_receipt(result: Dict[str, Any]) -> Dict[str, Any]:
    """ref 回执的出仓裁剪（几何/影像不进对话；path 不透明）。"""
    out = dict(result)
    out.pop("path", None)
    return out


__all__ = ["register_ogc_service_tools"]
