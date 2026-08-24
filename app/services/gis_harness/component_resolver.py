"""ComponentResolver — 根据 Composition / MapModel / Artifact 选择组件.

确定性、 bounded、 registry-indexed；不把 template library 发给 LLM。
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
        if composition_template_id:
            compo = compo_reg.get(composition_template_id)
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

            # check output compatibility for the preferred component type
            chosen_type = slot.allowed_component_types[0] if slot.allowed_component_types else slot.id
            desc = comp_reg.get(chosen_type) or comp_reg.get_by_type(chosen_type)
            if desc and output_target not in desc.supported_outputs:
                sel.rejected.append({"slot": slot.id, "reason": f"output {output_target} not supported"})
                sel.fallback.append(slot.id)
                continue

            # resolve conditional legend slot: pick the allowed type that matches map_model's recommended_components or descriptor compatibility
            resolved_type = chosen_type
            if slot.cardinality == "conditional" and len(slot.allowed_component_types) > 1:
                # prefer legend type whose descriptor lists this map_model
                for cand in slot.allowed_component_types:
                    d = comp_reg.get(cand) or comp_reg.get_by_type(cand)
                    if d and d.compatible_map_models and map_model_id in d.compatible_map_models:
                        resolved_type = cand
                        desc = d
                        break
                    # also check model_library recommended_components
                    try:
                        from app.lib.cartography.model_library import get_map_model_registry
                        m = get_map_model_registry().resolve(map_model_id)
                        if m and cand in (m.recommended_components or []):
                            resolved_type = cand
                            desc = comp_reg.get(cand) or comp_reg.get_by_type(cand)
                            break
                    except Exception:
                        pass
                chosen_type = resolved_type

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

        return sel


_resolver: Optional[ComponentResolver] = None


def get_component_resolver() -> ComponentResolver:
    global _resolver
    if _resolver is None:
        _resolver = ComponentResolver()
    return _resolver


__all__ = ["ComponentResolver", "ComponentSelection", "get_component_resolver"]
