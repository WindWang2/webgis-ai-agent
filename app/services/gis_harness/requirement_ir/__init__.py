"""GIS Intent & Requirement IR（F02 / ADR-0215）。

公开面（单向消费纪律见 docs/dev/f02-gis-intent-requirement-ir-decisions.md）：

- contracts：版本化 typed 契约（GISIntentSpec / MapRequirementSpec /
  RequirementDocument / Provenance / Ambiguity / PatchRecord）；
- classify：五类顶层意图分类（query_only/analysis/map/edit/export）；
- build：core→sections 确定性派生 + 多轮 edit→patch 差分 + 超替携带；
- patch：白名单 patch 协议（CAS/幂等/user-wins/回放/diff/归因）；
- clarify：typed 澄清策略（blocking/安全默认/context-key 去重/legacy 兼容）；
- digest：requirement_digest（同义稳定）/ document_digest（变更检测）；
- projection：六路下游单向投影（IR 只写权威模块的输入面）；
- corpus：真实场景验收 corpus；
- service：会话级生命周期（唯一 IO 面；store 可注入）。

本包不造第二理解器/第二 planner：语义真相仍是
``app.services.gis_harness.intent.MapRequestIntent``。
"""
from app.services.gis_harness.requirement_ir.contracts import (
    REQUIREMENT_DOCUMENT_SCHEMA,
    Ambiguity,
    AmbiguityOption,
    AOISpec,
    GISIntentSpec,
    MapRequirementSpec,
    MeasureSpec,
    OutputSpec,
    PatchOp,
    PatchRecord,
    Provenance,
    RequirementDocument,
    RequirementItem,
    RepresentationSpec,
    RequiredComponentsSpec,
    SpatialRelationSpec,
    StatisticsSpec,
    SubjectSpec,
    TaskKind,
    TaskSpec,
    TimeSpec,
    UserLock,
)
from app.services.gis_harness.requirement_ir.classify import (
    TaskClassification,
    classify_task_kind,
    classification_from_core,
)
from app.services.gis_harness.requirement_ir.build import (
    build_document,
    carry_user_state,
    derive_requirements,
    derive_sections,
    diff_to_patches,
)
from app.services.gis_harness.requirement_ir.clarify import (
    context_key,
    plan_clarifications,
    pending_questions,
    record_ambiguities,
    to_legacy_request,
)
from app.services.gis_harness.requirement_ir.digest import (
    canonical_core,
    document_digest,
    requirement_digest,
)
from app.services.gis_harness.requirement_ir.lifecycle import (
    LifecycleError,
    can_accept,
    supersede,
)
from app.services.gis_harness.requirement_ir.patch import (
    PatchBlocked,
    PatchConflict,
    PatchError,
    PatchStale,
    PatchUnknownPath,
    PatchValueInvalid,
    apply_patch,
    attribute_changes,
    diff_documents,
    replay,
)
from app.services.gis_harness.requirement_ir.projection import (
    export_obligations,
    field_query_inputs,
    grammar_request_face,
    goal_requirements,
    intent_view,
    template_obligations,
)

__all__ = [
    "REQUIREMENT_DOCUMENT_SCHEMA",
    "Ambiguity", "AmbiguityOption", "AOISpec", "GISIntentSpec",
    "MapRequirementSpec", "MeasureSpec", "OutputSpec", "PatchOp", "PatchRecord",
    "Provenance", "RequirementDocument", "RequirementItem",
    "RepresentationSpec", "RequiredComponentsSpec", "SpatialRelationSpec",
    "StatisticsSpec", "SubjectSpec", "TaskKind", "TaskSpec", "TimeSpec",
    "UserLock",
    "TaskClassification", "classify_task_kind", "classification_from_core",
    "build_document", "carry_user_state", "derive_requirements",
    "derive_sections", "diff_to_patches",
    "context_key", "plan_clarifications", "pending_questions",
    "record_ambiguities", "to_legacy_request",
    "canonical_core", "document_digest", "requirement_digest",
    "LifecycleError", "can_accept", "supersede",
    "PatchBlocked", "PatchConflict", "PatchError", "PatchStale",
    "PatchUnknownPath", "PatchValueInvalid",
    "apply_patch", "attribute_changes", "diff_documents", "replay",
    "export_obligations", "field_query_inputs", "grammar_request_face",
    "goal_requirements", "intent_view", "template_obligations",
]
