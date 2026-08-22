"""
Network Analyst V2 Tools for ToolRegistry.
Exposes network_shortest_path, network_od_matrix, network_closest_facility,
network_service_area, network_accessibility, location_allocation, and optimize_route.
"""
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, ToolExecutionPolicy, tool
from app.tools._utils import trim_features
from app.services.network.engine import NetworkGraphEngine
from app.services.network.models import TravelProfile

logger = logging.getLogger(__name__)

# Issue #540: 2-opt local search is super-linear per stop count and the tool
# receives unbounded `stops` lists from the agent. Requests beyond this cap are
# rejected with an EXPLICIT error (never silently truncated); the underlying
# engine API stays uncapped for programmatic callers.
MAX_OPTIMIZE_STOPS = 200

# Issue #582: the OD matrix is O(origins × destinations) — an unbounded product
# (e.g. 300×300 = 90000 rows) serializes to a multi-MB payload that goes
# straight into the LLM context. Requests beyond this cap are rejected with an
# EXPLICIT error (never silently truncated), mirroring MAX_OPTIMIZE_STOPS.
MAX_OD_MATRIX_PAIRS = 10_000


def _geometry_descriptor(geom: Any, max_features: int = 50) -> Dict[str, Any]:
    """Collapse a heavy geometry field into a Fetch-on-Demand descriptor.

    Network results embed isochrone polygons, reachable-edge MultiLineStrings and
    full accessibility layers — inlining these into tool_result blows past the
    50k DB/LLM-context cap. We keep the geometry *shape* (so the caller can render
    a preview) but trim coordinates/features, and always record a count.
    """
    if not isinstance(geom, dict):
        return {"present": bool(geom)}
    gtype = geom.get("type")
    if gtype == "FeatureCollection":
        trimmed = trim_features(geom, max_features=max_features)
        return {
            "type": gtype,
            "feature_count": len(geom.get("features", [])),
            "preview": trimmed,
        }
    # Bare geometry (Point/LineString/Polygon/Multi*). Keep type + coord arity so
    # the caller knows the shape without the full coordinate payload.
    coords = geom.get("coordinates")
    coord_count = _count_coords(coords)
    return {
        "type": gtype,
        "coordinate_count": coord_count,
        "preview": {**geom, "coordinates": _trim_coords(coords)},
    }


def _count_coords(coords: Any) -> int:
    """Count leaf coordinate positions in a nested coordinate array."""
    if not isinstance(coords, list):
        return 0
    if coords and isinstance(coords[0], (int, float)):
        return len(coords)
    return sum(_count_coords(c) for c in coords)


def _trim_coords(coords: Any, limit: int = 50) -> Any:
    """Truncate a coordinate array to its first `limit` leaf positions per ring."""
    if not isinstance(coords, list):
        return coords
    if coords and isinstance(coords[0], (int, float)):
        return coords[:limit]
    return [_trim_coords(c, limit) for c in coords[:limit]]


def trim_network_result(payload: Dict[str, Any], max_features: int = 50) -> Dict[str, Any]:
    """Apply Fetch-on-Demand trimming to a network analysis result dict.

    Replaces the heaviest geometry-bearing fields with descriptors, keeping the
    metrics / route summaries the LLM actually reasons over.
    """
    if not isinstance(payload, dict):
        return payload

    out = dict(payload)

    for sa in out.get("service_areas", []) or []:
        if not isinstance(sa, dict):
            continue
        for brk in sa.get("breaks", []) or []:
            if not isinstance(brk, dict):
                continue
            if brk.get("geometry"):
                brk["geometry"] = _geometry_descriptor(brk["geometry"], max_features=max_features)
            if brk.get("reachable_network_geometry"):
                brk["reachable_network_geometry"] = _geometry_descriptor(
                    brk["reachable_network_geometry"], max_features=max_features
                )
        if sa.get("overall_geometry"):
            sa["overall_geometry"] = _geometry_descriptor(sa["overall_geometry"], max_features=max_features)

    for brk in out.get("service_area_breaks", []) or []:
        if not isinstance(brk, dict):
            continue
        if brk.get("geometry"):
            brk["geometry"] = _geometry_descriptor(brk["geometry"], max_features=max_features)
        if brk.get("reachable_network_geometry"):
            brk["reachable_network_geometry"] = _geometry_descriptor(
                brk["reachable_network_geometry"], max_features=max_features
            )

    accessibility = out.get("accessibility")
    if isinstance(accessibility, dict) and accessibility.get("accessibility_layer_geojson"):
        accessibility["accessibility_layer_geojson"] = _geometry_descriptor(
            accessibility["accessibility_layer_geojson"], max_features=max_features
        )

    if out.get("result_geojson"):
        out["result_geojson"] = _geometry_descriptor(out["result_geojson"], max_features=max_features)

    # Issue #582: the >40k-character last-resort truncation used to clear only
    # ``features`` keys while attaching a "Payload truncated for context safety"
    # notice unconditionally — network payloads bloat in ``od_matrix`` rows,
    # ``routes[].coordinates`` and per-route ``directions`` instead, so the
    # notice LIED (payload stayed multi-MB while claiming it was trimmed). The
    # fallback now compresses those shapes for real, records explicit
    # ``_*_trimmed`` summaries, and only claims truncation when it happened.
    try:
        import json
        if len(json.dumps(out)) > 40000:
            truncated = _truncate_network_fallback(out)
            if truncated:
                out["_payload_notice"] = (
                    "Payload exceeded the context budget and was trimmed to summaries "
                    "(see _*_trimmed keys for original counts). "
                    "Use layer endpoints for full geometry access."
                )
            elif len(json.dumps(out)) > 40000:
                out["_payload_notice"] = (
                    "Payload exceeds the context budget with no further compressible "
                    "fields; narrow the analysis scope and retry."
                )
    except Exception:
        pass

    return out


# 兜底截断的逐形状上限（Issue #582）：od 行/路由/每段 directions 超过即裁，
# 计数保留在 _*_trimmed 汇总里，绝不静默丢数据。兜底分支里 route 几何直接
# 压成纯摘要（坐标预览也裁掉——超预算时 LLM 该推理的是 total_distance/time
# 等指标，完整坐标走图层端点）。
_FALLBACK_MAX_ROWS = 50
_FALLBACK_MAX_ROUTE_COORDS = 50
_FALLBACK_MAX_DIRECTIONS = 5


def _truncate_network_fallback(out: Dict[str, Any]) -> bool:
    """Force-compress a network result that still exceeds the context budget.

    Handles the non-features shapes network payloads actually bloat on:
    ``od_matrix`` rows, the ``routes`` list (route geometry coordinates and
    per-route ``directions``), plus a defensive ``features``-array pass (the
    pre-fix behavior, kept for untrimmed FeatureCollections nested elsewhere).
    Returns True if anything was actually removed — the caller only attaches an
    honest truncation notice in that case.
    """
    truncated = False

    od = out.get("od_matrix")
    if isinstance(od, list) and len(od) > _FALLBACK_MAX_ROWS:
        out["od_matrix"] = od[:_FALLBACK_MAX_ROWS]
        out["_od_matrix_trimmed"] = {
            "original_pair_count": len(od),
            "kept_pair_count": _FALLBACK_MAX_ROWS,
        }
        truncated = True

    routes = out.get("routes")
    if isinstance(routes, list):
        if len(routes) > _FALLBACK_MAX_ROWS:
            original_route_count = len(routes)
            routes = routes[:_FALLBACK_MAX_ROWS]
            out["routes"] = routes
            out["_routes_trimmed"] = {
                "original_route_count": original_route_count,
                "kept_route_count": _FALLBACK_MAX_ROWS,
            }
            truncated = True
        for route in routes:
            if not isinstance(route, dict):
                continue
            geometry = route.get("geometry")
            if (
                isinstance(geometry, dict)
                and isinstance(geometry.get("coordinates"), list)
                and _count_coords(geometry["coordinates"]) > _FALLBACK_MAX_ROUTE_COORDS
            ):
                route["geometry"] = {
                    "type": geometry.get("type"),
                    "coordinate_count": _count_coords(geometry["coordinates"]),
                    "note": "Coordinates trimmed in the fallback; full geometry available via layer endpoints.",
                }
                truncated = True
            directions = route.get("directions")
            if isinstance(directions, list) and len(directions) > _FALLBACK_MAX_DIRECTIONS:
                route["directions"] = directions[:_FALLBACK_MAX_DIRECTIONS]
                route["_directions_trimmed"] = {
                    "original_count": len(directions),
                    "kept_count": _FALLBACK_MAX_DIRECTIONS,
                }
                truncated = True

    # Defensive pass: drop any remaining full ``features`` arrays (e.g. inside
    # an untrimmed nested payload) — mirrors the pre-fix behavior.
    def _clear_features(obj: Any) -> bool:
        hit = False
        if isinstance(obj, dict):
            if isinstance(obj.get("features"), list) and obj["features"]:
                obj["features"] = []
                hit = True
            for v in obj.values():
                hit = _clear_features(v) or hit
        elif isinstance(obj, list):
            for item in obj:
                hit = _clear_features(item) or hit
        return hit

    if _clear_features(out):
        truncated = True

    return truncated


# --- Pydantic Args Models ---

class NetworkShortestPathArgs(BaseModel):
    network: Any = Field(..., description="Network GeoJSON LineString dataset, ref ID, or 'osm_road'")
    origin: Any = Field(..., description="Origin point (lng, lat), GeoJSON point, or address string")
    destination: Any = Field(..., description="Destination point (lng, lat), GeoJSON point, or address string")
    profile: str = Field(default="driving", description="Travel profile: walking, driving, cycling, custom")
    impedance: str = Field(default="travel_time_s", description="Impedance field: length_m, travel_time_s")
    barriers: Optional[List[Dict[str, Any]]] = Field(default=None, description="Optional barrier points or polygons")


class NetworkODMatrixArgs(BaseModel):
    network: Any = Field(..., description="Network GeoJSON dataset, ref ID, or 'osm_road'")
    origins: List[Any] = Field(..., description=f"List of origin points or features（起终点组合数上限 {MAX_OD_MATRIX_PAIRS}，超限请求必须拆分）")
    destinations: List[Any] = Field(..., description=f"List of destination points or features（起终点组合数上限 {MAX_OD_MATRIX_PAIRS}，超限请求必须拆分）")
    profile: str = Field(default="driving", description="Travel profile: walking, driving, cycling, custom")
    cutoff_s: Optional[float] = Field(default=None, description="Maximum travel time cutoff in seconds")


class NetworkClosestFacilityArgs(BaseModel):
    network: Any = Field(..., description="Network GeoJSON dataset, ref ID, or 'osm_road'")
    incidents: List[Any] = Field(..., description="Incident / demand points needing service")
    facilities: List[Any] = Field(..., description="Facility locations")
    profile: str = Field(default="driving", description="Travel profile: walking, driving, cycling, custom")
    number_to_find: int = Field(default=1, description="Number of closest facilities to return per incident")
    cutoff_cost: Optional[float] = Field(
        default=None,
        description="Maximum travel cost cutoff (in the active impedance's units: seconds for travel_time_s, meters for length_m). Pairs beyond it are excluded from both the routing and the results — bound the analysis before Routes are built.",
    )


class NetworkServiceAreaArgs(BaseModel):
    network: Any = Field(..., description="Network GeoJSON dataset, ref ID, or 'osm_road'")
    facilities: List[Any] = Field(..., description="Facility point locations")
    breaks: List[float] = Field(default_factory=lambda: [5.0, 10.0, 15.0], description="Service area cutoff breaks in minutes")
    profile: str = Field(default="driving", description="Travel profile: walking, driving, cycling, custom")


class NetworkAccessibilityArgs(BaseModel):
    network: Any = Field(..., description="Network GeoJSON dataset, ref ID, or 'osm_road'")
    demand_layer: Any = Field(..., description="Demand / population points or polygon layer")
    facilities: List[Any] = Field(..., description="Service facility locations")
    cutoff_minutes: float = Field(default=15.0, description="Target travel time cutoff in minutes")
    profile: str = Field(default="walking", description="Travel profile: walking, driving, cycling, custom")


class LocationAllocationArgs(BaseModel):
    network: Any = Field(..., description="Network GeoJSON dataset, ref ID, or 'osm_road'")
    candidate_facilities: List[Any] = Field(..., description="Candidate facility locations")
    demand_points: List[Any] = Field(..., description="Demand points or population centers")
    number_to_choose: int = Field(default=2, description="Number of facilities to select")
    objective: str = Field(default="minimize_cost", description="Objective: minimize_cost or maximize_coverage")
    profile: str = Field(default="driving", description="Travel profile")


class OptimizeRouteArgs(BaseModel):
    network: Any = Field(..., description="Network GeoJSON dataset, ref ID, or 'osm_road'")
    depot: Any = Field(..., description="Starting depot point")
    stops: List[Any] = Field(..., description=f"Intermediate stops to visit (max {MAX_OPTIMIZE_STOPS} — the 2-opt local search is super-linear; larger requests must be split into smaller tours)")
    profile: str = Field(default="driving", description="Travel profile")


def register_network_tools(registry: ToolRegistry):
    """Register Network Analyst V2 tools into ToolRegistry."""
    engine = NetworkGraphEngine()

    @tool(
        registry,
        name="network_shortest_path",
        description="计算沿真实拓扑路网的最短路径（支持步行、驾车、骑行模式，容错捕捉与障碍物避让）。",
        tier=2,
        domains=["network"],
        args_model=NetworkShortestPathArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
    )
    async def network_shortest_path(
        network: Any,
        origin: Any,
        destination: Any,
        profile: str = "driving",
        impedance: str = "travel_time_s",
        barriers: Optional[List[Dict[str, Any]]] = None,
        session_id: str = "",
    ) -> dict:
        try:
            travel_profile = TravelProfile(name=profile, impedance_field=impedance)
            res = await engine.solve_shortest_path(
                network=network,
                origin=origin,
                destination=destination,
                profile=travel_profile,
                barriers=barriers,
                session_id=session_id,
            )
            return trim_network_result(res.model_dump())
        except Exception as e:
            logger.error(f"[network_shortest_path] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"路网最短路径计算失败: {str(e)}"}

    @tool(
        registry,
        name="network_od_matrix",
        description="高效计算多起点 × 多终点网络成本矩阵（距离、通行时间、可达性分析）。",
        tier=2,
        domains=["network"],
        args_model=NetworkODMatrixArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
    )
    async def network_od_matrix(
        network: Any,
        origins: List[Any],
        destinations: List[Any],
        profile: str = "driving",
        cutoff_s: Optional[float] = None,
        session_id: str = "",
    ) -> dict:
        try:
            # Issue #582: bound the O(origins × destinations) matrix at the
            # agent-facing surface. Explicit rejection — never silently
            # truncate the rest (mirrors MAX_OPTIMIZE_STOPS in optimize_route).
            pair_count = len(origins) * len(destinations)
            if pair_count > MAX_OD_MATRIX_PAIRS:
                return {
                    "type": "error",
                    "message": (
                        f"network_od_matrix 最多支持 {MAX_OD_MATRIX_PAIRS} 个起终点组合"
                        f"（origins {len(origins)} × destinations {len(destinations)} = {pair_count}）；"
                        f"请拆分起点/终点或缩小范围后重试。"
                    ),
                }
            travel_profile = TravelProfile(name=profile)
            res = await engine.solve_od_matrix(
                network=network,
                origins=origins,
                destinations=destinations,
                profile=travel_profile,
                cutoff_s=cutoff_s,
                session_id=session_id,
            )
            return trim_network_result(res.model_dump())
        except Exception as e:
            logger.error(f"[network_od_matrix] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"OD 矩阵计算失败: {str(e)}"}

    @tool(
        registry,
        name="network_closest_facility",
        description="基于真实路网通行时间查找最近设施（如最近医院、消防站、学校）。",
        tier=2,
        domains=["network"],
        args_model=NetworkClosestFacilityArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
    )
    async def network_closest_facility(
        network: Any,
        incidents: List[Any],
        facilities: List[Any],
        profile: str = "driving",
        number_to_find: int = 1,
        cutoff_cost: Optional[float] = None,
        session_id: str = "",
    ) -> dict:
        try:
            travel_profile = TravelProfile(name=profile)
            res = await engine.solve_closest_facility(
                network=network,
                incidents=incidents,
                facilities=facilities,
                profile=travel_profile,
                number_to_find=number_to_find,
                cutoff_cost=cutoff_cost,
                session_id=session_id,
            )
            return trim_network_result(res.model_dump())
        except Exception as e:
            logger.error(f"[network_closest_facility] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"最近设施查找失败: {str(e)}"}

    @tool(
        registry,
        name="network_service_area",
        description="生成多时间断点（如 5/10/15/30 分钟）真实路网等时圈与可达范围多边形。",
        tier=2,
        domains=["network"],
        args_model=NetworkServiceAreaArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
    )
    async def network_service_area(
        network: Any,
        facilities: List[Any],
        breaks: List[float] = None,
        profile: str = "driving",
        session_id: str = "",
    ) -> dict:
        if breaks is None:
            breaks = [5.0, 10.0, 15.0]
        try:
            travel_profile = TravelProfile(name=profile)
            res = await engine.solve_service_area(
                network=network,
                facilities=facilities,
                breaks_minutes=breaks,
                profile=travel_profile,
                session_id=session_id,
            )
            return trim_network_result(res.model_dump())
        except Exception as e:
            logger.error(f"[network_service_area] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"路网服务区等时圈计算失败: {str(e)}"}

    @tool(
        registry,
        name="network_accessibility",
        description="评估 15 分钟生活圈空间可达性（如医疗、教育、公园、商业设施服务覆盖率与人口服务分配）。",
        tier=2,
        domains=["network"],
        args_model=NetworkAccessibilityArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver (honest declaration)
    )
    async def network_accessibility(
        network: Any,
        demand_layer: Any,
        facilities: List[Any],
        cutoff_minutes: float = 15.0,
        profile: str = "walking",
        session_id: str = "",
    ) -> dict:
        try:
            travel_profile = TravelProfile(name=profile)
            res = await engine.solve_accessibility(
                network=network,
                demand_layer=demand_layer,
                facilities=facilities,
                cutoff_minutes=cutoff_minutes,
                profile=travel_profile,
                session_id=session_id,
            )
            return trim_network_result(res.model_dump())
        except Exception as e:
            logger.error(f"[network_accessibility] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"空间可达性评估失败: {str(e)}"}

    @tool(
        registry,
        name="location_allocation",
        description="设施选址优化（Location-Allocation）：从多个候选设施中选取最佳组合（最小化加权通行成本或最大化需求覆盖）。",
        tier=3,
        domains=["network"],
        args_model=LocationAllocationArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver; CELERY channel was never implemented
    )
    async def location_allocation(
        network: Any,
        candidate_facilities: List[Any],
        demand_points: List[Any],
        number_to_choose: int = 2,
        objective: str = "minimize_cost",
        profile: str = "driving",
        session_id: str = "",
    ) -> dict:
        try:
            travel_profile = TravelProfile(name=profile)
            res = await engine.solve_location_allocation(
                network=network,
                candidate_facilities=candidate_facilities,
                demand_points=demand_points,
                n_to_choose=number_to_choose,
                objective=objective,
                profile=travel_profile,
                session_id=session_id,
            )
            return trim_network_result(res.model_dump())
        except Exception as e:
            logger.error(f"[location_allocation] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"设施选址优化失败: {str(e)}"}

    @tool(
        registry,
        name="optimize_route",
        description="路径巡航与配送路线优化（Route Optimization / VRP）：计算访问多个停靠点的最佳拜访顺序与巡检路线。",
        tier=3,
        domains=["network"],
        args_model=OptimizeRouteArgs,
        execution_policy=ToolExecutionPolicy.ASYNC,  # audit #827: async solver; CELERY channel was never implemented
    )
    async def optimize_route(
        network: Any,
        depot: Any,
        stops: List[Any],
        profile: str = "driving",
        session_id: str = "",
    ) -> dict:
        try:
            # Issue #540: bound the super-linear 2-opt scan at the agent-facing
            # surface. Explicit rejection — never silence/truncate the rest.
            if len(stops) > MAX_OPTIMIZE_STOPS:
                return {
                    "type": "error",
                    "message": (
                        f"optimize_route 最多支持 {MAX_OPTIMIZE_STOPS} 个 stops "
                        f"(收到 {len(stops)})；请拆分路线或减少停靠点。"
                    ),
                }
            travel_profile = TravelProfile(name=profile)
            res = await engine.solve_optimize_route(
                network=network,
                depot=depot,
                stops=stops,
                profile=travel_profile,
                session_id=session_id,
            )
            return trim_network_result(res.model_dump())
        except Exception as e:
            logger.error(f"[optimize_route] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"路线巡航优化失败: {str(e)}"}
