"""Three-dimensional terrain measurement tool registration."""

import json
import math
from typing import Any, Dict, List, Tuple


from app.tools.registry import ToolRegistry, tool

from app.lib.geo_analysis import measurements as measurements_lib
from app.lib.geo_analysis import terrain as terrain_lib
from app.lib.geo_analysis import viewshed_geometry as viewshed_geometry_lib
from app.lib.geo_analysis import volumes as volumes_lib
from app.lib.gis.crs_safety import classify_crs
from app.lib.gis.parameter_contracts import apply_contract
from app.lib.gis.scientific_evidence import Diagnostic

from app.tools._terrain_support import (
    _terrain_evidence,
    _read_terrain_window,
    _metric_cell_sizes,
    _non_metric_warning,
    _base_diagnostics,
)

def _register_measurement_tools(registry: ToolRegistry) -> None:
    """G3D03（ADR-0274）：三维量测工具族（剖面/LOS/地表距离/地表面积）。"""

    def _parse_point_seq(
        raw: Any, *, name: str, min_points: int, max_points: int
    ) -> List[Tuple[float, float]]:
        """[[x, y], ...]（内联数组或 JSON 文本）→ 校验后的 (x, y) 列表。"""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{name}: not valid JSON ({exc}) — expected '[[x, y], ...]'"
                ) from exc
        if not isinstance(raw, (list, tuple)) or len(raw) < min_points:
            raise ValueError(
                f"{name}: expected a list of at least {min_points} "
                f"[x, y] pairs (got {type(raw).__name__}, len "
                f"{len(raw) if isinstance(raw, (list, tuple)) else '?'})"
            )
        if len(raw) > max_points:
            raise ValueError(f"{name}: too many points ({len(raw)} > {max_points})")
        pts: List[Tuple[float, float]] = []
        for i, v in enumerate(raw):
            if not isinstance(v, (list, tuple)) or len(v) < 2:
                raise ValueError(f"{name}[{i}]: expected [x, y]")
            x, y = float(v[0]), float(v[1])
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError(f"{name}[{i}]: non-finite coordinates")
            pts.append((x, y))
        return pts

    def _measurement_transformations(
        base: List[str],
        cy: float,
        cx: float,
        m_meta: dict,
    ) -> List[str]:
        """米制像元换算（cos(lat)）+ 垂直单位换算的披露合成。"""
        out = list(base)
        out.append(
            f"metric cell sizes: cy={cy:.6f} m, cx={cx:.6f} m"
            + (" (geographic CRS: cos-lat policy)" if "cos(" in " ".join(base) else "")
        )
        out.extend(m_meta.get("z_unit_transformations", []))
        return out

    @tool(
        registry,
        name="terrain_profile",
        description=(
            "DEM 地形剖面：沿折线 bilinear 采样（水平距离/高程/逐段坡度/累计爬升与下降），"
            "返回统计 + 3D LineString GeoJSON（ref 化挂图）+ 科学证据。"
            "\n何时用：徒步/管线/道路纵断面、两地点间高差与爬升评估、剖面图数据。"
            "\n何时不用：(1) 可见性 — line_of_sight_analysis；(2) 距离汇总 — surface_distance_analysis。"
            "\n关键约束：采样步长默认 ~1 像元；nodata_policy=error 遇洞类型化失败、skip 剔除并披露；"
            "垂直基准由声明参数显式化（GeoTIFF 无 z 元数据）。"
        ),
        tier=2,
        domains=["raster"],
        cost="light",
        param_descriptions={
            "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
            "vertices": "折线顶点 [[x, y], ...]（栅格 CRS 单位；JSON 文本或内联数组，2-256 点）",
            "sampling_step_m": "采样步长（米；默认 ~1 像元）",
            "nodata_policy": "nodata 策略：error（默认）| skip",
            "vertical_datum": "垂直基准声明（默认 unknown，输出披露）",
            "z_units": "DEM 高程单位：m（默认）| ft",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="heavy",
        scale_class="large",
        tags=("剖面", "纵断面", "profile", "三维量测", "高差"),
        output_semantic_type="geojson_fc",
        result_size_policy="bounded",
        crs_semantics="crs_agnostic",
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
    def terrain_profile(
        raster_path: str,
        vertices: list | str,
        sampling_step_m: float | None = None,
        nodata_policy: str = "error",
        vertical_datum: str = "unknown",
        z_units: str = "m",
    ) -> dict:
        contract_params: Dict[str, Any] = {
            "nodata_policy": nodata_policy,
            "vertical_datum": vertical_datum,
            "z_units": z_units,
        }
        if sampling_step_m is not None:
            contract_params["sampling_step_m"] = sampling_step_m
        params = apply_contract("terrain_profile", contract_params)
        verts = _parse_point_seq(
            vertices, name="vertices", min_points=2, max_points=256
        )
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            raster_path, None
        )
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        prof, meta = measurements_lib.terrain_profile(
            arr,
            cy,
            cell_size_x=cx,
            vertices=verts,
            transform=transform,
            sampling_step_m=params.get("sampling_step_m"),
            nodata=eff_nodata,
            nodata_policy=str(params["nodata_policy"]),
            z_units=str(params["z_units"]),
            vertical_datum=str(params["vertical_datum"]),
        )
        coords: List[List[float]] = []
        for p in prof["points"]:
            if p["z_m"] is None:
                continue
            coords.append([p["x"], p["y"], p["z_m"]])
        # gaps 按全采样口径（meta.invalid_sample_count），不随输出抽稀低估
        gaps = int(meta["invalid_sample_count"])
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "terrain_profile",
                        "sampling_step_m": meta["sampling_step_requested_m"],
                        "nodata_gaps": gaps,
                        "vertical_datum": meta["vertical_datum"],
                        **{k: v for k, v in prof["statistics"].items()},
                    },
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            ],
        }
        payload: Dict[str, Any] = {
            "success": True,
            **prof["statistics"],
            "sample_count": meta["sample_count"],
            "output_point_count": meta["output_point_count"],
            "nodata_gaps": gaps,
            "quality_flags": meta["quality_flags"],
            "geojson": fc,
            "meta": meta,
        }
        # 剖面图 artifact 种子（G3D-V2-C / ADR-0276）：dispatch 缝认
        # profile_chart 键 → 铸 ref:profile-* + upsert elevation_profile
        # 组件（chart-artifact 工件通道）。面板可用性优先于逐点保真：
        # ≤512 点均匀抽稀（z_m 恒为米）；统计与 GeoJSON 仍按全采样口径。
        _chart_stride = max(1, math.ceil(len(prof["points"]) / 512))
        _chart_slice = prof["points"][::_chart_stride]
        # review G6：抽稀不丢末端点（线路终点是剖面语义的一部分）
        if _chart_stride > 1 and prof["points"][-1] not in _chart_slice:
            _chart_slice = _chart_slice + [prof["points"][-1]]
        _chart_points = [
            {
                "name": (
                    f"{p['distance_m'] / 1000:.2f}km"
                    if p["distance_m"] >= 1000
                    else f"{p['distance_m']:.0f}m"
                ),
                "value": p["z_m"],
            }
            for p in _chart_slice
            if p["z_m"] is not None
        ]
        if len(_chart_points) >= 2:
            payload["profile_chart"] = {
                "chart": {
                    "type": "line",
                    "title": "高程剖面",
                    "data": _chart_points,
                    "x_label": "线路里程",
                    "y_label": "高程（m）",
                }
            }
            meta["profile_chart_points"] = len(_chart_points)
            if _chart_stride > 1:
                meta["quality_flags"] = list(meta["quality_flags"]) + [
                    f"profile_chart_downsampled:stride={_chart_stride}"
                ]
        return _terrain_evidence(
            payload,
            "terrain.profile",
            tool="terrain_profile",
            parameters_applied={
                "n_vertices": len(verts),
                "sampling_step_m": meta["sampling_step_requested_m"],
                "nodata_policy": str(params["nodata_policy"]),
                "vertical_datum": str(params["vertical_datum"]),
                "z_units": str(params["z_units"]),
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform,
                arr.shape[0],
                arr.shape[1],
                extra=(
                    Diagnostic(name="sample_count", value=float(meta["sample_count"])),
                ),
            ),
            transformations=_measurement_transformations(transformations, cy, cx, meta),
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(
        registry,
        name="line_of_sight_analysis",
        description=(
            "DEM 两点通视线（LOS）：可见性判定 + 首遮挡点 + 视线/地形双剖面，"
            "地球曲率与大气折射可选（默认平面视线）；返回统计 + GeoJSON + 科学证据。"
            "\n何时用：瞭望塔/监控选址的点到点通视、天线/视距链路评估、遮挡诊断。"
            "\n何时不用：(1) 一点的可见范围 — viewshed_analysis；(2) 沿线高程 — terrain_profile。"
            "\n关键约束：遮挡 = 间隙 < -1e-9 m（相切记可见）；绝对高程覆盖值（observer_z/target_z）"
            "须与 DEM 同垂直基准声明，混算类型化拒绝；几何视线对称：角色互换（连同各自高度）"
            "判定不变；高度本身改变视线与可见性。"
        ),
        tier=2,
        domains=["raster"],
        cost="light",
        param_descriptions={
            "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
            "observer_x": "观察点世界 x（栅格 CRS 单位）",
            "observer_y": "观察点世界 y（栅格 CRS 单位）",
            "target_x": "目标点世界 x",
            "target_y": "目标点世界 y",
            "observer_height": "观察者离地高度（米，默认 2）",
            "target_height": "目标离地高度（米，默认 0）",
            "observer_z": "观察点绝对高程覆盖（DEM z_units；可选）",
            "target_z": "目标点绝对高程覆盖（DEM z_units；可选）",
            "override_vertical_datum": "覆盖值的垂直基准声明（缺省继承 DEM 声明；不同即拒绝）",
            "curvature": "曲率模型：none（默认）| earth | earth_refracted（k=0.1307）",
            "sampling_step_m": "视线采样步长（米；默认 ~1 像元）",
            "nodata_policy": "nodata 策略：error（默认）| skip",
            "vertical_datum": "DEM 垂直基准声明（默认 unknown）",
            "z_units": "DEM 高程单位：m（默认）| ft",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="heavy",
        scale_class="large",
        tags=("通视", "视线", "LOS", "遮挡", "三维量测"),
        output_semantic_type="geojson_fc",
        result_size_policy="bounded",
        crs_semantics="crs_agnostic",
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
    def line_of_sight_analysis(
        raster_path: str,
        observer_x: float,
        observer_y: float,
        target_x: float,
        target_y: float,
        observer_height: float = 2,
        target_height: float = 0,
        observer_z: float | None = None,
        target_z: float | None = None,
        override_vertical_datum: str | None = None,
        curvature: str = "none",
        sampling_step_m: float | None = None,
        nodata_policy: str = "error",
        vertical_datum: str = "unknown",
        z_units: str = "m",
    ) -> dict:
        contract_params: Dict[str, Any] = {
            "observer_x": observer_x,
            "observer_y": observer_y,
            "target_x": target_x,
            "target_y": target_y,
            "observer_height": observer_height,
            "target_height": target_height,
            "curvature": curvature,
            "nodata_policy": nodata_policy,
            "vertical_datum": vertical_datum,
            "z_units": z_units,
        }
        for opt in (
            "observer_z",
            "target_z",
            "override_vertical_datum",
            "sampling_step_m",
        ):
            if locals()[opt] is not None:
                contract_params[opt] = locals()[opt]
        params = apply_contract("line_of_sight_analysis", contract_params)
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            raster_path, None
        )
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        los, meta = measurements_lib.line_of_sight(
            arr,
            cy,
            cell_size_x=cx,
            observer_xy=(float(params["observer_x"]), float(params["observer_y"])),
            target_xy=(float(params["target_x"]), float(params["target_y"])),
            transform=transform,
            observer_height=float(params["observer_height"]),
            target_height=float(params["target_height"]),
            observer_z=(
                None
                if params.get("observer_z") is None
                else float(params["observer_z"])
            ),
            target_z=(
                None if params.get("target_z") is None else float(params["target_z"])
            ),
            override_vertical_datum=(
                None
                if params.get("override_vertical_datum") is None
                else str(params["override_vertical_datum"])
            ),
            curvature=str(params["curvature"]),
            sampling_step_m=params.get("sampling_step_m"),
            nodata=eff_nodata,
            nodata_policy=str(params["nodata_policy"]),
            z_units=str(params["z_units"]),
            vertical_datum=str(params["vertical_datum"]),
        )
        stats = los["statistics"]
        features: List[Dict[str, Any]] = []
        sight_coords = [
            [p["x"], p["y"], p["z_m"]]
            for p in los["sight_profile"]
            if p["z_m"] is not None
        ]
        terrain_coords: List[List[float]] = []
        terrain_gaps = 0
        for p in los["terrain_profile"]:
            if p["z_m"] is None:
                terrain_gaps += 1
                continue
            terrain_coords.append([p["x"], p["y"], p["z_m"]])
        if sight_coords:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"kind": "sight_line", "visible": stats["visible"]},
                    "geometry": {"type": "LineString", "coordinates": sight_coords},
                }
            )
        if terrain_coords:
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "terrain_profile",
                        "nodata_gaps": terrain_gaps,
                    },
                    "geometry": {"type": "LineString", "coordinates": terrain_coords},
                }
            )
        if stats["obstruction"] is not None:
            ob = stats["obstruction"]
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "obstruction",
                        "clearance_m": ob["clearance_m"],
                        "distance_from_observer_m": ob["distance_from_observer_m"],
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [ob["x"], ob["y"], ob["terrain_z_m"]],
                    },
                }
            )
        fc = {"type": "FeatureCollection", "features": features}
        payload: Dict[str, Any] = {
            "success": True,
            **{k: v for k, v in stats.items() if k != "obstruction"},
            "obstruction": stats["obstruction"],
            "sample_count": meta["sample_count"],
            "curvature_model": meta["curvature"]["model"],
            "quality_flags": meta["quality_flags"],
            "geojson": fc,
            "meta": meta,
        }
        return _terrain_evidence(
            payload,
            "terrain.line_of_sight",
            tool="line_of_sight_analysis",
            parameters_applied={
                "observer": [float(params["observer_x"]), float(params["observer_y"])],
                "target": [float(params["target_x"]), float(params["target_y"])],
                "observer_height": float(params["observer_height"]),
                "target_height": float(params["target_height"]),
                "observer_z": params.get("observer_z"),
                "target_z": params.get("target_z"),
                "curvature": str(params["curvature"]),
                "sampling_step_m": meta["sampling_step_requested_m"],
                "nodata_policy": str(params["nodata_policy"]),
                "vertical_datum": str(params["vertical_datum"]),
                "z_units": str(params["z_units"]),
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform,
                arr.shape[0],
                arr.shape[1],
                extra=(
                    Diagnostic(name="sample_count", value=float(meta["sample_count"])),
                    Diagnostic(
                        name="min_clearance_m",
                        value=(
                            stats["min_clearance_m"]
                            if stats["min_clearance_m"] is not None
                            else None
                        ),
                    ),
                ),
            ),
            transformations=_measurement_transformations(transformations, cy, cx, meta),
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(
        registry,
        name="surface_distance_analysis",
        description=(
            "DEM 地表距离量测：同一折线的水平（网格米制）/ 三维直线斜距 / 地表（沿面采样）距离"
            " + 高差 + 累计爬升/下降 + 半步收敛指示；地理 CRS 额外报告椭球测地距离（参考量）。"
            "\n何时用：山脊两点地表距离、路线真实长度（vs 平面距离）评估。"
            "\n何时不用：(1) 高程剖面细节 — terrain_profile；(2) 面积 — surface_area_analysis。"
            "\n关键约束：surface ≥ covered_horizontal 无条件成立；surface ≥ horizontal 仅在无跨洞剔除时成立；"
            "地表距离为采样近似（收敛指示披露，非解析误差界）。"
        ),
        tier=2,
        domains=["raster"],
        cost="light",
        param_descriptions={
            "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
            "vertices": "折线顶点 [[x, y], ...]（栅格 CRS 单位；JSON 文本或内联数组，2-256 点）",
            "sampling_step_m": "采样步长（米；默认 ~1 像元）",
            "nodata_policy": "nodata 策略：error（默认）| skip",
            "vertical_datum": "垂直基准声明（默认 unknown）",
            "z_units": "DEM 高程单位：m（默认）| ft",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="heavy",
        scale_class="large",
        tags=("地表距离", "坡面距离", "斜距", "三维量测", "距离"),
        output_semantic_type="stats",
        result_size_policy="inline_small",
        crs_semantics="crs_agnostic",
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
    def surface_distance_analysis(
        raster_path: str,
        vertices: list | str,
        sampling_step_m: float | None = None,
        nodata_policy: str = "error",
        vertical_datum: str = "unknown",
        z_units: str = "m",
    ) -> dict:
        contract_params: Dict[str, Any] = {
            "nodata_policy": nodata_policy,
            "vertical_datum": vertical_datum,
            "z_units": z_units,
        }
        if sampling_step_m is not None:
            contract_params["sampling_step_m"] = sampling_step_m
        params = apply_contract("surface_distance_analysis", contract_params)
        verts = _parse_point_seq(
            vertices, name="vertices", min_points=2, max_points=256
        )
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            raster_path, None
        )
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        stats, meta = measurements_lib.surface_distance(
            arr,
            cy,
            cell_size_x=cx,
            vertices=verts,
            transform=transform,
            sampling_step_m=params.get("sampling_step_m"),
            nodata=eff_nodata,
            nodata_policy=str(params["nodata_policy"]),
            z_units=str(params["z_units"]),
            vertical_datum=str(params["vertical_datum"]),
            include_samples=True,
        )
        # 地理 CRS：独立椭球测地距离（参考量；不参与网格空间内部一致性）
        geodesic_horizontal_m = None
        geodesic_note = None
        if classify_crs(crs) == "geographic":
            try:
                from pyproj import CRS as ProjCRS
                from pyproj import Geod

                pcrs = ProjCRS.from_user_input(crs)
                geod = pcrs.get_geod()
                if geod is None:
                    geod = Geod(
                        a=pcrs.ellipsoid.semi_major_metre,
                        b=pcrs.ellipsoid.semi_minor_metre,
                    )
                total = 0.0
                for (x1, y1), (x2, y2) in zip(verts, verts[1:]):
                    _, _, seg = geod.inv(x1, y1, x2, y2)
                    total += seg
                geodesic_horizontal_m = round(total, 4)
                geodesic_note = (
                    "geodesic_horizontal_m uses the ellipsoid (pyproj Geod) "
                    "independently; grid-metric horizontal_m keeps internal "
                    "consistency with surface stats (cos-lat policy)"
                )
            except Exception:  # noqa: BLE001 — 参考量是 additive，不阻塞主结果
                geodesic_note = "geodesic distance unavailable (CRS not Geod-parseable)"
        # 单次采样：GeoJSON 复用 stats 计算的采样点（meta.sample_points，
        # ≤MAX_OUTPUT_SAMPLES 抽稀），不再二次跑 terrain_profile
        coords = [
            [p["x"], p["y"], p["z_m"]]
            for p in meta["sample_points"]
            if p["z_m"] is not None
        ]
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "surface_distance",
                        "sampling_step_m": meta["sampling_step_requested_m"],
                        **{k: v for k, v in stats.items()},
                    },
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            ],
        }
        # sample_points 只服务 FC 装配（几何由 geojson/ref 承载），
        # 不随 meta 进 payload —— 防止数千点膨胀 LLM 载荷。
        meta_public = {k: v for k, v in meta.items() if k != "sample_points"}
        payload: Dict[str, Any] = {
            "success": True,
            **stats,
            "geodesic_horizontal_m": geodesic_horizontal_m,
            "sample_count": meta["sample_count"],
            "convergence": meta["convergence"],
            "quality_flags": meta["quality_flags"],
            "geojson": fc,
            "meta": meta_public,
        }
        if geodesic_note:
            payload["meta"] = dict(meta_public)
            payload["meta"]["geodesic_note"] = geodesic_note
        return _terrain_evidence(
            payload,
            "terrain.surface_distance",
            tool="surface_distance_analysis",
            parameters_applied={
                "n_vertices": len(verts),
                "sampling_step_m": meta["sampling_step_requested_m"],
                "nodata_policy": str(params["nodata_policy"]),
                "vertical_datum": str(params["vertical_datum"]),
                "z_units": str(params["z_units"]),
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform,
                arr.shape[0],
                arr.shape[1],
                extra=(
                    Diagnostic(name="sample_count", value=float(meta["sample_count"])),
                    Diagnostic(
                        name="surface_distance_m", value=stats["surface_distance_m"]
                    ),
                ),
            ),
            transformations=_measurement_transformations(transformations, cy, cx, meta),
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(
        registry,
        name="surface_area_analysis",
        description=(
            "DEM 真实地表面积：多边形内相邻像元中心两三角面 3D 面积和 vs 投影面积"
            "（粗糙比 ≥1）；nodata quad 剔除并披露，超阈值类型化失败。"
            "\n何时用：坡地真实面积（vs 平面投影）、地表粗糙度评估、生态/工程量测。"
            "\n何时不用：(1) 平面多边形面积（不需 DEM）— 几何工具；(2) 体积 — 不在本工具。"
            "\n关键约束：最外圈像元中心不构成 facet（对偶网格口径）；平面全域精确。"
        ),
        tier=2,
        domains=["raster"],
        cost="light",
        param_descriptions={
            "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
            "polygon": "多边形顶点 [[x, y], ...]（栅格 CRS 单位；JSON 文本或内联数组，3-1024 点）",
            "max_invalid_fraction": "quad 无效比例上限（默认 0.5，超过类型化失败）",
            "vertical_datum": "垂直基准声明（默认 unknown）",
            "z_units": "DEM 高程单位：m（默认）| ft",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="heavy",
        scale_class="large",
        tags=("地表面积", "真实面积", "粗糙比", "三维量测", "面积"),
        output_semantic_type="stats",
        result_size_policy="inline_small",
        crs_semantics="crs_agnostic",
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
    def surface_area_analysis(
        raster_path: str,
        polygon: list | str,
        max_invalid_fraction: float = 0.5,
        vertical_datum: str = "unknown",
        z_units: str = "m",
    ) -> dict:
        params = apply_contract(
            "surface_area_analysis",
            {
                "max_invalid_fraction": max_invalid_fraction,
                "vertical_datum": vertical_datum,
                "z_units": z_units,
            },
        )
        poly = _parse_point_seq(polygon, name="polygon", min_points=3, max_points=1024)
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            raster_path, None
        )
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        # 多边形 → 像元中心 mask（rasterio.geometry_mask，中心点判定）
        import rasterio.transform as rio_transform
        from rasterio.features import geometry_mask

        poly_ring = [[x, y] for x, y in poly]
        if poly_ring[0] != poly_ring[-1]:
            poly_ring.append(poly_ring[0])
        mask = geometry_mask(
            [{"type": "Polygon", "coordinates": [poly_ring]}],
            out_shape=(int(arr.shape[0]), int(arr.shape[1])),
            transform=rio_transform.Affine(*transform[:6]),
            invert=True,
        )
        stats, meta = measurements_lib.surface_area(
            arr,
            cy,
            cell_size_x=cx,
            mask=mask,
            nodata=eff_nodata,
            max_invalid_fraction=float(params["max_invalid_fraction"]),
            z_units=str(params["z_units"]),
            vertical_datum=str(params["vertical_datum"]),
        )
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "surface_area",
                        **{k: v for k, v in stats.items()},
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[list(p) for p in poly_ring]],
                    },
                }
            ],
        }
        payload: Dict[str, Any] = {
            "success": True,
            **stats,
            "quality_flags": meta["quality_flags"],
            "geojson": fc,
            "meta": meta,
        }
        return _terrain_evidence(
            payload,
            "terrain.surface_area",
            tool="surface_area_analysis",
            parameters_applied={
                "n_polygon_vertices": len(poly),
                "max_invalid_fraction": float(params["max_invalid_fraction"]),
                "vertical_datum": str(params["vertical_datum"]),
                "z_units": str(params["z_units"]),
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform,
                arr.shape[0],
                arr.shape[1],
                extra=(
                    Diagnostic(name="usable_quads", value=float(stats["usable_quads"])),
                    Diagnostic(name="roughness_ratio", value=stats["roughness_ratio"]),
                ),
            ),
            transformations=_measurement_transformations(transformations, cy, cx, meta),
            warnings=_non_metric_warning(crs) or None,
        )

    @tool(
        registry,
        name="cut_fill_volume_analysis",
        description=(
            "DEM 基准面土方量：多边形区域内挖方/填方/净方（cut/fill/net，m³），"
            "基准面 constant（绝对高程）或 mean（区域均值——平衡挖填）；"
            "返回统计 + 挖方/填方分区 GeoJSON（ref 化挂图）+ 科学证据。"
            "\n何时用：场平/基坑/道路土方平衡估算、以 DEM 为真的工程量初算。"
            "\n何时不用：(1) 面积 — surface_area_analysis；(2) 剖面 — terrain_profile；"
            "(3) 地质储量/分层开挖（需地质模型，本工具不做）。"
            "\n关键约束：像元中心中点求积（平面/斜面精确，曲面随分辨率收敛）；"
            "基准面 datum 与 DEM 不一致类型化拒绝；nodata 超阈类型化失败；"
            "边界半像元误差以 boundary_cells 披露。"
        ),
        tier=2,
        domains=["raster"],
        cost="light",
        param_descriptions={
            "raster_path": "DEM GeoTIFF 路径（data_dir 内）",
            "polygon": "区域多边形顶点 [[x, y], ...]（栅格 CRS 单位；JSON 文本或内联数组，3-1024 点）",
            "reference": "基准面模式：constant（默认，需 base_z）| mean（区域均值）",
            "base_z": "constant 模式的基准面绝对高程（DEM z_units）",
            "max_invalid_fraction": "区域内 nodata 像元比例上限（默认 0.5）",
            "vertical_datum": "DEM 垂直基准声明（默认 unknown）",
            "override_vertical_datum": "基准面高程的垂直基准声明（不同即拒绝）",
            "z_units": "DEM 高程单位：m（默认）| ft",
            "include_geometry": "输出挖方/填方分区 GeoJSON 面（默认 True）",
        },
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="heavy",
        scale_class="large",
        tags=("土方", "挖方", "填方", "体积", "三维量测", "cut", "fill"),
        output_semantic_type="geojson_fc",
        result_size_policy="bounded",
        crs_semantics="crs_agnostic",
        failure_modes=("invalid_args", "missing_data", "memory"),
    )
    def cut_fill_volume_analysis(
        raster_path: str,
        polygon: list | str,
        reference: str = "constant",
        base_z: float | None = None,
        max_invalid_fraction: float = 0.5,
        vertical_datum: str = "unknown",
        override_vertical_datum: str | None = None,
        z_units: str = "m",
        include_geometry: bool = True,
    ) -> dict:
        contract_params: Dict[str, Any] = {
            "reference": reference,
            "max_invalid_fraction": max_invalid_fraction,
            "vertical_datum": vertical_datum,
            "z_units": z_units,
        }
        if base_z is not None:
            contract_params["base_z"] = float(base_z)
        if override_vertical_datum is not None:
            contract_params["override_vertical_datum"] = override_vertical_datum
        contract_params["include_geometry"] = include_geometry
        params = apply_contract("cut_fill_volume_analysis", contract_params)
        poly = _parse_point_seq(
            polygon, name="polygon", min_points=3, max_points=1024
        )
        arr, transform, crs, eff_nodata, bounds = _read_terrain_window(
            raster_path, None
        )
        cy, cx, transformations = _metric_cell_sizes(crs, transform, bounds)
        import rasterio.transform as rio_transform
        from rasterio.features import geometry_mask

        poly_ring = [[x, y] for x, y in poly]
        if poly_ring[0] != poly_ring[-1]:
            poly_ring.append(poly_ring[0])
        mask = geometry_mask(
            [{"type": "Polygon", "coordinates": [poly_ring]}],
            out_shape=(int(arr.shape[0]), int(arr.shape[1])),
            transform=rio_transform.Affine(*transform[:6]),
            invert=True,
        )
        stats, meta = volumes_lib.cut_fill_volume(
            arr,
            cy,
            cell_size_x=cx,
            mask=mask,
            reference=str(params["reference"]),
            base_z=params.get("base_z"),
            nodata=eff_nodata,
            max_invalid_fraction=float(params["max_invalid_fraction"]),
            z_units=str(params["z_units"]),
            vertical_datum=str(params["vertical_datum"]),
            override_vertical_datum=params.get("override_vertical_datum"),
        )
        features: List[Dict[str, Any]] = [
            {
                "type": "Feature",
                "properties": {
                    "kind": "cut_fill_site",
                    **{k: v for k, v in stats.items()},
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[list(p) for p in poly_ring]],
                },
            }
        ]
        if bool(params["include_geometry"]):
            # 挖方/填方分区面：dz 符号掩膜 → cell-edge 对齐多边形
            # （与可视几何同一多边形化纪律）。换算因子/基准面取 meta
            # 单一真相（review C1：不自带 0.3048 第二份；base_z_m 未
            # 舍入——stats.base_z 是 6 位回显，分区判定不得与其漂移）。
            _z, valid = terrain_lib._prepare(arr, eff_nodata)
            z_to_m = float(meta["z_to_m"])
            z_m = _z * z_to_m
            in_region = mask & valid
            dz = z_m - float(meta["base_z_m"])
            for kind, region_mask in (
                ("cut_region", in_region & (dz > 0)),
                ("fill_region", in_region & (dz < 0)),
            ):
                region_fc, region_meta = viewshed_geometry_lib.raster_mask_polygons(
                    region_mask,
                    transform,
                    kind=kind,
                )
                for feat in region_fc["features"]:
                    feat["properties"]["zone"] = kind
                    features.append(feat)
                meta.setdefault("region_geometry", {})[kind] = {
                    k: v
                    for k, v in region_meta.items()
                    if k in ("polygon_count", "attributed_cells", "dropped_polygon_count")
                }
        fc = {"type": "FeatureCollection", "features": features}
        payload: Dict[str, Any] = {
            "success": True,
            **stats,
            "quality_flags": meta["quality_flags"],
            "geojson": fc,
            "meta": meta,
        }
        return _terrain_evidence(
            payload,
            "terrain.cut_fill_volume",
            tool="cut_fill_volume_analysis",
            parameters_applied={
                "n_polygon_vertices": len(poly),
                "reference": str(params["reference"]),
                "base_z": stats["base_z"],
                "max_invalid_fraction": float(params["max_invalid_fraction"]),
                "vertical_datum": str(params["vertical_datum"]),
                "override_vertical_datum": params.get("override_vertical_datum"),
                "z_units": str(params["z_units"]),
                "include_geometry": bool(params["include_geometry"]),
            },
            crs=crs,
            diagnostics=_base_diagnostics(
                transform,
                arr.shape[0],
                arr.shape[1],
                extra=(
                    Diagnostic(name="cut_volume_m3", value=float(stats["cut_volume_m3"])),
                    Diagnostic(name="fill_volume_m3", value=float(stats["fill_volume_m3"])),
                    Diagnostic(name="net_volume_m3", value=float(stats["net_volume_m3"])),
                ),
            ),
            transformations=_measurement_transformations(transformations, cy, cx, meta),
            warnings=_non_metric_warning(crs) or None,
        )
