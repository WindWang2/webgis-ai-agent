"""ModelOps 输出 artifact 写入与发布（ADR-0119 §4 接缝表）。

产物通道（R1-M2 落定）：

- 分类栅格/置信度栅格/时序栈 → GeoTIFF（GTiff driver 平铺写）→
  ``publish_cog_data_object``（kind=cog_raster，renderable）；
- 检测/实例/评估/manifest JSON → ``publish_data_object``
  （kind=modelops_artifact，本 Epic 显式新增的 kind 成员）。

owner scope 恰好一维（session_id/project_id），跨 owner 不可见。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def write_raster_output(
    path: Path,
    *,
    arrays: Sequence[np.ndarray],
    band_names: Sequence[str],
    template: Any,           # rasterio dataset / RasterReader（取 crs/transform/shape）
    nodata: float = 255.0,
    dtype: str = "uint8",
    window_origin: Optional[Tuple[int, int]] = None,  # (x0, y0) 窗口原点（R1-M2）
) -> Path:
    """栅格产物写盘（GTiff，平铺块结构；CRS/transform 继承源）。

    ``window_origin``：数组是源栅格的子窗口时，transform 平移到窗口原点
    ——否则产物 georef 错位（renderable 产物放错地理位置）。
    """
    import rasterio

    if len(arrays) != len(band_names):
        raise ValueError("arrays/band_names length mismatch")
    src = template.dataset if hasattr(template, "dataset") else template
    height, width = arrays[0].shape
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": len(arrays),
        "dtype": dtype,
        "crs": src.crs,
        # R1-M2：子窗口产物 transform 平移到窗口原点（否则 georef 错位）。
        "transform": (
            src.transform * __import__("affine").Affine.translation(*window_origin)
            if window_origin is not None
            else src.transform
        ),
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        for i, (arr, name) in enumerate(zip(arrays, band_names), start=1):
            dst.write(arr.astype(dtype), i)
            dst.set_band_description(i, name)
    return path


def write_geojson_output(path: Path, geojson: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")
    return path


def publish_raster_artifact(
    path: Path,
    *,
    owner_scope: Dict[str, str],
    source_refs: List[str],
    producer: Dict[str, Any],
) -> Dict[str, Any]:
    """GeoTIFF → cog_raster DataObject（renderable + merkle 身份）。"""
    from app.services.lakehouse.raster_object import publish_cog_data_object

    result = publish_cog_data_object(
        path,
        session_id=owner_scope.get("session_id"),
        project_id=owner_scope.get("project_id"),
        source_refs=source_refs,
        producer=producer,
    )
    if not result.get("published"):
        # 诚实降级：产物仍在磁盘（路径返回），但无 DataObject 身份。
        logger.warning("raster artifact publish skipped: %s", result.get("reason"))
    return result


def publish_json_artifact(
    path: Path,
    *,
    owner_scope: Dict[str, str],
    source_refs: List[str],
    producer: Dict[str, Any],
) -> Dict[str, Any]:
    """JSON 产物 → modelops_artifact DataObject（检测/实例/评估/manifest）。"""
    from app.services.lakehouse.data_object import publish_data_object

    identity = publish_data_object(
        {path.name: path},
        kind="modelops_artifact",
        owner_scope=owner_scope,
        payload={"artifact_type": path.stem, "media_type": "application/json"},
        producer=producer,
        source_refs=source_refs,
    )
    return {
        "published": True,
        "data_object_id": identity.data_object_id,
        "content_sha256": identity.content_sha256,
        "manifest": identity.manifest_location,
        "path": str(path),
    }


def build_geojson_from_detections(
    detections: List[Dict[str, Any]],
    *,
    crs: Optional[str],
    transform: Any,
    class_names: Optional[List[str]] = None,
    to_geographic: bool = True,
) -> Dict[str, Any]:
    """DetectionRecord 列表 → GeoJSON FeatureCollection（全局像素→地理）。"""
    features: List[Dict[str, Any]] = []
    for det in detections:
        x, y, w, h = det["box"]
        label = int(det["label"])
        props: Dict[str, Any] = {
            "label": label,
            "label_name": class_names[label] if class_names and 0 <= label < len(class_names) else str(label),
            "score": det["score"],
        }
        if to_geographic and transform is not None:
            from rasterio.transform import xy as transform_xy

            # 像素角点 → 地理坐标（bbox 多边形）。
            xs = []
            ys = []
            for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
                gx, gy = transform_xy(transform, py, px)
                xs.append(gx)
                ys.append(gy)
            ring = [
                [min(xs), min(ys)],
                [max(xs), min(ys)],
                [max(xs), max(ys)],
                [min(xs), max(ys)],
                [min(xs), min(ys)],
            ]
            geometry = {"type": "Polygon", "coordinates": [ring]}
        else:
            geometry = {
                "type": "Polygon",
                "coordinates": [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]],
            }
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
    collection = {"type": "FeatureCollection", "features": features}
    if crs and not to_geographic:
        collection["crs"] = {"type": "name", "properties": {"name": crs}}
    return collection
