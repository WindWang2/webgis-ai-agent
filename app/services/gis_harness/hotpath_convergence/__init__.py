"""Harness Hot-path Convergence (Direction 04).

Wires MissionRuntime × SkillPolicy × Evidence/Claim into the default
Pi / SessionPlan multi-step GIS turn — reuse only, no new platforms.
"""
from __future__ import annotations

from app.services.gis_harness.hotpath_convergence.capability_bind import (
    CAPABILITY_DISPATCH_BIND_ENV,
    CAPABILITY_INELIGIBLE_CODE,
    CAPABILITY_INELIGIBLE_KEY,
    CapabilityBindOutcome,
    CapabilityDispatchDecision,
    bind_tool_capability,
    capability_dispatch_bind_enabled,
    CAPABILITY_BIND_POLICY_VERSION,
    check_tool_capability_at_dispatch,
)
from app.services.gis_harness.hotpath_convergence.capability_reasons import (
    MAX_REASON_CODES,
    REASON_CODES,
    canonical_reason_code,
    reason_codes_from_qualification,
)
from app.services.gis_harness.hotpath_convergence.claim_ingest import (
    ClaimIngestReport,
    ingest_map_product_settle,
    ingest_on_settle,
)
from app.services.gis_harness.hotpath_convergence.flags import (
    CLAIM_INGEST_ENV,
    MISSION_HOTPATH_ENV,
    claim_ingest_enabled,
    mission_hotpath_enabled,
    skill_policy_enabled,
)
from app.services.gis_harness.hotpath_convergence.mission_bind import (
    MissionBindResult,
    maybe_bind_mission_for_turn,
)
from app.services.gis_harness.hotpath_convergence.pi_card import build_hotpath_pi_context
from app.services.gis_harness.hotpath_convergence.pi_mission import (
    maybe_bind_mission_for_pi_turn,
)
from app.services.gis_harness.hotpath_convergence.runtime_situation import (
    OFFLINE_ENV,
    SITUATION_SUPPLY_ENV,
    WORKER_PROBE_ENV,
    RuntimeSituation,
    build_runtime_situation,
    build_runtime_situation_async,
    merge_situation_facts,
    reset_runtime_situation_cache,
    situation_supply_enabled,
)
from app.services.gis_harness.hotpath_convergence.security_supply import (
    SECURITY_SUPPLY_ENV,
    SecurityBridgeView,
    bind_session_credentials,
    security_supply_enabled,
)
from app.services.gis_harness.hotpath_convergence.session_ctx import (
    get_or_create_claim_store,
    get_turn_context,
    reset_turn_context,
    set_mission_id,
    set_skill_bundle,
)
from app.services.gis_harness.hotpath_convergence.skill_bind import (
    bind_skill_guidance_at_plan_seam,
)

__all__ = [
    "CAPABILITY_DISPATCH_BIND_ENV",
    "CAPABILITY_INELIGIBLE_CODE",
    "CAPABILITY_INELIGIBLE_KEY",
    "CapabilityBindOutcome",
    "CapabilityDispatchDecision",
    "CLAIM_INGEST_ENV",
    "ClaimIngestReport",
    "MAX_REASON_CODES",
    "MISSION_HOTPATH_ENV",
    "MissionBindResult",
    "OFFLINE_ENV",
    "REASON_CODES",
    "RuntimeSituation",
    "SECURITY_SUPPLY_ENV",
    "SITUATION_SUPPLY_ENV",
    "SecurityBridgeView",
    "WORKER_PROBE_ENV",
    "bind_session_credentials",
    "bind_skill_guidance_at_plan_seam",
    "bind_tool_capability",
    "build_hotpath_pi_context",
    "build_runtime_situation",
    "build_runtime_situation_async",
    "canonical_reason_code",
    "capability_dispatch_bind_enabled",
    "CAPABILITY_BIND_POLICY_VERSION",
    "check_tool_capability_at_dispatch",
    "claim_ingest_enabled",
    "get_or_create_claim_store",
    "get_turn_context",
    "ingest_map_product_settle",
    "ingest_on_settle",
    "maybe_bind_mission_for_pi_turn",
    "maybe_bind_mission_for_turn",
    "merge_situation_facts",
    "mission_hotpath_enabled",
    "reason_codes_from_qualification",
    "reset_runtime_situation_cache",
    "reset_turn_context",
    "security_supply_enabled",
    "set_mission_id",
    "set_skill_bundle",
    "situation_supply_enabled",
    "skill_policy_enabled",
]
