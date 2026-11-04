"""Hydrology V4 tool registration and bounded result projection."""

import math
from typing import Any, List, Tuple, Callable, Optional

import numpy as np

from app.tools.registry import ToolRegistry, tool

from app.lib.geo_analysis import terrain as terrain_lib
from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.parameter_contracts import apply_contract
from app.lib.gis.scientific_evidence import Diagnostic, build_evidence

from app.tools._terrain_support import (
    _terrain_evidence,
    _read_terrain_window,
    _metric_cell_sizes,
    _bounded_sample,
    _base_diagnostics,
    _persist_filled_dem,
    _persist_raster_product,
)

def _register_hydrology_v4_tool(
    registry: ToolRegistry,
    *,
    _load_dem: Callable[
        [str, Optional[float]],
        Tuple[np.ndarray, Tuple[float, ...], str, Optional[float],
              Tuple[float, ...], float, float, List[str]],
    ],
) -> None:
    """Science V4（W8/W9）：水文与地形分析 V4 合并入口（单工具分派）。"""

    @tool(
        registry,
        name="hydrology_v4_analysis",
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
        tier=2,
        domains=["raster"],
        cost="heavy",
        param_descriptions={
            "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
            "analysis": (
                "breach|hand|shreve|pfafstetter|"
                "pfafstetter_multilevel|flow_topology|hypsometry|solar_radiation"
            ),
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
        tags=(
            "水文",
            "breaching",
            "HAND",
            "shreve",
            "pfafstetter",
            "hypsometry",
            "solar",
        ),
        output_semantic_type="stats",
        result_size_policy="ref_offload",
        crs_semantics="crs_agnostic",
        side_effect="deterministic_compute",
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
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
        from app.lib.gis.parameter_contracts import apply_contract
        from app.lib.gis.scientific_errors import DegenerateData

        params = apply_contract(
            "hydrology_v4_analysis",
            {
                "raster_path": raster_path,
                "analysis": analysis,
                "stream_threshold": float(stream_threshold),
                "outlet_row": int(outlet_row),
                "outlet_col": int(outlet_col),
                "levels": int(levels),
                "latitude_deg": float(latitude_deg),
                "day_of_year": int(day_of_year),
                "transmissivity": float(transmissivity),
            },
        )
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
                f"analysis 必须是 {'|'.join(algo_map)} 之一，got {analysis!r}"
            )
        arr, transform, crs, eff_nodata, bounds, cy, cx, transformations = _load_dem(
            raster_path, nodata
        )

        if analysis == "breach":
            breached, meta = terrain_lib.breach_depressions(
                arr, cy, cell_size_x=cx, nodata=eff_nodata
            )
            result = {
                "summary": (
                    f"Breaching 完成：{meta['n_depressions']} 个洼地，切沟像元 "
                    f"{meta['breached_cells']}，开挖量 {meta['carved_volume']}；"
                    f"回退填洼像元 {meta['fallback_filled_cells']}。"
                ),
                "breach_metadata": meta,
            }
            # review R2-M4：切沟 DEM 是本分析的产出物 —— persist 供下游消费
            if persist_output:
                result["output_raster"] = _persist_filled_dem(
                    raster_path, breached, transform, crs, eff_nodata
                )
        elif analysis == "hand":
            hand_arr, meta = terrain_lib.hand(
                arr,
                cy,
                cell_size_x=cx,
                stream_threshold=float(stream_threshold),
                nodata=eff_nodata,
            )
            result = {
                "summary": (
                    f"HAND 完成：{meta['stream_cells']} 河网像元（阈值 "
                    f"{stream_threshold}）；HAND 范围 {meta['hand_range']}；"
                    f"未解析（无河网下排）像元 {meta['unresolvable_cells']}。"
                ),
                "hand_stats": meta,
                "hand_preview": np.nanpercentile(hand_arr, [5, 25, 50, 75, 95])
                .round(4)
                .tolist(),
            }
            if persist_output:
                result["output_raster"] = _persist_filled_dem(
                    raster_path, hand_arr, transform, crs, eff_nodata
                )
        elif analysis == "shreve":
            filled, _ = terrain_lib.fill_depressions(
                arr, cy, cell_size_x=cx, nodata=eff_nodata
            )
            d8, _ = terrain_lib.d8_flow(filled, cy, cell_size_x=cx, nodata=eff_nodata)
            acc, _ = terrain_lib.flow_accumulation(d8)
            mag, meta = terrain_lib.shreve_magnitude(d8, acc, float(stream_threshold))
            result = {
                "summary": (
                    f"Shreve 量级完成：{meta['stream_cells']} 河网像元，"
                    f"最大量级 {meta['max_magnitude']}。"
                ),
                "shreve_metadata": meta,
            }
        elif analysis == "pfafstetter":
            if outlet_row < 0 or outlet_col < 0:
                raise DegenerateData(
                    "analysis=pfafstetter 需要 outlet_row/outlet_col（出口像元）",
                    correction_hint="用 flow_analysis 的最大汇流像元作为出口",
                )
            filled, _ = terrain_lib.fill_depressions(
                arr, cy, cell_size_x=cx, nodata=eff_nodata
            )
            d8, _ = terrain_lib.d8_flow(filled, cy, cell_size_x=cx, nodata=eff_nodata)
            acc, _ = terrain_lib.flow_accumulation(d8)
            codes, meta = terrain_lib.pfafstetter_codes(
                d8, acc, float(stream_threshold), (int(outlet_row), int(outlet_col))
            )
            result = {
                "summary": (
                    f"Pfafstetter 编码完成：干流 {meta['mainstem_cells']} 像元，"
                    f"编码分布 {meta['code_distribution']}（单级层级，已披露）。"
                ),
                "pfafstetter_metadata": meta,
            }
        elif analysis == "pfafstetter_multilevel":
            if outlet_row < 0 or outlet_col < 0:
                raise DegenerateData(
                    "analysis=pfafstetter_multilevel 需要 outlet_row/outlet_col",
                    correction_hint="用 flow_analysis 的最大汇流像元作为出口",
                )
            filled, _ = terrain_lib.fill_depressions(
                arr, cy, cell_size_x=cx, nodata=eff_nodata
            )
            d8, _ = terrain_lib.d8_flow(filled, cy, cell_size_x=cx, nodata=eff_nodata)
            acc, _ = terrain_lib.flow_accumulation(d8)
            codes, meta = terrain_lib.pfafstetter_codes_multilevel(
                d8,
                acc,
                float(stream_threshold),
                (int(outlet_row), int(outlet_col)),
                levels=int(params.get("levels", 2)),
            )
            result = {
                "summary": (
                    f"多级 Pfafstetter 完成：{meta['levels']} 层、"
                    f"{meta['distinct_codes']} 个不同码、"
                    f"跳过细分段 {meta['skipped_segments']}（子段过小，已披露）。"
                ),
                "pfafstetter_metadata": meta,
            }
        elif analysis == "flow_topology":
            filled, _ = terrain_lib.fill_depressions(
                arr, cy, cell_size_x=cx, nodata=eff_nodata
            )
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
                    f"{report['equal_accumulation_plateaus']}。"
                ),
                "topology_report": report,
                "topology_metadata": meta,
            }
        elif analysis == "hypsometry":
            hyp, meta = terrain_lib.hypsometry(
                arr, cy, cell_size_x=cx, nodata=eff_nodata
            )
            # review R1-C2：meta["n_levels"] 是 int（非容器）；curve 在返回
            # 值 hyp（元组）而非 meta —— 此前双重 bug 工具路径必崩。
            n_levels = int(meta.get("n_levels", 0) or 0)
            result = {
                "summary": (
                    f"Hypsometry 完成：高程面积曲线 {n_levels} 级，"
                    f"高程积分 {meta['hypsometric_integral']}（矩形=1）。"
                ),
                "hypsometry": dict(meta),
                "curve_preview": {
                    "elevation_norm": [round(float(v), 4) for v in hyp[0][:24]],
                    "area_above_norm": [round(float(v), 4) for v in hyp[1][:24]],
                },
            }
        else:  # solar_radiation
            sol, meta = terrain_lib.solar_radiation(
                arr,
                cy,
                cell_size_x=cx,
                latitude_deg=latitude_deg,
                day_of_year=day_of_year,
                transmissivity=transmissivity,
                nodata=eff_nodata,
            )
            result = {
                "summary": (
                    f"Solar radiation 完成（lat={latitude_deg}°, DOY={day_of_year}，"
                    "晴空模型）：日辐照量范围 "
                    f"{meta['insolation_range']} MJ/m²。"
                ),
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
        transform: Tuple[float, ...],
        x: float,
        y: float,
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

    @tool(
        registry,
        name="cost_distance_analysis",
        description="累积成本面（最小成本距离）：在正摩擦栅格上从源点做 8 邻接 "
        "Dijkstra（边成本 = 平均摩擦 × 米距，Tobler 摩擦面语义），"
        "输出累积成本 GeoTIFF（下游 least_cost_path_analysis 直接消费）"
        "与可达/不可达诊断。nodata 像元不可通行",
        tier=2,
        domains=["raster", "network"],
        cost="heavy",
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
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
    def cost_distance_analysis(
        raster_path: str, sources_geojson: Any, nodata: float = 0
    ) -> dict:
        from app.lib.geo_processor.core import safe_parse, to_feature_collection
        from app.lib.geo_analysis.cost_surface import cost_distance

        params = apply_contract(
            "cost_distance_analysis",
            {
                "raster_path": raster_path,
                "sources_geojson": sources_geojson,
                "nodata": nodata,
            },
        )
        raster_path = str(params["raster_path"])
        sources_geojson = params["sources_geojson"]
        nodata = float(params["nodata"])
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            raster_path, nodata if nodata else None
        )
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
            arr, sources_rc, cy, cell_size_x=cx, nodata=eff_nodata
        )

        # 成本面独立产物（_costdist 后缀，不覆盖源 DEM 的 _filled 链产物）。
        surface_path = _persist_raster_product(
            raster_path,
            surface,
            transform,
            crs,
            "_costdist",
            nodata_value=eff_nodata,
        )

        payload = {
            "success": True,
            "summary": (
                f"累积成本面完成：{meta['reachable']} 像元可达（源 "
                f"{meta['n_source_cells']} 个），最大累积成本 "
                f"{meta['max_cost']}，不可达 {meta['unreachable']} 个"
                f"（无效像元 {meta['invalid_cells']}）。产物：{surface_path}"
            ),
            "cost_metadata": meta,
            "accumulated_raster_path": surface_path,
            "sample": _bounded_sample(surface),
        }
        return _terrain_evidence(
            payload,
            "terrain.cost_distance",
            tool="cost_distance_analysis",
            parameters_applied={
                "raster_path": str(raster_path),
                "n_sources": len(sources_rc),
                "nodata": float(nodata) if nodata else 0,
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform,
                h,
                w,
                extra=(
                    Diagnostic(
                        name="n_source_cells", value=float(meta["n_source_cells"])
                    ),
                    Diagnostic(name="reachable", value=float(meta["reachable"])),
                ),
            ),
            transformations=transformations or None,
        )

    @tool(
        registry,
        name="least_cost_path_analysis",
        description="最小成本路径：在 cost_distance_analysis 产出的累积成本面上，"
        "从目标像元沿严格下降方向回溯排水到源（GRASS r.drain 语义），"
        "输出 LineString 折线要素与路径总成本。输入必须是累积成本面",
        tier=2,
        domains=["raster", "network"],
        cost="light",
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
        failure_modes=("invalid_args", "missing_data"),
    )
    def least_cost_path_analysis(
        accumulated_raster_path: str, target_x: float, target_y: float
    ) -> dict:
        from app.lib.geo_analysis.cost_surface import least_cost_path

        params = apply_contract(
            "least_cost_path_analysis",
            {
                "accumulated_raster_path": accumulated_raster_path,
                "target_x": target_x,
                "target_y": target_y,
            },
        )
        accumulated_raster_path = str(params["accumulated_raster_path"])
        target_x = float(params["target_x"])
        target_y = float(params["target_y"])
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            accumulated_raster_path, None
        )
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
                f"{meta['source']}）。"
            ),
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "total_cost": meta["total_cost"],
                        "n_cells": meta["n_cells"],
                    },
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            ],
            "path_metadata": meta,
        }
        return _terrain_evidence(
            payload,
            "terrain.least_cost_path",
            tool="least_cost_path_analysis",
            parameters_applied={
                "accumulated_raster_path": str(accumulated_raster_path),
                "target": [row, col],
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform,
                arr.shape[0],
                arr.shape[1],
                extra=(
                    Diagnostic(name="path_cells", value=float(meta["n_cells"])),
                    Diagnostic(name="total_cost", value=float(meta["total_cost"])),
                ),
            ),
        )
