"""Durable Swarm bridge — persist/settle swarm tasks under a Mission.

Does not schedule specialists; wraps existing SwarmOrchestrator settle
points so process restart can reconstruct unfinished work.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional

from app.services.mission_runtime import contracts as C
from app.services.mission_runtime.store import MissionStore, new_swarm_run_id

logger = logging.getLogger(__name__)


def _receipt_from_swarm(
    task_id: str,
    *,
    assignment_id: str = "",
    status: str = "succeeded",
    produced_refs: Optional[List[str]] = None,
    summary: str = "",
    error_code: str = "",
    attempt: int = 0,
    side_effect: str = "pure",
    idempotency_key: str = "",
) -> C.SwarmTaskReceipt:
    op = C.operation_class_from_side_effect(side_effect)
    st_map = {
        "succeeded": C.SwarmTaskDurableState.SUCCEEDED,
        "success": C.SwarmTaskDurableState.SUCCEEDED,
        "failed": C.SwarmTaskDurableState.FAILED,
        "failure": C.SwarmTaskDurableState.FAILED,
        # degraded/partial must NOT become SKIPPED→recovery COMPLETED (#1323).
        "degraded": C.SwarmTaskDurableState.FAILED,
        "partial": C.SwarmTaskDurableState.FAILED,
        "skipped": C.SwarmTaskDurableState.SKIPPED,
        "cancelled": C.SwarmTaskDurableState.CANCELLED,
        "canceled": C.SwarmTaskDurableState.CANCELLED,
        "running": C.SwarmTaskDurableState.RUNNING,
        "pending": C.SwarmTaskDurableState.PENDING,
        "ready": C.SwarmTaskDurableState.READY,
        "unresolved": C.SwarmTaskDurableState.UNRESOLVED,
    }
    state = st_map.get(str(status).lower(), C.SwarmTaskDurableState.FAILED)
    # Destructive RUNNING stays RUNNING in the ledger; recovery maps it to
    # UNRESOLVED so we never blind-rerun. Callers may also settle unresolved.
    return C.SwarmTaskReceipt(
        task_id=task_id,
        assignment_id=assignment_id or "",
        state=state,
        operation_class=op,
        produced_refs=list(produced_refs or []),
        summary=summary or "",
        error_code=error_code or "",
        attempt=int(attempt or 0),
        idempotency_key=idempotency_key or assignment_id or task_id,
        settled_at=time.time(),
    )


class DurableSwarmBridge:
    """Bridge Mission ledger ↔ Specialist Swarm receipts."""

    def __init__(self, store: Optional[MissionStore] = None) -> None:
        self.store = store or MissionStore()

    def begin_run(
        self,
        mission_id: str,
        *,
        org_id: str,
        goal_slice: str = "",
        task_descriptors: Optional[Iterable[Dict[str, Any]]] = None,
        lease_epoch: int = 0,
        owner: str = "",
    ) -> C.SwarmRunDurable:
        tasks: Dict[str, Dict[str, Any]] = {}
        for desc in task_descriptors or []:
            tid = str(desc.get("task_id") or "").strip()
            if not tid:
                continue
            side = str(desc.get("side_effect") or "pure")
            tasks[tid] = C.SwarmTaskReceipt(
                task_id=tid,
                state=C.SwarmTaskDurableState.PENDING,
                operation_class=C.operation_class_from_side_effect(side),
                idempotency_key=str(desc.get("idempotency_key") or tid),
            ).model_dump(mode="json")
        run = self.store.create_swarm_run(
            mission_id,
            org_id=org_id,
            goal_slice=goal_slice,
            tasks=tasks,
            swarm_run_id=new_swarm_run_id(),
        )
        # Attach ref onto mission when fencing credentials provided.
        if lease_epoch and owner:
            rec = self.store.get_mission(mission_id)
            if rec is not None:
                refs = rec.refs.model_dump(mode="json")
                swarm_refs = list(refs.get("swarm_run_refs") or [])
                if run.swarm_run_id not in swarm_refs:
                    swarm_refs = (swarm_refs + [run.swarm_run_id])[: C.MAX_REFS]
                    refs["swarm_run_refs"] = swarm_refs
                try:
                    self.store.patch_mission(
                        mission_id,
                        lease_epoch=lease_epoch,
                        owner=owner,
                        refs=refs,
                    )
                except Exception:  # noqa: BLE001 — bridge must not kill swarm
                    logger.warning(
                        "[DurableSwarmBridge] attach swarm ref failed mission=%s",
                        mission_id, exc_info=True)
        return run

    def mark_running(
        self, swarm_run_id: str, task_id: str, *, assignment_id: str = "",
        side_effect: str = "pure",
    ) -> C.SwarmRunDurable:
        receipt = _receipt_from_swarm(
            task_id,
            assignment_id=assignment_id,
            status="running",
            side_effect=side_effect,
        )
        return self.store.settle_swarm_task(swarm_run_id, receipt)

    def settle(
        self,
        swarm_run_id: str,
        *,
        task_id: str,
        status: str,
        assignment_id: str = "",
        produced_refs: Optional[List[str]] = None,
        summary: str = "",
        error_code: str = "",
        attempt: int = 0,
        side_effect: str = "pure",
        idempotency_key: str = "",
    ) -> C.SwarmRunDurable:
        receipt = _receipt_from_swarm(
            task_id,
            assignment_id=assignment_id,
            status=status,
            produced_refs=produced_refs,
            summary=summary,
            error_code=error_code,
            attempt=attempt,
            side_effect=side_effect,
            idempotency_key=idempotency_key,
        )
        return self.store.settle_swarm_task(swarm_run_id, receipt)

    def settle_from_subagent_receipt(
        self,
        swarm_run_id: str,
        *,
        task_id: str,
        receipt: Any,
        side_effect: str = "pure",
    ) -> C.SwarmRunDurable:
        """Normalize a live SubagentReceipt-like object into durable settle."""
        status = getattr(receipt, "status", None)
        if hasattr(status, "value"):
            status = status.value
        status = str(status or "failed")
        produced = list(getattr(receipt, "produced_refs", None) or [])
        return self.settle(
            swarm_run_id,
            task_id=task_id,
            status=status,
            assignment_id=str(getattr(receipt, "assignment_id", "") or ""),
            produced_refs=produced,
            summary=str(getattr(receipt, "summary", "") or ""),
            error_code=str(getattr(receipt, "error_code", "") or ""),
            attempt=int(getattr(receipt, "attempt", 0) or 0),
            side_effect=side_effect,
        )

    def collect_artifact_refs(self, swarm_run_id: str) -> List[str]:
        run = self.store.get_swarm_run(swarm_run_id)
        if run is None:
            return []
        refs: List[str] = []
        for receipt in run.tasks.values():
            if receipt.state == C.SwarmTaskDurableState.SUCCEEDED:
                for r in receipt.produced_refs:
                    if r not in refs:
                        refs.append(r)
        return refs[: C.MAX_REFS]
