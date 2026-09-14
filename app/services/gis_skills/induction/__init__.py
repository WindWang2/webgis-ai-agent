"""Skill Induction Engine —— 从 ReplayTrace 自合成 GIS Skill（ADR-0191）。

端到端编排::

    trace → analyze_trace（D1 准入门）
          → generalize_parameters（参数泛化）
          → compile_skill（数据流提取 + 契约封装 → ADR-0182 SkillContract）
          → validate_compiled（静态 + 去毒 + 沙盒重放三门）
          → InducedSkillStore.save（induced 草案资产入库；可选）

任一环节失败即整体拒绝（fail-closed），拒绝必须携带机器可读原因码。
全部确定性：零 LLM、零网络；唯一受控副作用是经动态挂钩登记
``induced.cap.*`` 声明型能力与 induced 资产目录写入。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, Field

from app.lib.gis.capability_registry import (
    DYNAMIC_CAPABILITY_PREFIX,
    DYNAMIC_STATUS_ALLOWLIST,
    MAX_DYNAMIC_CAPABILITIES,
    CapabilityRegistry,
)
from app.lib.gis.capability_registry import (
    load_dynamic_capabilities,  # noqa: F401 - 对外面再导出（D5 挂钩）
)

from app.services.gis_skills.induction.dynamic_provider import (
    InducedSkillStore,
    register_dynamic_capability,
)
from app.services.gis_skills.induction.parameter_generalizer import (
    InducedParameter,
    ParameterGeneralization,
    generalize_parameters,
)
from app.services.gis_skills.induction.procedure_compiler import (
    CompiledSkill,
    compile_skill,
)
from app.services.gis_skills.induction.sandbox_validator import (
    SandboxReport,
    VariantReplayResult,
    scan_for_injection,
    topology_fingerprint,
    validate_compiled,
)
from app.services.gis_skills.induction.trace_analyzer import (
    DEFAULT_SATISFACTION_THRESHOLD,
    MIN_INDUCTION_STEPS,
    REJECTION_CODES,
    InducedStep,
    TraceAnalysis,
    analyze_trace,
    merge_analyses,
    trace_satisfaction,
)

__all__ = [
    "DYNAMIC_CAPABILITY_PREFIX",
    "DYNAMIC_STATUS_ALLOWLIST",
    "MAX_DYNAMIC_CAPABILITIES",
    "DEFAULT_SATISFACTION_THRESHOLD",
    "MIN_INDUCTION_STEPS",
    "REJECTION_CODES",
    "InducedParameter",
    "InducedSkillStore",
    "InducedStep",
    "InductionOutcome",
    "ParameterGeneralization",
    "SandboxReport",
    "SkillInductionEngine",
    "TraceAnalysis",
    "VariantReplayResult",
    "CompiledSkill",
    "analyze_trace",
    "compile_skill",
    "generalize_parameters",
    "load_dynamic_capabilities",
    "merge_analyses",
    "register_dynamic_capability",
    "scan_for_injection",
    "topology_fingerprint",
    "trace_satisfaction",
    "validate_compiled",
]


def _default_registry() -> CapabilityRegistry:
    """引擎私有注册表实例（隔离：不污染进程级能力单例）。"""
    registry = CapabilityRegistry()
    registry.load_builtins()
    return registry


class InductionOutcome(BaseModel):
    """一次归纳的端到端裁决（全部字段可序列化，审计面）。"""
    status: str                          # induced | rejected
    analysis: TraceAnalysis
    compiled: Optional[CompiledSkill] = None
    sandbox: Optional[SandboxReport] = None
    rejection_codes: List[str] = Field(default_factory=list)
    report: Dict[str, Any] = Field(default_factory=dict)


class SkillInductionEngine:
    """轨迹 → 技能 的自合成门面（组合 D1 门 + 三步法 + 三重验证）。"""

    def __init__(
        self,
        *,
        registry: Optional[CapabilityRegistry] = None,
        capability_map: Optional[Mapping[str, str]] = None,
        store: Optional[InducedSkillStore] = None,
        satisfaction_threshold: float = DEFAULT_SATISFACTION_THRESHOLD,
        min_steps: int = MIN_INDUCTION_STEPS,
        domain: str = "general",
        variant_scenarios: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.registry = registry if registry is not None else _default_registry()
        self.capability_map = dict(capability_map) if capability_map else None
        self.store = store
        self.satisfaction_threshold = satisfaction_threshold
        self.min_steps = min_steps
        self.domain = domain
        self.variant_scenarios = variant_scenarios

    def induce(self, trace) -> InductionOutcome:
        analysis = analyze_trace(
            trace,
            satisfaction_threshold=self.satisfaction_threshold,
            capability_map=self.capability_map,
            registry=self.registry,
            min_steps=self.min_steps,
        )
        if not analysis.accepted:
            return self._outcome("rejected", analysis, None, None,
                                 list(analysis.rejection_codes))
        return self._induce_from_analysis(analysis)

    def induce_many(self, traces) -> InductionOutcome:
        """同构高分轨迹簇的合并归纳（ADR-0191 D2 跨轨迹证据互证）。

        合取纪律：任一成员未过 D1 门、或簇内拓扑不同构 → 整体拒绝。
        """
        analyses = [
            analyze_trace(t, satisfaction_threshold=self.satisfaction_threshold,
                          capability_map=self.capability_map,
                          registry=self.registry, min_steps=self.min_steps)
            for t in traces
        ]
        if not analyses:
            return self._outcome("rejected",
                                 TraceAnalysis(accepted=False,
                                               rejection_codes=["IND_EMPTY_STEPS"]),
                                 None, None, ["IND_EMPTY_STEPS"])
        bad = [a for a in analyses if not a.accepted]
        if bad:
            codes = sorted({c for a in bad for c in a.rejection_codes})
            return self._outcome("rejected", analyses[0], None, None,
                                 sorted(set(codes) | {"IND_MEMBER_REJECTED"}))
        merged, codes = merge_analyses(analyses)
        if codes:
            return self._outcome("rejected", merged, None, None, codes)
        return self._induce_from_analysis(merged)

    def _induce_from_analysis(self, analysis: TraceAnalysis) -> InductionOutcome:
        generalization = generalize_parameters(
            analysis, capability_map=self.capability_map)
        compiled = compile_skill(
            analysis, generalization,
            registry=self.registry, domain=self.domain,
            capability_map=self.capability_map)
        sandbox = validate_compiled(
            compiled, analysis,
            variant_scenarios=self.variant_scenarios)

        codes: List[str] = []
        if compiled.violations:
            codes.append("IND_COMPILE_VIOLATIONS")
        codes.extend(sandbox.reasons)
        status = "induced" if not codes else "rejected"
        if status == "induced" and self.store is not None:
            self.store.save(compiled)
        return self._outcome(status, analysis, compiled, sandbox, codes)

    @staticmethod
    def _outcome(status: str, analysis: TraceAnalysis,
                 compiled: Optional[CompiledSkill],
                 sandbox: Optional[SandboxReport],
                 codes: List[str]) -> InductionOutcome:
        return InductionOutcome(
            status=status,
            analysis=analysis,
            compiled=compiled,
            sandbox=sandbox,
            rejection_codes=codes,
            report={
                "status": status,
                "skill_id": compiled.contract.id if compiled else "",
                "satisfaction": analysis.satisfaction,
                "session_id": analysis.session_id,
                "turn_id": analysis.turn_id,
                "steps": len(analysis.steps),
                "parameters": [p.name for p in compiled.parameters]
                if compiled else [],
                "rejection_codes": codes,
                "sandbox_accepted": bool(sandbox.accepted) if sandbox else False,
                "topology_fingerprint":
                    topology_fingerprint(compiled.contract) if compiled else "",
            },
        )
