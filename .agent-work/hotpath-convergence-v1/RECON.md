# RECON — Hot-path Convergence v1 (Direction 04)

Date: 2026-09-15 (UTC+8)
Base master: `b44c1c9b`

## Gaps (from D01–D03 reviews)

| Envelope | Gap |
|---|---|
| Mission Runtime | Not auto-created on chat turns; swarm mirror only when `mission_id` passed |
| SkillPolicy | Only via `gis_skill_policy` tool; planners had zero skill imports |
| Evidence/Claim | Projectors ready; no settle hot-path ingest |

## Call-site census (wire targets)

| Seam | Location | Action |
|---|---|---|
| Situation→plan SkillPolicy | `workflow_compiler.compile_workflow` (pre-7b) | `bind_skill_guidance_at_plan_seam` → `compilation.skill_guidance` |
| Situation→plan SkillPolicy | `tools.webgis_map_intent` | attach `skill_guidance` + `hotpath_pi_context` |
| Optional Mission bind | `agent_pi_bridge.SwarmBridge.delegate_compound_task` | `maybe_bind_mission_for_turn` before durable swarm mirror |
| Claim ingest on settle | `completion/pipeline.map_product_block` | `ingest_map_product_settle` + pi card |
| SkillAuthority | `skills/hotpath.py` | REUSE `resolve_skill_guidance` / `attach_skill_guidance_to_plan_inputs` |
| Kill-switch Skill | `skills/policy.policy_enabled` / `GIS_SKILL_POLICY` | REUSE |
| Kill-switch Mission runtime | `GIS_MISSION_RUNTIME` | REUSE |
| Opt-in Mission hotpath | **new** `GIS_MISSION_HOTPATH` (default OFF) | no durable effects without flag |
| Claim ingest kill | **new** `GIS_CLAIM_INGEST` (default ON) | process-local only |

## Non-goals (confirmed)

- No StoryMap compiler rewrite (D05)
- No Alembic ClaimStore table (D05)
- No durable promotion ledger (D06)
- No second planner / DAG / Artifact Registry
