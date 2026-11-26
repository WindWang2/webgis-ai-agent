"""有界决策辅助（goal-pi-native-decision-governance WP2）。

消费 Pi v1.6.1 ``DECISION_MODEL_CLASSIFY`` 能力：classifier 型模型经
vendor ``ctx.modelRegistry.classify()``（**扩展侧**调用，不经 codemode、
不向主模型开放任何新工具面）回答**宿主出题的闭集问题**。

安全边界（红线，测试锁定）：

- 出题投影只含：任务类型候选 / 制图模板候选 / 是否需要澄清 + 有限资格
  摘要与歧义原因；``state`` 有字节上限，不含用户原文长文或地理数据。
- **宿主权威**：答案必须落在候选闭集；低置信 / 未知选项 / 畸形 / 重复 /
  provider 错误一律 ``rejected``；``rejected`` 与 ``unavailable`` 都回落
  到既有确定性结果（调用方语义不变）。
- 决策永不扩大数据访问、不伪造地理实体、不批准权限/destructive；
  clarification 域只允许在策略已触发时影响默认项排序，不能凭空制造澄清。
- 默认关闭（``PI_DECISION_ASSIST`` 未设即 disabled）；关闭时零网络、零行为差。
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

# ── 常量（封闭词表） ────────────────────────────────────────────────

DECISION_ASSIST_ENV = "PI_DECISION_ASSIST"

DOMAIN_TASK_IDENTIFICATION = "task_identification"
DOMAIN_TEMPLATE_CANDIDATES = "template_candidates"
DOMAIN_CLARIFICATION_NEEDED = "clarification_needed"
DOMAINS = (DOMAIN_TASK_IDENTIFICATION, DOMAIN_TEMPLATE_CANDIDATES,
           DOMAIN_CLARIFICATION_NEEDED)

#: status 词表（typed；调用方按 status 分支，不解析 reason 细节也能安全回落）
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_UNAVAILABLE = "unavailable"

#: rejected reason 封闭词表
REASON_DISABLED = "disabled"
REASON_NOT_APPLICABLE = "not_applicable"
REASON_NO_PROVIDER = "no_provider"
REASON_OVERSIZED_STATE = "oversized_state"
REASON_MALFORMED = "malformed_result"
REASON_PROVIDER_ERROR = "provider_error"
REASON_ABORTED = "aborted"
REASON_MISSING_ANSWER = "missing_answer"
REASON_WRONG_TYPE = "wrong_answer_type"
REASON_UNKNOWN_CHOICE = "unknown_choice"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_EXTRA_ANSWERS = "extra_answers"
REASON_DUPLICATE = "duplicate_result"
REJECT_REASONS = frozenset({
    REASON_DISABLED, REASON_NOT_APPLICABLE, REASON_OVERSIZED_STATE,
    REASON_MALFORMED, REASON_PROVIDER_ERROR, REASON_ABORTED,
    REASON_MISSING_ANSWER, REASON_WRONG_TYPE, REASON_UNKNOWN_CHOICE,
    REASON_LOW_CONFIDENCE, REASON_EXTRA_ANSWERS, REASON_DUPLICATE,
})

#: 保守置信下限：choice 域要求 ≥0.60；bool 域 yes 判定要求 ≥0.70，
#: 0.5<p<0.7 的模糊带一律拒绝（宁可不消费也不采信含混信号）。
CHOICE_CONFIDENCE_FLOOR = 0.60
BOOL_YES_FLOOR = 0.70

#: 出题投影预算
MAX_CANDIDATES = 6
MAX_STATE_BYTES = 4096
MAX_STATE_FIELDS = 12
MAX_REASON_CHARS = 200
MAX_FIELD_CHARS = 300

_TASK_KEYS = ("task_type", "subject_type", "scope_name", "audience",
              "measure_hint", "ambiguity_reason")


def decision_assist_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """feature gate：``PI_DECISION_ASSIST=1`` 才启用（默认关闭）。"""
    env = os.environ if environ is None else environ
    return str(env.get(DECISION_ASSIST_ENV, "")).strip() == "1"


def _clip(text: Any, limit: int = MAX_FIELD_CHARS) -> str:
    return str(text)[:limit]


def _bounded_state(fields: Mapping[str, Any]) -> dict[str, str]:
    """构建有界 state：白名单键 + 截断 + 字段数/字节双重上限。"""
    state: dict[str, str] = {}
    for key in _TASK_KEYS:
        if len(state) >= MAX_STATE_FIELDS:
            break
        value = fields.get(key)
        if value in (None, ""):
            continue
        state[key] = _clip(value)
    # 超预算时按插入序丢弃尾部字段（候选与原因永不在 state，不受影响）。
    while len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > MAX_STATE_BYTES:
        if not state:
            break
        state.pop(next(reversed(state)))
    return state


# ── 请求（出题） ────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionRequest:
    """宿主出题（闭集）。``to_classifier_context()`` 产出 vendor
    ``ClassifierContext`` 兼容形状（state + questions）。"""

    domain: str
    question_key: str
    question: dict[str, Any]
    candidates: tuple[str, ...]
    request_id: str
    state: dict[str, str] = field(default_factory=dict)
    confidence_floor: float = CHOICE_CONFIDENCE_FLOOR

    def to_classifier_context(self) -> dict[str, Any]:
        return {"state": dict(self.state),
                "questions": {self.question_key: dict(self.question)}}


def _request_id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"dec-{digest[:16]}"


def _choice_question(instructions: str, candidates: Sequence[str],
                     descriptions: Mapping[str, str]) -> dict[str, Any]:
    return {
        "type": "choice",
        "instructions": _clip(instructions, MAX_REASON_CHARS),
        "criteria": {cid: _clip(descriptions.get(cid, ""), 120) or cid
                     for cid in candidates},
    }


def build_task_identification_request(
    query: str,
    *,
    task_candidates: Sequence[tuple[str, str, int, int]],
    matched_task: str,
    state_fields: Optional[Mapping[str, Any]] = None,
) -> Optional[DecisionRequest]:
    """任务识别出题。候选 <2（确定性结果唯一）→ None（无需辅助）。

    ``task_candidates`` 项为 ``(rule_id, task, specificity, span)``
    （``intent_semantic.TaskDecision.candidates`` 形状）；同名任务去重保序。
    """
    unique: list[str] = []
    descriptions: dict[str, str] = {}
    for _rule_id, task, _spec, _span in task_candidates:
        if task not in unique:
            unique.append(task)
            descriptions.setdefault(task, task.replace("_", " "))
    if len(unique) < 2:
        return None
    candidates = tuple(unique[:MAX_CANDIDATES])
    descriptions = {cid: descriptions[cid] for cid in candidates}
    request = DecisionRequest(
        domain=DOMAIN_TASK_IDENTIFICATION,
        question_key="task_choice",
        question=_choice_question(
            "选择与用户请求最匹配的 GIS 分析任务类型", candidates, descriptions),
        candidates=candidates,
        request_id=_request_id(DOMAIN_TASK_IDENTIFICATION, query[:200],
                               "\x1f".join(candidates)),
        state=_bounded_state({"task_type": matched_task,
                              **(dict(state_fields) if state_fields else {})}),
    )
    return request


def build_template_candidates_request(
    query: str,
    *,
    tied_template_ids: Sequence[str],
    reasons: Optional[Mapping[str, str]] = None,
    state_fields: Optional[Mapping[str, Any]] = None,
) -> Optional[DecisionRequest]:
    """制图模板候选出题。仅当确定性评分出现**并列第一**时非 None。"""
    unique: list[str] = []
    for tid in tied_template_ids:
        tid = str(tid)
        if tid and tid not in unique:
            unique.append(tid)
    if len(unique) < 2:
        return None
    candidates = tuple(unique[:MAX_CANDIDATES])
    request = DecisionRequest(
        domain=DOMAIN_TEMPLATE_CANDIDATES,
        question_key="template_choice",
        question=_choice_question(
            "在并列候选中选择更贴合请求意图的制图模板", candidates,
            {cid: _clip((reasons or {}).get(cid, cid), 120) for cid in candidates}),
        candidates=candidates,
        request_id=_request_id(DOMAIN_TEMPLATE_CANDIDATES, query[:200],
                               "\x1f".join(candidates)),
        state=_bounded_state(dict(state_fields) if state_fields else {}),
    )
    return request


def build_clarification_request(
    query: str,
    *,
    policy_reasons: Sequence[str],
    task_candidates: Sequence[str],
    state_fields: Optional[Mapping[str, Any]] = None,
) -> Optional[DecisionRequest]:
    """是否需要澄清出题（bool）。**仅在确定性策略已产生触发理由时**出题
    ——决策只能影响既有澄清的默认项排序，不能凭空制造澄清。"""
    if not policy_reasons:
        return None
    candidates = tuple(dict.fromkeys(str(c) for c in task_candidates if c))[:MAX_CANDIDATES]
    state = _bounded_state({
        **(dict(state_fields) if state_fields else {}),
        "ambiguity_reason": _clip(";".join(policy_reasons), MAX_REASON_CHARS),
    })
    request = DecisionRequest(
        domain=DOMAIN_CLARIFICATION_NEEDED,
        question_key="needs_clarification",
        question={
            "type": "bool",
            "instructions": _clip(
                "判断该请求是否需要向用户澄清（存在歧义/缺主体/缺范围）",
                MAX_REASON_CHARS),
            "criteria": {"true": "需要澄清", "false": "意图明确"},
        },
        candidates=candidates,
        request_id=_request_id(DOMAIN_CLARIFICATION_NEEDED, query[:200],
                               ";".join(policy_reasons)),
        state=state,
        confidence_floor=BOOL_YES_FLOOR,
    )
    return request


# ── 结果（验题） ────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionOutcome:
    """决策结论（typed）。rejected/unavailable 时调用方**必须**走确定性路径。"""

    status: str
    domain: str
    request_id: str
    choice: str = ""
    confidence: float = 0.0
    response_id: str = ""
    reason_code: str = ""
    usage_total_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_ACCEPTED


def _reject(request: DecisionRequest, reason: str) -> DecisionOutcome:
    return DecisionOutcome(status=STATUS_REJECTED, domain=request.domain,
                           request_id=request.request_id, reason_code=reason)


def _answer_confidence(answer: Mapping[str, Any]) -> Optional[float]:
    raw = answer.get("confidence", answer.get("probability"))
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value != value or value < 0.0 or value > 1.0:  # NaN / 越界
        return None
    return value


def validate_classifier_result(
    request: DecisionRequest,
    result: Any,
    *,
    seen_request_ids: Optional[set[str]] = None,
) -> DecisionOutcome:
    """严格验证 vendor ``ClassifierResult``（dict 形状）。

    任何异常形状/越权选项/低置信/重复都 typed 拒绝；永不抛出。
    """
    if seen_request_ids is not None:
        if request.request_id in seen_request_ids:
            return _reject(request, REASON_DUPLICATE)
        seen_request_ids.add(request.request_id)
    if not isinstance(result, Mapping):
        return _reject(request, REASON_MALFORMED)
    stop = result.get("stopReason")
    if stop == "aborted":
        return _reject(request, REASON_ABORTED)
    if stop != "stop":
        return _reject(request, REASON_PROVIDER_ERROR)
    answers = result.get("answers")
    if not isinstance(answers, Mapping):
        return _reject(request, REASON_MALFORMED)
    if set(answers.keys()) != {request.question_key}:
        if request.question_key not in answers:
            return _reject(request, REASON_MISSING_ANSWER)
        return _reject(request, REASON_EXTRA_ANSWERS)
    answer = answers[request.question_key]
    if not isinstance(answer, Mapping):
        return _reject(request, REASON_MALFORMED)
    answer_type = answer.get("type")
    expected_type = request.question.get("type")
    if answer_type != expected_type:
        return _reject(request, REASON_WRONG_TYPE)
    response_id = result.get("responseId")
    response_id = str(response_id)[:128] if isinstance(response_id, str) else ""
    usage = result.get("usage")
    usage_tokens = 0
    if isinstance(usage, Mapping):
        total = usage.get("totalTokens", usage.get("tokens", {}).get("total")
                          if isinstance(usage.get("tokens"), Mapping) else 0)
        if isinstance(total, int) and 0 <= total <= 10**9:
            usage_tokens = total

    if expected_type == "choice":
        choice = answer.get("choice")
        if not isinstance(choice, str):
            return _reject(request, REASON_MALFORMED)
        if choice not in request.candidates:
            return _reject(request, REASON_UNKNOWN_CHOICE)
        confidence = _answer_confidence(answer)
        if confidence is None:
            return _reject(request, REASON_MALFORMED)
        if confidence < max(request.confidence_floor, CHOICE_CONFIDENCE_FLOOR):
            return _reject(request, REASON_LOW_CONFIDENCE)
        return DecisionOutcome(
            status=STATUS_ACCEPTED, domain=request.domain,
            request_id=request.request_id, choice=choice,
            confidence=confidence, response_id=response_id,
            usage_total_tokens=usage_tokens)

    # bool 域（clarification）：yes 需要 ≥0.70；0.5<p<0.7 模糊带拒绝。
    probability = answer.get("probability")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)):
        return _reject(request, REASON_MALFORMED)
    p = float(probability)
    if p != p or p < 0.0 or p > 1.0:
        return _reject(request, REASON_MALFORMED)
    if p < 0.5:
        return DecisionOutcome(
            status=STATUS_ACCEPTED, domain=request.domain,
            request_id=request.request_id, choice="no",
            confidence=1.0 - p, response_id=response_id,
            usage_total_tokens=usage_tokens)
    if p < BOOL_YES_FLOOR:
        return _reject(request, REASON_LOW_CONFIDENCE)
    return DecisionOutcome(
        status=STATUS_ACCEPTED, domain=request.domain,
        request_id=request.request_id, choice="yes",
        confidence=p, response_id=response_id,
        usage_total_tokens=usage_tokens)


# ── 传输（可注入；生产默认不可用 → 确定性路径） ────────────────────


class DecisionTransport(Protocol):
    """传输协议：把出题交给 classifier 执行并返回 dict 结果。"""

    async def classify(self, model_ref: str, context: dict[str, Any]) -> dict[str, Any]:
        ...


class UnavailableDecisionTransport:
    """生产默认：未配置 classifier provider → 诚实不可用。"""

    async def classify(self, model_ref: str, context: dict[str, Any]) -> dict[str, Any]:
        raise DecisionTransportUnavailable(model_ref)


class DecisionTransportUnavailable(RuntimeError):
    def __init__(self, model_ref: str = "") -> None:
        super().__init__(f"decision transport unavailable: {model_ref!r}")
        self.model_ref = model_ref


def default_model_ref(environ: Optional[Mapping[str, str]] = None) -> str:
    """classifier 引用（``provider/model``），由宿主配置注入；空 = 未配置。"""
    env = os.environ if environ is None else environ
    return str(env.get("PI_DECISION_CLASSIFIER", "")).strip()


# ── 编排（闸门聚合） ───────────────────────────────────────────────


class DecisionAssist:
    """出题 → 传输 → 验证 的聚合器（dedupe 有界，幂等）。

    用法：``outcome = await assist.assist(request)``；``outcome.ok`` 为假时
    调用方直接沿用确定性结果。**不得**用 outcome 越过任何确定性闸。
    """

    def __init__(
        self,
        transport: Optional[DecisionTransport] = None,
        *,
        enabled: Optional[bool] = None,
        model_ref: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
        dedupe_capacity: int = 64,
    ) -> None:
        self._transport = transport
        self._enabled = (decision_assist_enabled(environ)
                         if enabled is None else bool(enabled))
        self._model_ref = (default_model_ref(environ)
                           if model_ref is None else model_ref)
        self._seen: OrderedDict[str, DecisionOutcome] = OrderedDict()
        self._dedupe_capacity = max(4, int(dedupe_capacity))

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def assist(self, request: Optional[DecisionRequest]) -> DecisionOutcome:
        if request is None or request.domain not in DOMAINS:
            return DecisionOutcome(status=STATUS_REJECTED,
                                   domain=getattr(request, "domain", ""),
                                   request_id=getattr(request, "request_id", ""),
                                   reason_code=REASON_NOT_APPLICABLE)
        if not self._enabled:
            return _reject(request, REASON_DISABLED)
        prior = self._seen.get(request.request_id)
        if prior is not None:
            return DecisionOutcome(
                status=STATUS_REJECTED, domain=request.domain,
                request_id=request.request_id, reason_code=REASON_DUPLICATE)
        if not self._model_ref or self._transport is None:
            outcome = _reject(request, REASON_NO_PROVIDER)
        else:
            try:
                raw = await self._transport.classify(
                    self._model_ref, request.to_classifier_context())
            except DecisionTransportUnavailable:
                outcome = DecisionOutcome(
                    status=STATUS_UNAVAILABLE, domain=request.domain,
                    request_id=request.request_id, reason_code=REASON_NO_PROVIDER)
            except Exception:  # noqa: BLE001 —— 传输层任何异常都诚实拒绝
                outcome = _reject(request, REASON_PROVIDER_ERROR)
            else:
                outcome = validate_classifier_result(
                    request, raw, seen_request_ids=None)
        self._seen[request.request_id] = outcome
        while len(self._seen) > self._dedupe_capacity:
            self._seen.popitem(last=False)
        return outcome


# ── 应用闸（调用方消费 outcome 的唯一方式；纯函数） ─────────────────


def apply_task_choice(
    outcome: DecisionOutcome,
    task_candidates: Sequence[tuple[str, str, int, int]],
    deterministic_task: str,
) -> str:
    """任务识别闸：仅当 accepted 且选择 ∈ 候选且其最优特异性**不高于**
    确定性胜者（即平级，规则未区分时）才采纳；否则返回确定性任务。
    """
    if not outcome.ok or outcome.domain != DOMAIN_TASK_IDENTIFICATION:
        return deterministic_task
    if outcome.choice not in {task for _r, task, _s, _sp in task_candidates}:
        return deterministic_task
    winner_spec: Optional[int] = None
    choice_spec: Optional[int] = None
    for _rule_id, task, specificity, _span in task_candidates:
        if task == deterministic_task:
            winner_spec = specificity if winner_spec is None else max(winner_spec, specificity)
        if task == outcome.choice:
            choice_spec = specificity if choice_spec is None else max(choice_spec, specificity)
    if winner_spec is None or choice_spec is None or choice_spec < winner_spec:
        return deterministic_task
    return outcome.choice


def apply_template_tie_break(
    outcome: DecisionOutcome,
    tied_top_ids: Sequence[str],
    deterministic_template_id: str,
) -> str:
    """模板闸：仅当 accepted 且选择 ∈ 并列第一集合才采纳；否则确定性模板。"""
    if not outcome.ok or outcome.domain != DOMAIN_TEMPLATE_CANDIDATES:
        return deterministic_template_id
    if outcome.choice not in set(tied_top_ids):
        return deterministic_template_id
    return outcome.choice


CLARIFICATION_PROMOTE_REASON = "decision_assist_uncertain"


def apply_clarification_signal(
    outcome: DecisionOutcome,
    policy_reasons: Sequence[str],
) -> list[str]:
    """澄清闸：**策略已触发**且 assist 明确 yes 时追加一条理由（供默认项
    排序参考）；no 或任何非 accepted 都不改变既有理由。不能凭空制造澄清。"""
    reasons = list(policy_reasons)
    if (outcome.ok and outcome.domain == DOMAIN_CLARIFICATION_NEEDED
            and outcome.choice == "yes" and reasons):
        if CLARIFICATION_PROMOTE_REASON not in reasons:
            reasons.append(CLARIFICATION_PROMOTE_REASON)
    return reasons
