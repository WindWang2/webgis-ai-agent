# 07 — Evaluation Baseline: Trace / Replay / Corpus / No-Progress Audit (HEAD 16d1c70)

Read-only audit for Wave 8 (18-stage evidence chain) and Wave 9 (≥20K corpus, ≥100 E2E scenarios,
deterministic gates). All counts below were **actually executed** against the tree (python builders /
`wc`), not copied from docs.

---

## (a) Trace event inventory — what exists TODAY

### A1. `TurnTrace` — turn event ring (ADR-0101 Wave 8 §33), `app/lib/runtime/trace.py`

Closed vocabulary of **17 event kinds** (`KNOWN_EVENT_KINDS` frozenset trace.py:46-53; unknown kinds
silently dropped at :185):

| Constant | Line | Production emitter? |
|---|---|---|
| `EVENT_TURN_START` | trace.py:28 | none (defined only) |
| `EVENT_MODEL_SELECTED` | :29 | none — only consumed by replay invariant check (replay.py:245) |
| `EVENT_CONTEXT_BUILT` | :30 | none |
| `EVENT_TOOL_SURFACE_SELECTED` | :31 | **YES** — app/services/chat/execution_engine.py:558-566 |
| `EVENT_MODEL_REQUEST` | :32 | none |
| `EVENT_TOOL_CALL_PROPOSED` | :33 | **YES** — app/services/chat/tool_pipeline.py:135 |
| `EVENT_TOOL_ARGS_NORMALIZED` | :34 | none |
| `EVENT_DISPATCH_STARTED` | :35 | **YES** — tool_pipeline.py:167 |
| `EVENT_DISPATCH_COMPLETED` | :36 | **YES** — tool_pipeline.py:174,186 |
| `EVENT_ARTIFACT_PRODUCED` | :37 | none |
| `EVENT_PLAN_PROGRESSED` | :38 | none |
| `EVENT_MAP_PRODUCT_CHANGED` | :39 | none |
| `EVENT_NO_PROGRESS` | :40 | none — only replay.py:263 invariant |
| `EVENT_FALLBACK` | :41 | **YES** — app/services/chat/model_runtime/routing.py:221-233 |
| `EVENT_SUBAGENT_SPAWNED` / `_COMPLETED` | :42-43 | none |
| `EVENT_TURN_SETTLED` | :44 | none (settled flag only set if someone emitted it) |

→ **5 of 17 kinds are actually emitted in production.** Emit sites are best-effort try/except
(tool_pipeline.py:24-33), never block execution.

**Bounding / sanitization / correlation / persistence:**
- Sanitization: `bound_meta()` trace.py:63-93 — ≤16 meta entries, keys ≤64 chars, sensitive-key
  hints (`key/token/secret/password/auth/credential/cookie/private`, :55-58) → `[REDACTED]`;
  strings clamped 512 chars; containers >8 items → `<type len=N>`; NaN/inf → None; huge ints → str.
- Bounded: 256 events/turn FIFO + `dropped_count` (TurnTrace :116-133); 128-turn LRU registry
  (TraceRegistry :153-212), thread-safe.
- Correlation: every event carries `turn_id / session_id / tool_call_id / ts` (:96-113).
- Persistence: **none — in-process only** (docstring :16-17: "debug bundle / replay 输入，不落盘";
  durable observation is delegated to tool_metrics ADR-0044 + decision_log).

### A2. `GisTraceChain` — 18-stage evidence chain (ADR-0103 §十), `app/lib/runtime/gis_trace.py`

Canonical 18 stages as `Stage(IntEnum)` gis_trace.py:27-47 (value = canonical order):
`USER_INTENT=1, PARSED_INTENT=2, TASK_ONTOLOGY=3, DATA_PROFILE=4, CANDIDATE_WORKFLOWS=5,
SELECTED_WORKFLOW=6, TOOL_SURFACE=7, MODEL_ROUTING=8, TOOL_CALLS=9, ARGUMENTS=10, TOOL_RESULTS=11,
ARTIFACT_CREATION=12, MAP_MUTATIONS=13, MAP_OBSERVATION=14, VERIFICATION=15, REPAIR=16,
FINAL_VERDICT=17, USER_OUTPUT=18`.

- Per-stage bucket ≤8 records, FIFO-drop-latest (:81-96, "记录绝不阻断执行"); total chain bounded;
  128-chain LRU registry (:122-146); RLock-protected.
- Sanitized through the **same** `bound_meta` (:24, :86) — sensitive keys redacted, payloads bounded.
- `completeness()` = covered stages / 18 (:109-110) — the Wave-8 ≥95% metric already exists as code.
- `record_stage()` module-level emitter (:166-171), never raises, lazy chain creation.
- Replay: `compare_chains()` in app/evaluation/replay.py:346-355 (stage-coverage diff + completeness
  A/B) — but requires **live** in-process chains; no serialization-to-disk / reload path.

**Production emitter coverage — only 5 of 18 stages:**

| Stage | Emitter (file:line) |
|---|---|
| 8 MODEL_ROUTING | app/services/chat/model_routing_bridge.py:114-128 (role/model/reasons/fallback_chain ≤8) |
| 9 TOOL_CALLS | app/agent_pi_bridge.py:903 |
| 10 ARGUMENTS | app/agent_pi_bridge.py:905 (args truncated to `_RECORD_ARGS_BOUND`) |
| 11 TOOL_RESULTS | app/agent_pi_bridge.py:907 (status + latency_ms) |
| 13 MAP_MUTATIONS | app/agent_pi_bridge.py:911 (action_ids/commands ≤8) |

Stages 1-7, 12, 14-18 have **zero production emitters** — only test usage
(tests/unit/test_gis_trace_v3.py:47,69,122-124). On real traffic `completeness()` ≈ 5/18 ≈ 28%,
far below the ≥95% Wave-8 goal. The subsystems that *compute* the missing stages exist
(intent.py, data_qualification.py, planner.py, tool_surface_v3.py, render_observation.py,
runtime_repair.py, map_completion verdicts) but never call `record_stage`.

### A3. Harness evidence model, `app/lib/harness/evidence.py`

- "Missing evidence is never success" invariant (module docstring :5-15).
- `MapSpecValidityTier` (:87-97): NOT_EVALUATED(0) < MUTATION_REJECTED(1) < MUTATION_ACCEPTED(2) <
  SEMANTIC_VALID(3) — ceiling deliberately capped at SEMANTIC_VALID per ADR-0060.
- `MapActionStatus` (:100-121): ISSUED→QUEUED→RUNNING→{SUCCEEDED,FAILED,CANCELLED,SUPERSEDED};
  terminal-only ACK semantics; `MapActionEvidence` (:124-150) carries full correlation
  run/session/turn/tool_call/step/sse_event_id + requested vs actual state.
- `RefResolutionStatus` (:73-84): 6-state (malformed→resolved), real SessionStore resolution.
- `CartographicReviewEvidence` (:192-252): trusted re-read + deterministic desired review;
  bounded checks(64)/findings(32)/repair_attempts(4)/visual(4) **with explicit `*_omitted` counts**
  (:52-70) so bounding can't masquerade as completeness.
- `MapProductEvidence` (:255-304): intent_resolution/recipe_selection/fallback_decisions(≤16)/
  component_selection(≤32)/completeness — harness only transcribes tool-provided evidence.
- `ToolCallEvidence` (:307-330) + `EvaluationRun` (:333-343) = session-scoped aggregation.

### A4. `TurnEvidence` runtime envelope, `app/lib/runtime/evidence.py`

- `Outcome` (:45-53): SUCCEEDED / FAILED / **CANCELLED** / SUPERSEDED / PARTIAL / NOT_EVALUATED —
  cancellation is explicitly not failure.
- `OutcomeRecorder` (:56-109): first-wins exactly-once settlement (cancel can't be overwritten to
  failed). `emit_turn_summary()` (:391-403) is the single sink → one structured INFO log per turn
  (correlation + timing + work counters + token usage #985 + bounded warnings). Registry bounded
  FIFO 64 turns (:39); orphan map-action TTL 120s → warning, never fabricated terminal (:42, :257-266).

### A5. `ToolCallEvent`, `app/lib/harness/tool_call_event.py:13-26`

Shared schema for PiAgentHarness + production `tool_metrics` logger — this is the **only
durable (logged) per-tool telemetry**; includes tool_call_id/args/duration/is_error/error_msg/
cache_hit/result byte counts.

### A6. `GISRuntimeTrace`, `app/services/gis_harness/trace.py` (ADR-0088 P7)

- 4 stages only (:53-56): `finalization`, `runtime_repair`, `action_intent`, `observation`
  (closed set enforced at :95-99); 7 registered counters (:34-50) incl. `runtime_repairs`,
  `runtime_repair_exhausted`, `observation_rejects`, `finalization_repairs`.
- Bounded 64 events/session, 256 sessions, detail ≤8 keys/≤96 chars (:59-75). In-process, never
  persisted, never enters LLM context.

### A7. Provenance, `app/services/provenance/`

- `build_run_manifest()` (manifest.py:48+) — canonical storable JSON of a workflow run
  (revision, graph fingerprint, input bindings + dataset fingerprints, steps w/ tool versions,
  artifacts, mapspec fingerprint, qa/finalization summaries).
- Fingerprint (manifest.py:10-28, fingerprint.py) — sha256 over a projection excluding all volatile
  fields (ids/timestamps/durations/random ref_ids); large keys (`geojson/data/features/...`)
  dropped, leaves ≤200 chars (manifest.py:24-44). Two replays of the same (revision, inputs, tool
  versions) → same fingerprint (INV-MAN2). This is the **only persisted, replay-oriented record**;
  it covers workflow runs, not chat turns / 18-stage chains.

---

## (b) 18-stage coverage table

| # | Stage (Wave-8 name) | Chain emitter | Nearest existing code (non-emitting) |
|---|---|---|---|
| 1 | User Intent | — | intent resolution `resolve_map_request_intent` (gis_harness/intent.py) |
| 2 | Semantic Interpretation | — | planner intent.task / `PARSED_INTENT` only in tests |
| 3 | Data Discovery | — | data_catalog / fixture resolution |
| 4 | Dataset Profile | — | gis_harness/data_qualification.py (compute only) |
| 5 | Scientific Requirements | — | methodology_warnings / warning codes in planner |
| 6 | Workflow Selection | — | MapProductPlanner.plan_from_intent → recipe_id |
| 7 | Algorithm Resolution | — | plan.algorithm_selections (resolved/rejected statuses) |
| 8 | Model Routing | **YES** model_routing_bridge.py:119 | also TurnTrace EVENT_MODEL_SELECTED (unemitted) |
| 9 | Tool Selection | — | tool_surface projection (TurnTrace `tool_surface_selected` emitted, chain stage not) |
| 10 | Tool Execution | **YES** agent_pi_bridge.py:903-907 (CALLS/ARGS/RESULTS) | tool_metrics durable log |
| 11 | Artifact Production | — | artifact_registry; TurnTrace `artifact_produced` unemitted |
| 12 | Map Model | **partial** agent_pi_bridge.py:911 (MAP_MUTATIONS) | SessionStore mutations |
| 13 | Template | — | template_selector.py (no recording) |
| 14 | Component Composition | — | component_composer.py (no recording) |
| 15 | MapSpec | — | mapspec validity ladder in harness evidence (not chain) |
| 16 | Rendering | — | render_observation.py; gis_harness trace `observation` counter |
| 17 | Observation | — | MapActionEvidence ACK path (not chain stage) |
| 18 | Verification / Verdict | — | verdict V2 in completion/ (not chain) |
| — | User Output | — | emit_turn_summary log only |

(Note: the code's Stage enum order differs from the Wave-8 prose list — MODEL_ROUTING sits at 8 and
there is no dedicated Template/Component pair. Mapping the prose list onto the enum is itself a
Wave-8 to-do.)

---

## (c) Corpus inventory — REAL counts (executed against builders)

| Corpus | Location | Count (measured) | Format / tier | zh/en |
|---|---|---|---|---|
| Golden G1–G33 | app/evaluation/golden_cases.py:13 | **33** (25 plan-only, ~8 with execute scripts) | Python `GISBenchmarkCase` | 33 zh / 0 en |
| Deterministic matrix | app/evaluation/case_matrix.py:590 (`build_matrix_cases`) | **273** (16 families 161 + negative 15 + form 10 + scope 50 + decision 17 + compound 30 + style 20) | Python, plan tier (+scope/compound) | 221 zh / 52 en |
| **"306"** = G + matrix | golden_cases.py:476 `get_all_cases` | **306** ✓ verified; groups: poi 73, decision 61, scope 50, raster 22, network 20, od 11, negative 15, form 10, compound 30, semantics 8, repair 3, interpolation 3 | plan + a few execute | 254 zh / 52 en |
| **"3,240"** (docs only) | docs/adr/0101...:47,68 | **stale** — V2 baseline of 47 families; current V3 (59 families) → see next row | — | — |
| Conformance corpus | app/evaluation/conformance.py:663 | **20,088** = Σ families(phrases) × 12 `SCOPE_VARIANTS`(:26) × 9 `UTTERANCE_VARIANTS`(:45); 59 audited `CONFORMANCE_FAMILIES`(:104); groups: distribution 1944, decision 2916, network 1728, terrain 1728, statistics 1620, remote-sensing 1512, accessibility 1404, hydrology 1296, temporal 972, change 972, density 972, sar 864, interpolation 1080, point-pattern 648, equity 432 | **all `plan_only=True`** (conformance.py:650) | 17,388 zh / 2,700 en |
| Anti-claim plan cases | app/evaluation/anti_claim.py:24 | **7** | plan tier, warning-code locks | zh |
| Workflow contract cases | anti_claim.py:122 | **11** (`WC-*`: equity/kriging×3/trend×2/sar/slope-crs/risk/site) | compile_workflow 15-stage + verdict V2 | — |
| Recipe coverage sweep | anti_claim.py:320 | **147** V2 recipes compile smoke | registry smoke | — |
| End-to-end scenarios | app/evaluation/scenarios.py:49 | **7** (`S-schools-distribution/equity`, `S-interpolation`, `S-site-selection`, `S-risk-exposure`, `S-sar-change`, `S-terrain-hydrology`) | plan+execute+contract composite | zh |
| Reliability corpus | app/evaluation/reliability_corpus.py:38 | **19** cases / 19 categories (success, deps, parallel, repair×4, missing-ref, large-result, tool-failure, no-progress×3, tier-3×2, cancellation(structural), artifact, payload-security) | `ScriptedCall` on real registry, no LLM/net | n/a (tool calls, no NL) |
| Cartography golden corpus | tests/cartography/golden_corpus/goldens/ | **503** JSON files (validated parse) | structured digests (resolve→compose→validate→solve), refresh gated by `GOLDEN_CORPUS_UPDATE=1` (corpus.py:16-18) | zh/en titles |
| Science oracles | tests/science_oracles/data/*.json | **1,084** (geostat 253, terrain 216, network 177, sar 101, statistics_local 86, edge_cases 87, regression 44, geodetector 43, statistics_global 36, point_pattern 29, crs_units 5, spectral 7) | JSON numeric oracles | — |
| Multi-turn scenarios | tests/unit/gis_harness/test_multiturn_scenarios.py | **4 sequences / 13 tests** (A equity chain, B kriging params+diff+recompute, C decision, D version V1→V2 style→V3 algo→style-only restore) | pytest against real services | zh |

Grand total of distinct deterministic case instances: ≈ **22,900** (20,088 + 306 + 7 + 11 + 7 + 19 +
503 + 1,084 + 147 sweep + 13 multiturn). Raw Wave-9 ≥20K count is *nominally met*, but nearly all
mass is one dimension (plan-tier paraphrase identity); runtime/failure/edit categories are covered
by ~40 cases total.

---

## (d) Evaluation harness capabilities + gaps

**Capabilities (all deterministic, zero-LLM):**
- `GISBenchmarkRunner` (app/evaluation/runner.py:163): plan tier gates — task (single or
  `expected_tasks` set), recipe (exact or set), capability precision/recall (optional caps exempt,
  :230-236), allowed/forbidden **algorithm prefixes**, methodology-warning patterns + stable
  warning codes (expected & forbidden, :258-286), max_tool_calls, product-facet contract;
  V3 opt-in tier (:347) — ontology top-1, qualification states, fallback tier, and
  **determinism by double plan run** (`check_determinism`); execute tier (:464) — scripted
  dispatch with `fixture:` alias resolution, `expect_error_contains` failure-semantics
  (case.py:23), numeric assertions incl. **byte-budget boundedness of LLM-facing results**
  (`step_result_bytes`, runner.py:118-132), artifact/component assertions, and interaction
  semantics probes user-wins / artifact-expired-no-remount (:630-713).
- Replay harness (app/evaluation/replay.py): (1) `replay_tools` :69 — allow-list only
  (`replay_safe is True`; tier-3 confirm **never** granted, :74-101); contract-key shape
  comparison via `ToolResultView.contract_key`; (2) `simulate_agent_loop` :143 — scripted model
  output through real registry + dedup + CallPatternTracker, invariant checks a/b/c (expected
  error codes, destructive-gate, no-progress reason codes); (3) `check_trace_invariants` :237 —
  dispatch pairing, settle-once, fallback-after-model-selected; plus `ab_compare_tool_surface`
  :298, `compare_chains` :346, `route_decision_diff` :358.
- Metrics library (app/evaluation/runtime_metrics.py): retrieval recall@k/precision@k/irrelevant
  rate (:69), routing fallback/failure/latency (:114), execution completion/repeat/timeout/
  no-progress incidence (:168), GIS correctness (:213), context overflow/truncation metrics (:275).
- Report: app/evaluation/report.py:42 markdown; states "no LLM judge" (:51).
- Test gates: tests/unit/gis_harness/test_conformance_corpus.py — full 20,088 run in the **default
  CI lane** (≈20s, :40-54) + determinism lock (:29) + stratified 543 sample + domain slices;
  tests/unit/test_reliability_security_perf_v2.py:83 (19-case corpus on real registry);
  tests/unit/test_trace_replay_v2.py, tests/unit/test_gis_trace_v3.py,
  tests/cartography/test_golden_corpus.py, tests/science_oracles/test_oracle_replay.py.

**Gaps vs Wave 8/9:**
1. **Chain emitters missing for 13/18 stages** — `completeness()≥95%` is untestable on real turns;
   no production code records USER_INTENT→SELECTED_WORKFLOW or OBSERVATION→USER_OUTPUT.
2. **No chain persistence** — TurnTrace/GisTraceChain are in-process LRU only; nothing serializes
   `as_dict()` for offline replay; `compare_chains` works only on live objects. Replay today =
   tool-call scripts + trace state-machine checks, not full-turn replay.
3. **Conformance corpus is 100% plan-tier** (`plan_only=True`): the 20,088 never execute tools.
   Execute-tier corpus = 19 reliability scripts + ~10 golden/scenario scripts.
4. **V3 opt-in contract fields unused**: `qualification_profile`, `expected_fallback_tier`,
   `check_determinism`, `expected_ontology_task` are implemented in runner.py:347 but **zero
   corpus cases set them** (grep over app/evaluation + tests).
5. **Reliability corpus docstring over-claims** (reliability_corpus.py:4-5): "provider 超时/降级"
   categories listed but `build_reliability_corpus` contains no provider-timeout/downgrade,
   timeout, or context-overflow cases; cancellation case is structural only (a probe call).
6. **≥100 E2E scenarios**: only 7 exist; multi-turn exists as 4 hardcoded pytest sequences, not a
   corpus; zh/en imbalance in execute-tier (0 en scripts).
7. Wave-9 edit categories (style-only, layer visibility, chart edits) and failure categories
   (map observation failure, repair success/failure, stale artifact beyond G9, wrong CRS beyond
   2 contract cases, invalid geometry, conflicting intent beyond C012/C020, data-later-arrives)
   have no corpus representation; some have only runtime counters (observation_rejects,
   runtime_repairs) or one-off pytest probes.

---

## (e) No-progress / cancellation guard inventory (runtime)

| Guard | Config / default | Code |
|---|---|---|
| Round cap | `CHAT_MAX_ROUNDS=60` (app/core/config.py:115) | execution_engine.py:350, loops :1378/:1954 |
| Turn wall-clock (legacy) | `TURN_TOTAL_TIMEOUT_S=900` (config.py:116) | execution_engine.py:356, deadline checks :1376-1385, :1952-1968 → TurnTimeoutError, settle `failure_class="turn_timeout"` |
| Turn wall-clock (Pi) | `PI_TURN_TOTAL_TIMEOUT=300` (#910) | agent_pi_bridge.py:86, checks :1733, :1777, :2121 |
| Pi stall budget | `PI_EVENT_STREAM_TIMEOUT=180s` continuous-silence stall | agent_pi_bridge.py:84-94, :1727-1741, :2085-2121 (`"stall"` vs `"total"` timeout classes) |
| Per-tool timeout | `TOOL_TIMEOUT_S=300` (config.py:110) | tool dispatch |
| No-progress fuse | `LLM_NO_PROGRESS_THRESHOLD=3` (#685, config.py:212) | execution_engine.py:1372 streak init, :1636-1650 progress evaluation (suspicious-result filter), :1949/:2432 stream twin; honest settle FAILED/no_progress :2530 |
| Pattern tracker | `CallPatternTracker` (app/services/chat/no_progress.py:87) — reasons `exact_repeat_failure`, `repeated_read:N` (thr 3), `repeated_mutation_no_state_change`, `alias_oscillation`; canonical signature = alias-folded name + payload-summarized args sha256 (:53-76); bounded 64 records with synced variant-map pruning (:137-146) |
| GIS停滞 diagnostics | `GisProgressTracker` (no_progress.py:176) — `unchanged_map:N` (thr 4, mapspec fingerprint epoch), `unchanged_workflow:N` (thr 6, SessionPlan epoch), `repeated_planning:N` (thr 2); wired per-dispatch at agent_pi_bridge.py:~920 `_record_gis_progress` → `no_progress_hints` in tool details; failures don't advance stall streaks (:208-210) |
| Cooperative cancel | task registry `is_cancelled` checked each round (execution_engine.py:1383); `_settle_cancel` :171 marks Outcome.CANCELLED; orphaned tool_call repair on cancel :1630-1636; `CANCEL_WAIT_TIMEOUT=5.0` bounded cleanup :418-423 |
| Cancellation ≠ failure | Outcome.CANCELLED (app/lib/runtime/evidence.py:50); MapActionStatus.CANCELLED terminal (harness/evidence.py:112); tools carry `cancelled: bool` distinct from error (tool_pipeline.py:51-53, "取消绝不触发 retry") |
| Subagent budgets | `BudgetExceeded` on tool_calls/tokens/wall_time (app/services/subagent_roles.py:34,153-163; subagent.py:344,394 `budget_exceeded:wall_time`) |
| Replay-side machine | app/services/geocompute/replay.py — node state machine: impossible transitions, complete-after-cancel, retry cap `_MAX_ATTEMPTS=4`, `node_marked` only `cancelled`; plus evaluation/replay.py:237 TurnTrace invariants |

---

## (f) Deterministic gates vs LLM-judge (today)

- Verdict path is **fully deterministic**: runner.py:4-6 ("every verdict comes from schema
  assertions, planner evidence, tool traces, MapSpec state, or numeric goldens — never from an LLM
  judge"); report.py:51; app/evaluation/__init__.py:7. No `llm_judge` code exists anywhere in app/.
  The only "judge"-adjacent item is `visual_judge_details` in app/services/runtime_validator.py:116
  (a runtime diagnostics field name; evaluation gates do not consume an LLM verdict).
- Gate strength ranking: numeric oracles + contract compile verdicts (strongest) → execute-tier
  script assertions → plan-tier semantic identity (20,088) → bounded-size invariants → pytest
  probes (weakest, not corpus).
- Missing gate: evidence-chain completeness (`GisTraceChain.completeness()`) and
  `check_trace_invariants` are not yet wired as evaluation gates over recorded runs — they run
  only in unit tests on synthetic traces.

---

## (g) Recommended path to ≥20K *meaningful* cases + ≥100 E2E scenarios

Raw count already clears 20K via the conformance corpus; the deficit is **tier and category**, so:

1. **Freeze conformance growth** (same 59 invariants × paraphrase — further expansion is
   duplication, exactly what case_matrix.py:3-4 warns against).
2. **Runtime scenario matrix (biggest gap → biggest win).** Reuse the proven
   "audited expectation table × deterministic expansion" pattern (conformance.py:616-660) but over
   *runtime* cells: ~24 Wave-9 situation categories (timeout, cancellation, tool-failure,
   provider-degradation, context-overflow, stale-artifact, invalid-geometry, wrong-CRS,
   missing-data, data-later-arrives, conflicting-intent, explicit-algorithm, algorithm-unavailable,
   backend-downgrade, style-only edit, visibility edit, chart edit, observation-failure,
   repair-success, repair-failure, no-progress-recovery, multi-turn follow-up, …) × 8 task families
   × zh/en × 2-3 phrasings → audited `RuntimeCase` table (~700 rows) expanded by fixture/seed
   variants → **3–6K execute-tier cases**. Express as `ReliabilityCase` scripts + `ScriptedCall`
   with the existing assertion hooks (`expect_error_code`, `expect_no_progress_reasons`,
   `expect_error_contains`, numeric/byte assertions); add a deterministic
   failure-injection registry wrapper (seeded timeout/error/degradation) so one script yields
   N injected variants without hand-writing them.
3. **Promote the 4 multiturn sequences into a scenario corpus**: generalize `GISScenario`
   (scenarios.py:25) to ordered turn lists with carried state; compose ≥100 E2E scenarios as
   {7 existing composites} × {failure injection} × {turn continuations} × {zh/en}, each gated by
   plan+execute+contract verdicts like `run_scenario` (scenarios.py:196).
4. **Fill the 13 missing chain emitters** (thin `record_stage` calls in intent.py, planner.py,
   data_qualification.py, tool_surface projection, artifact registry, render_observation.py,
   runtime_repair.py, completion verdict, chat response) — then add
   `completeness ≥ 0.95` as an evaluation gate and serialize `chain.as_dict()` per case run to
   JSONL so `compare_chains` becomes an offline regression/A-B gate (replay-friendly).
5. **Rebalance languages**: generation-time EN seeds for execute-tier scripts (currently 0) and a
   hard per-language floor in the runtime matrix (e.g. ≥30% en) to avoid zh-only drift.
6. **Exercise the dead V3 contract fields**: seed `qualification_profile` / `expected_fallback_tier`
   / `check_determinism` / `expected_ontology_task` into the runtime matrix cells — the runner
   support already exists (runner.py:347), it only lacks cases.
