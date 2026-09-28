"""ValidatedSpatialInput —— geo_analysis 共享前置验证上下文（#1548 / H06）。

statistics/kriging/interpolation 各自复制的「解析 GeoJSON → 投影 metric
frame → 过滤数值字段」管道（statistics.py 内 ``to_utm_gdf`` 前导 22 处、
``_filter_numeric_gdf`` 调用 18 处）上收为本模块的单一事实源：

- :func:`validate_spatial_input`：解析 + 投影 + 尺寸/质量披露。失败抛
  typed :class:`~app.lib.gis.scientific_errors.ScientificError`
  （InvalidGeometry / InvalidCRS 经由底层 to_utm_gdf_with_note 的既有
  类型化路径），成功返回携带 metric gdf、CRS、要素数、bbox、字节估算
  与质量旗标的不可变上下文；
- :func:`extract_numeric_frame`：数值字段存在性/类型对齐过滤。缺字段或
  全部值无效抛 typed ``MissingRequiredField``（detail 与历史错误消息逐字
  一致，guidance 区分「缺字段」与「全非法值」两种科学成因）；
- :func:`estimate_frame_bytes`：粗粒度但**保守偏高**的内存估算，只用于
  准入（admission）与披露，不用于计费。

兼容边界：历史 ``GeoAnalysisResult(False, None, msg)`` 失败形状由调用方
（statistics.py 的 legacy preamble / analysis runner）负责折叠 —— 本模块
只产 typed 错误与成功上下文，不感知 GeoAnalysisResult。

导入方向纪律：geo_analysis/* → 本模块 → geo_processor.core +
gis.scientific_errors；本模块不 import 任何 geo_analysis 兄弟模块（防环）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from app.lib.gis.scientific_errors import (
    InvalidGeometry,
    MissingRequiredField,
    ResourceScaleMismatch,
)
from app.lib.geo_processor.core import to_utm_gdf_with_note

logger = logging.getLogger(__name__)

__all__ = [
    "ValidatedSpatialInput",
    "validate_spatial_input",
    "extract_numeric_frame",
    "estimate_frame_bytes",
]

#: 保守字节估算系数（每行）：object 列按 96B、几何列按 256B 计。
#: 估算目的是准入与披露（宁高勿低），不是精确计量。
_OBJECT_CELL_BYTES = 96
_GEOMETRY_CELL_BYTES = 256

#: inline 准入上限（H06 DoD3：主进程不做无界重计算）。
#: 语义：**内联** narrated 路径的规模红线，远高于常规工具调用量级；
#: 超限在**投影之前**廉价拒绝（只数原始 features，不做解析/投影），
#: guidance 引导走 geocompute durable 计划（worker 隔离 + 排队/降级）。
#: 数值是稳定的工程常数，不是机器速度断言。
INLINE_MAX_FEATURES = 200_000
_INLINE_MAX_ESTIMATED_BYTES = 2 * 1024**3
_DURABLE_GUIDANCE = (
    "submit via a geocompute plan (policy=durable_job) so the kernel runs "
    "on an isolated worker with admission/queueing, or narrow the extent / "
    "aggregate first"
)


class ValidatedSpatialInput:
    """一次分析的前置验证产物（metric frame + 尺寸/质量披露）。

    ``gdf`` 已投影到 metric CRS（UTM / 极地方位），行序与输入一致；
    ``quality`` 是 CRS 转换 note 的有界投影（键集封闭），供 narration
    与 evidence 披露（GCJ-02 归一化、几何修复等不再静默）。
    """

    __slots__ = ("gdf", "metric_crs", "source_crs", "quality", "note")

    def __init__(
        self,
        gdf: gpd.GeoDataFrame,
        metric_crs: str,
        source_crs: str,
        note: dict,
    ) -> None:
        self.gdf = gdf
        self.metric_crs = metric_crs
        self.source_crs = source_crs
        #: 有界 note 透传（底层 dict 键集封闭：target_crs/source_crs/
        #: gcj02_normalized/geometry_repaired）。
        self.note = dict(note)
        self.quality = {
            "target_crs": self.note.get("target_crs", metric_crs),
            "source_crs": self.note.get("source_crs", source_crs),
            "gcj02_normalized": bool(self.note.get("gcj02_normalized", False)),
            "geometry_repaired": bool(self.note.get("geometry_repaired", False)),
        }

    @property
    def feature_count(self) -> int:
        return int(len(self.gdf))

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """metric frame 下的 (minx, miny, maxx, maxy)。"""
        return tuple(float(v) for v in self.gdf.total_bounds)  # type: ignore[return-value]

    @property
    def estimated_bytes(self) -> int:
        return estimate_frame_bytes(self.gdf)

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return (
            f"ValidatedSpatialInput(features={self.feature_count}, "
            f"crs={self.metric_crs}, source={self.source_crs})"
        )


def validate_spatial_input(
    geojson: Any,
    *,
    source_crs: Optional[str] = None,
    purpose: str = "analysis",
) -> ValidatedSpatialInput:
    """解析 GeoJSON 并投影到 metric frame（typed 失败，不返回 None）。

    底层复用 :func:`geo_processor.core.to_utm_gdf_with_note`（含 GCJ-02
    归一化、跨 AM 分区、polar 回退、make_valid 与身份缓存 —— 行为与历史
    ``to_utm_gdf`` 逐位一致）。不可解析 / 无可用要素 → ``InvalidGeometry``
    （detail 为中性描述；调用方需要历史错误文案时由 runner/调用点折叠）。

    inline 准入（H06 DoD3）：FeatureCollection 原始要素数超过
    ``INLINE_MAX_FEATURES`` 时在**解析/投影之前**抛
    ``ResourceScaleMismatch``（typed，带 estimated/limit 与 durable 引导）；
    投影后对估算字节复核 2GiB 上限（超限同样 typed 拒绝）。地理处理的重
    计算属于 worker 领域 —— 拒绝不消耗主进程的投影/内存预算。
    """
    raw_count = _raw_feature_count(geojson)
    if raw_count is not None and raw_count > INLINE_MAX_FEATURES:
        raise ResourceScaleMismatch(
            f"inline analysis rejected: {raw_count} features exceed the "
            f"{INLINE_MAX_FEATURES}-feature inline ceiling for {purpose}",
            estimated=f"{raw_count} features",
            limit=f"{INLINE_MAX_FEATURES} features (inline)",
            correction_hint=_DURABLE_GUIDANCE,
            guidance=[
                "submit a geocompute plan with policy=durable_job",
                "or aggregate/bin (e.g. h3_binning) before detailed analysis",
            ],
        )
    gdf, metric_crs, note = to_utm_gdf_with_note(geojson, source_crs=source_crs)
    if gdf is None or metric_crs is None:
        raise InvalidGeometry(
            f"no usable features for {purpose}",
            correction_hint=(
                "provide a GeoJSON FeatureCollection with non-empty "
                "geometries (declare 'crs' only for non-WGS84 coordinates)"
            ),
        )
    vsi = ValidatedSpatialInput(
        gdf, metric_crs, str(note.get("source_crs", "EPSG:4326")), note
    )
    estimated = vsi.estimated_bytes
    if estimated > _INLINE_MAX_ESTIMATED_BYTES:
        raise ResourceScaleMismatch(
            f"inline analysis rejected: estimated frame {estimated} bytes "
            f"exceeds the {_INLINE_MAX_ESTIMATED_BYTES}-byte inline budget "
            f"for {purpose}",
            estimated=f"~{estimated} bytes",
            limit=f"{_INLINE_MAX_ESTIMATED_BYTES} bytes (inline)",
            correction_hint=_DURABLE_GUIDANCE,
        )
    return vsi


def _raw_feature_count(geojson: Any) -> Optional[int]:
    """廉价预检：只数 FeatureCollection 原始 features（不解析几何）。

    仅在输入是 dict 且 features 为 list 时可数；其他形状（str/URL/已解析
    frame）返回 None → 跳过预检，投影后的字节复核仍然生效。
    """
    if isinstance(geojson, dict):
        features = geojson.get("features")
        if isinstance(features, list):
            return len(features)
    return None


def extract_numeric_frame(
    gdf: gpd.GeoDataFrame, value_field: str
) -> Tuple[gpd.GeoDataFrame, np.ndarray]:
    """按数值有效性过滤并对齐 (gdf, values)。

    语义与历史 ``statistics._filter_numeric_gdf`` 逐位一致：非数值列
    ``pd.to_numeric(errors="coerce")``；丢弃 NaN 与 ±inf（审计 E-11/E-2）。
    缺字段或全值无效抛 ``MissingRequiredField``，detail 与历史错误文案
    逐字一致（``Field '<f>' missing or non-numeric``）。
    """
    if value_field not in gdf.columns:
        raise MissingRequiredField(
            f"Field '{value_field}' missing or non-numeric",
            correction_hint=f"add a numeric field named '{value_field}'",
        )
    series = gdf[value_field]
    if not np.issubdtype(series.dtype, np.number):
        series = pd.to_numeric(series, errors="coerce")
    valid_mask = series.notna() & np.isfinite(series.astype(float))
    gdf_valid = gdf[valid_mask].reset_index(drop=True)
    values = series[valid_mask].astype(float).values
    if len(values) == 0:
        raise MissingRequiredField(
            f"Field '{value_field}' missing or non-numeric",
            correction_hint=(
                f"every row has a non-numeric/NaN/inf '{value_field}' value; "
                "fix the values or pick another field"
            ),
        )
    return gdf_valid, values


def estimate_frame_bytes(gdf: gpd.GeoDataFrame) -> int:
    """保守偏高的 DataFrame 内存估算（准入/披露用，非计费）。

    numeric 列按 dtype itemsize，object 列按 96B/格，geometry 列按
    256B/格 —— 对文本属性与复杂几何显著高估，对准入是安全方向。
    """
    n = len(gdf)
    if n == 0:
        return 0
    total = 0
    for name in gdf.columns:
        series = gdf[name]
        if name == gdf.geometry.name:
            total += _GEOMETRY_CELL_BYTES * n
            continue
        if isinstance(series.dtype, pd.CategoricalDtype):
            total += _OBJECT_CELL_BYTES * n
        elif np.issubdtype(series.dtype, np.number):
            total += int(series.dtype.itemsize) * n
        else:
            total += _OBJECT_CELL_BYTES * n
    return int(total)
