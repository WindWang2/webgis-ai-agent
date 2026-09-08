# 05 — Chaos & Fault-Injection Map (webgis-ai-agent-quality-v1)

Date: 2026-09-08. Method: static audit (grep + read). No tests executed. All paths relative to repo root; `file:line` evidence cited.

## 1. Executive summary

- The repo already has **six dedicated deterministic chaos suites** for the Agent Runtime (`tests/test_runtime_chaos_{engine,resume,pi,store,lifecycle,plan_mode}.py`, ~4,200 lines, defect IDs F1–F28), one for GeoCompute (`tests/unit/test_geocompute_chaos.py`, ADR-0096), one transport-level suite for Data Fabric (`tests/unit/test_data_fabric_fault_injection.py` + offline fake-server fixture), and a cartography "fault-injection subset" (`tests/cartography/test_cartography_closed_loop.py:313` + `tests/fixtures/runtime/fault-*` probe fixtures, gated by the `cartography` marker in `pytest.ini`).
- Determinism is achieved **by convention, not by framework**: `asyncio.Event` barriers, monkeypatched seams, patched timeout constants, `fakeredis`, `httpx.MockTransport`, byte-level file corruption. No shared fault-injection utility exists; every suite hand-rolls its fakes (only Pi and Data Fabric have reusable fixtures: `tests/fixtures/pi_mocks.py`, `tests/fixtures/data_fabric/fake_server.py`). No hypothesis/RNG anywhere (`grep hypothesis tests/` → empty; `tests/test_adversarial_sse_chaos.py` says "random" in its docstring but contains zero randomness).
- Coverage is **strong** where failure handling was recently hardened (LLM provider taxonomy, jobs/celery durable lifecycle, cancellation primitives, session store Redis degradation, MVT/epoch + ref-payload cache races, data fabric retry/breaker).
- Coverage is **weak** for: the disk artifact cache failure/LRU paths (`app/lib/artifact_cache.py` has 3 happy-path tests only), lock-loss during artifact-registry writes, ingest dedup races, cache-broadcast listener reconnect, export/PNG & report HTML write failures, and NaN/Inf propagation through `sanitize_json_obj`/chart/report paths.
- Highest-value gap: **`app/lib/artifact_cache.py` LRU eviction + publish-failure paths are completely untested** while sitting under every expensive raster op (silent wrong-output / silent cache loss risk), followed by **cross-pod lock-loss semantics on artifact_registry/cartography_runtime writers** (`fail_on_degraded=False/True` mismatch is asserted nowhere).

## 2. Existing fault-injection patterns

| Mechanism | Location | Covered faults |
|---|---|---|
| Event-barrier + monkeypatch engine seams (async) | `tests/test_runtime_chaos_engine.py` (1,360 ln) | F1 lock-eviction race, F3 terminal overwrite, F7 stuck running after cancel, F8 cancel/fail indistinguishable, F9 orphan tool_calls, F15 fire-and-forget drain, F17 durable-job cancel cascade, F18 silent task eviction, F21 partial clear_session, F23 stub permanently cached, F28 non-stream no cancel_watch |
| Same, for SSE resume/route layer | `tests/test_runtime_chaos_resume.py` (1,168 ln) | F2 cross-anonymous-session leak, F4 queued POST clobbering resume, F5 session-blind abort, F20 premature done fabrication, F26 dropped-vs-duplicate ack, P1 shadow-stream, P2 buffer eviction preference, Last-Event-ID 0 refusal |
| Fake RPC client + subprocess death + patched timeouts | `tests/test_runtime_chaos_pi.py` (742 ln) | F5 abort scoped per session, F10 stall timeout sends abort, F19 pending-request leak on cancel, F24 callback runs under CancellationToken, G mid-stream Pi process death |
| `fakeredis.aioredis` + error injection | `tests/test_runtime_chaos_store.py` (295 ln) | F13 L1 invalidation on clear, F22 Redis fault isolation per store op, F12 loop-change client rebuild, F27 first-terminal-wins race |
| Tracking fakes for lifespan/broadcast/session | `tests/test_runtime_chaos_lifecycle.py` (359 ln) | F11 async engine dispose, F14 `async_db_session` close on success/rollback/cancel, F15 lifespan drain, F16 broadcast bounded-wait + dead-socket eviction, repeated-lifecycle orphan tasks |
| Gated tool coroutines | `tests/test_runtime_chaos_plan_mode.py` (255 ln) | F6 cancel mid-plan leaves `__status__=running` forever |
| Event-gated fake bridge (no RNG) | `tests/test_adversarial_sse_chaos.py` (257 ln) | disconnect+cancel+reconnect races, multi-client resume, double-send, ring-buffer overflow, terminal immutability |
| Fake DB session + patchable clock + real rasterio bytes | `tests/unit/test_geocompute_chaos.py` (520 ln, ADR-0096) | DB disconnect mid-query (typed failure, descendant skip :73), deadline expiry mid-run (:136), budget exceeded (:188), WORKER_LOSS stale-sweep typed error (:299) + executor node fail (:338), COG garbage/truncated bytes (:385), cancel between waves leaves no threads (:413), singleflight builder failure recovery (:491) |
| Canned-route `HTTPAdapter` running real SSRF/redirect machinery | `tests/fixtures/data_fabric/fake_server.py` + `tests/unit/test_data_fabric_fault_injection.py` | redirect-to-metadata SSRF, loopback redirect, retry-on-503, auth-fail no-retry, pagination, oversized response |
| Typed-error unit grid | `tests/unit/test_data_fabric_reliability.py`, `_v2.py` | retry policy (transient/permanent, jitter bounds), circuit breaker open/half-open/closed, cancellation soak (no ghost refs), cursor pagination, malformed JSON typed |
| `httpx.MockTransport` + real socket | `tests/unit/test_provider_contract_v2.py`, `test_llm_http_lifecycle.py` | LLM failure taxonomy: rate limit fails honestly (:138), read timeout classified (:146), disconnect mid-stream ≠ fake done (:203), malformed tool args surface honestly (:118), duplicate chunks idempotent (:219), context-too-large (:239), tool-schema rejection (:249), cancelled stream doesn't poison pool (:268) + real-socket release (:440), cross-loop clients (:513) |
| `AsyncMock(side_effect=...)` store seams | `tests/cartography/test_cartography_closed_loop.py:313` (`save_mapspec` → `RuntimeError("disk full")` → rollback, last-known-good intact), `tests/unit/test_mapspec_store.py:283` (evicted live-ref checkpoint fails truthfully), `tests/data/test_ingest_pipeline.py:99` (register failure rolls back ref) | MapSpec transactional rollback; honest failure, no false success |
| Fixture-driven probe corruption (browserless) | `tests/fixtures/runtime/fault-missing-source`, `fault-wrong-color` (`probes.json` `expect:"fail"`) consumed by runtime validator; contract pinned in `tests/unit/test_runtime_fixture_contract.py:65-69`, verdict in `tests/unit/test_runtime_validator.py:264-273`; closed-loop cartography gate = `cartography` marker (`pytest.ini`) | missing source, wrong color must produce invalid verdict, never false pass |
| Redis/epoch race harnesses | `tests/unit/test_cache_race_windows_v3.py`, `test_mvt_epoch_race_p11.py`, `test_tool_cache_singleflight.py`, `test_redis_eviction_ghost_cache_1111.py` | invalidate-during-build never resurrects; put/invalidate interleaving; stale-lock degrade; failed winner releases lock; Redis eviction invalidates derived caches (no ghost data) |
| Job/celery failure grid | `tests/jobs/test_job_worker.py`, `test_job_store.py`, `test_job_celery_e2e.py` | claim-once vs duplicate delivery, cancel-before-exec, late success after cancel converges to cancelled, exception → redacted failure, artifact atomic finalize/discard/nothing-written, stale heartbeat sweep, enqueue-raise → job failed (:171), missing job row skipped |
| Lock degradation | `tests/unit/test_session_lock_resilience.py` | degraded mode visibility, lock-lost signal on renew expiry (:52), session_plan aborts save on lost lock (:97), in-process mode when Redis disabled |
| Env/rollback hardening batch | `tests/unit/test_audit_reliability_E.py` | #745 lock client socket timeouts, #746 rollback missing blob, #747 missing token fail-closed, #748 rollback failure honesty, #752 clear_session degrades on Redis error |
| NaN/numeric hardening | `tests/unit/test_raster_math_hardening.py` (uint16 overflow :43, NaN nodata mask :64/:135), `tests/unit/test_gis_crash_bugs.py` (non-numeric field, invalid network → failure not crash), `tests/unit/test_mapspec_to_svg.py:193` (NaN/Inf bounds) | numerical failure containment |

## 3. Failure-path inventory per subsystem

Legend: **handled** = code path exists (file:line); **tested** = a test exercises it.

### 3.1 LLM provider (`app/services/chat/llm_client.py`, `model_runtime/`)
| Fault | Handled | Tested |
|---|---|---|
| Connect-phase failure / pool timeout (retry ≤3, backoff) | `llm_client.py:362,389-407` | Y (`test_provider_contract_v2.py`, `test_llm_http_lifecycle.py`) |
| Read/stream timeout; mid-stream disconnect never retried | `llm_client.py:181-190,469-499`; taxonomy `model_runtime/provider.py:20-87` | Y (`test_provider_contract_v2.py:146,203`) |
| Malformed stream / JSON decode / bad tool args | `provider.py:31` MALFORMED_OUTPUT; `llm_client.py:515` | Y (`test_provider_contract_v2.py:118,219`) |
| 429/5xx/context-too-large/schema-rejection classification | `provider.py:35-65`; routing `model_runtime/routing.py:32` | Y (`test_model_runtime_v2.py:146,226-338`) |
| Provider health cooldown / circuit | `model_runtime/health.py:105`; `services/provider_health.py:84-141` | Y (`test_model_runtime_v2.py:106-131`) |
| **All-provider failure (fallback chain exhausted) end-to-end turn** | routing fallback chain exists (`test_model_routing_bridge.py:146`) | Partial — unit-level only; no test that a whole chat turn degrades to truthful error after chain exhaustion |
| Error-body injection back into model context | `provider.py:157-178` | Y (`test_provider_contract_v2.py:267`, `test_model_runtime_v2.py:184,346`) |
| Cross-provider geo fallback (`with_fallback`) | `app/tools/chinese_maps/http.py` | Y (`tests/unit/test_683_provider_fallback_semantics.py:194-241`) |

### 3.2 Disk writes (artifacts, exports)
| Fault | Handled | Tested |
|---|---|---|
| Atomic artifact finalize (temp + replace, cancel-discard, nothing-written) | `app/lib/artifacts.py:16-52` | Y (`tests/jobs/test_job_worker.py:277-403`) |
| MapSpec save disk-full → rollback, last-known-good | `mapspec` lifecycle engine | Y (`test_cartography_closed_loop.py:313`) |
| **`artifact_cache.publish_artifact` copy failure → tmp unlink, fallback** | `app/lib/artifact_cache.py:155-172` | **N** (only happy path: `tests/unit/test_artifact_cache.py:45`) |
| **`artifact_cache` LRU byte-cap eviction** | `artifact_cache.py:191-222` | **N** (no test touches `_evict_if_needed`/`MAX_ARTIFACT_BYTES`) |
| Corrupt `.meta` sidecar → treated as miss | `artifact_cache.py:123-126` | **N** |
| Orphan/aged disk sweep | `artifact_cache.py:267-336`, `artifact_lifecycle.py:176` | Y (`tests/data/test_gc.py:25-71`) |
| Session disk purge survives purge failure | `artifact_lifecycle.py:101-169` | Y (`tests/test_session_disk_lifecycle.py:97`) |
| **Report HTML / map-export PNG write failure** | weak: `runtime_asset_assembly` / report service write paths | **N** (only off-loop placement: `tests/test_event_loop_offload_590_592.py:298,328`) |
| Upload temp write failures | upload pipeline | Partial (`tests/test_issue546_upload_cleanup.py`, `test_upload_delete_ordering.py`) |

### 3.3 Cache (`artifact_cache`, `tool_cache`, `ref_payload_cache`, MVT, session L1)
| Fault | Handled | Tested |
|---|---|---|
| Invalidate-during-build / put-invalidate interleave | epochs in `mvt.py`, `ref_payload_cache` | Y (`test_cache_race_windows_v3.py:35,75,265,313`) |
| Singleflight builder crash / stale lock / Redis failure | `app/services/singleflight.py:58-` ; `app/lib/tool_cache.py` | Y (`test_tool_cache_singleflight.py:103-186`, `test_geocompute_chaos.py:491`) |
| Redis eviction ghost-cache (L1 vs Redis eviction) | session L1 | Y (`test_redis_eviction_ghost_cache_1111.py:26-119`) |
| **Artifact cache hit/miss correctness under source mtime collision** | `artifact_cache.py:111-131` | Partial (identity test only; no collision/rewrite test) |
| Cache-key namespace bump invalidation | `ARTIFACT_VERSION_NS` (`artifact_cache.py:52`) | N (only key determinism tested) |

### 3.4 DB (`async_db_session`, job store)
| Fault | Handled | Tested |
|---|---|---|
| Commit/rollback/close on success, exception, cancellation | `app/tools/_utils.py:92-113` | Y (`test_runtime_chaos_lifecycle.py` F14, `tests/test_async_db.py`) |
| Transient DB failure mid-query (geocompute) | typed GeoComputeError | Y (`test_geocompute_chaos.py:73`) |
| Concurrent claim/duplicate completion/cancel-complete race | `jobs/store.py` rowcount guards | Y (`test_job_store.py:269-364`) |
| Stale heartbeat sweep (worker loss) | `jobs/store.py:974` sweep | Y (`test_job_store.py:508`) |
| Sync `db_session` failure path (`_utils.py:70-89`) | rollback/raise | Partial (async variant tested; sync variant implicit) |

### 3.5 Artifact store / promotion
| Fault | Handled | Tested |
|---|---|---|
| Missing/unreadable promoted content disclosed | `project_artifact_promotion.py:189-224` | Y (`tests/unit/test_reproducible_gis_runtime.py:548-658`) |
| Store outage during promotion disclosed, not fatal | `project_artifact_promotion.py:290` | Y (same file) |
| Evicted live-ref checkpoint fails truthfully | `test_mapspec_store.py:283` | Y |
| **Lock lost mid artifact-registry write (cross-pod)** | `artifact_registry.py:444,552,592` use `fail_on_degraded=False` — lost lock raises nothing | **N** (lock-lost abort tested only for `session_plan`, `test_session_lock_resilience.py:97`) |

### 3.6 Jobs / Celery (`app/services/jobs/*`, `app/services/geocompute/tasks.py`)
| Fault | Handled | Tested |
|---|---|---|
| Worker loss (heartbeat timeout → stale) | `jobs/worker.py` watchdog + store sweep | Y (`test_job_worker.py`, `test_job_store.py:508`, `test_geocompute_chaos.py:299,338`) |
| Retry/requeue duplicate delivery (acks_late) | claim-once `worker.py:332-346` | Y (`test_job_worker.py:234`, `test_job_celery_e2e.py:365`) |
| Timeout/stall | Pi stall (F10), node deadline (`geocompute` policy) | Y |
| Rejection (enqueue raise / unknown job / budget) | `submit` → failed; `AlreadyFinished` | Y (`test_job_celery_e2e.py:171`, `test_job_worker.py:251`, `test_geocompute_execution.py:257`) |
| **Celery worker hard-kill (SIGKILL) mid-write** | covered only via stale sweep (time-based) | Partial — no fault-injected kill; acceptable since DB probe is the mechanism |
| Cooperative cancel honored across lib compute | `checkpoint()` call sites in 14 `app/lib/geo_*` modules + `data_fabric/manager.py`, `spatial_tasks.py` | Y (`tests/jobs/test_job_cancellation.py` 26 cases; per-lib spot checks) |

### 3.7 Cancellation (`app/lib/cancellation.py`) — who honors it
- Producers of checkpoints: all `app/lib/geo_analysis/*` (14 modules), `geo_raster/windowed.py`, `data_fabric/manager.py`, `jobs/worker.py` (`DurableJobHandle.checkpoint` :106), geocompute executor (`executor.py:606,646`), Pi callback (`dispatch_tool`, F24), plan waves (F6), execution engine cancel_watch (F28).
- Tested: primitive semantics exhaustively (`tests/jobs/test_job_cancellation.py`), cascade/link (`:72`), thread propagation (`:173`), registry LRU (`:285`). Gaps: **`cancellable()` overshoot under `every>1` on real lib loops** is unit-tested (`:230`) but no integration test cancels a *long-running raster op* at a chunk boundary; `DurableCancellationProbe` (DB-backed cancel) has only indirect coverage via stale tests.

### 3.8 Scheduler / GeoCompute queue
| Fault | Handled | Tested |
|---|---|---|
| Budget/admission rejection | `executor.py:371-403` | Y (`test_geocompute_execution.py:257`, chaos :188) |
| Deadline expiry mid-run | policy deadline | Y (chaos :136) |
| Node crash (thread exception swallowed) | typed capture `executor.py:675-689` | Y (`test_geocompute_execution.py:181,223`) |
| Descendant skip on failure/cancel | `_run_ready_set` settle | Y (:181 + chaos :73) |
| Cancel between waves leaves no threads | `chaos:413` | Y |
| **Queue stall (no worker picks up durable node)** | stale sweep is the only recovery | Partial (stale-tested; "job queued forever" UX path untested) |

### 3.9 Renderer / exporter
| Fault | Handled | Tested |
|---|---|---|
| SVG compile with NaN/Inf/degenerate/malformed input | `mapspec_to_svg` | Y (`test_mapspec_to_svg.py:158-251`) |
| SVG sanitize (XSS) | `test_svg_sanitize.py` | Y |
| **PNG/tile encode failure (rasterio/Pillow raise)** | `raster_tile_service` raises propagate | N (only render correctness: `test_raster_tile_service.py`) |
| **Export batch view mismatch** | rejects | Y (`test_export_phase3.py:133`) |
| Chart tool invalid data / NaN series | invalid type/json rejected | Partial (`test_chart_tool.py:45,75`; no NaN-series test) |

### 3.10 Numerical (NaN/Inf)
- Masking + dtype promotion: tested (`test_raster_math_hardening.py`).
- JSON sanitize `app/tools/_utils.py:14-25` (`sanitize_json_obj`) — **no direct test** that NaN/Inf from arbitrary tool payloads is nulled before SSE.
- Stats paths use `np.nan*` guarded by `np.isfinite(...).any()` (`remote_sensing.py:281-295`, `change_detection.py:217-224`) — tested via oracle suites (`tests/science_oracles`, `test_golden_gis_numerics.py`) on happy data; **all-NaN-input** cases untested outside raster_math.

## 4. Race / consistency hotspots

| Hotspot | Guard in code | Test evidence | Residual risk |
|---|---|---|---|
| Cache invalidate during build (ref payload, MVT, spatial index) | epochs + `put_if_current` (`mvt.py`, `ref_payload_cache`) | `test_cache_race_windows_v3.py`, `test_mvt_epoch_race_p11.py` | low |
| Session lock across pods | Redis SET-NX + token Lua release/renew (`distributed_lock.py:46-58`) | `test_session_lock_resilience.py` | **renew-loop treats an eval *exception* as "keep trying" (`:239-240 continue`); only a 0-return sets `lost` — a network-error renewal loop can let TTL lapse without ever marking lost. Untested.** |
| Lock-degradation policy mismatch | `fail_on_degraded` per call site | visibility tested (`:27`) | artifact_registry uses `False` (`:444,552,592,749`) vs session_plan/cartography `True` — no test pins which writers tolerate silent cross-pod serialization loss |
| Simultaneous promotion of same run | promotion writes content-addressed blobs (`_content_path` fingerprint) | happy + outage (`test_reproducible_gis_runtime.py:548`) | no concurrent-promotion test (idempotency of double materialize untested) |
| Duplicate ingest (same content, concurrent) | sha256 dedup check-then-register (`data_ingest/pipeline.py:158-166`) | sequential dedup only (`test_ingest_pipeline.py:62-94`) | **two concurrent ingests can both miss `_find_duplicate` → duplicate refs; rollback path (`_rollback_ref` :257) is best-effort and untested under concurrency** |
| Concurrent writes to same session (chat turn + MapSpec mutation) | `session_lock` + turn locks (F1/F4 fixed) | chaos suites F1/F2/F4 | low-moderate |
| SSE resume buffer eviction vs live turns | ended-first eviction (P2) | `test_runtime_chaos_resume.py` | low |
| Job store terminal-state races | transition table + rowcount | `test_job_store.py:269-364` | low |
| Broadcast listener reconnect (`cache_broadcast.py:148-158`) | daemon loop, 5 s retry | **N** — only publish-side and `_apply_event` wiring tested (`test_cache_lineage_v4.py:154-234`) | missed cross-process invalidations are masked by TTL/fingerprint by design (ADR-0101), so blast radius = staleness window, not corruption |
| Executor `NodeResultStore` (LRU 128 MiB) concurrent put/evict | lock-free dict assumptions (`executor.py:140-189`) | no dedicated race test | low (best-effort reuse cache) |

## 5. Determinism status

- **No RNG, no wall-clock dependence by convention.** The chaos suites state this explicitly (`test_runtime_chaos_engine.py:21-23`: "asyncio.Event 屏障 + monkeypatch 引擎接缝，无 wall-clock 依赖"; `test_geocompute_chaos.py:5-8`: "no sleeps > 1s, no network, no docker, no marks"; `test_runtime_chaos_pi.py:23-25`: "tiny patched timeout constants"). `tests/test_adversarial_sse_chaos.py` is despite its name fully event-sequenced (no `random` import).
- Time manipulation is done by patching monotonic/deadline constants or store sweep functions, not a fake clock library (`test_geocompute_chaos.py:279-298` invokes sweep directly with synthetic timestamps).
- Residual wall-clock: `distributed_lock` renewal uses real `asyncio.sleep(8.0)` (`distributed_lock.py:234`) — tests avoid exercising renewal intervals; llm retry backoff `_RETRY_BACKOFF_S=0.5` (`llm_client.py:368-369`) is real-sleeped in any test that trips retries (contract tests assert classification, mostly single-attempt).
- **No shared utility.** Reusable pieces exist only per-domain: `tests/fixtures/pi_mocks.py`, `tests/fixtures/data_fabric/fake_server.py`, `tests/fixtures/mapspec_cow_fixtures.py`, plus `fakeredis` as a dependency. Fault IDs exist only inside the WP-chaos suites (F1–F28) as docstring conventions — not machine-readable, not a registry.
- pytest harness: `pytest.ini` timeout 60 s thread-based, `asyncio_mode=auto`; markers `heavy`, `cartography` (includes the fault-injection subset), `perf`, `real_services`. Chaos suites run in the default lane (no marker needed) — good.

## 6. Top-25 unprotected fault points (ranked by blast radius)

Ranking: data corruption > silent degradation > error surfacing > cosmetic.

1. **`artifact_cache._evict_if_needed` LRU eviction** (`artifact_cache.py:191-222`) — untested; a bug silently deletes cache entries other readers hold paths to (returned `final` path can be evicted mid-consumer). Data-loss-adjacent, silent.
2. **`artifact_cache.publish_artifact` copy/replace failure path** (`:155-172`) — untested; broken cleanup would leave phantom `<key>.tif` with no `.meta` (invisible to LRU until sweep). Silent corruption of cache accounting.
3. **Corrupt `.meta` → miss** (`:123-126`) and **`.meta` write failure after replace** (`:184-185`) — untested; the publish-vs-meta gap is acknowledged (`:277-280`) but only the sweeper (tested) guards it.
4. **Distributed-lock lost-ownership via renewal *exception*** (`distributed_lock.py:239-240` `except Exception: continue`) — renewal errors never set `_lost`, so `fail_on_lost` callers keep writing under an expired lock. Cross-pod concurrent write = data corruption. Untested.
5. **`fail_on_degraded=False` on all `artifact_registry` critical sections** (`artifact_registry.py:444,552,592,749`) — a Redis outage silently converts cross-pod mutual exclusion to per-pod; no test documents this tradeoff for registry writes (only session_plan aborts, `test_session_lock_resilience.py:97`).
6. **Concurrent duplicate ingest** (`data_ingest/pipeline.py:158-166` check-then-act) — untested race → duplicate refs + best-effort rollback (`:257-262`) can orphan a ref with a live artifact. Consistency.
7. **`atomic_output` `os.replace` failure mid-finalize** (`artifacts.py:52`) — untested (permissions/EXDEV); job would be marked completed in code order but no test pins the semantics.
8. **Concurrent `promote_run_artifacts` of the same run** (`project_artifact_promotion.py:226-`) — idempotency under double promotion untested.
9. **`cache_broadcast` listener reconnect loop** (`cache_broadcast.py:148-158`) — untested; a permanently wedged listener silently disables cross-process invalidation (mitigated by TTL/fingerprint authority — silent degradation).
10. **`broadcast_ref_invalidation` failure ignored by callers** (returns `False`, `cache_broadcast.py:104`) — no test asserts callers don't treat stale cross-process caches as fresh within TTL semantics.
11. **`sanitize_json_obj` NaN/Inf → null** (`app/tools/_utils.py:14-25`) — no direct test; regression would crash SSE JSON serialization or emit `NaN` tokens to browsers. Silent degradation of results.
12. **All-NaN raster band statistics** (`remote_sensing.py:293-295`, `change_detection.py:223-224`) — `finite.any()==False` branch untested outside raster_math; would surface `None` stats — is any consumer NaN-safe?
13. **Report HTML / export PNG write failure** (report service, runtime asset assembly; write paths exercised only for off-loop placement `test_event_loop_offload_590_592.py:298,328`) — no disk-full/permission test; error surfacing to user unverified.
14. **Raster tile PNG encode failure** (`raster_tile_service` render exceptions) — untested; HTTP 500 shape for a half-rendered tile unverified.
15. **`provider_health.tracked_provider_get` catch-all** (`provider_health.py:272` `except (json.JSONDecodeError, TypeError, Exception)`) — always-true tuple makes the JSONDecodeError branch dead; status-string contract per provider (`:171-221`) only partially exercised via geocoding tests.
16. **Whole-turn LLM failure (fallback chain exhausted) → user-visible error** — unit fallback tested (`test_model_routing_bridge.py:146`), end-to-end SSE error event after chain exhaustion untested.
17. **`llm_client` connect-retry backoff timing** (`llm_client.py:368-369`) — retries with real sleeps untested for attempt-count/backoff bound (only single-fail classification).
18. **`SessionLockRegistry` 60 s re-verify gate after Redis recovery** (`distributed_lock.py:305-308`) — untested that a recovered Redis is actually re-adopted within bounded time.
19. **Celery worker SIGKILL mid-artifact-write** — only time-based stale sweep tested; `.part-*` temp file left in `data/` relies on `artifact_cache` temp-leftover sweep (tested there, `test_gc.py:25`) but job-side temp (`track_temp`) cleanup on hard kill untested.
20. **`task_queue.revoke_task` / `get_task_status` failure paths** (`task_queue.py:129-161`) — only `submit` raise tested (`test_job_celery_e2e.py:171`); revoke of running/unknown task on the explorer chain untested.
21. **`sync db_session` rollback failure masking** (`_utils.py:85-88`) — untested (async variant F14 tested).
22. **Durable `DurableCancellationProbe` DB-poll cancel** (`jobs/worker.py:212-285`) — watchdog push tested (`test_job_worker.py:156`), the DB-probe-only path (no registry token, post-restart cancel) untested.
23. **Chart tool NaN/Inf series** (`app/tools/chart.py`) — untested; NaN would produce corrupt SVG paths if not sanitized upstream (depends on #11).
24. **`mvt` encode thread crash mid-singleflight** — cancellation leak tested (`test_mvt_cache_memory_pressure.py:265`), but builder *exception* (not cancellation) propagation for MVT singleflight untested (thread-side SingleFlight is tested; asyncio-side partially).
25. **`artifact_cache` cache-key `ARTIFACT_VERSION_NS` bump migration** (`:52`) — no test that old-namespace entries are simply missed (not served stale) after a bump.

## 7. Recommendations — test-only deterministic fault-injection framework

1. **Where to hook (no production code changes):**
   - Keep the existing seams — they are already sufficient: store methods (`save_mapspec`, `RedisSessionStore` ops), engine `_call_llm`, `ToolRegistry` handlers, `httpx.MockTransport`/`FakeFabricAdapter` for HTTP, `fakeredis` for Redis, and constructor-injected `session_factory` for DB (jobs). Standardize on these six seam kinds instead of ad-hoc `patch.object` on deep attributes.
   - For time, adopt one tiny test helper (e.g. `tests/fixtures/fault_clock.py`) exposing `FakeMonotonic` used by the 3 suites that currently patch constants by hand (`test_geocompute_chaos.py`, `test_runtime_chaos_pi.py` patched timeout constants, `test_session_lock_resilience.py:77` real sleeps).
2. **Central registry, production-off by construction:** create `tests/fixtures/chaos.py` with `@fault("LLM_TIMEOUT_MIDSTREAM")`-decorated context managers. Faults live only under `tests/` (import path `tests.fixtures.chaos`), so nothing ships in `app/` — no env flag, no dead code in prod. If a prod seam is missing, prefer adding a constructor parameter / module-level indirection already used by tests (the repo pattern: `original = ...; with patch.object(...)`) rather than a runtime chaos toggle.
3. **Naming & fault IDs:** adopt the existing F-number convention into machine-readable form: `FAULTS = {"STORE_REDIS_DOWN": <ctx mgr>, "LOCK_RENEW_ERROR": <ctx>, "ARTIFACT_COPY_FAIL": <ctx>, ...}` with IDs `<SUBSYS>_<FAULT>` (SUBSYS ∈ LLM, STORE, LOCK, CACHE, DISK, DB, JOB, GCMP, SSE, INGEST). Each ID documented with: injection point (file:line), expected observable (typed error? disclosed field? terminal status?), and the race window it targets. Register the IDs in a `tests/fixtures/chaos_catalog.py` consumed by a meta-test asserting every ID is used by ≥1 test (mirrors `test_runtime_fixture_contract.py` style).
4. **Deterministic schedules:** for multi-fault scenarios use an explicit schedule object (`ChaosSchedule([("LOCK_DEGRADE", at="acquire"), ("DISK_FULL", at="artifact.finalize")])`) driven by the same Event-barrier pattern the WP-chaos suites use; never RNG. If fuzzing is wanted later, seed and pin (`random.Random(seed)`), and record seed in the test id — currently unnecessary given suite conventions.
5. **Priority test additions (feeds the Top-25):** (a) `artifact_cache` failure/eviction/corrupt-meta trio (items 1–3, 25) — pure tmp_path tests, cheap; (b) `LOCK_RENEW_ERROR` → `lost` semantics + `fail_on_degraded` policy matrix per writer (items 4–5); (c) concurrent duplicate-ingest race via two tasks + Event gate (item 6); (d) export/report disk-full via monkeypatched write (item 13); (e) NaN-through-SSE contract test on `sanitize_json_obj` (item 11).
6. **CI placement:** keep fault suites marker-free (default lane, 60 s timeout is enough — existing chaos suites comply); any new suite doing real-socket work should self-skip like `test_llm_http_lifecycle.py:440` pattern or take the `heavy` marker.

## Appendix — key file map (fault-relevant)

- Cancellation primitive: `app/lib/cancellation.py` (token :36, checkpoint :163, registry :193); jobs re-export `app/services/jobs/cancellation.py`.
- Distributed lock: `app/services/distributed_lock.py` (release/renew Lua :46-58, degrade :185-208, renew loop :230-249, registry loop-rebuild :285-337).
- Atomic artifact write: `app/lib/artifacts.py`; disk cache: `app/lib/artifact_cache.py`; sweeps: `app/services/artifact_lifecycle.py`.
- Broadcast: `app/services/cache_broadcast.py`; ws: `app/services/ws_service.py:35-68`.
- LLM: `app/services/chat/llm_client.py` (retry :356-407, stream :458-), `app/services/chat/model_runtime/{provider,routing,health}.py`, `app/services/provider_health.py`.
- Jobs: `app/services/jobs/{store,worker,submit,lifecycle}.py`; geocompute durable: `app/services/geocompute/{executor,durable}.py`, celery entry `app/services/geocompute/tasks.py:23`.
- Data plane: `app/services/data_fabric/{reliability,security,errors}.py`, `app/services/data_ingest/pipeline.py`, `app/services/data_lifecycle/{gc,service}.py`.
- Cartography fault subset: `tests/cartography/test_cartography_closed_loop.py`, fixtures `tests/fixtures/runtime/fault-*`, validator `app/services/runtime_validator.py`.
