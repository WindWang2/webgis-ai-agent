"""Deterministic session observation for the GIS working context
(ADR-0204 D4 / Direction 06 M3).

Reads the *existing* authorities — session map_state, MapSpec, and the
gis_situation snapshot — and projects a bounded ``SessionObservation``.
No LLM, no wall clock, no writes. ``diff_against`` compares an observation
with the accepted working basis and emits typed ``ContextChange`` events
for the invalidation engine.

Projection anchors (review-verified against the producers):
- AOI: ``map_state.viewport.bounds`` (frontend observed) → ``_viewport_bbox``
  over ``mapspec.view`` {center,zoom} (real MapSpec view carries no bounds,
  composite_builder.py) → snapshot ``geographic.viewport``.
- CRS: source-level ``sources[sid].crs`` / ``profile.crs``
  (lifecycle_engine writes CRS per source, never on the view).
- measure: ``layer.legend_spec.field`` (composite_builder writes the themed
  field there) with tolerant fallbacks.
- user edits: ``_gis_provenance`` is a ProvenanceEntry *list*; the
  user-hidden derivation mirrors gis_situation/compiler.py exactly
  (origin=="user" ∧ kind=="PatchLayerPresentationIntent" ∧ visible is
  False), with the compiled snapshot fact as the preferred source.

Tolerant-extraction discipline: fields the authorities do not carry stay
empty/None = unknown. Unknown never triggers invalidation — only a *known*
drift does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_context.working_context import BasisDataset, GISWorkingContext

MAX_OBS_DATASETS = 12
MAX_OBS_LAYERS = 24
MAX_OBS_HIDDEN = 24
_AOI_EPSILON = 1e-9

_USER_HIDDEN_KIND = "PatchLayerPresentationIntent"  # gis_situation/compiler.py
_PROVENANCE_KEY = "_gis_provenance"                 # gis_world_state/provenance.py


@dataclass
class ContextChange:
    kind: str                    # AOI_CHANGED / DATASET_VERSION_CHANGED / ...
    detail: str = ""             # bounded, reason-grade
    ref_id: str = ""             # for dataset changes


@dataclass
class SessionObservation:
    aoi_bbox: Optional[List[float]] = None
    aoi_name: str = ""
    time_period: str = ""
    crs: str = ""
    measure_field: str = ""
    measure_statistic: str = ""
    recipe_id: str = ""
    export_format: str = ""
    datasets: List[BasisDataset] = field(default_factory=list)
    layer_ids: List[str] = field(default_factory=list)
    user_hidden_layers: List[str] = field(default_factory=list)


def _bbox_close(a: List[float], b: List[float]) -> bool:
    if len(a) != 4 or len(b) != 4:
        return False
    return all(abs(float(x) - float(y)) <= _AOI_EPSILON for x, y in zip(a, b))


def _bounds_of(value: Any) -> Optional[List[float]]:
    """Extract a 4-float [w, s, e, n] bbox from a viewport-ish dict."""
    if not isinstance(value, dict):
        return None
    raw = value.get("bounds") or value.get("bbox")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return [float(v) for v in raw]
        except (TypeError, ValueError):
            return None
    return None


def _derive_viewport_bbox(mapspec: Dict[str, Any]) -> Optional[List[float]]:
    """Derive the web-mercator viewport bbox from the real MapSpec view
    ({center, zoom, pitch, bearing} — it carries no explicit bounds)."""
    view = mapspec.get("view")
    if not isinstance(view, dict):
        return None
    try:
        from app.lib.cartography.semantic_checks import _viewport_bbox

        return _viewport_bbox(view)
    except Exception:  # noqa: BLE001 — derivation is additive
        return None


def _source_crs(mapspec: Dict[str, Any]) -> str:
    """CRS lives at source level (view carries none)."""
    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        for sid in sorted(raw_sources):
            entry = raw_sources.get(sid)
            if not isinstance(entry, dict):
                continue
            crs = entry.get("crs")
            if isinstance(crs, str) and crs:
                return crs[:64]
            profile = entry.get("profile")
            if isinstance(profile, dict):
                crs = profile.get("crs")
                if isinstance(crs, str) and crs:
                    return crs[:64]
    return ""


def _layer_measure(raw_layers: List[Any]) -> tuple:
    """First themed measure: legend_spec.field (producer anchor), with
    tolerant fallbacks to older/alternate keys."""
    for ln in raw_layers:
        if not isinstance(ln, dict):
            continue
        legend = ln.get("legend_spec")
        field_name = None
        statistic = None
        if isinstance(legend, dict):
            f = legend.get("field")
            if isinstance(f, str) and f:
                field_name = f
            s = legend.get("statistic")
            if isinstance(s, str) and s:
                statistic = s
        if field_name is None:
            f = ln.get("metric") or ln.get("measure_field") or ln.get("field")
            if isinstance(f, str) and f:
                field_name = f
        if statistic is None:
            s = ln.get("statistic") or ln.get("aggregation")
            if isinstance(s, str) and s:
                statistic = s
        if field_name or statistic:
            return (field_name or "", statistic or "")
    return ("", "")


def _user_hidden_from_provenance(state: Dict[str, Any]) -> List[str]:
    """Mirror gis_situation/compiler.py: _gis_provenance is a list of
    ProvenanceEntry dicts; user-hidden = origin user ∧
    PatchLayerPresentationIntent ∧ detail.visible is False."""
    provenance = state.get(_PROVENANCE_KEY)
    provenance = list(provenance) if isinstance(provenance, list) else []
    hidden = [
        str(entry.get("target"))
        for entry in provenance
        if isinstance(entry, dict)
        and entry.get("origin") == "user"
        and entry.get("kind") == _USER_HIDDEN_KIND
        and entry.get("detail", {}).get("visible") is False
        and entry.get("target")
    ]
    return sorted(set(hidden))[:MAX_OBS_HIDDEN]


def observe_session(
    state: Optional[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]],
    *,
    situation_snapshot: Any = None,
) -> SessionObservation:
    """Project a bounded observation from map_state + mapspec (+ optional
    GISSituation snapshot for plan-derived facts like requested period)."""
    state = state if isinstance(state, dict) else {}
    mapspec = mapspec if isinstance(mapspec, dict) else {}
    obs = SessionObservation()

    # AOI: frontend-observed bounds → derived viewport bbox → snapshot.
    obs.aoi_bbox = _bounds_of(state.get("viewport"))
    if obs.aoi_bbox is None:
        obs.aoi_bbox = _derive_viewport_bbox(mapspec)

    obs.crs = _source_crs(mapspec)

    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        for sid in sorted(raw_sources)[:MAX_OBS_DATASETS]:
            entry = raw_sources.get(sid)
            if not isinstance(entry, dict):
                continue
            ref = entry.get("ref_id") or entry.get("ref") or sid
            rev = entry.get("content_revision") or entry.get("revision") or ""
            obs.datasets.append(BasisDataset(
                ref_id=str(ref)[:64],
                alias=str(sid)[:64],
                content_revision=str(rev)[:64],
                role=str(entry.get("context_role") or entry.get("role") or "")[:32],
            ))

    raw_layers = mapspec.get("layers")
    if isinstance(raw_layers, list):
        for ln in raw_layers[:MAX_OBS_LAYERS]:
            if isinstance(ln, dict) and isinstance(ln.get("id"), str):
                obs.layer_ids.append(ln["id"][:64])

    obs.user_hidden_layers = _user_hidden_from_provenance(state)

    if situation_snapshot is not None:
        try:
            geo = getattr(situation_snapshot, "geographic", None)
            if obs.aoi_bbox is None:
                viewport = getattr(geo, "viewport", None)
                obs.aoi_bbox = _bounds_of(getattr(viewport, "value", None))
            scope = getattr(geo, "scope_name", None)
            sval = getattr(scope, "value", None)
            if isinstance(sval, str):
                obs.aoi_name = sval[:64]
            temporal = getattr(situation_snapshot, "temporal", None)
            req = getattr(temporal, "requested_period", None)
            val = getattr(req, "value", None)
            if isinstance(val, str):
                obs.time_period = val[:64]
            goal = getattr(situation_snapshot, "user_goal", None)
            recipe = getattr(goal, "recipe_id", None)
            rval = getattr(recipe, "value", None)
            if isinstance(rval, str):
                obs.recipe_id = rval[:64]
            interaction = getattr(situation_snapshot, "interaction", None)
            if not obs.user_hidden_layers:
                hidden = getattr(interaction, "user_hidden_layers", None)
                hval = getattr(hidden, "value", None)
                if isinstance(hval, list):
                    obs.user_hidden_layers = [
                        str(h)[:64] for h in hval[:MAX_OBS_HIDDEN]]
        except Exception:  # noqa: BLE001 — snapshot facts are additive
            pass

    if isinstance(raw_layers, list):
        obs.measure_field, obs.measure_statistic = _layer_measure(raw_layers)

    export_target = state.get("_export_target")
    if isinstance(export_target, dict):
        fmt = export_target.get("format")
        if isinstance(fmt, str):
            obs.export_format = fmt[:32]

    return obs


def diff_against(
    wc: GISWorkingContext, obs: SessionObservation
) -> List[ContextChange]:
    """Observation vs accepted basis → typed changes. Unknown-vs-unknown
    never counts as change. Known drift → invalidating change kinds;
    unknown→known → a single ``BASIS_ESTABLISHED`` event (basis refresh
    without invalidation — learning a fact is not a drift).
    """
    changes: List[ContextChange] = []
    established: List[str] = []
    basis = wc.basis

    if obs.aoi_bbox is not None:
        if basis.aoi_bbox is not None:
            if not _bbox_close(obs.aoi_bbox, basis.aoi_bbox):
                changes.append(ContextChange(kind="AOI_CHANGED", detail="view_bounds_drift"))
        else:
            established.append("aoi")
    for ds in obs.datasets:
        prev = basis.dataset(ds.ref_id)
        if (
            prev is not None
            and prev.content_revision
            and ds.content_revision
            and prev.content_revision != ds.content_revision
        ):
            changes.append(ContextChange(
                kind="DATASET_VERSION_CHANGED",
                detail=f"{ds.ref_id}:{prev.content_revision}->{ds.content_revision}"[:96],
                ref_id=ds.ref_id,
            ))
    if obs.time_period:
        if basis.time_period and obs.time_period != basis.time_period:
            changes.append(ContextChange(kind="TIME_PERIOD_CHANGED", detail=obs.time_period[:64]))
        elif not basis.time_period:
            established.append("time_period")
    if obs.crs:
        if basis.crs and obs.crs != basis.crs:
            changes.append(ContextChange(kind="CRS_CHANGED", detail=obs.crs[:64]))
        elif not basis.crs:
            established.append("crs")
    if (obs.measure_field or obs.measure_statistic):
        if (
            (obs.measure_field and basis.measure_field and obs.measure_field != basis.measure_field)
            or (obs.measure_statistic and basis.measure_statistic
                and obs.measure_statistic != basis.measure_statistic)
        ):
            changes.append(ContextChange(kind="MEASURE_CHANGED", detail="measure_drift"))
        elif not basis.measure_field and not basis.measure_statistic:
            established.append("measure")
    if obs.recipe_id:
        if basis.recipe_id and obs.recipe_id != basis.recipe_id:
            changes.append(ContextChange(kind="PRODUCT_GOAL_CHANGED", detail=obs.recipe_id[:64]))
        elif not basis.recipe_id:
            established.append("recipe")
    if obs.export_format and obs.export_format != basis.export_format:
        # Export target is never analysis-invalidating — refresh only.
        established.append("export")
    if established:
        changes.append(ContextChange(
            kind="BASIS_ESTABLISHED", detail=",".join(established)[:96]))
    return changes


__all__ = [
    "ContextChange",
    "SessionObservation",
    "diff_against",
    "observe_session",
]
