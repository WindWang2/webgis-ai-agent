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
        compatible_map_models=["administrative_choropleth", "administrative_aggregation"],
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
        purpose_template="密度面",
    ),
    CapabilityDescriptor(
        id="kde_density", name="核密度估计", category="density",
        description="KDE 连续密度面/等值线（定量密度表达）。",
        input_artifact_types=["poi_feature_set", "point_feature_set"],
        output_artifact_types=["density_surface"],
        geometry_requirements=["point"],
        deterministic=False,
        compatible_map_models=["visual_heatmap"],
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
