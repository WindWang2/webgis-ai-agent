"""有界 release projection（WP2）——发布物化的唯一投影实现。

写时投影（物化落盘前）+ 读时二次过滤（reader 端点）= 纵深防御：
- 字段白名单：properties 只保留白名单键（白名单外的字段在物化文件里就
  不存在，撤销后残留文件也不含敏感字段）；
- 几何精度：坐标按 ``geometry_precision`` 钳制小数位（确定性）；
- bbox 裁剪：支持要素级粗滤（几何与窗口相交近似，不引入几何库依赖）。
  当前发布链未启用（调用恒 bbox=None，scope.bbox 如实为 null）——能力
  面向后续"按范围发布"，在启用前读者不会看到该语义；
- 预算：单层要素上限 + 快照总字节预算，超限 typed 阻断（不静默截断）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.publication.contract import (
    PublicationBlocker,
    PublicationBlockerReason,
)

#: 单层要素上限（超过 = 该层无法有界投影，typed 阻断）。
PUBLICATION_LAYER_FEATURE_LIMIT = 50_000
#: 快照物化总字节预算（GeoJSON 落盘累计；超过 = 整个发布阻断）。
PUBLICATION_TOTAL_BUDGET_BYTES = 24 * 1024 * 1024
#: 默认几何精度（小数位 ≈ 厘米级 @EPSG:3857；诚实标注在 scope）。
DEFAULT_GEOMETRY_PRECISION = 6

_PROP_LIMIT = 512
_STR_LIMIT = 2000


def _round_coord(value: Any, precision: int) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return round(value, precision)
    return value


def _clip_coords(coords: Any, precision: int) -> Any:
    """递归钳制坐标数组精度（Point→多维嵌套统一处理）。"""
    if isinstance(coords, (list, tuple)):
        if coords and isinstance(coords[0], (int, float)) and not isinstance(coords[0], bool):
            return [_round_coord(c, precision) for c in coords]
        return [_clip_coords(c, precision) for c in coords]
    return _round_coord(coords, precision)


def bbox_of_geometry(geometry: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    """要素几何的粗 bbox（不支持 GeometryCollection → None=不可判定）。"""
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")

    def _walk(node: Any) -> Optional[List[List[float]]]:
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)) and not isinstance(node[0], bool):
                return [list(node[:2]) if len(node) >= 2 else None]  # type: ignore[list-item]
            out: List[List[float]] = []
            for child in node:
                sub = _walk(child)
                if sub is None:
                    return None
                out.extend(sub)
            return out
        return None

    if geom_type == "GeometryCollection" or not isinstance(coords, (list, tuple)):
        return None
    pts = _walk(coords)
    if not pts:
        return None
    xs = [p[0] for p in pts if p]
    ys = [p[1] for p in pts if p and len(p) >= 2]
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_intersects(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def project_properties(
    properties: Optional[Dict[str, Any]],
    *,
    field_whitelist: Sequence[str],
) -> Dict[str, Any]:
    """字段白名单投影（读时二次过滤同用此函数 —— 单一实现）。"""
    if not isinstance(properties, dict):
        return {}
    projected: Dict[str, Any] = {}
    for key in field_whitelist:
        if key in properties:
            value = properties[key]
            if isinstance(value, str) and len(value) > _STR_LIMIT:
                value = value[:_STR_LIMIT]
            projected[key] = value
    # 白名单外字段一概不采（键名完全匹配才收；读路径不抛错，与写时投影一致）。
    return projected


def project_feature_collection(
    fc: Any,
    *,
    field_whitelist: Sequence[str],
    precision: int = DEFAULT_GEOMETRY_PRECISION,
    bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Tuple[Optional[Dict[str, Any]], PublicationBlocker | None]:
    """FeatureCollection → 投影后 FC（或 None）+ typed 阻断。

    返回 ``(projected_fc, blocker)``：成功时 blocker=None（含空结果 FC，
    空结果不是阻断 —— 层仍可发布，读者看到空层+计数 0）；超上限时
    fc=None + typed 阻断。
    """
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        return None, PublicationBlocker(
            reason=PublicationBlockerReason.SOURCE_DATA_UNAVAILABLE,
            message="层数据不是 FeatureCollection，无法安全投影",
        )
    features = fc.get("features")
    if not isinstance(features, list):
        features = []
    if len(features) > PUBLICATION_LAYER_FEATURE_LIMIT:
        return None, PublicationBlocker(
            reason=PublicationBlockerReason.SNAPSHOT_BUDGET_EXCEEDED,
            message=f"层要素数 {len(features)} 超过发布上限 {PUBLICATION_LAYER_FEATURE_LIMIT}",
        )
    out_features: List[Dict[str, Any]] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if bbox is not None and isinstance(geometry, dict):
            feature_bbox = bbox_of_geometry(geometry)
            if feature_bbox is not None and not bbox_intersects(feature_bbox, bbox):
                continue
        projected_feature: Dict[str, Any] = {
            "type": "Feature",
            "properties": project_properties(
                feature.get("properties"), field_whitelist=field_whitelist
            ),
        }
        fid = feature.get("id")
        if isinstance(fid, (str, int)) and not isinstance(fid, bool):
            projected_feature["id"] = fid
        if isinstance(geometry, dict) and isinstance(geometry.get("coordinates"), (list, tuple)):
            projected_feature["geometry"] = {
                "type": geometry.get("type"),
                "coordinates": _clip_coords(geometry.get("coordinates"), precision),
            }
        else:
            projected_feature["geometry"] = None
        out_features.append(projected_feature)
    return {"type": "FeatureCollection", "features": out_features}, None


def estimate_fc_bytes(fc: Dict[str, Any]) -> int:
    """粗估 FC 序列化字节数（预算会计；导入 json 放函数内避免模块重）。

    用 len(str()) 近似（ASCII 场景 ≈ UTF-8 上界；中文按 utf-8 编码真实计数）。
    """
    import json

    try:
        return len(json.dumps(fc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "PUBLICATION_LAYER_FEATURE_LIMIT",
    "PUBLICATION_TOTAL_BUDGET_BYTES",
    "DEFAULT_GEOMETRY_PRECISION",
    "project_properties",
    "project_feature_collection",
    "bbox_of_geometry",
    "bbox_intersects",
    "estimate_fc_bytes",
]
