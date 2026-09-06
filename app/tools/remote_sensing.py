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
              "从 11 种公式族指数（ndvi/gndvi/savi/msavi/ndwi/mndwi/ndbi/ndmi/nbr/evi/evi2）"
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
              "index_id": "指数 id：ndvi/gndvi/savi/msavi/ndwi/mndwi/ndbi/ndmi/nbr/evi/evi2",
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
              "SAR 斑点噪声滤波（Lee 1980 / Refined-Lee 边缘方向 MMSE / Frost 1982）。"
              "输入须线性强度（非负；dB 输入被拒绝——先定标）；ENL 显式优先，"
              "缺省整图矩估计 ENL=mean²/var（均匀假设，enl_source 披露）。"
              "\n何时用：已对齐的单波段 SAR 强度切片的斑点抑制（时序统计/极值前）。"
              "\n关键约束：窗口 3/5/7；refined_lee 为 7 子窗方向 MMSE 近似"
              "（非 Lopes 1990 完整 MAP 变体）；网格 ≤4096×4096。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "array": "2D 线性强度数组（非负）",
              "filter": "滤波器：lee(默认)/refined_lee/frost",
              "window": "奇数窗口：3(默认)/5/7",
              "enl": "等效视数（>0；缺省整图矩估计并披露）",
              "damping": "Frost 阻尼 D（0.5-5，默认 1；仅 frost 使用）",
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
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.geo_analysis.sar_filter import speckle_filter

        contract_params: Dict[str, Any] = {
            "filter": filter, "window": window, "damping": damping}
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
            nodata=nodata_value)
        finite = np.isfinite(res["array"])
        payload = {
            "success": True,
            "method": res["method"],
            "window": res["window"],
            "enl": round(res["enl"], 6),
            "enl_source": res["enl_source"],
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
              "诚实边界：逐像元定标 LUT 未实现（仅常数定标）；热噪声去除未实现。"
              "\n何时用：已有定标常数（如 Sentinel-1 A²/AUT）与入射角的强度切片定标。"
              "\n关键约束：calibration_constant 必需（缺失拒绝，绝不虚构）；"
              "入射角 (0,90) 开区间（度）；负 DN 被拒绝。"
          ),
          tier=2, domains=["raster"],
          param_descriptions={
              "array": "2D DN/强度数组（非负）",
              "calibration_constant": "定标常数 K（必需，如 Sentinel-1 A²/AUT）",
              "incidence_deg": "标量入射角（度，0-90 开区间；σ⁰/γ⁰ 必需）",
              "incidence_map": "可选逐像元入射角平面（与 array 同形；与 incidence_deg 互斥）",
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
        inc_map = None
        if incidence_map is not None:
            inc_map = np.asarray(incidence_map, dtype=float)
            if inc_map.size > _TOOL_ARRAY_MAX_VALUES:
                raise ValueError("incidence_map 超过内联数组工具上界")
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
