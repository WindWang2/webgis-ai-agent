"""WorkingContextStore — durable mission-scoped working context (ADR-0206 D3).

Revision-CAS store. Deliberately **not** lease-fenced: chat turns are the
writers and must never touch the mission lease (acquire_lease bumps the
epoch, which would fence out live swarm workers). Single-writer-per-session
plus integer CAS covers the multi-writer corner (two sessions attached to
one mission).

Terminal missions are purged lazily on load (authoritative — missions can
terminate in any worker) and eagerly by MissionRuntimeService hooks
(hygiene).
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from sqlalchemy import create_engine  # noqa: F401 — typing parity, unused
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.gis_context import GISWorkingContextRow
from app.services.gis_context.working_context import (
    GISWorkingContext,
    MAX_DECISIONS,
)

logger = logging.getLogger(__name__)

MAX_SAVE_ATTEMPTS = 2


class WorkingContextConflict(Exception):
    """CAS lost after retries — caller may re-load and re-apply."""


class WorkingContextStore:
    """Durable store for :class:`GISWorkingContext` (injectable factory)."""

    def __init__(self, factory: Optional[Callable[[], Session]] = None) -> None:
        self._factory = factory

    def _sf(self):
        if self._factory is not None:
            return self._factory()
        from app.core.database import SessionLocal

        return SessionLocal()

    # ── read ────────────────────────────────────────────────────────────

    def load(self, mission_id: str, *, org_id: str = "") -> Optional[GISWorkingContext]:
        """Load the active working context. Org check is fail-closed: a row
        that carries an org is only visible to that org — an unknown
        (empty) requester org is refused, never wild-carded."""
        mid = str(mission_id or "")[:64]
        if not mid:
            return None
        try:
            with self._sf() as db:
                row = (
                    db.query(GISWorkingContextRow)
                    .filter(GISWorkingContextRow.mission_id == mid)
                    .first()
                )
                if row is None or row.state != "active":
                    return None
                if str(row.org_id or "") != str(org_id or ""):
                    # Scope mismatch (including empty requester org vs
                    # org-carrying row): never cross org boundaries.
                    return None
                return GISWorkingContext.from_payload(dict(row.payload or {}))
        except OperationalError as exc:
            logger.warning("[gis_context] load failed for %s: %s", mid, type(exc).__name__)
            return None
        except ValueError:
            # Corrupted payload (schema drift / truncation) — treat as
            # absent rather than poisoning the turn; next save re-grounds.
            logger.warning("[gis_context] corrupt payload for %s — ignored", mid)
            return None

    # ── write ───────────────────────────────────────────────────────────

    def save(
        self,
        wc: GISWorkingContext,
        *,
        expected_revision: Optional[int] = None,
    ) -> GISWorkingContext:
        """Insert-or-update with revision CAS and an org guard on every
        write path (an update may never touch a row belonging to another
        org — P0-2). On CAS loss or concurrent first-insert, reloads the
        winner, re-bases the payload onto it and retries once; raises
        WorkingContextConflict when the re-base cannot converge.
        """
        mid = str(wc.mission_id or "")[:64]
        if not mid:
            raise ValueError("mission_id_required")
        payload = wc.payload()  # size gate raises before any DB work
        for attempt in range(MAX_SAVE_ATTEMPTS):
            try:
                with self._sf() as db:
                    row = (
                        db.query(GISWorkingContextRow)
                        .filter(GISWorkingContextRow.mission_id == mid)
                        .with_for_update()
                        .first()
                    )
                    if row is None:
                        row = GISWorkingContextRow(
                            mission_id=mid,
                            org_id=str(wc.org_id or ""),
                            project_id=wc.project_id or None,
                            user_id=str(wc.user_id or ""),
                            state="active",
                            revision=wc.revision,
                            goal_revision_mirror=int(wc.goal_revision_mirror),
                            schema_version=wc.schema_version,
                            payload=payload,
                        )
                        db.add(row)
                        db.commit()
                        return wc
                    if str(row.org_id or "") != str(wc.org_id or ""):
                        # Row belongs to another org — never overwrite.
                        raise WorkingContextConflict("org_mismatch")
                    current = int(row.revision or 1)
                    if expected_revision is not None and current != int(expected_revision):
                        continue  # CAS lost → re-base below
                    if row.state != "active":
                        raise WorkingContextConflict("working_context_purged")
                    row.payload = payload
                    row.revision = wc.revision
                    row.goal_revision_mirror = int(wc.goal_revision_mirror)
                    row.project_id = wc.project_id or None
                    row.user_id = str(wc.user_id or "")
                    db.commit()
                    return wc
            except OperationalError as exc:
                logger.warning(
                    "[gis_context] save attempt %d failed for %s: %s",
                    attempt + 1, mid, type(exc).__name__,
                )
                if attempt + 1 >= MAX_SAVE_ATTEMPTS:
                    return wc  # fail-open persistence, fail-closed semantics upstream
            except IntegrityError:
                # Concurrent first-insert of the same PK → the other
                # writer won the row; re-base onto it (P1-2).
                logger.info("[gis_context] insert race on %s — rebasing", mid)
        # CAS lost / insert race on every attempt → re-base onto the stored winner.
        stored = self.load(mid, org_id=str(wc.org_id or ""))
        if stored is None:
            raise WorkingContextConflict("working_context_vanished")
        rebased = _rebase(stored, wc)
        rebased = rebased.next_revision()
        return self._save_final(rebased)

    def _save_final(self, wc: GISWorkingContext) -> GISWorkingContext:
        mid = wc.mission_id
        payload = wc.payload()
        try:
            with self._sf() as db:
                row = (
                    db.query(GISWorkingContextRow)
                    .filter(
                        GISWorkingContextRow.mission_id == mid,
                        GISWorkingContextRow.org_id == str(wc.org_id or ""),
                    )
                    .with_for_update()
                    .first()
                )
                if row is None:
                    raise WorkingContextConflict("working_context_vanished")
                if row.state != "active":
                    raise WorkingContextConflict("working_context_purged")
                row.payload = payload
                row.revision = wc.revision
                row.goal_revision_mirror = int(wc.goal_revision_mirror)
                db.commit()
                return wc
        except OperationalError as exc:
            logger.warning("[gis_context] rebase save failed for %s: %s", mid, type(exc).__name__)
            return wc

    # ── purge ───────────────────────────────────────────────────────────

    def purge(self, mission_id: str) -> bool:
        """Tombstone the row (payload dropped). Idempotent."""
        mid = str(mission_id or "")[:64]
        if not mid:
            return False
        try:
            with self._sf() as db:
                row = (
                    db.query(GISWorkingContextRow)
                    .filter(GISWorkingContextRow.mission_id == mid)
                    .first()
                )
                if row is None or row.state == "purged":
                    return False
                row.state = "purged"
                row.payload = {}
                db.commit()
                return True
        except OperationalError as exc:
            logger.warning("[gis_context] purge failed for %s: %s", mid, type(exc).__name__)
            return False


def _rebase(stored: GISWorkingContext, incoming: GISWorkingContext) -> GISWorkingContext:
    """Re-base an incoming mutation onto the stored winner.

    Decisions/findings/user_edits are unioned (stored history wins on
    conflicts — it may be newer than what the loser observed); basis and
    stale come from the incoming observation (fresher). Budgets enforced by
    the bounded model constructors.
    """
    merged = stored.model_copy(deep=True)
    merged.basis = incoming.basis.model_copy(deep=True)
    merged.stale = dict(incoming.stale)
    merged.updated_turn_id = incoming.updated_turn_id
    merged.goal_revision_mirror = incoming.goal_revision_mirror

    def _union(existing, incoming_items, key):
        seen = {key(item) for item in existing}
        for item in incoming_items:
            if key(item) not in seen:
                seen.add(key(item))
                existing.append(item)
        return existing

    def _cap(items, cap):
        return items[:cap]

    merged.accepted_assumptions = _cap(
        _union(merged.accepted_assumptions, incoming.accepted_assumptions,
               lambda d: (d.text, d.turn_id)),
        MAX_DECISIONS)
    merged.rejected_alternatives = _cap(
        _union(merged.rejected_alternatives, incoming.rejected_alternatives,
               lambda d: (d.text, d.turn_id)),
        MAX_DECISIONS)
    merged.unresolved_constraints = _cap(
        _union(merged.unresolved_constraints, incoming.unresolved_constraints,
               lambda d: (d.text, d.turn_id)),
        MAX_DECISIONS)
    for f in incoming.findings:
        merged.upsert_finding(f.claim_id, f.status, f.basis_revision)
    for e in incoming.user_edits:
        merged.add_user_edit(layer_id=e.layer_id, kind=e.kind, turn_id=e.turn_id)
    return merged


__all__ = [
    "WorkingContextConflict",
    "WorkingContextStore",
]
