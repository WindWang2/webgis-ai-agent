"""OGC API - Tiles 受控瓦片代理（GOAL-02 / M2）。

把**已治理目录条目**（ogc_api_tiles 源经 sync 注册）的远端瓦片以本服务
URL 暴露给 MapSpec/MapLibre，浏览器瓦片流量收敛到本服务：

    注册 ogc_api_tiles 源 → catalog item（descriptor 携带三态能力 +
    瓦片模板）→ ``GET /data-fabric/catalog/{item_id}/ogc-tiles/{z}/{x}/{y}``
    → MapSpec vector source tiles / raster url 模板 → MapLibre

纪律（与 ``tile_service`` / ``pmtiles_tile_service`` 同源，W12/ADR-0094 §8）：

- route 只做协议适配；鉴权必须先于缓存查找（API-01）→ 本服务 →
  ETag/304/typed-error 映射；
- 缓存身份 = (item_id, tenant_scope, fingerprint, z, x, y)，键前缀
  ``ogc-tiles:``；ETag = sha256(body + fingerprint)[:16] 内容寻址；
- 远端获取经 ``OGCTilesAdapter.fetch_tile``（SSRF 同源模板、display 资格
  门、zoom 矩阵校验、单瓦片字节帽、401/403/429 typed）；
- 远端 404 / zoom 超出声明范围 → ``None`` → route 204（诚实空瓦片）；
  绝不把失败伪装成空瓦片（#766 同族）；
- 不 gzip 重压缩：MVT 归档瓦片可能已是 gzip，PNG/JPEG 已压缩。

MapSpec 桥（D1：不新增 schema 版本）：

- vector tileset → 既有 ``VectorTileMapSpecSource``（tiles + min/maxzoom）；
- raster tileset → 既有 data_fabric source 的 ``url`` 字符串模板
  （runtime 兜底分支 → addRasterTileSource）。
"""
from __future__ import annotations

from app.services.data_fabric.tile_cache import DfTileCache
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.services.data_fabric.errors import UnsupportedSourceError
from app.services.data_fabric.tile_cache import (
    TileBuildCoalescer,
    TileCacheEntry,
    TILE_CACHE,
)
from app.services.data_fabric.tile_identity import (
    compute_tile_etag,
    tenant_scope_of,
    tile_cache_key,
)

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ogc-tiles:"


@dataclass(frozen=True)
class OgcTileServeResult:
    """OGC 瓦片服务结果（route 据此做 HTTP 映射）。

    ``body``: None = 远端无该瓦片 / zoom 超出声明范围（route → 204）。
    """

    body: Optional[bytes]
    content_type: str
    fingerprint: str
    cache_state: str
    etag: str = ""


def build_mapspec_source(
    descriptor_metadata: Dict[str, Any],
    *,
    item_id: str,
    session_id: str,
    api_prefix: str = "/api/v1",
) -> Dict[str, Any]:
    """tileset descriptor → MapSpec source dict（不新增 schema 版本，D1）。

    - vector tileset → ``{type: "vector", tiles, minzoom, maxzoom}``
      （既有 VectorTileMapSpecSource；前端 applySource vector 分支渲染）；
    - raster tileset → ``{type: "data_fabric", catalog_item_id, url}``
      （既有 data_fabric 源；runtime 字符串 URL 兜底 → addRasterTileSource）；
    - 非 display-ready → 抛 ``UnsupportedSourceError``（含 reason codes ——
      agent 可解释，MapSpec 绝不挂一个必然 4xx 的源）。
    """
    cap = descriptor_metadata.get("capability") or {}
    if not descriptor_metadata.get("display_ready"):
        reasons = ",".join(r.get("code", "?") for r in cap.get("reasons", []))
        raise UnsupportedSourceError(
            "tileset is not display-ready "
            f"(state={cap.get('state')}, reasons=[{reasons}])",
            details={"item_id": item_id, "capability": cap},
        )
    from urllib.parse import quote

    # R1 P2：item_id/session_id 进 URL 模板一律段级编码（&/= 空格等不破坏
    # 模板）；maxzoom clamp 到代理路由的服务上限 24（超出区间的请求会被
    # route 400 —— MapLibre 声明面与实际服务面必须一致）。
    _ROUTE_MAX_ZOOM = 24
    tile_url = (
        f"{api_prefix}/data-fabric/catalog/{quote(str(item_id), safe='')}"
        f"/ogc-tiles/{{z}}/{{x}}/{{y}}?session_id={quote(str(session_id), safe='')}"
    )
    minzoom = descriptor_metadata.get("minzoom")
    maxzoom = descriptor_metadata.get("maxzoom")
    if isinstance(maxzoom, int):
        maxzoom = min(maxzoom, _ROUTE_MAX_ZOOM)
    if descriptor_metadata.get("media_kind") == "vector":
        source: Dict[str, Any] = {"type": "vector", "tiles": [tile_url]}
        if isinstance(minzoom, int):
            source["minzoom"] = max(0, minzoom)
        if isinstance(maxzoom, int):
            source["maxzoom"] = maxzoom
        return source
    return {
        "type": "data_fabric",
        "protocol": "ogc_api_tiles",
        "catalog_item_id": item_id,
        "url": tile_url,
    }


class OgcTilesProxyService:
    """OGC 瓦片代理的进程内单例事实源。"""

    def __init__(self, cache: DfTileCache=TILE_CACHE, coalescer: Optional[TileBuildCoalescer] = None) -> None:
        self._cache = cache
        self._coalescer = coalescer or TileBuildCoalescer()
        self._content_types: dict[tuple, str] = {}
        self._content_types_max = 2048

    def _remember_content_type(self, key: tuple, content_type: str) -> None:
        self._content_types[key] = content_type
        overflow = len(self._content_types) - self._content_types_max
        if overflow > 0:
            for stale in list(self._content_types)[:overflow]:
                self._content_types.pop(stale, None)

    async def serve_tile(
        self,
        item: Any,
        ds_model: Any,
        z: int,
        x: int,
        y: int,
    ) -> OgcTileServeResult:
        """鉴权之后、HTTP 之前的全部瓦片事实（缓存/治理解析/获取/收敛）。

        调用方（route）必须已完成会话归属与目录条目租户校验；本方法不
        重复鉴权，也绝不在鉴权前触碰缓存。
        """
        fingerprint = getattr(item, "fingerprint", None) or "-"
        tenant_scope = tenant_scope_of(
            getattr(ds_model, "org_id", None), getattr(ds_model, "owner_id", None)
        )
        cache_key = tile_cache_key(f"{_KEY_PREFIX}{item.id}", tenant_scope, fingerprint, z, x, y)

        cached = self._cache.get(cache_key)
        if cached is not None:
            from app.services.data_fabric.pmtiles_tile_service import sniff_content_type

            return OgcTileServeResult(
                body=cached.gz,
                content_type=self._content_types.get(cache_key)
                or sniff_content_type(cached.gz),
                fingerprint=cached.fingerprint,
                cache_state="hit",
                etag=compute_tile_etag(cached.gz, cached.fingerprint),
            )

        async def _build() -> OgcTileServeResult:
            adapter = self._resolve_governed_adapter(ds_model)
            fetch = getattr(adapter, "fetch_tile", None)
            if fetch is None:
                raise UnsupportedSourceError(
                    f"source_type={getattr(ds_model, 'source_type', '?')} 不提供 OGC Tiles 瓦片获取"
                )
            dataset_id = self._dataset_id_of(item)
            body_ct = await asyncio.to_thread(fetch, dataset_id, z, x, y)
            if body_ct is None:
                return OgcTileServeResult(None, "application/octet-stream", fingerprint, "miss")
            body, content_type = body_ct
            self._remember_content_type(cache_key, content_type)
            stored = self._cache.put(cache_key, TileCacheEntry(gz=body, fingerprint=fingerprint))
            return OgcTileServeResult(
                body=body,
                content_type=content_type,
                fingerprint=fingerprint,
                cache_state="miss" if stored else "bypass",
                etag=compute_tile_etag(body, fingerprint),
            )

        return await self._coalescer.run(cache_key, _build)

    @staticmethod
    def _dataset_id_of(item: Any) -> str:
        """catalog 条目 id（= ``cid:tms``）就是 adapter 的 dataset_id。"""
        return str(getattr(item, "id", "") or "")

    def stats(self) -> dict:
        s = self._cache.stats()
        s["inflight"] = self._coalescer.inflight_keys()
        return s

    def _resolve_governed_adapter(self, ds_model: Any) -> GeospatialDataSourceAdapter:
        """治理解析（registry 优先）—— 与 manager.query 路径同一条门。"""
        from app.services.data_fabric.manager import DataFabricManager

        return DataFabricManager._governed_adapter(ds_model)


#: 进程内单例（route 与测试共用）。
ogc_tiles_proxy_service = OgcTilesProxyService()

__all__ = [
    "OgcTileServeResult",
    "OgcTilesProxyService",
    "build_mapspec_source",
    "ogc_tiles_proxy_service",
]
