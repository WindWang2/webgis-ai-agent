"""CompositionValidator —— 组合级组件校验（声明必须被执行）.

descriptor 声明的 conflicts / dependencies / cardinality / allowed_positions
与 composition 模板的 required / forbidden / max_count 此前只是数据，
没有任何执行点（audit §1.9-1.11）。本模块把「组件组合规则」变成确定性
校验：

- 槽位语义：required 缺席 / forbidden 在场 / max_count 超限 → error；
- 组件互斥：descriptor.conflicts 双双在场 → error（保留 priority 小者）；
- 组件依赖：descriptor.dependencies 缺席 → error；
- 位置合法性：position 不在 descriptor.allowed_positions → error；
- planned 组件进入最终地图 → error（不伪装 native）；
- 孤儿绑定：layerId 指向不存在图层 → error（复用 layout_constraints）；
- zone 碰撞 → warning（视觉层问题，semantic_checks QA 已单独报告，
  不阻塞 planner 组合路径——title+subtitle 同驻 top-center 是合法栈叠）。

纯函数、无 I/O、有界（组件数 × 槽位数）。planner 在组合后调用，
error 级违规触发 build_default_components 兜底并记录 evidence。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.lib.cartography.layout_constraints import (
    detect_collisions,
    detect_orphan_components,
)
from app.services.gis_harness.components import CartographyComponent


class CompositionViolation(BaseModel):
    code: str
    severity: str = "error"  # error | warning
    slot: str = ""
    component_id: str = ""
    component_type: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class CompositionValidationResult(BaseModel):
    ok: bool = True
    composition_template_id: str = ""
    violations: List[CompositionViolation] = Field(default_factory=list)
    warnings: List[CompositionViolation] = Field(default_factory=list)

    @property
    def errors(self) -> List[CompositionViolation]:
        return [v for v in self.violations if v.severity == "error"]


def _descriptor_for(component_type: str):
    from app.lib.cartography.component_registry import get_component_registry
    reg = get_component_registry()
    return reg.get(component_type) or reg.get_by_type(component_type)


def validate_component_composition(
    components: List[CartographyComponent],
    *,
    composition_template_id: str = "",
    map_model_id: str = "",
    layer_ids: Optional[List[str]] = None,
    output_target: str = "interactive",
) -> CompositionValidationResult:
    """对最终组件实例列表执行组合规则校验（确定性、只读）。"""
    from app.lib.cartography.composition_templates import get_composition_template_registry

    result = CompositionValidationResult(composition_template_id=composition_template_id)
    layer_ids = layer_ids or []
    enabled = [c for c in components if c.enabled]

    # ── 1. 组合模板槽位语义 ───────────────────────────────────────────
    compo = None
    if composition_template_id:
        compo = get_composition_template_registry().get(composition_template_id)
        if compo is None:
            result.violations.append(CompositionViolation(
                code="unknown_composition_template",
                detail=f"composition template '{composition_template_id}' not registered",
            ))
    if compo is not None:
        for slot in compo.component_slots:
            instances = [
                c for c in enabled
                if slot.allowed_component_types and c.type in slot.allowed_component_types
            ]
            if slot.cardinality == "forbidden" and instances:
                result.violations.append(CompositionViolation(
                    code="forbidden_component_present", slot=slot.id,
                    component_id=instances[0].id, component_type=instances[0].type,
                    detail=f"composition forbids {slot.id} "
                           f"({', '.join(c.id for c in instances)})",
                ))
                continue
            if slot.cardinality == "required" and not instances:
                result.violations.append(CompositionViolation(
                    code="required_slot_missing", slot=slot.id,
                    detail=f"composition requires {slot.id} "
                           f"(allowed: {slot.allowed_component_types})",
                ))
            elif slot.cardinality == "recommended" and not instances:
                # 推荐缺席是软信号（resolver 可因 context 缺失合法剔除）
                result.violations.append(CompositionViolation(
                    code="recommended_slot_missing", severity="warning", slot=slot.id,
                    detail=f"composition recommends {slot.id} but none present",
                ))
            elif len(instances) > max(slot.max_count, 1):
                result.violations.append(CompositionViolation(
                    code="cardinality_exceeded", slot=slot.id,
                    component_id=instances[0].id, component_type=instances[0].type,
                    detail=f"{len(instances)} instances exceed max_count={slot.max_count}",
                ))

    # ── 2. 组件级规则（descriptor 声明） ─────────────────────────────
    present_types = {c.type for c in enabled}
    reported_conflicts: set = set()
    for comp in enabled:
        desc = _descriptor_for(comp.type)
        if desc is None:
            continue
        if desc.runtime_status == "planned":
            result.violations.append(CompositionViolation(
                code="planned_component_present",
                component_id=comp.id, component_type=comp.type,
                detail=f"{comp.type} is runtime_status=planned — must not appear in a final map",
            ))
        if desc.runtime_status == "unavailable":
            result.violations.append(CompositionViolation(
                code="unavailable_component_present",
                component_id=comp.id, component_type=comp.type,
            ))
        for other in sorted(desc.conflicts):
            if other in present_types and other != comp.type:
                pair = tuple(sorted((comp.type, other)))
                if pair not in reported_conflicts:
                    reported_conflicts.add(pair)
                    result.violations.append(CompositionViolation(
                        code="conflicting_components",
                        component_id=comp.id, component_type=comp.type,
                        detail=f"{pair[0]} conflicts with {pair[1]} "
                               f"(descriptor.conflicts)",
                    ))
        for dep in desc.dependencies:
            if dep not in present_types:
                result.violations.append(CompositionViolation(
                    code="dependency_missing",
                    component_id=comp.id, component_type=comp.type,
                    detail=f"{comp.type} requires dependency '{dep}'",
                ))
        if desc.allowed_positions and comp.position not in desc.allowed_positions:
            result.violations.append(CompositionViolation(
                code="position_not_allowed",
                component_id=comp.id, component_type=comp.type,
                detail=f"position '{comp.position}' not in "
                       f"{desc.allowed_positions}",
            ))
        if output_target and output_target not in desc.supported_outputs:
            result.violations.append(CompositionViolation(
                code="output_not_supported",
                component_id=comp.id, component_type=comp.type,
                detail=f"{comp.type} does not support output '{output_target}'",
            ))
        # 限定型组件（如 legend 族）必须与当前主表达模型兼容 —— 防止
        # 「categorical_legend 挂在 heatmap 图上」这类模型错配实例。
        if map_model_id and desc.compatible_map_models \
                and map_model_id not in desc.compatible_map_models:
            result.violations.append(CompositionViolation(
                code="model_not_compatible",
                component_id=comp.id, component_type=comp.type,
                detail=f"{comp.type} not compatible with map model '{map_model_id}'",
            ))

    # ── 3. 布局碰撞 / 孤儿绑定（layout_constraints 复用） ────────────
    for issue in detect_collisions(enabled):
        result.violations.append(CompositionViolation(
            code="zone_collision", severity="warning", detail=issue,
        ))
    for issue in detect_orphan_components(enabled, layer_ids):
        result.violations.append(CompositionViolation(
            code="orphan_layer_binding", detail=issue,
        ))

    result.warnings = [v for v in result.violations if v.severity == "warning"]
    result.ok = not result.errors
    return result


__all__ = [
    "CompositionViolation",
    "CompositionValidationResult",
    "validate_component_composition",
]
