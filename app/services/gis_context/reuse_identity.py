"""Reuse identity — fingerprint-fed bridge between the working context and
the project_knowledge reuse verdicts (ADR-0215 D5/D10).

Two responsibilities, both deterministic and bounded:

- :func:`reconcile_dataset_fingerprints` — record the *authoritative*
  version tokens (`ProjectDataset.version_fingerprint` via
  ``live_version_token``) on the accepted basis datasets at acceptance
  time, and surface authority-side drift the MapSpec-side diff cannot see
  (the dataset under an unchanged MapSpec was re-versioned). Learning a
  token is not drift; a recorded token that no longer matches live is.
- :func:`reuse_query_from_context` — build the full
  ``project_knowledge.ReuseQuery`` from the working basis: bbox, temporal
  label and the accepted dataset fingerprints, so retrieval's request-level
  input liveness downgrade (`request_input_stale` / `request_input_gone`)
  fires for genuinely stale planner inputs. Unknown fingerprints are
  omitted (honest `upstream_unverified`), never guessed; ``method_key``
  stays None unless the basis carries a real method key.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.gis_context.working_context import (
    GISWorkingContext,
    MAX_BASIS_DATASETS,
)

logger = logging.getLogger(__name__)

MAX_RESOLVE_LOOKUPS = MAX_BASIS_DATASETS * 2  # ref_id + alias fallback per dataset


class ReconcileResult:
    """Outcome of one fingerprint reconciliation pass.

    ``events`` are invalidation-shaped change dicts for the engine;
    ``dataset_fingerprints`` maps **resolved authority ids** (project_dataset
    ids — ref_id when it resolves, else the alias) to their live tokens —
    exactly the key namespace retrieval's ref-tags and request-level
    liveness check speak. Unresolved datasets appear in neither.
    ``mutated`` is True when this pass wrote to the context (first-time
    token learning or drift adoption) — the caller's save-condition input.
    """

    __slots__ = ("events", "dataset_fingerprints", "mutated")

    def __init__(self) -> None:
        self.events: List[Dict[str, str]] = []
        self.dataset_fingerprints: Dict[str, str] = {}
        self.mutated = False

    def __iter__(self):
        return iter(self.events)


def _live_token(db: Any, *, project_id: str, authority_id: str) -> Optional[str]:
    from app.services.project_knowledge.liveness import live_version_token

    return live_version_token(
        db, project_id=project_id,
        authority_store="project_dataset", authority_id=str(authority_id or "")[:64],
    )


def reconcile_dataset_fingerprints(
    wc: GISWorkingContext,
    db: Any,
    *,
    project_id: str,
) -> ReconcileResult:
    """Record authoritative tokens on basis datasets; report authority-side
    drift as bounded change dicts (``{kind, detail, ref_id}`` shaped like
    ``observation.ContextChange`` so the invalidation engine consumes them
    unchanged).

    Semantics (ADR-0215 D10):
    - authority unknown → nothing recorded (honest unknown, never a guess);
    - token recorded and unchanged → no event;
    - token empty → record it (learning, not drift);
    - token recorded and live differs → ``DATASET_VERSION_CHANGED`` event
      (the recorded value is NOT updated here — the invalidation engine's
      basis refresh owns the new acceptance);
    - authority gone → nothing here: the reuse query simply omits it.
    """
    result = ReconcileResult()
    lookups = 0
    for ds in wc.basis.datasets[:MAX_BASIS_DATASETS]:
        if lookups >= MAX_RESOLVE_LOOKUPS:
            break
        ref_id = str(ds.ref_id or "")[:64]
        if not ref_id:
            continue
        token: Optional[str] = None
        matched_id = ""
        try:
            live = _live_token(db, project_id=project_id, authority_id=ref_id)
            lookups += 1
            if live is not None:
                token = str(live or "")
                matched_id = ref_id
            elif ds.alias and ds.alias != ref_id and lookups < MAX_RESOLVE_LOOKUPS:
                live = _live_token(db, project_id=project_id, authority_id=ds.alias)
                lookups += 1
                if live is not None:
                    token = str(live or "")
                    matched_id = str(ds.alias)[:64]
        except Exception:  # noqa: BLE001 — liveness probing never blocks the turn
            continue
        if token is None:
            continue
        if token:
            result.dataset_fingerprints[matched_id] = token[:128]
        if not ds.version_fingerprint or not ds.authority_id:
            if token:
                ds.version_fingerprint = token[:128]
                ds.authority_id = matched_id
                result.mutated = True
            continue
        if token and token != ds.version_fingerprint:
            # Authority-side drift: the engine re-accepts the dataset at the
            # new authority version (mirroring _refresh_basis's adoption of
            # new content revisions) while the emitted event stales every
            # conclusion accepted under the old token — fail-closed, and
            # the new token stops the event from re-firing next turn.
            result.events.append({
                "kind": "DATASET_VERSION_CHANGED",
                "detail": f"{ref_id}:authority_drift"[:96],
                "ref_id": ref_id,
            })
            ds.version_fingerprint = token[:128]
            ds.authority_id = matched_id
            result.mutated = True
    return result


def accepted_fingerprints(wc: GISWorkingContext) -> Dict[str, str]:
    """The tokens the mission *accepted* its datasets under, keyed by their
    resolved authority ids — the reuse-query "claimed" side of the liveness
    comparison.

    Callers must snapshot this BEFORE :func:`reconcile_dataset_fingerprints`
    adopts drift: retrieval's request-level check compares claimed vs live,
    which only downgrades when the claimed value is the *accepted* token
    (ADR-0215 D5). Datasets never resolved (no authoritative id) are
    omitted — honest `upstream_unverified` downstream.
    """
    return {
        str(ds.authority_id or ds.ref_id)[:64]: str(ds.version_fingerprint)[:128]
        for ds in wc.basis.datasets[:MAX_BASIS_DATASETS]
        if ds.version_fingerprint
    }


def reuse_query_from_context(
    wc: GISWorkingContext,
    *,
    dataset_fingerprints: Optional[Dict[str, str]] = None,
    limit: int = 3,
) -> Any:
    """Build the fingerprint-fed ``ReuseQuery`` from the working basis.

    ``dataset_fingerprints`` defaults to :func:`accepted_fingerprints`;
    callers that ran :func:`reconcile_dataset_fingerprints` this turn pass
    the snapshot taken *before* it (P1-3: claiming freshly-resolved live
    tokens would make the request-level liveness check tautological).
    """
    from app.services.project_knowledge.contract import ReuseQuery

    if dataset_fingerprints is None:
        dataset_fingerprints = accepted_fingerprints(wc)

    method_key = None
    if ":" in (wc.basis.recipe_id or ""):
        # Only a real capability:algorithm key is a method identity; bare
        # recipe ids stay unknown (never guessed into the verdict).
        method_key = wc.basis.recipe_id[:200]

    return ReuseQuery(
        bbox=wc.basis.aoi_bbox,
        temporal_label=wc.basis.time_period or None,
        method_key=method_key,
        dataset_fingerprints=dataset_fingerprints or None,
        limit=limit,
    )


__all__ = [
    "MAX_RESOLVE_LOOKUPS",
    "ReconcileResult",
    "accepted_fingerprints",
    "reconcile_dataset_fingerprints",
    "reuse_query_from_context",
]
