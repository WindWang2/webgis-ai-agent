# Export / offline QA issue verification — 2026-11-06

Scope: #1825 all 7 findings; #1864 S5f Low findings; #1822 all 4 findings; #1821 S5d-003. No inferred aggregate closure.

| Issue / finding | Status | Verified source and resulting behavior |
|---|---|---|
| #1825 S5f-001 | fixed | visual_judge/vlm_provider.py strict schema requires every field, optional values nullable; provider-unsupported bounds remain local contract checks. All golden samples validate. |
| #1825 S5f-002 | fixed | critic_engine.py reuses engines through bounded 8-config LRU; memo survives repeated build_critic_engine calls from evaluator/provider. |
| #1825 S5f-003 | fixed | snapshot_extractor.py checks header dimensions/pixel budget before verify/decode/grayscale; decompression-bomb errors become oversized SnapshotError. |
| #1825 S5f-004 | fixed | lab/fakes.py and replay/receipt.py fallback selects first remaining receipt, avoiding cursor applied to shrinking candidates. Dedicated 3-receipt regression passes. |
| #1825 S5f-005 | fixed | lab/settlement.py resets chain/finalization/projection observations before each check. |
| #1825 S5f-006 | fixed | replay/faults.py generic not_evaluated goal is excluded from degradation evidence; explicit failed gate/check/outcome/goal required. |
| #1825 S5f-007 | fixed | Old scale_matrix location no longer contains parameter variants. Actual replay/scenarios.py generator now varies zoom and circle radius/line width/fill opacity, preserving independent paint dictionaries. Multi-turn count documented as 9×4=36; serialized corpus regenerated. |
| #1864 S5f-008 | fixed | settlement.py duplicate check drives real ToolDispatchService through receipt sandbox and asserts production repeated status, rather than injected duplicate flag. |
| #1864 S5f-009 | fixed | lab/adapters.py component presence checks layout.components alongside legacy shapes. |
| #1864 S5f-010 | fixed | VisualFixtureAdapter reports not_evaluated for scenario compliance; fixed golden image only demonstrates judge wiring. Lab baseline regenerated after verifying resulting report. |
| #1864 S5f-011 | fixed | evidence.py serialized checks and visual_evidence retain latest N entries. |
| #1864 S5f-012 | fixed | cartography_feedback.py warning desired status is an evaluated passing semantic review with findings retained. |
| #1864 S5f-013 | fixed | cartography_feedback.py reads authoritative map_product_completeness. |
| #1864 S5f-014 | fixed | cartography_feedback.py is_compiled fallback searches newest result first. |
| #1864 S5f-015 | fixed | template_codegen_evaluator.py reads layout.composition.template_id. |
| #1864 S5f-016 | fixed | vlm_provider.py requires dedicated key for different configured endpoint; legacy evaluator resolves through same configuration function. Dedicated key-isolation test passes. |
| #1864 S5f-017 | fixed | Both legacy visual_evaluator memo and CriticMemoCache skip transient provider/not-evaluated failures; retry regression updated to expect second provider call. |
| #1864 S5f-018 | fixed | visual_evaluator.py resolves candidate screenshot path and requires containment under authoritative BASE_STORAGE_DIR before reading. |
| #1864 S5f-019 | fixed | replay/recorder.py global 7-day TTL, 256-session quota, 128MiB quota supplement per-session ring; user/final text passes secret-string sanitization at write boundary. |
| #1864 S5f-020 | fixed | replay/schema.py sanitizes outcome/warnings/reason_codes and excludes forbidden prompt/payload keys from decisions. |
| #1864 S5f-021 | fixed | OfflineReplayer deep-copies scenario before placeholder/fault-state rewriting. |
| #1864 S5f-022 | fixed | Text comparisons consume replay output_text, never user_input compared against itself. Offline missing output remains absent in reporting. |
| #1864 S5f-023 | fixed | Deterministic replay judge moved into app/lib/harness/replay/offline_judge.py; production import no longer depends on tests package. |
| #1864 S5f-024 | fixed | receipt memory store emits ref:type-t4-id consistent with ref_resolver regex; updated contract test passes. |
| #1864 S5f-025 | fixed | replay_receipt_level serializes entire env/singleton sandbox with cross-thread lock acquired cooperatively by tasks, released in finally. |
| #1864 S5f-026 | fixed | DeterministicClock uses virtual elapsed time and explicit nonnegative advance, never real monotonic. Dedicated regression passes. |
| #1864 S5f-027 | fixed | shrink.py fault identity uses actual type field. |
| #1864 S5f-028 | fixed | shrink.py permits final allowed round; marks truncation only when budget prevents further required search. |
| #1864 S5f-029 | fixed | replayer_oracle freezes failing contract path/class signature and rejects candidates that merely reproduce another failure. |
| #1864 S5f-030 | fixed | triage.py not_run has distinct category, separate from artifact drift. |
| #1864 S5f-031 | fixed | drift.py documentation matches explicit decision_id pairing plus added/removed identities; no unsupported kind-pairing promise. |
| #1864 S5f-032 | fixed | scenarios.py JSON includes error_code, decisions, registry digest, registry declarations, receipt_backed and expectation provenance/calibration metadata. |
| #1864 S5f-033 | fixed | Removed unused WRONG_SESSION status; isolated store lookup cannot establish another session's ownership and reports NOT_FOUND. |
| #1864 S5f-034 | fixed | lab/runner.py goal status reads latest contribution. |
| #1864 S5f-035 | fixed | FaultStep.as_replay_fault preserves target_call and params. |
| #1864 S5f-036 | fixed | evaluator.py unknown threshold produces explicit ValueError naming metric. |
| #1864 S5f-037 | fixed | Nullable optional bbox in strict schema agrees with fake_vlm canned outputs; every golden sample schema-validated. |
| #1864 S5f-038 | fixed | roundtrip calibration preserves recorded expectations by default and reports residual drift; explicit allow_prune required to alter baseline. New regression demonstrates impossible recorded expectation remains red. |
| #1822 S5b-002 | fixed | svg_charts.py uses signed zero-containing domains; stacked positives/negatives separate; bars include title. New bar/line domain tests pass. |
| #1822 S5b-003 | fixed | label_engine.py excludes polygon centroid outside polygon, uses interior representative point. Concave U regression passes. |
| #1822 S5b-004 | fixed | label_engine.py callouts search multiple directions/radii for free label box; impossible placement suppresses instead of overlapping. |
| #1822 S5b-005 | fixed | pdf_renderer.py passes resolved CJK font properties to legend and scale-bar text. |
| #1821 S5d-003 | fixed | lib/storymap/export_packager.py recursively scrubs string leaves and sensitive URL parameters inside prose. Dedicated secret URL/bearer test passes. |

Validation: 390 tests passed across replay/lab/visual runtime/cartography/PDF suites (66.08s), plus 24 export/new-boundary tests and 133 focused rerun tests. Regenerated 140 scenario fixtures and lab baseline after intentional contract changes. Generated-fixture parity and lab baseline35passed; final new-boundary18tests passed in two focused runs (7 harness +11 export). Full harness integration3271passed4skipped; final PDF/SVG/story/export44passed and additional boundary12passed.

## #1864 S5d-007 — storymap longitude union (2026-11-06 addendum)

**fixed** for the delegated storymap branch. `app/lib/storymap/story_compiler.py` obtains coordinate-derived extents from `minimal_longitude_interval` (largest empty longitude gap). Box union preserves the interior width of every authored interval, shifts all boxes into each candidate common longitude domain and selects the minimal covering interval. Wrapped [170,-170] and ordinary negative-longitude neighbors therefore union near 180°, while an authored 300° wide extent retains its wide coverage. Core/interpolation branches remain data-owned and use the shared longitude primitive.

`/tmp/storymap-longitude-final.log`: **106 passed**, including new dateline MultiPoint, mixed wrapped/ordinary box, and broad-box cases; `/tmp/storymap-longitude-lint.log`: clean.
