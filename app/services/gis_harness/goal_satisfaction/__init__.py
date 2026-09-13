"""Harness-level Goal Satisfaction Evaluator（ADR-0183）。

回答既有评价栈不回答的问题：**「用户的任务是否真正完成」**——
工具成功 / 地图好看之外，还需数据充分、分析语义成立、交付兑现、
显式约束被尊重。

- 需求面（G1）：``derive_goal_contract`` / ``resolve_goal_contract``
- 证据面（G2）：``build_evidence_registry``（既有真相源只读投影）
- 裁决面（G3/G4）：``evaluate_goal_satisfaction``（deterministic 纯函数，
  逐需求 ledger + 全局 verdict）
- 信号面（G5）：report.signal ∈ complete/continue/repair_cartography/
  replan/request_clarification/blocked_by_data（Harness 消费）
- 解释面（G8）：report.requirements[*].{evidence_ids,missing,failed_rule,
  next_action,uncertainty} + summary_line

红线：本包不是第二产品裁决（READY 族 / final_map / L5 visual 全部留在
既有模块），只消费不重算（G7）；visual/assisted 证据永不单独判 PASS。
"""
from .contracts import (
    CONTRACT_SCHEMA,
    REPORT_SCHEMA,
    EvidenceClass,
    EvidenceKind,
    EvidenceStatus,
    GoalContract,
    GoalEvidence,
    GoalRequirement,
    GoalSatisfactionReport,
    GoalVerdict,
    HarnessSignal,
    PASS_CAPABLE_CLASSES,
    RequirementKind,
    RequirementState,
    RequirementVerdict,
)
from .evidence import build_evidence_registry, data_family_blockers
from .evaluator import evaluate_goal_satisfaction, resolve_goal_contract
from .requirements import contract_fingerprint, derive_goal_contract

__all__ = [
    "CONTRACT_SCHEMA",
    "REPORT_SCHEMA",
    "PASS_CAPABLE_CLASSES",
    "EvidenceClass",
    "EvidenceKind",
    "EvidenceStatus",
    "GoalContract",
    "GoalEvidence",
    "GoalRequirement",
    "GoalSatisfactionReport",
    "GoalVerdict",
    "HarnessSignal",
    "RequirementKind",
    "RequirementState",
    "RequirementVerdict",
    "build_evidence_registry",
    "contract_fingerprint",
    "data_family_blockers",
    "derive_goal_contract",
    "evaluate_goal_satisfaction",
    "resolve_goal_contract",
]
