"""Unified evaluation evidence model for the Harness ↔ GIS ↔ MapSpec ↔ Cartography loop.

This module defines the structured evidence that flows across the whole chain.
It replaces the legacy "didn't-error = success" booleans with a tiered,
evidence-backed model where MISSING EVIDENCE IS NEVER SUCCESS.

核心不变量：
- 缺失证据 → ``NOT_EVALUATED`` / ``UNKNOWN``，绝不被当作 success。
- MapSpecValidity 是分层证据（mutation accepted → semantic valid → compile valid
  → runtime valid），不压成一个假 boolean。
- CursorResolution 来自真实 SessionStore 解析（存在 + 归属 session + 类型匹配），
  不是 ref 字符串前缀检查。
- 每条证据携带完整 correlation（run/session/turn/tool_call/mapspec revision/
  checkpoint/compile/runtime），并发 session 不互相污染。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Dict,
    List,
    Optional,
    TypedDict,
    cast,
)


_MAX_CARTOGRAPHIC_CHECKS = 64
_MAX_CARTOGRAPHIC_FINDINGS = 32
# Two desired-state attempts plus two runtime convergence attempts.

# #1069(A-7): 从工具结果向 harness 证据投影的键清单（单一事实源）。
# 此前 agent_pi_bridge 与 cartography_runtime 各持一份逐字拷贝 —— 新增
# 证据键（如 is_compiled 的下游依赖）只改一处会让另一条运行时的
# validity ladder 缺料。两条 agent 路径（Pi 回调 / legacy seam）共用。
CARTOGRAPHIC_RESULT_EVIDENCE_KEYS: tuple[str, ...] = (
    "success",
    "is_compiled",
    "warnings",
    "checkpoint_id",
    "message",
    "correction_hint",
    "cartography_findings",
    "cartographic_review",
    "mapspec_fingerprint",
    "runtime_observation_seq",
    "runtime_projection_fingerprint",
    "mutation_revision",
    "map_product_evidence",
)
_MAX_REPAIR_ATTEMPTS = 4
_MAX_VISUAL_EVIDENCE = 4
_MAX_PRODUCT_FALLBACKS = 16
_MAX_PRODUCT_COMPONENTS = 32


class CartographicResultEvidence(TypedDict, total=False):
    """#1550：工具结果 → harness 证据投影的**具名载体**（替代裸 dict）。

    键集 = ``CARTOGRAPHIC_RESULT_EVIDENCE_KEYS`` + 两条 agent 路径各自的
    correlation 腿（``status`` / ``llm_payload_len``）。``total=False``：
    证据键按在场性投影 —— 缺失键即缺失证据，绝不伪造默认值（与模块核心
    不变量「缺失证据 → NOT_EVALUATED」同源）。
    """

    status: str
    llm_payload_len: int
    success: bool
    is_compiled: bool
    warnings: List[str]
    checkpoint_id: str
    message: str
    correction_hint: str
    cartography_findings: List[Dict[str, Any]]
    cartographic_review: Dict[str, Any]
    mapspec_fingerprint: str
    runtime_observation_seq: int
    runtime_projection_fingerprint: str
    mutation_revision: int
    map_product_evidence: Dict[str, Any]
    # agent_pi_bridge 取消路径的标记（无世代证据可投影时唯一的在场键）。
    cancelled: bool


def project_cartographic_result_evidence(
    raw: Dict[str, Any],
    *,
    status: str,
    llm_payload_len: int,
) -> CartographicResultEvidence:
    """#1550：工具结果 → harness 证据的**单一投影入口**（两条 agent 路径共用）。

    此前 ``cartography_runtime.record_cartographic_dispatch_evidence`` 与
    ``pi_post_dispatch._record_cartographic_evidence`` 各持一份逐字相同的
    逐键拷贝；键清单已由 CARTOGRAPHIC_RESULT_EVIDENCE_KEYS 单一化，投影
    循环在此收敛为一处。动态键循环经 ``cast`` 落地 —— 键清单即契约
    （TypedDict 键必须是字面量，无法按变量下标赋值）。
    """
    projected: Dict[str, Any] = {
        "status": status,
        "llm_payload_len": llm_payload_len,
    }
    for key in CARTOGRAPHIC_RESULT_EVIDENCE_KEYS:
        if key in raw:
            projected[key] = raw[key]
    return cast("CartographicResultEvidence", projected)


def _bounded_cartographic_review(review: Dict[str, Any]) -> Dict[str, Any]:
    """Return a storage-safe review summary without changing its verdict.

    Rule counts and terminal status remain authoritative even when an extreme
    MapSpec emits more diagnostic rows than are useful in session state.  The
    omitted count is explicit so bounded retention cannot masquerade as a
    complete evidence dump.
    """
    bounded = dict(review)
    for key, limit in (
        ("checks", _MAX_CARTOGRAPHIC_CHECKS),
        ("findings", _MAX_CARTOGRAPHIC_FINDINGS),
    ):
        values = review.get(key)
        if not isinstance(values, list) or len(values) <= limit:
            continue
        bounded[key] = values[:limit]
        bounded[f"{key}_omitted"] = len(values) - limit
    return bounded


class RefResolutionStatus(str, Enum):
    """Ref cursor 解析的真实状态（非布尔）。"""
    MALFORMED = "malformed"                 # 不符合 ref:<type>:<id> 语法
    SYNTACTICALLY_VALID = "syntactically_valid"  # 语法合法但未解析
    NOT_FOUND = "not_found"                 # 解析过，session 内不存在
    WRONG_SESSION = "wrong_session"         # 存在但归属其它 session
    TYPE_MISMATCH = "type_mismatch"         # 存在但 payload 类型与 ref 前缀不符
    RESOLVED = "resolved"                   # 存在 + 归属正确 session + 类型匹配

    @property
    def is_resolved(self) -> bool:
        return self is RefResolutionStatus.RESOLVED


class MapSpecValidityTier(int, Enum):
    """MapSpec 有效性的分层证据（值越大证据越强）。

    ADR-0060: 天花板止于 SEMANTIC_VALID（3）。COMPILE_VALID 与 RUNTIME_VALID
    已被移除，阶梯不假装拥有这些证明。
    缺失证据为 NOT_EVALUATED（0），绝不为 success。
    """
    NOT_EVALUATED = 0       # 未采集到任何证据
    MUTATION_REJECTED = 1   # mutation 被校验拒绝（transaction 回滚）
    MUTATION_ACCEPTED = 2   # 工具未报错（最弱证据——"没崩"≠"有效"）
    SEMANTIC_VALID = 3      # 通过纯 Python 结构校验 validate()


class MapActionStatus(str, Enum):
    """地图运行时交互动作的生命周期状态（Harness–Map Interaction V3）。

    终态为 SUCCEEDED / FAILED / CANCELLED / SUPERSEDED；ISSUED/QUEUED/RUNNING
    为瞬态。没有终态 ACK 的动作永远停留在非终态 —— 缺失证据绝不被当作 success。
    """
    ISSUED = "issued"            # 后端已铸 action_id 并发出（尚未收到终态 ACK）
    QUEUED = "queued"            # 前端已入队
    RUNNING = "running"          # 前端开始执行
    SUCCEEDED = "succeeded"      # 执行完成（相机类携带实际落定视口）
    FAILED = "failed"            # 未知命令/参数非法/MapLibre 抛错/目标缺失/超时
    CANCELLED = "cancelled"      # 被用户手势或 session 切换取消
    SUPERSEDED = "superseded"    # 被更新的同类命令取代（camera coalesce）

    @property
    def is_terminal(self) -> bool:
        return self in (
            MapActionStatus.SUCCEEDED,
            MapActionStatus.FAILED,
            MapActionStatus.CANCELLED,
            MapActionStatus.SUPERSEDED,
        )


@dataclass
class MapActionEvidence:
    """一次 AI 地图命令从前端真实执行回传的结构化证据（ACK）。

    每条记录独立携带 run/session/turn/tool_call/step/SSE-event 全链路
    correlation；requested 为请求目标（如 fly_to 的 center/zoom），actual 为
    执行后的真实状态（如落定视口），供收敛性判定。
    """
    action_id: str
    command: str
    session_id: str
    status: MapActionStatus = MapActionStatus.ISSUED
    run_id: str = ""
    turn_id: str = ""
    tool_call_id: str = ""
    sse_event_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_ms: Optional[float] = None
    error: str = ""
    requested: Dict[str, Any] = field(default_factory=dict)
    actual: Dict[str, Any] = field(default_factory=dict)
    mapspec_fingerprint: Optional[str] = None

    @property
    def has_terminal_evidence(self) -> bool:
        return self.status.is_terminal


@dataclass
class RefResolution:
    """单次 ref cursor 解析的真实结果。"""
    ref: str
    session_id: Optional[str]
    status: RefResolutionStatus = RefResolutionStatus.SYNTACTICALLY_VALID
    expected_type: Optional[str] = None   # ref 前缀声明的类型 (geojson/raster/table)
    actual_type: Optional[str] = None     # payload 实际类型
    detail: str = ""

    @property
    def is_resolved(self) -> bool:
        return self.status.is_resolved


@dataclass
class MapSpecValidityEvidence:
    """一次 MapSpec mutation 的分层有效性证据。"""
    tier: MapSpecValidityTier = MapSpecValidityTier.NOT_EVALUATED
    # 各层原始证据（可观察，不静默）
    mutation_accepted: Optional[bool] = None
    semantic_errors: List[Dict[str, Any]] = field(default_factory=list)
    compile_success: Optional[bool] = None
    compile_errors: List[Dict[str, Any]] = field(default_factory=list)
    runtime_valid: Optional[bool] = None
    runtime_fatal_error: Optional[str] = None
    mapspec_revision: Optional[str] = None
    checkpoint_id: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        """有效 = 至少达到 SEMANTIC_VALID（真实证据），MUTATION_ACCEPTED 不算有效。"""
        return self.tier >= MapSpecValidityTier.SEMANTIC_VALID

    @property
    def evaluated(self) -> bool:
        return self.tier is not MapSpecValidityTier.NOT_EVALUATED


@dataclass
class CartographicReviewEvidence:
    """Trusted desired-vs-runtime cartographic evidence for one final MapSpec.

    Tool-returned review payloads are transport evidence only. ``trusted`` is
    true only when the harness re-read session-owned state and recomputed the
    deterministic desired review itself.
    """
    session_id: str
    status: str = "not_evaluated"
    desired_status: str = "not_evaluated"
    runtime_status: str = "not_evaluated"
    mapspec_fingerprint: Optional[str] = None
    reported_fingerprint: Optional[str] = None
    source_tool_call_id: Optional[str] = None
    trusted: bool = False
    desired_review: Dict[str, Any] = field(default_factory=dict)
    # #1550：check 条目是 CartographicCheck 投影（_cartography_check 产出）。
    checks: List["CartographicCheck"] = field(default_factory=list)
    repair_attempts: List[Dict[str, Any]] = field(default_factory=list)
    visual_evidence: List[Dict[str, Any]] = field(default_factory=list)
    # Unified cartography feedback axes (visual / template_codegen / gis_semantics).
    # Populated by cartography_feedback.attach_unified_feedback — never invent pass.
    feedback: Dict[str, Any] = field(default_factory=dict)
    termination_reason: str = "missing_evidence"
    counters: Dict[str, int] = field(default_factory=dict)

    @property
    def evaluated(self) -> bool:
        return self.status not in ("not_evaluated", "superseded")

    @property
    def passed(self) -> bool:
        return self.status in ("passed", "passed_with_warnings")

    def to_dict(self) -> Dict[str, Any]:
        desired_review = _bounded_cartographic_review(self.desired_review)
        checks = self.checks[:_MAX_CARTOGRAPHIC_CHECKS]
        repair_attempts = self.repair_attempts[:_MAX_REPAIR_ATTEMPTS]
        visual_evidence = self.visual_evidence[:_MAX_VISUAL_EVIDENCE]
        return {
            "stage": "actual_runtime",
            "status": self.status,
            "desired_status": self.desired_status,
            "runtime_status": self.runtime_status,
            "mapspec_fingerprint": self.mapspec_fingerprint,
            "reported_fingerprint": self.reported_fingerprint,
            "source_tool_call_id": self.source_tool_call_id,
            "trusted": self.trusted,
            "evaluated": self.evaluated,
            "passed": self.passed,
            "desired_review": desired_review,
            "checks": checks,
            "checks_omitted": max(0, len(self.checks) - len(checks)),
            "repair_attempts": repair_attempts,
            "repair_attempts_omitted": max(
                0, len(self.repair_attempts) - len(repair_attempts)
            ),
            "visual_evidence": visual_evidence,
            "visual_evidence_omitted": max(
                0, len(self.visual_evidence) - len(visual_evidence)
            ),
            "feedback": self.feedback if isinstance(self.feedback, dict) else {},
            "termination_reason": self.termination_reason,
            "counters": self.counters,
        }


@dataclass
class MapProductEvidence:
    """GIS Harness 产品证据（IntentResolution / RecipeSelection / RecipeEligibility /
    FallbackDecision / ComponentSelection / MapProductCompleteness）。

    由 ``webgis_map_product`` 等产品组装工具在结果中携带
    ``map_product_evidence`` —— harness 只转录，不推断；缺证据即 None
    （绝不为 PASS）。条目有界：fallback/组件列表截断保留 + omitted 计数。
    """
    tool_call_id: str = ""
    intent_resolution: Dict[str, Any] = field(default_factory=dict)
    recipe_selection: Dict[str, Any] = field(default_factory=dict)
    recipe_eligibility: Dict[str, Any] = field(default_factory=dict)
    fallback_decisions: List[Dict[str, Any]] = field(default_factory=list)
    component_selection: List[str] = field(default_factory=list)
    completeness: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(cls, result: Optional[Dict[str, Any]]) -> Optional["MapProductEvidence"]:
        """从工具结果提取产品证据；无 ``map_product_evidence`` 键 → None。"""
        if not isinstance(result, dict):
            return None
        raw = result.get("map_product_evidence")
        if not isinstance(raw, dict):
            return None
        fallbacks = [
            fb for fb in raw.get("fallback_decisions", [])
            if isinstance(fb, dict)
        ][:_MAX_PRODUCT_FALLBACKS]
        components = [
            str(c) for c in raw.get("component_selection", [])
        ][:_MAX_PRODUCT_COMPONENTS]
        return cls(
            intent_resolution=raw.get("intent_resolution") or {},
            recipe_selection=raw.get("recipe_selection") or {},
            recipe_eligibility=raw.get("recipe_eligibility") or {},
            fallback_decisions=fallbacks,
            component_selection=components,
            completeness=raw.get("map_product_completeness") or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_resolution": self.intent_resolution,
            "recipe_selection": self.recipe_selection,
            "recipe_eligibility": self.recipe_eligibility,
            "fallback_decisions": self.fallback_decisions,
            "component_selection": self.component_selection,
            "completeness": self.completeness,
        }


@dataclass
class ToolCallEvidence:
    """单次工具调用的完整 correlation + 证据记录。

    每条记录独立携带 run/session/turn/tool_call 标识，避免并发 session 互相污染。
    """
    run_id: str
    session_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    duration_ms: int = 0
    is_error: bool = False
    error_msg: str = ""
    # ref 解析证据（真实 SessionStore 解析）
    ref_resolutions: List[RefResolution] = field(default_factory=list)
    # MapSpec 有效性分层证据（仅 mutation 工具）
    mapspec_validity: Optional[MapSpecValidityEvidence] = None
    # 地图运行时交互证据（V3：前端真实执行 ACK，可选）
    map_actions: List["MapActionEvidence"] = field(default_factory=list)
    # 运行时制图证据路径（screenshot/trace/report，可选）
    runtime_evidence_path: Optional[str] = None
    # GIS Harness 产品证据（Intent/Recipe/Fallback/Component，可选）
    map_product: Optional[MapProductEvidence] = None


@dataclass
class EvaluationRun:
    """一次评估 run 的 session-scoped 证据集合。"""
    run_id: str
    session_id: str
    expected_tools: List[str] = field(default_factory=list)
    ideal_step_count: int = 0
    evidence: List[ToolCallEvidence] = field(default_factory=list)

    def add(self, ev: ToolCallEvidence) -> None:
        self.evidence.append(ev)


# ─── #1550：评估证据的序列化投影（具名载体，替代裸 dict[str, Any]）────────
#
# dataclass 出栈即拍平回裸 dict 的三条序列化接缝（pi_agent_harness 的
# _cartography_check / _map_action_evidence_to_dict / _evidence_to_dict）
# 从此有具名键集契约。三个序列化函数的键全部必中（可选语义以显式 None
# 在场，不缺键）—— 故这里用 total 契约；投影的开放载荷腿（evidence /
# map_product 等内部结构）仍是 dict，其自身契约在各自产出处。


class CartographicCheck(TypedDict):
    """``_cartography_check`` 的产出形状（desired/runtime 各 check 条目）。

    全部键必中（不可修复/无建议的 check 以 ``suggested_fix=None`` 在场，
    不缺席 —— 缺键与显式 None 在消费端同义，但形状保持稳定）。
    """

    rule: str
    status: str
    severity: str
    message: str
    evidence_class: str
    evidence: Dict[str, Any]
    repairability: str
    suggested_fix: Optional[Dict[str, Any]]


class MapActionEvidenceDict(TypedDict):
    """``_map_action_evidence_to_dict`` 的产出形状（单条 ACK 的观测投影）。"""

    action_id: str
    command: str
    session_id: str
    status: str
    run_id: str
    turn_id: str
    tool_call_id: str
    sse_event_id: str
    started_at: str
    finished_at: str
    duration_ms: Optional[float]
    error: str
    requested: Dict[str, Any]
    actual: Dict[str, Any]
    mapspec_fingerprint: Optional[str]
    verifiable: bool
    # 不可验证 ACK 的收敛判定为 None（绝不用假收敛充数）。
    converged: Optional[bool]


class RefResolutionDict(TypedDict):
    """``_evidence_to_dict`` 内 refs 条目的投影形状。"""

    ref: str
    status: str
    resolved: bool


class MapSpecValidityDict(TypedDict):
    """``_evidence_to_dict`` 内 mapspec_validity 腿的投影形状。"""

    tier: str
    is_valid: bool
    evaluated: bool
    semantic_errors: List[Any]
    checkpoint_id: Optional[str]


class ToolCallEvidenceDict(TypedDict):
    """``_evidence_to_dict`` 的产出形状（单次工具调用的完整证据投影）。

    可选腿（非 mutation 工具 / 无产品证据 / 无运行时路径）以 ``None``
    在场而非缺席 —— 消费端按 falsy 判定，不区分两种缺席。
    """

    run_id: str
    session_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    duration_ms: int
    is_error: bool
    error_msg: str
    refs: List[RefResolutionDict]
    mapspec_validity: Optional[MapSpecValidityDict]
    runtime_evidence_path: Optional[str]
    map_product: Optional[Dict[str, Any]]
    map_actions: List[MapActionEvidenceDict]
