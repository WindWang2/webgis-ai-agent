# Root unmapped-finding reconciliation — 2026-11-06

Exact IDs from the full152-issue closure audit. This table does not close aggregates or replace final integrated gates.

| Issue | Finding | Status | Evidence |
|---|---|---|---|
| #1819 | S4c-003 | fixed | snapshot lists newest creation times first before the 50-item cap; durable-manifest scan prefers newest files; actual newest-order regression |
| #1863 | S4c-005 | fixed | clone/delete require nonempty matching creator and explicit caller; malformed/legacy ownerless records cannot be mutated; four negative owner combinations + positive service regressions |
| #1863 | S4c-016 | fixed | verify/restore load and parse manifests through asyncio.to_thread; public synchronous loader remains for synchronous callers |
| #1863 | S4e-017 | fixed | ActionPlanDiff.semantic_changed records all other action fields, including dependency/failure/precondition/compensation/input/output/resource semantics; empty/describe now include them |
| #1863 | S4f-009 | fixed | coordinate patterns run before address/name matching; English complete tokens and Chinese semantic suffixes avoid false substring matches; first equal-strength mapping retained; location_x/LOCATION_Y/位置经度 and competing-column behavior tested |
| #1863 | S4f-010 | fixed | assess at most32 candidates with concurrency4 before selecting top3; isolate individual failure and disclose failed/truncated counts; all assessments failing returns failure; deterministic concurrent test finds top-ranked candidates beyond the old first3 |
| #1863 | S4f-011 | fixed | parse failure message separately reports unresolved references and decoding/parser errors; present corrupt payload no longer claims a store handoff failure |
| #1863 | S4f-012 | fixed | misaligned result counts fail the partition without crediting ambiguous coordinates or inventing timeout; progress clamped0–100; stored rows processed even when declared row_count is0; actual result/status/count regressions |
| #1863 | S4f-013 | fixed | reuse existing deterministic ASCII/CJK-bigram tokenizer with bounded text, stopword and short-token filtering for intent/title/description/tags/fields; existing-layer skip requires most meaningful layer terms, not a shared city alone; Chinese positive/negative and metadata-case tests |
| #1863 | S4j-014 | fixed | gis_situation dataset ref/CRS, active roles and spatial-event fields receive bounded XML escaping/fences |
| #1863 | S4j-015 | fixed | offline evaluation moved to scripts/gis_memory_eval.py; same-org other-user and hot-recall-after-retirement tests use real DB/index; hot hits revalidate durable active/expiry/scope authority before projection |
| #1863 | S4j-016 | fixed | revision comparison is componentwise; equal transport counters retain idempotence only for equal facts, otherwise refresh the saved situation |
| #1863 | S4m-009 | fixed | revalidation peeks only current session+tenant turn context, never scans another session claim store; negative same-tenant test |
| #1863 | S4m-010 | fixed | basis revision0 participates in stale comparison for claims and decisions; actual basis drift regression |
| #1863 | S4m-011 | fixed | facts-only memory graphs project their bounded facts instead of returning empty |
| #1863 | S4m-012 | fixed | reserve complete anchor+finding token/item group before any insertion, leaving budget for later findings if a conclusion does not fit |
| #1863 | S4m-013 | not_a_bug | original gives no file/symbol for duplicate constants; current gis_context Ruff F401 clean, card imports shared projection budget, distinct bounds govern different output surfaces; no invented removal |
| #1863 | S4m-014 | fixed | escaped card truncation backs off partial entity and reserves a complete omission notice; truncation receipt stays honest |
| #1864 | S5-006 | not_a_bug | all four subprocess sites use fixed internal argv and no shell; original explicitly states no injection; shutil.which does not add a trusted PATH boundary |
| #1864 | S5-003 | fixed | all app/lib S110/S112 findings inspected by domain owners; preserved optional fallbacks now emit bounded diagnostics, business failures remain explicit; exact paths in generic-library-low-review and generic-data-lib-review; full app/lib five-rule check passes |
| #1864 | S5-004 | fixed | numerical/index correspondence zips use strict=True; intentional adjacent-pair iteration explicitly strict=False with reason; per-file ledgers and numerical behavior suites verify the distinction; full app/lib B905 check passes |
| #1864 | S5-005 | fixed | unused loop bindings renamed locally and no-op export expression replaced by explicit compatibility reexport; full app/lib B007/B018 check passes, without suppressing rules or raising baseline |
| #1864 | S5-025 | fixed | removed duplicate legend-vs-paint field-mismatch finding, leaving one counted error |
| #1864 | S5-026 | fixed | stop-domain min/max accepts only finite numeric stops; mixed string/number input still yields a report |
| #1864 | S5-028 | fixed | shared pure suspicious-result predicate moved to app/lib/tool_result_errors.py; harness accepts injection and rejects credit on predicate failure; cartographic import-time thresholds read bounded environment values without app.core dependency |
| #1864 | S5b-006 | fixed | PDF legend color validated with matplotlib is_color_like; invalid value uses neutral fallback and still exports |
| #1864 | S5b-007 | already_fixed | classify_values filters np.isfinite before all methods and returns empty for no finite data, same fix as1967 S14-006 |
| #1864 | S5b-008 | fixed | 2x2 bivariate palette selects four corners0/2/6/8 of the3x3 palette, preserving low/high endpoints on both axes |
| #1864 | S5b-009 | fixed | dot-density metadata reports under_placed and exact placement_shortfall when rejection sampling exhausts attempts |
| #1864 | S5d-008 | fixed | camera uses both longitude and latitude/aspect constraints; allows zoom0 for large extents and syncs synthesized center+zoom bbox to the same16:9 convention |
| #1952 | S58-L04 | fixed | nginx strips queries; Uvicorn HTTP and WS transport filters redact query/fragment before access formatting, idempotently installed even with existing root handlers |
| #1953 | S100-013 | fixed | legacy feature anchors without parent are UNVERIFIED; inlineData source features participate in evaluation; ingress already rejects new missing-parent anchors |
| #1953 | S100-014 | fixed | submit cannot replace stored server base revision; differing client base must use explicit rebase target validation |
| #1953 | S100-015 | not_a_bug | default explicitly documents low-risk human self-approval as confirmation; high-risk and agent authors retain distinct/human restrictions; no claim of universal four-eyes policy |
| #1953 | S100-018 | fixed | report atomic write already offloaded by prior runtime work; evidence helper doc now accurately names screenshot+trace checked before report persistence |

| #1863 | S4c-016 | fixed | Snapshot verify/restore loads the manifest off the event loop once and verifies the exact object consumed by register; `test_restore_loads_one_manifest_off_event_loop` checks actual load count/thread plus restored ledger records. |

Validation: transport29 passed; project-tab17 passed; snapshot/carto focused73 passed. Combined root regression519 passed (`.planning/2026-11-05-all-issues-deep-review/root-unmapped-broad3.log`); supplemental STAC/config/card58 passed, relocated memory eval7 passed. Final complete integrated gates and derived artifact verification remain pending integration. Action IR golden addition was one `semantic_changed: {}` field; later compiler digest changes belong to ongoing core fixes and must be generated after source freeze. No threshold was loosened to conceal a failing check: the camera lower bound is a product acceptance change enabling whole-region framing, and revision/golden expectations track corrected source contracts.
