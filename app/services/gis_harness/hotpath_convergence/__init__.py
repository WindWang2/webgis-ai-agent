"""Harness Hot-path Convergence (Direction 04).

Wires MissionRuntime × SkillPolicy × Evidence/Claim into the default
Pi / SessionPlan multi-step GIS turn — reuse only, no new platforms.
"""
from __future__ import annotations

from app.services.gis_harness.hotpath_convergence.capability_bind import (
    CAPABILITY_DISPATCH_BIND_ENV,
    CAPABILITY_INELIGIBLE_CODE,
    CapabilityDispatchDecision,
    capability_dispatch_bind_enabled,
    check_tool_capability_at_dispatch,
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
    "CapabilityDispatchDecision",
    "CLAIM_INGEST_ENV",
    "ClaimIngestReport",
    "MISSION_HOTPATH_ENV",
    "MissionBindResult",
    "bind_skill_guidance_at_plan_seam",
    "capability_dispatch_bind_enabled",
    "check_tool_capability_at_dispatch",
    "build_hotpath_pi_context",
    "claim_ingest_enabled",
    "get_or_create_claim_store",
    "get_turn_context",
    "ingest_map_product_settle",
    "ingest_on_settle",
    "maybe_bind_mission_for_pi_turn",
    "maybe_bind_mission_for_turn",
    "mission_hotpath_enabled",
    "reset_turn_context",
    "set_mission_id",
    "set_skill_bundle",
    "skill_policy_enabled",
]
