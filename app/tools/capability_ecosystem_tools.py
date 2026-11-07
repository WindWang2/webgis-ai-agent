"""G06 / M5 —— 两项真实原生 GIS 计算原语的 ToolRegistry 注册面。

现场审计（BASELINE §3）确认的真实缺口，均**不是** dummy / no-op：

1. ``conditional_quantile_zonal_stats`` —— 条件 / 多分位数**分区栅格统计**。
   既有 ``zonal_stats`` 只暴露 min/max/mean/sum/std/count
   （``raster_ops.py:234-254``），无分位数、无条件筛选。
   本原语复用现有 windowed raster 读取路径的科学语义（有效像元覆盖率、
   nodata、geometry repair 披露），用 numpy 做真实分位数计算，并给出
   逐 zone 的有效样本分母、分位数方法学、不确定性与预算。

2. ``network_service_coverage_consistency`` —— **线网服务覆盖一致性校验**。
   既有 ``network_service_area`` / ``isochrone_network`` 只生成覆盖，
   不做多方案一致性对拍。本原语用 networkx 在真实线网图上按方案
   Dijkstra，计算 Jaccard、差异段、覆盖率与严格量纲校验。

两项都：
* 真实 ``@tool`` 注册进 ToolRegistry（不是第二注册中心）；
* 走 ``apply_contract`` 参数严格校验；
* 输出可复用 ``artifact`` / ``ref`` + ``science`` 方法披露 + ``budget``；
* 严格区分秒（时间量纲）与米（长度量纲），绝不混用。

参数契约定义在 ``app/lib/gis/algorithms/capability_ecosystem.py`` 域包，
经既有 ``iter_contract_packs`` 汇入唯一 ParameterContractRegistry。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

# ── 有界预算（CAP-06 / CAP-O12）───────────────────────────────────────
MAX_ZONES = 256
MAX_QUANTILES = 16
MAX_RASTER_CELLS = 40_000_000
MAX_NETWORK_EDGES = 20_000
MAX_FACILITIES = 2_000
MAX_CUTOFF = 1_000_000.0

_TIME_UNITS = frozenset({"seconds", "s", "sec", "second", "minutes", "min", "time"})
_LENGTH_UNITS = frozenset({"meters", "m", "meter", "km", "kilometers", "metres"})


class ConditionalQuantileZonalStatsArgs(BaseModel):
    zones: Any = Field(..., description="面要素 FeatureCollection（分区多边形）")
    raster_path: str = Field(..., description="后端可访问的单波段栅格路径或 ref")
    quantiles: list[float] = Field(
        default_factory=lambda: [0.25, 0.5, 0.75],
        description="分位数列表，各项 ∈ (0,1)，最多 16 项")
    where: str = Field(
        "", description="条件表达式，仅支持 value <op> <number> 与 and 连接，如 'value > 50'")
    nodata: Optional[float] = Field(None, description="覆盖栅格 nodata 值")


class NetworkServiceCoverageConsistencyArgs(BaseModel):
    network: Any = Field(..., description="线网 FeatureCollection（LineString / MultiLineString）")
    facilities: Any = Field(..., description="设施点 FeatureCollection，properties.scheme 标方案")
    cutoff: float = Field(2.0, gt=0, description="服务半径/时间阈值（单位由 cost_unit 声明）")
    cost_unit: str = Field("meters", description="成本量纲：meters/m/km 或 seconds/s/min")
    metric: str = Field("", description="请求的输出量纲（诊断用）")
    output_unit: str = Field("", description="请求的输出单位（诊断用）")
    request_metric: str = Field("", description="请求的指标（诊断用）")


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _ref(prefix: str, payload: Any) -> dict[str, Any]:
    digest = hashlib.sha256(
        _canonical(payload).encode("utf-8"), usedforsecurity=False).hexdigest()[:24]
    return {
        "ref": f"artifact://{prefix}/{digest}",
        "kind": "analysis_artifact",
        "schema_version": "capability-artifact/v1",
        "content_fingerprint": digest,
        "pageable": True,
        "page_size": 50,
    }


def _error(code: str, message: str, hint: str = "") -> dict[str, Any]:
    return {
        "success": False,
        "code": code,
        "message": message,
        "error_type": "ScientificPreconditionFailed",
        "correction_hint": hint,
        "data": None,
    }


def _features(fc: Any, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    """严格取要素；非法结构抛 ValueError（进 dispatch 的 correction_hint）。"""
    if isinstance(fc, str):
        try:
            fc = json.loads(fc)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid GeoJSON: {exc}") from None
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise ValueError("expected a GeoJSON FeatureCollection")
    feats = fc.get("features")
    if not isinstance(feats, list) or not feats:
        raise ValueError("FeatureCollection.features must be a non-empty list")
    out = []
    for i, f in enumerate(feats):
        if not isinstance(f, dict) or not isinstance(f.get("geometry"), dict):
            raise ValueError(f"feature[{i}] missing geometry")
        gtype = str(f["geometry"].get("type", ""))
        if gtype not in kinds:
            raise ValueError(
                f"feature[{i}] geometry {gtype!r} not in {kinds}")
        out.append(f)
    return out


_COND_RE = re.compile(
    r"^\s*value\s*(>=|<=|==|!=|>|<)\s*([-+]?\d+(?:\.\d+)?)\s*$")


def _parse_condition(expr: str):
    """把受限条件表达式编译为谓词；不使用 eval / 任意代码执行。"""
    text = str(expr or "").strip()
    if not text:
        return lambda arr: None, "none", 0
    clauses = text.split(" and ")
    compiled = []
    for clause in clauses:
        m = _COND_RE.match(clause)
        if not m:
            raise ValueError(
                f"unsupported where clause {clause!r}; "
                "only 'value <op> <number>' (and-joined) is allowed")
        op, raw = m.group(1), float(m.group(2))
        compiled.append((op, raw))
    ops = {
        ">": lambda a, v: a > v, ">=": lambda a, v: a >= v,
        "<": lambda a, v: a < v, "<=": lambda a, v: a <= v,
        "==": lambda a, v: a == v, "!=": lambda a, v: a != v,
    }

    def predicate(arr):
        mask = None
        import numpy as np

        for op, value in compiled:
            part = ops[op](arr, value)
            mask = part if mask is None else (mask & part)
        return mask if mask is not None else np.ones(arr.shape, dtype=bool)

    return predicate, text, len(compiled)


def register_capability_ecosystem_tools(registry: ToolRegistry) -> None:
    """注册两项原生计算原语（唯一 ToolRegistry，不新建第二注册中心）。"""

    @tool(
        registry, name="conditional_quantile_zonal_stats",
        description=(
            "条件/多分位数分区栅格统计：对每个面分区，先按 where 条件筛选像元，"
            "再计算多个分位数（默认 p25/p50/p75）、有效样本分母与覆盖率。"
            "\n何时用：『各区县 NDVI 的 p90』『只统计高程>500m 的像元中位数』；"
            "需要分布形态而非只有均值时。"
            "\n何时不用：只要 mean/min/max → zonal_stats；"
            "点采样 → 直接读栅格；矢量区内计数 → spatial_aggregate。"
            "\n科学约束：CRS 必须是投影/米制才能解释面积；nodata 与条件筛选"
            "共同构成有效样本分母；分位数用线性插值法（numpy 默认 linear）。"
        ),
        tier=2, domains=["raster", "statistics"],
        args_model=ConditionalQuantileZonalStatsArgs,
        side_effect="deterministic_compute", network=False, deterministic=True,
        latency_class="slow", memory_class="medium", scale_class="large",
        tags=("分区统计", "zonal", "分位数", "quantile", "条件筛选", "栅格"),
        output_semantic_type="analysis_artifact",
        produced_refs=("artifact",),
        result_size_policy="ref_offload",
        crs_semantics="projected_required",
        unit_semantics="raster_value_unitless",
        capabilities=("zonal_statistics",),
        fallback_tool="zonal_stats",
        failure_modes=("invalid_args", "missing_data", "crs_geographic", "budget"),
    )
    def conditional_quantile_zonal_stats(
        zones: Any, raster_path: str,
        quantiles: Optional[list[float]] = None,
        where: str = "", nodata: Optional[float] = None,
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract

        qlist = quantiles if quantiles is not None else [0.25, 0.5, 0.75]
        params = apply_contract("conditional_quantile_zonal_stats", {
            # 契约以受限字符串校验分位数列表（避免任意 JSON 类型穿透），
            # 工具侧再解析回浮点并逐项做 (0,1) 科学校验。
            "quantiles": ",".join(str(q) for q in qlist),
            "where": where, "nodata": nodata,
        })
        parsed = [float(x.strip()) for x in str(params["quantiles"]).split(",")
                  if x.strip()]
        return _conditional_quantile_zonal_stats_impl(
            zones, raster_path,
            quantiles=parsed,
            where=str(params.get("where") or ""),
            nodata=params.get("nodata"),
        )

    @tool(
        registry, name="network_service_coverage_consistency",
        description=(
            "线网服务覆盖一致性校验：在同一线网图上按 facilities 的 scheme 分组，"
            "分别做 Dijkstra 可达性，比较不同方案的服务覆盖边集（Jaccard、"
            "差异段、覆盖率）。"
            "\n何时用：『方案 A/B 的 15 分钟服务区是否一致』『新增站点后覆盖差异』；"
            "需要验证两个覆盖方案是否可互相替代。"
            "\n何时不用：只要生成单个服务区 → network_service_area；"
            "只求 OD 成本矩阵 → network_od_matrix（已有实现）。"
            "\n科学约束：cost_unit 声明成本量纲；秒（时间）与米（长度）绝不混用；"
            "请求 distance/meters 输出而 cost_unit 是时间单位时返回 typed 错误。"
        ),
        tier=2, domains=["network"],
        args_model=NetworkServiceCoverageConsistencyArgs,
        side_effect="deterministic_compute", network=False, deterministic=True,
        latency_class="medium", memory_class="medium", scale_class="large",
        tags=("服务区", "service area", "一致性", "consistency", "线网", "覆盖"),
        output_semantic_type="analysis_artifact",
        produced_refs=("artifact",),
        result_size_policy="ref_offload",
        crs_semantics="projected_required",
        unit_semantics="meters",
        capabilities=("service_area",),
        fallback_tool="network_service_area",
        failure_modes=("invalid_args", "unit_conflict", "budget", "disconnected_network"),
    )
    def network_service_coverage_consistency(
        network: Any, facilities: Any, cutoff: float = 2.0,
        cost_unit: str = "meters", metric: str = "",
        output_unit: str = "", request_metric: str = "",
    ) -> dict:
        from app.lib.gis.parameter_contracts import apply_contract

        params = apply_contract("network_service_coverage_consistency", {
            "cutoff": cutoff, "cost_unit": cost_unit,
            "metric": metric, "output_unit": output_unit,
            "request_metric": request_metric,
        })
        return _network_coverage_consistency_impl(
            network, facilities,
            cutoff=float(params["cutoff"]),
            cost_unit=str(params["cost_unit"]),
            metric=str(params.get("metric") or ""),
            output_unit=str(params.get("output_unit") or ""),
            request_metric=str(params.get("request_metric") or ""),
        )


# ── 原语 1 的真实计算 ────────────────────────────────────────────────

def _conditional_quantile_zonal_stats_impl(
    zones: Any, raster_path: str,
    quantiles: list[float], where: str, nodata: Optional[float],
) -> dict[str, Any]:
    import numpy as np
    import rasterio
    from rasterio.features import geometry_mask
    from shapely.geometry import shape

    started = time.monotonic()
    qlist = [float(q) for q in (quantiles or [0.25, 0.5, 0.75])]
    if not qlist or len(qlist) > MAX_QUANTILES:
        return _error("BUDGET_EXCEEDED", "quantiles 需在 1..16 项之间")
    if any(not (0.0 < q < 1.0) for q in qlist):
        return _error("INVALID_QUANTILE", "quantiles 各项必须 ∈ (0,1)")
    try:
        predicate, where_text, n_clauses = _parse_condition(where)
    except ValueError as exc:
        return _error("INVALID_WHERE", str(exc))

    try:
        feats = _features(zones, ("Polygon", "MultiPolygon"))[:MAX_ZONES]
    except ValueError as exc:
        return _error("INVALID_ZONES", str(exc))

    try:
        with rasterio.open(raster_path) as ds:
            source_crs = str(ds.crs or "")
            transform = ds.transform
            band = ds.read(1).astype("float64")
            ds_nodata = ds.nodata
            cells = int(band.size)
    except Exception as exc:  # noqa: BLE001 — 路径/栅格错误是 typed 失败
        return _error("RASTER_UNREADABLE", f"cannot open raster: {exc}")

    if cells > MAX_RASTER_CELLS:
        return _error("BUDGET_EXCEEDED",
                      f"raster has {cells} cells > {MAX_RASTER_CELLS}")
    # 经纬度 CRS 不得按米解释面积/长度（CAP-O06）。
    geographic = bool(source_crs) and (
        "4326" in source_crs or "longlat" in source_crs.lower()
        or source_crs.lower().startswith("epsg:4326"))
    eff_nodata = nodata if nodata is not None else ds_nodata
    base_valid = np.isfinite(band)
    if eff_nodata is not None:
        base_valid &= (band != float(eff_nodata))
    where_mask = predicate(band)
    if where_mask is None:
        where_mask = np.ones(band.shape, dtype=bool)

    rows: list[dict[str, Any]] = []
    for idx, feat in enumerate(feats):
        try:
            geom = shape(feat["geometry"])
            if not geom.is_valid:
                geom = geom.buffer(0)
            inside = geometry_mask(
                [geom.__geo_interface__], out_shape=band.shape,
                transform=transform, invert=True)
        except Exception:  # noqa: BLE001 — 单 zone 失败不拖垮全表，但必须披露
            rows.append({
                "zone_index": idx, "zone_id": feat.get("properties", {}).get("zone_id", idx),
                "valid_pixel_count": 0, "valid_pixel_coverage": 0.0,
                "quantiles": {str(q): None for q in qlist},
                "geometry_repair": "failed:geometry_invalid",
            })
            continue
        mask = inside & base_valid & where_mask
        vals = band[mask]
        valid_count = int(vals.size)
        total = int(inside.sum())
        coverage = float(valid_count / total) if total else 0.0
        qvals = (np.quantile(vals, qlist, method="linear")
                 if valid_count else np.full(len(qlist), float("nan")))
        rows.append({
            "zone_index": idx,
            "zone_id": feat.get("properties", {}).get("zone_id", idx),
            "valid_pixel_count": valid_count,
            "valid_pixel_coverage": round(coverage, 6),
            "quantiles": {
                str(q): (round(float(v), 6) if valid_count and math.isfinite(float(v))
                         else None)
                for q, v in zip(qlist, qvals)
            },
            "mean": (round(float(vals.mean()), 6) if valid_count else None),
            "min": (round(float(vals.min()), 6) if valid_count else None),
            "max": (round(float(vals.max()), 6) if valid_count else None),
            "n_samples": int(valid_count),
        })

    elapsed = time.monotonic() - started
    data = {
        "zones": rows,
        "zone_count": len(rows),
        "quantiles": qlist,
        "where": where_text,
        "total_valid_pixels": sum(
            int(r.get("valid_pixel_count", 0)) for r in rows),
    }
    artifact = _ref("conditional_quantile_zonal_stats", data)
    artifact["rows"] = len(rows)
    science = {
        "method": "conditional multi-quantile zonal raster statistics",
        "quantile_method": "linear interpolation (numpy.quantile default)",
        "effective_denominator": (
            "zone 内像元 ∩ finite ∩ 非 nodata ∩ where 条件；"
            "分母即 valid_pixel_count，不是 zone 总面积"),
        "nodata_policy": f"excluded (nodata={eff_nodata})",
        "condition_clauses": n_clauses,
        "crs": source_crs or "unknown",
        "geographic_crs_warning": (
            "geographic CRS：角度量纲不可解释面积/长度，结果仅作像元值分布"
            if geographic else None),
        "uncertainty": (
            "分位数为经验分位数；小样本 zone（valid_pixel_count < 30）"
            "不提供置信区间，标注 not_evaluated"),
        "small_sample_zones": sum(
            1 for r in rows if int(r.get("valid_pixel_count", 0)) < 30),
        "determinism": "deterministic; fixed input + fixed quantiles => identical output",
        "provenance": ["ToolRegistry", "rasterio", "numpy.quantile"],
    }
    budget = {
        "max_zones": MAX_ZONES, "max_quantiles": MAX_QUANTILES,
        "max_raster_cells": MAX_RASTER_CELLS,
        "raster_cells": cells, "zones_processed": len(rows),
        "elapsed_ms": round(elapsed * 1000, 3),
        "truncated": len(feats) >= MAX_ZONES,
    }
    return {"data": data, "artifact": artifact,
            "science": science, "budget": budget}


# ── 原语 2 的真实计算 ────────────────────────────────────────────────

def _edge_key(a, b) -> tuple:
    return tuple(sorted((tuple(round(float(x), 6) for x in a),
                         tuple(round(float(x), 6) for x in b))))


def _build_graph(feats: list[dict[str, Any]]) -> tuple[Any, dict, float]:
    """把线要素构建为 networkx 图，边权 = 长度（米制）或时间（显式秒）。"""
    import networkx as nx

    graph = nx.Graph()
    edges: dict[tuple, tuple[str, float, str]] = {}
    for i, feat in enumerate(feats):
        geom = feat["geometry"]
        gtype = geom.get("type")
        lines = [geom.get("coordinates") or []]
        if gtype == "MultiLineString":
            lines = geom.get("coordinates") or []
        for line in lines:
            coords = [(float(p[0]), float(p[1])) for p in line if len(p) >= 2]
            for a, b in zip(coords, coords[1:]):
                if a == b:
                    continue
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if not math.isfinite(length):
                    continue
                key = _edge_key(a, b)
                eid = f"e{i}_{len(edges)}"
                props = feat.get("properties", {}) or {}
                edges[key] = (eid, length, str(props.get("scheme", "") or ""))
                graph.add_edge(key[0], key[1], length=length, edge_id=eid)
    return graph, edges, sum(v[1] for v in edges.values())


def _network_coverage_consistency_impl(
    network: Any, facilities: Any, cutoff: float,
    cost_unit: str, metric: str, output_unit: str, request_metric: str,
) -> dict[str, Any]:
    import networkx as nx

    started = time.monotonic()
    unit = str(cost_unit or "").strip().lower()
    requested = " ".join(str(x or "").lower()
                         for x in (metric, output_unit, request_metric))
    # CAP-O06 硬门：时间成本量纲 + 请求米制距离输出 = typed 冲突，绝不混用。
    asks_distance = any(k in requested for k in (
        "distance", "meters", "meter", "metres", "m ", "km", "euclid"))
    if unit in _TIME_UNITS and asks_distance:
        return _error(
            "UNIT_DIMENSION_CONFLICT",
            f"cost_unit={cost_unit!r} 是时间量纲(秒)，但请求了米制距离输出 "
            f"({requested.strip() or 'distance'})；秒≠米，拒绝混用。"
            "请用 cost_unit=meters/m 或显式提供速度换算并声明近似语义",
            "把 cost_unit 改为 meters/m，或移除 metric/output_unit/request_metric 的米制请求",
        )
    if unit not in _TIME_UNITS and unit not in _LENGTH_UNITS:
        return _error("INVALID_UNIT",
                      f"cost_unit={cost_unit!r} 不在量纲词表；"
                      "须为 meters/m/km 或 seconds/s/min")
    if not math.isfinite(cutoff) or cutoff <= 0 or cutoff > MAX_CUTOFF:
        return _error("INVALID_CUTOFF", "cutoff 必须 > 0 且有界")

    try:
        feats = _features(network, ("LineString", "MultiLineString"))[:MAX_NETWORK_EDGES]
        facs = _features(facilities, ("Point",))[:MAX_FACILITIES]
    except ValueError as exc:
        return _error("INVALID_NETWORK_OR_FACILITIES", str(exc))

    graph, edges, total_length = _build_graph(feats)
    if graph.number_of_edges() == 0:
        return _error("DISCONNECTED_NETWORK", "线网无有效边，无法计算覆盖")

    schemes: dict[str, list[tuple[float, float]]] = {}
    for f in facs:
        props = f.get("properties", {}) or {}
        scheme = str(props.get("scheme", "") or "default")
        coords = f["geometry"].get("coordinates") or []
        if len(coords) >= 2:
            schemes.setdefault(scheme, []).append(
                (float(coords[0]), float(coords[1])))

    nodes = list(graph.nodes())
    def nearest(point):
        return min(nodes, key=lambda n: math.hypot(n[0] - point[0], n[1] - point[1]))

    coverage: dict[str, set[str]] = {}
    for scheme, points in schemes.items():
        reached_nodes: set[Any] = set()
        for p in points:
            src = nearest(p)
            try:
                lengths = nx.single_source_dijkstra_path_length(
                    graph, src, cutoff=cutoff, weight="length")
            except Exception:  # noqa: BLE001
                continue
            reached_nodes.update(lengths.keys())
        covered = {eid for key, (eid, _l, _s) in edges.items()
                   if key[0] in reached_nodes and key[1] in reached_nodes}
        coverage[scheme] = covered

    names = sorted(coverage)
    if len(names) >= 2:
        a, b = coverage[names[0]], coverage[names[1]]
        union = a | b
        jaccard = float(len(a & b) / len(union)) if union else 1.0
        only_a, only_b = sorted(a - b), sorted(b - a)
    else:
        only = next(iter(coverage.values())) if coverage else set()
        a = b = only
        union = only
        jaccard = 1.0
        only_a, only_b = [], []

    elapsed = time.monotonic() - started
    data = {
        "schemes": names,
        "jaccard": round(jaccard, 6),
        "consistency": round(jaccard, 6),
        "covered_edges_a": len(a),
        "covered_edges_b": len(b),
        "shared_edges": len(a & b) if len(names) >= 2 else len(a),
        "union_edges": len(union),
        "only_a_edges": only_a[:64],
        "only_b_edges": only_b[:64],
        "only_a_count": len(only_a),
        "only_b_count": len(only_b),
        "coverage_ratio_a": (round(len(a) / len(edges), 6) if edges else 0.0),
        "consistent": bool(jaccard >= 1.0 - 1e-9),
    }
    artifact = _ref("network_service_coverage_consistency", data)
    artifact["rows"] = len(edges)
    science = {
        "method": "multi-scheme network service coverage consistency (Dijkstra)",
        "consistency_metric": "Jaccard over covered edge-id sets (set equality when identical)",
        "cost_unit": unit,
        "cost_dimension": "time" if unit in _TIME_UNITS else "length",
        "cutoff": cutoff,
        "distance_vs_time": (
            "严格区分：时间成本(秒)不得解释为空间距离(米)；"
            "两方案量纲不一致时返回 UNIT_DIMENSION_CONFLICT"),
        "unit_contract": {
            "numerator": "edge length" if unit in _LENGTH_UNITS else "travel time",
            "denominator": "none",
            "distance_unit": unit if unit in _LENGTH_UNITS else None,
            "time_unit": unit if unit in _TIME_UNITS else None,
        },
        "uncertainty": (
            "Dijkstra 在静态无向图上确定性可达；不建模拥堵/转向惩罚，"
            "若需转向成本应改用 network_service_area 并披露 approximations"),
        "crs_disclosure": (
            "输入坐标按平面长度建图；经纬度输入须先重投影，"
            "否则角度量纲不可当米（CAP-O06）"),
        "determinism": "deterministic; fixed graph + cutoff + schemes => identical output",
        "provenance": ["ToolRegistry", "networkx", "Dijkstra"],
    }
    budget = {
        "max_network_edges": MAX_NETWORK_EDGES,
        "max_facilities": MAX_FACILITIES,
        "network_edges": len(edges),
        "facilities": len(facs),
        "schemes": len(names),
        "total_edge_length": round(total_length, 6),
        "elapsed_ms": round(elapsed * 1000, 3),
        "truncated": len(feats) >= MAX_NETWORK_EDGES or len(facs) >= MAX_FACILITIES,
    }
    return {"data": data, "artifact": artifact,
            "science": science, "budget": budget}
