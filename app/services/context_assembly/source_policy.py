"""Session source-selection policy (G43, ADR-0295).

The user-facing vocabulary for「本轮之后新装配可选资料」: which *optional
project priors* the typed context assembly should prefer or exclude for the
NEXT and subsequent turns of one session.

Design invariants (single truth, no second store):

- Source identity is **server-minted and namespaced** (``pk:<entry_id>`` /
  ``cm:<fact_id>`` / ``gm:block``). Clients never submit source text, paths,
  URLs or tool grants — only these ids, validated against the authorized
  catalog at write time.
- Only OPTIONAL priors are selectable. Control-plane / authority domains
  (user message, verdict, SessionPlan, environment, tool surface, active
  tools, turn marker, GIS state rebuild, …) can never be excluded here; the
  closed set below is the single authority both API and assembly consult.
- A policy applies from the NEXT assembled turn onward (frozen per turn at
  assembly time via ``policy_version``); exclusion never deletes shared
  facts, assets or native Pi history, and never claims "forgotten".
- CAS: ``policy_version`` increments on every accepted write; conflicting
  writers get 409 + the current row.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict

POLICY_SCHEMA = "ctx.source_policy.v1"

#: source-id namespaces (server-minted only)
SOURCE_NS_PROJECT_KNOWLEDGE = "pk"
SOURCE_NS_CARTO_MEMORY = "cm"
SOURCE_NS_GIS_MEMORY = "gm"

#: the single optional block-level source (gis_memory projection text)
GIS_MEMORY_BLOCK_SOURCE_ID = "gm:block"


class SourceKind(str, Enum):
    PROJECT_KNOWLEDGE_ENTRY = "project_knowledge_entry"
    CARTO_MEMORY_FACT = "carto_memory_fact"
    GIS_MEMORY_BLOCK = "gis_memory_block"


#: domains a user may influence through this policy (closed set — anything
#: else is authority/control plane and must stay injectable). The typed
#: providers consult this via :func:`optional_domains`.
_OPTIONAL_DOMAIN_VALUES = frozenset({
    "cartography_memory",
    "project_knowledge",
    "gis_memory",
})


def optional_domains() -> FrozenSet[str]:
    """Domain values a source policy may influence (closed vocabulary)."""
    return _OPTIONAL_DOMAIN_VALUES


def is_optional_domain(domain: Any) -> bool:
    value = getattr(domain, "value", domain)
    return str(value) in _OPTIONAL_DOMAIN_VALUES


def source_kind_for_id(source_id: str) -> Optional[SourceKind]:
    """Namespace a server-minted source id belongs to (None = malformed)."""
    if not isinstance(source_id, str) or not source_id or len(source_id) > 96:
        return None
    if source_id == GIS_MEMORY_BLOCK_SOURCE_ID:
        return SourceKind.GIS_MEMORY_BLOCK
    if source_id.startswith(f"{SOURCE_NS_PROJECT_KNOWLEDGE}:"):
        return (
            SourceKind.PROJECT_KNOWLEDGE_ENTRY
            if len(source_id) > len(SOURCE_NS_PROJECT_KNOWLEDGE) + 1
            else None
        )
    if source_id.startswith(f"{SOURCE_NS_CARTO_MEMORY}:"):
        return (
            SourceKind.CARTO_MEMORY_FACT
            if len(source_id) > len(SOURCE_NS_CARTO_MEMORY) + 1
            else None
        )
    return None


class SourcePolicy(BaseModel):
    """Effective policy for one session (immutable snapshot per turn).

    ``mode="auto"`` is the default no-op: builders behave exactly as before
    (byte-compat for sessions that never touched the panel). ``mode=
    "explicit"`` freezes a concrete preferred/excluded set.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = POLICY_SCHEMA
    session_id: str = ""
    mode: str = "auto"                       # auto | explicit
    preferred: Tuple[str, ...] = ()          # server-minted source ids
    excluded: Tuple[str, ...] = ()           # server-minted source ids
    policy_version: int = 0                  # CAS token (0 = no row / auto)

    @property
    def is_explicit(self) -> bool:
        return self.mode == "explicit"

    def decision_for(self, source_id: str, *, default: str = "included") -> str:
        """Policy decision for one optional source id.

        Returns ``included`` / ``preferred`` / ``excluded_by_policy``.
        ``auto`` mode never rewrites the default.
        """
        if not self.is_explicit:
            return default
        if source_id in self.excluded:
            return "excluded_by_policy"
        if source_id in self.preferred:
            return "preferred"
        return default

    def as_bounded_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema_version,
            "session_id": self.session_id[:48],
            "mode": self.mode,
            "preferred": list(self.preferred[:64]),
            "excluded": list(self.excluded[:64]),
            "policy_version": self.policy_version,
        }


AUTO_POLICY = SourcePolicy(mode="auto", policy_version=0)


def validate_policy_payload(
    *,
    mode: str,
    preferred: List[str],
    excluded: List[str],
) -> Tuple[List[str], List[str], Optional[str]]:
    """Structural validation before any authority check.

    Returns ``(preferred, excluded, error)``; ``error`` is a closed reason
    code (``mode_invalid`` / ``source_id_malformed`` / ``conflict`` /
    ``too_many``). Duplicates are collapsed deterministically.
    """
    if mode not in ("auto", "explicit"):
        return [], [], "mode_invalid"
    seen: List[str] = []
    for raw in preferred:
        if source_kind_for_id(raw) is None:
            return [], [], "source_id_malformed"
        if raw not in seen:
            seen.append(raw)
    drop: List[str] = []
    for raw in excluded:
        if source_kind_for_id(raw) is None:
            return [], [], "source_id_malformed"
        if raw in seen:
            return [], [], "conflict"
        if raw not in drop:
            drop.append(raw)
    if len(seen) > 64 or len(drop) > 64:
        return [], [], "too_many"
    return seen, drop, None


__all__ = [
    "AUTO_POLICY",
    "GIS_MEMORY_BLOCK_SOURCE_ID",
    "POLICY_SCHEMA",
    "SourceKind",
    "SourcePolicy",
    "is_optional_domain",
    "optional_domains",
    "source_kind_for_id",
    "validate_policy_payload",
]
