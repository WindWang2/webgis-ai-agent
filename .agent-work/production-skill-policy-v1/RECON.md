# Production GIS Skill Policy V1 — Phase 0 Recon

- Branch: `harness/production-skill-policy-v1`
- Baseline: `origin/master` @ `e21314a5` (GOAL seeded `9e86f61c`)
- Date: 2026-09-15 (Asia/Shanghai)
- Conclusion: Skill infrastructure (ADR-0182/0191) is a **queryable procedure library** with deterministic resolver + bridges; **no production SkillPolicy** decides when planning may trust a skill. Induced skills are correctly isolated but have no shadow/promotion path into planning.

## 1. Open PR / master delta

| Item | Status | Relation |
|------|--------|----------|
| Open PRs | none at start | clear runway |
| #1326 mission/cartography durability | merged on master | out of ownership — do not touch |
| #1321 cartography feedback eval | merged | out of ownership |
| #1320 Durable Mission Runtime | merged | Mission owns envelope; SkillPolicy must not rebuild Mission |
| #1316 Skill Induction (ADR-0191) | merged | consume InducedSkillStore; add shadow/promotion only |

## 2. Where skills are selected today

| Path | Mechanism | Production planning? |
|------|-----------|----------------------|
| `gis_skill_search` / `gis_skill_detail` / `gis_skill_replay` | Pi-callable tier-2 tools → `SkillLibrary.resolver.resolve` | **Opt-in by LLM**; not automatic |
| `SkillResolver.resolve(SelectionFacts)` | Deterministic rank + eligibility | Library/API only; **not wired into MapProductPlanner / plan_candidates** |
| `project_capability_plan_inputs` / `project_product_requirements` | Pure projection bridges | Available but **no hot-path caller** |
| Core loader `library/core/*.yaml` | Fail-loud singleton | 37 core skills |
| `InducedSkillStore` | Separate dir, pack=`induced`, quarantine on bad assets | **Never merged into core singleton** |

**Answer:** Automatic production selection does **not** exist. Selection is tool-mediated or unit-tested.

## 3. Does procedure influence SessionPlan / capability planning?

- `SkillProcedure` IR exists (steps/decisions/fallbacks/evidence).
- Bridges expose capability ids, roles, completion evidence, step ids.
- `MapProductPlanner` / `plan_candidates` / `plan_graph` have **zero** skill imports.
- Procedure steps do **not** currently rewrite SessionPlan / PlanGraph.

## 4. Selection evidence → replay/evaluation

- `SkillEvidenceRecorder` events: skill_selected, step_*, fallback_triggered, composition_planned, skill_completed.
- Tools can record selection when search selects; no planner-side automatic evidence.
- ReplayTrace / GoalSatisfaction consume plan completion — skill selection is **not** a first-class ReplayTrace field today (bounded notes only if callers write them).

## 5. Induced skills — load + exclusion

- Loader only globs `core/*.yaml` → induced never enters `get_skill_library()`.
- `SKILL_PACKS = ("core", "induced")`; resolver `packs=` filter defaults to all loaded skills (core-only in singleton).
- Why excluded: ADR-0191 D3 — induced are draft assets; runtime must not self-modify reviewed core; promotion = human review + YAML PR.
- Gap: no **shadow** evaluation against production plans; no **promotion proposal** evidence aggregator.

## 6. Trust / lifecycle already present

| Existing | Meaning |
|----------|---------|
| `pack=core` | Reviewed library |
| `pack=induced` | Draft / data asset |
| `deprecated` + `deprecated_by` | Soft version succession |
| Induced quarantine dir | Malformed/toxic assets |

**Do not invent a parallel pack registry.** Policy trust tiers map onto these.

## 7. Smallest authoritative seam (chosen)

```
SelectionFacts (from intent/situation)
        ↓
SkillPolicy.resolve(...)          ← NEW authoritative decision
        ↓
SkillPlanningProjection | fallback_none
        ↓
existing planner / plan_candidates / Execution Graph (unchanged authority)
```

Optional Pi context: bounded decision card only (progressive disclosure preserved).

Kill-switch: env `GIS_SKILL_POLICY=0` → always `mode=none` / clean planner fallback.

## 8. Ownership vs parallel work

**Owns:** SkillPolicy, projection, trust mapping, shadow, performance profile, promotion proposals, lineage, hot-path seam.

**Must not:** Mission Runtime, Evidence/Claim Graph, MapSpec renderer, StoryMap, second ExecutionGraph, cartography feedback loops already on master.

## 9. Implementation plan (post-recon)

1. `policy.py` — SkillPolicyDecision + SkillPolicy
2. `planning_projection.py` — SkillPlanningProjection
3. `performance.py` — situation-conditioned SkillPerformanceProfile
4. `shadow.py` — induced shadow compare (no mutation)
5. `promotion.py` — PromotionCandidateReport (proposals only)
6. `lineage.py` — version lineage records
7. Hot-path helper + evidence event extensions + bounded context
8. Tests per GOAL §18 matrix
9. `review/PRODUCTION-SKILL-POLICY-REVIEW.md` + PR
