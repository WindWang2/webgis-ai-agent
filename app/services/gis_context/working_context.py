"""GIS Working Context (ADR-0204 D3 / Direction 06 M2).

Mission-scoped structured cartographic working state — what the mission
currently *accepts* about the world, what it decided, what it verified and
what the user did by hand. This is not chat memory: no free-text history,
no raw payloads, every section bounded, every decision carrying the basis
revision it was accepted under (so the invalidation engine can tell which
conclusions the world has moved away from).

Schema ``gis_working_context.v1``; pydantic ``extra="forbid"`` so typos
fail at construction, not silently at injection.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_context.scope import mission_scope_ref

SCHEMA_VERSION = "gis_working_context.v1"

#: Hard bounds — payload is validated against these before persisting.
MAX_BASIS_DATASETS = 12
MAX_DECISIONS = 8
MAX_FINDINGS = 8
MAX_USER_EDITS = 12
MAX_STALE_FIELDS = 16
MAX_TEXT = 200
MAX_PAYLOAD_BYTES = 16 * 1024


class BasisDataset(BaseModel):
    """A dataset accepted into the working basis, with the content revision
    it was accepted at — the invalidation anchor for version bumps."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    alias: str = ""
    content_revision: str = ""
    role: str = ""


class WorkingBasis(BaseModel):
    """The accepted working state of the mission (AOI/time/CRS/measure/
    datasets/recipe/product/export). Fields left None are explicitly
    unknown — the engine never guesses."""

    model_config = ConfigDict(extra="forbid")

    aoi_bbox: Optional[List[float]] = Field(default=None, max_length=4)
    aoi_name: str = ""
    time_period: str = ""       # bounded label, e.g. "2024-Q2" / "2020..2024"
    crs: str = ""
    measure_field: str = ""
    measure_unit: str = ""
    measure_statistic: str = ""
    datasets: List[BasisDataset] = Field(default_factory=list, max_length=MAX_BASIS_DATASETS)
    recipe_id: str = ""
    product_ref: str = ""
    export_format: str = ""

    def dataset(self, ref_id: str) -> Optional[BasisDataset]:
        rid = str(ref_id or "")
        for ds in self.datasets:
            if ds.ref_id == rid:
                return ds
        return None


class DecisionRecord(BaseModel):
    """Accepted assumption / rejected alternative / unresolved constraint.

    ``basis_revision`` = working-context revision the decision was accepted
    under; the invalidation engine uses it to decide whether the decision
    still stands on current ground.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_TEXT)
    turn_id: str = ""
    basis_revision: int = 0
    #: Set by the invalidation engine when the basis this decision stood on
    #: has drifted — rendered with a re-check marker, never silently reused.
    stale_basis: bool = False


class FindingRef(BaseModel):
    """A verified claim bound to this mission (mission↔claim edge)."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    status: str = "unknown"     # ClaimStatus value; refreshed, never invented
    basis_revision: int = 0


class UserEditRecord(BaseModel):
    """Append-only user canvas decision (user-wins). Never auto-staled."""

    model_config = ConfigDict(extra="forbid")

    seq: int
    layer_id: str = ""
    kind: str = ""              # hide / restyle / reorder / rename / delete
    turn_id: str = ""

    def key(self) -> tuple:
        return (self.layer_id, self.kind, self.seq)


class GISWorkingContext(BaseModel):
    """Root contract. ``revision`` is the CAS token; ``stale`` maps field
    paths to invalidation reasons (produced only by the engine)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    mission_id: str
    org_id: str = ""
    project_id: str = ""
    user_id: str = ""
    revision: int = 1
    goal_revision_mirror: int = 0
    updated_turn_id: str = ""
    basis: WorkingBasis = Field(default_factory=WorkingBasis)
    accepted_assumptions: List[DecisionRecord] = Field(default_factory=list, max_length=MAX_DECISIONS)
    rejected_alternatives: List[DecisionRecord] = Field(default_factory=list, max_length=MAX_DECISIONS)
    unresolved_constraints: List[DecisionRecord] = Field(default_factory=list, max_length=MAX_DECISIONS)
    findings: List[FindingRef] = Field(default_factory=list, max_length=MAX_FINDINGS)
    user_edits: List[UserEditRecord] = Field(default_factory=list, max_length=MAX_USER_EDITS)
    stale: Dict[str, str] = Field(default_factory=dict)

    # ── scope ───────────────────────────────────────────────────────────

    def scope_ref(self):
        return mission_scope_ref(
            mission_id=self.mission_id,
            org_id=self.org_id,
            project_id=self.project_id,
            user_id=self.user_id,
            revision=str(self.revision),
        )

    # ── mutation helpers (pure — persistence is the store's job) ───────

    def next_revision(self) -> "GISWorkingContext":
        return self.model_copy(update={"revision": int(self.revision) + 1})

    def mark_stale(self, field: str, reason: str) -> None:
        if len(self.stale) < MAX_STALE_FIELDS:
            self.stale[str(field)[:64]] = str(reason)[:96]

    def clear_stale(self, field: str) -> None:
        self.stale.pop(field, None)

    def add_user_edit(
        self, *, layer_id: str = "", kind: str = "", turn_id: str = ""
    ) -> bool:
        """Append a user edit (deduped on (layer_id, kind, seq-tail));
        returns False when the bound is hit — user-wins records are never
        overwritten silently."""
        nxt = (max((e.seq for e in self.user_edits), default=0)) + 1
        edit = UserEditRecord(seq=nxt, layer_id=layer_id[:64], kind=kind[:32], turn_id=turn_id[:64])
        if any(e.key() == edit.key() for e in self.user_edits):
            return True
        if len(self.user_edits) >= MAX_USER_EDITS:
            return False
        self.user_edits.append(edit)
        return True

    def upsert_finding(self, claim_id: str, status: str, basis_revision: int) -> None:
        cid = str(claim_id or "")[:64]
        for f in self.findings:
            if f.claim_id == cid:
                f.status = str(status or "unknown")[:24]
                f.basis_revision = int(basis_revision)
                return
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append(FindingRef(claim_id=cid, status=str(status or "unknown")[:24], basis_revision=int(basis_revision)))

    # ── serialization ───────────────────────────────────────────────────

    def payload(self) -> Dict[str, Any]:
        """Bounded JSON payload for the store row; raises ValueError when
        the serialized form exceeds the byte budget (fail-closed size gate).
        """
        data = self.model_dump(mode="json", exclude_none=True)
        import json

        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("working_context_payload_over_budget")
        return data

    @classmethod
    def from_payload(cls, data: Dict[str, Any]) -> "GISWorkingContext":
        return cls.model_validate(data)


__all__ = [
    "BasisDataset",
    "DecisionRecord",
    "FindingRef",
    "GISWorkingContext",
    "MAX_PAYLOAD_BYTES",
    "SCHEMA_VERSION",
    "UserEditRecord",
    "WorkingBasis",
]
