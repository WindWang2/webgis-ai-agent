# TEST_MATRIX — V01–V20 evidence plan

| # | Scenario | Test location (backend unless noted) |
|---|---|---|
| V01 | Empty typed layer first point/line/polygon | `tests/unit/feature_edit/test_datasets.py::test_create_empty_then_first_feature_*` (real ingest → ref + descriptor + revision; read back via session_data + feature page) |
| V02 | Sketch → project layer, reload-safe, Pi-readable | `test_datasets.py::test_promote_sketch_*` (artifact registered → catalog search finds it; mapspec layer mounted) + FE bridge test |
| V03 | Vertex edit on real uploaded layer | `test_structural_ops.py::test_replace_geometry_*` (service-transaction path: plan→approve→overwrite→stamp) + FE bridge unit |
| V04 | 100 mousemoves → 1 commit | FE `layer-edit-bridge.test.ts` (gesture end = single preview+approve; mock fetch counts) + `test_tools.py` idempotence |
| V05 | Escape cancels | FE bridge test (no REST call on cancel) |
| V06 | Delete + undo/redo identity | `test_structural_ops.py::test_delete_undo_redo_*` (wfid/count/attrs/geometry restored; no new ids) |
| V07 | Split holed polygon lineage | `test_structural_ops.py::test_split_*` (holes preserved per rule; parent/children lineage; union ≈ original within tolerance) |
| V08 | Merge with field conflicts | `test_structural_ops.py::test_merge_*` (explicit policy; disclosure; no silent loss; single part enforced) |
| V09 | Declared no_overlap violation | `test_topology.py::test_no_overlap_rejects_*` (located finding; commit blocked; payload unchanged) |
| V10 | Ordinary layer unaffected | `test_topology.py::test_undeclared_rules_not_applied` |
| V11 | Concurrent stale revision | `test_concurrency.py` extension (two plans, second → 409 stale_revision; re-preview OK) |
| V12 | Idempotent replay | existing idempotency tests extended to create (same wfid, no duplicate features, one revision bump) |
| V13 | Cross-session ref/plan | existing tests (dataset_not_found; no existence leak) extended for new endpoints |
| V14 | Budget/truncation honesty | `test_plan_bounds.py` (targets>limits → target_limit_exceeded; entry bytes → entry_too_large; no implicit first-N) |
| V15 | Crash between write/journal/stamp | `test_transactions.py` fault-injection (overwrite ok/journal fail, stamp fail — receipts honest, no partial truth) |
| V16 | Pi proposal vs manual interleave | `test_e2e_journey.py` extension (agent plan stale after user edit → CONFIRMATION/stale; user data not silently overwritten) |
| V17 | Split invalidates old references | lineage endpoint + receipt retired ids; selection prune FE test; artifact staleness via ref revision |
| V18 | Service-area recompute + export binding | `test_flagship_journey.py` (new e2e: draw area → stats artifact → edit boundary → artifact stale verdict → recompute → mapspec source revision bump → export payload contains new geometry) |
| V19 | Reload/session persistence | memory-store session re-open simulation (journal + wfids survive; history honest after FIFO) |
| V20 | Read-only source → derived copy | `test_datasets.py::test_editable_copy_*` (original fingerprint unchanged; copy has derived_from) |

Performance plan (WP12): bounded fixtures near limits; measure plan build/commit latency, journal entry sizes, gesture-time zero server calls (FE).
