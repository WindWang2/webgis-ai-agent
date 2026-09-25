"""RequirementDocument 会话级服务（F02 的唯一 IO 面）。

职责：会话存储（复用 SessionStore map_state 键值面，模式与
``clarification.load/save_asked_slots`` 一致）、CAS/幂等、edit 路由
（多轮增量 → patch 协议）、澄清入档与应答、有界 summary 出口。

store 协议（可注入；单测用内存实现）::

    class _Store:
        async def get_map_state(self, session_id) -> dict: ...
        async def set_map_state(self, session_id, key, value, seq=None) -> bool: ...

生产 store 由调用方传入（Pi turn 上下文中的 SessionStore）；store 故障
**绝不阻塞** intent 主链路（fail-open，与 clarification 同纪律）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from app.services.gis_harness.requirement_ir.build import (
    build_document,
    carry_user_state,
    diff_to_patches,
)
from app.services.gis_harness.requirement_ir.classify import (
    classification_from_core,
)
from app.services.gis_harness.requirement_ir.clarify import (
    pending_questions,
    plan_clarifications,
    record_ambiguities,
    to_legacy_request,
)
from app.services.gis_harness.requirement_ir.contracts import (
    MAX_PATCHES,
    PatchRecord,
    RequirementDocument,
)
from app.services.gis_harness.requirement_ir.digest import (
    document_digest,
    requirement_digest,
)
from app.services.gis_harness.requirement_ir.lifecycle import supersede
from app.services.gis_harness.requirement_ir.patch import apply_patch
from app.services.gis_harness.intent import merge_intent_hints, resolve_map_request_intent
from app.services.gis_harness.workflow_instance import canonical_fingerprint

logger = logging.getLogger(__name__)

REQUIREMENT_STATE_KEY = "requirement_ir_document"
STATE_SCHEMA = "requirement_ir_state.v1"
MAX_SUMMARY_CLARIFICATIONS = 2
MAX_FOLDED_OP_IDS = 128
# 并发假设（诚实披露，review §4）：service 的读-改-写是**单写者**模型——
# 生产路径由 Pi turn lease 串行化（agent_pi_bridge._acquire_turn_lease），
# 同 session 不会有并发写。set_map_state 的 seq 参数传 doc.revision 作为
# best-effort fencing；跨进程双写不被本模块防御（IR 是 fail-open 旁路，
# 最坏丢失一次增量，由下一轮 ensure 重建收敛）。


class SessionState(BaseModel):
    """持久化 envelope：genesis 快照 + 现网文档（回放不变式的载体）。

    journal 达到 MAX_PATCHES 时在此层折叠：genesis 前滚为
    ``replay(genesis, folded_part)``（清空其 journal），现网只保留尾部
    journal——``replay(genesis, document.patches) == document`` 恒成立。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = STATE_SCHEMA
    genesis: Dict[str, Any]      # checkpoint 处的文档 dump（journal 折叠时前滚）
    document: Dict[str, Any]     # 现网文档 dump（含有界 journal）
    folded_patch_count: int = 0
    folded_op_ids: List[str] = Field(default_factory=list, max_length=MAX_FOLDED_OP_IDS)


class TurnPatch(BaseModel):
    """agent/服务层提交 patch 的入参形状（tools/service 边界）。"""

    model_config = ConfigDict(extra="forbid")

    op: str
    path: str = ""
    value: Optional[Any] = None
    reason: str = Field("", max_length=200)
    turn: int = 0
    actor: str = "user"
    op_id: str = ""
    expected_revision: Optional[int] = None


async def load_state(session_store: Any, session_id: str) -> Optional[SessionState]:
    try:
        state = await session_store.get_map_state(session_id)
        payload = (state or {}).get(REQUIREMENT_STATE_KEY)
        if isinstance(payload, dict) and payload.get("schema_version") == STATE_SCHEMA:
            return SessionState.model_validate(payload)
        if isinstance(payload, dict):
            # schema skew：有旧载荷但版本不符——有损降级（重建），显式披露
            logger.warning(
                "[requirement_ir] state schema skew (%s != %s): rebuilding",
                payload.get("schema_version"), STATE_SCHEMA)
    except Exception as exc:  # noqa: BLE001 — 会话面故障不阻塞
        logger.debug("[requirement_ir] load_state skipped: %s", exc)
    return None


async def save_state(session_store: Any, session_id: str, state: SessionState) -> None:
    try:
        revision = int(state.document.get("revision") or 0)
        await session_store.set_map_state(
            session_id, REQUIREMENT_STATE_KEY, state.model_dump(),
            seq=revision or None)
    except Exception as exc:  # noqa: BLE001 — 会话面故障不阻塞
        logger.debug("[requirement_ir] save_state skipped: %s", exc)


def _fold_if_needed(state: SessionState, doc: RequirementDocument):
    """journal 折叠：genesis 前滚 + 现网保留尾部 journal（回放不变式守卫）。"""
    if len(doc.patches) < MAX_PATCHES:
        return state, doc
    from app.services.gis_harness.requirement_ir.patch import replay as _replay
    fold_n = len(doc.patches) // 2
    folded_part = doc.patches[:fold_n]
    genesis_doc = RequirementDocument.model_validate(state.genesis)
    rolled = _replay(genesis_doc, folded_part)
    total_folded = doc.folded_patch_count + fold_n
    # checkpoint 语义：清空 journal、与现网对齐折叠计数（document_digest
    # 折叠计数入账，两侧一致才能保证 replay_document 的 digest 相等）
    rolled = rolled.model_copy(update={
        "patches": [], "folded_patch_count": total_folded})
    trimmed = doc.model_copy(update={
        "patches": doc.patches[fold_n:], "folded_patch_count": total_folded})
    state.genesis = rolled.model_dump()
    folded_ids = [p.op_id for p in folded_part]
    state.folded_op_ids = (folded_ids + state.folded_op_ids)[:MAX_FOLDED_OP_IDS]
    state.folded_patch_count = total_folded
    return state, trimmed


def _is_known_op_id(state: SessionState, op_id: str) -> bool:
    """折叠出 journal 的 op_id 幂等防线（现网 journal 查重之外的补充）。"""
    return op_id in set(state.folded_op_ids)


def _resolve_core(
    query: str,
    hints: Optional[Dict[str, Any]],
    core: Optional[Any] = None,
):
    """core 优先（调用方已解析 → 与 intent 载荷保证一致）；否则本地解析。"""
    if core is not None:
        return core
    base = resolve_map_request_intent(query)
    if hints:
        return merge_intent_hints(base, hints)
    return base


def _doc_from(dump: Dict[str, Any]) -> RequirementDocument:
    return RequirementDocument.model_validate(dump)


def summarize(doc: RequirementDocument) -> Dict[str, Any]:
    """有界 summary（工具 payload 出口；含 digest/澄清/锁/归因面）。"""
    questions = pending_questions(doc)
    legacy = to_legacy_request(questions)
    return {
        "schema": doc.schema_version,
        "document_id": doc.document_id,
        "revision": doc.revision,
        "lifecycle": doc.lifecycle,
        "task_kind": doc.intent.task.kind,
        "stages": list(doc.intent.task.stages),
        "requirement_digest": requirement_digest(doc),
        "document_digest": document_digest(doc),
        "requirement_count": len(doc.requirements.items),
        "open_clarifications": [
            {"code": q.code, "path": q.path, "question_zh": q.question_zh,
             "blocking": q.blocking}
            for q in questions[:MAX_SUMMARY_CLARIFICATIONS]
        ],
        "clarification_request": legacy.model_dump() if legacy else None,
        "locks": [
            {"scope": item.scope, "value": item.value,
             "origin": item.provenance.origin}
            for item in doc.intent.locks[:8]
        ],
        "patch_count": len(doc.patches) + doc.folded_patch_count,
        "user_owned_paths": sorted([
            path for path, prov in doc.intent.field_provenance.items()
            if prov.is_user()])[:16],
    }


async def ensure_document(
    session_store: Any,
    session_id: str,
    query: str,
    *,
    hints: Optional[Dict[str, Any]] = None,
    turn: int = 0,
    core: Optional[Any] = None,
) -> Dict[str, Any]:
    """意图入口的 requirement 面（幂等；edit → patch，新任务 → 超替携带）。

    ``core`` 允许调用方传入已解析的 ``MapRequestIntent``（工具路径复用
    同一解析产物，保证 intent 载荷与 requirement 文档一致且不重复解析）。

    Returns:
        {"summary": summarize(doc), "created": bool, "patched": int,
         "superseded": bool, "patch_errors": [reason codes]}
    """
    state = await load_state(session_store, session_id)
    patch_errors: List[str] = []

    if state is None:
        resolved = _resolve_core(query, hints, core)
        classification = classification_from_core(query, resolved, has_document=False)
        doc = build_document(query, resolved, turn=turn, classification=classification)
        doc = record_ambiguities(doc, plan_clarifications(doc), turn)
        state = SessionState(genesis=doc.model_dump(), document=doc.model_dump())
        await save_state(session_store, session_id, state)
        return {"summary": summarize(doc), "created": True, "patched": 0,
                "superseded": False, "patch_errors": patch_errors}

    doc = _doc_from(state.document)
    resolved = _resolve_core(query, hints, core)
    classification = classification_from_core(query, resolved, has_document=True)

    if classification.kind == "edit":
        patches = diff_to_patches(doc, resolved, turn=turn, query=query)
        applied = 0
        for record in patches:
            try:
                doc, was_applied = apply_patch(doc, record)
                if was_applied:
                    applied += 1
            except ValueError as exc:  # typed PatchError 系
                code = getattr(exc, "code", "patch_rejected")
                patch_errors.append(code)
                if code == "patch_stale_revision":
                    break   # CAS 失败：其余 patch 基于过期基线，全部放弃
        doc = record_ambiguities(doc, plan_clarifications(doc), turn)
        state, doc = _fold_if_needed(state, doc)
        state.document = doc.model_dump()
        await save_state(session_store, session_id, state)
        return {"summary": summarize(doc), "created": False, "patched": applied,
                "superseded": False, "patch_errors": patch_errors}

    # 新任务：语义核变化才超替（同 core 重复触发 = 幂等无操作）
    from app.services.gis_harness.requirement_ir.digest import canonical_core
    if canonical_core(doc) == _canonical_core_of_core(resolved, classification, query):
        return {"summary": summarize(doc), "created": False, "patched": 0,
                "superseded": False, "patch_errors": patch_errors}
    locks, user_fields = carry_user_state(doc)
    new_doc = build_document(
        query, resolved, turn=turn, classification=classification,
        carried_locks=locks)
    new_doc = _restore_user_fields(new_doc, user_fields, turn)
    new_doc = record_ambiguities(new_doc, plan_clarifications(new_doc), turn)
    superseded_doc = supersede(doc, new_doc.document_id)
    state.genesis = new_doc.model_dump()   # 新 checkpoint（旧史随旧文档超替封存）
    state.document = new_doc.model_dump()
    state.folded_patch_count = 0
    state.folded_op_ids = []
    await save_state(session_store, session_id, state)
    return {"summary": summarize(new_doc), "created": True, "patched": 0,
            "superseded": True, "patch_errors": patch_errors,
            "superseded_document": {
                "document_id": superseded_doc.document_id,
                "revision": superseded_doc.revision,
            }}


def _canonical_core_of_core(core, classification, query: str = "") -> Dict[str, Any]:
    """构造"若按该 core 重建"的语义核（与 canonical_core 同构，用于幂等比较）。"""
    from app.services.gis_harness.requirement_ir.build import derive_sections
    from app.services.gis_harness.requirement_ir.contracts import RequirementDocument
    from app.services.gis_harness.requirement_ir.digest import canonical_core
    spec = derive_sections(core, classification, query=query)
    probe = RequirementDocument(document_id="probe", intent=spec)
    return canonical_core(probe)


def _restore_user_fields(
    doc: RequirementDocument,
    user_fields: Dict[str, Any],
    turn: int,
) -> RequirementDocument:
    """超替时把旧文档 user-origin 字段**连值带 provenance** 携带到新文档。

    只在「新派生对该字段无表达（默认值）且旧值非默认」时恢复——新话语
    显式表达了该面时，最新用户表达（经 resolver）优先；恢复失败的路径
    （值不再合法）不携带 provenance（杜绝"幽灵 user-wins"冻结字段，
    review P1-2）。
    """
    from app.services.gis_harness.requirement_ir.patch import (
        PatchError,
        _get_path_value,
        _is_default_value,
        _resolve_setter,
    )
    spec = doc.intent.model_copy(deep=True)
    for path, payload in list(user_fields.items())[:16]:
        old_value, prov = payload if isinstance(payload, tuple) else (None, payload)
        if old_value is None or prov is None or not getattr(prov, "is_user", lambda: False)():
            continue
        handler = _resolve_setter(path)
        if handler is None:
            continue
        current = _get_path_value(spec, path)
        if not _is_default_value(current) or _is_default_value(old_value):
            continue    # 新话语已表达 / 旧值无内容
        setter, validator = handler
        try:
            value = validator(path, old_value)
        except PatchError:
            continue
        try:
            setter(spec, value)
        except Exception:  # noqa: BLE001 — 携带失败不阻塞重建
            continue
        spec.field_provenance[path] = prov
    return doc.model_copy(update={"intent": spec})


async def apply_user_patches(
    session_store: Any,
    session_id: str,
    patches: List[TurnPatch],
) -> Dict[str, Any]:
    """显式 patch 提交面（CAS/幂等由 apply_patch 保证；折叠 op_id 幂等
    由 folded_op_ids 补充——已折叠进 genesis 的历史 patch 不再重放）。"""
    state = await load_state(session_store, session_id)
    if state is None:
        return {"applied": 0, "errors": ["requirement_document_absent"], "summary": None}
    doc = _doc_from(state.document)
    applied, errors = 0, []
    for spec in patches:
        record = PatchRecord(
            op_id=spec.op_id or f"patch-{canonical_fingerprint({'op': spec.op, 'p': spec.path, 'v': spec.value, 't': spec.turn})[:12]}",
            turn=spec.turn,
            actor=spec.actor,  # type: ignore[arg-type]
            op=spec.op,  # type: ignore[arg-type]
            path=spec.path, value=spec.value, reason=spec.reason,
            expected_revision=spec.expected_revision,
        )
        if _is_known_op_id(state, record.op_id):
            continue    # 已折叠历史：幂等跳过
        try:
            doc, was_applied = apply_patch(doc, record)
            if was_applied:
                applied += 1
        except ValueError as exc:
            errors.append(getattr(exc, "code", "patch_rejected"))
            if getattr(exc, "code", "") == "patch_stale_revision":
                break
    doc = record_ambiguities(doc, plan_clarifications(doc), doc.updated_turn)
    state, doc = _fold_if_needed(state, doc)
    state.document = doc.model_dump()
    await save_state(session_store, session_id, state)
    return {"applied": applied, "errors": errors, "summary": summarize(doc)}


async def answer_clarification(
    session_store: Any,
    session_id: str,
    context_key: str,
    answer: str,
    *,
    turn: int = 0,
    actor: str = "user",
) -> Dict[str, Any]:
    """澄清应答：answer_ambiguity patch（走同一协议，可回放可归因）。"""
    return await apply_user_patches(session_store, session_id, [
        TurnPatch(
            op="answer_ambiguity", value={"context_key": context_key, "answer": answer},
            turn=turn, actor=actor, reason="clarification answer",
        ),
    ])


async def get_requirement_view(
    session_store: Any,
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """只读视图（无副作用；无文档 → None）。"""
    state = await load_state(session_store, session_id)
    if state is None:
        return None
    doc = _doc_from(state.document)
    summary = summarize(doc)
    summary["requirements"] = [item.model_dump() for item in doc.requirements.items[:32]]
    return summary


def replay_document(state: SessionState) -> RequirementDocument:
    """从 genesis 重放 journal（回放不变式执行面；drift 检测用）。"""
    genesis = _doc_from(state.genesis)
    doc = _doc_from(state.document)
    rebuilt = apply_patches_from_journal(genesis, doc.patches)
    return rebuilt


def apply_patches_from_journal(
    genesis: RequirementDocument,
    journal: List[PatchRecord],
) -> RequirementDocument:
    from app.services.gis_harness.requirement_ir.patch import replay as _replay
    return _replay(genesis, journal)


__all__ = [
    "REQUIREMENT_STATE_KEY",
    "SessionState",
    "TurnPatch",
    "ensure_document",
    "apply_user_patches",
    "answer_clarification",
    "get_requirement_view",
    "summarize",
    "replay_document",
    "load_state",
    "save_state",
]
