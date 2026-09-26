"""Deterministic context dedupe (F04).

Rules (all inputs → same output, no clock, no RNG):

1. **Exact-content dedupe** — two items with the same content fingerprint
   are the same fact. Keep the *authoritative* one: lower priority number
   wins, then lower scope tier (turn beats session beats mission beats
   project: the freshest scope wins ties), then stable ``item_id``.
2. **Authority dedupe** — within one authority namespace (provider +
   evidence ref), two items with *different* fingerprints are competing
   renderings of one authority; the loser is stale and never mixes into
   current facts (dropped with a receipt reason, not silently merged).
3. Cross-scope leaks cannot dedupe-merge: items whose sensitivity gate
   fails for this turn never reach the dedupe input (assembly drops them
   earlier with ``scope_denied``).

Dedupe never edits content — it only selects whole items.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.services.context_assembly.contract import ContextItem
from app.services.gis_context.scope import ScopeTier

_SCOPE_ORDER = {
    ScopeTier.TURN: 0,
    ScopeTier.SESSION: 1,
    ScopeTier.MISSION: 2,
    ScopeTier.PROJECT: 3,
}

#: reason codes emitted into the receipt
REASON_EXACT_DUP = "dedupe:exact_content"
REASON_STALE_AUTHORITY = "dedupe:stale_authority"


@dataclass
class DedupeResult:
    kept: List[ContextItem] = field(default_factory=list)
    dropped: List[Tuple[ContextItem, str]] = field(default_factory=list)  # (item, reason)


def _authority_key(item: ContextItem) -> Optional[str]:
    return f"{item.provider_id}:{item.evidence_ref}" if item.evidence_ref else ""


def _prefer(a: ContextItem, b: ContextItem) -> ContextItem:
    """Deterministic winner: priority → scope tier → item_id."""
    a_key = (a.priority, _SCOPE_ORDER.get(a.scope, 9), a.item_id)
    b_key = (b.priority, _SCOPE_ORDER.get(b.scope, 9), b.item_id)
    return a if a_key <= b_key else b


def dedupe_items(items: List[ContextItem]) -> DedupeResult:
    """Two-phase winner selection; output preserves input order."""
    dropped: Dict[int, Tuple[ContextItem, str]] = {}

    # phase 1 — exact-content winners
    by_fingerprint: Dict[str, ContextItem] = {}
    for item in items:
        fp = item.fingerprint or item.content
        incumbent = by_fingerprint.get(fp)
        if incumbent is None:
            by_fingerprint[fp] = item
            continue
        winner = _prefer(incumbent, item)
        loser = item if winner is incumbent else incumbent
        dropped[id(loser)] = (loser, REASON_EXACT_DUP)
        by_fingerprint[fp] = winner

    # phase 2 — authority-scoped freshness among the survivors
    by_authority: Dict[str, ContextItem] = {}
    survivors = [i for i in items if id(i) not in dropped
                 and by_fingerprint.get(i.fingerprint or i.content) is i]
    for item in survivors:
        auth = _authority_key(item)
        if not auth:
            continue
        incumbent = by_authority.get(auth)
        if incumbent is None:
            by_authority[auth] = item
            continue
        winner = _prefer(incumbent, item)
        loser = item if winner is incumbent else incumbent
        stale = (
            loser.freshness != ""
            and winner.freshness != ""
            and loser.freshness != winner.freshness
        )
        dropped[id(loser)] = (
            loser, REASON_STALE_AUTHORITY if stale else REASON_EXACT_DUP
        )
        by_authority[auth] = winner

    kept = [i for i in items if id(i) not in dropped]
    return DedupeResult(kept=kept, dropped=list(dropped.values()))
