"""下游单向投影（F02：IR → 既有权威模块的**输入面**；D-09）。

六路出口，全部只构造下游权威模块的输入，不代替其决策：

1. :func:`intent_view` —— 既有 ``MapRequestIntent`` 消费方（recipes /
   planner / template_selector 零改动继续工作）；
2. :func:`field_query_inputs` —— field_resolver 的短语+量纲提示输入
   （解析权威不动；``needs_clarification`` 经 clarify 回流）；
3. :func:`grammar_request_face` —— ``GrammarRequest`` 构造键面
   （purpose/audience 走 standards 词表；pinned_* 只透传 user-origin）；
4. :func:`template_obligations` —— template/composition 上下文义务；
5. :func:`export_obligations` —— export/completeness 义务；
6. :func:`goal_requirements` —— 合法 ``GoalRequirement`` 列表
   （goal_satisfaction 显式合约缝的升级载体）。

IR 不写 MapSpec、不选工具、不算 grammar/completeness 结果。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.requirement_ir.contracts import RequirementDocument


def intent_view(doc: RequirementDocument) -> MapRequestIntent:
    """出口 1：语义核原样外泄（patch 已同步；单一理解真相）。"""
    return doc.intent.core


def field_query_inputs(doc: RequirementDocument) -> List[Dict[str, Any]]:
    """出口 2：field_resolver 输入面。

    返回形状与 ``field_resolver.FieldQuery`` 的期望语义对齐
    （phrase → parse_measure_phrase 的输入；denominator/role/kind/
    temporal_required 为提示面）。解析结果（field/candidates/
    needs_clarification）归 field_resolver，IR 只记录（measure.field）。
    """
    inputs: List[Dict[str, Any]] = []
    for measure in doc.intent.measures:
        inputs.append({
            "measure_id": measure.id,
            "phrase": measure.phrase or measure.subject_token,
            "subject": measure.subject_token,
            "denominator": measure.denominator or doc.intent.statistics.denominator,
            "temporal_required": measure.temporal_required or doc.intent.time.series,
            "role": measure.role,
            "kind": measure.kind,
        })
    return inputs


def grammar_request_face(
    doc: RequirementDocument,
    *,
    feature_count: int = 0,
    zoom: float = 11.0,
    fields: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """出口 3：``GrammarRequest`` 构造键面（键名一一对应，可直接构造）。

    user-wins：``pinned_palette / pinned_representation / pinned_channels``
    只在存在 user-origin 锁定时透传；rule 推断值绝不冒充用户 pin。
    """
    spec = doc.intent
    fp = spec.field_provenance
    geometry_map = {
        "point": "point", "line": "line", "polygon": "polygon", "raster": "raster",
    }
    pinned_palette = spec.representation.palette \
        if spec.representation.palette and fp.get("representation.palette", spec.representation.provenance).is_user() \
        else None
    pinned_representation = spec.representation.geometry_kind \
        if spec.representation.geometry_kind and fp.get("representation.geometry_kind", spec.representation.provenance).is_user() \
        else None
    pinned_channels = dict(spec.representation.pinned_channels) \
        if spec.representation.pinned_channels and fp.get("representation.pinned_channels", spec.representation.provenance).is_user() \
        else {}
    face: Dict[str, Any] = {
        "geometry": geometry_map.get(spec.core.geometry_expectation, "polygon"),
        "feature_count": int(feature_count),
        "zoom": float(zoom),
        "purpose": spec.purpose,
        "audience": spec.audience,
        "output_purpose": spec.output.output_purpose,
        "pinned_palette": pinned_palette,
        "pinned_representation": pinned_representation,
        "pinned_channels": pinned_channels,
    }
    if fields:
        face["fields"] = fields
    return face


def template_obligations(doc: RequirementDocument) -> Dict[str, Any]:
    """出口 4：template/composition 上下文义务（选择权在 selector）。"""
    spec = doc.intent
    explicit_components = [
        name for name in ("title", "legend", "scale_bar", "north_arrow", "labels", "attribution")
        if getattr(spec.components, name) is not None
    ]
    return {
        "task": spec.task.task_type,
        "task_kind": spec.task.kind,
        "subject": {
            "type": spec.subject.type,
            "category": spec.subject.category,
        },
        "scope": {"name": spec.aoi.name, "level": spec.aoi.level},
        "purpose": spec.purpose,
        "audience": spec.audience,
        "stages": list(spec.task.stages),
        "required_components": {
            name: getattr(spec.components, name) for name in explicit_components
        },
        "group_by": spec.statistics.group_by,
        "formats": list(spec.output.formats),
    }


def export_obligations(doc: RequirementDocument) -> List[Dict[str, Any]]:
    """出口 5：export/completeness 义务（对齐 export lineage/completeness 面）。"""
    spec = doc.intent
    obligations: List[Dict[str, Any]] = []
    for fmt in spec.output.formats:
        obligations.append({
            "format": fmt,
            "publish": spec.output.publish,
            "dpi": spec.output.dpi,
            "output_purpose": spec.output.output_purpose,
            "report": spec.output.report,
            "pinned": any(
                lock.scope == "export_format" and lock.value == fmt
                for lock in spec.locks),
        })
    if spec.output.live_map and not obligations:
        obligations.append({
            "format": "live_map", "publish": False, "dpi": None,
            "output_purpose": spec.output.output_purpose, "report": False,
            "pinned": False,
        })
    return obligations


def goal_requirements(doc: RequirementDocument) -> List[Any]:
    """出口 6：合法 ``GoalRequirement`` 列表（构造期校验 = 兼容性测试面）。"""
    from app.services.gis_harness.goal_satisfaction.contracts import (
        GoalRequirement,
        RequirementKind as GoalRequirementKind,
    )

    items: List[Any] = []
    for item in doc.requirements.items:
        items.append(GoalRequirement(
            id=f"req-{item.id}",
            kind=GoalRequirementKind(item.kind),
            summary=item.statement,
            required=True,
            pinned=item.pinned,
            capability=item.capability,
            export_format=item.export_format,
            scope_name=item.scope_name,
            group_by=item.group_by,
            source=f"requirement_ir:{item.source_path}"[:64],
        ))
    return items
