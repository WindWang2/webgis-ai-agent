# Final diagnostic tail closure — 2026-11-06

This ledger covers the delegated failures from unit-tail176/181/183/188 and integration-tail180/189. All producer changes retain the existing ownership, scientific sample-size and fail-closed evidence constraints. No commits, baseline edits or relaxed performance limits.

| Scope | Disposition and concrete evidence |
| --- | --- |
| scene_tools; behavioral execution / data discovery / workspace / webgis | Correct the fixtures to inject session identity through the server's dispatch argument. A model-provided JSON session ID is not authority. The execution schema test verifies this field stays hidden, and the actual foreign authoritative-context denial remains exercised. Five complete files:30 passed. |
| catalog discovery | Supply the actual test registry through the explicit authoritative catalog seam; runtime no longer invents a private registry. Candidate and lookup behavior remain checked against registered executable tools. |
| tool category rationalization | Real metadata omission fixed: webgis_product_edit and webgis_map_design_select are explicitly planning tools. The seven-tool planning surface and complete classification tests remain intact. |
| compiler tile bounds parity | Python's extent fixture now includes the same visible vector extent layer as its TypeScript twin. An unused GeoJSON source must not silently affect rendered bounds. The exact existing numeric parity assertion is unchanged. |
| model-library dead fallback | The removed NEEDS_ADMIN_UNITS fallback is explicitly absent: missing administrative units must not quietly change a choropleth to point distribution. |
| BE audit and template-delete matrix | Seed actual live User / Organization identities, retaining real auth dependencies. Owner succeeds; foreign tenant sees404; same-org admin succeeds; anonymous, built-in and missing cases retain their correct denial semantics. No stale JWT-only privilege is restored. |
| collaboration periodic authentication | Raw SQL fixtures now seed the same hash an ORM insert/migration stores, preserving typed-verifier reads. Correct raw-token connection and idle rotation revocation both pass. |
| recovery write failure | Inject failure at the actual atomic state-update seam rather than the retired set_map_state path. Recovery still fails closed. |
| mission lifecycle | Construct MissionStore normally so its injected/default clock exists; empty-owner fencing still rejects before mutation. |
| decision scenarios | Population growth has no shipped applicable domain rule. Its parameter cannot self-prove applicability of unrelated rules; the result truthfully reports no applied rules. Other scenario-specific rule assertions remain. |
| scientific quality corpus | Four too_few_samples contracts (three interpolation families and landcover) now expect degraded. Production sample minima remain unchanged. The entire scenario corpus passes:12 tests. |
| centroid documentation check | Replaced a source-word check with an actual asymmetric-hole centroid calculation, asserting the independent area-subtraction result. |
| stale send closure | Static checks follow the current loadingGuardRef/sendingRef names. Actual hook invariant/adversarial/base suites:49 passed; no frontend producer change. |
| realtime required fields | The AST projection now recognizes direct isinstance/finite guards and excludes non-None-default input fields. It exposes both layer_id and visible for layer_toggled while leaving defaulted viewport inputs optional. A behavior test proves missing/wrong visibility cannot write, but explicit false can. |
| offline execution catalog docs | The offline generator explicitly initializes its own registry and compiles it without polluting the runtime singleton; generated tools remain populated instead of accepting a zero-tool artifact after removal of the runtime fallback. |
| config / API / response fields / realtime / generated graph | Regenerated through the owning scripts and snapshot update interfaces. No manual changes to derived payloads or blanket baseline resets. The new response-field entry is the intended report-share DELETE endpoint. |

## Real workflow continuation correction

The prior HTTP workflow API supplied no session to execution. Completed-step logical results were therefore never persisted; safe reconstruction correctly rejected every partial-run resume. Restoring the old ref-only assumption would have hidden this producer defect.

WorkflowRun now persists a nullable server-owned session_id via additive migration0103, registered after0102 in migrations/.alloc.json. HTTP fresh execution/replay creates a real user-bound Conversation through AsyncHistoryService, retaining the existing admission, history cap and eviction protocol. Session identity is never accepted from model arguments or a new client-controlled DTO field. Execution stores this scope before running steps. HTTP resume/rerun-from-step recovers the prior scope and rechecks the live actor and Conversation owner; a different user, even within the same project organization, cannot claim that private evidence. Old NULL scopes truthfully reject continuation with409.

The real HTTP partial-failure regression verifies the persisted Conversation owner, readable physical result, one completed step, same-org other-user404 and successful owner resume dispatching only the remaining tool. The resumed run retains the same evidence scope; clearing the legacy association produces409. Migration upgrade/downgrade separately preserves the old failed row and never invents a scope.

Validation:

- `/tmp/frontend-final-tail-frozen-all.log`: **149 passed**, all21 complete delegated test files including related workflow/API Low regressions.
- `/tmp/frontend-workflow-http-session3.log`: **42 passed**, workflow API / transactions / concurrency / migration and API Low.
- `/tmp/frontend-final-science-corpus1.log`: **12 passed**, complete quality scenario corpus.
- `/tmp/frontend-staleclosure-behavior-final.log`: **49 passed**, actual frontend hook behavior.
- `/tmp/frontend-final-derived-check1.log`: **47 passed**, all initial derived contract/config/catalog/API files. Final graph/manifest validation is recorded below after generation finishes.
- `/tmp/frontend-tail-new-source-types2.log`: new workflow scope helper mypy clean. Source/default Ruff and the five requested generic-library rules passed. All73 migrations pass allocation, uniqueness and single-head validation.
- `/tmp/frontend-tail-debt-final2.log`: debt gate OK, no baseline changes; `git diff --check` clean.

Final generated-artifact closure: `/tmp/frontend-final-derived-verified2.log` passed62 tests; its sole failure was the graph's resource/cancellation input fingerprint changing while root applied the final project-tab button fix. After rerunning the actual resource generator and updating the graph, `/tmp/frontend-generated-graph-final3.log` passed all5 graph tests, and `scripts/check_generated_staleness.py` reported all inputs current. The eight owning generators and aggregate quality-report generator were run before this final update (`/tmp/frontend-derived-generators-final.log`); no generated artifact was blessed by an input-hash update alone.
