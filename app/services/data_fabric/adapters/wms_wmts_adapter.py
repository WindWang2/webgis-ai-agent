"""
WMS & WMTS Raster Data Source Adapter
"""
import re
import time
import logging
from typing import Any, List, Dict, Optional, Tuple
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.security import (
    DataFabricSecurity,
    bounded_get,
    make_safe_session,
)
from app.services.data_fabric.metadata import normalize_crs
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)

logger = logging.getLogger(__name__)


def _local_name(tag: str) -> str:
    """剥掉 XML 命名空间取本地标签名（WMS 1.1/1.3 与 WMTS 的 ns 各不相同）。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


# 自由文本（异常 message 等）里内嵌的绝对 URL：scheme://非空白串。
_URL_LIKE_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://\S+")


def _redact_text(text: str) -> str:
    """对自由文本里内嵌的每个 URL 做 userinfo 脱敏后再落到 egress 字段。

    requests 的 HTTPError/ConnectionError message 携带完整请求 URL——若含
    ``user:pass@`` userinfo，直接拼进 notes/describe_error 就是凭证泄漏；
    复用模块统一的 redact_url 逐个剥掉 userinfo（非 URL 文本原样保留）。"""
    return _URL_LIKE_RE.sub(
        lambda m: DataFabricSecurity.redact_url(m.group(0)) or m.group(0), text
    )


def _ancestor_layers(layer_el: Any, parents: Dict[Any, Any]) -> List[Any]:
    """Layer → 根 的祖先链（ElementTree 无 parent 指针，用 parent map）。"""
    chain: List[Any] = []
    node = layer_el
    while node is not None:
        chain.append(node)
        node = parents.get(node)
    return chain


def _crs_at_level(layer: Any) -> List[str]:
    """单个 Layer 元素上直接声明的 CRS/SRS 列表（WMS 1.3.0 用 <CRS>，
    1.1.x 用 <SRS>，且 SRS 文本允许空白/逗号分隔多个）。"""
    values: List[str] = []
    for child in layer:
        name = _local_name(child.tag)
        if name in ("CRS", "SRS"):
            text = (child.text or "").replace(",", " ")
            values.extend(v for v in (t.strip() for t in text.split()) if v)
    return values


def _bbox_at_level(layer: Any) -> Optional[List[float]]:
    """单个 Layer 元素上直接声明的地理 bbox（WGS84 经纬度 [w, s, e, n]）。

    兼容三种常见编码：WMS 1.3.0 ``EX_GeographicBoundingBox``（子元素经纬度）、
    WMS 1.1.x ``LatLonBoundingBox``（minx/miny/maxx/maxy 属性）、
    WMTS 1.0.0 ``WGS84BoundingBox``（LowerCorner/UpperCorner 文本）。
    解析不了返回 None，绝不猜。"""
    for child in layer:
        name = _local_name(child.tag)
        if name.endswith("GeographicBoundingBox"):
            vals = {
                _local_name(sub.tag).lower(): (sub.text or "").strip() for sub in child
            }
            try:
                w = float(vals["westboundlongitude"])
                e = float(vals["eastboundlongitude"])
                s = float(vals["southboundlatitude"])
                n = float(vals["northboundlatitude"])
            except (KeyError, TypeError, ValueError):
                return None
            return [w, s, e, n]
        if name == "LatLonBoundingBox":
            attrs = {k.lower(): v for k, v in child.attrib.items()}
            try:
                w = float(attrs["minx"])
                s = float(attrs["miny"])
                e = float(attrs["maxx"])
                n = float(attrs["maxy"])
            except (KeyError, TypeError, ValueError):
                return None
            return [w, s, e, n]
        if name == "WGS84BoundingBox":
            corners = {
                _local_name(sub.tag).lower(): (sub.text or "").strip() for sub in child
            }
            try:
                lower = [float(v) for v in corners["lowercorner"].split()]
                upper = [float(v) for v in corners["uppercorner"].split()]
            except (KeyError, TypeError, ValueError):
                return None
            if len(lower) < 2 or len(upper) < 2:
                return None
            return [lower[0], lower[1], upper[0], upper[1]]
    return None


def _is_plausible_geographic_bbox(bbox: List[float]) -> bool:
    """地理 bbox 语义校验：WGS84 经纬度范围 + 角序正确（反经线跨越等
    非常规编码诚实拒绝，不猜语义）。"""
    w, s, e, n = bbox
    if w > e or s > n:
        return False
    return (
        -180.0 <= w <= 180.0 and -180.0 <= e <= 180.0
        and -90.0 <= s <= 90.0 and -90.0 <= n <= 90.0
    )


def _extract_layer_crs_and_bbox(
    root: Any, dataset_id: str
) -> Tuple[List[str], Optional[List[float]], List[str]]:
    """在 capabilities 树中定位指定 Layer，提取 (advertised_crs, bbox, notes)。

    WMS 的 CRS 与地理 bbox 均可从祖先 Layer 继承：取祖先链上**最近**一级
    的声明。任何一项拿不到都以诚实空值 + note 返回，绝不伪造默认值。"""
    notes: List[str] = []
    layer_el = None
    for elem in root.iter():
        if _local_name(elem.tag) != "Layer":
            continue
        for child in elem:
            if _local_name(child.tag) in ("Name", "Identifier"):
                if (child.text or "").strip() == dataset_id:
                    layer_el = elem
                    break
        if layer_el is not None:
            break

    if layer_el is None:
        notes.append(
            f"layer '{dataset_id}' not found in GetCapabilities; CRS/bbox unknown"
        )
        return [], None, notes

    parents = {child: parent for parent in root.iter() for child in parent}
    advertised_crs: List[str] = []
    bbox: Optional[List[float]] = None
    for layer in _ancestor_layers(layer_el, parents):
        if _local_name(layer.tag) != "Layer":
            continue
        if not advertised_crs:
            advertised_crs = _crs_at_level(layer)
        if bbox is None:
            candidate = _bbox_at_level(layer)
            if candidate is not None:
                if _is_plausible_geographic_bbox(candidate):
                    bbox = candidate
                else:
                    notes.append(
                        "declared geographic bbox is malformed or outside WGS84 "
                        f"range ({candidate}); ignored"
                    )
        if advertised_crs and bbox is not None:
            break

    if not advertised_crs:
        notes.append(
            "no CRS/SRS declared on the layer chain; srs left unknown "
            "(never silently assumed)"
        )
    if bbox is None:
        notes.append(
            "no GeographicBoundingBox/LatLonBoundingBox/WGS84BoundingBox on the "
            "layer chain; bbox left unknown (never a fabricated world extent)"
        )
    return advertised_crs, bbox, notes


class WMSWMTSAdapter(GeospatialDataSourceAdapter):
    """
    Concrete Data Fabric adapter for OGC WMS (Web Map Service) and WMTS (Web Map Tile Service).
    Parses GetCapabilities XML safely, provides raster tile source descriptors and metadata.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        self.raw_url = self.profile.url or ""
        self.url = (
            DataFabricSecurity.validate_url(self.raw_url, allow_private=self.profile.allow_private)
            if self.raw_url
            else ""
        )
        self.service_type = self.profile.source_type.lower()
        self.options = self.profile.options or {}
        self.session = make_safe_session(allow_private=self.profile.allow_private)

    def probe(self) -> bool:
        """Lightweight WMS/WMTS GetCapabilities probe（有界下载，禁裸 resp.content）。"""
        if not self.url:
            return False
        try:
            service = "WMTS" if "wmts" in self.service_type else "WMS"
            params = {
                "SERVICE": service,
                "REQUEST": "GetCapabilities",
                "VERSION": "1.3.0" if service == "WMS" else "1.0.0",
            }
            bounded_get(self.session, self.url, params=params, timeout=5)
            return True
        except Exception as e:
            logger.debug(
                "WMS/WMTS probe failed for %s: %s",
                DataFabricSecurity.redact_url(self.url),
                _redact_text(str(e)),
            )
            return False

    def capabilities(self) -> List[str]:
        """List WMS/WMTS adapter capabilities."""
        return [
            "raster_tile",
            "wms",
            "wmts",
            "ogc_standard",
        ]

    def list_datasets(self) -> List[Dict[str, Any]]:
        """Discover available layers from WMS/WMTS GetCapabilities."""
        if not self.url:
            return []
        try:
            service = "WMTS" if "wmts" in self.service_type else "WMS"
            params = {
                "SERVICE": service,
                "REQUEST": "GetCapabilities",
            }
            body = bounded_get(self.session, self.url, params=params, timeout=10)
            tree = DataFabricSecurity.parse_safe_xml(body)
            datasets = []

            for elem in tree.iter():
                tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag_name == "Layer":
                    layer_name = ""
                    layer_title = ""
                    for child in elem:
                        child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_tag == "Name" or child_tag == "Identifier":
                            layer_name = (child.text or "").strip()
                        elif child_tag == "Title":
                            layer_title = (child.text or "").strip()

                    if layer_name:
                        datasets.append({
                            "id": layer_name,
                            "title": layer_title or layer_name,
                            "source_type": self.service_type,
                            "geometry_type": "Raster",
                        })

            return datasets
        except Exception as e:
            logger.warning(
                "WMS/WMTS list_datasets failed for %s: %s",
                DataFabricSecurity.redact_url(self.url),
                _redact_text(str(e)),
            )
            return []

    def _get_capabilities_tree(self, notes: List[str]) -> Optional[Any]:
        """拉取并安全解析 GetCapabilities；失败记入 notes 并返回 None
        （describe 绝不因能力文档缺失而整体失败，只做诚实降级）。"""
        if not self.url:
            notes.append("no service URL configured")
            return None
        try:
            service = "WMTS" if "wmts" in self.service_type else "WMS"
            params = {"SERVICE": service, "REQUEST": "GetCapabilities"}
            body = bounded_get(self.session, self.url, params=params, timeout=10)
            return DataFabricSecurity.parse_safe_xml(body)
        except Exception as e:  # noqa: BLE001 — 降级为未知，不带假值继续
            # 异常 message 可能携带含 user:pass@ 的完整请求 URL；先脱敏再进
            # notes/describe_error（egress 字段绝不回显未脱敏 URL）。
            logger.warning(
                "WMS/WMTS GetCapabilities failed for %s: %s",
                DataFabricSecurity.redact_url(self.url),
                _redact_text(str(e)),
            )
            notes.append(f"GetCapabilities fetch/parse failed: {_redact_text(str(e))}")
            return None

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch DatasetDescriptor for a WMS/WMTS raster layer.

        CRS 诚实性（项目红线「never silently assume CRS」）：srs/bbox 只来自
        GetCapabilities 的显式声明（Layer 链的 CRS/SRS 元素与地理 bbox，含
        继承），拿不到就返回 None/空列表并在 metadata.notes 说明——绝不伪造
        EPSG:3857 或全球 extent。bbox 恒为 WGS84 经纬度 [w, s, e, n]（来源
        见 metadata.bbox_crs）。"""
        notes: List[str] = []
        advertised_crs: List[str] = []
        bbox: Optional[List[float]] = None
        service_version = ""

        root = self._get_capabilities_tree(notes)
        if root is not None:
            service_version = root.get("version") or ""
            advertised_crs, bbox, layer_notes = _extract_layer_crs_and_bbox(
                root, dataset_id
            )
            notes.extend(layer_notes)

        # 规范化（EPSG:XXXX / URN / CRS84 → 规范名，去重保序）。
        normalized: List[str] = []
        for raw in advertised_crs:
            name = normalize_crs(raw)
            if name and name not in normalized:
                normalized.append(name)
        # 主 srs：地理 bbox 是 WGS84，优先取 4326/CRS84；否则取第一个已声明项。
        primary = next(
            (c for c in normalized if c in ("EPSG:4326", "CRS84")),
            normalized[0] if normalized else None,
        )

        safe_url = DataFabricSecurity.redact_url(self.url)
        metadata: Dict[str, Any] = {
            "endpoint_url": safe_url,
            "layer": dataset_id,
            "tile_url_template": (
                f"{safe_url}?SERVICE={self.service_type.upper()}&REQUEST=GetMap"
                f"&LAYERS={dataset_id}&FORMAT=image/png"
            ),
            # 附加（additive）键：原 keys 不动。
            "advertised_crs": advertised_crs,
            "normalized_crs": normalized,
            "bbox_crs": "EPSG:4326" if bbox is not None else None,
            "notes": notes,
        }
        if any("GetCapabilities" in n for n in notes):
            metadata["describe_error"] = "; ".join(notes)
        # WMS 1.3.0 + EPSG:4326 轴序诚实性（additive note，不重排存储数组：
        # 消费方依赖 [w, s, e, n]）：存储恒为 lon,lat，而 1.3.0 GetMap 的
        # BBOX 参数要求轴序一致（lat,lon）——显式说明差异，不静默误导。
        if service_version.startswith("1.3") and "EPSG:4326" in normalized:
            metadata["axis_order_note"] = (
                "stored bbox is minx,miny,maxx,maxy (lon,lat, WGS84); WMS 1.3.0 "
                "GetMap requires an axis-conformant BBOX (lat,lon) for EPSG:4326"
            )

        return DatasetDescriptor(
            id=dataset_id,
            title=dataset_id,
            description=f"{self.service_type.upper()} Raster Layer {dataset_id}",
            source_type=self.service_type,
            geometry_type="Raster",
            srs=primary,
            bbox=bbox,
            feature_count=None,
            fields=[],
            metadata=metadata,
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch bounded raster metadata preview.

        bbox 诚实性（与 describe() 同一红线）：预览路径不做 GetCapabilities
        往返，extent 未知 → 恒为 None + 说明 note（放在既有 properties 里，
        不新增顶层 shape key），绝不伪造全球 bbox。"""
        return {
            "schema": {"layer": dataset_id, "type": "Raster"},
            "properties": {
                "layer_name": dataset_id,
                "endpoint": DataFabricSecurity.redact_url(self.url),
                "bbox_note": (
                    "layer extent unknown without GetCapabilities; "
                    "no fabricated world bbox"
                ),
            },
            "features": [],
            "bbox": None,
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """WMS/WMTS does not support vector feature queries; returns raster metadata descriptor.

        CRS/bbox 诚实性：GetMap URL 的 CRS/BBOX 只取 describe() 从
        capabilities 读到的声明值（bbox 已知则带上）；未知则整个省略参数
        （服务器默认生效）并在 metadata 里如实说明——绝不硬编码
        EPSG:3857 或全球 extent。既有 metadata keys 全部保留。"""
        desc_meta: Dict[str, Any] = {}
        described_bbox: Optional[List[float]] = None
        crs: Optional[str] = None
        try:
            desc = self.describe(dataset_id)
            desc_meta = desc.metadata or {}
            described_bbox = desc.bbox
            crs = desc.srs
        except Exception as e:  # noqa: BLE001 — 查询面不因 describe 失败而炸
            logger.warning(
                "WMS/WMTS query describe fallback for %s: %s",
                DataFabricSecurity.redact_url(self.url),
                _redact_text(str(e)),
            )

        base = (
            f"{DataFabricSecurity.redact_url(self.url)}"
            f"?SERVICE=WMS&REQUEST=GetMap&LAYERS={dataset_id}&STYLES="
        )
        if crs:
            base += f"&CRS={crs}"
        if described_bbox is not None:
            w, s, e, n = described_bbox
            base += f"&BBOX={w},{s},{e},{n}"
        base += "&WIDTH=256&HEIGHT=256&FORMAT=image/png"

        metadata: Dict[str, Any] = {
            "getmap_url": base,
            "tile_url": base,
            "pushdown_bbox": bool(query_spec.bbox),
            # 附加（additive）键：原 keys 不动。
            "crs": crs,
        }
        if crs is None:
            metadata["crs_note"] = "advertised crs unknown; CRS parameter omitted"
        if described_bbox is None:
            metadata["bbox_note"] = "layer extent unknown; BBOX parameter omitted"
        axis_note = desc_meta.get("axis_order_note")
        if axis_note:
            metadata["axis_order_note"] = axis_note

        return QueryResult(
            dataset_id=dataset_id,
            features=[],
            total_count=0,
            schema_info={"geometry_type": "Raster", "layer": dataset_id},
            metadata=metadata,
        )

    def health(self) -> DataFabricHealth:
        """Diagnostic health check for WMS/WMTS endpoint."""
        start_time = time.time()
        try:
            ok = self.probe()
            latency = round((time.time() - start_time) * 1000, 2)
            if ok:
                return DataFabricHealth(
                    status="healthy",
                    message=f"{self.service_type.upper()} raster service responsive",
                    latency_ms=latency,
                )
            return DataFabricHealth(
                status="unreachable",
                message=f"{self.service_type.upper()} probe failed",
                latency_ms=latency,
            )
        except Exception as e:
            latency = round((time.time() - start_time) * 1000, 2)
            return DataFabricHealth(
                status="unreachable",
                message=f"{self.service_type.upper()} health check error: {e}",
                latency_ms=latency,
            )
