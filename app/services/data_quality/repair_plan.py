"""RepairPlan 实体 —— 质量发现 → 显式修复计划的持久契约（plan-only）。

审计 08 §4.3 / §5.1（"repair-plan state machine MISSING"）：此前没有任何
实体把 QualityIssue 与修复操作连接成可返回、可持久化为证据的计划。本模块
在 Wave-3 的 ``data_ingest.repair_planning.propose_repairs``（单一
QualityIssue → REMEDIATION_OPS 映射）之上构建**计划实体**：

    build_repair_plan(quality_report, dataset_identity) → RepairPlan

红线（与 repair_planning 同纪律）：

- **plan-only**：本模块绝不执行任何修复 —— 执行只经显式调用
  （``repair_spatial_dataset`` 工具 / REST 修复路由 → ``repair_execution``）；
- **单一词表**：``RepairStep.operation`` 只允许引用 ``REMEDIATION_OPS``
  （模块导入期校验，漂移即 fail-fast）；码 → 操作映射只走 W3 的
  ``propose_repairs``，本模块不复制映射表；
- **确定性**：``plan_id`` 是 (ops + dataset_identity) 的 sha256 ——
  同报告同数据身份 ⇒ 同 plan_id（``created_at`` 只是元数据，不参与指纹）；
- **有界**：≤16 步，字段截断；无映射的诊断码诚实不提案（不硬凑）。

计划是被工具返回、并可随 ``repair_evidence`` 落到既有血缘边上的**证据**，
不是新表。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from app.services.data_ingest.repair_planning import propose_repairs
from app.services.gis_harness.data_qualification import REMEDIATION_OPS

#: 计划步数上限（有界投影；与 dispatch seam ≤16 inputs 同一数量级约定）。
_MAX_STEPS = 16
#: reason_codes 每步上限（同操作同参数的码合并时可能多于 1 个）。
_MAX_REASON_CODES_PER_STEP = 4


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class RepairStep:
    """一步显式修复意图（与 W3 RepairProposal / RemediationStep 同形约束）。"""

    operation: str                    # ⊆ REMEDIATION_OPS（词表校验见构造）
    target: str = ""                  # 字段/波段定位；空 = 整个数据集
    params: Dict[str, Any] = field(default_factory=dict)
    reason_codes: Tuple[str, ...] = ()  # 触发本步的质量诊断码（稳定机器可读）
    disclosure: str = ""              # 用户可见披露（为什么会提这一步）
    auto_applicable: bool = False     # True = 有确定性实现且无缺输入
    confidence: float = 0.5

    def __post_init__(self) -> None:
        if self.operation not in REMEDIATION_OPS:
            raise ValueError(
                f"RepairStep.operation '{self.operation}' 不在 REMEDIATION_OPS "
                f"词表内（单一事实源 app.services.gis_harness.data_qualification）"
            )

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation[:32],
            "target": self.target[:64],
            "params": dict(list(self.params.items())[:6]),
            "reason_codes": [str(c)[:64] for c in self.reason_codes[:_MAX_REASON_CODES_PER_STEP]],
            "disclosure": self.disclosure[:200],
            "auto_applicable": self.auto_applicable,
            "confidence": round(self.confidence, 2),
        }


@dataclass(frozen=True)
class RepairPlan:
    """一次质量评估对应的显式修复计划（plan-only 证据，不是新表）。"""

    plan_id: str                      # 确定性 sha（ops + dataset_identity）
    dataset_identity: str = ""        # 数据身份 / 输入指纹（plan_id 的一部分）
    operations: List[RepairStep] = field(default_factory=list)
    created_at: str = ""              # ISO 时间戳；元数据，绝不参与 plan_id
    source_report_digest: str = ""    # 源质量报告的有界摘要指纹

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id[:64],
            "dataset_identity": self.dataset_identity[:128],
            "operations": [step.to_bounded_dict() for step in self.operations[:_MAX_STEPS]],
            "created_at": self.created_at,
            "source_report_digest": self.source_report_digest[:64],
        }


def compute_plan_id(operations: List[RepairStep], dataset_identity: str) -> str:
    """确定性 plan_id：sha256(canonical(ops 有界投影 + dataset_identity))。

    ``created_at`` 等墙钟量刻意排除 —— 同输入同计划 ⇒ 同 id（可幂等复用、
    可作为 ``repair_evidence.plan_id`` 的稳定回链键）。
    """
    payload = _canonical_json(
        {
            "dataset_identity": str(dataset_identity or "")[:128],
            "operations": [step.to_bounded_dict() for step in operations[:_MAX_STEPS]],
        }
    )
    return f"rplan_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _group_key(operation: str, target: str, params: Dict[str, Any]) -> str:
    return _canonical_json(
        {"operation": operation, "target": target, "params": params}
    )


def _source_report_digest(quality_report: Any) -> str:
    """质量报告 → 有界确定性摘要（诊断码 + 严重度 + 字段定位）。

    只取分类学事实，不取消息文本/时间戳 —— 报告的**内容身份**，不是快照。
    """
    issues = list(getattr(quality_report, "issues", None) or [])
    projection = sorted(
        {
            _canonical_json(
                {
                    "code": str(getattr(getattr(i, "code", ""), "value", getattr(i, "code", "")))[:64],
                    "severity": str(getattr(i, "severity", ""))[:16],
                    "field": str(getattr(i, "field", "") or "")[:64],
                }
            )
            for i in issues
        }
    )
    return hashlib.sha256(_canonical_json(projection).encode("utf-8")).hexdigest()


def build_repair_plan(quality_report: Any, dataset_identity: str = "") -> RepairPlan:
    """QualityReport → 有界 RepairPlan（plan-only，绝不执行）。

    码 → 操作映射**只**委托 W3 的 ``propose_repairs``（单一映射，不复制）；
    同 (operation, target, params) 的诊断码合并为一个 RepairStep（reason_codes
    聚合），不同参数的同类操作保持独立步（合并会丢谓词语义）。
    """
    proposals = propose_repairs(quality_report)

    merged: Dict[str, RepairStep] = {}
    order: List[str] = []
    for p in proposals:
        key = _group_key(p.operation, p.target, p.params)
        if key in merged:
            prev = merged[key]
            merged[key] = RepairStep(
                operation=prev.operation,
                target=prev.target,
                params=prev.params,
                reason_codes=(*prev.reason_codes, p.reason_code)[:_MAX_REASON_CODES_PER_STEP],
                disclosure=prev.disclosure,
                auto_applicable=prev.auto_applicable and p.auto_applicable,
                confidence=max(prev.confidence, p.confidence),
            )
            continue
        merged[key] = RepairStep(
            operation=p.operation,
            target=p.target,
            params=dict(p.params),
            reason_codes=(p.reason_code,),
            disclosure=p.disclosure,
            auto_applicable=p.auto_applicable,
            confidence=p.confidence,
        )
        order.append(key)

    steps = [merged[k] for k in order][:_MAX_STEPS]
    return RepairPlan(
        plan_id=compute_plan_id(steps, dataset_identity),
        dataset_identity=str(dataset_identity or "")[:128],
        operations=steps,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_report_digest=_source_report_digest(quality_report),
    )


__all__ = [
    "RepairPlan",
    "RepairStep",
    "build_repair_plan",
    "compute_plan_id",
]
