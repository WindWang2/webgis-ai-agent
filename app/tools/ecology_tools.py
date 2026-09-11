"""生态分析工具面（Goal 07 Science V6）— register_ecology_tools。

薄包装职责：validate → apply_contract → 调 app/lib/geo_analysis/
ecology.py 纯函数 → 挂 scientific_evidence → 返回有界结果。
独立模块防并行分支冲突（与 sampling_tools 同理）。
"""
import json
import logging
from typing import Any, Optional

import numpy as np

from app.lib.geo_analysis.raster_math import rasterio_env
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.parameter_contracts import apply_contract
from app.lib.gis.scientific_evidence import build_evidence
from app.tools.registry import ToolRegistry, tool
from app.utils.path import validate_data_path

logger = logging.getLogger(__name__)


def _attach_scientific_evidence(
    payload: dict,
    algorithm_id: str,
    *,
    tool: str,
    parameters_applied: dict,
    feature_count: Optional[int],
    crs: str = "",
    diagnostics: Optional[list] = None,
) -> dict:
    """descriptor 驱动的证据块（假设/局限/出处唯一事实源）。"""
    descriptor = get_algorithm_registry().get(algorithm_id)
    if descriptor is None:
        logger.warning(
            "scientific evidence requested for unknown algorithm %s", algorithm_id)
        return payload
    input_facts = {"feature_count": feature_count}
    if crs:
        input_facts["crs"] = crs
    payload["scientific_evidence"] = build_evidence(
        descriptor,
        tool=tool,
        parameters_applied=parameters_applied,
        input_facts=input_facts,
        diagnostics=diagnostics,
    )
    return payload


def register_ecology_tools(registry: ToolRegistry):

    @tool(registry, name="habitat_suitability_analysis",
           description="生境适宜度指数（HSI，USFWS 1981）：按变量响应曲线"
                       "（trapezoid[a,b,c,d] / gaussian[mu,sigma]）给每个要素"
                       "评分，加权聚合为 0-1 适宜度并分级（unsuitable/marginal/"
                       "suitable/optimal）。geometric 聚合为限制因子语义",
           tier=2, domains=["what_if", "raster"], cost="light",
           param_descriptions={
               "geojson": "分析框 GeoJSON 或数据引用(ref:xxx)（要素属性含全部变量字段）",
               "variables_json": "变量定义 JSON 数组：[{field, curve: trapezoid|gaussian, params: [a,b,c,d]|[mu,sigma], weight}]",
               "aggregation": "聚合：arithmetic(默认，加权平均) / geometric(限制因子)",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="fast",
           memory_class="light",
           scale_class="medium",
           tags=("hsi", "生境适宜度", "栖息地", "生态", "保护区选址"),
           output_semantic_type="geojson_fc",
           result_size_policy="ref_offload",
           crs_semantics="crs_agnostic",
           failure_modes=("invalid_args", "missing_data"))
    def habitat_suitability_analysis(geojson: Any, variables_json: str,
                                     aggregation: str = "arithmetic") -> dict:
        from app.lib.geo_analysis.ecology import hsi_score_features

        try:
            variables = json.loads(variables_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"variables_json is not valid JSON: {exc}") from exc
        if not isinstance(variables, list) or not variables:
            raise ValueError(
                "variables_json must be a non-empty JSON array of variable specs")
        params = apply_contract("habitat_suitability_analysis", {
            "variables_json": variables_json,
            "aggregation": aggregation,
        })
        fc, meta = hsi_score_features(
            geojson, variables, aggregation=str(params["aggregation"]))
        payload = {
            "success": True,
            **fc,
            "summary": (
                f"生境适宜度（HSI）：{meta['n_features']} 个要素，均值 "
                f"{meta['hsi_mean']}；分级 optimal="
                f"{meta['hsi_class_counts']['optimal']}、suitable="
                f"{meta['hsi_class_counts']['suitable']}、marginal="
                f"{meta['hsi_class_counts']['marginal']}、unsuitable="
                f"{meta['hsi_class_counts']['unsuitable']}（{meta['aggregation']} "
                "聚合；曲线参数见 scientific_evidence）。"),
            "hsi_metadata": meta,
        }
        return _attach_scientific_evidence(
            payload, "ecology.habitat_suitability",
            tool="habitat_suitability_analysis",
            parameters_applied={
                "variables": meta["variables"],
                "aggregation": meta["aggregation"],
            },
            feature_count=meta["n_features"],
        )

    @tool(registry, name="landscape_metrics_analysis",
           description="景观格局指标（FRAGSTATS 口径 4 邻接）：分类栅格的类级"
                       "PLAND/斑块数/斑块密度/最大斑块指数/边缘密度与景观级"
                       "SHDI/SIDI/PR 多样性。破碎化与连通性诊断。类别 >256 先重分类",
           tier=2, domains=["raster"], cost="medium",
           param_descriptions={
               "raster_path": "分类栅格 GeoTIFF 路径（data_dir 内）",
               "nodata": "nodata 覆盖值（0=用栅格自带 nodata）",
           },
           side_effect="deterministic_compute",
           network=False,
           deterministic=True,
           latency_class="medium",
           memory_class="medium",
           scale_class="large",
           tags=("景观格局", "破碎化", "连通性", "shdi", "fragstats"),
           output_semantic_type="stats",
           result_size_policy="inline_small",
           crs_semantics="crs_agnostic",
           unit_semantics="meters",
           failure_modes=("invalid_args", "missing_data", "memory"))
    def landscape_metrics_analysis(raster_path: str, nodata: float = 0) -> dict:
        from app.lib.geo_analysis.ecology import landscape_metrics

        params = apply_contract("landscape_metrics_analysis", {
            "raster_path": raster_path,
            "nodata": nodata,
        })
        raster_path = str(params["raster_path"])
        nodata = float(params["nodata"])
        path = validate_data_path(raster_path)
        import rasterio
        from rasterio.errors import RasterioIOError

        # 读取前护栏（与 terrain 工具 _read_terrain_window 同闸）：
        # check_grid + 整读预算，先拒绝后分配。
        from app.lib.geo_analysis.raster_guard import RasterResourceGuard

        try:
            with rasterio_env():
                with rasterio.open(path) as src:
                    RasterResourceGuard.check_grid(
                        src.width, src.height, num_bands=src.count)
                    estimated = (int(src.width) * int(src.height)
                                 * (8 + int(np.dtype(src.dtypes[0]).itemsize)))
                    if estimated > int(
                            RasterResourceGuard.MAX_ESTIMATED_OUTPUT_BYTES):
                        raise ValueError(
                            f"landscape metrics full-read budget exceeded "
                            f"(~{estimated / 1024 ** 3:.2f} GiB); clip or "
                            "downsample first")
                    arr = src.read(1).astype("float64")
                    transform = tuple(float(v) for v in src.transform)[:6]
                    crs = str(src.crs) if src.crs is not None else ""
                    eff_nodata = (float(nodata) if nodata
                                  else (float(src.nodata)
                                        if src.nodata is not None else None))
        except RasterioIOError as exc:
            from app.lib.geo_raster.reader import RasterReaderError

            raise RasterReaderError(
                f"cannot open raster {path!r}: {exc}") from exc
        # 像元尺寸取 affine 对角并换算米制（地理 CRS 按 cos(lat) 政策，
        # 与 terrain 工具 _metric_cell_sizes 同口径）。
        cell_size = abs(float(transform[4]))
        cell_size_x = abs(float(transform[0]))
        if "4326" in crs or not crs:
            cell_size *= 111320.0
            cell_size_x *= 111320.0
        table, meta = landscape_metrics(
            arr, cell_size, cell_size_x=cell_size_x, nodata=eff_nodata)
        payload = {
            "success": True,
            "summary": (
                f"景观格局指标：{meta['n_classes']} 类，总面积 "
                f"{meta['total_area_ha']} ha；景观级 ED="
                f"{meta['edge_density_m_per_ha']} m/ha，SHDI={meta['shdi']}，"
                f"SIDI={meta['sidi']}，PR={meta['pr']}（4 邻接）。"),
            **table,
            "landscape_metadata": meta,
        }
        return _attach_scientific_evidence(
            payload, "ecology.landscape_metrics",
            tool="landscape_metrics_analysis",
            parameters_applied={
                "raster_path": str(raster_path),
                "nodata": float(nodata) if nodata else 0,
            },
            feature_count=None,
            crs=crs,
        )
