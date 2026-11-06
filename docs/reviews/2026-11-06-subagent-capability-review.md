# Subagent capability verification — 2026-11-06

Exact finding: #1862 S3-024. Read original issue snapshot before changes. No commits/GitHub writes. Core explicitly authorized only the delegation.py capability-count constant12→256 and extraction of the existing subagent role/domain/tools selection seam; its other lifecycle logic remains unchanged. Frontend owns harness integration.

| Subfinding | State | Evidence |
|---|---|---|
| Outcome mapper never fills capabilities_used | fixed | Parent-owned per-run CapabilityRecordingRegistry intercepts the actual registry.dispatch path; descriptors are read at dispatch time. outcome_from_subagent_result accepts explicit parent evidence and ignores result/summary/lineage self-reports. Failed/denied/exceptional runs retain observed evidence. |
| Production allowed_capabilities only configured in tests | fixed / verified | run_single_delegation prepares the parent policy and fills request.allowed_capabilities from the actual role-filtered selected-schema descriptor union. The real child dispatcher uses the identical shared resolve_subagent_surface seam. More than256 capabilities reject explicitly; no truncation. Core authorized bounded expansion from12 to256 so ordinary multi-capability role surfaces are representable. |
| Role/extra_tools authority | fixed | Core confirmed allowed_domains is a hard ceiling. Final selected schemas must intersect descriptor.domains with role.allowed_domains, including extra_tools. Adhoc extra behavior unchanged; tier3/readonly/blacklist/zero-tool/maxrounds rules retain their prior seams. |
| Parent explicit capability grant | fixed | Optional parent explicit grants can only narrow the trusted policy; dispatch denies foreign or unprovable capabilities before effects. Any parent-recorded denial forces failed receipt even if child returns success. Default undeclared capability metadata retains the existing parent tool allowlist authority and explicitly reports incomplete evidence; empty capabilities never claim arbitrary capability approval. |
| Parallel/shared registry mutation | fixed | Each runner owns its proxy and observation sets; parent registry is never monkeypatched. Parent names/grants are frozen; changed descriptor capability IDs are checked again at actual dispatch. Source callbacks deny oversized IDs before effects. |
| Capability ID/report bounds | fixed / verified | Counts bounded256 and individual IDs1..128 chars, including parent/plugin descriptor inputs; reject instead of clipping. tools/missing/denial audit previews bounded; typed outcome.capabilities_used retains the actual authoritative set. |
| FakeSubagentRunner in production module | already_fixed | Implementation resides in agent_swarm/testing.py. delegation.py only retains lazy compatibility __getattr__ for historical tests; no default production instantiation. |
| ensure_future not retained | already_fixed | delegation.py now stores runner_task, awaits it, cancels on timeout/cancellation, and bounded-reaps the cancelled runner; the reported dangling-task premise no longer exists. |

Checkpoint `/tmp/data-capability87.log`:128passed existing delegation/role/budget/accounting/team tests. `/tmp/data-capability88.log`:**134passed**, including actual parent dispatch, spoofed child lineage, parent denied effect, separate runner observation, role domain/extra narrowing,13-capacity grant and explicit over-bound rejection. Fixtures migrated from object()/nonexistent role stubs to a real empty registry and registered role rather than bypass policy validation. Two final capability-ID/mutated-descriptor regressions pending.

Files: `agent_swarm/capability_observation.py`, `agent_swarm/delegation_adapters.py`; subagent.py only the approved shared selection extraction; delegation.py only the approved bounded constant. Harness default production wiring owned by frontend uses the same Runner.prepare_policy; custom in-process injected dispatchers remain explicitly unobserved trusted test seams.

## Actual production registry inventory / bound choice

Independent `ToolRegistry()+init_tools()` inventory (`/tmp/data-capability-real-count92.log`) disproved the interim128 bound: planner132, spatial_scientist129 and geocompute129 would be rejected. Core authorized the final256 constant, keeping individual IDs≤128 chars and explicit257+ rejection. The policy never truncates a grant or falls back to empty on a usable parent surface. A new real-registry regression checks every production role's complete descriptor union equals its grant.

| Role | Selected tools | Distinct trusted capabilities |
|---|---:|---:|
| adhoc |37|16|
| data_researcher |56|18|
| gis_inspector |110|61|
| scientific_reviewer |15|13|
| cartography_reviewer |15|13|
| tool_result_verifier |15|13|
| cheap_summarizer |0|0|
| planner |245|132|
| corpus_worker |56|18|
| spatial_scientist |223|129|
| algorithm_reviewer |206|117|
| map_observer |44|18|
| result_verifier |118|62|
| doc_crosschecker |30|11|
| data_scout |56|18|
| geocompute |223|129|
| cartography_specialist |48|19|
| audit_judge |83|55|

`/tmp/data-capability89.log`:136passed with ID-length/mid-run descriptor-change regressions. `/tmp/data-capability-debt90.log`:debt ratchet passes, baseline unchanged. Final256 real-inventory regression and frontend harness integration proof pending.

## Final verification / no scoped pending items

- `/tmp/data-capability94.log`:**138 passed**, all new parent-dispatch/role/grant/metadata/adversarial tests plus original protocol/shared runner/subagent/roles/budget/accounting/team suites. Includes real production role inventory, count257 refusal, ID129 refusal, dispatch-time changed descriptor refusal, and failed parent preflight remaining failed after registry recovery (cannot queue with an empty declared grant then start a recovered policy).
- `/tmp/data-capability-final97.log`:**65 passed** after explicitly aligning denied outcome verdict/reasons with its failed status; parent denial cannot leave an “ok” receipt or a child success claim. Ruff passes.
- Frontend final fresh harness integration (`/tmp/harness-capability-final5.log`):**75 passed**, actual parent proxy dispatch, forged child lineage overwritten, custom injected in-process seam explicitly unobserved, overflow rejected before child and settled durably, all256 actual capability IDs retained in bounded persisted lineage.
- `/tmp/data-final-debt95.log`:debt ratchet passes (dict_str_any7081/7086; god_modules17/19; baseline unchanged). Owned diff whitespace checks pass.

S3-024 is fixed for both production gateway entry points; the two already-fixed historical premises remain recorded separately. No claims are made that unlabeled descriptor metadata proves a capability: actual tool allowlist remains authoritative and incomplete metadata is disclosed. All source changes remain uncommitted for root integration.
