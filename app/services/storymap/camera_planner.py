"""StoryMap 相机服务壳（ADR-0196 §5.1）——图层要素 bbox 扫描 + lib 委托。"""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from app.lib.storymap.camera_planner import (  # noqa: F401  (re-export)
    ARC_PITCH_BASE,
    CameraSample,
    build_camera_track,
    plan_camera_for_bbox,
    validate_track,
)


def bbox_from_geojson(feature_collection: Optional[Mapping[str, Any]]) -> Optional[List[float]]:
    """GeoJSON FeatureCollection → [w, s, e, n] 并集包围盒（空/非法 → None）。

    坐标扫描复用编译器的公共走子 ``iter_coord_points``（同源抗 schema 漂移，
    覆盖 Polygon/LineString 等全部顶点）。
    """
    from app.lib.storymap.story_compiler import iter_coord_points

    if not isinstance(feature_collection, Mapping):
        return None
    features = feature_collection.get("features")
    if not isinstance(features, list) or not features:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for feature in features:
        if not isinstance(feature, Mapping):
            continue
        for point in iter_coord_points(feature):
            xs.append(point[0])
            ys.append(point[1])
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


__all__ = [
    "ARC_PITCH_BASE",
    "CameraSample",
    "bbox_from_geojson",
    "build_camera_track",
    "plan_camera_for_bbox",
    "validate_track",
]
