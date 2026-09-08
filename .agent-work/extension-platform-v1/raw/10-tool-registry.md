# Tool Registry / Descriptor / Loading System — Investigation (10)

## 1. File inventory
- `app/tools/registry.py` — `ToolRegistry`: single execution truth; `tool`/`register`, dispatch pipeline (validation, ref deref, tier-3 gate), descriptor/fingerprint projection (1850 lines).
- `app/tools/descriptor.py` — `ToolDescriptor` V2/V3 frozen dataclass, `ToolStatus`/`SideEffectClass` enums, vocab tables, register-time validation, canonical-JSON SHA-256 fingerprints.
- `app/tools/categories.py` — deterministic functional category projection (module-default + name-override rule tables → `uncategorized` fallback).
- `app/tools/argument_normalization.py` — declarative arg normalization: `TOOL_NAME_ALIASES` (34 entries), per-tool field-alias rules, `ArgRepair` evidence ContextVar.
- `app/tools/__init__.py` — hard-coded `_TOOL_MODULES` list + `init_tools()` startup loader.
- `app/tools/skills.py` — dynamic skill loading (`app/skills/*.py` with `register_skills(registry)` / `register(registry)`, `.md` prompt skills), `create_new_skill` tier-3 meta-tool, AST/builtins sandbox.
- `app/tools/skill_surface_refresh.py` — tier-3 `refresh_skill_surface` tool; truthful report that registry layer hot-refreshes but Pi native schema surface is frozen at spawn.
- `app/tools/meta_tools.py` — `list_available_tools` (domain discovery; hides tier-3) + `refresh_list_available_tools_args` (post-registration schema rebuild).
- `app/tools/plan_mode.py` — plan-mode tool wrappers (propose/execute/get_plan_status) over `app/services/plan_mode`.
- `app/tools/harness_runner.py` — CLI benchmark runner over `PiAgentHarness`/`HarnessEvaluator` (eval only, not part of registry).
- `app/tools/policy_audit.py` — registration-time execution-policy audit (warning-level, called from `register`).
- `app/agent_pi_bridge.py` — Pi subprocess RPC bridge; registry injection, `dispatch_tool` HTTP-callback adapter, SSE adapter, Pi-side tier>=3 hard reject.
- `app/services/chat/pi_native_surface.py` — Pi tool-surface projection: frozen `NATIVE_TOOL_NAMES` (7), spawn dump v2 (`registered_surface_names`, `pi_surface_for_spawn`), per-turn `compute_turn_active_tools`.
- `app/services/chat/pi_rpc_client.py` — spawns bundled Pi with `--extension` flags; dump-surface-to-`native-tools.json` fail-fast at spawn (L282-287).
- `app/services/chat/tool_surface_v3.py` — `DynamicToolSurface` per-turn selector (contract filter, semantic retrieval, `ROLE_SIDE_EFFECT_POLICY`).
- `app/services/tool_catalog.py` — legacy ChatEngine `ToolCatalog`: tier-1 always-on, tier-2 keyword/sticky domains, tier-3 never auto.
- `app/api/routes/pi_tools.py` — `/pi-tools/execute` HTTP callback: `verify_bridge_secret` (HMAC, L36-47) + `verify_turn_token` (L61-63).
- `app/extensions/webgis-tools/index.mjs` (+ dead-copy `index.ts`, flagged #694) — the Pi-side JS extension: registers all dumped schemas + `webgis_execute` proxy, applies per-turn active set.
- `app/lib/gis/runtime_manifest.py` — compiled manifest over registry, strict validation at startup.
- `app/main.py` — lifespan: constructs registry, runs `init_tools`, injects into services + Pi bridge, starts Pi with extension.

## 2. ToolDescriptor fields (app/tools/descriptor.py)
43 dataclass fields (`ToolDescriptor`, L155-229):
- Identity: `name`(L167) `description` `summary` `version` `contract_version` `status` `deprecation_of`(L168-173)
- Layering/scheduling: `tier` `domains` `cost` `execution_policy` `timeout`(L176-180)
- Side effects/security: `side_effect` `requires_credentials`(L183-184)
- Capability refs: `capabilities` `algorithms` `provider_dependencies` `tags`(L187-190)
- I/O contract: `output_semantic_type` `produced_refs` `accepts_ref_types` `required_fields` `network` `deterministic` `result_size_policy`(L193-199)
- Aliases: `aliases`(L202)
- V3 artifacts/context/mutations: `input_artifacts` `required_context` `map_mutations` `data_mutations`(L205-208)
- V3 resource/semantics: `latency_class` `memory_class` `scale_class` `crs_semantics` `unit_semantics` `idempotent`(L211-216)
- V3 security/permission: `security_tier` `required_permission`(L219-220)
- V3 retrieval/eval: `examples` `anti_examples` `failure_modes` `fallback_tool`(L223-226)
- Provenance: `capability_source`(L229; none|declared|derived:algorithm_registry)
Derived properties (not fingerprinted): `tool_id`, `destructive_level`, `requires_confirmation`, `executable`, `model_visible`, `retry_safe`, `cacheable`, `replay_safe`, `effective_security_tier`, `effective_idempotent` (L231-308).
`SideEffectClass` values L64-71; `ToolStatus` L46-51; latency/memory/scale vocab L125-127; `required_context`/`map_mutations`/`data_mutations` vocabularies L137-152; `RESULT_SIZE_POLICIES` L117.
Descriptors are derived read-only projections; `ToolRegistry.descriptor()` builds+caches (registry.py:870-960); capability backfill from AlgorithmRegistry (registry.py:53-73).

## 3. Registration end-to-end
- Global instance created once in app lifespan: `app/main.py:68-69` (`ToolRegistry()` + `init_tools(registry)`); injected into services (`set_app_registry`, main.py:89) and Pi bridge (`set_tool_registry`, main.py:93-94 → bridge L173-185 which also refreshes the runtime manifest).
- `init_tools` (app/tools/__init__.py:61-90) iterates the hard-coded `_TOOL_MODULES` tuple (L11-57, ~45 entries of `(module, register_func)`), imports lazily, calls `register_func(registry)`; per-module exceptions are caught and logged as warnings (L70-71) — a failing register drops the whole module's tools at startup without aborting. Then `load_skills` (L76) and `refresh_list_available_tools_args` (L86).
- Registration APIs: instance decorator `ToolRegistry.tool(...)` (registry.py:408-435), `ToolRegistry.register(...)` (L459-694), module-level decorator `tool(registry, ...)` (L1817-1849). Each tool module exposes a `register_*_tools(registry)` function (e.g. meta_tools.py:51, skills.py:271).
- Descriptor kwargs are a CLOSED set: `_DESCRIPTOR_KWARGS` (registry.py:442-454) + `parameters`/`field_extras` (L455-457); unknown kwargs raise `ValueError` (L486-491); `validate_descriptor_fields` runs at register time and raises on invalid vocab (L497-524, impl descriptor.py:405-530).
- Schema: args model from explicit `args_model`, explicit `parameters` (validation model still generated), or signature-derived pydantic model (`_generate_model`, L738-772; `**kwargs` → `extra="allow"` L765-771). OpenAI-style schema stored in `_schemas` (L568-579).
- Dedup/collision: same name + different func → **warning + silent overwrite, last-wins** (registry.py:525-535, "旧 X -> 新 Y"); same-name schema replaced for uniqueness (L580-582). `update_args_model` is the sanctioned in-place schema mutation (L777-804). No namespace/ownership checks.
- Caches (`_descriptor_cache`, schema/descriptor/registry fingerprints) invalidated on register/update (L690-694, L800-804). Registry fingerprint is order-independent (L994-1005).
- Other `init_tools` call sites rebuild full registries for validation/eval: `app/lib/gis/runtime_manifest.py:263-264`, `app/services/gis_harness/registry_validation.py:259-260`, `app/evaluation/runner.py:175-176`. Strict manifest validation at startup (main.py:75-86).

## 4. Tool surface exposed to Pi + gating
- Surface projection: `pi_native_surface.py`. Frozen `NATIVE_TOOL_NAMES` 7 tools (L20-29). Spawn dump v2 = registered superset + default_active: `registered_surface_names` = all tools with `model_visible`, not `EXTERNAL_UNAVAILABLE`, `tier<3` and `effective_security_tier<3` (L256-273); `pi_surface_for_spawn` (L296-318); dumped to `native-tools.json` and passed via `WEBGIS_NATIVE_TOOLS_PATH` at Pi spawn (pi_rpc_client.py:280-287), fail-fast.
- JS extension registers every dumped tool + `webgis_execute` proxy (index.mjs, "export default function webgisToolsExtension"); per-turn active subset applied via `pi.setActiveTools` driven by a `[WEBGIS_ACTIVE_TOOLS:[...]]` prompt marker, hard ceiling 48, only names in registered superset (index.mjs `applyActiveTools`).
- Per-turn selection: `compute_turn_active_tools` (pi_native_surface.py:355-401) → `DynamicToolSurface.select` (tool_surface_v3.py:188+) with contract filter dropping HIDDEN/PLANNED/EXTERNAL_UNAVAILABLE/tier>=3 + role side-effect policy (tool_surface_v3.py:174-186); native front door always included; final tier double-check (pi_native_surface.py:390-398). Feature flag `PI_DYNAMIC_TOOL_SURFACE` (L253).
- Dispatch path: extension POSTs `/pi-tools/execute` with `X-Pi-Bridge-Secret` (pi_tools.py:36-47) and signed turn token (L61-63) → `dispatch_tool` (agent_pi_bridge.py:427+): classify via `resolve_pi_tool_call` (pi_native_surface.py:73-150: native | execute(wrap) | reject; unknown bare names reject with discover guidance L142-150), registry existence check (bridge L480-493), **tier>=3 hard reject at the bridge** (L495-506), then shared `ToolDispatchService` (L508-570).
- Deepest gate: registry `_dispatch_impl` refuses tier>=3 unless `confirm_tier3()` ContextVar is set in the calling context (registry.py:81-88, 116-127, 1213-1218); PLANNED tools unexecutable (L1201-1206). Admin channel: chat route uses `confirm_tier3` (app/api/routes/chat.py:2008). Legacy path gating: `ToolCatalog` tier-1 always / tier-2 domain / tier-3 never (tool_catalog.py:216-231); `list_available_tools` hides tier-3 with count disclosure (meta_tools.py:107-121).

## 5. Existing extension seams
- `app/extensions/webgis-tools/` is **not** a Python plugin system — it is the vendored Pi runtime's JS extension (index.mjs = the loaded artifact; index.ts is a documented dead copy, #694). It is the only "extension" concept, and it registers Pi-host-side tool shells that proxy back over HTTP.
- Dynamic skill loading: `app/tools/skills.py:439-454` scans `app/skills/*.py` calling module `register_skills(registry)` or `register(registry)` (L425-430), plus `.md` prompt skills. Hot reload via `create_new_skill` (tier-3, gated by `ALLOW_DYNAMIC_SKILLS=true`, skills.py:308-314; AST deny-list + restricted builtins, L12-100/377-409) → `load_skills` (L365). `refresh_skill_surface` (tier-3) re-scans and honestly reports the native-schema layer stays frozen until Pi worker respawn (skill_surface_refresh.py:34-90).
- What does NOT exist: no Python-side plugin/domain-pack loader, no entry-point discovery, no per-plugin registry isolation, no third-party contributor path besides editing `_TOOL_MODULES` or dropping a sandboxed skill file.

## 6. Gaps/risks for a third-party Tool Extension SDK
1. **No install seam**: a new built-in module must be hand-added to `_TOOL_MODULES` (app/tools/__init__.py:11-57); startup failure mode is a silent per-module warning-drop (L70-71), so a broken plugin's absence is easy to miss (descriptor docstring claims "registration failure → process startup failure" at descriptor.py:436-440, which `init_tools`' catch neutralizes).
2. **Collision policy is warn+overwrite (last-wins)** (registry.py:525-535): a third-party tool reusing a core name silently replaces a live tool; no namespace, no ownership, no reject option.
3. **Closed descriptor vocabulary**: register kwargs are a frozen tuple (registry.py:442-457, unknown→ValueError L486-491); status/side-effect/latency/memory/scale/mutation vocabularies are fixed enums in descriptor.py — a plugin cannot extend the contract without core changes (versioning hazard for an SDK).
4. **Category manifest misses third parties**: `categories._MODULE_DEFAULTS` keys on core module paths; external modules land `uncategorized` (categories.py:135-139) and audit tests expect declarations.
5. **Pi surface latency**: native schema surface is frozen at Pi worker spawn (skill_surface_refresh.py:1-15, pi_rpc_client.py:280-287); dynamically registered tools are reachable via `webgis_execute` immediately but only get first-class schemas after respawn — an SDK must handle this two-layer truth.
6. **Gating contracts are implicit**: plugins must set `tier`/`side_effect`/`security_tier` honestly or their tools get rejected at the bridge (agent_pi_bridge.py:495-506), uncallable via dispatch (registry.py:1213-1218), or invisibly unprojected (pi_native_surface.py:256-273). tier>=3 forces `side_effect=destructive` (registry.py:629-631).
7. **Static alias table**: `TOOL_NAME_ALIASES` is hard-coded in argument_normalization.py:105 (~34 entries); plugins cannot register aliases; ref-cursor support requires `json_schema_extra={"ref_cursor": True}` / `{"capture_ref_of": ...}` conventions (registry.py:696-736) that are documented only in code.
8. **Single-process singleton assumption**: registry injected once at startup (main.py:68-94); validation/eval paths rebuild the *full* registry from `_TOOL_MODULES` (runtime_manifest.py:263-264, evaluation/runner.py:175-176) — a plugin loaded out-of-band would vanish in those contexts.
9. **JS side is hand-maintained**: extension authors must edit `index.mjs` (the `.ts` source is a dead copy per #694); bridge secret + turn-token plumbing (pi_tools.py:36-63) must be replicated by any new host integration.
