"""gis_context — Layered GIS Context Scopes (ADR-0206 / Direction 06).

Turn ⊂ Session ⊂ Mission ⊂ Project. This package owns the joint between
the layers: the scope contract, the mission-scoped GIS working context
(accept-basis/decisions/findings/user-edits), the invalidation engine, the
bounded ``[GIS_CONTEXT]`` card and the Pi hot-path assembly.

It is glue, not truth: session facts stay in gis_situation/session_data,
project knowledge stays in project_knowledge, mission lifecycle stays in
mission_runtime. Master switch ``GIS_CONTEXT_SCOPES`` (default ON).
"""
from __future__ import annotations

from app.services.gis_context.flags import (
    CONTEXT_SCOPES_ENV,
    context_scopes_enabled,
)
from app.services.gis_context.scope import (
    InvalidationPolicy,
    ScopeRef,
    ScopeTier,
    SensitivityClass,
    mission_scope_ref,
)
from app.services.gis_context.working_context import GISWorkingContext

__all__ = [
    "CONTEXT_SCOPES_ENV",
    "GISWorkingContext",
    "InvalidationPolicy",
    "ScopeRef",
    "ScopeTier",
    "SensitivityClass",
    "context_scopes_enabled",
    "mission_scope_ref",
]
