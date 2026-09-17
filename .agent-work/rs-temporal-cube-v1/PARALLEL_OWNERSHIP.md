# PARALLEL OWNERSHIP — matrix vs PR #1336 and PR #1335

Diffs computed from the worktree at baseline `faa453a8`:
`git diff --name-only origin/master...origin/zcode/geoai-promptable-foundation-platform-11` (95 files, +7963/−38) and `git diff --name-only origin/master...origin/fix/harness-claim-mission-failclosed`.

## Ownership matrix

| Area / file | This direction (rs-temporal-cube) | PR #1336 (geoai-promptable) | PR #1335 (harness fail-closed) | Notes |
|---|---|---|---|---|
| `app/lib/geo_analysis/**` (temporal_cube, phenology, sar_*, rs_v3, spectral, tasseled_cap, cv, spatial_sampling, raster_change) | **OWN** (extend/new modules) | — | — | No PR touches this tree |
| `app/services/lakehouse/**` (rs_cube, cube_schema, labeled_selection, cube_service, data_object) | **OWN** (additive) | — | — | V7/V8 stable since ADR-0119/0130 |
| `app/lib/geo_raster/**` (source/reader/cog/windowed/zarr/chunk) | **OWN** (additive) | — | — | |
| `app/lib/gis/algorithms/remote_sensing.py`, `algorithms/temporal.py` | **OWN** (add descriptors) | — | — | #1336 touches only `algorithms/modelops.py` |
| `app/lib/gis/capabilities/raster.py`, `capabilities/temporal.py`, or NEW `capabilities/remote_sensing_ext.py` | **OWN** | — | — | #1336 touches only `capabilities/modelops.py` |
| `app/lib/gis/{capability_registry,algorithm_registry,algorithm_resolver,artifacts,parameter_contracts,scientific_evidence,scientific_errors}.py` | shared-stable (read; additive edits only if unavoidable) | — | — | Central registries; prefer additive domain-pack edits over registry edits |
| `app/tools/remote_sensing.py`, `app/tools/science_temporal_tools.py`, `app/tools/temporal_tools.py`, `app/tools/raster_tools_cog.py` | **OWN** (extend register functions) | — | — | |
| `app/tools/__init__.py` | **CONFLICT RISK** | **touches** (adds geoai_tools) | — | Mitigation below |
| `app/services/gis_harness/recipe_packs/{remote_sensing,sar,change_detection,temporal}.py` or new pack | **OWN** | — | — | |
| `app/services/gis_harness/skills/library/core/*.yaml` or new yaml | **OWN** | — | — | |
| `app/services/gis_harness/capability_graph.py` | **CONFLICT** | **touches** | — | Do not edit; capabilities flow in via domain packs and are projected automatically |
| `app/services/gis_harness/skills/policy.py` | read/import only | — | — | Direction 02 shipped at HEAD; #1335 does not touch policy.py but touches adjacent dirs |
| `app/services/gis_harness/{evidence_claim,hotpath_convergence,completion}/**`, `app/services/mission_runtime/**` | **DO NOT TOUCH** | — | **owns** | Hard boundary |
| `app/lib/modelops/temporal.py`, `descriptor.py`, `preprocess.py`, `evaluation.py` | import-only (reuse), avoid edits | #1336 touches siblings (`candidates/capabilities/errors/foundation/geo_prompt/metrics/multimodal/promptable`) | — | Directory-adjacency risk: merge tooling/reviewers may see modelops churn; reuse via import is safe, edits are not |
| `app/services/map_product_service.py`, `app/models/project.py` | additive only if required | — | — | MapProductVersion already supports refs via `artifact_ids`/`compute_plan`; prefer zero-edit wiring |
| `app/api/routes/geoai.py`, `app/tools/geoai_tools.py`, `frontend/**` (esp. `components/geoai`) | **DO NOT TOUCH** | **owns** | — | |
| `app/main.py` | avoid | **touches** | — | Do not add routers/middleware there |
| `tests/conftest.py` | avoid | **touches** | — | Use local fixtures in new test files instead |
| `tests/unit/gis_harness/test_capability_graph_v8.py` | avoid | **touches** | — | New capability-graph assertions go in a NEW test file |
| `pytest.ini`, `pyproject.toml` | shared-stable; only additive marker if truly needed | — | — | Prefer existing markers |
| `CHANGELOG.md`, `docs/adr/` | shared-append (conflict-prone but unavoidable for ADR/CHANGELOG) | #1336 touches CHANGELOG.md | — | Append-only, low merge risk |

## #1336 touched files (code subset)

`app/api/routes/geoai.py`; `app/lib/gis/algorithms/modelops.py`; `app/lib/gis/capabilities/modelops.py`; `app/lib/modelops/{candidates,capabilities,errors,foundation,geo_prompt,metrics,multimodal,promptable}.py`; `app/main.py`; `app/services/gis_harness/capability_graph.py`; `app/services/modelops/**`; `app/tools/__init__.py`; `app/tools/geoai_tools.py`; `frontend/{app/geoai,components/geoai,lib/i18n,messages}/**`; `tests/conftest.py`; `tests/{integration/unit}/modelops/**`; `tests/unit/gis_harness/test_capability_graph_v8.py`.

## #1335 touched files (complete)

`app/services/gis_harness/completion/pipeline.py`; `app/services/gis_harness/evidence_claim/{census,grounding,verify}.py`; `app/services/gis_harness/hotpath_convergence/{claim_ingest,pi_card,session_ctx}.py`; `app/services/mission_runtime/{service,store}.py`; `tests/unit/gis_harness/test_{evidence_claim_graph_v1,hotpath_convergence_v1}.py`.

## Required-by-us files that they touch → stable adapter seams

1. **`app/tools/__init__.py` (#1336)** — we must register new tools. Seam: **do not add a new module to `_TOOL_MODULES`; extend `register_rs_tools` in `app/tools/remote_sensing.py` (or `register_science_temporal_tools` in `app/tools/science_temporal_tools.py`)** with the new tool definitions. Both modules are untouched by #1336/#1335. If a separate module is cleaner, a lazy re-export inside `app/tools/remote_sensing.py` (`from app.tools.rs_temporal_fusion import register_rs_temporal_fusion_tools`) keeps `__init__.py` frozen.
2. **`app/services/gis_harness/capability_graph.py` (#1336)** — we must make new capabilities visible. Seam: none needed — the graph is a *projection* rebuilt from `CapabilityRegistry` + `AlgorithmRegistry`; adding `CapabilityDescriptor`s in a domain pack (`app/lib/gis/capabilities/...`) and `AlgorithmDescriptor`s in `app/lib/gis/algorithms/remote_sensing.py`/`temporal.py` flows through automatically. Graph-level assertions go in a NEW test file (not `test_capability_graph_v8.py`).
3. **`tests/conftest.py` (#1336)** — no repo-wide fixtures. Seam: self-contained fixtures in the new test files (repo convention already favors inline numpy fixtures).
4. **`app/lib/modelops/` imports (#1336 sibling churn)** — we want `SAR_POLARIZATIONS` (`app/lib/modelops/temporal.py` L20) and `spatial_blocked_split` (`app/lib/modelops/evaluation.py` L265). Seam: **import, never edit**; if a constant is needed locally, define it in our own module and note the provenance, so a #1336 rename cannot break us at merge time (wrap import in try/except with local fallback is acceptable but only for constants, not behavior).
5. **SkillPolicy / hotpath / mission_runtime (#1335)** — our skill YAML + policy integration must go through the *existing* data-driven seams: `skills/library/core/*.yaml` (skill id + capability_requirements) and `capability_requirements` ids validated against `CapabilityRegistry` (see `UBIQUITOUS_LANGUAGE.md` "Relationships"). No code edits in policy.py / hotpath_convergence / mission_runtime.
