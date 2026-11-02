"""空间语义 fixture 语料（GOAL 01 · M7）—— 确定性、无网、无密钥。

覆盖 TEST_MATRIX 的输入形态谱：
- EPSG:4326 声明（成都）；
- 未声明度样（rfc7946_default 路径）；
- 未声明投影样（成都 UTM 48N 米坐标 —— blocked 路径）；
- EPSG:3857 声明（Web Mercator 失真路径）；
- GCJ-02 / BD-09 声明（偏移帧归一路径）；
- 跨 180°（斐济簇 —— antimeridian 路径）；
- 高纬（斯瓦尔巴 —— 极区投影路径）；
- 无效几何（null geometry / 空 FC）；
- 非米投影（EPSG:2270 ftUS —— 面积轴因子路径）。

全部由确定性公式生成（无外部数据依赖），坐标值用 pyproj 变换锚定。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

CHENGDU_LON, CHENGDU_LAT = 104.065735, 30.659462


def _pt(lon: float, lat: float, props: Optional[Dict[str, Any]] = None) -> Dict:
    return {
        "type": "Feature",
        "properties": props or {},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def _fc(features: List[Dict], crs: Optional[str] = None) -> Dict:
    out: Dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if crs:
        out["crs"] = crs
    return out


def utm48_of(lon: float, lat: float) -> List[float]:
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:4326", "EPSG:32648", always_xy=True)
    return list(t.transform(lon, lat))


def webmercator_of(lon: float, lat: float) -> List[float]:
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    return list(t.transform(lon, lat))


def chengdu_points_wgs84(n: int = 8) -> Dict:
    return _fc([
        _pt(CHENGDU_LON + 0.001 * i, CHENGDU_LAT + 0.0005 * i,
            {"name": f"小学{i}"})
        for i in range(n)
    ], "EPSG:4326")


def chengdu_points_undeclared(n: int = 8) -> Dict:
    fc = chengdu_points_wgs84(n)
    fc.pop("crs")
    return fc


def chengdu_points_projected_undeclared(n: int = 4) -> Dict:
    return _fc([
        {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point",
                         "coordinates": utm48_of(
                             CHENGDU_LON + 0.001 * i, CHENGDU_LAT)},
        }
        for i in range(n)
    ])  # 无 crs member —— blocked 路径


def chengdu_points_webmercator_declared(n: int = 4) -> Dict:
    return _fc([
        {**_pt(*[0, 0]),
         "geometry": {"type": "Point",
                      "coordinates": webmercator_of(
                          CHENGDU_LON + 0.001 * i, CHENGDU_LAT)}}
        for i in range(n)
    ], "EPSG:3857")


def chengdu_points_gcj02_declared(n: int = 4) -> Dict:
    return _fc([
        _pt(CHENGDU_LON + 0.006 * i, CHENGDU_LAT, {"frame": "gcj02"})
        for i in range(n)
    ], "GCJ02")


def chengdu_points_bd09_declared(n: int = 4) -> Dict:
    return _fc([
        _pt(CHENGDU_LON + 0.007 * i, CHENGDU_LAT + 0.002, {"frame": "bd09"})
        for i in range(n)
    ], "BD-09")


def chengdu_districts_wgs84() -> Dict:
    return _fc([
        {"type": "Feature",
         "properties": {"district": "锦江区"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [104.05, 30.65], [104.07, 30.65], [104.07, 30.67],
             [104.05, 30.67], [104.05, 30.65]]]}},
        {"type": "Feature",
         "properties": {"district": "青羊区"},
         "geometry": {"type": "Polygon", "coordinates": [[
             [104.08, 30.65], [104.10, 30.65], [104.10, 30.67],
             [104.08, 30.67], [104.08, 30.65]]]}},
    ], "EPSG:4326")


def antimeridian_fiji_cluster() -> Dict:
    """斐济跨 180° 簇：179.72E 与 179.98W（真实跨度 ~0.3°，环绕表示）。"""
    return _fc([
        _pt(179.72, -17.9), _pt(179.85, -17.5),
        _pt(-179.98, -16.8), _pt(-179.9, -17.2),
    ], "EPSG:4326")


def polar_svalbard() -> Dict:
    """高北极（>84°N，格陵兰北缘外海）—— UPS 极方位路径。"""
    return _fc([_pt(15.0, 84.5), _pt(16.0, 85.0)], "EPSG:4326")


def wide_span_china() -> Dict:
    """北京—乌鲁木齐（经度跨 ~29°）—— 多带降级路径。"""
    return _fc([_pt(116.4, 39.9), _pt(87.6, 43.8)], "EPSG:4326")


def invalid_geometry_mixed() -> Dict:
    return _fc([
        {"type": "Feature", "properties": {}, "geometry": None},
        _pt(CHENGDU_LON, CHENGDU_LAT),
    ], "EPSG:4326")


def empty_fc() -> Dict:
    return _fc([])


def bulk_points(n: int, declared: bool = True) -> Dict:
    """性能语料：n 点网格（成都周边），O(bounded) preflight 输入。"""
    feats = []
    for i in range(n):
        lon = CHENGDU_LON + (i % 1000) * 0.0001
        lat = CHENGDU_LAT + (i // 1000) * 0.0001
        feats.append(_pt(lon, lat, {"idx": i}))
    return _fc(feats, "EPSG:4326" if declared else None)
