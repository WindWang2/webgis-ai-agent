"""Geometry & Scale Semantics —— 几何族 × 显示尺度兼容提示（ADR-0204 S5）。

回答"这份数据在这个几何/数量下怎么显示才对"：

- 高密度点层（要素数超阈值）→ 聚合/聚类提示（逐点渲染不可读）；
- 面要素过多 → 标注密度提示；
- multipart 几何 → 面积/长度双计提示（centroid-is-not-feature 语义：
  质心不代表 multipart 要素本身，聚合前须按要素而非质心）；
- 栅格像元分辨率（单位随 CRS）→ 与目标缩放的兼容提示；
- centroid 语义：点族数据作面分析输入 → 提示质心近似口径。

全部纯函数、有界输出、零 IO（证据只来自 DatasetProfile）；提示是
**建议性证据**（写入 layer_meta.display_hints / qualification checks），
不改变渲染权威（MapSpec 仍是 desired state 唯一权威）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.gis.dataset_profile import DatasetProfile

#: 高密度点层阈值（超过 → 聚类提示；与 symbology 密度 k 下修口径互补）。
HIGH_DENSITY_POINTS = 5000
#: 面要素标注阈值（超过 → 建议缩放门控标注）。
POLYGON_LABEL_CAP = 400
#: 栅格像元数阈值（超过 → 渲染金字塔/降采样提示）。
RASTER_PIXEL_CAP = 40_000_000


def display_hints(
    profile: Optional[DatasetProfile],
    *,
    feature_count: Optional[int] = None,
) -> Dict[str, Any]:
    """DatasetProfile → 有界 display hints dict（纯函数，零扫描）。"""
    out: Dict[str, Any] = {"hints": [], "geometry_family": "unknown"}
    if profile is None:
        return out
    hints: List[Dict[str, str]] = []
    family = profile.geometry_kind
    out["geometry_family"] = family
    count = feature_count if isinstance(feature_count, int) else profile.feature_count

    multipart = [g for g in profile.geometry_types if str(g).startswith("Multi")]
    if multipart:
        hints.append({
            "code": "MULTIPART_GEOMETRY",
            "detail": f"含 multipart 几何（{','.join(multipart[:3])}）——"
                      "聚合/统计须按要素整体而非质心；质心不代表 multipart 要素",
        })

    if family == "point" and isinstance(count, int) and count > HIGH_DENSITY_POINTS:
        hints.append({
            "code": "HIGH_DENSITY_POINTS",
            "detail": f"点要素 {count} 超过高密度阈值 {HIGH_DENSITY_POINTS}"
                      "——建议聚合/热力/聚类表达，避免逐点渲染",
        })
    if family == "polygon" and isinstance(count, int) and count > POLYGON_LABEL_CAP:
        hints.append({
            "code": "POLYGON_LABEL_DENSITY",
            "detail": f"面要素 {count} 超过标注阈值 {POLYGON_LABEL_CAP}"
                      "——建议按缩放级别门控标注",
        })
    if family == "point" and profile.geometry_types and any(
        "Polygon" in g for g in profile.geometry_types
    ):
        hints.append({
            "code": "MIXED_GEOMETRY_FAMILY",
            "detail": "点面混合几何——面统计口径不适用于点要素（质心近似需披露）",
        })

    raster = profile.raster
    if raster is not None and raster.pixel_size:
        pixels = None
        if raster.width and raster.height:
            pixels = int(raster.width) * int(raster.height)
        detail = f"栅格像元尺寸 {raster.pixel_size}（单位随 CRS: {profile.crs or '未知'}）"
        if pixels and pixels > RASTER_PIXEL_CAP:
            detail += f"；总像元 {pixels} 超阈值——建议金字塔/降采样渲染"
        hints.append({"code": "RASTER_RESOLUTION", "detail": detail})

    if hints:
        out["hints"] = hints[:8]
    return out
