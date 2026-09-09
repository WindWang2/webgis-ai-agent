"""高级空间分析工具 (FC)"""
import logging
from typing import Any, List, Optional

import numpy as np
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools._utils import cached_tool, trim_features
from app.services.spatial_analyzer import SpatialAnalyzer
from app.lib.geo_processor.core import safe_parse as safe_parse_geojson

logger = logging.getLogger(__name__)


class ZonalStatsArgs(BaseModel):
    geojson: Any = Field(..., description="矢量区域要素 (GeoJSON 或 ref:xxx)")
    raster_path: str = Field(..., description="栅格数据路径或标识")

class OverlayAnalysisArgs(BaseModel):
    layer_a: Any = Field(..., description="图层 A (GeoJSON 或 ref:xxx)")
    layer_b: Any = Field(..., description="图层 B (GeoJSON 或 ref:xxx)")
    how: str = Field("intersection", description="叠加方式: intersection(交集), union(并集), identity(标识), symmetric_difference(对称差异), difference(差异/擦除)")

class AttributeFilterArgs(BaseModel):
    geojson: Any = Field(..., description="输入数据 (GeoJSON 或 ref:xxx)")
    query: str = Field(..., description="Pandas 风格的查询字符串，例如: 'pop > 1000' 或 'type == \"park\"'")

class SpatialJoinArgs(BaseModel):
    left_layer: Any = Field(..., description="左图层 (GeoJSON 或 ref:xxx)")
    right_layer: Any = Field(..., description="右图层 (GeoJSON 或 ref:xxx)")
    join_type: str = Field("inner", description="连接类型: inner, left, right")
    predicate: str = Field("intersects", description="空间谓词: intersects, within, contains, touches, crosses")

class IsochroneAnalysisArgs(BaseModel):
    network_layer: Any = Field(..., description="路网数据 (GeoJSON 或 ref:xxx)")
    facilities: Any = Field(..., description="设施点 (GeoJSON 或 ref:xxx)")
    travel_time: float = Field(15, description="行驶/步行时间（单位由路网权重决定，通常为分钟或米）")
    mode: str = Field("walking", description="出行模式: walking, driving, cycling")

class FishnetGridArgs(BaseModel):
    bounds: List[float] = Field(..., description="网格范围 [xmin, ymin, xmax, ymax]")
    cell_size: float = Field(..., description="网格大小（米）")
    type: str = Field("square", description="网格类型: square(正方形), hexagon(六边形)")

class RasterReclassifyArgs(BaseModel):
    raster_path: str = Field(..., description="输入栅格文件路径（data/ 内）")
    scheme: List[dict] = Field(..., description="重分类方案，每项含 min/max/value/label，如 [{\"min\":0,\"max\":0.2,\"value\":1,\"label\":\"低\"},...]")
    nodata: Optional[float] = Field(None, description="输出 NoData 值（默认继承输入栅格）")

class RasterCalculatorArgs(BaseModel):
    raster_a: str = Field(..., description="主栅格文件路径（data/ 内）")
    raster_b: Optional[str] = Field(None, description="副栅格文件路径（data/ 内）；留空则用 constant")
    expression: str = Field("A + B", description="运算表达式，用 A/B 指代栅格，如 A+B, (A-B)/(A+B), where(A>0,A,0)")
    constant: Optional[float] = Field(None, description="当 raster_b 留空时的常数")
    nodata: Optional[float] = Field(None, description="输出 NoData 值")
    resampling: Optional[str] = Field(None, description="B 对齐到 A 的重采样方法（bilinear 默认；分类栅格必须 nearest）")

class RasterResampleArgs(BaseModel):
    raster_path: str = Field(..., description="输入栅格文件路径（data/ 内）")
    target_resolution: float = Field(..., description="目标像元大小（米或度，取决于 CRS）")
    target_crs: Optional[str] = Field(None, description="目标 CRS（如 EPSG:3857），留空则保持原 CRS")
    resampling: str = Field("bilinear", description="重采样方法: bilinear(默认), nearest, cubic, mode, average")

def register_advanced_spatial_tools(registry: ToolRegistry):
    """注册高级空间分析工具"""

    @tool(registry, name="zonal_stats",
           description=(
               "区域栅格统计：对每个矢量多边形，统计落入其内的栅格像素 (min/max/mean/sum/count) 并回写到 properties。"
               "\n何时用：『每个区县的平均 NDVI / 平均 DEM 高程 / 累积降雨量』；"
               "用 NDVI 或高程图层给区县着色；遥感产物 (compute_ndvi/fetch_dem 的输出) 接入到行政统计。"
               "\n何时不用：(1) 仅做点的栅格采样 — 直接读栅格即可；"
               "(2) 矢量内的矢量统计 (区内 POI 数) — 用 spatial_aggregate；"
               "(3) 没有现成栅格 — 先 fetch_dem / compute_ndvi 再 zonal_stats。"
               "\n关键约束：zones 是 FeatureCollection (面)；raster_path 必须是后端可访问的本地路径或 ref。"
           ),
           tier=2, domains=["raster"],
           args_model=ZonalStatsArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("区域统计", "zonal", "栅格统计", "ndvi", "dem", "分区"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def zonal_stats(geojson: Any, raster_path: str) -> dict:
        data = safe_parse_geojson(geojson)
        # GIS-682: forward the whole FeatureCollection so a declared `crs`
        # member survives the tool boundary — previously only the features
        # list was forwarded and the FC-level crs was silently dropped
        # (mirrors the pre-#599 clip/overlay half-stack).
        res = SpatialAnalyzer.zonal_stats(data, raster_path)
        return res.to_llm_response()

    @tool(registry, name="idw_interpolation",
           description="反距离加权插值(IDW)：将离散采样点转换为连续的 H3 六边形网格表面。适用于气象、污染等连续变量建模。默认附 LOOCV 交叉验证指标与残差分位数证据（无理论方差，不确定性全部为经验残差）。",
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)",
               "value_field": "用于插值的数值字段名",
               "resolution": "H3 分辨率（6-9），默认 8",
               "power": "距离权重幂次，默认 2（0 < power ≤ 5）",
               "cross_validate": "是否计算 LOOCV 验证指标（默认 true，附加 validation/uncertainty/scientific_evidence 证据块）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("插值", "idw", "反距离加权", "表面", "连续面"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def idw_interpolation(geojson: Any, value_field: str, resolution: int = 8, power: int = 2, cross_validate: bool = True) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            ScalarUncertainty,
            UncertaintyMeasure,
            ValidationMetrics,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.interpolation import idw_surface as _idw_surface

        data = safe_parse_geojson(geojson)
        driver = _idw_surface(
            data, value_field, resolution=resolution, power=power,
            cross_validate=cross_validate,
        )
        meta = driver["metadata"]

        # Convert H3 results to GeoJSON Features (审计：消除重复的 H3-to-GeoJSON 转换)
        geojson_result = h3_to_geojson(driver["records"], value_field)
        geojson_result["summary"] = f"Generated IDW interpolation surface with {len(geojson_result['features'])} H3 cells (res={resolution})."
        geojson_result["idw_metadata"] = meta
        # VNext additive evidence blocks (LOOCV residual evidence — IDW never
        # claims a theoretical variance).
        val_metrics = None
        unc_blocks: list = []
        if meta.get("validation") is not None:
            geojson_result["validation"] = meta["validation"]
            geojson_result["uncertainty"] = meta["uncertainty"]
            val_metrics = ValidationMetrics(
                target="idw_surface",
                method="loocv",
                rmse=meta["validation"]["rmse"],
                mae=meta["validation"]["mae"],
                bias=meta["validation"]["bias"],
                sample_count=meta["validation"]["sample_count"],
            )
            quant = meta["uncertainty"]["quantiles"]
            unc_blocks = [ScalarUncertainty(
                target="idw_surface",
                measures=[
                    UncertaintyMeasure(
                        measure="quantile", value=quant["p50"],
                        method="loocv_residual_quantiles p50 (|residual|)",
                    ),
                    UncertaintyMeasure(
                        measure="quantile", value=quant["p90"],
                        method="loocv_residual_quantiles p90 (|residual|)",
                    ),
                ],
            )]
        descriptor = get_algorithm_registry().get("interpolation.idw")
        if descriptor is not None:
            geojson_result["scientific_evidence"] = build_evidence(
                descriptor,
                tool="idw_interpolation",
                parameters_applied={
                    "value_field": value_field,
                    "resolution": int(resolution),
                    "power": float(power),
                    "cross_validate": bool(cross_validate),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) before distance math",
                ],
                validation=val_metrics,
                uncertainty=unc_blocks,
            )
        return geojson_result

    @tool(registry, name="kriging_interpolation",
           description=(
               "克里金插值：普通克里金(method=ordinary, 默认)或泛克里金(method=universal, 线性漂移+残差变异函数)。"
               "拟合变异函数(spherical/exponential/gaussian)后在 H3 网格上同时产出预测面与克里金方差(不确定性)面，"
               "附 K 折交叉验证指标(RMSE/MAE/bias/R²)。适用于需要不确定性量化的连续变量建模(地统计)。"
               "\n何时用：『用克里金插值』/ 需要置信度或误差棒的表面 / 样本≥8 且空间相关；"
               "趋势明显(如沿海拔线性变化)时选 method=universal（样本≥12）或有辅助变量时 method=external_drift（KED，漂移字段目标处经 IDW 近似）；已知先验均值时 method=simple（SK）。"
               "\n何时不用：样本<8 或只求快速表面 — 用 idw_interpolation。"
               "\n失败语义：变异函数拟合失败时抛结构化错误(建议改用 IDW)，不静默降级。"
               "\nV2 可选项：anisotropy_angle/ratio(几何各向异性,默认各向同性)、cv_scheme="
               "spatial_block(空间分块交叉验证)、solve_backend(线性求解后端,默认 auto)。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何）",
               "value_field": "用于插值的数值字段名",
               "resolution": "H3 分辨率（5-9），默认 7",
               "variogram_model": "变异函数模型: auto(默认)/spherical/exponential/gaussian",
               "neighbors": "每个预测点的克里金邻域样本数(2-24)，默认 12",
               "cross_validate": "是否运行 K 折交叉验证，默认 true",
               "declared_crs": (
                   "声明的输入 CRS: EPSG:4326(默认)/EPSG:4490/EPSG:3857/UTM(EPSG:326xx|327xx)。"
                   "距离计算在投影坐标系执行；不支持的 CRS 结构化报错，绝不静默按 WGS84。"
               ),
               "method": "克里金方法: ordinary(默认)/universal（泛克里金需样本≥12）",
               "anisotropy_angle": "各向异性主轴方位角（度，默认 0=各向同性）",
               "anisotropy_ratio": "各向异性长短轴变程比（≥1，默认 1=各向同性）",
               "cv_scheme": "CV 分折方案: index(默认,索引取模)/spatial_block(确定性网格分块)",
               "solve_backend": "线性求解后端: auto(默认,批式numpy+逐行回退)/numpy_batched/scipy_linalg",
               "mean": "SK 先验均值（method=simple；缺省=样本均值估计并披露）",
               "drift_field": "KED 辅助漂移变量字段名（method=external_drift 必需；目标处 IDW 近似）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="medium",
           tags=("克里金", "kriging", "地统计", "插值", "方差", "变异函数"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def kriging_interpolation(
        geojson: Any,
        value_field: str,
        resolution: int = 7,
        variogram_model: str = "auto",
        neighbors: int = 12,
        cross_validate: bool = True,
        declared_crs: Optional[str] = None,
        method: str = "ordinary",
        anisotropy_angle: float = 0.0,
        anisotropy_ratio: float = 1.0,
        cv_scheme: str = "index",
        solve_backend: str = "auto",
        mean: Optional[float] = None,
        drift_field: Optional[str] = None,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.backend_selection import ScaleProfile, select_backend
        from app.lib.gis.crs_safety import classify_crs
        from app.lib.gis.scientific_evidence import Diagnostic, build_evidence
        from app.lib.gis.uncertainty import (
            RasterUncertainty,
            UncertaintyMeasure,
            ValidationMetrics,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.kriging import (
            KrigingCrsError,
            kriging_interpolation as _kriging,
        )

        data = safe_parse_geojson(geojson)
        # Declared CRS precedence: explicit argument > FC-level crs member
        # (#1110 discipline — an UNRECOGNIZED crs member is a structured
        # rejection, never a silent WGS84 fallback that misreads projected
        # metres as degrees).
        fc_crs = None
        if isinstance(data, dict):
            crs_member = data.get("crs")
            if isinstance(crs_member, dict):
                name = str((crs_member.get("properties") or {}).get("name", ""))
                if "4490" in name:
                    fc_crs = "EPSG:4490"
                elif "3857" in name:
                    fc_crs = "EPSG:3857"
                elif "4326" in name or "WGS84" in name.upper():
                    fc_crs = "EPSG:4326"
                elif name:
                    raise KrigingCrsError(name)
        declared = declared_crs or fc_crs
        # A7 backend selection: deterministic ScaleProfile → variant decision,
        # recorded into the evidence diagnostics. An explicit non-auto
        # solve_backend argument wins; otherwise the selected variant is
        # passed through (scipy_linalg window → forced LAPACK path).
        n_points = 0
        if isinstance(data, dict):
            for f in data.get("features", []) or []:
                props = (f.get("properties") or {}) if isinstance(f, dict) else {}
                if isinstance(props, dict) and value_field in props:
                    n_points += 1
        decision = select_backend(
            "interpolation.kriging", ScaleProfile(feature_count=n_points or None)
        )
        if solve_backend != "auto":
            effective_backend = solve_backend
        elif decision.variant_id == "scipy_linalg":
            effective_backend = "scipy_linalg"
        else:
            effective_backend = "auto"
        driver = _kriging(
            data, value_field, resolution=resolution,
            variogram_model=variogram_model, neighbors=neighbors,
            cross_validate=cross_validate,
            declared_crs=declared,
            method=method,
            anisotropy_angle=anisotropy_angle,
            anisotropy_ratio=anisotropy_ratio,
            cv_scheme=cv_scheme,
            solve_backend=effective_backend,
            mean=mean,
            drift_field=drift_field,
        )
        meta = driver["metadata"]
        meta["backend_selection"] = {
            "variant_id": decision.variant_id or "default",
            "matched": decision.matched,
            "scale_tier": decision.scale_tier,
            "rationale": decision.rationale,
        }

        # Prediction surface: H3 cells carrying BOTH the interpolated value
        # and the per-cell kriging stddev (first-class properties, not prose).
        pred_records = [
            {"h3_index": r["h3_index"], "value": r["value"]} for r in driver["records"]
        ]
        prediction_fc = h3_to_geojson(pred_records, value_field)
        for feat, rec in zip(prediction_fc["features"], driver["records"]):
            feat["properties"]["kriging_variance"] = round(rec["kriging_variance"], 6)
            feat["properties"]["kriging_stddev"] = round(rec["kriging_stddev"], 6)

        # Uncertainty surface: same cells, stddev as the primary value —
        # a second, independently consumable artifact.
        uncertainty_records = [
            {"h3_index": r["h3_index"], "value": r["kriging_stddev"]}
            for r in driver["records"]
        ]
        uncertainty_fc = h3_to_geojson(uncertainty_records, "kriging_stddev")

        cv = meta["cross_validation"]
        cv_text = ""
        if cv:
            if cv.get("rmse") is not None:
                cv_text = (
                    f"；交叉验证({cv['folds']}折): RMSE={cv['rmse']:.4f} "
                    f"MAE={cv['mae']:.4f} bias={cv['bias']:.4f}"
                    + (f" R²={cv['r2']:.4f}" if cv.get("r2") is not None else "")
                )
            elif cv.get("note"):
                cv_text = f"；交叉验证: {cv['note']}"

        vario = meta.get("variogram")
        if vario:
            vario_text = (
                f"(res={resolution}, 变异函数={vario['model']}, "
                f"sill={vario['sill']}, range={vario['range_meters']}m)"
            )
        else:
            # UK zero-residual degenerate case: honest disclosure, no fake variogram
            vario_text = f"(res={resolution}, 零残差退化：数据严格线性趋势，方差=0)"

        prediction_fc.update({
            "summary": (
                f"克里金插值完成：{len(prediction_fc['features'])} 个 H3 单元"
                f"{vario_text}；"
                f"不确定面(克里金标准差)已随本结果输出。{cv_text}"
            ),
            "uncertainty": uncertainty_fc,
            "kriging_metadata": meta,
        })

        # VNext scientific evidence (descriptor = assumptions/limitations source)
        descriptor = get_algorithm_registry().get(
            "interpolation.universal_kriging"
            if method == "universal" else "interpolation.kriging"
        )
        if descriptor is not None:
            validation = None
            if cv and cv.get("rmse") is not None:
                validation = ValidationMetrics(
                    target="kriging_surface",
                    method="k_fold",
                    rmse=cv.get("rmse"),
                    mae=cv.get("mae"),
                    bias=cv.get("bias"),
                    r_squared=cv.get("r2"),
                    folds=cv.get("folds"),
                    sample_count=cv.get("n_samples"),
                )
            variance_range = meta.get("variance_range") or [None, None]
            declared_eff = meta.get("declared_crs") or "EPSG:4326"
            transformations = []
            if classify_crs(declared_eff) == "geographic":
                transformations.append(
                    f"reprojected {declared_eff} -> {meta['working_crs']} for metric kriging"
                )
            prediction_fc["scientific_evidence"] = build_evidence(
                descriptor,
                tool="kriging_interpolation",
                parameters_applied={
                    "value_field": value_field,
                    "resolution": int(resolution),
                    "variogram_model": variogram_model,
                    "neighbors": int(neighbors),
                    "cross_validate": bool(cross_validate),
                    "method": method,
                    "anisotropy_angle": float(anisotropy_angle),
                    "anisotropy_ratio": float(anisotropy_ratio),
                    "cv_scheme": cv_scheme,
                    "solve_backend": effective_backend,
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": declared_eff,
                    "units": "m",
                },
                transformations=transformations,
                diagnostics=[Diagnostic(
                    name=decision.to_diagnostic()["name"],
                    text=decision.to_diagnostic()["text"],
                )],
                validation=validation,
                uncertainty=[RasterUncertainty(
                    target="kriging_variance",
                    interpretation="kriging prediction variance (working CRS units squared)",
                    summary=[UncertaintyMeasure(
                        measure="value", value=variance_range[1],
                        method="max kriging variance",
                    )],
                )],
            )
            prediction_fc["scientific_evidence"]["solve_backend_used"] = meta.get(
                "solve_backend_used"
            )
        return prediction_fc

    @tool(registry, name="rbf_interpolation",
           description=(
               "径向基函数(RBF)插值：scipy RBFInterpolator（thin_plate_spline 默认 / "
               "linear / cubic / quintic），在 H3 网格上生成平滑插值面，"
               "附 LOOCV 验证指标与残差分位数证据。smoothing=0 时为精确插值器（过样本点）。"
               "\n何时用：样本较规则、需要光滑表面（气温/地形类连续场）；比 IDW 更平滑。"
               "\n何时不用：样本<3；样本>10 万（先确定性抽稀）；需要克里金方差 — 用 "
               "kriging_interpolation。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何）",
               "value_field": "用于插值的数值字段名",
               "resolution": "H3 分辨率（5-9），默认 7",
               "kernel": "RBF 核: thin_plate_spline(默认)/linear/cubic/quintic",
               "smoothing": "平滑系数 0-10，默认 0（0=精确过样本点）",
               "neighbors": "局部 RBF 邻域样本数 1-64，默认 32",
               "cross_validate": "是否计算 LOOCV 验证指标，默认 true",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="medium",
           tags=("插值", "rbf", "径向基", "平滑", "表面"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def rbf_interpolation(
        geojson: Any,
        value_field: str,
        resolution: int = 7,
        kernel: str = "thin_plate_spline",
        smoothing: float = 0.0,
        neighbors: int = 32,
        cross_validate: bool = True,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            ScalarUncertainty,
            UncertaintyMeasure,
            ValidationMetrics,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.rbf_interpolation import (
            rbf_interpolation as _rbf,
        )

        params = apply_contract("rbf_interpolation", {
            "value_field": value_field,
            "kernel": kernel,
            "smoothing": smoothing,
            "neighbors": neighbors,
            "resolution": resolution,
        })
        data = safe_parse_geojson(geojson)
        driver = _rbf(
            data, params["value_field"],
            resolution=int(params["resolution"]),
            kernel=params["kernel"],
            smoothing=float(params["smoothing"]),
            neighbors=int(params["neighbors"]),
            cross_validate=cross_validate,
        )
        meta = driver["metadata"]

        geojson_result = h3_to_geojson(driver["records"], value_field)
        geojson_result["summary"] = (
            f"RBF 插值完成：{len(geojson_result['features'])} 个 H3 单元"
            f"(res={params['resolution']}, kernel={meta['kernel']}, "
            f"smoothing={meta['smoothing']}, neighbors={meta['neighbors']})。"
        )
        geojson_result["rbf_metadata"] = meta
        val_metrics = None
        unc_blocks: list = []
        if meta.get("validation") is not None:
            geojson_result["validation"] = meta["validation"]
            geojson_result["uncertainty"] = meta["uncertainty"]
            val_metrics = ValidationMetrics(
                target="rbf_surface",
                method="loocv",
                rmse=meta["validation"]["rmse"],
                mae=meta["validation"]["mae"],
                bias=meta["validation"]["bias"],
                sample_count=meta["validation"]["sample_count"],
            )
            quant = meta["uncertainty"]["quantiles"]
            unc_blocks = [ScalarUncertainty(
                target="rbf_surface",
                measures=[
                    UncertaintyMeasure(
                        measure="quantile", value=quant["p50"],
                        method="loocv_residual_quantiles p50 (|residual|)",
                    ),
                    UncertaintyMeasure(
                        measure="quantile", value=quant["p90"],
                        method="loocv_residual_quantiles p90 (|residual|)",
                    ),
                ],
            )]
        descriptor = get_algorithm_registry().get("interpolation.rbf")
        if descriptor is not None:
            geojson_result["scientific_evidence"] = build_evidence(
                descriptor,
                tool="rbf_interpolation",
                parameters_applied={
                    "value_field": params["value_field"],
                    "resolution": int(params["resolution"]),
                    "kernel": params["kernel"],
                    "smoothing": float(params["smoothing"]),
                    "neighbors": int(params["neighbors"]),
                    "cross_validate": bool(cross_validate),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) before distance math",
                ],
                validation=val_metrics,
                uncertainty=unc_blocks,
            )
        return geojson_result

    @tool(registry, name="tin_interpolation",
           description=(
               "TIN 三角网插值：Delaunay 三角剖分上的 linear(C⁰ 重心插值, 默认)或 "
               "clough_tocher(C¹ 三次)方法，在 H3 网格上生成插值面，附 LOOCV 验证证据。"
               "凸包外诚实空缺（不外推——凸包外的格网无值，缺失计数进 metadata）。"
               "\n何时用：样本构成不规则三角网（地形/测量点）、需要过样本点的精确插值；"
               "平滑连续场可试 method=clough_tocher。"
               "\n何时不用：样本<3 或共线；需要凸包外覆盖（TIN 不外推）— 用 idw_interpolation / trend_surface；"
               "需要克里金方差 — 用 kriging_interpolation。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥3 个非共线点）",
               "value_field": "用于插值的数值字段名",
               "resolution": "H3 分辨率（5-9），默认 7",
               "method": "三角网插值方法: linear(默认,C⁰)/clough_tocher(C¹ 三次)",
               "cross_validate": "是否计算 LOOCV 验证指标（有界 500 点），默认 true",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="medium",
           tags=("tin", "三角网", "delaunay", "插值", "地形"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def tin_interpolation(
        geojson: Any,
        value_field: str,
        resolution: int = 7,
        method: str = "linear",
        cross_validate: bool = True,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            ScalarUncertainty,
            UncertaintyMeasure,
            ValidationMetrics,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.tin_interpolation import tin_surface as _tin

        params = apply_contract("tin_interpolation", {
            "value_field": value_field,
            "method": method,
            "resolution": resolution,
        })
        data = safe_parse_geojson(geojson)
        driver = _tin(
            data, params["value_field"],
            resolution=int(params["resolution"]),
            method=params["method"],
            cross_validate=cross_validate,
        )
        meta = driver["metadata"]

        geojson_result = h3_to_geojson(driver["records"], value_field)
        geojson_result["summary"] = (
            f"TIN 插值完成：{len(geojson_result['features'])} 个 H3 单元"
            f"(res={params['resolution']}, method={meta['method']}, "
            f"三角形数={meta['triangle_count']}, 凸包覆盖率={meta['fill_fraction']})。"
            "凸包外格网不外推（无值）。"
        )
        geojson_result["tin_metadata"] = meta
        val_metrics = None
        unc_blocks: list = []
        if meta.get("validation") is not None:
            geojson_result["validation"] = meta["validation"]
            geojson_result["uncertainty"] = meta["uncertainty"]
            val_metrics = ValidationMetrics(
                target="tin_surface",
                method="loocv",
                rmse=meta["validation"]["rmse"],
                mae=meta["validation"]["mae"],
                bias=meta["validation"]["bias"],
                sample_count=meta["validation"]["sample_count"],
            )
            quant = meta["uncertainty"]["quantiles"]
            unc_blocks = [ScalarUncertainty(
                target="tin_surface",
                measures=[
                    UncertaintyMeasure(
                        measure="quantile", value=quant["p50"],
                        method="loocv_residual_quantiles p50 (|residual|)",
                    ),
                    UncertaintyMeasure(
                        measure="quantile", value=quant["p90"],
                        method="loocv_residual_quantiles p90 (|residual|)",
                    ),
                ],
            )]
        descriptor = get_algorithm_registry().get("interpolation.tin")
        if descriptor is not None:
            geojson_result["scientific_evidence"] = build_evidence(
                descriptor,
                tool="tin_interpolation",
                parameters_applied={
                    "value_field": params["value_field"],
                    "resolution": int(params["resolution"]),
                    "method": params["method"],
                    "cross_validate": bool(cross_validate),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) before triangulation",
                ],
                validation=val_metrics,
                uncertainty=unc_blocks,
            )
        return geojson_result

    @tool(registry, name="trend_surface",
           description=(
               "趋势面分析：全局多项式（阶数 1-3）OLS 拟合，在 H3 网格上输出趋势面。"
               "附 R²/调整 R²/残差方差（有效模型方差证据）与 LOOCV 验证指标；"
               "样本 bbox 外的格网照常输出但逐格标记 extrapolated=true（趋势模型本就全局外推）。"
               "\n何时用：只关心大尺度空间趋势（如整体升温梯度/城市化梯度）、快速平滑背景面。"
               "\n何时不用：需要局地细节 — 用 kriging_interpolation / idw_interpolation；"
               "样本太少（order=1 至少 6 点）或坐标零跨度时结构化报错。"
           ),
           tier=2, domains=["statistics"], cost="medium",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何）",
               "value_field": "用于拟合的数值字段名",
               "resolution": "H3 分辨率（5-9），默认 7",
               "order": "多项式阶数: 1(平面,默认)/2(二次)/3(三次)；阶数越高越易振荡(Runge)",
               "cross_validate": "是否计算 LOOCV 验证指标（有界 500 点），默认 true",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("趋势面", "多项式", "ols", "梯度", "trend"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def trend_surface(
        geojson: Any,
        value_field: str,
        resolution: int = 7,
        order: int = 1,
        cross_validate: bool = True,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            ScalarUncertainty,
            UncertaintyMeasure,
            ValidationMetrics,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.trend_surface import trend_surface as _trend

        params = apply_contract("trend_surface_analysis", {
            "value_field": value_field,
            "order": str(order),
            "resolution": resolution,
        })
        data = safe_parse_geojson(geojson)
        driver = _trend(
            data, params["value_field"],
            resolution=int(params["resolution"]),
            order=int(params["order"]),
            cross_validate=cross_validate,
        )
        meta = driver["metadata"]

        pred_records = [
            {"h3_index": r["h3_index"], "value": r["value"]} for r in driver["records"]
        ]
        geojson_result = h3_to_geojson(pred_records, value_field)
        for feat, rec in zip(geojson_result["features"], driver["records"]):
            if rec.get("extrapolated"):
                feat["properties"]["extrapolated"] = True
        geojson_result["summary"] = (
            f"趋势面分析完成：order={meta['order']}，{len(driver['records'])} 个 H3 单元，"
            f"R²={meta['r2']}，残差方差={meta['residual_variance']}；"
            f"外推格网 {meta['extrapolated_count']} 个（已逐格标记）。"
        )
        geojson_result["trend_metadata"] = meta
        val_metrics = None
        if meta.get("validation") is not None:
            geojson_result["validation"] = meta["validation"]
            val_metrics = ValidationMetrics(
                target="trend_surface",
                method="loocv",
                rmse=meta["validation"]["rmse"],
                mae=meta["validation"]["mae"],
                bias=meta["validation"]["bias"],
                sample_count=meta["validation"]["sample_count"],
            )
        descriptor = get_algorithm_registry().get("interpolation.trend_surface")
        if descriptor is not None:
            unc_blocks = [ScalarUncertainty(
                target="trend_surface",
                measures=[UncertaintyMeasure(
                    measure="variance", value=meta.get("residual_variance"),
                    method="OLS residual variance SS_res/(n-p)",
                )],
            )]
            geojson_result["scientific_evidence"] = build_evidence(
                descriptor,
                tool="trend_surface",
                parameters_applied={
                    "value_field": params["value_field"],
                    "resolution": int(params["resolution"]),
                    "order": int(params["order"]),
                    "cross_validate": bool(cross_validate),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar)",
                    "coordinates affine-scaled to the unit box for OLS conditioning",
                ],
                validation=val_metrics,
                uncertainty=unc_blocks,
            )
        return geojson_result

    @tool(registry, name="regression_kriging",
           description=(
               "回归克里金：OLS 趋势（z ~ 协变量字段）+ 残差普通克里金（Odeh 1995），"
               "在 H3 网格上同时输出 rk_prediction 预测面与 rk_variance 残差克里金方差面，"
               "附全流程 LOOCV 证据。注意：目标处协变量值由样本协变量 IDW 近似"
               "（approximate 语义）；rk_variance 不含趋势系数不确定性（如实披露）。"
               "\n何时用：主变量与协变量（如高程/距水距离）强相关、且协变量更易获取时；"
               "样本≥8、至少 2 个协变量字段、协变量非常量。"
               "\n何时不用：无协变量字段 — 用 kriging_interpolation；协变量常量场会结构化报错。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，属性需含主字段与全部协变量字段）",
               "value_field": "主变量数值字段名",
               "explanatory_fields": "协变量字段名列表（≥2 个，逗号分隔），同一 FC 的属性",
               "resolution": "H3 分辨率（5-9），默认 7",
               "variogram_model": "残差变异函数模型: auto(默认)/spherical/exponential/gaussian/matern",
               "neighbors": "残差克里金邻域样本数(2-24)，默认 12",
               "cross_validate": "是否计算全流程 LOOCV（有界 200 点），默认 true",
               "declared_crs": (
                   "声明的输入 CRS: EPSG:4326(默认)/EPSG:4490/EPSG:3857/UTM(EPSG:326xx|327xx)。"
                   "不支持的 CRS 结构化报错。"
               ),
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="medium",
           tags=("回归克里金", "regression kriging", "协变量", "残差克里金", "插值"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def regression_kriging(
        geojson: Any,
        value_field: str,
        explanatory_fields: str,
        resolution: int = 7,
        variogram_model: str = "auto",
        neighbors: int = 12,
        cross_validate: bool = True,
        declared_crs: Optional[str] = None,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            RasterUncertainty,
            UncertaintyMeasure,
            ValidationMetrics,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.kriging import KrigingCrsError
        from app.lib.geo_analysis.regression_kriging import (
            regression_kriging_surface as _rk,
        )

        params = apply_contract("regression_kriging_analysis", {
            "value_field": value_field,
            "explanatory_fields": explanatory_fields,
            "resolution": resolution,
            "variogram_model": variogram_model,
            "neighbors": neighbors,
        })
        fields = [f.strip() for f in str(params["explanatory_fields"]).split(",") if f.strip()]
        data = safe_parse_geojson(geojson)
        fc_crs = None
        if isinstance(data, dict):
            crs_member = data.get("crs")
            if isinstance(crs_member, dict):
                name = str((crs_member.get("properties") or {}).get("name", ""))
                if "4490" in name:
                    fc_crs = "EPSG:4490"
                elif "3857" in name:
                    fc_crs = "EPSG:3857"
                elif "4326" in name or "WGS84" in name.upper():
                    fc_crs = "EPSG:4326"
                elif name:
                    raise KrigingCrsError(name)
        declared = declared_crs or fc_crs
        driver = _rk(
            data, params["value_field"], fields,
            resolution=int(params["resolution"]),
            variogram_model=params["variogram_model"],
            neighbors=int(params["neighbors"]),
            cross_validate=cross_validate,
            declared_crs=declared,
        )
        meta = driver["metadata"]

        # prediction surface: H3 cells carrying rk_prediction + rk_variance
        pred_records = [
            {"h3_index": r["h3_index"], "value": r["rk_prediction"]}
            for r in driver["records"]
        ]
        prediction_fc = h3_to_geojson(pred_records, params["value_field"])
        for feat, rec in zip(prediction_fc["features"], driver["records"]):
            feat["properties"]["rk_variance"] = round(rec["rk_variance"], 6)
            feat["properties"]["rk_stddev"] = round(rec["rk_stddev"], 6)
        uncertainty_records = [
            {"h3_index": r["h3_index"], "value": r["rk_stddev"]}
            for r in driver["records"]
        ]
        uncertainty_fc = h3_to_geojson(uncertainty_records, "rk_stddev")

        val = meta.get("validation")
        val_text = (
            f"；LOOCV({val['sample_count']}点): RMSE={val['rmse']:.4f} MAE={val['mae']:.4f}"
            if val else ""
        )
        prediction_fc.update({
            "summary": (
                f"回归克里金完成：{len(prediction_fc['features'])} 个 H3 单元"
                f"(协变量={fields}, 趋势系数={meta['trend_coefficients']['values']})；"
                f"rk_variance 仅含残差克里金方差。{val_text}"
            ),
            "uncertainty": uncertainty_fc,
            "rk_metadata": meta,
        })

        descriptor = get_algorithm_registry().get("interpolation.regression_kriging")
        if descriptor is not None:
            validation = None
            if val:
                validation = ValidationMetrics(
                    target="regression_kriging_surface",
                    method="loocv",
                    rmse=val.get("rmse"),
                    mae=val.get("mae"),
                    bias=val.get("bias"),
                    sample_count=val.get("sample_count"),
                )
            variance_range = meta.get("variance_range") or [None, None]
            prediction_fc["scientific_evidence"] = build_evidence(
                descriptor,
                tool="regression_kriging",
                parameters_applied={
                    "value_field": params["value_field"],
                    "explanatory_fields": fields,
                    "resolution": int(params["resolution"]),
                    "variogram_model": params["variogram_model"],
                    "neighbors": int(params["neighbors"]),
                    "cross_validate": bool(cross_validate),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": meta.get("declared_crs") or "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "target covariates approximated by per-field IDW (k=5, power=2)",
                ],
                validation=validation,
                uncertainty=[RasterUncertainty(
                    target="rk_variance",
                    interpretation=(
                        "residual kriging variance ONLY — trend-coefficient "
                        "uncertainty not propagated (disclosed)"
                    ),
                    summary=[UncertaintyMeasure(
                        measure="value", value=variance_range[1],
                        method="max residual kriging variance",
                    )],
                )],
            )
        return prediction_fc

    @tool(registry, name="interpolation_model_compare",
           description=(
               "插值模型比较：对同一份点要素，运行 idw / tin / trend_surface / rbf / "
               "ordinary_kriging 各方法的 LOOCV/CV 证据，按 RMSE 排名并确定性推荐最优方法。"
               "样本不足的方法逐行披露跳过原因；CV 预算按固定方法序走查（超出跳过并披露）。"
               "\n何时用：不确定选哪种插值方法时——先用本工具比较证据，再调用对应插值工具出表面。"
               "\n何时不用：已确定方法；或样本 <2（无任何方法可验证）。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何）",
               "value_field": "用于比较的数值字段名",
               "cv_budget": "总 CV 残差评估预算（默认 2500；超出按固定方法序跳过并披露）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="medium",
           tags=("插值", "模型比较", "loocv", "rmse", "选型"),
           output_semantic_type="table",
           result_size_policy="bounded",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def interpolation_model_compare(
        geojson: Any,
        value_field: str,
        cv_budget: int = 2500,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import ValidationMetrics
        from app.lib.geo_analysis.interpolation_compare import (
            compare_interpolation_models as _compare,
        )

        params = apply_contract("interpolation_model_compare", {
            "value_field": value_field,
            "cv_budget": cv_budget,
        })
        data = safe_parse_geojson(geojson)
        result = _compare(data, params["value_field"], cv_budget=int(params["cv_budget"]))
        meta = result["metadata"]

        recommended = result["recommended"]
        summary_lines = [
            f"插值模型比较完成：{meta['n_samples']} 个样本，"
            f"{meta['n_eligible']}/{len(meta['method_order'])} 个方法可行"
            f"（预算 {meta['budget_used']}/{meta['cv_budget']}）。"
        ]
        if recommended:
            summary_lines.append(recommended["evidence_note"])
        else:
            summary_lines.append("无可行方法（样本不足或预算耗尽）——见逐行跳过原因。")

        val_metrics = None
        for row in result["comparison"]:
            if row.get("validation"):
                v = ValidationMetrics(
                    target=f"interpolation_compare:{row['method']}",
                    method=row["validation"].get("method", "loocv"),
                    rmse=row["validation"].get("rmse"),
                    mae=row["validation"].get("mae"),
                    bias=row["validation"].get("bias"),
                    folds=row["validation"].get("folds"),
                    sample_count=row["validation"].get("sample_count"),
                )
                if recommended and row["method"] == recommended["method"]:
                    val_metrics = v
                break
        geojson_result = {
            "type": "FeatureCollection",
            "features": [],
            "summary": "；".join(summary_lines),
            "comparison": result["comparison"],
            "recommended": recommended,
            "compare_metadata": meta,
        }
        descriptor = get_algorithm_registry().get("interpolation.model_compare")
        if descriptor is not None:
            geojson_result["scientific_evidence"] = build_evidence(
                descriptor,
                tool="interpolation_model_compare",
                parameters_applied={
                    "value_field": params["value_field"],
                    "cv_budget": int(params["cv_budget"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) for CV distance math",
                ],
                validation=val_metrics,
            )
        return geojson_result

    # ── Foundation V3（Geostatistics/Interpolation 批次）────────────────

    @tool(registry, name="directional_variogram_analysis",
    side_effect="deterministic_compute",
    tags=('变异函数', '各向异性', '地统计', '方向半方差'),
           description=(
               "方向变异函数：沿单一方位角轴向（双向）计算经验半方差曲线，"
               "带角度容差与可选带宽（GSLIB band 语义），用于各向异性诊断与变异函数建模。"
               "方位角为数学约定：0°=东(+x)、逆时针（与 kriging 的 anisotropy_angle 一致，非罗盘方位）。"
               "\n何时用：怀疑场有方向性结构（如沿河谷/风向的污染物输运）、"
               "为 kriging 选 anisotropy_angle/ratio 前的证据收集。"
               "\n何时不用：只要全向变异函数+克里金表面 — 用 kriging_interpolation；"
               "要多方位角自动拟合各向异性椭圆 — 本工具不自动拟合，请多角度调用"
               "（库级 kriging.fit_anisotropy 已提供多方位扫描自动拟合）。"
               "\n关键约束：tolerance_deg 为轴向半角（≤90，90=全向退化）；"
               "azimuth+180° 与 azimuth 返回同一条轴（双向语义）；"
               "大 n 输入入口确定性分层抽稀 ≤2000（meta 披露）。"
           ),
           tier=2, domains=["statistics"], cost="medium",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥8 点）",
               "value_field": "数值字段名",
               "azimuth_deg": "轴方位角（度，数学约定 0°=东、逆时针；默认 0=东西向）",
               "tolerance_deg": "轴向半角（度，默认 22.5；90=全向）",
               "band_width": "带宽（米，配对中点到轴线垂距上限；默认不限）",
               "n_lags": "滞后 bin 数（4-64，默认 12）",
           })
    def directional_variogram_analysis(
        geojson: Any,
        value_field: str,
        azimuth_deg: float = 0.0,
        tolerance_deg: float = 22.5,
        band_width: Optional[float] = None,
        n_lags: int = 12,
    ) -> dict:
        import geopandas as gpd

        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.geo_analysis.interpolation import (
            _parse_point_values,
            _pick_metric_crs,
        )
        from app.lib.geo_analysis.kriging import (
            directional_variogram as _directional,
        )

        params = apply_contract("directional_variogram_analysis", {
            "value_field": value_field,
            "azimuth_deg": azimuth_deg,
            "tolerance_deg": tolerance_deg,
            "band_width": band_width if band_width is not None else 0.0,
            "n_lags": n_lags,
        })
        lonlat, values = _parse_point_values(
            geojson, params["value_field"],
            purpose="方向变异函数", log_prefix="directional_variogram",
        )
        utm_crs = _pick_metric_crs(lonlat)
        pts_gdf = gpd.GeoDataFrame(
            {"v": values},
            geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
            crs="EPSG:4326",
        ).to_crs(utm_crs)
        pts_metric = np.column_stack(
            (pts_gdf.geometry.x.values, pts_gdf.geometry.y.values)
        )
        bw = float(params["band_width"]) if float(params["band_width"]) > 0 else None
        lags, gamma, counts, meta = _directional(
            pts_metric, values, float(params["azimuth_deg"]),
            tolerance_deg=float(params["tolerance_deg"]),
            band_width=bw, n_lags=int(params["n_lags"]),
        )
        payload = {
            "success": True,
            "azimuth_deg": float(params["azimuth_deg"]),
            "tolerance_deg": float(params["tolerance_deg"]),
            "band_width": bw,
            "lags": [round(float(x), 3) for x in lags],
            "gamma": [round(float(x), 6) for x in gamma],
            "pair_counts": [int(c) for c in counts],
            "meta": meta,
            "summary": (
                f"方向变异函数完成：方位角 {params['azimuth_deg']}°（数学约定），"
                f"{len(lags)} 个有效滞后 bin，保留配对 {meta['n_pairs_kept']}/{meta['n_pairs_total']}。"
                + (
                    f" 输入 {meta['n_samples_input']} 点已确定性分层抽稀至 "
                    f"{meta['n_samples']}（拟合上限）。"
                    if meta.get("subsample_applied") else ""
                )
            ),
        }
        descriptor = get_algorithm_registry().get("interpolation.directional_variogram")
        if descriptor is not None:
            payload["scientific_evidence"] = build_evidence(
                descriptor,
                tool="directional_variogram_analysis",
                parameters_applied={
                    "value_field": params["value_field"],
                    "azimuth_deg": float(params["azimuth_deg"]),
                    "tolerance_deg": float(params["tolerance_deg"]),
                    "band_width": bw,
                    "n_lags": int(params["n_lags"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) before pair geometry",
                ],
            )
        return payload

    @tool(registry, name="variogram_model_selection",
    side_effect="deterministic_compute",
    tags=('变异函数', '模型选型', '地统计', 'aicc'),
           description=(
               "变异函数模型选择：spherical/exponential/gaussian/matern/wave/cubic 6 家族"
               "在同一经验变异函数上同台拟合，按加权 RSS 排名并附 AICc"
               "（k=3：sill/range/nugget；matern k=4，已披露）。完全确定性。"
               "\n何时用：克里金前为 variogram_model 选型提供证据；"
               "比较 hole-effect（wave）或平滑度（matern）家族是否更贴合数据。"
               "\n何时不用：直接用 kriging_interpolation 的 auto（生产 3 族选型）即可出表面；"
               "本工具只出统计表不出表面。"
               "\n关键约束：样本 <8 拒绝；AICc 基于加权残差（非严格极大似然，已披露）；"
               "robust=true 走 Cressie–Hawkins(1980) 稳健估计（对离群对稳健，opt-in，"
               "默认 false 经典 Matheron 主路径逐位不变）。"
           ),
           tier=2, domains=["statistics"], cost="medium",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥8 点）",
               "value_field": "数值字段名",
               "n_lags": "滞后 bin 数（4-64，默认 12）",
               "matern_smoothness": "Matérn 平滑度 ν（0.1-5.0，默认 0.5；仅 matern 家族使用）",
               "robust": "稳健估计 opt-in（Cressie–Hawkins 1980；默认 false=经典 Matheron）",
           })
    def variogram_model_selection(
        geojson: Any,
        value_field: str,
        n_lags: int = 12,
        matern_smoothness: float = 0.5,
        robust: bool = False,
    ) -> dict:
        import geopandas as gpd

        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.geo_analysis.interpolation import (
            _parse_point_values,
            _pick_metric_crs,
        )
        from app.lib.geo_analysis.kriging import select_variogram_model as _select

        params = apply_contract("variogram_selection_analysis", {
            "value_field": value_field,
            "n_lags": n_lags,
            "matern_smoothness": matern_smoothness,
            "robust": bool(robust),
        })
        lonlat, values = _parse_point_values(
            geojson, params["value_field"],
            purpose="变异函数模型选择", log_prefix="variogram_selection",
        )
        utm_crs = _pick_metric_crs(lonlat)
        pts_gdf = gpd.GeoDataFrame(
            {"v": values},
            geometry=gpd.points_from_xy(lonlat[:, 0], lonlat[:, 1]),
            crs="EPSG:4326",
        ).to_crs(utm_crs)
        pts_metric = np.column_stack(
            (pts_gdf.geometry.x.values, pts_gdf.geometry.y.values)
        )
        ranking, meta = _select(
            pts_metric, values,
            n_lags=int(params["n_lags"]),
            matern_smoothness=float(params["matern_smoothness"]),
            robust=bool(params["robust"]),
        )
        best = ranking[0]
        payload = {
            "success": True,
            "ranking": ranking,
            "best": best["model"],
            "best_params": best["params"],
            "robust": bool(params["robust"]),
            "meta": meta,
            "summary": (
                f"变异函数模型选择完成：{len(ranking)} 家族同台，"
                f"加权 RSS 最优={meta['best_weighted_rss']}"
                f"（rss={best['weighted_rss']:.4f}, aicc={best['aicc']:.1f}），"
                f"AICc 最优={meta['best_aicc']}。"
                + (
                    " 经验变异函数为 Cressie–Hawkins(1980) 稳健估计。"
                    if bool(params["robust"]) else ""
                )
            ),
        }
        descriptor = get_algorithm_registry().get("interpolation.variogram_selection")
        if descriptor is not None:
            payload["scientific_evidence"] = build_evidence(
                descriptor,
                tool="variogram_model_selection",
                parameters_applied={
                    "value_field": params["value_field"],
                    "n_lags": int(params["n_lags"]),
                    "matern_smoothness": float(params["matern_smoothness"]),
                    "robust": bool(params["robust"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) before variogram binning",
                ],
            )
        return payload

    @tool(registry, name="indicator_kriging_surface",
    side_effect="deterministic_compute",
    tags=('指示克里金', '概率面', '地统计', '插值'),
           description=(
               "指示克里金：逐阈值把数值转为指示变量（I=1[z≤t]），各自拟合指示变异函数后做"
               "普通克里金，输出每个 H3 单元 P(Z≤t) 概率、p50 阈值面（首个 p≥0.5 的阈值）"
               "与可选 E-type 估计。适用于类别/越界风险制图（如污染物超标概率）。"
               "\n何时用：『某处 PM2.5 超过 75 的概率』类问题；需要空间风险/概率面而非均值面。"
               "\n何时不用：只要连续均值面 — 用 kriging_interpolation；样本<8 — 用 idw_interpolation。"
               "\n关键约束：概率面已钳制 [0,1]（钳制计数披露）；逐阈值独立克里金"
               "不保证阈值间单调（如实披露）；auto 模型=逐阈值 6 家族加权 RSS 选型。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥8 点）",
               "value_field": "数值字段名",
               "thresholds": "阈值列表（逗号分隔，如 '35,75,115'；自动排序去重）",
               "resolution": "H3 分辨率（5-9），默认 7",
               "variogram_model": "指示变异函数模型: auto(默认,逐阈值6族选型)/spherical/exponential/gaussian",
               "k_neighbors": "指示克里金邻域样本数(2-24)，默认 16",
               "n_lags": "经验变异函数 bin 数（4-64，默认 12）",
               "etype": "是否输出 E-type 估计（类代表值=阈值本身，保守离散近似），默认 false",
           })
    def indicator_kriging_surface(
        geojson: Any,
        value_field: str,
        thresholds: str,
        resolution: int = 7,
        variogram_model: str = "auto",
        k_neighbors: int = 16,
        n_lags: int = 12,
        etype: bool = False,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.kriging import (
            indicator_kriging_surface as _indicator,
        )

        params = apply_contract("indicator_kriging_analysis", {
            "value_field": value_field,
            "thresholds": thresholds,
            "variogram_model": variogram_model,
            "k_neighbors": k_neighbors,
            "n_lags": n_lags,
            "resolution": resolution,
            "etype": bool(etype),
        })
        try:
            thr_list = [
                float(t) for t in str(params["thresholds"]).replace("；", ",").replace(";", ",").split(",")
                if str(t).strip()
            ]
        except ValueError as exc:
            raise ValueError(
                f"thresholds 解析失败：'{params['thresholds']}' 不是逗号分隔的数值列表"
            ) from exc
        if not thr_list:
            raise ValueError("thresholds 至少需要一个阈值（逗号分隔，如 '35,75,115'）")
        # F1 修复（science-v3 审计）：守卫对象是解析后的阈值个数，
        # 此前误用原始字符串长度 —— 8 阈值合法请求（23 字符）被误拒。
        if len(thr_list) > 20:
            from app.lib.gis.scientific_errors import ResourceScaleMismatch

            raise ResourceScaleMismatch(
                f"indicator kriging 需要 {len(thr_list)} 次独立变差函数拟合+求解"
                f"（概率面 n_thr×H×W 内存线性放大）",
                estimated=f"{len(thr_list)} thresholds × 变差函数拟合+克里金求解",
                limit="≤20 thresholds",
                correction_hint="用分位数子集（如 10/30/50/70/90 分位）刻画分布",
            )
        data = safe_parse_geojson(geojson)
        driver = _indicator(
            data, params["value_field"], thr_list,
            resolution=int(params["resolution"]),
            variogram_model=params["variogram_model"],
            n_lags=int(params["n_lags"]),
            k_neighbors=int(params["k_neighbors"]),
            etype=bool(params["etype"]),
        )
        meta = driver["metadata"]

        pred_fc = h3_to_geojson(
            [{"h3_index": r["h3_index"], "value": r["value"]} for r in driver["records"]],
            params["value_field"],
        )
        thr_sorted = meta["thresholds"]
        for feat, rec in zip(pred_fc["features"], driver["records"]):
            props = feat["properties"]
            props["p50_threshold"] = rec["p50_threshold"]
            for j, p in (rec.get("probabilities") or {}).items():
                props[f"p_le_{thr_sorted[int(j)]}"] = p
        pred_fc.update({
            "summary": (
                f"指示克里金完成：{len(pred_fc['features'])} 个 H3 单元，"
                f"{len(thr_sorted)} 个阈值（{thr_sorted}）；"
                f"概率钳制 {meta['clamped_cells']} 格，p50 缺失 {meta['p50_missing_cells']} 格"
                f"{'；E-type 已随主值输出' if params['etype'] else ''}。"
            ),
            "indicator_metadata": meta,
        })
        descriptor = get_algorithm_registry().get("interpolation.indicator_kriging")
        if descriptor is not None:
            # F2 修复（science-v3 审计）：descriptor 声明 raster_uncertainty，
            # 工具必须实际产出 typed 块 —— 概率面摘要（不搬格网，走属性通道）。
            from app.lib.gis.uncertainty import RasterUncertainty, UncertaintyMeasure

            p_values = [
                p
                for rec in driver["records"]
                for p in (rec.get("probabilities") or {}).values()
            ]
            uncertainty_blocks = []
            if p_values:
                p_mean = sum(p_values) / len(p_values)
                uncertainty_blocks.append(RasterUncertainty(
                    target="indicator_probability_surface",
                    interpretation=(
                        "阈值条件概率面 P(Z≤t)（逐阈值指示克里金）；"
                        "摘要为全部阈值×单元概率值的有界统计，非方差"
                    ),
                    summary=[
                        UncertaintyMeasure(
                            measure="value", value=p_mean,
                            method="mean indicator probability"),
                        UncertaintyMeasure(
                            measure="quantile", value=min(p_values),
                            method="p_min"),
                        UncertaintyMeasure(
                            measure="quantile", value=max(p_values),
                            method="p_max"),
                    ],
                ))
            pred_fc["scientific_evidence"] = build_evidence(
                descriptor,
                tool="indicator_kriging_surface",
                uncertainty=uncertainty_blocks,
                parameters_applied={
                    "value_field": params["value_field"],
                    "thresholds": thr_sorted,
                    "variogram_model": params["variogram_model"],
                    "k_neighbors": int(params["k_neighbors"]),
                    "n_lags": int(params["n_lags"]),
                    "resolution": int(params["resolution"]),
                    "etype": bool(params["etype"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) before kriging",
                ],
            )
        return pred_fc

    @tool(registry, name="cokriging_surface",
    side_effect="deterministic_compute",
    tags=('协同克里金', '地统计', '插值', '协变量'),
           description=(
               "协同定位协同克里金（Markov Model 1 近似）：主/次两个点要素集联合建模，"
               "交叉结构=ρ×主变量结构，次变量仅在目标格点协同定位进入系统，"
               "在 H3 网格上同时输出预测面与协同克里金方差(不确定性)面。"
               "ρ 缺省由最近配对 Pearson 自动估计；|ρ|<0.2 结构化拒绝（弱相关时"
               "协同克里金不会优于普通克里金——诚实拒绝而非输出无意义表面）。"
               "\n何时用：有一个强相关的易得协变量（如高程↔气温、AOD↔PM2.5）且希望"
               "把它注入克里金。"
               "\n何时不用：没有协变量 — 用 kriging_interpolation；弱相关（|ρ|<0.2）— 同样用普通克里金。"
               "\n诚实边界：MM1 为近似核化（全交叉协方差未建模）；次变量标准化假设；"
               "非协同定位处次变量由最近邻补格（均已披露）。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "主变量点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥8 点）",
               "secondary_geojson": "次变量点要素集 GeoJSON 或引用(ref:xxx)（覆盖同一区域）",
               "value_field": "主变量数值字段名",
               "secondary_field": "次变量数值字段名（次要素集的属性）",
               "resolution": "H3 分辨率（5-9），默认 7",
               "correlation_rho": "主/次相关系数 ρ（-1~1）；缺省 0=自动估计",
               "neighbors": "主变量克里金邻域样本数(2-24)，默认 12",
               "variogram_model": "主变量变异函数模型: auto(默认)/spherical/exponential/gaussian",
           })
    def cokriging_surface(
        geojson: Any,
        secondary_geojson: Any,
        value_field: str,
        secondary_field: str,
        resolution: int = 7,
        correlation_rho: Optional[float] = None,
        neighbors: int = 12,
        variogram_model: str = "auto",
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            RasterUncertainty,
            UncertaintyMeasure,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.kriging import (
            collocated_cokriging_surface as _cokriging,
        )

        params = apply_contract("cokriging_analysis", {
            "value_field": value_field,
            "secondary_field": secondary_field,
            "correlation_rho": correlation_rho if correlation_rho is not None else 0.0,
            "neighbors": neighbors,
            "variogram_model": variogram_model,
            "resolution": resolution,
        })
        data = safe_parse_geojson(geojson)
        sec_data = safe_parse_geojson(secondary_geojson)
        rho_arg = float(params["correlation_rho"])
        driver = _cokriging(
            data, params["value_field"], sec_data, params["secondary_field"],
            resolution=int(params["resolution"]),
            correlation_rho=rho_arg if rho_arg != 0.0 else None,
            neighbors=int(params["neighbors"]),
            variogram_model=params["variogram_model"],
        )
        meta = driver["metadata"]

        pred_records = [
            {"h3_index": r["h3_index"], "value": r["value"]} for r in driver["records"]
        ]
        prediction_fc = h3_to_geojson(pred_records, params["value_field"])
        for feat, rec in zip(prediction_fc["features"], driver["records"]):
            feat["properties"]["ck_variance"] = round(rec["ck_variance"], 6)
            feat["properties"]["ck_stddev"] = round(rec["ck_stddev"], 6)
        uncertainty_records = [
            {"h3_index": r["h3_index"], "value": r["ck_stddev"]}
            for r in driver["records"]
        ]
        uncertainty_fc = h3_to_geojson(uncertainty_records, "ck_stddev")

        rho_text = (
            f"ρ={meta['rho_used']:.3f}"
            + ("（自动估计）" if meta["rho_estimated"] else "（显式给定）")
        )
        prediction_fc.update({
            "summary": (
                f"协同克里金完成：{len(prediction_fc['features'])} 个 H3 单元，"
                f"主样本 {meta['n_samples']} / 次样本 {meta['n_secondary']}，{rho_text}；"
                f"不确定面(ck_stddev)已随本结果输出。"
            ),
            "uncertainty": uncertainty_fc,
            "ck_metadata": meta,
        })
        descriptor = get_algorithm_registry().get("interpolation.cokriging")
        if descriptor is not None:
            variance_range = meta.get("variance_range") or [None, None]
            prediction_fc["scientific_evidence"] = build_evidence(
                descriptor,
                tool="cokriging_surface",
                parameters_applied={
                    "value_field": params["value_field"],
                    "secondary_field": params["secondary_field"],
                    "correlation_rho": meta["rho_used"],
                    "neighbors": int(params["neighbors"]),
                    "variogram_model": params["variogram_model"],
                    "resolution": int(params["resolution"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "secondary regridded to target cells by nearest neighbour (collocation assumption)",
                ],
                uncertainty=[RasterUncertainty(
                    target="ck_variance",
                    interpretation="collocated cokriging variance (MM1 approximation, working CRS units squared)",
                    summary=[UncertaintyMeasure(
                        measure="value", value=variance_range[1],
                        method="max cokriging variance",
                    )],
                )],
            )
        return prediction_fc

    @tool(registry, name="nearest_neighbor_surface",
    side_effect="deterministic_compute",
    tags=('最近邻', '泰森多边形', '插值', '分段常值'),
           description=(
               "最近邻插值：每个 H3 单元取最近样本值，输出 Voronoi（泰森）分段常值场。"
               "无平滑、单元边界不连续（跳变是方法语义）；全域有值——凸包外为最近样本外推（已披露）。"
               "\n何时用：类别/离散标签的面化（土地利用分区、行政归属填充）；"
               "要求每个单元严格归属最近采样站的场景。"
               "\n何时不用：连续变量需要平滑面 — 用 idw_interpolation / kriging_interpolation；"
               "需要概率面 — 用 indicator_kriging_surface。"
               "\n关键约束：>20 万样本或 >400 万目标格点类型化拒绝。"
           ),
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何）",
               "value_field": "数值字段名",
               "resolution": "H3 分辨率（6-9），默认 8",
           })
    def nearest_neighbor_surface(geojson: Any, value_field: str, resolution: int = 8) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.interpolation import (
            nearest_neighbor_surface as _nn_surface,
        )

        params = apply_contract("nearest_neighbor_analysis", {
            "value_field": value_field,
            "resolution": resolution,
        })
        data = safe_parse_geojson(geojson)
        driver = _nn_surface(
            data, params["value_field"], resolution=int(params["resolution"])
        )
        meta = driver["metadata"]
        geojson_result = h3_to_geojson(driver["records"], params["value_field"])
        geojson_result["summary"] = (
            f"最近邻插值完成：{len(geojson_result['features'])} 个 H3 单元"
            f"(res={params['resolution']})；Voronoi 分段常值场，无平滑；"
            f"最大最近样本距离 {meta.get('max_nearest_distance')} m。"
        )
        geojson_result["nn_metadata"] = meta
        descriptor = get_algorithm_registry().get("interpolation.nearest_neighbor")
        if descriptor is not None:
            geojson_result["scientific_evidence"] = build_evidence(
                descriptor,
                tool="nearest_neighbor_surface",
                parameters_applied={
                    "value_field": params["value_field"],
                    "resolution": int(params["resolution"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) before nearest-neighbour math",
                ],
            )
        return geojson_result

    @tool(registry, name="natural_neighbor_surface",
    side_effect="deterministic_compute",
    tags=('自然邻域', '插值', 'sibson', 'voronoi'),
           description=(
               "自然邻域插值（Sibson 坐标）：权重=插入点从各自然邻域 Voronoi 单元"
               "窃取的面积比例（精确面积，Watson 阶梯算法收集自然邻域）。"
               "平滑、精确过样本点、精确再现线性函数；凸包外诚实空缺（不外推）。"
               "\n何时用：地形/气象类连续场的平滑插值，且不允许凸包外虚构值；"
               "比 IDW 平滑、比 TIN 更连续（C¹ 类行为、无网格伪影）。"
               "\n何时不用：需要凸包外覆盖 — 用 idw_interpolation / trend_surface；"
               "需要克里金方差 — 用 kriging_interpolation。"
               "\n关键约束：样本≥3 且非共线；>20 万样本 / >400 万目标格点类型化拒绝。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥3 个非共线点）",
               "value_field": "数值字段名",
               "resolution": "H3 分辨率（5-9），默认 7",
           })
    def natural_neighbor_surface(geojson: Any, value_field: str, resolution: int = 7) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.tin_interpolation import (
            natural_neighbor_surface as _sibson_surface,
        )

        params = apply_contract("natural_neighbor_analysis", {
            "value_field": value_field,
            "resolution": resolution,
        })
        data = safe_parse_geojson(geojson)
        driver = _sibson_surface(
            data, params["value_field"], resolution=int(params["resolution"])
        )
        meta = driver["metadata"]

        geojson_result = h3_to_geojson(driver["records"], params["value_field"])
        geojson_result["summary"] = (
            f"自然邻域插值完成：{len(geojson_result['features'])} 个 H3 单元"
            f"(res={params['resolution']}, 三角形数={meta['triangle_count']}, "
            f"凸包覆盖率={meta['fill_fraction']})；凸包外格网不外推（无值）。"
        )
        geojson_result["sibson_metadata"] = meta
        descriptor = get_algorithm_registry().get("interpolation.natural_neighbor")
        if descriptor is not None:
            geojson_result["scientific_evidence"] = build_evidence(
                descriptor,
                tool="natural_neighbor_surface",
                parameters_applied={
                    "value_field": params["value_field"],
                    "resolution": int(params["resolution"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "auto-projected to metric CRS (estimate_utm_crs/polar) before Delaunay/Sibson math",
                ],
            )
        return geojson_result

    @tool(registry, name="block_kriging_surface",
    side_effect="deterministic_compute",
    tags=('克里金', '块克里金', '地统计', '插值'),
           description=(
               "块克里金：以块支撑（默认自动=H3 单元尺度）估计块均值与块方差，"
               "2×2 子点离散化近似块均值协方差（Isaaks & Srivastava 1989，已披露）。"
               "块方差平均意义上不大于点方差——支撑越大不确定越小，输出面更稳健。"
               "\n何时用：关心『这个单元/地块的平均值』而非点值（环境限值对标、网格化报表）；"
               "样本点密集但采样噪声大，需要支撑平滑。"
               "\n何时不用：需要点尺度预测 — 用 kriging_interpolation；样本<8 — 用 idw_interpolation。"
               "\n关键约束：block_size=0 时按 H3 分辨率平均六边形边长自动取值；"
               "块尺寸相对变程越大离散化近似误差越大。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥8 点）",
               "value_field": "数值字段名",
               "resolution": "H3 分辨率（5-9），默认 7",
               "block_size": "块尺寸（米）；默认 0=按 H3 分辨率平均六边形边长自动取值",
               "neighbors": "克里金邻域样本数(2-24)，默认 12",
               "variogram_model": "变异函数模型: auto(默认)/spherical/exponential/gaussian",
           })
    def block_kriging_surface(
        geojson: Any,
        value_field: str,
        resolution: int = 7,
        block_size: float = 0.0,
        neighbors: int = 12,
        variogram_model: str = "auto",
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            RasterUncertainty,
            UncertaintyMeasure,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.kriging import (
            block_kriging_surface as _block_surface,
        )

        params = apply_contract("block_kriging_analysis", {
            "value_field": value_field,
            "block_size": block_size,
            "neighbors": neighbors,
            "variogram_model": variogram_model,
            "resolution": resolution,
        })
        data = safe_parse_geojson(geojson)
        driver = _block_surface(
            data, params["value_field"],
            resolution=int(params["resolution"]),
            block_size=float(params["block_size"]),
            neighbors=int(params["neighbors"]),
            variogram_model=params["variogram_model"],
        )
        meta = driver["metadata"]

        pred_records = [
            {"h3_index": r["h3_index"], "value": r["value"]} for r in driver["records"]
        ]
        prediction_fc = h3_to_geojson(pred_records, params["value_field"])
        for feat, rec in zip(prediction_fc["features"], driver["records"]):
            feat["properties"]["block_variance"] = round(rec["block_variance"], 6)
            feat["properties"]["block_stddev"] = round(rec["block_stddev"], 6)
        uncertainty_records = [
            {"h3_index": r["h3_index"], "value": r["block_stddev"]}
            for r in driver["records"]
        ]
        uncertainty_fc = h3_to_geojson(uncertainty_records, "block_stddev")

        prediction_fc.update({
            "summary": (
                f"块克里金完成：{len(prediction_fc['features'])} 个 H3 单元"
                f"(res={params['resolution']}, 块尺寸={meta['block_size']:.1f}m, "
                f"{meta['discretization']})；不确定面(block_stddev)已随本结果输出。"
            ),
            "uncertainty": uncertainty_fc,
            "block_metadata": meta,
        })
        descriptor = get_algorithm_registry().get("interpolation.block_kriging")
        if descriptor is not None:
            variance_range = meta.get("variance_range") or [None, None]
            prediction_fc["scientific_evidence"] = build_evidence(
                descriptor,
                tool="block_kriging_surface",
                parameters_applied={
                    "value_field": params["value_field"],
                    "block_size": float(params["block_size"]),
                    "neighbors": int(params["neighbors"]),
                    "variogram_model": params["variogram_model"],
                    "resolution": int(params["resolution"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "block support discretized with a fixed 2x2 sub-point grid (Isaaks & Srivastava 1989)",
                ],
                uncertainty=[RasterUncertainty(
                    target="block_variance",
                    interpretation="block kriging variance incl. within-block correction −γ̄(B,B)",
                    summary=[UncertaintyMeasure(
                        measure="value", value=variance_range[1],
                        method="max block kriging variance",
                    )],
                )],
            )
        return prediction_fc

    @tool(registry, name="sgs_simulation",
           description=(
               "SGS 条件高斯模拟：序贯高斯多实现采样，输出逐格 P10/P50/P90 与"
               "实现间标准差（风险制图 / 不确定性带，而非单一面）。normal-score "
               "域条件 SK + 随机路径；同 seed 逐位复现（caller_seeded）。"
               "\n何时用：需要『区间/概率/风险』而非单值——污染超标概率、储量区间、"
               "不确定性制图；克里金方差面不够时（方差≠分布）。"
               "\n何时不用：只要最优估计面 — 用 kriging_interpolation；样本<8 — 用 idw。"
               "\n关键约束：实现数×格点数有预算硬顶（先拒绝不 OOM）；"
               "ensemble 统计是蒙特卡洛近似（k 邻域条件近似，已披露）。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           side_effect="deterministic_compute",
           tags=("插值", "sgs", "条件模拟", "不确定性", "表面"),
           param_descriptions={
               "geojson": "输入点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥8 点）",
               "value_field": "数值字段名",
               "resolution": "H3 分辨率（5-9），默认 7",
               "n_realizations": "模拟实现数（默认 100；越大 ensemble 越稳、耗时线性增）",
               "seed": "随机种子（默认 42；同 seed 逐位复现）",
               "neighbors": "条件 SK 邻域样本数(2-24)，默认 16",
           })
    def sgs_simulation(
        geojson: Any,
        value_field: str,
        resolution: int = 7,
        n_realizations: int = 100,
        seed: int = 42,
        neighbors: int = 16,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            MonteCarloSummary,
            UncertaintyMeasure,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.kriging_simulation import (
            sgs_simulation_surface as _sgs_surface,
        )

        params = apply_contract("sgs_analysis", {
            "value_field": value_field,
            "resolution": resolution,
            "n_realizations": n_realizations,
            "seed": seed,
            "neighbors": neighbors,
        })
        data = safe_parse_geojson(geojson)
        driver = _sgs_surface(
            data, params["value_field"],
            resolution=int(params["resolution"]),
            n_realizations=int(params["n_realizations"]),
            seed=int(params["seed"]),
            neighbors=int(params["neighbors"]),
        )
        meta = driver["metadata"]
        if not driver["records"]:
            return {
                "summary": "SGS：0 个目标单元（极地/范围退化）——诚实空结果。",
                "features": [],
                "sgs_metadata": meta,
            }

        pred_records = [
            {"h3_index": r["h3_index"], "value": r["value"]} for r in driver["records"]
        ]
        prediction_fc = h3_to_geojson(pred_records, params["value_field"])
        for feat, rec in zip(prediction_fc["features"], driver["records"]):
            feat["properties"]["sgs_std"] = round(rec["sgs_std"], 6)
            feat["properties"]["p10"] = round(rec["p10"], 6)
            feat["properties"]["p90"] = round(rec["p90"], 6)
        # P90−P10 宽度面：逐格不确定性带（map/export 披露消费）
        width_records = [
            {"h3_index": r["h3_index"], "value": r["p90"] - r["p10"]}
            for r in driver["records"]
        ]
        uncertainty_fc = h3_to_geojson(width_records, "p90_minus_p10")

        prediction_fc.update({
            "summary": (
                f"SGS 模拟完成：{len(prediction_fc['features'])} 个 H3 单元 × "
                f"{meta['n_realizations']} 实现（seed={meta['seed']}，同 seed 逐位复现）；"
                f"主值=P50，P90−P10 不确定带面已随结果输出。"
            ),
            "uncertainty": uncertainty_fc,
            "sgs_metadata": meta,
        })
        descriptor = get_algorithm_registry().get("interpolation.sgs")
        if descriptor is not None:
            prediction_fc["scientific_evidence"] = build_evidence(
                descriptor,
                tool="sgs_simulation",
                parameters_applied={
                    "value_field": params["value_field"],
                    "resolution": int(params["resolution"]),
                    "n_realizations": int(params["n_realizations"]),
                    "seed": int(params["seed"]),
                    "neighbors": int(params["neighbors"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "degrees (input); metric working frame internally",
                },
                transformations=[
                    "normal-score transform -> conditional SK on random path -> back-transform",
                ],
                uncertainty=[
                    MonteCarloSummary(
                        target="sgs_ensemble",
                        interpretation=(
                            "多实现 ensemble 统计（P10/P50/P90/实现间 std）——"
                            "来自真实多实现采样，非解析方差面"),
                        summary=[
                            UncertaintyMeasure(
                                measure="std", value=float(meta["ensemble_std_range"][1]),
                                method="max inter-realization std (ddof=1)"),
                            UncertaintyMeasure(
                                measure="p10", value=float(meta["p10_range"][0]),
                                method="ensemble min p10"),
                            UncertaintyMeasure(
                                measure="p90", value=float(meta["p90_range"][1]),
                                method="ensemble max p90"),
                        ],
                    ),
                ],
            )
        return prediction_fc

    @tool(registry, name="cokriging_lmc_surface",
           description=(
               "LMC 全共克里金：线性共区域化模型（逐结构半正定）下主/次变量"
               "联合建模，次变量样本全部进入邻域系统（非仅目标协同定位——"
               "区别于 MM1 近似的 cokriging_surface）。"
               "\n何时用：次变量密集且与主变量强相关(|ρ|≥0.2)、需要真实全共克里金；"
               "次变量在目标处有独立观测信息。"
               "\n何时不用：|ρ|<0.2（弱相关不如 OK）；次变量与主变量完全复制"
               "（系统近奇异，方差不可信）。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           side_effect="deterministic_compute",
           tags=("插值", "cokriging", "共克里金", "多变量", "表面"),
           param_descriptions={
               "geojson": "主变量点要素集 GeoJSON 或引用(ref:xxx)（Point 几何，≥8 点）",
               "secondary_geojson": "次变量点要素集 GeoJSON（Point 几何，≥4 点；>2 万点自动确定性抽稀）",
               "primary_field": "主变量数值字段名",
               "secondary_field": "次变量数值字段名",
               "resolution": "H3 分辨率（5-9），默认 7",
               "neighbors1": "主变量邻域样本数(2-24)，默认 12",
               "neighbors2": "次变量邻域样本数(2-24)，默认 8",
           })
    def cokriging_lmc_surface(
        geojson: Any,
        secondary_geojson: Any,
        primary_field: str,
        secondary_field: str,
        resolution: int = 7,
        neighbors1: int = 12,
        neighbors2: int = 8,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            RasterUncertainty,
            UncertaintyMeasure,
        )
        from app.lib.geo_analysis.cokriging_lmc import (
            cokriging_lmc_surface as _ck_surface,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson

        params = apply_contract("cokriging_lmc_analysis", {
            "primary_field": primary_field,
            "secondary_field": secondary_field,
            "resolution": resolution,
            "neighbors1": neighbors1,
            "neighbors2": neighbors2,
        })
        data = safe_parse_geojson(geojson)
        data_sec = safe_parse_geojson(secondary_geojson)
        driver = _ck_surface(
            data, params["primary_field"], data_sec, params["secondary_field"],
            resolution=int(params["resolution"]),
            neighbors1=int(params["neighbors1"]),
            neighbors2=int(params["neighbors2"]),
        )
        meta = driver["metadata"]
        if not driver["records"]:
            return {
                "summary": "LMC 共克里金：0 个目标单元（极地/范围退化）——诚实空结果。",
                "features": [],
                "lmc_metadata": meta,
            }
        pred_records = [
            {"h3_index": r["h3_index"], "value": r["value"]} for r in driver["records"]
        ]
        prediction_fc = h3_to_geojson(pred_records, params["primary_field"])
        for feat, rec in zip(prediction_fc["features"], driver["records"]):
            feat["properties"]["ck_variance"] = round(rec["ck_variance"], 6)
            feat["properties"]["ck_stddev"] = round(rec["ck_stddev"], 6)
        prediction_fc.update({
            "summary": (
                f"LMC 全共克里金完成：{len(prediction_fc['features'])} 个 H3 单元；"
                f"主/次相关 ρ={meta['lmc']['rho']}，次变量 {meta['n_secondary']} 点"
                f"（k1={meta['neighbors']['primary']}, k2={meta['neighbors']['secondary']}）；"
                "方差面已随结果输出。"
            ),
            "lmc_metadata": meta,
        })
        descriptor = get_algorithm_registry().get("interpolation.cokriging_lmc")
        if descriptor is not None:
            prediction_fc["scientific_evidence"] = build_evidence(
                descriptor,
                tool="cokriging_lmc_surface",
                parameters_applied={
                    "primary_field": params["primary_field"],
                    "secondary_field": params["secondary_field"],
                    "resolution": int(params["resolution"]),
                    "neighbors1": int(params["neighbors1"]),
                    "neighbors2": int(params["neighbors2"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "LMC: two shared structures, B matrices PSD by construction",
                    "full cokriging system with both variables in the neighborhood",
                ],
                uncertainty=[RasterUncertainty(
                    target="ck_variance",
                    interpretation="full cokriging variance under the fitted LMC",
                    summary=[UncertaintyMeasure(
                        measure="value", value=float(meta["variance_range"][1]),
                        method="max cokriging variance"),
                    ]),
                ],
            )
        return prediction_fc

    @tool(registry, name="st_kriging_surface",
           description=(
               "时空克里金：在 (x,y,t) 时空协方差下预测指定时刻的表面。"
               "product_sum（双时间尺度可分离正混合，按构造半正定）或 "
               "separable（可分离）模型；时间单位秒（epoch/相对秒）。"
               "\n何时用：多时相观测（站点时序、传感器网络），需要『某时刻』"
               "的连续面且时间相关性真实存在。"
               "\n何时不用：单一时刻观测（用 kriging_interpolation）；"
               "时间维退化（全部同时刻→结构化拒绝）。"
           ),
           tier=2, domains=["statistics"], cost="heavy",
           side_effect="deterministic_compute",
           tags=("插值", "kriging", "时空", "表面", "时序"),
           param_descriptions={
               "geojson": "点要素集 GeoJSON（Point 几何，≥12 点、跨多时相）",
               "value_field": "数值字段名",
               "time_field": "时间字段名（epoch/相对秒）",
               "target_time_sec": "目标时刻（秒）",
               "resolution": "H3 分辨率（5-9），默认 7",
               "model": "时空协方差: product_sum(默认)/separable",
               "temporal_range_sec": "时间相关变程（秒，默认 30 天）",
               "time_window_sec": "邻域时间窗（秒，默认 30 天）",
               "neighbors": "时空邻域样本数(2-24)，默认 16",
           })
    def st_kriging_surface(
        geojson: Any,
        value_field: str,
        time_field: str,
        target_time_sec: float,
        resolution: int = 7,
        model: str = "product_sum",
        temporal_range_sec: float = 2592000.0,
        time_window_sec: Optional[float] = None,
        neighbors: int = 16,
    ) -> dict:
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_evidence import build_evidence
        from app.lib.gis.uncertainty import (
            RasterUncertainty,
            UncertaintyMeasure,
        )
        from app.lib.geo_analysis.interpolation import h3_to_geojson
        from app.lib.geo_analysis.kriging_st import (
            st_kriging_surface as _st_surface,
        )

        params = apply_contract("st_kriging_analysis", {
            "value_field": value_field,
            "time_field": time_field,
            "target_time_sec": float(target_time_sec),
            "resolution": resolution,
            "model": model,
            "temporal_range_sec": float(temporal_range_sec),
            "time_window_sec": (
                float(time_window_sec) if time_window_sec is not None else None),
            "neighbors": neighbors,
        })
        data = safe_parse_geojson(geojson)
        # review R2-M1：工具缺省 None 会绕过 lib 的 30 天默认窗（显式 None
        # = 无窗）—— 此处落地缺省并把生效窗写进 metadata。
        effective_window = (
            float(params["time_window_sec"])
            if params["time_window_sec"] is not None
            else 2592000.0)
        driver = _st_surface(
            data, params["value_field"], params["time_field"],
            target_time_sec=params["target_time_sec"],
            resolution=int(params["resolution"]),
            model=params["model"],
            temporal_range_sec=float(params["temporal_range_sec"]),
            time_window_sec=effective_window,
            neighbors=int(params["neighbors"]),
        )
        meta = driver["metadata"]
        if not driver["records"]:
            return {
                "summary": "时空克里金：0 个目标单元（极地/范围退化）——诚实空结果。",
                "features": [],
                "st_metadata": meta,
            }
        pred_records = [
            {"h3_index": r["h3_index"], "value": r["value"]} for r in driver["records"]
        ]
        prediction_fc = h3_to_geojson(pred_records, params["value_field"])
        for feat, rec in zip(prediction_fc["features"], driver["records"]):
            feat["properties"]["st_variance"] = round(rec["st_variance"], 6)
            feat["properties"]["st_stddev"] = round(rec["st_stddev"], 6)
        prediction_fc.update({
            "summary": (
                f"时空克里金完成：{len(prediction_fc['features'])} 个 H3 单元 @ "
                f"t={meta['target_time_sec']:.0f}s（{meta['st_model']['model']} 模型，"
                f"时间变程 {meta['st_model']['temporal_range_seconds']:.0f}s）；"
                "方差面已随结果输出。"
            ),
            "st_metadata": meta,
        })
        descriptor = get_algorithm_registry().get("interpolation.st_kriging")
        if descriptor is not None:
            prediction_fc["scientific_evidence"] = build_evidence(
                descriptor,
                tool="st_kriging_surface",
                parameters_applied={
                    "value_field": params["value_field"],
                    "time_field": params["time_field"],
                    "target_time_sec": params["target_time_sec"],
                    "model": params["model"],
                    "temporal_range_sec": params["temporal_range_sec"],
                    "time_window_sec": params["time_window_sec"],
                    "neighbors": int(params["neighbors"]),
                    "resolution": int(params["resolution"]),
                },
                input_facts={
                    "artifact_type": "point_feature_set",
                    "feature_count": meta.get("n_samples"),
                    "crs": "EPSG:4326",
                    "units": "m",
                },
                transformations=[
                    "space-time covariance (separable/product-sum, PSD by construction)",
                    "time-bounded neighborhoods (windowed samples only)",
                ],
                uncertainty=[RasterUncertainty(
                    target="st_variance",
                    interpretation="space-time kriging variance at target_time_sec",
                    summary=[UncertaintyMeasure(
                        measure="value", value=float(meta["variance_range"][1]),
                        method="max ST kriging variance"),
                    ]),
                ],
            )
        return prediction_fc

    @tool(registry, name="overlay_analysis",
           description="对两个几何图层进行空间叠加分析（如求交、合并、擦除等），返回结果及其统计信息",
           args_model=OverlayAnalysisArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("叠加", "相交", "union", "擦除", "overlay", "merge"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"),
           examples=("两个图层求交集", "用行政区范围擦除POI图层"),
           summary=("对两个矢量图层做空间叠加（intersection/union/identity/"
                    "symmetric_difference/difference），返回叠加结果 FC 与统计。"
                    "缓冲/裁剪等几何准备后再做本工具，可回答『范围内有什么』类问题。"))
    def overlay_analysis(layer_a: Any, layer_b: Any, how: str = "intersection") -> dict:
        data_a = safe_parse_geojson(layer_a)
        data_b = safe_parse_geojson(layer_b)
        # #765: forward the parsed FeatureCollection (not the bare features
        # list) so a declared `crs` member survives the tool boundary — the
        # deep operators (overlay_smart -> gdf_from_features) honor it.
        res = SpatialAnalyzer.overlay(data_a, data_b, how)
        return res.to_llm_response()

    @tool(registry, name="attribute_filter",
    capabilities=['data_source_pipeline'],
           description=(
               "属性筛选：按 Pandas 风格查询表达式从要素集中筛出新的要素集。"
               "✅ 用于：要把筛选结果作为新图层用于后续分析 / 导出。"
               "\n❌ 不要用于：只想临时改现有图层的可见要素 — 用 apply_layer_filter。"
           ),
           args_model=AttributeFilterArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("筛选", "属性过滤", "filter", "query", "子集"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "missing_data"),
           examples=("筛出人口大于1000的区县", "只保留类型是公园的要素"),
           summary=("按 Pandas 风格表达式（如 'pop > 1000'）从要素集筛出子集 FC。"
                    "产出可用于后续分析/导出的新图层；只想改现有图层可见要素用 apply_layer_filter。"))
    def attribute_filter(geojson: Any, query: str) -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        # #1110: forward the parsed FeatureCollection (not the bare features
        # list) — to_feature_collection() rebuilds a fresh FC from a list and
        # drops the declared `crs` member, so projected input fell back to
        # EPSG:4326 (metres silently read as degrees).
        res = SpatialAnalyzer.attribute_filter(data, query)
        return res.to_llm_response()

    @tool(registry, name="spatial_join",
           description=(
               "空间连接：按拓扑关系 (intersects/within/contains 等) 将右图层属性附加到左图层要素。"
               "\n何时用：『把人口属性挂到行政区上』『把 POI 所属街道写回 POI』『判断每个建筑是否在保护区内』；"
               "做主题图（按属性着色）前的属性预处理。"
               "\n何时不用：(1) 只要点数 / 求和 — 用 spatial_aggregate（不返回连接后的全部右属性，更轻量）；"
               "(2) 要保留左图层全部、空匹配补 NaN — join_type='left'；inner 只保留有匹配的。"
               "\n关键约束：predicate 取值 intersects/within/contains/touches/crosses；"
               "左右图层 CRS 必须一致（内部自动按 WGS84 处理）。"
           ),
           args_model=SpatialJoinArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("空间连接", "spatial join", "属性挂接", "sjoin", "拓扑关系"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"),
           examples=("把人口属性挂到行政区上", "给每个POI标注所属街道"),
           summary=("按拓扑谓词（intersects/within/contains/touches/crosses）把右图层属性"
                    "附加到左图层要素，做主题图前的属性预处理。只计数不求属性用 "
                    "spatial_aggregate；空匹配补 NaN 用 join_type='left'。"))
    def spatial_join(left_layer: Any, right_layer: Any, join_type: str = "inner", predicate: str = "intersects") -> dict:
        data_left = safe_parse_geojson(left_layer)
        data_right = safe_parse_geojson(right_layer)
        from app.services.spatial_analyzer import SpatialAnalyzer
        # #765: forward the parsed FeatureCollections (not bare features
        # lists) so declared `crs` members reach gdf_from_features (#599).
        res = SpatialAnalyzer.spatial_join(
            data_left,
            data_right,
            join_type=join_type,
            predicate=predicate
        )
        return res.to_llm_response()

    @tool(registry, name="clip_layer",
           description="裁剪图层：仅保留位于指定遮罩图层（通常是行政边界）范围内的要素。适合解决『搜索结果超出了行政区范围』的问题，实现精准区域分析。",
           param_descriptions={
               "target_layer": "待裁剪的图层（点、线、面）GeoJSON 或引用(ref:xxx)",
               "mask_layer": "裁剪遮罩（通常是一个行政区面）GeoJSON 或引用(ref:xxx)",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("裁剪", "clip", "掩膜", "行政区", "范围裁剪"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "missing_data"),
           examples=("用成都市边界裁剪POI图层", "只保留景区范围内的轨迹点"),
           summary=("仅保留位于遮罩图层（通常是行政边界）范围内的要素，解决『搜索结果超出"
                    "行政区范围』问题；裁剪结果是精准区域分析的新图层。"))
    def clip_layer(target_layer: Any, mask_layer: Any) -> dict:
        target = safe_parse_geojson(target_layer)
        mask = safe_parse_geojson(mask_layer)
        # #765: forward BOTH layers as parsed FeatureCollections — the
        # target was previously stripped to its features list (dropping its
        # declared `crs`) while the mask kept its FC (asymmetric).
        res = SpatialAnalyzer.clip(target, mask)
        return res.to_llm_response()

    @tool(registry, name="spatial_aggregate",
           description=(
               "空间聚合：统计落在每个多边形（如行政区）内的点位（如 POI）数量。"
               "✅ 用于：矢量点要素的计数聚合，返回带统计结果的多边形图层。"
               "\n❌ 不要用于：多边形内的栅格统计（人口/降雨/海拔）— 用 zonal_stats。"
               "\n科学语义（ADR-0099）：count 不是率/密度 —— 需要 rate/density 时"
               "必须显式给分母（denominator_kind=field/area），零分母区 rate=null。"
           ),
           param_descriptions={
               "points": "点要素集 GeoJSON 或引用(ref:xxx)",
               "polygons": "多边形要素集（如行政区）GeoJSON 或引用(ref:xxx)",
               "count_field": "存储统计数量的字段名，默认 'point_count'",
               "numerator_field": "可选：点要素数值字段（按区求和作为分子）；缺省=要素计数",
               "denominator": "denominator_kind=field 时的分母字段名（区面属性，如人口）",
               "denominator_kind": "分母类型：count(默认,纯计数)/field(区字段)/area(区面积密度)",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("空间聚合", "点计数", "分区内数量", "aggregation", "密度"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"),
           examples=("统计每个区有多少家咖啡店", "按街道统计POI数量并算每平方公里密度"),
           summary=("统计落在每个多边形（行政区等）内的点位数量/求和，返回带统计字段的"
                    "多边形图层。需要率/密度时必须显式给分母（denominator_kind=field/area）。"
                    "多边形内的栅格统计用 zonal_stats。"))
    def spatial_aggregate(
        points: Any, polygons: Any, count_field: str = "point_count",
        numerator_field: Optional[str] = None,
        denominator: Optional[str] = None,
        denominator_kind: str = "count",
    ) -> dict:
        pts = safe_parse_geojson(points)
        polys = safe_parse_geojson(polygons)
        # #764: count_field names the OUTPUT count column, it is not a point
        # attribute selector — forwarding it as value_field was inert for
        # stats=['count'] (and would silently change the aggregated metric if
        # the points carried a same-named field). Request only the count and
        # rename the output column afterwards.
        # #765: pass the parsed FeatureCollections (not bare features
        # lists) so declared `crs` members reach to_utm_gdf.
        if denominator_kind != "count" or numerator_field:
            # ADR-0099 显式分母通道（库层 aggregate_with_denominator）：
            # rate 语义 —— 零/负分母 → rate=null（绝不静默 0/inf）。
            from app.lib.geo_analysis.aggregation import aggregate_with_denominator
            from app.lib.geo_processor.core import (
                declare_crs, to_utm_gdf,
            )
            from app.lib.gis.scientific_evidence import build_evidence
            from app.lib.gis.algorithm_registry import get_algorithm_registry
            _pts = to_utm_gdf(pts)
            _zones = to_utm_gdf(polys)
            pts_gdf = _pts[0] if _pts[0] is not None else None
            zones_gdf = _zones[0] if _zones[0] is not None else None
            if pts_gdf is None or zones_gdf is None:
                raise ValueError("invalid input: empty points or polygons layer")
            out_gdf, norm_evidence = aggregate_with_denominator(
                pts_gdf, zones_gdf,
                numerator_field=numerator_field,
                denominator=denominator,
                denominator_kind=denominator_kind,
            )
            import json as _json
            fc = _json.loads(out_gdf.to_json())
            descriptor = get_algorithm_registry().get("spatial.aggregate.rates")
            evidence = build_evidence(
                descriptor, tool="spatial_aggregate",
                parameters_applied={
                    "numerator_field": numerator_field,
                    "denominator": denominator,
                    "denominator_kind": denominator_kind,
                },
                diagnostics=[],
            ) if descriptor else {}
            evidence["normalization"] = norm_evidence
            return {
                "success": True,
                "data": declare_crs(fc, "EPSG:4326"),
                "summary": {
                    "rate_kind": denominator_kind,
                    "zones": int(len(out_gdf)),
                },
                "scientific_evidence": evidence,
            }
        res = SpatialAnalyzer.aggregate(
            pts,
            polys,
            stats=['count'],
        )
        if res.success and count_field and count_field != "count":
            for feat in ((res.data or {}).get("features", []) if isinstance(res.data, dict) else []):
                props = feat.setdefault("properties", {})
                if "count" in props:
                    props[count_field] = props.pop("count")
        return res.to_llm_response()

    @tool(registry, name="isochrone_network",
           description="等时线分析（路网模式）：基于路网计算从设施点出发在指定时间内可达的范围。需要输入路网要素。",
           tier=2, domains=["network"],
           args_model=IsochroneAnalysisArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("等时线", "isochrone", "可达范围", "路网", "通达时间"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "missing_data"))
    def isochrone_network(network_layer: Any, facilities: Any, travel_time: float = 15, mode: str = "walking") -> dict:
        net = safe_parse_geojson(network_layer)
        facs = safe_parse_geojson(facilities)
        # #765: forward the parsed FeatureCollections so declared `crs`
        # members reach to_utm_gdf (calculate_isochrones).
        res = SpatialAnalyzer.isochrone_network(net, facs, travel_time, mode)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["statistics"], name="fishnet_grid",
    anti_examples=("六边形蜂窝网格（用 h3_binning）",),
           description=(
               "鱼网格网生成：在 bbox 内生成正方形或六边形覆盖网格 (空 cell，无属性)。"
               "\n何时用：作为 spatial_aggregate / spatial_join 的底图做空间统计；"
               "需要规则网格做密度可视化但不想用 H3 索引（如要导出兼容 ArcGIS 的 shp）。"
               "\n何时不用：(1) 仅需点的网格聚合 — 直接用 h3_binning（自带 H3 索引、性能更好）；"
               "(2) 要平滑等值面 — 用 kde_contours / idw_interpolation。"
               "\n关键约束：bounds=[west,south,east,north] WGS84；cell_size 单位米；"
               "大 bbox + 小 cell_size 会爆内存（>10⁶ 格警告）。"
           ),
           args_model=FishnetGridArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="heavy",
           scale_class="large",
           tags=("鱼网", "网格", "fishnet", "六边形", "hexagon", "格网"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "memory"))
    def fishnet_grid(bounds: List[float], cell_size: float, type: str = "square") -> dict:
        from app.lib.geo_analysis.aggregation import generate_fishnet
        res = generate_fishnet(bounds, cell_size, type)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["statistics"], name="central_feature",
           description="中心分析：寻找点集的中心位置。支持计算平均中心(mean_center)或寻找距离所有点最近的中心要素(central_feature)。",
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "method": "方法: 'mean_center'(平均中心) 或 'central_feature'(中心要素)",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("中心", "平均中心", "中心要素", "mean center"),
           output_semantic_type="geojson_fc",
           result_size_policy="inline_small",
           crs_semantics="auto_project",
           failure_modes=("invalid_args", "missing_data"))
    def central_feature(geojson: Any, method: str = "mean_center") -> dict:
        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError("invalid GeoJSON input: could not parse a FeatureCollection")
        # #1110: forward the full FeatureCollection so the declared `crs`
        # member reaches to_utm_gdf() — a bare list was rebuilt into a CRS-less
        # FC and projected coordinates were misread as WGS84 degrees.
        res = SpatialAnalyzer.central_feature(data, method)
        return res.to_llm_response()

    @tool(registry, name="service_area_simple",
    anti_examples=("直线半径圈（欧氏缓冲用 buffer_analysis）",),
           description=(
               "简单服务区分析：按出行模式和时间生成可达范围。"
               "✅ 用于：沿出行速度估算的行程时间/距离可达范围（等时圈），"
               "如『某设施 15 分钟步行圈』。"
               "\n❌ 不要用于：简单直线半径缓冲 — 用 buffer_analysis。"
           ),
           tier=2, domains=["network"],
           param_descriptions={
               "geojson": "设施点要素集 GeoJSON 或引用(ref:xxx)",
               "travel_time_min": "出行时间（分钟），默认 15",
               "mode": "出行方式: 'walking'(默认, 5km/h), 'cycling'(15km/h), 'driving'(40km/h)",
               "dissolve": "是否合并所有点的服务区，默认 True",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("服务区", "等时圈", "可达范围", "行程时间", "service area"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="auto_project",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def service_area_simple(geojson: Any, travel_time_min: float = 15, mode: str = "walking", dissolve: bool = True) -> dict:
        speeds = {"walking": 5.0, "cycling": 15.0, "driving": 40.0}
        speed = speeds.get(mode.lower(), 5.0)
        distance_m = (speed * 1000) * (travel_time_min / 60.0)
        data = safe_parse_geojson(geojson)
        # #765: forward the parsed FeatureCollection so a declared `crs`
        # member reaches buffer_smart's gdf_from_features.
        res = SpatialAnalyzer.buffer(data, distance=distance_m, unit="m", dissolve=dissolve)
        return res.to_llm_response()

    @tool(registry, name="h3_binning",
    anti_examples=("方形公里渔网（用 fishnet_grid）",),
           description=(
               "H3 六边形网格聚合：把点数据聚合到指定分辨率的 H3 网格（代替传统鱼网）。"
               "✅ 用于：需要每个网格的统计值（计数/求和/均值）做数据驱动渲染，"
               "或作为 h3_lisa 空间聚类检验的前置步骤。"
               "\n❌ 不要用于：(1) 只想快速看分布趋势 — 用 heatmap_data(render_type='native')；"
               "(2) 需要平滑的连续密度面 — 用 kde_surface。"
           ),
           tier=2, domains=["statistics"],
           param_descriptions={
               "geojson": "点要素集 GeoJSON 或引用(ref:xxx)",
               "resolution": "H3分辨率（通常 6 到 9 之间，越大网格越小），例如 8",
               "stat_field": "可选：参与统计的字段名",
               "stat_method": "统计方法，如 'count'（默认）, 'sum', 'mean'",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("h3", "网格聚合", "六边形", "计数", "binning"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "missing_data"),
           examples=("把POI聚合到H3网格看数量分布", "按网格统计每个格子内的店铺数"),
           summary=("把点数据聚合到 H3 六边形网格（count/sum/mean），返回带统计值与 "
                    "legend_spec 的网格 FC，可做数据驱动渲染，也是 h3_lisa 的前置步骤。"
                    "快速看趋势用 heatmap_data；连续密度面用 kde_surface。"))
    @cached_tool(ttl=3600)
    def h3_binning(geojson: Any, resolution: int = 8, stat_field: str = None, stat_method: str = 'count') -> dict:
        from app.lib.geo_analysis.aggregation import h3_binning as _h3_binning
        data = safe_parse_geojson(geojson)
        res = _h3_binning(data, resolution, stat_field, stat_method)
        payload = res.to_llm_response()

        try:
            out_geojson = payload.get("data") if isinstance(payload, dict) else None
            # G-9（#873）：lib 层 sum/mean 缺有效 stat_field 时降级 count 并在
            # data 上标记 stat_method_effective —— 列名推断以它为准（此前
            # 按 stat_method 取列名会拿到不存在的列，legend_spec 静默缺失）。
            effective_method = "count"
            if isinstance(out_geojson, dict) and out_geojson.get("stat_method_effective"):
                effective_method = out_geojson["stat_method_effective"]
            elif stat_field and stat_method in ("sum", "mean"):
                effective_method = stat_method
            if effective_method != "count":
                stat_field_name = effective_method
            else:
                stat_field_name = "count"
            if (
                stat_method in ("sum", "mean") and effective_method == "count"
                and isinstance(payload, dict)
            ):
                payload["correction_hint"] = (
                    f"stat_method={stat_method} 需要同时传 stat_field（数值列名）；"
                    f"本次已降级为 count 统计。"
                )
            if isinstance(out_geojson, dict):
                # Single canonical graduated-spec builder (ADR-0078): runs the
                # one classification algorithm, resolves palette colors through
                # one path (midpoint sampling, matching create_thematic_map), and
                # filters NaN/Inf once. Replaces the verbatim palette truncation
                # that diverged from every other graduated emitter.
                from app.lib.cartography.thematic_spec import build_graduated_spec
                spec = build_graduated_spec(
                    out_geojson, stat_field_name, method="quantiles", k=5, palette="YlOrRd"
                )
                if spec is not None and isinstance(payload, dict):
                    payload["legend_spec"] = spec
        except Exception as e:  # noqa: BLE001 — legend failure never blocks tool result
            import logging
            logging.getLogger(__name__).warning(f"[h3_binning] legend_spec construction failed: {e}")

        if isinstance(payload, dict) and isinstance(payload.get("data"), dict) and payload["data"].get("type") == "FeatureCollection":
            payload["data"] = trim_features(payload["data"])
        return payload

    @tool(registry, tier=2, domains=["statistics"], name="dissolve_layer",
           description=(
               "矢量融合 (Dissolve)：把相邻同属性的多边形/线合并为单一几何，可选按字段分组。"
               "\n何时用：(1) 把街道边界合并为区县轮廓；"
               "(2) 把同类用地（如『住宅』『商业』）的相邻地块合并；"
               "(3) overlay/intersect 后清理碎片；"
               "(4) 生成清洁的母图层用于 clip_layer。"
               "\n何时不用：(1) 只想统计每个多边形的属性 — 用 spatial_aggregate；"
               "(2) 要联合两个不同图层 — 用 overlay_analysis(how='union')；"
               "(3) 单纯按属性筛选 — 用 attribute_filter。"
               "\n关键约束：未给 field 时会把整个图层融合成 1 个要素；"
               "给定 field 后会按字段值分组，每组一个融合结果。"
           ),
           param_descriptions={
               "geojson": "输入图层 GeoJSON 或引用(ref:xxx)，几何类型应一致（全部 polygon 或全部 line）",
               "field": "可选属性字段名。若提供，按该字段的不同值分组分别融合；不提供则整体融合为单一要素",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="medium",
           tags=("融合", "dissolve", "合并", "相邻", "多边形合并"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           failure_modes=("invalid_args", "missing_data"))
    def dissolve_layer(geojson: Any, field: Optional[str] = None) -> dict:
        from app.lib.geo_processor.geometry import dissolve_smart
        res = dissolve_smart(geojson, field=field)
        return res.to_llm_response()

    @tool(registry, tier=2, domains=["network"], name="nearest_facility",
           description=(
               "最近设施匹配：对每个源点找出目标集合中距离最近的目标，并标注距离（米）。"
               "\n何时用：『每户居民最近的医院/学校』『100 个 POI 最近的地铁站』『每个公交站最近的商圈』 — "
               "**双集合最近邻匹配的唯一工具**。"
               "\n何时不用：(1) 同一集合内的最近邻距离/聚集度 — 用 nearest_neighbor (单集合统计)；"
               "(2) 服务区/可达性 — 用 isochrone_analysis 或 service_area_simple；"
               "(3) 沿路网最近 — 当前是欧氏距离，沿路网路网最短路径暂不支持。"
               "\n返回：每个源点的副本，properties 新增最近目标标识与 distance_m；目标含 id/name/fid 字段时标识为 nearest_target_id（取其值），否则为 nearest_target_index（目标行号）。"
           ),
           param_descriptions={
               "source_points": "源点要素集 (GeoJSON 或 ref:xxx) — 每个点会找一个最近目标",
               "target_points": "目标点要素集 (GeoJSON 或 ref:xxx) — 候选设施集合",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="medium",
           scale_class="medium",
           tags=("最近设施", "最近邻匹配", "nearest facility", "双集合", "距离"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data"))
    def nearest_facility(source_points: Any, target_points: Any) -> dict:
        from app.lib.geo_analysis.network import nearest_neighbor_features
        res = nearest_neighbor_features(source_points, target_points)
        return res.to_llm_response()

    @tool(registry, name="raster_reclassify",
    anti_examples=("改变像元分辨率（用 raster_resample）",),
           description=(
               "栅格重分类：将连续栅格值按方案映射为离散类别。"
               "\n何时用：『把 NDVI 连续值分成低/中/高植被覆盖等级』；"
               "把高程分成平原/丘陵/山地；把坡度分成安全/中等/危险等级。"
               "\n何时不用：(1) 只是查看统计值 — 用 zonal_stats；"
               "(2) 两个栅格做运算 — 用 raster_calculator。"
               "\n关键约束：scheme 按 min→max 排序，首个匹配 wins；未匹配像素变 nodata。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterReclassifyArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("重分类", "栅格", "reclassify", "分级", "ndvi", "dem"),
           output_semantic_type="ref",
           result_size_policy="inline_small",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def raster_reclassify(raster_path: str, scheme: List[dict], nodata: Optional[float] = None) -> dict:
        res = SpatialAnalyzer.raster_reclassify(raster_path, scheme, nodata)
        return res.to_llm_response()

    @tool(registry, name="raster_calculator",
           description=(
               "栅格计算器：对两个栅格做像素级数学运算（A+B, A-B, (A-B)/(A+B) 等）。"
               "\n何时用：『NDVI = (NIR-Red)/(NIR+Red)』『DEM 差值 = A-B』『比值指数』；"
               "任意两个栅格的逐像素运算。"
               "\n何时不用：(1) 单栅格重分类 — 用 raster_reclassify；"
               "(2) 双时相变化检测（差值+阈值分类一步到位）— 用 detect_raster_change；"
               "(3) 需要地理加权（如 focal）— 未实现，先用 focal_stats。"
               "\n关键约束：expression 用 A/B 指代栅格；支持 numexpr 语法；自动 nodata 掩膜。"
               "A 是基准网格：分辨率/CRS 不一致的 B 会自动虚拟重投影对齐到 A（连续量 bilinear），"
               "对齐事实进 quality_evidence。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterCalculatorArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("栅格计算", "raster calculator", "ndvi", "像元运算", "差值"),
           output_semantic_type="ref",
           result_size_policy="inline_small",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def raster_calculator(raster_a: str, raster_b: Optional[str] = None, expression: str = "A + B", constant: Optional[float] = None, nodata: Optional[float] = None, resampling: Optional[str] = None) -> dict:
        res = SpatialAnalyzer.raster_calculator(raster_a, raster_b, expression, constant, nodata, resampling)
        return res.to_llm_response()

    @tool(registry, name="detect_raster_change",
           description=(
               "双时相栅格变化检测：对本地两个栅格工件（如两期 NDVI/分类/DEM 产物）"
               "做像元级变化检测，输出变化栅格 + 统计 + 质量证据。"
               "\n何时用：『对比这两个时期的 NDVI 栅格哪里变了』『两期分类图差异』；"
               "已上传/已生成两个 .tif 工件的变化分析。"
               "\n何时不用：(1) 无本地栅格、只有 bbox+日期 — 用 detect_vegetation_change（在线 STAC）；"
               "(2) 只要差值统计不要栅格 — temporal_raster；"
               "(3) 两个栅格做任意表达式运算 — raster_calculator。"
               "\n关键约束：raster_a（T1）是基准网格；B 分辨率/CRS 不一致时自动虚拟对齐"
               "（对齐/重采样/裁剪事实进 quality_evidence）；method 可选 "
               "difference/absolute_difference/normalized_difference；threshold 给定时"
               "输出二分类变化栅格（1=变化，0=稳定，255=nodata）；分类图输入时"
               "传 resampling=nearest（默认 bilinear 仅适用连续量）。"
           ),
           tier=2, domains=["raster"],
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("变化检测", "双时相", "ndvi变化", "raster change", "差异"),
           output_semantic_type="ref",
           result_size_policy="inline_small",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def detect_raster_change(raster_a: str, raster_b: str, method: str = "difference", threshold: Optional[float] = None, band: int = 1, resampling: Optional[str] = None) -> dict:
        res = SpatialAnalyzer.raster_change(raster_a, raster_b, method=method, threshold=threshold, band=band, resampling=resampling)
        return res.to_llm_response()

    @tool(registry, name="raster_resample",
    anti_examples=("重分类分级（用 raster_reclassify）",),
           description=(
               "栅格重采样：改变像元大小和/或 CRS。"
               "\n何时用：『把 30m DEM 重采样到 90m 做概览』『把 WGS84 栅格转到 UTM 做面积计算』；"
               "不同分辨率/投影的栅格对齐前预处理。"
               "\n何时不用：(1) 只改元数据 — 用 gdal_translate 更快；"
               "(2) 已经同分辨率同 CRS — 不需要重采样。"
               "\n关键约束：resampling 可选 bilinear/nearest/cubic/mode/average。"
           ),
           tier=2, domains=["raster"],
           args_model=RasterResampleArgs,
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="slow",
           memory_class="heavy",
           scale_class="large",
           tags=("重采样", "resample", "分辨率", "投影转换", "栅格"),
           output_semantic_type="ref",
           result_size_policy="inline_small",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def raster_resample(raster_path: str, target_resolution: float, target_crs: Optional[str] = None, resampling: str = "bilinear") -> dict:
        res = SpatialAnalyzer.raster_resample(raster_path, target_resolution, target_crs, resampling)
        return res.to_llm_response()
