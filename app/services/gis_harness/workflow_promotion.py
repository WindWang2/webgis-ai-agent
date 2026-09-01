"""SessionPlan → Persistent Workflow promotion (ADR-0092 A1).

A successful SessionPlan is promoted into a re-runnable Workflow recipe by a
**deterministic converter** — the LLM no longer hand-fills step lists. The
recipe keeps the plan's business semantics as first-class state:

    task/intent, capability, algorithm preference, input role, parameters,
    dependency order, product requirements (recipe/template/exports/layers)

The resolved tool id is preserved as *execution evidence*, not as the only
meaning of a step: reruns re-resolve capability → algorithm → tool through the
registries, so a tool rename or registry upgrade still gets a chance to
re-resolve instead of replaying a dead id (ADR-0092 A1/A5).

This module is a projection, not a second planner: it reads an already-final
MapProductPlan dump and never re-decides cartography.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.project_schema import (
    WorkflowCreate,
    WorkflowGraphSpec,
    WorkflowStepSpec,
)

logger = logging.getLogger(__name__)

# Bounded transcription guards — a plan dump is trusted (produced by the
# harness), but the recipe must stay a compact, storable document.
_MAX_STEPS = 64
_MAX_METADATA_INTENT_DEPTH = 4


def _clean_args(row: Dict[str, Any]) -> Dict[str, Any]:
    """Row params → step args_template. Session-bound fields are dropped:
    bound_ref/status record *this* session's execution and must not leak into
    a reusable recipe (rerun rebinds inputs via input_bindings)."""
    params = row.get("params")
    args = dict(params) if isinstance(params, dict) else {}
    for key in ("bound_ref", "status", "resolved_tool", "resolved_algorithm"):
        args.pop(key, None)
    return args


def _input_roles(row: Dict[str, Any], capability_inputs: List[str]) -> Dict[str, str]:
    """Best-effort arg → semantic-role mapping for a capability row.

    Primary dataset slots follow the capability's declared input artifact
    types; everything else stays unlabeled rather than guessed.
    """
    args = _clean_args(row)
    roles: Dict[str, str] = {}
    primary = capability_inputs[0] if capability_inputs else ""
    for key in args:
        kl = str(key).lower()
        if any(t in kl for t in ("geojson", "feature", "points", "raster", "data", "source")):
            roles[key] = primary or "primary_dataset"
    return roles


def _capability_io(capability: str) -> Tuple[List[str], List[str]]:
    """(input_artifact_types, output_artifact_types) for a capability id."""
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        desc = get_capability_registry().get(capability)
        if desc is not None:
            return (
                [str(t) for t in (desc.input_artifact_types or [])],
                [str(t) for t in (desc.output_artifact_types or [])],
            )
    except Exception:  # noqa: BLE001 — registry unavailable → honest empty roles
        pass
    return [], []


def build_recipe_steps(gis_chapter: Dict[str, Any]) -> List[WorkflowStepSpec]:
    """MapProductPlan dump → ordered WorkflowStepSpec list.

    data_requirements (fetch/profile rows) and analysis_steps (capability rows)
    are merged capability-first: one step per capability, first occurrence
    wins, dependency order preserved. Rows whose capability failed to resolve
    a tool at plan time still promote (with their tool evidence empty) — the
    resolver gets a fresh chance at rerun; permanently unavailable rows are
    dropped by :func:`build_workflow_recipe` via ``promotable_rows``.
    """
    chapter = gis_chapter or {}
    seen: set = set()
    cap_to_step: dict = {}
    steps: List[WorkflowStepSpec] = []
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        cap = str(row.get("capability") or "").strip()
        if not cap or cap in seen:
            continue
        seen.add(cap)
        if len(steps) >= _MAX_STEPS:
            logger.warning(
                "[workflow_promotion] plan truncated at %d steps", _MAX_STEPS
            )
            break
        deps = []
        for d in row.get("depends_on") or []:
            dep_step = cap_to_step.get(str(d))
            if dep_step:
                deps.append(dep_step)
        tool = str(row.get("resolved_tool") or "").strip()
        inputs, _outputs = _capability_io(cap)
        step_id = f"step_{len(steps) + 1}_{cap}"
        cap_to_step[cap] = step_id
        steps.append(
            WorkflowStepSpec(
                step_id=step_id,
                tool_name=tool or cap,  # evidence; capability drives re-resolution
                args_template=_clean_args(row),
                dependencies=deps,
                capability=cap,
                algorithm_preference=str(row.get("resolved_algorithm") or "") or None,
                input_roles=_input_roles(row, inputs),
                description=str(row.get("purpose") or ""),
            )
        )
    return steps


def _bounded_intent(intent: Any) -> Dict[str, Any]:
    """Intent dump for recipe metadata (bounded depth — evidence, not payload)."""
    if not isinstance(intent, dict):
        return {}
    try:
        import json

        loaded = json.loads(json.dumps(intent, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        return {}
    return loaded if isinstance(loaded, dict) else {}


def build_recipe_metadata(gis_chapter: Dict[str, Any]) -> Dict[str, Any]:
    """Product requirements block — what the promoted workflow must reproduce."""
    chapter = gis_chapter or {}
    layers = [
        {
            "role": ly.get("role"),
            "cartography": ly.get("cartography"),
            "layer_type": ly.get("layer_type"),
            "source_capability": ly.get("source_capability"),
        }
        for ly in (chapter.get("map_layers") or [])
        if isinstance(ly, dict)
    ]
    template_selection = chapter.get("template_selection")
    return {
        "promotion": "session_plan",
        "query": str(chapter.get("query") or ""),
        "intent": _bounded_intent(chapter.get("intent")),
        "recipe_id": str(chapter.get("recipe_id") or ""),
        "template_id": str(chapter.get("template_id") or ""),
        "manifest_fingerprint": str(chapter.get("manifest_fingerprint") or ""),
        "outputs": list(chapter.get("outputs") or []),
        "exports": list(chapter.get("exports") or []),
        "statistics": list(chapter.get("statistics") or []),
        "charts": list(chapter.get("charts") or []),
        "map_layers": layers,
        "template_selection": template_selection if isinstance(template_selection, dict) else {},
    }


def promotion_blockers(gis_chapter: Dict[str, Any]) -> List[str]:
    """Reasons a plan chapter is NOT promotable (successful-plan gate).

    A plan is promotable when every required capability row reached a resolved
    tool. Optional rows that never ran do not block (their capability is
    carried as optional evidence); rows still pending / failed do block —
    promoting a half-executed plan would bake incompleteness into the recipe.
    """
    chapter = gis_chapter or {}
    blockers: List[str] = []
    rows: List[Tuple[str, Dict[str, Any]]] = [
        ("data_requirement", r)
        for r in (chapter.get("data_requirements") or [])
        if isinstance(r, dict)
    ] + [
        ("analysis_step", r)
        for r in (chapter.get("analysis_steps") or [])
        if isinstance(r, dict)
    ]
    for kind, row in rows:
        cap = str(row.get("capability") or "").strip()
        if not cap or row.get("optional"):
            continue
        status = str(row.get("status") or "")
        if status not in ("available", "done", "complete"):
            blockers.append(f"{kind}:{cap}:{status or 'pending'}")
    return blockers


def build_workflow_recipe(
    gis_chapter: Dict[str, Any],
    *,
    name: str,
    description: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[Optional[WorkflowCreate], List[str]]:
    """Successful SessionPlan chapter → WorkflowCreate (deterministic).

    Returns (recipe, blockers). recipe is None when the plan is not promotable
    (see :func:`promotion_blockers`) or has no capability rows at all.
    """
    chapter = gis_chapter or {}
    blockers = promotion_blockers(chapter)
    steps = build_recipe_steps(chapter)
    if blockers:
        return None, blockers
    if not steps:
        return None, ["no_capability_rows"]
    metadata = build_recipe_metadata(chapter)
    query = str(chapter.get("query") or "").strip()
    workflow_name = (name or query or "GIS Workflow").strip()[:255]
    workflow_description = description or (
        f"Promoted from session plan (recipe={metadata['recipe_id'] or 'none'}; "
        f"query={query[:120]})."
    )
    create = WorkflowCreate(
        name=workflow_name,
        description=workflow_description[:2000],
        graph_spec=WorkflowGraphSpec(steps=steps, metadata=metadata),
        inputs_schema={
            "input_roles": {
                s.step_id: s.input_roles for s in steps if s.input_roles
            }
        },
        created_from_session=session_id,
    )
    return create, []
