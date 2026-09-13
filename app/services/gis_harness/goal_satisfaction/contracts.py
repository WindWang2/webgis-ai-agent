"""Goal Satisfaction 契约层（ADR-0183 G1/G2）—— 需求面 + 证据面的词表与模型。

定位（防重复施工红线，见 docs/dev/harness-goal-evaluator-recon.md §1）：

- 本模块**不是第二个 L5 / Product Verdict**。地图产品裁决（READY 族）、
  七维完成契约、final_map 状态、L5 视觉裁判全部留在既有模块；本层只回答
  一个此前无人回答的问题：**「用户要的任务是否真正完成」**——地图好看
  ≠ 任务完成（比较缺位 / 导出未兑现 / 证据不足时，产品 READY 仍须判
  partial）。
- 全部模型 pydantic v2、可序列化、有界（bounded everything）；推导与
  评估是零 IO 纯函数（同输入同输出），与 SpatialGoalGraph 同纪律。
- 证据分级红线：``deterministic > structural > visual/assisted``；
  **visual/assisted 证据永远不能单独把需求判为 fulfilled**（fail-closed，
  测试钉死）。
"""
from __future__ import annotations

from enum import Enum
from typing import List
from pydantic import BaseModel, Field

# ── 词表（冻结；机器读契约）────────────────────────────────────────────

CONTRACT_SCHEMA = "goal_contract.v1"
REPORT_SCHEMA = "goal_satisfaction.v1"

#: 需求种类（结构化事实可确定性派生的全集；不在词表内的意图不造需求）。
class RequirementKind(str, Enum):
    MAP = "map"                # 用户要一张图（output_intents / map_layers）
    ANALYSIS = "analysis"      # 每个 analysis_steps 行 = 一个子目标
    COMPARISON = "comparison"  # 对比/各区比较（intent.comparison / compare 族行）
    STATISTICS = "statistics"  # 统计/聚合数值（output_intents.statistics）
    CHART = "chart"            # 图表（output_intents.chart）
    EXPORT = "export"          # 导出交付（intent.export_intents → export:<fmt>）


#: 单需求状态词表（G4；任务书词表，master 无冲突）。
class RequirementState(str, Enum):
    FULFILLED = "fulfilled"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    NOT_EVALUATED = "not_evaluated"
    FAILED = "failed"


#: 全局裁决词表。
class GoalVerdict(str, Enum):
    SATISFIED = "satisfied"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILED = "failed"
    NOT_EVALUATED = "not_evaluated"


#: Harness 信号词表（G5；消费方 = continuation / SSE additive 键 / 前端）。
class HarnessSignal(str, Enum):
    COMPLETE = "complete"
    CONTINUE = "continue"
    REPAIR_CARTOGRAPHY = "repair_cartography"
    REPLAN = "replan"
    REQUEST_CLARIFICATION = "request_clarification"
    BLOCKED_BY_DATA = "blocked_by_data"


#: 证据种类（G2；每条绑定既有真相源，不造第二事实源）。
class EvidenceKind(str, Enum):
    TOOL_RECEIPT = "tool_receipt"              # chapter 行状态（capabilities_hit_by_tool）
    DATA_QUALIFICATION = "data_qualification"  # workflow_contract.roles[].status
    ANALYSIS_ARTIFACT = "analysis_artifact"    # 行 bound_ref / 产物在证
    SPATIAL_VALIDATION = "spatial_validation"  # 完成管线空间校验发现
    MAP_PRODUCT = "map_product"                # product_verdict / final_map / task_complete
    CARTOGRAPHY = "cartography"                # V11 cartographic review 摘要（只消费）
    RENDER_OBSERVATION = "render_observation"  # 观察状态（verified/stale/…）
    EXPORT_RECEIPT = "export_receipt"          # 导出回执（format+revision）
    USER_OVERRIDE = "user_override"            # user-wins 披露（隐藏层等）
    VISUAL = "visual"                          # L5 visual judge 摘要（只降级不背书）


#: 证据分级：只有 deterministic/structural 能背书 PASS；visual 只降级；
#: assisted（LLM 语义辅助）只能补注，不能把缺证据判 PASS。
class EvidenceClass(str, Enum):
    DETERMINISTIC = "deterministic"
    STRUCTURAL = "structural"
    VISUAL = "visual"
    ASSISTED = "assisted"


#: 能背书 fulfilled 的证据分级集合（fail-closed 白名单）。
PASS_CAPABLE_CLASSES = frozenset({
    EvidenceClass.DETERMINISTIC.value,
    EvidenceClass.STRUCTURAL.value,
})

#: 证据在证状态。
class EvidenceStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    STALE = "stale"
    FAILED = "failed"


def row_evidence_id(capability: str) -> str:
    """行证据 id 的唯一构造点（截断口径一致 —— 证据面与查询面必须
    逐位同串，否则长 capability 的完成行会被误判 pending）。"""
    return ("row:" + str(capability or "").strip())[:64]


# ── 有界常量 ─────────────────────────────────────────────────────────────
MAX_REQUIREMENTS = 24
MAX_EVIDENCE = 64
MAX_VERDICTS = MAX_REQUIREMENTS
MAX_MISSING = 6
MAX_TEXT = 200
MAX_CONSTRAINTS = 8


# ── 模型 ─────────────────────────────────────────────────────────────────

class GoalRequirement(BaseModel):
    """一条用户任务需求（G1）—— 只从结构化事实派生，不做 raw-text regex。

    ``required=False`` = 可选披露项（无法确定性验证的 output intent，
    如 summary 文案）；缺atisfied不阻断全局 satisfied，但进 ledger。
    ``polarity="must_not"`` = 禁止结局（违反 → 全局 failed）。
    ``pinned=True`` = 用户显式钉死的决策，不得被 graceful-degradation 吞掉。
    """
    id: str
    kind: RequirementKind
    summary: str = ""                  # NL 摘要（来自行 purpose / intent 文本，截断）
    required: bool = True
    polarity: str = Field("must", pattern="^(must|must_not)$")
    pinned: bool = False
    capability: str = ""               # analysis 需求关联的能力行
    export_format: str = ""            # export 需求的目标格式
    scope_name: str = ""               # intent.scope.name（scope-match 规则输入）
    group_by: str = ""                 # intent.group_by（filter-match 规则输入）
    source: str = ""                   # 派生来源（intent:output_intents 等，审计面）

    def to_bounded_dict(self) -> dict:
        return {
            "id": self.id[:64],
            "kind": self.kind.value,
            "summary": self.summary[:MAX_TEXT],
            "required": self.required,
            "polarity": self.polarity,
            "pinned": self.pinned,
            "capability": self.capability[:64],
            "export_format": self.export_format[:16],
            "scope_name": self.scope_name[:64],
            "group_by": self.group_by[:48],
            "source": self.source[:64],
        }


class GoalContract(BaseModel):
    """统一 Goal Contract（G1）—— 用户任务的机器可验需求清单。"""
    schema_version: str = CONTRACT_SCHEMA
    goal_id: str = ""                  # goal_key(chapter, user_goal) 同源
    summary: str = ""                  # 用户目标 NL（有界）
    requirements: List[GoalRequirement] = Field(default_factory=list)
    success_threshold: float = Field(1.0, ge=0.0, le=1.0)
    uncertainty_policy: str = Field("disclose", pattern="^(disclose|block)$")
    user_constraints: List[str] = Field(default_factory=list)  # 显式 must/must-not NL

    def required_ids(self) -> List[str]:
        return [r.id for r in self.requirements
                if r.required and r.polarity == "must"]

    def to_bounded_dict(self) -> dict:
        return {
            "schema": self.schema_version,
            "goal_id": self.goal_id[:128],
            "summary": self.summary[:MAX_TEXT],
            "requirement_count": len(self.requirements),
            "requirements": [r.to_bounded_dict()
                             for r in self.requirements[:MAX_REQUIREMENTS]],
            "success_threshold": self.success_threshold,
            "uncertainty_policy": self.uncertainty_policy,
            "user_constraints": [c[:MAX_TEXT]
                                 for c in self.user_constraints[:MAX_CONSTRAINTS]],
        }


class GoalEvidence(BaseModel):
    """一条证据（G2）—— 引用既有真相源的只读投影，带 source/revision/confidence。"""
    id: str
    kind: EvidenceKind
    evidence_class: EvidenceClass
    status: EvidenceStatus = EvidenceStatus.ABSENT
    source: str = ""                   # 真相源路径（chapter:analysis_steps / map_product …）
    revision: str = ""                 # checked_revision / 指纹摘要（stale 判定输入）
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    ref: str = ""                      # 关联需求 id（直接绑定时）
    detail: str = ""                   # 机器可读补充（codes / layer ids，截断）

    def to_bounded_dict(self) -> dict:
        return {
            "id": self.id[:64],
            "kind": self.kind.value,
            "evidence_class": self.evidence_class.value,
            "status": self.status.value,
            "source": self.source[:64],
            "revision": self.revision[:64],
            "confidence": round(float(self.confidence), 3),
            "ref": self.ref[:64],
            "detail": self.detail[:160],
        }


class RequirementVerdict(BaseModel):
    """单需求裁决（G4 ledger 行）+ 可解释面（G8）。"""
    requirement_id: str
    kind: RequirementKind
    verdict: RequirementState = RequirementState.NOT_EVALUATED
    evidence_ids: List[str] = Field(default_factory=list)
    missing: List[str] = Field(default_factory=list)   # 机器可读缺失原因
    failed_rule: str = ""                              # 命中的失败规则 id
    next_action: str = ""                              # 建议下一步（有界词）
    uncertainty: List[str] = Field(default_factory=list)  # 披露（visual 批评等）

    def to_bounded_dict(self) -> dict:
        return {
            "requirement_id": self.requirement_id[:64],
            "kind": self.kind.value,
            "verdict": self.verdict.value,
            "evidence_ids": [e[:64] for e in self.evidence_ids[:MAX_MISSING]],
            "missing": [m[:96] for m in self.missing[:MAX_MISSING]],
            "failed_rule": self.failed_rule[:48],
            "next_action": self.next_action[:64],
            "uncertainty": [u[:120] for u in self.uncertainty[:4]],
        }


class GoalSatisfactionReport(BaseModel):
    """评估报告（G3+G4+G5+G8 的单一可序列化产物）。"""
    schema_version: str = REPORT_SCHEMA
    goal_id: str = ""
    contract_fingerprint: str = ""
    verdict: GoalVerdict = GoalVerdict.NOT_EVALUATED
    signal: HarnessSignal = HarnessSignal.CONTINUE
    requirements: List[RequirementVerdict] = Field(default_factory=list)
    counts: dict = Field(default_factory=dict)         # verdict → n
    missing_summary: List[str] = Field(default_factory=list)  # 全局缺失面（≤6）
    uncertainties: List[str] = Field(default_factory=list)    # 全局披露（≤4）
    summary_line: str = ""                             # 单行投影（LLM/SSE 面）

    def to_bounded_dict(self) -> dict:
        return {
            "schema": self.schema_version,
            "goal_id": self.goal_id[:128],
            "contract_fingerprint": self.contract_fingerprint[:64],
            "verdict": self.verdict.value,
            "signal": self.signal.value,
            "counts": dict(list(self.counts.items())[:8]),
            "requirements": [r.to_bounded_dict()
                             for r in self.requirements[:MAX_VERDICTS]],
            "missing_summary": [m[:96]
                                for m in self.missing_summary[:MAX_MISSING]],
            "uncertainties": [u[:120] for u in self.uncertainties[:4]],
            "summary_line": self.summary_line[:240],
        }


__all__ = [
    "CONTRACT_SCHEMA",
    "REPORT_SCHEMA",
    "RequirementKind",
    "RequirementState",
    "GoalVerdict",
    "HarnessSignal",
    "EvidenceKind",
    "EvidenceClass",
    "EvidenceStatus",
    "PASS_CAPABLE_CLASSES",
    "MAX_REQUIREMENTS",
    "MAX_EVIDENCE",
    "GoalRequirement",
    "GoalContract",
    "GoalEvidence",
    "RequirementVerdict",
    "GoalSatisfactionReport",
]
