# TEST MATRIX — ProjectKnowledgeProjection v1

Conventions: mirror `tests/test_gis_memory_store.py` — per-test **sqlite in-memory `StaticPool` engine + `Base.metadata.create_all`**, `pytestmark = pytest.mark.cartography` (deterministic release gate, no Node/LLM/network), `asyncio_mode=auto` from `pytest.ini`; suite is `pytest -n 2`-safe (no shared process state; module-level caches get `reset()` hooks like `ProactiveRetriever.reset`, `proactive_retriever.py:314-318`). Mission-style hermetic fakes via injected session factory (`mission_runtime/store.py:112-122` pattern).

## Files to create

| Test file | Oracle items covered |
|---|---|
| `tests/test_project_knowledge_store.py` | write gate/org stamping, budget eviction, idempotent rebuild, status transitions (active→stale→invalidated), malformed inputs |
| `tests/test_project_knowledge_verdicts.py` | reuse verdicts: exact / recompute_partial / not_reusable incl. all negative oracles |
| `tests/test_project_knowledge_invalidation.py` | stale after version bump; artifact/claim revision invalidation; scope-gone |
| `tests/test_project_knowledge_context_card.py` | card budget (chars/items), ids/refs-only, omission receipt, escaping |
| `tests/test_project_knowledge_api.py` | router surface, flag-off behavior, auth/IDOR, openapi additive |
| `tests/test_project_knowledge_isolation.py` | tenant + project isolation negatives |
| `tests/unit/project_knowledge/test_fakes.py` | shared fakes (below) if they grow beyond one file |

## Oracle → test mapping

| Oracle (from brief) | Test | Method sketch |
|---|---|---|
| Cross-mission reuse of valid refs | `test_exact_reuse_across_missions` (verdicts) | Two missions in one project; mission A produces artifact (promoted: content_sha256 revision row); request scope equal to A's AOI/temporal/method → candidate for mission B's pre-flight is `exact` with `authority_id` pointing at the same artifact row; authoritative row untouched (back-ref oracle) |
| Stale after version bump | `test_stale_after_dataset_version_bump` (invalidation) | Index entry with `version_fingerprint` F1 → re-attach dataset (new `compute_dataset_fingerprint`) → retrieval demotes entry to `stale`, verdict degrades `exact`→`recompute_partial`/`not_reusable`; mirrors `invalidate_for_dataset` semantics (`gis_memory/store.py:360-392`) |
| No mis-reuse: similar name / different AOI / different year | `test_not_reusable_on_scope_mismatch`, `test_name_similarity_never_upgrades` (verdicts) | Same subject string, `aoi_ref` differs → `not_reusable`; same AOI, `temporal_label` differs → `not_reusable`; subject text score high but digest mismatch → verdict unchanged |
| Context card budget | `test_card_char_and_item_budget` (card) | 50 entries → rendered card ≤1600 chars, ≤12 items, ends with omission receipt; assert no payload substrings (e.g. feature coordinates) and `_xml_fence` escaping of hostile subject `<script>` |
| Back-references to authoritative stores | `test_backrefs_resolve` (store) | Every entry's `(authority_store, authority_id)` resolves against seeded authoritative rows (artifact uuid, `ds_*`, `msn-*`, map product `(project_id, version_no)`); deleting a projection row leaves authoritative rows intact |
| Tenant isolation | `test_cross_org_read_empty`, `test_cross_project_read_empty`, `test_api_idor_404` (isolation/api) | Org B query over org A project returns empty (org-equality predicate); API with foreign `project_id` → None via `get_project_with_auth` gate; mirrors `test_gis_memory_security.py` discipline |
| Invalidation on artifact/claim revision | `test_artifact_head_change_demotes`, `test_claim_status_change_demotes` (invalidation) | New `ArtifactRevision` (different sha256) → entries pinned to old digest → `stale` on read; claim flips `supported`→`contradicted` in live ClaimStore → `verified_by` edge → `unknown`, never `supported` (positive-proof) |
| Empty / missing / malformed inputs | `test_empty_project_card`, `test_malformed_row_skipped`, `test_none_and_oversize_inputs` (store/card) | Empty project → empty card string (never an empty block), search → `[]`; corrupted projection row (bad JSON value, unknown kind) → skipped per-row fail-open like `_render_line` (`gis_memory/projection.py:142-145`); oversize subject/value rejected at write gate |
| Idempotent rebuild | `test_rebuild_idempotent` (store) | Rebuild twice over same seeded stores → identical entry set (same natural keys), no duplicate active rows (partial unique index), counts stable |
| Kill-switch off behavior | `test_flag_off_no_router_no_hooks` (api) | `GIS_PROJECT_KNOWLEDGE=0` → router not registered (app routes unchanged vs snapshot), ingestion hooks are zero-cost no-ops, hot path identical (pattern: `GIS_MISSION_HOTPATH` default-off, `flags.py:31-36`) |
| Bounded failure-warnings surface | `test_failure_warnings_bounded` (card) | Failure entries render as ≤N bounded warning lines with TTL/expiry respected (`provider_failure` TTL 7d precedent, `contract.py:109`) |
| Session-ref lifecycle hook | `test_ref_overwrite_hook_demotes` (invalidation) | `register_ref_invalidation_hook` fires → `ref:*`-keyed entries demoted; hook raising must not break authority (observer contract, `ref_lifecycle.py:60-94`) |

## Existing fixtures/fakes reused

- sqlite `StaticPool` engine fixture pattern — `tests/test_gis_memory_store.py:51-60`.
- `Base.metadata.create_all` model registration incl. CheckConstraints — same file.
- Hermetic store fakes via injected factory — `tests/unit/mission_runtime/**` conventions.
- Session-store fake for ref lifecycle tests — `tests/unit/test_artifact_registry.py` alias-ledger fakes.
- Auth dependency overrides / IDOR matrices — `tests/unit/test_project_api.py`, `tests/unit/test_project_domain.py`.
- Cache-fingerprint/perf assertion style — `tests/perf/test_project_context_cache.py` (exact-substring block assertions).
- OpenAPI additive-snapshot refresh via `API_SNAPSHOT_UPDATE=1` — `tests/quality/snapshots/openapi.json` (as PR #1355/#1353 did).
