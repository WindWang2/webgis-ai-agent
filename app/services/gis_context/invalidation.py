"""Working-context invalidation engine (ADR-0206 D4 / Direction 06 M3).

Closed rule table: which accepted facts die when the observed world drifts.
Two invariants:

- **Fail-closed against stale reuse** — a finding/decision accepted on an
  older basis is marked stale on any basis-affecting change; conservatism
  (over-staling) is the safe direction. USER_EDIT never stales anything
  (user-wins).
- **Claim propagation reuses the existing currency** — affected mission
  findings get their ClaimStore claims marked ``STALE`` through
  ``ClaimStore.mark_claim_status``; no fifth verdict system is created.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_context.observation import ContextChange, SessionObservation
from app.services.gis_context.working_context import GISWorkingContext

#: Change kinds that invalidate previously accepted conclusions.
_BASIS_AFFECTING = frozenset({
    "AOI_CHANGED",
    "DATASET_VERSION_CHANGED",
    "TIME_PERIOD_CHANGED",
    "CRS_CHANGED",
    "MEASURE_CHANGED",
    "PRODUCT_GOAL_CHANGED",
})

#: Change kinds that refresh the accepted basis without invalidating.
_BASIS_REFRESHING = frozenset({"BASIS_ESTABLISHED"})

#: Which wc.stale fields each change kind marks (rule table).
_STALE_FIELDS: Dict[str, tuple] = {
    "AOI_CHANGED": ("basis.aoi",),
    "DATASET_VERSION_CHANGED": ("basis.datasets", "basis.recipe_id"),
    "TIME_PERIOD_CHANGED": ("basis.time_period",),
    "CRS_CHANGED": ("basis.crs", "basis.recipe_id"),
    "MEASURE_CHANGED": ("basis.measure",),
    "PRODUCT_GOAL_CHANGED": ("basis.recipe_id", "basis.product_ref"),
    "USER_EDIT": (),
}


@dataclass
class InvalidationOutcome:
    stale_fields: Dict[str, str] = field(default_factory=dict)
    stale_claim_ids: List[str] = field(default_factory=list)
    recorded_edits: int = 0
    changes: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


def apply_changes(
    wc: GISWorkingContext,
    changes: List[ContextChange],
    *,
    obs: Optional[SessionObservation] = None,
    claim_store: Any = None,
    turn_id: str = "",
) -> InvalidationOutcome:
    """Mutate ``wc`` in place per the rule table and return the outcome.

    When ``obs`` is given the accepted basis is refreshed to it first (the
    basis always tracks the latest accepted observation; staleness lives in
    ``wc.stale`` + finding/decision ``basis_revision`` markers).
    """
    outcome = InvalidationOutcome()
    basis_changes = [c for c in changes if c.kind in _BASIS_AFFECTING]
    refreshing = [c for c in changes if c.kind in _BASIS_REFRESHING]
    if not basis_changes and not refreshing and not (obs is not None and obs.user_hidden_layers):
        return outcome

    new_revision = int(wc.revision) + 1

    # Basis refresh (before staling so markers reference the prior basis).
    if obs is not None and (basis_changes or refreshing):
        _refresh_basis(wc, obs)

    for change in basis_changes:
        outcome.changes.append(change.kind)
        for fld in _STALE_FIELDS.get(change.kind, ()):
            reason = f"{change.kind}:{change.detail}" if change.detail else change.kind
            wc.mark_stale(fld, reason)
            outcome.stale_fields[fld] = reason
        # Fail-closed: conclusions accepted on an older basis are stale.
        for f in wc.findings:
            if f.basis_revision and f.basis_revision < new_revision:
                if f.claim_id not in outcome.stale_claim_ids:
                    outcome.stale_claim_ids.append(f.claim_id)
                    f.status = "stale"
        for rec in (*wc.accepted_assumptions, *wc.rejected_alternatives,
                    *wc.unresolved_constraints):
            if rec.basis_revision and rec.basis_revision < new_revision:
                rec.stale_basis = True

    if refreshing:
        outcome.changes.append(refreshing[0].kind)
    wc.revision = new_revision
    wc.updated_turn_id = str(turn_id or "")[:64]

    # User edits: append-only, never invalidated.
    if obs is not None:
        known = {(e.layer_id, "hide") for e in wc.user_edits}
        for layer_id in obs.user_hidden_layers:
            if (layer_id, "hide") not in known:
                if wc.add_user_edit(layer_id=layer_id, kind="hide", turn_id=turn_id):
                    outcome.recorded_edits += 1
                    outcome.changes.append("USER_EDIT")

    # Claim propagation through the existing store (best-effort — the
    # process-local ClaimStore may not exist on this worker).
    if claim_store is not None and outcome.stale_claim_ids:
        _mark_claims_stale(claim_store, outcome.stale_claim_ids,
                           reason="working_context_invalidation")
    return outcome


def _mark_claims_stale(claim_store: Any, claim_ids: List[str], *, reason: str) -> int:
    marked = 0
    try:
        from app.services.gis_harness.evidence_claim.contracts import ClaimStatus

        for cid in claim_ids[:64]:
            claim = claim_store.get_claim(cid)
            if claim is None:
                continue
            if claim.status in (ClaimStatus.STALE, ClaimStatus.CONTRADICTED,
                                ClaimStatus.UNSUPPORTED):
                continue
            updated = claim.model_copy(update={
                "status": ClaimStatus.STALE,
                "narrative": (claim.narrative or "")[:200],
            })
            claim_store.upsert_claim(updated)
            marked += 1
    except Exception:  # noqa: BLE001 — propagation is best-effort; wc.stale
        return marked  # is the durable record of the invalidation
    return marked


def _refresh_basis(wc: GISWorkingContext, obs: SessionObservation) -> None:
    basis = wc.basis
    if obs.aoi_bbox is not None:
        basis.aoi_bbox = list(obs.aoi_bbox)
    if obs.aoi_name:
        basis.aoi_name = obs.aoi_name[:64]
    if obs.time_period:
        basis.time_period = obs.time_period[:64]
    if obs.crs:
        basis.crs = obs.crs[:64]
    if obs.measure_field:
        basis.measure_field = obs.measure_field[:64]
    if obs.measure_statistic:
        basis.measure_statistic = obs.measure_statistic[:32]
    if obs.recipe_id:
        basis.recipe_id = obs.recipe_id[:64]
    if obs.export_format:
        basis.export_format = obs.export_format[:32]
    if obs.datasets:
        merged = {ds.ref_id: ds for ds in basis.datasets}
        for ds in obs.datasets:
            merged[ds.ref_id] = ds
        basis.datasets = sorted(merged.values(), key=lambda d: d.ref_id)[:12]


__all__ = [
    "InvalidationOutcome",
    "apply_changes",
]
