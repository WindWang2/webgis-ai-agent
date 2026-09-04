"""CRS Safety —— CRS 分类与度量投影推荐（VNext §39）。

职责（纯函数、无 I/O、无数据加载）：

- ``classify_crs(crs)`` → geographic / projected / projected_local_metric /
  unknown。LOCAL_METRIC 与普通 projected 的区分点：EPSG:3857 是投影米但
  离赤道越远尺度失真越大（高纬 ~40%），不能当「局部度量」用；UTM 分带
  （326xx/327xx）在其带内是可用的局部度量。
- ``recommend_metric_crs(bbox)`` → 从 bbox 纯计算推荐 UTM EPSG（极区
  → UPS 极方位投影）。给 resolver 的 REQUIRES_TRANSFORM 建议用，不执行
  重投影（执行在 Data/执行层）。

词表（descriptor.crs_class，封闭）：
  CRS_AGNOSTIC            —— 算法不关心坐标语义（纯属性统计）
  GEOGRAPHIC_OK           —— 度数坐标下方法仍正确（地学距离/geohash）
  PROJECTED_REQUIRED      —— 任何投影平面坐标（含 3857，失真进 limitations）
  LOCAL_METRIC_REQUIRED   —— 局部度量投影（UTM 等；3857 不算）
  GEODESIC                —— 算法内部做大地测量（输入可为度）
  RASTER_GRID             —— 栅格管线自管 CRS（对齐/重投影由 runtime 裁决）
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal, Optional, Tuple

CRSSpatialClass = Literal[
    "", "CRS_AGNOSTIC", "GEOGRAPHIC_OK", "PROJECTED_REQUIRED",
    "LOCAL_METRIC_REQUIRED", "GEODESIC", "RASTER_GRID",
]

CRSDataClass = Literal["geographic", "projected", "projected_local_metric", "unknown"]

_UTM_NORTH = re.compile(r"^(?:EPSG:)?(326\d{2})$", re.IGNORECASE)
_UTM_SOUTH = re.compile(r"^(?:EPSG:)?(327\d{2})$", re.IGNORECASE)
_EPSG = re.compile(r"^(?:EPSG:)?(\d{1,5})$", re.IGNORECASE)

_GEOGRAPHIC_EPSG = {4326, 4490, 4269, 4258}
# 3857 投影米但非局部度量（Web Mercator 尺度失真）；3413/3031 极方位
# 度量投影按局部度量对待（极区标准分析投影）。
_PROJECTED_LOCAL_EPSG_PREFIXES = {326, 327}
_POLAR_METRIC_EPSG = {3413, 3031}


@lru_cache(maxsize=512)
def classify_crs(crs: Optional[str]) -> CRSDataClass:
    """CRS 字符串 → 数据 CRS 分类（词法优先，pyproj 兜底，缓存）。"""
    if not crs or not isinstance(crs, str):
        return "unknown"
    text = crs.strip()
    if not text:
        return "unknown"
    m = _EPSG.match(text)
    if m:
        code = int(m.group(1))
        if code in _GEOGRAPHIC_EPSG:
            return "geographic"
        if code == 3857:
            return "projected"
        if code in _POLAR_METRIC_EPSG:
            return "projected_local_metric"
        prefix = code // 100
        if prefix in _PROJECTED_LOCAL_EPSG_PREFIXES and 1 <= code % 100 <= 60:
            return "projected_local_metric"
        # 其它 EPSG 交给 pyproj
    lowered = text.lower()
    if "wgs84" in lowered and "utm" not in lowered and "mercator" not in lowered:
        return "geographic"
    if lowered.startswith("epsg:4490") or "cgcs2000" in lowered:
        return "geographic"
    try:  # pyproj 兜底（本地计算，无 I/O）；失败 = unknown（诚实缺省）
        from pyproj import CRS

        parsed = CRS.from_user_input(text)
        if parsed.is_geographic:
            return "geographic"
        if parsed.is_projected:
            # 投影单位是米且非世界级 Web Mercator → 局部度量近似
            axis = parsed.axis_info[0].unit_name if parsed.axis_info else ""
            metric = str(axis).lower() in {"metre", "meter", "m"}
            if metric and "mercator" not in parsed.name.lower():
                return "projected_local_metric"
            return "projected"
    except Exception:  # noqa: BLE001
        return "unknown"
    return "unknown"


def recommend_metric_crs(bbox: Optional[Tuple[float, float, float, float]]) -> str:
    """从 bbox（minx, miny, maxx, maxy，度）推荐度量 CRS（纯计算）。

    极区（|lat|>84）→ UPS 极方位；否则 UTM 分带（中央经线最近原则）。
    返回 EPSG 字符串；bbox 非法时返回通用 Web Mercator 之外的保守值
    （WGS84 UTM 带无法推断 → 返回空串由调用方处理）。
    """
    if not bbox or len(bbox) != 4:
        return ""
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return ""
    clat = (miny + maxy) / 2.0
    clon = (minx + maxx) / 2.0
    if clat > 84.0:
        return "EPSG:3413"
    if clat < -80.0:
        return "EPSG:3031"
    zone = int((clon + 180.0) // 6.0) + 1
    zone = max(1, min(60, zone))
    return f"EPSG:{326 if clat >= 0.0 else 327}{zone:02d}"


def crs_class_allows(crs_class: str, data_class: CRSDataClass) -> bool:
    """算法 CRS 类 vs 数据 CRS 分类是否兼容（resolver 硬门核心谓词）。

    unknown 数据类永远放行（未知 ≠ 不满足 —— 诚实缺省哲学）。
    """
    if data_class == "unknown" or not crs_class:
        return True
    if crs_class == "CRS_AGNOSTIC" or crs_class == "RASTER_GRID":
        return True
    if crs_class == "GEOGRAPHIC_OK":
        return True     # 度数也能算；投影更能算
    if crs_class == "GEODESIC":
        return True     # 内部大地测量，度/米输入都正确处理
    if crs_class == "PROJECTED_REQUIRED":
        return data_class in ("projected", "projected_local_metric")
    if crs_class == "LOCAL_METRIC_REQUIRED":
        return data_class == "projected_local_metric"
    return True
