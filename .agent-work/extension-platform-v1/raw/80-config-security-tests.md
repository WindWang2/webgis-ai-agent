# Investigation: Config / Security / Package Loading / Tests / Docs / Lint (extension-platform-v1)

## A) Configuration — `app/core/config.py`

- Single pydantic v2 `Settings(BaseSettings)` (pydantic-settings) at app/core/config.py:10-16; singleton `settings = Settings()` at :454.
- `model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")` (:18-23). **No `env_prefix`** — env var names match field names exactly (e.g. `JWT_SECRET_KEY`, `REDIS_URL`).
- Feature toggles are plain `bool` fields: `AUTH_DISABLED` (:34, test-only auth bypass), `USE_NEW_AGENT` (:121, vendor/pi RPC host vs ChatEngine fallback; conftest pins false), `ALLOW_PUBLIC_REGISTER` (:109), `LOCAL_QUERY_FIRST` (:41), `USE_REDIS` (:174), `RAG_EMBEDDING_OFFLINE` (:180). There is no generic plugin/feature-flag registry — toggles are per-setting env vars.
- Production fail-fast `@model_validator(mode="after")` chain: JWT secret required (:236-251, dev auto-generates `secrets.token_urlsafe(32)`), placeholder `LLM_API_KEY` + Postgres-only DATABASE_URL (:253-294), CORS `*` ban (:296-305), `AUTH_DISABLED` ban in prod (:307-318), SSRF validation of OVERPASS/NOMINATIM/LLM URLs incl. DNS-resolution check (:320-435), open-register ban (:438-451).
- Env loading ownership: `app/main.py:1-6` docstring — import-time env side effects forbidden; root `main.py`/`manage.py` call `load_dotenv()`; bare uvicorn needs `--env-file .env` (contract locked by tests/unit/test_env_hygiene.py).

## B) Security

- **JWT auth** (`app/core/auth.py`): HS256 via PyJWT (`import jwt`, :23), `SECRET_KEY = settings.JWT_SECRET_KEY` (:138). Two token types: access 30min / refresh 7d with `type` + `ver` claims (:141-150); `ver` checked against `User.token_version` in `get_current_user_with_version` (:378-464). Passwords: stdlib `hashlib.scrypt` (N=2^14, r=8, p=1) format `scrypt$N$r$p$salt$key` (:152-186), constant-time verify + dummy-hash timing defense (:162-169, :189-211). Role deps: `get_current_user` (no DB), `get_current_user_with_version` (DB ver check), `require_admin` (:467-489, reads live DB role). Anonymous-session ownership via `X-Session-Token` header + `hmac.compare_digest` (`authorize_session_write` :107-135, fail-closed for legacy NULL rows).
- **Rate limiting** (`app/core/rate_limiter.py`): `RateLimiter` Protocol (:14-25); Redis sliding-window (sorted sets, unique members :90) with fail-open on Redis errors (:95-100) and MemoryRateLimiter fallback (deque, 10k key cap :130-180). `get_rate_limiter()` module singleton re-probes Redis every 60s after fallback (:183-222). Applied globally in `app/main.py` `RateLimitMiddleware` 240 req/60s keyed on real client IP (:339-382); exempt prefixes `/api/v1/layers/data/`, `/api/v1/health`, `/api/v1/local-data/` (:346-350).
- **Client IP** (`app/core/client_ip.py`): `client_ip_from` = X-Real-IP → LAST XFF hop → peer (:21-31); leftmost XFF never trusted.
- **HMAC signed URLs** (`app/core/signing.py`): `?exp=<ts>&sig=<hex hmac-sha256>` over `"{path}|{exp}"`, keyed with JWT_SECRET_KEY, default TTL 3600s, constant-time verify (:24-45).
- **/pi-tools/execute protection** (`app/api/routes/pi_tools.py`): (1) `X-Pi-Bridge-Secret` header vs shared secret, `hmac.compare_digest` (:36-48); (2) HMAC turn capability token verified with same bridge secret (`verify_turn_token`, :63-68); (3) live-turn liveness check `is_active_pi_turn` → 409 if completed/superseded (:74-89). Secret source: `app/core/bridge_secret.py:22-66` — env `WEBGIS_BRIDGE_SECRET` or `DATA_DIR/.pi_bridge_secret` created atomically (mkstemp + chmod 0600 + flock); write failure is fail-fast RuntimeError (no per-worker divergent secrets).

## C) Package loading / boot

- Lifespan (`app/main.py:38-192`): `init_db()` guard migration → cache-broadcast listener → `ToolRegistry()` + `init_tools(registry)` (:68-69) → compile + strict-validate GIS runtime manifest (fail-fast; `GIS_MANIFEST_STRICT=0` escape, :75-86) → registry injected into services layer + Pi bridge (:87-94) → `ToolCatalog` + `ChatEngine` singletons (:96-101) → if `USE_NEW_AGENT`, spawn bundled `vendor/pi` Node RPC subprocess with extension `app/extensions/webgis-tools/index.mjs` (:104-118; falls back to ChatEngine on failure) → periodic session-cleanup and stale-job-sweep asyncio tasks. Shutdown drains background tasks, closes httpx pools, kills Pi subprocess, disposes engines (:146-192).
- Startup imports are eager at module top (routes, registry, ChatEngine); many optional/lazy imports inside functions (cache_broadcast, prometheus instrumentator guarded by try/ImportError :303-309). No `pkg_resources`/`importlib.metadata`/`entry_points` usage anywhere in `app/`, `main.py`, `manage.py`, or pyproject (grep: zero hits). No `[project.scripts]`, no `[build-system]` in pyproject — package loading is plain in-repo imports (`pythonpath = .` in pytest.ini).
- Python: `requires-python = ">=3.12"` (pyproject.toml `requires-python`).
- Deps: pyproject `[project].dependencies` and `requirements.txt` are mirrored duplicates; **httpx>=0.26.0 present** (both), **pydantic>=2.13.4 + pydantic-settings>=2.14.2 (v2)** present; **lxml NOT declared** anywhere (only defusedxml/ijson for XML/JSON). Policy comments enforce direct-declaration of directly-imported libs (#618-35, rich/redis/psycopg2 incident notes). Test deps split into `requirements-dev.txt` (pytest>=9.1.1, pytest-asyncio, pytest-cov, pytest-timeout, fakeredis, openpyxl, python-calamine). requirements.txt also pins `osmium>=4.3.1` (not in pyproject).
- `vendor/` contains only `vendor/pi` (bundled Node agent host), referenced by path from `app/agent_pi_bridge.py` (spawned subprocess, not imported by Python); CI without built vendor/pi asserts dump contract only (CHANGELOG #1032 note).

## D) Tests

- `pytest.ini` (root; no [tool.pytest] in pyproject): `testpaths = tests`, `pythonpath = .`, `asyncio_mode = auto`, `asyncio_default_fixture_loop_scope = function`, `timeout = 60` / `timeout_method = thread`; `addopts = --ignore=tests/smoke-test-buffer.py --ignore=tests/smoke_deep_enhancement.py --cov=app --cov-report=term-missing` (coverage gate via CI `--cov-fail-under=75`).
- Markers: `heavy`, `perf` (isolated-run only; auto-skipped unless `-m perf`, enforced in `tests/conftest.py:pytest_collection_modifyitems`), `cartography`, `real_services` (needs `REAL_SERVICES=1`).
- Layout: flat `tests/test_*.py` (≈250 files incl. contract tests like `tests/test_ci_local_gate_contract.py`, `test_tool_meta_contract.py`) plus subdirs `tests/unit/` (373 entries; further `unit/gis/`, `unit/gis_harness/`, `unit/lib/`), `tests/integration/`, `tests/benchmarks/`, `tests/cartography/`, `tests/perf/`, `tests/jobs/`, `tests/science_oracles/`, `tests/fixtures/` (pi_mocks.py, mapspec_cow_fixtures.py, data_fabric/, runtime/). **No tests/contract/ dir** — contract tests live at tests root; no formal naming split beyond dir + issue-number suffixes (`test_event_loop_offload_386.py`, `test_issue543_*.py`).
- `tests/conftest.py` (194 lines): `_ENV_BASELINE` setdefaults every env key to Settings-default-safe values (#663-B); autouse `_pin_auth_bypass_off` (AUTH_DISABLED=False, LOCAL_QUERY_FIRST=False), autouse `_offline_embedding_model` (real SentenceTransformer load fails fast), perf-isolation skip hook.
- Conformance corpus (`tests/unit/gis_harness/test_conformance_corpus.py`): deterministic generated corpus ≥20,000 cases (59 semantic families, `build_conformance_corpus()`); full run ≈20s offline, zero LLM, in default lane; stratified sample `cases[::37]` and domain slices as fast feedback; anti-claim + workflow-contract case builders from `app.evaluation.anti_claim`; registry-coverage sweep compiles all 147 V2 recipes. No registry test fixtures/utilities beyond this — tests construct `ToolRegistry()` directly.
- CI mirror: `scripts/ci-local.sh` reproduces the CI PR lanes verbatim; alignment asserted by `tests/test_ci_local_gate_contract.py`.

## E) Docs / versioning conventions

- `docs/adr/`: 114 files, scheme `NNNN-slug.md` (0001-fetch-on-demand … ), zero-padded 4-digit numbers; **numbers are reused across multiple AIDs (e.g. three distinct 0103-* files)**. **Latest number: 0103** (0103-cartographic-design-system-v4.md, 0103-data-artifact-workspace-foundation-v3.md, 0103-pi-gis-runtime-v3.md); highest otherwise 0102-model-provider-runtime-foundation.md.
- Other docs: docs/architecture.md, DEPLOYMENT.md, SETUP_INSTRUCTIONS.md, api-docs.md, gis-harness.md, release-notes-v3.2…v3.6.md, per-domain subdirs (agent-runtime/, data-plane/, cartography/, science/, dev/, research/, reviews/).
- `CHANGELOG.md`: Keep-a-Changelog-ish — repeated `## [Unreleased] - YYYY-MM-DD` dated sections (multiple Unreleased blocks), `### Fixed/Added/Changed` subsections, entries cite issue numbers `(#NNN)`.
- `VERSION` file: `0.1.0.0` — differs from pyproject `version = "0.1.3"` and FastAPI app `version="0.1.3"` (app/main.py:280).

## F) Lint / typecheck / verification commands

- Ruff config lives only in pyproject `[tool.ruff.lint]`: `select = ["E4", "E7", "E9", "F"]`, `ignore = ["E402"]` (deliberate deferred imports). **No `line-length` setting → ruff default 88, and unenforced since E501 is not selected** (comment: full E would surface 1300+ legacy long lines). Per-file-ignores for re-export facades (F401) and F821 false positives: app/services/chat_engine.py, app/services/chat/context_builder.py, app/services/mapspec_store.py, app/services/tool_dispatch_service.py, app/services/chat/execution_engine.py, app/tools/_utils.py, app/agent_pi_bridge.py, tests/unit/test_raster_*.py.
- **No mypy/pyright config anywhere** (no [tool.mypy]/[tool.pyright], no mypy.ini/setup.cfg; ci-local.sh has no Python typecheck step — "typecheck" is frontend tsc only).
- Runnable verification commands (`scripts/ci-local.sh`, mirrors CI):
  - `ruff check --output-format=github app/ tests/ main.py manage.py`
  - `pytest tests/test_tool_meta_contract.py tests/test_subagent_context_isolation_436.py tests/test_ci_local_gate_contract.py tests/test_ci_perf_coverage_contract.py --no-cov -q` (fast contract tier)
  - Full backend: `pytest --cov=app --cov-report=term-missing --cov-fail-under=75 --timeout=60 --timeout-method=thread -m "not perf and not cartography and not real_services" -q`
  - Perf: `pytest tests/benchmarks/... -m perf --no-cov --timeout=180 -q`
  - Cartography: `pytest -m cartography --no-cov --timeout=120 -q`
  - `scripts/ci-local.sh --fast` = ruff + eslint + frontend typecheck + vitest only; `scripts/flip-red <base-ref> [pytest-args]` = guarded mutation-test (revert→prove red→restore, refuses dirty tree).
