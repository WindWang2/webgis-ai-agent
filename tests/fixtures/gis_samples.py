"""合成 GIS 样本库（ADR-0104 Wave 18）——小、确定、可生成的测试夹具。

设计契约（对应 goal Wave 18）：
- **小**：每个 fixture 的序列化字节有硬上界（测试锁）；
- **确定**：纯函数生成（显式 seed → np.random.default_rng），同参数两次
  构造逐位一致；不提交二进制数据、不拷贝真实数据（license clean —— 全合成）；
- **可生成**：调用即得，不落盘（栅格除外，显式 tmp_path 参数）；
- **避免 copy-paste**：所有"坏数据"场景由一个参数化工厂派生，不允许各
  测试自造变体。

词汇（scenario_kind 与 quality corpus 对齐）：
- nominal: point/line/polygon FC（WGS84，小 bbox，含 value 字段）
- bad_crs: 声明不一致 CRS 的 FC（crs_safety 应当拒绝/降级）
- invalid_geometry: 自交多边形（bowtie）与未闭合环
- missing_field: 缺分析字段
- temporal: 带时间戳的点序列
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

_WGS84 = "EPSG:4326"


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def point_fc(n: int = 20, seed: int = 42, crs: str = _WGS84,
             value_field: Optional[str] = "value") -> Dict[str, Any]:
    """n 个点（成都 bbox 内），value 均匀分布。确定。"""
    rng = _rng(seed)
    features = []
    for i in range(n):
        lon = float(rng.uniform(103.95, 104.10))
        lat = float(rng.uniform(30.55, 30.70))
        props: Dict[str, Any] = {"name": f"p{i:03d}"}
        if value_field is not None:
            props[value_field] = float(rng.uniform(0, 100))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features, "crs": crs}


def polygon_fc(n: int = 8, seed: int = 7, crs: str = _WGS84,
               value_field: Optional[str] = "value") -> Dict[str, Any]:
    """n 个网格对齐矩形多边形（2×4 布局内切分），无重叠。确定。"""
    rng = _rng(seed)
    cols = 4
    x0, y0, dx, dy = 103.95, 30.55, 0.04, 0.04
    features = []
    for i in range(n):
        c, r = i % cols, i // cols
        xmin, ymin = x0 + c * dx, y0 + r * dy
        xmax, ymax = xmin + dx * 0.95, ymin + dy * 0.95
        props = {"name": f"cell{i:03d}"}
        if value_field is not None:
            props[value_field] = float(rng.uniform(0, 100))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin],
            ]]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features, "crs": crs}


def line_fc(n: int = 5, seed: int = 11, crs: str = _WGS84,
            value_field: Optional[str] = "length_km") -> Dict[str, Any]:
    """n 条折线（路网样，每条 4 段）。确定。"""
    rng = _rng(seed)
    features = []
    for i in range(n):
        start_lon = float(rng.uniform(103.96, 104.05))
        start_lat = float(rng.uniform(30.56, 30.68))
        coords = [[start_lon, start_lat]]
        for _ in range(4):
            last = coords[-1]
            coords.append([last[0] + float(rng.uniform(0.002, 0.02)),
                           last[1] + float(rng.uniform(-0.01, 0.01))])
        props = {"name": f"road{i:03d}"}
        if value_field is not None:
            props[value_field] = float(rng.uniform(0.5, 12.0))
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features, "crs": crs}


# ── 坏数据场景（参数化工厂派生，不许测试自造变体）────────────────────────


def bad_crs_fc(n: int = 10) -> Dict[str, Any]:
    """声明与坐标域不一致的 CRS（WGS84 坐标 + GCJ02 声明）。"""
    fc = point_fc(n=n, seed=13, value_field="value")
    fc["crs"] = "EPSG:4490"  # CGCS2000 声明 vs 成都 WGS84 域坐标 —— 语义冲突样本
    return fc


def invalid_geometry_fc() -> Dict[str, Any]:
    """自交多边形（bowtie）：geometry_ops/校验器应当能识别。"""
    bowtie = [[
        [104.0, 30.6], [104.05, 30.65], [104.05, 30.55], [104.0, 30.65], [104.0, 30.6],
    ]]
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": bowtie},
            "properties": {"name": "bowtie"},
        }],
        "crs": _WGS84,
    }


def missing_field_fc(n: int = 10, absent_ratio: float = 0.5) -> Dict[str, Any]:
    """部分要素缺分析字段 value（分母缺失类场景的输入态）。"""
    fc = point_fc(n=n, seed=17, value_field="value")
    absent = max(1, int(n * absent_ratio))
    for f in fc["features"][:absent]:
        f["properties"].pop("value", None)
    return fc


def zero_variance_fc(n: int = 20) -> Dict[str, Any]:
    """value 恒定 → 方差为零（统计检验不可计算场景）。"""
    fc = point_fc(n=n, seed=19, value_field="value")
    for f in fc["features"]:
        f["properties"]["value"] = 3.14
    return fc


def temporal_fc(n_per_period: int = 5, periods: int = 6) -> Dict[str, Any]:
    """带 ISO 时间戳的点序列（趋势 + 周期成分，确定）。"""
    rng = _rng(23)
    features = []
    base_props = ["2024-01", "2024-02", "2024-03", "2024-04", "2024-05", "2024-06"]
    for p in range(periods):
        for i in range(n_per_period):
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [
                    float(rng.uniform(103.95, 104.10)),
                    float(rng.uniform(30.55, 30.70)),
                ]},
                "properties": {
                    "name": f"t{p}-{i:02d}",
                    "time": base_props[p % len(base_props)],
                    "value": float(10 + 5 * p + rng.uniform(0, 2)),
                },
            })
    return {"type": "FeatureCollection", "features": features, "crs": _WGS84}


def tiny_raster(path, width: int = 32, height: int = 32,
                seed: int = 29, dtype: str = "float32") -> str:
    """写一个确定性小 GeoTIFF（默认 32×32，<10KB）。需要 rasterio。"""
    import rasterio
    from rasterio.transform import from_origin

    rng = _rng(seed)
    data = rng.uniform(0, 255, size=(1, height, width)).astype(dtype)
    transform = from_origin(103.95, 30.70, 0.005, 0.005)
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height,
        count=1, dtype=dtype, crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(data)
    return str(path)


SCENARIO_BUILDERS = {
    "nominal_point": point_fc,
    "nominal_polygon": polygon_fc,
    "nominal_line": line_fc,
    "bad_crs": bad_crs_fc,
    "invalid_geometry": invalid_geometry_fc,
    "missing_field": missing_field_fc,
    "zero_variance": zero_variance_fc,
    "temporal": temporal_fc,
}
