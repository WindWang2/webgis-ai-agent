"""Semantic GIS Intelligence agent tools (ADR-0092 Phase C).

Two tier-2 tools:
- ``profile_dataset_semantics``: DatasetProfile + bounded value samples →
  field-level semantic roles with evidence-graded confidence.
- ``suggest_analysis_patterns``: query (+ optional dataset) → analysis
  pattern projection with honest capability-boundary disclosures.

These are advisory/metadata tools — they never execute analysis and never
replace the planner. Recommended capabilities still flow through
SessionPlan → CapabilityRegistry → AlgorithmResolver.
"""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool
from app.tools._utils import trim_features
from app.lib.gis.dataset_profile import DatasetProfile
from app.lib.gis.semantic_profile import (
    MAX_VALUE_SAMPLES,
    derive_semantic_profile,
)
from app.lib.gis.pattern_projection import project_patterns

logger = logging.getLogger(__name__)

#: bounded sample fetch from ref data (value evidence, never LLM payload)
_SAMPLE_FEATURES = 200


def _collect_value_samples(geojson: Dict[str, Any]) -> Dict[str, List[Any]]:
    features = (geojson or {}).get("features") or []
    samples: Dict[str, List[Any]] = {}
    seen: Dict[str, int] = {}
    for f in features[:_SAMPLE_FEATURES]:
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        if not isinstance(props, dict):
            continue
        for key, val in props.items():
            if val is None or isinstance(val, (dict, list)):
                continue
            n = seen.get(key, 0)
            if n >= MAX_VALUE_SAMPLES:
                continue
            samples.setdefault(key, []).append(val)
            seen[key] = n + 1
    return samples


class ProfileDatasetSemanticsArgs(BaseModel):
    """geojson_ref is a declared ref cursor: the registry's transparent alias
    resolution must NOT inline the dataset payload into the argument (the
    tool resolves the ref itself, fetch-on-demand)."""

    geojson_ref: Optional[str] = Field(None, json_schema_extra={"ref_cursor": True})
    geojson: Optional[Dict[str, Any]] = None
    user_roles: Optional[Dict[str, str]] = None
    session_id: str = ""


class SuggestAnalysisPatternsArgs(BaseModel):
    query: str
    geojson_ref: Optional[str] = Field(None, json_schema_extra={"ref_cursor": True})
    semantic_profile: Optional[Dict[str, Any]] = None
    session_id: str = ""


def register_semantic_tools(registry: ToolRegistry) -> None:

    @tool(registry,
        name="profile_dataset_semantics",
        description=(
            "Derive field-level semantic roles for a dataset (admin dimension, "
            "population/area/count measures, temporal field, normalization "
            "denominator...) with evidence-graded confidence. Use BEFORE "
            "comparative/equity analysis to know what conclusions the data "
            "can and cannot support."
        ),
        parameters={
            "type": "object",
            "properties": {
                "geojson_ref": {
                    "type": "string",
                    "description": "Session ref/alias of the dataset to profile",
                },
                "geojson": {"type": "object", "description": "Inline GeoJSON (fallback when no ref)"},
                "user_roles": {
                    "type": "object",
                    "description": "field → role overrides declared by the user (highest confidence)",
                },
            },
        },
        args_model=ProfileDatasetSemanticsArgs,
        tier=2, domains=["statistics"],
        tags=["semantic", "profile", "metadata"],
    )
    async def profile_dataset_semantics(
        geojson_ref: Optional[str] = None,
        geojson: Optional[Dict[str, Any]] = None,
        user_roles: Optional[Dict[str, str]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            payload: Optional[Dict[str, Any]] = geojson
            if payload is None and geojson_ref and session_id:
                from app.services.session_data import session_data_manager

                # ref/alias → payload via the session store's own resolution.
                try:
                    ref_id = await session_data_manager.resolve_alias(
                        session_id, geojson_ref
                    )
                except Exception:  # noqa: BLE001
                    ref_id = geojson_ref
                if ref_id:
                    payload = await session_data_manager.get(session_id, ref_id)
            if not isinstance(payload, dict) or not (
                (payload.get("features") or [])
                and payload.get("type") == "FeatureCollection"
            ):
                return {
                    "success": False,
                    "error": "需要 GeoJSON FeatureCollection（经 geojson_ref 或 geojson 内联提供）",
                }
            # Bounded feature view for both the structural profile and the
            # value samples — the full payload never leaves the session store.
            bounded = trim_features(payload, max_features=_SAMPLE_FEATURES)
            features = bounded.get("features") or []
            profile = DatasetProfile(
                source="synthetic",
                feature_count=len(payload.get("features") or []),
                fields={},
            )
            # Derive dtype evidence from the bounded sample.
            dtypes: Dict[str, str] = {}
            for f in features:
                for k, v in (f.get("properties") or {}).items():
                    if k in dtypes:
                        continue
                    if isinstance(v, bool):
                        dtypes[k] = "boolean"
                    elif isinstance(v, int):
                        dtypes[k] = "integer"
                    elif isinstance(v, float):
                        dtypes[k] = "number"
                    elif isinstance(v, str):
                        dtypes[k] = "string"
            profile.fields = dtypes
            profile.fields_status = "explicit" if dtypes else "unknown"
            samples = _collect_value_samples(payload)
            sem = derive_semantic_profile(
                profile, value_samples=samples, user_roles=user_roles
            )
            return {
                "success": True,
                "semantic_profile": sem.to_dict(),
                "sample_size": min(len(payload.get("features") or []), _SAMPLE_FEATURES),
                "note": "unknown = 证据不足，不代表字段无意义；user_roles 可提升置信",
            }
        except Exception as e:  # noqa: BLE001 — metadata tool must not crash turns
            logger.warning("[semantic_tools] profile_dataset_semantics failed: %s", e)
            return {"success": False, "error": str(e)[:300]}

    @tool(registry,
        name="suggest_analysis_patterns",
        description=(
            "Match the query against the GIS analysis pattern library and "
            "report: recommended capabilities, required output facets, "
            "normalization guidance, classic pitfalls, and — critically — "
            "which methodological preconditions the current data CANNOT "
            "support (e.g. equity claims need a population denominator). "
            "Advisory only; execution still goes through the planner."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user's analysis question"},
                "geojson_ref": {
                    "type": "string",
                    "description": "Optional dataset ref to ground the pattern check in real fields",
                },
                "semantic_profile": {
                    "type": "object",
                    "description": "Precomputed semantic profile (from profile_dataset_semantics)",
                },
            },
            "required": ["query"],
        },
        args_model=SuggestAnalysisPatternsArgs,
        tier=2, domains=["statistics"],
        tags=["semantic", "pattern", "methodology"],
    )
    async def suggest_analysis_patterns(
        query: str,
        geojson_ref: Optional[str] = None,
        semantic_profile: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            sem: Optional[Any] = None
            if semantic_profile is None and geojson_ref:
                # Reuse the semantic tool's derivation for the same ref.
                built = await profile_dataset_semantics(geojson_ref=geojson_ref, session_id=session_id)
                if built.get("success"):
                    from app.lib.gis.semantic_profile import SemanticDatasetProfile

                    sem = SemanticDatasetProfile.model_validate(built["semantic_profile"])
            elif semantic_profile is not None:
                from app.lib.gis.semantic_profile import SemanticDatasetProfile

                sem = SemanticDatasetProfile.model_validate(semantic_profile)
            from app.services.gis_harness.intent import resolve_map_request_intent

            intent = resolve_map_request_intent(query)
            projection = project_patterns(
                query, intent_task=intent.task, semantic_profile=sem
            )
            return {
                "success": True,
                "task": intent.task,
                "patterns": projection.to_bounded_list(),
                "data_disclosures": projection.data_disclosures,
                "note": (
                    "advisory only — 能力执行仍经 webgis_map_intent / planner 路径；"
                    "disclosures 是数据能力边界，必须如实转达给用户"
                ),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("[semantic_tools] suggest_analysis_patterns failed: %s", e)
            return {"success": False, "error": str(e)[:300]}
