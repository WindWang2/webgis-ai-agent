# DECISIONS — Production GIS Skill Policy v1

## D1 — Policy wraps Resolver; does not replace it

`SkillPolicy` consumes `SkillResolver.resolve` for ranking/eligibility, then
decides **trust mode** (`none|guide|execute_guided|shadow|blocked|fallback`).
No second skill selector.

## D2 — Trusted pack = core only

Production guide/execute paths filter `packs=("core",)`. Induced skills never
control execution; they may appear only as `shadow_candidate`.

## D3 — Trust tiers map existing assets

| Tier | Source |
|------|--------|
| core | pack=core, not deprecated |
| experimental | pack=induced |
| quarantined | explicit quarantine / InducedSkillStore quarantine |
| deprecated | skill.deprecated |
| candidate | promotion disposition only (proposal) |

No new pack registry.

## D4 — Single hot-path seam

`resolve_skill_guidance(facts) -> SkillGuidanceBundle` is the authoritative
integration point. Pi tool: `gis_skill_policy`. Planner callers attach via
`attach_skill_guidance_to_plan_inputs` (additive key; never bypasses guards).

## D5 — Kill switch

`GIS_SKILL_POLICY=0|false|off` → mode=none, clean existing planner fallback.

## D6 — Shadow is read-only

`ShadowEvaluationReport.mutates_production` is contractually False. Shadow
compares topology/capabilities/eligibility; does not write SessionPlan,
WorldState, or skill library.

## D7 — Promotion is proposal-only

`build_promotion_report` emits dispositions; never writes core YAML.
Single success never promotes; counterexamples fail closed.

## D8 — Lineage is append-only

`SkillLineageStore` preserves v1 when registering v2; rollback reactivates
prior version without deleting history.

## D9 — Parallel ownership

Do not implement Mission Runtime, Evidence/Claim Graph, MapSpec renderer,
StoryMap, or a second ExecutionGraph/planner.
