"""Requirement ↔ MapSpec Representation Binding（ADR-0219，混合主动制图一致性）。

语义桥（semantic binding，非双写）：

- **Requirement → MapSpec**（requirement_to_mapspec）：显式且已接受的
  表达/用户选择构造**现有** MutationIntent，经 ``apply_gis_mutation``
  正式事务落地（CAS/mutation_id 幂等/user-wins 守卫全复用）；
  palette / geometry_kind 走既有 grammar face（``grammar_request_face``
  → GrammarRequest pin），不强写 paint。
- **MapSpec → Requirement**（semantic_projection）：用户直接 UI mutation
  的**服务端已提交 receipt** 经 taxonomy 分类后，把 requirement-significant
  语义写回 RequirementDocument（``apply_user_patches``，actor=user）。

MapSpec 仍是图面 desired truth；RequirementDocument 只保留"用户要什么"。
本包不新建状态存储：binding 是 proposal + transaction + receipt 的动词，
幂等由引擎 mutation_id dedup 与确定性 binding fingerprint 承载。
"""
from app.services.gis_harness.representation_binding.flags import (
    requirement_binding_enabled,
    requirement_context_block_enabled,
    requirement_projection_enabled,
)
from app.services.gis_harness.representation_binding.contracts import (
    BINDING_SCHEMA,
    BINDING_TARGET_GRAMMAR_FACE,
    BindingStatus,
    RepresentationBinding,
)
from app.services.gis_harness.representation_binding.taxonomy import (
    MutationTaxonomy,
    SemanticDimension,
    TaxonomyClass,
    classify_mutation,
)

__all__ = [
    "BINDING_SCHEMA",
    "BINDING_TARGET_GRAMMAR_FACE",
    "BindingStatus",
    "MutationTaxonomy",
    "RepresentationBinding",
    "SemanticDimension",
    "TaxonomyClass",
    "classify_mutation",
    "requirement_binding_enabled",
    "requirement_context_block_enabled",
    "requirement_projection_enabled",
]
