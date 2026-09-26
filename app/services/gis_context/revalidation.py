"""Evidence-backed revalidation engine (ADR-0215 D1–D4).

The write-authority half of context freshness: the *only* code path that can
flip a stale fact back to current. Every attempt — successful or not —
appends a :class:`RevalidationReceipt` to the working context's bounded ring,
so "stale → current" is always an evidence-checked, replayable transition
and never a narrative upgrade: callers may only *name* a claim / decision /
field; every check re-reads live state (claim store, deterministic verifier,
observation) inside the engine.

Closed restore kinds:

- ``CLAIM_REVERIFIED`` — a stale finding's claim is re-resolved from the
  claim store, basis dataset liveness is re-checked against the recorded
  authoritative fingerprints, and the deterministic
  ``evidence_claim.verify_claim`` re-runs (persisting the authoritative
  status). Only a fresh SUPPORTED verdict restores.
- ``DECISION_REAFFIRM`` — exact-text match against a stale decision
  (match-only: this path can never invent decisions).
- ``BASIS_RECONFIRMED`` — passive marker maintenance: a stale marker clears
  only when every fact attributed to that field has left the stale set and
  the observation currently knows the field. ``goal`` is intentionally
  outside the closed set (goal revisions re-ground through new work, not
  through re-confirmation).

Loop protection is state-based (D4): an already-restored target yields a
``not_stale`` no-op receipt, so repeated calls converge; per-turn marker
reconfirmations are capped. There is no TTL anywhere — a restored fact
re-stales through the ordinary invalidation engine on the next drift.
"""
from __future__ import annotations

import hashlib
from typing import Any, List, Optional

from app.services.gis_context.observation import SessionObservation
from app.services.gis_context.working_context import (
    CheckResult,
    DecisionRecord,
    EvidenceRef,
    GISWorkingContext,
    RevalidationReceipt,
)

#: Closed marker families eligible for passive reconfirmation (ADR-0215 D1).
RECONFIRMABLE_FIELDS = frozenset({
    "basis.aoi",
    "basis.datasets",
    "basis.recipe_id",
    "basis.time_period",
    "basis.crs",
    "basis.measure",
    "basis.product_ref",
})

#: Closed reject-reason codes (reason-grade observability contract).
REJECT_TARGET_UNKNOWN = "target_unknown"
REJECT_NOT_STALE = "not_stale"
REJECT_NO_CLAIM_STORE = "no_claim_store"
REJECT_CLAIM_UNVERIFIABLE = "claim_unverifiable"
REJECT_CLAIM_NOT_SUPPORTED = "claim_not_supported"
REJECT_BASIS_ADVANCED = "basis_advanced"
REJECT_ATTRIBUTED_STALE_REMAINING = "attributed_stale_remaining"
REJECT_FIELD_UNOBSERVED = "field_unobserved"

MAX_PASSIVE_MARKERS = 4

KIND_CLAIM = "CLAIM_REVERIFIED"
KIND_DECISION = "DECISION_REAFFIRM"
KIND_MARKER = "BASIS_RECONFIRMED"


def _digest(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _obs_knows_field(wc: GISWorkingContext, field: str, obs: SessionObservation) -> tuple:
    """(known, evidence_ref) — does the current observation actually carry
    the field? Unknown fields are never reconfirmed (fail-closed)."""
    if field == "basis.aoi":
        if obs.aoi_bbox is not None:
            return True, EvidenceRef(ref="obs.aoi", token=",".join(
                f"{float(v):.6f}" for v in obs.aoi_bbox)) 
        return False, None
    if field == "basis.time_period":
        return (bool(obs.time_period), EvidenceRef(ref="obs.time", token=obs.time_period) if obs.time_period else None)
    if field == "basis.crs":
        return (bool(obs.crs), EvidenceRef(ref="obs.crs", token=obs.crs) if obs.crs else None)
    if field == "basis.measure":
        token = obs.measure_field or obs.measure_statistic
        return (bool(token), EvidenceRef(ref="obs.measure", token=token) if token else None)
    if field in ("basis.recipe_id", "basis.product_ref"):
        return (bool(obs.recipe_id), EvidenceRef(ref="obs.recipe", token=obs.recipe_id) if obs.recipe_id else None)
    if field == "basis.datasets":
        if obs.datasets:
            # Content-addressed digest of what the observation actually
            # carries (ref@revision), not a bare count.
            raw = ";".join(sorted(
                f"{d.ref_id}@{d.content_revision}" for d in obs.datasets))
            return True, EvidenceRef(ref="obs.datasets", token=_digest(raw))
        return False, None
    return False, None


def _stale_facts_blocking(wc: GISWorkingContext, field: Optional[str]) -> bool:
    """True when a stale fact still blocks marker reconfirmation.

    With ``field`` given: any fact explicitly attributed to that field, or
    any *legacy unattributed* stale fact (pre-v2 payloads carry no
    attribution — conservatively they block every marker clear until the
    context completes one full re-verification round).
    """
    for f in wc.findings:
        if f.status == "stale":
            if field is None or not f.stale_reasons or field in f.stale_reasons:
                return True
    for rec in (*wc.accepted_assumptions, *wc.rejected_alternatives, *wc.unresolved_constraints):
        if rec.stale_basis:
            if field is None or not rec.stale_reasons or field in rec.stale_reasons:
                return True
    return False


def _rejected(wc: GISWorkingContext, *, kind: str, target: str, reason: str,
              turn_id: str, prior_reason: str = "",
              record: bool = True) -> RevalidationReceipt:
    receipt = RevalidationReceipt(
        receipt_id="", kind=kind, target=str(target or "")[:64],
        basis_revision=int(wc.revision), verdict="rejected",
        reject_reason=str(reason)[:48], prior_reason=str(prior_reason or "")[:96],
        turn_id=str(turn_id or "")[:64],
    )
    if record:
        return wc.append_receipt(receipt)
    # Passive callers keep rejections in-memory (observability counts only) —
    # a persistent blocker must not flood the bounded ring or turn every
    # read-mostly turn into a write.
    receipt.receipt_id = "rtv-passive"
    return receipt


# ── CLAIM_REVERIFIED ─────────────────────────────────────────────────────

def revalidate_claims(
    wc: GISWorkingContext,
    claim_ids: List[str],
    *,
    claim_store: Any = None,
    token_resolver: Any = None,
    turn_id: str = "",
    max_claims: int = 8,
) -> List[RevalidationReceipt]:
    """Active re-verification of stale findings (tool-facing entry).

    Bounded to ``max_claims`` ids per call; every id produces exactly one
    receipt. The engine re-reads the claim, re-checks basis dataset
    liveness (via ``token_resolver(ref_id) -> live token | None`` — the
    hot path binds it to ``live_version_token``) and re-runs the
    deterministic verifier — caller-supplied narrative is never consulted.
    """
    receipts: List[RevalidationReceipt] = []
    for cid in claim_ids[:max(0, int(max_claims))]:
        cid = str(cid or "")[:64]
        if not cid:
            continue
        finding = next((f for f in wc.findings if f.claim_id == cid), None)
        if finding is None:
            receipts.append(_rejected(
                wc, kind=KIND_CLAIM, target=cid, reason=REJECT_TARGET_UNKNOWN,
                turn_id=turn_id))
            continue
        if finding.status != "stale":
            receipts.append(_rejected(
                wc, kind=KIND_CLAIM, target=cid, reason=REJECT_NOT_STALE,
                turn_id=turn_id))
            continue
        prior = ";".join(finding.stale_reasons)[:96]
        if claim_store is None:
            receipts.append(_rejected(
                wc, kind=KIND_CLAIM, target=cid, reason=REJECT_NO_CLAIM_STORE,
                turn_id=turn_id, prior_reason=prior))
            continue
        try:
            claim = claim_store.get_claim(cid)
        except Exception:  # noqa: BLE001 — store failure is not an upgrade
            claim = None
        if claim is None:
            receipts.append(_rejected(
                wc, kind=KIND_CLAIM, target=cid, reason=REJECT_CLAIM_UNVERIFIABLE,
                turn_id=turn_id, prior_reason=prior))
            continue

        checks: List[CheckResult] = []
        evidence: List[EvidenceRef] = [EvidenceRef(ref="claim", token=cid)]

        # Basis dataset liveness — the datasets this mission accepted the
        # finding under must not have moved since (recorded authoritative
        # token vs live token; unknown tokens are skipped, never guessed).
        basis_ok = True
        if token_resolver is not None:
            for ds in wc.basis.datasets:
                if not ds.version_fingerprint:
                    continue
                try:
                    live = token_resolver(ds.ref_id)
                except Exception:  # noqa: BLE001 — liveness is a guard
                    live = None
                if live is None:
                    continue
                if str(live) != ds.version_fingerprint:
                    basis_ok = False
                    evidence.append(EvidenceRef(ref=f"ds:{ds.ref_id}", token=str(live)[:96]))
                    break
        checks.append(CheckResult(
            check="basis_liveness", verdict="pass" if basis_ok else "fail",
            detail="accepted datasets unchanged" if basis_ok else "authority token advanced"))
        if not basis_ok:
            receipts.append(_rejected(
                wc, kind=KIND_CLAIM, target=cid, reason=REJECT_BASIS_ADVANCED,
                turn_id=turn_id, prior_reason=prior))
            wc.revalidations[-1].checks = checks[:4]
            wc.revalidations[-1].evidence = evidence[:4]
            continue

        # Deterministic re-verification (the authoritative verdict path).
        try:
            from app.services.gis_harness.evidence_claim.verify import verify_claim

            result = verify_claim(claim, claim_store, persist_status=True)
        except Exception:  # noqa: BLE001 — verifier failure is not an upgrade
            receipts.append(_rejected(
                wc, kind=KIND_CLAIM, target=cid, reason=REJECT_CLAIM_UNVERIFIABLE,
                turn_id=turn_id, prior_reason=prior))
            continue
        verify_digest = (
            f"{result.status.value}:{len(result.steps)}:"
            + ",".join(result.reasons[:3])
        )[:96]
        evidence.append(EvidenceRef(ref="verify", token=verify_digest))
        supported = result.status.value == "supported" and result.positive_proof
        checks.append(CheckResult(
            check="claim_verify", verdict="pass" if supported else "fail",
            detail=f"status={result.status.value}"))
        if not supported:
            receipts.append(_rejected(
                wc, kind=KIND_CLAIM, target=cid, reason=REJECT_CLAIM_NOT_SUPPORTED,
                turn_id=turn_id, prior_reason=prior))
            wc.revalidations[-1].checks = checks[:4]
            wc.revalidations[-1].evidence = evidence[:4]
            continue

        # Restore: re-stamp the finding onto the current basis revision so
        # the next drift re-stales it through the ordinary engine.
        wc.revision = int(wc.revision) + 1
        finding.status = "supported"
        finding.basis_revision = int(wc.revision)
        finding.stale_reasons = []
        receipts.append(wc.append_receipt(RevalidationReceipt(
            receipt_id="", kind=KIND_CLAIM, target=cid,
            basis_revision=int(wc.revision), verdict="restored",
            prior_reason=prior, turn_id=str(turn_id or "")[:64],
            evidence=evidence[:4], checks=checks[:4],
        )))
    return receipts


# ── DECISION_REAFFIRM ────────────────────────────────────────────────────

def reaffirm_decisions(
    wc: GISWorkingContext,
    texts: List[str],
    *,
    turn_id: str = "",
    max_decisions: int = 4,
) -> List[RevalidationReceipt]:
    """Re-accept stale decisions by exact text match (match-only)."""
    receipts: List[RevalidationReceipt] = []
    for raw in texts[:max(0, int(max_decisions))]:
        text = str(raw or "").strip()[:200]
        if not text:
            continue
        target = f"dec:{_digest(text)}"
        hit: Optional[DecisionRecord] = None
        for rec in (*wc.accepted_assumptions, *wc.rejected_alternatives,
                    *wc.unresolved_constraints):
            if rec.text.strip() == text:
                hit = rec
                break
        if hit is None:
            receipts.append(_rejected(
                wc, kind=KIND_DECISION, target=target,
                reason=REJECT_TARGET_UNKNOWN, turn_id=turn_id))
            continue
        if not hit.stale_basis:
            receipts.append(_rejected(
                wc, kind=KIND_DECISION, target=target,
                reason=REJECT_NOT_STALE, turn_id=turn_id))
            continue
        wc.revision = int(wc.revision) + 1
        hit.stale_basis = False
        hit.basis_revision = int(wc.revision)
        prior = ";".join(hit.stale_reasons)
        hit.stale_reasons = []
        receipts.append(wc.append_receipt(RevalidationReceipt(
            receipt_id="", kind=KIND_DECISION, target=target,
            basis_revision=int(wc.revision), verdict="restored",
            prior_reason=prior, turn_id=str(turn_id or "")[:64],
            evidence=[EvidenceRef(ref="decision", token=target[:64])],
            checks=[CheckResult(check="exact_text_match", verdict="pass")],
        )))
    return receipts


# ── BASIS_RECONFIRMED (passive) ──────────────────────────────────────────

def reconfirm_markers(
    wc: GISWorkingContext,
    obs: SessionObservation,
    *,
    turn_id: str = "",
    max_markers: int = MAX_PASSIVE_MARKERS,
    record_rejections: bool = True,
) -> List[RevalidationReceipt]:
    """Passive hot-path pass: clear stale markers whose attributed facts are
    all resolved and whose field the observation currently confirms.

    ``record_rejections=False`` (hot-path default) keeps rejections
    in-memory — only restores mutate the persisted context."""
    receipts: List[RevalidationReceipt] = []
    restored_count = 0
    for field in sorted(wc.stale):
        if restored_count >= max(0, int(max_markers)):
            break
        if field not in RECONFIRMABLE_FIELDS:
            continue
        prior_reason = wc.stale.get(field, "")
        blocking = _stale_facts_blocking(wc, field)
        known, obs_ref = _obs_knows_field(wc, field, obs)
        checks = [
            CheckResult(
                check="facts_resolved", verdict="pass" if not blocking else "fail",
                detail="no stale facts attributed" if not blocking
                else "stale facts still attributed"),
            CheckResult(
                check="observation_confirms", verdict="pass" if known else "fail",
                detail="field observed" if known else "field unknown"),
        ]
        evidence = [obs_ref] if obs_ref is not None else []
        if not known:
            receipts.append(_receipt_with_checks(
                wc, field=field, reason=REJECT_FIELD_UNOBSERVED, turn_id=turn_id,
                prior_reason=prior_reason, checks=checks, evidence=evidence,
                record=record_rejections))
            continue
        if blocking:
            receipts.append(_receipt_with_checks(
                wc, field=field, reason=REJECT_ATTRIBUTED_STALE_REMAINING,
                turn_id=turn_id, prior_reason=prior_reason,
                checks=checks, evidence=evidence,
                record=record_rejections))
            continue
        wc.revision = int(wc.revision) + 1
        wc.clear_stale(field)
        receipts.append(wc.append_receipt(RevalidationReceipt(
            receipt_id="", kind=KIND_MARKER, target=field,
            basis_revision=int(wc.revision), verdict="restored",
            prior_reason=prior_reason, turn_id=str(turn_id or "")[:64],
            evidence=evidence[:4], checks=checks[:4],
        )))
        restored_count += 1
    return receipts


def _receipt_with_checks(
    wc: GISWorkingContext, *, field: str, reason: str, turn_id: str,
    prior_reason: str, checks: List[CheckResult], evidence: List[EvidenceRef],
    record: bool = True,
) -> RevalidationReceipt:
    receipt = _rejected(
        wc, kind=KIND_MARKER, target=field, reason=reason, turn_id=turn_id,
        prior_reason=prior_reason, record=record)
    receipt.checks = checks[:4]
    receipt.evidence = evidence[:4]
    return receipt


__all__ = [
    "KIND_CLAIM",
    "KIND_DECISION",
    "KIND_MARKER",
    "MAX_PASSIVE_MARKERS",
    "RECONFIRMABLE_FIELDS",
    "REJECT_ATTRIBUTED_STALE_REMAINING",
    "REJECT_BASIS_ADVANCED",
    "REJECT_CLAIM_NOT_SUPPORTED",
    "REJECT_CLAIM_UNVERIFIABLE",
    "REJECT_FIELD_UNOBSERVED",
    "REJECT_NOT_STALE",
    "REJECT_NO_CLAIM_STORE",
    "REJECT_TARGET_UNKNOWN",
    "reaffirm_decisions",
    "reconfirm_markers",
    "revalidate_claims",
]
