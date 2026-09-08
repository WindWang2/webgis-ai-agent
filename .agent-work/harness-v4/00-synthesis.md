# Phase 0 Synthesis — GIS Harness V4 baseline (master@16d1c70)

Sources: 01..08 audit docs in this directory (all claims file:line-cited there).

## The one-paragraph picture

The platform's contracts are strong (ToolDescriptor V3, WorkflowProfile/recipes,
ArtifactContract V3, fingerprints, deterministic compiler, 18-stage trace constants),
but the *production runtime* still re-derives reasoning from natural-language
projections every turn and re-validates completion wholesale: the 15-stage
deterministic workflow compiler is evaluation-only; DatasetProfileV3 is produced but
never persisted/linked (`ArtifactContract.profile_ref` has zero producers); CRS never
reaches the live resolver path (`profile_from_descriptor` hardcodes `crs=None`);
context policy is measure-and-advise (only DROP_OLDEST + dispatch-time offload
execute); traces are in-process rings with 5/18 stages emitted; and the 20,088-case
conformance corpus is 100% plan-tier.

## Load-bearing facts per wave

- **W1 (WorkflowInstance)**: live snapshot = `gis_chapter["map_product"]`, wholesale
  re-validation behind a dedup gate; no instance identity/revision; `rows_fingerprint`
  = capability:status:bound_ref (params/algorithm edits don't invalidate); blocked
  qualification/obligation states don't recompute on data arrival; style/science split
  implicit in gate keys only. Seam: additive `gis_chapter["workflow_instance"]` block,
  single-writer persist pattern, pure evaluators already exist (workflow_schema.py,
  data_qualification.py, plan_graph.py blocked-recovery, 5-dim recompute vocabulary).
- **W2 (data→resolver)**: three profile shapes; V3 richest but orphaned; resolver has
  real hard gates but facts are dead at runtime; precondition fact keys have zero
  producers (and can false-reject); interpolation candidates fragmented across 5
  capabilities, planned only via text keywords. Seam: extend
  `DatasetProfile.to_resolver_profile` adapter + fact producers + profile-aware
  execution-time re-resolution + `select_backend` as pure plan-time function + hinted
  promotion as facts>text steering.
- **W3 (goal graph)**: analysis_graph/product_graph/plan_graph exist as projections;
  no serializable diffable goal graph; SessionPlan chapters are the reconciliation
  anchor.
- **W4 (retrieval)**: V3 dynamic surface (capability hits + lexical rank + contract
  filter + byte-budget projection) with tier-3/destructive gating that retrieval
  cannot bypass; descriptor rich fields unused by ranking; no artifact-aware matching,
  failure feedback, or continuation boost; index keyed on schema-only fingerprint.
  Seam: enrich `ToolLexicon`, pure rerank before contract filter, 3 additive
  `ToolSelectionContext` fields, descriptor-fingerprint index key.
- **W5 (context)**: assembler computes advice after assembly and only logs it;
  SUMMARIZE absent; RELOAD_REF broken by LRU eviction (payload+alias+descriptor
  deleted); no overflow re-trim/retry; content-blind folding can drop verdict-bearing
  messages. Seam: `context_policy.py` executing the six ops at assemble time; ref
  spill-to-artifact on eviction; tombstones; safety pins; deterministic overflow
  recovery.
- **W6 (subagents)**: SubagentDispatcher + SubagentRole/Budget exist (depth≤2,
  fail-closed mutation filter, budgets, model-role routing); 5 of 9 specialist roles
  missing; `spawn_subagent` tool exposes no role param; no expected_outputs/
  failure_behavior fields; no parallel spawn; no budget roll-up.
- **W7 (observation)**: loop exists structural-only (frontend observation with
  fingerprint/generation guards → finalizer ≤2 repair passes → verdict persisted);
  gaps: chart_required not validated at completion, map-model compat not audited at
  completion, extent ignores zoom-form, uncertainty disclosure detects only blocked
  obligations, no pixel evidence (heuristic validator is agent-tool-only; stays
  non-gating, honestly disclosed).
- **W8 (trace)**: 18-stage constants exist; 5/18 emitted (routing + pi tool seams);
  traces bounded/sanitized/correlated but in-process only; provenance manifests are
  the only durable/replay-stable store. Need 13 emitters + JSONL serialization +
  completeness metric as an offline gate.
- **W9 (corpus)**: conformance 20,088 plan-only (17,388 zh / 2,700 en) + 306 intent +
  7 E2E + 1,084 oracles + 503 cartography digests; runtime-tier categories absent;
  reliability corpus over-claims. Need runtime-tier matrix (failure injection,
  staleness, style/visibility/chart edits, observation failure, repair outcomes,
  timeout/cancel/overflow), ≥100 E2E scenarios, chain-completeness gate. Keep
  deterministic gates (no LLM-judge); sampling may be added separately.

## Frozen-seam verdict

No frozen seam requires breaking changes. All waves attach additively
(domain-pack/module additions, additive gis_chapter block, additive schema fields,
retrieval layer above the registry, assembler-internal policy application). ADR-0104
documents this; contract/parity tests pin the touched shared files
(session_plan apply path untouched; `RefDescriptor` gains optional `crs` field;
`profile_from_descriptor` stops fabricating `crs=None` honesty-fix).

## V4 runtime architecture (target)

```
chat → Pi bridge → bind_turn_prompt(WorkflowInstance projection + tool surface V4
     + context policy) → dispatch → ToolRegistry → artifacts(+DatasetProfileV3 link)
        │                                      │
        │ event: artifact/staleness/style/     ▼
        │ algorithm/data arrival        MapSpec → render → observation
        ▼                                      │
WorkflowInstance.apply_event (deterministic)   ▼
  → recompute dimensions → blocked/unblocked → repair actions → evidence/trace (18)
  → completion contract re-eval (incremental, revisioned, fingerprinted)
```

WorkflowInstance is a **runtime projection** persisted at `gis_chapter["workflow_instance"]`
single-writer, mirroring the map_product pattern; it never replaces SessionPlan or
WorkflowProfile truth.

## Wave sequencing (implementation order)

1. ADR-0104 + W1 core (state machine + events + persist + tests) — main agent.
2. Parallel subagent batch A: W2 (profile→resolver), W4 (retrieval v4),
   W5 (context policy), W6 (subagent roles) — precise specs from audits.
3. W3 goal graph (on top of W1) + W7 observation completion — main + subagent.
4. W8 emitters + JSONL chain persistence + completeness gate; W9 runtime corpus + E2E.
5. Cross-wave integration tests, ruff/compile sweep, benchmark gates.
6. Review round 1 (6 perspectives) → fixes → round 2 → PR.
