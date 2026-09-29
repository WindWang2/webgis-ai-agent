"""Catalog MVT tile serving —— 数据平面域内的唯一事实源（W12 vNext）。

Route（``app.api.routes.data_fabric``）只做协议适配：鉴权（必须先于缓存
查找，API-01）→ 调本服务 → ETag/304/HTTP 状态映射。此前路由内直接
``PostGISAdapter(conn_profile)`` 构建 adapter（完全绕过 governed
resolution —— 无 registry/健康/secret 分离），且非 PostGIS 源 422 硬拒绝，
12 类 adapter 的矢量目录项无法获得瓦片显示路径。

双路径（能力驱动，ADR-0047「MVT 为 additive 显示路径」）：
- ``server_mvt``：adapter 具备 ``serve_mvt_tile``（PostGIS ST_AsMVT）——
  高性能路径，行为与既有路由逐字节一致；
- ``python_fallback``：其余源 → governed adapter 的 **bbox 有界查询**
  （``tile_bounds_lonlat`` 视口翻译 + ``TIER_SCAN_CAP_FEATURES`` 帽）→
  纯 Python ``mvt.encode_tile``。编码 CPU 在 to_thread；取消在查询前后
  checkpoint（与 query_catalog_item_async 同语义）。

缓存与收敛：``TILE_CACHE``（fingerprint 入键入 ETag，数据版本失效）+
``TileBuildCoalescer``（同键并发合并为一次构建；50 并发同瓦片 = 1 次
ST_AsMVT / 1 次查询编码）。
"""
from __future__ import annotations

import asyncio
import gzip
import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.lib.cartography.data_tiers import TIER_SCAN_CAP_FEATURES
from app.schemas.data_fabric_schema import QuerySpec
from app.services.data_fabric.tile_cache import (
    TileBuildCoalescer,
    TileCacheEntry,
    TILE_CACHE,
)
from app.services.data_fabric.tile_identity import (
    compute_tile_etag,
    tenant_scope_of,
    tile_bounds_lonlat,
    tile_cache_key,
)

logger = logging.getLogger(__name__)

#: 纯 Python fallback 单瓦片要素帽（与 PostGIS 路径 MVT_MAX_FEATURES_PER_TILE
#: 同一量纲锚点；远端忽略 limit 时本地截断 —— 有界编码，不信任远端 limit）。
TILE_FALLBACK_FEATURE_CAP = TIER_SCAN_CAP_FEATURES


@dataclass(frozen=True)
class TileServeResult:
    """tile 服务结果（route 据此做 HTTP 映射）。

    ``cache_state``: hit（缓存命中）| miss（本次构建）| bypass（诚实降级，
    构建成功但超缓存单条上限未驻留）。
    ``source``: cache | server_mvt | python_fallback。
    """

    gz: Optional[bytes]
    fingerprint: str
    cache_state: str
    source: str
    etag: str = ""


class CatalogTileService:
    """目录瓦片服务的进程内单例事实源（服务层拥有缓存与构建所有权）。"""

    def __init__(self, cache=TILE_CACHE, coalescer: Optional[TileBuildCoalescer] = None):
        self._cache = cache
        self._coalescer = coalescer or TileBuildCoalescer()

    async def serve_catalog_tile(
        self,
        item: Any,
        ds_model: Any,
        z: int,
        x: int,
        y: int,
        *,
        cancel_token: Any = None,
    ) -> TileServeResult:
        """鉴权之后、HTTP 之前的全部 tile 事实（缓存/治理/构建/收敛）。

        调用方（route）必须已完成 tenant 归属校验；本方法不重复鉴权，也
        绝不在鉴权前触碰缓存。
        """
        fingerprint = getattr(item, "fingerprint", None) or "-"
        tenant_scope = tenant_scope_of(
            getattr(ds_model, "org_id", None), getattr(ds_model, "owner_id", None)
        )
        cache_key = tile_cache_key(item.id, tenant_scope, fingerprint, z, x, y)

        cached = self._cache.get(cache_key)
        if cached is not None:
            return TileServeResult(
                gz=cached.gz,
                fingerprint=cached.fingerprint,
                cache_state="hit",
                source="cache",
                etag=compute_tile_etag(cached.gz, cached.fingerprint),
            )

        async def _build() -> TileServeResult:
            if cancel_token is not None:
                cancel_token.raise_if_cancelled()
            adapter = self._resolve_governed_adapter(ds_model)
            if hasattr(adapter, "serve_mvt_tile"):
                raw = await asyncio.to_thread(
                    adapter.serve_mvt_tile, item.name, z, x, y, timeout_s=30.0
                )
                source = "server_mvt"
            else:
                raw = await self._build_fallback_tile(
                    adapter, ds_model, item, z, x, y, cancel_token
                )
                source = "python_fallback"
            if raw is None:
                return TileServeResult(None, fingerprint, "miss", source)
            gz = await asyncio.to_thread(gzip.compress, raw, 6, mtime=0)
            stored = self._cache.put(cache_key, TileCacheEntry(gz=gz, fingerprint=fingerprint))
            return TileServeResult(
                gz=gz,
                fingerprint=fingerprint,
                cache_state="miss" if stored else "bypass",
                source=source,
                etag=compute_tile_etag(gz, fingerprint),
            )

        result = await self._coalescer.run(cache_key, _build)
        return result

    def stats(self) -> dict:
        """缓存观测快照（hit/miss/驱逐/超限拒绝 + 在飞收敛数）。"""
        s = self._cache.stats()
        s["inflight"] = self._coalescer.inflight_keys()
        return s

    def _resolve_governed_adapter(self, ds_model: Any):
        """治理解析（registry 优先）—— 与 manager.query 路径同一条门。"""
        from app.services.data_fabric.manager import DataFabricManager

        return DataFabricManager._governed_adapter(ds_model)

    async def _build_fallback_tile(
        self,
        adapter: Any,
        ds_model: Any,
        item: Any,
        z: int,
        x: int,
        y: int,
        cancel_token: Any = None,
    ) -> Optional[bytes]:
        """非 server-MVT 源：bbox 有界查询 → 纯 Python MVT 编码。

        - bbox = 视口瓦片的经纬度包络（tile_identity.tile_bounds_lonlat）；
        - limit = TILE_FALLBACK_FEATURE_CAP，且结果本地再截断（远端可能
          忽略 limit —— 有界编码不以远端自觉为前提）；
        - 空结果 = None（route → 204，与 server_mvt 空瓦片同语义）。
        """
        from app.services.data_fabric.manager import _execute_remote_query
        from app.services.mvt import encode_tile

        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        spec = QuerySpec(bbox=list(tile_bounds_lonlat(z, x, y)), limit=TILE_FALLBACK_FEATURE_CAP)
        result = await asyncio.to_thread(
            _execute_remote_query, adapter, ds_model.id, item.name, spec
        )
        if cancel_token is not None:
            cancel_token.raise_if_cancelled()
        features = list(getattr(result, "features", None) or [])[:TILE_FALLBACK_FEATURE_CAP]
        if not features:
            return None
        return await asyncio.to_thread(encode_tile, features, z, x, y)


#: 进程内单例（route 与测试共用；测试可构造独立实例注入 cache/coalescer）。
catalog_tile_service = CatalogTileService()

__all__ = [
    "CatalogTileService",
    "TileServeResult",
    "TILE_FALLBACK_FEATURE_CAP",
    "catalog_tile_service",
]
