"""Reuse policy (H09) — four-tier reuse decisions with structured reason
and evidence.

``project_knowledge`` retrieval already owns the candidate verdicts
(``exact`` / ``recompute_partial`` / ``not_reusable`` + ``stale_causes``).
This module is the *decision layer* on top — it maps a candidate's verdict
and causes onto the operational tier the mission can act on, always with
closed reason codes and token evidence (never a bare label):

- ``exact`` — fingerprints live-match; reuse as-is.
- ``compatible`` — partially recomputable; reuse with the bounded
  recompute the retrieval verdict already scoped.
- ``stale_but_informative`` — not reusable as-is (revoked claim, scope
  gone, manual withdrawal) but the entry still informs the task.
- ``must_recompute`` — the underlying data moved (version bump / stale
  request input) while the mission's basis still depends on it; fresh
  derivation is owed.

No retrieval, no store access — a pure mapping over the candidate the
hot path already fetched (single assembler, no second retrieval).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

TIER_EXACT = "exact"
TIER_COMPATIBLE = "compatible"
TIER_STALE_INFORMATIVE = "stale_but_informative"
TIER_MUST_RECOMPUTE = "must_recompute"
REUSE_TIERS = (TIER_EXACT, TIER_COMPATIBLE, TIER_STALE_INFORMATIVE, TIER_MUST_RECOMPUTE)

#: Closed reason codes (causal evidence for the decision).
REASON_FINGERPRINTS_MATCH = "fingerprints_live_match"
REASON_PARTIAL_RECOMPUTE = "partial_recompute_scoped"
REASON_KNOWLEDGE_ONLY = "knowledge_only"
REASON_DATA_MOVED = "data_moved_since_acceptance"

#: Cause classes that mean "the data itself moved" (→ must-recompute),
#: everything else means "the knowledge is no longer applicable as-is"
#: (→ informative only). Prefix classes match request-level downgrades
#: like ``request_input_stale:<ds>``.
_VERSION_CAUSES = ("version_bump", "head_changed", "request_input_stale")


def _is_version_cause(cause: str) -> bool:
    c = str(cause or "")
    return any(c == p or c.startswith(p + ":") or c.startswith(p + "_") for p in _VERSION_CAUSES)


@dataclass
class ReuseDecision:
    """One candidate's operational reuse tier with causal evidence."""

    tier: str
    subject: str = ""
    authority: str = ""
    reasons: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return _LABELS.get(self.tier, self.tier)


_LABELS = {
    TIER_EXACT: "✓可复用",
    TIER_COMPATIBLE: "◐可兼容复用",
    TIER_STALE_INFORMATIVE: "○仅背景参考",
    TIER_MUST_RECOMPUTE: "↻需重算",
}


def decide_reuse(candidate: Any, *, wc: Any = None) -> ReuseDecision:
    """Map a retrieval candidate onto the four-tier policy.

    Pure function of the candidate (and the working context for evidence
    tokens); never talks to stores. Unknown verdicts degrade to
    ``stale_but_informative`` — an unreadable candidate must never look
    reusable (fail-closed against stale reuse).
    """
    entry = getattr(candidate, "entry", None)
    verdict = str(getattr(candidate, "verdict", "") or "")
    causes = [str(c) for c in (
        list(getattr(candidate, "stale_causes", []) or [])
        + list(getattr(candidate, "reasons", []) or [])) if str(c or "").strip()]
    subject = str(getattr(entry, "subject", "") or "")[:64] if entry is not None else ""
    authority = ""
    if entry is not None:
        authority = f"{getattr(entry, 'authority_store', '') or ''}:" \
                    f"{getattr(entry, 'authority_id', '') or ''}"[:96]

    decision = ReuseDecision(
        tier=TIER_STALE_INFORMATIVE, subject=subject, authority=authority)

    if verdict == "exact":
        decision.tier = TIER_EXACT
        decision.reasons = [REASON_FINGERPRINTS_MATCH]
        decision.evidence = _claimed_tokens(wc)
        return decision

    if verdict == "recompute_partial":
        decision.tier = TIER_COMPATIBLE
        decision.reasons = [REASON_PARTIAL_RECOMPUTE] + causes[:3]
        decision.evidence = _claimed_tokens(wc)
        return decision

    # not_reusable (and anything unknown): split by *why*.
    version_causes = [c for c in causes if _is_version_cause(c)]
    if version_causes and verdict == "not_reusable":
        decision.tier = TIER_MUST_RECOMPUTE
        decision.reasons = [REASON_DATA_MOVED] + causes[:3]
        decision.evidence = _version_evidence(version_causes, wc)
        return decision

    decision.tier = TIER_STALE_INFORMATIVE
    decision.reasons = [REASON_KNOWLEDGE_ONLY] + causes[:3]
    return decision


def _claimed_tokens(wc: Any) -> List[str]:
    """The accepted dataset fingerprints the reuse query claimed — the
    evidence that made this an exact/compatible match."""
    tokens: List[str] = []
    if wc is None:
        return tokens
    try:
        for ds in getattr(getattr(wc, "basis", None), "datasets", None) or []:
            fp = str(getattr(ds, "version_fingerprint", "") or "")
            if fp:
                tokens.append(f"{ds.ref_id}@{fp}"[:96])
            if len(tokens) >= 4:
                break
    except Exception:  # noqa: BLE001 — evidence is best-effort
        return []
    return tokens


def _version_evidence(version_causes: List[str], wc: Any) -> List[str]:
    """Token evidence for must-recompute: the dataset whose request input
    went stale, with the anchor the mission still holds."""
    evidence: List[str] = []
    for cause in version_causes[:4]:
        evidence.append(str(cause)[:96])
    claimed = {t.split("@", 1)[0]: t for t in _claimed_tokens(wc)}
    for cause in version_causes[:4]:
        ref = str(cause).split(":", 1)[1].strip() if ":" in str(cause) else ""
        if ref and ref in claimed:
            evidence.append(f"claimed:{claimed[ref]}"[:96])
    return evidence[:4]


__all__ = [
    "REASON_DATA_MOVED",
    "REASON_FINGERPRINTS_MATCH",
    "REASON_KNOWLEDGE_ONLY",
    "REASON_PARTIAL_RECOMPUTE",
    "REUSE_TIERS",
    "ReuseDecision",
    "TIER_COMPATIBLE",
    "TIER_EXACT",
    "TIER_MUST_RECOMPUTE",
    "TIER_STALE_INFORMATIVE",
    "decide_reuse",
]
