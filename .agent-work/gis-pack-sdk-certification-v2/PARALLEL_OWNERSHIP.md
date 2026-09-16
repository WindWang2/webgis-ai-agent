# PARALLEL OWNERSHIP — open PRs × files × this task

Open PRs at recon (2026-09-17): #1335, #1336, #1351, #1352, #1353, #1354, #1355, #1356. **None of them touches `app/extensions_platform/`** — the pack-SDK / certification space is unclaimed. Verified via `gh pr diff <n> --name-only`.

## Overlap matrix (files this task would plausibly touch × PRs)

| This task's likely files | #1335 claim/mission | #1336 geoai | #1351 data-quality | #1352 eval | #1353 cockpit | #1354 rs-cube | #1355 spatial-events | #1356 scene |
|---|---|---|---|---|---|---|---|---|
| `app/extensions_platform/**` (certification, sdk, manifest, host — additive) | | | | | | | | |
| `tests/unit/extensions_platform/**` | | | | | | | | |
| `docs/extension-platform/**`, new ADR | | | | | | | | |
| `app/main.py` (only if new settings/lifespan wiring needed) | | X | | | X | | X | |
| `app/core/config.py` (new EXTENSIONS_* settings) | | (`.env.example` X) | | | | | | |
| `app/services/gis_harness/capability_graph.py` (read/enrich nodes) | | **X** | | | | | | |
| `app/services/gis_harness/capability_resolution.py` (read) | | | **X** | | | | | |
| `app/lib/gis/capabilities/__init__.py` (seed packs) | | | | | | **X** | | |
| `app/lib/gis/artifacts.py` (artifact-type vocab, read) | | | | | | **X** | | |
| `app/tools/__init__.py` (`_TOOL_MODULES` — we should NOT need it) | | X | | | | | | X |
| `CHANGELOG.md`, `UBIQUITOUS_LANGUAGE.md` | | X | | | | | | X |

X = PR modifies that file. Empty = no overlap.

## PR-by-PR summary

- **#1335** `fix/harness-claim-mission-failclosed`: `gis_harness/completion|evidence_claim|hotpath_convergence`, `mission_runtime/`. No overlap.
- **#1336** `zcode/geoai-promptable-foundation-platform-11`: modelops/geoai stack, `app/tools/geoai_tools.py` (new), `app/services/gis_harness/capability_graph.py` (modifies graph projection!), `app/main.py`, `tests/conftest.py`, `.env.example`, ADR-0198. **Hot**: capability_graph.py — coordinate or avoid editing; our consumption should be read-only.
- **#1351** `data/spatial-quality-harmonization-v1`: `data_quality/*`, `qualification_v8.py`, `capability_resolution.py`. Read-only consumers for us.
- **#1352** `eval/gis-agent-benchmark-factory-v2`: `app/evaluation/*` incl. `skill_policy_corpus.py`. No overlap; useful prior art for certification corpora.
- **#1353** `frontend/spatial-agent-ops-cockpit-v1`: frontend + `app/api/routes/cockpit.py` + `app/main.py`. Overlap only if we wire new routes (avoid; CLI-first).
- **#1354** `rs/temporal-cube-sar-optical-v1`: adds seed algorithms/capabilities (`app/lib/gis/capabilities/rs_cube.py`), `recipe_packs/rs_temporal_cube.py`, new tools module, `app/lib/gis/capabilities/__init__.py`. Overlap: seed-pack `__init__.py` (only if we add a seed, which we shouldn't) and `app/lib/gis/scientific_preconditions.py`.
- **#1355** `harness/event-driven-spatial-ops-v1`: new `app/services/spatial_events/*`, `app/main.py`, `tests/conftest.py`, migrations. Overlap only via `tests/conftest.py` — **avoid editing tests/conftest.py**; put extension fixtures in `tests/unit/extensions_platform/conftest.py`.
- **#1356** `cartography/multiscale-scene-intelligence-v1`: scene modules, `app/tools/__init__.py`, `app/tools/cartography.py`, `UBIQUITOUS_LANGUAGE.md`, `CHANGELOG.md`. Overlap: docs files (append-only entries are low-conflict but expect textual conflicts) and `app/tools/__init__.py` (do not touch).

## Don't-touch / hot files for this task

1. **Do not rewrite** `app/extensions_platform/host.py`, `context.py`, `manifest.py`, `loader.py` — extend additively (new modules + small hooks). Issues #1337–#1339 (P0 audit) will likely draw fixes into `app/tools/skills.py` and `host.py:152` (`allow_unsigned_dev`) — keep our diff away from those lines.
2. `app/services/gis_harness/capability_graph.py` — owned by PR #1336 right now. Project certification status as *node attributes read from host records at build time*, or defer to a follow-up after #1336 lands.
3. `tests/conftest.py` — PRs #1336/#1355 touch it. Use local conftest in `tests/unit/extensions_platform/`.
4. `app/main.py` — three PRs touch it. Prefer zero-diff wiring: reuse existing `EXTENSIONS_ENABLED` lifespan block and settings bridge (config.py additions are lower-conflict than main.py edits; still coordinate).
5. `app/tools/skills.py` — P0 audit issues #1337/#1338 target it; a future fix PR is likely. Our skill-pack work must not modify this file; it belongs to the GIS Skill Library axis (`gis_harness/skills/`), which is currently quiet.
6. `app/services/data_quality/**` (#1351), `app/evaluation/**` (#1352), `frontend/**` (#1353), `app/lib/cartography/scene_*` + `app/services/mapspec/**` (#1356), `app/services/spatial_events/**` (#1355), `app/services/mission_runtime/**` (#1335) — foreign territory.
7. `app/lib/quality/certification.py` — different "certification" (Quality platform). Never import or edit from this task.
8. `CHANGELOG.md` / `UBIQUITOUS_LANGUAGE.md` — expect conflicts with #1336/#1354/#1356; keep entries minimal and append-only.
