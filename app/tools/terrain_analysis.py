"""地形分析与遥感指数工具 — 坡度/坡向/山体阴影、多源植被指数、地形科学（VNext）

VNext 地形科学工具（ADR-0099）：TPI/TRI/粗糙度/曲率、视域、D8 流向与
汇流累积、流域圈定、等值线提取。薄包装职责：validate → 读有界窗口 →
调 app/lib/geo_analysis/terrain.py 纯函数 → 挂 scientific_evidence →
返回有界结果。算法语义一律在注册表 descriptor 与 lib 层，本层不复算。
"""
import json
import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.tools.registry import ToolRegistry, tool
from app.services.rs.spectral_engine import spectral_engine
from app.services.rs.band_math import compute_raster_stats
from app.tools._utils import parse_bbox, trim_features
from app.utils.path import validate_data_path

from app.lib.geo_analysis import terrain as terrain_lib
from app.lib.geo_analysis.raster_guard import RasterResourceGuard
from app.lib.geo_analysis.raster_math import rasterio_env
from app.lib.gis.algorithm_registry import get_algorithm_registry
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


def _read_terrain_window(
    raster_path: str, nodata_override: Optional[float],
) -> Tuple[np.ndarray, Tuple[float, ...], str, Optional[float], Tuple[float, ...]]:
    """validate → 有界读取（RasterResourceGuard 护栏）→ (数组, transform, crs, nodata, bounds)。"""
    path = validate_data_path(raster_path)
    import rasterio

    with rasterio_env():
        with rasterio.open(path) as src:
            RasterResourceGuard.check_grid(src.width, src.height, num_bands=src.count)
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
           })
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
               "在线遥感指数统一入口：NDVI(植被)/NDWI(水体)/NBR(燃烧)/EVI(增强植被)，自动 STAC 拉 Sentinel-2 波段并计算。"
               "\n何时用：除 NDVI 外的指数（NDWI 水体面积、NBR 火烧迹地、EVI 高生物量）；指数随场景动态选择时。"
               "\n何时不用：(1) 只算 NDVI — 直接 compute_ndvi（接口更窄、参数更少）；"
               "(2) 要双时相对比 — 用 detect_vegetation_change；"
               "(3) 本地 TIFF 处理 — 用 analyze_vegetation_index。"
               "\n关键约束：index_type ∈ {ndvi, ndwi, nbr, evi}；返回 {stats, classification, bbox}。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD",
               "index_type": "指数类型: 'ndvi'(默认), 'ndwi', 'nbr', 'evi'",
           })
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
           })
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
           })
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
               "\n何时不用：(1) 需要流域边界 — watershed_delineation；(2) 多向流 D∞ — 未实现（D8 单向流限制）。"
               "\n关键约束：平地/洼地即汇（不填洼、不路由）；边界=出口。"
           ),
           tier=2, domains=["raster"], cost="heavy",
           param_descriptions={
               "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
               "product": "输出产品: 'flow_accumulation'(默认) | 'flow_direction'",
           })
    def flow_analysis(raster_path: str, product: str = "flow_accumulation") -> dict:
        params = apply_contract("flow_analysis", {"product": product})
        product = str(params["product"])
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(raster_path, None)
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)

        d8, d8_meta = terrain_lib.d8_flow(arr, cy, cell_size_x=cx, nodata=eff_nodata)
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
            parameters_applied={"product": product},
            crs=crs,
            diagnostics=_base_diagnostics(
                transform, arr.shape[0], arr.shape[1],
                extra=(Diagnostic(name="nodata_effective", value=eff_nodata),)),
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
           })
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
           })
    def extract_contours(raster_path: str, levels: list[float] | None = None,
                         n_levels: int = 10, interval: float | None = None) -> dict:
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
