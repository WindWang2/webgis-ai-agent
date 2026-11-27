"""视觉证据注册表（G36 / ADR-0291）。

索引存放于 ``session_data``（与 critic 截图索引同机制、**不同 key**：
``_visual_context_evidence``）；字节经 ``refs`` 引用计数入 blob store。

生命周期：
- ``draft``：已上传未发送。每会话 ≤ ``MAX_DRAFTS_PER_SESSION``（FIFO 淘汰
  最旧 draft，释放其 blob 引用）；超 24h 的 draft 由清理路径回收。
- ``bound``：随消息发送（``bind_evidence_to_message``）——保留策略与消息
  行一致，不被 critic FIFO 或 draft 上限淘汰（V19）。
- ``revoked``：显式撤销 tombstone —— 记录保留（可审计），解析恒拒绝，
  blob 引用即时释放（V28/V33）。

并发：索引经 ``atomic_update_state``（CAS 重试）更新；blob I/O 在工作
线程。回滚语义：blob 引用先登记，索引提交失败 → 释放引用（不留悬空
字节计数）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Sequence, Tuple

from app.services.visual_context.evidence import (
    EvidenceRejected,
    STATE_BOUND,
    STATE_DRAFT,
    STATE_REVOKED,
    VisualEvidenceRecord,
    viewport_identity_hash,
)
from app.services.visual_context import refs

logger = logging.getLogger(__name__)

INDEX_KEY = "_visual_context_evidence"
MAX_DRAFTS_PER_SESSION = 12
DRAFT_TTL_S = 24 * 3600
#: 单会话证据登记预算（1 小时滚动窗；防刷图）。
MAX_REGISTRATIONS_PER_HOUR = 60
_REGISTRATION_WINDOW_KEY = "_visual_context_registrations"


async def _update_index(session_id: str, mutate) -> Optional[List[VisualEvidenceRecord]]:
    """CAS 原子更新索引；mutate(records) → (new_records, result)。

    review P1#4：``atomic_update_state`` 在 Redis WATCH 冲突时**重跑** update
    —— mutate 必须纯（无闭包累积副作用），且结果取**最后一次**（已提交）
    尝试，不能取首次（stale）。mutate 的 result 里携带本次尝试的副作用
    清单（每次尝试重建），提交方只用最终一份。
    """
    from app.services.session_data import session_data_manager
    from app.services.session_state_atomic import atomic_update_state

    result_holder: List[Tuple] = []

    def update(snapshot):
        raw = snapshot.get(INDEX_KEY) or []
        records = [r for item in raw
                   if (r := VisualEvidenceRecord.from_dict(item)) is not None]
        new_records, result = mutate(records)
        # 覆盖式记录：重试时旧尝试的结果整体作废（防 stale/重复副作用）。
        result_holder.clear()
        result_holder.append((new_records, result))
        return {INDEX_KEY: [r.to_dict() for r in new_records]}

    committed = await atomic_update_state(session_data_manager, session_id, [INDEX_KEY], update)
    if committed is None:
        return None
    _new_records, result = result_holder[0] if result_holder else ([], None)
    return result


async def load_evidence_index(session_id: str) -> List[VisualEvidenceRecord]:
    """读索引（畸形条目诚实跳过；**读取异常同样 → 空列表**，UI/解析面用）。

    review P2#10：V33 台账复验必须区分「索引确为空」与「存储瞬态不可用」
    ——后者把全部已送 sha 误判为已撤会毁灭性换代。复验路径改用
    ``load_evidence_index_strict``（异常上抛 → 只阻断图片，不换代）。
    """
    try:
        return await load_evidence_index_strict(session_id)
    except Exception:  # noqa: BLE001 — 解析面：索引缺席 = 无证据（诚实缺席）
        return []


async def load_evidence_index_strict(session_id: str) -> List[VisualEvidenceRecord]:
    """读索引（异常上抛；复验/审计路径用 —— 区分空与不可用）。"""
    from app.services.session_data import session_data_manager

    raw = await session_data_manager.get_map_state(session_id)
    items = (raw or {}).get(INDEX_KEY)
    if not isinstance(items, list):
        return []
    out = []
    # 不做条数截断：bound 证据永不淘汰，任何读取上限都会让较早证据
    # 「查无此条」→ 台账误判已撤（V33）。
    for item in items:
        r = VisualEvidenceRecord.from_dict(item)
        if r is not None:
            out.append(r)
    return out


async def list_evidence(session_id: str) -> List[VisualEvidenceRecord]:
    """UI 列表（全部状态，含 tombstone —— 前端显示「已移除」）。"""
    return await load_evidence_index(session_id)


class _BudgetExceeded(Exception):
    pass


async def _admit_registration(session_id: str) -> None:
    """登记预算（滚动 1 小时；超限拒绝，不无限刷图）。"""
    now = time.time()
    from app.services.session_data import session_data_manager
    from app.services.session_state_atomic import atomic_update_state

    def update(snapshot):
        raw = snapshot.get(_REGISTRATION_WINDOW_KEY) or []
        stamps = [float(t) for t in raw if isinstance(t, (int, float))][-MAX_REGISTRATIONS_PER_HOUR:]
        stamps = [t for t in stamps if now - t < 3600.0]
        if len(stamps) >= MAX_REGISTRATIONS_PER_HOUR:
            raise _BudgetExceeded()
        return {_REGISTRATION_WINDOW_KEY: stamps + [now]}

    try:
        committed = await atomic_update_state(session_data_manager, session_id,
                                              [_REGISTRATION_WINDOW_KEY], update)
    except _BudgetExceeded:
        raise EvidenceRejected(
            "registration_budget_exceeded",
            "图片登记过于频繁，请稍后再试。") from None
    if committed is None:
        raise EvidenceRejected("registration_commit_unavailable")


async def register_evidence(
    session_id: str,
    derived_bytes: bytes,
    record: VisualEvidenceRecord,
) -> VisualEvidenceRecord:
    """入库：blob 引用 → 索引登记（draft FIFO 上限）→ 回滚护栏。

    返回已固化 ``evidence_id`` 的记录；并发登记/回滚见模块 docstring。
    """
    from app.services.durable_blob_store import sha256_of_bytes

    if not session_id:
        raise EvidenceRejected("session_required")
    if not derived_bytes:
        raise EvidenceRejected("empty_input")
    record.session_id = session_id
    record.validate_invariants()
    await _admit_registration(session_id)

    if record.derived_sha256 and record.derived_sha256 != sha256_of_bytes(derived_bytes):
        raise EvidenceRejected("derived_sha_mismatch", "派生字节与声明 sha 不符。")
    if not record.derived_sha256:
        record.derived_sha256 = sha256_of_bytes(derived_bytes)
    if record.derived_size != len(derived_bytes):
        record.derived_size = len(derived_bytes)

    stored = await asyncio.to_thread(
        refs.store_evidence_ref, record.derived_sha256, session_id, derived_bytes)
    if not stored:
        raise EvidenceRejected("reference_commit_unavailable")
    # review P2#12：draft TTL 的会话内惰性执行（无全局会话枚举的最小实现
    # —— 每次登记顺带回收本会话超龄 draft；全局 blob 级兜底在周期 sweep）。
    try:
        await purge_expired_drafts(session_id)
    except Exception:  # noqa: BLE001 — 清理绝不阻断登记
        logger.debug("[VisualContextStore] lazy draft purge failed sid=%s",
                     session_id[:24], exc_info=True)

    def mutate(records: List[VisualEvidenceRecord]):
        # review P1#4：CAS 重试会重跑本函数 —— 副作用（淘汰清单）随 result
        # 返回（每次尝试重建，提交方只用最终一份），不在闭包累积。
        evicted: List[VisualEvidenceRecord] = []
        # draft FIFO：超上限淘汰最旧 draft（bound/revoked 不动，P2#13：
        # 总量帽只作用于 draft —— bound/revoked 永不被截断驱逐）。
        drafts = [r for r in records if r.state == STATE_DRAFT]
        overflow = len(drafts) + 1 - MAX_DRAFTS_PER_SESSION
        if overflow > 0:
            drafts_sorted = sorted(drafts, key=lambda r: r.created_at)
            drop_ids = {r.evidence_id for r in drafts_sorted[:overflow]}
            evicted = [r for r in drafts_sorted[:overflow]]
            records = [r for r in records if r.evidence_id not in drop_ids]
        # 同 evidence_id 幂等：重登记 = 拒绝（identity 不可覆盖）。
        if any(r.evidence_id == record.evidence_id for r in records):
            return records, ("duplicate_id", (None, []))
        records = records + [record]
        return records, ("ok", (record, evicted))

    try:
        result = await _update_index(session_id, mutate)
    except BaseException:
        await asyncio.to_thread(refs.release_evidence_ref,
                                record.derived_sha256, session_id)
        raise
    if result is None:
        await asyncio.to_thread(refs.release_evidence_ref,
                                record.derived_sha256, session_id)
        raise EvidenceRejected("index_commit_unavailable")
    status, payload = result
    if status == "duplicate_id":
        await asyncio.to_thread(refs.release_evidence_ref,
                                record.derived_sha256, session_id)
        raise EvidenceRejected("duplicate_evidence_id")
    saved, evicted = payload
    # 淘汰 draft 的引用释放（失败留给 sweep；不影响新登记）。
    for old in evicted:
        await asyncio.to_thread(refs.release_evidence_ref,
                                old.derived_sha256, session_id)
    return saved or record


async def resolve_evidence(
    session_id: str,
    evidence_id: str,
    *,
    max_bytes: int,
) -> Optional[Tuple[VisualEvidenceRecord, bytes]]:
    """受权解析：会话索引在场 + 非 revoked + blob 授权读取 + sha 校验。"""
    if not evidence_id or not session_id:
        return None
    records = await load_evidence_index(session_id)
    rec = next((r for r in records if r.evidence_id == evidence_id), None)
    if rec is None or rec.state == STATE_REVOKED:
        return None
    data = await asyncio.to_thread(
        refs.read_evidence_bytes, rec.derived_sha256, session_id, max_bytes)
    if data is None:
        return None
    return rec, data


async def get_evidence_record(
    session_id: str, evidence_id: str,
) -> Optional[VisualEvidenceRecord]:
    """只读元数据（不取字节；UI/工具状态用）。"""
    records = await load_evidence_index(session_id)
    return next((r for r in records if r.evidence_id == evidence_id), None)


async def revoke_evidence(session_id: str, evidence_id: str) -> Optional[VisualEvidenceRecord]:
    """撤销（draft/bound 均可）：tombstone + 引用释放（V17 共享安全）。

    review P1#4：释放清单随 mutate result（每次 CAS 尝试重建）——重试不会
    双减共享 blob 的引用计数。
    """

    def mutate(records: List[VisualEvidenceRecord]):
        target = next((r for r in records if r.evidence_id == evidence_id), None)
        if target is None:
            return records, ("not_found", (None, []))
        if target.state == STATE_REVOKED:
            return records, ("already", (target, []))
        target.state = STATE_REVOKED
        return records, ("ok", (target, [target.derived_sha256]))

    result = await _update_index(session_id, mutate)
    if result is None:
        raise EvidenceRejected("index_commit_unavailable")
    status, (rec, released) = result
    for sha in released:
        await asyncio.to_thread(refs.release_evidence_ref, sha, session_id)
    return rec if status in ("ok", "already") else None


async def bind_evidence_to_message(
    session_id: str,
    evidence_ids: Sequence[str],
    message_id: int,
) -> int:
    """发送时绑定：draft → bound（幂等；返回成功绑定数）。"""
    wanted = [e for e in evidence_ids if e]

    def mutate(records: List[VisualEvidenceRecord]):
        bound = 0
        for r in records:
            if r.evidence_id in wanted and r.state == STATE_DRAFT:
                r.state = STATE_BOUND
                r.bound_message_id = int(message_id)
                bound += 1
        return records, ("ok", bound)

    result = await _update_index(session_id, mutate)
    if result is None:
        return 0
    _status, bound = result
    return int(bound or 0)


async def purge_expired_drafts(session_id: str) -> int:
    """过期 draft 回收（24h；由周期清理任务调用，见 wire_periodic_sweep）。"""
    now = time.time()

    def mutate(records: List[VisualEvidenceRecord]):
        # review P1#4：释放清单随 result（CAS 重试安全）。
        expired: List[str] = []
        keep = []
        for r in records:
            if r.state == STATE_DRAFT and now - r.created_at > DRAFT_TTL_S:
                expired.append(r.derived_sha256)
                # draft 无消息引用：整条移除（无 tombstone 必要 —— 从未发送）。
                continue
            keep.append(r)
        return keep, ("ok", expired)

    result = await _update_index(session_id, mutate)
    if result is None:
        return 0
    _status, expired = result
    for sha in expired:
        await asyncio.to_thread(refs.release_evidence_ref, sha, session_id)
    return len(expired)


def viewport_hash_of(viewport: Optional[Dict]) -> str:
    """evidence.py 的身份指纹便捷入口（intake 路由用）。"""
    return viewport_identity_hash(viewport)
