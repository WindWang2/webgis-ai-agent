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


# 图例族语义家族：同一 layerId 上跨家族并存 = 对同一层的两种竞争语义。
_LEGEND_FAMILY = {"legend", "categorical_legend", "continuous_colorbar"}


def validate_binding_conflicts(
    components: List[CartographyComponent],
) -> List[tuple]:
    """binding 级冲突检测（v2 图例族语义）。

    规则（不同层互不干涉；未绑定 layerId 的实例不参与 —— HUD 发现语义）：
    - 同一 layerId 上 图例家族成员 ≥2 且分属不同语义家族（离散 legend vs
      连续 colorbar）→ 冲突（对同一层同时宣称离散与连续语义）；
    - 同一 layerId 上同型组件重复 → 冲突（重复图例，无信息增益）。

    返回 [(component_id, component_type, detail), ...]，由组合校验包装为
    ``binding_conflict`` violation（error 级 —— planner 会走兜底重排）。
    """
    by_layer: Dict[str, List[CartographyComponent]] = {}
    for comp in components:
        layer_id = str((comp.options or {}).get("layerId") or "")
        if not layer_id or comp.type not in _LEGEND_FAMILY:
            continue
        by_layer.setdefault(layer_id, []).append(comp)

    issues: List[tuple] = []
    for layer_id, comps in by_layer.items():
        # 同型重复
        seen_types: Dict[str, str] = {}
        for comp in comps:
            if comp.type in seen_types:
                issues.append((
                    comp.id, comp.type,
                    f"duplicate {comp.type} bound to layer '{layer_id}' "
                    f"(first: {seen_types[comp.type]})",
                ))
            else:
                seen_types[comp.type] = comp.id
        # 跨语义家族：离散（legend/categorical_legend）vs 连续（colorbar）
        discrete = [c for c in comps if c.type in ("legend", "categorical_legend")]
        continuous = [c for c in comps if c.type == "continuous_colorbar"]
        if discrete and continuous:
            issues.append((
                continuous[0].id, continuous[0].type,
                f"continuous_colorbar competes with discrete legend "
                f"on the same layer '{layer_id}'",
            ))
    return issues


def validate_component_composition(
    components: List[CartographyComponent],
    *,
    composition_template_id: str = "",
    map_model_id: str = "",
    layer_ids: Optional[List[str]] = None,
    output_target: str = "interactive",
    layer_model_ids: Optional[Dict[str, str]] = None,
) -> CompositionValidationResult:
    """对最终组件实例列表执行组合规则校验（确定性、只读）。

    ``layer_model_ids``：layerId → 该层 MapModel id（v2：图例族按绑定层
    判模型兼容性；缺省按主模型判定 —— 行为向后兼容）。
    """
    from app.lib.cartography.composition_templates import get_composition_template_registry

    result = CompositionValidationResult(composition_template_id=composition_template_id)
    layer_ids = layer_ids or []
    layer_model_ids = layer_model_ids or {}
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
        # v2：判定按组件自身绑定的图层的模型（layer_model_ids），无绑定
        # 信息时退回主模型 —— 多图层地图上 choropleth 层的图例不应被
        # heatmap 主模型误杀。
        if desc.compatible_map_models:
            bound_model = ""
            if layer_model_ids:
                bound_model = layer_model_ids.get(str((comp.options or {}).get("layerId") or ""), "")
            effective_model = bound_model or map_model_id
            if effective_model and effective_model not in desc.compatible_map_models:
                result.violations.append(CompositionViolation(
                    code="model_not_compatible",
                    component_id=comp.id, component_type=comp.type,
                    detail=f"{comp.type} not compatible with map model '{effective_model}'",
                ))

    # ── 2.5 binding 级冲突（v2：图例族互斥从 type 级升级为 binding 级）──
    for issue in validate_binding_conflicts(enabled):
        result.violations.append(CompositionViolation(
            code="binding_conflict",
            component_id=issue[0], component_type=issue[1],
            detail=issue[2],
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
    "validate_binding_conflicts",
]
