"""Mission-level resource accounting (extends Governor concepts; no billing).

Persists cumulative consumption on the Mission row. Live reservations remain
process-local via Harness Resource Governor; durable totals survive restart.
"""
from __future__ import annotations

from typing import Dict, Optional

from app.services.mission_runtime import contracts as C
from app.services.mission_runtime.store import (
    FencingError,
    MissionStore,
    TransitionRejected,
)


class MissionResourceLedger:
    #: RUN-15: bounded CAS retries for the read-modify-write budget charge.
    _MAX_CAS_ATTEMPTS = 5

    def __init__(self, store: Optional[MissionStore] = None) -> None:
        self.store = store or MissionStore()

    def set_quota(
        self,
        mission_id: str,
        *,
        lease_epoch: int,
        owner: str,
        quota: Dict[str, float],
    ) -> C.MissionRecord:
        rec = self.store.get_mission(mission_id)
        if rec is None:
            raise TransitionRejected("MISSION_NOT_FOUND")
        budget = rec.resource_budget.model_dump()
        budget["quota"] = {str(k)[:64]: float(v) for k, v in (quota or {}).items()}
        return self.store.patch_mission(
            mission_id, lease_epoch=lease_epoch, owner=owner,
            resource_budget=budget)

    def reserve(
        self,
        mission_id: str,
        *,
        lease_epoch: int,
        owner: str,
        dim: str,
        amount: float,
    ) -> C.MissionRecord:
        rec = self.store.get_mission(mission_id)
        if rec is None:
            raise TransitionRejected("MISSION_NOT_FOUND")
        budget = rec.resource_budget
        # reject if would exceed quota
        quota = float(budget.quota.get(dim, 0.0) or 0.0)
        if quota > 0:
            projected = (
                float(budget.consumed.get(dim, 0.0))
                + float(budget.reserved.get(dim, 0.0))
                + float(amount)
            )
            if projected > quota:
                raise TransitionRejected(f"BUDGET_EXHAUSTED:{dim}")
        budget.reserve(dim, amount)
        return self.store.patch_mission(
            mission_id, lease_epoch=lease_epoch, owner=owner,
            resource_budget=budget.model_dump())

    def commit_consumption(
        self,
        mission_id: str,
        *,
        lease_epoch: int,
        owner: str,
        dim: str,
        amount: float,
        release_reservation: float = 0.0,
        retry: bool = False,
        idempotency_key: str = "",
        seen_keys: Optional[set] = None,
    ) -> C.MissionRecord:
        """Charge cumulative consumption; duplicate idempotency_key is no-op.

        RUN-15：读-改-写必须走 revision CAS（``expected_revision``）并重试
        ``REVISION_CONFLICT`` —— 并发计费/预留交错时绝不丢计费。
        """
        if idempotency_key and seen_keys is not None and idempotency_key in seen_keys:
            got = self.store.get_mission(mission_id)
            if got is None:
                raise TransitionRejected("MISSION_NOT_FOUND")
            return got

        last_conflict: Optional[Exception] = None
        for _attempt in range(self._MAX_CAS_ATTEMPTS):
            rec = self.store.get_mission(mission_id)
            if rec is None:
                raise TransitionRejected("MISSION_NOT_FOUND")
            budget = rec.resource_budget
            # durable idempotency via evidence_refs ticket
            if idempotency_key:
                ticket = f"receipt:budget:{idempotency_key}"
                if ticket in (rec.refs.evidence_refs or []):
                    return rec
                refs = rec.refs.model_dump()
                ev = list(refs.get("evidence_refs") or [])
                if ticket not in ev:
                    ev = (ev + [ticket])[: C.MAX_REFS]
                    refs["evidence_refs"] = ev
            else:
                refs = None
            if release_reservation:
                budget.release_reservation(dim, release_reservation)
            budget.charge(dim, amount, retry=retry)
            try:
                updated = self.store.patch_mission(
                    mission_id, lease_epoch=lease_epoch, owner=owner,
                    resource_budget=budget.model_dump(),
                    refs=refs,
                    expected_revision=rec.revision,
                )
            except (TransitionRejected, FencingError) as exc:
                # REVISION_CONFLICT = revision moved before patch's re-read;
                # CAS_LOST = another writer won between read and update. Both
                # are safe to retry with a fresh read (charge is additive).
                if str(exc) not in ("REVISION_CONFLICT", "CAS_LOST"):
                    raise
                last_conflict = exc
                continue
            if idempotency_key and seen_keys is not None:
                seen_keys.add(idempotency_key)
            return updated
        assert last_conflict is not None
        raise last_conflict

    def release(
        self,
        mission_id: str,
        *,
        lease_epoch: int,
        owner: str,
        dim: str,
        amount: float,
    ) -> C.MissionRecord:
        rec = self.store.get_mission(mission_id)
        if rec is None:
            raise TransitionRejected("MISSION_NOT_FOUND")
        budget = rec.resource_budget
        budget.release_reservation(dim, amount)
        return self.store.patch_mission(
            mission_id, lease_epoch=lease_epoch, owner=owner,
            resource_budget=budget.model_dump())

    def exhausted(self, mission_id: str) -> list[str]:
        rec = self.store.get_mission(mission_id)
        if rec is None:
            return []
        return rec.resource_budget.exhausted()
