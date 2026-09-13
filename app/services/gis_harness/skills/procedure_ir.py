"""GIS Skill 过程 IR —— 声明式作业步骤 / 决策点 / 证据 / 回退（ADR-0182 §2.2）。

Skill procedure 描述「这类作业**应该做什么**」：有序步骤、显式决策点、
步骤级证据要求与回退节点。它**不是**执行图：

- 不调度、不重试、不管并发与 checkpoint —— 运行期执行由 SessionPlan /
  PlanGraph / geocompute 负责（ExecutionGraph 对应物）；
- 步骤不绑定工具名 —— 步骤引用 capability requirement id，具体工具由
  Tool Resolver / CapabilityRegistry 解析；
- 描述 procedure，不是 CoT —— 节点是结构化契约，`guidance` 有界，
  禁止把 chain-of-thought 写进技能（防 Skill Prompt 化，S26）。

全部确定性：结构校验为纯函数（零 LLM、零 I/O）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from app.services.gis_harness.skills._base import SkillAssetModel

#: 步骤在作业过程中的功能类别（稳定词表，replay 按类聚合）。
STEP_KINDS = (
    "inspect",        # 数据检视 / 质检（重复、CRS、空几何、缺失值）
    "prepare",        # 数据准备 / 预处理（投影、裁剪、修复）
    "analyze",        # 空间/统计分析
    "decide",         # 携带决策点的方法选择步骤
    "design",         # 制图表达设计（引用 recipe / cartography 语义）
    "validate",       # 质量义务核查（统计/地理/时间语义、完整性）
    "deliver",        # 产出组装（主图/统计图/图例/来源披露）
)

#: 步骤可跳过策略（replay 据此区分 skipped_declared 与 missing）。
SKIP_POLICIES = (
    "never",                  # 必做：缺失即 missing（违规）
    "if_unavailable",         # 能力/数据不可用时可跳过，但必须披露
    "if_irrelevant",          # 与目标无关时可跳过（须记录原因）
)

#: 回退动作词表（S14：禁止 silent fallback —— 每个动作都携带披露义务）。
FALLBACK_ACTIONS = (
    "alternative_skill",   # 换替代技能（fallback_skill_id 必填）
    "reduced_output",      # 降级产出（disclosure 必填）
    "methodology_note",    # 附方法论说明继续
    "blocked",             # 科学上不可执行，声明缺什么（不虚构）
    "clarify",             # 向用户澄清
)

#: 回退触发条件词表（S14 的五类失败面）。
FALLBACK_TRIGGERS = (
    "missing_input",
    "unsupported_geometry",
    "insufficient_data",
    "provider_unavailable",
    "budget_exceeded",
    "quality_failure",
)

#: 过程 IR 规模上限（防止把整本教材塞进一个技能）。
MAX_STEPS = 32
MAX_DECISIONS = 16
MAX_FALLBACKS = 12


class StepEvidenceRequirement(SkillAssetModel):
    """步骤级证据要求：完成该步骤必须留下的证据种类。

    ``evidence_kind`` 是稳定字符串（进入 replay 报告）；``description``
    说明什么证据满足。replay 阶段按 evidence_kind 对照 plan/evidence 投影。
    """
    evidence_kind: str
    description: str = ""


class ProcedureStep(SkillAssetModel):
    """一个作业步骤（声明式；顺序由 procedure.steps 列表序决定）。"""
    step_id: str                       # 技能内唯一（"s1"/"inspect_data"…）
    title: str
    kind: str                          # ⊆ STEP_KINDS
    description: str = ""
    # 本步骤依赖的 capability requirement id（⊆ contract.capability_requirements）
    capability_refs: List[str] = Field(default_factory=list)
    # 本步骤依赖的前置步骤 id（显式依赖；空 = 顺序前一步）
    depends_on: List[str] = Field(default_factory=list)
    evidence_requirements: List[StepEvidenceRequirement] = Field(default_factory=list)
    required: bool = True
    skip_policy: str = "never"         # ⊆ SKIP_POLICIES
    # 有界模型提示（≤240 字；只允许"怎么做这类步骤"的方法提示，禁止 CoT）
    guidance: str = ""

    def validate_node(self, known_capabilities: set) -> List[str]:
        violations: List[str] = []
        if self.kind not in STEP_KINDS:
            violations.append(f"step[{self.step_id}]: unknown kind {self.kind}")
        if self.skip_policy not in SKIP_POLICIES:
            violations.append(f"step[{self.step_id}]: unknown skip_policy {self.skip_policy}")
        if self.skip_policy == "never" and not self.required:
            violations.append(
                f"step[{self.step_id}]: required=False 与 skip_policy=never 矛盾")
        if len(self.guidance) > 240:
            violations.append(f"step[{self.step_id}]: guidance 超 240 字（prompt 化红线）")
        for ref in self.capability_refs:
            if ref not in known_capabilities:
                violations.append(
                    f"step[{self.step_id}]: capability ref {ref} 未在契约声明")
        for ev in self.evidence_requirements:
            if not ev.evidence_kind:
                violations.append(f"step[{self.step_id}]: 空 evidence_kind")
        return violations


class DecisionOption(SkillAssetModel):
    """决策点的一个分支：什么条件 → 走哪些步骤（显式、可校验）。"""
    option_id: str
    condition: str                     # 机器可读语义条件（审计用；非代码）
    then_step_ids: List[str] = Field(default_factory=list)  # ⊆ procedure.steps
    disclosure: str = ""               # 选择该分支时必须披露的语义（空=无）


class DecisionNode(SkillAssetModel):
    """显式决策点（如 choose_point_or_density_view）。"""
    node_id: str                       # 技能内唯一
    question: str
    options: List[DecisionOption] = Field(min_length=1)
    default_option_id: str = ""        # 证据不足时的确定性缺省分支
    # 决策依据的输入信号（如 geometry/n_f/goal_comparison）——审计面
    decided_by: List[str] = Field(default_factory=list)

    def validate_node(self, known_steps: set) -> List[str]:
        violations: List[str] = []
        seen: set = set()
        for opt in self.options:
            if opt.option_id in seen:
                violations.append(
                    f"decision[{self.node_id}]: duplicate option {opt.option_id}")
            seen.add(opt.option_id)
            for sid in opt.then_step_ids:
                if sid not in known_steps:
                    violations.append(
                        f"decision[{self.node_id}]: option {opt.option_id} "
                        f"引用未知步骤 {sid}")
        if self.default_option_id and self.default_option_id not in seen:
            violations.append(
                f"decision[{self.node_id}]: default {self.default_option_id} 不在选项中")
        if not self.default_option_id:
            violations.append(
                f"decision[{self.node_id}]: 缺 default_option_id（确定性红线）")
        return violations


class RequirementNode(SkillAssetModel):
    """过程级前置/资格节点：进入该技能前必须成立的事实或资格。"""
    node_id: str
    # 资格种类：geometry_kind / data_role / field / crs / scope / measure
    requirement_kind: str
    value: str                         # 期望值（如 "point"、"denominator"）
    reason_code: str = ""              # 不满足时的稳定原因码
    on_fail: str = "reject"            # reject | degrade_with_disclosure


class EvidenceNode(SkillAssetModel):
    """技能级证据节点：完成声明（completion evidence）的种类要求。"""
    node_id: str
    evidence_kind: str
    description: str = ""
    required: bool = True


class FallbackNode(SkillAssetModel):
    """技能级回退节点（S14：禁止 silent fallback）。"""
    node_id: str
    trigger: str                       # ⊆ FALLBACK_TRIGGERS
    action: str                        # ⊆ FALLBACK_ACTIONS
    fallback_skill_id: str = ""        # action=alternative_skill 时必填（可悬空校验）
    disclosure: str = ""               # material 回退必须用户可见
    reason_code: str = ""              # 稳定机器可读码（空则派生）

    def validate_node(self) -> List[str]:
        violations: List[str] = []
        if self.trigger not in FALLBACK_TRIGGERS:
            violations.append(f"fallback[{self.node_id}]: unknown trigger {self.trigger}")
        if self.action not in FALLBACK_ACTIONS:
            violations.append(f"fallback[{self.node_id}]: unknown action {self.action}")
        if self.action == "alternative_skill" and not self.fallback_skill_id:
            violations.append(
                f"fallback[{self.node_id}]: alternative_skill 需要 fallback_skill_id")
        if self.action in ("reduced_output", "methodology_note", "blocked") \
                and not self.disclosure:
            violations.append(
                f"fallback[{self.node_id}]: {self.action} 必须携带 disclosure"
                "（禁止 silent fallback）")
        return violations


class SkillProcedure(SkillAssetModel):
    """技能过程 IR：步骤 + 决策点 + 资格 + 证据 + 回退（声明式，不执行）。"""
    steps: List[ProcedureStep] = Field(min_length=1, max_length=MAX_STEPS)
    decisions: List[DecisionNode] = Field(default_factory=list, max_length=MAX_DECISIONS)
    requirements: List[RequirementNode] = Field(default_factory=list)
    evidence_nodes: List[EvidenceNode] = Field(default_factory=list)
    fallbacks: List[FallbackNode] = Field(default_factory=list, max_length=MAX_FALLBACKS)

    # ── 结构校验（纯函数；loader/validation 共用）────────────────────
    def validate_structure(self, known_capabilities: set) -> List[str]:
        violations: List[str] = []
        step_ids = [s.step_id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            dupes = sorted({s for s in step_ids if step_ids.count(s) > 1})
            violations.append(f"procedure.steps: duplicate ids {dupes}")
        known_steps = set(step_ids)

        declared_cap_ids = known_capabilities
        for step in self.steps:
            violations.extend(step.validate_node(declared_cap_ids))
            for dep in step.depends_on:
                if dep not in known_steps:
                    violations.append(
                        f"step[{step.step_id}]: depends_on 未知步骤 {dep}")
        # 步骤依赖不得成环（显式 depends_on 边）
        violations.extend(_check_step_cycles(self.steps))

        node_ids = [d.node_id for d in self.decisions]
        if len(node_ids) != len(set(node_ids)):
            violations.append("procedure.decisions: duplicate node ids")
        for dec in self.decisions:
            violations.extend(dec.validate_node(known_steps))

        req_ids = [r.node_id for r in self.requirements]
        if len(req_ids) != len(set(req_ids)):
            violations.append("procedure.requirements: duplicate node ids")
        for req in self.requirements:
            if req.requirement_kind not in (
                    "geometry_kind", "data_role", "field", "crs", "scope",
                    "measure_semantics", "temporal_mode"):
                violations.append(
                    f"requirement[{req.node_id}]: unknown kind {req.requirement_kind}")
            if req.on_fail not in ("reject", "degrade_with_disclosure"):
                violations.append(
                    f"requirement[{req.node_id}]: unknown on_fail {req.on_fail}")

        ev_ids = [e.node_id for e in self.evidence_nodes]
        if len(ev_ids) != len(set(ev_ids)):
            violations.append("procedure.evidence_nodes: duplicate node ids")

        fb_ids = [f.node_id for f in self.fallbacks]
        if len(fb_ids) != len(set(fb_ids)):
            violations.append("procedure.fallbacks: duplicate node ids")
        for fb in self.fallbacks:
            violations.extend(fb.validate_node())
        return violations

    def step(self, step_id: str) -> Optional[ProcedureStep]:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def required_steps(self) -> List[ProcedureStep]:
        return [s for s in self.steps if s.required]

    def all_evidence_kinds(self) -> List[str]:
        """全部证据种类（步骤级 + 技能级，去重排序）——replay 的对账域。"""
        kinds = {ev.evidence_kind for s in self.steps
                 for ev in s.evidence_requirements if ev.evidence_kind}
        kinds.update(e.evidence_kind for e in self.evidence_nodes if e.evidence_kind)
        return sorted(kinds)

    def to_bounded_dict(self, *, include_steps: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "steps": [s.step_id for s in self.steps][:MAX_STEPS] if include_steps else len(self.steps),
            "decisions": [d.node_id for d in self.decisions][:MAX_DECISIONS],
            "requirements": [r.node_id for r in self.requirements][:16],
            "evidence_kinds": self.all_evidence_kinds()[:16],
            "fallbacks": [f.node_id for f in self.fallbacks][:MAX_FALLBACKS],
        }
        return payload


def _check_step_cycles(steps: List[ProcedureStep]) -> List[str]:
    """显式 depends_on 边的环检测（Kahn；含自环）。纯函数。"""
    by_id = {s.step_id: s for s in steps}
    indeg = {sid: 0 for sid in by_id}
    out: Dict[str, List[str]] = {sid: [] for sid in by_id}
    for s in steps:
        for dep in s.depends_on:
            if dep in by_id:
                out[dep].append(s.step_id)
                indeg[s.step_id] += 1
    queue = sorted(sid for sid, d in indeg.items() if d == 0)
    seen = 0
    while queue:
        nid = queue.pop(0)
        seen += 1
        for nxt in sorted(out[nid]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen != len(by_id):
        cyclic = sorted(sid for sid, d in indeg.items() if d > 0)
        return [f"procedure.steps: depends_on 环 involving {cyclic[:8]}"]
    return []


__all__ = [
    "STEP_KINDS",
    "SKIP_POLICIES",
    "FALLBACK_ACTIONS",
    "FALLBACK_TRIGGERS",
    "MAX_STEPS",
    "MAX_DECISIONS",
    "MAX_FALLBACKS",
    "StepEvidenceRequirement",
    "ProcedureStep",
    "DecisionOption",
    "DecisionNode",
    "RequirementNode",
    "EvidenceNode",
    "FallbackNode",
    "SkillProcedure",
]
