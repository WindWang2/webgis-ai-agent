"""GIS Skill Contract —— 版本化领域技能契约（ADR-0182 §2.2）。

一个 Skill = reusable domain procedure：Agent 完成空间分析/遥感分析/制图
目标时可调用、组合、约束、复用的作业方法知识。契约是**结构化、版本化、
可校验**的资产 —— 不是 prompt 文本，不是工具注册表，不是第二个 planner。

引用纪律（单一事实源，不复制）：

- capability id → ``app/lib/gis/capability_registry``（校验谓词注入）；
- recipe id → ``gis_harness.recipes.RecipeRegistry``；
- 本体任务 id → ``gis_harness.gis_ontology``；
- 数据角色 → ``workflow_schema.DATA_ROLES``（直接 import 同一词表）；
- 产物类型 → ``app/lib/gis/artifacts``；
- 科学义务 → 算法层 scientific precondition id（可选引用）。

本模块零 I/O、零 LLM：契约是纯数据 + 纯校验函数。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from app.services.gis_harness.skills._base import SkillAssetModel

from app.services.gis_harness.skills.procedure_ir import (
    SkillProcedure,
)
from app.services.gis_harness.skills.semantics import (
    GeographicSemantics,
    StatisticalSemantics,
    TemporalSemantics,
    validate_semantics,
)

#: 契约版本：结构或语义变化必须提升（进入指纹）。
SKILL_SCHEMA_VERSION = 1

#: Skill 域词表（pack 内的领域分组；与 ontology domain 对齐的超集）。
SKILL_DOMAINS = (
    "vector_analysis",
    "raster_terrain",
    "remote_sensing",
    "cartography",
    "data_preparation",
    "temporal_analysis",
    "network_accessibility",
    "general",
)

#: 技能包词表（S15：core=审定资产；ADR-0191 additive 扩展 induced=轨迹
#: 自合成草案资产——独立目录、独立 fail-closed 装载面，晋升 core 走人工
#: review；pack registry 纯加法演进，无迁移）。
SKILL_PACKS = ("core", "induced")

#: 几何类别词表（对齐 ontology geometry expectations）。
GEOMETRY_KINDS = ("point", "line", "polygon", "raster", "table", "network", "unknown")


class CapabilityRequirement(SkillAssetModel):
    """能力需求：Skill 只存 capability id（引用，不复制能力语义）。"""
    capability_id: str
    purpose: str = ""                      # 在本技能中承担什么（审计面）
    criticality: Literal["required", "optional"] = "required"
    # 能力不可用时本技能是否整体不可行（required+hard_gate=True → resolver
    # 直接 ineligible；False → 降级可行，触发 fallback 语义）
    hard_gate: bool = True


class SituationRequirement(SkillAssetModel):
    """会话/数据事实需求（S29：Situation 分支合并前的事实最小面）。

    全部字段是"需求声明"而非事实：事实由 SelectionFacts 供给，缺席按
    unknown 处理（unknown ≠ 不满足，与 eligibility 红线一致）。
    """
    geometry_kinds: List[str] = Field(default_factory=list)   # ⊆ GEOMETRY_KINDS
    data_roles: List[str] = Field(default_factory=list)       # ⊆ workflow DATA_ROLES
    scope_unit: str = ""                   # ⊆ semantics.GEOGRAPHIC_UNITS（空=不约束）
    min_features: Optional[int] = None
    requires_boundary: bool = False        # 需要行政/自然边界在场
    requires_network: bool = False
    requires_dem: bool = False


class QualityObligation(SkillAssetModel):
    """质量义务（S13）：什么条件下这个作业才算正确。

    ``precondition_id`` 非空时必须命中算法层已注册 scientific precondition
    （联动不重复）；其余 obligation_kind 为技能级声明（由 replay 对照
    evidence）。
    """
    obligation_id: str
    obligation_kind: str = "procedure"     # procedure | precondition | disclosure
    precondition_id: str = ""              # obligation_kind=precondition 时必填
    description: str = ""
    evidence_kind: str = ""                # 满足该义务的证据种类


class SkillFallbackPolicy(SkillAssetModel):
    """技能级降级分类声明（对齐 workflow_schema.DOWNGRADE_CLASSES 语义）。"""
    trigger: str                           # ⊆ procedure_ir.FALLBACK_TRIGGERS
    downgrade_class: str = "degraded"      # equivalent/approximation/proxy/degraded/not_allowed
    disclosure: str = ""
    blocks_completion: bool = False


class SkillExample(SkillAssetModel):
    """有界示例（进入 detail 投影，不进入 SkillCard）。"""
    title: str
    query: str = ""
    notes: str = ""


class SkillContract(SkillAssetModel):
    """GIS 领域技能契约（versioned；技能是审定资产，修改走 code review）。"""
    schema_version: int = SKILL_SCHEMA_VERSION
    id: str
    name: str
    description: str = ""
    domain: str = "general"                # ⊆ SKILL_DOMAINS
    pack: str = "core"                     # ⊆ SKILL_PACKS

    # ── 选择面（resolver 信号；deterministic-first）───────────────────
    intent_patterns: List[str] = Field(default_factory=list)  # zh/en 关键词（紧词表）
    ontology_tasks: List[str] = Field(default_factory=list)   # ⊆ gis_ontology（校验悬空）
    task_types: List[str] = Field(default_factory=list)       # ⊆ intent.TaskType
    when_to_use: str = ""
    when_not_to_use: str = ""

    # ── 事实需求面（S9 前置条件；unknown ≠ 不满足）────────────────────
    required_situation: SituationRequirement = Field(default_factory=SituationRequirement)
    optional_situation: SituationRequirement = Field(default_factory=SituationRequirement)

    # ── 能力与角色（引用不复制）───────────────────────────────────────
    capability_requirements: List[CapabilityRequirement] = Field(default_factory=list)
    input_roles: List[str] = Field(default_factory=list)      # ⊆ workflow DATA_ROLES
    output_roles: List[str] = Field(default_factory=list)     # 产出数据角色
    output_artifacts: List[str] = Field(default_factory=list) # ⊆ ArtifactTypeRegistry
    recipe_refs: List[str] = Field(default_factory=list)      # ⊆ RecipeRegistry（表达选择引用）

    # ── 过程 IR（S2）──────────────────────────────────────────────────
    procedure: SkillProcedure

    # ── 语义词汇（S10-S12）────────────────────────────────────────────
    statistical_semantics: Optional[StatisticalSemantics] = None
    geographic_semantics: Optional[GeographicSemantics] = None
    temporal_semantics: Optional[TemporalSemantics] = None

    # ── 质量与回退（S13/S14）──────────────────────────────────────────
    quality_obligations: List[QualityObligation] = Field(default_factory=list)
    fallback_policy: List[SkillFallbackPolicy] = Field(default_factory=list)
    completion_evidence: List[str] = Field(default_factory=list)  # 完成证据种类

    # ── MapProduct 需求（S22：投影而非 paint）─────────────────────────
    product_requirements: List[str] = Field(default_factory=list)  # 组件/产物期望（引用既有组件词表）

    # ── 资产治理（S23）────────────────────────────────────────────────
    version: str = "1.0.0"
    deprecated: bool = False
    deprecated_by: str = ""                # deprecated=True 时应指向替代技能 id
    examples: List[SkillExample] = Field(default_factory=list, max_length=8)
    guidance: str = ""                     # ≤240 字有界模型提示（prompt 化红线）
    source_doc: str = ""                   # 方法出处（provenance 审计面）

    # ── 契约自检（结构 + 词汇；registry_validation 与 loader 共用）────
    def validate_contract(
        self,
        *,
        capability_exists=None,
        recipe_exists=None,
        ontology_task_exists=None,
        artifact_type_exists=None,
        precondition_exists=None,
        skill_id_exists=None,
    ) -> List[str]:
        violations: List[str] = []
        tag = f"skill[{self.id}]"
        if self.schema_version != SKILL_SCHEMA_VERSION:
            violations.append(f"{tag}: unknown schema_version {self.schema_version}")
        if self.domain not in SKILL_DOMAINS:
            violations.append(f"{tag}: unknown domain {self.domain}")
        if self.pack not in SKILL_PACKS:
            violations.append(f"{tag}: unknown pack {self.pack}")
        if not self.when_to_use:
            violations.append(f"{tag}: 缺 when_to_use（SkillCard 必需）")
        if len(self.guidance) > 240:
            violations.append(f"{tag}: guidance 超 240 字（prompt 化红线）")
        if self.deprecated and not self.deprecated_by:
            violations.append(f"{tag}: deprecated=True 需要 deprecated_by")
        if self.deprecated and skill_id_exists and self.deprecated_by != self.id \
                and not skill_id_exists(self.deprecated_by):
            violations.append(f"{tag}: deprecated_by {self.deprecated_by} 不存在")

        known_cap_ids = {c.capability_id for c in self.capability_requirements}
        cap_ids_seen: set = set()
        for req in self.capability_requirements:
            if req.capability_id in cap_ids_seen:
                violations.append(
                    f"{tag}: duplicate capability requirement {req.capability_id}")
            cap_ids_seen.add(req.capability_id)
            if capability_exists and not capability_exists(req.capability_id):
                violations.append(
                    f"{tag}: unknown capability {req.capability_id}")

        from app.services.gis_harness.workflow_schema import DATA_ROLES as _DATA_ROLES
        for role in list(self.input_roles) + list(self.output_roles):
            if role not in _DATA_ROLES:
                violations.append(f"{tag}: unknown data role {role}")

        for task in self.ontology_tasks:
            if ontology_task_exists and not ontology_task_exists(task):
                violations.append(f"{tag}: unknown ontology task {task}")
        for rid in self.recipe_refs:
            if recipe_exists and not recipe_exists(rid):
                violations.append(f"{tag}: unknown recipe ref {rid}")
        for at in self.output_artifacts:
            if artifact_type_exists and not artifact_type_exists(at):
                violations.append(f"{tag}: unknown artifact type {at}")

        # 几何/角色词汇
        for sit in (self.required_situation, self.optional_situation):
            for g in sit.geometry_kinds:
                if g not in GEOMETRY_KINDS:
                    violations.append(f"{tag}: unknown geometry kind {g}")
        from app.services.gis_harness.skills.semantics import GEOGRAPHIC_UNITS
        for sit, label in ((self.required_situation, "required"),
                           (self.optional_situation, "optional")):
            if sit.scope_unit and sit.scope_unit not in GEOGRAPHIC_UNITS:
                violations.append(f"{tag}.{label}_situation: unknown scope {sit.scope_unit}")
            # 拼写错误的 data role 会静默改变资格语义（facts 带角色时恒
            # DATA_ROLE_MISSING），必须对账词表。
            for role in sit.data_roles:
                if role not in _DATA_ROLES:
                    violations.append(
                        f"{tag}.{label}_situation: unknown data role {role}")

        # task_types ⊆ intent.TaskType（词表漂移防线）
        from typing import get_args as _get_args
        from app.services.gis_harness.intent import TaskType as _TaskType
        _task_vocab = set(_get_args(_TaskType))
        for tt in self.task_types:
            if tt not in _task_vocab:
                violations.append(f"{tag}: unknown task_type {tt}")

        # 过程 IR 结构校验（能力引用域=契约声明的 capability id 全集）
        violations.extend(self.procedure.validate_structure(known_cap_ids))

        # 语义词汇
        violations.extend(validate_semantics(
            self.statistical_semantics, self.geographic_semantics,
            self.temporal_semantics))

        # 质量义务
        obl_ids = [o.obligation_id for o in self.quality_obligations]
        if len(obl_ids) != len(set(obl_ids)):
            violations.append(f"{tag}: duplicate quality_obligation ids")
        for obl in self.quality_obligations:
            if obl.obligation_kind not in ("procedure", "precondition", "disclosure"):
                violations.append(
                    f"{tag}.obligation[{obl.obligation_id}]: unknown kind "
                    f"{obl.obligation_kind}")
            if obl.obligation_kind == "precondition":
                if not obl.precondition_id:
                    violations.append(
                        f"{tag}.obligation[{obl.obligation_id}]: precondition "
                        "kind 需要 precondition_id")
                elif precondition_exists and not precondition_exists(obl.precondition_id):
                    violations.append(
                        f"{tag}.obligation[{obl.obligation_id}]: precondition "
                        f"{obl.precondition_id} 未注册")

        # 降级分类
        from app.services.gis_harness.workflow_schema import DOWNGRADE_CLASSES
        from app.services.gis_harness.skills.procedure_ir import FALLBACK_TRIGGERS
        for pol in self.fallback_policy:
            if pol.trigger not in FALLBACK_TRIGGERS:
                violations.append(f"{tag}.fallback_policy: unknown trigger {pol.trigger}")
            if pol.downgrade_class not in DOWNGRADE_CLASSES:
                violations.append(
                    f"{tag}.fallback_policy: unknown downgrade_class "
                    f"{pol.downgrade_class}")
            if pol.downgrade_class == "not_allowed" and not pol.blocks_completion:
                violations.append(f"{tag}.fallback_policy: not_allowed 必须 blocks_completion")

        # 完成证据与 IR 证据节点一致（completion_evidence ⊆ IR 证据种类 ∪ 义务证据）
        known_evidence = set(self.procedure.all_evidence_kinds()) | {
            o.evidence_kind for o in self.quality_obligations if o.evidence_kind}
        for ev in self.completion_evidence:
            if ev not in known_evidence:
                violations.append(
                    f"{tag}: completion_evidence {ev} 无对应 IR 证据节点/义务")

        # 过程 IR 的 capability_refs ⊆ 契约能力声明（上面 validate_structure
        # 已校验；这里再拦 fallback 的悬空替代技能引用由 library 级校验完成）
        return violations

    def capability_ids(self, *, required_only: bool = False) -> List[str]:
        """契约引用的全部 capability id（排序去重）。"""
        reqs = self.capability_requirements
        if required_only:
            reqs = [r for r in reqs if r.criticality == "required"]
        return sorted({r.capability_id for r in reqs})

    def to_card_dict(self) -> Dict[str, Any]:
        """SkillCard 投影（S17/S19 渐进披露第一层；有界）。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description[:160],
            "domain": self.domain,
            "pack": self.pack,
            "version": self.version,
            "deprecated": self.deprecated,
            "when_to_use": self.when_to_use[:160],
            "required_situation": {
                "geometry_kinds": list(self.required_situation.geometry_kinds),
                "data_roles": list(self.required_situation.data_roles),
                "requires_boundary": self.required_situation.requires_boundary,
                "requires_network": self.required_situation.requires_network,
                "requires_dem": self.required_situation.requires_dem,
            },
            "capability_requirements": [
                {"capability_id": r.capability_id, "criticality": r.criticality}
                for r in self.capability_requirements
            ],
            "step_count": len(self.procedure.steps),
            "ontology_tasks": list(self.ontology_tasks)[:6],
        }

    def to_detail_dict(self) -> Dict[str, Any]:
        """完整投影（按需读取；S19 渐进披露第二层）。"""
        payload = self.model_dump()
        return payload

    def fingerprint(self) -> str:
        """契约内容指纹：canonical JSON SHA256（稳定、排序、无时间戳）。"""
        import hashlib
        import json
        payload = self.model_dump()
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()


__all__ = [
    "SKILL_SCHEMA_VERSION",
    "SKILL_DOMAINS",
    "SKILL_PACKS",
    "GEOMETRY_KINDS",
    "CapabilityRequirement",
    "SituationRequirement",
    "QualityObligation",
    "SkillFallbackPolicy",
    "SkillExample",
    "SkillContract",
]
