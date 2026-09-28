"""Kill-switches for the layered GIS context system (ADR-0206 D6).

``GIS_CONTEXT_SCOPES`` — master switch, **default ON**: gates the
``[GIS_CONTEXT]`` injection, the mission-scoped working context store and
the chat-path mission auto-bind. ``0`` restores pre-ADR-0206 behavior
byte-identically.

``GIS_CONTEXT_REVALIDATION`` (ADR-0215 D9) — **default ON**: gates the
*automatic* revalidation behavior (passive marker reconfirmation + dataset
fingerprint reconciliation inside the turn assembly). ``0`` restores the
post-#1487 one-way invalidation behavior for everything automatic; the
explicit ``webgis_context_revalidate`` tool stays available (user-driven,
evidence-checked, receipted). Both tool entries are gated by the master
switch only.

``GIS_PROJECT_KNOWLEDGE`` promotion lives in its own module
(``project_knowledge``); ``GIS_MISSION_HOTPATH`` stays opt-in — it gates
swarm durable mission creation, a deliberately separate blast radius.

``GIS_CONTEXT_MEMORY_GRAPH`` (H09) — **default ON**: gates the situational
memory graph behavior (durable fact capture, dependency-anchored precise
invalidation of derived findings, recompute orchestration, extended
user-edit capture, budgeted memory projection). ``0`` restores the v2
(post-ADR-0215) behavior exactly — no facts are captured, no graph rows
render, FindingRef/decision invalidation keeps its F05 semantics.
"""
from __future__ import annotations

import os

CONTEXT_SCOPES_ENV = "GIS_CONTEXT_SCOPES"
REVALIDATION_ENV = "GIS_CONTEXT_REVALIDATION"
MEMORY_GRAPH_ENV = "GIS_CONTEXT_MEMORY_GRAPH"

#: Blocks assembled before the context card yield when already large.
COMBINED_BUDGET_CHARS = 4500


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


def context_scopes_enabled() -> bool:
    """Master gate for the layered context system (default ON)."""
    return _env_truthy(CONTEXT_SCOPES_ENV, "1")


def revalidation_enabled() -> bool:
    """Gate for the ADR-0215 revalidation + fingerprint reconciliation
    pass (default ON; subordinate to the master switch)."""
    return context_scopes_enabled() and _env_truthy(REVALIDATION_ENV, "1")


def memory_graph_enabled() -> bool:
    """Gate for the H09 situational memory graph (default ON; subordinate
    to the master switch)."""
    return context_scopes_enabled() and _env_truthy(MEMORY_GRAPH_ENV, "1")


__all__ = [
    "COMBINED_BUDGET_CHARS",
    "CONTEXT_SCOPES_ENV",
    "MEMORY_GRAPH_ENV",
    "REVALIDATION_ENV",
    "context_scopes_enabled",
    "memory_graph_enabled",
    "revalidation_enabled",
]
