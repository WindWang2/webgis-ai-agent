"""ComponentResolver — 根据 Composition / MapModel / Artifact 选择组件.

确定性、 bounded、 registry-indexed；不把 template library 发给 LLM。

本轮（component library phase-2）修正：
- 多类型槽位（图例族 legend/categorical_legend/continuous_colorbar）不再
  盲取 allowed_component_types[0]：required/conditional 一律按 descriptor
  兼容性 + 模型库 recommended_components 解析（proportional_symbol 之类
  模型此前会拿到错误的图例类型）；
- descriptor.conflicts 从「纯声明」变为 selection 层执行：同批互斥类型
  并存时保留 priority 小（语义优先）者，另一方以 conflict_with 记录剔除。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ComponentSelection(BaseModel):
    selected: List[str] = Field(default_factory=list)
    rejected: List[Dict[str, Any]] = Field(default_factory=list)
    required: List[str] = Field(default_factory=list)
    optional: List[str] = Field(default_factory=list)
    fallback: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    composition_template_id: str = ""
    component_templates: Dict[str, str] = Field(default_factory=dict)


class ComponentResolver:
    """确定性组件解析：CompositionTemplate + MapModel + output → ComponentSelection."""

    def resolve(
        self,
        *,
        composition_template_id: str = "",
        map_model_id: str = "",
        output_target: str = "interactive",
        available_context: Optional[List[str]] = None,
        artifact_types: Optional[List[str]] = None,
        preferred_variants: Optional[Dict[str, str]] = None,
    ) -> ComponentSelection:
        from app.lib.cartography.composition_templates import get_composition_template_registry
        from app.lib.cartography.component_registry import get_component_registry
        from app.lib.cartography.component_templates import get_component_template_registry

        comp_reg = get_component_registry()
        tmpl_reg = get_component_template_registry()
        compo_reg = get_composition_template_registry()

        available_context = available_context or []
        artifact_types = artifact_types or []
        preferred_variants = preferred_variants or {}

        # pick composition template — prefer model-specific density/academic maps for heatmap-like models
        compo = None
        wired_discarded = False
        if composition_template_id:
            compo = compo_reg.get(composition_template_id)
            # 显式指定的 composition 必须仍与最终 map model 兼容 ——
            # 资格降级（如热力→点图回退）后 density_map 的必需 colorbar
            # 会污染点图语境；不兼容则记因并走自动选择（select+fallback）。
            if compo is not None and map_model_id and compo.compatible_map_models \
                    and map_model_id not in compo.compatible_map_models:
                compo = None
                wired_discarded = True
        if compo is None:
            candidates = compo_reg.find_for_map_model(map_model_id, output_target)
            # prefer a template that explicitly declares compatible_map_models for this model
            specific = [c for c in candidates if c.compatible_map_models and map_model_id in c.compatible_map_models]
            if specific:
                # prioritize density_map for heatmap-type models so legend resolves to colorbar
                density_pref = [c for c in specific if "density" in c.id]
                compo = (density_pref[0] if density_pref else specific[0])
            elif candidates:
                compo = candidates[0]
            else:
                all_cands = [c for c in compo_reg.all_templates() if not output_target or output_target in c.output_targets]
                compo = all_cands[0] if all_cands else None

        if compo is None:
            return ComponentSelection(reason_codes=["no_composition_template"])

        sel = ComponentSelection(composition_template_id=compo.id)
        if wired_discarded:
            # 丢弃的接线模板记因（evidence 可追溯，不静默降级）
            sel.rejected.append({
                "slot": "composition", "reason": "wired_composition_incompatible_model",
                "detail": f"{composition_template_id} not compatible with map model {map_model_id!r}",
            })
            sel.reason_codes.append("wired_composition_incompatible_model")

        for slot in compo.component_slots:
            if slot.cardinality == "forbidden":
                sel.rejected.append({"slot": slot.id, "reason": "forbidden_by_composition"})
                continue

            # check dependencies / context
            # legend conditional — need a layer binding; for now include if map_model suggests it
            should_include = False
            if slot.cardinality == "required":
                should_include = True
                sel.required.append(slot.id)
            elif slot.cardinality == "recommended":
                should_include = True
                sel.optional.append(slot.id)
            elif slot.cardinality == "conditional":
                # conditional legend/colorbar — include if map_model compatible
                # we check if any allowed type's descriptor lists this map_model
                for ctype in slot.allowed_component_types:
                    desc = comp_reg.get(ctype)
                    if desc is None:
                        desc = comp_reg.get_by_type(ctype)
                    if desc and (not desc.compatible_map_models or map_model_id in desc.compatible_map_models):
                        should_include = True
                        break
                    if not desc:
                        # unknown type — include anyway for generic slots
                        should_include = True
                        break
                if not should_include:
                    sel.rejected.append({"slot": slot.id, "reason": "conditional_not_met"})
                    continue
            elif slot.cardinality == "optional":
                # include optional if context/output supports it
                # For minimal density, always include unless explicitly excluded
                should_include = True
                sel.optional.append(slot.id)

            if not should_include:
                continue

            # 统一的槽位类型解析：多类型槽位（图例族）按 map_model 兼容性选型
            chosen_type = self._resolve_slot_type(slot, comp_reg, map_model_id)
            desc = comp_reg.get(chosen_type) or comp_reg.get_by_type(chosen_type)

            # check output compatibility for the chosen component type
            if desc and output_target not in desc.supported_outputs:
                sel.rejected.append({"slot": slot.id, "reason": f"output {output_target} not supported"})
                sel.fallback.append(slot.id)
                continue

            # check required_context (e.g., statistics_panel needs statistics)
            if desc and desc.required_context:
                missing = [c for c in desc.required_context if c not in available_context]
                if missing:
                    sel.rejected.append({"slot": slot.id, "reason": f"missing_context {missing}"})
                    continue

            # runtime_status check — skip planned/unavailable
            if desc and desc.runtime_status == "planned":
                sel.rejected.append({"slot": slot.id, "reason": "runtime_planned"})
                continue
            if desc and desc.runtime_status == "unavailable":
                sel.rejected.append({"slot": slot.id, "reason": "runtime_unavailable"})
                continue

            sel.selected.append(chosen_type)

            # pick component template for this slot
            preferred = preferred_variants.get(chosen_type, "")
            if preferred and tmpl_reg.has(preferred):
                sel.component_templates[chosen_type] = preferred
            elif slot.preferred_templates:
                for pt in slot.preferred_templates:
                    if tmpl_reg.has(pt):
                        sel.component_templates[chosen_type] = pt
                        break
                if chosen_type not in sel.component_templates:
                    cands = tmpl_reg.find_by_type(chosen_type)
                    if cands:
                        sel.component_templates[chosen_type] = cands[0].id
            else:
                cands = tmpl_reg.find_by_type(chosen_type)
                if cands:
                    sel.component_templates[chosen_type] = cands[0].id

        self._enforce_conflicts(sel, comp_reg)
        return sel

    def _resolve_slot_type(self, slot, comp_reg, map_model_id: str) -> str:
        """多类型槽位选型：descriptor 兼容 > 模型库推荐 > 第一个 allowed.

        单类型槽位直接返回（行为不变）。图例族槽位由此保证
        choropleth→legend / heatmap→continuous_colorbar /
        categorical_thematic→categorical_legend 的确定性配对。
        """
        if not slot.allowed_component_types:
            return slot.id
        if len(slot.allowed_component_types) == 1:
            return slot.allowed_component_types[0]
        for cand in slot.allowed_component_types:
            d = comp_reg.get(cand) or comp_reg.get_by_type(cand)
            if d and d.compatible_map_models and map_model_id in d.compatible_map_models:
                return cand
        try:
            from app.lib.cartography.model_library import get_map_model_registry
            model = get_map_model_registry().resolve(map_model_id)
            if model and model.recommended_components:
                for cand in slot.allowed_component_types:
                    if cand in (model.recommended_components or []):
                        return cand
        except Exception:  # noqa: BLE001 - 模型库不可用时回退第一候选
            pass
        return slot.allowed_component_types[0]

    def _enforce_conflicts(self, sel: ComponentSelection, comp_reg) -> None:
        """descriptor.conflicts 执行：互斥类型并存时保留 priority 小者."""
        def _priority(ctype: str) -> int:
            d = comp_reg.get(ctype) or comp_reg.get_by_type(ctype)
            return d.priority if d else 100

        def _conflict_type(ctype: str) -> list:
            d = comp_reg.get(ctype) or comp_reg.get_by_type(ctype)
            return list(d.conflicts) if d else []

        ordered = sorted(sel.selected, key=lambda t: (_priority(t), t))
        kept: List[str] = []
        for ctype in ordered:
            keeper_conflict = None
            for k in kept:
                if ctype in _conflict_type(k) or k in _conflict_type(ctype):
                    keeper_conflict = k
                    break
            if keeper_conflict is not None:
                sel.rejected.append({
                    "component_type": ctype, "reason": f"conflict_with:{keeper_conflict}",
                })
                continue
            kept.append(ctype)
        dropped = [t for t in sel.selected if t not in kept]
        sel.selected = kept
        for t in dropped:
            sel.component_templates.pop(t, None)
        if dropped:
            sel.reason_codes.append("conflict_resolution_applied")


_resolver: Optional[ComponentResolver] = None


def get_component_resolver() -> ComponentResolver:
    global _resolver
    if _resolver is None:
        _resolver = ComponentResolver()
    return _resolver


__all__ = ["ComponentResolver", "ComponentSelection", "get_component_resolver"]
