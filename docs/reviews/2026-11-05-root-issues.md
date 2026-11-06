# Root/deployment/testing issue verification — 2026-11-05

Base: `e0e97b462`, branch `codex/all-issues-deep-review`. This ledger records evidence, not blanket aggregate closure. All three domain ledgers must be reconciled before closing cross-domain aggregates.

## Standalone and root findings

| Issue/finding | Disposition | Evidence |
|---|---|---|
| #1774 | fixed | Both API **and worker** volumes were scalars. Base volumes are now arrays; explicit `docker-compose.dev.yml` opts both services into source mounts. Real Compose v5.7 validates base and merged dev stack. CI compose config check is release-blocking. |
| #1806 | fixed | Both Dockerfiles copy lock. Loader + standalone integrity CLI fail closed on absent/invalid locks. Failed upload approval returns503 and removes unapproved file. Uploads default disabled and always disabled in production; no installed skill overwrite. Missing lock behavior test changed to rejection. |
| #1890 | already_fixed, regression strengthened | Master uses Python3.12-bookworm in every Python stage and `/usr/local/lib/python3.12/site-packages`. Docker Dependabot now ignores unsupported Python minor/major changes; independent runner image build added to CI. |
| #1891 | already_fixed | `requirements.lock` pypdf6.21.4 satisfies both requirements files. Existing dependency bounds gate checks every locked direct requirement. |
| #1914 S1-003 | fixed | Docker major/minor interpreter updates constrained. Requirements consistency already enforced by `scripts/check_dependency_bounds.py` and lock freshness job. |
| #1914 S1-005 | fixed | Government search accepts JSON independent of Content-Type; malformed individual records are logged/skipped; unknown row counts become0; valid sibling records remain. Behavioral regression added. |
| #1814 S1-036 | fixed | Runtime Python uploads default off and production rejects even if opt-in flag is set. Development requires admin + new filename + valid/writable integrity lock. No main-process sandbox claim. |
| #1814 S1-006 | fixed | Production LLM config changes and skill refresh now reject process-local mutation and instruct a consistent deployment update. Avoids secret/config persistence and split worker state. Development overrides remain process-local by design. |
| #1814 S1-007 | fixed | create-admin uses hidden getpass or `--password-stdin`; plaintext positional secret removed. Real subprocess admin/bootstrap tests pass. |
| #1814 S1-008 | fixed | Stored provider key cannot be sent to a different origin unless caller explicitly supplies a key; equivalent default ports normalized. Regression rejects cross-origin probe before network. |
| #1860 S1-012 | fixed | `get_async_db` fails before yielding when async drivers unavailable; never yields an incompatible synchronous Session. |
| #1860 S1-013 | fixed | Dev API uses non-root runner. Dev image carries Alembic entrypoint and migrations; API owns migrations, worker skips and waits for API health; writable runtime directories initialized for appuser. |
| #1860 S1-060 | fixed root portion | nginx logs omit all query strings; health proxy already real; production remote deployment probes now include real frontend through nginx. Header/UI ownership parts reviewed by core. |
| #1865 S6-005 | not_a_bug / boundary hardened | Scientific libraries never form a Python sandbox. Trusted admin+integrity lock was the intended boundary; production in-process mutation is now disabled. Third-party extensions have their separate isolation policy. |
| #1865 S6-006 | not_a_bug | turn_stats closure reads a single invocation frame, not changing outer iteration variables; `_sid=turn_sid` already binds the actual changing session loop value. File length tracked separately by closed #1544. |
| #1865 S6-007 | not_a_bug | Python preserves implicit exception context for a plain raise inside except; B904 explicit cause is a style improvement, not loss of original traceback. Do not rewrite error surfaces solely to satisfy an inactive style rule. |
| #1865 S6-008 | fixed | Geometry/record joins and terrain coordinate/count zips now strict. Scenario gather iterates returned results directly instead of zipping an unused item. |
| #1865 S6-009 | fixed | GeoAI embedding JSON read/parse and task-acceptance report directory/write moved off loop. |
| #1950 S1-006 | fixed | Legacy xls explicitly rejected with conversion guidance; xlsx read-only/data-only iteration and workbook close. Actual workbook regression verifies formulas are not returned as strings. |
| #1950 S1-007 | fixed | CORS templates and validation hint use JSON array syntax. |
| #1950 S1-008 | fixed/already_fixed | Duplicate ENV/DEBUG removed; production runner comments now match Node26. Node25+ Corepack removal comment describes when behavior changed, not current image version. |
| #1950 S1-009 | fixed | Independent PR production runner build (for image inputs) has no test-lane dependency, no image push. Release production build remains gated. |
| #1950 S1-010 | not_a_bug | Search and fetch each validate every redirect before the next request; search alone carries query params that are cleared at redirect. Local imports/duplicated loops are an advisory simplification, no demonstrated behavior defect. |

## Validation checkpoints

- 88 skill loader/registry/sandbox/CI contracts passed.
- 103 config, session cache, Explorer tasks, create-admin, Gov adapter and CI-local regressions passed.
- 49 deployment/nginx/k8s/cache/loop/config tests passed after fixing worker volumes and synchronizing embedded nginx.
- 109 offload/lifecycle/environment tests passed. Fixed timing races retain assertions on actual worker execution thread. Lifespan task tests now enter the real context and verify start plus cancellation cleanup.
- 98 guarded-raster/COG/STAC/config/Gov/root regressions passed after TIFF fixture correction and declared fake-host DNS fixture fix.
- All four actual Compose configurations validate: dev, dev+source overlay, prod, secure. Downloaded official Docker Composev5.7 binary SHA-256 verified. This environment has no Docker daemon/Kubernetes cluster; no live image/cluster deployment claim.
- Mypy passes all29 core allowlist files. Ruff, debt ratchet and full suite must be rerun after domain edits settle.
- uv.lock refreshed after numexpr>=2.8.5 and rasterio>=1.4 declarations; pins unchanged. [Numexpr upstream release notes](https://github.com/pydata/numexpr/blob/master/RELEASE_NOTES.rst) document input validation added in2.8.5.

## Remote raster transport

#1694 SEC-P3-01 is implemented through public Rasterio Python openers, rather than declaring Python-level protection impossible. HTTP COGs use the guarded fsspec range client with no redirects, finite timeout and explicit client teardown. Remote content is GeoTIFF-only so VRT cannot introduce native remote references. Compound remote archives reject before fallback. Reader, COG adapter/materialization/export/tile and STAC lifetimes route through this seam. [Rasterio opener API](https://rasterio.readthedocs.io/en/stable/topics/vsi.html#python-file-and-filesystem-openers). Further broad raster regressions pending.


## Additional root verification

- #1694 TEST-12: current11 DTZ-exempt test modules execute identically under TZ=UTC and TZ=Asia/Shanghai:135 passed in each. Naive fixtures explicitly exercise database/input boundary semantics. Exemption count24 was stale; no demonstrated current TZ-dependent assertion. Both runs disclose an existing unclosed aiohttp-session teardown warning for later lifecycle review.
- #1694 TEST-13: duplicate basename packages are currently disambiguated by package initialization, and complete suite collection must remain verified. Changing every test import mode is not justified solely by a hypothetical future collision; no current collision demonstrated.
- #1694 ARC-P3-01: largest semantic-check helper is271 lines; former806-line monolith was extracted on master. chat_stream is380 vs original964; lifecycle apply_mutation571 vs1520. ARC-05/ARC-07 explicitly duplicate closed #1544/#1549 (GitHub closed state verified2026-11-05).
- #1694 SEC-P3-02: osascript appName comes exclusively from a fixed five-string literal list, not caller/browser content. Shell interpolation has no attacker-controlled input here; injection report is not reproducible.
- #1694 OPS-P3-01: Grafana URL prometheus:9090 matches the Compose service alias prometheus in both production stacks; container_name differs intentionally and DNS resolves the service alias.
- #1694 OPS-P3-02: API HPA minReplicas2 is strictly greater than PDB minAvailable1. Single-worker eviction protection is intentional; production availability tradeoff remains documented.
- #1694 DEV-P3-01 already corrected to .agents/skills in glossary. DEV-P3-02 Windows mirror root resolution fixed and all99 skills mirrored. DEV-P3-03 old ten-minute inspection rules explicitly archived and no monitor/automation created.

- #1860 S1-059 migration concurrency: Alembic online migrations acquire a PostgreSQL session advisory lock with bounded lock wait before reading/running revisions; held across autocommit blocks, released in finally. Session lock acquisition transaction is committed before Alembic configuration so the migration transaction still commits. Multiple replica initContainers therefore serialize rather than race the version table. SQLite/direct uvicorn unchanged.
