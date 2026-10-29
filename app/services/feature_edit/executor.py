"""FeatureEditExecutor —— 唯一要素数据写路径（/goal 03）。

并发纪律（与仓内既有模式一致，绝不发明第二种锁语义）：
- **工具路径（chat turn 内）**：turn 已全程持有会话锁（RUN-03，非重入），
  本执行器经 harness_kernel.legacy_adapter 的 ContextVar 绑定检测到
  「父已持锁」即透传跳过重取（session_plan.py:1136 / artifact_registry.py:521
  同款）；同波并发工具再以**进程内 per-session 编辑锁**串行化。
- **REST 路径**：正常取 session_lock_registry 分布式锁（跨 pod 互斥）。
- **MapSpec 盖章绝不嵌套**：数据锁释放后才调 apply_gis_mutation（引擎自
  取同一把非重入锁）；turn 内改为后台任务等锁外执行
  （cartography_runtime._schedule_selfheal_commit 同款）。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.data_ingest.pipeline import compute_payload_fingerprint
from app.services.distributed_lock import session_lock_registry
from app.services.feature_edit.contracts import (
    JOURNALS_STATE_KEY,
    MAX_JOURNALS_PER_SESSION,
    PLANS_STATE_KEY,
)
from app.services.feature_edit.identity import has_wfids, materialize_wfids
from app.services.feature_edit.journal import (
    append_entry,
    apply_deltas_to_features,
    apply_schema_ops_to_features,
    entry_for_idempotency_key,
    head_entry,
    invert_entry,
    new_journal,
)
from app.services.feature_edit.plan import derive_changes, extract_fc
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

# 进程内 per-session 编辑锁：同波并发工具（turn 锁透传时）的 RMW 串行化。
_PROCESS_LOCKS: Dict[str, asyncio.Lock] = {}
_PROCESS_LOCK_CAP = 256


def _process_lock(session_id: str) -> asyncio.Lock:
    lock = _PROCESS_LOCKS.get(session_id)
    if lock is None:
        if len(_PROCESS_LOCKS) > _PROCESS_LOCK_CAP:
            _PROCESS_LOCKS.clear()  # 会话级低频写路径，简单容量回收即可
        lock = _PROCESS_LOCKS[session_id] = asyncio.Lock()
    return lock


def _turn_lock_held(session_id: str) -> bool:
    """父 turn 是否已持会话锁（ContextVar 绑定检测；失败按未持锁处理）。"""
    try:
        from app.services.harness_kernel.legacy_adapter import _current_lock

        return _current_lock(session_id) is not None
    except Exception:  # noqa: BLE001 — 绑定通道不可用即诚实取锁
        return False


@contextlib.asynccontextmanager
async def _edit_lock(session_id: str):
    """编辑互斥：turn 内透传（父锁已序列化），否则自取分布式会话锁。"""
    passthrough = _turn_lock_held(session_id)
    distributed = None
    if not passthrough:
        distributed = session_lock_registry.lock(
            session_id, fail_on_degraded=True, fail_on_lost=True
        )
    proc = _process_lock(session_id)
    async with proc:
        if distributed is not None:
            async with distributed:
                yield
        else:
            yield


class FeatureEditConflict(Exception):
    """CAS 冲突（HTTP 409 面）——携带 head diff 供前端/模型收敛。"""

    def __init__(self, code: str, detail: Dict[str, Any]):
        super().__init__(code)
        self.code = code
        self.detail = detail


class FeatureEditBlocked(Exception):
    """typed 阻断（finding code + 有界 detail）。"""

    def __init__(self, code: str, message: str, detail: Optional[Dict[str, Any]] = None):
        super().__init__(code)
        self.code = code
        self.message = message
        self.detail = detail or {}


# ─── journal 读写（会话锁内） ───────────────────────────────────────────────

async def _load_journals(session_id: str) -> Dict[str, Dict[str, Any]]:
    journals = await session_data_manager.get_state_field(session_id, JOURNALS_STATE_KEY)
    return journals if isinstance(journals, dict) else {}


async def _save_journal(session_id: str, journals: Dict[str, Dict[str, Any]], ref_id: str) -> None:
    """写回 + LRU 逐出（≤ MAX_JOURNALS_PER_SESSION 个活跃 journal）。"""
    if len(journals) > MAX_JOURNALS_PER_SESSION:
        def _last_ts(j: Dict[str, Any]) -> float:
            head = head_entry(j) or {}
            return float(head.get("created_at") or 0)

        for key in sorted(journals, key=lambda k: _last_ts(journals[k]))[
            : len(journals) - MAX_JOURNALS_PER_SESSION
        ]:
            journals.pop(key, None)
    await session_data_manager.set_map_state_fields(session_id, {JOURNALS_STATE_KEY: journals})


def _receipt(entry: Dict[str, Any], stamp_status: str = "pending", mapspec_revision: Optional[int] = None) -> Dict[str, Any]:
    return {
        "entry_seq": entry.get("seq"),
        "kind": entry.get("kind"),
        "ref_id": entry.get("ref_id"),
        "changed_wfids": [d.get("wfid") for d in entry.get("deltas") or []][:64],
        "schema_ops": entry.get("schema_ops") or [],
        "content_revision_after": entry.get("content_revision_after"),
        "fingerprint_after": entry.get("fingerprint_after"),
        "actor": entry.get("actor"),
        "origin": entry.get("origin"),
        "idempotency_key": entry.get("idempotency_key"),
        "undo_of_seq": entry.get("undo_of_seq"),
        "stamp_status": stamp_status,
        "mapspec_revision": mapspec_revision,
    }


def _head_summary(journal: Dict[str, Any]) -> Dict[str, Any]:
    head = head_entry(journal)
    if not head:
        return {"head_seq": journal.get("head_seq") or 0, "head": None}
    return {
        "head_seq": journal.get("head_seq") or 0,
        "head": {
            "seq": head.get("seq"),
            "kind": head.get("kind"),
            "actor": head.get("actor"),
            "origin": head.get("origin"),
            "created_at": head.get("created_at"),
        },
    }


async def _load_ref(session_id: str, ref_id: str) -> Tuple[Any, Dict[str, Any]]:
    """ref 载荷 + descriptor（跨会话/不存在统一 dataset_not_found，不泄存在性）。"""
    payload = await session_data_manager.get(session_id, ref_id)
    if payload is None:
        raise FeatureEditBlocked("dataset_not_found", f"ref {ref_id[:48]} not resolvable in this session")
    descriptor = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    return payload, descriptor


def _prefetch_target_refs(ops: List[Dict[str, Any]]) -> List[str]:
    return [
        op["target_ref"]
        for op in ops
        if isinstance(op, dict) and op.get("op") == "snap_to_ref" and isinstance(op.get("target_ref"), str)
    ]


# ─── commit ────────────────────────────────────────────────────────────────

async def commit_plan(
    session_id: str,
    plan: Dict[str, Any],
    *,
    actor: str,
    origin: str,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """提交 pending plan：锁内 CAS → 重新求值（不信任预览缓存）→ overwrite →
    journal → 锁外 MapSpec 盖章 + 广播。返回 receipt。"""
    plan_id = plan.get("plan_id") or ""
    ref_id = plan.get("ref_id") or ""
    key = idempotency_key or f"plan:{plan_id}"

    async with _edit_lock(session_id):
        payload, descriptor = await _load_ref(session_id, ref_id)
        journals = await _load_journals(session_id)
        journal = journals.get(ref_id) or new_journal()

        # 幂等：同 key 已入账 → 重放 receipt（绝不写第二版）。
        hit = entry_for_idempotency_key(journal, key)
        if hit is not None:
            return _receipt(hit, stamp_status="replayed")

        current_revision = int(descriptor.get("content_revision") or 0)
        fp_now = compute_payload_fingerprint(payload)
        if (
            int(plan.get("expected_content_revision") or -1) != current_revision
            or str(plan.get("expected_fingerprint") or "") != fp_now
        ):
            await _mark_plan_status(session_id, plan_id, "conflicted")
            raise FeatureEditConflict(
                "stale_revision",
                {
                    "plan_id": plan_id,
                    "expected_content_revision": plan.get("expected_content_revision"),
                    "current_content_revision": current_revision,
                    "journal": _head_summary(journal),
                },
            )

        # 重新求值（与 preview 同一纯函数路径；载荷在锁内冷读，指纹已验）。
        fc = extract_fc(payload)
        if fc is None:
            raise FeatureEditBlocked("dataset_not_editable", "payload is not an editable FeatureCollection")
        materialized = False
        if plan.get("needs_materialization") and not has_wfids(fc):
            fc, _assigned = materialize_wfids(fc, fp_now)
            materialized = True
        target_payloads: Dict[str, Any] = {}
        for target_ref in _prefetch_target_refs(plan.get("ops") or []):
            target_payload, _ = await _load_ref(session_id, target_ref)
            target_payloads[target_ref] = target_payload
        deltas, schema_ops, derive_findings, _ = derive_changes(
            fc, plan.get("ops") or [], plan.get("target_wfids") or [], target_payloads
        )
        if derive_findings:
            # 数据在预览后仍变了内容形态（同指纹不应发生——防御面）。
            raise FeatureEditConflict(
                "stale_revision",
                {"plan_id": plan_id, "revalidation": derive_findings[:8], "journal": _head_summary(journal)},
            )

        new_features, applied = apply_deltas_to_features(fc.get("features") or [], deltas)
        new_features, _ = apply_schema_ops_to_features(new_features, schema_ops)
        new_fc = {**fc, "features": new_features}
        if isinstance(payload, dict) and isinstance(payload.get("geojson"), dict):
            new_payload: Any = {**payload, "geojson": new_fc}
        else:
            new_payload = new_fc

        fp_after = compute_payload_fingerprint(new_payload)
        # 链指纹定义在「物化后空间」：物化是一次性数据集身份升级（首次编辑
        # 落 _wfid），不属于可撤销的编辑效果 —— undo 恢复的是数据编辑而非
        # 去物化。非物化提交的 fp_chain_before 即 fp_now。
        if materialized:
            pre_chain_payload: Any = (
                {**payload, "geojson": fc} if isinstance(payload, dict) and isinstance(payload.get("geojson"), dict) else fc
            )
            fp_chain_before = compute_payload_fingerprint(pre_chain_payload)
        else:
            fp_chain_before = fp_now
        entry = {
            "kind": "apply",
            "idempotency_key": key,
            "actor": actor,
            "origin": origin,
            "ref_id": ref_id,
            "deltas": deltas,
            "schema_ops": schema_ops,
            "op_summary": {
                "ops": plan.get("op_summaries") or [],
                "materialized": materialized,
                "plan_id": plan_id,
            },
            "fingerprint_before": fp_chain_before,
            "fingerprint_after": fp_after,
            "content_revision_before": current_revision,
            "content_revision_after": current_revision,  # overwrite 后更新
            "created_at": time.time(),
        }
        journals[ref_id] = append_entry(journal, entry)
        ok = await session_data_manager.overwrite(session_id, ref_id, new_payload)
        if not ok:
            # 竞态删除：不入账（overwrite 失败即载荷未变）。
            raise FeatureEditBlocked("dataset_not_found", "ref disappeared during commit")
        descriptor_after = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
        revision_after = int(descriptor_after.get("content_revision") or current_revision + 1)
        # append_entry 分配了 seq；回填 revision_after（dict 就地补，CoW 由
        # append_entry 保证——这里改的是刚构造、尚未共享的对象）。
        entry = journals[ref_id]["entries"][-1]
        entry["content_revision_after"] = revision_after
        await _save_journal(session_id, journals, ref_id)
        _pending_receipt_entry = entry

    # ── 销账（锁内完成主体；receipt 在盖章后回写 plan，供幂等重放回放） ──
    entry = _pending_receipt_entry
    await _mark_plan_status(
        session_id, plan_id, "applied", extra={"entry_seq": entry.get("seq"), "applied_at": time.time()}
    )

    # ── 锁外：MapSpec 盖章 + 广播（引擎自取会话锁，绝不嵌套） ──
    receipt = _receipt(entry)
    stamp_status, mapspec_revision = await stamp_data_revision(
        session_id, ref_id, revision_after, defer_if_turn_locked=True
    )
    receipt["stamp_status"] = stamp_status
    receipt["mapspec_revision"] = mapspec_revision
    # receipt 快照入 plan（幂等重放/决策端二次点击原样回放，不再跑盖章）。
    await _mark_plan_status(
        session_id, plan_id, "applied", extra={"receipt": receipt, "entry_seq": entry.get("seq")}
    )
    _broadcast_dataset_updated(session_id, ref_id, revision_after, [d.get("wfid") for d in deltas][:64])
    return receipt


# ─── undo / redo ───────────────────────────────────────────────────────────

async def apply_undo(
    session_id: str, ref_id: str, *, actor: str, origin: str
) -> Dict[str, Any]:
    """撤销 head 条目：逆 delta 走同一 CAS 写路径（链完整性断言）。"""
    async with _edit_lock(session_id):
        payload, descriptor = await _load_ref(session_id, ref_id)
        journals = await _load_journals(session_id)
        journal = journals.get(ref_id) or new_journal()
        head = head_entry(journal)
        if head is None or head.get("kind") not in ("apply", "redo"):
            raise FeatureEditBlocked("undo_unavailable", "no undoable entry at journal head", _head_summary(journal))
        key = f"undo:{head.get('seq')}"
        hit = entry_for_idempotency_key(journal, key)
        if hit is not None:
            return _receipt(hit, stamp_status="replayed")

        current_revision = int(descriptor.get("content_revision") or 0)
        fp_now = compute_payload_fingerprint(payload)
        if fp_now != head.get("fingerprint_after"):
            raise FeatureEditConflict(
                "stale_revision",
                {"ref_id": ref_id, "current_content_revision": current_revision, "journal": _head_summary(journal)},
            )
        inverse = invert_entry(head)
        entry = await _apply_inverse_locked(
            session_id, ref_id, payload, journal, journals, inverse,
            kind="undo", key=key, actor=actor, origin=origin,
            revision_before=current_revision, fp_before=fp_now,
        )
    revision_after = entry["content_revision_after"]
    receipt = _receipt(entry)
    stamp_status, mapspec_revision = await stamp_data_revision(
        session_id, ref_id, revision_after, defer_if_turn_locked=True
    )
    receipt["stamp_status"] = stamp_status
    receipt["mapspec_revision"] = mapspec_revision
    _broadcast_dataset_updated(session_id, ref_id, revision_after, [])
    return receipt


async def apply_redo(
    session_id: str, ref_id: str, *, actor: str, origin: str
) -> Dict[str, Any]:
    """重做：head 必须是 undo，且被撤销条目仍在 journal 内。"""
    async with _edit_lock(session_id):
        payload, descriptor = await _load_ref(session_id, ref_id)
        journals = await _load_journals(session_id)
        journal = journals.get(ref_id) or new_journal()
        head = head_entry(journal)
        if head is None or head.get("kind") != "undo":
            raise FeatureEditBlocked("undo_unavailable", "journal head is not an undo entry", _head_summary(journal))
        target_seq = head.get("undo_of_seq")
        target = next(
            (e for e in journal.get("entries") or [] if e.get("seq") == target_seq), None
        )
        if target is None:
            raise FeatureEditBlocked(
                "undo_unavailable", "undone entry no longer in journal (FIFO dropped)", _head_summary(journal)
            )
        key = f"redo:{target_seq}"
        hit = entry_for_idempotency_key(journal, key)
        if hit is not None:
            return _receipt(hit, stamp_status="replayed")

        current_revision = int(descriptor.get("content_revision") or 0)
        fp_now = compute_payload_fingerprint(payload)
        if fp_now != head.get("fingerprint_after"):
            raise FeatureEditConflict(
                "stale_revision",
                {"ref_id": ref_id, "current_content_revision": current_revision, "journal": _head_summary(journal)},
            )
        redo_entry = {
            "kind": "redo",
            "undo_of_seq": target_seq,
            "idempotency_key": key,
            "actor": actor,
            "origin": origin,
            "ref_id": ref_id,
            "deltas": target.get("deltas") or [],
            "schema_ops": target.get("schema_ops") or [],
            "op_summary": {"ops": (target.get("op_summary") or {}).get("ops") or [], "redo_of_seq": target_seq},
            "fingerprint_before": fp_now,
            "fingerprint_after": target.get("fingerprint_after"),
            "content_revision_before": current_revision,
            "content_revision_after": current_revision,
            "created_at": None,
        }
        entry = await _apply_inverse_locked(
            session_id, ref_id, payload, journal, journals, redo_entry,
            kind="redo", key=key, actor=actor, origin=origin,
            revision_before=current_revision, fp_before=fp_now,
        )
    revision_after = entry["content_revision_after"]
    receipt = _receipt(entry)
    stamp_status, mapspec_revision = await stamp_data_revision(
        session_id, ref_id, revision_after, defer_if_turn_locked=True
    )
    receipt["stamp_status"] = stamp_status
    receipt["mapspec_revision"] = mapspec_revision
    _broadcast_dataset_updated(session_id, ref_id, revision_after, [])
    return receipt


async def _apply_inverse_locked(
    session_id: str,
    ref_id: str,
    payload: Any,
    journal: Dict[str, Any],
    journals: Dict[str, Any],
    effect_entry: Dict[str, Any],
    *,
    kind: str,
    key: str,
    actor: str,
    origin: str,
    revision_before: int,
    fp_before: str,
) -> Dict[str, Any]:
    """undo/redo 共用：应用 deltas/schema_ops → 链断言 → overwrite → 入账。"""
    fc = extract_fc(payload)
    if fc is None:
        raise FeatureEditBlocked("dataset_not_editable", "payload is not an editable FeatureCollection")
    new_features, _ = apply_deltas_to_features(fc.get("features") or [], effect_entry.get("deltas") or [])
    new_features, _ = apply_schema_ops_to_features(new_features, effect_entry.get("schema_ops") or [])
    new_fc = {**fc, "features": new_features}
    if isinstance(payload, dict) and isinstance(payload.get("geojson"), dict):
        new_payload: Any = {**payload, "geojson": new_fc}
    else:
        new_payload = new_fc
    fp_after = compute_payload_fingerprint(new_payload)
    expected_after = effect_entry.get("fingerprint_after")
    if expected_after and fp_after != expected_after:
        # 链完整性断言失败：载荷与 journal 记录不一致（外部写入/损坏）。
        raise FeatureEditConflict(
            "fingerprint_mismatch",
            {"ref_id": ref_id, "journal": _head_summary(journal)},
        )
    entry = dict(effect_entry)
    entry.update(
        {
            "kind": kind,
            "idempotency_key": key,
            "actor": actor,
            "origin": origin,
            "ref_id": ref_id,
            "fingerprint_before": fp_before,
            "fingerprint_after": fp_after,
            "content_revision_before": revision_before,
            "content_revision_after": revision_before,
            "created_at": time.time(),
        }
    )
    journals[ref_id] = append_entry(journal, entry)
    ok = await session_data_manager.overwrite(session_id, ref_id, new_payload)
    if not ok:
        raise FeatureEditBlocked("dataset_not_found", "ref disappeared during commit")
    descriptor_after = await session_data_manager.get_ref_descriptor(session_id, ref_id) or {}
    stored = journals[ref_id]["entries"][-1]
    stored["content_revision_after"] = int(descriptor_after.get("content_revision") or revision_before + 1)
    await _save_journal(session_id, journals, ref_id)
    return stored


# ─── plan store ────────────────────────────────────────────────────────────

async def load_plans(session_id: str) -> Dict[str, Dict[str, Any]]:
    plans = await session_data_manager.get_state_field(session_id, PLANS_STATE_KEY)
    return plans if isinstance(plans, dict) else {}


async def store_plan(session_id: str, plan: Dict[str, Any]) -> None:
    from app.services.feature_edit.contracts import MAX_ACTIVE_PLANS, PLAN_TTL_S

    plans = await load_plans(session_id)
    now = time.time()
    # TTL 清扫 + 容量淘汰（最旧优先）。
    for pid in [p for p, v in plans.items() if not isinstance(v, dict) or now - float(v.get("created_at") or 0) > PLAN_TTL_S]:
        plans.pop(pid, None)
    plans[plan["plan_id"]] = plan
    while len(plans) > MAX_ACTIVE_PLANS:
        oldest = min(plans, key=lambda p: float(plans[p].get("created_at") or 0))
        plans.pop(oldest, None)
    await session_data_manager.set_map_state_fields(session_id, {PLANS_STATE_KEY: plans})


async def get_plan(session_id: str, plan_id: str) -> Optional[Dict[str, Any]]:
    plans = await load_plans(session_id)
    plan = plans.get(plan_id)
    if not isinstance(plan, dict):
        return None
    return plan


async def _mark_plan_status(
    session_id: str, plan_id: str, status: str, extra: Optional[Dict[str, Any]] = None
) -> None:
    plans = await load_plans(session_id)
    plan = plans.get(plan_id)
    if not isinstance(plan, dict):
        return
    plan = {**plan, "status": status, **(extra or {})}
    plans[plan_id] = plan
    await session_data_manager.set_map_state_fields(session_id, {PLANS_STATE_KEY: plans})


# ─── MapSpec 盖章 + 广播（锁外） ───────────────────────────────────────────

async def stamp_data_revision(
    session_id: str,
    ref_id: str,
    content_revision: int,
    *,
    defer_if_turn_locked: bool = True,
    retries: int = 2,
) -> Tuple[str, Optional[int]]:
    """把 ref 的新 content_revision 盖到引用该 ref 的 MapSpec 源上。

    引擎自取会话锁 → 调用点必须已在编辑锁外；turn 内（锁透传场景）调度
    后台任务等锁执行（避免嵌套自锁）。失败有界重试；最终失败返回
    pending（数据已正确，戳滞后由下一笔 mutation 收敛——诚实披露）。
    """
    from app.services.feature_edit.mapspec_stamp import apply_data_revision_stamp

    if defer_if_turn_locked and _turn_lock_held(session_id):
        task = asyncio.create_task(_deferred_stamp(session_id, ref_id, content_revision))
        _TRACKED_STAMP_TASKS.add(task)
        task.add_done_callback(_TRACKED_STAMP_TASKS.discard)
        return "deferred", None

    last_error: Optional[Exception] = None
    for _ in range(retries + 1):
        try:
            result = await apply_data_revision_stamp(session_id, ref_id, content_revision)
            if getattr(result, "is_error", False):
                logger.warning(
                    "[feature_edit] mapspec stamp rejected for %s/%s: %s",
                    session_id, ref_id, getattr(result, "error_msg", "")[:120],
                )
                return "skipped", None
            return "stamped", getattr(result, "mutation_revision", None)
        except Exception as e:  # noqa: BLE001 — 锁竞争等瞬时失败按重试预算
            last_error = e
            await asyncio.sleep(0.2)
    logger.warning(
        "[feature_edit] mapspec stamp failed for %s/%s after retries: %s",
        session_id, ref_id, last_error,
    )
    return "pending", None


_TRACKED_STAMP_TASKS: set = set()


async def _deferred_stamp(session_id: str, ref_id: str, content_revision: int) -> None:
    """turn 锁释放后的后台盖章（有界等待 + 与前台同款纪律）。"""
    try:
        await asyncio.sleep(0.5)  # 让父 turn 的收尾先走
        status, _ = await stamp_data_revision(
            session_id, ref_id, content_revision, defer_if_turn_locked=False, retries=3
        )
        if status != "stamped":
            logger.info(
                "[feature_edit] deferred mapspec stamp status=%s for %s/%s rev=%s",
                status, session_id, ref_id, content_revision,
            )
    except Exception as e:  # noqa: BLE001 — 后台任务绝不向上抛
        logger.warning("[feature_edit] deferred stamp error for %s/%s: %s", session_id, ref_id, e)


def _broadcast_dataset_updated(
    session_id: str, ref_id: str, content_revision: int, changed_wfids: List[str]
) -> None:
    """fire-and-forget WS 广播（消费面可选；主刷新通道是 mapspec 盖章→SSE 镜像）。"""
    try:
        import asyncio as _asyncio

        from app.services.ws_service import broadcast_ws_event

        task = _asyncio.create_task(
            broadcast_ws_event(
                session_id,
                "dataset_updated",
                {"ref": ref_id, "content_revision": content_revision, "changed_wfids": changed_wfids[:16]},
            )
        )
        _TRACKED_STAMP_TASKS.add(task)
        task.add_done_callback(_TRACKED_STAMP_TASKS.discard)
    except Exception as e:  # noqa: BLE001 — 广播失败绝不影响提交结果
        logger.debug("[feature_edit] dataset_updated broadcast failed: %s", e)
