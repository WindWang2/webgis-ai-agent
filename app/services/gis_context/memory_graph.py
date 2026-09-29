"""Situational memory graph (H09) — durable facts, anchored derived
findings, and dependency-precise invalidation.

The invalidation engine (ADR-0206) stales every conclusion accepted on an
older basis revision — the conservative direction, but coarse: one dataset
version bump amputates the whole session. This module adds the missing
precision layer *additively*: derived findings that record what they were
computed from (:class:`DependencyEdge` anchors) are staled only when a
dimension they actually depend on drifts. Findings without edges — the
F05 ``FindingRef`` mirror and decisions — keep the existing fail-closed
semantics untouched.

Discipline carried over from ADR-0215 (do not rebuild here):

- **Perception never enters the graph.** Viewport/hover stay in the
  per-turn ``SessionObservation``; only durable world anchors (dataset
  content revisions, the MapSpec mutation revision) are captureable, and
  only through the closed ``FACT_KINDS`` vocabulary.
- **Learning is not drift.** The first sighting of a token records it;
  only ``known → different`` advances are drifts.
- **Stale is never current by narrative.** Only an engine-verified
  derivation flips a stale row back — the recompute engine under a new
  generation, or a fresh completion-verify re-deriving the same critique
  family (capture *is* the derivation; never an assertion).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_context.observation import ContextChange, SessionObservation
from app.services.gis_context.working_context import (
    MAX_EDGES,
    MAX_STALE_ATTR,
    DependencyEdge,
    DerivedFinding,
    FINDING_FAMILIES,
    GISWorkingContext,
)

logger = logging.getLogger(__name__)

MISSION_BINDING_KEY = "_mission_binding"  # hotpath.py

#: Critique codes whose findings are recorded into the graph (the closed
#: ``critique_map_state`` vocabulary — validator findings stay in the
#: completion pipeline where they are owned).
CRITIQUE_CODE_PREFIXES = (
    "blank_map_risk",
    "invalid_result_bounds",
    "export_component_missing",
    "label_collision",
    "planned_observed_mismatch",
)

#: Change kinds → the (dim, ref) dimensions they drift (parity with the
#: invalidation engine's rule table). ``DATASET_VERSION_CHANGED`` is
#: token-scoped and resolved per change ``ref_id``.
_CHANGE_DIMS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "AOI_CHANGED": (("basis.aoi", ""),),
    "TIME_PERIOD_CHANGED": (("basis.time_period", ""),),
    "CRS_CHANGED": (("basis.crs", ""), ("basis.recipe_id", "")),
    "MEASURE_CHANGED": (("basis.measure", ""),),
    "PRODUCT_GOAL_CHANGED": (("basis.recipe_id", ""), ("basis.product_ref", "")),
    "MAPSPEC_SEMANTIC_CHANGED": (("mapspec", ""),),
}


@dataclass
class FactDrift:
    """A known token advanced — the world moved under the graph."""

    kind: str
    ref: str
    old_token: str
    new_token: str


@dataclass
class GraphInvalidation:
    """One derived finding staled by the walk, with attribution."""

    finding_id: str
    reasons: List[str] = field(default_factory=list)


def mapspec_token(wc: GISWorkingContext) -> str:
    fact = wc.fact("mapspec", "")
    return fact.token if fact is not None else ""


def dataset_token(wc: GISWorkingContext, ref_id: str) -> str:
    fact = wc.fact("dataset", str(ref_id or ""))
    return fact.token if fact is not None else ""


def observe_facts(
    wc: GISWorkingContext,
    obs: SessionObservation,
    *,
    basis_revision: Optional[int] = None,
) -> List[FactDrift]:
    """Capture durable world anchors from the observation.

    Idempotent per token: re-observing the same world appends nothing and
    drifts nothing. First sighting = learning (recorded, not a drift);
    ``known → different`` = drift (returned so the invalidation walk and
    the recompute planner can attribute it). Unknown (empty) tokens never
    overwrite known ones.
    """
    rev = int(wc.revision if basis_revision is None else basis_revision)
    drifts: List[FactDrift] = []

    for ds in obs.datasets:
        token = str(ds.content_revision or "")[:96]
        if not token:
            continue
        cur = wc.fact("dataset", ds.ref_id)
        old = cur.token if cur is not None else ""
        if cur is None:
            wc.upsert_fact(kind="dataset", ref=ds.ref_id, token=token, basis_revision=rev)
            continue
        if old != token:
            drifts.append(FactDrift(
                kind="dataset", ref=ds.ref_id, old_token=old, new_token=token))
            wc.upsert_fact(kind="dataset", ref=ds.ref_id, token=token, basis_revision=rev)

    mapspec_rev = str(getattr(obs, "mapspec_revision", "") or "")[:96]
    if mapspec_rev:
        cur = wc.fact("mapspec", "")
        old = cur.token if cur is not None else ""
        if cur is None:
            wc.upsert_fact(kind="mapspec", ref="", token=mapspec_rev, basis_revision=rev)
        elif old != mapspec_rev:
            drifts.append(FactDrift(
                kind="mapspec", ref="", old_token=old, new_token=mapspec_rev))
            wc.upsert_fact(kind="mapspec", ref="", token=mapspec_rev, basis_revision=rev)

    return drifts


def _live_token(wc: GISWorkingContext, dim: str, ref: str) -> str:
    if dim == "data":
        return dataset_token(wc, ref)
    if dim == "mapspec":
        return mapspec_token(wc)
    return ""


def _edge_hit(
    finding: DerivedFinding,
    dims: Tuple[Tuple[str, str], ...],
    wc: GISWorkingContext,
    at_revision: int,
) -> bool:
    """Does this change hit the finding's anchors?

    Token dims (``data``/``mapspec``): an edge matches when its ref matches
    (empty edge ref = wildcard → fail-closed hit) and its anchor no longer
    equals the live token. Basis dims: revision-anchored — a finding
    derived before the change revision drifts with it. A finding with no
    edges at all is fail-closed (stales on any basis-affecting change),
    matching the un-modeled ``FindingRef`` semantics.
    """
    edges = list(finding.depends_on)
    if not edges:
        return True
    for dim, ref in dims:
        for edge in edges:
            if edge.dim != dim:
                continue
            if dim in ("data", "mapspec"):
                if edge.ref and ref and edge.ref != ref:
                    continue
                live = _live_token(wc, dim, edge.ref or ref)
                if not edge.anchor or not live or edge.anchor != live:
                    return True
            else:
                if finding.generation and finding.generation < at_revision:
                    return True
    return False


def invalidate_graph(
    wc: GISWorkingContext,
    changes: List[ContextChange],
    *,
    at_revision: Optional[int] = None,
) -> List[GraphInvalidation]:
    """Dependency-precise invalidation of derived findings.

    Only ``current`` findings are considered (``stale``/``superseded`` rows
    are already terminal or pending recompute). Each finding is staled by a
    change only when an edge it actually depends on drifts; attribution
    records the change kind (and the drifted dimension) — the "why am I
    stale" evidence the card and the recompute planner surface verbatim.
    """
    rev = int(wc.revision if at_revision is None else at_revision)
    out: List[GraphInvalidation] = []
    for change in changes:
        if change.kind == "DATASET_VERSION_CHANGED":
            dims: Tuple[Tuple[str, str], ...] = (("data", str(change.ref_id or "")),)
        else:
            dims = _CHANGE_DIMS.get(change.kind, ())
        if not dims:
            continue
        reason = f"{change.kind}:{change.detail}" if change.detail else change.kind
        for finding in wc.derived_findings:
            if finding.status != "current":
                continue
            if not _edge_hit(finding, dims, wc, rev):
                continue
            reasons = [reason[:96]]
            drifted = ",".join(sorted({d for d, _r in dims}))[:48]
            if drifted and len(reasons) < MAX_STALE_ATTR:
                reasons.append(f"dim:{drifted}")
            finding.status = "stale"
            finding.stale_reasons = reasons
            out.append(GraphInvalidation(finding_id=finding.finding_id, reasons=reasons))
    return out


# ── anchor builders (capture sites) ──────────────────────────────────────

def _basis_anchor(wc: GISWorkingContext) -> str:
    return str(wc.revision)


def critique_edges(wc: GISWorkingContext) -> List[DependencyEdge]:
    """Edges for a critique derivation: MapSpec structure/semantic revision,
    the accepted datasets it renders, and the AOI it was critiqued under."""
    edges: List[DependencyEdge] = [
        DependencyEdge(dim="mapspec", ref="", anchor=mapspec_token(wc) or str(wc.revision)),
    ]
    for ds in wc.basis.datasets[:3]:
        edges.append(DependencyEdge(
            dim="data", ref=ds.ref_id,
            anchor=dataset_token(wc, ds.ref_id) or ds.content_revision))
    if wc.basis.aoi_bbox is not None or wc.basis.aoi_name:
        edges.append(DependencyEdge(dim="basis.aoi", anchor=_basis_anchor(wc)))
    return edges[:MAX_EDGES]


def reuse_edges(wc: GISWorkingContext) -> List[DependencyEdge]:
    """Edges for a reuse decision: the datasets whose fingerprints decided
    it, plus the query-shaping basis fields (AOI / time period)."""
    edges: List[DependencyEdge] = []
    for ds in wc.basis.datasets[:3]:
        edges.append(DependencyEdge(
            dim="data", ref=ds.ref_id,
            anchor=dataset_token(wc, ds.ref_id) or ds.content_revision))
    if wc.basis.time_period:
        edges.append(DependencyEdge(dim="basis.time_period", anchor=_basis_anchor(wc)))
    if wc.basis.aoi_bbox is not None or wc.basis.aoi_name:
        edges.append(DependencyEdge(dim="basis.aoi", anchor=_basis_anchor(wc)))
    return edges[:MAX_EDGES]


def make_derived_finding(
    wc: GISWorkingContext,
    *,
    family: str,
    ref: str,
    label: str,
    detail: str = "",
    digest: str = "",
    priority: int = 1,
    edges: List[DependencyEdge],
) -> DerivedFinding:
    """Build a derived finding under the *current* generation with fresh
    anchors — the only constructor capture sites and the recompute engine
    share (one anchoring discipline)."""
    if family not in FINDING_FAMILIES:
        raise ValueError(f"unknown_derived_family:{family}")
    return DerivedFinding(
        finding_id=wc.next_derived_finding_id(),
        family=family,
        ref=str(ref or "")[:96],
        label=str(label or "")[:120],
        detail=str(detail or "")[:160],
        status="current",
        generation=int(wc.revision),
        digest=str(digest or "")[:96],
        priority=max(0, min(3, int(priority))),
        depends_on=list(edges)[:MAX_EDGES],
        basis_revision=int(wc.revision),
    )


# ── production capture: completion-pipeline critique findings ────────────

async def record_critique_findings(
    session_id: str,
    *,
    findings: List[Any],
    state: Optional[Dict[str, Any]] = None,
    org_id: str = "",
) -> int:
    """Record critique-family findings into the mission's memory graph.

    Called from the completion finalization right after
    ``critique_map_state`` produced fresh findings — that *is* the
    derivation, so rows publish as ``current`` under the current
    generation with fresh anchors. The mission target resolves from the
    caller's state when given, else from the session's own durable
    binding / server-side turn context (the finalizer passes neither
    state nor org — resolution is this function's job, not the caller's).
    Best-effort end to end: no mission, no context, flag off, or any
    store failure → 0 recorded and the verification flow is untouched.
    """
    try:
        from app.services.gis_context.flags import memory_graph_enabled

        if not memory_graph_enabled():
            return 0
        crit = [
            f for f in (findings or [])
            if str(getattr(f, "code", "") or "").startswith(CRITIQUE_CODE_PREFIXES)
        ]
        if not crit:
            return 0
        live_state = state if isinstance(state, dict) else await _session_state(session_id)
        mission_id, org = _resolve_capture_target(
            session_id, live_state if isinstance(live_state, dict) else None,
            org_id=org_id)
        if not mission_id or not org:
            return 0
        mapspec = None
        try:
            from app.services.mapspec.store import mapspec_store_instance

            mapspec = await mapspec_store_instance.get_mapspec(session_id)
        except Exception:  # noqa: BLE001 — anchors degrade to basis revision
            mapspec = None
        return await asyncio.to_thread(
            _capture_critique_sync, mission_id, org, crit,
            mapspec, live_state if isinstance(live_state, dict) else None)
    except Exception as exc:  # noqa: BLE001 — capture is additive to verify
        logger.debug("[gis_context] critique capture skipped: %s", type(exc).__name__)
        return 0


def _resolve_capture_target(
    session_id: str, live_state: Optional[Dict[str, Any]], *, org_id: str = ""
) -> tuple:
    """(mission_id, org) for the capture entry — durable binding first,
    then the server-side session turn context; org from the caller, the
    binding, or the session tenant scan. Never model-supplied."""
    mission_id = ""
    org = str(org_id or "")[:64]
    binding_org = ""
    if isinstance(live_state, dict):
        raw = live_state.get(MISSION_BINDING_KEY)
        if isinstance(raw, dict) and raw.get("mission_id"):
            mission_id = str(raw["mission_id"])[:64]
            binding_org = str(raw.get("org_id") or "")[:64]
    org = org or binding_org
    if not mission_id:
        try:
            from app.services.gis_harness.hotpath_convergence.session_ctx import (
                get_turn_context,
            )

            ctx = get_turn_context(session_id, tenant_id=org)
            mission_id = str(getattr(ctx, "mission_id", "") or "")[:64]
        except Exception:  # noqa: BLE001 — no turn context is a clean miss
            mission_id = ""
    if not org and mission_id:
        try:
            from app.services.gis_context.hotpath import _tenant_scan

            org = _tenant_scan(session_id)[:64]
        except Exception:  # noqa: BLE001
            org = ""
    return mission_id, org


def _capture_critique_sync(
    mission_id: str,
    org: str,
    crit: List[Any],
    mapspec: Optional[Dict[str, Any]],
    live_state: Optional[Dict[str, Any]],
) -> int:
    """Sync capture body — runs inside ``asyncio.to_thread`` (store load/
    save are blocking DB calls; the hot-path convention)."""
    from app.services.gis_context.observation import observe_session
    from app.services.gis_context.store import WorkingContextStore

    store = WorkingContextStore()
    wc = store.load(mission_id, org_id=org)
    if wc is None:
        return 0
    disk_revision = int(wc.revision)
    obs = observe_session(live_state, mapspec if isinstance(mapspec, dict) else None)
    observe_facts(wc, obs)
    wc.revision = int(wc.revision) + 1  # fact transitions advance the CAS token
    recorded = 0
    for f in crit:
        code = str(getattr(f, "code", "") or "")[:48]
        target = str(getattr(f, "target", "") or "")[:64]
        severity = str(getattr(f, "severity", "") or "")
        detail = str(getattr(f, "detail", "") or "")[:160]
        digest_src = f"{code}|{target}|{severity}|{detail}"
        digest = _digest(digest_src)
        ref = f"{code}:{target}"[:96]
        existing = next(
            (x for x in wc.derived_findings
             if x.family == "critique" and x.ref == ref), None)
        if existing is not None and existing.status == "current" \
                and existing.digest == digest:
            continue  # unchanged derivation — read-mostly, no write
        priority = {"error": 3, "warning": 2}.get(severity, 1)
        finding = make_derived_finding(
            wc, family="critique", ref=ref, label=code, detail=detail,
            digest=digest, priority=priority, edges=critique_edges(wc))
        if existing is not None:
            finding.finding_id = existing.finding_id  # stable identity
        wc.upsert_derived_finding(finding)
        recorded += 1
    if not recorded:
        return 0
    try:
        store.save(wc, expected_revision=disk_revision)
    except Exception as exc:  # noqa: BLE001 — capture never blocks verify
        logger.debug("[gis_context] critique capture save failed: %s",
                     type(exc).__name__)
        return 0
    return recorded


async def _session_state(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id)
        return state if isinstance(state, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


__all__ = [
    "CRITIQUE_CODE_PREFIXES",
    "FactDrift",
    "GraphInvalidation",
    "critique_edges",
    "dataset_token",
    "invalidate_graph",
    "make_derived_finding",
    "mapspec_token",
    "observe_facts",
    "record_critique_findings",
    "reuse_edges",
]
