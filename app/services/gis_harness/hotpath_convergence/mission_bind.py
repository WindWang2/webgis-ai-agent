"""Optional Mission create/reuse for multi-step GIS turns.

Default OFF (``GIS_MISSION_HOTPATH`` unset/0) → no durable Mission side effects.
When enabled, create or reuse a Mission and return its id for swarm bridge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.services.gis_harness.hotpath_convergence.flags import mission_hotpath_enabled


@dataclass
class MissionBindResult:
    mission_id: str = ""
    created: bool = False
    reused: bool = False
    skipped_reason: str = ""

    def to_bounded_dict(self) -> dict:
        return {
            "mission_id": (self.mission_id or "")[:64],
            "created": self.created,
            "reused": self.reused,
            "skipped_reason": (self.skipped_reason or "")[:120],
        }


def maybe_bind_mission_for_turn(
    *,
    session_id: str = "",
    org_id: str = "",
    user_id: str = "",
    root_goal: str = "",
    mission_id: str = "",
    project_id: Optional[str] = None,
    runtime: Any = None,
) -> MissionBindResult:
    """Opt-in Mission bind. No durable writes when hotpath flag is off."""
    if not mission_hotpath_enabled():
        # Pass through explicit mission_id for callers that already have one
        # (swarm bridge still honors it when GIS_MISSION_RUNTIME is on), but
        # do not create/reuse via this hotpath helper.
        if mission_id:
            return MissionBindResult(
                mission_id=str(mission_id)[:64],
                skipped_reason="hotpath_off_passthrough",
            )
        return MissionBindResult(skipped_reason="GIS_MISSION_HOTPATH disabled")

    org = str(org_id or "0")[:64]
    sid = str(session_id or "")[:64]
    goal = str(root_goal or "")[:2000]

    try:
        if runtime is None:
            from app.services.mission_runtime.service import get_mission_runtime

            runtime = get_mission_runtime()

        mid = str(mission_id or "").strip()
        if mid:
            existing = runtime.store.get_mission(mid, org_id=org)
            if existing is not None:
                return MissionBindResult(mission_id=mid[:64], reused=True)
            # Unknown id under org → create fresh rather than invent ownership.
            mid = ""

        rec = runtime.create(
            org_id=org,
            user_id=str(user_id or "")[:64],
            project_id=project_id,
            root_goal=goal,
            session_id=sid,
        )
        return MissionBindResult(
            mission_id=str(rec.mission_id)[:64],
            created=True,
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed: no silent success
        return MissionBindResult(
            skipped_reason=f"mission_bind_failed:{type(exc).__name__}"[:120],
        )


__all__ = ["MissionBindResult", "maybe_bind_mission_for_turn"]
