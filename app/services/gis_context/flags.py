"""Kill-switches for the layered GIS context system (ADR-0204 D6).

``GIS_CONTEXT_SCOPES`` — master switch, **default ON**: gates the
``[GIS_CONTEXT]`` injection, the mission-scoped working context store and
the chat-path mission auto-bind. ``0`` restores pre-ADR-0204 behavior
byte-identically.

``GIS_PROJECT_KNOWLEDGE`` promotion lives in its own module
(``project_knowledge``); ``GIS_MISSION_HOTPATH`` stays opt-in — it gates
swarm durable mission creation, a deliberately separate blast radius.
"""
from __future__ import annotations

import os

CONTEXT_SCOPES_ENV = "GIS_CONTEXT_SCOPES"

#: Blocks assembled before the context card yield when already large.
COMBINED_BUDGET_CHARS = 4500


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


def context_scopes_enabled() -> bool:
    """Master gate for the layered context system (default ON)."""
    return _env_truthy(CONTEXT_SCOPES_ENV, "1")


__all__ = [
    "COMBINED_BUDGET_CHARS",
    "CONTEXT_SCOPES_ENV",
    "context_scopes_enabled",
]
