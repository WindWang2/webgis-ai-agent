"""Typed context contract (F04 core).

One vocabulary for every piece of text that may enter a Pi turn prompt:

- :class:`ContextDomain` — closed set of prompt block families. The domain
  pins the budget pool (``context_budget.Category``), the render order, the
  fence discipline and the dedupe authority.
- :class:`ContextItem` — one bounded, provenance-carrying block candidate.
  Every item declares *where it came from* (provider/scope/revision/
  freshness fingerprint), *who may see it* (``SensitivityClass`` — reused
  from ADR-0206, not reinvented), *what it costs* (``est_tokens`` via the
  single CJK-aware estimator) and *why it exists* (``evidence_ref``).
- :class:`ContextProvider` — protocol for projecting one authority into
  items. Providers wrap existing single-render-source builders; they never
  re-render authority data themselves.

Nothing here reads session state: identity arrives via
:class:`TurnContextRequest`, shared read-only facts via
:class:`SharedTurnFacts` (fetched once per turn by the orchestrator — the
N+1 discipline).
"""
from __future__ import annotations

import hashlib
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.services.chat.context.history_compression import _estimate_tokens
from app.services.gis_context.scope import (
    ScopeRef,
    ScopeTier,
    SensitivityClass,
)

#: contract version (bump on any breaking field change)
CONTEXT_ITEM_SCHEMA = "ctx.item.v1"
CONTEXT_REQUEST_SCHEMA = "ctx.req.v1"


class ContextDomain(str, Enum):
    """Closed vocabulary of prompt block families (single render source each).

    ``render_rank`` below pins the final prompt order to the legacy
    ``attach_turn_context`` order; new domains must append before the
    control-plane rows, never reshuffle existing ranks.
    """

    USER_MESSAGE = "user_message"
    CARTOGRAPHY_VERDICT = "cartography_verdict"      # [CARTOGRAPHY_VERDICT]
    CARTOGRAPHY_MEMORY = "cartography_memory"        # [CARTOGRAPHY_MEMORY] (ADR-0069)
    PROJECT_KNOWLEDGE = "project_knowledge"          # <project_knowledge> (#1395)
    GIS_MEMORY = "gis_memory"                        # [GIS_MEMORY] (ADR-0183)
    GIS_CONTEXT_CARD = "gis_context_card"            # [GIS_CONTEXT] (ADR-0206)
    SESSION_PLAN = "session_plan"                    # SessionPlan bounded projection
    ENVIRONMENT = "environment"                      # [GIS 情境]/[环境感知] (ADR-0180)
    TOOL_SURFACE = "tool_surface"                    # [工具面提示] (V4)
    EVICTED_REFS = "evicted_refs"                    # ADR-0104 #6 tombstone
    V6_BLOCKS = "v6_blocks"                          # V6 Wave 13 three-layer blocks
    HARNESS_STATE = "harness_state"                  # HarnessTurnContext projection (F04)
    ACTIVE_TOOLS = "active_tools"                    # ADR-0103 marker (control plane)
    TURN_MARKER = "turn_marker"                      # [WEBGIS_TURN_CONTEXT:...] (control)


class Pool(IntEnum):
    """Budget pool per domain — values are ``context_budget.Category`` values
    (single planning truth; F04 adds no new category)."""

    USER_PROMPT = 2
    HARNESS_EVIDENCE = 6   # verdict + harness state
    SESSION_PLAN = 4       # plan projection + tool surface
    MAP_STATE = 7          # situation/environment + v6
    PROJECT_CONTEXT = 9    # memory/knowledge/gis_memory/gis_context card
    TRACE_SUMMARIES = 11   # evicted-refs tombstone
    CONTROL = 1            # control plane (active tools / turn marker)


#: domain → (budget pool, render rank, fence discipline, default priority)
#: fence: "internal" = the single renderer already XML-fences untrusted
#: values; "wrap" = the pipeline wraps the whole block in an
#: ``untrusted_*`` fence; "none" = control plane / user text (never altered
#: beyond legacy marker neutralization).
_DOMAIN_TABLE: Dict[ContextDomain, Dict[str, Any]] = {
    ContextDomain.USER_MESSAGE:      dict(pool=Pool.USER_PROMPT, rank=0, fence="none", priority=0),
    ContextDomain.CARTOGRAPHY_VERDICT:   dict(pool=Pool.HARNESS_EVIDENCE, rank=1, fence="internal", priority=10),
    ContextDomain.CARTOGRAPHY_MEMORY:    dict(pool=Pool.PROJECT_CONTEXT, rank=2, fence="internal", priority=40),
    ContextDomain.PROJECT_KNOWLEDGE:     dict(pool=Pool.PROJECT_CONTEXT, rank=3, fence="internal", priority=30),
    ContextDomain.GIS_MEMORY:            dict(pool=Pool.PROJECT_CONTEXT, rank=4, fence="wrap", priority=50),
    ContextDomain.GIS_CONTEXT_CARD:      dict(pool=Pool.PROJECT_CONTEXT, rank=5, fence="internal", priority=35),
    ContextDomain.SESSION_PLAN:      dict(pool=Pool.SESSION_PLAN, rank=6, fence="internal", priority=20),
    ContextDomain.ENVIRONMENT:       dict(pool=Pool.MAP_STATE, rank=7, fence="internal", priority=15),
    ContextDomain.TOOL_SURFACE:      dict(pool=Pool.SESSION_PLAN, rank=8, fence="none", priority=25),
    ContextDomain.EVICTED_REFS:      dict(pool=Pool.TRACE_SUMMARIES, rank=9, fence="internal", priority=60),
    ContextDomain.V6_BLOCKS:         dict(pool=Pool.MAP_STATE, rank=10, fence="internal", priority=45),
    ContextDomain.HARNESS_STATE:     dict(pool=Pool.HARNESS_EVIDENCE, rank=11, fence="none", priority=22),
    ContextDomain.ACTIVE_TOOLS:      dict(pool=Pool.CONTROL, rank=12, fence="none", priority=1),
    ContextDomain.TURN_MARKER:       dict(pool=Pool.CONTROL, rank=13, fence="none", priority=0),
}

#: block-family cap (chars) applied at item creation — a runaway renderer can
#: never smuggle unbounded text into the pipeline (boundedness discipline).
DOMAIN_CHAR_CAPS: Dict[ContextDomain, int] = {
    ContextDomain.USER_MESSAGE: 32_000,
    ContextDomain.CARTOGRAPHY_VERDICT: 4_000,
    ContextDomain.CARTOGRAPHY_MEMORY: 2_000,
    ContextDomain.PROJECT_KNOWLEDGE: 2_000,
    ContextDomain.GIS_MEMORY: 2_000,
    ContextDomain.GIS_CONTEXT_CARD: 2_000,
    ContextDomain.SESSION_PLAN: 4_000,
    ContextDomain.ENVIRONMENT: 4_000,
    ContextDomain.TOOL_SURFACE: 500,
    ContextDomain.EVICTED_REFS: 1_200,
    ContextDomain.V6_BLOCKS: 4_000,
    ContextDomain.HARNESS_STATE: 900,
    ContextDomain.ACTIVE_TOOLS: 1_500,
    ContextDomain.TURN_MARKER: 300,
}

#: domains whose content joins *inline* (no separator) to form one legacy
#: block — the ``cartography_context`` string was ``"".join`` of its five
#: builders; byte-equivalence of the retirement boundary depends on this.
INLINE_JOIN_DOMAINS = frozenset({
    ContextDomain.CARTOGRAPHY_VERDICT,
    ContextDomain.CARTOGRAPHY_MEMORY,
    ContextDomain.PROJECT_KNOWLEDGE,
    ContextDomain.GIS_MEMORY,
    ContextDomain.GIS_CONTEXT_CARD,
})

#: domains that must never be dropped or truncated by the allocator
UNTOUCHABLE_DOMAINS = frozenset({
    ContextDomain.USER_MESSAGE,
    ContextDomain.ACTIVE_TOOLS,
    ContextDomain.TURN_MARKER,
})

#: guaranteed minimum (tokens) for floor-protected domains under pool
#: pressure — floor items are truncated to their floor, never dropped.
DOMAIN_FLOOR_TOKENS: Dict[ContextDomain, int] = {
    ContextDomain.CARTOGRAPHY_VERDICT: 120,
    ContextDomain.SESSION_PLAN: 60,
    ContextDomain.ENVIRONMENT: 60,
}


def domain_pool(domain: ContextDomain) -> Pool:
    return _DOMAIN_TABLE[domain]["pool"]


def domain_rank(domain: ContextDomain) -> int:
    return _DOMAIN_TABLE[domain]["rank"]


def domain_fence_mode(domain: ContextDomain) -> str:
    return _DOMAIN_TABLE[domain]["fence"]


def domain_priority(domain: ContextDomain) -> int:
    return _DOMAIN_TABLE[domain]["priority"]


def content_fingerprint(text: str) -> str:
    """Deterministic 16-hex content fingerprint (dedupe + receipt digest)."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


class ContextItem(BaseModel):
    """One bounded prompt-block candidate with full provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = CONTEXT_ITEM_SCHEMA
    item_id: str                       # provider-scoped stable id (dedupe key part)
    provider_id: str
    domain: ContextDomain
    content: str

    # provenance / scoping
    scope: ScopeTier = ScopeTier.TURN
    scope_id: str = ""                 # session/mission/project id per tier
    org_id: str = ""
    project_id: str = ""
    revision: str = ""                 # authority revision (CAS token / seq)
    freshness: str = ""                # fingerprint of the authority state
    sensitivity: SensitivityClass = SensitivityClass.SESSION_LOCAL
    evidence_ref: str = ""             # pointer into trace/evidence, not payload

    # economics / policy
    est_tokens: int = 0
    priority: int = 100                # lower = more important (allocator order)
    floor_tokens: int = 0              # 0 = omittable
    control_plane: bool = False        # OOB control markers (never fenced/trimmed)

    # identity of the rendered text (dedupe + digest; computed once)
    fingerprint: str = ""

    def __init__(self, **data: Any) -> None:
        content = data.get("content", "")
        domain = data.get("domain")
        if "est_tokens" not in data and content:
            data["est_tokens"] = _estimate_tokens(content)
        if not data.get("fingerprint") and content:
            data["fingerprint"] = content_fingerprint(content)
        if domain is not None and "priority" not in data:
            data["priority"] = domain_priority(domain)
        if domain is not None and "floor_tokens" not in data:
            data["floor_tokens"] = DOMAIN_FLOOR_TOKENS.get(domain, 0)
        if domain is not None:
            table = _DOMAIN_TABLE[domain]
            data.setdefault("control_plane", table["fence"] == "none" and domain in (
                ContextDomain.ACTIVE_TOOLS, ContextDomain.TURN_MARKER))
        super().__init__(**data)

    def trimmed_copy(self, content: str) -> "ContextItem":
        """Bounded re-render of this item (same provenance, new fingerprint)."""
        return self.model_copy(update={
            "content": content,
            "est_tokens": _estimate_tokens(content),
            "fingerprint": content_fingerprint(content),
        })

    def renderable_in(self, *, org_id: str = "", project_id: str = "",
                      session_id: str = "") -> bool:
        """Scope gate — delegates to the ADR-0206 ``ScopeRef`` semantics
        (single implementation; no second sensitivity truth here)."""
        from app.services.gis_context.scope import InvalidationPolicy

        ref = ScopeRef(
            tier=self.scope,
            scope_id=self.scope_id,
            org_id=self.org_id,
            project_id=self.project_id,
            sensitivity=self.sensitivity,
            policy=InvalidationPolicy.DERIVED,
        )
        return ref.renderable_in(
            org_id=org_id, project_id=project_id, session_id=session_id
        )


class ProviderReport(BaseModel):
    """One provider's collection outcome (receipt input; honest misses)."""

    model_config = ConfigDict(frozen=True)

    provider_id: str
    domain: ContextDomain
    items: List[ContextItem] = Field(default_factory=list)
    skipped_reason: str = ""           # "" = collected; else machine-readable miss
    degraded: bool = False             # collected via fallback path
    latency_ms: int = 0
    error: str = ""


@runtime_checkable
class ContextProvider(Protocol):
    """Projection of one authority into items.

    Contract: ``collect`` never raises (fail-open → ``ProviderReport`` with
    ``error``), never mutates shared facts, and reads only ``facts`` (no
    private re-fetches — the orchestrator owns I/O identity).
    """

    provider_id: str
    domain: ContextDomain

    def enabled(self, req: "TurnContextRequest", facts: "SharedTurnFacts") -> bool: ...

    async def collect(
        self, req: "TurnContextRequest", facts: "SharedTurnFacts"
    ) -> ProviderReport: ...


class TurnContextRequest(BaseModel):
    """Identity + caller-injected inputs for one turn assembly.

    ``environment_block`` is caller-injected **by design**: the situation
    compiler owns the forward-only snapshot advance (side-effectful), so the
    pipeline must not re-derive it. Everything else is provider-derived.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = CONTEXT_REQUEST_SCHEMA
    session_id: str = ""
    turn_id: str = ""
    message: str = ""
    token: str = ""                    # signed turn capability (marker payload)
    org_id: str = ""
    project_id: str = ""
    user_id: str = ""
    query_text: str = ""               # retrieval key for memory providers
    environment_block: str = ""        # caller-injected situation/env projection
    legacy_cartography_block: str = "" # compat input: pre-F04 prebuilt string


class SharedTurnFacts(BaseModel):
    """Read-only facts fetched once per turn (I/O identity / N+1 discipline).

    Pydantic-free on purpose for ``plan``/``spec``: they are rich domain
    objects (SessionPlan envelope / MapSpec dict) carried opaquely.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    plan: Optional[Any] = None
    mapspec: Optional[Dict[str, Any]] = None
    map_state: Optional[Dict[str, Any]] = None
    harness_context: Optional[Any] = None   # HarnessTurnContext (hk.ctx.v1)
    fetch_errors: Dict[str, str] = Field(default_factory=dict)


def bounded_item(
    *,
    item_id: str,
    provider_id: str,
    domain: ContextDomain,
    content: str,
    **kwargs: Any,
) -> ContextItem:
    """Item factory enforcing the per-domain char cap (single chokepoint)."""
    cap = DOMAIN_CHAR_CAPS[domain]
    if len(content) > cap:
        content = content[: cap - 1] + "…"
    return ContextItem(
        item_id=item_id, provider_id=provider_id, domain=domain,
        content=content, **kwargs,
    )


__all__ = [
    "CONTEXT_ITEM_SCHEMA",
    "CONTEXT_REQUEST_SCHEMA",
    "CONTEXT_DOMAIN_RANKS",
    "ContextDomain",
    "ContextItem",
    "ContextProvider",
    "Pool",
    "ProviderReport",
    "SharedTurnFacts",
    "TurnContextRequest",
    "DOMAIN_CHAR_CAPS",
    "DOMAIN_FLOOR_TOKENS",
    "INLINE_JOIN_DOMAINS",
    "UNTOUCHABLE_DOMAINS",
    "bounded_item",
    "content_fingerprint",
    "domain_fence_mode",
    "domain_pool",
    "domain_priority",
    "domain_rank",
]

#: stable render order derived once (rank → domain), for receipts/renderers
CONTEXT_DOMAIN_RANKS: List[ContextDomain] = sorted(
    ContextDomain, key=domain_rank
)
