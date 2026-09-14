"""Sandbox Validator —— 合成技能的三重验证门禁（ADR-0191 D4）。

入库前合取门，任一失败整体拒绝（不静默改写放行）：

1. **静态类型检查**：``validate_contract`` 结构校验零违规 + 编译期
   违规清空 + YAML round-trip 幂等 + 参数模型可实例化；
2. **去毒过滤（Detox）**：契约/参数/溯源的全部字符串面做代码注入
   模式扫描（import/eval/exec/dunder/模板注入/shell/绝对路径/URL），
   命中即整体拒绝（``SBX_DETOX_BLOCKED``）；
3. **沙盒重放验证（Replay Verification）**：零 I/O、零网络、零 LLM
   的离线 Mock 执行——自重放（源轨迹投影 × ``replay_procedure``
   必须 complete）+ 变体重放（约束内新参数拓扑指纹一致；越界参数
   Schema 层拒绝；注入值运行期去毒拒绝）。

全部确定性：同输入同输出。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.services.gis_harness.skills.contract import SkillContract
from app.services.gis_harness.skills.replay import replay_procedure

#: 去毒模式词表（tag, 编译正则）；扫描命中任意一个即拒绝。
DETOX_PATTERNS = (
    ("code_import", re.compile(r"\b(?:import|__import__)\b", re.IGNORECASE)),
    ("code_exec", re.compile(r"\b(?:eval|exec|compile)\s*\(", re.IGNORECASE)),
    ("code_def", re.compile(r"\bdef\s+\w+\s*\(", re.IGNORECASE)),
    ("dunder", re.compile(r"__\w+__")),
    ("template_inject", re.compile(r"\$\{|\{\{|\{%")),
    ("shell_exec", re.compile(r"\b(?:os\.system|subprocess|popen)\b",
                              re.IGNORECASE)),
    ("abs_path_win", re.compile(r"[A-Za-z]:\\")),
    ("abs_path_unix", re.compile(r"(?<![\w])/(?:etc|usr|bin|var|home|root|tmp)\b")),
    ("url", re.compile(r"(?<![\w])(?:https?|file|ftp)://", re.IGNORECASE)),
)


def scan_for_injection(text: str) -> Optional[str]:
    """文本去毒扫描：命中返回模式 tag，干净返回 None。纯函数。"""
    value = text or ""
    for tag, pattern in DETOX_PATTERNS:
        if pattern.search(value):
            return tag
    return None


def _iter_strings(payload: Any, path: str = ""):
    if isinstance(payload, str):
        yield path, payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            yield from _iter_strings(value, f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for i, value in enumerate(payload):
            yield from _iter_strings(value, f"{path}[{i}]")


#: 公开别名（dynamic_provider 等消费方面扫描复用）。
iter_strings = _iter_strings


def _canonical_fingerprint(shape: List[List[Any]]) -> str:
    canonical = json.dumps(shape, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8"),
                          usedforsecurity=False).hexdigest()


def topology_fingerprint(contract: SkillContract) -> str:
    """过程拓扑指纹：契约有序 (能力引用, kind) 序列的 canonical SHA256。"""
    return _canonical_fingerprint(
        [[sorted(s.capability_refs), s.kind] for s in contract.procedure.steps])


def source_topology_fingerprint(analysis) -> str:
    """源轨迹拓扑指纹：分析产物步骤的 (能力投影, kind) 序列。

    契约指纹必须与它一致（编译器忠实转写源拓扑）；变体重放的一致性
    也以**源**指纹为基准——防止"契约和自己比"的恒绿灯（审查 P2）。
    """
    return _canonical_fingerprint(
        [[sorted([s.capability_id]), s.kind] for s in analysis.steps])


def source_evidence_kinds(analysis) -> List[str]:
    """源轨迹产出的证据种类（trace receipts 投影——自重放对账域）。"""
    kinds: set = set()
    for step in analysis.steps:
        kinds.update(step.evidence_kinds)
    return sorted(kinds)


def _facts_from_source(analysis) -> Dict[str, List[Any]]:
    """D4 自重放事实面 = **源轨迹投影**（不是被测契约自身，否则恒绿）。"""
    return {
        "capabilities": sorted({s.capability_id for s in analysis.steps}),
        "step_ids": [f"s{s.seq}" for s in analysis.steps],
        "evidence_kinds": source_evidence_kinds(analysis),
    }


def _evidence_facts(contract: SkillContract) -> Dict[str, Any]:
    produced = set(contract.procedure.all_evidence_kinds())
    produced.update(o.evidence_kind for o in contract.quality_obligations
                    if o.evidence_kind)
    return {
        "capabilities": contract.capability_ids(),
        "step_ids": [s.step_id for s in contract.procedure.steps],
        "evidence_kinds": sorted(produced),
    }


class VariantReplayResult(BaseModel):
    """一个变体场景的重放裁决。"""
    scenario: str
    expect: str = "ok"
    passed: bool = False
    detail: str = ""
    topology_fingerprint: str = ""


class SandboxReport(BaseModel):
    """三重门禁的汇总裁决（可序列化，随 InductionReport 落盘）。"""
    static_ok: bool = False
    static_violations: List[str] = Field(default_factory=list)
    detox_ok: bool = False
    detox_findings: List[str] = Field(default_factory=list)
    self_replay_complete: bool = False
    self_replay_detail: str = ""
    topology_ok: bool = False
    source_fingerprint: str = ""
    variant_results: List[VariantReplayResult] = Field(default_factory=list)
    accepted: bool = False
    reasons: List[str] = Field(default_factory=list)


def _static_gate(compiled) -> List[str]:
    violations: List[str] = list(compiled.violations)
    violations.extend(compiled.contract.validate_contract())
    try:
        restored = SkillContract.model_validate(
            yaml.safe_load(compiled.yaml_text))
        if restored != compiled.contract:
            violations.append("yaml_round_trip: 资产往返不一致")
    except ValidationError as exc:
        violations.append(f"yaml_round_trip: 装载失败 ({exc})")
    except Exception as exc:  # noqa: BLE001 - yaml 层任何失败都是静态违规
        violations.append(f"yaml_round_trip: 解析失败 ({exc})")
    try:
        compiled.build_parameter_model()
    except Exception as exc:  # noqa: BLE001 - 参数模型不可实例化即违规
        violations.append(f"parameter_model: 不可实例化 ({exc})")
    return violations


def _detox_gate(compiled) -> List[str]:
    findings: List[str] = []
    surfaces = [compiled.contract.model_dump(),
                [p.model_dump() for p in compiled.parameters],
                compiled.provenance]
    for surface in surfaces:
        for path, text in _iter_strings(surface):
            tag = scan_for_injection(text)
            if tag:
                findings.append(f"{path}: {tag}")
    return findings


def _run_variant(compiled, scenario: Dict[str, Any],
                 source_fingerprint: str) -> VariantReplayResult:
    """Mock 离线执行器：去毒 → Schema → 沿编译拓扑仿真 → 断言对账。"""
    name = str(scenario.get("name") or "variant")
    expect = str(scenario.get("expect") or "ok")
    params = scenario.get("params") if isinstance(scenario.get("params"),
                                                  dict) else {}

    def finish(passed: bool, detail: str,
               fingerprint: str = "") -> VariantReplayResult:
        return VariantReplayResult(scenario=name, expect=expect,
                                   passed=passed, detail=detail,
                                   topology_fingerprint=fingerprint)

    # 1) 运行期去毒：字符串参数值不得携带注入载荷
    for key, value in sorted(params.items()):
        if isinstance(value, str):
            tag = scan_for_injection(value)
            if tag:
                if expect == "reject":
                    return finish(True, f"detox:{tag} rejected")
                return finish(False, f"detox:{tag} blocked execution")

    # 2) 参数 Schema：越界/类型错误值必须在此被拒
    try:
        compiled.build_parameter_model()(**params)
    except ValidationError as exc:
        if expect == "reject":
            return finish(True, "param_schema rejected")
        return finish(False, f"param_schema: {exc.errors()[:1]}")
    if expect == "reject":
        return finish(False, "expected reject but schema accepted")

    # 3) 拓扑仿真 + 过程断言：证据齐备、报告 complete、指纹与源一致
    facts = _evidence_facts(compiled.contract)
    report = replay_procedure(compiled.contract,
                              plan_facts={"capabilities": facts["capabilities"],
                                          "step_ids": facts["step_ids"]},
                              evidence_facts={"evidence_kinds":
                                              facts["evidence_kinds"]})
    fingerprint = topology_fingerprint(compiled.contract)
    if not report.complete:
        return finish(False, f"replay_incomplete: {report.missing_steps}",
                      fingerprint)
    if fingerprint != source_fingerprint:
        return finish(False, "topology_drift", fingerprint)
    return finish(True, "topology_stable", fingerprint)


def validate_compiled(compiled, analysis,
                      *, variant_scenarios: Optional[List[Dict[str, Any]]] = None
                      ) -> SandboxReport:
    """三重门禁总入口（纯函数；零 I/O、零网络、零 LLM）。

    自重放与拓扑对账全部以**源轨迹投影**（``analysis``）为基准：
    契约证据面 ⊉ 源 receipts → 自重放 incomplete；契约拓扑 ≠ 源拓扑
    → SBX_TOPOLOGY_DRIFT——杜绝"契约和自己比"的恒绿灯。
    """
    static_violations = _static_gate(compiled)
    detox_findings = _detox_gate(compiled)

    facts = _facts_from_source(analysis)
    self_report = replay_procedure(
        compiled.contract,
        plan_facts={"capabilities": facts["capabilities"],
                    "step_ids": facts["step_ids"]},
        evidence_facts={"evidence_kinds": facts["evidence_kinds"]})
    source_fp = source_topology_fingerprint(analysis)
    contract_fp = topology_fingerprint(compiled.contract)
    topology_ok = source_fp == contract_fp

    variants = [
        _run_variant(compiled, scenario, source_fp)
        for scenario in (variant_scenarios or [])
    ]

    reasons: List[str] = []
    if static_violations:
        reasons.append("SBX_STATIC_VIOLATIONS")
    if detox_findings:
        reasons.append("SBX_DETOX_BLOCKED")
    if not self_report.complete:
        reasons.append("SBX_SELF_REPLAY_INCOMPLETE")
    if not topology_ok:
        reasons.append("SBX_TOPOLOGY_DRIFT")
    for variant in variants:
        if not variant.passed:
            reasons.append(f"SBX_VARIANT_FAILED:{variant.scenario}")

    return SandboxReport(
        static_ok=not static_violations,
        static_violations=static_violations[:16],
        detox_ok=not detox_findings,
        detox_findings=detox_findings[:16],
        self_replay_complete=self_report.complete,
        self_replay_detail="" if self_report.complete
        else f"missing={self_report.missing_steps}",
        topology_ok=topology_ok,
        source_fingerprint=source_fp,
        variant_results=variants,
        accepted=not reasons,
        reasons=reasons,
    )


__all__ = [
    "DETOX_PATTERNS",
    "SandboxReport",
    "VariantReplayResult",
    "iter_strings",
    "scan_for_injection",
    "topology_fingerprint",
    "validate_compiled",
]
