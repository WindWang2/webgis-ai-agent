"""Token-budgeted memory projection (H09).

The context assembler (F04) budgets *prompt blocks*; the memory graph
needs its own selection discipline one level down: which facts and
findings — of everything the mission durably knows — are worth their
tokens *this turn*. Rules:

- **Deterministic total order**: current derived findings (priority desc,
  family, id) → facts (priority desc, kind, ref) → stale findings as
  "needs recompute" lines. Same context in, same projection out — golden
  tested.
- **Dependency closure**: a current finding renders only together with
  its anchored facts; when the budget (or the fact cap) drops an anchor,
  the finding is downgraded out of the current section — an unexplained
  conclusion must not masquerade as grounded.
- **Token budget**: every line is estimated at ``ceil(chars / 4)`` tokens
  (worst case, never under-count); projection stops at the budget and
  counts what it dropped. Raw facts always stay in the store — the
  projection only decides what the LLM sees.
- **Stale is never projected as current**: stale findings render only as
  recompute-owed lines carrying their attribution (the causal evidence).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.services.gis_context.working_context import (
    DerivedFinding,
    FACT_KINDS,
    GISWorkingContext,
)

#: Default projection budget (~800 chars of card real estate). The card's
#: own char budget stays the outer hard cap — this bounds the graph's
#: share of it.
DEFAULT_TOKEN_BUDGET = 200
DEFAULT_MAX_ITEMS = 12


def estimate_tokens(line: str) -> int:
    """Worst-case token estimate (≈4 chars/token; never under-count)."""
    return max(1, (len(line) + 3) // 4)


@dataclass
class ProjectionItem:
    kind: str          # finding | fact | stale_finding
    text: str
    tokens: int
    priority: int


@dataclass
class ContextProjection:
    items: List[ProjectionItem] = field(default_factory=list)
    used_tokens: int = 0
    dropped: int = 0
    #: Attribution for a dropped current finding: its anchors were not
    #: projectable within budget (dependency closure violated).
    closure_drops: List[str] = field(default_factory=list)

    @property
    def lines(self) -> List[str]:
        return [i.text for i in self.items]

    @property
    def truncated(self) -> bool:
        return self.dropped > 0


def _edge_summary(finding: DerivedFinding) -> str:
    parts = []
    for e in finding.depends_on[:2]:
        anchor = e.anchor[-12:] if e.anchor else "?"
        parts.append(f"{e.dim}:{e.ref}@{anchor}" if e.ref else f"{e.dim}@{anchor}")
    return ",".join(parts)[:64]


def _anchor_keys(finding: DerivedFinding) -> List[tuple]:
    """(fact kind, ref) anchors a finding depends on — the closure set."""
    keys = []
    for e in finding.depends_on:
        if e.dim == "data" and e.ref:
            keys.append(("dataset", e.ref))
        elif e.dim == "mapspec":
            keys.append(("mapspec", ""))
    return keys


def project_memory(
    wc: Optional[GISWorkingContext],
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> ContextProjection:
    """Select the bounded graph projection for this turn (pure)."""
    projection = ContextProjection()
    if wc is None or not wc.derived_findings:
        return projection
    budget = max(0, int(token_budget))

    current = [f for f in wc.derived_findings if f.status == "current"]
    current.sort(key=lambda f: (-int(f.priority), f.family, f.finding_id))
    stale = [f for f in wc.derived_findings if f.status == "stale"]
    stale.sort(key=lambda f: (-int(f.priority), f.family, f.finding_id))

    # Facts are projected before findings so closure is decidable against
    # what actually made the cut (facts are the cheap, reusable lines).
    fact_keys_included: set = set()

    def try_item(kind: str, text: str, priority: int) -> bool:
        nonlocal projection
        if len(projection.items) >= max_items:
            projection.dropped += 1
            return False
        tokens = estimate_tokens(text)
        if projection.used_tokens + tokens > budget:
            projection.dropped += 1
            return False
        projection.items.append(ProjectionItem(
            kind=kind, text=text, tokens=tokens, priority=priority))
        projection.used_tokens += tokens
        return True

    ordered_facts = sorted(wc.facts, key=lambda f: (-int(f.priority), f.kind, f.ref))
    fact_lines = {}
    for fact in ordered_facts:
        if fact.kind not in FACT_KINDS:
            continue
        line = f"事实: {fact.kind}:{fact.ref or 'mapspec'}@{fact.token[-16:]}" \
            if fact.kind == "dataset" else f"事实: mapspec@{fact.token[-16:]}"
        fact_lines[fact.key()] = line

    for finding in current:
        line = f"结论: {finding.label or finding.ref}（依据 {_edge_summary(finding)}）"
        missing = [k for k in _anchor_keys(finding) if k in fact_lines]
        if missing and any(k not in fact_keys_included for k in missing):
            # Anchor facts must be part of the projection for the finding
            # to count as grounded; reserve their tokens first.
            for key in missing:
                if key in fact_keys_included:
                    continue
                if try_item("fact", fact_lines[key], 2):
                    fact_keys_included.add(key)
        grounded = all(k in fact_keys_included for k in _anchor_keys(finding))
        if _anchor_keys(finding) and not grounded:
            projection.closure_drops.append(finding.finding_id)
            projection.dropped += 1
            continue
        try_item("finding", line, int(finding.priority))

    # Facts not pulled in by closure (still bounded by the same budget).
    for key, line in fact_lines.items():
        if key in fact_keys_included:
            continue
        if try_item("fact", line, 2):
            fact_keys_included.add(key)

    for finding in stale:
        reason = finding.stale_reasons[0][:48] if finding.stale_reasons else "basis_drift"
        try_item("stale_finding",
                 f"⚠ 需重算: {finding.label or finding.ref}（{reason}）",
                 int(finding.priority))
    return projection


__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "ContextProjection",
    "ProjectionItem",
    "estimate_tokens",
    "project_memory",
]
