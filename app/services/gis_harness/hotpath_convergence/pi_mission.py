"""Wire Mission bind onto the default Pi turn path (#1395).

``maybe_bind_mission_for_turn`` previously had production callers only on the
swarm bridge / evaluation driver — so even with ``GIS_MISSION_HOTPATH=1``,
ordinary multi-step Pi turns never created a durable Mission.

Call ``maybe_bind_mission_for_pi_turn`` from the chat route (both stream and
non-stream) before ``stream_prompt`` / ``prompt``. Flag-gated; fail-open.
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.gis_harness.hotpath_convergence.mission_bind import (
    MissionBindResult,
    maybe_bind_mission_for_turn,
)
from app.services.gis_harness.hotpath_convergence.session_ctx import set_mission_id


def maybe_bind_mission_for_pi_turn(
    *,
    session_id: str = "",
    org_id: str = "",
    user_id: str = "",
    root_goal: str = "",
    mission_id: str = "",
    project_id: Optional[str] = None,
    runtime: Any = None,
) -> MissionBindResult:
    """Opt-in Mission create/reuse for a Pi chat turn (default OFF via flag)."""
    try:
        bind = maybe_bind_mission_for_turn(
            session_id=session_id,
            org_id=org_id,
            user_id=user_id,
            root_goal=root_goal,
            mission_id=mission_id,
            project_id=project_id,
            runtime=runtime,
        )
    except Exception as exc:  # noqa: BLE001 — never break the turn
        return MissionBindResult(
            skipped_reason=f"pi_mission_bind_failed:{type(exc).__name__}"[:120],
        )
    if bind.mission_id:
        try:
            set_mission_id(
                session_id,
                bind.mission_id,
                tenant_id=str(org_id or "")[:64],
            )
        except Exception:  # noqa: BLE001
            pass
    return bind


__all__ = ["maybe_bind_mission_for_pi_turn"]
