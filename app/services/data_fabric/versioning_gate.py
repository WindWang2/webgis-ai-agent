"""ads-v1 version pinning & drift governance (DS5, ADR-0175, gaps A5/A6).

Same request, same data: ``version_pin`` freezes a snapshot at acquisition
time (content + schema fingerprints + revision evidence from
``app.lib.data.versioning``) and replays it byte-identically on later
requests with the same pin. ``latest`` stays honest — explicit, unversioned.

Schema drift (gap A6): the pinned snapshot's field table is diffed against
the live source's fields on every gated acquisition — four classes
(``added_column`` / ``removed_column`` / ``type_change`` / ``rename_suspect``)
with severity grading (breaking / degraded / info) and, for rename suspects,
**suggestions only** (confidence + needs_human_confirmation; automatic
renaming is forbidden — silently changing semantics is worse than failing).

Impact analysis: a detected drift resolves to the affected registered
artifacts via the project lineage table (``source_dataset_id`` links), so the
output is an actionable list, not just an alarm.

Blocking policy: ``ADS_DRIFT_BLOCKING`` (default off until the DS8 baseline
is stable) — when on, breaking drift blocks the acquisition (typed error);
when off, the drift is recorded on the D4 fact and the acquisition proceeds
(flagged). The switch is env-driven and reversible.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from app.lib.data.versioning import SourceRevision

# ── Drift model ──────────────────────────────────────────────────────────────


class RenameSuggestion(BaseModel):
    model_config = ConfigDict(extra="allow")

    from_column: str
    to_column: str
    confidence: float = Field(ge=0.0, le=1.0)
    needs_human_confirmation: bool = True  # never auto-applied (DS5.5)


class DriftReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    dataset_key: str
    pin: Optional[str] = None
    change_class: str  # none / added_column / removed_column / type_change / rename_suspect
    severity: str = "info"  # breaking / degraded / info
    added: List[str] = Field(default_factory=list)
    removed: List[str] = Field(default_factory=list)
    type_changes: Dict[str, Dict[str, str]] = Field(default_factory=dict)  # col → {from,to}
    rename_suggestions: List[RenameSuggestion] = Field(default_factory=list)
    old_schema_fingerprint: Optional[str] = None
    new_schema_fingerprint: Optional[str] = None
    detected_at: str = ""
    affected_artifacts: List[str] = Field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return self.change_class != "none"


#: Severity mapping (single point; DS8 revisits after the baseline).
SEVERITY = {
    "none": "info",
    "added_column": "info",
    "removed_column": "breaking",
    "type_change": "breaking",
    "rename_suspect": "degraded",
}

_RENAME_SIMILARITY = 0.6  # provisional; DS8 calibrates


def schema_fingerprint(fields: Dict[str, str]) -> str:
    """Deterministic fingerprint over the column table (name:type pairs)."""
    canonical = json.dumps(sorted(fields.items()), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def suggest_renames(
    old_fields: Dict[str, str], new_fields: Dict[str, str]
) -> List[RenameSuggestion]:
    """Pair removed columns with added ones of the same declared type and
    similar names. Data-only suggestions — never auto-applied."""
    removed = [c for c in old_fields if c not in new_fields]
    added = [c for c in new_fields if c not in old_fields]
    out: List[RenameSuggestion] = []
    for r in removed:
        best: Optional[RenameSuggestion] = None
        for a in added:
            if old_fields[r] != new_fields[a]:
                continue  # renames keep the type; type changes are not renames
            ratio = difflib.SequenceMatcher(None, r.lower(), a.lower()).ratio()
            if ratio < _RENAME_SIMILARITY:
                continue
            confidence = round(min(0.95, ratio), 2)
            if best is None or confidence > best.confidence:
                best = RenameSuggestion(
                    from_column=r, to_column=a, confidence=confidence,
                    needs_human_confirmation=True,
                )
        if best is not None:
            out.append(best)
    return out


def detect_drift(
    old_fields: Dict[str, str],
    new_fields: Dict[str, str],
    *,
    dataset_key: str,
    pin: Optional[str] = None,
    old_schema_fingerprint: Optional[str] = None,
) -> DriftReport:
    """Field-table diff → DriftReport (the four classes, severity graded)."""
    added = sorted(c for c in new_fields if c not in old_fields)
    removed = sorted(c for c in old_fields if c not in new_fields)
    type_changes = {
        c: {"from": old_fields[c], "to": new_fields[c]}
        for c in sorted(set(old_fields) & set(new_fields))
        if old_fields[c] != new_fields[c]
    }
    suggestions = suggest_renames(old_fields, new_fields)
    # rename_suspect wins when every removal has a paired suggestion
    if removed and added and len(suggestions) == len(removed) and not type_changes:
        change_class = "rename_suspect"
    elif type_changes:
        change_class = "type_change"
    elif removed:
        change_class = "removed_column"
    elif added:
        change_class = "added_column"
    else:
        change_class = "none"
    new_fp = schema_fingerprint(new_fields)
    return DriftReport(
        dataset_key=dataset_key,
        pin=pin,
        change_class=change_class,
        severity=SEVERITY[change_class],
        added=added,
        removed=removed,
        type_changes=type_changes,
        rename_suggestions=suggestions,
        old_schema_fingerprint=old_schema_fingerprint or (schema_fingerprint(old_fields) if old_fields else None),
        new_schema_fingerprint=new_fp,
        detected_at=datetime.now(timezone.utc).isoformat(),
    )


def drift_blocking_enabled() -> bool:
    """``ADS_DRIFT_BLOCKING`` env switch (default off until DS8 stabilises)."""
    return os.environ.get("ADS_DRIFT_BLOCKING", "").strip().lower() in {"1", "true", "yes", "on"}


# ── Snapshot store ───────────────────────────────────────────────────────────


class SnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    dataset_key: str
    pin: str
    version_token: str
    content_fingerprint: Optional[str] = None
    schema_fields: Dict[str, str] = Field(default_factory=dict)
    revision: Optional[Dict[str, Any]] = None
    payload: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str = ""

    def schema_fingerprint_of(self) -> Optional[str]:
        return schema_fingerprint(self.schema_fields) if self.schema_fields else None


class DriftBlockedError(RuntimeError):
    """Breaking drift with blocking enabled — the acquisition is refused."""

    def __init__(self, drift: DriftReport):
        self.drift = drift
        super().__init__(
            f"breaking schema drift on {drift.dataset_key} "
            f"({drift.change_class}: {drift.removed or drift.type_changes}); "
            "blocking enabled via ADS_DRIFT_BLOCKING"
        )


class InMemorySnapshotStore:
    """Process-local snapshot backend (tests / single-worker deployments).

    The alembic table (0070) mirrors this shape for multi-worker persistence;
    the DAO over it is wired in DS8 together with the facts store.
    """

    def __init__(self) -> None:
        self._snapshots: Dict[Tuple[str, str], SnapshotRecord] = {}

    def put(self, record: SnapshotRecord) -> None:
        self._snapshots[(record.dataset_key, record.pin)] = record

    def get(self, dataset_key: str, pin: str) -> Optional[SnapshotRecord]:
        return self._snapshots.get((dataset_key, pin))

    def clear(self) -> None:
        self._snapshots.clear()




_STORE: Optional[InMemorySnapshotStore] = None


def get_snapshot_store() -> InMemorySnapshotStore:
    global _STORE
    if _STORE is None:
        _STORE = InMemorySnapshotStore()
    return _STORE


def content_fingerprint(features: List[Dict[str, Any]]) -> str:
    canonical = json.dumps(features, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Version gate ─────────────────────────────────────────────────────────────


class GateResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    features: List[Dict[str, Any]] = Field(default_factory=list)
    source: str = "fresh"  # snapshot / fresh
    version: str = "latest"
    content_fingerprint: Optional[str] = None
    drift: Optional[DriftReport] = None
    fact_updates: Dict[str, Any] = Field(default_factory=dict)  # merged into D4 fact
    blocked: bool = False


def revision_from_fields(fields: Dict[str, str], source_type: str, source_ref: str) -> SourceRevision:
    """Build the revision evidence snapshot for a live source fetch."""
    return SourceRevision(
        source_type=source_type,
        source_ref=source_ref,
        revision_token="",
        content_fingerprint=None,
        schema_fingerprint=schema_fingerprint(fields),
        metadata_fingerprint=None,
        crs="",
        captured_at=datetime.now(timezone.utc),
    )


def pin_or_fetch(
    dataset_key: str,
    version: str,
    fields: Dict[str, str],
    fetch: Callable[[], List[Dict[str, Any]]],
    *,
    store: Optional[InMemorySnapshotStore] = None,
    source_type: str = "",
    source_ref: str = "",
    lineage_lookup: Optional[Any] = None,
) -> GateResult:
    """Gate one acquisition on its version semantics.

    - ``latest`` → fresh fetch, drift checked against the pinned snapshot if
      one exists (advisory by default); the fresh schema is NOT auto-pinned.
    - pinned (``version != "latest"``) → snapshot hit returns the frozen
      payload (byte-identical, same content fingerprint); miss fetches and
      freezes. Breaking drift with blocking on → typed block.
    """
    store = store or get_snapshot_store()
    current_fields = dict(fields or {})

    if version == "latest":
        features = fetch()
        drift = None
        snap = _latest_snapshot_for(dataset_key, store)
        if snap is not None and snap.schema_fields:
            drift = detect_drift(
                snap.schema_fields, current_fields, dataset_key=dataset_key, pin=None,
                old_schema_fingerprint=snap.schema_fingerprint_of(),
            )
        return _finish(features, "fresh", version, drift, lineage_lookup, dataset_key)

    snap = store.get(dataset_key, version)
    if snap is not None:
        drift = detect_drift(
            snap.schema_fields, current_fields, dataset_key=dataset_key, pin=version,
            old_schema_fingerprint=snap.schema_fingerprint_of(),
        ) if current_fields else None
        return _finish(list(snap.payload), "snapshot", version, drift, lineage_lookup, dataset_key)

    features = fetch()
    record = SnapshotRecord(
        dataset_key=dataset_key,
        pin=version,
        version_token=version,
        content_fingerprint=content_fingerprint(features),
        schema_fields=current_fields,
        revision=revision_from_fields(current_fields, source_type, source_ref).to_dict(),
        payload=features,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    store.put(record)
    return _finish(features, "fresh", version, None, lineage_lookup, dataset_key)


def _latest_snapshot_for(dataset_key: str, store: InMemorySnapshotStore) -> Optional[SnapshotRecord]:
    """Most recent pinned snapshot for the dataset (drift evidence source)."""
    candidates = [r for (k, _p), r in store._snapshots.items() if k == dataset_key]  # noqa: SLF001
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.created_at, reverse=True)
    return candidates[0]


def _finish(
    features: List[Dict[str, Any]],
    source: str,
    version: str,
    drift: Optional[DriftReport],
    lineage_lookup: Optional[Any],
    dataset_key: str,
) -> GateResult:
    blocked = False
    fact_updates: Dict[str, Any] = {}
    if drift is not None and drift.has_drift:
        drift.affected_artifacts = _affected(dataset_key, drift, lineage_lookup)  # type: ignore[attr-defined]
        fact_updates["drift"] = drift.change_class
        if drift.severity == "breaking" and drift_blocking_enabled():
            blocked = True
    if blocked:
        raise DriftBlockedError(drift)  # type: ignore[arg-type]
    fp = content_fingerprint(features) if features else None
    return GateResult(
        features=features,
        source=source,
        version=version,
        content_fingerprint=fp,
        drift=drift,
        fact_updates=fact_updates,
        blocked=blocked,
    )


def _affected(dataset_key: str, drift: DriftReport, lineage_lookup: Optional[Any]) -> List[str]:
    """Impact list: registered artifacts whose lineage roots at this dataset."""
    if lineage_lookup is None or not drift.has_drift:
        return []
    try:
        return list(lineage_lookup(dataset_key))
    except Exception:  # noqa: BLE001 — impact analysis must not break the gate
        return []




__all__ = [
    "DriftReport",
    "RenameSuggestion",
    "detect_drift",
    "suggest_renames",
    "schema_fingerprint",
    "drift_blocking_enabled",
    "DriftBlockedError",
    "SnapshotRecord",
    "InMemorySnapshotStore",
    "get_snapshot_store",
    "content_fingerprint",
    "pin_or_fetch",
    "GateResult",
    "revision_from_fields",
]
