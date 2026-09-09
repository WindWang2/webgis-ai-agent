"""Template Intelligence V2 —— TemplateSpec / Component Intelligence /
Composition Planner（Epic 11 §5.F/§5.G/§5.H）。

模板从「静态样式」升级为可校验的组合规格：

    TemplateSpecV2 = base(MapCompositionTemplate 引用，不复制槽位)
                   + data_bindings（槽位 → artifact/角色 绑定）
                   + capability_requirements（渲染前置能力）
                   + export_constraints（导出目标/版式约束）
                   + uncertainty / comparison requirements

Composition Planner（确定性规则驱动，**禁止 query 硬编码**）：

    category（taxonomy）+ artifacts（viz bridge）+ qualification（不确定性）
      → 基底组合模板（输出目标过滤 + 类目亲和评分）
      → 槽位填充（taxonomy 组件期望 ∪ viz bridge 槽位绑定 ∪
         uncertainty/methodology 披露组件）
      → 数据绑定（主产物 → 主层；统计产物 → 图表面板）
      → CompositionPlan（含披露 + 置信度）

「成都学校分布」在此框架下自然得到：点分布主层 + 行政聚合伴生 +
图例/比例尺/指北针 + 统计面板 —— 由 taxonomy 组件期望与 viz bridge
槽位绑定**推导**，而非「学校 → 热力图」式硬编码（case corpus 负例锁定）。

与 Cartography V6 边界：本模块只裁决「该用什么组件、怎么组合」；
renderer 负责「如何正确绘制」。校验全部对账 canonical registries
（composition/model/component/artifact/capability id，悬空 fatal）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

#: template intelligence schema 版本（进指纹）。
TEMPLATE_INTEL_SCHEMA_VERSION = 2


class DataBinding(BaseModel):
    """槽位 → 数据绑定（artifact 语义类型或数据角色）。"""

    component_type: str            # 组件 id/type（校验）
    bind_artifact: str = ""        # ⊆ ArtifactTypeRegistry（校验；二选一）
    bind_role: str = ""            # ⊆ DATA_ROLES（校验；二选一）
    bind_scope: str = "primary"    # primary / all_thematic

    @model_validator(mode="after")
    def _one_of(self) -> "DataBinding":
        if not self.bind_artifact and not self.bind_role:
            raise ValueError(
                f"data binding {self.component_type}: 需 bind_artifact 或 bind_role")
        if self.bind_artifact and self.bind_role:
            raise ValueError(
                f"data binding {self.component_type}: artifact/role 二选一")
        return self


class ExportConstraint(BaseModel):
    """导出约束（模板的输出面契约）。"""

    targets: Tuple[str, ...] = ("interactive",)     # interactive/png/pdf/svg
    page_profiles: Tuple[str, ...] = ()             # A4_landscape/…
    min_dpi: int = 0
    live_export_parity: bool = True                 # live/export 组件一致义务

    @field_validator("targets")
    @classmethod
    def _bounded_targets(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(str(x)[:16] for x in tuple(v)[:6])


class TemplateSpecV2(BaseModel):
    """模板组合规格 V2（引用既有 composition template，不复制槽位）。"""

    spec_id: str
    label_zh: str = ""
    base_composition_template_id: str   # ⊆ CompositionTemplateRegistry（校验）
    #: 类目/族亲和（planner 评分依据；⊆ 词表，校验）
    affinity_categories: Tuple[str, ...] = ()
    affinity_families: Tuple[str, ...] = ()
    output_targets: Tuple[str, ...] = ("interactive",)
    data_bindings: Tuple[DataBinding, ...] = ()
    capability_requirements: Tuple[str, ...] = ()   # ⊆ CapabilityRegistry（校验）
    export_constraints: ExportConstraint = Field(default_factory=ExportConstraint)
    requires_uncertainty: bool = False   # 基底是否必须配不确定性面板
    requires_comparison: bool = False
    provenance_id: str = "prov.taxonomy.alignment"

    @field_validator("affinity_categories", "affinity_families")
    @classmethod
    def _bounded_affinity(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(str(x)[:40] for x in tuple(v)[:6])

    @field_validator("output_targets")
    @classmethod
    def _bounded_outputs(cls, v: Tuple[str, ...]) -> Tuple[str, ...]:
        return tuple(str(x)[:16] for x in tuple(v)[:6])

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "spec_id": self.spec_id[:48],
            "base": self.base_composition_template_id[:48],
            "categories": list(self.affinity_categories[:4]),
            "families": list(self.affinity_families[:4]),
            "outputs": list(self.output_targets[:4]),
            "bindings": [b.component_type[:24] for b in self.data_bindings[:6]],
            "uncertainty": self.requires_uncertainty,
            "comparison": self.requires_comparison,
        }


class SlotFill(BaseModel):
    """一个槽位的填充决定（组件 + 来源证据）。"""

    component_type: str
    source: str                    # base | taxonomy | viz_bridge | disclosure
    binding: Optional[DataBinding] = None
    cardinality: str = "required"  # required | conditional | optional

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component_type[:32],
            "source": self.source[:12],
            "cardinality": self.cardinality[:12],
            "binding": (self.binding.model_dump(mode="json")
                        if self.binding else None),
        }


class CompositionPlan(BaseModel):
    """组合规划产物（可序列化、有界、可解释）。"""

    spec_id: str = ""
    base_composition_template_id: str = ""
    output_target: str = "interactive"
    slot_fills: List[SlotFill] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    evidence: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "spec_id": self.spec_id[:48],
            "base": self.base_composition_template_id[:48],
            "output_target": self.output_target[:16],
            "slots": [s.to_bounded_dict() for s in self.slot_fills[:14]],
            "disclosures": [d[:160] for d in self.disclosures[:6]],
            "confidence": round(self.confidence, 2),
            "evidence": {
                str(k)[:24]: v for k, v in
                list(self.evidence.items())[:5]
            },
        }


#: 审定规格表（category → 基底模板亲和；纯加法演进；悬空 fatal）。
_CURATED_SPECS: Tuple[TemplateSpecV2, ...] = (
    TemplateSpecV2(
        spec_id="spec.distribution_interactive",
        label_zh="分布交互图",
        base_composition_template_id="composition.minimal_interactive",
        affinity_categories=("spatial_distribution", "density",
                             "administrative_aggregation"),
        output_targets=("interactive", "png"),
        data_bindings=(
            DataBinding(component_type="legend", bind_role="subject",
                        bind_scope="all_thematic"),
            DataBinding(component_type="chart_panel",
                        bind_artifact="admin_aggregate_table"),
        ),
        capability_requirements=("admin_aggregation",),
        export_constraints=ExportConstraint(targets=("interactive", "png")),
    ),
    TemplateSpecV2(
        spec_id="spec.statistical_map",
        label_zh="统计专题图",
        base_composition_template_id="composition.statistical_map",
        affinity_categories=("thematic_cartography", "density",
                             "administrative_aggregation", "comparison"),
        output_targets=("interactive", "png", "pdf"),
        data_bindings=(
            DataBinding(component_type="legend", bind_role="measure",
                        bind_scope="primary"),
            DataBinding(component_type="chart_panel",
                        bind_artifact="admin_aggregate_table"),
            DataBinding(component_type="statistics_panel",
                        bind_artifact="stats_table"),
        ),
        capability_requirements=("rate_aggregation",),
        export_constraints=ExportConstraint(
            targets=("interactive", "png", "pdf")),
    ),
    TemplateSpecV2(
        spec_id="spec.statistical_report",
        label_zh="统计报告版面",
        base_composition_template_id="composition.statistical_report",
        affinity_categories=("thematic_cartography", "atlas_reporting",
                             "multi_criteria"),
        output_targets=("interactive", "pdf"),
        data_bindings=(
            DataBinding(component_type="chart_panel",
                        bind_artifact="stats_table"),
            DataBinding(component_type="table_panel",
                        bind_artifact="admin_aggregate_table"),
        ),
        capability_requirements=("admin_aggregation",),
        export_constraints=ExportConstraint(
            targets=("pdf",), page_profiles=("A4_landscape",)),
    ),
    TemplateSpecV2(
        spec_id="spec.interpolation_surface",
        label_zh="插值面版面",
        base_composition_template_id="composition.academic_map",
        affinity_categories=("interpolation", "uncertainty"),
        output_targets=("png", "pdf", "interactive"),
        data_bindings=(
            DataBinding(component_type="continuous_colorbar",
                        bind_artifact="raster_surface"),
        ),
        capability_requirements=("spatial_interpolation",),
        export_constraints=ExportConstraint(targets=("png", "pdf")),
        requires_uncertainty=True,
    ),
    TemplateSpecV2(
        spec_id="spec.hotspot_map",
        label_zh="热点显著性图",
        base_composition_template_id="composition.academic_map",
        affinity_categories=("hotspot", "clustering",
                             "spatiotemporal_pattern"),
        output_targets=("interactive", "png", "pdf"),
        data_bindings=(
            DataBinding(component_type="legend",
                        bind_artifact="hotspot_result"),
        ),
        capability_requirements=("getis_ord_gi_star",),
        export_constraints=ExportConstraint(targets=("png", "pdf")),
    ),
    TemplateSpecV2(
        spec_id="spec.terrain_analysis",
        label_zh="地形分析版面",
        base_composition_template_id="composition.terrain_analysis",
        affinity_categories=("terrain", "hydrology"),
        output_targets=("png", "pdf", "svg"),
        data_bindings=(
            DataBinding(component_type="continuous_colorbar",
                        bind_artifact="terrain_surface"),
        ),
        capability_requirements=("terrain_slope",),
        export_constraints=ExportConstraint(
            targets=("png", "pdf"), page_profiles=("A4_landscape",)),
    ),
    TemplateSpecV2(
        spec_id="spec.change_report",
        label_zh="变化对比报告",
        base_composition_template_id="composition.temporal_change_report",
        affinity_categories=("change_detection", "comparison"),
        output_targets=("interactive", "pdf"),
        data_bindings=(
            DataBinding(component_type="chart_panel",
                        bind_artifact="stats_table"),
            DataBinding(component_type="legend",
                        bind_artifact="change_set"),
        ),
        capability_requirements=("raster_change_detection",),
        export_constraints=ExportConstraint(targets=("pdf",)),
        requires_comparison=True,
    ),
    TemplateSpecV2(
        spec_id="spec.rs_report",
        label_zh="遥感解译报告",
        base_composition_template_id="composition.rs_index_report",
        affinity_categories=("remote_sensing_extraction",),
        output_targets=("interactive", "pdf"),
        data_bindings=(
            DataBinding(component_type="continuous_colorbar",
                        bind_artifact="remote_sensing_index"),
        ),
        capability_requirements=("spectral_index",),
        export_constraints=ExportConstraint(targets=("pdf",)),
    ),
    TemplateSpecV2(
        spec_id="spec.network_service",
        label_zh="网络服务区报告",
        base_composition_template_id="composition.service_area_report",
        affinity_categories=("accessibility_network", "proximity", "overlay"),
        output_targets=("interactive", "pdf"),
        data_bindings=(
            DataBinding(component_type="legend",
                        bind_artifact="service_area"),
        ),
        capability_requirements=("service_area",),
        export_constraints=ExportConstraint(targets=("pdf",)),
    ),
    TemplateSpecV2(
        spec_id="spec.mcda_report",
        label_zh="多准则决策报告",
        base_composition_template_id="composition.mcda_score_report",
        affinity_categories=("multi_criteria", "suitability"),
        output_targets=("interactive", "pdf"),
        data_bindings=(
            DataBinding(component_type="legend", bind_role="criteria",
                        bind_scope="primary"),
            DataBinding(component_type="chart_panel",
                        bind_artifact="stats_table"),
        ),
        capability_requirements=("mcda_evaluation",),
        export_constraints=ExportConstraint(targets=("pdf",)),
    ),
    TemplateSpecV2(
        spec_id="spec.risk_report",
        label_zh="风险暴露报告",
        base_composition_template_id="composition.risk_exposure_report",
        affinity_categories=("multi_criteria", "overlay"),
        output_targets=("interactive", "pdf"),
        data_bindings=(
            DataBinding(component_type="legend", bind_role="hazard",
                        bind_scope="primary"),
        ),
        capability_requirements=("mcda_evaluation", "geometry_overlay"),
        export_constraints=ExportConstraint(targets=("pdf",)),
    ),
    TemplateSpecV2(
        spec_id="spec.watershed_report",
        label_zh="水文报告",
        base_composition_template_id="composition.watershed_report",
        affinity_categories=("hydrology",),
        output_targets=("interactive", "pdf"),
        data_bindings=(
            DataBinding(component_type="legend",
                        bind_artifact="polygon_feature_set"),
        ),
        capability_requirements=("terrain_hydrology",),
        export_constraints=ExportConstraint(targets=("pdf",)),
    ),
)


class TemplateSpecRegistry:
    """规格登记表：O(1) by spec_id + 亲和索引 + 校验 + 指纹。"""

    def __init__(
        self,
        specs: Tuple[TemplateSpecV2, ...] = _CURATED_SPECS,
    ) -> None:
        self._by_id: Dict[str, TemplateSpecV2] = {}
        self._by_category: Dict[str, List[str]] = {}
        for spec in specs:
            if spec.spec_id in self._by_id:
                raise ValueError(f"duplicate template spec: {spec.spec_id}")
            self._by_id[spec.spec_id] = spec
            for cid in spec.affinity_categories:
                self._by_category.setdefault(cid, []).append(spec.spec_id)

    def get(self, spec_id: str) -> Optional[TemplateSpecV2]:
        return self._by_id.get(spec_id)

    def has(self, spec_id: str) -> bool:
        return spec_id in self._by_id

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    def specs_for_category(self, category_id: str) -> List[TemplateSpecV2]:
        ids = self._by_category.get(category_id, [])
        return [self._by_id[i] for i in ids]

    def validate(
        self,
        *,
        composition_exists: Optional[Callable[[str], bool]] = None,
        category_exists: Optional[Callable[[str], bool]] = None,
        family_exists: Optional[Callable[[str], bool]] = None,
        artifact_type_exists: Optional[Callable[[str], bool]] = None,
        component_exists: Optional[Callable[[str], bool]] = None,
        capability_exists: Optional[Callable[[str], bool]] = None,
        data_role_vocabulary: Tuple[str, ...] = (),
    ) -> List[str]:
        violations: List[str] = []
        for sid, spec in self._by_id.items():
            tag = f"template_spec[{sid}]"
            if composition_exists and not composition_exists(
                    spec.base_composition_template_id):
                violations.append(
                    f"{tag}: base composition {spec.base_composition_template_id} 不存在")
            for cid in spec.affinity_categories:
                if category_exists and not category_exists(cid):
                    violations.append(f"{tag}: category {cid} 不存在")
            for fid in spec.affinity_families:
                if family_exists and not family_exists(fid):
                    violations.append(f"{tag}: family {fid} 不存在")
            for b in spec.data_bindings:
                if component_exists and not component_exists(b.component_type):
                    violations.append(
                        f"{tag}: binding component {b.component_type} 未注册")
                if b.bind_artifact and artifact_type_exists and \
                        not artifact_type_exists(b.bind_artifact):
                    violations.append(
                        f"{tag}: binding artifact {b.bind_artifact} 未注册")
                if b.bind_role and data_role_vocabulary and \
                        b.bind_role not in data_role_vocabulary:
                    violations.append(f"{tag}: unknown bind_role {b.bind_role}")
            for cap in spec.capability_requirements:
                if capability_exists and not capability_exists(cap):
                    violations.append(f"{tag}: capability {cap} 不存在")
            if not spec.affinity_categories and not spec.affinity_families:
                violations.append(f"{tag}: 无亲和（不可被规划器选中）")
        return violations

    def fingerprint(self) -> str:
        payload = {
            "version": TEMPLATE_INTEL_SCHEMA_VERSION,
            "specs": [self._by_id[k].model_dump(mode="json")
                      for k in sorted(self._by_id)],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Composition Planner（规则驱动；无 query 硬编码）──────────────────────

def _default_registries() -> Tuple[Any, Any, Any, Any]:
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    from app.lib.cartography.model_library import get_map_model_registry
    from app.lib.gis.artifacts import get_artifact_type_registry
    return (get_composition_template_registry(), get_map_model_registry(),
            get_component_registry(), get_artifact_type_registry())


def _component_known(comp_reg: Any, name: str) -> bool:
    return comp_reg.has(name) or comp_reg.get_by_type(name) is not None


def plan_composition(
    category_id: str,
    *,
    artifact_types: Sequence[str],
    output_target: str = "interactive",
    uncertainty_required: bool = False,
    comparison_required: bool = False,
    constraints: Optional[Dict[str, Any]] = None,
    taxonomy: Any = None,
    spec_registry: Optional[TemplateSpecRegistry] = None,
    viz_bridge_plan: Optional[Dict[str, Any]] = None,
    facts: Optional[Any] = None,
) -> CompositionPlan:
    """类目 + 产物 + 资格事实 → 组合计划（确定性规则；无 query 输入）。

    规则源（全部审定知识，非 per-query 硬编码）：
    - 基底模板 = 输出目标过滤 + 类目亲和评分 + 不确定性/比较义务匹配；
    - 槽位 = 基底槽位 ∪ taxonomy 组件期望 ∪ viz bridge 槽位绑定；
    - 披露组件：qualification 报告带披露 → methodology_note；不确定性
      支持 → uncertainty_panel；
    - 绑定 = 规格 data_bindings ∩ 实际产物（有产物才绑，防空绑）。
    """
    from app.lib.gis.methodology.viz_bridge import bridge_plan

    if taxonomy is None:
        from app.lib.gis.methodology.taxonomy import get_task_taxonomy
        taxonomy = get_task_taxonomy()
    if spec_registry is None:
        spec_registry = get_template_spec_registry()
    if viz_bridge_plan is None:
        viz_bridge_plan = bridge_plan(list(artifact_types))

    compositions, models, comp_reg, _arts = _default_registries()
    cat = taxonomy.get(category_id)
    disclosures: List[str] = list(viz_bridge_plan.get("disclosures_text", []))

    # ── 1. 基底模板选择（确定性评分）────────────────────────────────
    candidates = spec_registry.specs_for_category(category_id)
    scored: List[Tuple[float, str, TemplateSpecV2]] = []
    for spec in candidates:
        if output_target not in spec.output_targets and \
                output_target in ("interactive", "png", "pdf"):
            # 输出目标不匹配 → 软罚不排除（spec 可含多目标）
            pass
        base_tpl = compositions.get(spec.base_composition_template_id)
        if base_tpl is None:
            continue
        if output_target not in base_tpl.output_targets:
            continue  # 基底不支持该输出 → 排除（硬约束）
        score = 1.0
        if spec.requires_uncertainty != uncertainty_required:
            score -= 0.4
        if spec.requires_comparison != comparison_required:
            score -= 0.3
        scored.append((score, spec.spec_id, spec))
    if not scored:
        # 兜底基底：标准分析版面（有界回退，显式披露）
        fallback = compositions.get("composition.standard_analysis")
        disclosures.append("无类目亲和规格命中：回退标准分析版面。")
        base_id = fallback.id if fallback else ""
        spec_used: Optional[TemplateSpecV2] = None
        confidence = 0.4
    else:
        scored.sort(key=lambda t: (-t[0], t[1]))
        confidence, _, spec_used = scored[0]
        base_id = spec_used.base_composition_template_id
    base_tpl = compositions.get(base_id)
    plan = CompositionPlan(
        spec_id=spec_used.spec_id if spec_used else "",
        base_composition_template_id=base_id,
        output_target=output_target,
        disclosures=disclosures,
        confidence=confidence,
        evidence={"category": category_id,
                  "artifact_count": len(list(artifact_types)[:8])},
    )

    # ── 2. 槽位填充（基底 ∪ taxonomy 期望 ∪ viz bridge 绑定）────────
    filled: Dict[str, SlotFill] = {}
    if base_tpl is not None:
        for slot in base_tpl.component_slots:
            if slot.cardinality == "forbidden" or slot.max_count == 0:
                continue  # 基底显式禁用的槽位不填充
            primary_type = (slot.allowed_component_types or [""])[0]
            if primary_type and _component_known(comp_reg, primary_type):
                filled[slot.id] = SlotFill(
                    component_type=primary_type, source="base",
                    cardinality=("required" if slot.required
                                 else slot.cardinality))
    if cat is not None:
        for comp in cat.required_components:
            key = f"tax:{comp}"
            if _component_known(comp_reg, comp) and \
                    not any(f.component_type == comp
                            for f in filled.values()):
                filled[key] = SlotFill(component_type=comp,
                                       source="taxonomy",
                                       cardinality="required")
        for comp in cat.optional_components:
            if _component_known(comp_reg, comp) and \
                    not any(f.component_type == comp
                            for f in filled.values()):
                filled[f"tax:{comp}"] = SlotFill(
                    component_type=comp, source="taxonomy",
                    cardinality="optional")
    for slot_name in viz_bridge_plan.get("slot_bindings", []):
        if _component_known(comp_reg, slot_name) and \
                not any(f.component_type == slot_name
                        for f in filled.values()):
            filled[f"viz:{slot_name}"] = SlotFill(
                component_type=slot_name, source="viz_bridge",
                cardinality="conditional")

    # ── 3. 义务组件（不确定性/方法论披露）────────────────────────────
    if uncertainty_required and _component_known(comp_reg, "uncertainty_panel") \
            and not any(f.component_type == "uncertainty_panel"
                        for f in filled.values()):
        filled["obligation:uncertainty"] = SlotFill(
            component_type="uncertainty_panel", source="disclosure",
            cardinality="required")
        plan.disclosures.append("方法产出不确定性：不确定性面板为必选槽位。")
    if (facts is not None and getattr(facts, "disclosures", None)
            and _component_known(comp_reg, "methodology_note")
            and not any(f.component_type == "methodology_note"
                        for f in filled.values())):
        filled["obligation:methodology"] = SlotFill(
            component_type="methodology_note", source="disclosure",
            cardinality="required")

    # ── 4. 数据绑定（规格绑定 ∩ 实际产物；防空绑）───────────────────
    artifact_set = set(list(artifact_types)[:12])
    if spec_used is not None:
        for binding in spec_used.data_bindings:
            if binding.bind_artifact and binding.bind_artifact not in artifact_set:
                continue  # 产物不在场 → 不绑（不虚构）
            match = next((f for f in filled.values()
                          if f.component_type == binding.component_type), None)
            if match is not None and match.binding is None:
                match.binding = binding

    plan.slot_fills = list(filled.values())[:14]
    return plan


def plan_composition_for_method(
    method_id: str,
    category_id: str,
    *,
    facts: Any = None,
    output_target: str = "interactive",
    constraints: Optional[Dict[str, Any]] = None,
) -> CompositionPlan:
    """方法 → 产物（V4 output_artifacts）→ 组合计划（方法级入口）。"""
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )
    reg = get_methodology_registry()
    candidate = reg.method(method_id)
    if candidate is None:
        raise ValueError(f"unknown method_id: {method_id}")
    uncertainty = False
    if facts is not None:
        from app.lib.gis.methodology.descriptors import (
            get_method_descriptor_registry,
        )
        desc = get_method_descriptor_registry().get(method_id)
        uncertainty = bool(desc and desc.uncertainty_support in
                           ("field", "ensemble", "analytic"))
    return plan_composition(
        category_id,
        artifact_types=list(candidate.output_artifacts),
        output_target=output_target,
        uncertainty_required=uncertainty,
        comparison_required=candidate.family_id in ("change_detection",
                                                    "compositional_mapping"),
        constraints=constraints,
        facts=facts,
    )


_singleton: Optional[TemplateSpecRegistry] = None


def get_template_spec_registry() -> TemplateSpecRegistry:
    global _singleton
    if _singleton is None:
        _singleton = TemplateSpecRegistry()
    return _singleton


def reset_template_spec_registry() -> None:
    global _singleton
    _singleton = None


__all__ = [
    "TEMPLATE_INTEL_SCHEMA_VERSION",
    "DataBinding",
    "ExportConstraint",
    "TemplateSpecV2",
    "TemplateSpecRegistry",
    "SlotFill",
    "CompositionPlan",
    "plan_composition",
    "plan_composition_for_method",
    "get_template_spec_registry",
    "reset_template_spec_registry",
]
