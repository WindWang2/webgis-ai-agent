"""Mission recovery coordinator (ADR-0197).

scan unfinished → validate refs → classify frontier → verify leases →
resume only incomplete work. Uncertainty never becomes success.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from app.services.mission_runtime import contracts as C
from app.services.mission_runtime.store import (
    DEFAULT_LEASE_TTL_S,
    FencingError,
    MissionStore,
    TransitionRejected,
)

logger = logging.getLogger(__name__)


def classify_swarm_task(receipt: C.SwarmTaskReceipt) -> C.RecoveryClass:
    st = receipt.state
    if st in (
        C.SwarmTaskDurableState.SUCCEEDED,
        C.SwarmTaskDurableState.SKIPPED,
    ):
        return C.RecoveryClass.COMPLETED
    if st == C.SwarmTaskDurableState.CANCELLED:
        return C.RecoveryClass.CANCELLED
    if st == C.SwarmTaskDurableState.UNRESOLVED:
        return C.RecoveryClass.DESTRUCTIVE_UNKNOWN
    if st == C.SwarmTaskDurableState.FAILED:
        if receipt.operation_class == C.OperationClass.DESTRUCTIVE_AT_MOST_ONCE:
            return C.RecoveryClass.DESTRUCTIVE_UNKNOWN
        if receipt.operation_class in (
            C.OperationClass.PURE,
            C.OperationClass.IDEMPOTENT,
            C.OperationClass.REPEATABLE,
        ):
            return C.RecoveryClass.RETRYABLE_FAILURE
        return C.RecoveryClass.NON_RETRYABLE_FAILURE
    if st == C.SwarmTaskDurableState.RUNNING:
        return C.RecoveryClass.RUNNING_OWNER_DEAD
    return C.RecoveryClass.RETRYABLE_FAILURE


def build_resume_plan(swarm: C.SwarmRunDurable) -> Dict[str, Any]:
    """Derive which tasks to skip / retry / leave unresolved."""
    skip: List[str] = []
    retry: List[str] = []
    unresolved: List[str] = []
    cancelled: List[str] = []
    for tid, receipt in swarm.tasks.items():
        klass = classify_swarm_task(receipt)
        if klass == C.RecoveryClass.COMPLETED:
            skip.append(tid)
        elif klass == C.RecoveryClass.CANCELLED:
            cancelled.append(tid)
        elif klass == C.RecoveryClass.DESTRUCTIVE_UNKNOWN:
            unresolved.append(tid)
        elif klass in (
            C.RecoveryClass.RETRYABLE_FAILURE,
            C.RecoveryClass.RUNNING_OWNER_DEAD,
        ):
            # destructive running → unresolved (never blind rerun)
            if (
                receipt.operation_class == C.OperationClass.DESTRUCTIVE_AT_MOST_ONCE
                and receipt.state == C.SwarmTaskDurableState.RUNNING
            ):
                unresolved.append(tid)
            else:
                retry.append(tid)
        else:
            unresolved.append(tid)
    return {
        "swarm_run_id": swarm.swarm_run_id,
        "skip": sorted(skip),
        "retry": sorted(retry),
        "unresolved": sorted(unresolved),
        "cancelled": sorted(cancelled),
    }


class MissionRecoveryCoordinator:
    """Deterministic startup/recovery for unfinished Missions."""

    def __init__(self, store: Optional[MissionStore] = None) -> None:
        self.store = store or MissionStore()

    def scan(self, *, org_id: Optional[str] = None) -> List[C.MissionRecord]:
        return self.store.list_unfinished(org_id=org_id)

    def inspect(self, mission_id: str, *, org_id: Optional[str] = None) -> Dict[str, Any]:
        rec = self.store.get_mission(mission_id, org_id=org_id)
        if rec is None:
            return {"ok": False, "reason": "NOT_FOUND"}
        lease_alive = (
            rec.lease_expires_at > time.time() and bool(rec.lease_owner)
        )
        swarms = self.store.list_swarm_runs_for_mission(mission_id)
        plans = [build_resume_plan(s) for s in swarms]
        cp = self.store.latest_checkpoint(mission_id)
        owner_dead = bool(rec.lease_owner) and not lease_alive
        return {
            "ok": True,
            "mission_id": mission_id,
            "state": rec.state.value,
            "revision": rec.revision,
            "lease_alive": lease_alive,
            "owner_dead": owner_dead,
            "lease_owner": rec.lease_owner,
            "lease_epoch": rec.lease_epoch,
            "frontier": rec.frontier.model_dump(),
            "checkpoint": (cp or {}).get("checkpoint_id", ""),
            "swarm_plans": plans,
            "resource_exhausted": rec.resource_budget.exhausted(),
        }

    def recover(
        self,
        mission_id: str,
        *,
        worker_id: str,
        org_id: Optional[str] = None,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
    ) -> Dict[str, Any]:
        """Acquire lease (if free/expired), enter recovering, apply resume plan.

        Never marks destructive-unknown work as completed.
        """
        inspection = self.inspect(mission_id, org_id=org_id)
        if not inspection.get("ok"):
            return inspection
        if inspection.get("lease_alive") and inspection.get("lease_owner") != worker_id:
            return {
                "ok": False,
                "reason": "LEASE_HELD",
                "lease_owner": inspection.get("lease_owner"),
            }

        acquired = self.store.acquire_lease(
            mission_id, owner=worker_id, ttl_s=ttl_s, org_id=org_id)
        if acquired is None:
            return {"ok": False, "reason": "LEASE_ACQUIRE_FAILED"}
        epoch, expires = acquired

        try:
            rec = self.store.get_mission(mission_id, org_id=org_id)
            assert rec is not None
            if rec.state != C.MissionState.RECOVERING:
                # only leave terminal alone
                if C.is_terminal(rec.state):
                    self.store.release_lease(
                        mission_id, owner=worker_id, lease_epoch=epoch)
                    return {"ok": False, "reason": "ALREADY_TERMINAL",
                            "state": rec.state.value}
                # created/planning may go via recovering only from suspended/running paths;
                # allow direct recover from active non-terminal via whitelist
                to = C.MissionState.RECOVERING
                if not C.transition_allowed(rec.state, to):
                    # force path: suspend first if needed
                    if C.transition_allowed(rec.state, C.MissionState.SUSPENDED):
                        rec = self.store.transition(
                            mission_id, to_state=C.MissionState.SUSPENDED.value,
                            lease_epoch=epoch, owner=worker_id)
                    if not C.transition_allowed(rec.state, to):
                        return {
                            "ok": False,
                            "reason": f"CANNOT_ENTER_RECOVERING_FROM_{rec.state.value}",
                            "lease_epoch": epoch,
                        }
                rec = self.store.transition(
                    mission_id,
                    to_state=C.MissionState.RECOVERING.value,
                    lease_epoch=epoch,
                    owner=worker_id,
                    recovery_patch={
                        "attempt": int(rec.recovery.attempt) + 1,
                        "last_recovery_at": time.time(),
                    },
                )

            # Mark destructive running tasks as UNRESOLVED (never blind rerun)
            unresolved_all: List[str] = []
            resume_plans = []
            for swarm in self.store.list_swarm_runs_for_mission(mission_id):
                plan = build_resume_plan(swarm)
                for tid in plan["unresolved"]:
                    receipt = swarm.tasks[tid]
                    if receipt.state != C.SwarmTaskDurableState.UNRESOLVED:
                        marked = receipt.model_copy(update={
                            "state": C.SwarmTaskDurableState.UNRESOLVED,
                            "error_code": receipt.error_code or "DESTRUCTIVE_UNKNOWN",
                        })
                        self.store.settle_swarm_task(swarm.swarm_run_id, marked)
                unresolved_all.extend(plan["unresolved"])
                resume_plans.append(plan)

            # Rebuild frontier from plans
            completed = sorted({t for p in resume_plans for t in p["skip"]})
            pending = sorted({t for p in resume_plans for t in p["retry"]})
            failed = sorted(set(unresolved_all))
            frontier = C.MissionFrontier(
                completed=completed,
                pending=pending,
                failed=failed,
                running=[],
                blocked=failed,
            )
            recovery = rec.recovery.model_dump()
            recovery["unresolved_ops"] = failed[: C.MAX_FRONTIER]
            recovery["blocked_reason"] = (
                "destructive_unknown" if failed else "")
            recovery["attempt"] = int(recovery.get("attempt") or 0)
            recovery["last_recovery_at"] = time.time()

            rec = self.store.patch_mission(
                mission_id,
                lease_epoch=epoch,
                owner=worker_id,
                frontier=frontier.model_dump(),
                recovery_state=recovery,
            )

            # Resume to running if there is retryable work and no hard block;
            # otherwise partially_complete / waiting.
            if failed and not pending:
                next_state = C.MissionState.PARTIALLY_COMPLETE
            elif pending:
                next_state = C.MissionState.RUNNING
            elif completed and not pending and not failed:
                next_state = C.MissionState.COMPLETE
            else:
                next_state = C.MissionState.PARTIALLY_COMPLETE

            if C.transition_allowed(rec.state, next_state) or rec.state == next_state:
                if rec.state != next_state:
                    rec = self.store.transition(
                        mission_id,
                        to_state=next_state.value,
                        lease_epoch=epoch,
                        owner=worker_id,
                    )

            return {
                "ok": True,
                "mission_id": mission_id,
                "lease_epoch": epoch,
                "lease_expires_at": expires,
                "state": rec.state.value,
                "frontier": rec.frontier.model_dump(),
                "resume_plans": resume_plans,
                "unresolved": failed,
            }
        except (FencingError, TransitionRejected) as exc:
            logger.warning(
                "[MissionRecovery] recover failed mission=%s err=%s",
                mission_id, exc)
            return {"ok": False, "reason": str(exc), "lease_epoch": epoch}
