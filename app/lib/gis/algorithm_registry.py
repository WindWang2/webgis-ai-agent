"""GIS Algorithm Registry —— 算法语义目录（非执行引擎）。

Algorithm 回答「如何计算」：capability（做什么）→ algorithm（哪种方法）
→ tool_candidates（哪个注册工具实现它）。实际执行永远在 ToolRegistry /
ToolDispatchService —— 本注册表只持 metadata，不持数据、不执行、不做
第二套 runtime。新增算法 = 注册 AlgorithmDescriptor，Harness 主规划代码
不改。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.lib.gis.artifacts import get_artifact_type_registry
from app.lib.gis.capability_registry import get_capability_registry

AlgorithmStatus = Literal["native", "planned", "unavailable"]
CostLevel = Literal["low", "medium", "high"]


class AlgorithmDescriptor(BaseModel):
    """一个 GIS 算法的机器可读描述。"""

    id: str                          # 如 "spatial.kde.contours"
    name: str
    capabilities: List[str]          # ≥1；首个为主 capability
    input_artifact_types: List[str] = Field(default_factory=list)
    output_artifact_type: str = ""
    geometry_requirements: List[str] = Field(default_factory=list)  # 主输入几何族
    required_fields: List[str] = Field(default_factory=list)
    optional_fields: List[str] = Field(default_factory=list)
    min_features: Optional[int] = None      # 镜像工具自身的硬门槛（非制图门槛）
    max_features_hint: Optional[int] = None
    crs_requirements: str = ""
    unit_requirements: str = ""
    deterministic: bool = True
    approximate: bool = False
    complexity: str = ""             # 复杂度备注（文档性）
    cpu_cost: CostLevel = "medium"
    memory_cost: CostLevel = "medium"
    io_cost: CostLevel = "medium"
    preferred_execution_policy: str = ""    # INLINE/ASYNC/THREAD/CELERY 提示
    tool_candidates: List[str] = Field(default_factory=list)  # 有序，首选在前
    runtime_status: AlgorithmStatus = "native"  # native 需 ≥1 真实工具
    compatible_map_models: List[str] = Field(default_factory=list)
    fallback_algorithms: List[str] = Field(default_factory=list)
    priority: int = 50               # 同 capability 内越小越优先（稳定排序键）
    version: str = "1.0"
    contract_version: int = 1        # 产物 artifact shape 变化时必须升位


_SEED_ALGORITHMS: List[AlgorithmDescriptor] = [
    # ── 数据获取 ─────────────────────────────────────────────────────
    AlgorithmDescriptor(
        id="poi.query.local", name="POI 查询（本地优先）",
        capabilities=["poi_query"],
        input_artifact_types=[],
        output_artifact_type="poi_feature_set",
        geometry_requirements=[],
        tool_candidates=["query_local_poi", "search_poi", "query_osm_poi"],
        cpu_cost="low", memory_cost="low", io_cost="medium",
        preferred_execution_policy="ASYNC",
        priority=10,
    ),
    AlgorithmDescriptor(
        id="admin.boundary.local", name="行政区边界获取（本地 SHP）",
        capabilities=["admin_boundary_query"],
        output_artifact_type="admin_boundary_set",
        geometry_requirements=["polygon"],
        tool_candidates=["get_local_admin_boundary"],
        cpu_cost="low", memory_cost="low", io_cost="medium",
        preferred_execution_policy="ASYNC",
        priority=10,
    ),
    AlgorithmDescriptor(
        id="raster.source.dem", name="DEM 栅格获取",
        capabilities=["raster_source"],
        output_artifact_type="terrain_surface",
        geometry_requirements=["raster"],
        tool_candidates=["fetch_dem"],
        cpu_cost="low", memory_cost="medium", io_cost="high",
        preferred_execution_policy="ASYNC",
        compatible_map_models=["raster_surface"],
        priority=10,
    ),
    # ── 统计/画像 ────────────────────────────────────────────────────
    AlgorithmDescriptor(
        id="profile.spatial.stats", name="空间数据画像",
        capabilities=["point_profile"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="point_feature_set",
        geometry_requirements=["point"],
        tool_candidates=["spatial_stats", "webgis_source_profile"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="INLINE",
        priority=10,
    ),
    AlgorithmDescriptor(
        id="stats.category.breakdown", name="类别构成统计",
        capabilities=["category_breakdown"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="stats_table",
        tool_candidates=["spatial_stats"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="INLINE",
        priority=10,
    ),
    # ── 聚合 ─────────────────────────────────────────────────────────
    AlgorithmDescriptor(
        id="spatial.aggregate.admin", name="点落入面聚合（行政区统计）",
        capabilities=["admin_aggregation"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="admin_aggregate_table",
        geometry_requirements=["point"],
        complexity="O(N·M) 点×面",
        tool_candidates=["spatial_aggregate"],
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD",
        compatible_map_models=["administrative_choropleth", "administrative_aggregation"],
        priority=10,
    ),
    AlgorithmDescriptor(
        id="spatial.grid.h3", name="H3 六边形聚合",
        capabilities=["grid_binning"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="grid_aggregate",
        geometry_requirements=["point"],
        complexity="O(N) H3 索引",
        tool_candidates=["h3_binning"],
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD",
        compatible_map_models=["aggregate_grid"],
        fallback_algorithms=["spatial.grid.fishnet"],
        priority=10,
    ),
    AlgorithmDescriptor(
        id="spatial.grid.fishnet", name="渔网格网聚合",
        capabilities=["grid_binning"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="grid_aggregate",
        geometry_requirements=["point"],
        complexity="O(N·M) 点×格",
        tool_candidates=["fishnet_grid"],
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD",
        compatible_map_models=["aggregate_grid"],
        fallback_algorithms=["spatial.grid.h3"],
        priority=20,
    ),
    # ── 密度 ─────────────────────────────────────────────────────────
    AlgorithmDescriptor(
        id="density.visual.heatmap", name="视觉热力（渲染态密度）",
        capabilities=["density_surface"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="density_surface",
        geometry_requirements=["point"],
        min_features=10,               # heatmap_data 工具硬门槛（HEATMAP_MIN_POINTS）
        approximate=True, deterministic=False,
        complexity="O(N) GPU/渲染端",
        tool_candidates=["heatmap_data"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="ASYNC",
        compatible_map_models=["visual_heatmap"],
        priority=10,
    ),
    AlgorithmDescriptor(
        id="spatial.kde.contours", name="核密度等值线",
        capabilities=["kde_density"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="density_surface",
        geometry_requirements=["point"],
        approximate=True, deterministic=False,
        complexity="O(N·grid)",
        tool_candidates=["kde_contours"],
        cpu_cost="high", memory_cost="high", io_cost="low",
        preferred_execution_policy="CELERY",
        compatible_map_models=["visual_heatmap"],
        fallback_algorithms=["spatial.kde.surface"],
        priority=10,
    ),
    AlgorithmDescriptor(
        id="spatial.kde.surface", name="核密度全格网表面",
        capabilities=["kde_density"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="density_surface",
        geometry_requirements=["point"],
        approximate=True, deterministic=False,
        complexity="O(N·grid)",
        tool_candidates=["kde_surface"],
        cpu_cost="high", memory_cost="high", io_cost="low",
        preferred_execution_policy="CELERY",
        compatible_map_models=["visual_heatmap"],
        fallback_algorithms=["spatial.kde.contours"],
        priority=20,
    ),
    AlgorithmDescriptor(
        id="density.analytical.mixed", name="分析密度（KDE/聚合混合路径）",
        capabilities=["analytical_density"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="density_surface",
        geometry_requirements=["point"],
        approximate=True, deterministic=False,
        tool_candidates=["kde_contours", "heatmap_data", "spatial_aggregate"],
        cpu_cost="high", memory_cost="medium", io_cost="low",
        preferred_execution_policy="CELERY",
        compatible_map_models=["administrative_choropleth", "aggregate_grid"],
        # 混合聚合路径（非专有算法）：priority 低于专有算法，使
        # tool_to_capability 的首选工具归属正确（kde_contours →
        # spatial.kde.contours/kde_density，而非本混合路径）。
        priority=30,
    ),
    # ── 热点/邻近/网络 ───────────────────────────────────────────────
    AlgorithmDescriptor(
        id="spatial.hotspot.local", name="局部热点显著性（Getis-Ord Gi*）",
        capabilities=["hotspot"],
        input_artifact_types=["poi_feature_set", "point_feature_set", "grid_aggregate"],
        output_artifact_type="hotspot_result",
        geometry_requirements=["point"],
        tool_candidates=["hotspot_analysis"],
        cpu_cost="high", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD",
        compatible_map_models=["hotspot_overlay"],
        priority=10,
    ),
    AlgorithmDescriptor(
        id="spatial.buffer.proximity", name="距离缓冲区",
        capabilities=["proximity_buffer"],
        input_artifact_types=["poi_feature_set", "point_feature_set",
                              "line_feature_set", "polygon_feature_set"],
        output_artifact_type="proximity_zone",
        tool_candidates=["buffer_analysis"],
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD",
        compatible_map_models=["proximity_overlay"],
        priority=10,
    ),
    AlgorithmDescriptor(
        id="network.isochrone", name="网络等时圈",
        capabilities=["service_area"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="service_area",
        tool_candidates=["isochrone_analysis"],
        cpu_cost="high", memory_cost="medium", io_cost="high",
        preferred_execution_policy="ASYNC",
        compatible_map_models=["proximity_overlay"],
        priority=10,
    ),
    AlgorithmDescriptor(
        id="network.service_area.simple", name="简化服务区（速度表缓冲）",
        capabilities=["service_area"],
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_type="service_area",
        approximate=True,
        tool_candidates=["service_area_simple"],
        cpu_cost="medium", memory_cost="low", io_cost="low",
        preferred_execution_policy="ASYNC",
        compatible_map_models=["proximity_overlay"],
        fallback_algorithms=["network.isochrone"],
        priority=20,
    ),
]


class AlgorithmRegistry:
    """算法目录：by-id / by-capability O(1) 索引、禁止静默重复、稳定排序。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, AlgorithmDescriptor] = {}
        self._by_capability: Dict[str, List[str]] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        self._by_capability.clear()
        for algo in _SEED_ALGORITHMS:
            self.register(algo)

    def register(self, algo: AlgorithmDescriptor) -> None:
        if algo.id in self._by_id:
            raise ValueError(f"duplicate algorithm id: {algo.id}")
        self._by_id[algo.id] = algo
        for cap in algo.capabilities:
            candidates = self._by_capability.setdefault(cap, [])
            if algo.id not in candidates:
                candidates.append(algo.id)
            # 稳定排序：priority 升序，id 兜底
            candidates.sort(
                key=lambda aid: (self._by_id[aid].priority, aid),
            )

    def get(self, algorithm_id: str) -> Optional[AlgorithmDescriptor]:
        return self._by_id.get(algorithm_id)

    def has(self, algorithm_id: str) -> bool:
        return algorithm_id in self._by_id

    def algorithms_for_capability(
        self, capability: str, *, include_planned: bool = False,
    ) -> List[AlgorithmDescriptor]:
        ids = self._by_capability.get(capability, [])
        algos = [self._by_id[i] for i in ids]
        if not include_planned:
            algos = [a for a in algos if a.runtime_status != "unavailable"]
        return algos

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def tool_to_capability(self) -> Dict[str, str]:
        """派生的 tool → 主 capability 反查索引（provenance 回填用）。

        确定性两遍：先把每个算法的**首选**工具（tool_candidates[0]）归给
        该算法的主 capability（spatial_aggregate → admin_aggregation 而非
        把它列为第三候选的 analytical_density），再按 (priority, id) 稳定
        序补齐其余候选。
        """
        ordered = sorted(self._by_id.values(), key=lambda a: (a.priority, a.id))
        mapping: Dict[str, str] = {}
        for algo in ordered:
            cap = algo.capabilities[0] if algo.capabilities else ""
            if cap and algo.tool_candidates:
                mapping.setdefault(algo.tool_candidates[0], cap)
        for algo in ordered:
            cap = algo.capabilities[0] if algo.capabilities else ""
            if not cap:
                continue
            for tool in algo.tool_candidates:
                mapping.setdefault(tool, cap)
        return mapping

    def capability_tool_map(self) -> Dict[str, List[str]]:
        """派生的 capability → 有序工具候选表（兼容视图，非第二事实源）。"""
        mapping: Dict[str, List[str]] = {}
        for cap, ids in self._by_capability.items():
            tools: List[str] = []
            for aid in ids:
                algo = self._by_id[aid]
                for tool in algo.tool_candidates:
                    if tool not in tools:
                        tools.append(tool)
            if tools:
                mapping[cap] = tools
        return mapping

    def validate(self, available_tools: Optional[set] = None) -> List[str]:
        """结构自检：capability/artifact 引用、native 工具存在性。"""
        capabilities = get_capability_registry()
        artifact_types = get_artifact_type_registry()
        issues: List[str] = []
        for algo in self._by_id.values():
            if not algo.capabilities:
                issues.append(f"algorithm {algo.id}: no capability declared")
            for cap in algo.capabilities:
                if not capabilities.has(cap):
                    issues.append(f"algorithm {algo.id}: unknown capability {cap}")
            if algo.output_artifact_type and not artifact_types.has(algo.output_artifact_type):
                issues.append(
                    f"algorithm {algo.id}: unknown output artifact {algo.output_artifact_type}")
            for ref in algo.input_artifact_types:
                if not artifact_types.has(ref):
                    issues.append(f"algorithm {algo.id}: unknown input artifact {ref}")
            if algo.runtime_status == "native" and not algo.tool_candidates:
                issues.append(f"algorithm {algo.id}: native but no tool candidates")
            if available_tools is not None and algo.runtime_status == "native":
                missing = [t for t in algo.tool_candidates if t not in available_tools]
                if missing:
                    issues.append(
                        f"algorithm {algo.id}: tools not registered: {missing}")
            for fb in algo.fallback_algorithms:
                if fb not in self._by_id:
                    issues.append(f"algorithm {algo.id}: fallback algorithm {fb} not registered")
        for cap in capabilities.all_ids:
            if not self._by_capability.get(cap):
                issues.append(f"capability {cap}: no algorithm registered")
        return issues


_registry: Optional[AlgorithmRegistry] = None


def get_algorithm_registry() -> AlgorithmRegistry:
    global _registry
    if _registry is None:
        _registry = AlgorithmRegistry()
        _registry.load_builtins()
    return _registry


def reset_algorithm_registry() -> None:
    global _registry
    _registry = None
