"""Requirement digest（F02 DoD #6：同义请求在稳定条件下形成可比较 digest）。

- :func:`requirement_digest`——**语义核**指纹：只含"用户要什么"的规范值
  （任务/范围/主体/指标规范形/统计口径/时间/空间关系/purpose/audience/
  表达约束/显式组件/输出/锁）。排除：raw phrase、provenance、turn、
  revision、patch journal、歧义状态、下游解析产物（field 解析值）。
  → 同义请求同 digest（可 replay/drift 比较），语义变化 digest 必变。
- :func:`document_digest`——envelope 指纹：含 revision/lifecycle/patches，
  用于变更检测与 stale 判定。

指纹实现复用 :func:`app.services.gis_harness.workflow_instance.canonical_fingerprint`
（仓库单一指纹真相；goal_satisfaction/goal_graph 同规）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.gis_harness.requirement_ir.contracts import RequirementDocument
from app.services.gis_harness.workflow_instance import canonical_fingerprint

#: digest 版本前缀——语义核组成变化时递增，避免跨版本误比。
REQUIREMENT_DIGEST_VERSION = "rd1"


def canonical_core(doc: RequirementDocument) -> Dict[str, Any]:
    """语义核的 canonical dict（稳定排序由 canonical_fingerprint 保证）。"""
    intent = doc.intent
    measures: List[Dict[str, Any]] = [
        {
            "subject_token": m.subject_token,
            "statistic": m.statistic,
            "denominator": m.denominator,
            "temporal_required": m.temporal_required,
        }
        for m in intent.measures
    ]
    components = {
        key: getattr(intent.components, key)
        for key in ("title", "legend", "scale_bar", "north_arrow", "labels", "attribution")
        if getattr(intent.components, key) is not None
    }
    core = {
        "v": REQUIREMENT_DIGEST_VERSION,
        "task": {
            "kind": intent.task.kind,
            "stages": list(intent.task.stages),
            "task_type": intent.task.task_type,
        },
        "aoi": {"name": intent.aoi.name, "level": intent.aoi.level},
        "subject": {"type": intent.subject.type, "category": intent.subject.category},
        "datasets": sorted(intent.datasets),
        "measures": measures,
        "time": {
            "range_start": intent.time.range_start,
            "range_end": intent.time.range_end,
            "granularity": intent.time.granularity,
            "series": intent.time.series,
        },
        "statistics": {
            "dimension": intent.statistics.dimension,
            "group_by": intent.statistics.group_by,
            "normalization": intent.statistics.normalization,
            "denominator": intent.statistics.denominator,
        },
        "spatial_relation": {
            "kind": intent.spatial_relation.kind,
            "target": intent.spatial_relation.target,
            "distance_m": intent.spatial_relation.distance_m,
        },
        "purpose": intent.purpose,
        "audience": intent.audience,
        "representation": {
            "palette": intent.representation.palette,
            "geometry_kind": intent.representation.geometry_kind,
            "hidden_layer_ids": sorted(intent.representation.hidden_layer_ids),
            "locked_layer_ids": sorted(intent.representation.locked_layer_ids),
            "pinned_channels": dict(sorted(intent.representation.pinned_channels.items())),
        },
        "components": components,
        "output": {
            "live_map": intent.output.live_map,
            "formats": sorted(intent.output.formats),
            "publish": intent.output.publish,
            "report": intent.output.report,
            "dpi": intent.output.dpi,
            **({"medium": intent.output.medium} if intent.output.medium else {}),
        },
        "locks": sorted([list(lock.canonical()) for lock in intent.locks]),
    }
    # G3D04：scene 块条件包含——无 3D 表达的文档 digest 与历史逐字节一致
    # （存量 pinned 语义零漂移）；有表达时进语义核（变更检测覆盖 3D 面）。
    if not intent.scene.is_default():
        core["scene"] = {
            "dimension": intent.scene.dimension,
            "degrade_reason": intent.scene.degrade_reason,
            "terrain_requested": intent.scene.terrain_requested,
            "extrusion_requested": intent.scene.extrusion_requested,
            "exaggeration": intent.scene.exaggeration,
            "camera_pitch": intent.scene.camera_pitch,
            "camera_bearing": intent.scene.camera_bearing,
            "analysis_goals": list(intent.scene.analysis_goals),
            "dataset_roles": dict(sorted(intent.scene.dataset_roles.items())),
            "vertical_evidence": [
                {"kind": e.kind, "ref": e.ref, "verified": e.verified}
                for e in intent.scene.vertical_evidence
            ],
        }
    return core


def requirement_digest(doc: RequirementDocument) -> str:
    """语义核 digest（同义稳定；带版本前缀，如 ``rd1:ab12…``）。"""
    return f"{REQUIREMENT_DIGEST_VERSION}:{canonical_fingerprint(canonical_core(doc))}"


def document_digest(doc: RequirementDocument) -> str:
    """envelope digest（revision/lifecycle/journal 变化即变；变更检测面）。"""
    payload = {
        "schema": doc.schema_version,
        "document_id": doc.document_id,
        "revision": doc.revision,
        "lifecycle": doc.lifecycle,
        "folded_patch_count": doc.folded_patch_count,
        "patch_count": len(doc.patches),
        "requirement": canonical_core(doc),
    }
    return canonical_fingerprint(payload)
