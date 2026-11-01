"""OGC API - Tiles 发现 adapter（GOAL-02 / M1+M2，read-only discovery）。

职责边界（诚实封闭）：

- **只发现不取数**：landing/conformance/collections/tilesets/TileMatrixSet
  全部为只读 GET；瓦片字节的受控获取在 ``ogc_tiles_proxy``（M2）；
  本 adapter 的 ``query`` 对要素语义 typed 拒绝 —— 瓦片服务不是矢量
  查询源（与 WMS/WMTS 同纪律，不宣称统一栅格可计算源）。
- **三态能力**：``supported``（服务声明 + WebMercatorQuad 族 + 声明
  媒体类型在支持子集）/``unsupported``（互斥事实）/``unknown``（关键
  声明缺失，绝不猜投影）。评估只读服务声明（``ogc_capability``）。
- **版本化 descriptor**：``metadata["descriptor_version"] =
  "ogc-tiles-descriptor/1"``；消费方按版本协商，未知版本不猜。
- CRS/bbox 缺失 → None/unknown，绝不伪造（元数据真实性契约）。

dataset_id 编码：``{collection_id}:{tile_matrix_set_id}``；顶层 /tiles
（非集合服务）用 ``_: {tms_id}``。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DataFabricHealth,
    DatasetDescriptor,
    QueryResult,
    QuerySpec,
)
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    DataFabricError,
    QueryUnsupportedError,
    SourceBadResponseError,
    SourceUnreachableError,
)
from app.services.data_fabric.ogc_capability import (
    CAP_SUPPORTED,
    assess_tileset,
)
from app.services.data_fabric.security import (
    DataFabricSecurity,
    make_safe_session,
    safe_json_get,
)

logger = logging.getLogger(__name__)

DESCRIPTOR_VERSION = "ogc-tiles-descriptor/1"

#: tileset/TileMatrixSet 文档的读取上限（声明文档都很小；超限视为异常）。
_META_MAX_BYTES = 2 * 1024 * 1024

_TILESET_LINK_RELS = {"tile"}
_TILESET_LIST_REL = "http://www.opengis.net/def/rel/ogc/1.0/tilesets-vector"


def split_tiles_dataset_id(dataset_id: str) -> Tuple[str, str]:
    """``cid:tms`` → (cid, tms)；无冒号 → ("_", dataset_id)。"""
    if ":" in dataset_id:
        cid, tms = dataset_id.rsplit(":", 1)
        return (cid or "_", tms)
    return ("_", dataset_id)


class OGCTilesAdapter(GeospatialDataSourceAdapter):
    """OGC API - Tiles 的 tileset 发现与三态资格判定。"""

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.raw_url = self.profile.url or ""
        self.url = (
            DataFabricSecurity.validate_url(self.raw_url, allow_private=self.profile.allow_private)
            if self.raw_url
            else ""
        )
        self.options = self.profile.options or {}
        self.session = make_safe_session(allow_private=self.profile.allow_private)
        if "headers" in self.options:
            self.session.headers.update(self.options["headers"])
        self._conformance_cache: Optional[Tuple[float, List[str]]] = None

    # ── 只读发现原语 ──────────────────────────────────────────────────

    def _get_conformance(self) -> List[str]:
        if self._conformance_cache and (time.monotonic() - self._conformance_cache[0]) < 60:
            return self._conformance_cache[1]
        classes: List[str] = []
        try:
            data = safe_json_get(
                self.session, self.url.rstrip("/") + "/conformance",
                timeout=5, max_bytes=_META_MAX_BYTES,
            )
            raw = data.get("conformsTo", []) if isinstance(data, dict) else []
            classes = [str(c) for c in raw if isinstance(c, str)]
        except Exception:
            classes = []
        self._conformance_cache = (time.monotonic(), classes)
        return classes

    def _declares_tiles(self) -> bool:
        return any("ogcapi-tiles" in c for c in self._get_conformance())

    def _get_json(self, url: str, timeout: float = 10.0) -> Dict[str, Any]:
        try:
            data = safe_json_get(self.session, url, timeout=timeout, max_bytes=_META_MAX_BYTES)
        except DataFabricError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SourceBadResponseError(f"OGC Tiles metadata fetch failed: {e}") from e
        if not isinstance(data, dict):
            raise SourceBadResponseError(f"OGC Tiles metadata at {DataFabricSecurity.redact_url(url)} is not a JSON object")
        return data

    def _list_collections(self) -> List[Dict[str, Any]]:
        data = self._get_json(self.url.rstrip("/") + "/collections")
        cols = data.get("collections") or []
        return [c for c in cols if isinstance(c, dict)] if isinstance(cols, list) else []

    def _tilesets_of_collection(self, cid: str) -> List[Dict[str, Any]]:
        """集合的 tileset 摘要列表（rel=tilesets-* 链接或直接探测 /tiles）。"""
        base = self.url.rstrip("/")
        if cid != "_":
            url = f"{base}/collections/{cid}/tiles"
        else:
            url = f"{base}/tiles"
        try:
            data = self._get_json(url)
        except DataFabricError:
            # 无 /tiles 端点 = 该集合无瓦片能力（unknown，不冒充空列表失败）
            logger.debug("[ogc-tiles] no tilesets endpoint for '%s'", cid)
            return []
        tilesets = data.get("tilesets") or []
        return [t for t in tilesets if isinstance(t, dict)] if isinstance(tilesets, list) else []

    # ── adapter 契约 ──────────────────────────────────────────────────

    def probe(self) -> bool:
        if not self.url:
            return False
        try:
            target = self.url.rstrip("/") + "/collections"
            resp = self.session.get(target, timeout=5)
            if resp.status_code in (200, 206):
                return True
            # 非集合布局（顶层 /tiles）：再探一层
            top = self.url.rstrip("/") + "/tiles"
            return self.session.get(top, timeout=5).status_code in (200, 206)
        except Exception as e:  # noqa: BLE001
            logger.debug("[ogc-tiles] probe failed for %s: %s", self.url, e)
            return False

    def capabilities(self) -> List[str]:
        return [
            "ogc_api_tiles",
            "tileset_discovery",
            "display_only",
            "tile_proxy_ready",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """发现所有 tileset：每个 tileset 一个目录条目（id=cid:tms）。"""
        if not self.url:
            return []
        out: List[Dict[str, Any]] = []
        try:
            collections = self._list_collections()
        except DataFabricError as e:
            logger.warning("[ogc-tiles] collections listing failed for %s: %s", self.url, e)
            collections = []
        cids: List[str] = []
        for col in collections:
            cid = str(col.get("id") or "").strip()
            if not cid:
                continue
            # 只有声明了 tilesets 链接或能列出 tilesets 的集合才进入目录
            # （无谓探测会被无 tiles 能力的服务噪音淹没）。
            if any(
                "tilesets" in str(lnk.get("rel") or "")
                for lnk in col.get("links", []) if isinstance(lnk, dict)
            ):
                cids.append(cid)
        if not cids:
            # 顶层 /tiles（无集合维度服务）
            cids = ["_"]
        for cid in cids:
            for ts in self._tilesets_of_collection(cid):
                tms_id = self._tms_id_of_tileset_summary(ts, cid)
                if not tms_id:
                    continue
                out.append({
                    "id": f"{cid}:{tms_id}" if cid != "_" else tms_id,
                    "title": str(ts.get("title") or f"{cid} {tms_id} tiles"),
                    "item_type": "tile",
                    "source_type": "ogc_api_tiles",
                })
        return out

    def _tms_id_of_tileset_summary(self, ts: Dict[str, Any], cid: str) -> Optional[str]:
        """tileset 摘要 → TileMatrixSet id：优先 tileset 链接 href 尾段，
        其次 tileMatrixSetDefinition URI 尾段（注册 URI 的最后一段即 id）。"""
        for link in ts.get("links", []) or []:
            if isinstance(link, dict) and str(link.get("rel") or "") == "tileset":
                href = str(link.get("href") or "")
                if href:
                    tail = href.rstrip("/").rsplit("/", 1)[-1]
                    if tail:
                        return tail
        uri = ts.get("tileMatrixSetDefinition") or ts.get("tileMatrixSetURI")
        if isinstance(uri, str) and uri:
            tail = uri.rstrip("/").rsplit("/", 1)[-1]
            return tail or None
        return None

    # ── describe：三态资格 + 版本化 descriptor ───────────────────────

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        if not self.url:
            raise ValueError("OGC Tiles endpoint URL is missing in connection profile")
        cid, tms_id = split_tiles_dataset_id(dataset_id)
        base = self.url.rstrip("/")
        if cid != "_":
            tileset_url = f"{base}/collections/{cid}/tiles/{tms_id}"
        else:
            tileset_url = f"{base}/tiles/{tms_id}"
        try:
            doc = self._get_json(tileset_url)
        except DataFabricError as e:
            raise SourceUnreachableError(
                f"OGC Tiles tileset fetch failed for '{dataset_id}': {e}"
            ) from e

        tms_uri = doc.get("tileMatrixSetDefinition") or doc.get("tileMatrixSetURI")
        tms_doc = doc.get("tileMatrixSet") if isinstance(doc.get("tileMatrixSet"), dict) else None
        if tms_doc is None:
            # tileset 未内嵌 TMS → 跟随其 tiling-scheme 链接读服务自己的
            # TMS 声明（同源校验；读不到保持 unknown，不猜 zoom 范围）。
            tms_href = self._tiling_scheme_href(doc)
            if tms_href:
                from app.services.data_fabric.security import ensure_same_origin_url

                try:
                    safe_href = ensure_same_origin_url(tms_href, self.url)
                    tms_doc = self._get_json(safe_href)
                except DataFabricError as e:
                    logger.debug(
                        "[ogc-tiles] tiling-scheme fetch failed for '%s': %s", dataset_id, e
                    )
        zoom_levels = self._zoom_levels(tms_doc)
        tile_links = [
            lnk for lnk in doc.get("links", []) or []
            if isinstance(lnk, dict) and str(lnk.get("rel") or "") in _TILESET_LINK_RELS
        ]
        media_types = sorted({str(lnk.get("type")) for lnk in tile_links if lnk.get("type")})
        assessment = assess_tileset(
            tile_links=tile_links,
            tms_uri=str(tms_uri) if tms_uri else None,
            tms_id=tms_id,
            crs=doc.get("crs"),
            zoom_levels=zoom_levels,
            media_types_declared=media_types,
        )
        media_kind = self._media_kind(media_types)
        bbox = self._tileset_bbox(doc)
        return DatasetDescriptor(
            id=dataset_id,
            title=str(doc.get("title") or f"{dataset_id} tiles"),
            description="OGC API - Tiles tileset (display service; not a feature query source)",
            source_type="ogc_api_tiles",
            source_id=self.profile.id,
            geometry_type="tile_vector" if media_kind == "vector" else "tile_raster",
            feature_type="tile",
            data_type="tile",
            srs=str(doc["crs"]) if doc.get("crs") else None,
            bbox=bbox,
            feature_count=None,
            fields=[],
            metadata={
                "descriptor_version": DESCRIPTOR_VERSION,
                "protocol": "ogc_api_tiles",
                "collection_id": cid,
                "tile_matrix_set_id": tms_id,
                "tile_matrix_set_uri": str(tms_uri) if tms_uri else None,
                "tile_matrix_set_declared": bool(tms_doc),
                "zoom_levels": zoom_levels,
                "minzoom": min(zoom_levels) if zoom_levels else None,
                "maxzoom": max(zoom_levels) if zoom_levels else None,
                "media_types_declared": media_types,
                "media_kind": media_kind or "unknown",
                # 出目录的 URL 统一 redact（元数据真实性契约）。
                "tileset_url": DataFabricSecurity.redact_url(tileset_url),
                "tile_url_template": self._tile_template(doc, redacted=True),
                "tile_url_template_raw": self._tile_template(doc, redacted=False),
                "capabilities_conformance_declared": self._declares_tiles(),
                "capability": assessment.to_metadata(),
                "display_ready": assessment.state == CAP_SUPPORTED,
            },
        )

    def _zoom_levels(self, tms_doc: Optional[Dict[str, Any]]) -> List[int]:
        if not tms_doc:
            return []
        levels = []
        for m in tms_doc.get("tileMatrices", []) or []:
            if isinstance(m, dict):
                try:
                    levels.append(int(m.get("id")))
                except (TypeError, ValueError):
                    continue
        return sorted(levels)

    @staticmethod
    def _tiling_scheme_href(doc: Dict[str, Any]) -> Optional[str]:
        for link in doc.get("links", []) or []:
            if not isinstance(link, dict):
                continue
            if (
                str(link.get("rel") or "")
                == "http://www.opengis.net/def/rel/ogc/1.0/tiling-scheme"
                and link.get("href")
            ):
                return str(link["href"])
        return None

    def _tile_template(
        self, doc: Dict[str, Any], *, redacted: bool
    ) -> Optional[str]:
        for lnk in doc.get("links", []) or []:
            if not isinstance(lnk, dict):
                continue
            if str(lnk.get("rel") or "") in _TILESET_LINK_RELS and lnk.get("href"):
                href = str(lnk["href"])
                return DataFabricSecurity.redact_url(href) if redacted else href
        return None

    def _tileset_bbox(self, doc: Dict[str, Any]) -> Optional[List[float]]:
        bb = doc.get("boundingBox")
        if isinstance(bb, dict) and isinstance(bb.get("bbox"), list) and len(bb["bbox"]) == 4:
            try:
                return [float(v) for v in bb["bbox"]]
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _media_kind(media_types: List[str]) -> Optional[str]:
        if any(t == "application/vnd.mapbox-vector-tile" for t in media_types):
            return "vector"
        if any(t in ("image/png", "image/jpeg") for t in media_types):
            return "raster"
        if not media_types:
            return None
        return "unknown"

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """descriptor 级预览（不取瓦片字节 —— 瓦片获取走受控代理）。"""
        desc = self.describe(dataset_id)
        cap = desc.metadata.get("capability") or {}
        return {
            "schema": {
                "descriptor_version": DESCRIPTOR_VERSION,
                "tile_matrix_set": desc.metadata.get("tile_matrix_set_id"),
                "media_types": desc.metadata.get("media_types_declared"),
            },
            "properties": {
                "display_ready": desc.metadata.get("display_ready"),
                "capability_state": cap.get("state"),
                "minzoom": desc.metadata.get("minzoom"),
                "maxzoom": desc.metadata.get("maxzoom"),
            },
            "features": [],
            "bbox": desc.bbox or [-180.0, -90.0, 180.0, 90.0],
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        raise QueryUnsupportedError(
            "OGC API - Tiles is a display tile service, not a feature query "
            "source; use the governed tile proxy (display) or a coverage/"
            "materialization source (numeric raster analysis)",
            details={"dataset_id": dataset_id, "protocol": "ogc_api_tiles"},
        )

    def health(self) -> DataFabricHealth:
        import time as _time

        started = _time.time()
        if not self.url:
            return DataFabricHealth(
                status="unreachable",
                message="OGC Tiles URL missing",
                details={"hint": "Specify valid HTTP/HTTPS URL in connection profile."},
            )
        try:
            target = self.url.rstrip("/") + "/conformance"
            resp = self.session.get(target, timeout=5)
            latency = round((_time.time() - started) * 1000, 2)
            if resp.status_code == 200:
                return DataFabricHealth(
                    status="healthy",
                    message="OGC API Tiles service online and responsive",
                    details={
                        "declares_tiles": self._declares_tiles(),
                        "conformsTo": (resp.json().get("conformsTo", []) or [])[:5],
                    },
                    latency_ms=latency,
                )
            return DataFabricHealth(
                status="degraded",
                message=f"OGC API Tiles service responded with HTTP {resp.status_code}",
                details={"status_code": resp.status_code},
                latency_ms=latency,
            )
        except Exception as e:  # noqa: BLE001
            return DataFabricHealth(
                status="unreachable",
                message=f"OGC Tiles health check failed: {e}",
                details={"hint": f"Unable to reach {self.url}."},
                latency_ms=round((_time.time() - started) * 1000, 2),
            )


__all__ = [
    "DESCRIPTOR_VERSION",
    "OGCTilesAdapter",
    "split_tiles_dataset_id",
]
