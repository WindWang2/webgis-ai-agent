# 03 — Loading Lifecycle (current vs target)

## 1. Current lifecycles

### 1.1 Tools (startup, one-shot)
`app/main.py:68-69` constructs `ToolRegistry()` → `init_tools(registry)` (`app/tools/__init__.py:61-90`):
lazy-import each of ~45 `_TOOL_MODULES` entries (:11-57), call `register_func(registry)`; per-module
exceptions caught → warning only (:70-71) → `load_skills` (:76) → `refresh_list_available_tools_args` (:86).
Then strict runtime-manifest validation (main.py:75-86, `GIS_MANIFEST_STRICT=0` escape), then injection into
services + Pi bridge (main.py:87-94) which recompiles the manifest (`agent_pi_bridge.py:173-185`).
Evaluation/validation paths rebuild the full registry from `_TOOL_MODULES` independently
(`runtime_manifest.py:263-264`, `registry_validation.py:259-260`, `evaluation/runner.py:175-176`) —
out-of-band loads would vanish there (raw 10 §6.8).

### 1.2 Skills (hot-loadable, two-layer)
`load_skills` scans `app/skills/*.py` (exec with restricted builtins, `skills.py:412-436`) + `.md`.
Hot path: tier-3 `refresh_skill_surface` re-scans (registry layer live immediately via `webgis_execute`)
but the native schema layer is frozen at Pi spawn (`skill_surface_refresh.py:34-90`, `pi_rpc_client.py:280-287`);
never auto-respawns (raw 50 §5.4).

### 1.3 Algorithms / capabilities / cartography (build-once singletons)
First `get_*_registry()` call runs `load_builtins()`: hard-coded module tuples
(`algorithms/__init__.py:26-30`, `capabilities/__init__.py:11-19`), seeds-then-packs deterministic order
(`model_library.py:495-502`, `composition_templates.py:240-246`), dup = raise. No runtime re-registration;
only test `reset_*` hooks (`algorithm_registry.py:511-513`). Validation (`validate()`, `validate_gis_library`)
is repo-layout bound (conformance node check needs `tests/` CWD, `algorithm_registry.py:471-479`).

### 1.4 Recipe packs (build-once, fail-loud)
`get_recipe_registry()` builds-then-assigns so a failed load leaves no half registry (`recipes.py:1011-1021`);
`load_builtins` (:712-742) loads 17 seeds then 24 `PACK_MODULES` in name order, fail-loud aggregation;
keep-first on duplicate id (:744-752); content fingerprints cached per recipe (:825-832).

### 1.5 Data adapters (lazy closed registry)
`get_registry()` lazily builds from literal `_build_registry()` list (`registry.py:105-169,175-179`);
sync ABC; adapters run via `asyncio.to_thread` (`tools/data_fabric_tools.py:154`).

### 1.6 Pi runtime (spawn-frozen)
Lifespan spawns vendored Pi subprocess with `--extension app/extensions/webgis-tools/index.mjs`
(main.py:104-118); `native-tools.json` dumped fail-fast pre-spawn (`pi_rpc_client.py:280-287`); surface
frozen until worker respawn; per-turn narrowing via prompt marker (`pi_turn_context.py:227-246`).

## 2. Target extension lifecycle

`discover → inspect → validate → resolve deps → load → register → activate → health → deactivate → unload`

| Stage | Contract |
|---|---|
| **discover** | Bounded scan of `extensions/` dirs (repo + `DATA_DIR/extensions`); each pack = dir with `extension.manifest.json` + entry module. Cap on pack count; enumeration order deterministic (name sort). No `importlib.metadata` dependency in V1. |
| **inspect** | Parse+schema-validate the manifest (frozen `manifest_schema_version`); compute content fingerprint (canonical-JSON SHA-256, same scheme as ToolDescriptor fingerprints, raw 10 §2). No extension code executes yet. |
| **validate** | Static checks: namespacing (`<ns>_` tools, `<ns>.<id>` algorithms/recipes), descriptor vocab membership, capability/algorithm reference resolution against the *current* registries, conformance-test declarations. Produces a `DiagnosticsReport` (errors block load; warnings do not). |
| **resolve deps** | Manifest declares `requires: {core_api, extension_api, packs[]}`; topo-sort packs; **cycle detection = hard error**; missing/unsatisfied dep = skip pack with diagnostic, never partial-load. |
| **load** | Import the entry module (trusted-code boundary, raw 04). Import failure = pack enters FAILED, no partial registrations (module-level `register()` must be transactional — SDK builder collects then commits). |
| **register** | Project into authoritative registries via SDK builders; each registry call recorded in a per-pack **journal** (registry, kind, id) for rollback. Idempotent: re-registering the same fingerprint is a no-op; different fingerprint with same ids = REJECTED (must bump version). |
| **activate** | Pack marked ACTIVE; Pi surface: tools immediately callable via `webgis_execute`; first-class schemas after next worker respawn (documented two-layer truth, raw 10 §6.5). SessionPlan staleness: if fingerprints change the runtime manifest, existing plans get STALE_PLAN (`session_plan.py:157-174`). |
| **health** | Introspection endpoint/CLI state per pack: `DISCOVERED/VALIDATED/LOADED/ACTIVE/DEGRADED/FAILED/DISABLED/UNLOADING`; last diagnostics + fingerprint exposed. |
| **deactivate** | Reverse-order unregister using the journal; additive `unregister_*` methods on each authoritative registry (new in this platform, scoped to pack-owned ids only). In-flight dispatches drained or cancelled via existing CancellationToken seams (raw 50 §5.8). |
| **unload** | Drop module refs; re-run registry `validate()`s to prove no dangling references (dangling capability/recipe refs are fatal in core validation — `registry_validation.py:120-186`, `runtime_manifest.py:397-410`); manifest refreshed. |

## 3. Required states and invariants

- **States**: `DISCOVERED → VALIDATED → LOADED → ACTIVE` happy path; `FAILED` (any stage, with diagnostics);
  `DISABLED` (config/quarantine); `DEGRADED` (activated with warnings); `UNLOADING → unloaded`.
- **No zombie entries**: register and unregister are journaled + atomic per registry; a FAILED load must
  leave every authoritative registry byte-identical to pre-load (asserted by parity tests, raw 08).
- **Idempotent reload**: load(same fingerprint) twice = no-op; deactivate(unloaded pack) = no-op; reload after
  edit requires fingerprint change. Registry fingerprints remain order-independent (`registry.py:994-1005`).
- **Cycle detection**: dependency graph is a DAG; cycles and self-deps rejected at resolve stage.
- **Bounded discovery**: max packs, max manifest size, max entries per pack; discovery never imports code.
- **Fingerprint everywhere**: pack fingerprint = hash(manifest + entry file + declared resources); feeds
  runtime manifest fingerprint and thus SessionPlan staleness and diagnostics caching.
- **Startup parity**: extension packs must be visible to validation/eval rebuild paths
  (`runtime_manifest.py:263-264`, `evaluation/runner.py:175-176`) — the platform hooks pack loading into the
  same seam `init_tools` uses, not a side channel.
- **Pi freeze acknowledged**: activation never respawns Pi workers automatically (kills active turns,
  raw 50 §5.4); surface refresh is an explicit operator action.
