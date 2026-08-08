"""
Network Analyst V2 Tools for ToolRegistry.
Exposes network_shortest_path, network_od_matrix, network_closest_facility,
network_service_area, network_accessibility, location_allocation, and optimize_route.
"""
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.services.network.engine import NetworkGraphEngine
from app.services.network.models import TravelProfile

logger = logging.getLogger(__name__)


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
    origins: List[Any] = Field(..., description="List of origin points or features")
    destinations: List[Any] = Field(..., description="List of destination points or features")
    profile: str = Field(default="driving", description="Travel profile: walking, driving, cycling, custom")
    cutoff_s: Optional[float] = Field(default=None, description="Maximum travel time cutoff in seconds")


class NetworkClosestFacilityArgs(BaseModel):
    network: Any = Field(..., description="Network GeoJSON dataset, ref ID, or 'osm_road'")
    incidents: List[Any] = Field(..., description="Incident / demand points needing service")
    facilities: List[Any] = Field(..., description="Facility locations")
    profile: str = Field(default="driving", description="Travel profile: walking, driving, cycling, custom")
    number_to_find: int = Field(default=1, description="Number of closest facilities to return per incident")


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
    stops: List[Any] = Field(..., description="Intermediate stops to visit")
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
            return res.model_dump()
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
            travel_profile = TravelProfile(name=profile)
            res = await engine.solve_od_matrix(
                network=network,
                origins=origins,
                destinations=destinations,
                profile=travel_profile,
                cutoff_s=cutoff_s,
                session_id=session_id,
            )
            return res.model_dump()
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
    )
    async def network_closest_facility(
        network: Any,
        incidents: List[Any],
        facilities: List[Any],
        profile: str = "driving",
        number_to_find: int = 1,
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
                session_id=session_id,
            )
            return res.model_dump()
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
            return res.model_dump()
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
            return res.model_dump()
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
            return res.model_dump()
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
    )
    async def optimize_route(
        network: Any,
        depot: Any,
        stops: List[Any],
        profile: str = "driving",
        session_id: str = "",
    ) -> dict:
        try:
            travel_profile = TravelProfile(name=profile)
            res = await engine.solve_optimize_route(
                network=network,
                depot=depot,
                stops=stops,
                profile=travel_profile,
                session_id=session_id,
            )
            return res.model_dump()
        except Exception as e:
            logger.error(f"[optimize_route] Failed: {e}", exc_info=True)
            return {"type": "error", "message": f"路线巡航优化失败: {str(e)}"}
