"""Runtime resume 编排（ADR-0220 §5/§6）。

``ResumeRequest → ValidationReport → RecoveryPlan → NewSessionBinding →
ResumeReceipt`` 的执行半边：

- verify-first：校验不过（unsupported_schema / blocked）绝不执行；
- 只重水合已验证字节 + 重建投影 —— 绝不重放 side-effect tool/job；
- observation / visual evidence / working-context 缓存 / in-flight job 一律
  invalidate（旧 browser 世代证据不得复活成当前观察）；
- 每 snapshot 一次 lease（并发 resume 第二请求 fail-fast）；
- 回执诚实披露：恢复了什么 / 没恢复什么 / 哪些要重算 / 哪些数据变了。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.services.project_runtime.contract import (
    DISPOSITION_NEEDS_RECOMPUTE,
    RESUME_BLOCKED,
    RESUME_UNSUPPORTED_SCHEMA,
    ResumeReceipt,
)
from app.services.project_runtime.rebind import (
    RebindResult,
    rebind_mapspec_into_session,
)
from app.services.project_runtime.store import (
    UnsupportedSnapshotSchema,
    get_project_runtime_store,
)

logger = logging.getLogger(__name__)


class ResumeBlocked(Exception):
    """resume 前置校验不通过（携带 ValidationReport 供路由如实上报）。"""

    def __init__(self, status: str, report: Any) -> None:
        super().__init__(f"resume blocked: {status}")
        self.status = status
        self.report = report


class ResumeConflict(Exception):
    """同 snapshot 的 resume lease 在场（并发恢复 fail-fast）。"""

    def __init__(self, holder: Optional[Dict[str, Any]]) -> None:
        super().__init__("a resume for this snapshot is already in progress")
        self.holder = holder or {}


async def resume_runtime_snapshot(
    db: Any,
    *,
    project_id: str,
    snapshot_id: str,
    user_id: Optional[str],
    allow_stale: bool = True,
) -> ResumeReceipt:
    """从 runtime snapshot 构建经验证的新 session runtime。

    ``allow_stale=False`` 时，任何 stale/recompute 发现都拒绝执行（调用
    方要求纯净恢复面的场景）；缺省 True —— 恢复可恢复面 + 诚实披露。
    """
    from app.services.project_runtime.validation import (
        build_recovery_plan,
        validate_runtime_snapshot,
    )

    store = get_project_runtime_store()
    try:
        snapshot = await asyncio.to_thread(store.load, project_id, snapshot_id)
    except UnsupportedSnapshotSchema as exc:
        raise ResumeBlocked(RESUME_UNSUPPORTED_SCHEMA, {"error": str(exc)}) from exc
    if snapshot is None:
        raise LookupError("runtime snapshot not found or unreadable")

    report = await validate_runtime_snapshot(
        db,
        project_id=project_id,
        snapshot_id=snapshot_id,
        current_user_id=str(user_id or ""),
    )
    status = report.status
    if status == RESUME_UNSUPPORTED_SCHEMA:
        raise ResumeBlocked(status, report)
    if status == RESUME_BLOCKED:
        raise ResumeBlocked(status, report)
    if not allow_stale and status != "ready":
        raise ResumeBlocked(status, report)
    plan = build_recovery_plan(snapshot, report)

    acquired, holder = store.acquire_resume_lease(project_id, snapshot_id)
    if not acquired:
        raise ResumeConflict(holder)
    try:
        return await _execute_resume(
            db, snapshot=snapshot, report=report, plan=plan, user_id=user_id
        )
    finally:
        store.release_resume_lease(project_id, snapshot_id)


async def _execute_resume(
    db: Any,
    *,
    snapshot: Any,
    report: Any,
    plan: Any,
    user_id: Optional[str],
) -> ResumeReceipt:
    from app.services.session_data import session_data_manager

    new_sid = f"rts-{snapshot.snapshot_id[4:12]}-{uuid.uuid4().hex[:8]}"
    receipt = ResumeReceipt(
        snapshot_id=snapshot.snapshot_id,
        project_id=snapshot.project_id,
        session_id=new_sid,
        source_session_id=str(snapshot.session_id or ""),
        status=report.status,
        mapspec_frozen_fingerprint=str(snapshot.mapspec_fingerprint or ""),
        invalidated=list(plan.invalidated),
    )

    # ── 1. 载荷重水合（digest 校验读；binary lane 归位新 session 磁盘面）──
    ref_map, restored, missing = await _restore_payloads(snapshot, new_sid)
    receipt.ref_map = dict(list(ref_map.items())[:128])
    receipt.restored_refs = restored[:128]
    receipt.missing_refs = missing[:64]

    # ── 2. MapSpec 重绑定（守卫事务；失败 = spec 不落，如实披露）──────────
    rebind: Optional[RebindResult] = None
    if report.mapspec_integrity_ok:
        try:
            rebind = await rebind_mapspec_into_session(
                new_sid,
                snapshot.mapspec_doc,
                snapshot.mapspec_fingerprint,
                ref_map,
            )
        except Exception as e:  # noqa: BLE001 — 重绑定失败按降级披露（不伪装）
            logger.exception("[RuntimeResume] rebind failed: %s", e)
            rebind = RebindResult(error=f"rebind_failed: {e}")
    else:
        rebind = RebindResult(error="frozen doc integrity failed validation")
    if rebind is not None and rebind.committed:
        receipt.mapspec_rebound = True
        receipt.mapspec_new_fingerprint = rebind.rebound_fingerprint
        if rebind.dangling_refs:
            receipt.disclosures.append(
                "mapspec sources unbound (need re-fetch): "
                + ",".join(rebind.dangling_refs[:8])
            )
    else:
        receipt.disclosures.append(
            f"mapspec not restored: {rebind.error if rebind else 'unknown'}"
        )

    # ── 3. 账本重注册（组合 workspace snapshot 契约；id 重映射到新 ref）──
    # 注意：_register_ledger 可能经 workspace 指针补恢复额外契约载荷并就地
    # 扩充 ref_map —— 回执的 ref_map/计数在其后刷新（恢复面完整披露）。
    registered, expired, degraded = await _register_ledger(
        snapshot, new_sid, ref_map
    )
    receipt.artifacts_registered = registered
    receipt.artifacts_expired = expired
    receipt.degraded = degraded[:64]
    receipt.ref_map = dict(list(ref_map.items())[:128])
    # workspace 指针补恢复成功的 ref 不再是 missing（恢复面完整披露）。
    receipt.missing_refs = [
        r for r in receipt.missing_refs if r not in ref_map
    ][:64]

    # ── 4/5. plan 种子 + 会话标记 + Conversation 行（失败按披露降级 ——
    # 载荷/spec/账本已提交，后续步骤失败不产生无回执的半成品会话）────────
    try:
        await _seed_plan(snapshot, new_sid, ref_map, report)
    except Exception:  # noqa: BLE001 — plan 种子失败：恢复面已成立，如实披露
        logger.warning("[RuntimeResume] plan seed failed sid=%s", new_sid,
                       exc_info=True)
        receipt.disclosures.append("session plan not seeded (store error)")
    try:
        await session_data_manager.set_map_state(
            new_sid, "_resumed_from_runtime",
            {
                "snapshot_id": snapshot.snapshot_id,
                "source_session_id": snapshot.session_id,
                "resumed_at": time.time(),
                "mapspec_frozen_fingerprint": snapshot.mapspec_fingerprint,
            },
        )
    except Exception:  # noqa: BLE001 — 标记失败不撤销已恢复面
        logger.warning("[RuntimeResume] resume marker failed sid=%s", new_sid,
                       exc_info=True)
        receipt.disclosures.append("resume marker not written")
    try:
        from app.models.db_model import Conversation

        def _create_conversation():
            existing = db.get(Conversation, new_sid)
            if existing is None:
                db.add(Conversation(
                    id=new_sid,
                    user_id=user_id,
                    title=str(
                        snapshot.plan.user_goal or "恢复的项目运行时"
                    )[:200],
                ))
                db.commit()

        import asyncio

        await asyncio.to_thread(_create_conversation)
    except Exception:  # noqa: BLE001 — DB 不可用时恢复本身仍成功（诚实降级）
        logger.warning(
            "[RuntimeResume] conversation row create failed sid=%s", new_sid,
            exc_info=True,
        )
        receipt.disclosures.append("conversation row not created (db unavailable)")

    # ── 6. dataset/capability 漂移披露（不自动改写任何状态）──────────────
    receipt.datasets_changed = [
        dataset_id for dataset_id, verdict in report.dataset_verdicts.items()
        if verdict != "ok"
    ]
    receipt.capabilities_unavailable = [
        cap for cap, verdict in report.capability_verdicts.items()
        if verdict in ("unavailable", "credential_missing", "error")
    ]
    if receipt.datasets_changed:
        receipt.disclosures.append(
            "dataset drift since freeze: " + ",".join(receipt.datasets_changed[:8])
        )
    if receipt.capabilities_unavailable:
        receipt.disclosures.append(
            "capability unavailable: "
            + ",".join(receipt.capabilities_unavailable[:8])
        )
    receipt.disclosures.extend([
        "render observations/visual evidence invalidated (old generation)",
        "in-flight jobs not restored",
    ])
    return receipt


async def _restore_from_pointer(
    new_sid: str, ref: str, pointer: Any
) -> Optional[str]:
    """digest 校验读 + 归位新 session → 新 ref id（失败 None，绝不半写）。

    binary lane（raster/parquet/cube）复位新 session 磁盘面，同 ref id
    （磁盘 lane 无会话令牌语义）；JSON 载荷 ``store()`` 铸新 ref id（会话
    ref 是能力令牌，跨 session 不复用旧 id）。
    """
    from app.services.artifact_registry import (
        is_cube_ref,
        is_fabric_parquet_ref,
    )
    from app.services.session_data import session_data_manager
    from app.services.workspace.durability import (
        RestoredBinary,
        read_back_payload,
        restore_cube_store,
        restore_fabric_parquet_file,
        restore_raster_png,
    )

    # O(payload) blob 读 + sha256 + json 解析 —— 绝不上事件循环
    #（durability 模块同款线程纪律；validation 的 verify 同理已线程化）。
    payload = await asyncio.to_thread(read_back_payload, pointer.model_dump())
    if payload is None:
        return None
    if isinstance(payload, RestoredBinary):
        if is_fabric_parquet_ref(ref):
            ok = await restore_fabric_parquet_file(new_sid, ref, payload.data)
        else:
            ok = await restore_raster_png(new_sid, ref, payload.data)
        return ref if ok else None
    if is_cube_ref(ref):
        ok = await restore_cube_store(
            new_sid, ref, payload,
            data_object_id=str(pointer.content_payload_sha256 or ""),
        )
        return ref if ok else None
    try:
        from app.services.workspace.durability import _ref_prefix

        new_ref = await session_data_manager.store(
            new_sid, payload, prefix=_ref_prefix(ref))
        return new_ref or None
    except Exception:  # noqa: BLE001 — 新 session 写失败按未恢复（诚实）
        logger.info("[RuntimeResume] json payload restore failed ref=%s", ref)
        return None


async def _restore_from_live(
    old_sid: str, new_sid: str, ref: str, *, expected_digest: str = ""
) -> Optional[str]:
    """无指针（或指针读失败）兜底：旧 session 直读 + RefSpill（24h 窗口）。

    ``expected_digest``（冻结指针的载荷摘要）在场时做 canonical 摘要比对
    —— 旧会话载荷与冻结世代内容不一致（中途被改写）即拒绝恢复，
    绝不把漂移内容塞进声称特定内容的绑定。
    """
    import asyncio

    from app.services.session_data import RefSpillStore, session_data_manager

    payload = None
    try:
        payload = await session_data_manager.get(old_sid, ref)
    except Exception:  # noqa: BLE001
        payload = None
    if payload is None:
        try:
            payload = RefSpillStore().load(old_sid, ref)
        except Exception:  # noqa: BLE001
            payload = None
    if payload is None:
        return None
    if expected_digest:
        try:
            from app.services.project_artifact_promotion import canonical_dumps

            def _digest() -> str:
                import hashlib

                blob = canonical_dumps(payload)
                return hashlib.sha256(blob.encode("utf-8")).hexdigest()

            if await asyncio.to_thread(_digest) != expected_digest:
                logger.info(
                    "[RuntimeResume] live payload digest drift ref=%s — refusing",
                    ref,
                )
                return None
        except Exception:  # noqa: BLE001 — 摘要不可算 = 无从验证 → 拒绝
            return None
    try:
        from app.services.workspace.durability import _ref_prefix

        return await session_data_manager.store(
            new_sid, payload, prefix=_ref_prefix(ref)) or None
    except Exception:  # noqa: BLE001
        return None


async def _restore_payloads(
    snapshot: Any, new_sid: str
) -> Tuple[Dict[str, str], List[str], List[str]]:
    """逐 binding 重水合载荷 → ``(ref_map, restored, missing)``。

    顺序 IO 纪律（低频恢复路径，正确优先；refs 有界 ≤64）：durable 指针
    digest 校验读优先；无指针走旧 session/RefSpill 兜底；双双缺席 →
    missing（诚实披露，绝不伪造在场）。
    """
    ref_map: Dict[str, str] = {}
    restored: List[str] = []
    missing: List[str] = []
    seen: set = set()
    old_sid = str(snapshot.session_id or "")
    for binding in snapshot.source_bindings:
        ref = str(binding.ref or "")
        if not ref or not ref.startswith("ref:") or ref in seen:
            continue
        seen.add(ref)
        if binding.promotion_class == "sensitive":
            # sensitive 载荷只披露不搬运（freeze 未物化；恢复同样拒绝）。
            missing.append(ref)
            continue
        new_ref: Optional[str] = None
        expected_digest = ""
        if binding.durable_pointer is not None:
            new_ref = await _restore_from_pointer(
                new_sid, ref, binding.durable_pointer)
            expected_digest = str(
                binding.durable_pointer.content_payload_sha256 or "")
        if new_ref is None:
            new_ref = await _restore_from_live(
                old_sid, new_sid, ref, expected_digest=expected_digest)
        if new_ref:
            ref_map[ref] = new_ref
            restored.append(new_ref)
        else:
            missing.append(ref)
    return ref_map, restored, missing


async def _register_ledger(
    snapshot: Any, new_sid: str, ref_map: Dict[str, str]
) -> Tuple[int, int, List[Dict[str, str]]]:
    # sensitive 分类（freeze 判定）在恢复侧同样生效：账本契约补恢复跳过。
    sensitive_refs = {
        str(b.ref) for b in snapshot.source_bindings
        if b.promotion_class == "sensitive" and b.ref
    }
    """workspace snapshot 契约 → 新 session 账本（id 重映射 + 诚实过期）。"""
    from app.services.artifact_registry import mark_status, register_artifact
    from app.services.workspace.snapshot import get_workspace_snapshot_service

    ws_id = str(snapshot.workspace_snapshot_id or "")
    if not ws_id:
        return 0, 0, []
    ws = get_workspace_snapshot_service().load_snapshot(
        str(snapshot.session_id or ""), ws_id, project_id=snapshot.project_id
    )
    if ws is None:
        return 0, 0, [{
            "artifact_id": "*", "reason": "workspace_snapshot_missing",
        }]
    registered = 0
    expired = 0
    degraded: List[Dict[str, str]] = []
    for contract in ws.artifact_contracts:
        old_id = contract.artifact_id
        if (
            old_id.startswith("ref:")
            and old_id not in ref_map
            and old_id not in sensitive_refs
            and old_id in ws.durable_pointers
        ):
            # spec source 面没覆盖的账本契约（chart/table/export 等）：从
            # workspace snapshot 自己的 durable 指针补恢复（同一 BlobStore
            # 真相；digest 校验读前置，失败如实 expired）。
            extra = await _restore_from_pointer(
                new_sid, old_id, ws.durable_pointers[old_id])
            if extra:
                ref_map[old_id] = extra
        target_id = ref_map.get(old_id, old_id)
        metadata: Dict[str, Any] = {
            "logical_role": contract.logical_role.value,
            "persistence": contract.persistence.value,
            "restored_from": snapshot.snapshot_id,
        }
        if contract.fingerprint.content:
            metadata["content_fingerprint"] = contract.fingerprint.content
        # 血缘边重映射到新 id（未恢复的 parent 保持旧 id —— 其契约行同样
        # 注册（expired），边仍可解析到诚实的过期行）。
        remapped_inputs = [
            ref_map.get(p, p) for p in contract.lineage.parents
        ]
        ok = await register_artifact(
            new_sid,
            artifact_id=target_id,
            artifact_type=contract.artifact_subtype or None,
            producer_tool=contract.produced_by.tool,
            inputs=remapped_inputs,
            metadata=metadata,
        )
        if ok is None:
            continue
        if old_id.startswith("ref:") and old_id not in ref_map:
            # 载荷未重水合：血缘行保留、状态如实 expired（不伪造可用）。
            await mark_status(new_sid, target_id, "expired")
            expired += 1
            degraded.append({
                "artifact_id": old_id,
                "reason": "payload_not_restored",
            })
        else:
            registered += 1
    return registered, expired, degraded


async def _seed_plan(snapshot: Any, new_sid: str, ref_map: Dict[str, str], report: Any) -> None:
    """fresh SessionPlan：身份投影 + ref 重写后的 restorable chapter + 披露块。"""
    from app.services.session_plan import SessionPlan, save_session_plan

    def _rewrite_refs(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: _rewrite_refs(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_rewrite_refs(v) for v in node]
        if isinstance(node, str) and node.startswith("ref:"):
            return ref_map.get(node, "")
        return node

    chapter: Dict[str, Any] = {
        k: _rewrite_refs(v)
        for k, v in (snapshot.plan.restorable_chapter or {}).items()
    }
    chapter["resumed_from_runtime"] = {
        "snapshot_id": snapshot.snapshot_id,
        "source_session_id": snapshot.session_id,
        "resumed_at": time.time(),
        "mapspec_frozen_fingerprint": snapshot.mapspec_fingerprint,
        "workflow_rows_fingerprint": snapshot.plan.workflow_rows_fingerprint,
        "validation_status": report.status,
        "datasets_changed": [
            d for d, v in report.dataset_verdicts.items() if v != "ok"
        ],
        "needs_recompute": [
            (d.ref or d.source_id)
            for d in report.dispositions
            if d.disposition == DISPOSITION_NEEDS_RECOMPUTE
        ][:32],
    }
    plan = SessionPlan(
        envelope_id=f"sp-{uuid.uuid4().hex[:12]}",
        session_id=new_sid,
        user_goal=str(snapshot.plan.user_goal or ""),
        gis_chapter=chapter,
        progress=[],
        previous_goal="",
    )
    await save_session_plan(plan)
