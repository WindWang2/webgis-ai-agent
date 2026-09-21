"""Context Scope Contract (ADR-0206 D2 / Direction 06 M1).

The typed joint between the four context layers::

    Turn ⊂ Session ⊂ Mission ⊂ Project

This module owns the *shared vocabulary only* — it is not a store and not a
second truth for any layer. Turn stays derived-per-turn (gis_situation),
session stays in session map_state, project stays in project_knowledge,
mission gets the working-context store (``store.py``). Every scoped record
can describe itself: scope id, owner, source, revision, sensitivity class
and invalidation policy — the fields the audit requires before any record
may cross a layer boundary.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict


class ScopeTier(str, Enum):
    TURN = "turn"
    SESSION = "session"
    MISSION = "mission"
    PROJECT = "project"


class SensitivityClass(str, Enum):
    """Who may see a record when contexts are rendered.

    ``session_local`` never leaves its session; ``project_scoped`` is
    renderable only inside its owning project; ``org_scoped`` inside its
    org. Injection sites must gate on this — the default is the most
    restrictive class.
    """

    SESSION_LOCAL = "session_local"
    PROJECT_SCOPED = "project_scoped"
    ORG_SCOPED = "org_scoped"


class InvalidationPolicy(str, Enum):
    """When a scoped record may be dropped or must be re-derived.

    ``derived`` records are recomputed from authorities each turn and are
    never persisted; ``on_session_end``/``on_mission_terminal`` records die
    with their scope; ``manual`` records only leave via explicit purge.
    """

    DERIVED = "derived"
    ON_SESSION_END = "on_session_end"
    ON_MISSION_TERMINAL = "on_mission_terminal"
    MANUAL = "manual"


_SCOPE_ID_MAX = 64
_SOURCE_MAX = 48
_REV_MAX = 32


class ScopeRef(BaseModel):
    """Typed identity of a scoped record (bounded, serializable)."""

    model_config = ConfigDict(extra="forbid")

    tier: ScopeTier
    scope_id: str = ""
    org_id: str = ""
    project_id: str = ""
    user_id: str = ""
    source: str = ""
    revision: str = ""
    sensitivity: SensitivityClass = SensitivityClass.SESSION_LOCAL
    policy: InvalidationPolicy = InvalidationPolicy.DERIVED

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier.value,
            "scope_id": self.scope_id[:_SCOPE_ID_MAX],
            "org_id": self.org_id[:_SCOPE_ID_MAX],
            "project_id": self.project_id[:_SCOPE_ID_MAX],
            "user_id": self.user_id[:_SCOPE_ID_MAX],
            "source": self.source[:_SOURCE_MAX],
            "revision": self.revision[:_REV_MAX],
            "sensitivity": self.sensitivity.value,
            "policy": self.policy.value,
    }

    def renderable_in(
        self,
        *,
        org_id: str = "",
        project_id: str = "",
        session_id: str = "",
    ) -> bool:
        """Injection gate: a record may render only inside its own scope.

        Empty owner fields are wildcard-by-absence (unknown owner cannot be
        cross-checked) — records that must not leak should carry explicit
        owner ids.
        """
        if self.sensitivity is SensitivityClass.SESSION_LOCAL:
            return bool(self.scope_id) and self.scope_id == session_id
        if self.sensitivity is SensitivityClass.PROJECT_SCOPED:
            if not self.project_id:
                return True
            return self.project_id == project_id
        if self.sensitivity is SensitivityClass.ORG_SCOPED:
            if not self.org_id:
                return True
            return self.org_id == org_id
        return False


def mission_scope_ref(
    *,
    mission_id: str,
    org_id: str = "",
    project_id: str = "",
    user_id: str = "",
    revision: str = "",
) -> ScopeRef:
    """Standard ScopeRef for mission working-context records."""
    return ScopeRef(
        tier=ScopeTier.MISSION,
        scope_id=mission_id[:_SCOPE_ID_MAX],
        org_id=org_id[:_SCOPE_ID_MAX],
        project_id=project_id[:_SCOPE_ID_MAX],
        user_id=user_id[:_SCOPE_ID_MAX],
        source="gis_working_context",
        revision=revision[:_REV_MAX],
        sensitivity=(
            SensitivityClass.PROJECT_SCOPED if project_id else SensitivityClass.ORG_SCOPED
        ),
        policy=InvalidationPolicy.ON_MISSION_TERMINAL,
    )


__all__ = [
    "InvalidationPolicy",
    "ScopeRef",
    "ScopeTier",
    "SensitivityClass",
    "mission_scope_ref",
]
