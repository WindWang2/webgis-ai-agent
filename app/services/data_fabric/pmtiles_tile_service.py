"""PMTiles→XYZ 服务层（G3D01 / 自托管底图路径）。

``PMTilesAdapter.read_tile_bytes``（v3 reader：header/varint/Hilbert/
有界解压/字节帽）此前只有元数据面消费 —— 本服务把**已受治理的本地或
远端 PMTiles 归档**的瓦片字节以 XYZ 语义经 HTTP 暴露，使 providers.ts
注释中承诺的"本地 PMTiles 派生 XYZ 服务"成为真实自托管路径：

    注册 pmtiles 数据源 → catalog item → /data-fabric/catalog/{id}/
    pmtiles-tiles/{z}/{x}/{y} → NEXT_PUBLIC_LOCAL_BASEMAP_URL 或 MapSpec
    raster source url 模板 → MapLibre

纪律（与 ``tile_service.CatalogTileService`` 同源，W12/ADR-0094 §8）：
- route 只做协议适配；鉴权（必须先于缓存查找，API-01）→ 本服务 →
  ETag/304/typed-error 映射；
- 缓存身份 = (item_id, tenant_scope, fingerprint, z, x, y)（键前缀
  ``pmtiles:`` 与 MVT 表示隔离；manager 同步失效两种前缀）；ETag =
  sha256(body + fingerprint)[:16] 内容寻址；
- 空瓦片（归档无该 zxy）= None → route 204，与 MVT 空瓦片同语义；
- 字节帽复用 adapter 的 ``MAX_TILE_BYTES``/``MAX_DECOMPRESSED_BYTES``
  （解压炸弹守卫），缓存驻留由 ``DfTileCache`` 单条上限裁决（超限 =
  bypass，仍服务但不驻留）；
- 绝不 gzip 重压缩：PNG/WebP/MVT 归档瓦片已各自压缩，再压只耗 CPU。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

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

#: 缓存键前缀 —— 与目录 MVT 键空间隔离（同一 pmtiles item 可同时被
#: python-fallback MVT 路由与 XYZ 路由服务，两种表示字节不同，键必须
#: 分离；manager._invalidate_catalog_tiles 同步失效两种前缀）。
_KEY_PREFIX = "pmtiles:"


def sniff_content_type(body: bytes) -> str:
    """瓦片字节魔数嗅探（确定性，无 IO）。

    ``TileCacheEntry`` 只驻留字节与 fingerprint（NamedTuple 兼容裸二元
    tuple 的历史契约，不可私加字段）；content-type 由旁路 LRU 记录，
    驱逐漂移时以魔数嗅探兜底 —— 归档瓦片类型封闭（PNG/JPEG/WebP/AVIF/
    MVT-gzip），前导字节足以判别。
    """
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if body[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    if body[4:8] == b"ftyp" and body[8:12] in (b"avif", b"avis", b"mif1", b"miaf"):
        return "image/avif"
    if body[:2] == b"\x1f\x8b":  # gzip —— MVT 矢量瓦片
        return "application/vnd.mapbox-vector-tile"
    return "application/octet-stream"


@dataclass(frozen=True)
class PmtilesServeResult:
    """PMTiles 瓦片服务结果（route 据此做 HTTP 映射）。

    ``body``: None = 归档中无该瓦片（route → 204）。
    ``cache_state``: hit | miss | bypass（构建成功但超缓存单条上限未驻留）。
    """

    body: Optional[bytes]
    content_type: str
    fingerprint: str
    cache_state: str
    etag: str = ""


class PmtilesTileService:
    """PMTiles XYZ 瓦片服务的进程内单例事实源。"""

    def __init__(self, cache=TILE_CACHE, coalescer: Optional[TileBuildCoalescer] = None):
        self._cache = cache
        self._coalescer = coalescer or TileBuildCoalescer()
        # content-type 旁路记录（与主缓存同键；条目上限对齐主缓存默认值，
        # 超限时最旧先行 —— 与主缓存 LRU 语义近似，残余键由魔数嗅探兜底）。
        self._content_types: dict[tuple, str] = {}
        self._content_types_max = 2048

    def _remember_content_type(self, key: tuple, content_type: str) -> None:
        self._content_types[key] = content_type
        overflow = len(self._content_types) - self._content_types_max
        if overflow > 0:
            for stale in list(self._content_types)[:overflow]:
                self._content_types.pop(stale, None)

    async def serve_pmtiles_tile(
        self,
        item: Any,
        ds_model: Any,
        z: int,
        x: int,
        y: int,
    ) -> PmtilesServeResult:
        """鉴权之后、HTTP 之前的全部瓦片事实（缓存/治理/构建/收敛）。

        调用方（route）必须已完成 tenant 归属校验；本方法不重复鉴权，也
        绝不在鉴权前触碰缓存。
        """
        fingerprint = getattr(item, "fingerprint", None) or "-"
        tenant_scope = tenant_scope_of(
            getattr(ds_model, "org_id", None), getattr(ds_model, "owner_id", None)
        )
        cache_key = tile_cache_key(f"{_KEY_PREFIX}{item.id}", tenant_scope, fingerprint, z, x, y)

        cached = self._cache.get(cache_key)
        if cached is not None:
            return PmtilesServeResult(
                body=cached.gz,
                content_type=self._content_types.get(cache_key)
                or sniff_content_type(cached.gz),
                fingerprint=cached.fingerprint,
                cache_state="hit",
                etag=compute_tile_etag(cached.gz, cached.fingerprint),
            )

        async def _build() -> PmtilesServeResult:
            adapter = self._resolve_governed_adapter(ds_model)
            read = getattr(adapter, "read_tile_bytes", None)
            if read is None:
                raise UnsupportedSourceError(
                    f"source_type={getattr(ds_model, 'source_type', '?')} 不提供 PMTiles 瓦片读取"
                )
            raw = await asyncio.to_thread(read, z, x, y)
            if raw is None:
                return PmtilesServeResult(None, "application/octet-stream", fingerprint, "miss")
            body, content_type = raw
            self._remember_content_type(cache_key, content_type)
            stored = self._cache.put(cache_key, TileCacheEntry(gz=body, fingerprint=fingerprint))
            return PmtilesServeResult(
                body=body,
                content_type=content_type,
                fingerprint=fingerprint,
                cache_state="miss" if stored else "bypass",
                etag=compute_tile_etag(body, fingerprint),
            )

        return await self._coalescer.run(cache_key, _build)

    def stats(self) -> dict:
        s = self._cache.stats()
        s["inflight"] = self._coalescer.inflight_keys()
        return s

    def _resolve_governed_adapter(self, ds_model: Any):
        """治理解析（registry 优先）—— 与 manager.query 路径同一条门。"""
        from app.services.data_fabric.manager import DataFabricManager

        return DataFabricManager._governed_adapter(ds_model)


#: 进程内单例（route 与测试共用）。
pmtiles_tile_service = PmtilesTileService()

__all__ = [
    "PmtilesServeResult",
    "PmtilesTileService",
    "pmtiles_tile_service",
    "sniff_content_type",
]
