# DECISIONS — Hot-path Convergence v1

## D1 — Adapter module, not planner rewrite

`app/services/gis_harness/hotpath_convergence/` owns seams. `generate_plan_candidates`
scoring path stays untouched; SkillPolicy attaches as additive metadata on
`WorkflowCompilation.skill_guidance` and tool responses.

## D2 — SkillPolicy once per Situation→plan

Authoritative call: `bind_skill_guidance_at_plan_seam` →
`resolve_skill_guidance` + `attach_skill_guidance_to_plan_inputs`.
`GIS_SKILL_POLICY=0` → empty guidance, no bundle (unchanged planner path).

## D3 — Mission bind is opt-in

`GIS_MISSION_HOTPATH=1` (and `GIS_MISSION_RUNTIME` on) required for auto
create/reuse. Default off → zero durable mission writes from this helper.
Explicit `mission_id` still passthrough for existing swarm bridge.

## D4 — Claim ingest fail-closed

Settle projectors reuse `evidence_claim.census` / `claims`. Ingest never sets
`ClaimStatus.SUPPORTED`; missing statistic evidence blocks rank claims.
`GIS_CLAIM_INGEST=0` disables. Store is process-local ClaimStore (D03).

## D5 — Bounded Pi card

`build_hotpath_pi_context` exposes skill `pi_context_card` + claim
`grounding_projection` only. Strips CoT / messages / thinking keys.

## D6 — Parallel ownership

Do not implement StoryMap rewrite, Alembic ClaimStore, or promotion ledger.
