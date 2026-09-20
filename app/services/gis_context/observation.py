"""Deterministic session observation for the GIS working context
(ADR-0204 D4 / Direction 06 M3).

Reads the *existing* authorities — session map_state, MapSpec, and the
gis_situation snapshot — and projects a bounded ``SessionObservation``.
No LLM, no wall clock, no writes. ``diff_against`` compares an observation
with the accepted working basis and emits typed ``ContextChange`` events
for the invalidation engine.

Tolerant-extraction discipline: fields the authorities do not carry (CRS
today, dataset content revisions when absent) stay empty/None = unknown.
Unknown never triggers invalidation — only a *known* drift does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.gis_context.working_context import BasisDataset, GISWorkingContext

MAX_OBS_DATASETS = 12
MAX_OBS_LAYERS = 24
_AOI_EPSILON = 1e-9


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

    view = mapspec.get("view") if isinstance(mapspec.get("view"), dict) else {}
    raw_bounds = view.get("bounds") or view.get("bbox")
    if isinstance(raw_bounds, (list, tuple)) and len(raw_bounds) == 4:
        try:
            obs.aoi_bbox = [float(v) for v in raw_bounds]
        except (TypeError, ValueError):
            obs.aoi_bbox = None

    crs = mapspec.get("crs") or view.get("crs")
    if isinstance(crs, str):
        obs.crs = crs[:64]

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

    prov = state.get("_gis_provenance")
    if isinstance(prov, dict):
        hidden = prov.get("user_hidden_layers")
        if isinstance(hidden, list):
            obs.user_hidden_layers = [str(h)[:64] for h in hidden[:MAX_OBS_LAYERS]]

    if situation_snapshot is not None:
        try:
            temporal = getattr(situation_snapshot, "temporal", None)
            req = getattr(temporal, "requested_period", None)
            val = getattr(req, "value", None)
            if isinstance(val, str):
                obs.time_period = val[:64]
            geo = getattr(situation_snapshot, "geographic", None)
            scope = getattr(geo, "scope_name", None)
            sval = getattr(scope, "value", None)
            if isinstance(sval, str):
                obs.aoi_name = sval[:64]
            goal = getattr(situation_snapshot, "user_goal", None)
            recipe = getattr(goal, "recipe_id", None)
            rval = getattr(recipe, "value", None)
            if isinstance(rval, str):
                obs.recipe_id = rval[:64]
        except Exception:  # noqa: BLE001 — snapshot facts are additive
            pass

    # Measure: first layer carrying an explicit metric/field binding.
    if isinstance(raw_layers, list):
        for ln in raw_layers:
            if not isinstance(ln, dict):
                continue
            metric = ln.get("metric") or ln.get("measure_field") or ln.get("field")
            stat = ln.get("statistic") or ln.get("aggregation")
            if isinstance(metric, str) and metric:
                obs.measure_field = metric[:64]
            if isinstance(stat, str) and stat:
                obs.measure_statistic = stat[:32]
            if obs.measure_field or obs.measure_statistic:
                break

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
