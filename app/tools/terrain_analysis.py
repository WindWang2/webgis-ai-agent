"""地形分析与遥感指数工具 — 坡度/坡向/山体阴影、多源植被指数、地形科学（VNext）

VNext 地形科学工具（ADR-0099）：TPI/TRI/粗糙度/曲率、视域、D8 流向与
汇流累积、流域圈定、等值线提取。薄包装职责：validate → 读有界窗口 →
调 app/lib/geo_analysis/terrain.py 纯函数 → 挂 scientific_evidence →
返回有界结果。算法语义一律在注册表 descriptor 与 lib 层，本层不复算。

Foundation V2（A5）新增：Priority-Flood 填洼、D∞ 多向流、流程长度、
河网提取 + Strahler 分级、流域形态量测、TWI/SPI、USLE LS、地形开放度、
geomorphons、Weiss 地类分级、多方位山体阴影（护栏/米制换算/证据模式
与既有工具逐一同构；select_backend 决策进 diagnostics）。

Terrain V3 新增：horizon_angle_analysis（地平线角）、
sky_view_factor_analysis（天空可视因子，Steyn 1980）；flow_analysis
追加 flat_routing additive 参数（epsilon = Barnes 2014 填洼后路由）。
"""
import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.tools.registry import ToolRegistry, tool
from app.services.rs.spectral_engine import spectral_engine
from app.services.rs.band_math import compute_raster_stats, compute_slope
from app.tools._utils import parse_bbox, trim_features
from app.utils.path import validate_data_path

from app.lib.geo_analysis import terrain as terrain_lib
from app.lib.geo_analysis.raster_guard import (
    RasterResourceExceededError,
    RasterResourceGuard,
)
from app.lib.geo_analysis.raster_math import rasterio_env
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.backend_selection import ScaleProfile, select_backend
from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.parameter_contracts import apply_contract
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
    algorithm_id: str, raster_cells: Optional[int],
) -> Optional[Diagnostic]:
    """select_backend 决策 → 证据诊断（additive，失败不阻塞主结果）。"""
    try:
        decision = select_backend(
            algorithm_id, ScaleProfile(raster_cells=raster_cells))
        raw = decision.to_diagnostic()
        value = raw.get("value")
        return Diagnostic(
            name=str(raw.get("name") or "backend_selection"),
            value=float(value) if isinstance(value, (int, float))
            and not isinstance(value, bool) else None,
            text=str(raw.get("text") or ""),
        )
    except Exception:  # noqa: BLE001 — 诊断是 additive，不阻塞主结果
        return None


def _terrain_evidence(
    payload: dict, algorithm_id: str, *, tool: str,
    parameters_applied: dict, crs: str,
    diagnostics: Sequence[Diagnostic],
    transformations: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
) -> dict:
    """挂 VNext 证据块（descriptor = 假设/局限/出处的唯一事实源）。"""
    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        logger.warning("scientific evidence requested for unknown algorithm %s", algorithm_id)
        return payload
    input_facts = {"artifact_type": "terrain_surface", "units": "m"}
    if crs:
        input_facts["crs"] = crs
    payload["scientific_evidence"] = build_evidence(
        descriptor, tool=tool,
        parameters_applied=parameters_applied,
        input_facts=input_facts,
        diagnostics=list(diagnostics),
        transformations=transformations,
        warnings=warnings,
    )
    return payload


def _check_full_read_budget(src) -> None:
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
        float(src.bounds.left), float(src.bounds.bottom),
        float(src.bounds.right), float(src.bounds.top),
    )
    suggested = RasterResourceGuard.suggest_safe_resolutions(bounds)
    msg = (
        f"Terrain tools read the whole band and cast to float64 "
        f"({src.width}x{src.height} = {pixels:,} px → ~{estimated / (1024 ** 3):.2f} GiB "
        f"working set, budget {cap / (1024 ** 3):.1f} GiB). "
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
    raster_path: str, nodata_override: Optional[float],
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
                RasterResourceGuard.check_grid(src.width, src.height, num_bands=src.count)
                _check_full_read_budget(src)
                arr = src.read(1).astype("float64")
                transform = tuple(float(v) for v in src.transform)[:6]
                crs = str(src.crs) if src.crs is not None else ""
                nodata = (
                    float(nodata_override) if nodata_override is not None
                    else (float(src.nodata) if src.nodata is not None else None)
                )
                bounds = (
                    float(src.bounds.left), float(src.bounds.bottom),
                    float(src.bounds.right), float(src.bounds.top),
                )
    except RasterioIOError as exc:
        raise RasterReaderError(f"cannot open raster {path!r}: {exc}") from exc
    return arr, transform, crs, nodata, bounds


def _metric_cell_sizes(
    crs: str, transform: Sequence[float], bounds: Sequence[float],
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
            f"res_x*{_METRES_PER_DEGREE}*cos({lat0:.4f}deg) (band_math cos-lat policy)")
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
            f"CRS linear units are '{units}', not metres; metric parameters and m2 outputs assume metres"]
    return []


def _bounded_sample(arr: np.ndarray, max_side: int = 64, decimals: int = 4) -> List[List[Any]]:
    """≤ max_side×max_side 降采样样本（NaN/Inf → None，JSON 安全）。"""
    step = max(1, math.ceil(max(arr.shape) / max_side))
    sub = arr[::step, ::step]
    return [
        [None if not math.isfinite(v) else round(float(v), decimals) for v in row]
        for row in sub
    ]


def _base_diagnostics(
    transform: Sequence[float], rows: int, cols: int,
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
    source_path: str, filled: np.ndarray,
    transform: Tuple[float, ...], crs: str,
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
        raise RasterReaderError(f"cannot write filled raster {target!r}: {exc}") from exc
    # 绝对路径：validate_data_path 对 data_dir 内绝对路径放行，
    # 下游工具可直接把该返回值作为 raster_path 消费。
    return target


def _persist_raster_product(
    source_path: str, arr: np.ndarray,
    transform: Tuple[float, ...], crs: str,
    suffix: str, *,
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
        raise RasterReaderError(f"cannot write raster product {target!r}: {exc}") from exc
    return target


def register_terrain_tools(registry: ToolRegistry):

    @tool(registry, name="compute_terrain",
           description=(
               "地形分析（slope/aspect/hillshade）：基于 Copernicus DEM 30m 数据，一步生成多产品。"
               "\n何时用：山区选址；坡度大于 X° 的危险区域识别；hillshade 用于专题图美化底图。"
               "\n何时不用：(1) 只要原始 DEM — 用 fetch_dem；(2) 城市地形（精度需求 > 30m）— 当前不支持。"
               "\n关键约束：products 可选 slope/aspect/hillshade，默认全部；bbox 跨省会超时。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]，如 [116.2, 39.7, 116.6, 40.1]",
               "products": "分析产品列表，可选: 'slope'(坡度), 'aspect'(坡向), 'hillshade'(山体阴影)，默认全部",
           },
           side_effect="cacheable_read",
           network=True,
           deterministic=False,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("坡度", "坡向", "山体阴影", "dem", "地形", "hillshade"),
           output_semantic_type="stats",
           result_size_policy="bounded",
           crs_semantics="wgs84",
           failure_modes=("timeout", "network_error", "empty_result"))
    async def compute_terrain(bbox: str, products: list[str] | None = None) -> dict:
        try:
            parts = parse_bbox(bbox)
            return await spectral_engine.compute_terrain(parts, products)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}

    @tool(registry, name="compute_vegetation_index",
           description=(
               "在线遥感指数统一入口：NDVI(植被)/NDWI(水体)/NBR(燃烧)/EVI(增强植被)，"
               "以及 NDWI 拆名变体 ndwi_water(=ndwi，McFeeters 开放水体显式别名)/"
               "ndwi_gao(Gao 1996 植被水分，nir−swir1——与 ndwi 开放水体不可互换)，"
               "自动 STAC 拉 Sentinel-2 波段并计算。"
               "\n何时用：除 NDVI 外的指数（NDWI 水体面积、NBR 火烧迹地、EVI 高生物量）；指数随场景动态选择时。"
               "\n何时不用：(1) 只算 NDVI — 直接 compute_ndvi（接口更窄、参数更少）；"
               "(2) 要双时相对比 — 用 detect_vegetation_change；"
               "(3) 本地 TIFF 处理 — 用 analyze_vegetation_index。"
               "\n关键约束：index_type ∈ {ndvi, ndwi, ndwi_water, ndwi_gao, nbr, evi}；"
               "返回 {stats, classification, bbox}。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD",
               "index_type": "指数类型: 'ndvi'(默认), 'ndwi'/'ndwi_water'(开放水体), "
                             "'ndwi_gao'(植被水分), 'nbr', 'evi'",
           },
           side_effect="cacheable_read",
           network=True,
           deterministic=False,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("ndvi", "ndwi", "nbr", "evi", "植被指数", "遥感"),
           output_semantic_type="stats",
           result_size_policy="bounded",
           crs_semantics="wgs84",
           failure_modes=("network_error", "empty_result", "invalid_args"))
    async def compute_vegetation_index(bbox: str, date_from: str, date_to: str,
                                        index_type: str = "ndvi") -> dict:
        try:
            parts = parse_bbox(bbox)
            return await spectral_engine.compute_vegetation_index(parts, date_from, date_to, index_type)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}

    # ── VNext 地形科学工具（本地 DEM 栅格；实现见 app/lib/geo_analysis/terrain.py）──

    @tool(registry, name="terrain_derivatives",
           description=(
               "DEM 地形衍生指标一步计算：TPI(地形位置)/TRI(崎岖度)/roughness(粗糙度)/"
               "plan_curvature(平面曲率)/profile_curvature(剖面曲率)，返回统计+降采样样本+科学证据。"
               "\n何时用：地貌分类（Weiss 双尺度 TPI）、地表粗糙度/曲率对水文与工程的意义评估。"
               "\n何时不用：(1) 在线 DEM 取数+坡度 — compute_terrain；(2) 需要坡向/山体阴影 — compute_terrain。"
               "\n关键约束：本地 DEM GeoTIFF（data_dir 内）；曲率单位 z·cell⁻²；窗口 3-101 奇数。"
           ),
           tier=2, domains=["raster"], cost="medium",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内，如 fetch_dem 的产物）",
               "derivative": "指标: 'tpi' | 'tri' | 'roughness' | 'plan_curvature' | 'profile_curvature'",
               "window": "TPI/粗糙度窗口（奇数 3-101，默认 3）",
               "z_factor": "垂直单位比例（z 米/值；英尺 DEM ≈0.3048，默认 1）",
               "nodata": "可选 nodata 覆盖值（缺省用文件声明/NaN）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="heavy",
           scale_class="large",
           tags=("tpi", "tri", "地形", "曲率", "粗糙度", "dem"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def terrain_derivatives(raster_path: str, derivative: str, window: int = 3,
                            z_factor: float = 1, nodata: float | None = None) -> dict:
        contract_params: Dict[str, Any] = {
            "derivative": derivative, "window": window, "z_factor": z_factor,
        }
        if nodata is not None:
            contract_params["nodata"] = nodata
        params = apply_contract("terrain_derivative", contract_params)
        derivative = str(params["derivative"])
        window = int(params["window"])
        z_factor = float(params["z_factor"])
        nodata_v = params.get("nodata")
        nodata_v = float(nodata_v) if nodata_v is not None else None

        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(raster_path, nodata_v)
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        warnings = _non_metric_warning(crs)
        z = arr * z_factor

        # z = arr·z_factor —— nodata 标记值随比例同步缩放后再交给 lib。
        scaled_nodata = None if eff_nodata is None else eff_nodata * z_factor
        if derivative == "tpi":
            result, meta = terrain_lib.topographic_position_index(
                z, window=window, nodata=scaled_nodata)
        elif derivative == "tri":
            result, meta = terrain_lib.terrain_ruggedness_index(z, nodata=scaled_nodata)
        elif derivative == "roughness":
            result, meta = terrain_lib.roughness(z, window=window, nodata=scaled_nodata)
        else:
            curv, meta = terrain_lib.surface_curvature(
                z, cy, cell_size_x=cx, nodata=scaled_nodata)
            result = curv["plan"] if derivative == "plan_curvature" else curv["profile"]
            meta["derivative"] = derivative

        payload = {
            "success": True,
            "derivative": derivative,
            "statistics": compute_raster_stats(result),
            "sample": _bounded_sample(result),
            "meta": meta,
        }
        return _terrain_evidence(
            payload, _DERIVATIVE_ALGORITHMS[derivative],
            tool="terrain_derivatives",
            parameters_applied={
                "derivative": derivative, "window": window,
                "z_factor": z_factor, "nodata": nodata_v,
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform, arr.shape[0], arr.shape[1],
                extra=(Diagnostic(name="window", value=float(window)),
                       Diagnostic(name="z_factor", value=z_factor),
                       Diagnostic(name="nodata_effective", value=eff_nodata))),
            transformations=transformations or None,
            warnings=warnings or None,
        )

    @tool(registry, name="viewshed_analysis",
           description=(
               "DEM 视域分析：观察点可见范围布尔判定（视线角扇区扫描），返回可见比例/可见面积/科学证据。"
               "\n何时用：瞭望塔/监控选址、景观通视评价、军事/规划遮蔽分析。"
               "\n何时不用：(1) 成本路径/可达性 — 网络分析；(2) 简单缓冲 — buffer_analysis。"
               "\n关键约束：无地球曲率/大气折射；观察者默认离地 2m；最大视线距离 10-50000m；"
               "地理 DEM 自动按 cos(lat) 换算米制距离。"
           ),
           tier=2, domains=["raster"], cost="medium",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "observer_x": "观察点世界 x（栅格 CRS 单位）",
               "observer_y": "观察点世界 y（栅格 CRS 单位）",
               "observer_height": "观察者离地高度（米，默认 2）",
               "target_height": "目标离地高度（米，默认 0 = 地表）",
               "max_distance": "最大视线距离（米，默认 5000）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="heavy",
           scale_class="large",
           tags=("视域", "可见性", "viewshed", "通视", "瞭望"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def viewshed_analysis(raster_path: str, observer_x: float, observer_y: float,
                          observer_height: float = 2, target_height: float = 0,
                          max_distance: float = 5000) -> dict:
        params = apply_contract("viewshed_analysis", {
            "observer_x": observer_x, "observer_y": observer_y,
            "observer_height": observer_height, "target_height": target_height,
            "max_distance": max_distance,
        })
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(raster_path, None)
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        res, meta = terrain_lib.viewshed(
            arr, cy, cell_size_x=cx,
            observer_xy=(float(params["observer_x"]), float(params["observer_y"])),
            transform=transform,
            observer_height=float(params["observer_height"]),
            target_height=float(params["target_height"]),
            max_distance=float(params["max_distance"]),
            nodata=eff_nodata,
        )
        payload = {
            "success": True,
            "visible_fraction": res["visible_fraction"],
            "visible_area_m2": round(float(res["visible_area_m2"]), 3),
            "visible_cells": int(res["visible"].sum()),
            "meta": meta,
        }
        return _terrain_evidence(
            payload, "terrain.viewshed", tool="viewshed_analysis",
            parameters_applied={
                "observer_x": float(params["observer_x"]),
                "observer_y": float(params["observer_y"]),
                "observer_height": float(params["observer_height"]),
                "target_height": float(params["target_height"]),
                "max_distance": float(params["max_distance"]),
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform, arr.shape[0], arr.shape[1],
                extra=(Diagnostic(name="nodata_effective", value=eff_nodata),)),
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="flow_analysis",
           description=(
               "DEM D8 水文分析：flow_direction（ESRI 2 的幂编码 1=E…128=NE，0=汇/出口）或 "
               "flow_accumulation（上游贡献像元数，不含自身），返回统计+降采样样本+科学证据。"
               "\n何时用：河网提取前奏、汇流趋势/流域湿润度快速评估。"
               "\n何时不用：(1) 需要流域边界 — watershed_delineation；(2) 多向流 — 用 dinf_flow_analysis（terrain.dinf_flow）。"
               "\n关键约束：默认平地/洼地即汇（flat_routing='none'，不填洼）；"
               "flat_routing='epsilon' 先经 Barnes 2014 填洼注入梯度再路由（平地排向溢流出口）；边界=出口。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "product": "输出产品: 'flow_accumulation'(默认) | 'flow_direction'",
               "flat_routing": "平地路由: 'none'(默认，平地即汇) | 'epsilon'(Barnes 2014 填洼后路由)",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("流向", "汇流", "d8", "水文", "flow accumulation"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def flow_analysis(raster_path: str, product: str = "flow_accumulation",
                      flat_routing: str = "none") -> dict:
        params = apply_contract("flow_analysis", {
            "product": product, "flat_routing": flat_routing,
        })
        product = str(params["product"])
        flat_routing_v = str(params["flat_routing"])
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(raster_path, None)
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)

        d8, d8_meta = terrain_lib.d8_flow(
            arr, cy, cell_size_x=cx, nodata=eff_nodata, flat_routing=flat_routing_v)
        meta = dict(d8_meta)
        if product == "flow_direction":
            codes = d8["direction"][d8["valid"]]
            unique, counts = np.unique(codes, return_counts=True)
            direction_counts = {str(int(c)): int(n) for c, n in zip(unique, counts)}
            statistics: Dict[str, Any] = {
                "direction_counts": direction_counts,
                "sink_or_outlet_cells": direction_counts.get("0", 0),
                "valid_cells": int(d8["valid"].sum()),
            }
            sample = _bounded_sample(d8["direction"].astype("float64"))
        else:
            acc, acc_meta = terrain_lib.flow_accumulation(d8)
            meta["accumulation"] = acc_meta["counting_convention"]
            statistics = compute_raster_stats(acc)
            statistics["max_accumulation"] = int(acc.max())
            sample = _bounded_sample(acc)

        payload = {
            "success": True,
            "product": product,
            "statistics": statistics,
            "sample": sample,
            "meta": meta,
        }
        return _terrain_evidence(
            payload, "terrain.flow", tool="flow_analysis",
            parameters_applied={"product": product, "flat_routing": flat_routing_v},
            crs=crs,
            diagnostics=_base_diagnostics(
                transform, arr.shape[0], arr.shape[1],
                extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                       Diagnostic(name="flat_routing_filled_cells", value=float(
                           d8_meta.get("filled_cell_count", 0))
                           if flat_routing_v == "epsilon" else None))),
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="watershed_delineation",
           description=(
               "DEM 流域圈定：从汇入点（pour point，世界坐标）逆 D8 BFS 圈出全部上游贡献区，"
               "返回像元数/面积/边界采样点/科学证据。"
               "\n何时用：水文响应单元、上游污染/汇水范围划定。"
               "\n何时不用：(1) 只要流向/汇流栅格 — flow_analysis；(2) pour point 需要河道 snap — 当前不做（调用方自行对齐河道）。"
               "\n关键约束：pour point 取最近像元中心；D8 单向流语义；平地/洼地为汇。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "pour_x": "汇入点世界 x（栅格 CRS 单位；应落在河道上）",
               "pour_y": "汇入点世界 y（栅格 CRS 单位；应落在河道上）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("流域", "汇水区", "watershed", "上游", "水文"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def watershed_delineation(raster_path: str, pour_x: float, pour_y: float) -> dict:
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(raster_path, None)
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        mask, meta = terrain_lib.watershed(
            arr, cy, [(float(pour_x), float(pour_y))],
            transform=transform, cell_size_x=cx, nodata=eff_nodata,
        )

        # 边界像元（4 邻域出掩膜或出网格 —— 网格外视作流域外）→ 世界坐标
        # 像元中心，≤256 点。
        h, w = mask.shape
        padded = np.zeros((h + 2, w + 2), dtype=bool)
        padded[1:-1, 1:-1] = mask
        interior4 = (padded[:-2, 1:-1] & padded[2:, 1:-1]
                     & padded[1:-1, :-2] & padded[1:-1, 2:])
        edge_rows, edge_cols = np.nonzero(mask & ~interior4)
        # 注（science-v3 审计复核）：GDAL 6 参数 transform 的原点是 UL
        # 角点 —— 实测 t*(0,0)=(0,210)=栅格角、src.xy(0,0)=(5,205)=中心，
        # 故像元中心映射必须 +0.5（此处原实现正确，审计 F3 判定有误，
        # 不采纳其删 +0.5 的建议）。
        centers_col = edge_cols.astype("float64") + 0.5
        centers_row = edge_rows.astype("float64") + 0.5
        a, b, c, d, e, f = transform
        xs = a * centers_col + b * centers_row + c
        ys = d * centers_col + e * centers_row + f
        pts = [[round(float(x), 6), round(float(y), 6)] for x, y in zip(xs, ys)]
        if len(pts) > 256:
            idx = np.linspace(0, len(pts) - 1, 256).astype(int)
            pts = [pts[int(i)] for i in idx]

        payload = {
            "success": True,
            "cell_count": int(mask.sum()),
            "area_m2": round(float(mask.sum()) * cx * cy, 3),
            "boundary_sample": pts,
            "boundary_sample_note": "subset of watershed-boundary cell centers (<=256 points)" if len(edge_rows) > 256 else "all watershed-boundary cell centers",
            "meta": meta,
        }
        return _terrain_evidence(
            payload, "terrain.watershed", tool="watershed_delineation",
            parameters_applied={"pour_x": float(pour_x), "pour_y": float(pour_y)},
            crs=crs,
            diagnostics=_base_diagnostics(
                transform, arr.shape[0], arr.shape[1],
                extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                       Diagnostic(name="watershed_cells", value=float(mask.sum())))),
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="extract_contours",
           description=(
               "DEM 等值线提取：marching squares → GeoJSON FeatureCollection(LineString, 带 level 属性)，"
               "顶点已映射到世界坐标；nodata 区自动断线。"
               "\n何时用：等高线制图、水位/高程阈值线提取。"
               "\n何时不用：(1) 需要标注平滑的制图级等高线 — 前端渲染；(2) 面要素（高程带）— raster_reclassify。"
               "\n关键约束：levels（显式列表）> interval（自最低值等间隔）> n_levels（默认 10 等间隔）。"
           ),
           tier=2, domains=["raster"], cost="medium",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "levels": "显式等值线水平列表（如 [100, 200, 300]；优先于 interval/n_levels）",
               "n_levels": "等间隔水平数（2-30，默认 10）",
               "interval": "等值线间隔（如 50；自数据最低值起）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="heavy",
           scale_class="large",
           tags=("等高线", "等值线", "contour", "制图", "dem"),
           output_semantic_type="geojson_fc",
           result_size_policy="bounded",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data"))
    def extract_contours(raster_path: str, levels: list[float] | None = None,
                         n_levels: int = 10, interval: float | None = None) -> dict:
        # 评审 M3：显式 levels 绕过契约的 n_levels 上限 —— 语义等价界
        # （n_levels 2-30）：≤30 个显式等值面，超出即拒绝。
        if levels is not None and len(levels) > 30:
            raise ValueError(
                f"levels 列表超限：{len(levels)} > 30（与 n_levels 契约上限一致）")
        contract_params: Dict[str, Any] = {"n_levels": n_levels}
        if interval is not None:
            contract_params["interval"] = interval
        params = apply_contract("extract_contours", contract_params)
        n_levels = int(params["n_levels"])
        interval_v = params.get("interval")
        interval_v = float(interval_v) if interval_v is not None else None
        levels_v: Optional[List[float]] = None
        if isinstance(levels, str):
            try:
                parsed = json.loads(levels)
                levels_v = [float(v) for v in parsed] if isinstance(parsed, list) else [float(parsed)]
            except (ValueError, TypeError):
                levels_v = [float(v) for v in levels.replace(",", " ").split()]
        elif levels:
            levels_v = [float(v) for v in levels]

        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(raster_path, None)
        fc, meta = terrain_lib.extract_contours(
            arr, transform=transform, levels=levels_v,
            n_levels=n_levels, interval=interval_v, nodata=eff_nodata,
        )
        fc_trimmed = trim_features(fc)
        payload = {
            "success": True,
            "feature_count": len(fc_trimmed["features"]),
            "contours": fc_trimmed,
            "meta": meta,
        }
        return _terrain_evidence(
            payload, "terrain.contours", tool="extract_contours",
            parameters_applied={
                "n_levels": n_levels,
                "interval": interval_v,
                "levels": levels_v,
                "explicit_levels": bool(levels_v is not None),
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform, arr.shape[0], arr.shape[1],
                extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                       Diagnostic(name="features", value=float(payload["feature_count"])))),
            warnings=_non_metric_warning(crs) or None,
        )

    # ── Foundation V2（A5）：水文与地貌量测工具（护栏/换算/证据同构）──

    def _load_dem(raster_path: str, nodata_override: Optional[float]):
        """读 DEM + 米制像元（depression_fill / openness / geomorphons 等）。"""
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            raster_path, nodata_override)
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        return arr, transform, crs, eff_nodata, bounds, cy, cx, transformations

    def _dem_slope_and_accum(
        raster_path: str, z_factor: float,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Tuple[float, ...], str, List[str], Optional[float], float, float]:
        """DEM → (z 掩膜数组, slope_deg, flow_accum, transform, crs, 变换, nodata, cy, cx)。

        供 TWI/SPI/LS 工具复用。变换披露：z = arr·z_factor；nodata 标记
        像元 → NaN（坡度/水文在接缝处不被哨兵值污染）；slope = Horn
        （band_math.compute_slope，度）；accum = D8 拓扑累积。
        """
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(raster_path, None)
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        z = arr * z_factor
        if eff_nodata is not None:
            z = np.where(arr == eff_nodata, np.nan, z)
        transformations = list(transformations) + [
            f"z = arr * {z_factor} (vertical units)",
            "nodata-marked cells -> NaN before slope/hydrology",
            "slope = Horn 3x3 (band_math.compute_slope, degrees)",
        ]
        slope_deg = compute_slope(z, cy, cell_size_x=cx)
        d8, _ = terrain_lib.d8_flow(z, cy, cell_size_x=cx, nodata=eff_nodata)
        acc, _ = terrain_lib.flow_accumulation(d8)
        return z, slope_deg, acc, transform, crs, transformations, eff_nodata, cy, cx

    @tool(registry, name="depression_fill",
           description=(
               "DEM 填洼（Priority-Flood，Barnes 2014）：洼地填至溢流高程；"
               "epsilon>0 变体给平地注入梯度 → 填后表面严格单调可排（D8/D∞ 前置）。"
               "\n何时用：水文分析前的 DEM 预处理（平地/洼地即汇的解药）。"
               "\n何时不用：(1) 只要洼地/汇位置 — flow_analysis 的 sink 统计；"
               "(2) 需要填洼后流向 — persist_filled=True 持久化填充面后，"
               "把返回的 filled_raster_path 喂给 flow_analysis / dinf_flow_analysis。"
               "\n关键约束：nodata/边界视作排水出口；返回填深统计+降采样样本+科学证据。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "epsilon": "逐像元抬升量（米，默认 0 = 纯填洼；如 0.01 → 单调可排）",
               "nodata": "可选 nodata 覆盖值（缺省用文件声明/NaN）",
               "persist_filled": "持久化填充后 DEM（*_filled.tif）并返回路径，供下游水文工具消费",
           },
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("填洼", "洼地", "priority flood", "dem预处理", "水文"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           # review R2-2：persist_filled=True 会写 data_dir（*_filled.tif）
           # —— 按「可能写」诚实标注；缺省 persist_filled=False 仍为纯计算。
           side_effect="artifact_creation",
           data_mutations=("artifact_write",),
           failure_modes=("invalid_args", "missing_data", "memory"))
    def depression_fill(raster_path: str, epsilon: float = 0.0,
                        nodata: float | None = None,
                        persist_filled: bool = False) -> dict:
        contract_params: Dict[str, Any] = {"epsilon": epsilon}
        if nodata is not None:
            contract_params["nodata"] = nodata
        contract_params["persist_filled"] = bool(persist_filled)
        params = apply_contract("sink_fill", contract_params)
        epsilon_v = float(params["epsilon"])
        nodata_v = params.get("nodata")
        nodata_v = float(nodata_v) if nodata_v is not None else None
        persist_v = bool(params.get("persist_filled", False))

        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, nodata_v)
        filled, meta = terrain_lib.fill_depressions(
            arr, cy, cell_size_x=cx, epsilon=epsilon_v, nodata=eff_nodata)
        depth = filled - arr
        payload = {
            "success": True,
            "filled_volume_z_m2": meta["filled_volume"],
            "filled_cell_count": meta["filled_cell_count"],
            "max_fill_depth": meta["max_fill_depth"],
            "statistics": compute_raster_stats(depth),
            "sample": _bounded_sample(filled),
            "meta": meta,
        }
        if persist_v:
            filled_path = _persist_filled_dem(
                raster_path, filled, transform, crs, eff_nodata)
            payload["filled_raster_path"] = filled_path
            transformations = list(transformations or []) + [
                "filled DEM persisted as GeoTIFF for downstream hydrology tools"]
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="epsilon", value=epsilon_v),
                   Diagnostic(name="filled_cells", value=float(meta["filled_cell_count"]))))
        backend = _backend_diagnostic("terrain.sink_fill", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.sink_fill", tool="depression_fill",
            parameters_applied={"epsilon": epsilon_v, "nodata": nodata_v},
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="dinf_flow_analysis",
           description=(
               "DEM D∞ 多向流（Tarboton 1997）：flow_direction（弧度角，x=东 y=北，"
               "-1=平地/洼地哨兵）或 flow_accumulation（面内角度比例分流累积）。"
               "\n何时用：需要比 D8 更平滑的汇流方向/分配（格网平行流向偏差缓解）。"
               "\n何时不用：(1) DEM 有洼地 — 先 depression_fill(epsilon>0)；"
               "(2) 只要简单 D8 — flow_analysis。"
               "\n关键约束：函数内不填洼（平地/洼地 = 哨兵 -1，诚实披露）。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "product": "输出产品: 'flow_accumulation'(默认) | 'flow_direction'",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("dinf", "多向流", "流向", "汇流", "水文"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def dinf_flow_analysis(raster_path: str, product: str = "flow_accumulation") -> dict:
        params = apply_contract("dinf_analysis", {"product": product})
        product = str(params["product"])
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        dinf, meta = terrain_lib.dinf_flow_direction(
            arr, cy, cell_size_x=cx, nodata=eff_nodata)

        if product == "flow_direction":
            ang = dinf["angle"]
            valid = dinf["valid"]
            no_flow = int(np.count_nonzero(valid & (ang == terrain_lib._DINF_NO_FLOW)))
            statistics: Dict[str, Any] = {
                "valid_cells": int(valid.sum()),
                "no_flow_cells": no_flow,
                "nodata_cells": int((~valid).sum()),
            }
            sample = _bounded_sample(ang)
        else:
            acc, acc_meta = terrain_lib.dinf_flow_accumulation(dinf)
            meta["accumulation"] = acc_meta["counting_convention"]
            statistics = compute_raster_stats(acc)
            statistics["max_accumulation"] = round(float(acc.max()), 6)
            sample = _bounded_sample(acc)

        payload = {
            "success": True,
            "product": product,
            "statistics": statistics,
            "sample": sample,
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),))
        backend = _backend_diagnostic("terrain.dinf_flow", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.dinf_flow", tool="dinf_flow_analysis",
            parameters_applied={"product": product},
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="flow_length_analysis",
           description=(
               "DEM 流程长度（米，D8 接收者拓扑）：downstream = 沿流路到出口的累计"
               "距离；upstream = 距最远山脊源的累计距离（MAX 口径）。"
               "\n何时用：USLE 坡长因子输入、流域汇水时间估算、Horton 分析前奏。"
               "\n何时不用：(1) 需要多向流路径长 — D∞ 路径长未实现（D8 折线口径）；"
               "(2) 汇流面积 — flow_analysis。"
               "\n关键约束：继承 D8 语义（平地/洼地即出口，长度归零）。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "mode": "长度口径: 'downstream'(默认) | 'upstream'",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("流程长度", "坡长", "flow length", "汇流时间", "usle"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def flow_length_analysis(raster_path: str, mode: str = "downstream") -> dict:
        params = apply_contract("flow_length_analysis", {"mode": mode})
        mode_v = str(params["mode"])
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        d8, _d8_meta = terrain_lib.d8_flow(arr, cy, cell_size_x=cx, nodata=eff_nodata)
        lengths, meta = terrain_lib.flow_length(d8, mode_v, cy, cell_size_x=cx)
        payload = {
            "success": True,
            "mode": mode_v,
            "max_length_m": meta["max_length_m"],
            "mean_length_m": meta["mean_length_m"],
            "statistics": compute_raster_stats(lengths),
            "sample": _bounded_sample(lengths),
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="max_length_m", value=float(meta["max_length_m"]))))
        backend = _backend_diagnostic("terrain.flow_length", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.flow_length", tool="flow_length_analysis",
            parameters_applied={"mode": mode_v},
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="stream_network",
           description=(
               "DEM 河网提取：汇流累积 ≥ 阈值 → 河网掩膜（stream_mask）或 Strahler "
               "河流分级图 + 等级分布（stream_order）。"
               "\n何时用：水系制图、流域形态量测（排水密度）输入、栖息地走廊分析。"
               "\n何时不用：(1) 阈值未知想看累积分布 — flow_analysis 先行；"
               "(2) DEM 有大量洼地 — 先 depression_fill 否则河网破碎。"
               "\n关键约束：阈值单位 = 上游贡献像元数（按流域尺度率定，无普适默认）。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "threshold": "河网阈值（上游贡献像元数，≥1；如 100、1000）",
               "product": "输出产品: 'stream_order'(默认) | 'stream_mask'",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("河网", "strahler", "水系", "河流分级", "水文"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def stream_network(raster_path: str, threshold: float,
                       product: str = "stream_order") -> dict:
        params = apply_contract("stream_network", {"threshold": threshold, "product": product})
        threshold_v = float(params["threshold"])
        product_v = str(params["product"])
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        d8, _d8_meta = terrain_lib.d8_flow(arr, cy, cell_size_x=cx, nodata=eff_nodata)
        acc, _acc_meta = terrain_lib.flow_accumulation(d8)

        if product_v == "stream_order":
            order, meta = terrain_lib.stream_order(d8, acc, threshold_v)
            statistics: Dict[str, Any] = {
                "order_distribution": meta["order_distribution"],
                "max_order": meta["max_order"],
                "stream_cells": meta["stream_cells"],
            }
            sample = _bounded_sample(order.astype("float64"))
            algorithm_id = "terrain.strahler"
        else:
            mask, meta = terrain_lib.extract_streams(acc, threshold_v)
            statistics = {
                "stream_cells": meta["stream_cells"],
                "cells_total": meta["cells_total"],
                "stream_fraction": round(float(mask.sum()) / max(1, mask.size), 6),
            }
            sample = _bounded_sample(mask.astype("float64"))
            algorithm_id = "terrain.streams"

        payload = {
            "success": True,
            "product": product_v,
            "threshold": threshold_v,
            "statistics": statistics,
            "sample": sample,
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="threshold", value=threshold_v)))
        backend = _backend_diagnostic(algorithm_id, arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, algorithm_id, tool="stream_network",
            parameters_applied={"threshold": threshold_v, "product": product_v},
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="watershed_morphometry_analysis",
           description=(
               "流域形态量测（Strahler 1957）：pour point 逆 D8 圈流域后一次性给出 "
               "面积/周长/盆地长（MAX 流程长口径）/form factor/elongation/relief/"
               "relief ratio；给 stream_threshold 时附河网长度与排水密度。"
               "\n何时用：水文响应单元刻画、流域对比（Horton-Strahler 定律）。"
               "\n何时不用：(1) 只要流域掩膜 — watershed_delineation；"
               "(2) 只要河网 — stream_network。"
               "\n关键约束：pour point 取最近像元中心；盆地长 = MAX 上游流程长（文档化）。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "pour_x": "pour point 世界 x（栅格 CRS 单位；应落在河道上）",
               "pour_y": "pour point 世界 y（栅格 CRS 单位）",
               "stream_threshold": "可选河网阈值（上游像元数；给定时计算排水密度）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("流域形态", "morphometry", "排水密度", "盆地", "horton"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def watershed_morphometry_analysis(raster_path: str, pour_x: float, pour_y: float,
                                       stream_threshold: float | None = None) -> dict:
        contract_params: Dict[str, Any] = {"pour_x": pour_x, "pour_y": pour_y}
        if stream_threshold is not None:
            contract_params["stream_threshold"] = stream_threshold
        params = apply_contract("morphometry_analysis", contract_params)
        threshold_v = params.get("stream_threshold")
        threshold_v = float(threshold_v) if threshold_v is not None else None

        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        d8, _d8_meta = terrain_lib.d8_flow(arr, cy, cell_size_x=cx, nodata=eff_nodata)
        metrics, meta = terrain_lib.watershed_morphometry(
            arr, d8, (float(params["pour_x"]), float(params["pour_y"])), cy,
            cell_size_x=cx, transform=transform, stream_threshold=threshold_v,
            nodata=eff_nodata)
        payload = {
            "success": True,
            "metrics": metrics,
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="basin_cells", value=float(metrics["cell_count"]))))
        backend = _backend_diagnostic("terrain.morphometry", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.morphometry", tool="watershed_morphometry_analysis",
            parameters_applied={
                "pour_x": float(params["pour_x"]), "pour_y": float(params["pour_y"]),
                "stream_threshold": threshold_v,
            },
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="topographic_index",
           description=(
               "DEM 湿润/水力指数：TWI = ln(SCA/tanβ)（Beven-Kirkby 1979，湿润度）"
               "或 SPI = SCA·tanβ（水力侵蚀潜势）。内部自动计算 Horn 坡度与 D8 汇流。"
               "\n何时用：土壤湿润格局、潜在饱和带识别、侵蚀潜势粗评。"
               "\n何时不用：(1) 需要降雨-径流过程模拟 — 静态地形指数不适用；"
               "(2) 需要多向流 SCA — 当前 κ=1 单流向口径（披露）。"
               "\n关键约束：SCA 等流宽度 = cell_size；tanβ 下限 1e-6（平地为截断上界）。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "product": "输出产品: 'twi'(默认) | 'spi'",
               "z_factor": "垂直单位比例（英尺 DEM ≈0.3048，默认 1）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("twi", "spi", "湿润度", "地形指数", "饱和带"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def topographic_index(raster_path: str, product: str = "twi",
                          z_factor: float = 1) -> dict:
        params = apply_contract("wetness_index", {"product": product})
        product_v = str(params["product"])
        z, slope_deg, acc, transform, crs, transformations, eff_nodata, cy, cx = \
            _dem_slope_and_accum(raster_path, float(z_factor))
        if product_v == "twi":
            result, meta = terrain_lib.topographic_wetness_index(
                slope_deg, acc, cy, cell_size_x=cx, slope_units="degrees")
            algorithm_id = "terrain.twi"
        else:
            result, meta = terrain_lib.stream_power_index(
                slope_deg, acc, cy, cell_size_x=cx, slope_units="degrees")
            algorithm_id = "terrain.spi"
        payload = {
            "success": True,
            "product": product_v,
            "statistics": compute_raster_stats(result),
            "sample": _bounded_sample(result),
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, slope_deg.shape[0], slope_deg.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="z_factor", value=float(z_factor))))
        backend = _backend_diagnostic(algorithm_id, result.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, algorithm_id, tool="topographic_index",
            parameters_applied={"product": product_v, "z_factor": float(z_factor)},
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="ls_factor_analysis",
           description=(
               "USLE LS 因子栅格：mccool（坡长经验式，Wischmeier-Smith 1978 + McCool "
               "1987 m 表）或 desmet_govers（比集水面积式，Desmet & Govers 1996）。"
               "\n何时用：土壤流失通用方程的坡长坡度因子输入、侵蚀敏感性制图。"
               "\n何时不用：(1) 完整 USLE/RUSLE 侵蚀量 — 还需 R/K/C/P 因子；"
               "(2) 流程长度本身 — flow_length_analysis。"
               "\n关键约束：desmet_govers 自动用内部 D8 汇流做 SCA；n=1.3 固定。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "method": "方法: 'mccool'(默认) | 'desmet_govers'",
               "flow_length": "mccool 坡长 λ（米，默认 100；建议用上游流程长度替代）",
               "z_factor": "垂直单位比例（英尺 DEM ≈0.3048，默认 1）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("usle", "ls因子", "土壤侵蚀", "坡长坡度", "侵蚀制图"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def ls_factor_analysis(raster_path: str, method: str = "mccool",
                           flow_length: float = 100.0, z_factor: float = 1) -> dict:
        params = apply_contract("ls_factor_analysis", {
            "method": method, "flow_length": flow_length,
        })
        method_v = str(params["method"])
        flow_length_v = float(params["flow_length"])
        z, slope_deg, acc, transform, crs, transformations, eff_nodata, cy, cx = \
            _dem_slope_and_accum(raster_path, float(z_factor))
        result, meta = terrain_lib.ls_factor(
            slope_deg, flow_length_v, cy, cell_size_x=cx, method=method_v,
            slope_units="percent",
            flow_accum=acc if method_v == "desmet_govers" else None)
        payload = {
            "success": True,
            "method": method_v,
            "statistics": compute_raster_stats(result),
            "sample": _bounded_sample(result),
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, slope_deg.shape[0], slope_deg.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="z_factor", value=float(z_factor)),
                   Diagnostic(name="flow_length_m", value=flow_length_v)))
        backend = _backend_diagnostic("terrain.ls_factor", result.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.ls_factor", tool="ls_factor_analysis",
            parameters_applied={
                "method": method_v, "flow_length": flow_length_v,
                "z_factor": float(z_factor),
            },
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="terrain_openness_analysis",
           description=(
               "DEM 地形开放度（Yokoyama 2002）：positive = 周边俯角均值（山脊/开阔 "
               "高），negative = 仰角均值（谷地/封闭高），单位度。"
               "\n何时用：地貌制图（脊/谷增强）、边坡稳定与风场暴露粗评、天际线分析。"
               "\n何时不用：(1) 需要 TPI 地类分级 — landform_classify；"
               "(2) 视域布尔判定 — viewshed_analysis。"
               "\n关键约束：半径 ≤100 像元；无采样方位从均值剔除（栅格角隅披露）。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "radius_cells": "搜索半径（像元，1-100，默认 8）",
               "azimuth_count": "方位数（4-64 等角距，默认 16）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("开放度", "openness", "脊谷", "地貌", "天际线"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def terrain_openness_analysis(raster_path: str, radius_cells: int = 8,
                                  azimuth_count: int = 16) -> dict:
        params = apply_contract("openness_analysis", {
            "radius_cells": radius_cells, "azimuth_count": azimuth_count,
        })
        radius_v = int(params["radius_cells"])
        azimuths_v = int(params["azimuth_count"])
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        res, meta = terrain_lib.terrain_openness(
            arr, cy, cell_size_x=cx, radius_cells=radius_v,
            azimuth_count=azimuths_v, nodata=eff_nodata)
        payload = {
            "success": True,
            "positive_openness": {
                "statistics": compute_raster_stats(res["positive"]),
                "sample": _bounded_sample(res["positive"]),
            },
            "negative_openness": {
                "statistics": compute_raster_stats(res["negative"]),
                "sample": _bounded_sample(res["negative"]),
            },
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="radius_cells", value=float(radius_v))))
        backend = _backend_diagnostic("terrain.openness", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.openness", tool="terrain_openness_analysis",
            parameters_applied={"radius_cells": radius_v, "azimuth_count": azimuths_v},
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="geomorphon_analysis",
           description=(
               "DEM geomorphons 地貌形态分类（Jasiewicz & Stepinski 2013）：8 方位视线"
               "三元码 → 10 类（flat/summit/ridge/shoulder/spur/slope/hollow/"
               "footslope/valley/depression），返回类图 + 类分布。"
               "\n何时用：无监督地貌制图、土地形态单元划分、遥感地貌对比。"
               "\n何时不用：(1) 需要绝对坡度分级 — compute_terrain；"
               "(2) 双尺度 TPI 地类 — landform_classify。"
               "\n关键约束：相对高程形态学（缓坡大尺度可判 flat）；flatten 容差按 DEM 噪声定。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "lookup_radius_cells": "视线查找半径（像元，1-128，默认 8）",
               "flatten": "平地容差（度，默认 0；建议 ≈ DEM 高程噪声）",
               "far": "近场跳过半径（像元，默认 0 = 不跳过）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("地貌分类", "geomorphon", "形态单元", "土地形态", "dem"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def geomorphon_analysis(raster_path: str, lookup_radius_cells: int = 8,
                            flatten: float = 0.0, far: float = 0.0) -> dict:
        params = apply_contract("geomorphon_analysis", {
            "lookup_radius_cells": lookup_radius_cells, "flatten": flatten, "far": far,
        })
        lookup_v = int(params["lookup_radius_cells"])
        flatten_v = float(params["flatten"])
        far_v = float(params["far"])
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        res, meta = terrain_lib.geomorphons(
            arr, cy, cell_size_x=cx, lookup_radius_cells=lookup_v,
            flatten=flatten_v, far=far_v, nodata=eff_nodata)
        payload = {
            "success": True,
            "class_distribution": meta["class_distribution"],
            "class_codes": meta["class_codes"],
            "sample": _bounded_sample(res["classes"].astype("float64")),
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="lookup_radius_cells", value=float(lookup_v)),
                   Diagnostic(name="flatten_degrees", value=flatten_v)))
        backend = _backend_diagnostic("terrain.geomorphons", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.geomorphons", tool="geomorphon_analysis",
            parameters_applied={
                "lookup_radius_cells": lookup_v, "flatten": flatten_v, "far": far_v,
            },
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="landform_classify",
           description=(
               "DEM 双尺度 TPI 地类分级（Weiss 2001）：10 类地貌（canyons…mountain "
               "tops…plains），返回类图 + 类分布 + 科学证据。"
               "\n何时用：地貌单元制图、生态分区、土壤-地形组合分析。"
               "\n何时不用：(1) 视线形态学 10 类 — geomorphon_analysis；"
               "(2) 常量面/无起伏 DEM — 分类阈值无定义（报错）。"
               "\n关键约束：TPI 窗口 3/25 格 + 百分位容差 0.1 为海报惯例起点（按尺度率定）。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "tpi_window_small": "小尺度 TPI 窗口（奇数 3-101，默认 3）",
               "tpi_window_large": "大尺度 TPI 窗口（奇数，默认 25）",
               "elevation_tolerance": "平地带高程百分位容差（0-0.5，默认 0.1）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("地类分级", "weiss", "tpi", "地貌单元", "landform"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def landform_classify(raster_path: str, tpi_window_small: int = 3,
                          tpi_window_large: int = 25,
                          elevation_tolerance: float = 0.1) -> dict:
        params = apply_contract("landform_analysis", {
            "tpi_window_small": tpi_window_small,
            "tpi_window_large": tpi_window_large,
            "elevation_tolerance": elevation_tolerance,
        })
        ws = int(params["tpi_window_small"])
        wl = int(params["tpi_window_large"])
        tol_v = float(params["elevation_tolerance"])
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        res, meta = terrain_lib.landform_classification(
            arr, cy, cell_size_x=cx, tpi_window_small=ws, tpi_window_large=wl,
            elevation_tolerance=tol_v, nodata=eff_nodata)
        payload = {
            "success": True,
            "class_distribution": meta["class_distribution"],
            "class_codes": meta["class_codes"],
            "sample": _bounded_sample(res["classes"].astype("float64")),
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="tpi_window_small", value=float(ws)),
                   Diagnostic(name="tpi_window_large", value=float(wl))))
        backend = _backend_diagnostic("terrain.landform", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.landform", tool="landform_classify",
            parameters_applied={
                "tpi_window_small": ws, "tpi_window_large": wl,
                "elevation_tolerance": tol_v,
            },
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="multiazimuth_hillshade",
           description=(
               "DEM 多方位山体阴影：多太阳方位照度合成（mean = 去阴影均值，min = "
               "制图最小），单方位公式与 compute_terrain 的 hillshade 逐位一致。"
               "\n何时用：地形制图增强（多方位去阴影）、坡向可视化。"
               "\n何时不用：(1) 单幅标准阴影 — compute_terrain；"
               "(2) 需要坡度/坡向产品 — compute_terrain。"
               "\n关键约束：altitude 0-90 度；azimuths 罗盘度列表（如 '315,135,45,225'）。"
           ),
           tier=2, domains=["raster"], cost="medium",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "altitude": "太阳高度角（度，0-90，默认 45）",
               "azimuths": "太阳方位列表（罗盘度，逗号分隔，默认 '315,135'）",
               "combine": "合成方式: 'mean'(默认) | 'min'",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="heavy",
           scale_class="large",
           tags=("山体阴影", "hillshade", "多方位", "地形制图", "晕渲"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def multiazimuth_hillshade(raster_path: str, altitude: float = 45.0,
                               azimuths: str | list[float] | None = None,
                               combine: str = "mean") -> dict:
        contract_params: Dict[str, Any] = {"altitude": altitude, "combine": combine}
        if azimuths is not None:
            contract_params["azimuths"] = (
                azimuths if isinstance(azimuths, str) else ",".join(str(a) for a in azimuths))
        params = apply_contract("hillshade_multiazimuth", contract_params)
        altitude_v = float(params["altitude"])
        combine_v = str(params["combine"])
        az_raw = params.get("azimuths") or "315,135"
        if isinstance(az_raw, str):
            try:
                parsed = json.loads(az_raw)
                az_list = [float(v) for v in parsed] if isinstance(parsed, list) else [float(parsed)]
            except (ValueError, TypeError):
                az_list = [float(v) for v in az_raw.replace(",", " ").split()]
        else:
            az_list = [float(v) for v in az_raw]

        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        shade, meta = terrain_lib.hillshade_multiazimuth(
            arr, cy, cell_size_x=cx, altitude=altitude_v, azimuths=az_list,
            combine=combine_v, nodata=eff_nodata)
        payload = {
            "success": True,
            "azimuths": az_list,
            "combine": combine_v,
            "statistics": compute_raster_stats(shade),
            "sample": _bounded_sample(shade, decimals=2),
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="altitude", value=altitude_v)))
        backend = _backend_diagnostic("terrain.hillshade_multi", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.hillshade_multi", tool="multiazimuth_hillshade",
            parameters_applied={
                "altitude": altitude_v, "azimuths": ",".join(str(a) for a in az_list),
                "combine": combine_v,
            },
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    # ── Terrain V3：地平线角与天空可视因子（Steyn 1980）────────────────

    def _parse_azimuth_list(az_raw: Any, default: str) -> List[float]:
        """azimuths 参数（str/list）→ 罗盘度列表（照 multiazimuth_hillshade）。"""
        if isinstance(az_raw, str):
            try:
                parsed = json.loads(az_raw)
                az_list = [float(v) for v in parsed] if isinstance(parsed, list) else [float(parsed)]
            except (ValueError, TypeError):
                az_list = [float(v) for v in az_raw.replace(",", " ").split()]
        elif az_raw is None:
            try:
                parsed = json.loads(default)
                az_list = [float(v) for v in parsed]
            except (ValueError, TypeError):
                az_list = [float(v) for v in default.replace(",", " ").split()]
        else:
            az_list = [float(v) for v in az_raw]
        return az_list

    @tool(registry, name="horizon_angle_analysis",
    side_effect="cacheable_read",
    tags=('地平线角', '天际线', 'dem', '地形'),
           description=(
               "DEM 地平线角：逐方位（罗盘度）射线行走取最大正仰角（度）与跨方位 max，"
               "天空可视因子（Steyn 1980）的输入量，也可单独做天际线/遮挡诊断。"
               "\n何时用：天际线分析、日照/通风遮挡诊断、景观开敞度评价的前置量。"
               "\n何时不用：(1) 要天空开敞度比值 — sky_view_factor_analysis（本工具后继）；"
               "(2) 布尔可见性 — viewshed_analysis。"
               "\n关键约束：射线遇 nodata 即停（数据外视作无遮挡，披露）；半径 ≤100 像元。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "azimuths": "地平线方位列表（罗盘度，逗号分隔；缺省 '0,45,90,135,180,225,270,315'）",
               "max_search_radius": "射线搜索半径（像元 1-100，默认 100）",
           })
    def horizon_angle_analysis(raster_path: str,
                               azimuths: str | list[float] | None = None,
                               max_search_radius: int = 100) -> dict:
        contract_params: Dict[str, Any] = {"max_search_radius": max_search_radius}
        if azimuths is not None:
            contract_params["azimuths"] = (
                azimuths if isinstance(azimuths, str)
                else ",".join(str(a) for a in azimuths))
        params = apply_contract("terrain_horizon_analysis", contract_params)
        radius_v = int(params["max_search_radius"])
        az_list = _parse_azimuth_list(params.get("azimuths"), "0,45,90,135,180,225,270,315")

        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        res, meta = terrain_lib.horizon_angle(
            arr, cy, cell_size_x=cx, azimuths=az_list,
            max_search_radius=radius_v, nodata=eff_nodata)
        payload = {
            "success": True,
            "azimuths": res["azimuths"],
            "azimuth_mean_degrees": {
                k: round(float(np.nanmean(v)), 6)
                for k, v in res["horizon"].items()
            },
            "max_horizon": {
                "statistics": compute_raster_stats(res["max"]),
                "sample": _bounded_sample(res["max"]),
            },
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="radius_cells", value=float(radius_v))))
        backend = _backend_diagnostic("terrain.horizon_angle", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.horizon_angle", tool="horizon_angle_analysis",
            parameters_applied={
                "azimuths": ",".join(str(a) for a in az_list),
                "max_search_radius": radius_v,
            },
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(registry, name="sky_view_factor_analysis",
    side_effect="cacheable_read",
    tags=('天空可视因子', 'svf', 'dem', '城市气候'),
           description=(
               "DEM 天空可视因子 SVF（Steyn 1980）：SVF = (1/N)Σcos²ψ，ψ 为共用射线"
               "行走得到的地平线角；平地 = 1、深洼/峡谷 → 0。"
               "\n何时用：城市热岛/日照/辐射估算输入、山谷雾与通风潜势、景观开敞度制图。"
               "\n何时不用：(1) 要逐方位地平线角本身 — horizon_angle_analysis；"
               "(2) 布尔视域 — viewshed_analysis。"
               "\n关键约束：半径 ≤100 像元；数据缝附近按无遮挡计（SVF 高估，披露）。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "n_azimuths": "方位数（4-64 等角距，默认 16）",
               "max_search_radius": "地平线射线搜索半径（像元 1-100，默认 100）",
           })
    def sky_view_factor_analysis(raster_path: str, n_azimuths: int = 16,
                                 max_search_radius: int = 100) -> dict:
        params = apply_contract("terrain_svf_analysis", {
            "n_azimuths": n_azimuths, "max_search_radius": max_search_radius,
        })
        n_az_v = int(params["n_azimuths"])
        radius_v = int(params["max_search_radius"])
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, None)
        res, meta = terrain_lib.sky_view_factor(
            arr, cy, cell_size_x=cx, n_azimuths=n_az_v,
            max_search_radius=radius_v, nodata=eff_nodata)
        payload = {
            "success": True,
            "svf": {
                "statistics": compute_raster_stats(res["svf"]),
                "sample": _bounded_sample(res["svf"]),
            },
            "mean_horizon_degrees": {
                k: round(float(np.nanmean(v)), 6)
                for k, v in res["horizon"].items()
            },
            "meta": meta,
        }
        diagnostics = _base_diagnostics(
            transform, arr.shape[0], arr.shape[1],
            extra=(Diagnostic(name="nodata_effective", value=eff_nodata),
                   Diagnostic(name="n_azimuths", value=float(n_az_v)),
                   Diagnostic(name="radius_cells", value=float(radius_v))))
        backend = _backend_diagnostic("terrain.sky_view_factor", arr.size)
        if backend is not None:
            diagnostics.append(backend)
        return _terrain_evidence(
            payload, "terrain.sky_view_factor", tool="sky_view_factor_analysis",
            parameters_applied={"n_azimuths": n_az_v, "max_search_radius": radius_v},
            crs=crs,
            diagnostics=diagnostics,
            transformations=transformations or None,
            warnings=_non_metric_warning(crs) or None,
        )

    # Science V4（W8/W9）：水文与地形分析 V4 合并入口
    _register_hydrology_v4_tool(registry, _load_dem=_load_dem)


def _register_hydrology_v4_tool(registry, *, _load_dem) -> None:
    """Science V4（W8/W9）：水文与地形分析 V4 合并入口（单工具分派）。"""
    import numpy as np

    from app.lib.gis.scientific_evidence import build_evidence

    @tool(registry, name="hydrology_v4_analysis",
           description=(
               "水文/地形分析 V4：depression breaching（切沟排洼）、HAND"
               "（最近排水高程）、Shreve 量级、Pfafstetter 编码、hypsometry"
               "（高程面积曲线/积分）、solar radiation（晴空直散辐射）。"
               "\n何时用：需要比填洼更保真的排洼（breach）、洪水易损性图层"
               "（HAND）、河网层级（shreve/pfafstetter/pfafstetter_multilevel）、"
               "流网拓扑校验（flow_topology）、库容曲线"
               "（hypsometry）、光伏/日照潜力（solar）。"
               "\n何时不用：基础填洼 — 用 depression_fill；D8 流向 — 用 flow_analysis。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "analysis": ("breach|hand|shreve|pfafstetter|"
                            "pfafstetter_multilevel|flow_topology|hypsometry|solar_radiation"),
               "levels": "pfafstetter_multilevel 层级（1-4，默认 2）",
               "stream_threshold": "河网阈值（上游像元数；hand/shreve/pfafstetter 用）",
               "outlet_row": "pfafstetter 出口行（数组坐标）",
               "outlet_col": "pfafstetter 出口列",
               "latitude_deg": "solar 纬度（度）",
               "day_of_year": "solar 年积日（1-366）",
               "nodata": "可选 nodata 覆盖值",
           },
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("水文", "breaching", "HAND", "shreve", "pfafstetter",
                 "hypsometry", "solar"),
           output_semantic_type="stats",
           result_size_policy="ref_offload",
           crs_semantics="crs_agnostic",
           side_effect="deterministic_compute",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def hydrology_v4_analysis(
        raster_path: str,
        analysis: str,
        stream_threshold: float = 1000.0,
        outlet_row: int = -1,
        outlet_col: int = -1,
        levels: int = 2,
        latitude_deg: float = 30.0,
        day_of_year: int = 172,
        transmissivity: float = 0.75,
        persist_output: bool = False,
        nodata: float | None = None,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_errors import DegenerateData

        params = apply_contract("hydrology_v4_analysis", {
            "raster_path": raster_path,
            "analysis": analysis,
            "stream_threshold": float(stream_threshold),
            "outlet_row": int(outlet_row),
            "outlet_col": int(outlet_col),
            "levels": int(levels),
            "latitude_deg": float(latitude_deg),
            "day_of_year": int(day_of_year),
            "transmissivity": float(transmissivity),
        })
        params.setdefault("nodata", None)
        analysis = params["analysis"]
        stream_threshold = float(params["stream_threshold"])
        latitude_deg = float(params["latitude_deg"])
        day_of_year = int(params["day_of_year"])
        transmissivity = float(params["transmissivity"])

        algo_map = {
            "breach": "terrain.breach",
            "hand": "terrain.hand",
            "shreve": "terrain.shreve",
            "pfafstetter": "terrain.pfafstetter",
            "pfafstetter_multilevel": "terrain.pfafstetter_multilevel",
            "flow_topology": "terrain.flow_topology_validate",
            "hypsometry": "terrain.hypsometry",
            "solar_radiation": "terrain.solar_radiation",
        }
        if analysis not in algo_map:
            raise ValueError(
                f"analysis 必须是 {'|'.join(algo_map)} 之一，got {analysis!r}")
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, nodata)

        if analysis == "breach":
            breached, meta = terrain_lib.breach_depressions(
                arr, cy, cell_size_x=cx, nodata=eff_nodata)
            result = {
                "summary": (
                    f"Breaching 完成：{meta['n_depressions']} 个洼地，切沟像元 "
                    f"{meta['breached_cells']}，开挖量 {meta['carved_volume']}；"
                    f"回退填洼像元 {meta['fallback_filled_cells']}。"),
                "breach_metadata": meta,
            }
            # review R2-M4：切沟 DEM 是本分析的产出物 —— persist 供下游消费
            if persist_output:
                result["output_raster"] = _persist_filled_dem(
                    raster_path, breached, transform, crs, eff_nodata)
        elif analysis == "hand":
            hand_arr, meta = terrain_lib.hand(
                arr, cy, cell_size_x=cx,
                stream_threshold=float(stream_threshold), nodata=eff_nodata)
            result = {
                "summary": (
                    f"HAND 完成：{meta['stream_cells']} 河网像元（阈值 "
                    f"{stream_threshold}）；HAND 范围 {meta['hand_range']}；"
                    f"未解析（无河网下排）像元 {meta['unresolvable_cells']}。"),
                "hand_stats": meta,
                "hand_preview": np.nanpercentile(hand_arr, [5, 25, 50, 75, 95]).round(4).tolist(),
            }
            if persist_output:
                result["output_raster"] = _persist_filled_dem(
                    raster_path, hand_arr, transform, crs, eff_nodata)
        elif analysis == "shreve":
            filled, _ = terrain_lib.fill_depressions(arr, cy, cell_size_x=cx, nodata=eff_nodata)
            d8, _ = terrain_lib.d8_flow(filled, cy, cell_size_x=cx, nodata=eff_nodata)
            acc, _ = terrain_lib.flow_accumulation(d8)
            mag, meta = terrain_lib.shreve_magnitude(d8, acc, float(stream_threshold))
            result = {
                "summary": (
                    f"Shreve 量级完成：{meta['stream_cells']} 河网像元，"
                    f"最大量级 {meta['max_magnitude']}。"),
                "shreve_metadata": meta,
            }
        elif analysis == "pfafstetter":
            if outlet_row < 0 or outlet_col < 0:
                raise DegenerateData(
                    "analysis=pfafstetter 需要 outlet_row/outlet_col（出口像元）",
                    correction_hint="用 flow_analysis 的最大汇流像元作为出口")
            filled, _ = terrain_lib.fill_depressions(arr, cy, cell_size_x=cx, nodata=eff_nodata)
            d8, _ = terrain_lib.d8_flow(filled, cy, cell_size_x=cx, nodata=eff_nodata)
            acc, _ = terrain_lib.flow_accumulation(d8)
            codes, meta = terrain_lib.pfafstetter_codes(
                d8, acc, float(stream_threshold), (int(outlet_row), int(outlet_col)))
            result = {
                "summary": (
                    f"Pfafstetter 编码完成：干流 {meta['mainstem_cells']} 像元，"
                    f"编码分布 {meta['code_distribution']}（单级层级，已披露）。"),
                "pfafstetter_metadata": meta,
            }
        elif analysis == "pfafstetter_multilevel":
            if outlet_row < 0 or outlet_col < 0:
                raise DegenerateData(
                    "analysis=pfafstetter_multilevel 需要 outlet_row/outlet_col",
                    correction_hint="用 flow_analysis 的最大汇流像元作为出口")
            filled, _ = terrain_lib.fill_depressions(arr, cy, cell_size_x=cx, nodata=eff_nodata)
            d8, _ = terrain_lib.d8_flow(filled, cy, cell_size_x=cx, nodata=eff_nodata)
            acc, _ = terrain_lib.flow_accumulation(d8)
            codes, meta = terrain_lib.pfafstetter_codes_multilevel(
                d8, acc, float(stream_threshold),
                (int(outlet_row), int(outlet_col)),
                levels=int(params.get("levels", 2)))
            result = {
                "summary": (
                    f"多级 Pfafstetter 完成：{meta['levels']} 层、"
                    f"{meta['distinct_codes']} 个不同码、"
                    f"跳过细分段 {meta['skipped_segments']}（子段过小，已披露）。"),
                "pfafstetter_metadata": meta,
            }
        elif analysis == "flow_topology":
            filled, _ = terrain_lib.fill_depressions(arr, cy, cell_size_x=cx, nodata=eff_nodata)
            d8, _ = terrain_lib.d8_flow(filled, cy, cell_size_x=cx, nodata=eff_nodata)
            acc, _ = terrain_lib.flow_accumulation(d8)
            report, meta = terrain_lib.validate_flow_topology(d8, acc)
            verdict = "一致" if report["is_consistent"] else "存在破损（见报告）"
            result = {
                "summary": (
                    f"流网拓扑校验：{verdict}——出口 {report['outlets']}、环 "
                    f"{report['cycles']}、悬挂 receiver "
                    f"{report['dangling_receivers']}、汇流违例 "
                    f"{report['accumulation_violations']}、等汇流平台 "
                    f"{report['equal_accumulation_plateaus']}。"),
                "topology_report": report,
                "topology_metadata": meta,
            }
        elif analysis == "hypsometry":
            hyp, meta = terrain_lib.hypsometry(arr, cy, cell_size_x=cx, nodata=eff_nodata)
            # review R1-C2：meta["n_levels"] 是 int（非容器）；curve 在返回
            # 值 hyp（元组）而非 meta —— 此前双重 bug 工具路径必崩。
            n_levels = int(meta.get("n_levels", 0) or 0)
            result = {
                "summary": (
                    f"Hypsometry 完成：高程面积曲线 {n_levels} 级，"
                    f"高程积分 {meta['hypsometric_integral']}（矩形=1）。"),
                "hypsometry": dict(meta),
                "curve_preview": {
                    "elevation_norm": [round(float(v), 4) for v in hyp[0][:24]],
                    "area_above_norm": [round(float(v), 4) for v in hyp[1][:24]],
                },
            }
        else:  # solar_radiation
            sol, meta = terrain_lib.solar_radiation(
                arr, cy, cell_size_x=cx, latitude_deg=latitude_deg,
                day_of_year=day_of_year, transmissivity=transmissivity,
                nodata=eff_nodata)
            result = {
                "summary": (
                    f"Solar radiation 完成（lat={latitude_deg}°, DOY={day_of_year}，"
                    "晴空模型）：日辐照量范围 "
                    f"{meta['insolation_range']} MJ/m²。"),
                "solar_metadata": meta,
            }

        descriptor = get_algorithm_registry().get(algo_map[analysis])
        if descriptor is not None:
            result["scientific_evidence"] = build_evidence(
                descriptor,
                tool="hydrology_v4_analysis",
                parameters_applied={
                    "analysis": analysis,
                    "stream_threshold": float(stream_threshold),
                    "latitude_deg": float(latitude_deg),
                    "day_of_year": int(day_of_year),
                },
                input_facts={
                    "artifact_type": "raster_grid",
                    "crs": crs or "",
                    "units": "m",
                },
                transformations=transformations or None,
            )
        return result


    # ── Science V6（Goal 07 Phase E）：累积成本面 / 最小成本路径 ──────

    def _world_to_rc(
        transform: Tuple[float, ...], x: float, y: float,
    ) -> Tuple[int, int]:
        """世界坐标 → (row, col)（affine 系数序反解；floor 取整）。

        ``_read_terrain_window`` 的 transform = ``tuple(src.transform)``，
        即 Affine (a, b, c, d, e, f)：x = a·col + b·row + c，
        y = d·col + e·row + f。
        """
        a, b, c, d, e, f = [float(v) for v in transform[:6]]
        if b != 0 or d != 0:
            raise ValueError("rotated raster transforms are not supported")
        col = int(math.floor((x - c) / a))
        row = int(math.floor((y - f) / e))
        return row, col

    @tool(registry, name="cost_distance_analysis",
           description="累积成本面（最小成本距离）：在正摩擦栅格上从源点做 8 邻接 "
                       "Dijkstra（边成本 = 平均摩擦 × 米距，Tobler 摩擦面语义），"
                       "输出累积成本 GeoTIFF（下游 least_cost_path_analysis 直接消费）"
                       "与可达/不可达诊断。nodata 像元不可通行",
           tier=2, domains=["raster", "network"], cost="heavy",
           param_descriptions={
               "raster_path": "摩擦（成本）面 GeoTIFF 路径（data_dir 内，正值）",
               "sources_geojson": "源点要素 GeoJSON（Point FeatureCollection）或数据引用(ref:xxx)",
               "nodata": "nodata 覆盖值（0=用栅格自带 nodata；NaN 一律视为无效）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("成本距离", "最小成本", "可达性", "摩擦面", "廊道"),
           output_semantic_type="raster",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def cost_distance_analysis(raster_path: str, sources_geojson: Any,
                               nodata: float = 0) -> dict:
        from app.lib.geo_processor.core import safe_parse, to_feature_collection
        from app.lib.geo_analysis.cost_surface import cost_distance

        params = apply_contract("cost_distance_analysis", {
            "raster_path": raster_path,
            "sources_geojson": sources_geojson,
            "nodata": nodata,
        })
        raster_path = str(params["raster_path"])
        sources_geojson = params["sources_geojson"]
        nodata = float(params["nodata"])
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            raster_path, nodata if nodata else None)
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)

        parsed = safe_parse(sources_geojson)
        if parsed is None:
            raise ValueError("无法解析源点要素 GeoJSON")
        features = to_feature_collection(parsed).get("features", [])
        sources_rc: List[Tuple[int, int]] = []
        h, w = arr.shape
        for f in features:
            geom = (f or {}).get("geometry") or {}
            if geom.get("type") != "Point":
                continue
            x, y = geom["coordinates"][:2]
            r_i, c_i = _world_to_rc(transform, float(x), float(y))
            if 0 <= r_i < h and 0 <= c_i < w:
                sources_rc.append((r_i, c_i))
        if not sources_rc:
            raise ValueError("源点要素集中没有落在栅格范围内的 Point 要素")

        surface, meta = cost_distance(
            arr, sources_rc, cy, cell_size_x=cx, nodata=eff_nodata)

        # 成本面独立产物（_costdist 后缀，不覆盖源 DEM 的 _filled 链产物）。
        surface_path = _persist_raster_product(
            raster_path, surface, transform, crs, "_costdist",
            nodata_value=eff_nodata,
        )

        payload = {
            "success": True,
            "summary": (
                f"累积成本面完成：{meta['reachable']} 像元可达（源 "
                f"{meta['n_source_cells']} 个），最大累积成本 "
                f"{meta['max_cost']}，不可达 {meta['unreachable']} 个"
                f"（无效像元 {meta['invalid_cells']}）。产物：{surface_path}"),
            "cost_metadata": meta,
            "accumulated_raster_path": surface_path,
            "sample": _bounded_sample(surface),
        }
        return _terrain_evidence(
            payload, "terrain.cost_distance",
            tool="cost_distance_analysis",
            parameters_applied={
                "raster_path": str(raster_path),
                "n_sources": len(sources_rc),
                "nodata": float(nodata) if nodata else 0,
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform, h, w,
                extra=(Diagnostic(name="n_source_cells",
                                  value=float(meta["n_source_cells"])),
                       Diagnostic(name="reachable",
                                  value=float(meta["reachable"])))),
            transformations=transformations or None,
        )

    @tool(registry, name="least_cost_path_analysis",
           description="最小成本路径：在 cost_distance_analysis 产出的累积成本面上，"
                       "从目标像元沿严格下降方向回溯排水到源（GRASS r.drain 语义），"
                       "输出 LineString 折线要素与路径总成本。输入必须是累积成本面",
           tier=2, domains=["raster", "network"], cost="light",
           param_descriptions={
               "accumulated_raster_path": "累积成本面 GeoTIFF 路径（cost_distance_analysis 产物）",
               "target_x": "目标点世界坐标 X",
               "target_y": "目标点世界坐标 Y",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="light",
           scale_class="medium",
           tags=("最小成本路径", "排水回溯", "廊道", "路径优化"),
           output_semantic_type="geojson_fc",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def least_cost_path_analysis(accumulated_raster_path: str,
                                 target_x: float, target_y: float) -> dict:
        from app.lib.geo_analysis.cost_surface import least_cost_path

        params = apply_contract("least_cost_path_analysis", {
            "accumulated_raster_path": accumulated_raster_path,
            "target_x": target_x,
            "target_y": target_y,
        })
        accumulated_raster_path = str(params["accumulated_raster_path"])
        target_x = float(params["target_x"])
        target_y = float(params["target_y"])
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            accumulated_raster_path, None)
        row, col = _world_to_rc(transform, float(target_x), float(target_y))
        path, meta = least_cost_path(arr, row, col)

        # affine 系数序（同 _world_to_rc）：像元中心 = (col+0.5, row+0.5)。
        a, b, c, d, e, f = [float(v) for v in transform[:6]]
        coords = []
        for r_i, c_i in path:
            x = a * (c_i + 0.5) + b * (r_i + 0.5) + c
            y = d * (c_i + 0.5) + e * (r_i + 0.5) + f
            coords.append([round(x, 6), round(y, 6)])
        payload = {
            "success": True,
            "summary": (
                f"最小成本路径：{meta['n_cells']} 个像元，总成本 "
                f"{meta['total_cost']}（目标 [{row},{col}] → 源 "
                f"{meta['source']}）。"),
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "total_cost": meta["total_cost"],
                    "n_cells": meta["n_cells"],
                },
                "geometry": {"type": "LineString", "coordinates": coords},
            }],
            "path_metadata": meta,
        }
        return _terrain_evidence(
            payload, "terrain.least_cost_path",
            tool="least_cost_path_analysis",
            parameters_applied={
                "accumulated_raster_path": str(accumulated_raster_path),
                "target": [row, col],
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform, arr.shape[0], arr.shape[1],
                extra=(Diagnostic(name="path_cells",
                                  value=float(meta["n_cells"])),
                       Diagnostic(name="total_cost",
                                  value=float(meta["total_cost"])))),
        )

