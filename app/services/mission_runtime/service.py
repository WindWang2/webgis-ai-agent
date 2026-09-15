"""MissionRuntimeService — facade for durable GIS Mission lifecycle (ADR-0197)."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.services.mission_runtime import contracts as C
from app.services.mission_runtime.artifacts import MissionArtifactOwnership
from app.services.mission_runtime.recovery import MissionRecoveryCoordinator
from app.services.mission_runtime.resources import MissionResourceLedger
from app.services.mission_runtime.store import (
    DEFAULT_LEASE_TTL_S,
    FencingError,
    MissionStore,
)
from app.services.mission_runtime.swarm_bridge import DurableSwarmBridge

logger = logging.getLogger(__name__)


def mission_runtime_enabled() -> bool:
    return os.getenv("GIS_MISSION_RUNTIME", "1") not in ("0", "false", "False")


class MissionRuntimeService:
    """Primary API for Mission create/start/suspend/resume/cancel/complete."""

    def __init__(self, store: Optional[MissionStore] = None) -> None:
        self.store = store or MissionStore()
        self.recovery = MissionRecoveryCoordinator(self.store)
        self.swarm = DurableSwarmBridge(self.store)
        self.resources = MissionResourceLedger(self.store)
        self.artifacts = MissionArtifactOwnership(self.store)

    # ── lifecycle ───────────────────────────────────────────────────────

    def create(
        self,
        *,
        org_id: str,
        user_id: str = "",
        project_id: Optional[str] = None,
        root_goal: str = "",
        session_id: str = "",
        quota: Optional[Dict[str, float]] = None,
    ) -> C.MissionRecord:
        return self.store.create_mission(
            org_id=org_id,
            user_id=user_id,
            project_id=project_id,
            root_goal=root_goal,
            session_id=session_id,
            quota=quota,
        )

    def _require_lease(
        self, mission_id: str, worker_id: str, *, ttl_s: float = DEFAULT_LEASE_TTL_S,
        org_id: Optional[str] = None,
    ) -> int:
        got = self.store.acquire_lease(
            mission_id, owner=worker_id, ttl_s=ttl_s, org_id=org_id)
        if got is None:
            raise FencingError("LEASE_ACQUIRE_FAILED")
        return got[0]

    def start(
        self, mission_id: str, *, worker_id: str, org_id: Optional[str] = None,
    ) -> C.MissionRecord:
        epoch = self._require_lease(mission_id, worker_id, org_id=org_id)
        rec = self.store.get_mission(mission_id, org_id=org_id)
        assert rec is not None
        if rec.state == C.MissionState.CREATED:
            rec = self.store.transition(
                mission_id, to_state=C.MissionState.PLANNING.value,
                lease_epoch=epoch, owner=worker_id)
        if rec.state == C.MissionState.PLANNING:
            rec = self.store.transition(
                mission_id, to_state=C.MissionState.RUNNING.value,
                lease_epoch=epoch, owner=worker_id)
        self.store.write_checkpoint(
            mission_id, lease_epoch=epoch, owner=worker_id, note="start")
        return rec

    def suspend(
        self, mission_id: str, *, worker_id: str, org_id: Optional[str] = None,
    ) -> C.MissionRecord:
        epoch = self._require_lease(mission_id, worker_id, org_id=org_id)
        self.store.write_checkpoint(
            mission_id, lease_epoch=epoch, owner=worker_id, note="suspend")
        rec = self.store.transition(
            mission_id, to_state=C.MissionState.SUSPENDED.value,
            lease_epoch=epoch, owner=worker_id)
        # Suspended missions must not pin a live owner — allow another worker
        # to resume without waiting for TTL expiry.
        self.store.release_lease(mission_id, owner=worker_id, lease_epoch=epoch)
        return rec

    def resume(
        self, mission_id: str, *, worker_id: str, org_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.recovery.recover(
            mission_id, worker_id=worker_id, org_id=org_id)

    def cancel(
        self, mission_id: str, *, worker_id: str, org_id: Optional[str] = None,
    ) -> C.MissionRecord:
        epoch = self._require_lease(mission_id, worker_id, org_id=org_id)
        return self.store.transition(
            mission_id, to_state=C.MissionState.CANCELLED.value,
            lease_epoch=epoch, owner=worker_id)

    def complete(
        self, mission_id: str, *, worker_id: str, org_id: Optional[str] = None,
    ) -> C.MissionRecord:
        epoch = self._require_lease(mission_id, worker_id, org_id=org_id)
        # Checkpoint while lease still held; terminal transition clears lease.
        self.store.write_checkpoint(
            mission_id, lease_epoch=epoch, owner=worker_id, note="complete")
        return self.store.transition(
            mission_id, to_state=C.MissionState.COMPLETE.value,
            lease_epoch=epoch, owner=worker_id)

    def heartbeat(
        self, mission_id: str, *, worker_id: str, lease_epoch: int,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
    ) -> bool:
        return self.store.heartbeat_lease(
            mission_id, owner=worker_id, lease_epoch=lease_epoch, ttl_s=ttl_s)

    # ── goal continuity ─────────────────────────────────────────────────

    def revise_goal(
        self,
        mission_id: str,
        *,
        worker_id: str,
        new_goal: str,
        invalidate_task_ids: Optional[List[str]] = None,
        retain_artifact_refs: Optional[List[str]] = None,
        org_id: Optional[str] = None,
    ) -> C.MissionRecord:
        """Bump goal_revision; invalidate affected frontier only; keep valid refs."""
        epoch = self._require_lease(mission_id, worker_id, org_id=org_id)
        rec = self.store.get_mission(mission_id, org_id=org_id)
        assert rec is not None
        frontier = rec.frontier.model_dump()
        inv = set(invalidate_task_ids or [])
        if inv:
            # Drop invalidated ids from completed/running; queue for re-exec.
            for key in ("completed", "running", "failed", "blocked"):
                frontier[key] = [t for t in frontier.get(key) or [] if t not in inv]
            pending = [t for t in frontier.get("pending") or [] if t not in inv]
            for t in sorted(inv):
                pending.append(t)
            frontier["pending"] = pending[: C.MAX_FRONTIER]
        refs = rec.refs.model_dump()
        if retain_artifact_refs is not None:
            keep = set(retain_artifact_refs)
            refs["artifact_refs"] = [
                r for r in (refs.get("artifact_refs") or []) if r in keep
            ][: C.MAX_REFS]
        return self.store.patch_mission(
            mission_id,
            lease_epoch=epoch,
            owner=worker_id,
            root_goal=new_goal,
            bump_goal_revision=True,
            frontier=frontier,
            refs=refs,
        )

    def attach_session(
        self, mission_id: str, *, worker_id: str, session_id: str,
        session_plan_ref: str = "",
    ) -> C.MissionRecord:
        epoch = self._require_lease(mission_id, worker_id)
        rec = self.store.get_mission(mission_id)
        assert rec is not None
        refs = rec.refs.model_dump()
        sessions = list(refs.get("active_session_ids") or [])
        if session_id and session_id not in sessions:
            sessions = (sessions + [session_id])[: C.MAX_SESSIONS]
            refs["active_session_ids"] = sessions
        if session_plan_ref:
            plans = list(refs.get("session_plan_refs") or [])
            if session_plan_ref not in plans:
                plans = (plans + [session_plan_ref])[: C.MAX_REFS]
                refs["session_plan_refs"] = plans
        return self.store.patch_mission(
            mission_id, lease_epoch=epoch, owner=worker_id, refs=refs)

    # ── diagnostics ─────────────────────────────────────────────────────

    def diagnostics(self, mission_id: str, *, org_id: Optional[str] = None) -> C.MissionDiagnostics:
        rec = self.store.get_mission(mission_id, org_id=org_id)
        if rec is None:
            return C.MissionDiagnostics(mission_id=mission_id, state="missing")
        swarms = self.store.list_swarm_runs_for_mission(mission_id)
        swarm_status = swarms[-1].state if swarms else ""
        return C.MissionDiagnostics(
            mission_id=mission_id,
            goal=rec.root_goal[:200],
            state=rec.state.value,
            revision=rec.revision,
            goal_revision=rec.goal_revision,
            current_frontier=rec.frontier,
            blocked_reason=rec.recovery.blocked_reason,
            artifact_count=len(rec.refs.artifact_refs),
            swarm_status=swarm_status,
            resource_use=dict(rec.resource_budget.consumed),
            last_checkpoint=rec.recovery.last_checkpoint_id,
            lease_owner=rec.lease_owner,
            lease_epoch=rec.lease_epoch,
            recovery_count=rec.recovery.attempt,
        )


_SERVICE: Optional[MissionRuntimeService] = None


def get_mission_runtime(store: Optional[MissionStore] = None) -> MissionRuntimeService:
    global _SERVICE
    if store is not None:
        return MissionRuntimeService(store=store)
    if _SERVICE is None:
        _SERVICE = MissionRuntimeService()
    return _SERVICE


def reset_mission_runtime_for_tests() -> None:
    global _SERVICE
    _SERVICE = None
