"""GISActionPlan 编译器：Action IR → 确定性有序 tool DAG（H10 / ADR-0217）。

纯函数、零 I/O、零 LLM。同输入（plan 指纹 + resolver 快照）字节级同输出：

- **contract validation**：tool 解析（capability-first，缺一 fail-closed）、
  副作用一致性（声明 vs resolver 事实）、弃用闸（deprecated → blocking，
  给 superseded_by 建议）；
- **dependency ordering**：Kahn 拓扑（声明序 tie-break），unknown dep /
  cycle → blocked；
- **resource admission hint**：逐维档位计数汇总 —— 只是 hint，准入权威
  仍在 Resource Governor（本模块绝不裁决、绝不阻塞资源面）；
- **failure strategy**：IR 声明即权威（投影期已落定），编译器只校验
  词表与副作用资格（external_io 不可自动补偿 → compensate 声明拒绝）。

编译产物 = dry-run 视图本身；真正执行由 ``executor`` 按 compilation 推进。
compiler 不持久化任何 runtime state。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.lib.gis.action_ir import (
    GISAction,
    GISActionPlan,
    IODescriptor,
    Precondition,
    Compensation,
    digest_of,
)

__all__ = [
    "ToolResolution", "ToolResolver", "CatalogToolResolver",
    "ResolvedAction", "ActionFinding", "ResourceSummary",
    "ActionPlanCompilation", "compile_actions",
]

_STR_MAX = 256
_DETAIL_MAX = 192
_RESOURCE_DIMS = ("latency", "memory", "scale", "cpu", "io")

#: registry SideEffectClass → IR SideEffectClass 映射（唯一权威表）。
_REGISTRY_SIDE_EFFECT_TO_IR: Dict[str, str] = {
    "pure": "pure",
    "deterministic_compute": "pure",
    "cacheable_read": "pure",
    "state_mutation": "session_state",
    "artifact_creation": "derived",
    "external_side_effect": "external_io",
    "destructive": "session_state",  # 破坏性另发 DESTRUCTIVE_TOOL warning
    "unclassified": "",              # 未知 → 跳过一致性裁决（发 warning）
}


class ToolResolution(BaseModel):
    """resolver 对一个动作的解析事实（registry/catalog 的只读投影）。"""

    model_config = ConfigDict(frozen=True)

    tool: str = Field(min_length=1, max_length=96)
    side_effect: str = Field(default="unclassified", max_length=32)
    deterministic: Optional[bool] = None
    deprecated: bool = False
    superseded_by: str = Field(default="", max_length=96)
    input_semantic_types: Tuple[str, ...] = Field(default_factory=tuple)
    output_semantic_types: Tuple[str, ...] = Field(default_factory=tuple)
    resource_class: Mapping[str, str] = Field(default_factory=dict)


class ToolResolver(Protocol):
    """编译期唯一注入面（registry/catalog 快照；测试可 stub）。"""

    def resolve_tool(self, tool_name: str) -> Optional[ToolResolution]:
        """tool 名 → 解析事实；未注册返回 None（fail-closed 依据）。"""
        ...

    def capability_candidates(self, capability_id: str) -> Sequence[str]:
        """capability → 有序工具候选（确定性序；空 = 不可解析）。"""
        ...


class CatalogToolResolver:
    """ExecutionCatalog 快照上的 resolver（lib 层只读投影）。"""

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog

    def resolve_tool(self, tool_name: str) -> Optional[ToolResolution]:
        entry = self._catalog.get("tool", tool_name)
        if entry is None:
            return None
        return ToolResolution(
            tool=str(entry.id),
            side_effect=str(entry.side_effect or "unclassified"),
            deterministic=entry.deterministic,
            deprecated=entry.is_deprecated,
            superseded_by=str(entry.superseded_by or ""),
            input_semantic_types=tuple(entry.input_semantic_types),
            output_semantic_types=tuple(entry.output_semantic_types),
            resource_class={
                k: str(v) for k, v in dict(entry.resource_class or {}).items()
                if k in _RESOURCE_DIMS
            },
        )

    def capability_candidates(self, capability_id: str) -> Sequence[str]:
        try:
            return tuple(self._catalog.tool_candidates_for_capability(capability_id))
        except Exception:  # noqa: BLE001 — 描述性视图失败 = 不可解析
            return ()


# ── 产物模型 ─────────────────────────────────────────────────────────────


class ResolvedAction(BaseModel):
    """一条编译后的有序动作（executor 的执行单元）。"""

    model_config = ConfigDict(frozen=True)

    step: int = Field(ge=1)
    wave: int = Field(ge=0)   # 拓扑深度（同 wave 无依赖，可并行——executor 自决）
    action_id: str = Field(min_length=4, max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    client_action_id: str = Field(min_length=8, max_length=160)
    capability: str = Field(default="", max_length=96)
    tool: str = Field(min_length=1, max_length=96)
    params: Dict[str, Any] = Field(default_factory=dict)
    inputs: List[IODescriptor] = Field(default_factory=list)
    outputs: List[IODescriptor] = Field(default_factory=list)
    preconditions: List[Precondition] = Field(default_factory=list)
    side_effect: str = Field(default="pure", max_length=24)
    idempotency: str = Field(default="idempotent", max_length=24)
    failure: str = Field(default="fail_closed", max_length=24)
    resource_class: Dict[str, str] = Field(default_factory=dict)
    depends_on: List[str] = Field(default_factory=list)   # action_id 引用
    compensation: Compensation = Field(default_factory=Compensation)
    reason_codes: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)


class ActionFinding(BaseModel):
    """一条 contract validation 发现（blocking → 整计划 blocked）。"""

    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1, max_length=64)
    severity: str = Field(default="warning", max_length=16)  # blocking|warning
    action_id: str = Field(default="", max_length=128)
    detail: str = Field(default="", max_length=_DETAIL_MAX)


def _finding(code: str, severity: str, action_id: str, detail: str) -> ActionFinding:
    return ActionFinding(code=code, severity=severity,
                         action_id=action_id[:128], detail=detail[:_DETAIL_MAX])


class ResourceSummary(BaseModel):
    """admission hint（逐维档位计数；非裁决 —— governor 是唯一权威）。"""

    model_config = ConfigDict(frozen=True)

    actions_total: int = Field(default=0, ge=0)
    by_kind: Dict[str, int] = Field(default_factory=dict)
    by_dimension: Dict[str, Dict[str, int]] = Field(default_factory=dict)

    def to_hint(self) -> Dict[str, Any]:
        return self.model_dump()


class ActionPlanCompilation(BaseModel):
    """一次编译的完整产物（= dry-run 视图；executor 的输入）。"""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    plan_version: str
    plan_fingerprint: str
    origin: str = "tool_call"
    revision: int = Field(default=1, ge=1)
    supersedes: str = Field(default="", max_length=128)
    status: str = Field(default="compiled", max_length=24)  # compiled|blocked
    steps: List[ResolvedAction] = Field(default_factory=list)
    findings: List[ActionFinding] = Field(default_factory=list)
    resource_summary: ResourceSummary = Field(default_factory=ResourceSummary)
    compile_digest: str = Field(default="", max_length=80)
    compile_id: str = Field(default="", max_length=48)
    reason_codes: List[str] = Field(default_factory=list, max_length=24)

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    def blocking_findings(self) -> List[ActionFinding]:
        return [f for f in self.findings if f.severity == "blocking"]

    def describe(self) -> str:
        """有界 dry-run 文本投影（LLM/日志视野）。"""
        lines = [
            f"[ActionPlan {self.plan_id}] status={self.status} "
            f"steps={len(self.steps)} findings={len(self.findings)}"
        ]
        for s in self.steps[:20]:
            lines.append(
                f"  {s.step:02d}. {s.kind}:{s.tool} se={s.side_effect} "
                f"fail={s.failure}")
        return "\n".join(lines)


# ── 主编译 ───────────────────────────────────────────────────────────────


def _resolve_tool_for(action: GISAction, resolver: ToolResolver,
                      findings: List[ActionFinding]) -> Optional[ToolResolution]:
    """动作 → 解析事实（tool 直接解析；capability-first 候选首选）。

    capability 命中多候选时取候选序首个（catalog priority,id 稳定序）——
    选择权在 resolver 快照，绝不在 LLM。
    """
    if action.tool:
        res = resolver.resolve_tool(action.tool)
        if res is None:
            findings.append(_finding(
                "TOOL_UNRESOLVED", "blocking", action.action_id,
                f"tool '{action.tool}' not resolvable by resolver snapshot"))
        return res
    candidates = resolver.capability_candidates(action.capability) \
        if action.capability else ()
    if not candidates:
        findings.append(_finding(
            "ACTION_UNRESOLVABLE", "blocking", action.action_id,
            f"capability '{action.capability}' has no executable candidates"))
        return None
    chosen = str(candidates[0])
    res = resolver.resolve_tool(chosen)
    if res is None:
        findings.append(_finding(
            "TOOL_UNRESOLVED", "blocking", action.action_id,
            f"candidate tool '{chosen}' not resolvable"))
    return res


def _check_side_effect(action: GISAction, res: ToolResolution,
                       findings: List[ActionFinding]) -> bool:
    """声明 vs 事实一致性（unclassified 事实 → warning 放行）。"""
    expected = _REGISTRY_SIDE_EFFECT_TO_IR.get(res.side_effect, "")
    if res.side_effect == "unclassified":
        findings.append(_finding(
            "SIDE_EFFECT_UNCLASSIFIED", "warning", action.action_id,
            f"tool '{res.tool}' declares no side-effect class; "
            "declaration kept as authored"))
        return True
    if res.side_effect == "destructive":
        findings.append(_finding(
            "DESTRUCTIVE_TOOL", "warning", action.action_id,
            f"tool '{res.tool}' is destructive; compensation unavailable"))
    if expected != action.side_effect:
        findings.append(_finding(
            "SIDE_EFFECT_MISMATCH", "blocking", action.action_id,
            f"declared side_effect='{action.side_effect}' but tool "
            f"'{res.tool}' implies '{expected}'"))
        return False
    return True


def _check_failure_strategy(action: GISAction,
                            findings: List[ActionFinding]) -> None:
    """external_io 不可自动补偿 → compensate/fail_closed+补偿 声明拒绝。"""
    if action.side_effect != "external_io":
        return
    if action.failure == "compensate":
        findings.append(_finding(
            "COMPENSATION_INFEASIBLE", "blocking", action.action_id,
            "external_io side effect cannot be auto-compensated"))
    if action.compensation.kind != "none":
        findings.append(_finding(
            "COMPENSATION_INFEASIBLE", "blocking", action.action_id,
            f"external_io action declares compensation "
            f"'{action.compensation.kind}'"))


def _order_actions(actions: Sequence[GISAction],
                   findings: List[ActionFinding]) -> List[GISAction]:
    """Kahn 拓扑（声明序 tie-break）；unknown/cycle → blocking finding。"""
    index = {a.action_id: i for i, a in enumerate(actions)}
    ordered: List[GISAction] = []
    pending = list(actions)
    # 重复 action_id 在此拒绝（diff/补偿寻址的唯一键必须唯一）。
    if len(index) != len(actions):
        findings.append(_finding(
            "ACTION_ID_DUPLICATED", "blocking", "",
            "plan contains duplicated action_id"))
        return []
    while pending:
        progressed = False
        remaining: List[GISAction] = []
        for action in pending:
            unknown = [d for d in action.depends_on if d not in index]
            if unknown:
                findings.append(_finding(
                    "DEPENDENCY_UNKNOWN", "blocking", action.action_id,
                    f"depends_on unknown action ids: {unknown[:4]}"))
                continue
            deps_done = all(
                any(done.action_id == d for done in ordered)
                for d in action.depends_on)
            if deps_done:
                ordered.append(action)
                progressed = True
            else:
                remaining.append(action)
        if not progressed:
            if not remaining:
                break  # 全部排空（unknown-dep 动作已被丢弃并记 finding）
            stuck = [a.action_id for a in remaining][:6]
            findings.append(_finding(
                "DEPENDENCY_CYCLE", "blocking", "",
                f"dependency cycle among: {stuck}"))
            return []
        pending = remaining
    return ordered


def _wave_numbers(ordered: Sequence[GISAction]) -> Dict[str, int]:
    wave: Dict[str, int] = {}
    for action in ordered:
        deps = action.depends_on
        wave[action.action_id] = (
            max((wave[d] for d in deps), default=-1) + 1)
    return wave


def _resource_summary(ordered: Sequence[GISAction]) -> ResourceSummary:
    by_kind: Dict[str, int] = {}
    by_dim: Dict[str, Dict[str, int]] = {}
    for action in ordered:
        by_kind[action.kind] = by_kind.get(action.kind, 0) + 1
        for dim in _RESOURCE_DIMS:
            value = str(action.resource_class.get(dim, "") or "")
            if not value:
                continue
            bucket = by_dim.setdefault(dim, {})
            bucket[value[:24]] = bucket.get(value[:24], 0) + 1
    return ResourceSummary(
        actions_total=len(ordered), by_kind=dict(sorted(by_kind.items())),
        by_dimension={d: dict(sorted(v.items()))
                      for d, v in sorted(by_dim.items())})


def compile_actions(
    plan: GISActionPlan,
    *,
    resolver: ToolResolver,
) -> ActionPlanCompilation:
    """GISActionPlan + resolver 快照 → ActionPlanCompilation（纯函数）。"""
    findings: List[ActionFinding] = []
    ordered = _order_actions(plan.actions, findings)

    steps: List[ResolvedAction] = []
    if ordered:
        waves = _wave_numbers(ordered)
        for i, action in enumerate(ordered, start=1):
            res = _resolve_tool_for(action, resolver, findings)
            if res is None:
                continue
            ok = _check_side_effect(action, res, findings)
            _check_failure_strategy(action, findings)
            if res.deprecated:
                findings.append(_finding(
                    "TOOL_DEPRECATED", "blocking", action.action_id,
                    f"tool '{res.tool}' deprecated; use "
                    f"'{res.superseded_by or "(see catalog)"}'"))
                ok = False
            if not ok:
                continue
            steps.append(ResolvedAction(
                step=i,
                wave=waves.get(action.action_id, 0),
                action_id=action.action_id,
                kind=action.kind,
                client_action_id=f"gac.{plan.plan_id}.{i:02d}.{action.kind}",
                capability=action.capability,
                tool=res.tool,
                params=dict(action.params),
                inputs=list(action.inputs),
                outputs=list(action.outputs),
                preconditions=list(action.preconditions),
                side_effect=action.side_effect,
                idempotency=action.idempotency,
                failure=action.failure,
                resource_class={k: str(v) for k, v in
                                sorted(action.resource_class.items())},
                depends_on=list(action.depends_on),
                compensation=action.compensation,
                reason_codes=list(action.reason_codes),
                evidence_refs=list(action.evidence_refs),
            ))

    blocking = [f for f in findings if f.severity == "blocking"]
    status = "blocked" if blocking else "compiled"
    # findings 确定性排序（digest 稳定的前提）。
    findings_sorted = sorted(
        findings, key=lambda f: (f.severity != "blocking", f.action_id, f.code, f.detail))
    digest_payload = {
        "plan_fingerprint": plan.plan_fingerprint(),
        "status": status,
        "steps": [s.model_dump() for s in steps],
        "findings": [f.model_dump() for f in findings_sorted],
        "resource_summary": _resource_summary(ordered).model_dump()
        if ordered else ResourceSummary().model_dump(),
    }
    digest = digest_of(digest_payload)
    return ActionPlanCompilation(
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        plan_fingerprint=plan.plan_fingerprint(),
        origin=plan.origin,
        revision=plan.revision,
        supersedes=plan.supersedes,
        status=status,
        steps=steps,
        findings=findings_sorted,
        resource_summary=_resource_summary(ordered),
        compile_digest=digest[:40],
        compile_id=f"gacc-{digest[:12]}",
        reason_codes=(["ACTION_PLAN_BLOCKED"] if blocking
                      else ["ACTION_PLAN_COMPILED"]),
    )
