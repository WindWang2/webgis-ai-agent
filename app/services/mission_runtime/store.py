"""Mission durable ledger — DB persistence + lease_epoch fencing (ADR-0197).

Mirrors GeoCompute cluster fencing: every mutating write CAS-checks
``lease_epoch`` (and ``revision`` where applicable). Stale owners fail closed.
"""
from __future__ import annotations

import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from app.models.mission import (
    GISMissionCheckpointRow,
    GISMissionRow,
    GISMissionSwarmRunRow,
)
from app.services.mission_runtime import contracts as C

logger = logging.getLogger(__name__)

DEFAULT_LEASE_TTL_S = 30.0
MAX_CHECKPOINT_RING = C.MAX_CHECKPOINTS_RING


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(dt: Optional[datetime]) -> float:
    if dt is None:
        return 0.0
    if dt.tzinfo is not None:
        return dt.timestamp()
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _from_ts(ts: float) -> Optional[datetime]:
    if not ts:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).replace(tzinfo=None)


def new_mission_id() -> str:
    return f"msn-{uuid.uuid4().hex[:16]}"


def new_checkpoint_id(mission_id: str, revision: int) -> str:
    return f"mcp-{mission_id}-r{revision}-{secrets.token_hex(3)}"


def new_swarm_run_id() -> str:
    return f"swr-{uuid.uuid4().hex[:14]}"


class StoreUnavailable(RuntimeError):
    """DB busy / unavailable — callers must not invent success."""


class FencingError(RuntimeError):
    """Stale lease_epoch — writer must stop committing."""


class TransitionRejected(RuntimeError):
    """Illegal MissionState transition or CAS conflict."""


def _row_to_record(row: GISMissionRow) -> C.MissionRecord:
    refs_raw = row.refs if isinstance(row.refs, dict) else {}
    return C.MissionRecord(
        schema_version=row.schema_version or C.SCHEMA_VERSION,
        mission_id=row.mission_id,
        org_id=row.org_id,
        user_id=row.user_id or "",
        project_id=row.project_id,
        owner_scope=row.owner_scope or "",
        root_goal=row.root_goal or "",
        goal_revision=int(row.goal_revision or 1),
        state=C.MissionState(row.state),
        revision=int(row.revision or 1),
        created_at=_ts(row.created_at),
        updated_at=_ts(row.updated_at),
        refs=C.MissionRefs(**{
            k: list(refs_raw.get(k) or [])
            for k in (
                "active_session_ids", "session_plan_refs",
                "workflow_instance_refs", "swarm_run_refs",
                "artifact_refs", "map_product_refs", "evidence_refs",
            )
        }),
        frontier=C.MissionFrontier(**(row.frontier if isinstance(row.frontier, dict) else {})),
        resource_budget=C.MissionResourceBudget(
            **(row.resource_budget if isinstance(row.resource_budget, dict) else {})
        ),
        failure=C.MissionFailureState(
            **(row.failure_state if isinstance(row.failure_state, dict) else {})
        ),
        recovery=C.MissionRecoveryState(
            **(row.recovery_state if isinstance(row.recovery_state, dict) else {})
        ),
        lease_owner=row.lease_owner or "",
        lease_epoch=int(row.lease_epoch or 0),
        lease_expires_at=_ts(row.lease_expires_at),
    )


class MissionStore:
    """Durable Mission ledger (injectable session factory for hermetic tests)."""

    def __init__(self, factory: Optional[Callable] = None) -> None:
        self._factory = factory

    def _sf(self):
        if self._factory is not None:
            return self._factory()
        from app.core.database import SessionLocal
        return SessionLocal()

    # ── create / get / list ─────────────────────────────────────────────

    def create_mission(
        self,
        *,
        org_id: str,
        user_id: str = "",
        project_id: Optional[str] = None,
        owner_scope: str = "",
        root_goal: str = "",
        session_id: str = "",
        quota: Optional[Dict[str, float]] = None,
        mission_id: Optional[str] = None,
    ) -> C.MissionRecord:
        mid = mission_id or new_mission_id()
        now = _utcnow()
        refs: Dict[str, List[str]] = {
            "active_session_ids": [session_id] if session_id else [],
            "session_plan_refs": [],
            "workflow_instance_refs": [],
            "swarm_run_refs": [],
            "artifact_refs": [],
            "map_product_refs": [],
            "evidence_refs": [],
        }
        budget = C.MissionResourceBudget(quota=dict(quota or {})).model_dump()
        row = GISMissionRow(
            mission_id=mid,
            org_id=str(org_id),
            user_id=str(user_id or ""),
            project_id=project_id,
            owner_scope=owner_scope or f"u:{user_id or 'anon'}",
            root_goal=str(root_goal or "")[: C.MAX_GOAL_CHARS],
            goal_revision=1,
            state=C.MissionState.CREATED.value,
            revision=1,
            schema_version=C.SCHEMA_VERSION,
            refs=refs,
            frontier={},
            resource_budget=budget,
            failure_state={},
            recovery_state={},
            lease_epoch=0,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._sf() as db:
                db.add(row)
                db.commit()
                db.refresh(row)
                return _row_to_record(row)
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def get_mission(
        self, mission_id: str, *, org_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[C.MissionRecord]:
        """Read one mission.

        ``user_id`` (SEC-04): when provided, non-admin callers only see
        missions attributed to that user — org alone is not an authz boundary.
        Internal/system callers omit it (no user predicate).
        """
        try:
            with self._sf() as db:
                q = db.query(GISMissionRow).filter(
                    GISMissionRow.mission_id == mission_id)
                if org_id is not None:
                    q = q.filter(GISMissionRow.org_id == str(org_id))
                if user_id is not None:
                    q = q.filter(GISMissionRow.user_id == str(user_id))
                row = q.first()
                return _row_to_record(row) if row else None
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def list_unfinished(
        self, *, org_id: Optional[str] = None, limit: int = 100,
        user_id: Optional[str] = None,
    ) -> List[C.MissionRecord]:
        active = [s.value for s in C.ACTIVE_STATES]
        try:
            with self._sf() as db:
                q = db.query(GISMissionRow).filter(
                    GISMissionRow.state.in_(active))
                if org_id is not None:
                    q = q.filter(GISMissionRow.org_id == str(org_id))
                if user_id is not None:
                    q = q.filter(GISMissionRow.user_id == str(user_id))
                rows = (
                    q.order_by(GISMissionRow.updated_at.asc())
                    .limit(max(1, min(int(limit), 500)))
                    .all()
                )
                return [_row_to_record(r) for r in rows]
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    # ── lease / fencing ─────────────────────────────────────────────────

    def acquire_lease(
        self,
        mission_id: str,
        *,
        owner: str,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
        org_id: Optional[str] = None,
    ) -> Optional[Tuple[int, float]]:
        """Acquire or renew Mission lease.

        Returns ``(lease_epoch, expires_at_ts)`` on success, else ``None``.
        New acquire (different owner or expired) bumps ``lease_epoch``.
        Same owner renew does **not** bump epoch (heartbeat).
        """
        now = _utcnow()
        expires = now + timedelta(seconds=max(1.0, float(ttl_s)))
        owner_tok = str(owner or "")[:128]
        if not owner_tok:
            return None
        try:
            with self._sf() as db:
                q = db.query(GISMissionRow).filter(
                    GISMissionRow.mission_id == mission_id)
                if org_id is not None:
                    q = q.filter(GISMissionRow.org_id == str(org_id))
                row = q.first()
                if row is None:
                    return None
                if C.is_terminal(row.state):
                    return None
                held = row.lease_expires_at or datetime.min
                same = (row.lease_owner or "") == owner_tok
                if held > now and not same:
                    return None
                new_epoch = int(row.lease_epoch or 0)
                if not same:
                    new_epoch += 1
                updated = db.execute(
                    sa.update(GISMissionRow)
                    .where(
                        GISMissionRow.mission_id == mission_id,
                        GISMissionRow.revision == row.revision,
                        GISMissionRow.lease_epoch == row.lease_epoch,
                    )
                    .values(
                        lease_owner=owner_tok,
                        lease_epoch=new_epoch,
                        lease_expires_at=expires,
                        heartbeat_at=now,
                        revision=int(row.revision) + 1,
                        updated_at=now,
                    )
                )
                db.commit()
                if not updated.rowcount:
                    return None
                return new_epoch, _ts(expires)
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def heartbeat_lease(
        self,
        mission_id: str,
        *,
        owner: str,
        lease_epoch: int,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
    ) -> bool:
        """Extend lease only if owner+epoch still match (fencing)."""
        now = _utcnow()
        expires = now + timedelta(seconds=max(1.0, float(ttl_s)))
        try:
            with self._sf() as db:
                updated = db.execute(
                    sa.update(GISMissionRow)
                    .where(
                        GISMissionRow.mission_id == mission_id,
                        GISMissionRow.lease_owner == str(owner)[:128],
                        GISMissionRow.lease_epoch == int(lease_epoch),
                    )
                    .values(
                        lease_expires_at=expires,
                        heartbeat_at=now,
                        updated_at=now,
                    )
                )
                db.commit()
                return bool(updated.rowcount)
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def release_lease(
        self, mission_id: str, *, owner: str, lease_epoch: int,
    ) -> bool:
        try:
            with self._sf() as db:
                updated = db.execute(
                    sa.update(GISMissionRow)
                    .where(
                        GISMissionRow.mission_id == mission_id,
                        GISMissionRow.lease_owner == str(owner)[:128],
                        GISMissionRow.lease_epoch == int(lease_epoch),
                    )
                    .values(
                        lease_owner=None,
                        lease_expires_at=None,
                        updated_at=_utcnow(),
                    )
                )
                db.commit()
                return bool(updated.rowcount)
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    # ── CAS mutations ───────────────────────────────────────────────────

    def transition(
        self,
        mission_id: str,
        *,
        to_state: str,
        lease_epoch: int,
        owner: str,
        expected_revision: Optional[int] = None,
        failure: Optional[Dict[str, Any]] = None,
        recovery_patch: Optional[Dict[str, Any]] = None,
    ) -> C.MissionRecord:
        """State transition under fencing + optional revision CAS."""
        dest = C.MissionState(to_state)
        now = _utcnow()
        # #1407: empty owner must never fence-match an unleased row.
        if not str(owner or "").strip():
            raise FencingError("EMPTY_LEASE_OWNER")
        try:
            with self._sf() as db:
                row = db.query(GISMissionRow).filter(
                    GISMissionRow.mission_id == mission_id).first()
                if row is None:
                    raise TransitionRejected("MISSION_NOT_FOUND")
                if int(row.lease_epoch or 0) != int(lease_epoch):
                    raise FencingError("STALE_LEASE_EPOCH")
                if (row.lease_owner or "") != str(owner)[:128]:
                    raise FencingError("STALE_LEASE_OWNER")
                if expected_revision is not None and int(row.revision) != int(expected_revision):
                    raise TransitionRejected("REVISION_CONFLICT")
                cur = C.MissionState(row.state)
                if cur == dest:
                    return _row_to_record(row)  # idempotent
                if not C.transition_allowed(cur, dest):
                    raise TransitionRejected(
                        f"ILLEGAL_TRANSITION:{cur.value}->{dest.value}")
                values: Dict[str, Any] = {
                    "state": dest.value,
                    "revision": int(row.revision) + 1,
                    "updated_at": now,
                }
                if C.is_terminal(dest):
                    values["terminal_at"] = now
                    values["lease_owner"] = None
                    values["lease_expires_at"] = None
                if failure is not None:
                    values["failure_state"] = dict(failure)
                if recovery_patch is not None:
                    merged = dict(row.recovery_state or {})
                    merged.update(recovery_patch)
                    values["recovery_state"] = merged
                updated = db.execute(
                    sa.update(GISMissionRow)
                    .where(
                        GISMissionRow.mission_id == mission_id,
                        GISMissionRow.lease_epoch == int(lease_epoch),
                        GISMissionRow.revision == row.revision,
                    )
                    .values(**values)
                )
                db.commit()
                if not updated.rowcount:
                    raise FencingError("CAS_LOST")
                row = db.query(GISMissionRow).filter(
                    GISMissionRow.mission_id == mission_id).first()
                return _row_to_record(row)
        except (FencingError, TransitionRejected):
            raise
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def patch_mission(
        self,
        mission_id: str,
        *,
        lease_epoch: int,
        owner: str,
        refs: Optional[Dict[str, Any]] = None,
        frontier: Optional[Dict[str, Any]] = None,
        resource_budget: Optional[Dict[str, Any]] = None,
        recovery_state: Optional[Dict[str, Any]] = None,
        root_goal: Optional[str] = None,
        bump_goal_revision: bool = False,
        expected_revision: Optional[int] = None,
    ) -> C.MissionRecord:
        """Fenced field patch (refs/frontier/budget/goal)."""
        now = _utcnow()
        try:
            with self._sf() as db:
                row = db.query(GISMissionRow).filter(
                    GISMissionRow.mission_id == mission_id).first()
                if row is None:
                    raise TransitionRejected("MISSION_NOT_FOUND")
                if int(row.lease_epoch or 0) != int(lease_epoch):
                    raise FencingError("STALE_LEASE_EPOCH")
                if (row.lease_owner or "") != str(owner)[:128]:
                    raise FencingError("STALE_LEASE_OWNER")
                if expected_revision is not None and int(row.revision) != int(expected_revision):
                    raise TransitionRejected("REVISION_CONFLICT")
                if C.is_terminal(row.state):
                    raise TransitionRejected("MISSION_TERMINAL")
                values: Dict[str, Any] = {
                    "revision": int(row.revision) + 1,
                    "updated_at": now,
                }
                if refs is not None:
                    values["refs"] = dict(refs)
                if frontier is not None:
                    values["frontier"] = dict(frontier)
                if resource_budget is not None:
                    values["resource_budget"] = dict(resource_budget)
                if recovery_state is not None:
                    values["recovery_state"] = dict(recovery_state)
                if root_goal is not None:
                    values["root_goal"] = str(root_goal)[: C.MAX_GOAL_CHARS]
                if bump_goal_revision:
                    values["goal_revision"] = int(row.goal_revision or 1) + 1
                updated = db.execute(
                    sa.update(GISMissionRow)
                    .where(
                        GISMissionRow.mission_id == mission_id,
                        GISMissionRow.lease_epoch == int(lease_epoch),
                        GISMissionRow.revision == row.revision,
                    )
                    .values(**values)
                )
                db.commit()
                if not updated.rowcount:
                    raise FencingError("CAS_LOST")
                row = db.query(GISMissionRow).filter(
                    GISMissionRow.mission_id == mission_id).first()
                return _row_to_record(row)
        except (FencingError, TransitionRejected):
            raise
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    # ── checkpoints ─────────────────────────────────────────────────────

    def write_checkpoint(
        self,
        mission_id: str,
        *,
        lease_epoch: int,
        owner: str,
        note: str = "",
    ) -> C.MissionCheckpoint:
        rec = self.get_mission(mission_id)
        if rec is None:
            raise TransitionRejected("MISSION_NOT_FOUND")
        # fencing pre-check (write still requires live lease)
        if rec.lease_epoch != int(lease_epoch) or rec.lease_owner != str(owner)[:128]:
            raise FencingError("STALE_LEASE_EPOCH")
        cp = C.MissionCheckpoint(
            checkpoint_id=new_checkpoint_id(mission_id, rec.revision),
            mission_id=mission_id,
            mission_revision=rec.revision,
            goal_revision=rec.goal_revision,
            state=rec.state.value,
            frontier=rec.frontier,
            refs=rec.refs,
            resource_budget=rec.resource_budget,
            swarm_run_id=(rec.refs.swarm_run_refs[-1]
                          if rec.refs.swarm_run_refs else ""),
            session_plan_ref=(rec.refs.session_plan_refs[-1]
                              if rec.refs.session_plan_refs else ""),
            workflow_instance_refs=list(rec.refs.workflow_instance_refs),
            note=str(note or "")[:200],
            created_at=time.time(),
        )
        snap = cp.to_bounded_dict()
        try:
            with self._sf() as db:
                # CAS lease+revision first — abort whole txn on loss (no orphan insert).
                updated = db.execute(
                    sa.update(GISMissionRow)
                    .where(
                        GISMissionRow.mission_id == mission_id,
                        GISMissionRow.lease_epoch == int(lease_epoch),
                        GISMissionRow.lease_owner == str(owner)[:128],
                        GISMissionRow.revision == int(rec.revision),
                    )
                    .values(
                        recovery_state={
                            **(rec.recovery.model_dump()),
                            "last_checkpoint_id": cp.checkpoint_id,
                            "last_checkpoint_at": cp.created_at,
                        },
                        revision=int(rec.revision) + 1,
                        updated_at=_utcnow(),
                    )
                )
                if not updated.rowcount:
                    db.rollback()
                    raise FencingError("CAS_LOST")
                db.add(GISMissionCheckpointRow(
                    checkpoint_id=cp.checkpoint_id,
                    mission_id=mission_id,
                    org_id=rec.org_id,
                    mission_revision=cp.mission_revision,
                    goal_revision=cp.goal_revision,
                    state=cp.state,
                    snapshot=snap,
                    created_at=_utcnow(),
                ))
                # prune ring
                rows = (
                    db.query(GISMissionCheckpointRow)
                    .filter(GISMissionCheckpointRow.mission_id == mission_id)
                    .order_by(GISMissionCheckpointRow.created_at.desc())
                    .all()
                )
                for stale in rows[MAX_CHECKPOINT_RING:]:
                    db.delete(stale)
                db.commit()
            return cp
        except FencingError:
            raise
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def list_checkpoints(
        self, mission_id: str, *, limit: int = MAX_CHECKPOINT_RING,
    ) -> List[Dict[str, Any]]:
        try:
            with self._sf() as db:
                rows = (
                    db.query(GISMissionCheckpointRow)
                    .filter(GISMissionCheckpointRow.mission_id == mission_id)
                    .order_by(GISMissionCheckpointRow.created_at.desc())
                    .limit(max(1, min(int(limit), 32)))
                    .all()
                )
                return [
                    {
                        "checkpoint_id": r.checkpoint_id,
                        "mission_revision": r.mission_revision,
                        "goal_revision": r.goal_revision,
                        "state": r.state,
                        "created_at": _ts(r.created_at),
                        "snapshot": r.snapshot or {},
                    }
                    for r in rows
                ]
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def latest_checkpoint(
        self, mission_id: str,
    ) -> Optional[Dict[str, Any]]:
        cps = self.list_checkpoints(mission_id, limit=1)
        return cps[0] if cps else None

    # ── swarm durable ledger ────────────────────────────────────────────

    def create_swarm_run(
        self,
        mission_id: str,
        *,
        org_id: str,
        goal_slice: str = "",
        tasks: Optional[Dict[str, Dict[str, Any]]] = None,
        swarm_run_id: Optional[str] = None,
    ) -> C.SwarmRunDurable:
        sid = swarm_run_id or new_swarm_run_id()
        now = _utcnow()
        task_map = dict(tasks or {})
        if len(task_map) > C.MAX_SWARM_TASKS:
            # deterministic truncate by sorted key
            keep = sorted(task_map.keys())[: C.MAX_SWARM_TASKS]
            task_map = {k: task_map[k] for k in keep}
        org_tok = str(org_id or "")
        if not org_tok:
            raise TransitionRejected("ORG_REQUIRED")
        row = GISMissionSwarmRunRow(
            swarm_run_id=sid,
            mission_id=mission_id,
            org_id=org_tok,
            goal_slice=str(goal_slice or "")[: C.MAX_GOAL_CHARS],
            state="running",
            tasks=task_map,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        try:
            with self._sf() as db:
                mission = db.query(GISMissionRow).filter(
                    GISMissionRow.mission_id == mission_id,
                ).first()
                if mission is None:
                    raise TransitionRejected("MISSION_NOT_FOUND")
                if str(mission.org_id) != org_tok:
                    raise TransitionRejected("ORG_MISMATCH")
                # #1407: refuse swarm runs on terminal missions.
                if C.is_terminal(mission.state):
                    raise TransitionRejected("MISSION_TERMINAL")
                db.add(row)
                db.commit()
            return C.SwarmRunDurable(
                swarm_run_id=sid,
                mission_id=mission_id,
                goal_slice=str(goal_slice or "")[: C.MAX_GOAL_CHARS],
                state="running",
                tasks={
                    tid: C.SwarmTaskReceipt(**td) if isinstance(td, dict) else td
                    for tid, td in task_map.items()
                },
                created_at=_ts(now),
                updated_at=_ts(now),
            )
        except TransitionRejected:
            raise
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def get_swarm_run(self, swarm_run_id: str) -> Optional[C.SwarmRunDurable]:
        try:
            with self._sf() as db:
                row = db.query(GISMissionSwarmRunRow).filter(
                    GISMissionSwarmRunRow.swarm_run_id == swarm_run_id).first()
                if row is None:
                    return None
                tasks = {}
                for tid, td in (row.tasks or {}).items():
                    if isinstance(td, dict):
                        tasks[tid] = C.SwarmTaskReceipt(**td)
                return C.SwarmRunDurable(
                    swarm_run_id=row.swarm_run_id,
                    mission_id=row.mission_id,
                    goal_slice=row.goal_slice or "",
                    state=row.state,
                    tasks=tasks,
                    created_at=_ts(row.created_at),
                    updated_at=_ts(row.updated_at),
                )
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def settle_swarm_task(
        self,
        swarm_run_id: str,
        receipt: C.SwarmTaskReceipt,
    ) -> C.SwarmRunDurable:
        """Idempotent task settle under CAS revision fencing.

        SUCCEEDED/SKIPPED/CANCELLED/UNRESOLVED stick forever (assignment_id
        independent). Concurrent settles of different tasks retry on CAS loss;
        exhausted CAS raises FencingError (fail-closed — never silent success).
        """
        last_cas: Optional[FencingError] = None
        for _attempt in range(8):
            try:
                return self._settle_swarm_task_once(swarm_run_id, receipt)
            except FencingError as exc:
                if str(exc) != "CAS_LOST":
                    raise
                last_cas = exc
        assert last_cas is not None
        raise last_cas

    def _settle_swarm_task_once(
        self,
        swarm_run_id: str,
        receipt: C.SwarmTaskReceipt,
    ) -> C.SwarmRunDurable:
        now = _utcnow()
        try:
            with self._sf() as db:
                row = db.query(GISMissionSwarmRunRow).filter(
                    GISMissionSwarmRunRow.swarm_run_id == swarm_run_id).first()
                if row is None:
                    raise TransitionRejected("SWARM_RUN_NOT_FOUND")
                tasks = dict(row.tasks or {})
                existing = tasks.get(receipt.task_id)
                if isinstance(existing, dict):
                    prev_state = existing.get("state")
                    # Sticky terminals: always no-op regardless of assignment_id
                    # (ADR-0197 — never overwrite a real receipt).
                    if prev_state in (
                        C.SwarmTaskDurableState.SUCCEEDED.value,
                        C.SwarmTaskDurableState.SKIPPED.value,
                        C.SwarmTaskDurableState.CANCELLED.value,
                        C.SwarmTaskDurableState.UNRESOLVED.value,
                    ):
                        tasks_out = {
                            tid: C.SwarmTaskReceipt(**td) if isinstance(td, dict) else td
                            for tid, td in tasks.items()
                        }
                        return C.SwarmRunDurable(
                            swarm_run_id=row.swarm_run_id,
                            mission_id=row.mission_id,
                            goal_slice=row.goal_slice or "",
                            state=row.state,
                            tasks=tasks_out,
                            created_at=_ts(row.created_at),
                            updated_at=_ts(row.updated_at),
                        )
                payload = receipt.model_dump(mode="json")
                payload["settled_at"] = time.time()
                tasks[receipt.task_id] = payload

                def _st(v):
                    if hasattr(v, "value"):
                        return str(v.value)
                    return str(v or "")

                states = [_st((t or {}).get("state")) for t in tasks.values()]
                terminal_ok = {
                    C.SwarmTaskDurableState.SUCCEEDED.value,
                    C.SwarmTaskDurableState.SKIPPED.value,
                    C.SwarmTaskDurableState.FAILED.value,
                    C.SwarmTaskDurableState.CANCELLED.value,
                    C.SwarmTaskDurableState.UNRESOLVED.value,
                }
                if states and all(s in terminal_ok for s in states):
                    if any(s == C.SwarmTaskDurableState.UNRESOLVED.value for s in states):
                        run_state = "partial"
                    elif any(s == C.SwarmTaskDurableState.FAILED.value for s in states):
                        run_state = "partial"
                    elif any(s == C.SwarmTaskDurableState.CANCELLED.value for s in states):
                        run_state = "cancelled"
                    else:
                        run_state = "succeeded"
                else:
                    run_state = "running"
                values: Dict[str, Any] = {
                    "tasks": tasks,
                    "state": run_state,
                    "revision": int(row.revision) + 1,
                    "updated_at": now,
                }
                if run_state != "running":
                    values["terminal_at"] = now
                updated = db.execute(
                    sa.update(GISMissionSwarmRunRow)
                    .where(
                        GISMissionSwarmRunRow.swarm_run_id == swarm_run_id,
                        GISMissionSwarmRunRow.revision == row.revision,
                    )
                    .values(**values)
                )
                if not updated.rowcount:
                    db.rollback()
                    raise FencingError("CAS_LOST")
                db.commit()
            got = self.get_swarm_run(swarm_run_id)
            assert got is not None
            return got
        except (FencingError, TransitionRejected):
            raise
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc

    def list_swarm_runs_for_mission(
        self, mission_id: str, *, org_id: Optional[str] = None,
    ) -> List[C.SwarmRunDurable]:
        try:
            with self._sf() as db:
                q = db.query(GISMissionSwarmRunRow).filter(
                    GISMissionSwarmRunRow.mission_id == mission_id,
                )
                if org_id is not None:
                    q = q.filter(GISMissionSwarmRunRow.org_id == str(org_id))
                rows = (
                    q.order_by(GISMissionSwarmRunRow.created_at.asc())
                    .all()
                )
                out: List[C.SwarmRunDurable] = []
                for row in rows:
                    tasks = {
                        tid: C.SwarmTaskReceipt(**td)
                        for tid, td in (row.tasks or {}).items()
                        if isinstance(td, dict)
                    }
                    out.append(C.SwarmRunDurable(
                        swarm_run_id=row.swarm_run_id,
                        mission_id=row.mission_id,
                        goal_slice=row.goal_slice or "",
                        state=row.state,
                        tasks=tasks,
                        created_at=_ts(row.created_at),
                        updated_at=_ts(row.updated_at),
                    ))
                return out
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)) from exc
