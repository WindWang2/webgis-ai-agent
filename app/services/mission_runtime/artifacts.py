"""Mission artifact ownership — refs into existing Artifact Registry only."""
from __future__ import annotations

from typing import Iterable, List, Optional

from app.services.mission_runtime import contracts as C
from app.services.mission_runtime.store import MissionStore, TransitionRejected


_BUCKETS = {
    "artifact": "artifact_refs",
    "map_product": "map_product_refs",
    "evidence": "evidence_refs",
    "session_plan": "session_plan_refs",
    "workflow": "workflow_instance_refs",
}


class MissionArtifactOwnership:
    """Attach/detach opaque refs owned by a Mission (no second registry)."""

    def __init__(self, store: Optional[MissionStore] = None) -> None:
        self.store = store or MissionStore()

    def attach(
        self,
        mission_id: str,
        *,
        lease_epoch: int,
        owner: str,
        refs: Iterable[str],
        kind: str = "artifact",
    ) -> C.MissionRecord:
        rec = self.store.get_mission(mission_id)
        if rec is None:
            raise TransitionRejected("MISSION_NOT_FOUND")
        bucket = _BUCKETS.get(kind, "artifact_refs")
        data = rec.refs.model_dump()
        cur: List[str] = list(data.get(bucket) or [])
        for r in refs:
            s = str(r or "").strip()
            if not s or s in cur:
                continue
            cur.append(s)
            if len(cur) >= C.MAX_REFS:
                break
        data[bucket] = cur
        return self.store.patch_mission(
            mission_id, lease_epoch=lease_epoch, owner=owner, refs=data)

    def inventory(self, mission_id: str) -> dict:
        rec = self.store.get_mission(mission_id)
        if rec is None:
            return {}
        return rec.refs.model_dump()
