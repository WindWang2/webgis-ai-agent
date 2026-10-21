"""Science Final Gate —— 结果入图前的科学 evidence 对账门（G02，schema v1）。

三段守门的第三段：plan（resolver 科学门 + 转录 evidence）→ execute
（re-resolution fail-closed + execution evidence）→ **final（本模块）**。

输入（全部可选、零 IO、确定性）：

- ``plan.algorithm_selections`` —— plan 侧转录（planner.AlgorithmSelectionRecord
  兼容鸭子类型：status/algorithm/fallback_trail/not_evaluated/profile_digest）；
- ``execution_evidence`` —— step_id → workflow trace 的 resolution_evidence
  （profile_digest / science_hard_block / not_evaluated / fallback_trail）。

判定词表（severity：block | disclose；有界 ≤16 findings）：

- G-FALLBACK-SEMANTICS（disclose）：发生非等价算法替换（semantics ∈
  proxy/degraded/approximation）—— 制品必须披露，不改变业务语义；
- G-NOT-EVALUATED（disclose）：PASS-deferred 前提（未知 ≠ 科学通过）——
  制品以 not_evaluated 披露，诚实降级不阻断；
- G-FRESHNESS（disclose）：plan/execute 画像摘要不一致（共享事实键变化）
  —— TOCTOU 痕迹入账（执行门已用新事实复验）；
- G-SCIENCE-BLOCKED（block）：执行 evidence 携带 science_hard_block ——
  该能力的结果不可入图（fail-closed 的最终对账锚点）。

本模块不重造 export conformance/manifest（#1755 已落地），只做方法科学
evidence 对账；不抛错、不阻断调用方（block 以 ``blocked`` 旗标机读）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

SCIENCE_GATE_SCHEMA_VERSION = "1"

#: 科学守门消费的画像事实键词表（plan/execute 两侧 freshness token 的
#: 单一键集 —— 两投影函数（to_resolver_profile / profile_from_descriptor）
#: 的公共词汇；未在两侧同时出现的键不参与对账，避免派生深度差异误报）。
SCIENCE_FACT_KEYS = (
    "featureCount", "geometryTypes", "bbox", "crs", "fields",
    "fields_status", "numericFields", "categoricalFields", "binaryFields",
    "hasTimeField", "temporalObservationCount", "valueVariance",
    "bandCount", "numericSampleCount",
)

#: 非等价 fallback 语义类（词表权威：FallbackSemanticsClass；equivalent
#: 之外的替换都必须在制品披露面出现）。
NON_EQUIVALENT_SEMANTICS = ("approximation", "proxy", "degraded")

_MAX_FINDINGS = 16
_MAX_DETAIL = 160


def science_profile_digest(profile: Optional[Dict[str, Any]]) -> str:
    """画像 → freshness token（SCIENCE_FACT_KEYS 键集的确定性摘要）。

    plan/execute/final 三段共用的单一实现（单一真相纪律：序列化复用
    provenance.fingerprint.canonical_dumps）。空画像/异常 → 空串（诚实
    缺省，绝不虚构可对账性）。
    """
    if not isinstance(profile, dict) or not profile:
        return ""
    subset = {k: profile[k] for k in SCIENCE_FACT_KEYS if k in profile}
    try:
        from app.services.provenance.fingerprint import canonical_dumps

        return hashlib.sha256(
            canonical_dumps(subset).encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001 — token 是增值证据，失败不阻断
        return ""


class ScienceGateFinding(BaseModel):
    """单条科学对账发现（机读 code + severity + 定位 + 有界 detail）。"""

    code: str
    severity: str = "disclose"   # block | disclose
    subject: str = ""            # capability / algorithm / step_id
    detail: str = ""

    @field_validator("detail")
    @classmethod
    def _bounded_detail(cls, v: str) -> str:
        return str(v)[:_MAX_DETAIL]

    @field_validator("severity")
    @classmethod
    def _severity_vocabulary(cls, v: str) -> str:
        if v not in ("block", "disclose"):
            raise ValueError(f"unknown severity: {v}")
        return v


class ScienceGateResult(BaseModel):
    """final gate 聚合结果（有界、可序列化、blocked 机读旗标）。"""

    schema_version: str = SCIENCE_GATE_SCHEMA_VERSION
    findings: List[ScienceGateFinding] = Field(
        default_factory=list, max_length=_MAX_FINDINGS)
    blocked: bool = False
    #: 披露文案（人读/制品面；与 findings 一一对应的短句，≤8 条）
    disclosures: List[str] = Field(default_factory=list, max_length=8)


def _selection_view(selection: Any) -> Dict[str, Any]:
    """selection（pydantic record / dict / 任意鸭子类型）→ 有界视图。"""
    if isinstance(selection, dict):
        return selection
    return {
        "capability": getattr(selection, "capability", ""),
        "status": getattr(selection, "status", ""),
        "algorithm": getattr(selection, "algorithm", ""),
        "fallback_trail": getattr(selection, "fallback_trail", None) or [],
        "not_evaluated": getattr(selection, "not_evaluated", None) or [],
        "profile_digest": getattr(selection, "profile_digest", ""),
    }


def _trail_semantics(step: Any) -> Tuple[str, str]:
    """(from, to, semantics) —— FallbackStep / dict 双形态。"""
    if isinstance(step, dict):
        return (
            str(step.get("from", "") or step.get("from_element", "")),
            str(step.get("to", "") or step.get("to_element", "")),
            str(step.get("semantics", "")),
        )
    return (
        str(getattr(step, "from_element", "") or getattr(step, "from", "")),
        str(getattr(step, "to_element", "") or getattr(step, "to", "")),
        str(getattr(step, "semantics", "")),
    )


def run_science_final_gate(
    plan: Any = None,
    *,
    execution_evidence: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ScienceGateResult:
    """对账 plan/execute 科学 evidence → 有界 findings（绝不抛错）。"""
    findings: List[ScienceGateFinding] = []
    disclosures: List[str] = []
    blocked = False

    selections = list(getattr(plan, "algorithm_selections", None) or []) \
        if plan is not None else []

    for sel in selections:
        view = _selection_view(sel)
        capability = str(view.get("capability", "") or "")[:64]
        algorithm = str(view.get("algorithm", "") or "")[:64]

        # G-FALLBACK-SEMANTICS：非等价替换必须可披露
        for step in list(view.get("fallback_trail", []))[:4]:
            frm, to, semantics = _trail_semantics(step)
            if semantics in NON_EQUIVALENT_SEMANTICS:
                findings.append(ScienceGateFinding(
                    code="G-FALLBACK-SEMANTICS",
                    subject=capability or algorithm,
                    detail=f"{frm} → {to} ({semantics})"))
                if len(disclosures) < 8:
                    disclosures.append(
                        f"方法替换：{frm} → {to}（科学等价性：{semantics}）")

        # G-NOT-EVALUATED：PASS-deferred 前提披露
        unevaluated = [
            str(e) for e in list(view.get("not_evaluated", []))[:4] if e]
        if unevaluated:
            findings.append(ScienceGateFinding(
                code="G-NOT-EVALUATED",
                subject=capability or algorithm,
                detail="; ".join(unevaluated)[:_MAX_DETAIL]))
            if len(disclosures) < 8:
                disclosures.append(
                    f"未评估前提（数据事实缺席，非科学通过）："
                    f"{capability or algorithm} — {unevaluated[0][:80]}")

    # 执行 evidence 对账（可选注入；注入即激活 freshness/block 面）
    exec_caps: Dict[str, Dict[str, Any]] = {}
    if execution_evidence:
        for step_id, ev in execution_evidence.items():
            if not isinstance(ev, dict):
                continue
            cap = str(ev.get("capability", "") or "")[:64]
            if cap:
                exec_caps[cap] = ev
            if ev.get("science_hard_block"):
                blocked = True
                findings.append(ScienceGateFinding(
                    code="G-SCIENCE-BLOCKED",
                    severity="block",
                    subject=str(step_id)[:64],
                    detail=str(ev["science_hard_block"])[:_MAX_DETAIL]))

    if execution_evidence and selections:
        for sel in selections:
            view = _selection_view(sel)
            capability = str(view.get("capability", "") or "")
            plan_digest = str(view.get("profile_digest", "") or "")
            ev = exec_caps.get(capability)
            if not ev or not plan_digest:
                continue
            exec_digest = str(ev.get("profile_digest", "") or "")
            if exec_digest and plan_digest != exec_digest:
                findings.append(ScienceGateFinding(
                    code="G-FRESHNESS",
                    subject=capability,
                    detail=(
                        f"plan={plan_digest} execute={exec_digest}"
                        "（数据事实在 plan 后变化；执行门已按新事实复验）")))

    # 有界 + 截断后重算
    findings = findings[:_MAX_FINDINGS]
    return ScienceGateResult(
        findings=findings,
        blocked=blocked,
        disclosures=disclosures[:8],
    )
