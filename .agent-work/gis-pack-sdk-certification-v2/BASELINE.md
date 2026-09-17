# BASELINE — gis-pack-sdk-certification-v2

## Checkout

- Worktree: `C:/Users/wangj.KEVIN/projects/webgis-wt-extension-sdk-v2`
- HEAD: `faa453a8935101378c23eb6694a42c3616d9c670` (origin/master)
- Date: 2026-09-15 20:46:56 +0800
- Subject: `feat(harness): Hot-path Convergence — Mission × SkillPolicy × Evidence (Direction 04) (#1329)`
- Working tree clean at recon time (no local modifications made by Phase 0).

## Key recent merges relevant to extensions / capabilities / skills

| SHA | PR | What |
|---|---|---|
| faa453a8 | #1329 | Hot-path Convergence: Mission × SkillPolicy × Evidence (Direction 04) — touches `app/services/gis_harness/hotpath_convergence/`, `mission_runtime/` |
| b44c1c9b | #1328 | Spatial Evidence / Claim / Provenance Graph (Direction 03) |
| 14a47cc6 | #1327 | Production GIS Skill Policy & self-evolving procedure runtime (Direction 02) — `app/services/gis_harness/skills/policy.py` (SkillPolicy), `promotion.py` |
| 87829572 | #1320 | Durable GIS Mission Runtime & Distributed Harness Control Plane (Direction 01) |
| d4480ca6 | #1321 | Cartography feedback evaluation |
| e21314a5 | #1326 | fail-closed durability fixes #1322–#1325 |
| 017d1d41 | — | **Windows-relevant**: EOL-invariant artifact-graph fingerprints (autocrlf checkouts broke staleness gate) — precedent for any content-hash we add: must be EOL/`__pycache__`-safe like `discovery.compute_fingerprint` |

The extension platform itself landed earlier: ADR-0104 (V1, PR branch `feat/gis-extension-platform-v1`), ADR-0105 (V2, `feat/extensions-v2-isolated-runtime`, 2372 platform tests), ADR-0119/0128 (V3 secure ecosystem: Ed25519 trust store, marketplace, distribution, bubblewrap, streaming), ADR-0131 (V4 production control: extension Prometheus metrics in `app/extensions_platform/metrics.py`).

## Environment notes

- OS: Windows 10.0.26200 x64, shell = Git Bash. Paths in commands must be absolute.
- Python: 3.13.9 (Anaconda, `C:/ProgramData/anaconda3/python`). pytest, pytest-asyncio, pytest-cov, pytest-timeout installed. **pytest-xdist is in requirements-dev (>=3.8.0, CI gate uses `pytest tests/unit -q -n 2`) but NOT installed in this local interpreter** — `import xdist` fails; run suites serially locally or `pip install pytest-xdist`.
- `bubblewrap` (bwrap) is Linux-only: on Windows the only worker isolation backend that can actually run is `process`. Never write a test that requires bwrap without a skip.
- Resource rlimits in `app/extensions_platform/worker/spawn.py` are POSIX (`resource` module): on Windows budget enforcement degrades to wall-clock timeouts only (typed degrade is by design; tests must mirror `tests/unit/extensions_platform/test_resource_limits.py` skip posture).
- Repo root has `skills-lock.json` — that is the **ZCode CLI agent-skill lock** (developer tooling), NOT a runtime artifact. The product runtime never reads it (this is audit issue #1337).

## Test-baseline status

- Targeted smoke (this machine): `python -m pytest tests/unit/extensions_platform/test_manifest.py tests/unit/extensions_platform/test_certification.py -q --no-cov` → **34 passed in 5.47s**.
- Full platform suite run on this machine: `python -m pytest tests/unit/extensions_platform -q --no-cov` → **2489 passed, 3 skipped, 8 failed in 125.85s**. All 8 failures are Windows-environment-caused, not code defects:
  - `test_worker_integration.py::TestWorkerLifecycle::test_activate_call_deactivate_roundtrip`, `test_worker_projection_v3.py::test_worker_v3_projection_all_sections`, `test_review_r2_fixes.py::TestSecretsNotReadableViaSettings::test_worker_settings_skip_env_file_and_env_channel`, `test_lifecycle_v3.py::test_drain_timeout_typed_and_refuses` — worker subprocess handshake fails with `OSError: [WinError 10106]` (socket provider init blocked for spawned processes in this environment).
  - `test_resource_limits.py` memory/cpu rlimit tests — POSIX `resource` module only.
  - `test_streaming_v3.py::test_bwrap_command_never_binds_repo_root` — bwrap/Linux layout assumptions.
  - Consequence for planning: the **worker path cannot be integration-tested as-is on this Windows box**; certification-pipeline tests must either mock `WorkerProcess`/spawn or mark worker-integration cases to self-skip like `test_resource_limits.py` does. CI (Linux) runs these green.
- Conformance corpus (`conformance.py`) generates 2000+ deterministic cases; entry = `tests/unit/extensions_platform/test_conformance_corpus.py`.
- pytest.ini facts that matter for new tests:
  - `testpaths = tests`, `pythonpath = .`, `asyncio_mode = auto`, `timeout = 60` (`timeout_method = thread`), `asyncio_default_fixture_loop_scope = function`.
  - Default `addopts` includes `--cov=app` → always pass `--no-cov` for local speed; coverage gate is CI-side (`--cov-fail-under`).
  - Markers: `heavy` (geopandas/numpy/rasterio; CI runs), `perf` (isolated lane, self-skips in unfiltered runs), `cartography` (release gate), `real_services` (REAL_SERVICES=1 only).
  - New tests belong in `tests/unit/extensions_platform/` (that dir has its own `conftest.py`); integration-style pack lifecycle tests copy the example pack pattern from `tests/unit/extensions_platform/test_example_pack.py`.
