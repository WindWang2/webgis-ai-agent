# BASELINE — rs/temporal-cube-sar-optical-v1

## Worktree / SHA

- Worktree: `C:\Users\wangj.KEVIN\projects\webgis-wt-rs-temporal-cube-v1`
- Branch: `rs/temporal-cube-sar-optical-v1`, checked out from `origin/master`
- Baseline SHA: `faa453a8935101378c23eb6694a42c3616d9c670` (verified via `git rev-parse HEAD`)
- Commit date: 2026-09-15 20:46:56 +0800
- HEAD subject: `feat(harness): Hot-path Convergence — Mission × SkillPolicy × Evidence (Direction 04) (#1329)`
- Working tree: clean at survey time (no local modifications)

## Test-env notes (verified on this machine)

- Python: 3.13.9 (anaconda3, `python` on PATH). `pyproject.toml` declares `requires-python = ">=3.12"`.
- pytest config: `pytest.ini` (repo root), key facts:
  - `testpaths = tests`, `pythonpath = .`, `asyncio_mode = auto`
  - `timeout = 60`, `timeout_method = thread` (pytest-timeout)
  - `addopts = --ignore=tests/smoke-test-buffer.py --ignore=tests/smoke_deep_enhancement.py --cov=app --cov-report=term-missing`
    → plain `pytest` runs WITH coverage; fast targeted runs in this repo conventionally use `-o addopts=` or `--no-cov -q` (see `review/PRODUCTION-SKILL-POLICY-REVIEW.md` evidence blocks: `pytest tests/unit/gis_harness/test_skill_policy_v1.py -o addopts= → 25 passed`).
  - Markers (pytest.ini `markers=`): `heavy` (geopandas/numpy/rasterio deps; CI runs them), `perf` (isolated lane, self-skips in unfiltered runs), `cartography` (release-blocking gate, no Node/LLM/network), `real_services` (armed only by `REAL_SERVICES=1`).
- ruff: configured in `pyproject.toml` `[tool.ruff]`; default E4/E7/E9 + F selection, E501 intentionally NOT selected. Invoke as `ruff check .` (or `python -m ruff check <paths>`).
- No pytest `-n` (xdist) usage found in CI workflows; CI lanes use e.g. `pytest tests/integration/chaos -m heavy --no-cov -q` (`.github/workflows/quality-e2e.yml:179`).
- Windows + Git Bash environment; heavy rasterio/numpy stack IS importable here (science tests run locally, e.g. science-v5 suites).

## Repo state / recent themes (git log --oneline -50)

The last ~50 commits are dominated by five merged "harness" directions and an agent-swarm train:

1. **Harness Directions 01–04 (most recent, #1320/#1327/#1328/#1329)**: Durable GIS Mission Runtime; Production GIS Skill Policy + self-evolving procedure runtime; Spatial Evidence/Claim/Provenance Graph; Hot-path Convergence (Mission × SkillPolicy × Evidence). HEAD is Direction 04.
2. **agent-swarm v1 train (#1300–#1317)**: VLM visual critic, visual self-healing mapspec, specialist packs (data-compute / carto-auditor), proactive spatial memory, trajectory-to-skill induction (ADR-0191), spatial causal simulation (ADR-0192), counterfactual what-if (ADR-0193), anti-hallucination guardrails (ADR-0195), storymap orchestrator (ADR-0196), mixed-initiative canvas (ADR-0194).
3. **adaptive-data-supply v1 (DS0–DS9, ADR-0170–0179)**: data_fabric contracts (D1–D4), source registry + 4 new adapters incl. **cog_adapter** (rasterio header-only) and **stac_adapter V2**, acquisition planning, fallback chains, version pinning/drift, semantic time parsing, local asset index, observability.
4. **Post-merge convergence fixes**: EOL-invariant artifact fingerprints (autocrlf), CI green after 19-PR merge train.
5. Science line (science-v3/v4/v5) landed earlier; **science-v5 W8 already shipped `temporal_cube.py` + `phenology.py` + `science_temporal_tools.py`** (see GAP_ANALYSIS.md).

## Open PR / issue landscape (2026-09-16)

- Open PRs (only 2, both HARD BOUNDARIES):
  - **#1336** `zcode/geoai-promptable-foundation-platform-11` — "GeoAI promptable foundation platform 11 (ADR-0198)": owns `app/lib/modelops/{candidates,geo_prompt,multimodal,...}`, `app/services/modelops/*`, `app/api/routes/geoai.py`, `app/tools/geoai_tools.py`, `frontend/components/geoai/*`, plus `app/services/gis_harness/capability_graph.py`, `app/lib/gis/{algorithms,capabilities}/modelops.py`, `app/tools/__init__.py`, `app/main.py`, `tests/conftest.py`. ~95 files, +7963/−38.
  - **#1335** `fix/harness-claim-mission-failclosed` — fail-closed claim verify / tenant scope / mission ownership: owns `app/services/gis_harness/{completion,evidence_claim,hotpath_convergence}/**`, `app/services/mission_runtime/{service,store}.py`, `tests/unit/gis_harness/test_{evidence_claim_graph_v1,hotpath_convergence_v1}.py`.
- Open issues: audit-tracking issues #1343–#1350 (dependency/CVE/frontend/backend/toolchain hygiene). None RS-cube-specific.
- `.agent-work/` already contains per-direction folders (science-v5, hotpath-convergence-v1, production-skill-policy-v1, …) — this directory follows that convention.

## ADR numbering

Latest ADRs: 0193 (what-if branching), 0194 (mixed-initiative canvas), 0195 (anti-hallucination guardrails), 0196 (storymap orchestrator), 0197 (durable mission runtime). #1336 will take **ADR-0198** (per its PR title). Next free number for this direction: **0199+** (verify at PR time).
