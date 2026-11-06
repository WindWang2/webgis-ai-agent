# Service findings #1863 and compiler/review Medium — 2026-11-06

All46 actual delegated Low anchors are mapped below. Requested S4f-008 is absent from archived #1863 and is not counted as a repaired finding. The separately delegated8 Medium findings are mapped by their own original issue IDs. No commits or baseline changes.

Source is frozen for this agent after the final416-test bundle. Five Low plus one Medium have explicit final ownership in core, five Explorer Low in root; their specialist ledgers carry final tests.

| Finding | Disposition | Evidence / implementation |
|---|---|---|
| #1863 S4-010 | fixed | coordinator invokes only pinned local jiti (missing dependency yields CLI_UNAVAILABLE), never npx download. Async directory/report/style IO checks offloaded; actual missing-bin regression. |
| #1863 S4-012 | transferred_to_core | Initial source patch handed to core for independent validation/final necessary fixes and dedicated regression ledger. Source ownership frozen here; core final disposition is authoritative. |
| #1863 S4-040 | fixed | discard_checkpoint validates ID before destructive path. Coordinator returns structured errors for malformed stops/shapes. Spatial guard exception rejects mutation with stable unavailable code. Presentation batch skips recovery checkpoint. New traversal/shape/guard/batch regressions. |
| #1863 S4-041 | fixed | visual_healer text-ignore-placement=False with overlap=False; client/agent quality_gate_bypass cannot override server enforcement. Actual dirty-layer bypass denied; advisory server mode remains explicitly tested. |
| #1863 S4b-010 | already_fixed | Presence heartbeat Lua atomically merges only live existing member. Independent real fakeredis Lua test proves missing/expired heartbeat cannot recreate records. |
| #1863 S4b-011 | fixed | Explorer chat bridge classifies ownerless tasks using stored registration tuple owner, not evictable process LRU. Exact stream-selection regression. |
| #1863 S4b-012 | fixed | Composition project authorization set no longer arbitrarily truncates at64; target70 accessible. Replay passes actual project grants; independent member regression. |
| #1863 S4b-013 | fixed | Simulation resume rejects changed dt_seconds; retained step diagnostics follow output stride and final horizon. Physical checkpoint equivalence and changed-dt rejection tests. |
| #1863 S4b-014 | fixed | Workspace cube rglob/stat budget measurement offloaded with to_thread; actual file walk thread observation asserts no event-loop directory walk. |
| #1863 S4d-012 | transferred_to_core | Initial source patch handed to core for independent validation/final necessary fixes and dedicated regression ledger. Source ownership frozen here; core final disposition is authoritative. |
| #1863 S4d-013 | transferred_to_core | Initial source patch handed to core for independent validation/final necessary fixes and dedicated regression ledger. Source ownership frozen here; core final disposition is authoritative. |
| #1863 S4d-014 | transferred_to_core | Initial source patch handed to core for independent validation/final necessary fixes and dedicated regression ledger. Source ownership frozen here; core final disposition is authoritative. |
| #1863 S4d-015 | fixed | Composition instance mapping consumes distinct same-type base slots; excess instances keep unique IDs and force truthful inline representation. Actual two-title saved payload retains2slots. |
| #1863 S4d-016 | fixed | _new_version flushes only; create/new/restore commit after publish gate, rollback on failure. List reads latest version by one join, no per-asset SELECT. Commit-after-failed-create leaves0rows; observer verifies2total reads independent of asset count. |
| #1863 S4d-017 | already_fixed | Feature anchor without layer_id returns UNVERIFIED before presence checks. tests/review/test_review_anchors.py contains direct missing-layer regression. |
| #1863 S4d-018 | transferred_to_core | Initial source patch handed to core for independent validation/final necessary fixes and dedicated regression ledger. Source ownership frozen here; core final disposition is authoritative. |
| #1863 S4e-007 | fixed | gis_skills/induction/trace_analyzer.merge_analyses rejects unaccepted members before pooling; direct rejected-member regression. |
| #1863 S4e-008 | fixed | gis_world_state/mutation batch guard reuses stable user_wins result and retains engine producer stamp; exact precommit boundary regression. |
| #1863 S4e-009 | fixed | Removed ineffective prior-less spec guard outside lock; authoritative guard remains inside engine precommit. |
| #1863 S4e-010 | fixed | provenance.last_presentation_owner reads batch shown/hidden, skips malformed entries and chooses actual revision; durable wrapper documents full retained ring rather than discarding user decisions by advisory threshold. |
| #1863 S4e-011 | fixed | precedence documentation now matches deliberate declared-default retention; SYSTEM fills absent values but does not reassert an already declared default. Direct policy tests. |
| #1863 S4e-012 | fixed | distribution_drift rejects nonfinite/bool/malformed quantile and null evidence as unevaluated, without shifting vector positions or minting a verification fingerprint. |
| #1863 S4e-013 | fixed | record_recipe_outcome flushes only; harvest holds project transaction lock, deduplicates session/cartographic identity with bounded128 receipt history, and rolls back facts plus affinity together. A→B→A, cross-session and injected failure tests. |
| #1863 S4e-014 | already_fixed | intent_learning initializes matched_rules/fallback/candidates before extraction. New throwing-property regression proves surviving evidence is still recorded. |
| #1863 S4e-015 | fixed | Both bounded queries already use deterministic created_at/id order. selfheal action table now derives every action from one snapshot, eliminating per-action queries; SQL observer asserts1 SELECT. |
| #1863 S4e-016 | fixed | selfheal_policy explicitly experimental/offline; attribution does not claim to publish signals. Cold-start neutral prior0.5 preserves initial order on ties; direct rank test. |
| #1863 S4e-018 | fixed | RegistryToolResolver verifies descriptor/has existence and folds aliases before metadata. Subclaim recommending deletion of deprecation_of is not_a_bug: authoritative descriptor defines this as the replacement; legitimate successor hint retained, never autoexecuted. Unknown fallback/alias tests. |
| #1863 S4f-004 | fixed | obligations uses shared component_delta; locked ensure is allowed only when actually no-op. |
| #1863 S4f-005 | fixed | Only caller-authoritative analysis outputs grant refs; IR self-declaration cannot. Layer source aliases canonicalize to a live actual source in obligation and compiler paths. |
| #1863 S4f-006 | fixed | projector preserves required grammar obligations for plan-provided components, including title/legend; final-display evidence checks them. |
| #1863 S4f-007 | fixed | apply receipt ring uses atomic_update_state instead of lock-free get/set. Concurrent8-receipt test retains all. |
| #1863 S4f-009 | fixed_by_root | Transferred exact source ownership to root. Per-code implementation and80focused pass evidence in 2026-11-06-root-unmapped-findings.md; root final gate owns integration. |
| #1863 S4f-010 | fixed_by_root | Transferred exact source ownership to root. Per-code implementation and80focused pass evidence in 2026-11-06-root-unmapped-findings.md; root final gate owns integration. |
| #1863 S4f-011 | fixed_by_root | Transferred exact source ownership to root. Per-code implementation and80focused pass evidence in 2026-11-06-root-unmapped-findings.md; root final gate owns integration. |
| #1863 S4f-012 | fixed_by_root | Transferred exact source ownership to root. Per-code implementation and80focused pass evidence in 2026-11-06-root-unmapped-findings.md; root final gate owns integration. |
| #1863 S4f-013 | fixed_by_root | Transferred exact source ownership to root. Per-code implementation and80focused pass evidence in 2026-11-06-root-unmapped-findings.md; root final gate owns integration. |
| #1863 S4h-010 | fixed | builder consumes one bounded islice window (no lost first generator member or extra fetch), sampling numeric and string fields separately. Generator/CJK-category evidence test. |
| #1863 S4h-011 | fixed | projections prioritizes authoritative temporal.time_field over field-role inference; direct projection regression. |
| #1863 S4h-012 | fixed | reuse guards descriptor=None even with record status OK, returning unknown rather than dereferencing absent evidence. |
| #1863 S4h-013 | fixed | Stable sidecar flock plus bounded64 thread stripes serialize provenance ring rewrite and descriptor head/history/prune. Real spawn12 lineage writers and8 descriptor writers retain every in-budget record/version and readable head. |
| #1863 S4j-008 | fixed | MapContext.layer_ids retains complete authoritative ID inventory independently of100 rendered summaries; consistency abstains when source unknown/unavailable or legacy inventory incomplete. 120-layer and unknown-source reconciliation tests. |
| #1863 S4j-009 | fixed | compile_interaction derives last presentation decision per target through shared revision-aware provenance helper; malformed entries/details tolerated and later show clears hide. Batch targets supported. |
| #1863 S4j-010 | fixed | projection fact_line isolates numeric/shape renderer failures and emits bounded compact fallback, preserving other fact rows and existing untrusted-content fences. |
| #1863 S4j-011 | fixed | canvas geometry rebuilt from validated finite in-range2D points. Fixed expected GeoJSON nesting, closed polygon rings and256-point/ring cap checked during traversal; invalid coordinates reject rather than entering persisted raw tree. |
| #1863 S4j-012 | fixed | associative_index naive ISO timestamps use UTC; aware offsets retain actual epoch. Offset/naive equality regression. |
| #1863 S4j-013 | fixed | Index search/get/touch update LRU recency; nonfinite coordinates rejected before floor; huge query grids fall back to bounded current entry set rather than enumerating global cells. Retention and global bbox regressions. |

| Separate Medium | Disposition | Evidence / implementation |
|---|---|---|
| #1820 S4f-001 | fixed | Mutation IDs bind IR fingerprint plus actual base revision and fingerprint; later recompile cannot deduplicate against an older base. Duplicate apply advances expected revision only from actual returned truth. |
| #1820 S4f-002 | fixed | Presentation patch removes contradictory legacy top-level visible when writing authoritative layout.visibility. Legacy hidden→show becomes visible; input unchanged. |
| #1820 S4f-003 | fixed | Absent layer restyle/set_visibility with complete blueprint+live source upserts; insufficient evidence remains TARGET_ABSENT no-op. |
| #1819 S4d-004 | transferred_to_core | Planning failed-dependency propagation implementation handed to core alongside planning Low for final independent tests. |
| #1819 S4d-005 | fixed | Replay resolution derives real project grants and defaults to current published revision, preserving a member's visible project asset despite a newer draft. |
| #1819 S4d-006 | fixed | Implicit versions are complete snapshots without a merging parent. Explicit inheritance remains supported; overdeep legacy chains raise instead of truncating. Twenty successive versions and forced20-node legacy chain tests. |
| #1819 S4d-007 | already_fixed | Checkpoint already carries expected_revision=current and maps superseded to base_revision_drift. New real-engine race injects concurrent mutation precisely between base check and checkpoint; merge refuses and other actor zoom7 survives. |
| #1819 S4d-008 | fixed | Rebase already rechecked state/merge reservation in atomic mutator; now also verifies unchanged old base. New reservation race preserves merge_in_progress and old base rather than overwriting state. |

Validation:

- `/tmp/frontend-service-final1863.log`: **416 passed,1 skipped** across all new behavior tests plus complete affected semantics/situation/compiler/review/template/simulation/learning/quality neighboring modules.
- `/tmp/frontend-1863-compiler-all2.log`: **65 passed** across7 compiler/projector/obligations/apply/tool files, including complete shared delta and plan-ID regressions.
- `/tmp/frontend-1863-world-learning3.log`: **101 passed** across6 world-state/learning/dispatch files.
- `/tmp/frontend-low-extra-behavior2.log`: **41 passed**, including actual cross-process and Redis-Lua tests.
- `/tmp/frontend-low-template5.log`: **169 passed,1 skipped** complete template/review/quality/simulation modules.
- Additional final situation contract/observation audit `/tmp/frontend-situation-contract-final1863.log`:27 passed,1 expected schema-dump drift caused by additive full layer_ids. Root owns final regeneration via `scripts/situation_inspect.py --dump-schema`; refresh recipe includes this command. No unexplained behavior failure.
- `/tmp/frontend-low-new-types-final.log`:6 new/changed helper modules pass targeted mypy; broader17-file exploratory mypy exposed pre-existing SQLAlchemy Column annotations/optional-shape issues outside the configured gate (recorded in `/tmp/frontend-low-types1.log`), not silently counted as a clean run.
- `/tmp/frontend-service-debt-final1863.log`: whole-repo debt ratchet passes, including DictAny7085≤7086, unannotated1756≤1788 and all17 god modules. No baseline was changed. Final targeted Ruff rerun includes the migrated template tests.

Limits: harvest receipt identity retention is bounded128 (historically evicted identities can be counted after that horizon); situation retains full authoritative layer IDs but renders only100 summaries. These are explicit retention/projection bounds, not claims of unlimited history.
