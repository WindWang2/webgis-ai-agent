# 01 — Extension-Points Inventory (current state)

Every seam where new capability can enter the system today, plus what is missing for third parties.
All citations from raw reports 10–80 (source of truth).

## 1. Tools — ToolRegistry (execution truth)

| Seam | Where | Mechanism |
|---|---|---|
| Global construction | `app/main.py:68-69` | `ToolRegistry()` + `init_tools(registry)` in lifespan; injected into services (main.py:89) and Pi bridge (main.py:93-94) |
| Startup loader | `app/tools/__init__.py:61-90` | iterates hard-coded `_TOOL_MODULES` tuple (L11-57, ~45 entries), lazy import, per-module exceptions logged as warnings (L70-71) |
| Registration APIs | `app/tools/registry.py:408-435` (decorator `tool`), `:459-694` (`register`), `:1817-1849` (module-level) | closed kwargs set `_DESCRIPTOR_KWARGS` (registry.py:442-457); unknown kwargs raise (L486-491) |
| Collision | `app/tools/registry.py:525-535` | warn + silent overwrite, **last-wins**; no namespace/ownership |
| Dynamic skills | `app/tools/skills.py:439-454` | scans `app/skills/*.py` for `register_skills(registry)`/`register(registry)` (L425-430) + `.md` prompt skills |
| Skill hot reload | `app/tools/skills.py:308-314,365`; `app/tools/skill_surface_refresh.py:34-90` | tier-3 `create_new_skill` (gated `ALLOW_DYNAMIC_SKILLS`) and `refresh_skill_surface`; registry layer hot, native schema frozen at spawn |
| Retrieval plugin | `app/services/chat/tool_surface_v3.py:37-40` | env `TOOL_RETRIEVAL_SEMANTIC="module:callable"` injected into DynamicToolSurface |

**Missing for third parties:** no install seam (must edit `_TOOL_MODULES`); silent warning-drop on broken module (`__init__.py:70-71`); last-wins collisions let a plugin shadow core tools; closed descriptor vocabulary (registry.py:442-457, descriptor.py vocabularies); no aliases registration (hard-coded `TOOL_NAME_ALIASES`, argument_normalization.py:105); no entry-point discovery, per-plugin isolation, or unload (raw 10 §5-6).

## 2. Algorithms + Capabilities (semantic catalog)

| Seam | Where | Mechanism |
|---|---|---|
| Domain packs | `app/lib/gis/algorithms/__init__.py:26-36` | 13 hard-coded modules in `_ALL_MODULES`, each exporting `ALGORITHMS`; `iter_domain_packs()` deterministic order |
| Singleton build | `app/lib/gis/algorithm_registry.py:503-514,208-212` | `get_algorithm_registry()` → `load_builtins()` → `register()` once; no runtime re-registration; `reset_algorithm_registry()` test-only (L511-513) |
| Capability packs | `app/lib/gis/capabilities/__init__.py:11-19` | 11 modules exporting `CAPABILITIES` |
| Collision | `algorithm_registry.py:214-216`; `capability_registry.py:67-69` | duplicate id → `ValueError` (raise) |

**Missing:** no runtime register path outside builtins/tests (raw 20 §7.1); conformance validation is repo-layout bound (`algorithm_registry.py:471-479`, requires `tests/` under CWD) so PRODUCTION/VALIDATED maturity is unreachable out-of-repo; closed vocabularies (`crs_class`, `BACKEND_VOCABULARY`, etc., algorithm_registry.py:23-45) need core edits; tool binding by name string only (algorithm_resolver.py:117-120).

## 3. Cartography (6 singleton registries)

| Seam | Where | Mechanism |
|---|---|---|
| Component registry | `app/lib/cartography/component_registry.py:426-436` | public `register()`; collision = raise (L427-428); 17 seeds (L78-405) |
| Model library | `app/lib/cartography/model_library.py:504-507` | `register()` **silently ignores duplicate ids**; packs raise in `load_builtins` (L498-502) |
| Model/composition packs | `model_packs/` + `composition_packs/`, loaded `model_library.py:495-502`, `composition_templates.py:240-246` | deterministic seed-then-pack order, dup id = raise |
| Themes | `app/lib/cartography/themes.py:435,443-447` | `CartographicThemeRegistry` load is dict-comprehension **last-wins, no check** |
| Catalog export | `app/lib/cartography/export_component_catalog.py:33-133` | versioned (schemaVersion 4) frontend contract JSON |

**Missing:** no external plugin loader or namespacing; three inconsistent collision policies (raise / silent-ignore / last-wins); adding a `ComponentType` touches 4+ files (closed Literal `app/services/gis_harness/components.py:18-43`, TS union `frontend/lib/mapspec-compiler/types.ts:171-201`, `_SUPPORT_MATRIX` reverse check `component_renderers.py:234-238`); renderer/exporter registration is frontend-TS only, no backend plugin interface; no per-type options schema registry for free-form `options` dicts (raw 30 §6).

## 4. Recipes / workflow packs

| Seam | Where | Mechanism |
|---|---|---|
| Pack contract | `app/services/gis_harness/recipe_packs/__init__.py:18-45` | 24 modules in `PACK_MODULES`, each exports `RECIPES: List[CartographyRecipe]`; `_kit.py` builders |
| Load | `app/services/gis_harness/recipes.py:712-742` | seeds (17 inline, L257) then packs in name order; fail-loud aggregation |
| Collision | `recipes.py:744-768` | duplicate id → **keep-first** + warning (seeds win); no runtime unregister |
| Routing | `recipes.py:927-1005` | deterministic 11-key tuple incl. seed-seniority demotion; not pluggable |

**Missing:** packs are hard-coded imports (no entry-point/manifest discovery, no packaged YAML/JSON, no signing/dependency metadata); keep-first collision decides by module order only; corpus authoring is in-tree code (`app/evaluation/conformance.py:104` `CONFORMANCE_FAMILIES`); no per-pack enable/disable or schema-version negotiation (raw 40 §6).

## 5. Data providers (data fabric)

| Seam | Where | Mechanism |
|---|---|---|
| Adapter ABC | `app/services/data_fabric/base_adapter.py:18-85` | `GeospatialDataSourceAdapter` (probe/capabilities/list_datasets/describe/preview/query/health); sync, run via `asyncio.to_thread` (`tools/data_fabric_tools.py:154`) |
| Registry | `app/services/data_fabric/registry.py:56-101,105-169` | `AdapterRegistry.register/resolve/build_adapter`; table is a **closed literal list** in `_build_registry()`; alias rebinding raises (L67-71); lazily built singleton (L175-179) |
| V2 capabilities override | `app/services/data_fabric/query/capabilities.py:237` | `get_capabilities(source_type, overrides)` |
| Secondary provider Protocol | `app/tools/chinese_maps/protocol.py:30-79` | `ChineseMapsProvider` + fallback dispatch (`http.py:125-206`) |
| Health tracker | `app/services/provider_health.py:12,26-159` | `PROVIDER_NAMES` frozenset is label-only — arbitrary keys work |

**Missing:** no manifest/entry-point discovery or per-org enablement; `mapspec_source.DATAFABRIC_SOURCE_TYPES` (`mapspec_source.py:19`) and capabilities defaults need hand edits per new type; sync-only ABC (no `fetch_tile`/`fetch_asset`/`stream`); split egress policy (raw 60 §3); plaintext credentials (raw 60 §3); no `ArtifactContract.from_fabric_descriptor` bridge (raw 60 §5).

## 6. Skills (two unrelated concepts)

- GIS skills: `.md` prompt skills + `.py` with `register_skills(registry)`, restricted-builtins exec (`app/tools/skills.py:377-409,412-436`); scanned at startup (`app/tools/__init__.py:75-76`).
- `skills-lock.json` + `agent/skills/` are coding-assistant prompts, not runtime (raw 50 §2b).

## 7. Pi bridge (vendored runtime)

| Seam | Where | Mechanism |
|---|---|---|
| Extension paths | `app/main.py:111-112`, `app/agent_pi_bridge.py:2515-2541` | `--extension <path>` per extension — **multiple extensions structurally supported** |
| JS extension | `app/extensions/webgis-tools/index.mjs` | registers dumped schemas + `webgis_execute` proxy; per-turn active set via `[WEBGIS_ACTIVE_TOOLS:[...]]` marker, `MAX_ACTIVE=48` |
| Surface dump | `app/services/chat/pi_rpc_client.py:280-287`; `pi_native_surface.py:296-318` | `native-tools.json` fail-fast at spawn; **frozen at spawn** until worker respawn |
| Registry injection | `app/agent_pi_bridge.py:173-185` | `set_tool_registry()` recompiles Runtime Manifest |
| Tool callback | `app/api/routes/pi_tools.py:36-63` | bridge secret + HMAC turn token + live-turn check |
| Model providers | `app/services/chat/pi_rpc_client.py:235-260` | `models.json` single provider `"webgis"`; descriptors config-driven (`MODEL_DESCRIPTORS_FILE`, `descriptors.py:97-143`) |

**Missing:** JS side hand-maintained (index.ts dead copy, #694); no toolchain for extension authors to ship their own JS/Python bundle; model adapter has no Protocol — two free functions `call_llm`/`call_llm_stream` hardcoded to OpenAI shape (`llm_client.py:378,444`), `provider_id="webgis"` hardcoded (`routing.py:78`, `descriptors.py:111`), one global `LLM_API_KEY` (raw 70 §5).

## 8. What is absent everywhere (platform-level)

1. No `importlib.metadata`/entry-points usage anywhere (grep zero hits, raw 80 §C); plain in-repo imports.
2. No manifest format, no discovery, no dependency resolution, no load/unload lifecycle, no namespacing, no permission model, no quarantine, no compatibility negotiation for any domain.
3. Per-process singletons with test-only `reset_*` assume immutable seeds; multi-worker consistency relies on identical seeds (raw 30 §6.7, raw 20 §7.6).
