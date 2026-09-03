"""Capability Registry —— Harness 能力面的正式注册表。

Capability 是「需要什么能力」的稳定词汇（recipe/plan 引用它），不绑定
具体工具实现；capability → algorithm → tool 的解析归 AlgorithmResolver。
本注册表取代 planner.py 里手写的 CAPABILITY_TOOLS 知识。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.lib.gis.artifacts import get_artifact_type_registry

CapabilityStatus = Literal["native", "planned", "unavailable"]


class CapabilityDescriptor(BaseModel):
    """一个 GIS 能力的机器可读描述。"""

    id: str
    name: str
    description: str = ""
    # artifact 语义（引用 ArtifactTypeRegistry）
    input_artifact_types: List[str] = Field(default_factory=list)
    output_artifact_types: List[str] = Field(default_factory=list)
    # 输入约束（数据访问类能力可为空 —— 输入是查询参数而非 artifact）
    geometry_requirements: List[str] = Field(default_factory=list)
    required_fields: List[str] = Field(default_factory=list)
    optional_fields: List[str] = Field(default_factory=list)
    domain: str = "general"           # general / network / raster / statistics
    category: str = "analysis"        # data_access / analysis / statistics / density / network / raster
    preferred_execution: str = ""     # 执行偏好提示（local_first / celery / async）
    supports_large_data: bool = True
    deterministic: bool = True
    compatible_map_models: List[str] = Field(default_factory=list)
    fallback_capabilities: List[str] = Field(default_factory=list)
    status: CapabilityStatus = "native"
    version: str = "1.0"
    # plan 里的用途文案（"{subject} 要素获取" 之类；planner 用 subject 格式化）
    purpose_template: str = ""


_SEED_CAPS: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="poi_query", name="POI 要素获取", category="data_access",
        description="按范围/类别获取点要素（本地优先，在线兜底）。",
        output_artifact_types=["poi_feature_set"],
        geometry_requirements=["point"],
        preferred_execution="local_first",
        compatible_map_models=["visual_heatmap", "point_overlay", "simple_point_map",
                               "proportional_symbol", "categorical_thematic"],
        purpose_template="{subject} 要素获取",
        version="1.0",
    ),
    CapabilityDescriptor(
        id="admin_boundary_query", name="行政区边界获取", category="data_access",
        description="获取行政区边界面（本地 SHP 优先）。",
        output_artifact_types=["admin_boundary_set"],
        geometry_requirements=["polygon"],
        compatible_map_models=["administrative_aggregation"],
        purpose_template="行政边界/区划面获取",
    ),
    CapabilityDescriptor(
        id="admin_aggregation", name="行政区聚合统计", category="analysis",
        description="点落入面聚合（各区数量）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["admin_aggregate_table"],
        geometry_requirements=["point"],
        compatible_map_models=["administrative_choropleth", "administrative_aggregation", "extrusion_3d"],
        purpose_template="按行政区聚合统计",
    ),
    CapabilityDescriptor(
        id="point_profile", name="数据画像", category="statistics",
        description="点数/几何/字段画像（不产出新数据，产出元数据）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["point_feature_set"],
        geometry_requirements=["point"],
        purpose_template="数据画像（点数/几何/字段）",
    ),
    CapabilityDescriptor(
        id="density_surface", name="视觉密度面", category="density",
        description="视觉热力（回答『大概哪儿密』，非定量）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["density_surface"],
        geometry_requirements=["point"],
        deterministic=False,
        compatible_map_models=["visual_heatmap"],
        # ADR-0083：超过原生渲染上限（FETCH_FEATURE_CAP 20k）时的确定性
        # 降级 —— 聚合通道（H3/渔网）承接大规模点数据。与 grid_binning 的
        # 反向 fallback（稀疏点 → 视觉热力）构成双向边，环路由 resolver
        # 的 _visited 守卫截断。
        fallback_capabilities=["grid_binning"],
        purpose_template="密度面",
    ),
    CapabilityDescriptor(
        id="kde_density", name="核密度估计", category="density",
        description="KDE 连续密度面/等值线（定量密度表达）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["density_surface"],
        geometry_requirements=["point"],
        deterministic=False,
        compatible_map_models=["visual_heatmap", "isoline_contour"],
        purpose_template="核密度分析",
    ),
    CapabilityDescriptor(
        id="hotspot", name="热点显著性分析", category="statistics",
        description="Getis-Ord Gi* 等空间聚类显著性检验。",
        input_artifact_types=["poi_feature_set", "point_feature_set", "grid_aggregate"],
        output_artifact_types=["hotspot_result"],
        geometry_requirements=["point"],
        compatible_map_models=["hotspot_overlay"],
        purpose_template="热点显著性分析",
    ),
    CapabilityDescriptor(
        id="category_breakdown", name="类别构成统计", category="statistics",
        description="按类别字段统计构成。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["stats_table"],
        purpose_template="类别构成统计",
    ),
    CapabilityDescriptor(
        id="proximity_buffer", name="邻近缓冲", category="analysis",
        description="距离缓冲区生成。",
        input_artifact_types=["poi_feature_set", "point_feature_set",
                              "line_feature_set", "polygon_feature_set"],
        output_artifact_types=["proximity_zone"],
        compatible_map_models=["proximity_overlay"],
        purpose_template="邻近缓冲",
    ),
    CapabilityDescriptor(
        id="service_area", name="网络服务区", category="network",
        domain="network",
        description="等时圈/网络可达服务区。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["service_area"],
        compatible_map_models=["proximity_overlay"],
        purpose_template="网络服务区",
    ),
    CapabilityDescriptor(
        id="raster_source", name="栅格数据源", category="raster",
        domain="raster",
        description="DEM/遥感栅格获取。",
        output_artifact_types=["terrain_surface", "raster_surface"],
        geometry_requirements=["raster"],
        compatible_map_models=["raster_surface"],
        purpose_template="栅格数据源",
    ),
    CapabilityDescriptor(
        id="grid_binning", name="格网聚合", category="density",
        description="点聚合入 H3 六边形/渔网格网。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["grid_aggregate"],
        geometry_requirements=["point"],
        compatible_map_models=["aggregate_grid"],
        fallback_capabilities=["density_surface"],
        purpose_template="H3/渔网格网聚合",
    ),
    CapabilityDescriptor(
        id="analytical_density", name="分析密度", category="density",
        description="定量密度（每平方公里密度等）——拒绝把视觉热力当定量结果。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["density_surface", "admin_aggregate_table"],
        geometry_requirements=["point"],
        deterministic=False,
        compatible_map_models=["administrative_choropleth", "aggregate_grid"],
        purpose_template="分析密度面/密度聚合",
    ),
    CapabilityDescriptor(
        id="global_morans_i", name="全局莫兰指数", category="statistics",
        description="全局空间自相关检验。",
        input_artifact_types=["poi_feature_set", "point_feature_set", "admin_aggregate_table"],
        output_artifact_types=["stats_table"],
        purpose_template="全局莫兰指数",
    ),
    CapabilityDescriptor(
        id="local_morans_i", name="局部莫兰/LISA", category="statistics",
        description="局部热点/冷点聚类。",
        input_artifact_types=["poi_feature_set", "point_feature_set", "admin_aggregate_table"],
        output_artifact_types=["hotspot_result"],
        compatible_map_models=["hotspot_overlay"],
        purpose_template="LISA 聚类",
    ),
    CapabilityDescriptor(
        id="getis_ord_gi_star", name="Getis-Ord Gi*", category="statistics",
        description="热点显著性 Gi*。",
        input_artifact_types=["poi_feature_set", "point_feature_set", "grid_aggregate"],
        output_artifact_types=["hotspot_result"],
        compatible_map_models=["hotspot_overlay"],
        purpose_template="Gi* 热点分析",
    ),
    CapabilityDescriptor(
        id="geometry_buffer", name="几何缓冲", category="analysis",
        description="点/线/面缓冲几何。",
        input_artifact_types=["poi_feature_set", "point_feature_set", "line_feature_set", "polygon_feature_set"],
        output_artifact_types=["proximity_zone"],
        purpose_template="几何缓冲",
    ),
    CapabilityDescriptor(
        id="geometry_clip", name="几何裁剪", category="analysis",
        description="要素裁剪。",
        input_artifact_types=["poi_feature_set", "point_feature_set", "polygon_feature_set"],
        output_artifact_types=["polygon_feature_set"],
        purpose_template="几何裁剪",
    ),
    CapabilityDescriptor(
        id="geometry_dissolve", name="融合/溶解", category="analysis",
        description="同属性面融合。",
        input_artifact_types=["polygon_feature_set", "admin_boundary_set"],
        output_artifact_types=["polygon_feature_set"],
        purpose_template="融合溶解",
    ),
    CapabilityDescriptor(
        id="spatial_interpolation", name="空间插值", category="analysis",
        description="IDW / Kriging 等插值。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["terrain_surface"],
        purpose_template="空间插值",
    ),
    CapabilityDescriptor(
        id="terrain_slope", name="坡度分析", category="raster",
        domain="raster", description="DEM 坡度。",
        input_artifact_types=["terrain_surface"],
        output_artifact_types=["terrain_surface"],
        purpose_template="坡度分析",
    ),
    CapabilityDescriptor(
        id="terrain_aspect", name="坡向分析", category="raster",
        domain="raster", description="DEM 坡向。",
        input_artifact_types=["terrain_surface"],
        output_artifact_types=["terrain_surface"],
        purpose_template="坡向分析",
    ),
    CapabilityDescriptor(
        id="terrain_hillshade", name="山体阴影", category="raster",
        domain="raster", description="DEM 山体阴影。",
        input_artifact_types=["terrain_surface"],
        output_artifact_types=["terrain_surface"],
        purpose_template="山体阴影",
    ),
    CapabilityDescriptor(
        id="ndvi", name="NDVI 植被指数", category="raster",
        domain="raster", description="遥感 NDVI 计算。",
        input_artifact_types=["terrain_surface", "raster_surface"],
        output_artifact_types=["raster_surface"],
        purpose_template="NDVI 指数",
    ),
    # Runtime V3（ADR-0089）：栅格计算器此前错挂在 raster_source（“栅格获取”）
    # 上 —— tool_to_capability 因此把 raster_calculator 归为“数据获取”，语义
    # 谎报。band_math 是它真实的语义位（逐像元波段/栅格代数）。
    CapabilityDescriptor(
        id="band_math", name="波段/栅格代数", category="raster",
        domain="raster",
        description="逐像元栅格代数（A/B 表达式、常数运算；A 为基准网格，B 自动对齐）。",
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface"],
        purpose_template="波段/栅格代数",
    ),
    # Runtime V3（ADR-0089 §变化语义）：raster 图像变化与 vector 时序变化是
    # 两种不同的分析。既有 change_detection capability 只由 temporal.change
    # （矢量时序）实现，此前把 raster_surface 列进输入词表是谎报 —— 栅格
    # 变化现在有自己的 capability（detect_raster_change，native）。
    CapabilityDescriptor(
        id="raster_change_detection", name="双时相栅格变化检测", category="raster",
        domain="raster",
        description="两个栅格工件的对齐像元级变化检测（差值/绝对差/归一化差 + 阈值分类）。",
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface", "change_set"],
        compatible_map_models=["raster_surface"],
        purpose_template="双时相栅格变化检测",
    ),
    CapabilityDescriptor(
        id="shortest_path", name="最短路径", category="network",
        domain="network", description="网络最短路径。",
        output_artifact_types=["line_feature_set"],
        purpose_template="最短路径",
    ),
    # ── 网络族（phase-2 接入 + v2(audit R2) parity 收口，两线合并取并集）──
    # phase-2 给出更宽的输入面与版面兼容（flow_od_arc/proximity_overlay）；
    # audit R2 保证五项网络语义全部有 capability 归属（无孤儿工具）。
    CapabilityDescriptor(
        id="closest_facility", name="最近设施", category="network",
        domain="network", description="从需求点到设施集合的 top-K 最近路径。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["line_feature_set"],
        purpose_template="最近设施分析",
    ),
    CapabilityDescriptor(
        id="accessibility", name="网络可达性", category="network",
        domain="network", description="需求点对设施集合的可达性指标计算（15 分钟生活圈等）。",
        input_artifact_types=["point_feature_set"],
        output_artifact_types=["service_area", "stats_table"],
        compatible_map_models=["proximity_overlay"],
        purpose_template="网络可达性分析",
    ),
    CapabilityDescriptor(
        id="od_matrix", name="OD 成本矩阵", category="network",
        domain="network", description="多起点×终点网络成本矩阵。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["od_matrix"],
        compatible_map_models=["flow_od_arc"],
        purpose_template="起讫点（OD）矩阵",
    ),
    CapabilityDescriptor(
        # ADR-0092 D：OD 边 → 带权流向线要素（flow_od_arc 渲染输入）。
        id="od_flow_mapping", name="OD 流向图", category="network",
        domain="network", description="把 OD 对（坐标+权重）构建为有界流向线要素层。",
        input_artifact_types=["od_matrix", "od_table", "line_feature_set"],
        output_artifact_types=["line_feature_set"],
        compatible_map_models=["flow_od_arc"],
        purpose_template="OD 流向表达",
    ),
    CapabilityDescriptor(
        id="location_allocation", name="区位配置", category="network",
        domain="network", description="设施选址-分配优化（tier-3 门控）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["point_feature_set", "stats_table"],
        purpose_template="区位配置优化",
    ),
    CapabilityDescriptor(
        id="route_optimization", name="路线优化", category="network",
        domain="network", description="多站点访问顺序优化（VRP，tier-3 门控）。",
        input_artifact_types=["point_feature_set"],
        output_artifact_types=["line_feature_set"],
        purpose_template="访问路线优化",
    ),
    # ── 时序族（phase-2 正式词汇 + #1075(D-10) temporal_trend 就位）──────
    CapabilityDescriptor(
        id="temporal_profile", name="时间画像", category="statistics",
        description="时间字段/跨度/粒度画像（元数据，不产新数据）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["stats_table"],
        purpose_template="时间维度画像",
    ),
    CapabilityDescriptor(
        id="temporal_aggregate", name="时间聚合", category="statistics",
        description="按时间窗重采样汇总。",
        input_artifact_types=["point_feature_set", "poi_feature_set"],
        output_artifact_types=["stats_table"],
        purpose_template="时间聚合统计",
    ),
    # #1075(D-10): temporal_trend capability 就位 —— temporal.* 算法此前因
    # 该 capability 缺失被 if False 挂到 spatial_interpolation 上。
    CapabilityDescriptor(
        id="temporal_trend", name="时序趋势", category="analysis",
        domain="statistics", description="时间维度的趋势/聚合/时空热点分析。",
        input_artifact_types=["poi_feature_set", "raster_surface", "stats_table"],
        output_artifact_types=["stats_table", "raster_surface"],
        purpose_template="时序趋势",
    ),
    CapabilityDescriptor(
        id="change_detection", name="时序要素变化检测", category="analysis",
        description="矢量要素的双时相对比变化集（栅格图像变化用 raster_change_detection）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["change_set"],
        purpose_template="时序变化检测",
    ),
    CapabilityDescriptor(
        id="spatiotemporal_clustering", name="时空聚类", category="statistics",
        description="ST-DBSCAN 等时空聚类（与 LISA 局部自相关是不同检验）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["hotspot_result"],
        purpose_template="时空聚类",
    ),
    # ── 几何/栅格统计错挂修复配套词汇 ──────────────────────────────────
    CapabilityDescriptor(
        id="spatial_join", name="空间连接", category="analysis",
        description="按拓扑关系把右表属性挂到左表（区别于几何裁剪）。",
        input_artifact_types=["poi_feature_set", "polygon_feature_set"],
        output_artifact_types=["polygon_feature_set"],
        purpose_template="空间连接",
    ),
    CapabilityDescriptor(
        id="zonal_statistics", name="分区统计", category="raster",
        domain="raster", description="面内栅格 min/max/mean/sum 统计。",
        input_artifact_types=["raster_surface", "polygon_feature_set"],
        output_artifact_types=["stats_table"],
        purpose_template="分区统计",
    ),
    CapabilityDescriptor(
        id="raster_reclassify", name="栅格重分类", category="raster",
        domain="raster", description="连续栅格值按方案映射为离散类别。",
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface"],
        purpose_template="栅格重分类",
    ),
    CapabilityDescriptor(
        id="raster_resample", name="栅格重采样", category="raster",
        domain="raster", description="改变像元大小和/或 CRS（对齐预处理）。",
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface"],
        purpose_template="栅格重采样",
    ),
]


class CapabilityRegistry:
    """capability 目录：O(1) by id、可枚举、可校验、禁止静默重复。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, CapabilityDescriptor] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        for cap in _SEED_CAPS:
            self.register(cap)

    def register(self, cap: CapabilityDescriptor) -> None:
        if cap.id in self._by_id:
            raise ValueError(f"duplicate capability id: {cap.id}")
        self._by_id[cap.id] = cap

    def get(self, capability_id: str) -> Optional[CapabilityDescriptor]:
        return self._by_id.get(capability_id)

    def has(self, capability_id: str) -> bool:
        return capability_id in self._by_id

    def purpose_for(self, capability_id: str, subject: str = "") -> str:
        """plan 用途文案：registry 的 purpose_template 优先，缺省回 id。"""
        cap = self._by_id.get(capability_id)
        if cap is None or not cap.purpose_template:
            return capability_id
        if "{subject}" in cap.purpose_template:
            return cap.purpose_template.format(subject=subject or "主体")
        return cap.purpose_template

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def validate(self) -> List[str]:
        """结构自检（artifact 引用存在性等）。空列表 = 通过。"""
        artifact_types = get_artifact_type_registry()
        issues: List[str] = []
        for cap in self._by_id.values():
            for ref in cap.input_artifact_types + cap.output_artifact_types:
                if not artifact_types.has(ref):
                    issues.append(f"capability {cap.id}: unknown artifact type {ref}")
            for fb in cap.fallback_capabilities:
                if fb not in self._by_id:
                    issues.append(f"capability {cap.id}: fallback capability {fb} not registered")
        return issues


_registry: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    global _registry
    if _registry is None:
        _registry = CapabilityRegistry()
        _registry.load_builtins()
    return _registry


def reset_capability_registry() -> None:
    global _registry
    _registry = None
