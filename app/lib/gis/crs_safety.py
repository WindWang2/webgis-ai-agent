"""CRS Safety —— CRS 分类与度量投影推荐（VNext §39）。

职责（纯函数、无 I/O、无数据加载）：

- ``classify_crs(crs)`` → geographic / projected / projected_local_metric /
  unknown。LOCAL_METRIC 与普通 projected 的区分点：EPSG:3857 是投影米但
  离赤道越远尺度失真越大（高纬 ~40%），不能当「局部度量」用；UTM 分带
  （326xx/327xx）在其带内是可用的局部度量。pyproj 兜底按**实测尺度失真**
  判定（有效域中心+四角的有限差分局部尺度因子 ≤1%），不按投影名字猜 ——
  全球/大洲级等积投影（Mollweide、Equal Earth、大洲 LAEA 等）因此不再
  通过 LOCAL_METRIC 硬门（#1720）。
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

import math
import re
from functools import lru_cache
from typing import Any, Literal, Optional, Tuple

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
# #1720：局部度量投影的最大容许尺度失真。LOCAL_METRIC 硬门的目的正是拒斥
# 尺度失真投影，而旧兜底对「投影米 + 名字不含 mercator」一律放行 —— 全球/
# 大洲级等积投影（Mollweide、Equal Earth、大洲 LAEA 等）由此绕过该门。改为
# 按投影有效域实测尺度失真：UTM/分带/国家级投影域内失真 ≤0.5%，阈值取 1%
# 留裕量；失真更小的等积投影同样是可信局部度量（判据是失真，不是投影名）。
_LOCAL_METRIC_MAX_SCALE_DISTORTION = 1.01
# 尺度采样：有限差分步长（度）与采样纬度钳制（远离投影奇点）。采样点 =
# 有效域中心 + 四角（5 点，CPU 有界；classify_crs 另有 lru_cache）。
_SCALE_PROBE_STEP_DEG = 0.05
_SCALE_PROBE_MAX_ABS_LAT = 89.5


def _local_metric_scale_ok(crs: Any) -> bool:
    """投影 CRS 在其有效域内的尺度失真是否 ≤ 阈值（可当局部度量）。

    实测而非猜名：在 ``area_of_use`` 的中心与四角做有限差分，比较投影
    位移与同起点同方向的 geodesic 位移，取最大局部尺度因子与
    ``_LOCAL_METRIC_MAX_SCALE_DISTORTION`` 比（纯本地计算，无 I/O）。

    不可判定（无有效域 / 无基础地理 CRS / 投影不可用 / 任何异常）→ True：
    无证据不收紧，保持既有判定（诚实缺省 —— EPSG:32600 这类不可投影的
    UTM 网格系统 CRS 照旧按旧行为放行）。
    """
    try:
        area = crs.area_of_use
        geodetic = crs.geodetic_crs
        if area is None or geodetic is None:
            return True
        from pyproj import Geod, Transformer

        west, east, south, north = (
            float(area.west), float(area.east),
            float(area.south), float(area.north),
        )
        if not all(math.isfinite(v) for v in (west, east, south, north)):
            return True
        transformer = Transformer.from_crs(geodetic, crs, always_xy=True)
        ellipsoid = geodetic.ellipsoid
        geod = Geod(a=ellipsoid.semi_major_metre, b=ellipsoid.semi_minor_metre)
        # 跨 180° 经线的有效域：中心按跨度归一（角点用原始值，投影自身处理）。
        clon = (west + ((east - west) % 360.0) / 2.0 + 180.0) % 360.0 - 180.0
        clat = min(max((south + north) / 2.0, -_SCALE_PROBE_MAX_ABS_LAT),
                   _SCALE_PROBE_MAX_ABS_LAT)
        step = _SCALE_PROBE_STEP_DEG
        worst = 0.0
        for lon, lat in (
            (clon, clat),
            (west, south), (east, south), (west, north), (east, north),
        ):
            lat0 = min(max(lat, -_SCALE_PROBE_MAX_ABS_LAT),
                       _SCALE_PROBE_MAX_ABS_LAT)
            x0, y0 = transformer.transform(lon, lat0)
            if not (math.isfinite(x0) and math.isfinite(y0)):
                continue
            for dlon, dlat in ((step, 0.0), (0.0, step)):
                _, _, geodesic = geod.inv(lon, lat0, lon + dlon, lat0 + dlat)
                if geodesic <= 0.0:
                    continue
                x1, y1 = transformer.transform(lon + dlon, lat0 + dlat)
                if not (math.isfinite(x1) and math.isfinite(y1)):
                    continue
                worst = max(worst, math.hypot(x1 - x0, y1 - y0) / geodesic)
        return worst <= _LOCAL_METRIC_MAX_SCALE_DISTORTION
    except Exception:  # noqa: BLE001 — 尺度不可判定 → 不收紧（诚实缺省）
        return True


@lru_cache(maxsize=512)
def classify_crs(crs: Optional[str]) -> CRSDataClass:
    """CRS 字符串 → 数据 CRS 分类（词法优先，pyproj 兜底，缓存）。

    pyproj 兜底里的 projected_local_metric 判定带尺度失真闸门：投影米 +
    有效域内实测尺度失真 ≤1% 才算局部度量（见 :func:`_local_metric_scale_ok`）。
    """
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
                # #1720：名字不含 mercator ≠ 尺度可信 —— 全球/大洲级等积投影
                # （Mollweide、Equal Earth、大洲 LAEA 等）旧逻辑会因此通过
                # LOCAL_METRIC 硬门。按有效域实测尺度失真后才判局部度量。
                if _local_metric_scale_ok(parsed):
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
