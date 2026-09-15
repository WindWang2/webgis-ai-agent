# Adversarial Review — Production GIS Skill Policy (Direction 02)

Date: 2026-09-15 (Asia/Shanghai)
Branch: `harness/production-skill-policy-v1`
Baseline master: `e21314a5`

## Axes

| Axis | Finding | Severity | Disposition |
|---|---|---|---|
| Planner bypass | Policy only attaches `skill_guidance` / suggestions; never clears security/resource gates | — | Pass |
| Skill overreach | Skills describe HOW; projection does not grant capability authority | — | Pass |
| Induced privilege escalation | Trusted path packs=("core",); induced only shadow; mutates_production=False | — | Pass |
| Cross-tenant state | Performance/lineage/evidence are process-local bounded stores; no shared durable tenant write | — | Pass |
| Overfitting | Promotion requires support≥5, situation diversity≥3, geometry diversity; counterexamples fail closed; single success never promotes | — | Pass |
| Stale skill version | Lineage append-only + rollback; deprecated → fallback | — | Pass |
| False promotion | Disposition is proposal-only; no core YAML write | — | Pass |
| Failure fallback | none/fallback/blocked → guides_planning=False; kill-switch GIS_SKILL_POLICY=0 | — | Pass |
| Context bloat | Pi card is bounded decision only; progressive disclosure preserved | — | Pass |
| Resource regressions | Pure CPU deterministic; no LLM/network; stores capped | — | Pass |
| Non-determinism | Same facts → same decision (tested) | — | Pass |
| Parallel ownership | No Mission Runtime / MapSpec / StoryMap / second ExecutionGraph | — | Pass |

## P0/P1

None confirmed after local matrix (25 policy tests + 50 existing skill suite regressions).

## Residual gaps (honest)

1. **Planner auto-call**: `generate_plan_candidates` is not rewritten to invoke SkillPolicy on every plan — callers/tools use `resolve_skill_guidance` / `gis_skill_policy`. Full automatic binding remains a follow-up once Mission/Session seams stabilize.
2. **CRS class / online-offline**: SelectionFacts lacks first-class fields; situation_signature reserves slots; performance profile records `unknown` until facts grow.
3. **Durable promotion ledger**: Promotion reports are in-memory/API objects; persistence can reuse ReplayTrace refs later without a second telemetry platform.
4. **Composition role sparsity**: Many core skills omit dense input/output roles; compatibility treats empty as unknown (not conflict).

## Test evidence

```text
pytest tests/unit/gis_harness/test_skill_policy_v1.py -o addopts=  → 25 passed
pytest tests/unit/gis_harness/test_skill_{resolver,tools,library,policy}_v1.py -o addopts= → 75 passed
```

Covers selection, hot-path, induced shadow, quarantine, promotion anti-overfit, lineage rollback, composition, determinism, kill-switch.
