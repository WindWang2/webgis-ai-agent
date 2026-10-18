"""Runtime snapshot freeze 编排（ADR-0226 §2/§3/§6）。

显式冻结点（explicit_save / named_checkpoint / publish）→ 一致切口 →
ref 晋升 → 组合 workspace snapshot（账本权威）→ 原子落盘 manifest。

一致切口（consistent cut）：持有 session mutation 锁只做 (mapspec, revision,
plan) 的读 —— mutation 持同一把锁，故切口要么在任一 mutation 前、要么在
其后，绝不在中间。锁释放后才做晋升 IO（blob 写、workspace snapshot 保存）：
refs 是不可变身份（checkpoint 设计契约），切口后载荷内容不会因并发
mutation 改变；切口 revision 如实记录 —— 与切口后发生的 mutation 不混淆。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.services.project_runtime.contract import (
    CAPABILITY_LIMIT,
    DATASET_DESCRIPTOR_LIMIT,
    DatasetDescriptor,
    EXTERNAL_PINNED,
    FREEZE_REASONS,
    MAPSPEC_DOC_MAX_BYTES,
    PlanProjectionRef,
    ProjectRuntimeSnapshotV1,
    RecoveryPolicy,
    SourceBinding,
)
from app.services.project_runtime.promotion import (
    DEFAULT_FREEZE_BUDGET_BYTES,
    PromotionOutcome,
    plan_and_promote,
)
from app.services.project_runtime.rebind import (
    canonical_fingerprint,
    extract_spec_refs,
)
from app.services.project_runtime.store import (
    get_project_runtime_store,
    validate_project_id,
)

logger = logging.getLogger(__name__)


class FreezeError(Exception):
    """freeze 失败（调用方如实上报，绝不半成品落盘）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


async def _consistent_cut(
    session_id: str,
) -> Tuple[Dict[str, Any], int, Optional[Any]]:
    """锁内读取 (mapspec, revision, plan) 一致切口。"""
    from app.services.distributed_lock import session_lock_registry
    from app.services.mapspec.store import MapSpecStore
    from app.services.session_data import session_data_manager
    from app.services.session_plan import load_session_plan

    async with session_lock_registry.lock(session_id, fail_on_degraded=False):
        map_state = await session_data_manager.get_map_state(session_id)
        mapspec = await MapSpecStore().get_mapspec(session_id, state_hint=map_state)
        try:
            revision = int(map_state.get("_cartographic_mutation_revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        plan = await load_session_plan(session_id)
    return mapspec if isinstance(mapspec, dict) else {}, revision, plan


def _spec_doc_within_bounds(mapspec: Dict[str, Any]) -> bool:
    import json as _json

    try:
        size = len(_json.dumps(mapspec, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return False
    return size <= MAPSPEC_DOC_MAX_BYTES


def _plan_projection(plan: Optional[Any]) -> PlanProjectionRef:
    """plan → 有界投影引用（identity refs；绝不是 plan dump）。"""
    from app.services.gis_harness.resume_anchor import RESTORABLE_CHAPTER_KEYS
    from app.services.gis_harness.workflow_instance import rows_fingerprint

    projection = PlanProjectionRef()
    if plan is None:
        return projection
    try:
        projection.envelope_id = str(getattr(plan, "envelope_id", "") or "")[:64]
        projection.user_goal = str(getattr(plan, "user_goal", "") or "")[:512]
        projection.progress = [
            p.model_dump() for p in (plan.progress or [])[:32]
        ] if plan.progress else []
    except Exception:  # noqa: BLE001 — 投影失败按空投影（诚实降级）
        logger.debug("[RuntimeFreeze] plan projection degraded", exc_info=True)
    chapter = getattr(plan, "gis_chapter", None)
    if isinstance(chapter, dict):
        projection.restorable_chapter = {
            k: chapter[k]
            for k in RESTORABLE_CHAPTER_KEYS
            if k in chapter
        }
        try:
            rows_fp = rows_fingerprint(chapter)
            if rows_fp:
                projection.workflow_rows_fingerprint = str(rows_fp)[:2048]
        except Exception:  # noqa: BLE001 — 指纹是增值不是前提
            logger.debug(
                "[RuntimeFreeze] workflow rows fingerprint skipped", exc_info=True
            )
        # capability 词表：workflow_instance 行的 capability 字段（有界去重）。
        caps: List[str] = []
        instance = chapter.get("workflow_instance")
        if isinstance(instance, dict) and isinstance(instance.get("rows"), list):
            for row in instance["rows"]:
                if isinstance(row, dict):
                    cap = str(row.get("capability") or "").strip()
                    if cap and cap not in caps:
                        caps.append(cap)
        projection.required_capabilities = caps[:CAPABILITY_LIMIT]
    return projection


def _capability_freeze_facts(
    projection: PlanProjectionRef,
) -> Dict[str, str]:
    """冻结时 capability 运行态（恢复时比对的先验；缺席 = 空投影）。"""
    if not projection.required_capabilities:
        return {}
    try:
        from app.services.capability_runtime.snapshot import (
            build_capability_runtime_snapshot,
        )

        snap = build_capability_runtime_snapshot(
            list(projection.required_capabilities)
        )
        projection.credential_presence_fingerprint = str(
            snap.credential_presence_fingerprint or ""
        )[:128]
        return {
            str(f.capability_id)[:128]: str(f.runtime_status)[:32]
            for f in snap.capabilities
        }
    except Exception as e:  # noqa: BLE001 — capability 快照缺席按无先验
        logger.info("[RuntimeFreeze] capability facts skipped: %s", e)
        return {}


async def _collect_sensitive_refs(session_id: str) -> set:
    """账本标记 sensitive 的全部 ref（组合 lane 排除面；与 promotion planner
    的 sensitive 判定同源 —— 账本 metadata.sensitive。覆盖账本全体而不仅
    spec 源：ledger-only 契约（chart/export 等）同样不得经组合 lane 物化）。

    扫描帽与账本自身的有界性同界（``MAX_ARTIFACT_RECORDS``，即 workspace
    snapshot 的 ``_MAX_SNAPSHOT_ARTIFACTS`` 同值）—— 覆盖账本全体；一旦
    被击穿（账本上限上调而忘了同步此处），排在后位的 sensitive ledger-only
    ref 会逃过 ``exclude_refs`` 被组合 lane 物化，故截断时显式计数披露，
    绝不静默。"""
    from app.services.artifact_registry import (
        MAX_ARTIFACT_RECORDS,
        list_artifacts,
    )

    sensitive: set = set()
    try:
        records = await list_artifacts(session_id)
    except Exception:  # noqa: BLE001 — 账本缺席按无 sensitive
        return sensitive
    for rec in records[:MAX_ARTIFACT_RECORDS]:
        ref = str(rec.artifact_id or "")
        if not ref.startswith("ref:"):
            continue
        if bool((rec.metadata or {}).get("sensitive")):
            sensitive.add(ref)
    skipped = max(0, len(records) - MAX_ARTIFACT_RECORDS)
    if skipped:
        logger.warning(
            "[RuntimeFreeze] sensitive ledger scan capped: %d record(s) "
            "beyond %d not checked — late sensitive ledger-only refs may "
            "escape exclude_refs",
            skipped, MAX_ARTIFACT_RECORDS,
        )
    return sensitive


async def _dataset_descriptors(
    db: Any, project_id: str, user_id: str = "", org_id: Optional[int] = None
) -> List[DatasetDescriptor]:
    """项目挂载数据集 → 冻结描述符（version_fingerprint = 恢复比对基准）。

    以调用方身份走既有 IDOR 门（list_project_datasets 内部 get_project_with_auth）
    —— 匿名对 owned 项目拿不到 dataset 是正确行为（route 面用户已鉴权）。
    """
    import asyncio

    from app.services.project_service import ProjectService

    try:
        rows, _total = await asyncio.to_thread(
            ProjectService.list_project_datasets,
            db, project_id,
            user_id or None,
            org_id,
        )
    except Exception as e:  # noqa: BLE001 — dataset 投影失败按空（诚实）
        logger.info("[RuntimeFreeze] dataset descriptors skipped: %s", e)
        return []
    out: List[DatasetDescriptor] = []
    for row in rows[:DATASET_DESCRIPTOR_LIMIT]:
        try:
            out.append(DatasetDescriptor(
                dataset_id=str(row.id)[:64],
                name=str(row.name or "")[:128],
                version_fingerprint=str(row.version_fingerprint or "")[:64],
                source_type=str(row.source_type or "")[:32],
            ))
        except Exception:  # noqa: BLE001 — 单行失败不阻断
            continue
    return out


async def save_runtime_snapshot(
    db: Any,
    *,
    session_id: str,
    project_id: str,
    user_id: str = "",
    org_id: Optional[int] = None,
    label: str = "",
    freeze_reason: str = "explicit_save",
    map_product_version_no: Optional[int] = None,
    budget_bytes: int = DEFAULT_FREEZE_BUDGET_BYTES,
) -> ProjectRuntimeSnapshotV1:
    """显式冻结当前 session 的制图运行时 → 项目域 manifest。

    抛 ``FreezeError`` 于：无 spec 可冻结 / spec 文档超界 / 磁盘写失败。
    绝不落半成品（manifest 原子写；workspace snapshot 是独立权威文件，
    其失败不产生 runtime manifest）。
    """
    if freeze_reason not in FREEZE_REASONS:
        freeze_reason = "explicit_save"
    if not validate_project_id(project_id):
        raise FreezeError("invalid_project_id")

    mapspec, revision, plan = await _consistent_cut(session_id)
    if not mapspec:
        raise FreezeError("no_mapspec", "session has no MapSpec to freeze")
    import asyncio

    if not await asyncio.to_thread(_spec_doc_within_bounds, mapspec):
        raise FreezeError("mapspec_doc_oversized")

    # 组合账本权威：workspace snapshot（materialize=claimed —— 账本契约面；
    # spec source refs 的物化由本层 planner 负责，双面幂等 CAS 去重）。
    # 组合 lane 与 planner 共享同一预算/quota 余量（P1 review fix）：
    # save_snapshot 的 max_materialize_bytes 收紧到 min(预算, quota 余量)，
    # 组合 lane 已物化的字节从 planner 预算中扣减 —— 拒绝即全链零字节。
    from app.services.project_runtime.promotion import _quota_remaining_bytes

    quota_remaining = (
        await asyncio.to_thread(_quota_remaining_bytes, db, project_id)
        if db is not None else None
    )
    shared_budget = int(budget_bytes)
    if quota_remaining is not None:
        shared_budget = min(shared_budget, quota_remaining)
    workspace_snapshot_id = ""
    composed_bytes = 0
    sensitive_refs = await _collect_sensitive_refs(session_id)
    try:
        from app.services.workspace.snapshot import get_workspace_snapshot_service

        ws = await get_workspace_snapshot_service().save_snapshot(
            session_id,
            label=f"runtime:{label or 'freeze'}"[:96],
            project_id=project_id,
            materialize="claimed",
            owner_id=user_id or "",
            max_materialize_bytes=shared_budget,
            exclude_refs=sensitive_refs,
        )
        if ws is not None:
            workspace_snapshot_id = ws.snapshot_id
            composed_bytes = sum(
                int(p.byte_size or 0) for p in ws.durable_pointers.values()
            )
    except Exception as e:  # noqa: BLE001 — 账本权威缺席如实披露（不阻断）
        logger.warning(
            "[RuntimeFreeze] workspace snapshot compose failed: %s", e)

    spec_refs = extract_spec_refs(mapspec)
    receipt = await plan_and_promote(
        session_id, project_id, spec_refs, db=db,
        budget_bytes=max(0, shared_budget - composed_bytes),
    )
    source_bindings: List[SourceBinding] = []
    for source_id, kind, ref in spec_refs:
        # planner 的 outcome 键约定：有 ref 键 ref；inline/外部源键 source_id。
        outcome = receipt.outcomes.get(ref) if ref else None
        if outcome is None:
            outcome = receipt.outcomes.get(source_id)
        if outcome is None:
            outcome = PromotionOutcome(
                promotion_class=EXTERNAL_PINNED, skip_reason="not_session_ref"
            )
        source_bindings.append(outcome.to_binding(source_id, kind, ref))

    projection = _plan_projection(plan)
    capability_statuses = _capability_freeze_facts(projection)
    projection.capability_statuses = dict(
        list(capability_statuses.items())[:CAPABILITY_LIMIT]
    )
    datasets = await _dataset_descriptors(
        db, project_id, user_id=user_id, org_id=org_id
    )

    snapshot = ProjectRuntimeSnapshotV1(
        snapshot_id=f"prs-{uuid.uuid4().hex[:16]}",
        project_id=project_id,
        session_id=session_id,
        created_by=str(user_id or "")[:64],
        created_at=time.time(),
        freeze_reason=freeze_reason,
        label=str(label)[:96],
        workspace_snapshot_id=workspace_snapshot_id,
        map_product_version_no=map_product_version_no,
        mapspec_doc=mapspec,
        mapspec_fingerprint=await asyncio.to_thread(
            canonical_fingerprint, mapspec),

        mapspec_mutation_revision=revision,
        source_bindings=source_bindings,
        datasets=datasets,
        plan=projection,
        policy=RecoveryPolicy(),
        disclosure={
            "promotion": receipt.summary(),
            "workspace_snapshot_composed": bool(workspace_snapshot_id),
        },
    )
    if not await get_project_runtime_store().save(snapshot):
        raise FreezeError("manifest_write_failed")
    return snapshot
