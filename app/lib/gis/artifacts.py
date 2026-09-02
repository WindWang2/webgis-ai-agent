"""GIS Artifact Semantic Types —— 算法输入/输出的机器可读语义层。

Artifact 不是数据的第二份拷贝：它只描述「计算得到了什么」（语义类型 +
几何族 + ref + 生产者 + 有界画像），GeoJSON 本体仍由 SessionStore/ref
体系承载。该层让 AlgorithmRegistry / MapModelRegistry / TemplateSelector
可以在不加载 FeatureCollection 的前提下做兼容性裁决。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

ARTIFACT_SCHEMA_VERSION = 1

# 有界性约束：descriptor 是 metadata，不是数据搬运工。
_MAX_FIELDS = 64
_MAX_LINEAGE = 16
_MAX_PROPERTIES = 32

GeometryKind = str  # point / line / polygon / raster / table / network / unknown


class ArtifactTypeDescriptor(BaseModel):
    """一个 artifact 语义类型的注册描述。"""

    id: str
    name_zh: str
    geometry_kind: GeometryKind
    category: str = ""          # feature_set / surface / aggregate / zone / raster / change / network / table
    description: str = ""
    typical_map_models: List[str] = Field(default_factory=list)  # 文档性：常见表达模型


SEED_ARTIFACT_TYPES: List[ArtifactTypeDescriptor] = [
    # V2(P2)：registry 下位词（service 层 infer_artifact_type 的兜底产出）。
    # 此前 ArtifactRecord.artifact_type 自由串漂移 —— infer 可产出
    # feature_collection / chart_spec，但二者未注册，ArtifactDescriptor
    # 校验与 registry 词表脱节。收编为正式类型，保证「registry 能产出的
    # artifact_type 一定是注册词」。
    ArtifactTypeDescriptor(
        id="feature_collection", name_zh="通用要素集", geometry_kind="unknown",
        category="feature_set",
        description="无更细语义的通用 FeatureCollection（capability 上下文缺席时的兜底推断）。",
    ),
    ArtifactTypeDescriptor(
        id="chart_spec", name_zh="图表规格", geometry_kind="table",
        category="table",
        description="chart 通道产物（chartRef 绑定的图表 payload）。",
    ),
    ArtifactTypeDescriptor(
        id="point_feature_set", name_zh="点要素集", geometry_kind="point",
        category="feature_set",
        typical_map_models=["simple_point_map", "point_overlay"],
    ),
    ArtifactTypeDescriptor(
        id="line_feature_set", name_zh="线要素集", geometry_kind="line",
        category="feature_set",
    ),
    ArtifactTypeDescriptor(
        id="polygon_feature_set", name_zh="面要素集", geometry_kind="polygon",
        category="feature_set",
    ),
    ArtifactTypeDescriptor(
        id="poi_feature_set", name_zh="POI 要素集", geometry_kind="point",
        category="feature_set",
        description="带类别/名称属性的点要素（查询产物）。",
        typical_map_models=["visual_heatmap", "point_overlay", "simple_point_map",
                            "proportional_symbol", "categorical_thematic"],
    ),
    ArtifactTypeDescriptor(
        id="admin_boundary_set", name_zh="行政区边界集", geometry_kind="polygon",
        category="feature_set",
        typical_map_models=["administrative_aggregation"],
    ),
    ArtifactTypeDescriptor(
        id="admin_aggregate_table", name_zh="行政区聚合表", geometry_kind="polygon",
        category="aggregate",
        description="按行政区聚合的统计结果（面 + 数值字段）。",
        typical_map_models=["administrative_choropleth", "administrative_aggregation"],
    ),
    ArtifactTypeDescriptor(
        id="stats_table", name_zh="统计表", geometry_kind="table",
        category="table",
        description="无几何或弱几何的统计结果（类别构成、排名等）。",
    ),
    ArtifactTypeDescriptor(
        id="density_surface", name_zh="密度面", geometry_kind="polygon",
        category="surface",
        description="KDE 等连续密度估计（面/等值线或网格载体）。",
        typical_map_models=["visual_heatmap", "raster_surface"],
    ),
    ArtifactTypeDescriptor(
        id="grid_aggregate", name_zh="格网聚合", geometry_kind="polygon",
        category="aggregate",
        description="H3/渔网格网计数或加权聚合。",
        typical_map_models=["aggregate_grid"],
    ),
    ArtifactTypeDescriptor(
        id="hotspot_result", name_zh="热点结果", geometry_kind="point",
        category="aggregate",
        description="Getis-Ord Gi*/LISA 显著性聚类结果。",
        typical_map_models=["hotspot_overlay"],
    ),
    ArtifactTypeDescriptor(
        id="proximity_zone", name_zh="邻近缓冲区", geometry_kind="polygon",
        category="zone",
        typical_map_models=["proximity_overlay"],
    ),
    ArtifactTypeDescriptor(
        id="service_area", name_zh="服务区", geometry_kind="polygon",
        category="zone",
        description="等时圈/网络服务区面。",
        typical_map_models=["proximity_overlay"],
    ),
    ArtifactTypeDescriptor(
        id="raster_surface", name_zh="栅格面", geometry_kind="raster",
        category="raster",
        typical_map_models=["raster_surface"],
    ),
    ArtifactTypeDescriptor(
        id="terrain_surface", name_zh="地形面", geometry_kind="raster",
        category="raster",
        description="DEM 派生的坡度/坡向/高程等栅格。",
        typical_map_models=["raster_surface"],
    ),
    ArtifactTypeDescriptor(
        id="remote_sensing_index", name_zh="遥感指数面", geometry_kind="raster",
        category="raster",
        description="NDVI/NDWI 等波段运算产物。",
        typical_map_models=["raster_surface"],
    ),
    ArtifactTypeDescriptor(
        id="change_set", name_zh="变化集", geometry_kind="polygon",
        category="change",
        description="两期变化检测的结果图斑/栅格。",
    ),
    ArtifactTypeDescriptor(
        id="od_matrix", name_zh="OD 矩阵", geometry_kind="network",
        category="network",
        typical_map_models=["flow_od_arc"],
    ),
    ArtifactTypeDescriptor(
        # ADR-0092 D1：结构化 OD 边表（origin/destination 坐标对 + weight）。
        # 与 od_matrix（纯成本表）的区别：携带坐标，可直接构建流向线要素。
        id="od_table", name_zh="OD 边表", geometry_kind="network",
        category="network",
        typical_map_models=["flow_od_arc"],
    ),
    ArtifactTypeDescriptor(
        id="network_graph", name_zh="网络图", geometry_kind="network",
        category="network",
    ),
]


class ArtifactTypeRegistry:
    """artifact 语义类型目录：O(1) by id、可枚举、禁止静默重复。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, ArtifactTypeDescriptor] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        for desc in SEED_ARTIFACT_TYPES:
            self.register(desc)

    def register(self, desc: ArtifactTypeDescriptor) -> None:
        if desc.id in self._by_id:
            raise ValueError(f"duplicate artifact type id: {desc.id}")
        self._by_id[desc.id] = desc

    def get(self, artifact_type: str) -> Optional[ArtifactTypeDescriptor]:
        return self._by_id.get(artifact_type)

    def has(self, artifact_type: str) -> bool:
        return artifact_type in self._by_id

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def validate(self) -> List[str]:
        issues: List[str] = []
        for tid, desc in self._by_id.items():
            if desc.geometry_kind not in (
                    "point", "line", "polygon", "raster", "table", "network", "unknown"):
                issues.append(f"artifact type {tid}: unknown geometry_kind {desc.geometry_kind}")
        return issues


_registry: Optional[ArtifactTypeRegistry] = None


def get_artifact_type_registry() -> ArtifactTypeRegistry:
    global _registry
    if _registry is None:
        _registry = ArtifactTypeRegistry()
        _registry.load_builtins()
    return _registry


def reset_artifact_type_registry() -> None:
    global _registry
    _registry = None


def artifact_type(artifact_type_id: str) -> Optional[ArtifactTypeDescriptor]:
    return get_artifact_type_registry().get(artifact_type_id)


def _dominant_geometry(geometry_types: Optional[List[str]]) -> str:
    """把 GeoJSON geometry 类型列表归约为单一几何族（与 recipes 口径一致）。"""
    point_family = {"Point", "MultiPoint"}
    line_family = {"LineString", "MultiLineString"}
    polygon_family = {"Polygon", "MultiPolygon"}
    types = set(geometry_types or [])
    if types & point_family:
        return "point"
    if types & polygon_family:
        return "polygon"
    if types & line_family:
        return "line"
    return "unknown"


class ArtifactDescriptor(BaseModel):
    """一次计算产物的语义描述（metadata + ref，绝不复制大 GeoJSON）。"""

    id: str = ""
    artifact_type: str
    geometry_kind: GeometryKind = ""
    data_ref: str = ""                 # ref: 游标 / 图层 id / 栅格 ref
    source_capability: str = ""
    producer_algorithm: str = ""       # AlgorithmRegistry id
    producer_tool: str = ""            # ToolRegistry 名（执行事实）
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    feature_count: Optional[int] = None
    fields: List[str] = Field(default_factory=list)
    crs: str = ""
    units: str = ""
    properties: Dict[str, Any] = Field(default_factory=dict)
    lineage: List[str] = Field(default_factory=list)  # 上游 artifact id / ref

    @field_validator("artifact_type")
    @classmethod
    def _artifact_type_registered(cls, v: str) -> str:
        if not get_artifact_type_registry().has(v):
            raise ValueError(f"unregistered artifact_type: {v}")
        return v

    @field_validator("fields")
    @classmethod
    def _bounded_fields(cls, v: List[str]) -> List[str]:
        if len(v) > _MAX_FIELDS:
            raise ValueError(f"fields exceed bound {_MAX_FIELDS}")
        return v

    @field_validator("lineage")
    @classmethod
    def _bounded_lineage(cls, v: List[str]) -> List[str]:
        if len(v) > _MAX_LINEAGE:
            raise ValueError(f"lineage exceed bound {_MAX_LINEAGE}")
        return v

    @field_validator("properties")
    @classmethod
    def _bounded_properties(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if len(v) > _MAX_PROPERTIES:
            raise ValueError(f"properties exceed bound {_MAX_PROPERTIES}")
        return v

    def resolve_type(self) -> Optional[ArtifactTypeDescriptor]:
        return artifact_type(self.artifact_type)


def artifact_from_profile(
    artifact_type_id: str,
    profile: Optional[Dict[str, Any]],
    *,
    artifact_id: str = "",
    data_ref: str = "",
    source_capability: str = "",
    producer_algorithm: str = "",
    producer_tool: str = "",
    lineage: Optional[List[str]] = None,
) -> ArtifactDescriptor:
    """从 Spatial Profile（descriptor 派生或全量扫描产物）构建 descriptor。

    只读 profile 的 O(1) 字段；fields 截断到上界（profile 可能带几十个字段）。
    """
    prof = profile or {}
    geometry_types = prof.get("geometryTypes") or []
    fields = [str(f) for f in (prof.get("fields") or {}).keys()][:_MAX_FIELDS]
    feature_count = prof.get("featureCount")
    return ArtifactDescriptor(
        id=artifact_id,
        artifact_type=artifact_type_id,
        geometry_kind=_dominant_geometry(
            [str(g) for g in geometry_types] if isinstance(geometry_types, list) else [],
        ),
        data_ref=data_ref,
        source_capability=source_capability,
        producer_algorithm=producer_algorithm,
        producer_tool=producer_tool,
        feature_count=int(feature_count) if isinstance(feature_count, (int, float)) else None,
        fields=fields,
        crs=str(prof.get("crs") or ""),
        lineage=(lineage or [])[:_MAX_LINEAGE],
    )


def artifact_from_ref_descriptor(
    artifact_type_id: str,
    ref_descriptor: Dict[str, Any],
    *,
    source_capability: str = "",
    producer_algorithm: str = "",
    producer_tool: str = "",
) -> ArtifactDescriptor:
    """从 RefDescriptor dict（ref 体系元数据）构建 descriptor —— O(1)、零扫描。"""
    geometry_types = ref_descriptor.get("geometry_types") or []
    return ArtifactDescriptor(
        artifact_type=artifact_type_id,
        geometry_kind=_dominant_geometry(
            [str(g) for g in geometry_types] if isinstance(geometry_types, list) else [],
        ),
        data_ref=str(ref_descriptor.get("ref_id") or ""),
        source_capability=source_capability,
        producer_algorithm=producer_algorithm,
        producer_tool=producer_tool,
        feature_count=(
            int(ref_descriptor["feature_count"])
            if isinstance(ref_descriptor.get("feature_count"), (int, float))
            else None
        ),
        fields=[str(f) for f in (ref_descriptor.get("filterable_fields") or {})][:_MAX_FIELDS]
        if isinstance(ref_descriptor.get("filterable_fields"), dict)
        else [],
    )
