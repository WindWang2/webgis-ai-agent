"""geo_analysis narrated 族共享分析脚手架（#1548）。

statistics.py / statistics_v3_diag.py 的 23 个分析入口共用同一条三段式
前置管道（「解析 + metric 投影 → 数值字段对齐 → 样本量下限」），历史上
每处自带一份检查 + 失败构造代码，失败按两种历史方言表达：

- legacy 方言：返回 ``GeoAnalysisResult(False, None, msg,
  error_type=code, correction_hint=hint)``（历史
  ``statistics._typed_failure`` 形状，消息逐字是 API 契约）；
- typed 方言：抛 :class:`~app.lib.gis.scientific_errors.ScientificError`
  子类（``correction_hint`` 随错误传递）。

本模块是管道的单一实现：检查顺序、折叠语义（``_filter_numeric_gdf``
缺字段 / 全值非法折叠到哪个失败形状）、两方言的失败构造都收在这里。
调用点通过 :class:`Failure` 传入自己的站点文案（message / code / hint
或异常类）与阈值 —— 本模块不发明任何文案，所以每个调用点的失败输出
与抽取前逐位一致（纯代码搬移 + 引用替换）。

积木层（解析 / 投影 / 数值过滤的语义本体）在 :mod:`.context`；本模块
只在其上做「校验管道」组合。导入方向纪律与 context.py 相同：
geo_analysis/* → 本模块 → context + geo_processor.core +
gis.scientific_errors + cancellation；本模块不 import statistics /
statistics_v3_diag（statistics 底部 re-export v3_diag、v3_diag 惰性引入
statistics，顶层反引会成环）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple, Union

import geopandas as gpd
import numpy as np

from app.lib.cancellation import checkpoint
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_analysis.context import (
    ValidatedSpatialInput,
    extract_numeric_frame,
    validate_spatial_input,
)
from app.lib.gis.scientific_errors import (
    InvalidGeometry,
    MissingRequiredField,
)

__all__ = [
    "Failure",
    "legacy_failure_result",
    "load_input",
    "filter_numeric",
    "filter_two_numeric",
    "validated_input",
    "aligned_numeric",
    "require_min_n",
    "validated_numeric_frame",
    "validated_two_field_frame",
]


@dataclass(frozen=True)
class Failure:
    """一个脚手架步骤的站点专属失败规格（文案 + 方言）。

    恰有一个方言标记：

    - ``code=`` —— legacy 方言，折叠为
      :func:`legacy_failure_result`（GeoAnalysisResult 失败形状）；
    - ``exc=`` —— typed 方言，抛 ``exc(message, correction_hint=hint)``。

    ``message`` / ``hint`` 逐字来自调用点（API 契约），本模块只做透传。
    """

    message: str
    hint: str = ""
    #: legacy 方言：GeoAnalysisResult.error_type 码（ScientificError 词表）。
    code: Optional[str] = None
    #: typed 方言：要抛的 ScientificError 子类。
    exc: Optional[type] = None

    def __post_init__(self) -> None:
        if (self.code is None) == (self.exc is None):
            raise ValueError(
                "Failure 需要恰好一个方言标记：code=（legacy "
                "GeoAnalysisResult）或 exc=（typed exception）"
            )


def legacy_failure_result(
    message: str, *, code: str, hint: str = ""
) -> GeoAnalysisResult:
    """legacy 方言失败形状（历史 ``statistics._typed_failure`` 本体）。

    Message 逐字保留（API compat）；``error_type`` 携带 ScientificError
    码（machine-readable），``correction_hint`` 携带 LLM 可执行的修复
    指引。dispatch 层已把 GeoAnalysisResult 失败形状折叠为 canonical
    failure，这里更丰富的 typing 可以端到端流通。
    """
    return GeoAnalysisResult(
        False, None, message,
        error_type=code,
        correction_hint=hint or None,
    )


def load_input(geojson: Any) -> Optional[ValidatedSpatialInput]:
    """解析 + metric 投影（历史 ``statistics._load_input`` 本体）。

    单一事实源是 :func:`context.validate_spatial_input`：不可解析 / 无可
    用要素折叠为 ``None``（各调用点保留自己的 legacy 错误文案）；声明
    CRS 路径的 typed CRS 失败仍向外传播，与历史 ``to_utm_gdf`` 行为
    一致。协作式取消检查点（无 token 时 no-op）。
    """
    checkpoint()  # 协作式取消检查点（无 token 时 no-op；H06）
    try:
        return validate_spatial_input(geojson)
    except InvalidGeometry:
        return None


def filter_numeric(
    gdf: gpd.GeoDataFrame, value_field: str
) -> Tuple[gpd.GeoDataFrame, np.ndarray] | None:
    """数值字段对齐（历史 ``statistics._filter_numeric_gdf`` 本体）。

    语义委托 :func:`context.extract_numeric_frame`（单一事实源）。缺字段
    折叠为 ``None``（历史契约）；字段存在但全值非有限同样折叠为
    ``None`` —— 历史实现返回空对，下游（density kde）以裸异常崩溃，现
    为带 guidance 的 typed 失败（H06 已审计的行为 delta）。
    """
    try:
        return extract_numeric_frame(gdf, value_field)
    except MissingRequiredField:
        return None


def filter_two_numeric(
    gdf: gpd.GeoDataFrame, field_x: str, field_y: str
) -> Tuple[gpd.GeoDataFrame, np.ndarray, np.ndarray] | None:
    """两字段同时数值过滤（历史 ``statistics._filter_two_numeric_gdf``
    本体）：行对齐、NaN/±inf 丢行；任一字段缺失 / 全值非法返回 None。
    """
    aligned_x = filter_numeric(gdf, field_x)
    if aligned_x is None or len(aligned_x[1]) == 0:
        return None
    gdf_x, vx = aligned_x
    aligned_xy = filter_numeric(gdf_x, field_y)
    if aligned_xy is None or len(aligned_xy[1]) == 0:
        return None
    gdf_xy, vy = aligned_xy
    return gdf_xy, vx, vy


def _emit(failure: Failure) -> GeoAnalysisResult:
    """按规格发射失败：legacy 方言返回结果，typed 方言抛出。"""
    if failure.exc is not None:
        raise failure.exc(failure.message, correction_hint=failure.hint)
    return legacy_failure_result(failure.message, code=failure.code,  # type: ignore[arg-type]
                                 hint=failure.hint)


def validated_input(
    geojson: Any, *, failure: Failure
) -> Union[ValidatedSpatialInput, GeoAnalysisResult]:
    """管道第 1 段：解析 + metric 投影，失败按 ``failure`` 发射。

    typed 方言（``failure.exc``）直接抛出，返回值恒为
    ValidatedSpatialInput；legacy 方言返回
    ``ValidatedSpatialInput | GeoAnalysisResult``（失败形状）。
    """
    vsi = load_input(geojson)
    if vsi is None:
        return _emit(failure)
    return vsi


def aligned_numeric(
    gdf: gpd.GeoDataFrame,
    value_field: str,
    *,
    failure: Failure,
    fold_empty: bool = True,
) -> Union[Tuple[gpd.GeoDataFrame, np.ndarray], GeoAnalysisResult]:
    """管道第 2 段（单字段）：数值字段对齐，失败按 ``failure`` 发射。

    ``fold_empty``：字段存在但全值非法（对齐后 0 行）是否也折叠进
    ``failure``。历史调用点两种形状都有 —— moran_i / hotspot / typed 族
    显式检查空对（True），h3_lisa 只检查 None（False，空对交由其
    n<3 检查按「样本不足」报告）。``filter_numeric`` 现状下空对不可达
    （extract_numeric_frame 对全非法抛 MissingRequiredField → None），
    该开关只为与历史代码逐位对齐而保留。
    """
    aligned = filter_numeric(gdf, value_field)
    if aligned is None or (fold_empty and len(aligned[1]) == 0):
        return _emit(failure)
    return aligned


def require_min_n(
    n: int, min_n: int, *, failure: Failure
) -> Union[int, GeoAnalysisResult]:
    """管道第 3 段：样本量下限，不足按 ``failure`` 发射，否则原样返回 n。

    用于对 gdf 行数（而非数值数组）设下限的调用点（SDE / 最近邻 /
    ST-DBSCAN / 权重诊断）。对 values 数组的下限由
    :func:`validated_numeric_frame` 的 ``too_few`` 段覆盖（其消息需要
    n 插值时传 ``Callable[[int], Failure]``）。
    """
    if n < min_n:
        return _emit(failure)
    return n


def validated_numeric_frame(
    geojson: Any,
    value_field: str,
    *,
    min_n: int,
    invalid: Failure,
    missing: Failure,
    too_few: Union[Failure, Callable[[int], Failure]],
    fold_empty: bool = True,
) -> Union[Tuple[gpd.GeoDataFrame, np.ndarray], GeoAnalysisResult]:
    """完整三段前置管道（单数值字段）：(gdf, values) 或失败。

    检查顺序与历史调用点一致：解析/投影 → 数值字段对齐 → 样本量
    下限。任一步失败按对应规格发射（legacy 方言返回 GeoAnalysisResult；
    typed 方言抛出）。``too_few`` 传 callable 时以实际 n 构造失败规格
    （历史消息含 ``(got {n})`` 插值的站点）。
    """
    vsi = load_input(geojson)
    if vsi is None:
        return _emit(invalid)
    aligned = filter_numeric(vsi.gdf, value_field)
    if aligned is None or (fold_empty and len(aligned[1]) == 0):
        return _emit(missing)
    gdf, values = aligned
    n = len(values)
    if n < min_n:
        return _emit(too_few(n) if callable(too_few) else too_few)
    return gdf, values


def validated_two_field_frame(
    geojson: Any,
    field_x: str,
    field_y: str,
    *,
    min_n: int,
    invalid: Failure,
    missing: Failure,
    too_few: Union[Failure, Callable[[int], Failure]],
) -> Union[Tuple[gpd.GeoDataFrame, np.ndarray, np.ndarray], GeoAnalysisResult]:
    """完整三段前置管道（双数值字段）：(gdf, vx, vy) 或失败。

    与 :func:`validated_numeric_frame` 同序；第 2 段走
    :func:`filter_two_numeric`（任一字段缺失 / 全值非法即折叠）。双变量
    族（bivariate_moran / bivariate_local_moran）的「两字段缺失或
    非数值」是单一失败消息，故无 fold_empty 开关。
    """
    vsi = load_input(geojson)
    if vsi is None:
        return _emit(invalid)
    aligned = filter_two_numeric(vsi.gdf, field_x, field_y)
    if aligned is None:
        return _emit(missing)
    gdf, vx, vy = aligned
    n = len(vx)
    if n < min_n:
        return _emit(too_few(n) if callable(too_few) else too_few)
    return gdf, vx, vy
