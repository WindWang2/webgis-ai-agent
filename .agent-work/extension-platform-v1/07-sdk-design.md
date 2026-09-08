# 07 — Extension Platform V1: SDK Design (agreed direction)

## 1. Design principles

1. **Project, never duplicate**: all state lives in the authoritative registries (raw 02); the platform is a
   loader + projector with per-pack rollback. No second executing tool table (ADR-0101 Decision 2,
   `pi_native_surface.py:243-253`).
2. **Trusted code, governed lifecycle**: in-process import = trusted boundary (raw 04 §1); the platform sells
   determinism, namespacing, validation, and blast-radius control — not isolation.
3. **Additive-only core changes**: new `unregister_*` methods and the WMS/STAC/SSRF hardening (raw 06 §3);
   existing registries' semantics, collision policies, and validation stay untouched.
4. **Fail loud where init_tools fails soft**: replace the silent per-module warning-drop
   (`app/tools/__init__.py:70-71`) with stateful pack diagnostics.

## 2. Package layout — `app/extensions_platform/`

| Module | Responsibility |
|---|---|
| `manifest.py` | `ExtensionManifest` pydantic model: `id`, `namespace`, `version` (semver-shaped), `manifest_schema_version=1`, `requires: {core_api, extension_api, packs{}}`, `permissions{network, filesystem, secrets, db_write, pi_surface}`, `entry` module path, `description`. Frozen schema (raw 05 §2); strict unknown-field rejection mirroring `descriptors.py:79-84`. |
| `diagnostics.py` | `Diagnostic(code, severity, message, ref)` + `DiagnosticsReport`; codes stable and machine-readable (like blocking codes `lifecycle_engine.py:56-60`); errors block load, warnings → DEGRADED. |
| `compatibility.py` | `CORE_API_VERSION=1`, `EXTENSION_API_VERSION=1`, static support matrix (raw 05 §3). |
| `permissions.py` | Permission set + checker; source of the projection-time enforcement (raw 04 §6). |
| `trust.py` | Trust policy: pack source dirs (repo `extensions/`, `DATA_DIR/extensions`), signature/allowlist placeholder for V1 (trusted-code doctrine, raw 04 §1). |
| `discovery.py` | Bounded, deterministic scan of pack dirs; parses manifests only — never imports (raw 03 §2 discover). |
| `lifecycle.py` | Lifecycle host implementing `discover→inspect→validate→resolve deps→load→register→activate→health→deactivate→unload` with the state machine and invariants of raw 03 §3; owns the per-pack **journal** for rollback and the quarantine policy (raw 04 §7). |
| `projection.py` | The only module that writes to authoritative registries; journals every `(registry, kind, id)`; calls new additive `unregister_*` methods on rollback; re-runs registry `validate()`s after unregister (raw 03 unload stage). |
| `cli.py` | `python -m app.extensions_platform {list,inspect,doctor,load,unload,migrate}` — operator surface; `doctor` prints pack states + diagnostics; `migrate` is the scaffolder (raw 05 §6). |

## 3. SDK builders — `app/extensions_platform/sdk/`

Importable by extension entry modules; each builder validates + defers, and `projection` commits
transactionally at the register stage (a failing build leaves no partial registration):

- `sdk/tool.py` — wraps `ToolRegistry.register` (`registry.py:459-694`): enforces `<ns>_` name prefix,
  honest `tier/side_effect/security_tier` defaults (tier≤2, read_only; higher = manifest permission,
  raw 04 §2), closed descriptor kwargs (registry.py:442-457), registers aliases via a new additive
  alias-registration path (static `TOOL_NAME_ALIASES` at `argument_normalization.py:105` stays core-only).
- `sdk/algorithm.py` — feeds `AlgorithmRegistry.register` (`algorithm_registry.py:208-212`): `<ns>.<id>`
  dotted ids, vocabulary membership pre-checked (`:23-45`), conformance declarations validated with the
  same node-existence logic (`:464-497`) against pack-shipped test dirs (removes the repo-layout bound,
  raw 20 §7.4); maturity gating unchanged (`:450-463`).
- `sdk/recipe.py` — `RECIPES: List[CartographyRecipe]` via `RecipeRegistry.register` (`recipes.py:744-768`):
  `<ns>.<recipe>` ids so keep-first never silently eats core; schema V2 `WorkflowProfile` construction via
  `_kit.py`-style builders; capability/ontology references resolved at validate stage (dangling = fatal,
  `registry_validation.py:120-186`).
- `sdk/cartography.py` — component/model/template/theme descriptors into the five cartography registries
  (raw 02 §1); `<ns>.`-prefixed ids; respects each registry's existing collision policy; `runtime_status=
  planned` lane for schema-now/renderer-later (raw 30 §6); optional per-component options JSON schema
  (closes the free-form `options` gap, raw 30 §6.6) stored as descriptor metadata.
- `sdk/provider.py` — `AdapterSpec` into `AdapterRegistry` (`registry.py:63-73`): namespaced source types
  `<ns>:<type>`; requires the SafeHttp facade for egress (raw 04 §4); optional `RasterTileCapableAdapter`
  protocol (raw 06 §3.6); auto-extends `mapspec_source`/capabilities defaults dynamically.

**Namespacing rules (normative)**: tools `<ns>_name` (snake_case, matches tool-name conventions, raw 20 §3);
algorithms/recipes/components/models `<ns>.<id>`; capability ids `kde_density`-style stay core vocabulary —
extensions must reference existing capabilities or register `<ns>.<capability>` via `sdk/algorithm.py`;
adapter source types `<ns>:<type>`. Collision with any existing namespaced id (core or other pack) = load
error, surfacing in DiagnosticsReport.

**Permission wrapper enforcement at projection time**: builders accept a `PermissionSet`; before any
registry write, `permissions.py` verifies the descriptor's egress/credential/security fields
(`descriptor.py:183-220`) fit the grant; over-declaration → diagnostic error (raw 04 §6).

## 4. Core additive fixes shipped with V1

Per raw 06 §3: WMS CRS/bbox honesty (`wms_wmts_adapter.py:113-114`); STAC catalog from `ConnectionProfile`
(`rs/stac_client.py:14,212`) + asset materialization (`registry.py:145`); `validate_url` before `/vsicurl`
and aiohttp egress; `ArtifactContract.from_fabric_descriptor`; additive `unregister_*` on ToolRegistry,
AlgorithmRegistry, RecipeRegistry, AdapterRegistry, cartography registries (scoped to pack-owned ids).

## 5. Example pack + conformance corpus

- `extensions/example-gis-tools/` — fixture pack under the repo `extensions/` dir: 1 tool, 1 algorithm
  (with pack-local conformance test), 1 recipe, 1 cartography component (`runtime_status=planned`),
  double-duty as integration fixture and authoring template. Loaded in tests only by default; opt-in in
  production config.
- Conformance corpus generator ≥2,000 cases: deterministic expansion in the style of
  `app/evaluation/conformance.py` (`_expand_family` :616-661, ≥20,088-case precedent, raw 40 §4): pack
  manifest + descriptors × scope variants × utterance variants; zero LLM/zero I/O; asserts every projected
  entry resolves (capability→algorithm→tool), namespacing holds, and route order stays deterministic
  (11-key tuple semantics, `recipes.py:900-1005`).

## 6. Non-goals (explicit)

1. **No second registries** — no parallel tool/algorithm/recipe/adapter store, no second plan or map truth
   (raw 02 §3 forbidden list).
2. **No sandbox** — no isolation claims; trust = code review + source policy (raw 04 §1); dynamic-skill
   builtins sandbox is unchanged and not a boundary.
3. **No marketplace** — no discovery service, download, auto-update, or signing infrastructure in V1; packs
   are installed by operators into declared dirs (`trust.py`).
4. Also out: JS-extension authoring toolchain (Pi side stays hand-maintained, raw 10 §6.9), LLM provider
   adapter ABC extraction (separate ADR-0102 track, raw 70 §5), forced migration of built-in modules.
