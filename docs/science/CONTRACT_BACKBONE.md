# Spatial Science Contract Backbone — Implementation Reference (ADR-0099)

> This document is the working reference for domain-pack implementers. It
> describes the typed scientific contracts added by the VNext backbone and
> the exact recipe for registering a new scientific algorithm. Read this
> before touching any domain module.

## 1. Layer responsibilities (do not blur)

```text
Capability (what) → Algorithm (which method) → Tool/Implementation (who)
→ ToolRegistry dispatch → GeoCompute (where) → Artifact + evidence
```

- Registry layer (`app/lib/gis/**`) holds **method semantics only** — no
  execution, no data loading, no second runtime.
- Library layer (`app/lib/geo_analysis/**`, `app/services/{rs,network,temporal}/**`)
  holds **implementations** (numpy/scipy/shapely/...).
- Tool layer (`app/tools/**`) is a thin wrapper: **validate → resolve refs →
  call implementation → attach evidence → return bounded result**. A tool
  must never become a second algorithm implementation.

## 2. AlgorithmDescriptor VNext fields (all optional, all consumed)

Declared in `app/lib/gis/algorithm_registry.py`. Beyond the legacy fields:

| Field | Type | Consumer |
|---|---|---|
| `algorithm_family` | str (snake/dotted) | docs generation, family grouping |
| `method_references` | `List[str]` ≤8 | must resolve in `method_references.py` |
| `assumptions` / `limitations` | `List[str]` ≤8×160ch | evidence blocks |
| `crs_class` | one of `CRS_AGNOSTIC / GEOGRAPHIC_OK / PROJECTED_REQUIRED / LOCAL_METRIC_REQUIRED / GEODESIC / RASTER_GRID` | resolver hard gate (`crs_safety.crs_class_allows`) |
| `scientific_preconditions` | `List[str]` ≤8 | resolver gate; ids from `scientific_preconditions.py` |
| `uncertainty_outputs` | `List[str]` ≤6 | ⊆ `uncertainty.UNCERTAINTY_TYPE_VOCABULARY` |
| `random_seed_policy` | `deterministic / fixed_seed / caller_seeded / unseeded / none` | consistency-checked vs `deterministic` flag |
| `numerical_tolerance` | str ≤160 | evidence/docs |
| `scientific_status` | `EXPERIMENTAL / VALIDATED / PRODUCTION / DEPRECATED` | PRODUCTION requires native+contract+refs+conformance tests; VALIDATED requires conformance tests; DEPRECATED requires fallback |
| `conformance_tests` | `List[str]` ≤8 pytest node ids (`tests/...py::test_x`) | file existence checked in repo checkouts |
| `backend_variants` | `List[BackendVariant]` ≤4, backend ⊆ `BACKEND_VOCABULARY` | implementation variants |
| `fallback_semantics` | `Dict[target, equivalent/approximation/proxy/degraded/not_allowed]` | resolver fallback trail; every `fallback_algorithms` entry MUST have one |

Existing preconditions vocabulary: `numeric_field_required`,
`nonzero_variance_required`, `projected_crs_required`,
`local_metric_crs_required`, `temporal_field_required`,
`min_temporal_observations:N`, `raster_band_required:N`,
`band_semantics_required`, `point_support_required`,
`positive_weights_required`, `min_numeric_samples:N`.
If you need a new precondition id, **report it — do not edit
`scientific_preconditions.py` yourself** (central file).

## 3. Parameter contracts

`app/lib/gis/parameter_contracts.py` — typed specs (type/default/min/max/
enum/unit/data-dependent rule). Contracts live **in your domain pack**:

```python
# app/lib/gis/algorithms/<your_domain>.py
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

PARAMETER_CONTRACTS = [ParameterContract(id="moran_i_analysis", ...)]

ALGORITHMS = [...]
```

`ParameterContractRegistry.load_builtins()` auto-aggregates
`PARAMETER_CONTRACTS` from every domain pack (deterministic order; duplicate
ids rejected). Units ⊆ `PARAM_UNIT_VOCABULARY`. Data-dependent "auto"
defaults must name a rule id from `DATA_DEPENDENT_RULES` (report if missing).
Tool side entry: `apply_contract(contract_id, params)` → normalized params
or `ValueError("parameter_contract_violation:...")`.

Parity gate: every contract's **required** param names must appear in each
candidate tool's OpenAI schema — checked by
`validate_algorithm_tool_parameter_parity()`.

## 4. Uncertainty blocks (`app/lib/gis/uncertainty.py`)

`ScalarUncertainty`, `FieldUncertainty`, `RasterUncertainty`,
`StatisticalSignificance` (statistic/p_value/method/permutations/
multiple_testing), `SensitivityEnvelope`, `ValidationMetrics`
(loocv|k_fold: rmse/mae/bias/r²), `MonteCarloSummary`. All bounded; full
data surfaces go through refs, blocks carry summaries only.
`uncertainty_outputs` on the descriptor must match what the implementation
actually emits.

## 5. Evidence blocks (`app/lib/gis/scientific_evidence.py`)

```python
from app.lib.gis.scientific_evidence import build_evidence
ev = build_evidence(
    descriptor, tool="moran_i",
    parameters_applied={...}, input_facts={...},
    warnings=[...], transformations=[...],
    uncertainty=[StatisticalSignificance(...)],
    validation=ValidationMetrics(...),   # optional
    fallback=FallbackRecord(...),        # only if a fallback occurred
    seed=42)
result["scientific_evidence"] = ev
```

## 6. Scientific errors (`app/lib/gis/scientific_errors.py`)

`InsufficientSamples`, `InvalidCRS`, `InvalidUnits`, `MissingRequiredField`,
`InvalidGeometry`, `DegenerateData`, `UnsupportedBandSemantics`,
`DisconnectedNetwork`, `IllConditionedSystem`, `NoValidObservations`,
`ScientificPreconditionFailed`, `UnsupportedMethod`, `ResourceScaleMismatch`.
All subclass `ValueError` (existing dispatch error mapping keeps working);
each carries `scientific_code` + `correction_hint`. Use them for
**scientific** failures in implementations — not for plumbing errors.

## 7. CRS safety (`app/lib/gis/crs_safety.py`)

`classify_crs("EPSG:4326") → "geographic"`; EPSG:3857 is `projected` but
**not** `projected_local_metric` (Web Mercator scale distortion) — algorithms
needing true metric distance should declare `crs_class="LOCAL_METRIC_REQUIRED"`
only if they genuinely reject 3857; otherwise `PROJECTED_REQUIRED` + a
limitation note. `recommend_metric_crs(bbox)` gives the UTM/polar suggestion.

## 8. Method references

Only use ids that exist in `app/lib/gis/method_references.py`
(moran1950, geary1954, getis_ord1992, ord_getis1995, anselin1995,
benjamini_hochberg1995, clark_evans1954, ripley1976, silverman1986,
ester_kriegel1996, matheron1963, shepard1968, horn1981,
zevenbergen_thorne1987, tarboton1997, weiss2001, wilson2007, mann1945,
kendall1975, sen1968, hirsch_slack1984, dijkstra1959, teitz_bart1968,
radke_mu2010, rouse1974, huete1988, gao1996, mcfeeters1996, xu2006,
zha_woodcock2003, key_nottrott2011, malila1980, hwang_yoon1981).
Need a new canonical reference? Report it; do not edit the file.

## 9. Registration recipe (per algorithm)

1. Implementation in your domain library module (pure functions, bounded
   memory, deterministic unless declared otherwise; fixed seeds where
   stochastic — declare `random_seed_policy`).
2. Capability in `app/lib/gis/capabilities/<domain>.py` (reuse an existing
   one if semantics match — prefer reuse; new artifact types are NOT added:
   reuse the existing artifact vocabulary).
3. Algorithm in `app/lib/gis/algorithms/<domain>.py` with VNext fields,
   `conformance_tests` pointing at the tests you wrote,
   `scientific_status` (`EXPERIMENTAL` unless you deliver trusted-reference
   conformance tests — then `VALIDATED`; never claim PRODUCTION without
   contract + references + tests).
4. `PARAMETER_CONTRACTS` in the same module for significant parameters.
5. Tool in the existing domain tool file (`@tool(registry, ...)` style,
   tier/domains metadata, schema auto-derived). Thin wrapper + evidence.
6. Tests (conformance w/ trusted reference values + property tests with
   fixed seeds + degenerate/empty inputs + determinism).
7. Validate:

```bash
PY=/home/kevin/projects/webgis/webgis-ai-agent/.venv/bin/python
$PY -c "from app.services.gis_harness.registry_validation import validate_gis_library, validate_algorithm_tool_parameter_parity; \
print(validate_gis_library()); print(validate_algorithm_tool_parameter_parity())"
$PY -m pytest tests/unit/<your files> -q --no-cov -p no:cacheprovider
$PY -m ruff check <files you touched>
```

Both validators must return `[]` (pre-existing `network_tool_orphan`
manifest warnings are the orchestrator's business, not yours).

## 10. Hard rules

- NEVER mark planned/unavailable implementations as `native`.
- NEVER let `count` masquerade as density/rate/equity — denominators are
  explicit; zero-denominator policy disclosed.
- NEVER call visual heatmap "analytical KDE".
- NEVER execute arbitrary user-supplied Python (band math stays
  formula-registry based).
- NEVER silently substitute Euclidean distance for network distance — proxy
  semantics must be declared (`fallback_semantics="proxy"` + limitations).
- NEVER interpret degrees as meters — declare `crs_class`, use
  `classify_crs`, reproject via existing utilities.
- NEVER treat 2 timestamps as a statistically robust trend —
  `min_temporal_observations` preconditions.
- Uncertainty is never suppressed to simplify presentation.
- Do NOT touch central files (`algorithm_registry.py`,
  `capability_registry.py`, `algorithm_resolver.py`, `runtime_manifest.py`,
  `registry_validation.py`, `parameter_contracts.py` core,
  `scientific_preconditions.py` core, `method_references.py`, `artifacts.py`,
  `app/tools/registry.py`, `app/tools/__init__.py`, other domains' files,
  frontend, data_fabric, gis_harness services).
- Do NOT run git write commands (no add/commit/checkout); the orchestrator
  commits. Read-only git (diff/log) is fine.
- New tools go into the EXISTING domain tool file (already registered in
  `app/tools/__init__.py`) — you cannot register new modules.
- Report at the end: files touched, algorithms/capabilities/contracts/tools
  added, exact test commands + pass counts, any central-file needs (new
  precondition ids, method references, artifact types), limitations.
