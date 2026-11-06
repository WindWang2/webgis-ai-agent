"""OGC 开放服务三态能力评估（GOAL-02 / M1）。

封闭语义（绝不宣称完整协议支持）：

- ``supported``：服务**自己声明**了该能力（tileset 链接/媒体类型/CRS/
  TileMatrixSet、coverage 的 domainset/rangetype/格式链接），且在本仓
  消费子集内（WebMercatorQuad 显示路径、COVJSON/GeoTIFF 读取路径）；
- ``unsupported``：服务声明了互斥事实（如非 WebMercatorQuad 的已知
  TMS、声明了但本仓不支持的媒体类型/格式）；
- ``unknown``：关键声明缺失（无 tile 链接、TMS URI 无法识别、CRS 未
  声明）—— 诚实拒绝推进，绝不猜投影/格式。

reason code 为封闭枚举（agent 可解释、测试可断言）；message 为人类
可读补充。评估**只读服务声明**，不发探测性写请求。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

#: 三态字面量（对外契约，勿改值）。
CAP_SUPPORTED = "supported"
CAP_UNSUPPORTED = "unsupported"
CAP_UNKNOWN = "unknown"

# ── reason codes（封闭枚举；新增必须同步 TEST_MATRIX 与文档） ────────────
RC_NO_TILE_LINKS = "no_tile_links"
RC_MEDIA_TYPE_UNSUPPORTED = "media_type_unsupported"
RC_MEDIA_TYPE_NOT_DECLARED = "media_type_not_declared"
RC_TMS_UNRECOGNIZED = "tms_unrecognized"
RC_TMS_NOT_DISPLAYABLE = "tms_not_displayable"
RC_CRS_NOT_DECLARED = "crs_not_declared"
RC_CRS_NOT_WEBMERCATOR = "crs_not_webmercator"
RC_ZOOM_RANGE_UNKNOWN = "zoom_range_unknown"

RC_NO_COVERAGE_ENDPOINT = "no_coverage_endpoint"
RC_DOMAINSET_MISSING = "domainset_missing"
RC_RANGETYPE_MISSING = "rangetype_missing"
RC_COVERAGE_FORMAT_NOT_DECLARED = "coverage_format_not_declared"
RC_COVERAGE_FORMAT_UNSUPPORTED = "coverage_format_unsupported"
RC_GRID_AXES_INSUFFICIENT = "grid_axes_insufficient"

#: 显示路径支持的瓦片媒体类型（封闭子集）。
MEDIA_TYPE_MVT = "application/vnd.mapbox-vector-tile"
MEDIA_TYPE_PNG = "image/png"
MEDIA_TYPE_JPEG = "image/jpeg"
SUPPORTED_TILE_MEDIA_TYPES = frozenset({MEDIA_TYPE_MVT, MEDIA_TYPE_PNG, MEDIA_TYPE_JPEG})

#: WebMercatorQuad 同族 URI（OGC 2D Tile Matrix Set 注册表 + 历史别名）。
#: 只有这一族进入显示路径 —— 与 XYZ/MapLibre 语义一致，绝不猜投影。
WEBMERCATOR_TMS_URIS = frozenset({
    "http://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad",
    "https://www.opengis.net/def/tilematrixset/OGC/1.0/WebMercatorQuad",
    "urn:ogc:def:tileMatrixSet:OGC:1.0:WebMercatorQuad",
    # GoogleMapsCompatible 是 WebMercatorQuad 的历史同族（EPSG:3857、
    # 256px、topLeft）—— OGCTileMatrixSet 注册表将其标注为 deprecated
    # 的 WebMercatorQuad 前身，语义等价。
    "http://www.opengis.net/def/tilematrixset/OGC/1.0/GoogleMapsCompatible",
    "https://www.opengis.net/def/tilematrixset/OGC/1.0/GoogleMapsCompatible",
    "urn:ogc:def:tileMatrixSet:OGC:1.0:GoogleMapsCompatible",
})

#: 其他**已知但不支持显示**的注册 TMS（诚实 unsupported，而非 unknown）。
KNOWN_NON_DISPLAYABLE_TMS_URIS = frozenset({
    "http://www.opengis.net/def/tilematrixset/OGC/1.0/WorldCRS84Quad",
    "https://www.opengis.net/def/tilematrixset/OGC/1.0/WorldCRS84Quad",
    "urn:ogc:def:tileMatrixSet:OGC:1.0:WorldCRS84Quad",
    "http://www.opengis.net/def/tilematrixset/OGC/1.0/UTM31WGS84Quad",
    "https://www.opengis.net/def/tilematrixset/OGC/1.0/UTM31WGS84Quad",
    "urn:ogc:def:tileMatrixSet:OGC:1.0:UTM31WGS84Quad",
})

#: WebMercatorQuad 注册定义（OGC 17-083r2 Annex E.4；z=0..29，此处只
#: 收敛为锚点常量供校验，不内置全量表 —— 显示路径以服务声明的 TMS
#: JSON / 注册 URI 为准，本表仅做 URI 识别与显示资格判定）。
_WEBMERCATOR_TMS_ID_ALIASES = frozenset({"webmercatorquad", "googlemapscompatible"})

#: WebMercatorQuad 家族的 EPSG:3857 CRS URI 变体（统一小写比较）。
_WEBMERCATOR_CRS_URIS = frozenset({
    "http://www.opengis.net/def/crs/epsg/0/3857",
    "https://www.opengis.net/def/crs/epsg/0/3857",
    "urn:ogc:def:crs:epsg::3857",
    "urn:ogc:def:crs:epsg:6.18:3:3857",
    "epsg:3857",
})


def tms_display_state(tms_uri_or_id: Optional[str]) -> str:
    """注册 TMS URI/id → 显示资格三态（只识别，不发请求）。"""
    if not tms_uri_or_id:
        return CAP_UNKNOWN
    key = str(tms_uri_or_id).strip()
    if key in WEBMERCATOR_TMS_URIS:
        return CAP_SUPPORTED
    if key in KNOWN_NON_DISPLAYABLE_TMS_URIS:
        return CAP_UNSUPPORTED
    # id 形态（tileset 的 TileMatrixSet id 字段，非 URI）
    if key.lower() in _WEBMERCATOR_TMS_ID_ALIASES:
        return CAP_SUPPORTED
    return CAP_UNKNOWN


def is_webmercator_crs(crs_uri: Optional[str]) -> Optional[bool]:
    """声明 CRS → WebMercator 族判定；未声明返回 None（unknown）。"""
    if not crs_uri:
        return None
    return str(crs_uri).strip().lower() in _WEBMERCATOR_CRS_URIS


@dataclass
class CapabilityAssessment:
    """一次能力评估的封闭结果（state + 封闭 reason code + 人读消息）。"""

    state: str
    reasons: List[Dict[str, str]] = field(default_factory=list)

    def add(self, code: str, message: str) -> None:
        self.reasons.append({"code": code, "message": message})

    def worst(self, other: "CapabilityAssessment") -> "CapabilityAssessment":
        """合并两个评估：unsupported > unknown > supported。"""
        order = {CAP_SUPPORTED: 0, CAP_UNKNOWN: 1, CAP_UNSUPPORTED: 2}
        merged = CapabilityAssessment(
            state=self.state if order[self.state] >= order[other.state] else other.state,
            reasons=self.reasons + other.reasons,
        )
        return merged

    def to_metadata(self) -> Dict[str, object]:
        return {"state": self.state, "reasons": list(self.reasons)}


def assess_tileset(
    *,
    tile_links: Optional[List[Dict[str, object]]],
    tms_uri: Optional[str],
    tms_id: Optional[str],
    crs: Optional[str],
    zoom_levels: Optional[List[int]],
    media_types_declared: Optional[List[str]],
) -> CapabilityAssessment:
    """tileset 声明 → 显示路径资格（三态 + reason code 封闭枚举）。

    输入全部来自 tileset 文档自身（服务声明），不做网络探测。
    """
    a = CapabilityAssessment(state=CAP_SUPPORTED)

    if not tile_links:
        a.state = CAP_UNKNOWN
        a.add(RC_NO_TILE_LINKS, "tileset 未声明任何 rel=tile 瓦片模板链接")
        return a

    declared = [str(t) for t in (media_types_declared or []) if t]
    supported = [t for t in declared if t in SUPPORTED_TILE_MEDIA_TYPES]
    if declared and not supported:
        a.state = CAP_UNSUPPORTED
        a.add(
            RC_MEDIA_TYPE_UNSUPPORTED,
            f"tileset 声明的媒体类型均不在支持子集内: {sorted(declared)}",
        )
        return a
    if not declared:
        a.state = CAP_UNKNOWN
        a.add(RC_MEDIA_TYPE_NOT_DECLARED, "瓦片链接未声明媒体类型")

    tms_state = tms_display_state(tms_uri) if tms_uri else tms_display_state(tms_id)
    if tms_state == CAP_UNSUPPORTED:
        a.state = CAP_UNSUPPORTED
        a.add(RC_TMS_NOT_DISPLAYABLE, f"TileMatrixSet 不在显示路径（仅 WebMercatorQuad 族）: {tms_uri or tms_id}")
    elif tms_state == CAP_UNKNOWN and (tms_uri or tms_id):
        a.state = CAP_UNKNOWN
        a.add(RC_TMS_UNRECOGNIZED, f"无法识别的 TileMatrixSet 标识: {tms_uri or tms_id}")
    if not tms_uri and not tms_id:
        a.state = CAP_UNKNOWN
        a.add(RC_TMS_UNRECOGNIZED, "tileset 未声明 TileMatrixSet")

    crs_merc = is_webmercator_crs(crs)
    if crs_merc is None:
        a.state = CAP_UNKNOWN if a.state == CAP_SUPPORTED else a.state
        a.add(RC_CRS_NOT_DECLARED, "tileset 未声明 CRS —— 不猜测投影")
    elif crs_merc is False:
        a.state = CAP_UNSUPPORTED
        a.add(RC_CRS_NOT_WEBMERCATOR, f"tileset CRS 非 WebMercator 族: {crs}")

    if not zoom_levels:
        if a.state == CAP_SUPPORTED:
            a.state = CAP_UNKNOWN
        a.add(RC_ZOOM_RANGE_UNKNOWN, "TileMatrixSet 缩放级别未知")

    return a


def assess_coverage(
    *,
    has_coverage_endpoint: bool,
    has_domainset: bool,
    has_rangetype: bool,
    grid_axis_names: Optional[List[str]],
    declared_formats: Optional[List[str]],
) -> CapabilityAssessment:
    """coverage 声明 → 数值读取资格（三态；格式以服务声明为准）。"""
    a = CapabilityAssessment(state=CAP_SUPPORTED)
    if not has_coverage_endpoint:
        a.state = CAP_UNSUPPORTED
        a.add(RC_NO_COVERAGE_ENDPOINT, "集合未声明 coverage 数据端点")
        return a
    if not has_domainset:
        a.state = CAP_UNKNOWN
        a.add(RC_DOMAINSET_MISSING, "domainset 不可用 —— 网格几何未知，不猜")
    if not has_rangetype:
        a.state = CAP_UNKNOWN
        a.add(RC_RANGETYPE_MISSING, "rangetype 不可用 —— 波段/ nodata 未知，不猜")
    axes = grid_axis_names or []
    if len([n for n in axes if n]) < 2:
        if a.state == CAP_SUPPORTED:
            a.state = CAP_UNKNOWN
        a.add(RC_GRID_AXES_INSUFFICIENT, f"网格轴不足 2 维: {axes}")
    formats = [str(f) for f in (declared_formats or []) if f]
    supported_set = {normalize_coverage_format(f) for f in SUPPORTED_COVERAGE_FORMATS}
    supported = [f for f in formats if normalize_coverage_format(f) in supported_set]
    if formats and not supported:
        a.state = CAP_UNSUPPORTED
        a.add(RC_COVERAGE_FORMAT_UNSUPPORTED, f"声明的 coverage 格式均不支持: {sorted(formats)}")
    elif not formats:
        if a.state == CAP_SUPPORTED:
            a.state = CAP_UNKNOWN
        a.add(RC_COVERAGE_FORMAT_NOT_DECLARED, "未声明可用 coverage 输出格式")
    return a


#: Coverage 受控读取支持的输出格式（封闭子集，以服务声明为准）。
SUPPORTED_COVERAGE_FORMATS = frozenset({
    "application/prs.coverage+json",
    "image/tiff; application=geotiff",
    "image/tiff; application=geotiff; profile=cloud-optimized",
    "image/tiff",
})


def normalize_coverage_format(fmt: str) -> str:
    """格式串规范化（大小写无关 + media type 参数序无关的判定键）。

    ``image/tiff; application=geotiff; profile=cloud-optimized`` 与参数换序
    变体归一到同一键（R1 P2：仅小写化曾把换序声明误判不支持）。
    """
    raw = str(fmt or "").strip().lower()
    if ";" not in raw:
        return raw
    head, _, tail = raw.partition(";")
    params = sorted(p.strip() for p in tail.split(";") if p.strip())
    return ";".join([head.strip()] + params)
