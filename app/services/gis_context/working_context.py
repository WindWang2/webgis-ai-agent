"""GIS Working Context (ADR-0206 D3 / Direction 06 M2).

Mission-scoped structured cartographic working state — what the mission
currently *accepts* about the world, what it decided, what it verified and
what the user did by hand. This is not chat memory: no free-text history,
no raw payloads, every section bounded, every decision carrying the basis
revision it was accepted under (so the invalidation engine can tell which
conclusions the world has moved away from).

Schema ``gis_working_context.v2``; pydantic ``extra="forbid"`` so typos
fail at construction, not silently at injection. v2 adds (ADR-0215):
``UserEditRecord.op_id`` (cross-replica edit identity), staleness
attribution (``stale_reasons``) on findings/decisions, authoritative
``BasisDataset.version_fingerprint`` tokens and the bounded
``RevalidationReceipt`` ring — the durable evidence record of every
stale→current restore attempt. v1 payloads load unchanged (new fields
default).

v3 adds (H09 situational memory graph): ``ContextFact`` (durable world
anchors: dataset content revisions, MapSpec mutation revision),
``DerivedFinding`` (a finding with its ``DependencyEdge`` anchors — the
basis a derivation stood on — and the ``generation`` it was computed
under) and ``UserEditRecord.detail`` (reason-grade edit payload). All
additive: v1/v2 payloads load unchanged; unknown-field payloads from a
newer writer fail the ``extra="forbid"`` gate and are treated as corrupt
by the store (re-grounded on next save — same degradation path as the
v1→v2 transition).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.gis_context.scope import mission_scope_ref

SCHEMA_VERSION = "gis_working_context.v3"

#: Hard bounds — payload is validated against these before persisting.
MAX_BASIS_DATASETS = 12
MAX_DECISIONS = 8
MAX_FINDINGS = 8
MAX_USER_EDITS = 12
MAX_STALE_FIELDS = 16
MAX_TEXT = 200
MAX_PAYLOAD_BYTES = 16 * 1024
#: ADR-0215 — receipt ring + per-record staleness attribution bounds.
MAX_REVALIDATIONS = 8
MAX_STALE_ATTR = 8
MAX_RECEIPT_EVIDENCE = 4
MAX_RECEIPT_CHECKS = 4
#: H09 — situational memory graph bounds.
MAX_FACTS = 24
MAX_DERIVED_FINDINGS = 8
MAX_EDGES = 6
MAX_EDIT_DETAIL = 96


class EvidenceRef(BaseModel):
    """One bounded piece of evidence behind a revalidation verdict
    (observed value / live authority token / verification digest)."""

    model_config = ConfigDict(extra="forbid")

    ref: str = ""
    token: str = ""


class CheckResult(BaseModel):
    """One deterministic engine check recorded on a receipt."""

    model_config = ConfigDict(extra="forbid")

    check: str
    verdict: str = "pass"        # pass | fail
    detail: str = ""


class RevalidationReceipt(BaseModel):
    """Durable evidence record of one stale→current restore attempt.

    Produced only by the revalidation engine (ADR-0215 D1) — never by LLM
    output. ``verdict="rejected"`` receipts are first-class: a failed check
    is as observable as a restore. No TTL field exists by design — a
    restored fact re-stales through the invalidation engine on drift.
    """

    model_config = ConfigDict(extra="forbid")

    receipt_id: str               # deterministic: rtv-<seq>
    kind: str                     # CLAIM_REVERIFIED | DECISION_REAFFIRM | BASIS_RECONFIRMED
    target: str = ""              # claim id / decision text digest / stale field path
    basis_revision: int = 0       # context revision the verdict was stamped under
    prior_reason: str = ""        # the stale reason being cleared (verbatim)
    evidence: List[EvidenceRef] = Field(default_factory=list, max_length=MAX_RECEIPT_EVIDENCE)
    checks: List[CheckResult] = Field(default_factory=list, max_length=MAX_RECEIPT_CHECKS)
    verdict: str = "rejected"     # restored | rejected
    reject_reason: str = ""       # closed reason code when rejected
    turn_id: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id[:32],
            "kind": self.kind[:24],
            "target": self.target[:64],
            "basis_revision": int(self.basis_revision),
            "verdict": self.verdict[:8],
            "reject_reason": self.reject_reason[:48],
            "evidence": [
                {"ref": e.ref[:64], "token": e.token[:96]} for e in self.evidence[:MAX_RECEIPT_EVIDENCE]
            ],
            "checks": [
                {"check": c.check[:48], "verdict": c.verdict[:8], "detail": c.detail[:96]}
                for c in self.checks[:MAX_RECEIPT_CHECKS]
            ],
            "prior_reason": self.prior_reason[:96],
            "turn_id": self.turn_id[:64],
        }


class BasisDataset(BaseModel):
    """A dataset accepted into the working basis, with the content revision
    it was accepted at — the invalidation anchor for version bumps — and
    (v2) the authoritative ``version_fingerprint`` token observed at
    acceptance time (ADR-0215 D10), the reuse-identity anchor."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str
    alias: str = ""
    content_revision: str = ""
    role: str = ""
    version_fingerprint: str = ""
    #: The project_dataset authority id the fingerprint resolved under
    #: (ref_id when it resolves, else the alias) — the reuse-query key
    #: namespace. Empty = never resolved (honest unknown).
    authority_id: str = ""


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
    still stands on current ground. v2 adds ``stale_reasons`` — the field
    paths whose drift staled this record (attribution, ADR-0215 D3).
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=MAX_TEXT)
    turn_id: str = ""
    basis_revision: int = 0
    #: Set by the invalidation engine when the basis this decision stood on
    #: has drifted — rendered with a re-check marker, never silently reused.
    stale_basis: bool = False
    #: Field paths (basis.aoi / basis.crs / …) whose drift staled this
    #: record; cleared by the revalidation engine on reaffirm.
    stale_reasons: List[str] = Field(default_factory=list, max_length=MAX_STALE_ATTR)


class FindingRef(BaseModel):
    """A verified claim bound to this mission (mission↔claim edge)."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    status: str = "unknown"     # ClaimStatus value; refreshed, never invented
    basis_revision: int = 0
    #: Field paths whose drift staled this finding (v2 attribution).
    stale_reasons: List[str] = Field(default_factory=list, max_length=MAX_STALE_ATTR)


# ── H09 situational memory graph (schema v3, additive) ───────────────────

#: Closed fact kinds. Only *durable* world anchors are captureable:
#: dataset content revisions and the MapSpec mutation revision. Decisions
#: live in ``user_edits``/``DecisionRecord``, tool results in the claim
#: store — duplicating them here would create a second source of truth.
#: Ephemeral perception (viewport/hover) has no kind and can never enter.
FACT_KINDS = ("dataset", "mapspec")

#: Closed dependency dimensions. ``basis.*`` dims drift through the
#: invalidation change kinds (revision-anchored); ``data``/``mapspec`` dims
#: drift through token advance (content-addressed anchors).
EDGE_DIMS = (
    "basis.aoi",
    "basis.datasets",
    "basis.crs",
    "basis.time_period",
    "basis.measure",
    "basis.recipe_id",
    "basis.product_ref",
    "data",
    "mapspec",
)

#: Closed derived-finding families (who owns the recompute executor).
FINDING_FAMILIES = ("critique", "reuse")

#: Closed derived-finding statuses. ``stale`` is never treated as current
#: by any renderer; ``superseded`` is terminal (replaced/gone).
DERIVED_STATUSES = ("current", "stale", "superseded")


class DependencyEdge(BaseModel):
    """One dependency of a derived finding, anchored to the world state it
    was computed under. ``anchor`` is the expected token (dataset content
    revision / MapSpec mutation revision) or the basis revision a ``basis.*``
    dim was derived under — the invalidation walk compares it against live
    anchors to decide precision staleness."""

    model_config = ConfigDict(extra="forbid")

    dim: str
    ref: str = ""               # dataset ref_id / layer_id / "" (wildcard)
    anchor: str = ""
    fact_id: str = ""           # optional edge into ``facts``


class ContextFact(BaseModel):
    """A durable world anchor observed for this mission (token-bearing).

    Facts are refs, never payloads: ``(kind, ref)`` identity with the
    token the world currently carries. Advancing a token is a transition —
    the graph walk runs against the *new* tokens and re-anchors findings
    only through a successful recompute."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    kind: str
    ref: str = ""
    token: str = ""
    basis_revision: int = 0
    priority: int = 2

    def key(self) -> tuple:
        return (self.kind, self.ref)


class DerivedFinding(BaseModel):
    """A finding derived under an explicit, anchored basis.

    Unlike :class:`FindingRef` (a claim-status mirror owned by F05), a
    derived finding records *what it was computed from* (``depends_on``)
    and *under which generation* (``generation`` = the context revision the
    last successful derivation published under). Only the recompute engine
    flips ``stale → current`` — atomically, with fresh anchors."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    family: str                 # critique | reuse
    ref: str = ""               # stable subject identity (code:target / "reuse:project")
    label: str = ""
    detail: str = ""
    status: str = "current"     # current | stale | superseded
    generation: int = 0         # context revision the result was published under
    digest: str = ""            # content fingerprint of the last derivation
    priority: int = 1
    depends_on: List[DependencyEdge] = Field(default_factory=list, max_length=MAX_EDGES)
    #: Field/dimension attributions whose drift staled this finding.
    stale_reasons: List[str] = Field(default_factory=list, max_length=MAX_STALE_ATTR)
    basis_revision: int = 0


class UserEditRecord(BaseModel):
    """Append-only user canvas decision (user-wins). Never auto-staled.

    ``op_id`` (v2) is the cross-replica operation identity — the MapSpec
    ``mutation_id`` carried by provenance — so one user delivery replayed
    across replicas dedupes to one record regardless of per-copy ``seq``.
    ``detail`` (v3) is a bounded reason-grade summary of the edit payload
    (e.g. ``opacity=0.4`` / ``paint:fill-color`` / ``order=3``) — never a
    raw dump; the full intent stays in the MapSpec authority.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int
    layer_id: str = ""
    kind: str = ""              # hide/show/opacity/restyle/reorder/component/delete/semantic
    turn_id: str = ""
    op_id: str = ""
    detail: str = ""

    def key(self) -> tuple:
        return (self.layer_id, self.kind, self.seq)


class GISWorkingContext(BaseModel):
    """Root contract. ``revision`` is the CAS token; ``stale`` maps field
    paths to invalidation reasons (produced only by the engine);
    ``revalidations`` is the bounded FIFO ring of restore-attempt receipts
    (produced only by the revalidation engine, ADR-0215)."""

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
    revalidations: List[RevalidationReceipt] = Field(
        default_factory=list, max_length=MAX_REVALIDATIONS)
    rtv_seq: int = 0
    #: H09 situational memory graph (v3): durable world anchors + findings
    #: with anchored dependencies. Facts are refs (≤24), findings bounded ≤8.
    facts: List[ContextFact] = Field(default_factory=list, max_length=MAX_FACTS)
    derived_findings: List[DerivedFinding] = Field(
        default_factory=list, max_length=MAX_DERIVED_FINDINGS)
    fact_seq: int = 0
    df_seq: int = 0

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
        self, *, layer_id: str = "", kind: str = "", turn_id: str = "",
        op_id: str = "", detail: str = "",
    ) -> bool:
        """Append a user edit (user-wins records are never overwritten).

        Identity: when ``op_id`` is present (the MapSpec mutation id) the
        append is idempotent on it — the same delivery replayed on any
        replica converges to one record (ADR-0215 D6). Without ``op_id``
        the legacy behavior holds (append with the next per-copy seq).
        Returns False when the bound is hit.
        """
        oid = str(op_id or "")[:64]
        if oid and any(e.op_id == oid for e in self.user_edits):
            return True
        nxt = (max((e.seq for e in self.user_edits), default=0)) + 1
        edit = UserEditRecord(
            seq=nxt, layer_id=layer_id[:64], kind=kind[:32],
            turn_id=turn_id[:64], op_id=oid,
            detail=str(detail or "")[:MAX_EDIT_DETAIL],
        )
        if not oid and any(e.key() == edit.key() for e in self.user_edits):
            return True
        if len(self.user_edits) >= MAX_USER_EDITS:
            return False
        self.user_edits.append(edit)
        return True

    def append_receipt(self, receipt: RevalidationReceipt) -> RevalidationReceipt:
        """Stamp the deterministic id, append to the FIFO ring, return it."""
        self.rtv_seq = int(self.rtv_seq) + 1
        stamped = receipt.model_copy(update={"receipt_id": f"rtv-{self.rtv_seq}"})
        self.revalidations.append(stamped)
        if len(self.revalidations) > MAX_REVALIDATIONS:
            self.revalidations = self.revalidations[-MAX_REVALIDATIONS:]
        return stamped

    def upsert_finding(
        self, claim_id: str, status: str, basis_revision: int,
        stale_reasons: Optional[List[str]] = None,
    ) -> None:
        cid = str(claim_id or "")[:64]
        for f in self.findings:
            if f.claim_id == cid:
                f.status = str(status or "unknown")[:24]
                f.basis_revision = int(basis_revision)
                if stale_reasons is not None:
                    f.stale_reasons = list(stale_reasons)[:MAX_STALE_ATTR]
                return
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append(FindingRef(
                claim_id=cid, status=str(status or "unknown")[:24],
                basis_revision=int(basis_revision),
                stale_reasons=list(stale_reasons or [])[:MAX_STALE_ATTR],
            ))

    # ── graph helpers (H09 — pure; persistence is the store's job) ──────

    def next_fact_id(self) -> str:
        self.fact_seq = int(self.fact_seq) + 1
        return f"cf-{self.fact_seq}"

    def next_derived_finding_id(self) -> str:
        self.df_seq = int(self.df_seq) + 1
        return f"df-{self.df_seq}"

    def fact(self, kind: str, ref: str) -> Optional[ContextFact]:
        key = (str(kind or "")[:24], str(ref or "")[:64])
        return next((f for f in self.facts if f.key() == key), None)

    def upsert_fact(
        self, *, kind: str, ref: str, token: str,
        basis_revision: int = 0, priority: int = 2,
    ) -> tuple:
        """Create-or-update a durable fact by ``(kind, ref)`` identity.

        Returns ``(fact, created)``. Token advancement is a transition the
        caller records; this helper never invents a token (empty token
        updates are ignored — unknown never overwrites known)."""
        k = str(kind or "")[:24]
        r = str(ref or "")[:64]
        t = str(token or "")[:96]
        cur = self.fact(k, r)
        if cur is not None:
            if not t:
                return cur, False
            cur.token = t
            cur.basis_revision = int(basis_revision)
            return cur, False
        if len(self.facts) >= MAX_FACTS:
            return cur, False
        fact = ContextFact(
            fact_id=self.next_fact_id(), kind=k, ref=r, token=t,
            basis_revision=int(basis_revision), priority=max(0, min(3, int(priority))),
        )
        self.facts.append(fact)
        return fact, True

    def derived_finding(self, finding_id: str) -> Optional[DerivedFinding]:
        fid = str(finding_id or "")[:64]
        return next((f for f in self.derived_findings if f.finding_id == fid), None)

    def upsert_derived_finding(self, finding: DerivedFinding) -> None:
        """Insert-or-replace by ``finding_id``; bounded FIFO with
        stale-first eviction (drop the oldest stale row before any current
        one — the graph never amputates fresh conclusions to remember
        dead ones)."""
        fid = finding.finding_id[:64]
        for i, cur in enumerate(self.derived_findings):
            if cur.finding_id == fid:
                self.derived_findings[i] = finding
                return
        if len(self.derived_findings) >= MAX_DERIVED_FINDINGS:
            stale_idx = next(
                (i for i, f in enumerate(self.derived_findings) if f.status == "stale"),
                None)
            if stale_idx is None:
                self.derived_findings.pop(0)
            else:
                self.derived_findings.pop(stale_idx)
        self.derived_findings.append(finding)

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
    "CheckResult",
    "ContextFact",
    "DecisionRecord",
    "DependencyEdge",
    "DerivedFinding",
    "DERIVED_STATUSES",
    "EvidenceRef",
    "EDGE_DIMS",
    "FACT_KINDS",
    "FindingRef",
    "FINDING_FAMILIES",
    "GISWorkingContext",
    "MAX_PAYLOAD_BYTES",
    "MAX_RECEIPT_CHECKS",
    "MAX_RECEIPT_EVIDENCE",
    "MAX_REVALIDATIONS",
    "MAX_STALE_ATTR",
    "RevalidationReceipt",
    "SCHEMA_VERSION",
    "UserEditRecord",
    "WorkingBasis",
]
