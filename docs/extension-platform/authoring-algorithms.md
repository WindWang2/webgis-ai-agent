# Authoring Extension Algorithms

SDK surface: `app/extensions_platform/sdk/algorithm.py`
(`AlgorithmExtensionSpec`, `NumericalSmokeCase`, `run_authoring_checks`).
The SDK builds the core `AlgorithmDescriptor`
(`app/lib/gis/algorithm_registry.py`) with kwargs identical to in-repo domain
packages — the `AlgorithmRegistry` remains the only source of truth.

Worked example: `COMPACTNESS_ALGORITHM` in
[`extensions/examples/extdemo-pack/main.py`](../../extensions/examples/extdemo-pack/main.py).

```python
from app.extensions_platform.sdk import AlgorithmExtensionSpec, NumericalSmokeCase

COMPACTNESS_ALGORITHM = AlgorithmExtensionSpec(
    id="compactness",
    name="Polsby-Popper Compactness",
    description="Scale-free shape compactness (Polsby-Popper 4*pi*A/P^2).",
    capabilities=[],                      # frozen seam: never invent capability ids
    category="morphometry",
    tags=["extdemo", "shape-metrics"],
    tool_candidates=["extdemo_polygon_compactness"],   # PROJECTED tool name
    deterministic=True,
    cpu_cost="low", memory_cost="low", io_cost="low",
    assumptions=["planar coordinates (CRS units); no geodesic correction"],
    limitations=["perimeter is sensitive to vertex densification"],
    scientific_status="EXPERIMENTAL",
    smoke_cases=[
        NumericalSmokeCase(
            arguments={"geojson": _SQUARE_GEOJSON},
            expect_key="compactness",
            expect_value=pi / 4,
            tolerance=1e-3,
        ),
    ],
)
```

`ctx.register_algorithm(spec)` projects the id to `<ns>.<id>` (e.g.
`extdemo.compactness`) and returns it.

## `AlgorithmExtensionSpec` field reference

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `id` | `str` | required | snake_case identifier, no leading digit; projected to `<ns>.<id>`; must be declared in the manifest `algorithms` section |
| `name` | `str` | required | Human name |
| `capabilities` | `list[str]` | `[]` | Must reference existing `CapabilityRegistry` ids when `known_capabilities` is provided (projection passes the real registry). Unknown id = typed error. Leave empty if nothing genuinely applies |
| `category` / `subcategory` | `str` | `""` | Catalog classification |
| `description` | `str` | `""` | — |
| `tags` | `list[str]` | `[]` | — |
| `input_artifact_types` | `list[str]` | `[]` | Artifact contract vocabulary |
| `output_artifact_type` | `str` | `""` | — |
| `geometry_requirements` | `list[str]` | `[]` | — |
| `required_fields` | `list[str]` | `[]` | — |
| `min_features` / `max_features_hint` | `Optional[int]` | `None` | Scale envelope |
| `crs_requirements` / `crs_class` | `str` | `""` | CRS statement |
| `deterministic` | `bool` | `True` | — |
| `cpu_cost` / `memory_cost` / `io_cost` | `str` | `"medium"` | — |
| `tool_candidates` | `list[str]` | `[]` | **Must reference registered tools by their projected, namespaced names** (`<ns>_<tool>`), and the tools must be registered *before* the algorithm — projection validates against the live `ToolRegistry` |
| `compatible_map_models` | `list[str]` | `[]` | Map model ids |
| `priority` | `int` | `50` | Stable tie-break ordering |
| `version` | `str` | `"1.0"` | — |
| `algorithm_family` | `str` | `""` | — |
| `method_references` | `list[str]` | `[]` | Citations / method provenance |
| `assumptions`, `limitations`, `scientific_preconditions`, `uncertainty_outputs` | `list[str]` | `[]` | Scientific honesty metadata (surfaced by the catalog) |
| `random_seed_policy` | `str` | `"deterministic"` | — |
| `numerical_tolerance` | `str` | `""` | — |
| `scientific_status` | `str` | `"EXPERIMENTAL"` | `""`, `EXPERIMENTAL`, `VALIDATED`, `PRODUCTION`, `DEPRECATED` |
| `conformance_tests` | `list[str]` | `[]` | Required for `VALIDATED`/`PRODUCTION` (see below) |
| `backend_variants` | `list[dict]` | `[]` | **Maximum 4 entries** (core rule); each validated into a core `BackendVariant` |
| `smoke_cases` | `list[NumericalSmokeCase]` | `[]` | Authoring-harness input only — never enters the descriptor |
| `scale_guard_features` | `Optional[int]` | `None` | Harness input only |
| `cancellation_probe` | `Optional[Callable]` | `None` | Harness input only |

## Namespacing and ordering

- The registered id is `<ns>.<id>`; collisions with core or other extensions
  are typed errors (`REGISTRY_PROJECTION_COLLISION`).
- `tool_candidates` must use the **projected** tool names. Register the tools
  first: algorithm projection validates every candidate against the live
  `ToolRegistry.tool_names()` at activation time.
- `capabilities` are validated against the authoritative
  `CapabilityRegistry`. The `CapabilityRegistry` itself is a frozen seam —
  extensions cannot add capabilities, so cite existing ids or leave empty.

## Scientific status ladder

`scientific_status` defaults to **`EXPERIMENTAL`**. The core registry
enforces the same rule for in-repo algorithms, and the SDK enforces it for
extensions:

- `EXPERIMENTAL` — default; no further evidence required.
- `VALIDATED` / `PRODUCTION` — requires a non-empty `conformance_tests` list;
  otherwise a typed error blocks projection ("core registry enforces the same
  rule for in-repo algorithms").
- `DEPRECATED` — allowed but produces a warning; provide a fallback in the
  descriptor.

## `run_authoring_checks` harness

```python
from app.extensions_platform.sdk import run_authoring_checks

diagnostics = run_authoring_checks(COMPACTNESS_ALGORITHM, _polygon_compactness_run)
assert diagnostics == []
```

A deterministic, offline, LLM-free local harness for the loop
"implement → validate contract → numerical smoke → scale guard → cancellation
probe → register":

1. **Smoke cases** — for each `NumericalSmokeCase`, calls
   `implementation(**case.arguments)` and compares `result[case.expect_key]`
   against `case.expect_value`. Floats compare within `case.tolerance`
   (default `1e-9`); other values compare exactly. A raising implementation
   or a mismatch is collected as an error diagnostic (the harness never
   stops at the first failure).
2. **Scale guard + cancellation probe** — when `scale_guard_features` is set
   and a `cancellation_probe` is provided, the probe is invoked; any exception
   it raises is recorded as an error.

A related helper, `validate_tool_algorithm_parity(algorithms, tools)`, checks
pack-internally that every `tool_candidates` entry is one of the pack's own
tool names.

The example pack's test drives the harness end to end (unit square →
`pi/4 ≈ 0.7854`); see [testing.md](testing.md).
