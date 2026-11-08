"""族声明 conformance（C3 / GC-03）—— families × 注册表 fail-closed 对账。

``families.validate_families`` 只在测试里被调用（BASELINE C3 断链）——
本模块把族声明对账收敛为**生产可用的纯函数审计器**（对齐
``tool_capability_conformance`` 的纪律：只读投影、不接管裁决、
fail 不进启动闸——报告是披露面，运行期权威仍在
``validate_workflow_contract`` / registry dispatch chokepoint）。

对账面（每族 × 每步骤；声明面事实）：

- tool 注册（ToolRegistry live / 注入）       → FC_TOOL_DANGLING
- capability 可解析（capability_registry）    → FC_CAPABILITY_DANGLING
- 参数单位 ∈ UNIT_VOCABULARY                  → FC_UNIT_OUT_OF_VOCAB
- 科学前置注册（scientific_preconditions）    → FC_PRECONDITION_DANGLING
- 角色需求 ∈ DATA_ROLES                        → FC_ROLE_OUT_OF_VOCAB

（envelope 实测口径的对账——aggregation_basis/实测单位量纲——属运行期
证据面，由 ``run_final_gate`` / envelope v2 语义承载，不在此静态层重复。）
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.analysis_workflows.contracts import (
    UNIT_VOCABULARY,
    AnalysisStepSpec,
)
from app.services.gis_harness.analysis_workflows.families import (
    FAMILY_IDS,
    build_contract,
)

__all__ = [
    "FC_TOOL_DANGLING",
    "FC_CAPABILITY_DANGLING",
    "FC_UNIT_OUT_OF_VOCAB",
    "FC_PRECONDITION_DANGLING",
    "FC_ROLE_OUT_OF_VOCAB",
    "StepConformanceVerdict",
    "FamilyConformanceReport",
    "audit_family_conformance",
]

FC_TOOL_DANGLING = "FC_TOOL_DANGLING"
FC_CAPABILITY_DANGLING = "FC_CAPABILITY_DANGLING"
FC_UNIT_OUT_OF_VOCAB = "FC_UNIT_OUT_OF_VOCAB"
FC_PRECONDITION_DANGLING = "FC_PRECONDITION_DANGLING"
FC_ROLE_OUT_OF_VOCAB = "FC_ROLE_OUT_OF_VOCAB"

_VERDICTS = ("pass", "fail", "not_evaluated")


class StepConformanceVerdict(BaseModel):
    """一步 × 一面的对账裁决（稳定 reason code + 有界 detail）。"""

    family: str = Field("", max_length=64)
    step_id: str = Field("", max_length=64)
    tool: str = Field("", max_length=64)
    verdict: str = "pass"                     # pass | fail | not_evaluated
    reason_code: str = ""
    detail: str = Field("", max_length=200)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "family": self.family[:64],
            "step_id": self.step_id[:64],
            "tool": self.tool[:64],
            "verdict": self.verdict,
            "reason_code": self.reason_code[:48],
            "detail": self.detail[:200],
        }


class FamilyConformanceReport(BaseModel):
    """全族对账报告（确定性排序 + 指纹；漂移检测面）。"""

    schema_version: str = "family_conformance.v1"
    step_verdicts: List[StepConformanceVerdict] = Field(
        default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    fingerprint: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema_version,
            "step_verdicts": [
                v.to_bounded_dict() for v in self.step_verdicts[:96]],
            "counts": dict(list(self.counts.items())[:4]),
            "fingerprint": self.fingerprint[:32],
        }


def _live_tool_exists() -> Optional[Callable[[str], bool]]:
    """生产 ToolRegistry 视图（lifespan 注入缺席 → None=not_evaluated）。"""
    try:
        from app.agent_pi_bridge import try_get_tool_registry

        registry = try_get_tool_registry()
    except Exception:  # noqa: BLE001 — bridge 缺席 = 注册面不可用
        return None
    if registry is None:
        return None
    return registry.has


def _capability_exists(capability: str) -> bool:
    """capability_registry 可解析性（induced.* 动态命名空间同样可查）。"""
    if not capability:
        return False
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        return get_capability_registry().get(capability) is not None
    except Exception:  # noqa: BLE001 — registry 缺席按不可解析（fail-closed）
        return False


def _audit_step(
    family: str,
    step: AnalysisStepSpec,
    tool_exists: Optional[Callable[[str], bool]],
) -> StepConformanceVerdict:
    if tool_exists is None:
        return StepConformanceVerdict(
            family=family, step_id=step.step_id, tool=step.tool_name,
            verdict="not_evaluated",
            reason_code="",
            detail="tool registry view not injected (lifespan absent)")
    if not tool_exists(step.tool_name):
        return StepConformanceVerdict(
            family=family, step_id=step.step_id, tool=step.tool_name,
            verdict="fail", reason_code=FC_TOOL_DANGLING,
            detail=f"步骤引用未注册工具 {step.tool_name}（模板腐烂——"
                   "run 期 AWF_TOOL_UNREGISTERED 前必须在此面可见）")
    if step.capability and not _capability_exists(step.capability):
        return StepConformanceVerdict(
            family=family, step_id=step.step_id, tool=step.tool_name,
            verdict="fail", reason_code=FC_CAPABILITY_DANGLING,
            detail=f"步骤声明能力 {step.capability} 在 capability_registry"
                   " 不可解析")
    for param, unit in sorted(step.unit_contract.items()):
        if unit not in UNIT_VOCABULARY:
            return StepConformanceVerdict(
                family=family, step_id=step.step_id, tool=step.tool_name,
                verdict="fail", reason_code=FC_UNIT_OUT_OF_VOCAB,
                detail=f"参数 {param} 单位 {unit} 越界（词表 {UNIT_VOCABULARY}）")
        # （量纲混淆的声明面守卫即词表成员校验；消费面[envelope units vs
        # 参数合同]由运行期 run_final_gate 承载——数值参数无量纲声明，
        # 此处不重复空转扫描。）
    try:
        from app.lib.gis.scientific_preconditions import (
            precondition_exists as _pc,
        )
        for pre in step.preconditions:
            if not _pc(pre):
                return StepConformanceVerdict(
                    family=family, step_id=step.step_id, tool=step.tool_name,
                    verdict="fail", reason_code=FC_PRECONDITION_DANGLING,
                    detail=f"科学前置 {pre} 未注册")
    except Exception:  # noqa: BLE001 — 前置注册面缺席 = not_evaluated
        return StepConformanceVerdict(
            family=family, step_id=step.step_id, tool=step.tool_name,
            verdict="not_evaluated",
            detail="scientific_preconditions registry unavailable")
    return StepConformanceVerdict(
        family=family, step_id=step.step_id, tool=step.tool_name,
        verdict="pass", detail="")


def audit_family_conformance(
    *,
    tool_exists: Optional[Callable[[str], bool]] = None,
    family_ids: Optional[List[str]] = None,
) -> FamilyConformanceReport:
    """全族 × 全步骤对账（纯函数；registry 视图注入或 live 读取）。

    ``tool_exists=None`` → 尝试 live ToolRegistry（缺席 → 该步
    not_evaluated——诚实留白，绝不虚构 pass）；测试注入
    ``tool_exists=lambda t: False`` → 全部悬空 fail（fail-closed 验证）。
    """
    checker = tool_exists
    if checker is None:
        checker = _live_tool_exists()
    families = tuple(family_ids) if family_ids else FAMILY_IDS

    verdicts: List[StepConformanceVerdict] = []
    role_verdicts: List[StepConformanceVerdict] = []
    from app.services.gis_harness.workflow_schema import DATA_ROLES

    for family in families:
        if family not in FAMILY_IDS:
            continue
        contract = build_contract(
            family, workflow_id=f"awf.__conformance__.{family}")
        for step in contract.steps:
            verdicts.append(_audit_step(family, step, checker))
        for req in contract.data_contracts.values():
            if req.role not in DATA_ROLES:
                role_verdicts.append(StepConformanceVerdict(
                    family=family, step_id=f"role:{req.role}",
                    tool="", verdict="fail",
                    reason_code=FC_ROLE_OUT_OF_VOCAB,
                    detail=f"角色 {req.role} 越界（词表 {DATA_ROLES}）"))

    all_verdicts = verdicts + role_verdicts
    all_verdicts.sort(key=lambda v: (v.family, v.step_id))
    counts: Dict[str, int] = {}
    for v in all_verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1

    payload = [v.to_bounded_dict() for v in all_verdicts]
    digest = hashlib.sha256(
        repr(payload).encode("utf-8")).hexdigest()[:32]
    return FamilyConformanceReport(
        step_verdicts=all_verdicts[:96],
        counts=counts,
        fingerprint=digest,
    )
