"""Trace Analyzer —— D1 归纳准入门与调用链提取（ADR-0191 D1/D2）。

输入是 ReplayTrace（``app/lib/harness/replay/schema.py``，生产录制的
打包单元），职责有二：

1. **准入门（fail-closed 合取门）**：满意度 ≥ 阈值且 verdict=satisfied、
   链完整（未截断/无错误调用/无 digest-only 参数降级）、步骤数达到
   归纳下限。任一不满足即拒绝并留下 ``IND-*`` 机器可读原因码——
   失败或低满意度轨迹绝不能被固化为技能（ADR-0191 §1 反面红线）。
2. **提取**：消毒后的工具调用序列 → 有序步骤（工具名、能力投影、
   过程 kind、参数载荷、证据种类）+ 变异/产物事件计数，供下游
   参数泛化与过程编译消费。

全部确定性：零 LLM、零 I/O、零网络。词表与判定规则见
docs/dev/skill-induction-spec.md §3。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field

from app.lib.harness.replay.schema import ReplayTrace

#: 拒绝原因码词表（机器可读；轨迹治理面消费）。
REJECTION_CODES = (
    "IND_EMPTY_STEPS",
    "IND_TOO_FEW_STEPS",
    "IND_TRACE_TRUNCATED",
    "IND_TOOL_ERROR",
    "IND_DIGEST_ONLY_ARGS",
    "IND_NO_SATISFACTION_FACE",
    "IND_VERDICT_NOT_SATISFIED",
    "IND_SATISFACTION_BELOW_THRESHOLD",
    "IND_INCOMPATIBLE_TOPOLOGY",
    "IND_MEMBER_REJECTED",
)

#: D1 满意度门槛（任务书：高满意度 ≥0.95）。
DEFAULT_SATISFACTION_THRESHOLD = 0.95

#: 归纳下限：少于该步数的轨迹不构成可复用的"作业流程"。
MIN_INDUCTION_STEPS = 3

#: 工具名 → 过程 kind 的确定性映射（首个命中的子串规则；顺序即优先级：
#: 交付语义最具体故最先判定，查询/检视语义最弱故最后兜底）。
STEP_KIND_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("deliver", ("export", "product", "compose", "deliver", "report",
                 "render")),
    ("analyze", ("threshold", "exceedance", "exposure", "statistic",
                 "aggregate", "overlay", "join", "density", "index",
                 "compute", "interpolate", "regress", "cluster")),
    ("design", ("symbolize", "symbology", "style", "colormap", "label")),
    ("validate", ("validate", "qc", "check", "verify", "audit")),
    ("decide", ("decide", "choose", "select_method")),
    ("prepare", ("clip", "filter", "project", "buffer", "reproject",
                 "extract", "prepare", "dissolve", "merge", "build")),
    ("inspect", ("query", "fetch", "search", "load", "profile", "inspect",
                 "list")),
)
_DEFAULT_STEP_KIND = "analyze"


def step_kind_for(tool_name: str) -> str:
    """工具名 → 过程 kind（确定性子串规则；未命中走缺省 analyze）。"""
    name = (tool_name or "").lower()
    for kind, keywords in STEP_KIND_RULES:
        if any(k in name for k in keywords):
            return kind
    return _DEFAULT_STEP_KIND


def project_capability_id(tool_name: str, *,
                          capability_map: Optional[Mapping[str, str]] = None,
                          registry=None) -> str:
    """工具 → capability id 投影：显式映射 > registry 查证 > induced 命名。

    未注册工具以 ``induced.cap.<slug>`` 命名空间诚实登记（不虚构核心
    词汇；见 ADR-0191 D2/D5）。
    """
    if capability_map and tool_name in capability_map:
        return capability_map[tool_name]
    if registry is not None and registry.has(tool_name):
        return tool_name
    slug = "".join(ch if ch.isalnum() else "_" for ch in tool_name.lower())
    slug = "_".join(part for part in slug.split("_") if part)[:48] or "tool"
    return f"induced.cap.{slug}"


def _goal_satisfaction_face(trace: ReplayTrace) -> Optional[Dict[str, Any]]:
    verdict = trace.verdict if isinstance(trace.verdict, dict) else {}
    product = verdict.get("map_product")
    if not isinstance(product, dict):
        return None
    face = product.get("goal_satisfaction")
    return face if isinstance(face, dict) else None


def trace_satisfaction(trace: ReplayTrace | Mapping[str, Any]) -> Optional[float]:
    """满意度确定性投影：``fulfilled / Σcounts``；无面或零需求 → None。

    unknown ≠ 合格（fail-closed）：没有满意度事实面的轨迹不参与归纳。
    """
    t = trace if isinstance(trace, ReplayTrace) else ReplayTrace.from_dict(
        dict(trace))
    face = _goal_satisfaction_face(t)
    if not face:
        return None
    counts = face.get("counts")
    if not isinstance(counts, dict) or not counts:
        return None
    total = 0
    fulfilled = 0
    for key, value in counts.items():
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        total += n
        if key == "fulfilled":
            fulfilled = n
    if total <= 0:
        return None
    return round(fulfilled / total, 4)


class InducedStep(BaseModel):
    """轨迹中的一个规范化步骤（消毒参数载荷 + 能力/kind 投影）。

    ``observations`` 是跨轨迹合并时的参数证据池（键 → 多次观测值），
    单轨迹归纳时为空、泛化只看 ``arguments``。
    """
    seq: int
    call_id: str = ""
    tool_name: str
    capability_id: str
    kind: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    observations: Dict[str, List[Any]] = Field(default_factory=dict)
    evidence_kinds: List[str] = Field(default_factory=list)
    result_status: str = ""
    duration_ms: float = 0.0
    is_mutation: bool = False


class TraceAnalysis(BaseModel):
    """D1 准入裁决 + 提取产物（可序列化，随 InductionReport 落盘）。"""
    accepted: bool = False
    rejection_codes: List[str] = Field(default_factory=list)
    satisfaction: Optional[float] = None
    goal_text: str = ""
    user_input: str = ""
    steps: List[InducedStep] = Field(default_factory=list)
    artifacts_count: int = 0
    mutations_count: int = 0
    session_id: str = ""
    turn_id: str = ""
    selected_workflow: str = ""
    behavior_digest: str = ""


def _as_replay_trace(trace: Any) -> Optional[ReplayTrace]:
    if isinstance(trace, ReplayTrace):
        return trace
    if isinstance(trace, Mapping):
        try:
            return ReplayTrace.from_dict(dict(trace))
        except (TypeError, ValueError):
            return None
    return None


def _evidence_kinds_for(kind: str, tool_name: str) -> List[str]:
    kinds = [f"{tool_name}.receipt"]
    if kind == "deliver":
        kinds.append("product_completeness")
    return kinds


def _is_digest_only(arguments: Any) -> bool:
    return isinstance(arguments, dict) and (
        "_digest_only" in arguments or "_arg_keys" in arguments)


def merge_analyses(analyses: List[TraceAnalysis]) -> Tuple[TraceAnalysis,
                                                            List[str]]:
    """同构轨迹合并（ADR-0191 D2：跨轨迹参数证据池互相印证）。

    合取纪律：成员分析必须**全部**已过 D1 门且工具序列完全同构——
    异构拓扑或任一成员被拒即整体拒绝（返回 ``(首个分析, 原因码)`` 供
    审计）。合并产物：参数观测池（observations）、最保守满意度、
    来源 session 清单。纯函数。
    """
    if not analyses:
        return (TraceAnalysis(accepted=False,
                              rejection_codes=["IND_EMPTY_STEPS"]),
                ["IND_EMPTY_STEPS"])
    base = analyses[0]
    codes: List[str] = []
    base_tools = [s.tool_name for s in base.steps]
    for other in analyses[1:]:
        if [s.tool_name for s in other.steps] != base_tools:
            codes.append("IND_INCOMPATIBLE_TOPOLOGY")
    if codes:
        return base, codes

    merged_steps: List[InducedStep] = []
    for i, step in enumerate(base.steps):
        pool: Dict[str, List[Any]] = {}
        for other in analyses:
            args = other.steps[i].arguments if i < len(other.steps) else {}
            for key, value in (args or {}).items():
                if isinstance(value, (str, int, float, bool)):
                    pool.setdefault(str(key), []).append(value)
        merged_steps.append(step.model_copy(update={"observations": pool}))

    satisfactions = [a.satisfaction for a in analyses
                     if a.satisfaction is not None]
    sessions: List[str] = []
    for a in analyses:
        if a.session_id and a.session_id not in sessions:
            sessions.append(a.session_id)
    return base.model_copy(update={
        "steps": merged_steps,
        "satisfaction": min(satisfactions) if satisfactions else None,
        "session_id": ";".join(sessions)[:255],
        "turn_id": "merged",
        "behavior_digest": "",
    }), []


def analyze_trace(
    trace: ReplayTrace | Mapping[str, Any],
    *,
    satisfaction_threshold: float = DEFAULT_SATISFACTION_THRESHOLD,
    capability_map: Optional[Mapping[str, str]] = None,
    registry=None,
    min_steps: int = MIN_INDUCTION_STEPS,
) -> TraceAnalysis:
    """D1 合取门 + 步骤提取（纯函数；拒绝时带 ``IND-*`` 原因码）。"""
    t = _as_replay_trace(trace)
    if t is None:
        return TraceAnalysis(accepted=False,
                             rejection_codes=["IND_EMPTY_STEPS"])

    codes: List[str] = []
    satisfaction = trace_satisfaction(t)
    face = _goal_satisfaction_face(t)

    # ── 满意度门（fail-closed：无面 / 非satisfied / 低于阈值都拒绝）──
    if face is None:
        codes.append("IND_NO_SATISFACTION_FACE")
    else:
        if str(face.get("verdict") or "") != "satisfied":
            codes.append("IND_VERDICT_NOT_SATISFIED")
        if satisfaction is None or satisfaction < satisfaction_threshold:
            codes.append("IND_SATISFACTION_BELOW_THRESHOLD")

    # ── 完整性门 ──────────────────────────────────────────────────────
    if t.truncated:
        codes.append("IND_TRACE_TRUNCATED")

    tool_calls = [c for c in (t.tool_calls or [])
                  if isinstance(c, dict) and c.get("tool_name")]
    if not tool_calls:
        codes.append("IND_EMPTY_STEPS")
    else:
        if any(c.get("is_error") or str(c.get("status") or "") == "error"
               for c in tool_calls):
            codes.append("IND_TOOL_ERROR")
        if any(_is_digest_only(c.get("arguments")) or c.get("args_truncated")
               for c in tool_calls):
            # digest-only 参数没有真实值：typed 参数泛化无从谈起（诚实降级
            # 的代价，见 ADR-0191 §4）。
            codes.append("IND_DIGEST_ONLY_ARGS")
        if len(tool_calls) < min_steps:
            codes.append("IND_TOO_FEW_STEPS")

    steps: List[InducedStep] = []
    for i, call in enumerate(tool_calls, start=1):
        tool_name = str(call["tool_name"])
        kind = step_kind_for(tool_name)
        arguments = call.get("arguments")
        steps.append(InducedStep(
            seq=i,
            call_id=str(call.get("tool_call_id") or ""),
            tool_name=tool_name,
            capability_id=project_capability_id(
                tool_name, capability_map=capability_map, registry=registry),
            kind=kind,
            arguments=arguments if isinstance(arguments, dict) else {},
            evidence_kinds=_evidence_kinds_for(kind, tool_name),
            result_status=str(call.get("status") or ""),
            duration_ms=float(call.get("duration_ms") or 0.0),
        ))
    if steps and (int((t.mutations or {}).get("count") or 0) > 0):
        steps[-1] = steps[-1].model_copy(
            update={"is_mutation": True,
                    "evidence_kinds": steps[-1].evidence_kinds
                    + ["map_mutation_receipt"]})

    goal = (t.normalized_goal or "").strip()
    return TraceAnalysis(
        accepted=not codes,
        rejection_codes=codes,
        satisfaction=satisfaction,
        goal_text=goal or (t.selected_workflow or "未声明目标"),
        user_input=(t.user_input or "")[:2000],
        steps=steps,
        artifacts_count=int((t.artifacts or {}).get("count") or 0),
        mutations_count=int((t.mutations or {}).get("count") or 0),
        session_id=t.session_id,
        turn_id=t.turn_id,
        selected_workflow=t.selected_workflow,
        behavior_digest=t.behavior_digest,
    )


__all__ = [
    "REJECTION_CODES",
    "DEFAULT_SATISFACTION_THRESHOLD",
    "MIN_INDUCTION_STEPS",
    "STEP_KIND_RULES",
    "InducedStep",
    "TraceAnalysis",
    "analyze_trace",
    "merge_analyses",
    "project_capability_id",
    "step_kind_for",
    "trace_satisfaction",
]
