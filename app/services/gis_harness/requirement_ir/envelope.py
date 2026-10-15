"""ClarificationEnvelope：blocking 歧义 → 用户 → 应答 的 typed 传输闭环（方向 3）。

设计约束（task_plan.md / ADR-0222）：

- **派生而非第二真相**：envelope 全部字段从 :class:`RequirementDocument`
  的 open blocking 歧义（``clarify.pending_questions``）确定性派生；
  ``request_id`` = document_id + revision + context_keys 指纹。歧义账本
  （contracts.Ambiguity）仍是唯一"什么未决"真相，本模块只是传输视图；
- **有界**：≤2 questions、每问 ≤4 options、所有字符串显式上限、
  extra="forbid"；
- **答案必须经 typed parser**：option 封闭值或 patch 协议目标 path 的既有
  validator（``patch._resolve_setter``）；自由文本先经 code 级归一
  （formats/statistics 词表），不做 NLP 猜测；
- **幂等/CAS**：answer 绑定 request_id + expected_revision；重复同答案 →
  同结果（duplicate）；revision 已前进 → typed ``clarification_stale_revision``；
  已答不同值 → typed ``clarification_already_answered``；
- **agent 不得代答**：blocking 歧义的 actor≠user 在本层直接拒绝
  （``clarification_actor_denied``；patch 层另有同名硬门）；
- **suppression 记账非真相**：``ClarificationLedger``（session map_state
  独立键）只记"哪些 context_key 已在 SSE 上问过"与有界 telemetry，
  丢失/损坏仅导致重复展示或少展示，不影响需求真相（fail-open）。

本模块不 import harness_kernel/chat 层（桥接在调用方：bridge 发射、
chat 路由应答后写章节状态）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_harness.requirement_ir import normalize as norm
from app.services.gis_harness.requirement_ir.clarify import (
    MAX_QUESTIONS_PER_TURN,
    pending_questions,
)
from app.services.gis_harness.requirement_ir.contracts import (
    MAX_OPTIONS,
    MAX_TEXT,
    Ambiguity,
    RequirementDocument,
)
from app.services.gis_harness.requirement_ir.patch import (
    PatchError,
    _resolve_setter,
)
from app.services.gis_harness.requirement_ir.service import (
    TurnPatch,
    _doc_from,
    load_state,
    summarize,
)
from app.services.gis_harness.workflow_instance import canonical_fingerprint

logger = logging.getLogger(__name__)

CLARIFICATION_ENVELOPE_SCHEMA = "clarification_envelope.v1"
CLARIFICATION_LEDGER_KEY = "requirement_clarification_ledger"
CLARIFICATION_LEDGER_SCHEMA = "clarification_ledger.v1"
MAX_LEDGER_EMITTED = 32          # 已问 context_key 记账上限（超出丢最旧）
MAX_TELEMETRY_EVENTS = 32        # telemetry 有界 journal
MAX_FREE_TEXT = 128
MAX_ANSWERS = 2                  # 与 questions 上限一致

STATISTIC_LABELS: Dict[str, str] = {
    "数量": "count", "个数": "count", "count": "count",
    "密度": "density", "density": "density",
    "占比": "share", "比例": "share", "构成": "share", "share": "share",
    "比率": "ratio", "ratio": "ratio",
    "总量": "sum", "总和": "sum", "sum": "sum",
    "均值": "mean", "平均": "mean", "mean": "mean",
    "中位数": "median", "median": "median",
}
_STATISTIC_VOCAB = ("count", "sum", "mean", "median", "min", "max",
                    "ratio", "rate", "density", "share", "index", "none")


# ── typed errors（fail-closed；code 稳定，供 API 层映射状态码） ─────────


class ClarificationError(ValueError):
    """澄清应答拒绝基类（``code`` 为稳定 reason code）。"""

    def __init__(self, code: str, message: str, *, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.detail = detail or {}


# ── envelope 契约 ─────────────────────────────────────────────────────


class EnvelopeOption(BaseModel):
    """候选项（值封闭：选中值必须逐字等于 ``value``）。"""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=128)
    label_zh: str = Field(max_length=MAX_TEXT)
    label_en: str = Field("", max_length=MAX_TEXT)
    is_default: bool = False


class EnvelopeQuestion(BaseModel):
    """一个 blocking 问题（从 :class:`Ambiguity` 派生 + 答题策略面）。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=64)
    context_key: str = Field(max_length=64)
    path: str = Field("", max_length=128)
    question_zh: str = Field(max_length=MAX_TEXT)
    question_en: str = Field("", max_length=MAX_TEXT)
    options: List[EnvelopeOption] = Field(default_factory=list, max_length=MAX_OPTIONS)
    allow_free_text: bool = False
    can_waive: bool = False          # 仅存在安全默认值时允许（default_value 非空）
    default_value: str = Field("", max_length=128)
    default_rationale: str = Field("", max_length=MAX_TEXT)
    reason_summary: str = Field("", max_length=MAX_TEXT)   # 为何 blocking（证据摘要）


class ClarificationEnvelope(BaseModel):
    """一次向用户的澄清请求（wire 契约；versioned、bounded）。

    ``requirement_revision`` 是答案 CAS 锚：应答必须携带与其一致的
    expected_revision；文档前进（新 patch/超替）后本 envelope 即过时
    （superseded 语义由 revision 比较承载，无需 TTL）。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = CLARIFICATION_ENVELOPE_SCHEMA
    request_id: str = Field(max_length=64)
    session_id: str = Field("", max_length=64)
    document_id: str = Field(max_length=64)
    requirement_revision: int = Field(1, ge=1)
    raised_turn_id: str = Field("", max_length=64)
    questions: List[EnvelopeQuestion] = Field(..., min_length=1,
                                              max_length=MAX_QUESTIONS_PER_TURN)
    defaultable: bool = False       # 全部问题可 waive 时才出现"使用默认"

    def context_keys(self) -> List[str]:
        return [q.context_key for q in self.questions]


# ── suppression / telemetry 记账（派生面；fail-open） ──────────────────


class TelemetryEvent(BaseModel):
    """有界遥测（不存答案文本/自由文本——只存 code 与计量）。"""

    model_config = ConfigDict(extra="forbid")

    event: Literal["asked", "answered", "waived", "duplicate", "stale",
                   "suppressed", "default_used", "actor_denied", "invalid"]
    code: str = Field("", max_length=64)     # ambiguity code（非答案内容）
    request_id: str = Field("", max_length=64)
    at: float = 0.0
    latency_ms: int = Field(0, ge=0)


class ClarificationLedger(BaseModel):
    """已问记账 + telemetry journal（丢失不影响需求真相）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = CLARIFICATION_LEDGER_SCHEMA
    emitted: Dict[str, str] = Field(default_factory=dict, max_length=MAX_LEDGER_EMITTED)
    telemetry: List[TelemetryEvent] = Field(default_factory=list, max_length=MAX_TELEMETRY_EVENTS)


async def load_ledger(session_store: Any, session_id: str) -> ClarificationLedger:
    try:
        state = await session_store.get_map_state(session_id)
        payload = (state or {}).get(CLARIFICATION_LEDGER_KEY)
        if isinstance(payload, dict) and payload.get("schema_version") == CLARIFICATION_LEDGER_SCHEMA:
            return ClarificationLedger.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — 记账面故障不阻塞
        logger.debug("[clarification] ledger load skipped: %s", exc)
    return ClarificationLedger()


async def save_ledger(session_store: Any, session_id: str, ledger: ClarificationLedger) -> None:
    try:
        await session_store.set_map_state(
            session_id, CLARIFICATION_LEDGER_KEY, ledger.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[clarification] ledger save skipped: %s", exc)


def _record_telemetry(
    ledger: ClarificationLedger, event: str, *, code: str = "",
    request_id: str = "", latency_ms: int = 0,
) -> None:
    ledger.telemetry.append(TelemetryEvent(
        event=event, code=code[:64], request_id=request_id[:64],
        at=time.time(), latency_ms=latency_ms))
    if len(ledger.telemetry) > MAX_TELEMETRY_EVENTS:
        ledger.telemetry = ledger.telemetry[-MAX_TELEMETRY_EVENTS:]


def _prune_emitted(ledger: ClarificationLedger, keep: set) -> None:
    """已答/已豁免的 context_key 移出记账（有界 + 不再抑制后续合法新问）。"""
    stale = [k for k in ledger.emitted if k not in keep]
    for key in stale[: max(0, len(ledger.emitted) - MAX_LEDGER_EMITTED)]:
        ledger.emitted.pop(key, None)


# ── envelope 构造（纯函数，从文档派生） ────────────────────────────────


def request_id_for(doc: RequirementDocument, context_keys: List[str]) -> str:
    """确定性 request_id：document + revision + 问题集合（同输入必同输出）。"""
    digest = canonical_fingerprint({
        "doc": doc.document_id, "rev": doc.revision,
        "keys": sorted(context_keys)})[:12]
    return f"clar-{doc.document_id}-{digest}"[:64]


def _question_from(ambiguity: Ambiguity, *, reason_summary: str) -> EnvelopeQuestion:
    return EnvelopeQuestion(
        code=ambiguity.code,
        context_key=ambiguity.context_key,
        path=ambiguity.path,
        question_zh=ambiguity.question_zh or ambiguity.code,
        question_en=ambiguity.question_en or ambiguity.code,
        options=[
            EnvelopeOption(value=o.value, label_zh=o.label_zh,
                           label_en=o.label_en, is_default=o.is_default)
            for o in ambiguity.options[:MAX_OPTIONS]
        ],
        allow_free_text=_code_allows_free_text(ambiguity.code),
        can_waive=bool(ambiguity.blocking and ambiguity.default_value),
        default_value=ambiguity.default_value,
        default_rationale=ambiguity.default_rationale,
        reason_summary=reason_summary[:MAX_TEXT],
    )


def _code_allows_free_text(code: str) -> bool:
    from app.services.gis_harness.requirement_ir.clarify import _CODE_POLICY
    return bool(_CODE_POLICY.get(code, {}).get("allow_free_text", False))


def build_envelope(
    doc: RequirementDocument,
    questions: List[Ambiguity],
    *,
    session_id: str = "",
    raised_turn_id: str = "",
) -> ClarificationEnvelope:
    """从 open blocking 歧义构造 envelope（questions 必须非空且 ≤2）。"""
    if not questions:
        raise ClarificationError("clarification_absent", "无可问问题")
    questions = questions[:MAX_QUESTIONS_PER_TURN]
    views = [
        _question_from(a, reason_summary=f"{a.code} @ {a.path or a.code}")
        for a in questions
    ]
    return ClarificationEnvelope(
        request_id=request_id_for(doc, [a.context_key for a in questions]),
        session_id=session_id[:64],
        document_id=doc.document_id,
        requirement_revision=doc.revision,
        raised_turn_id=raised_turn_id[:64],
        questions=views,
        defaultable=all(q.can_waive for q in views),
    )


async def pending_envelope(
    session_store: Any, session_id: str,
) -> Optional[ClarificationEnvelope]:
    """当前文档的 pending envelope（GET 恢复面；无 open blocking → None）。"""
    state = await load_state(session_store, session_id)
    if state is None:
        return None
    doc = _doc_from(state.document)
    questions = pending_questions(doc)
    if not questions:
        return None
    return build_envelope(doc, questions, session_id=session_id)


async def collect_turn_clarification(
    session_store: Any,
    session_id: str,
    turn_id: str,
) -> Tuple[Optional[ClarificationEnvelope], str]:
    """turn 收口发射判定：只发"本 turn 新出现"的未决问题。

    Returns:
        (envelope, outcome)；outcome ∈ {"emitted", "suppressed", "absent"}。
        suppressed = 全部 open 问题均已问过（context 未变不重复问）。
    """
    state = await load_state(session_store, session_id)
    if state is None:
        return None, "absent"
    doc = _doc_from(state.document)
    open_questions = pending_questions(doc)
    if not open_questions:
        return None, "absent"
    ledger = await load_ledger(session_store, session_id)
    fresh = [q for q in open_questions if q.context_key not in ledger.emitted]
    # 已答/已豁免的 key 清出记账（防有界淘汰误伤 + 语义自洁）
    _prune_emitted(ledger, {a.context_key for a in doc.intent.ambiguities
                            if a.state == "open"})
    if not fresh:
        _record_telemetry(ledger, "suppressed",
                          code=",".join(q.code for q in open_questions[:2]))
        await save_ledger(session_store, session_id, ledger)
        return None, "suppressed"
    envelope = build_envelope(doc, fresh, session_id=session_id,
                              raised_turn_id=turn_id)
    for key in envelope.context_keys():
        ledger.emitted[key] = envelope.request_id
    if len(ledger.emitted) > MAX_LEDGER_EMITTED:
        for key in list(ledger.emitted)[: len(ledger.emitted) - MAX_LEDGER_EMITTED]:
            ledger.emitted.pop(key, None)
    _record_telemetry(ledger, "asked",
                      code=",".join(q.code for q in fresh),
                      request_id=envelope.request_id)
    await save_ledger(session_store, session_id, ledger)
    return envelope, "emitted"


# ── 答案归一（typed parser；不做 NLP 猜测） ───────────────────────────


def resolve_answer_value(question: EnvelopeQuestion, raw: str) -> str:
    """自由文本/选项标签 → 封闭值或 patch validator 可接受的规范值。

    顺序：option ``value`` 精确匹配 → label_zh/label_en 匹配 →
    code 级词表归一（formats/statistics）→ 拒绝。
    """
    text = str(raw or "").strip()
    if not text:
        raise ClarificationError("clarification_answer_invalid", "空答案")
    if len(text) > MAX_FREE_TEXT:
        raise ClarificationError("clarification_answer_invalid", "答案超长")
    folded = text.casefold()
    for option in question.options:
        if folded == option.value.casefold() or folded == option.label_zh.casefold() \
                or (option.label_en and folded == option.label_en.casefold()):
            return option.value
    if question.code == "export_format_missing_when_publish":
        formats = norm.normalize_formats([text])
        if formats:
            return formats[0]
    if question.code == "measure_missing_for_statistics":
        mapped = STATISTIC_LABELS.get(folded) or STATISTIC_LABELS.get(text)
        if mapped in _STATISTIC_VOCAB:
            return mapped
    if not question.allow_free_text:
        raise ClarificationError(
            "clarification_answer_invalid", "答案不在候选项内",
            detail={"context_key": question.context_key})
    return text[:MAX_FREE_TEXT]


def _validate_against_patch(path: str, value: str) -> Optional[str]:
    """用 patch 协议目标 path 的既有 validator 预检（fail-closed）。"""
    handler = _resolve_setter(path) if path else None
    if handler is None:
        return None   # 无 setter 的 path（如 measures）由 patch 层特殊语义处理
    _, validator = handler
    try:
        validator(path, value)
    except PatchError as exc:
        raise ClarificationError(
            "clarification_answer_invalid", str(exc),
            detail={"path": path, "context_key_hint": value[:32]}) from exc
    return value


# ── 应答提交（幂等 / CAS / actor 门） ─────────────────────────────────


async def submit_answers(
    session_store: Any,
    session_id: str,
    request_id: str,
    answers: List[Dict[str, str]],
    *,
    waives: Optional[List[str]] = None,
    expected_revision: Optional[int] = None,
    actor: str = "user",
    turn: int = 0,
) -> Dict[str, Any]:
    """用户应答落地：幂等 answer/waive patch + telemetry。

    全程持 per-session 写锁（与 Pi turn 内的 ensure_document 串行——
    应答发生在 turn 之外，存在并发窗口）。

    Returns:
        {"status": "answered"|"duplicate", "resolved_all": bool,
         "summary": summarize(doc), "still_pending": [codes],
         "applied_patches": n, "default_used": bool}

    Raises:
        ClarificationError（typed；见模块 docstring 的 code 表）。
    """
    if actor != "user":
        # agent 不得代答 blocking 澄清（C5；patch 层同步硬门）
        ledger = await load_ledger(session_store, session_id)
        _record_telemetry(ledger, "actor_denied", request_id=request_id[:64])
        await save_ledger(session_store, session_id, ledger)
        raise ClarificationError("clarification_actor_denied",
                                 f"actor={actor!r} 不可提交澄清应答")
    if not answers and not waives:
        raise ClarificationError("clarification_answer_invalid", "空应答")
    if len(answers) > MAX_ANSWERS or len(waives or []) > MAX_ANSWERS:
        raise ClarificationError("clarification_answer_invalid", "应答数超上限")
    from app.services.gis_harness.requirement_ir.service import session_writer_lock
    async with session_writer_lock(session_id):
        return await _submit_answers_body(
            session_store, session_id, request_id, answers,
            waives=waives, expected_revision=expected_revision, turn=turn)


async def _submit_answers_body(
    session_store: Any,
    session_id: str,
    request_id: str,
    answers: List[Dict[str, str]],
    *,
    waives: Optional[List[str]] = None,
    expected_revision: Optional[int] = None,
    turn: int = 0,
) -> Dict[str, Any]:
    """submit_answers 的锁内主体（调用方已持 session 写锁 + actor 门）。"""
    started = time.monotonic()
    state = await load_state(session_store, session_id)
    if state is None:
        raise ClarificationError("clarification_absent", "无 requirement 文档")
    doc = _doc_from(state.document)
    by_key: Dict[str, Ambiguity] = {
        a.context_key: a for a in doc.intent.ambiguities}
    open_blocking = {a.context_key for a in pending_questions(doc)}

    ledger = await load_ledger(session_store, session_id)

    # ── 幂等检查先行（重复同答案 → 同结果；不看 revision、不要求
    #    仍有 open blocking——全部答完后的重复 POST 必须幂等返回） ──
    duplicates: List[Ambiguity] = []
    pending_answers: List[Tuple[Ambiguity, str]] = []
    for item in answers:
        key = str(item.get("context_key") or "")
        value = str(item.get("value") or "")
        target = by_key.get(key)
        if target is None:
            raise ClarificationError(
                "clarification_unknown_context", f"context_key {key!r} 不在本文档",
                detail={"context_key": key})
        if target.state == "answered":
            if (target.answer or "").strip() == value.strip():
                duplicates.append(target)
                continue
            raise ClarificationError(
                "clarification_already_answered",
                f"context_key {key!r} 已以不同答案作答",
                detail={"context_key": key, "recorded_answer": target.answer})
        if target.state == "waived":
            raise ClarificationError(
                "clarification_already_answered",
                f"context_key {key!r} 已被豁免",
                detail={"context_key": key})
        if key not in open_blocking:
            raise ClarificationError(
                "clarification_unknown_context",
                f"context_key {key!r} 非 open blocking 歧义",
                detail={"context_key": key})
        pending_answers.append((target, value))
    pending_waives: List[Ambiguity] = []
    for key in waives or []:
        target = by_key.get(key)
        if target is None:
            raise ClarificationError(
                "clarification_unknown_context", f"context_key {key!r} 不在本文档",
                detail={"context_key": key})
        if target.state == "waived":
            continue   # 重复豁免 → 幂等
        if target.state == "answered":
            raise ClarificationError(
                "clarification_already_answered",
                f"context_key {key!r} 已作答，不可豁免",
                detail={"context_key": key})
        if not target.default_value:
            raise ClarificationError(
                "clarification_waive_not_allowed",
                f"context_key {key!r} 无安全默认值，不可豁免",
                detail={"context_key": key})
        pending_waives.append(target)

    if not pending_answers and not pending_waives:
        # 全部为重复应答 → 幂等返回当前状态（同结果语义）
        summary = summarize(doc)
        still = [q.code for q in pending_questions(doc)]
        _record_telemetry(
            ledger, "duplicate", request_id=request_id[:64],
            latency_ms=int((time.monotonic() - started) * 1000))
        await save_ledger(session_store, session_id, ledger)
        return {"status": "duplicate", "resolved_all": not still,
                "summary": summary, "still_pending": still,
                "applied_patches": 0, "default_used": False}

    # ── request_id / revision CAS（在幂等检查之后） ──
    # request_id 绑定当前文档派生值：不匹配即未知请求（防跨会话/伪造 id）
    all_keys = sorted(open_blocking)
    if request_id != request_id_for(doc, all_keys):
        # 允许部分作答：request_id 覆盖全部 open，但作答子集也合法
        if request_id != request_id_for(doc, sorted(
                {a.context_key for a, _ in pending_answers}
                | {a.context_key for a in pending_waives})):
            raise ClarificationError(
                "clarification_unknown_request",
                f"request_id {request_id!r} 与本文档不匹配",
                detail={"expected": request_id_for(doc, all_keys)})
    if expected_revision is not None and expected_revision != doc.revision:
        _record_telemetry(ledger, "stale", request_id=request_id[:64])
        await save_ledger(session_store, session_id, ledger)
        raise ClarificationError(
            "clarification_stale_revision",
            f"expected_revision={expected_revision} != 当前 revision={doc.revision}",
            detail={"current_revision": doc.revision})

    # ── 答案 typed 预检（fail-closed：全部通过才提交，无部分应用） ──
    validated: List[Tuple[Ambiguity, str]] = []
    default_used = bool(pending_waives)   # waive 即显式采用安全默认
    question_views = {q.context_key: q for q in build_envelope(
        doc, [by_key[k] for k in sorted(open_blocking)],
        session_id=session_id).questions}
    for target, raw in pending_answers:
        view = question_views.get(target.context_key)
        if view is None:
            raise ClarificationError(
                "clarification_unknown_context",
                f"context_key {target.context_key!r} 不在 pending 集",
                detail={"context_key": target.context_key})
        value = resolve_answer_value(view, raw)
        if value == target.default_value and target.default_value:
            default_used = True
        _validate_against_patch(target.path, value)
        validated.append((target, value))

    # ── 转 TurnPatch（确定性 op_id → patch 层幂等二道防线） ──
    patches: List[TurnPatch] = []
    for target, value in validated:
        patches.append(TurnPatch(
            op="answer_ambiguity",
            value={"context_key": target.context_key, "answer": value},
            turn=turn or doc.updated_turn,
            actor="user",
            reason="clarification answer",
            op_id=f"ans-{request_id[:32]}-{target.context_key}"[:64],
        ))
    for target in pending_waives:
        patches.append(TurnPatch(
            op="waive_ambiguity",
            value={"context_key": target.context_key},
            turn=turn or doc.updated_turn,
            actor="user",
            reason="clarification waive",
            op_id=f"wv-{request_id[:32]}-{target.context_key}"[:64],
        ))

    from app.services.gis_harness.requirement_ir.service import (
        _apply_user_patches_unlocked,
    )
    result = await _apply_user_patches_unlocked(session_store, session_id, patches)
    errors = list(result.get("errors") or [])
    if errors:
        # patch 层 typed 拒绝码直接透出（patch_value_invalid 等）
        raise ClarificationError(
            errors[0], f"patch 拒绝: {','.join(errors)}",
            detail={"errors": errors})

    # ── 记账：已落地 context_key 出 emitted（后续同 context 不再问） ──
    for key in [a.context_key for a, _ in validated] + \
               [a.context_key for a in pending_waives]:
        ledger.emitted.pop(key, None)
    if pending_waives:
        _record_telemetry(ledger, "waived",
                          code=",".join(a.code for a in pending_waives),
                          request_id=request_id[:64])
    if validated:
        _record_telemetry(
            ledger, "answered",
            code=",".join(a.code for a, _ in validated),
            request_id=request_id[:64],
            latency_ms=int((time.monotonic() - started) * 1000))
    if default_used:
        _record_telemetry(ledger, "default_used",
                          code=",".join(a.code for a, _ in validated
                                        if a.default_value),
                          request_id=request_id[:64])
    await save_ledger(session_store, session_id, ledger)

    summary = result.get("summary") or {}
    fresh_state = await load_state(session_store, session_id)
    fresh_doc = _doc_from(fresh_state.document) if fresh_state else doc
    still = [q.code for q in pending_questions(fresh_doc)]
    return {
        "status": "answered",
        "resolved_all": not still,
        "summary": summary,
        "still_pending": still,
        "applied_patches": int(result.get("applied") or 0),
        "default_used": default_used,
    }


__all__ = [
    "CLARIFICATION_ENVELOPE_SCHEMA",
    "CLARIFICATION_LEDGER_KEY",
    "ClarificationEnvelope",
    "EnvelopeQuestion",
    "EnvelopeOption",
    "ClarificationLedger",
    "TelemetryEvent",
    "ClarificationError",
    "build_envelope",
    "request_id_for",
    "pending_envelope",
    "collect_turn_clarification",
    "submit_answers",
    "resolve_answer_value",
    "load_ledger",
    "save_ledger",
]
