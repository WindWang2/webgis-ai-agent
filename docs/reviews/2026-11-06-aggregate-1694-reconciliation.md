# Aggregate #1694 — exact finding reconciliation

This records all 74 P2/P3 IDs omitted by the first machine reconciliation. Existing P0/P1 evidence remains in the domain ledgers. Dispositions are based on current source, original requirements and behavioral evidence; unrelated refactors are not prerequisites for aggregate closure. Final integrated gates and upstream deployment reconciliation remain required.

| Issue | Finding | Status | Evidence |
|---|---|---|---|
| #1694 | SEC-05 | fixed | kustomization uses fail-closed zero SHA placeholders; pin-image requires a nonzero SHA256 and verifies registry availability before changing manifests; migration/deployment regression suite |
| #1694 | SEC-06 | fixed | data_fabric.security and core.egress include postgres/postgresql/s3/minio/gs in outbound policy; security tests and data-domain ledger |
| #1694 | SEC-07 | fixed | Settings validates STAC_API_URL; StacApiIO now uses make_safe_session for catalog/search/pagination/redirects, with actual per-connection DNS validation; test_stac_catalog_security rejects private DNS, nonallowlisted hosts, metadata search links and redirects before transport |
| #1694 | SEC-08 | fixed | security._SafeConnectionMixin resolves and pins only the current connection; no global socket.getaddrinfo replacement; HTTP/TLS keep original hostname and validate redirects |
| #1694 | SEC-09 | fixed | verify_password rejects scrypt combinations above 64MiB before hashing and requires the expected digest size; async login work remains off the request loop |
| #1694 | SEC-10 | fixed | logout requires get_current_user_with_version; revoked credentials cannot mutate token_version again |
| #1694 | SEC-11 | fixed | config/egress/data security explicitly deny Alibaba and Tencent IMDS addresses and names, including private-network exemption paths; config SSRF regressions |
| #1694 | SEC-12 | fixed | RAG retrieval denies unowned double-null documents to anonymous callers; explicit tenant/admin scope is required; RAG scope regressions |
| #1694 | SEC-13 | fixed | combined service supervisor invokes uvicorn with --ws-max-size 16384; both image entrypoints use the supervisor; application frame checks remain |
| #1694 | SEC-14 | fixed | _live_identity obtains org/role/version/activity from DB and rejects missing/unavailable identities; downstream get_current_user returns authoritative DB org_id |
| #1694 | ARC-05 | already_fixed | original explicitly duplicates closed #1544; hot-path extraction and debt ratchet already landed; no unsupported claim that every long method must be rewritten |
| #1694 | ARC-06 | fixed | translator/renderer/user-mutation/copilot console calls use devOnly; frontend-domain ledger and production lint/build evidence |
| #1694 | ARC-07 | already_fixed | original explicitly duplicates closed #1549; exception debt ratchet is maintained; broad exception classification is handled by exact service findings, not blanket BLE001 rewrites |
| #1694 | ARC-08 | fixed | FakeSubagentRunner is explicit test support; _late_tasks holds strong references, removes completed tasks and consumes errors |
| #1694 | ARC-09 | fixed | README and architecture documentation refer to the ADR directory instead of contradictory hard-coded counts |
| #1694 | ARC-10 | fixed | skill integrity gate consumes computedHash plus repository_skills, includes all installed skills and approved runtime Python skills; CI and ci-local invoke it; missing/tampered locks fail |
| #1694 | ARC-11 | fixed | all installed skills have Claude mirror symlinks; Windows helper resolves the repository correctly; canonical source and required Windows materialization documented |
| #1694 | ARC-12 | fixed | docs/agents/skills.md explains that retained generated-header command belongs to upstream gstack source checkout and must not be run at WebGIS root; generated upstream files are not misrepresented as local scripts |
| #1694 | ARC-13 | fixed | README uses authoritative migration/ADR paths and pnpm commands instead of drifting counts/npm commands |
| #1694 | ARC-14 | fixed | ci-local adds both .venv/bin and .venv/Scripts to PATH when present |
| #1694 | ARC-15 | fixed | .workbuddy and .zwork explicitly ignored; no dependence on incidental nested memory ignores |
| #1694 | PERF-04 | fixed | both SQLAlchemy engine constructors enable pool_pre_ping |
| #1694 | PERF-05 | fixed | spatial_tasks._compute_change_indices uses supervised run_coro_sync rather than creating/closing the calling thread's event loop |
| #1694 | PERF-06 | fixed | Explorer shares the existing supervised bridge; _BridgeLoop._ensure serializes initialization under a thread lock; concurrent-thread behavior tested |
| #1694 | PERF-07 | fixed | RedisSessionStore L1 compound operations protected by RLock; concurrent mutation regression |
| #1694 | PERF-08 | not_a_bug | durable polls use a fresh read-only Session without commit; pooled connections are reused and closed before sleeping, avoiding long transactions and stale ORM state; original commit claim is stale |
| #1694 | PERF-09 | fixed | executor governor retries use exponential 10–500ms backoff bounded by run deadline, reset after admission |
| #1694 | PERF-10 | fixed | clear_session retains locks with holders or waiters through shared _lock_in_use predicate |
| #1694 | PERF-11 | fixed | rule validity uses clock.utc_now().date(); same issue family as closed #1551 |
| #1694 | PERF-12 | fixed | template versioning and lifecycle GC use UTC-aware clock conventions; boundary-naive conversions remain explicit |
| #1694 | API-03 | fixed | SSE terminal releases the send gate immediately; per-send generation prevents an old stream finalizer from unlocking a newer send; frontend behavior regression |
| #1694 | API-04 | fixed | collaboration routes and bus reject nonfinite inbound/outbound JSON; realtime snapshot includes collaboration transport |
| #1694 | API-05 | fixed | rate-limit 429 includes Retry-After derived from the configured window |
| #1694 | API-06 | fixed | describeApiError reads structured detail/data/message and recovery action; user receives actionable transport errors |
| #1694 | API-07 | fixed | SSE serialization failure emits an explicit error envelope rather than a partial business event with missing payload |
| #1694 | API-08 | fixed | unused chat.py terminal helper removed; chat_resume owns and calls the remaining helper |
| #1694 | API-09 | fixed | WS authentication anchors inspect actual callable AST and verified dependency semantics; realtime contract regenerated from source |
| #1694 | TEST-04 | fixed | debt scanner recognizes numpy.testing assertions and excludes pytest fixtures; positive/negative temporary-project regression |
| #1694 | TEST-05 | fixed | TEST_INFRA documents backend setup, markers, isolated DBs, coverage aggregation and replay seeds; generated/local maps are not authoritative test inventory |
| #1694 | TEST-06 | fixed | seeded nightly lane targets all tests with the main lane's marker boundaries; workflow contract verifies full suite scope |
| #1694 | TEST-07 | fixed | six-shard coverage combiner validates provenance/completeness and enforces union threshold; synthetic under-threshold/missing/stale evidence regressions |
| #1694 | TEST-08 | fixed | chaos injection derives the semantic acquire-degradation anchor rather than a stale hard-coded release line |
| #1694 | TEST-09 | fixed | .coveragerc omits actual runtime skills/cache/migration/test paths; namespace packages explicitly remain in the denominator |
| #1694 | TEST-10 | fixed | offload regressions use worker/event barriers and thread identity, replacing fixed-delay assumptions; root ledger records109 passed |
| #1694 | TEST-11 | fixed | memory lifecycle tests enter the actual lifespan and verify startup and cancellation behavior rather than dead-source wiring matches |
| #1694 | TEST-12 | not_a_bug | current11 DTZ-exempt modules intentionally test naive input/DB boundaries;135 passed under UTC and135 under Asia/Shanghai; historical24 count stale |
| #1694 | TEST-13 | not_a_bug | current duplicate basenames are packaged with __init__.py and disambiguated; final complete collection remains mandatory, no demonstrated current import collision |
| #1694 | TEST-14 | fixed | distributed_lock uses a single Exception handler with warning on release failure; no redundant RuntimeError tuple |
| #1694 | OPS-05 | fixed | deploy and rollback health probes include the frontend through nginx; combined supervisor also fails the container if either service exits |
| #1694 | OPS-06 | fixed | nginx /health proxies API readiness rather than unconditional200; deployment contract tests |
| #1694 | OPS-07 | fixed | nginx applies request/connection limits and bounded client header/body/send timeouts; streaming endpoints keep their required long read timeout |
| #1694 | OPS-08 | fixed | development and production image stages use Node26 consistently |
| #1694 | OPS-09 | fixed | all three Compose stacks use bounded json-file logging; actual Compose config validation recorded |
| #1694 | OPS-10 | fixed | namespace PSA labels, explicit NetworkPolicy and API startupProbe added; cluster execution not claimed |
| #1694 | OPS-11 | fixed | worker liveness checks the local Celery process, not broker availability; acks_late remains with reject_on_worker_lost=False intentionally, since arbitrary GIS side effects are not universally replay-safe |
| #1694 | OPS-12 | fixed | Redis dangerous commands disabled; secure service drops capabilities and uses readonly root plus writable tmpfs/runtime data; upstream TLS entrypoint must be reconciled before merge |
| #1694 | OPS-13 | fixed | Docker context excludes internal agent/review/local artifact directories |
| #1694 | OPS-14 | fixed | contract and quality-e2e concurrency keys include event_name, separating manual and automated runs |
| #1694 | OPS-15 | fixed | Node26/engines and deployment health/monitoring documentation aligned to implementation; upstream Alertmanager changes retained during integration |
| #1694 | OPS-16 | fixed | ingress explicitly sets3600-second proxy read/send timeouts for long-lived streams |
| #1694 | SEC-P3-01 | fixed | HTTP GeoTIFF opens through guarded fsspec/Python Rasterio openers with per-connection validation and no redirects; remote VRT/compound archives rejected; raster behavior regressions |
| #1694 | SEC-P3-02 | not_a_bug | osascript appName originates exclusively from a fixed five-literal allowlist; no caller/browser-controlled interpolation |
| #1694 | SEC-P3-03 | fixed | step-plan example uses canonical your-api-key-here placeholder recognized by credential validation |
| #1694 | ARC-P3-01 | already_fixed | former806-line evaluate_cartography_semantics body extracted into bounded helpers on master; current semantic correctness changes tested separately |
| #1694 | ARC-P3-02 | not_a_bug | models_v3 compatibility reexport follows shared type definitions; both supported import orders execute package initialization correctly; coupling alone does not demonstrate a cycle failure |
| #1694 | PERF-P3-01 | fixed | _SessionWaveGate rejects active cross-loop reuse and rebuilds its Condition only for an idle stopped-loop handoff |
| #1694 | PERF-P3-02 | fixed | distributed lock timing uses monotonic time rather than deprecated get_event_loop().time() |
| #1694 | TEST-P3-01 | already_fixed | science-oracle contract uses explicit raise rather than asserts removable by python -O |
| #1694 | OPS-P3-01 | not_a_bug | prometheus:9090 matches the Compose service DNS alias in both production stacks; container_name is not the service alias |
| #1694 | OPS-P3-02 | not_a_bug | API HPA minimum2 exceeds PDB minimum1; single-worker voluntary eviction protection is an explicit availability tradeoff |
| #1694 | OPS-P3-03 | fixed | pin-image verifies SHA presence in registry before modifying deployment manifest; negative registry response regression |
| #1694 | DEV-P3-01 | already_fixed | domain glossary correctly names .agents/skills |
| #1694 | DEV-P3-02 | fixed | Windows mirror helper root resolution and complete mirror corrected; same evidence as ARC-11 |
| #1694 | DEV-P3-03 | fixed | outdated ten-minute inspection instructions archived in MEMORY.md; no new recurring monitor created |

Validation sources: `2026-11-05-root-issues.md`, `2026-11-05-data-issues.md`, `2026-11-05-frontend-issues.md`, the new unmapped-finding ledgers and local logs under `.planning/2026-11-05-all-issues-deep-review`. These are checkpoints, not a claim that the final integrated tree has passed. Live Docker/Kubernetes/real-service failover is unavailable in this environment and must not be represented as executed.
