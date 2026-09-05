"""遥感数据 FC 工具"""
import logging
from typing import Annotated, Any, Dict, List, Optional

import numpy as np
from pydantic import Field

from app.tools.registry import ToolRegistry, tool
from app.services.rs.spectral_engine import spectral_engine
from app.tools._utils import parse_bbox

logger = logging.getLogger(__name__)

# #995: 日期格式约束 —— 描述写明 YYYY-MM-DD，schema 层 pattern 同步拦截
# （签名注解驱动 registry._generate_model，Annotated 携带 Field 约束）。
DateStr = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]

# VNext 工具内联数组的规模闸（JSON 通道的实用上界；SAR 栈的科学规模
# 上限由 sar_temporal.SAR_SCALE_LIMIT_* 语义层执行）。
_TOOL_ARRAY_MAX_VALUES = 4_000_000


def _bands_to_arrays(bands: Dict[str, List[List[float]]]) -> Dict[str, np.ndarray]:
    """角色 → 2D 数组（形状一致性校验 + 规模闸）。"""
    if not isinstance(bands, dict) or not bands:
        raise ValueError("bands 必须是非空 {role: 2D array} 字典（角色显式命名）")
    arrays: Dict[str, np.ndarray] = {}
    shape = None
    for role, data in bands.items():
        arr = np.asarray(data, dtype=float)
        if arr.ndim != 2:
            raise ValueError(f"bands[{role!r}] 必须是 2D 数组，got ndim={arr.ndim}")
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"bands[{role!r}] 像元数 {arr.size} 超过内联数组工具上界 "
                f"{_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES} values per band",
                correction_hint="改用栅格工件路径（raster_calculator/窗口化底座）",
            )
        if shape is None:
            shape = arr.shape
        elif arr.shape != shape:
            raise ValueError(
                f"bands 各角色形状不一致：{role}={arr.shape} vs {shape}")
        arrays[str(role)] = arr
    return arrays


def _attach_science_evidence(
    payload: dict,
    algorithm_id: str,
    *,
    tool: str,
    parameters_applied: dict,
    input_facts: Optional[dict] = None,
    warnings: Optional[list] = None,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
    seed: Optional[int] = None,
) -> dict:
    """薄包装职责（ADR-0099 §1）：调科学实现 → 挂 descriptor 证据块。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.scientific_evidence import Diagnostic, build_evidence

    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        logger.warning("scientific evidence requested for unknown algorithm %s", algorithm_id)
        return payload
    diag_blocks = [
        d if isinstance(d, Diagnostic) else Diagnostic(**d)
        for d in (diagnostics or [])
    ]
    payload["scientific_evidence"] = build_evidence(
        descriptor,
        tool=tool,
        parameters_applied=parameters_applied,
        input_facts=input_facts or {},
        warnings=warnings,
        diagnostics=diag_blocks,
        seed=seed,
    )
    return payload


def register_rs_tools(registry: ToolRegistry):
    """注册遥感数据工具"""

    @tool(registry, name="fetch_sentinel",
           description=(
               "Sentinel-2 卫星影像快视图获取：从 AWS STAC 拉取指定 bbox + 日期窗内最少云覆盖的一景影像缩略图。"
               "\n何时用：用户想『看下某区域近期的卫星影像』；为后续 compute_ndvi / detect_vegetation_change 选片；"
               "做时序对比的基础查询。"
               "\n何时不用：(1) 要做 NDVI 计算 — 直接调 compute_ndvi (一步到位)；"
               "(2) 要本地高分影像 NDVI — 上传 TIFF 后用 analyze_vegetation_index；"
               "(3) bbox 极大 (跨省) — STAC 检索会超时，按区县切分。"
               "\n关键约束：bbox=[west,south,east,north] WGS84；日期窗建议 1–3 个月以提高有云容忍度。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north]，如 [116.2, 39.7, 116.6, 40.1]",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD",
               "bands": "波段组合：'true-color'(默认) / 'false-color' / 'ndvi'",
           })
    async def fetch_sentinel(bbox: str, date_from: DateStr, date_to: DateStr, bands: str = "true-color") -> dict:
        try:
            parts = parse_bbox(bbox)
            return await spectral_engine.fetch_sentinel_thumbnail(parts, date_from, date_to, bands)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}

    @tool(registry, name="compute_ndvi",
           description=(
               "在线 NDVI 计算 (Sentinel-2)：给 bbox + 日期窗，自动从 STAC 拉 B04/B08 并算 NDVI，返回统计 + 覆盖率分类。"
               "\n何时用：『北京海淀区上个月植被覆盖如何』『查 XX 区 NDVI 趋势』；"
               "不需要落地 TIFF、只要统计指标 (mean / vegetation_coverage_pct)。"
               "\n何时不用：(1) 已上传本地遥感影像 — 用 analyze_vegetation_index (Celery 异步，结果落地为资产)；"
               "(2) 要看两期对比 — 用 detect_vegetation_change；"
               "(3) 要 NDWI / NBR / EVI — 用 compute_vegetation_index (统一入口，可选 index_type)。"
               "\n关键约束：bbox 不要过大（>1°× 1° 易触发下采样导致精度损失）；日期窗 1–3 个月以容忍有云。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north] WGS84",
               "date_from": "起始日期 YYYY-MM-DD",
               "date_to": "结束日期 YYYY-MM-DD",
           })
    async def compute_ndvi(bbox: str, date_from: DateStr, date_to: DateStr) -> dict:
        try:
            parts = parse_bbox(bbox)
            return await spectral_engine.compute_ndvi(parts, date_from, date_to)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}

    @tool(registry, name="fetch_dem",
           description=(
               "DEM 高程数据获取 (Copernicus 30m)：拉指定 bbox 内的数字高程模型 TIFF + 统计 + 缩略图。"
               "\n何时用：(a) 山区/选址分析的地形底图；(b) zonal_stats 求行政区平均海拔；"
               "(c) 接下来要算坡度坡向 (compute_terrain) 但只想先看一眼 DEM 范围与极值。"
               "\n何时不用：(1) 直接要坡度坡向产品 — 用 compute_terrain (一步到位含 slope/aspect/hillshade)；"
               "(2) bbox 跨多省 — 数据下载会超时，按市级切分。"
               "\n关键约束：bbox 单位为 WGS84 度；分辨率 30m，对城市精细地形不够（用专题 DEM 替代）。"
           ),
           tier=2, domains=["raster"],
           param_descriptions={
               "bbox": "边界框 [west, south, east, north] WGS84",
           })
    async def fetch_dem(bbox: str) -> dict:
        try:
            parts = parse_bbox(bbox)
            return await spectral_engine.fetch_dem(parts)
        except ValueError as e:
            return {"error": str(e)}
        except (RuntimeError, OSError) as e:
            return {"error": str(e)}

    # ── VNext（ADR-0099）：类型化光谱指数 / SAR 时序统计 / VV-VH 比 ──

    @tool(registry, name="compute_spectral_index",
          description=(
              "类型化光谱指数计算：波段按语义角色（red/nir/swir1/...）显式命名后，"
              "从 12 种公式族指数（ndvi/gndvi/savi/msavi/ndwi/mndwi/ndbi/ndmi/nbr/evi）"
              "计算，附公式出处、有效像元率与超理论值域比例（未定标 DN 输入的诚实信号）。"
              "\n何时用：已有各波段数值矩阵（小范围样本/切片），需要可审计出处的指数计算；"
              "\n何时不用：(1) 要在线 Sentinel-2 NDVI —— compute_ndvi；"
              "(2) 本地上传的 TIFF —— analyze_vegetation_index；"
              "(3) 大幅影像 —— raster_calculator（窗口化）。"
              "\n关键约束：bands 键必须是语义角色名（不按波段位置猜测）；"
              "DN 输入需给 scale_factors（如 10000）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "index_id": "指数 id：ndvi/gndvi/savi/msavi/ndwi/mndwi/ndbi/ndmi/nbr/evi",
              "bands": "语义角色 → 2D 数组，如 {\"red\": [[...]], \"nir\": [[...]]}（各角色形状一致）",
              "scale_factors": "角色 → 线性定标除数，如 {\"red\": 10000, \"nir\": 10000}（DN→反射率）",
              "nodata_value": "可选标量哨兵值（等于该值的像元视为无效 → NaN）",
          })
    async def compute_spectral_index(
        index_id: str,
        bands: Dict[str, List[List[float]]],
        scale_factors: Optional[Dict[str, float]] = None,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract

        params = apply_contract("spectral_index_analysis", {"index_id": index_id})
        arrays = _bands_to_arrays(bands)
        nodata_mask = None
        if nodata_value is not None:
            first = next(iter(arrays.values()))
            nodata_mask = np.zeros(first.shape, dtype=bool)
            for arr in arrays.values():
                nodata_mask |= arr == float(nodata_value)

        from app.lib.geo_analysis.spectral import compute_spectral_index as _compute

        res = _compute(
            arrays, params["index_id"],
            scale_factors=scale_factors, nodata=nodata_mask)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "index_id": res["index_id"],
            "formula": res["formula"],
            "reference": res["reference"],
            "roles_used": res["roles_used"],
            "scale_factors_applied": res["scale_factors_applied"],
            "valid_pixel_fraction": round(res["valid_pixel_fraction"], 6),
            "out_of_range_fraction": round(res["out_of_range_fraction"], 6),
            "valid_range": list(res["valid_range"]),
            "stats": {
                "min": float(np.nanmin(res["array"])) if finite.any() else None,
                "max": float(np.nanmax(res["array"])) if finite.any() else None,
                "mean": float(np.nanmean(res["array"])) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(res["array"].size),
            },
            "array": res["array"].round(6).tolist(),
            "disclosure": res["disclosure"],
        }
        return _attach_science_evidence(
            payload, "remote.spectral_index", tool="compute_spectral_index",
            parameters_applied={
                "index_id": res["index_id"],
                "roles": ",".join(res["roles_used"]),
                "scale_factors": res["scale_factors_applied"] or "none",
            },
            input_facts={"feature_count": int(res["array"].size)},
            warnings=(
                ["out_of_range_fraction>0：疑似未定标 DN 输入"]
                if res["out_of_range_fraction"] > 0 else None),
            diagnostics=[
                {"name": "valid_pixel_fraction",
                 "value": round(res["valid_pixel_fraction"], 6)},
                {"name": "out_of_range_fraction",
                 "value": round(res["out_of_range_fraction"], 6)},
            ],
        )

    @tool(registry, name="sar_temporal_stats",
          description=(
              "SAR 时序栈逐像元统计（mean/std/min/max/range，时间维聚合，nodata 感知）。"
              "诚实边界：不做斑点滤波、不做辐射定标——假定输入已几何校正并对齐。"
              "\n何时用：已对齐的多期 SAR 切片（小范围）需要时序合成/极值分析；"
              "\n关键约束：栈深 ≤24、H·W ≤ 4096×4096（超限结构化拒绝）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "stack": "时序栈 [T][H][W]（T 个同形状 2D 切片，按时间序）",
              "product": "统计量：mean(默认)/std(总体 ddof=0)/min/max/range",
              "nodata_value": "可选标量哨兵值（逐切片剔除，剩余有效切片上统计）",
          })
    async def sar_temporal_stats(
        stack: List[List[List[float]]],
        product: str = "mean",
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_temporal import temporal_stack_statistics

        params = apply_contract("sar_temporal_stats_analysis", {"product": product})
        arr = np.asarray(stack, dtype=float)
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联 SAR 栈元素数 {arr.size} 超过工具上界 {_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="分块/分年统计，或走栅格工件路径",
            )
        res = temporal_stack_statistics(arr, product=params["product"],
                                        nodata=nodata_value)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "product": res["product"],
            "stats": {
                "min": float(np.nanmin(res["array"])) if finite.any() else None,
                "max": float(np.nanmax(res["array"])) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(res["array"].size),
            },
            "array": res["array"].round(6).tolist(),
            "meta": res["meta"],
        }
        return _attach_science_evidence(
            payload, "sar.temporal_stats", tool="sar_temporal_stats",
            parameters_applied={
                "product": res["product"],
                "time_slices": res["meta"]["time_slices"],
                "nodata_value": nodata_value if nodata_value is not None else "none",
            },
            input_facts={"feature_count": int(res["array"].size)},
            warnings=[res["meta"]["disclosure"]],
        )

    @tool(registry, name="sar_vh_ratio",
          description=(
              "SAR VV/VH 极化比（植被结构对比代理；VH=0 → NaN）。"
              "线性域为比值、dB 域为 dB 差（VV−VH）——单位语义由输入决定。"
              "\n何时用：同景双极化 SAR（如 Sentinel-1 VV+VH）的结构对比。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "vv": "VV 极化 2D 数组",
              "vh": "VH 极化 2D 数组（与 VV 同形状）",
          })
    async def sar_vh_ratio(vv: List[List[float]], vh: List[List[float]]) -> dict:
        from app.lib.geo_analysis.sar_temporal import vh_ratio as _vh_ratio

        vv_arr = np.asarray(vv, dtype=float)
        vh_arr = np.asarray(vh, dtype=float)
        # 评审 M3：内联数组与 _bands_to_arrays 同一资源包络（先拒绝不 OOM）。
        for _name, _arr in (("vv", vv_arr), ("vh", vh_arr)):
            if _arr.size > _TOOL_ARRAY_MAX_VALUES:
                from app.lib.gis.scientific_errors import ResourceScaleMismatch

                raise ResourceScaleMismatch(
                    f"sar_vh_ratio {_name} 数组超限：{_arr.size} 值",
                    estimated=f"{_arr.size * 8 / 1e6:.1f} MB float64",
                    limit=f"≤{_TOOL_ARRAY_MAX_VALUES} values",
                    correction_hint="分块处理或走栅格文件路径",
                )
        if vv_arr.shape != vh_arr.shape:
            raise ValueError(
                f"vv/vh 形状不一致：{vv_arr.shape} vs {vh_arr.shape}")
        res = _vh_ratio(vv_arr, vh_arr)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "formula": res["meta"]["formula"],
            "stats": {
                "min": float(np.nanmin(res["array"])) if finite.any() else None,
                "max": float(np.nanmax(res["array"])) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(res["array"].size),
            },
            "array": res["array"].round(6).tolist(),
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "sar.vh_ratio", tool="sar_vh_ratio",
            parameters_applied={"roles": "vv,vh"},
            input_facts={"feature_count": int(res["array"].size)},
            warnings=[res["meta"]["disclosure"]],
        )
