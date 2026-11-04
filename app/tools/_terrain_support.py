"""Shared bounded raster I/O and scientific evidence for terrain tools."""

import logging
import math
from typing import Any, List, Optional, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from rasterio.io import DatasetReader

import numpy as np

from app.utils.path import validate_data_path

from app.lib.geo_analysis.raster_guard import (
    RasterResourceExceededError,
    RasterResourceGuard,
)
from app.lib.geo_analysis.raster_math import rasterio_env
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.backend_selection import ScaleProfile, select_backend
from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.scientific_evidence import Diagnostic, build_evidence

logger = logging.getLogger(__name__)

# 度 → 米（与 band_math/stac_client 的 cos(lat) 政策同一换算常数）。
_METRES_PER_DEGREE = 111320.0

_DERIVATIVE_ALGORITHMS = {
    "tpi": "terrain.tpi",
    "tri": "terrain.tri",
    "roughness": "terrain.roughness",
    "plan_curvature": "terrain.curvature",
    "profile_curvature": "terrain.curvature",
}


def _backend_diagnostic(
    algorithm_id: str,
    raster_cells: Optional[int],
) -> Optional[Diagnostic]:
    """select_backend 决策 → 证据诊断（additive，失败不阻塞主结果）。"""
    try:
        decision = select_backend(algorithm_id, ScaleProfile(raster_cells=raster_cells))
        raw = decision.to_diagnostic()
        value = raw.get("value")
        return Diagnostic(
            name=str(raw.get("name") or "backend_selection"),
            value=float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None,
            text=str(raw.get("text") or ""),
        )
    except Exception:  # noqa: BLE001 — 诊断是 additive，不阻塞主结果
        return None


def _terrain_evidence(
    payload: dict,
    algorithm_id: str,
    *,
    tool: str,
    parameters_applied: dict,
    crs: str,
    diagnostics: Sequence[Diagnostic],
    transformations: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
) -> dict:
    """挂 VNext 证据块（descriptor = 假设/局限/出处的唯一事实源）。"""
    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        logger.warning(
            "scientific evidence requested for unknown algorithm %s", algorithm_id
        )
        return payload
    input_facts = {"artifact_type": "terrain_surface", "units": "m"}
    if crs:
        input_facts["crs"] = crs
    payload["scientific_evidence"] = build_evidence(
        descriptor,
        tool=tool,
        parameters_applied=parameters_applied,
        input_facts=input_facts,
        diagnostics=list(diagnostics),
        transformations=transformations,
        warnings=warnings,
    )
    return payload


def _check_full_read_budget(src: "DatasetReader") -> None:
    """Wave 6（audit 05 §2 整读隐患 #1）：整波段 read + float64 提升会把
    工作集翻倍 —— check_grid 只计像元数（float32 口径，最多放进 ~1 GiB
    名义 / ~2 GiB 实际），这里在**读取发生之前**记账：px × (8 + 源
    itemsize) 字节 = float64 目标 + 源缓冲瞬态（round-1 review MINOR：
    此前只记 8 字节目标，16-bit DEM 的源缓冲被漏记 —— 实际工作集是
    px×8 + px×itemsize）。超限抛类型化错误。全局算法（D8/视域/BFS）仍是
    有意的整读面 —— 本护栏只拒绝超出预算者，不改变算法语义。"""
    pixels = int(src.width) * int(src.height)
    try:
        itemsize = int(np.dtype(src.dtypes[0]).itemsize)
    except (IndexError, TypeError, ValueError):
        itemsize = 2  # DEM 常态 uint16 的保守缺省
    estimated = pixels * (8 + itemsize)  # float64 cast target + source transient
    cap = int(RasterResourceGuard.MAX_ESTIMATED_OUTPUT_BYTES)
    if estimated <= cap:
        return
    bounds = (
        float(src.bounds.left),
        float(src.bounds.bottom),
        float(src.bounds.right),
        float(src.bounds.top),
    )
    suggested = RasterResourceGuard.suggest_safe_resolutions(bounds)
    msg = (
        f"Terrain tools read the whole band and cast to float64 "
        f"({src.width}x{src.height} = {pixels:,} px → ~{estimated / (1024**3):.2f} GiB "
        f"working set, budget {cap / (1024**3):.1f} GiB). "
        f"Downsample or clip the DEM first (suggested target_resolution "
        f"values: {suggested})."
    )
    logger.warning("[terrain] full-read budget exceeded: %s", msg)
    raise RasterResourceExceededError(
        message=msg,
        requested_width=int(src.width),
        requested_height=int(src.height),
        total_pixels=pixels,
        estimated_bytes=estimated,
        suggested_resolutions=suggested,
        error_code="RASTER_FULL_READ_BUDGET_EXCEEDED",
    )


def _read_terrain_window(
    raster_path: str,
    nodata_override: Optional[float],
) -> Tuple[np.ndarray, Tuple[float, ...], str, Optional[float], Tuple[float, ...]]:
    """validate → 有界读取（RasterResourceGuard 护栏）→ (数组, transform, crs, nodata, bounds)。"""
    path = validate_data_path(raster_path)
    import rasterio
    from rasterio.errors import RasterioIOError

    # science-v4 W2：裸 RasterioIOError 经 dispatch 变 TOOL_ERROR 丢修正
    # 提示 —— 与 lib reader（geo_raster/reader.py RasterReaderError）同款
    # ValueError 包装，保住科学错误通道。
    from app.lib.geo_raster.reader import RasterReaderError

    try:
        with rasterio_env():
            with rasterio.open(path) as src:
                RasterResourceGuard.check_grid(
                    src.width, src.height, num_bands=src.count
                )
                _check_full_read_budget(src)
                arr = src.read(1).astype("float64")
                transform = tuple(float(v) for v in src.transform)[:6]
                crs = str(src.crs) if src.crs is not None else ""
                nodata = (
                    float(nodata_override)
                    if nodata_override is not None
                    else (float(src.nodata) if src.nodata is not None else None)
                )
                bounds = (
                    float(src.bounds.left),
                    float(src.bounds.bottom),
                    float(src.bounds.right),
                    float(src.bounds.top),
                )
    except RasterioIOError as exc:
        raise RasterReaderError(f"cannot open raster {path!r}: {exc}") from exc
    return arr, transform, crs, nodata, bounds


def _metric_cell_sizes(
    crs: str,
    transform: Sequence[float],
    bounds: Sequence[float],
) -> Tuple[float, float, List[str]]:
    """(cy_m, cx_m, transformations)；地理栅格按 cos(lat) 政策换算米制像元。"""
    res_y = abs(float(transform[4]))
    res_x = abs(float(transform[0]))
    if classify_crs(crs) == "geographic":
        lat0 = (float(bounds[1]) + float(bounds[3])) / 2.0
        cy = res_y * _METRES_PER_DEGREE
        cx = res_x * _METRES_PER_DEGREE * math.cos(math.radians(lat0))
        note = (
            f"geographic CRS: metric cell sizes res_y*{_METRES_PER_DEGREE} and "
            f"res_x*{_METRES_PER_DEGREE}*cos({lat0:.4f}deg) (band_math cos-lat policy)"
        )
        return cy, cx, [note]
    return res_y, res_x, []


def _non_metric_warning(crs: str) -> List[str]:
    """投影 CRS 但线性单位非米 → 披露（米制参数假设）。"""
    if classify_crs(crs) != "projected":
        return []
    try:
        from rasterio.crs import CRS

        units = CRS.from_user_input(crs).linear_units or ""
    except Exception:  # noqa: BLE001 — CRS 不可解析时无从判定，静默
        return []
    if units and units not in ("metre", "meter", "m"):
        return [
            f"CRS linear units are '{units}', not metres; metric parameters and m2 outputs assume metres"
        ]
    return []


def _bounded_sample(
    arr: np.ndarray, max_side: int = 64, decimals: int = 4
) -> List[List[Any]]:
    """≤ max_side×max_side 降采样样本（NaN/Inf → None，JSON 安全）。"""
    step = max(1, math.ceil(max(arr.shape) / max_side))
    sub = arr[::step, ::step]
    return [
        [None if not math.isfinite(v) else round(float(v), decimals) for v in row]
        for row in sub
    ]


def _base_diagnostics(
    transform: Sequence[float],
    rows: int,
    cols: int,
    extra: Sequence[Diagnostic] = (),
) -> List[Diagnostic]:
    diags = [
        Diagnostic(name="pixel_size_y", value=abs(float(transform[4]))),
        Diagnostic(name="pixel_size_x", value=abs(float(transform[0]))),
        Diagnostic(name="rows", value=float(rows)),
        Diagnostic(name="cols", value=float(cols)),
    ]
    diags.extend(extra)
    return diags


def _persist_filled_dem(
    source_path: str,
    filled: np.ndarray,
    transform: Tuple[float, ...],
    crs: str,
    nodata: Optional[float],
) -> str:
    """填充后 DEM → data_dir 内 GeoTIFF（水文组合链的持久化半边）。

    science-v3 审计 P0：depression_fill 此前只返回统计/样本，填充面
    无法传递给任何下游水文工具 —— 「fill(epsilon)→D∞/河网/TWI」组合
    在工具层不可执行。写盘文件名 = 源名 + ``_filled`` 后缀（确定性，
    同源重跑覆盖同文件）。
    """
    import os

    import rasterio
    from rasterio.transform import Affine

    src_real = validate_data_path(source_path)
    root, ext = os.path.splitext(src_real)
    target = root + "_filled" + (ext or ".tif")
    # review R2-3：写前对 target 过同一条路径安全闸 —— data_dir 内预置的
    # 同名 symlink（指向 data_dir 外）会被 realpath 比对拒绝（S36 威胁
    # 模型）。同源重跑覆盖同名产物是文档化语义（确定性命名）。
    validate_data_path(target)
    profile = {
        "driver": "GTiff",
        "height": filled.shape[0],
        "width": filled.shape[1],
        "count": 1,
        "dtype": "float64",
        # transform 是 affine 系数序（tuple(src.transform)），直接 Affine(*)
        # 重建 —— 此前 from_gdal(affine) 把 origin/pixel 尺寸错位解析，
        # 写出的下游 GeoTIFF 地理参考错乱（Science V6 review 修复）。
        "transform": Affine(*[float(v) for v in transform[:6]]),
        "nodata": float(nodata) if nodata is not None else -9999.0,
    }
    if crs:
        profile["crs"] = crs
    from rasterio.errors import RasterioIOError

    from app.lib.geo_raster.reader import RasterReaderError

    try:
        with rasterio_env():
            with rasterio.open(target, "w", **profile) as dst:
                dst.write(filled, 1)
    except RasterioIOError as exc:
        raise RasterReaderError(
            f"cannot write filled raster {target!r}: {exc}"
        ) from exc
    # 绝对路径：validate_data_path 对 data_dir 内绝对路径放行，
    # 下游工具可直接把该返回值作为 raster_path 消费。
    return target


def _persist_raster_product(
    source_path: str,
    arr: np.ndarray,
    transform: Tuple[float, ...],
    crs: str,
    suffix: str,
    *,
    nodata_value: Optional[float],
) -> str:
    """数组 → data_dir 内 GeoTIFF 产物（``<源名><suffix>.tif`` 确定性命名）。

    Science V6 成本面链的写盘半边：cost_distance 产物后缀 ``_costdist``，
    同源重跑覆盖同文件；返回绝对路径供下游工具按 raster_path 直接消费。
    """
    import os

    import rasterio
    from rasterio.transform import Affine
    from rasterio.errors import RasterioIOError

    from app.lib.geo_raster.reader import RasterReaderError

    src_real = validate_data_path(source_path)
    root, ext = os.path.splitext(src_real)
    target = root + suffix + (ext or ".tif")
    validate_data_path(target)  # 写前过同一条路径安全闸（S36 威胁模型）
    profile = {
        "driver": "GTiff",
        "height": arr.shape[0],
        "width": arr.shape[1],
        "count": 1,
        "dtype": "float64",
        # transform 是 affine 系数序（tuple(src.transform)），直接 Affine(*)
        # 重建（from_gdal 会错位解析 —— 见 _persist_filled_dem 同款修复）。
        "transform": Affine(*[float(v) for v in transform[:6]]),
        "nodata": float(nodata_value) if nodata_value is not None else -9999.0,
    }
    if crs:
        profile["crs"] = crs
    try:
        with rasterio_env():
            with rasterio.open(target, "w", **profile) as dst:
                dst.write(arr, 1)
    except RasterioIOError as exc:
        raise RasterReaderError(
            f"cannot write raster product {target!r}: {exc}"
        ) from exc
    return target
