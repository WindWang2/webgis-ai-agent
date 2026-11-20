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
#: v2（G30，加性）：新增 ``parameters`` / ``role_bindings`` / ``execution``
#: 三个可选字段 —— 声明式可执行薄配置（typed 参数 + 数据角色绑定 + 过程
#: 步骤到既有生产工具的映射）。旧 YAML 不写这些字段时行为与 v1 逐位一致
#: （可执行面缺席 = guidance_only，不改变知识咨询/规划投影语义）。
SKILL_SCHEMA_VERSION = 2

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

# ── G30 可执行参数面词表（closed vocabularies；schema v2）───────────────

#: 参数类型词表。``field_ref`` 表示参数引用某个数据角色绑定中的真实字段
#: 身份（prepare 期对绑定数据事实核对存在性与期望类型，重命名/删除后
#: 重新核对，显示名/别名不能替代稳定字段名）。
SKILL_PARAMETER_TYPES = ("number", "string", "enum", "boolean", "field_ref")

#: ``field_ref`` 期望的字段值类型（有界采样证据；unknown ≠ 不满足）。
SKILL_FIELD_EXPECTS = ("numeric", "categorical", "any", "")

#: number 参数的量纲词表（canonical code；对齐 measurement.CANONICAL_UNITS
#: 语义但只收可执行校验必需的最小集合，空 = dimensionless/不约束）。
SKILL_PARAM_UNITS = ("", "m", "km", "m2", "km2", "persons", "persons_per_km2",
                     "percent", "count", "year", "hour", "degree")

#: 执行结果类型词表（决定完成证据的分型验收语义：report=可回读报告、
#: map=MapSpec 图层落图+图例、dataset=数据产物 ref）。
SKILL_RESULT_KINDS = ("report", "map", "dataset")

#: argument_map 表达式词表：``role:<name>``（数据角色绑定的已解析数据）、
#: ``param:<name>``（typed 参数值；缺席即省略该工具实参）、
#: ``const:<json>``（字面常量）。闭词表 —— 不接受表达式求值。
SKILL_ARG_EXPR_KINDS = ("role", "param", "const")


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


# ── G30 可执行薄配置（schema v2；全部可选字段，缺席 = guidance_only）───

class SkillParameterSpec(SkillAssetModel):
    """typed 可执行参数声明（prepare 期校验；来源在绑定时记录 provenance）。

    默认值只是「技能声明的缺省」，不是用户决定 —— prepare 输出中参数
    provenance 必须区分 user / skill_default / preset / derived。
    """
    name: str
    label: str = ""
    type: str                                  # ⊆ SKILL_PARAMETER_TYPES
    required: bool = False
    description: str = ""
    role: str = ""                             # type=field_ref 时必填：字段取自哪个角色绑定
    expects: str = "any"                       # ⊆ SKILL_FIELD_EXPECTS（field_ref）
    unit: str = ""                             # ⊆ SKILL_PARAM_UNITS（number）
    default: Optional[Any] = None              # 缺省值（来源=skill_default）
    enum_values: List[str] = Field(default_factory=list, max_length=16)  # type=enum
    min_value: Optional[float] = None          # number 范围（闭区间）
    max_value: Optional[float] = None
    max_length: int = 0                        # string 上界（0=不约束）
    required_if_param: str = ""                # 条件必填：另一参数名（须已声明）
    required_if_in: List[str] = Field(default_factory=list, max_length=8)  # 且其取值 ∈ 此集合


class SkillRoleBindingSpec(SkillAssetModel):
    """数据角色绑定需求：本技能可执行时该角色必须能绑定的数据事实。

    这是 required_situation 之外的**执行面**约束（prepare 逐条核对绑定
    数据的真实事实，unknown ≠ 不满足，但可执行路径要求事实可证）。
    """
    role: str                                  # ⊆ input_roles
    required: bool = True
    geometry_kinds: List[str] = Field(default_factory=list)  # ⊆ GEOMETRY_KINDS（空=不约束）
    min_features: int = 0
    description: str = ""


class SkillExecutionStepSpec(SkillAssetModel):
    """procedure step → 既有生产工具的具体调用映射（声明式）。

    ``argument_map``：工具形参名 → 绑定表达式。值可以是：

    - 字符串表达式（``role:<name>`` / ``param:<name>`` / ``const:<json>``）；
    - 嵌套 JSON 对象/数组：**叶子**位置允许字符串表达式，其余为字面量
      （递归解析；``param:`` 叶子缺席时该叶子整体省略）—— 供需要结构化
      规范实参的工具（如 MapSpec layer 对象）声明映射。

    闭词表、不求值：字符串叶子**仅当**以 ``role:`` / ``param:`` / ``const:``
    前缀开头时视为表达式，其余一律字面量；不接受 YAML 之外的任何表达式
    语法。工具存在性与 capability 归属在 prepare 期对 ToolRegistry /
    AlgorithmRegistry 现场核对 —— 契约装载不依赖工具注册表。
    """
    step_id: str                               # ⊆ procedure.steps
    tool: str                                  # ToolRegistry 工具名
    argument_map: Dict[str, Any] = Field(default_factory=dict)
    produces_evidence: List[str] = Field(default_factory=list)  # 该步产物满足的证据种类
    required: bool = True


class SkillExecutionSpec(SkillAssetModel):
    """技能的可执行映射（G30）：过程步骤 → 注册表既有工具调用序列。

    红线：这里只有**映射**没有调度 —— 运行仍经 ToolDispatchService 同一
    owner/scope/side-effect 闸；SessionPlan 仍是步骤状态唯一真相。
    """
    steps: List[SkillExecutionStepSpec] = Field(default_factory=list, max_length=8)
    result_kind: str = "report"                # ⊆ SKILL_RESULT_KINDS


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

    # ── G30 可执行薄配置（schema v2 加性；缺席 = guidance_only）────────
    parameters: List[SkillParameterSpec] = Field(default_factory=list, max_length=16)
    role_bindings: List[SkillRoleBindingSpec] = Field(default_factory=list, max_length=6)
    execution: Optional[SkillExecutionSpec] = None

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

        # ── G30 可执行薄配置校验（schema v2）──────────────────────────
        violations.extend(self._validate_execution_specs())

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

    def _validate_execution_specs(self) -> List[str]:
        """G30 parameters/role_bindings/execution 结构与词汇校验（纯语法面）。

        工具存在性/capability 归属属 prepare 期现场核对（契约装载不依赖
        ToolRegistry）；此处只保证：词表闭包、引用闭包（step/role/param
        悬空必须在装载期红掉，不能运行期静默跳过）。
        """
        import json as _json
        violations: List[str] = []
        tag = f"skill[{self.id}]"

        # parameters：唯一名、类型词表、enum/field_ref/number 约束形状
        param_names = [p.name for p in self.parameters]
        if len(param_names) != len(set(param_names)):
            violations.append(f"{tag}: duplicate parameter names")
        for p in self.parameters:
            ptag = f"{tag}.param[{p.name}]"
            if p.type not in SKILL_PARAMETER_TYPES:
                violations.append(f"{ptag}: unknown type {p.type}")
            if p.type == "enum" and not p.enum_values:
                violations.append(f"{ptag}: enum 参数需要 enum_values")
            if p.type == "field_ref":
                if not p.role:
                    violations.append(f"{ptag}: field_ref 需要 role")
                elif p.role not in self.input_roles:
                    violations.append(f"{ptag}: field_ref role {p.role} 不在 input_roles")
                if p.expects not in SKILL_FIELD_EXPECTS:
                    violations.append(f"{ptag}: unknown expects {p.expects}")
            if p.type == "number":
                if p.unit not in SKILL_PARAM_UNITS:
                    violations.append(f"{ptag}: unknown unit {p.unit!r}")
                if p.min_value is not None and p.max_value is not None \
                        and p.min_value > p.max_value:
                    violations.append(f"{ptag}: min_value > max_value")
                if p.default is not None and (
                        isinstance(p.default, bool)
                        or not isinstance(p.default, (int, float))):
                    violations.append(f"{ptag}: number 参数 default 必须是数值")
            if p.type == "string" and p.max_length < 0:
                violations.append(f"{ptag}: max_length 不能为负")
            if p.required_if_param:
                if p.required_if_param == p.name:
                    violations.append(f"{ptag}: required_if_param 不能指向自身")
                elif p.required_if_param not in param_names:
                    violations.append(
                        f"{ptag}: required_if_param {p.required_if_param} 未声明")
                elif not p.required_if_in:
                    violations.append(f"{ptag}: required_if_in 不能为空")

        # role_bindings：角色引用闭包 + 几何词表
        known_roles = set(self.input_roles)
        rb_roles = [rb.role for rb in self.role_bindings]
        if len(rb_roles) != len(set(rb_roles)):
            violations.append(f"{tag}: duplicate role_bindings")
        for rb in self.role_bindings:
            if rb.role not in known_roles:
                violations.append(
                    f"{tag}.role_binding[{rb.role}]: 不在 input_roles")
            for g in rb.geometry_kinds:
                if g not in GEOMETRY_KINDS:
                    violations.append(
                        f"{tag}.role_binding[{rb.role}]: unknown geometry {g}")

        # execution：step 引用闭包、argument_map 表达式闭词表（递归叶子）、
        # param/role 引用存在性、result_kind 词表
        if self.execution is not None:
            ex = self.execution
            if ex.result_kind not in SKILL_RESULT_KINDS:
                violations.append(f"{tag}.execution: unknown result_kind {ex.result_kind}")
            if not ex.steps:
                violations.append(f"{tag}.execution: 至少一个 step 映射")
            step_ids = {s.step_id for s in self.procedure.steps}
            seen_steps: set = set()

            def _check_expr(stag: str, arg: str, expr: Any, path: str) -> None:
                if isinstance(expr, dict):
                    for k, v in expr.items():
                        _check_expr(stag, arg, v, f"{path}.{k}")
                    return
                if isinstance(expr, list):
                    for i, v in enumerate(expr):
                        _check_expr(stag, arg, v, f"{path}[{i}]")
                    return
                if not isinstance(expr, str):
                    return  # 嵌套字面量（number/bool/null 由 JSON 语义承载）
                if not any(expr.startswith(f"{k}:") for k in SKILL_ARG_EXPR_KINDS):
                    return  # 非前缀字符串 = 字面量（闭词表只在显式前缀上生效）
                kind, _, value = expr.partition(":")
                if not value:
                    violations.append(
                        f"{stag}.arg[{arg}]{path}: 非法表达式 {expr!r}"
                        "（词表 role:/param:/const:）")
                    return
                if kind == "role" and value not in known_roles:
                    violations.append(
                        f"{stag}.arg[{arg}]{path}: role:{value} 不在 input_roles")
                if kind == "param" and value not in param_names:
                    violations.append(
                        f"{stag}.arg[{arg}]{path}: param:{value} 未声明")
                if kind == "const":
                    try:
                        _json.loads(value)
                    except (TypeError, ValueError):
                        violations.append(
                            f"{stag}.arg[{arg}]{path}: const:{value!r} 不是 JSON 字面量")

            for st in ex.steps:
                stag = f"{tag}.execution[{st.step_id}]"
                if st.step_id in seen_steps:
                    violations.append(f"{stag}: 重复 step 映射")
                seen_steps.add(st.step_id)
                if st.step_id not in step_ids:
                    violations.append(f"{stag}: 不在 procedure.steps（悬空映射）")
                if not st.tool:
                    violations.append(f"{stag}: 缺 tool")
                for arg, expr in st.argument_map.items():
                    _check_expr(stag, arg, expr, "")

        return violations

    def is_runnable_asset(self) -> bool:
        """可执行资产判定：有 execution 映射且未被弃用（guidance_only 反面）。

        注意这只是**资产形状**判定 —— 运行资格还受 pack/tier/能力可用性/
        数据事实约束（prepare 期裁决），此处不越权。
        """
        return self.execution is not None and bool(self.execution.steps) \
            and not self.deprecated

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
            # G30：可执行资产形状标记（guidance_only 的反面；运行资格仍在 prepare）
            "runnable": self.is_runnable_asset(),
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
    "SKILL_PARAMETER_TYPES",
    "SKILL_FIELD_EXPECTS",
    "SKILL_PARAM_UNITS",
    "SKILL_RESULT_KINDS",
    "SKILL_ARG_EXPR_KINDS",
    "CapabilityRequirement",
    "SituationRequirement",
    "QualityObligation",
    "SkillFallbackPolicy",
    "SkillExample",
    "SkillParameterSpec",
    "SkillRoleBindingSpec",
    "SkillExecutionStepSpec",
    "SkillExecutionSpec",
    "SkillContract",
]
