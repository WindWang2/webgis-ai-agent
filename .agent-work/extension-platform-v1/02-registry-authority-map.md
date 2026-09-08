# 02 — Registry Authority Map

Rule: the Extension Platform **projects INTO** these registries. It never creates parallel
registries, never bypasses their collision/validation policies, and never persists state outside
them (Pi-runtime constraint 2/7: ToolRegistry is the single execution truth; planner/manifest are
pure projections — raw 50 §5).

## 1. Domain → authoritative registry

| Domain | Authoritative registry | Location | Collision policy | Who writes today |
|---|---|---|---|---|
| Tool execution | `ToolRegistry` | `app/tools/registry.py` (`register` :459-694) | warn + overwrite, last-wins (:525-535) | `init_tools` over `_TOOL_MODULES` (`app/tools/__init__.py:61-90`), `load_skills` |
| Algorithm semantics | `AlgorithmRegistry` | `app/lib/gis/algorithm_registry.py` (`register` :208-212) | duplicate id → `ValueError` (:214-216) | `load_builtins()` at singleton build (:503-514) |
| Capability vocabulary | `CapabilityRegistry` | `app/lib/gis/capability_registry.py` | duplicate id → `ValueError` (:67-69) | 11 capability packs (`capabilities/__init__.py:11-19`) |
| Artifact types | `ArtifactTypeRegistry` | `app/lib/gis/artifacts.py:158-172` | duplicate → raise (:169-172) | builtins |
| Cartography components | `ComponentRegistry` | `app/lib/cartography/component_registry.py:408,426-436` | duplicate id → raise (:427-428) | 17 seeds + `validate()` fail-closed (:493-536) |
| Map models | `MapModelRegistry` | `app/lib/cartography/model_library.py:483-536` | `register()` silent-ignore (:504-507); packs raise in load_builtins (:498-502); alias first-wins (:509-515) | seeds + `model_packs/` |
| Composition templates | registry in `composition_templates.py:231` (register :247) | raise on dup (packs :240-246) | seeds + `composition_packs/` |
| Themes/palettes | `CartographicThemeRegistry` | `app/lib/cartography/themes.py:435,443-447` | last-wins, no check | seeds |
| Recipes | `RecipeRegistry` | `app/services/gis_harness/recipes.py` (`register` :744-768) | duplicate id → **keep-first** + warning (seeds win; pack order deterministic :723-725) | `load_builtins()` :712-742 over `PACK_MODULES` (`recipe_packs/__init__.py:18-45`) |
| Data source adapters | `AdapterRegistry` | `app/services/data_fabric/registry.py:56-101,105-169` | alias rebinding raises (:67-71); unknown type raises `UnsupportedSourceError` (:79-85) | closed literal `_build_registry()` (:105-169) |
| Session plan truth | `SessionPlan` (envelope, ADR-0076) | `app/services/session_plan.py:67-78` | same-goal replace voids old rows (`goal_key` :96-110); supersede via SSE | `webgis_map_intent` result hook (:601-638) |
| Map desired state | `MapSpec` store + lifecycle engine | `app/services/mapspec/store.py:155`; `lifecycle_engine.py:522` | transactional candidate-validate-commit; blocking codes (:56-60) | cartography tools via intents (:188-284) |
| Data artifacts | `ArtifactContract` V3 | `app/lib/data/artifact_contract.py:173-209` | vocab-validated types (:212-221); CRS never fabricated (:192) | bridges `from_*` (:351,:453,:508,:551) |
| Geocompute execution | `ExecutionPlan`/`GeoExecutionEngine` | `app/services/geocompute/plan.py:150-247`; `executor.py` | graph validation (graph.py:56-164); admission vs `ResourceBudget` (executor.py:371) | geocompute API/tools |
| Model descriptors | `ModelDescriptorRegistry` | `app/services/chat/model_runtime/descriptors.py:97-143` | strict unknown-field rejection (:79-84) | settings + `MODEL_DESCRIPTORS_FILE`/overrides; admin `upsert_override` (:171-175) |
| Provider health | `ProviderHealthTracker` | `app/services/provider_health.py:26-159` | arbitrary keys; label-only frozenset (:12) | call sites via `tracked_provider_get` (:225-286) |

## 2. Frozen integration seams (platform contracts — stable, versioned surfaces)

These seven are the seams the Extension Platform commits to as its stable API surface:

1. **ToolRegistry** — `register`/`tool` kwargs contract (`registry.py:442-457`), descriptor fingerprint
   (canonical-JSON SHA-256, raw 10 §2), tier/side-effect/security gating (`:1213-1218`, `:629-631`).
2. **CapabilityRegistry** — demand-side vocabulary consumed by plans/recipes (`capability_registry.py:1-6,18-41`);
   AlgorithmRegistry.validate() enforces both directions (`algorithm_registry.py:331-372`).
3. **AlgorithmRegistry** — `AlgorithmDescriptor` fields + scientific block (`algorithm_registry.py:91-139`),
   maturity gating (:450-463), conformance node ids (:464-497).
4. **SessionPlan** — single plan truth (ADR-0076): capability progress, `manifest_fingerprint` staleness
   (`session_plan.py:157-174` STALE_PLAN flag), supersede semantics.
5. **MapSpec** — desired-state JSON, authoritative TS schema `frontend/lib/mapspec-compiler/types.ts:222`;
   backend intent engine + structural validate (`coordinator.py:99`); `legend_spec` contract (ADR-0078).
6. **ArtifactContract** — read-only projection + `summary()` bounded LLM view ≤1600 chars
   (`artifact_contract.py:270-327`); storage_ref never payload.
7. **ExecutionPlan** — geocompute data-plane DAG, per-node semantic fingerprints (ADR-0101 D2), budgets,
   cancellation (`executor.py:219-354`).

## 3. Projection obligations for the Extension Platform

- **Additive unregister**: registries lack unload paths (only test `reset_*`: `algorithm_registry.py:511-513`,
  `recipes.py` reset, cartography `reset_*`). Platform adds `unregister_*` methods to each registry
  (scoped to one extension's ids) — additive, never changes existing semantics for core entries.
- **Namespacing before projection**: extension entries are namespaced (`<ns>_` tools, `<ns>.<id>`
  algorithms/recipes, `<ns>:` adapter ids) so collision policies never fire against core; a collision
  with an existing namespaced id is a hard validation failure at load, not registry-time overwrite.
- **Validation parity**: every projected entry passes the same `validate()` the builtins pass
  (`validate_gis_library`, `registry_validation.py:43-80,238-285`; component/model `validate()`s raw 30 §3;
  runtime manifest cross-checks `runtime_manifest.py:222-291`).
- **Fingerprint integrity**: registration must invalidate derived caches exactly as builtin registration does
  (`registry.py:690-694,800-804`; `RecipeRegistry.content_fingerprint` :825-832 → manifest fingerprint →
  SessionPlan STALE_PLAN, `session_plan.py:157-174`).
- **Surface projection honesty**: Pi surface stays a projection (`pi_native_surface.py:243-253`); extension
  tools appear in the spawn superset only if filter-compliant (`:256-273`) and otherwise remain callable via
  `webgis_execute` until respawn (raw 50 §5.4).
- **Forbidden**: a second executing tool table (ADR-0101 Decision 2), a second plan truth besides SessionPlan,
  a second map truth besides MapSpec store, persisted state in projections (`planner_runtime.py:19-27`,
  `plan_graph.py:26-28`, `runtime_manifest.py:24-26`).
