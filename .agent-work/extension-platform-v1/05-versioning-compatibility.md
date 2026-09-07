# 05 — Versioning & Compatibility

## 1. Current versioning signals (fragmented)

| Signal | Where | Meaning today |
|---|---|---|
| `contract_version` | `ToolDescriptor` field (raw 10 §2, descriptor.py L169-173); `AlgorithmDescriptor.contract_version=1` (`algorithm_registry.py:91-139`) | per-descriptor integer; **no negotiation, no schema-publishing surface** (raw 20 §7.8) |
| `MANIFEST_VERSION=3` | `app/lib/gis/runtime_manifest.py:44` | runtime manifest snapshot format |
| `manifest_fingerprint` | planner stamps (`planner.py:440`); `RecipeRegistry.content_fingerprint()` (`recipes.py:825-832`) | content-addressed staleness: change → SessionPlan STALE_PLAN flag (`session_plan.py:157-174`, ADR-0101 C12) |
| `schema_version` (recipes) | `recipes.py:84`; `RECIPE_SCHEMA_VERSION=2` (`workflow_schema.py:30`); V3 additive `ontology_tasks` (:92) | V1/V2/V3 coexist via additive fields only; no negotiation (raw 40 §6.5) |
| MapSpec `version` + catalog `schemaVersion 4` | `types.ts:222`; `export_component_catalog.py` (schemaVersion 4) | frontend contract versioning |
| Spawn dump v2 | `pi_native_surface.py:296-318`, `native-tools.json` | Pi surface format frozen at spawn |
| App/version drift | `VERSION` file `0.1.0.0` vs pyproject `0.1.3` vs FastAPI `version="0.1.3"` (`main.py:280`) | no single release truth (raw 80 §E) |
| Descriptor provenance only | `descriptors.py:49` `source`, `contract_payload()` fingerprints (`:60-76`, `roles.py:40-54`) | model descriptors have **no schema/contract version field** (raw 70 §5.5) |
| ADR scheme | `docs/adr/NNNN-slug.md`, latest 0103 (reused across AIDs) | documentation versioning only |

## 2. Proposed version axes (Extension Platform V1)

| Axis | Name | Semantics |
|---|---|---|
| Core | `CORE_API_VERSION` (int, start `1`) | The seven frozen seams (raw 02 §2): ToolRegistry register contract, CapabilityRegistry, AlgorithmRegistry descriptor schema, SessionPlan envelope, MapSpec intent surface, ArtifactContract, ExecutionPlan. Bump (major) only when a frozen seam breaks; all bumps documented in an ADR + `compatibility.md` matrix. |
| Extensions | `EXTENSION_API_VERSION` (int, start `1`) | SDK builder signatures, lifecycle states, diagnostics format, permission vocabulary. Independent of CORE: core may add without breaking extensions. |
| Manifest | `manifest_schema_version` (int, start `1`) | `extension.manifest.json` shape. Loader accepts exactly `1` in V1; unknown = validation error (fail-fast like strict manifest validation, `main.py:75-86`). |

Per-descriptor `contract_version` fields stay (they remain the fine-grained signal inside registries);
the platform never redefines their meaning, only requires manifest-declared extensions to state the
descriptor contract versions they target (validated against registry-accepted values at inspect time).

## 3. Compatibility matrix (published, machine-readable)

`app/extensions_platform/compatibility.py` ships a static table:

| CORE_API | EXTENSION_API | manifest_schema | Recipe schema | Pi dump | Status |
|---|---|---|---|---|---|
| 1 | 1 | 1 | 2 and 3 (additive coexistence, raw 40 §3) | v2 (`pi_native_surface.py:296-318`) | supported |
| 1 | 0 (pre-platform) | n/a | — | — | N/A (platform is new) |

- Manifests declare `requires: {core_api, extension_api, packs: {name: version_range}}`; loader checks the
  matrix at inspect stage; unsatisfied → skip with diagnostic (raw 03 lifecycle).
- Extension pack `version` is semver-shaped (validated identifier like `algorithm_family`,
  `algorithm_registry.py:178-183`); re-registering same ids with a changed fingerprint requires a version
  bump (raw 03 register invariant).

## 4. Deprecation policy

1. Mark, don't break: registries already carry `status` (`ToolStatus` raw 10 §2; algorithm
   `scientific_status` includes DEPRECATED and **requires a fallback**, `algorithm_registry.py:450-463`);
   extension entries use the same vocabulary — deprecation = descriptor status change + `deprecation_of`
   pointer (descriptor.py:168-173), never silent removal.
2. Timeline: deprecated in minor release → removal only in a CORE_API major bump, with the ADR matrix
   updated in the same PR.
3. Loader behavior: manifests targeting a deprecated extension API load with a DEGRADED warning diagnostic;
   targeting an unsupported API → FAILED (never half-registered).
4. SessionPlan staleness is the runtime deprecation signal: fingerprint change flags old plans STALE_PLAN
   rather than invalidating them (`session_plan.py:157-174`).

## 5. Feature negotiation

- **Capability discovery, not probing**: extensions read the frozen seams' introspection (runtime manifest
  `MANIFEST_VERSION`, catalog export `schemaVersion`, registry `runtime_status` vocabularies
  `native|planned|unavailable` — `component_registry.py:12`, `algorithm_registry.py:18`) to decide what to
  register; `runtime_status=planned` is the sanctioned "register schema now, renderer later" lane (raw 30 §6).
- **Pi surface**: extensions must accept the two-layer truth — tools callable via `webgis_execute`
  immediately, first-class schemas after respawn (raw 10 §6.5); no negotiation with the frozen spawn dump
  is possible, so the SDK encodes it in lifecycle docs and health output.
- **Query capabilities**: data providers override `capabilities_v2()` truthfully (`query/capabilities.py:237`);
  extensions must not over-declare (declared fields `query/models.py:197-216` are checked by the planner).

## 6. Migration helper

- `python -m app.extensions_platform migrate` scaffolds a pack from legacy shapes: an `_TOOL_MODULES` entry
  (`app/tools/__init__.py:11-57`), a recipe pack module (`recipe_packs/__init__.py:18-45`), an
  `app/skills/*.py` file, or a fabric adapter registration — emits manifest + SDK builder skeleton with the
  correct namespacing, preserving ids via `<ns>_`/`<ns>.` prefixing where required.
- The helper is advisory-only: legacy in-repo loading paths keep working during V1 (no forced migration of
  the ~45 built-in tool modules); the generator plus conformance corpus (raw 08) prove parity before a pack
  switches to platform loading.
