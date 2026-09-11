"""Cartographic Design System manifest — V4 统一契约投影.

本模块**不建立第二套真相源**：它是既有四个 registry（model / component /
composition / theme）+ renderer 支持矩阵 + chart kind 词表的**单一投影
（manifest）**，供 Workflow / Agent 侧以一份带版本的快照回答：

- 「这个模型要什么数据、配什么组件、缺省什么主题、能导出什么」
  → :func:`resolve_map_model_requirement`
- 「这个组件有哪些 variant、占什么位置、可做什么状态迁移、导出支持什么」
  → :func:`resolve_component_requirement`
- 「整套设计系统现在长什么样」→ :func:`build_design_system_manifest`

边界（Goal §十一）：本分支只提供 descriptor / capability / requirement
**描述**，不重写 Workflow planner 与 Pi runtime。planner 读取 requirement
后如何请求 ``map_model_requirement`` / ``component_requirement`` 属于
workflow 域，由既有 contract 通道承接。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.cartography.chart_kinds import (
    AGENT_CHART_OPERATIONS,
    CHART_STATES,
    CHART_KINDS,
    can_transition,
)

DESIGN_SYSTEM_SCHEMA_VERSION = 4


def build_design_system_manifest() -> Dict[str, Any]:
    """四个 registry + 能力矩阵的单份确定性投影（纯读取，无 I/O）。"""
    from app.lib.cartography.model_library import get_map_model_registry
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    from app.lib.cartography.themes import get_cartographic_theme_registry
    from app.lib.cartography.component_renderers import (
        get_component_renderer_registry,
    )

    model_reg = get_map_model_registry()
    comp_reg = get_component_registry()
    tmpl_reg = get_composition_template_registry()
    theme_reg = get_cartographic_theme_registry()
    renderer_reg = get_component_renderer_registry()

    native = model_reg.native_ids()
    planned = model_reg.planned_ids()
    return {
        "schemaVersion": DESIGN_SYSTEM_SCHEMA_VERSION,
        "counts": {
            "mapModels": model_reg.count,
            "mapModelsNative": len(native),
            "mapModelsPlanned": len(planned),
            "componentTypes": comp_reg.count,
            "componentVariants": sum(
                len(d.variants) for d in comp_reg.native_descriptors()
            ),
            "compositionTemplates": tmpl_reg.count,
            "themes": len(theme_reg.themes()),
            "palettes": len(theme_reg.palettes()),
            "chartKinds": len(CHART_KINDS),
        },
        "mapModels": {
            "native": native,
            "planned": planned,
        },
        "componentTypes": [
            {
                "id": d.id,
                "type": d.type,
                "category": d.category,
                "placementDomain": d.placement_domain,
                "variants": list(d.variants),
                "defaultVariant": d.default_variant,
                "states": list(d.states),
                "collisionClass": d.collision_class,
                "responsive": d.responsive,
                "interactions": list(d.interactions),
                "runtimeStatus": d.runtime_status,
                # V7（Goal 08）：语义角色/弃用/预览投影（additive）。
                "semanticRole": d.semantic_role,
                "deprecated": d.deprecated,
                "deprecatedBy": d.deprecated_by,
                "preview": {
                    "glyph": d.preview.glyph,
                    "accent": d.preview.accent,
                },
            }
            for d in sorted(
                comp_reg.native_descriptors(), key=lambda d: d.id)
        ],
        "compositionTemplates": sorted(tmpl_reg.all_ids),
        "themes": [t.id for t in theme_reg.themes()],
        "rendererCapability": {
            t: {
                "renderers": s.renderers,
                "exporters": s.exporters,
            }
            for t in renderer_reg.all_types
            for s in [renderer_reg.support_for(t)]
            if s is not None
        },
        "chartKinds": [
            {
                "id": k.id,
                "dataShape": k.data_shape,
                "liveEngine": k.live_engine,
                "exportLevel": k.export_level,
                "selectionLinkage": k.selection_linkage,
            }
            for k in sorted(CHART_KINDS, key=lambda k: k.id)
        ],
        "chartStates": sorted(CHART_STATES),
        "agentChartOperations": {
            op: sorted(pre) for op, pre in sorted(AGENT_CHART_OPERATIONS.items())
        },
    }


class MapModelRequirement(BaseModel):
    """`map_model_requirement` 契约：模型作为主表达的完整需求描述。"""

    model_id: str
    runtime_status: str
    semantic_purpose_zh: str = ""
    data_requirements: Dict[str, Any] = Field(default_factory=dict)
    preconditions_zh: List[str] = Field(default_factory=list)
    layer_composition: Dict[str, Any] = Field(default_factory=dict)
    required_components: List[str] = Field(default_factory=list)
    recommended_components: List[str] = Field(default_factory=list)
    default_theme: str = ""
    legend_needs: Dict[str, Any] = Field(default_factory=dict)
    chart_needs: List[str] = Field(default_factory=list)
    interaction_needs: List[str] = Field(default_factory=list)
    export_constraints: Dict[str, Any] = Field(default_factory=dict)
    fallback_model_id: str = ""
    pitfalls_zh: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ComponentRequirement(BaseModel):
    """`component_requirement` 契约：组件实例化的完整约束描述。"""

    component_type: str
    descriptor_id: str
    variant: str = ""
    variants: List[str] = Field(default_factory=list)
    runtime_status: str = "native"
    placement_domain: str = "overlay"
    allowed_positions: List[str] = Field(default_factory=list)
    default_position: str = "none"
    states: List[str] = Field(default_factory=list)
    collision_class: str = "panel"
    size_range: Dict[str, Any] = Field(default_factory=dict)
    interactions: List[str] = Field(default_factory=list)
    cardinality: str = "single"
    requires_layer_binding: bool = False
    export_support: List[str] = Field(default_factory=list)
    renderer_support: List[str] = Field(default_factory=list)
    compatible_map_models: List[str] = Field(default_factory=list)
    accessibility: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


def resolve_map_model_requirement(model_or_alias_id: str) -> Optional[MapModelRequirement]:
    """从权威目录投影模型需求；未注册模型返回 None（不编造）。"""
    from app.lib.cartography.model_library import get_map_model_registry

    model = get_map_model_registry().resolve(model_or_alias_id)
    if model is None:
        return None

    # legend 需求：从推荐组件里辨认图例族（categorical/graduated/colorbar）
    legend_family = [
        c for c in model.recommended_components
        if c in ("legend", "categorical_legend", "continuous_colorbar")
    ]
    legend_needs: Dict[str, Any] = {"components": legend_family}
    if model.classification in ("graduated", "quantiles", "equal_interval",
                                "natural_breaks", "std_dev", "head_tail"):
        legend_needs["kind"] = "graduated"
    elif model.classification == "categorical":
        legend_needs["kind"] = "categorical"
    elif legend_family and model.maplibre_layer_type == "raster":
        legend_needs["kind"] = "continuous"

    req = MapModelRequirement(
        model_id=model.id,
        runtime_status=model.runtime_status,
        semantic_purpose_zh=model.purpose_zh,
        data_requirements={
            "geometryKinds": list(model.geometry_kinds),
            "acceptedArtifactTypes": list(model.accepted_artifact_types),
            "classification": model.classification,
            "recommendedClassifiers": list(model.recommended_classifiers),
            "defaultClassCount": model.default_class_count,
            "colorSchemeKind": model.color_scheme_kind,
            "defaultPalette": model.default_palette,
        },
        preconditions_zh=list(model.data_preconditions_zh),
        layer_composition={
            "maplibreLayerType": model.maplibre_layer_type,
            "geometryLayerTypes": dict(model.geometry_layer_types),
            "deckGlLayer": model.deck_gl_layer,
            "keplerLayer": model.kepler_layer,
            "qgisRenderer": model.qgis_renderer,
        },
        required_components=[
            c for c in model.recommended_components
            if c in ("legend", "categorical_legend", "continuous_colorbar", "title")
        ],
        recommended_components=list(model.recommended_components),
        default_theme=model.default_theme,
        legend_needs=legend_needs,
        chart_needs=list(model.chart_needs),
        interaction_needs=list(model.interaction_needs),
        export_constraints={
            "formats": list(model.export_compatibility),
        },
        fallback_model_id=model.fallback_model_id,
        pitfalls_zh=list(model.pitfalls_zh),
    )
    return req


def resolve_component_requirement(
    component_type: str, variant: str = ""
) -> Optional[ComponentRequirement]:
    """从权威目录投影组件需求；未注册类型/variant 返回 None。"""
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.component_renderers import (
        get_component_renderer_registry,
    )

    desc = get_component_registry().get_by_type(component_type)
    if desc is None:
        return None
    if variant and variant not in desc.variants:
        return None
    support = get_component_renderer_registry().support_for(component_type)
    return ComponentRequirement(
        component_type=desc.type,
        descriptor_id=desc.id,
        variant=variant or desc.default_variant,
        variants=list(desc.variants),
        runtime_status=desc.runtime_status,
        placement_domain=desc.placement_domain,
        allowed_positions=list(desc.allowed_positions),
        default_position=desc.default_position,
        states=list(desc.states),
        collision_class=desc.collision_class,
        size_range=desc.size_range.model_dump(),
        interactions=list(desc.interactions),
        cardinality=desc.cardinality,
        requires_layer_binding=desc.requires_layer_binding,
        export_support=list(support.exporters) if support else [],
        renderer_support=list(support.renderers) if support else [],
        compatible_map_models=list(desc.compatible_map_models),
        accessibility=desc.accessibility.model_dump(),
    )


def validate_design_system() -> List[str]:
    """跨 registry 一致性断言（在单测运行；复用各 registry 自检并追加跨域检查）。"""
    issues: List[str] = []
    from app.lib.cartography.model_library import validate_model_library
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.chart_kinds import validate_chart_kind_registry

    issues.extend(validate_model_library())
    issues.extend(get_component_registry().validate())
    issues.extend(validate_chart_kind_registry())

    # 跨域：模型 chart_needs ↔ chart kind 词表已由 validate_model_library
    # 覆盖；这里补 chart 状态词表与组件 states 的对账。
    comp_reg = get_component_registry()
    desc = comp_reg.get_by_type("chart_panel")
    if desc is not None:
        for s in desc.states:
            if s not in CHART_STATES:
                issues.append(
                    f"chart_panel: state '{s}' 不在图表状态词表")
    # manifest 投影自检：投影必须可构建且计数一致
    manifest = build_design_system_manifest()
    counts = manifest["counts"]
    if counts["mapModelsNative"] + counts["mapModelsPlanned"] != counts["mapModels"]:
        issues.append("manifest: native + planned != 总模型数")
    if counts["chartKinds"] < 1:
        issues.append("manifest: chartKinds 为空")
    return issues


# can_transition re-export：Agent/序列化侧统一从 design_system 取状态机
__all__ = [
    "DESIGN_SYSTEM_SCHEMA_VERSION",
    "MapModelRequirement",
    "ComponentRequirement",
    "build_design_system_manifest",
    "resolve_map_model_requirement",
    "resolve_component_requirement",
    "validate_design_system",
    "can_transition",
]
