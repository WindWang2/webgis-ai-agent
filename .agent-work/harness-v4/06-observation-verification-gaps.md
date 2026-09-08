# 06 — Observation & Verification Gaps (Cartography completion / Map observation / Map product audit)

Repo: `webgis-ai-agent-harness-v4` @ `16d1c70`. Scope: GIS Harness V4 Wave 7 closed loop
(Desired Map State → render → Observed Map State → QA → repair → re-observe → completion).
All paths relative to repo root unless absolute. Line numbers from working tree at HEAD.

---

## (a) Current observation pipeline (file:line)

There is **no `DesiredMapState` class**. The Desired Map State is `MapSpec`
(single desired-state authority: `app/services/gis_harness/render_observation.py:7-9`,
`docs/cartographic-closed-loop.md:44-46`) plus the planner chapter
(`gis_chapter.map_layers` = planned result layers with `role` ∈ primary|secondary|result,
`app/services/gis_harness/completion/contracts.py:26`). The Observed Map State is the
frontend `RenderObservation` stored in session `map_state` key `_cartographic_observation`
(`render_observation.py:49`).

### Pipeline stages

1. **Desired state authoring/mutation**: `MapSpecLifecycleEngine` intents
   (`app/services/mapspec/lifecycle_engine.py`, exported `app/services/mapspec/__init__.py:4-28`);
   mutation revision `_cartographic_mutation_revision` advanced under session lock
   (`completion/pipeline.py:402-409`, `chat.py` stamping at `1537-1543`).
2. **Render**: frontend MapSpecRuntime reconciles spec → MapLibre
   (`frontend/lib/mapspec-runtime/runtime.ts`; compiler output `frontend/lib/mapspec-compiler/`).
3. **Observe (structural only)**: after reconcile settles (bounded 400ms idle race,
   `frontend/lib/mapspec-runtime/render-observation.ts:47,221`), `collectRenderObservation`
   (`render-observation.ts:269`) reads live MapLibre via `map.getLayer`/`map.getSource`
   (`frontend/lib/mapspec-runtime/runtime-evidence.ts:45,165`), plus chrome component
   projection (`observeComponents`, `render-observation.ts:168`) and a bounded runtime-error
   ring (`RuntimeErrorRing`, `render-observation.ts:136-166`). POSTed to
   `POST /sessions/{sid}/cartographic-observation` (`app/api/routes/chat.py:1446`; client
   `frontend/lib/hooks/use-cartographic-observation.ts:226`).
4. **Acceptance gate**: server rejects stale fingerprint / stale client_generation
   (`chat.py:1487-1499`), stamps server-side `mapspec_revision` (`chat.py:1537-1543`),
   persists latest-wins observation with monotonic `sequence` (`chat.py:1545-1560`).
5. **Evaluate — three coexisting layers**:
   - **L2–L4 quality loop** (`chat.py:1577` → `evaluate_cartographic_session`,
     `app/services/cartography_runtime.py:427,517`): harness evidence
     (`app/lib/harness/pi_agent_harness.py:1023-1105`) runs deterministic desired-state
     rules `evaluate_cartography_semantics` (`app/lib/cartography/semantic_checks.py:1558`)
     + runtime convergence review, gate `CartographicQuality`
     (`app/lib/harness/evaluator.py:197-237`), then may issue AUTO_SAFE
     `cartographic_runtime_repair` map action (`cartography_runtime.py:632-813`,
     planner `app/lib/cartography/quality_loop.py:289,531`).
   - **P9 Map Product Finalizer** (structural QA): triggered by each observation
     (`chat.py:1609-1611` `reason="render_observation"`), tool results, and turn settle
     (`final_gate=True`: `app/agent_pi_bridge.py:2159-2161`, `chat.py:780-782`) via
     `maybe_finalize_map_product` (`completion/pipeline.py:434`). `run_map_finalization`
     (`pipeline.py:72`) = validate → repair → revalidate ≤ 2 passes (`pipeline.py:126-155`,
     `MAX_FINALIZATION_PASSES=2` `contracts.py:15`), then render-observation validation
     (`pipeline.py:165-181` → `validate_render_observation`, `render_observation.py:139-288`),
     viewport/V3 checks (`pipeline.py:184-224`), status freeze (`pipeline.py:229-266`),
     `final_map_status` aggregation (`pipeline.py:269-279` → `map_verification.py:232-255`).
   - **V3 final map verification** (additive warnings): layer order / extent / stale overlay
     (`completion/map_verification.py:85-118,121-138,141-210`).
6. **Repair** (see (d)) → revision advances → frontend re-reconcile → new observation →
   dedup gate broken by new `(revision, render_seq, rows_fingerprint)` keys
   (`pipeline.py:294-328,495-499`) → re-verify. P9 render errors归 needs_repair (self-healing
   semantics, not failed): `contracts.py:54-56,79-84`, `pipeline.py:242-263`.
7. **Completion/verdict**: `MapCompletionResult` (`contracts.py:333-398`) with
   `status` (pending/needs_repair/complete/failed `contracts.py:30-33`), `render_status`
   (verified/issues/stale/unknown/not_applicable `contracts.py:87-91`), `final_map_status`
   (verified/verified_with_degradation/failed/unknown `contracts.py:97-100`), and single-word
   `product_verdict` READY/READY_WITH_WARNINGS/NEEDS_REPAIR/BLOCKED_BY_DATA/BLOCKED_BY_METHOD
   (`derive_product_verdict`, `contracts.py:211-280`) with 7-dimension completion contract
   (`evaluate_completion_contract`, `contracts.py:131-208`; vocab `contracts.py:125-128` =
   workflow_schema.COMPLETION_DIMENSIONS `app/services/gis_harness/workflow_schema.py:74-81`).
   Persisted as `gis_chapter["map_product"]` (`pipeline.py:351-399,592-601`), disclosed via
   `finalization_sse_payload` (`pipeline.py:667-695`) and `task_complete` turn stats
   (`app/agent_pi_bridge.py:2157-2183`); `read_stored_map_product` fallback
   (`pipeline.py:640-664`).

### Checks that exist today (validator inventory)

| Check | Where |
|---|---|
| DAG terminal gate (needs_execution / execution_blocked) | `completion/validators/execution.py:13-92` |
| Artifact bound ref alive / expired / empty result (capability-aware ref policy) | `completion/validators/artifacts.py:14-81,86-117` |
| Result layer present in MapSpec (planned vs spec, roles primary/secondary/result) | `completion/validators/layers.py:24-77` |
| Layer source registered in MapSpec sources | `layers.py:73-82` |
| Source ref alive (descriptor probe incl. disk raster `ref:raster/*`) | `layers.py:83-111`; inputs `completion/inputs.py:52-143` |
| Desired visibility / user-wins hidden layer | `layers.py:112-140` (`_layer_declared_visible` :19-21) |
| No-visible-data-layer fallback assertion | `layers.py:141-162` |
| Required component slot present/enabled (family: legend/categorical_legend/continuous_colorbar etc.) + singleton dup + orphan binding | `completion/validators/components.py:35-115`; slot sourcing `inputs.py:148-171` (fallback title+scale_bar :171) |
| Floating-component overlap layout conflict | `completion/validators/layout.py:9-64` |
| Semantic legend type↔layer match (legend_spec kind → component) | `completion/validators/semantics.py:21-26,86-109` |
| Required-legend thematic-layer coverage (facet contract `legend_required`) | `semantics.py:111-141` |
| Report-product title presence | `semantics.py:143-158` |
| CRS WGS84 contract on artifact records | `semantics.py:160-187`, `_normalize_crs_for_wgs84` :29-40 |
| Viewport: result bbox union derived; no-bbox warning | `completion/validators/viewport_export.py:7-48`; `pipeline.py:187-202` (F_VIEWPORT_NO_BBOX `contracts.py:52`) |
| Export parity (component→exporter support matrix, static) | `viewport_export.py:51-83` |
| **Render-level** (P9): observation missing/pre-revision → `render_unverified`; stale revision → `render_revision_stale`; per result layer: not mounted / 0 live layers / mounted-not-visible → `render_layer_missing`; source not converged → `render_source_missing`; required slot not observed in chrome → `render_component_missing`; bounded runtime errors → `render_error` | `render_observation.py:139-288` (unknown :167-175, stale :177-198, per-layer :203-247, slots :249-271, errors :273-283) |
| V3: layer order (result above context), extent intersection (needs observation `viewport.bbox`), stale overlay (dead/superseded ref on non-planned layer) | `map_verification.py:85-118,121-138,141-210` |
| Verdict/product verdict/7-dim contract | `contracts.py:131-280` |

---

## (b) Wave-7 verification checklist

| Wave-7 item | Status | Existing check (file:line) / gap |
|---|---|---|
| Expected layers present/visible (desired) | PRESENT | `validators/layers.py:24-77` (presence), `:112-140` (desired visibility) |
| Expected layers present/visible (observed) | PRESENT | `render_observation.py:203-239` (mounted + runtime_layer_count>0 + visible==spec expectation) |
| Hidden state respected (user-wins) | PRESENT | desired: `layers.py:114-130` (owner==user → warning, no repair); runtime: `runtime_repair.py:198-204` (user_owned no-op); repair anti-undo `repairs.py:60-67,145-163` |
| Data refs alive | PRESENT | `artifacts.py:44-70`; MapSpec-source refs `layers.py:83-111`; 3-state probe (ok/missing/unknown) `inputs.py:96-131` |
| Map source valid | PRESENT (desired + observed) | desired: `layers.py:73-82`; observed: `render_observation.py:240-247` (`source_converged`), frontend `runtime-evidence.ts:165` liveSource |
| Map model compatible | **PARTIAL / composition-time only** | `component_resolver.py:77-127` + `template_catalog.py:21,89` (`compatible_map_models`) filter slot/template selection at compose time; **no completion-time check** that composed spec still matches the planned MapModel (grep: `compatible_map_models` absent from `completion/`) |
| Required legend/colorbar present | PRESENT | slot-level `components.py:56-87` (legend family via `inputs.py:148-171`); semantic-level `semantics.py:111-141` (`legend_required` from `product_facets.py:49,120`); render-level `render_observation.py:249-271` |
| Chart/component presence | **PARTIAL** | generic component slots yes (above). `chart_required` (`product_facets.py:48,123,140`) is consumed only by ProductGraph pending-node synthesis (`product_graph.py:327,648,664`) — **no validator/finding in the completion pipeline asserts an enabled/observed chart_panel when chart_required** (only default id `contracts.py:303`) |
| Extent sanity | PARTIAL | `F_EXTENT_MISMATCH` `map_verification.py:121-138` requires observation `viewport.bbox` (zoom form NOT converted server-side, `map_verification.py:64-82`); no-bbox → `F_VIEWPORT_NO_BBOX` `pipeline.py:193-200`; camera mismatch handled frontend-side only (doc `cartographic-closed-loop.md:193-197`) |
| No all-transparent layer (blank canvas) | **MISSING in live loop** | only headless heuristic: `frontend/lib/mapspec-compiler/runtime-validate.ts` (`isBlank`/`analyseCanvas` canvas-analysis; screenshot `:214`) via `webgis_runtime_validate` tool (`app/tools/cartography_tools.py:806-825`) and `app/tools/spatial_decision_tools.py:132-133`; labelled `heuristic_visual_proxies` (`app/services/runtime_validator.py:36-80`, doc :238-241); **no production observation measures opacity/blankness** (grep "transparent"/"blank" in gis_harness: none) |
| No stale artifact layer | PRESENT | dead/superseded overlay `map_verification.py:141-210`; expired bound ref `artifacts.py:58-70`; stale observation `render_observation.py:187-198` |
| Stale-layer suppression (spec revision) | PRESENT | server revision stamping `chat.py:1537-1543`; stale observation → no repair `runtime_repair.py:150-151,317-320` |
| Cartography completion (methodology of completion) | PRESENT | 7-dim `contracts.py:131-208` (`cartography_ok` :183-186, `observed_ok` :188); `final_map_status` aggregate `map_verification.py:232-255` |
| Methodology disclosure | PRESENT (obligation-driven) | plan-tier `planner.py:223,553,589,906`; stored `session_plan.py:528-543`; consumed `pipeline.py:598-600` → `contracts.py:188` (methodology_ok = obligations ⊆ disclosed), verdict demotion `contracts.py:249-253`; anti-claim corpus `app/evaluation/anti_claim.py:27-50` |
| Uncertainty disclosure | PRESENT (weaker) | `contracts.py:189-191` — only fails when an obligation of kind "uncertainty" has status "blocked"; no positive proof an uncertainty statement was rendered/disclosed in output text |
| No false-complete under missing evidence | PRESENT | unknown/stale degrade not complete (`render_observation.py:167-198`, `map_verification.py:249-255`); stale观察 final 仍 complete 但投影披露 `render:stale` (`contracts.py:390-393`); non-READY final_gate force re-verify at turn end (`pipeline.py:307-328`, `agent_pi_bridge.py:2159`) |

---

## (c) Structural vs pixel observation status

- **Structural observation is the only live pipeline.** `RenderObservation` = IDs/booleans/
  small metadata: per-layer `{id, runtime_store_id, runtime_layer_count, visible,
  source_converged}` (via `map.getLayer`/`map.getSource`, `runtime-evidence.ts:45,155,165`),
  chrome components `{id,type,enabled,mounted,anchor,floating,collapsed,rect}` — `mounted`
  is a *projection of the committed spec through the same resolver MapSpecChrome uses*
  (`render-observation.ts:168-219`), i.e. component "observation" is still derived, not DOM-
  verified; bounded runtime-error ring; `map_idle` settle boolean; viewport dict
  (`render-observation.ts:71-95,249-330`; DTO `chat.py:1466-1499`). Contract: never GeoJSON /
  pixel data (`render-observation.ts:13-25`).
- **Pixel/screenshot vision exists only as an out-of-band headless validator**:
  `frontend/lib/mapspec-compiler/runtime-validate.ts` (Playwright Chromium, canvas
  `screenshot()` :214, pixel probes :19-21, blank detection via `canvas-analysis`), driven by
  `app/services/runtime_validator.py:127+` and exposed only as the agent tool
  `webgis_runtime_validate` (`cartography_tools.py:806-825`) / spatial-decision internal call
  (`spatial_decision_tools.py:132-133`). Its luminance/blank/control-layout scores are
  explicitly `heuristic_visual_proxies` that "cannot produce L4/L5 PASS by themselves"
  (`docs/cartographic-closed-loop.md:238-241`); harness treats a matching headless result as
  record-only visual evidence (`pi_agent_harness.py:1088-1105`). **No live screenshot/vision
  pipeline feeds the finalizer or the verdict; Wave-7 "pixel-level vision unless a real
  screenshot pipeline exists" is currently unmet in the automatic loop.**

## (d) Repair loop status

Three bounded, non-competing repair channels (plus a viewport repair owned by the frontend):

1. **Desired-state repairs** (finalizer): add_component / enable_component / show_layer
   (`completion/repairs.py:21-137`; codes `contracts.py:284-286`); user-wins guards:
   one-shot repair memory `repairs.py:60-67` (memory ≤32 `contracts.py:21`), user-removed
   components never resurrected `repairs.py:53,145-163`; ≤2 passes, fatal findings short-
   circuit (`pipeline.py:126-155`); post-repair re-validation `pipeline.py:159-160,153`;
   repaired spec + revision pushed to frontend via SSE payload (`pipeline.py:667-695`).
2. **Runtime reassert repairs** (ADR-0088, `gis_harness/runtime_repair.py`): triggered only
   when finalizer reports `render_status=="issues"` and no desired-state repairs were applied
   (`chat.py:1629-1659`). Deterministic classifier `classify_runtime_repairs`
   (`runtime_repair.py:131-253`): reassert_spec_layer / restore_expected_visibility /
   reassert_component (CAS `expected_revision` :392-447); dead-ref → execution_debt (never
   remount, :213-219); stale observation → empty plan (:150-151,317-320). Budget:
   `MAX_RUNTIME_REPAIR_PASSES=2` with fingerprint-generation ledger (:57-60,343-367,455-498);
   applied → spec snapshot returned → `commitMapSpecDocument` → reconcile → **automatic
   re-observe closes the loop** (`use-cartographic-observation.ts:286-294`).
3. **AUTO_SAFE style repairs** (`app/lib/cartography/quality_loop.py:289-460,531`): desired-
   state review repairs (opacity normalize / restore explicit expected_visible / regenerate
   paint from authoritative legend_spec) + event-driven `cartographic_runtime_repair` map
   action with ACK + newer-observation-before-re-review requirement
   (`cartography_runtime.py:632-813`; `MAX_RUNTIME_REPAIR_ITERATIONS` enforcement :741-748;
   duplicate/cancelled/superseded handling :716-740); frontend dispatch budget 8/session,
   dedup ring 16 (`use-cartographic-observation.ts:22-24,71-81,263-281`).

Re-observe loop: yes — every accepted observation re-runs finalizer (`chat.py:1609-1611`)
and the finalizer dedup gate keys on `(checked_revision, render_observation_seq,
rows_fingerprint)` (`pipeline.py:495-499,323-328`); turn-end `final_gate=True` forces one
more diagnose→repair→re-observe→re-verify pass for non-READY sessions
(`pipeline.py:307-328`; `agent_pi_bridge.py:2159-2161`). All loops bounded (2/2/2 + 8).

## (e) Recommended additive integration seam for the Wave-7 closed loop

Keep the single-truth discipline (MapSpec = desired; observation = observation; findings =
disclosure; repairs via existing mutation channels). Concretely:

1. **New validators are pure functions in `completion/validators/`** (register in
   `_validate_all`, `completion/pipeline.py:47-69`) with new finding codes added ONLY to
   `completion/contracts.py:35-84` (single vocab table). Candidates:
   - `chart_required` → `F_CHART_MISSING`: consume `inputs.facet_contract.chart_required`
     (already gathered, `inputs.py:174-180`) and assert an enabled `chart_panel` (desired)
     + observed mounted component (render, `render_observation.py:249-271` already receives
     `components[]` with `mounted`).
   - Map-model compatibility audit: recheck composed spec layers against
     `compatible_map_models` (`component_resolver.py:102-118`) — desired-state-only, warning.
2. **Pixel-level vision**: do NOT inline pixels into RenderObservation (bounded-by-contract,
   `chat.py:1484-1499` 256KB cap). Instead add a **vision finding consumer**: run the
   existing headless seam (`runtime_validator.validate_runtime`) as an optional finalizer
   step keyed on `render_status in {issues, stale}` or `final_gate=True`, and inject its
   `canvas` stats (luminance/dominantRatio/blank) as `evidence_class: visual` findings
   (pattern already reserved: `pi_agent_harness.py:1088-1105` visual_evidence; doc
   :250-252). This covers "no all-transparent layer" without a second truth source.
3. **Extent sanity hardening**: extend `_observation_viewport_bbox`
   (`map_verification.py:64-82`) to accept `center+zoom` → bbox via a single server-side
   projection helper (currently deferred to frontend truth), or have the frontend include
   `viewport.bbox` unconditionally in `collectRenderObservation` (cheapest, additive).
4. **Verdict feed**: map observation already feeds completion, but task completion is still
   advisory (`task_complete` turn_stats `agent_pi_bridge.py:2157-2183`). Gate hard-completion
   on `product_verdict ∈ {READY, READY_WITH_WARNINGS}` + `final_map_status ∈ {verified,
   verified_with_degradation}` at the `final_gate` site (`pipeline.py:434-499`) — surface
   `BLOCKED_*` as non-completion rather than disclosure-only.
5. **Uncertainty disclosure proof**: `uncertainty_ok` (`contracts.py:189-191`) currently only
   detects blocked obligations; require an actual disclosed uncertainty artifact/section key
   on the chapter (mirror of `methodology_warnings`, `session_plan.py:541-543`) before the
   dimension passes.

### Biggest gaps (summary)

- No pixel/screenshot evidence in the automatic observe→verify loop (headless heuristic
  validator is tool-invoked only); "no all-transparent layer" unverifiable live.
- Component observation is a spec projection (`mounted` via shared resolver), not DOM/paint
  verification — legend/colorbar "present" means chrome would mount it, not that pixels exist.
- `chart_required` never checked by the completion pipeline; map-model compatibility checked
  only at composition time.
- Extent check is best-effort (bbox-form viewport only; zoom-form ignored server-side).
- Uncertainty disclosure dimension is obligation-status-based, not output-evidence-based.
