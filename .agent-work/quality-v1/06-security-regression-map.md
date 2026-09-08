# SECURITY REGRESSION MAP — webgis-ai-agent-quality-v1

Date: 2026-09-08 · Method: static code read + test read. No tests executed, nothing exploited.
Scope: `app/` (FastAPI backend) vs `tests/` (~793 files). All citations `file:line` relative to repo root.

## 1. Executive summary

The backend has a **mature, layered security implementation** with unusually good traceability: controls carry audit IDs (SEC-03/04/05/08, SEC-F1/F3/F4/F6, S28–S50, #374/#616/#757/#758/#933/#1109/#525/#677) and most fixes landed with a co-located regression test. The strongest layers are (a) session-ownership/IDOR guards with a real e2e cross-tenant matrix, (b) the data-fabric SSRF stack (pre-flight validate + per-hop adapter revalidation + offline redirect fixture), and (c) job-payload redaction.

The regression platform's biggest problems are **coverage shape, not coverage volume**:

1. **One fake-coverage test file**: `tests/test_api_path_traversal.py` re-implements `report.py:_validate_file_path` inside the test and never imports the route code — it can pass while the real helper regresses. This is the only outright "smoke masquerading as security" finding.
2. **authZ is well tested, tenancy is patchy**: the integration matrix (`tests/integration/test_cross_tenant_isolation.py`, 565 lines) covers sessions/map-state/tasks/uploads/reports/chat — but **not projects, layers, templates, knowledge docs, or data-fabric sources at the route level**.
3. **Error oracle is thin**: 4 tests total, one endpoint (chat), and the `is_production()` branch that gates traceback/`error_detail` egress (`app/core/exception.py:133`) has no e2e test.
4. **Redaction is denylist-based** where it feeds the DB (`app/services/jobs/redaction.py:38-56`); near-miss keys (`auth`, `api-key`, value-embedded JWTs) are not covered and only the geocompute tracer uses the stronger allowlist pattern (`app/services/geocompute/tracing.py:44-52`).
5. Several **"guards that read config at use-time"** (probe-time `allow_private` from stored profiles, `AUTH_DISABLED` bypass, `DATA_FABRIC_LOCAL_FILE_ROOTS`) are one schema/validator change away from silent bypass and lack pinning tests.

Top-line risk ranking and 15 concrete gaps with attack scenarios are in §4.

---

## 2. Control-area matrix (14 areas)

Legend: **Impl** = where control lives. **Tests** = files exercising it. **Quality**: REAL = asserts deny/fail with real code under test; SMOKE = duplicated logic, happy-path only, or static scan only. **Gaps** = what's missing.

### (1) Tenant isolation (org)
- Impl: `app/models/db_model.py:29,55,78,109` (org_id FKs on users/layers/tasks/templates); `app/core/auth.py:459-464` (org_id from DB row in `get_current_user_with_version`); `app/core/auth.py:90-104` (`actor_ids` collapses anonymous sentinels — the `user.get("id")` bug class); `app/services/project_service.py:23-63` (`_caller_may_access_project`: owner XOR same-org); data-fabric catalog: `authorize_catalog_item` (exercised via `tests/unit/test_data_fabric_security.py:99-120`).
- Tests: `tests/integration/test_cross_tenant_isolation.py` (real route-level 404 matrix: session detail/messages/map-state/tasks/uploads/reports/chat-stream/delete, lines 264-524); `tests/test_zero_review_authz.py:37-66` (owned project unreadable by other user; anonymous list excludes owned rows; `require_tenant_owned` blocks foreign owner without org claim); `tests/unit/test_data_fabric_security.py:99-127` (catalog cross-tenant deny, anonymous blocked for owned source); `tests/unit/test_data_fabric_security_v2.py:21,77` (catalog + connection-manager owner isolation).
- Quality: REAL.
- Gaps: (a) projects/layers/templates/data-fabric sources absent from the integration matrix — the only cross-tenant project tests are unit-level; (b) no test that a user in org A cannot list org-B layers; (c) `actor_ids` normalization is tested (`test_zero_review_authz.py:15`) but no canary asserts new routes call it rather than `user.get("id")` (the exact historical bug).

### (2) Project isolation
- Impl: `app/services/project_service.py:23` (`_caller_may_access_project`), applied in `get_project_with_auth:116`, `get_project_context_summary:132`, `get_project_fingerprint:235`, `list_projects:293-316` (`owner_id == user OR owner_id IS NULL` branches), `update_project:329`, `attach/detach_dataset` (routes `app/api/routes/project.py:162-195` return 404 on deny).
- Tests: `tests/test_zero_review_authz.py:37-53`; `tests/unit/test_project_api.py`; `tests/test_round2_review_findings.py` (project guard fixes).
- Quality: REAL (unit/service layer).
- Gaps: no route-level cross-tenant project test (GET/PUT/datasets/artifacts/workflows under `app/api/routes/project.py`); no test that `owner_id IS NULL + org mismatch` is denied at the list endpoint (the list branch at `project_service.py:309-316` is a different code path from the row gate — only the row gate is tested).

### (3) Session refs / IDOR on session & chat endpoints
- Impl: `app/core/auth.py:499-522` (`verify_session_owner`, uniform 404), `:525-541` (`require_owned_session` FastAPI dep), `:107-135` (`authorize_session_write` — SEC-08 owner_token + **#1109 fail-closed for legacy NULL/NULL rows**, constant-time compare :132), `:492-496` (`get_owner_token` X-Session-Token). Guard used in 13 route files (chat 18 sites, layer 12, upload 8, jobs 5, geocompute 5, task/report/raster/project/explorer/data_fabric/mapspec_mutations/analysis_graph 2-4 each — counts from grep). WS: `app/api/routes/ws.py:30-90` (token required, ownership checked, ver-checked per #758, subprotocol token per #757).
- Tests: `tests/test_sec08_session_owner_token.py` (REAL e2e: new anon session mints token; no/wrong token → 404; legacy NULL/NULL fail-closed); `tests/test_sec08_uploads_session_token.py` (same matrix for uploads GET/geojson/DELETE); `tests/test_upload_ownership_matrix_1109.py` (7-case matrix + migration mints tokens); `tests/test_session_ownership.py`; `tests/test_ownership_guard_light_525.py` (guard uses metadata query — perf-shaped security test); `tests/test_ws_auth.py` (9 tests); `tests/test_critical_auth_hardening.py:261-357` (S31/S32/S33); `tests/integration/test_cross_tenant_isolation.py` (full matrix).
- Quality: REAL — this is the model control area.
- Gaps: new route files are not forced to use the guard; the AST authN scan (`tests/test_api_auth_required.py`) checks *authentication*, not *ownership*. A route accepting `session_id` without `verify_session_owner` would pass authN scanning. Suggest a contract test enumerating all routes with a `session_id` path/query param and asserting guard presence.

### (4) Artifact ownership
- Impl: `app/services/artifact_registry.py:521-532` (`get_artifact(session_id, artifact_id)` — **no owner parameter**; isolation is delegated to callers' session guards), `:361` register, `:535` list (session-scoped). Project-scoped artifacts go through `ProjectService.list_project_artifacts` (owner gate) behind `app/api/routes/project.py:210`.
- Tests: functional only — `tests/data/test_artifact_contract.py`, `tests/data/test_gc.py`, `tests/unit/test_raster_artifact_v4.py`, `tests/test_deep_explore_owner_chain.py` (owner-chain, partial). Ref-payload cache correctness: `tests/unit/test_data_fabric_security_v2.py:179`.
- Quality: PARTIAL — no test asserts a foreign session/user cannot read or mutate another session's artifact via any route; no test that `project.py:210` list applies `_caller_may_access_project` (it passes user/org into the service — unverified behavior).
- Gaps: add route-level IDOR tests for `/projects/{id}/artifacts` and `get_artifact_lineage` (`project.py:1068`); add a registry-level unit test documenting that `get_artifact` MUST only be called post-guard (or add an owner check).

### (5) SSRF (outbound fetchers)
- Impl: `app/services/data_fabric/security.py` — `validate_url:95-191` (scheme allowlist incl. postgres/s3 :117, blocked hostnames :31, `.local`/`.internal` :156-158, literal-IP + **all A/AAAA resolved** :165-189, IPv4-mapped IPv6 :87-89, metadata IPs :40-43); `SSRFSafeHTTPAdapter:316-331` re-validates **every hop incl. redirects**; `make_safe_session:334`; `bounded_get:350-387` (decompression-bomb byte cap, min floor :347); `resolve_safe_local_path:399`; `ensure_same_origin_url:464-489` (cursor/next-link credential-exfil defense); `redact_url:217`. Probe-time revalidation `app/services/data_fabric/connection_manager.py:96,246,262`. Startup/runtime config URLs: `app/core/config.py:322-436` (`_validate_external_urls`, `_validate_no_ssrf`), enforced at runtime in `app/api/routes/config.py:88-93,145-148`. Create path forces `allow_private=False` (`app/api/routes/data_fabric.py:326-342`). Probe/preview/data endpoints require auth (`data_fabric.py:460-493,706-746`). OSM/POI/geocode/crawler fetchers use **fixed provider endpoints** (nominatim/overpass/DDG/qianfan — `app/tools/web_crawler.py:35,94`, `app/services/local_osm.py`) with no user-supplied URL.
- Tests: `tests/test_connection_manager_ssrf.py` (7 REAL deny tests incl. IPv6 loopback, postgres scheme, allow_private escape hatch); `tests/unit/test_data_fabric_security.py:20-47` (adapter blocks metadata IP **on send**, both schemes mounted); `tests/unit/test_data_fabric_security_v2.py:211` (s3 endpoint gate, F-5); `tests/fixtures/data_fabric/fake_server.py` (offline transport that subclasses `SSRFSafeHTTPAdapter` — `302 → 169.254.169.254` exercises real redirect→revalidate); `tests/test_config_routes.py:253` (LLM-test SSRF rejected e2e); `tests/test_config.py` (startup validation); `tests/test_web_crawler.py` (15, prompt-injection escaping mostly).
- Quality: REAL.
- Gaps: (a) probe/query-time `allow_private` is read back from the **stored** profile (`data_fabric.py:485,836` → `connection_manager.py:96`) — no test pins that a stored profile can never carry `allow_private=true`; (b) unresolvable hostname passes pre-flight with only a warning (`security.py:172-183`) and the s3 branch skips resolution for dot-less, port-less bucket hosts (`:126-151`) — e.g. `s3://minio-data/` behind a search domain; no test; (c) DNS-rebinding TOCTOU is documented as residual (comment :106-113, ADR-0053) — no harness pins connect-time behavior; (d) aiohttp clients (`app/core/network.py:52-64,166-200`) have **no per-hop SSRF adapter equivalent** — safe today because URLs are fixed, but nothing fails loudly if a URL arg is added to an aiohttp fetcher.

### (6) Path traversal (download/export/static)
- Impl: `app/utils/path.py:8-49` (`validate_data_path`: realpath + symlink-escape, audit S36); `app/api/routes/static.py:47-69` (`_resolve_under_data_dir`: reject `..` segments, hidden files, resolve-under-DATA_DIR) + access triple (public/ prefix, admin-only JWT per SEC-F3, HMAC signed URL via `app/core/signing.py:24-45`); `app/api/routes/report.py:40-44` (`_validate_file_path` realpath-prefix) used at :165 and :228 (download); `app/api/routes/map.py:273-300` (export download: filename sanitize + owner check with `.owner` sidecar fail-closed :282-290, SEC-10/#616); `app/api/routes/raster.py:35-90` (ref-id charset regex + resolve-escape check).
- Tests: `tests/test_path_traversal_security.py` (REAL for `validate_data_path` + upload/nature tools dispatch); `tests/test_api_path_traversal.py` (**SMOKE — re-implements the report helper in the test file, never imports route code**); `tests/test_map_export_download_auth.py` (6 REAL e2e incl. unknown-owner fail-closed and sidecar recovery); `tests/test_static_route.py`; `tests/test_raster_route.py`.
- Quality: MIXED — map export REAL; report path REAL code / **fake test**.
- Gaps: (a) rewrite `test_api_path_traversal.py` to import `_validate_file_path` from `app.api.routes.report` and hit the real download route; (b) no test for symlink-escape against the *report/static* resolvers (only `utils/path.py` is symlink-tested); (c) signed-URL expiry/tamper asserts unverified in this audit — confirm `test_static_route.py` covers bad `sig`/`exp` (static route exists; not read in full).

### (7) Archive extraction (zip slip, tar bombs)
- Impl: `app/services/data_parser.py:33-62` (`_validate_shapefile_zip`: entry-name traversal reject :40-42, required members, **uncompressed-size cap** :49-53); no `extractall` anywhere in `app/` (zip members read in memory via `zf.read`); upload body caps `app/api/routes/upload.py:135-149` (SEC-F6 cap+1 read) + `MAX_RASTER_SIZE`/`MAX_VECTOR_SIZE`; HTTP-fetched archives bounded by `bounded_get` (`security.py:350`); skill upload code validation `app/api/routes/config.py:28-41` (+ `app/tools/skills.py` `_validate_skill_code`).
- Tests: `tests/test_shapefile_upload.py:64-99` (REAL: zip without shp/dbf, **path-traversal zip rejected**, **zip bomb rejected** — real `ParseError` path); `tests/test_upload_api.py:36` (raster too large); `tests/test_skill_upload_security.py`; `tests/test_upload_delete_ordering.py`.
- Quality: REAL for the shapefile path.
- Gaps: (a) offline ingest zips read per-entry with no per-entry size cap (`app/services/local_yearbook.py:381-384`, `app/services/local_poi.py:381`) — memory zip-bomb if those loaders ever face user input; (b) no nested-archive or high-compression-ratio ratio test; (c) no test that `MAX_VECTOR_SIZE` cap is enforced when members are read (cap is checked from central directory `file_size` — a lying header vs streamed read is untested; current code never extracts to disk so impact is memory).

### (8) Query injection (SQL / PostGIS)
- Impl: PostGIS adapter `app/services/data_fabric/adapters/postgis_adapter.py` — dataset identifier validation :252-264 (`InvalidQueryError`), all user values via bound params (:376 `SET LOCAL statement_timeout`, :418-425, :437-445, :483-534), count-bbox via `%s::regclass` **parameterized literal** :541-543; predicate/temporal/spatial SQL compiled separately (`compile_predicate_sql` etc., imported :37-39); only raw `text()` in app is `SELECT 1` (`app/api/routes/health.py:26`); yearbook/POI sqlite ingestion fully parameterized (`local_yearbook.py:377-397`).
- Tests: PostGIS identifier/SQL behavior not directly asserted in read tests; `tests/unit/test_data_fabric_contract.py`, `tests/unit/test_data_fabric_services.py` (functional). No dedicated injection regression file found (grep `injection` hits prompt-injection files instead: `test_context_builder_injection.py`, `test_cartography_turn_injection.py`).
- Quality: SMOKE-to-MISSING — control appears sound by inspection, but zero tests would catch a regression to f-string table names.
- Gaps: add unit tests for identifier validation (:252-264) rejecting `schema."; DROP`-style identifiers; add a compiler test that `compile_predicate_sql` output parameterizes literals; add a test asserting no `execute(f"`/`execute(text(f"` patterns in app (AST scan, mirroring `test_api_auth_required.py` style).

### (9) Tool permission (registry enforcement)
- Impl: `app/tools/registry.py:76-127` (tier-3 contextvar: `tier3_confirmed`/`confirm_tier3`), **hard gate in dispatch** `:1209-1213` (tier>=3 refused unless enclosing context confirmed — route-level checks explicitly NOT trusted), tier>=3 forced to `SideEffectClass.DESTRUCTIVE` :629-630 (can't "forget" to mark); route: `app/api/routes/chat.py:1980-2011` (`/tools/execute` admin-only + `confirm_destructive`); Pi bridge tier>=3 hard-reject `app/api/routes/pi_tools.py:12`.
- Tests: `tests/test_critical_auth_hardening.py:363` (S30 tier-3 requires confirm), `:404` (non-admin rejected); `tests/test_tool_registry.py`; `tests/test_tool_registry_async_policy.py`; `tests/test_prompt_tool_name_integrity_438.py` (tool-name spoofing); `tests/test_pi_status_fail_closed.py` (bridge fail-closed).
- Quality: REAL.
- Gaps: no test that the **plan-mode** execution path (`app/services/plan_mode.py:664-772`, `app/tools/plan_mode.py:150-160`) cannot reach a tier-3 tool with `confirm_destructive=False` *at the registry gate* (the service pre-check `plan_mode.py:772` is tested only if a dedicated test exists — none found by name); no test that `confirm_tier3()` context cannot leak across async task boundaries (contextvar semantics unpinned).

### (10) Destructive confirmation / delete endpoints
- Impl: DELETE routes all carry either session guard or owner gate: `app/api/routes/chat.py:1854` (session), `upload.py:394` (upload, ownership matrix), `jobs.py:167` (cancel), `task.py:139,215`, `project.py:176` (detach dataset via service gate), `data_fabric.py:438` (source owner), `knowledge.py:118` (document, authN), `templates.py:351` (user template). Tier-3 destructive tools additionally require `confirm_destructive` (see #9).
- Tests: `tests/integration/test_cross_tenant_isolation.py:334` (cross-tenant delete → 404); `tests/test_upload_ownership_matrix_1109.py` (DELETE matrix); `tests/test_upload_delete_ordering.py` (file/record ordering); S30 confirm tests (#9).
- Quality: REAL for sessions/uploads.
- Gaps: **templates delete and knowledge delete authZ untested** (authN covered by `test_api_auth_required.py` AST scan only) — IDOR-on-delete scenario open; no test that task cancel at `task.py:215` checks ownership of the underlying session (guard count suggests yes, unverified by a named test).

### (11) Secret redaction (logs, traces, errors, artifacts)
- Impl: `app/services/jobs/redaction.py` — key-substring denylist :38-56, bulk-geometry summarization :59-73, hard byte caps :22-32/149-166, `safe_error` never emits traceback :191-204, `safe_dispatch_spec` refuses sensitive kwargs entirely :228-257; data-fabric profile/URL redaction `security.py:217-274` (F-2 key set incl. headers subtree); trace allowlist `app/services/geocompute/tracing.py:44-52` (**allowlist** — near-miss-proof, S-M2); error single-lining in durable jobs `app/services/geocompute/durable.py:124`.
- Tests: `tests/jobs/test_job_redaction_progress.py` (17 REAL: parametrized sensitive keys, nesting, case-insensitivity, bulk omission, byte bounds, traceback-free errors); `tests/unit/test_data_fabric_security_v2.py:196` (auth-header variants); `tests/unit/test_replay_security_v4.py:165,176` (trace drops secret-like fields, error path scrubbing); `tests/test_runtime_observability.py`.
- Quality: REAL at unit level.
- Gaps: (a) denylist misses `auth`, `api-key` (hyphen), `passwd`-adjacent variants in other modules' key sets (`security.py:255-259` has `x-api-key` but not `api-key`; `redaction.py` has `apikey` not `api-key`) — no cross-module shared key-set, no test pinning parity between the two denylists; (b) no value-level redaction (a JWT string under key `state` is stored verbatim in `parameters` up to 512 chars); (c) `logging_config.py` has **no secret-scrubbing filter** (only correlation-id filter :21-51) — any `logger.info("...%s", payload)` regression leaks to disk logs with zero test backstop; (d) nginx query-token logging covered only for WS (`#757`, `tests/test_nginx_security.py`).

### (12) Cache isolation (cross-tenant cache keys)
- Impl: ref payload cache keyed `(session_id, ref_id)` with epoch guard (`app/services/ref_payload_cache.py:35-130`); tool cache keys `tool_cache:v1:sha256(tool+args)` but **skips caching whenever any arg leaf is a `ref:` string** (`app/lib/tool_cache.py:36-66`, `_contains_ref:69-107`) — the invariant that keeps session data out of the global Redis namespace; disk artifact cache `app/lib/artifact_cache.py:71-100` keyed on source path+mtime+size+op+params (no tenant ns — benign under per-session upload dirs; intentional sharing for identical inputs); cross-process invalidation broadcast carries **no payload** (`app/services/cache_broadcast.py:82-104`).
- Tests: `tests/test_tool_cache.py:36-49` (REAL: ref strings at leaf/nested/deep levels force `None` key — this IS the isolation invariant test); `tests/unit/test_data_fabric_security_v2.py:179` (epoch guard vs stale put); `tests/test_session_l1_cache.py`, `tests/test_buffer_caching.py`, `tests/test_heatmap_caching.py` (functional).
- Quality: REAL for the tool-cache invariant; ref-payload epoch guard REAL.
- Gaps: (a) no test that `artifact_cache` keys never collide across sessions given distinct source dirs (property is implicit in path-in-key — pin it); (b) no test that the broadcast listener cannot be abused cross-tenant (it invalidates, never serves — low risk, but a test asserting `_apply_event` never returns payload data would lock the "no payload" contract); (c) `arg_size_hint_var` shortcut (`tool_cache.py:47-52`) can return a key decision without the ref walk when a hint says "not oversized" — verify a regression test exists for hint-vs-walk disagreement (none found).

### (13) Error oracle (stack traces / internal info in API errors)
- Impl: `app/core/exception.py:33-61` (`sanitize_traceback` strips paths/line numbers), `:63-111` (`format_error_response`; `include_details = not settings.is_production()` :133 decides `error_detail`/`traceback` egress), registered globally `app/main.py:286`; provider errors minimized `app/api/routes/config.py:103`; job errors single-line (`redaction.py:191`); adapter exc scrubbing `app/services/data_fabric/vector_carrier.py:81`.
- Tests: `tests/test_error_sanitization.py` (2), `tests/test_error_sanitization_v2.py` (2 — REAL e2e on chat: 500 detail must not contain exception message, isolated SQLite fixture); `tests/test_swagger_prod_disable.py` (docs off in prod).
- Quality: REAL but tiny (4 tests, one endpoint).
- Gaps: (a) the production branch (`include_details=False`) is never e2e-tested — a flip of the default or a `DEBUG` override regression ships silently; (b) no test that non-chat endpoints (data-fabric probe 502s at `data_fabric.py:746`, postgis connect errors `postgis_adapter.py:319` which embed internal host/port/db) don't leak topology; (c) `HTTPException(detail=...)` messages are passed through everywhere by design — no lint/test that details don't embed `str(e)` of underlying exceptions (several sites do, e.g. `config.py:93` includes nothing internal — good; `upload.py` includes file sizes — fine).

### (14) Rate limiting / auth bypass
- Impl: `app/core/rate_limiter.py` (Redis sliding window :28-102, memory fallback :130+, **fail-open on Redis blip** C-F10 :96-99); auth limits `app/api/routes/auth.py:146` (register 5/h/IP), `:200-207` (login: 5 fails/5min block + attempt cap), `:281-284` (refresh 30/5min/user); global middleware `app/main.py:339-382` (240 req/min); WS limit `ws.py:72-77`; IP extraction trust model `app/core/client_ip.py:19-40` (X-Real-IP → last XFF hop → peer; leftmost-XFF spoof documented as accepted tradeoff); **AUTH_DISABLED prod fail-loud** `app/core/config.py:308-319` (#756); JWT secret check `:237`; CORS validation `:297`; bypass identity is a real random-password row, not an anonymous sentinel (`app/core/auth.py:42-87`).
- Tests: `tests/test_rate_limiter.py` (4); `tests/test_critical_auth_hardening.py:121-133` (S28 register off by default), S39 login limit; `tests/test_auth_bypass.py` (REAL contract: bypass off → 401/anonymous sentinel; on → bound test-admin identity); `tests/test_token_refresh.py`; `tests/test_env_prod_template.py`; `tests/test_docker_security.py` (20: container/posture); `tests/test_nginx_security.py` (9: proxy/log hygiene).
- Quality: REAL.
- Gaps: (a) fail-open semantics mean a Redis outage disables *all* limiting — accepted (C-F10) but no test pins "fail-open must never fail-closed *for auth endpoints*" or vice versa; (b) per-IP keys inherit `client_ip_from` trust model — header-spoof bucket-rotation has no regression test documenting the accepted tradeoff; (c) no test that the global middleware applies to newly added routers (it's app-level, so structurally safe — a one-line contract test would still prevent someone mounting a sub-app).

---

## 3. Existing security test inventory (by area)

Core named files (all paths under `tests/`):

| File | Areas | Tests | Verdict |
|---|---|---|---|
| `integration/test_cross_tenant_isolation.py` | 1,2,3,10 | 20+ | REAL route-level deny matrix (565 LOC) |
| `test_sec08_session_owner_token.py` | 3 | e2e | REAL (owner_token matrix + legacy fail-closed) |
| `test_sec08_uploads_session_token.py` | 3,10 | e2e | REAL |
| `test_upload_ownership_matrix_1109.py` | 3,10 | 8 | REAL (7-case matrix + migration) |
| `test_session_ownership.py` | 3 | unit | REAL (policy layer) |
| `test_ownership_guard_light_525.py` | 3 | 4 | REAL (+perf guard) |
| `test_zero_review_authz.py` | 1,2,3 | 6 | REAL |
| `test_critical_auth_hardening.py` | 3,9,14 | 11 | REAL (S28-S39) |
| `test_api_auth_required.py` | 14 (authN) | many | REAL static AST gate + per-route e2e; authN only |
| `test_auth.py`, `test_auth_routes.py`, `test_token_refresh.py` | 14 | — | REAL (JWT lifecycle) |
| `test_auth_bypass.py` | 14 | 6 | REAL (bypass contract) |
| `test_ws_auth.py` | 3,14 | 9 | REAL |
| `test_api_path_traversal.py` | 6 | 5 | **SMOKE — duplicated helper, does not import app code** |
| `test_path_traversal_security.py` | 6 | 5 | REAL (utils + tool dispatch) |
| `test_map_export_download_auth.py` | 3,6 | 6 | REAL (fail-closed owner) |
| `test_static_route.py`, `test_raster_route.py` | 6 | — | REAL (route-level) |
| `test_shapefile_upload.py` | 7 | 8 | REAL (zip slip + bomb) |
| `test_upload_api.py` | 7,14 | 3 | REAL (size caps) |
| `test_skill_upload_security.py` | 7,9 | 1 | REAL but minimal |
| `test_connection_manager_ssrf.py` | 5 | 7 | REAL |
| `unit/test_data_fabric_security.py` | 5,1,11 | 9 | REAL (per-hop SSRF, tenant, redact_url) |
| `unit/test_data_fabric_security_v2.py` | 5,11,12 | 8 | REAL |
| `unit/test_security_p0_fixes.py`, `unit/test_security_round2.py` | 5,11 | 9+8 | REAL (historical fix regressions) |
| `unit/test_be_audit_fixes.py`, `unit/test_data_fabric_reliability*.py` | 5,7 | — | REAL (fault injection w/ fake server) |
| `fixtures/data_fabric/fake_server.py` | 5 | fixture | REAL redirect-revalidation harness (key asset) |
| `test_config_routes.py` | 5,14 | 15+ | REAL (admin gating + SSRF reject e2e) |
| `test_config.py` | 5,14 | — | REAL (startup validators) |
| `test_rate_limiter.py` | 14 | 4 | REAL |
| `test_error_sanitization.py` / `_v2.py` | 13 | 2+2 | REAL but chat-only |
| `test_swagger_prod_disable.py` | 13 | 2 | REAL |
| `jobs/test_job_redaction_progress.py` | 11 | 17 | REAL |
| `unit/test_replay_security_v4.py` | 11 | 9 | REAL (trace allowlist, path scrub) |
| `test_svg_sanitize.py`, `test_report_xss.py`, `test_monitoring_report_xss.py` | XSS | 23 | REAL (out of the 14 scope but adjacent) |
| `test_context_builder_injection.py`, `test_cartography_turn_injection.py` | prompt-inj. | 28 | REAL (LLM layer) |
| `test_tool_registry.py`, `test_prompt_tool_name_integrity_438.py` | 9 | — | REAL |
| `test_tool_cache.py` | 12 | 13 | REAL (ref-exclusion invariant) |
| `test_docker_security.py`, `test_nginx_security.py`, `test_env_prod_template.py`, `test_env_template_parity.py` | 14 (deploy) | 33 | REAL (posture) |
| `test_data_fabric_stac_truthfulness_430.py`, `test_critical_infra_hardening.py`, `test_medium_infra_hardening.py`, `test_security_audit_fixes.py` | mixed | 50+ | REAL historical-fix regressions |

Notable named gaps in the inventory: **no file** for (a) route-level project/layer/template/knowledge IDOR, (b) SQL-injection regression, (c) artifact-ownership, (d) production-mode error sanitization, (e) stored-profile `allow_private` pinning, (f) prompt-injection *egress* to tools beyond the two existing files.

---

## 4. Highest-risk gaps (ranked top 15)

1. **Report/upload path validation has no real regression test.** `tests/test_api_path_traversal.py` tests a copy of the helper. Attack scenario: refactor changes `report.py:42` `realpath` → `abspath`; `GET /reports/{id}/download` serves `report.file_path` outside `REPORT_DIR` (symlink planted via upload), leaks arbitrary files; CI stays green. Fix: import the real helper + one e2e with a planted symlink.
2. **Stored-profile `allow_private` resurrects SSRF.** `data_fabric.py:485,836` pass `allow_private` from the *stored* `connection_profile`; only the create path forces False (:342). Scenario: a future "update source" or import/migration path persists caller-supplied `allow_private=true`; every probe then dials 169.254.169.254/RFC1918. No test pins stored-profile shape.
3. **Project routes lack cross-tenant e2e coverage.** `_caller_may_access_project` (`project_service.py:23`) is the sole guard for reads, updates, dataset attach/detach, artifact/lineage listing. Scenario: new route forgets to pass `user_id/org_id` (or uses `user.get("id")` — the historical bug `auth.py:93-99` guards against) → tenant A reads/mutates tenant B's projects; the integration matrix never notices.
4. **Production error-detail branch untested.** `exception.py:133` gates traceback + `error_detail` egress. Scenario: `is_production()` detection regresses (ENV naming change) → stack traces with internal paths/DSN fragments on every 500. One e2e with `ENV=production` + forced exception would pin it.
5. **Templates / knowledge delete authZ untested.** `templates.py:351`, `knowledge.py:118` — authN verified by AST scan only. Scenario: delete-by-id without owner check → enumerable IDOR deletion (the exact #1109 class). Requires: owner matrix tests like `test_upload_ownership_matrix_1109.py`.
6. **Artifact ownership asserted nowhere.** `artifact_registry.get_artifact` has no owner concept (`artifact_registry.py:521`); safety depends on invisible call ordering. Scenario: a new convenience route `/artifacts/{id}` (or ref-resolution in a tool skipping the session guard) exposes any session's artifacts; `project.py:210`'s gate is also untested.
7. **Job redaction denylist drift.** `redaction.py:38-56` vs `security.py:255-259` use different key sets; keys like `auth`, `api-key`, `apikey_v1` edge cases vary; value-embedded JWTs stored verbatim. Scenario: job parameter `{"auth": "Bearer eyJ..."}` lands in `analysis_tasks.parameters`, readable by the task center of anyone with list access to that session row. Add shared key-set + parity test + one value-pattern test.
8. **SQL-injection regression net is empty.** `postgis_adapter.py` is parameterized today, but identifier handling (`:252-264`, `%s::regclass` `:541`) and the predicate compilers (`compile_predicate_sql`) have no adversarial tests. Scenario: "convenience" f-string table name or predicate interpolation during a refactor → blind SQLi via data-fabric query endpoint.
9. **Unresolvable-host + s3 bucket-name hole in SSRF pre-flight.** `security.py:172-183` warns-and-allows unresolvable hosts; `:126-151` skips resolution for dot-less/port-less s3 bucket hosts. Scenario (internal DNS): `s3://grafana/` or an unresolvable-at-check hostname that resolves via search domain at request time reaches an internal service; the per-hop adapter catches http(s) but the s3 path uses the S3 client, not `requests`. Add explicit tests + resolution for the s3-client path.
10. **aiohttp egress has no SSRF revalidation layer.** `core/network.py` sessions are used by provider fetchers with fixed URLs today. Scenario: any fetcher grows a URL parameter (e.g., "custom tile server") and there is no mounted guard to trip — unlike `requests` where `make_safe_session` exists. Add a canary test asserting every module that constructs `aiohttp.ClientSession` goes through `create_client_session`/`get_shared_client` (AST scan).
11. **Rate-limit fail-open + IP trust tradeoff unpinned.** `rate_limiter.py:96-99` fails open; `client_ip.py` trusts X-Real-IP. Scenario: Redis outage during credential-stuffing campaign, or direct-to-app deployment where XFF is client-controlled → limiter keyed on spoofable value; tests document neither behavior.
12. **`AUTH_DISABLED` bypass blast radius depends on one validator.** `config.py:308-319` blocks prod, and `auth.py:42-47` bypass role is **admin**. Scenario: staging-like deploy with `ENV` unset (not "production") + `.env` copied → full admin bypass. Add a test asserting `auth_bypass_enabled()` is False under prod-shaped env, and consider demoting bypass role to viewer.
13. **Plan-mode tier-3 path untested at the registry gate.** `plan_mode.py:664-772` pre-checks `tier3_steps` in service code; the backstop (`registry.py:1213`) isn't asserted for this caller. Scenario: plan executor refactor drops the service check → tier-3 (destructive/RCE-class) tool runs on user-approved-looking plan without confirmation.
14. **Trace/logging redaction has no app-wide backstop.** `tracing.py` is allowlist-based (strong), but `logging_config.py` installs no scrubbing filter and job `safe_parameters` is denylist-based. Scenario: a tool logs its full kwargs (`logger.info("args=%s", kwargs)`) → owner_tokens (`redaction.py:53` knows the key, logs don't) persisted to rotating file logs; nothing fails.
15. **No control→test traceability gate.** Security fixes reference IDs (SEC-08, #1109…) in comments, but nothing fails CI when a file carrying a guard (`verify_session_owner` call sites, `validate_url` mounts, redaction call sites) changes without its corresponding test file changing. The regression platform (§6) should add this as a first-class rule.

---

## 5. Error-oracle & redaction status (deep dive)

**Error oracle current state:**
- Global handler: `app/main.py:286` → `exception.py:114-149`. Dev: returns `error_detail` + sanitized traceback (`sanitize_traceback` strips absolute paths/line numbers, `exception.py:33-61`). Prod: generic `SERVER_ERROR` only.
- E2E coverage: 4 tests, chat endpoint only (`tests/test_error_sanitization_v2.py:51-77` asserts the 500 body does NOT contain `str(e)`; good fixture hygiene with isolated SQLite).
- Known untested leak surfaces: data-fabric probe 502 details (`data_fabric.py:746` logs internally but returns error string), PostGIS connection errors embedding host/port/db (`postgis_adapter.py:314-319`), upload errors embedding absolute paths/size (`upload.py:379`), FastAPI validation errors (422) echoing input structure (benign but never asserted).

**Redaction current state:**
- Strongest: `tracing.py:44-52` allowlist (denylist explicitly rejected in-comment as near-miss-unsafe); `safe_dispatch_spec` refuses entire payloads with sensitive keys (`redaction.py:228-257`); data-fabric `sanitize_profile_dict` treats whole `headers` subtree as sensitive (`security.py:252-268`).
- Weakest: `redaction.py` denylist (substring match) applied to everything persisted in job rows; no log-record filter anywhere (`logging_config.py`); no parity/inheritance between the three sensitive-key sets in the codebase (`redaction.py:38`, `security.py:255`, `tracing.py` allowlist).
- Test quality: redaction unit tests are strong (17 tests), but they test the function, not the *flow* — no test asserts a real job submission carrying a password in kwargs results in a redacted DB row end-to-end.

---

## 6. Recommendations for the regression platform

**Layout** (mirrors existing repo idioms; no new framework needed):
- `tests/security/<area>/` — one directory per control area (the 14 above), named `test_<area>_<control>.py`, e.g. `tests/security/session_idor/test_delete_routes_matrix.py`. Co-located historical-fix files (`test_sec08_*`, `#1109`) can stay but should be linked via a manifest.
- `tests/security/manifest.py` — machine-readable map: `CONTROL_ID → (impl files+lines, test files, last_verified_sha)`. Feeds a CI report and the traceability gate (gap 15).
- Keep the two patterns that already work here: (a) AST/static contract scans (`test_api_auth_required.py` style) for *structural* invariants; (b) offline transports/fixtures (`tests/fixtures/data_fabric/fake_server.py`) for *behavioral* attack replays.

**Naming convention:** `test__<control>__<actor>__<expected_deny>` e.g. `test__session_idor__anon_without_owner_token__404`, `test__ssrf__redirect_to_metadata__blocked_per_hop`. Encode deny expectation in the name so a pass/fail reads as a security verdict.

**Deterministic tenant-isolation testing:**
- Standardize on the existing recipe: per-test aiosqlite engine (`test_error_sanitization_v2.py:16-45` fixture is the cleanest), two seeded users with distinct `org_id`, one anon session with minted `owner_token`.
- Build one shared fixture module (`tests/security/conftest.py`) exposing `two_users`, `two_orgs`, `anon_session_with_token`, `owned_project`, `foreign_project`; every area test composes the same matrix: owner→200, other-user→404, anon-no-token→404, anon-wrong-token→404, legacy-NULL→404 (fail-closed), missing→404.
- Assert on **404 + body shape** (no existence leak), never 401/403 mixing, per `verify_session_owner` (`auth.py:505-521`).
- Add the missing matrix members: projects, layers, templates, knowledge, data-fabric sources, project artifacts/lineage (gaps 3, 5, 6).

**Add to fixtures:**
1. Path-traversal e2e fixture: tmp `DATA_DIR` + planted symlink → assert report/static download refuses (closes gap 1).
2. `allow_private` pinning fixture: assert stored `connection_profile` JSON can never contain `allow_private=true` after create/update/import (gap 2).
3. Production-mode app fixture: `ENV=production`, forced exception, assert generic body + no `error_detail`/`traceback` keys (gap 4).
4. SSRF: s3-bucket-host resolution case + an aiohttp-side canary (AST scan) (gaps 9, 10).
5. SQL canary: parametrized adversarial identifiers through `postgis_adapter` identifier validation and predicate compilers with an in-memory sqlite stand-in (gap 8).
6. Redaction flow fixture: submit a job with planted secrets in kwargs → read back `analysis_tasks.parameters/result_summary` from the test DB and assert `[REDACTED]`/absence (gap 7, closes the "function vs flow" hole).
7. Tier-3 plan-mode fixture: execute a plan containing a tier-3 step with `confirm_destructive=False` through the *registry dispatch* and assert the contextvar gate refuses (gap 13).

**Cheap wins to do first:** rewrite `test_api_path_traversal.py` (30 min, removes fake coverage), add the production-mode error test, add projects to `test_cross_tenant_isolation.py`, add the stored-profile `allow_private` pin. These four close the highest-experience-value gaps with no new infrastructure.
