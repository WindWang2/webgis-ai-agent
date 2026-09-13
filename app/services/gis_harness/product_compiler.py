"""Product Compiler —— MapProductSpec → ProductCompileResult（ADR-0183 §5，M4/M5）。

纯函数、确定性（同 (spec, plan, 模板/registry 状态) 同输出同 digest）、
零 IO、零 LLM。产出是**组合 IR + 执行需求 refs**（方向 5 ExecutionGraph
未合并 —— 不产 scheduler、不写 MapSpec）：

    MapProductSpec ─┐
    MapProductPlan ─┼→ compile_product_spec() → ProductCompileResult
    类型库/registry ─┘        (视图→组件族指派 / 图表需求 / 槽位满足 /
                               决策与降级披露 / per-view evidence)

单一真相纪律（M5）：

- 组件类型词表与存在性 = `component_registry`（唯一登记处）；
- 槽位必需性裁决 = `MapCompositionTemplate.component_slots`（既有语义，
  required-component auto-fill 仍是 layout self-heal 的职责 —— 本编译器只
  披露 required 槽位未被视图构成支撑的缺口，不重复其修复逻辑）；
- 图表 kind 词表 = `chart_kinds`；recipe 级别名（admin_bar 等）不在此强映射，
  以 fallback 披露（词表外 → 缺省 kind + 原别名随需求透传给图表通道）。

explicit choice 优先级：spec.overrides > intent 显式信号（已固化在 spec
required 标志）> recipe/template 缺省（shape）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.product_spec import spec_digest
from app.services.provenance.fingerprint import canonical_dumps

#: 视图 kind → 支撑组件族（MapSpec 组件类型；存在性由 component_registry 把关）
VIEW_COMPONENT_FAMILIES: Dict[str, tuple] = {
    "chart": ("chart_panel",),
    "stats_panel": ("statistics_panel",),
    "narrative": ("methodology_note",),
    "inset": ("inset_map",),
    "comparison": ("chart_panel",),
    "time_panel": ("chart_panel",),
}

#: shape 的来源注记/图框级 chrome 组件（模板 default_components 同词表）
_CHROME_FAMILY_TYPES = (
    "title", "subtitle", "legend", "categorical_legend", "continuous_colorbar",
    "north_arrow", "scale_bar", "attribution", "map_border", "export_layout",
    "graticule", "annotation", "table_panel", "methodology_note",
    "uncertainty_panel", "decision_panel", "statistics_panel", "chart_panel",
    "inset_map", "label_layer", "basemap",
)

_MAX_DECISIONS = 12
_MAX_FALLBACKS = 8
_MAX_VIEWS_OUT = 12

#: recipe 级图表别名 → chart_kinds 缺省（词表外别名的诚实降级面；原别名
#: 始终随需求透传 —— 物理映射是图表通道的职责）
_CHART_KIND_FALLBACK = "bar"


class ViewComponentPlan(BaseModel):
    """单视图的编译产物（组件族指派 + 需求 refs + evidence）。"""

    view_id: str
    kind: str
    enabled: bool = True
    required: bool = False
    component_types: List[str] = Field(default_factory=list, max_length=4)
    chart_kind: str = ""          # 解析后的 chart_kinds 词表值（chart 视图）
    chart_kind_alias: str = ""    # 词表外原别名（透传给图表通道；有界披露）
    dataset_ref: str = ""
    analysis_ref: str = ""
    layer_hint: str = ""
    decisions: List[str] = Field(default_factory=list, max_length=4)
    user_override: str = ""
    quality_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ChartRequirement(BaseModel):
    """图表执行需求（方向 5 并行接口：只产需求，不产调度）。"""

    view_id: str
    chart_kind: str
    kind_alias: str = ""
    dataset_ref: str = ""
    analysis_ref: str = ""
    bound_view_id: str = ""       # chart_linked_to_map 的地图端视图
    note: str = Field(default="", max_length=160)


class SlotSatisfaction(BaseModel):
    """组合槽位满足摘要（披露面；修复仍归 layout/composition 引擎）。"""

    slot_id: str
    component_types: List[str] = Field(default_factory=list, max_length=4)
    required: bool = False
    satisfied: bool = True
    note: str = Field(default="", max_length=160)


class ProductCompileResult(BaseModel):
    """编译产物（bounded / serializable / digest-stable）。"""

    spec_id: str = ""
    spec_revision: int = 0
    spec_digest: str = ""
    composition_template_id: str = ""
    views: List[ViewComponentPlan] = Field(default_factory=list, max_length=_MAX_VIEWS_OUT)
    chart_requirements: List[ChartRequirement] = Field(default_factory=list, max_length=8)
    slots: List[SlotSatisfaction] = Field(default_factory=list, max_length=24)
    decisions: List[str] = Field(default_factory=list, max_length=_MAX_DECISIONS)
    fallbacks: List[Dict[str, Any]] = Field(default_factory=list, max_length=_MAX_FALLBACKS)
    compile_digest: str = ""
    ok: bool = True

    def view(self, view_id: str) -> Optional[ViewComponentPlan]:
        for v in self.views:
            if v.view_id == view_id:
                return v
        return None


def compile_digest(result: ProductCompileResult) -> str:
    """确定性编译摘要（排除 compile_digest 自身）。"""
    payload = result.model_dump()
    payload.pop("compile_digest", None)
    return hashlib.sha256(
        canonical_dumps(payload).encode("utf-8")).hexdigest()[:32]


def _registry_types() -> Optional[set]:
    """component_registry 的类型集合（registry 不可用 → None，诚实降级）。"""
    try:
        from app.lib.cartography.component_registry import get_component_registry

        reg = get_component_registry()
        types: set = set()
        for desc in reg.native_descriptors():
            if getattr(desc, "component_type", ""):
                types.add(desc.component_type)
        return types or None
    except Exception:  # noqa: BLE001 — registry 是增值把关，降级不阻断
        return None


def _resolve_chart_kind(spec_chart_kind: str, alias: str) -> tuple:
    """(resolved_kind, fallback_or_none)：词表单源 chart_kinds。"""
    from app.lib.cartography.chart_kinds import CHART_KINDS

    known = {k.id for k in CHART_KINDS}
    if spec_chart_kind and spec_chart_kind in known:
        return spec_chart_kind, None
    if alias and alias in known:
        return alias, None
    return _CHART_KIND_FALLBACK, {
        "code": "chart_kind_unmapped",
        "from": alias or spec_chart_kind,
        "to": _CHART_KIND_FALLBACK,
        "reason": "chart kind not in chart_kinds registry — honest default",
    }


def compile_product_spec(
    spec: "MapProductSpec",
    *,
    plan: Any = None,
    template: Any = None,
    composition: Any = None,
) -> ProductCompileResult:
    """编译入口（纯函数）。plan/template/composition 缺省时按 None 降级。"""
    decisions: List[str] = []
    fallbacks: List[Dict[str, Any]] = []
    registry_types = _registry_types()
    if registry_types is None:
        fallbacks.append({
            "code": "component_registry_unavailable",
            "reason": "component registry unreachable — families passed through unvalidated",
        })

    # ── override 索引（explicit choice 最高优先）────────────────────────
    toggle_off: set = set()
    for ov in spec.overrides:
        if ov.op == "toggle_component" and ov.payload.get("enabled") is False:
            toggle_off.add(str(ov.payload.get("component_type") or ov.target))

    # plan 侧血缘（图层/能力 → 视图 dataset/analysis refs 的转录面）
    plan_layers = list(getattr(plan, "map_layers", None) or [])
    primary_layer = next(
        (ly for ly in plan_layers if ly.role == "primary" and ly.enabled), None)

    # 图表 kind 别名（plan.charts 的 recipe 级标识，词表外时透传）
    chart_alias = ""
    for c in (getattr(plan, "charts", None) or []):
        c = str(c)
        if c:
            chart_alias = c
            break

    views_out: List[ViewComponentPlan] = []
    chart_requirements: List[ChartRequirement] = []
    map_view_id = next(
        (v.view_id for v in spec.views if v.kind == "map"), "")

    for v in spec.views:
        if len(views_out) >= _MAX_VIEWS_OUT:
            break
        families: List[str] = []
        for fam in VIEW_COMPONENT_FAMILIES.get(v.kind, ()):
            if fam in toggle_off:
                decisions.append(
                    f"{v.view_id}: {fam} suppressed by user override")
                continue
            if registry_types is not None and fam not in registry_types:
                fallbacks.append({
                    "code": "component_type_unregistered",
                    "component_type": fam,
                    "view_id": v.view_id,
                    "reason": "family absent from component registry",
                })
                continue
            families.append(fam)

        chart_kind_resolved = ""
        if v.kind in ("chart", "comparison", "time_panel") and families:
            resolved, fb = _resolve_chart_kind(
                v.chart_kind, chart_alias if not v.chart_kind else "")
            chart_kind_resolved = resolved
            if fb is not None:
                fallbacks.append({**fb, "view_id": v.view_id})
            if v.chart_kind and v.chart_kind not in (
                    resolved, chart_alias):
                chart_alias = v.chart_kind

        # evidence 转录（M7：只转录既有事实）
        dataset_ref = v.binding.dataset_ref or (
            primary_layer.bound_ref if primary_layer is not None else "")
        decisions_view: List[str] = []
        if template is not None and getattr(template, "id", ""):
            decisions_view.append(f"template:{template.id}")
        if spec.recipe_id:
            decisions_view.append(f"recipe:{spec.recipe_id}")
        if spec.composition_template_id:
            decisions_view.append(f"composition:{spec.composition_template_id}")

        views_out.append(ViewComponentPlan(
            view_id=v.view_id, kind=v.kind, enabled=v.enabled,
            required=v.required, component_types=families,
            chart_kind=chart_kind_resolved,
            chart_kind_alias=v.chart_kind or "",
            dataset_ref=dataset_ref or v.evidence.dataset_ref,
            analysis_ref=v.binding.analysis_ref or v.evidence.analysis_ref,
            layer_hint=v.binding.layer_hint or v.evidence.dataset_ref,
            decisions=decisions_view[:4],
            user_override=v.evidence.user_override,
            quality_ref=v.evidence.quality_ref,
        ))

        if v.enabled and "chart_panel" in families:
            bound = next(
                (r.dst if r.src == v.view_id else r.src
                 for r in spec.relations
                 if r.kind == "chart_linked_to_map"
                 and v.view_id in (r.src, r.dst)),
                map_view_id,
            )
            chart_requirements.append(ChartRequirement(
                view_id=v.view_id, chart_kind=chart_kind_resolved,
                kind_alias=chart_alias,
                dataset_ref=views_out[-1].dataset_ref,
                analysis_ref=views_out[-1].analysis_ref,
                bound_view_id=bound,
                note=f"chart view {v.view_id} (linked: {bound})",
            ))

    # ── 槽位满足（组合模板是必需性裁决者；本编译器只披露）────────────────
    slots: List[SlotSatisfaction] = []
    composition_id = spec.composition_template_id
    if composition is None and composition_id:
        try:
            from app.lib.cartography.composition_templates import (
                get_composition_template_registry,
            )

            composition = get_composition_template_registry().get(composition_id)
        except Exception:  # noqa: BLE001 — 披露面降级
            composition = None
    if composition is None and composition_id:
        fallbacks.append({
            "code": "composition_template_unresolved",
            "from": composition_id,
            "reason": "composition template not registered — slot plan skipped",
        })
    if composition is not None:
        # chrome 组件来源单一：template.default_components（模板声明的缺省
        # 组件族）—— 编译器不发明第三份组件目录（CA-P1-3 纪律）。
        chrome_types: List[str] = []
        for t in list(getattr(template, "default_components", None) or []):
            t = str(t)
            if t in _CHROME_FAMILY_TYPES and t not in chrome_types:
                chrome_types.append(t)
        view_backed_types = {
            t for v in views_out if v.enabled for t in v.component_types
        }
        for slot in getattr(composition, "component_slots", None) or []:
            allowed = [str(t) for t in (slot.allowed_component_types or [])]
            if not allowed:
                continue
            required = bool(getattr(slot, "required", False)) or (
                str(getattr(slot, "cardinality", "")) == "required")
            fillers = [
                t for t in allowed
                if t in view_backed_types or t in chrome_types
            ]
            satisfied = (not required) or bool(fillers)
            slots.append(SlotSatisfaction(
                slot_id=str(slot.id), component_types=fillers[:4],
                required=required, satisfied=satisfied,
                note="" if satisfied else "required slot unsupported by view composition",
            ))
            if required and not satisfied:
                fallbacks.append({
                    "code": "required_slot_unsupported",
                    "slot": str(slot.id),
                    "reason": "composition requires a family the view graph "
                              "does not back — layout auto-fill owns the fix",
                })

    result = ProductCompileResult(
        spec_id=spec.spec_id,
        spec_revision=spec.revision,
        spec_digest=spec_digest(spec),
        composition_template_id=composition_id,
        views=views_out,
        chart_requirements=chart_requirements,
        slots=slots,
        decisions=decisions[:_MAX_DECISIONS],
        fallbacks=fallbacks[:_MAX_FALLBACKS],
        ok=True,
    )
    result.compile_digest = compile_digest(result)
    return result


__all__ = [
    "VIEW_COMPONENT_FAMILIES",
    "ProductCompileResult",
    "ViewComponentPlan",
    "ChartRequirement",
    "SlotSatisfaction",
    "compile_product_spec",
    "compile_digest",
]
