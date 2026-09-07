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


def _backend_selection_diagnostic(algorithm_id: str, cells: int):
    """select_backend 决策 → 证据块诊断（additive，失败不阻塞主结果）。

    ``BackendDecision.to_diagnostic()`` 的 value 是变体名字符串，而证据块
    Diagnostic.value 只收数值 —— 这里保留其 name/text、把非数值 value 归一
    为 None（文本里已含 variant 信息）。
    """
    try:
        from app.lib.gis.backend_selection import ScaleProfile, select_backend

        decision = select_backend(
            algorithm_id, ScaleProfile(feature_count=cells, raster_cells=cells))
        raw = decision.to_diagnostic()
        value = raw.get("value")
        return {
            "name": str(raw.get("name") or "backend_selection"),
            "value": float(value) if isinstance(value, (int, float))
            and not isinstance(value, bool) else None,
            "text": str(raw.get("text") or ""),
        }
    except Exception:  # noqa: BLE001 — 诊断是 additive，不阻塞主结果
        return None


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
           },
           side_effect="cacheable_read",
           deterministic=False,
           network=True,
           latency_class="slow",
           memory_class="medium",
           scale_class="large",
           output_semantic_type="list",
           result_size_policy="bounded",
           crs_semantics="wgs84",
           tags=("sentinel", "卫星影像", "遥感", "影像快视图", "stac", "影像查询"),
           failure_modes=("network_error", "timeout", "empty_result"),
           )
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
           },
           side_effect="cacheable_read",
           deterministic=False,
           network=True,
           latency_class="slow",
           memory_class="medium",
           scale_class="large",
           output_semantic_type="stats",
           result_size_policy="bounded",
           crs_semantics="wgs84",
           unit_semantics="ratio",
           tags=("ndvi", "植被覆盖", "遥感", "sentinel", "在线计算", "植被指数"),
           failure_modes=("network_error", "timeout", "empty_result"),
           )
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
           },
           side_effect="cacheable_read",
           deterministic=False,
           network=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           output_semantic_type="list",
           result_size_policy="bounded",
           crs_semantics="wgs84",
           unit_semantics="meters",
           tags=("dem", "高程", "地形", "copernicus", "elevation", "数字高程模型"),
           failure_modes=("network_error", "timeout", "empty_result"),
           )
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
              "从 13 种公式族指数（ndvi/gndvi/savi/msavi/ndwi/ndwi_gao/ndwi_water/"
              "mndwi/ndbi/ndmi/nbr/evi/evi2）计算，附公式出处、有效像元率与超理论值域比例"
              "（未定标 DN 输入的诚实信号）。"
              "\nNDWI 拆名：ndwi/ndwi_water=McFeeters 开放水体 (green−nir)/(green+nir)；"
              "ndwi_gao=Gao 植被水分 (nir−swir1)/(nir+swir1)——同名异式不可互换。"
              "\n何时用：已有各波段数值矩阵（小范围样本/切片），需要可审计出处的指数计算；"
              "\n何时不用：(1) 要在线 Sentinel-2 NDVI —— compute_ndvi；"
              "(2) 本地上传的 TIFF —— analyze_vegetation_index；"
              "(3) 大幅影像 —— raster_calculator（窗口化）。"
              "\n关键约束：bands 键必须是语义角色名（不按波段位置猜测）；"
              "DN 输入需给 scale_factors（如 10000）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "index_id": "指数 id：ndvi/gndvi/savi/msavi/ndwi/ndwi_gao/"
                          "ndwi_water/mndwi/ndbi/ndmi/nbr/evi/evi2",
              "bands": "语义角色 → 2D 数组，如 {\"red\": [[...]], \"nir\": [[...]]}（各角色形状一致）",
              "scale_factors": "角色 → 线性定标除数，如 {\"red\": 10000, \"nir\": 10000}（DN→反射率）",
              "nodata_value": "可选标量哨兵值（等于该值的像元视为无效 → NaN）",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="medium",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          unit_semantics="ratio",
          tags=("光谱指数", "ndvi", "ndwi", "evi", "波段运算", "spectral_index"),
          failure_modes=("invalid_args", "memory"),
          )
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
              "SAR 时序栈逐像元统计（mean/std/min/max/range + 可选 CV 与鲁棒分位数，"
              "时间维聚合，nodata 感知）。"
              "诚实边界：不做斑点滤波、不做辐射定标（两者为独立工具 sar_speckle_filter/"
              "sar_calibrate）——假定输入已几何校正并对齐。"
              "\n何时用：已对齐的多期 SAR 切片（小范围）需要时序合成/极值/变异分析；"
              "\n关键约束：栈深 ≤24、H·W ≤ 4096×4096（超限结构化拒绝）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "stack": "时序栈 [T][H][W]（T 个同形状 2D 切片，按时间序）",
              "product": "统计量：mean(默认)/std(总体 ddof=0)/min/max/range",
              "nodata_value": "可选标量哨兵值（逐切片剔除，剩余有效切片上统计）",
              "include_cv": "是否追加 CV=std/mean（|mean|≤1e-12 → NaN；描述性披露）",
              "percentiles": "逗号分隔分位数（如 '10,50,90'；≤5 个，0-100；空=不计算）",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="medium",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          tags=("sar", "时序统计", "时间维聚合", "变异系数", "分位数", "均值合成"),
          failure_modes=("invalid_args", "memory"),
          )
    async def sar_temporal_stats(
        stack: List[List[List[float]]],
        product: str = "mean",
        nodata_value: Optional[float] = None,
        include_cv: bool = False,
        percentiles: str = "",
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_temporal import temporal_stack_statistics

        pct_req: tuple = ()
        if (percentiles or "").strip():
            try:
                pct_req = tuple(
                    float(x) for x in percentiles.split(",") if x.strip())
            except ValueError:
                raise ValueError(
                    f"percentiles 必须是逗号分隔数字（如 '10,50,90'），"
                    f"got {percentiles!r}")
        params = apply_contract("sar_temporal_stats_analysis", {
            "product": product,
            "include_cv": include_cv,
            "percentiles": percentiles or "",
        })
        arr = np.asarray(stack, dtype=float)
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联 SAR 栈元素数 {arr.size} 超过工具上界 {_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="分块/分年统计，或走栅格工件路径",
            )
        res = temporal_stack_statistics(
            arr, product=params["product"], nodata=nodata_value,
            include_cv=params["include_cv"], percentiles=pct_req)
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
        if "cv" in res:
            payload["cv"] = res["cv"].round(6).tolist()
        if "percentiles" in res:
            payload["percentiles"] = {
                k: v.round(6).tolist() for k, v in res["percentiles"].items()}
        return _attach_science_evidence(
            payload, "sar.temporal_stats", tool="sar_temporal_stats",
            parameters_applied={
                "product": res["product"],
                "time_slices": res["meta"]["time_slices"],
                "nodata_value": nodata_value if nodata_value is not None else "none",
                "include_cv": bool(params["include_cv"]),
                "percentiles": params["percentiles"] or "none",
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
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="medium",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          tags=("sar", "极化比", "vv", "vh", "植被结构", "后向散射"),
          failure_modes=("invalid_args", "memory"),
          )
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

    # ── Foundation V2（A6）：斑点滤波 / 辐射定标 / GLCM / PCA / ──────
    # ── Tasseled Cap / 时序合成（planned→native + 新原生能力）────────

    @tool(registry, name="sar_speckle_filter",
          description=(
              "SAR 斑点噪声滤波（Lee 1980 / Refined-Lee 边缘方向 MMSE / Frost 1982 / "
              "Gamma MAP 三分支+Newton 迭代 / Kuan 1985 闭式 MMSE）。"
              "输入须线性强度（非负；dB 输入被拒绝——先定标）；ENL 显式优先，"
              "缺省整图矩估计 ENL=mean²/var（均匀假设，enl_source 披露）。"
              "\n何时用：已对齐的单波段 SAR 强度切片的斑点抑制（时序统计/极值前）。"
              "\n关键约束：窗口 3/5/7；refined_lee 为 7 子窗方向 MMSE 近似"
              "（非 Lopes 1990 完整 MAP 变体）；gamma_map 的 Newton 迭代上限与"
              "实际轮数披露；网格 ≤4096×4096。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "array": "2D 线性强度数组（非负）",
              "filter": "滤波器：lee(默认)/refined_lee/frost/gamma_map/kuan",
              "window": "奇数窗口：3(默认)/5/7",
              "enl": "等效视数（>0；缺省整图矩估计并披露）",
              "damping": "Frost 阻尼 D（0.5-5，默认 1；仅 frost 使用）",
              "max_iterations": "Gamma MAP Newton 迭代上限（默认 10，1-50；仅 gamma_map）",
              "nodata_value": "可选标量哨兵值（该值像元视为无效）",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="slow",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          tags=("sar", "斑点滤波", "lee", "frost", "去噪", "speckle"),
          failure_modes=("invalid_args", "memory"),
          )
    async def sar_speckle_filter(
        array: List[List[float]],
        filter: str = "lee",
        window: str = "3",
        enl: Optional[float] = None,
        damping: float = 1.0,
        nodata_value: Optional[float] = None,
        max_iterations: int = 10,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_filter import speckle_filter

        contract_params: Dict[str, Any] = {
            "filter": filter, "window": window, "damping": damping,
            "max_iterations": max_iterations}
        if enl is not None:
            contract_params["enl"] = enl
        params = apply_contract("sar_speckle_filter_analysis", contract_params)
        arr = np.asarray(array, dtype=float)
        if arr.ndim != 2:
            raise ValueError(f"array 必须是 2D 数组，got ndim={arr.ndim}")
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联 SAR 数组像元数 {arr.size} 超过工具上界 "
                f"{_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="分块（瓦片）滤波，或走栅格工件路径",
            )
        res = speckle_filter(
            arr, params["filter"], window=int(params["window"]),
            enl=params.get("enl"), damping=params["damping"],
            nodata=nodata_value, max_iterations=params["max_iterations"])
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "method": res["method"],
            "window": res["window"],
            "enl": round(res["enl"], 6),
            "enl_source": res["enl_source"],
            "iterations_used": res["meta"]["iterations_used"],
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
            payload, "sar.speckle_filter", tool="sar_speckle_filter",
            parameters_applied={
                "filter": res["method"],
                "window": res["window"],
                "enl_source": res["enl_source"],
                "damping": params["damping"] if res["method"] == "frost" else "n/a",
                "iterations_used": res["meta"]["iterations_used"]
                if res["method"] == "gamma_map" else "n/a",
            },
            input_facts={"feature_count": int(res["array"].size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "sar.speckle_filter", int(res["array"].size))],
        )

    @tool(registry, name="sar_calibrate",
          description=(
              "SAR 辐射定标：DN → β⁰/σ⁰/γ⁰ 常数定标（β⁰=I/K、σ⁰=β⁰·sin(θ)、"
              "γ⁰=β⁰·tan(θ)，振幅域先平方为 I=DN² 并披露）。"
              "入射角：标量 incidence_deg 或逐像元 2D LUT（incidence_lut，"
              "与网格同形，互斥，lut_pixels 披露）。"
              "诚实边界：定标常数 K 仍为标量——σ⁰ 逐像元定标 LUT（SAFE "
              "annotation XML）不解析；热噪声去除见 sar_remove_thermal_noise"
              "（本工具不做隐式前置）。"
              "\n何时用：已有定标常数（如 Sentinel-1 A²/AUT）与入射角"
              "（标量或 LUT）的强度切片定标。"
              "\n关键约束：calibration_constant 必需（缺失拒绝，绝不虚构）；"
              "入射角 (0,90) 开区间（度）；负 DN 被拒绝。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "array": "2D DN/强度数组（非负）",
              "calibration_constant": "定标常数 K（必需，如 Sentinel-1 A²/AUT）",
              "incidence_deg": "标量入射角（度，0-90 开区间；σ⁰/γ⁰ 必需；"
                               "与 incidence_lut 互斥）",
              "incidence_lut": "可选逐像元入射角 LUT（2D 数组，与 array 同形；"
                               "与 incidence_deg 互斥；lut_pixels 披露）",
              "incidence_map": "incidence_lut 的别名（历史参数名，保留兼容）",
              "input_domain": "dn_intensity(默认)/dn_amplitude（振幅先平方，披露）",
              "output_product": "sigma0(默认)/beta0/gamma0/all",
              "to_db": "输出 10·log₁₀（强度量纲惯例；默认 false）",
              "nodata_value": "可选标量哨兵值",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="medium",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          tags=("sar", "辐射定标", "sigma0", "beta0", "gamma0", "calibration"),
          failure_modes=("invalid_args", "memory"),
          )
    async def sar_calibrate(
        array: List[List[float]],
        calibration_constant: float,
        incidence_deg: Optional[float] = None,
        incidence_map: Optional[List[List[float]]] = None,
        incidence_lut: Optional[List[List[float]]] = None,
        input_domain: str = "dn_intensity",
        output_product: str = "sigma0",
        to_db: bool = False,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_calibration import calibrate_sar

        contract_params: Dict[str, Any] = {
            "calibration_constant": calibration_constant,
            "input_domain": input_domain,
            "output_product": output_product,
            "to_db": to_db,
        }
        if incidence_deg is not None:
            contract_params["incidence_deg"] = incidence_deg
        # incidence_lut（2D 数组）不经 apply_contract——契约词表无 array
        # 类型，LUT 由工具签名形状校验 + 实现层 (0,90) 守卫（同 incidence_map）。
        params = apply_contract("sar_calibration_analysis", contract_params)

        arr = np.asarray(array, dtype=float)
        if arr.ndim != 2:
            raise ValueError(f"array 必须是 2D 数组，got ndim={arr.ndim}")
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联 SAR 数组像元数 {arr.size} 超过工具上界 "
                f"{_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="分块定标，或走栅格工件路径",
            )
        if incidence_lut is not None and incidence_map is not None:
            raise ValueError(
                "incidence_lut 与 incidence_map 是同一参数的两个名字——只能传其一")
        inc_map = None
        lut_source = None
        if incidence_lut is not None:
            inc_map = np.asarray(incidence_lut, dtype=float)
            lut_source = "incidence_lut"
        elif incidence_map is not None:
            inc_map = np.asarray(incidence_map, dtype=float)
            lut_source = "incidence_map"
        if inc_map is not None:
            if inc_map.size > _TOOL_ARRAY_MAX_VALUES:
                raise ValueError("入射角 LUT 超过内联数组工具上界")
        res = calibrate_sar(
            arr,
            calibration_constant=params["calibration_constant"],
            incidence_deg=params.get("incidence_deg"),
            incidence_map=inc_map,
            input_domain=params["input_domain"],
            output_product=params["output_product"],
            to_db=params["to_db"],
            nodata=nodata_value,
        )
        products_out = {
            name: arr2.round(6).tolist()
            for name, arr2 in res["products"].items() if arr2 is not None
        }
        primary = res["meta"]["primary_product"]
        payload = {
            "success": True,
            "products": products_out,
            "products_computed": res["meta"]["products_computed"],
            "formula": res["meta"]["formula"],
            "incidence_lut_source": lut_source or "none",
            "lut_pixels": res["meta"]["lut_pixels"],
            "stats": {},
            "disclosure": res["meta"]["disclosure"],
        }
        if primary is not None:
            prim = np.asarray(res["products"][primary], dtype=float)
            finite = np.isfinite(prim)
            payload["stats"] = {
                "min": float(np.nanmin(prim)) if finite.any() else None,
                "max": float(np.nanmax(prim)) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(prim.size),
            }
        return _attach_science_evidence(
            payload, "sar.radiometric_calibration", tool="sar_calibrate",
            parameters_applied={
                "input_domain": res["meta"]["input_domain"],
                "output_product": res["meta"]["output_product"],
                "incidence_mode": res["meta"]["incidence_mode"],
                "incidence_lut": lut_source or "none",
                "to_db": res["meta"]["to_db"],
            },
            input_facts={"feature_count": int(arr.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "sar.radiometric_calibration", int(arr.size))],
        )

    def _bounded_sample(arr, max_side: int = 64, decimals: int = 4):
        """64×64 步进抽样（与 terrain 工具同约定），把平面载荷钉到有界。"""
        step = max(1, int(np.ceil(max(
            arr.shape[0] / max_side, arr.shape[1] / max_side, 1.0))))
        sub = arr[::step, ::step]
        return [[None if not np.isfinite(v) else round(float(v), decimals)
                 for v in row] for row in sub]

    @tool(registry, name="sar_glcm_texture",
          description=(
              "GLCM 窗口纹理特征（Haralick 1973）：contrast/dissimilarity/homogeneity/"
              "asm/energy/entropy/mean/variance/correlation，量化 2-98 分位（8/16/32/64 档）、"
              "P+Pᵀ 对称、d=1 偏移（all4=四方向均值）。纯 numpy 手工实现（确定性）。"
              "\n何时用：SAR/光学切片的纹理测度（如城市/植被结构区分）。"
              "\n关键约束：窗口 3/5/7；零方差窗口 correlation → NaN（诚实披露）；"
              "H·W·window²·方向数 ≤ 64M 操作估算上界。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "array": "2D 数组（任意连续值；内部量化）",
              "window": "奇数窗口：3(默认)/5/7",
              "levels": "量化档数：8/16/32/64（默认 16）",
              "directions": "all4(默认)/0/45/90/135",
              "properties": "逗号分隔属性子集或 'all'(默认)",
              "nodata_value": "可选标量哨兵值",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="slow",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          tags=("纹理", "glcm", "haralick", "对比度", "sar", "texture"),
          failure_modes=("invalid_args", "memory"),
          )
    async def sar_glcm_texture(
        array: List[List[float]],
        window: str = "3",
        levels: str = "16",
        directions: str = "all4",
        properties: str = "all",
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.glcm import glcm_texture

        params = apply_contract("sar_glcm_texture_analysis", {
            "window": window, "levels": levels,
            "directions": directions, "properties": properties,
        })
        arr = np.asarray(array, dtype=float)
        if arr.ndim != 2:
            raise ValueError(f"array 必须是 2D 数组，got ndim={arr.ndim}")
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联数组像元数 {arr.size} 超过工具上界 {_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="分块纹理计算，或走栅格工件路径",
            )
        res = glcm_texture(
            arr, window=int(params["window"]), levels=int(params["levels"]),
            directions=params["directions"],
            properties=params["properties"], nodata=nodata_value)
        # 多属性 × 全幅 .tolist() 最坏可达 ~9× 单幅载荷上限（评审 R3
        # MINOR-2）—— 属性平面只回 64×64 有界抽样，全幅统计照常返回；
        # 全幅栅格走栅格工件路径。
        prop_payload = {}
        prop_stats = {}
        for name, plane in res["properties"].items():
            finite = np.isfinite(plane)
            prop_payload[name] = _bounded_sample(plane.astype(float))
            prop_stats[name] = {
                "min": float(np.nanmin(plane)) if finite.any() else None,
                "max": float(np.nanmax(plane)) if finite.any() else None,
                "mean": float(np.nanmean(plane)) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
            }
        payload = {
            "success": True,
            "window": res["window"],
            "levels": res["levels"],
            "directions_used": res["directions_used"],
            "quantiles": [round(q, 6) for q in res["quantiles"]],
            "properties_sampled": prop_payload,
            "property_stats": prop_stats,
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "sar.glcm_texture", tool="sar_glcm_texture",
            parameters_applied={
                "window": res["window"],
                "levels": res["levels"],
                "directions": res["directions_used"],
                "properties": ",".join(res["properties"].keys()),
            },
            input_facts={"feature_count": int(arr.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "sar.glcm_texture", int(arr.size))],
        )

    @tool(registry, name="raster_pca",
          description=(
              "波段栈 PCA 降维（SVD 实现）：explained_variance_ratio、载荷矩阵、"
              "前 k 分量栅格。standardize=false(默认)=协方差 PCA；true=相关矩阵 PCA。"
              "\n何时用：多波段切片的去相关/降维/主成分合成图。"
              "\n关键约束：波段按给定顺序进入（PCA 不依赖语义角色——顺序披露）；"
              "公共有效掩膜（任一波段无效 → 整行剔除，占比披露）；"
              "n_bands·H·W ≤ 16M 像元（无流式实现，超限先拒绝）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 [n_bands][H][W]（同形 2D 数组按序堆叠）",
              "standardize": "True=相关矩阵 PCA（逐波段 z-score）；默认 False",
              "n_components": "输出分量栅格数 k（≤ n_bands；缺省 = n_bands）",
              "nodata_value": "可选标量哨兵值",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="slow",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          tags=("pca", "主成分", "降维", "波段合成", "svd", "去相关"),
          failure_modes=("invalid_args", "memory"),
          )
    async def raster_pca(
        bands: List[List[List[float]]],
        standardize: bool = False,
        n_components: Optional[int] = None,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.raster_pca import pca_bands

        contract_params: Dict[str, Any] = {"standardize": standardize}
        if n_components is not None:
            contract_params["n_components"] = n_components
        params = apply_contract("raster_pca_analysis", contract_params)

        stack = np.asarray(bands, dtype=float)
        if stack.ndim != 3:
            raise ValueError(
                f"bands 必须是 3D 波段栈 [n_bands][H][W]，got ndim={stack.ndim}")
        if stack.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联波段栈元素数 {stack.size} 超过工具上界 "
                f"{_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{stack.size} values (~{stack.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="降采样/分块，或走栅格工件路径",
            )
        res = pca_bands(
            stack, standardize=params["standardize"],
            n_components=params.get("n_components"), nodata=nodata_value)
        payload = {
            "success": True,
            "explained_variance_ratio": [
                round(v, 6) for v in res["explained_variance_ratio"]],
            "explained_variance": [
                round(v, 6) for v in res["explained_variance"]],
            "loadings": np.asarray(res["loadings"]).round(6).tolist(),
            "component_rasters": [
                r.round(6).tolist() for r in res["component_rasters"]],
            "scores_preview_rows": int(res["scores_preview"].shape[0]),
            "n_valid_pixels": res["n_valid_pixels"],
            "common_valid_fraction": round(res["common_valid_fraction"], 6),
            "pca_type": res["meta"]["pca_type"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.pca", tool="raster_pca",
            parameters_applied={
                "standardize": bool(params["standardize"]),
                "n_components": params.get("n_components") or "all",
            },
            input_facts={"feature_count": int(stack.size)},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.pca", int(stack.size))],
        )

    @tool(registry, name="tasseled_cap",
          description=(
              "Tasseled Cap 冠层变换（亮度/绿度/湿度三轴）：传感器系数注册表驱动"
              "（landsat5_tm=Crist&Cicone 1984、landsat8_oli=Baig 2014、"
              "sentinel2=Shi&Xu 2019）。波段必须按六语义角色显式映射"
              "（blue/green/red/nir/swir1/swir2），缺角色拒绝（不按位置猜测）。"
              "\n何时用：已有各波段反射率矩阵的物候/湿度结构变换。"
              "\n关键约束：reflectance_domain 仅披露（Baig/Shi 系数于 at-satellite "
              "反射率推导）；未注册传感器显式拒绝。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "语义角色 → 2D 数组（六角色全需：blue/green/red/nir/swir1/swir2）",
              "sensor": "landsat5_tm/landsat8_oli/sentinel2",
              "reflectance_domain": "surface/at_satellite(默认；仅披露)",
              "nodata_value": "可选标量哨兵值（任一角色无效 → 三轴全 NaN）",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="medium",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          tags=("tasseled_cap", "缨帽变换", "亮度", "绿度", "湿度", "canopy"),
          failure_modes=("invalid_args", "memory"),
          )
    async def tasseled_cap(
        bands: Dict[str, List[List[float]]],
        sensor: str,
        reflectance_domain: str = "at_satellite",
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.tasseled_cap import tasseled_cap as _tc

        params = apply_contract("tasseled_cap_analysis", {
            "sensor": sensor,
            "reflectance_domain": reflectance_domain,
        })
        arrays = _bands_to_arrays(bands)
        res = _tc(
            arrays, sensor=params["sensor"],
            reflectance_domain=params["reflectance_domain"],
            nodata=nodata_value)
        comp_payload = {}
        comp_stats = {}
        for name, plane in res["components"].items():
            finite = np.isfinite(plane)
            comp_payload[name] = plane.round(6).tolist()
            comp_stats[name] = {
                "min": float(np.nanmin(plane)) if finite.any() else None,
                "max": float(np.nanmax(plane)) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
            }
        payload = {
            "success": True,
            "sensor": res["meta"]["sensor"],
            "reflectance_domain": res["meta"]["reflectance_domain"],
            "roles_used": res["roles_used"],
            "coefficients": res["coefficients"],
            "components": comp_payload,
            "component_stats": comp_stats,
            "contribution": res["contribution"],
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "remote.tasseled_cap", tool="tasseled_cap",
            parameters_applied={
                "sensor": res["meta"]["sensor"],
                "reflectance_domain": res["meta"]["reflectance_domain"],
                "roles": ",".join(res["roles_used"]),
            },
            input_facts={"feature_count": int(
                next(iter(res["components"].values())).size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.tasseled_cap", int(
                    next(iter(res["components"].values())).size))],
        )

    @tool(registry, name="sar_temporal_composite",
          description=(
              "SAR 时序栈合成：mean/median/percentile 时间维聚合为单面"
              "（nodata 感知；median 为斑点拖尾下的鲁棒惯用）。"
              "诚实边界：不做滤波/定标的隐式前置（独立工具 sar_speckle_filter/"
              "sar_calibrate）。"
              "\n何时用：已对齐多期 SAR 切片的季节/年度底图合成。"
              "\n关键约束：栈深 ≤24、H·W ≤ 4096×4096；method=percentile 需显式"
              " percentile 参数（0-100）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "stack": "时序栈 [T][H][W]（T 个同形状 2D 切片，按时间序）",
              "method": "mean(默认)/median/percentile",
              "percentile": "分位数（0-100；method=percentile 时必需）",
              "nodata_value": "可选标量哨兵值",
          },
          side_effect="deterministic_compute",
          deterministic=True,
          network=False,
          latency_class="slow",
          memory_class="heavy",
          scale_class="large",
          output_semantic_type="stats",
          result_size_policy="bounded",
          crs_semantics="crs_agnostic",
          tags=("sar", "时序合成", "median", "年度合成", "底图", "composite"),
          failure_modes=("invalid_args", "memory"),
          )
    async def sar_temporal_composite(
        stack: List[List[List[float]]],
        method: str = "mean",
        percentile: Optional[float] = None,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_temporal import temporal_composite

        contract_params: Dict[str, Any] = {"method": method}
        if percentile is not None:
            contract_params["percentile"] = percentile
        params = apply_contract(
            "sar_temporal_composite_analysis", contract_params)
        arr = np.asarray(stack, dtype=float)
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联 SAR 栈元素数 {arr.size} 超过工具上界 {_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="分块合成，或走栅格工件路径",
            )
        res = temporal_composite(
            arr, method=params["method"],
            percentile=params.get("percentile"), nodata=nodata_value)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "method": res["method"],
            "percentile": res["percentile"],
            "time_slices": res["meta"]["time_slices"],
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
            payload, "sar.temporal_composite", tool="sar_temporal_composite",
            parameters_applied={
                "method": res["method"],
                "percentile": res["percentile"]
                if res["percentile"] is not None else "n/a",
                "time_slices": res["meta"]["time_slices"],
            },
            input_facts={"feature_count": int(res["array"].size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "sar.temporal_composite", int(res["array"].size))],
        )

    # ── Foundation V3：遥感 V3 批次（波段栈科学算法族）────────────────

    def _stack_from_3d(stack, what: str) -> np.ndarray:
        """3D 波段栈入参校验 + 工具级规模闸（与 raster_pca 同约定）。"""
        arr = np.asarray(stack, dtype=float)
        if arr.ndim != 3:
            raise ValueError(
                f"{what} 必须是 3D 波段栈 [n_bands][H][W]，got ndim={arr.ndim}")
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联{what}元素数 {arr.size} 超过工具上界 "
                f"{_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="降采样/分块，或走栅格工件路径",
            )
        return arr

    def _plane_payload(plane: np.ndarray, decimals: int = 6) -> dict:
        """单面统计（nan-aware，空面 None 兜底）。"""
        finite = np.isfinite(plane)
        return {
            "min": float(np.nanmin(plane)) if finite.any() else None,
            "max": float(np.nanmax(plane)) if finite.any() else None,
            "mean": float(np.nanmean(plane)) if finite.any() else None,
            "valid_pixels": int(np.sum(finite)),
            "total_pixels": int(plane.size),
        }

    @tool(registry, name="mnf_transform",
          description=(
              "最小噪声分数变换 MNF（Green 1988）：局部差分估计噪声协方差 → 噪声白化 → "
              "白化空间 PCA，分量按 SNR 排序（SNR=λ−1），含载荷与逆变换语义。"
              "\n何时用：多波段切片的去噪降维、按信噪比选分量、高维波段结构探查。"
              "\n何时不用：(1) 只要方差排序不看噪声 —— raster_pca；"
              "(2) 要独立源分离 —— ica_transform。"
              "\n关键约束：n_bands·H·W ≤ 16M 像元（无流式实现）；"
              "常量/共线波段使噪声协方差奇异（诚实拒绝）；公共有效掩膜。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}（按给定序进入，band_order 披露）",
              "n_components": "输出分量栅格数 k（≤ n_bands；缺省 = n_bands）",
              "noise_estimation": "噪声估计：local_diff（缺省，披露估计量）",
              "standardize": "True=逐波段 z-score 后变换；默认 False",
              "nodata_value": "可选标量哨兵值（任一波段无效 → 整像元剔除）",
          })
    async def mnf_transform(
        bands: Dict[str, List[List[float]]],
        n_components: Optional[int] = None,
        noise_estimation: str = "local_diff",
        standardize: bool = False,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import mnf as _mnf

        contract_params: Dict[str, Any] = {
            "noise_estimation": noise_estimation, "standardize": standardize}
        if n_components is not None:
            contract_params["n_components"] = n_components
        params = apply_contract("mnf_analysis", contract_params)

        arrays = _bands_to_arrays(bands)
        res = _mnf(arrays, params.get("n_components"),
                   noise_estimation=params["noise_estimation"],
                   standardize=params["standardize"], nodata=nodata_value)
        payload = {
            "success": True,
            "snr": [round(v, 6) for v in res["snr"]],
            "eigenvalues": [round(v, 6) for v in res["eigenvalues"]],
            "loadings_original": np.asarray(
                res["loadings_original"]).round(6).tolist(),
            "component_rasters": [
                r.round(6).tolist() for r in res["component_rasters"]],
            "n_components_rasters": res["meta"]["n_components_rasters"],
            "n_valid_pixels": res["n_valid_pixels"],
            "common_valid_fraction": round(res["common_valid_fraction"], 6),
            "band_order": res["meta"]["band_order"],
            "noise_estimation": res["meta"]["noise_estimation"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.mnf", tool="mnf_transform",
            parameters_applied={
                "n_components": params.get("n_components") or "all",
                "noise_estimation": params["noise_estimation"],
                "standardize": bool(params["standardize"]),
            },
            input_facts={"feature_count": int(
                next(iter(arrays.values())).size * len(arrays))},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.mnf", int(next(iter(arrays.values())).size
                                   * len(arrays)))],
        )

    @tool(registry, name="ica_transform",
          description=(
              "FastICA 独立成分分析（Hyvärinen 1999）：random_state=42 固定、"
              "unit-variance 白化，输出分量栅格 + 混合矩阵；收敛性显式披露。"
              "\n何时用：多波段切片的统计独立源分离/混叠信号拆解。"
              "\n何时不用：(1) 按方差/信噪比排序降维 —— raster_pca / mnf_transform。"
              "\n关键约束：n_valid ≥ max(8, k+2)；分量序与符号不唯一（算法固有）；"
              "未收敛 → converged=false 披露（不静默）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}",
              "n_components": "分量数 k（≤ n_bands；缺省 = n_bands）",
              "max_iter": "固定点迭代上限（默认 200）",
              "tol": "收敛容差（默认 1e-4）",
              "nodata_value": "可选标量哨兵值",
          })
    async def ica_transform(
        bands: Dict[str, List[List[float]]],
        n_components: Optional[int] = None,
        max_iter: int = 200,
        tol: float = 1e-4,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import ica as _ica

        contract_params: Dict[str, Any] = {"max_iter": max_iter, "tol": tol}
        if n_components is not None:
            contract_params["n_components"] = n_components
        params = apply_contract("ica_analysis", contract_params)

        arrays = _bands_to_arrays(bands)
        res = _ica(arrays, params.get("n_components"),
                   max_iter=params["max_iter"], tol=params["tol"],
                   nodata=nodata_value)
        payload = {
            "success": True,
            "converged": res["converged"],
            "n_iter": res["n_iter"],
            "mixing_matrix": np.asarray(res["mixing_matrix"]).round(6).tolist(),
            "component_rasters": [
                r.round(6).tolist() for r in res["component_rasters"]],
            "n_valid_pixels": res["n_valid_pixels"],
            "common_valid_fraction": round(res["common_valid_fraction"], 6),
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.ica", tool="ica_transform",
            parameters_applied={
                "n_components": params.get("n_components") or "all",
                "max_iter": params["max_iter"], "tol": params["tol"],
                "converged": res["converged"],
            },
            input_facts={"feature_count": int(
                next(iter(arrays.values())).size * len(arrays))},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            seed=42,
            diagnostics=[_backend_selection_diagnostic(
                "remote.ica", int(next(iter(arrays.values())).size
                                   * len(arrays)))],
        )

    @tool(registry, name="spectral_angle_mapper",
          description=(
              "光谱角制图 SAM（Kruse 1993）：逐像元光谱角 θ=arccos(⟨x,e⟩/(‖x‖‖e‖))，"
              "输出逐端元角度栅格 + argmin 类别栅格；零范数像元 → NaN（不产伪 0 角）。"
              "\n何时用：已有端元光谱（如植被/水体/矿物参考）的逐像元光谱匹配分类。"
              "\n何时不用：(1) 要分布差异敏感度量 —— spectral_information_divergence；"
              "(2) 无参考端元 —— 先 extract_endmembers_vca。"
              "\n关键约束：端元向量与波段序逐波段对齐；SAM 只看形状对亮度增益不变。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}（端元按同序对齐）",
              "endmembers": "端元 {name: 向量(len k)}（与波段逐波段对齐）",
              "angle_unit": "radians(默认)/degrees",
              "nodata_value": "可选标量哨兵值",
          })
    async def spectral_angle_mapper(
        bands: Dict[str, List[List[float]]],
        endmembers: Dict[str, List[float]],
        angle_unit: str = "radians",
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import spectral_angle_mapper as _sam

        params = apply_contract("sam_analysis", {"angle_unit": angle_unit})
        arrays = _bands_to_arrays(bands)
        res = _sam(arrays, endmembers, nodata=nodata_value)
        scale = 180.0 / np.pi if params["angle_unit"] == "degrees" else 1.0
        angles_payload = {
            name: (plane * scale).round(6).tolist()
            for name, plane in res["angles"].items()}
        angle_stats = {
            name: _plane_payload(plane * scale)
            for name, plane in res["angles"].items()}
        payload = {
            "success": True,
            "names": res["names"],
            "angle_unit": params["angle_unit"],
            "angles": angles_payload,
            "angle_stats": angle_stats,
            "class_raster": res["class_raster"].round(6).tolist(),
            "zero_norm_fraction": round(res["zero_norm_fraction"], 6),
            "n_valid_pixels": res["n_valid_pixels"],
            "band_order": res["meta"]["band_order"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.sam", tool="spectral_angle_mapper",
            parameters_applied={
                "angle_unit": params["angle_unit"],
                "endmembers": ",".join(res["names"]),
            },
            input_facts={"feature_count": int(
                next(iter(arrays.values())).size * len(arrays))},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.sam", int(next(iter(arrays.values())).size
                                   * len(arrays)))],
        )

    @tool(registry, name="spectral_information_divergence",
          description=(
              "光谱信息散度 SID（Chang 2000 对称形式）：p=x/Σx、q=e/Σe，"
              "D=Σp·ln(p/q)+Σq·ln(q/p)；比 SAM 对光谱分布差异更敏感。"
              "\n何时用：反射率类正值输入的逐像元光谱分布匹配（如矿物精细区分）。"
              "\n何时不用：(1) 含负值/零和输入（dB 等）—— 信息熵无定义会 NaN；"
              "(2) 只看形状 —— spectral_angle_mapper。"
              "\n关键约束：非正分量/非正和 → NaN（nonpositive_fraction 披露，不钳制）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}（须正值语义）",
              "endmembers": "端元 {name: 向量(len k)}",
              "divergence": "symmetric（实现约定，披露）",
              "nodata_value": "可选标量哨兵值",
          })
    async def spectral_information_divergence(
        bands: Dict[str, List[List[float]]],
        endmembers: Dict[str, List[float]],
        divergence: str = "symmetric",
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import (
            spectral_information_divergence as _sid,
        )

        params = apply_contract("sid_analysis", {"divergence": divergence})
        arrays = _bands_to_arrays(bands)
        res = _sid(arrays, endmembers, nodata=nodata_value)
        div_payload = {
            name: plane.round(6).tolist()
            for name, plane in res["divergence"].items()}
        div_stats = {
            name: _plane_payload(plane)
            for name, plane in res["divergence"].items()}
        payload = {
            "success": True,
            "names": res["names"],
            "divergence": div_payload,
            "divergence_stats": div_stats,
            "nonpositive_fraction": round(res["nonpositive_fraction"], 6),
            "n_valid_pixels": res["n_valid_pixels"],
            "band_order": res["meta"]["band_order"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.sid", tool="spectral_information_divergence",
            parameters_applied={
                "divergence": params["divergence"],
                "endmembers": ",".join(res["names"]),
                "nonpositive_fraction": round(res["nonpositive_fraction"], 6),
            },
            input_facts={"feature_count": int(
                next(iter(arrays.values())).size * len(arrays))},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.sid", int(next(iter(arrays.values())).size
                                   * len(arrays)))],
        )

    @tool(registry, name="matched_filter",
          description=(
              "匹配滤波目标检测（Boardman 1995）：全局协方差白化下的目标投影，"
              "score=tᵀΣ⁻¹(x−μ)/(tᵀΣ⁻¹t)——纯目标像元≈1、背景≈0（丰度式）。"
              "\n何时用：已知目标光谱在场景中的丰度式检测（矿物/人造目标初筛）。"
              "\n何时不用：(1) 无目标签名找异常 —— rx_anomaly；"
              "(2) 目标与背景强相关（tᵀΣ⁻¹t≈0）被诚实拒绝。"
              "\n关键约束：零方差波段剔除披露；单高斯背景假设。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}",
              "target_signal": "目标光谱向量（len = 波段数，逐波段对齐）",
              "center": "True=均值中心化（默认）；False=不中心化",
              "nodata_value": "可选标量哨兵值",
          })
    async def matched_filter(
        bands: Dict[str, List[List[float]]],
        target_signal: List[float],
        center: bool = True,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import matched_filter as _mf

        params = apply_contract("matched_filter_analysis", {"center": center})
        arrays = _bands_to_arrays(bands)
        res = _mf(arrays, target_signal, center=params["center"],
                  nodata=nodata_value)
        stats = _plane_payload(res["score"])
        payload = {
            "success": True,
            "score": res["score"].round(6).tolist(),
            "stats": stats,
            "dropped_bands": res["dropped_bands"],
            "band_order": res["meta"]["band_order"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.matched_filter", tool="matched_filter",
            parameters_applied={
                "center": bool(params["center"]),
                "dropped_bands": res["dropped_bands"] or "none",
            },
            input_facts={"feature_count": int(stats["total_pixels"])},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.matched_filter", int(stats["total_pixels"]))],
        )

    @tool(registry, name="rx_anomaly",
          description=(
              "RX 全局异常检测（Reed & Xiaoli 1990）：逐像元 Mahalanobis 距离 δ(x)，"
              "尺度不变岭正则 + mean(δ)+k·σ(δ) 启发式阈值建议。"
              "\n何时用：无目标签名的场景异常初筛（离群光谱像元定位）。"
              "\n何时不用：(1) 有目标光谱 —— matched_filter；"
              "(2) 需要局部/核 RX 的精细检测 —— 未实现（诚实披露）。"
              "\n关键约束：单高斯背景假设；常量场 → δ=0（零方差披露）；阈值非显著性检验。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}（≥3 波段推荐）",
              "regularize": "岭正则系数（默认 1e-6；Σ_r=Σ+regularize·(trΣ/k)·I）",
              "threshold_sigma": "阈值倍数 k（默认 3.0）",
              "nodata_value": "可选标量哨兵值",
          })
    async def rx_anomaly(
        bands: Dict[str, List[List[float]]],
        regularize: float = 1e-6,
        threshold_sigma: float = 3.0,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import rx_anomaly as _rx

        params = apply_contract("rx_analysis", {
            "regularize": regularize, "threshold_sigma": threshold_sigma})
        arrays = _bands_to_arrays(bands)
        res = _rx(arrays, regularize=params["regularize"],
                  threshold_sigma=params["threshold_sigma"],
                  nodata=nodata_value)
        stats = _plane_payload(res["delta"])
        payload = {
            "success": True,
            "delta": res["delta"].round(6).tolist(),
            "stats": stats,
            "threshold": round(res["threshold"], 6),
            "anomaly_fraction": round(res["anomaly_fraction"], 6),
            "zero_variance": res["zero_variance"],
            "band_order": res["meta"]["band_order"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.rx_anomaly", tool="rx_anomaly",
            parameters_applied={
                "regularize": params["regularize"],
                "threshold_sigma": params["threshold_sigma"],
                "anomaly_fraction": round(res["anomaly_fraction"], 6),
            },
            input_facts={"feature_count": int(stats["total_pixels"])},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.rx_anomaly", int(stats["total_pixels"]))],
        )

    @tool(registry, name="mad_change",
          description=(
              "MAD / IR-MAD 变化检测（Nielsen 1998）：两期栈标准化 → SVD-CCA → "
              "MAD 变分量（按规范相关升序，noisiest first）+ χ² 栅格"
              "（k dof，Nielsen 1998 χ²_k 惯例）。"
              "\n何时用：两期多波段影像的结构性变化检测（对线性辐射偏移/增益不变）。"
              "\n何时不用：(1) 单波段差值/比值 —— detect_raster_change/detect_ratio_change；"
              "(2) 恒定辐射偏移（标准化吸收，不构成检测目标）。"
              "\n关键约束：n_iterms≤10（IR-MAD 固定点迭代上限）；两期需同网格同波段数。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "stack_a": "T1 波段栈 [n_bands][H][W]",
              "stack_b": "T2 波段栈（与 T1 同形状）",
              "n_iterms": "IR-MAD 迭代次数（0=一次性 MAD，默认；≤10）",
              "nodata_value": "可选标量哨兵值（两期同值）",
          })
    async def mad_change(
        stack_a: List[List[List[float]]],
        stack_b: List[List[List[float]]],
        n_iterms: int = 0,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import mad_change as _mad

        params = apply_contract("mad_change_analysis", {"n_iterms": n_iterms})
        arr_a = _stack_from_3d(stack_a, "MAD stack_a")
        arr_b = _stack_from_3d(stack_b, "MAD stack_b")
        res = _mad(arr_a, arr_b, params["n_iterms"], nodata_a=nodata_value,
                   nodata_b=nodata_value)
        payload = {
            "success": True,
            "canonical_correlations": [round(v, 6) for v in
                                       res["canonical_correlations"]],
            "variate_variances": [round(v, 8) for v in
                                  res["variate_variances"]],
            "mad_rasters": [r.round(6).tolist() for r in res["mad_rasters"]],
            "chi2_raster": res["chi2_raster"].round(6).tolist(),
            "chi2_dof": res["meta"]["chi2_dof"],
            "iterations_ran": res["iterations_ran"],
            "converged": res["converged"],
            "convergence_delta": (
                None if res["convergence_delta"] is None
                else round(float(res["convergence_delta"]), 9)),
            "n_valid_pixels": res["n_valid_pixels"],
            "band_order": res["meta"]["band_order"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["weights_raster"] is not None:
            payload["weights_raster"] = res["weights_raster"].round(6).tolist()
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.mad_change", tool="mad_change",
            parameters_applied={
                "n_iterms": params["n_iterms"],
                "iterations_ran": res["iterations_ran"],
                "converged": res["converged"],
            },
            input_facts={"feature_count": int(arr_a.size)},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.mad_change", int(arr_a.size))],
        )

    @tool(registry, name="linear_unmixing",
          description=(
              "线性光谱解混 FCLS（Heinz & Chang 2001）：逐像元 "
              "min‖Ex−f‖² s.t. x≥0, Σx=1，输出 m 个丰度面（[0,1]）+ RMS 残差面"
              "（重建不确定性摘要）。与 endmember_vca 组成端元提取→丰度反演链。"
              "\n何时用：已知端元光谱（VCA/光谱库），要丰度/覆盖度反演"
              "（矿物丰度、植被/土壤/不透水面比例）。"
              "\n何时不用：(1) 无端元先找端元 —— extract_endmembers_vca；"
              "(2) 单目标检测 —— matched_filter；"
              "(3) 非线性混合（多层散射）不适用（诚实披露）。"
              "\n关键约束：端元矩阵 k 波段×m 端元逐波段对齐、列满秩"
              "（秩亏/端元数>波段数被拒绝）；内联数组 ≤4M 值/波段。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}（与端元矩阵波段维逐行对齐）",
              "endmembers": "端元矩阵 [n_bands][n_endmembers]（列=端元光谱）",
              "sum_to_one_weight": "和一约束 δ 增广权重（默认 1e6，一般无需调整）",
              "nodata_value": "可选标量哨兵值（任一波段无效 → 整像元 NaN）",
          })
    async def linear_unmixing(
        bands: Dict[str, List[List[float]]],
        endmembers: List[List[float]],
        sum_to_one_weight: float = 1e6,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import fcls_unmix as _fcls

        params = apply_contract("linear_unmixing_analysis", {
            "sum_to_one_weight": sum_to_one_weight})
        arrays = _bands_to_arrays(bands)
        e_mat = np.asarray(endmembers, dtype=float)
        if e_mat.ndim != 2:
            raise ValueError(
                f"endmembers 必须是 2D 矩阵 [n_bands][n_endmembers]，"
                f"got ndim={e_mat.ndim}")
        res = _fcls(arrays, e_mat, sum_to_one_weight=params["sum_to_one_weight"],
                    nodata=nodata_value)
        rms = res["rms_residual"]
        payload = {
            "success": True,
            "n_endmembers": res["meta"]["n_endmembers"],
            "abundances": [a.round(6).tolist() for a in res["abundances"]],
            "rms_residual": rms.round(6).tolist(),
            "rms_residual_stats": _plane_payload(rms),
            "n_valid_pixels": res["n_valid_pixels"],
            "band_order": res["meta"]["band_order"],
            "n_boundary_pixels_nnls": res["meta"]["n_boundary_pixels_nnls"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.linear_unmixing", tool="linear_unmixing",
            parameters_applied={
                "n_endmembers": res["meta"]["n_endmembers"],
                "n_boundary_pixels_nnls": res["meta"]["n_boundary_pixels_nnls"],
            },
            input_facts={"feature_count": int(rms.size)},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.linear_unmixing", int(rms.size))],
        )

    @tool(registry, name="medoid_composite",
          description=(
              "medoid 时序合成（Flood 2013 多维中位数）：多时相波段栈逐像元选"
              "到其余观测波段欧氏距离和最小的**真实切片**——跨波段光谱一致性保持"
              "（逐波段 median 会拼出不存在观测）；云/影污染时相自动边缘化。"
              "\n何时用：多时相光学合成（季节/年度底图）、云污染栈的鲁棒合成。"
              "\n何时不用：(1) 单波段时序统计 —— sar.temporal_stats；"
              "(2) 需要显式加权/质量掩膜合成 —— 未实现（披露）。"
              "\n关键约束：输入须已配准对齐 (T,k,H,W)；2≤T≤24、T·H·W≤32M；"
              "任一波段无效的切片整条剔除。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "stack": "4D 时相栈 [T][n_bands][H][W]（须已配准对齐）",
              "nodata_value": "可选标量哨兵值（切片任一波段等于该值 → 整条剔除）",
          })
    async def medoid_composite(
        stack: List[List[List[List[float]]]],
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.geo_analysis.sar_temporal import (
            medoid_composite as _medoid,
        )

        arr = np.asarray(stack, dtype=float)
        if arr.ndim != 4:
            raise ValueError(
                "stack 必须是 4D 时相栈 [T][n_bands][H][W]，"
                f"got ndim={arr.ndim}")
        if arr.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"内联时相栈元素数 {arr.size} 超过工具上界 "
                f"{_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{arr.size} values (~{arr.size * 8 / 1e6:.1f} MB float64)",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="减少时相数或降采样/分块",
            )
        res = _medoid(arr, nodata=nodata_value)
        out = res["array"]
        payload = {
            "success": True,
            "n_bands": res["meta"]["n_bands"],
            "time_slices": res["meta"]["time_slices"],
            "array": out.round(6).tolist(),
            "medoid_index": res["medoid_index"].tolist(),
            "pixels_all_invalid": res["meta"]["pixels_all_invalid"],
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "remote.medoid_composite", tool="medoid_composite",
            parameters_applied={
                "time_slices": res["meta"]["time_slices"],
                "n_bands": res["meta"]["n_bands"],
            },
            input_facts={"feature_count": int(out.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.medoid_composite", int(out.size))],
        )

    @tool(registry, name="segment_image",
          description=(
              "图像分割（Lloyd 1982 k-means 基座）：标准化光谱特征 + 加权空间坐标特征，"
              "random_state=42 + n_init=10 确定性；输出标签栅格 + 逐段均值光谱。"
              "\n何时用：小范围切片的无监督分块（地物初分/分割底座）。"
              "\n何时不用：要 SLIC 超像素（几何紧致/watershed 精化）—— 未实现（诚实边界）。"
              "\n关键约束：compactness 仅是空间权重乘子（非 SLIC 语义）；"
              "段数 > 有效像元数时钳制并披露。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}",
              "n_segments": "目标段数（默认 50；≥2）",
              "spatial_weight": "空间坐标特征权重 0-1（默认 0.5）",
              "compactness": "空间权重乘子 0-1（默认 0.5；非 SLIC 语义，披露）",
              "nodata_value": "可选标量哨兵值",
          })
    async def segment_image(
        bands: Dict[str, List[List[float]]],
        n_segments: int = 50,
        spatial_weight: float = 0.5,
        compactness: float = 0.5,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import segment_image as _seg

        params = apply_contract("segmentation_analysis", {
            "n_segments": n_segments, "spatial_weight": spatial_weight,
            "compactness": compactness})
        arrays = _bands_to_arrays(bands)
        res = _seg(arrays, params["n_segments"],
                   spatial_weight=params["spatial_weight"],
                   compactness=params["compactness"], nodata=nodata_value)
        label_finite = np.isfinite(res["label_raster"])
        payload = {
            "success": True,
            "label_raster": res["label_raster"].round(6).tolist(),
            "label_stats": {
                "segments_realized": res["n_segments_realized"],
                "labeled_pixels": int(np.sum(label_finite)),
                "total_pixels": int(res["label_raster"].size),
            },
            "segment_mean_spectra": res["segment_mean_spectra"],
            "n_segments_requested": params["n_segments"],
            "n_segments_realized": res["n_segments_realized"],
            "band_order": res["meta"]["band_order"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.segmentation", tool="segment_image",
            parameters_applied={
                "n_segments": params["n_segments"],
                "n_segments_realized": res["n_segments_realized"],
                "spatial_weight": params["spatial_weight"],
                "compactness": params["compactness"],
            },
            input_facts={"feature_count": int(
                next(iter(arrays.values())).size * len(arrays))},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            seed=42,
            diagnostics=[_backend_selection_diagnostic(
                "remote.segmentation", int(next(iter(arrays.values())).size
                                            * len(arrays)))],
        )

    @tool(registry, name="extract_endmembers_vca",
          description=(
              "端元提取 VCA（Nascimento & Dias 2005 简化确定性变体，EXPERIMENTAL）："
              "SVD 降维 + 随机投影逐顶点选择；输出端元光谱 + 像元位置。"
              "\n何时用：疑似含纯像元的高光谱/多波段切片的端元初提取（结果需人工核验）。"
              "\n何时不用：强混合无纯像元场景（恢复的是凸包顶点，非真实端元）。"
              "\n关键约束：2 ≤ n_endmembers < n_bands（守卫）；EXPERIMENTAL 成熟度。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}",
              "n_endmembers": "端元数 m（必须 < 波段数；≥2）",
              "seed": "随机投影种子（默认 42，固定可复现）",
              "nodata_value": "可选标量哨兵值",
          })
    async def extract_endmembers_vca(
        bands: Dict[str, List[List[float]]],
        n_endmembers: int,
        seed: int = 42,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import (
            extract_endmembers_vca as _vca,
        )

        params = apply_contract("endmember_vca_analysis", {
            "n_endmembers": n_endmembers, "seed": seed})
        arrays = _bands_to_arrays(bands)
        res = _vca(arrays, params["n_endmembers"], seed=params["seed"],
                   nodata=nodata_value)
        em = np.asarray(res["endmembers"])
        payload = {
            "success": True,
            "endmembers": em.round(6).tolist(),
            "locations": res["locations"],
            "n_endmembers": res["meta"]["n_endmembers"],
            "band_order": res["meta"]["band_order"],
            "scientific_status": res["meta"]["scientific_status"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.endmember_vca", tool="extract_endmembers_vca",
            parameters_applied={
                "n_endmembers": params["n_endmembers"], "seed": params["seed"],
            },
            input_facts={"feature_count": int(
                next(iter(arrays.values())).size * len(arrays))},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
            seed=int(params["seed"]),
        )

    @tool(registry, name="band_correlation_table",
          description=(
              "波段×波段 Pearson 相关矩阵 + 逐对样本数（stats_table 形）。"
              "公共有效掩膜（任一波段无效 → 整像元剔除，非 pairwise-complete，披露）。"
              "\n何时用：波段冗余/共线诊断（PCA/MNF 前的结构探查）。"
              "\n何时不用：非线性关联（互信息等未实现）。"
              "\n关键约束：零方差波段行列 NaN（不伪造）；standardize 不改变 r（披露）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}（≥2 波段）",
              "standardize": "逐波段 z-score（r 不变——线性不变性披露）；默认 False",
              "nodata_value": "可选标量哨兵值",
          })
    async def band_correlation_table(
        bands: Dict[str, List[List[float]]],
        standardize: bool = False,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import (
            band_correlation_table as _bct,
        )

        params = apply_contract("band_correlation_analysis",
                                {"standardize": standardize})
        arrays = _bands_to_arrays(bands)
        res = _bct(arrays, standardize=params["standardize"],
                   nodata=nodata_value)
        payload = {
            "success": True,
            "columns": res["columns"],
            "rows": res["rows"],
            "correlation": res["correlation"],
            "n_observations": res["n_observations"],
            "n_valid_pixels": res["n_valid_pixels"],
            "pairing": res["meta"]["pairing"],
            "coefficient": res["meta"]["coefficient"],
            "disclosure": res["meta"]["disclosure"],
        }
        if res["warnings"]:
            payload["warnings"] = res["warnings"]
        return _attach_science_evidence(
            payload, "remote.band_correlation", tool="band_correlation_table",
            parameters_applied={"standardize": bool(params["standardize"])},
            input_facts={"feature_count": int(res["n_valid_pixels"])},
            warnings=res["warnings"] or [res["meta"]["disclosure"]],
        )

    @tool(registry, name="temporal_features",
          description=(
              "逐像元时序特征（栈第 0 轴=时间序）：min/max/mean/std/amplitude/"
              "first−last + 单周期谐波（幅值/相位，联合线性趋势 LS）。"
              "\n何时用：多时相切片的逐像元物候描述统计（幅值/趋势/季节振荡强度）。"
              "\n何时不用：物候期提取/双谐波/SG 滤波——未实现（诚实边界披露）。"
              "\n关键约束：谐波要求完整序列 + T≥4（否则 NaN）；std 为总体 ddof=0。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "stack": "时序栈 [T][H][W]（T 个同形状切片，按时间序）",
              "features": "逗号分隔特征子集或 'all'（默认）",
              "nodata_value": "可选标量哨兵值",
          })
    async def temporal_features(
        stack: List[List[List[float]]],
        features: str = "all",
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import temporal_features as _tf

        params = apply_contract("temporal_features_analysis",
                                {"features": features})
        arr = _stack_from_3d(stack, "时序栈")
        res = _tf(arr, nodata=nodata_value)
        all_features = res["features"]
        requested = [f.strip() for f in params["features"].split(",")
                     if f.strip()] \
            if params["features"].strip().lower() != "all" \
            else list(all_features.keys())
        unknown = [f for f in requested if f not in all_features]
        if unknown:
            raise ValueError(
                f"未知特征 {unknown}；可用: {sorted(all_features)}")

        # 多面载荷有界抽样（GLCM properties_sampled 同约定）
        features_sampled = {
            name: _bounded_sample(plane.astype(float))
            for name, plane in all_features.items() if name in requested
        }
        feature_stats = {
            name: _plane_payload(plane)
            for name, plane in all_features.items() if name in requested
        }
        payload = {
            "success": True,
            "time_slices": res["meta"]["time_slices"],
            "features_requested": requested,
            "features_sampled": features_sampled,
            "feature_stats": feature_stats,
            "complete_series_fraction": round(
                res["complete_series_fraction"], 6),
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "remote.temporal_features", tool="temporal_features",
            parameters_applied={
                "features": ",".join(requested),
                "time_slices": res["meta"]["time_slices"],
            },
            input_facts={"feature_count": int(arr.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.temporal_features", int(arr.size))],
        )

    @tool(registry, name="robust_normalize",
          description=(
              "稳健波段/场景归一化：逐波段 2-98 分位（可调）拉伸到 [0,1]，"
              "或匹配到参考栈同序波段的分位区间（NaN-aware，逐波段分位披露）。"
              "\n何时用：跨期影像辐射一致性预处理（线性增益/偏移差异校正）。"
              "\n何时不用：非线性辐射差异（直方图形状/PIF 全量匹配未实现，披露）。"
              "\n关键约束：percentile_match 需要 reference；常量波段 → 诚实拒绝。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "bands": "波段栈 {role_or_index: 2D 数组}",
              "method": "percentile_match(默认，需 reference)/percentile_stretch",
              "reference": "参考栈 [n_bands][H][W]（同波段数；网格可不同）",
              "lower_percentile": "下分位（默认 2.0）",
              "upper_percentile": "上分位（默认 98.0）",
              "nodata_value": "可选标量哨兵值",
          })
    async def robust_normalize(
        bands: Dict[str, List[List[float]]],
        method: str = "percentile_match",
        reference: Optional[List[List[List[float]]]] = None,
        lower_percentile: float = 2.0,
        upper_percentile: float = 98.0,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import robust_normalize as _rn

        contract_params: Dict[str, Any] = {
            "method": method,
            "lower_percentile": lower_percentile,
            "upper_percentile": upper_percentile,
        }
        params = apply_contract("robust_normalize_analysis", contract_params)
        arrays = _bands_to_arrays(bands)
        ref_stack = None
        if reference is not None:
            ref_stack = _stack_from_3d(reference, "reference 栈")
        res = _rn(arrays, method=params["method"], reference=ref_stack,
                  lower_percentile=params["lower_percentile"],
                  upper_percentile=params["upper_percentile"],
                  nodata=nodata_value)
        planes = res["normalized"]
        finite = np.isfinite(planes)
        payload = {
            "success": True,
            "method": res["method"],
            "normalized": planes.round(6).tolist(),
            "stats": {
                "min": float(np.nanmin(planes)) if finite.any() else None,
                "max": float(np.nanmax(planes)) if finite.any() else None,
                "valid_cells": int(np.sum(finite)),
                "total_cells": int(planes.size),
            },
            "percentiles_used": res["percentiles_used"],
            "band_order": res["meta"]["band_order"],
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "remote.robust_normalize", tool="robust_normalize",
            parameters_applied={
                "method": res["method"],
                "quantiles": [params["lower_percentile"],
                              params["upper_percentile"]],
                "has_reference": ref_stack is not None,
            },
            input_facts={"feature_count": int(planes.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "remote.robust_normalize", int(planes.size))],
        )

    @tool(registry, name="cloud_qc_basic",
          description=(
              "云 QC 基础咨询掩膜（EXPERIMENTAL）：brightness=(red+nir)/2 亮度阈值"
              "（缺省场景 97.5 分位）+ 可选 |NDVI| 近零条件；qc_mask=True=疑似云。"
              "\n何时用：光学双波段（red/nir）切片的快速亮云初筛。"
              "\n何时不用：当云概率产品用——非 Fmask：无热红外/卷云/视差检验（强披露）；"
              "亮地物（屋顶/沙地/雪）会误报。"
              "\n关键约束：EXPERIMENTAL；显式 brightness_thresholds 优先于分位阈值。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "red": "红光 2D 数组（反射率语义）",
              "nir": "近红外 2D 数组（与 red 同形状）",
              "brightness_thresholds": "可选显式亮度绝对阈值（缺省按 97.5 分位）",
              "brightness_percentile": "亮度分位（50-100，默认 97.5）",
              "ndvi_max_abs": "可选 |NDVI| ≤ 阈值条件（云光谱平坦）",
          })
    async def cloud_qc_basic(
        red: List[List[float]],
        nir: List[List[float]],
        brightness_thresholds: Optional[float] = None,
        brightness_percentile: float = 97.5,
        ndvi_max_abs: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.rs_v3 import cloud_qc_basic as _qc

        contract_params: Dict[str, Any] = {
            "brightness_percentile": brightness_percentile}
        if brightness_thresholds is not None:
            contract_params["brightness_threshold"] = brightness_thresholds
        if ndvi_max_abs is not None:
            contract_params["ndvi_max_abs"] = ndvi_max_abs
        params = apply_contract("cloud_qc_analysis", contract_params)

        red_arr = np.asarray(red, dtype=float)
        nir_arr = np.asarray(nir, dtype=float)
        for _name, _arr in (("red", red_arr), ("nir", nir_arr)):
            if _arr.ndim != 2:
                raise ValueError(
                    f"{_name} 必须是 2D 数组，got ndim={_arr.ndim}")
            if _arr.size > _TOOL_ARRAY_MAX_VALUES:
                from app.lib.gis.scientific_errors import ResourceScaleMismatch

                raise ResourceScaleMismatch(
                    f"cloud_qc_basic {_name} 数组超限：{_arr.size} 值",
                    estimated=f"{_arr.size * 8 / 1e6:.1f} MB float64",
                    limit=f"≤{_TOOL_ARRAY_MAX_VALUES} values",
                    correction_hint="分块处理或走栅格工件路径",
                )
        res = _qc(
            red_arr, nir_arr,
            brightness_thresholds=params.get("brightness_threshold"),
            brightness_percentile=params["brightness_percentile"],
            ndvi_max_abs=params.get("ndvi_max_abs"))
        mask = res["qc_mask"]
        payload = {
            "success": True,
            "qc_mask": mask.tolist(),
            "brightness": res["brightness"].round(6).tolist(),
            "threshold": round(res["threshold"], 6),
            "threshold_source": res["meta"]["threshold_source"],
            "ndvi_condition": res["ndvi_condition"],
            "suspect_fraction": round(res["suspect_fraction"], 6),
            "suspect_pixels": int(np.sum(mask)),
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "remote.cloud_qc", tool="cloud_qc_basic",
            parameters_applied={
                "brightness_percentile": params["brightness_percentile"],
                "explicit_threshold": params.get("brightness_threshold")
                is not None,
                "ndvi_condition": res["ndvi_condition"],
                "suspect_fraction": round(res["suspect_fraction"], 6),
            },
            input_facts={"feature_count": int(red_arr.size)},
            warnings=[res["meta"]["disclosure"]],
        )

    # ── Foundation V3：SAR 批次（热噪声 / 量纲换算 / MT-Lee / 相干性 / ──
    # ── RTC / 叠掩阴影 / ENL 图）────────────────────────────────────────

    def _plane_from_json(arr, what: str) -> np.ndarray:
        """2D 内联数组校验 + 规模闸（与 _bands_to_arrays 同约定）。"""
        plane = np.asarray(arr, dtype=float)
        if plane.ndim != 2:
            raise ValueError(f"{what} 必须是 2D 数组，got ndim={plane.ndim}")
        if plane.size > _TOOL_ARRAY_MAX_VALUES:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"{what} 像元数 {plane.size} 超过内联数组工具上界 "
                f"{_TOOL_ARRAY_MAX_VALUES}",
                estimated=f"{plane.size * 8 / 1e6:.1f} MB float64",
                limit=f"≤{_TOOL_ARRAY_MAX_VALUES}",
                correction_hint="分块处理，或走栅格工件路径",
            )
        return plane

    @tool(registry, name="sar_remove_thermal_noise",
          description=(
              "SAR 热噪声去除：I_dn = max(I − N, 0)（标量噪声底或逐像元噪声 "
              "LUT 相减，二者互斥）；钳 0 像元数披露。"
              "诚实边界：Sentinel-1 GRD IPF 噪声 LUT 是 annotation XML"
              "（denoising 需逐 swath 插值）——本工具接收**已提取**的"
              "噪声底/LUT，不解析 SAFE XML。"
              "\n何时用：Sentinel-1 GRD 强度切片定标前的噪声底扣除"
              "（配合 sar_calibrate）。"
              "\n关键约束：输入须线性强度（dB 被拒绝）；noise_floor 或 "
              "noise_lut 必须提供其一（绝不虚构噪声参数）。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "intensity": "2D 线性强度数组（非负）",
              "noise_floor": "标量噪声底（≥0，线性强度域；与 noise_lut 互斥）",
              "noise_lut": "可选逐像元噪声 LUT（2D 数组，与 intensity 同形）",
              "nodata_value": "可选标量哨兵值",
          })
    async def sar_remove_thermal_noise(
        intensity: List[List[float]],
        noise_floor: Optional[float] = None,
        noise_lut: Optional[List[List[float]]] = None,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_calibration import remove_thermal_noise

        contract_params: Dict[str, Any] = {}
        if noise_floor is not None:
            contract_params["noise_floor"] = noise_floor
        params = apply_contract("sar_thermal_noise_removal_analysis",
                                contract_params)
        arr = _plane_from_json(intensity, "intensity")
        lut = None
        if noise_lut is not None:
            lut = _plane_from_json(noise_lut, "noise_lut")
            if arr.shape != lut.shape:
                raise ValueError(
                    f"noise_lut 形状 {lut.shape} 与 intensity {arr.shape} 不一致")
        res = remove_thermal_noise(
            arr, params.get("noise_floor"), lut, nodata=nodata_value)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "mode": res["mode"],
            "clamped_pixels": res["meta"]["clamped_pixels"],
            "invalid_pixels": res["meta"]["invalid_pixels"],
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
            payload, "sar.thermal_noise_removal",
            tool="sar_remove_thermal_noise",
            parameters_applied={
                "mode": res["mode"],
                "noise_floor": res["meta"]["noise_floor"],
                "clamped_pixels": res["meta"]["clamped_pixels"],
            },
            input_facts={"feature_count": int(arr.size)},
            warnings=[res["meta"]["disclosure"]],
        )

    @tool(registry, name="sar_log_scale",
          description=(
              "SAR 量纲换算（纯代数恒等式）：amplitude_to_intensity（A²）、"
              "intensity_to_amplitude（√I）、linear_to_db（10·log₁₀）、"
              "db_to_linear（10^(dB/10)）。"
              "\n何时用：定标前后的振幅/强度/dB 域统一（配合 sar_calibrate）。"
              "\n关键约束：振幅/强度域负值 → NaN（计数披露）；linear_to_db "
              "对 ≤0 钳 ε=1e-12 下限（计数披露，非静默）；无定标语义。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "array": "2D 数组",
              "mode": "换算方向：amplitude_to_intensity/intensity_to_amplitude/"
                      "linear_to_db/db_to_linear",
              "nodata_value": "可选标量哨兵值",
          })
    async def sar_log_scale_tool(
        array: List[List[float]],
        mode: str,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_calibration import sar_log_scale

        params = apply_contract("sar_log_scaling_analysis", {"mode": mode})
        arr = _plane_from_json(array, "array")
        res = sar_log_scale(arr, params["mode"], nodata=nodata_value)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "mode": res["mode"],
            "formula": res["meta"]["formula"],
            "floored_cells": res["meta"]["floored_cells"],
            "nonpositive_to_nan": res["meta"]["nonpositive_to_nan"],
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
            payload, "sar.log_scaling", tool="sar_log_scale",
            parameters_applied={"mode": res["mode"]},
            input_facts={"feature_count": int(arr.size)},
            warnings=[res["meta"]["disclosure"]],
        )

    @tool(registry, name="sar_multitemporal_speckle",
          description=(
              "多时相 SAR 斑点抑制（强度域 MT-Lee）：时序均值与空域 Lee 估计的"
              "逐像元逆方差加权（确定性、无随机成分）。"
              "诚实边界：非 Quegan 谱域多时相滤波（需 SLC 复数相干分解）——"
              "强度栈近似，披露。"
              "\n何时用：≥3 期已配准对齐的 SAR 强度栈（时序底图去斑）。"
              "\n关键约束：T≥3、T≤24、H·W≤4096²；栈须对齐；ENL 缺省整图矩估计。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "stack": "时序栈 [T][H][W]（T≥3 个同形状切片，按时间序）",
              "window": "空域 Lee 窗口：3(默认)/5/7",
              "enl": "等效视数（>0；缺省整图矩估计并披露）",
              "nodata_value": "可选标量哨兵值（逐切片 + 时序共同感知）",
          })
    async def sar_multitemporal_speckle(
        stack: List[List[List[float]]],
        window: str = "3",
        enl: Optional[float] = None,
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_v3 import multitemporal_speckle

        contract_params: Dict[str, Any] = {"window": window}
        if enl is not None:
            contract_params["enl"] = enl
        params = apply_contract("sar_multitemporal_speckle_analysis",
                                contract_params)
        arr = _stack_from_3d(stack, "MT-Lee 时序栈")
        res = multitemporal_speckle(
            arr, window=int(params["window"]), enl=params.get("enl"),
            nodata=nodata_value)
        out = np.asarray(res["stack"], dtype=float)
        finite = np.isfinite(out)
        payload = {
            "success": True,
            "time_slices": res["meta"]["time_slices"],
            "window": res["meta"]["window"],
            "enl": round(res["meta"]["enl"], 6),
            "enl_source": res["meta"]["enl_source"],
            "mean_temporal_weight": (
                round(res["mean_temporal_weight"], 6)
                if res["mean_temporal_weight"] is not None else None),
            "pixels_fallback_spatial": res["meta"]["pixels_fallback_spatial"],
            "pixels_fallback_temporal": res["meta"]["pixels_fallback_temporal"],
            "stats": {
                "min": float(np.nanmin(out)) if finite.any() else None,
                "max": float(np.nanmax(out)) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(out.size),
            },
            "stack": out.round(6).tolist(),
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "sar.multitemporal_speckle",
            tool="sar_multitemporal_speckle",
            parameters_applied={
                "window": res["meta"]["window"],
                "enl_source": res["meta"]["enl_source"],
                "time_slices": res["meta"]["time_slices"],
            },
            input_facts={"feature_count": int(out.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "sar.multitemporal_speckle", int(out.size))],
        )

    @tool(registry, name="sar_coherence_estimate",
          description=(
              "复数相干性估计（EXPERIMENTAL）：γ = |Σ a·b*|/√(Σ|a|²Σ|b|²)"
              "（窗口化，nodata 感知），输出 γ 栅格 + 窗口有效对数。"
              "两历元各为 (re, im) 双通道复 SLC。"
              "\n何时用：双期 SLC 的变化/失相干初筛（InSAR 基础量）。"
              "\n何时不用：只有强度/幅度（无相位）——被类型化拒绝"
              "（相干性仅凭强度不物理）。"
              "\n关键约束：EXPERIMENTAL（无轨道元数据/配准质量输入）；"
              "γ 钳 [0,1]（计数披露）；不输出干涉相位/解缠。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "slc_a_real": "历元 A 实部 2D 数组",
              "slc_a_imag": "历元 A 虚部 2D 数组（与实部同形）",
              "slc_b_real": "历元 B 实部 2D 数组",
              "slc_b_imag": "历元 B 虚部 2D 数组",
              "window": "估计窗口：3/5(默认)/7",
              "nodata_value": "可选标量哨兵值（对两历元实部生效）",
          })
    async def sar_coherence_estimate(
        slc_a_real: List[List[float]],
        slc_a_imag: List[List[float]],
        slc_b_real: List[List[float]],
        slc_b_imag: List[List[float]],
        window: str = "5",
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_v3 import coherence_estimate

        params = apply_contract("sar_coherence_analysis", {"window": window})
        re_a = _plane_from_json(slc_a_real, "slc_a_real")
        im_a = _plane_from_json(slc_a_imag, "slc_a_imag")
        re_b = _plane_from_json(slc_b_real, "slc_b_real")
        im_b = _plane_from_json(slc_b_imag, "slc_b_imag")
        res = coherence_estimate(
            (re_a, im_a), (re_b, im_b), window=int(params["window"]),
            nodata=nodata_value)
        finite = np.isfinite(res["gamma"])
        payload = {
            "success": True,
            "window": res["meta"]["window"],
            "scientific_status": res["meta"]["scientific_status"],
            "clamped_pixels": res["meta"]["clamped_pixels"],
            "stats": {
                "min": float(np.nanmin(res["gamma"])) if finite.any() else None,
                "max": float(np.nanmax(res["gamma"])) if finite.any() else None,
                "mean": float(np.nanmean(res["gamma"])) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(res["gamma"].size),
            },
            "gamma": res["gamma"].round(6).tolist(),
            "valid_pairs": np.asarray(res["valid_pairs"]).round(3).tolist(),
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "sar.coherence", tool="sar_coherence_estimate",
            parameters_applied={"window": res["meta"]["window"]},
            input_facts={"feature_count": int(re_a.size * 2)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "sar.coherence", int(re_a.size))],
        )

    @tool(registry, name="sar_radiometric_terrain_correction",
          description=(
              "SAR 地形辐射校正 RTC（Small 2011）：γ_flat = σ⁰·cosθi/cosθl；"
              "本地入射角由 DEM Horn 梯度导出（坡度/坡向 + 雷达视线方位角）。"
              "叠掩/阴影像元 → nodata（计数披露）。"
              "\n何时用：山区 σ⁰ 切片的地形效应平坦化（同网格 DEM）。"
              "\n关键约束：range-only 几何简化（无轨道元数据，披露）；"
              "cell_size 与 radar_range_azimuth（地面指向传感器方位角，"
              "[0,360) 度）显式必需；入射角标量或 2D LUT。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "sigma0": "2D σ⁰ 强度数组（线性域）",
              "dem": "DEM 2D 数组（与 sigma0 同形、同网格对齐；米）",
              "cell_size": "DEM 像元大小（米，>0；必需）",
              "radar_range_azimuth": "雷达视线方位角（度 [0,360)，顺时针自北、"
                                     "地面指向传感器；降轨 IW≈270°；必需）",
              "incidence_deg": "标量入射角（度，0-90 开区间；与 incidence_lut 互斥）",
              "incidence_lut": "可选逐像元入射角 LUT（2D 数组，与 sigma0 同形）",
              "nodata_value": "σ⁰ 哨兵值（可选）",
              "dem_nodata": "DEM 哨兵值（可选）",
          })
    async def sar_radiometric_terrain_correction(
        sigma0: List[List[float]],
        dem: List[List[float]],
        cell_size: float,
        radar_range_azimuth: float,
        incidence_deg: Optional[float] = None,
        incidence_lut: Optional[List[List[float]]] = None,
        nodata_value: Optional[float] = None,
        dem_nodata: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_v3 import radiometric_terrain_correction

        contract_params: Dict[str, Any] = {
            "cell_size": cell_size,
            "radar_range_azimuth": radar_range_azimuth,
        }
        if incidence_deg is not None:
            contract_params["incidence_deg"] = incidence_deg
        params = apply_contract("sar_rtc_analysis", contract_params)
        sig = _plane_from_json(sigma0, "sigma0")
        dem_arr = _plane_from_json(dem, "dem")
        if dem_arr.shape != sig.shape:
            raise ValueError(
                f"dem 形状 {dem_arr.shape} 与 sigma0 {sig.shape} 不一致")
        inc_map = None
        if incidence_lut is not None:
            inc_map = _plane_from_json(incidence_lut, "incidence_lut")
            if inc_map.shape != sig.shape:
                raise ValueError(
                    f"incidence_lut 形状 {inc_map.shape} 与 sigma0 "
                    f"{sig.shape} 不一致")
        res = radiometric_terrain_correction(
            sig, dem_arr, params["cell_size"], params["radar_range_azimuth"],
            incidence_deg=params.get("incidence_deg"), incidence_map=inc_map,
            nodata=nodata_value, dem_nodata=dem_nodata)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "cell_size": res["meta"]["cell_size"],
            "radar_range_azimuth": res["meta"]["radar_range_azimuth"],
            "incidence_mode": res["meta"]["incidence_mode"],
            "lut_pixels": res["meta"]["lut_pixels"],
            "invalid_geometry_pixels": res["meta"]["invalid_geometry_pixels"],
            "dem_invalid_pixels": res["meta"]["dem_invalid_pixels"],
            "local_incidence_deg": res["local_incidence_deg"].round(4).tolist(),
            "stats": {
                "min": float(np.nanmin(res["array"])) if finite.any() else None,
                "max": float(np.nanmax(res["array"])) if finite.any() else None,
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(res["array"].size),
            },
            "array": res["array"].round(6).tolist(),
            "convention": res["meta"]["convention"],
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "sar.rtc", tool="sar_radiometric_terrain_correction",
            parameters_applied={
                "cell_size": params["cell_size"],
                "radar_range_azimuth": params["radar_range_azimuth"],
                "incidence_mode": res["meta"]["incidence_mode"],
                "invalid_geometry_pixels":
                    res["meta"]["invalid_geometry_pixels"],
            },
            input_facts={"feature_count": int(sig.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic("sar.rtc", int(sig.size))],
        )

    @tool(registry, name="sar_layover_shadow_mask",
          description=(
              "SAR 叠掩/阴影几何分类：{0=normal,1=layover,2=shadow,3=nodata}"
              " + 占比。layover = 面坡且坡度陡于入射角（α > θi）；"
              "shadow = 本地入射角余弦 ≤ 0（背坡超掠射角）。"
              "\n何时用：山区 SAR 可用性制图（叠掩/阴影像元识别）。"
              "\n何时不含：视线遮蔽（ray-casting cast shadow）——单像元几何"
              "判定，非可视域/投影阴影（诚实披露）。"
              "\n关键约束：range-only 几何简化（无轨道元数据）；cell_size 与 "
              "radar_range_azimuth 显式必需；入射角标量或 2D LUT。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "dem": "DEM 2D 数组（米；北朝上网格）",
              "cell_size": "DEM 像元大小（米，>0；必需）",
              "radar_range_azimuth": "雷达视线方位角（度 [0,360)，顺时针自北、"
                                     "地面指向传感器；必需）",
              "incidence_deg": "标量入射角（度，0-90 开区间；与 incidence_lut 互斥）",
              "incidence_lut": "可选逐像元入射角 LUT（2D 数组，与 dem 同形）",
              "dem_nodata": "DEM 哨兵值（可选）",
          })
    async def sar_layover_shadow_mask(
        dem: List[List[float]],
        cell_size: float,
        radar_range_azimuth: float,
        incidence_deg: Optional[float] = None,
        incidence_lut: Optional[List[List[float]]] = None,
        dem_nodata: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_v3 import layover_shadow_mask

        contract_params: Dict[str, Any] = {
            "cell_size": cell_size,
            "radar_range_azimuth": radar_range_azimuth,
        }
        if incidence_deg is not None:
            contract_params["incidence_deg"] = incidence_deg
        params = apply_contract("sar_layover_shadow_analysis", contract_params)
        dem_arr = _plane_from_json(dem, "dem")
        inc_map = None
        if incidence_lut is not None:
            inc_map = _plane_from_json(incidence_lut, "incidence_lut")
            if inc_map.shape != dem_arr.shape:
                raise ValueError(
                    f"incidence_lut 形状 {inc_map.shape} 与 dem "
                    f"{dem_arr.shape} 不一致")
        res = layover_shadow_mask(
            dem_arr, params["cell_size"], params["radar_range_azimuth"],
            incidence_deg=params.get("incidence_deg"), incidence_map=inc_map,
            dem_nodata=dem_nodata)
        payload = {
            "success": True,
            "cell_size": res["meta"]["cell_size"],
            "radar_range_azimuth": res["meta"]["radar_range_azimuth"],
            "incidence_mode": res["meta"]["incidence_mode"],
            "classes": res["meta"]["classes"],
            "fractions": res["fractions"],
            "mask": res["mask"].astype(int).tolist(),
            "convention": res["meta"]["convention"],
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "sar.layover_shadow", tool="sar_layover_shadow_mask",
            parameters_applied={
                "cell_size": params["cell_size"],
                "radar_range_azimuth": params["radar_range_azimuth"],
                "incidence_mode": res["meta"]["incidence_mode"],
                "fractions": res["fractions"],
            },
            input_facts={"feature_count": int(dem_arr.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "sar.layover_shadow", int(dem_arr.size))],
        )

    @tool(registry, name="sar_enl_map",
          description=(
              "滑窗 ENL（等效视数）估计图：ENL = mean²/var（nan 感知）+ "
              "全局 ENL。斑点模型诊断量（滤波/定标质检）。"
              "\n何时用：检查 SAR 切片视数/处理一致性（常数图=均匀处理）。"
              "\n关键约束：非均匀窗口把纹理计入方差 → ENL 被低估（估计偏差"
              "披露）；退化窗口（方差≈0）→ NaN（计数披露）；dB 输入被拒绝。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "intensity": "2D 线性强度数组（非负）",
              "window": "滑窗：3/5/7(默认)",
              "nodata_value": "可选标量哨兵值",
          })
    async def sar_enl_map(
        intensity: List[List[float]],
        window: str = "7",
        nodata_value: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_v3 import enl_map as _enl_map

        params = apply_contract("sar_enl_map_analysis", {"window": window})
        arr = _plane_from_json(intensity, "intensity")
        res = _enl_map(arr, window=int(params["window"]), nodata=nodata_value)
        finite = np.isfinite(res["enl_map"])
        payload = {
            "success": True,
            "window": res["meta"]["window"],
            "global_enl": round(res["global_enl"], 6),
            "degenerate_windows": res["meta"]["degenerate_windows"],
            "stats": {
                "min": float(np.nanmin(res["enl_map"])) if finite.any() else None,
                "max": float(np.nanmax(res["enl_map"])) if finite.any() else None,
                "median": (float(np.nanmedian(res["enl_map"]))
                           if finite.any() else None),
                "valid_pixels": int(np.sum(finite)),
                "total_pixels": int(res["enl_map"].size),
            },
            "enl_map": res["enl_map"].round(6).tolist(),
            "disclosure": res["meta"]["disclosure"],
        }
        return _attach_science_evidence(
            payload, "sar.enl_map", tool="sar_enl_map",
            parameters_applied={"window": res["meta"]["window"]},
            input_facts={"feature_count": int(arr.size)},
            warnings=[res["meta"]["disclosure"]],
            diagnostics=[_backend_selection_diagnostic(
                "sar.enl_map", int(arr.size))],
        )
