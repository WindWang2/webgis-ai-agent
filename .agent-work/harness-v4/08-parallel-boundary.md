# 08 — Parallel Boundary & Registry Audit (GIS Harness V4)

Repo: `webgis-ai-agent-harness-v4` @ `16d1c70`. Read-only audit. All paths relative to repo root.
Frozen seams: ToolRegistry, CapabilityRegistry, AlgorithmRegistry, SessionPlan, MapSpec, ArtifactContract, ExecutionPlan.

---

## (a) Per-registry table

| Seam | Canonical definition | Type | Persistence | Owners/writers | Key mutation sites | Freeze risk |
|---|---|---|---|---|---|---|
| **ToolRegistry** | `app/tools/registry.py:387` (`class ToolRegistry`) | Plain class, per-process instance (NOT pydantic, NOT DB, NOT singleton-by-construction) | None (in-memory, rebuilt each lifespan; `registry_fingerprint()` at `registry.py:994` pins content for stale-plan detection) | Injected once at startup `app/main.py:68-99` → `set_app_registry` (`app/services/chat/engine_instance.py:32`), `chat.registry`, Pi bridge `set_tool_registry`; shared by ChatEngine, dispatch service, subagents | `register()` `registry.py:459` (validation `486-524`, metadata write `631-672`), decorator `tool()` `:408`, `update_args_model()` `:777`; 43 registrar modules via `_TOOL_MODULES` → `init_tools()` `app/tools/__init__.py:61-77`; dynamic skills `app/tools/skills.py:load_skills`; `refresh_list_available_tools_args` (`__init__.py:87`) | **High** — single execution chokepoint `dispatch()` `:1014` / `_dispatch_impl()` `:1183`; tier-3 ContextVar gate `:81,:1213` (SEC-F1); arg-lineage capture `:96`; policy audit at register `:675`. Additive only: descriptor kwargs (`_DESCRIPTOR_KWARGS` `:442`), unknown kwargs raise `:486` |
| **CapabilityRegistry** | `app/lib/gis/capability_registry.py:56` (`class CapabilityRegistry`); descriptor `CapabilityDescriptor` (pydantic) `:18` | Plain dict-index class + pydantic descriptors; process singleton via `get_capability_registry()` `:112` / `reset_capability_registry()` `:120` | None (in-process; seeded once, static after load) | `load_builtins()` `:62` ← `iter_capability_packs()` (`app/lib/gis/capabilities/__init__.py:20`, 11 domain modules each exporting `CAPABILITIES`) | `register()` `:67` (duplicate id raises); `load_builtins()` `:62` (only bulk writer) | Low — extension = new domain module + one entry in aggregator tuple. Cross-checked by `validate()` `:95` and `app/services/gis_harness/registry_validation.py:validate_gis_library` |
| **AlgorithmRegistry** | `app/lib/gis/algorithm_registry.py:199`; descriptor `AlgorithmDescriptor` (pydantic, ~40 fields incl. VNext science metadata) `:91` | Same pattern: dict-index class + pydantic descriptors; singleton `get_algorithm_registry()` `:503` / `reset_algorithm_registry()` `:511` | None (in-process seed) + generated doc `docs/science/ALGORITHM_CATALOG.md` | `load_builtins()` `:208` ← `iter_domain_packs()` (`app/lib/gis/algorithms/__init__.py:33`, 13 modules exporting `ALGORITHMS` + optional `PARAMETER_CONTRACTS` via `iter_contract_packs()` `:40`) | `register()` `:214` (dup raises; invalidates derived-index caches `:217`); derived views `tool_to_capability()` `:256`, `tool_to_algorithms()` `:286`, `capability_tool_map()` `:305` | Low-Med — derived indexes feed ToolRegistry capability backfill (`app/tools/registry.py:53-73`) and SessionPlan progress (`app/services/session_plan.py:409-414`); changes ripple into manifest fingerprint (`app/lib/gis/runtime_manifest.py`) → stale-plan flags |
| **SessionPlan** | `app/services/session_plan.py:67` (`class SessionPlan(BaseModel)`), plus `CapabilityProgress` `:59`, `SessionPlanEvent` `:81` | **Pydantic models**, persisted documents (not DB tables) | SessionStore (`session_data_manager`) under alias `session-plan` (`:23-26`), history alias `session-plan-id:<envelope>`; load/save `:284`,`:307`; envelope slot create `:323` (session lock, fail-closed `:339`) | Pi path only — "ChatEngine does not read or write this object" (module docstring `:1-7`); subagent engines suppress plan advancement (`is_subagent_engine`) | `apply_tool_result()` `:481` (whole load→mutate→save under per-session distributed lock `:506`); intent supersede constructs new envelope `:601-613`; replace/void `:616-629`; `_mark_progress()` `:442`; `merge_map_product_result()` `:518`; Pi bridge call sites `app/agent_pi_bridge.py:475,673,695,760,782`; artifact registration `:678` | **High** — this is the host-plan truth (ADR-0076); mutation is semantic (capability completion). Extending = additive fields + new tool-result branches in `_apply_tool_result_unlocked` `:548` |
| **MapSpec** | **No class** — a plain `dict` (desired cartographic state; ADR-0008 shape, ADR-0054/0057/0058 semantics). Store class `MapSpecStore` `app/services/mapspec/store.py:155`; deep engine `MapSpecLifecycleEngine` `app/services/mapspec/lifecycle_engine.py:522` | Dict + service classes (store is a file/Redis persistence manager, not a model) | Disk: `settings.DATA_DIR/.webgis-agent/<session>/mapspec.json` + no-op fingerprint sidecar `mapspec.json.fp` (`store.py:38,148`) + revision CAS sidecar `mapspec.json.rev` (`:152`) + full revision snapshots; Redis mirror in session layer | `MapSpecLifecycleEngine.apply_mutation(intent, ...)` `lifecycle_engine.py:581` is the single atomic mutator (per-session lock + CAS revision, ADR-0058). Compat adapter `app/services/mapspec_store.py:84` delegates to engine (ADR-0016) | Intent types from `app/services/mapspec/__init__.py` (InitProject/SetView/UpsertLayer/RemoveLayer/SetLayout/Checkpoint/Rollback/…). Callers: `app/tools/cartography_tools.py`, `app/tools/layer_manager.py`, `app/tools/templates.py`, `app/tools/chart.py`, `app/tools/report.py`, `app/services/gis_harness/tools.py`, `app/services/spatial_decision/mapspec_integration.py`, API `app/api/routes/mapspec_mutations.py`, `project.py`, `chat.py`, `report.py`; user-Chrome wins path `app/services/gis_world_state/` | **High** — "唯一 desired cartographic state"; every new mutation MUST be a new intent through the engine, never direct dict writes |
| **ArtifactContract** | `app/lib/data/artifact_contract.py:173` (`class ArtifactContract(BaseModel)`) | **Pydantic model**, explicitly a *read-only projection* ("一个产物一份，不落第二份真相", `:174`) | None itself — projected from: artifact records (`from_artifact_record` `:351`), ref descriptors (`from_ref_descriptor` `:453`), DB rows (`from_db_artifact` `:508`), raster descriptors (`from_raster_descriptor` `:551`). Underlying facts live in `app/services/artifact_registry.py` (records/lineage, ADR-0082) | Fact writers: `register_artifact()` in `app/services/artifact_registry.py` (called from dispatch seam + plan-apply seam `app/services/session_plan.py:678`); contract projection rebuilt per read | Consumers: `app/services/data_catalog/catalog.py:202`, `data_catalog/lineage_query.py:26`, `app/services/workspace/snapshot.py:37`, `app/services/data_lifecycle/service.py:30` | Low — controlled vocabulary escape hatch exists: `artifact_type` validated via `vocab.coerce_category` + `vocabulary.register_category` (`:212-221`) = the sanctioned additive extension point |
| **ExecutionPlan** | `app/services/geocompute/plan.py:217` (`class ExecutionPlan(BaseModel)`); node `ExecutionNode` (semantic fingerprint `:190-205`), `ResourceBudget` `:130`, `NodeEvidence` `:249`, `ExecutionRun` `:279` | **Pydantic models** (API request/response, not persisted as tables; runs are evidence objects) | Ephemeral per request; `plan_id`/`graph_fingerprint` (`:228`) used for cache/reuse keyed storage in geocompute runtime | Constructed from API: `app/api/routes/geocompute.py:102`, `app/services/geocompute/api.py:101`; executed by geocompute engine (node-level admission) | Budget enforcement: hierarchical scope tree `app/services/geocompute/budgets.py` (`ScopeKind` global→tenant→project→session→execution→node; `BudgetLimits.max_concurrency` D3/D10; `BudgetExceededError`) | Medium — "V4 additive" fields already precedent (`NodeEvidence.failure_codes`, `checkpoint_verified` `plan.py:264-268`); versioned fingerprints (`EXECUTION_PLAN_VERSION`) make additive fields safe |

Notes: none of the seven is a DB table. Pydantic models: SessionPlan, ArtifactContract, ExecutionPlan (+ all descriptors). Plain classes: ToolRegistry, CapabilityRegistry, AlgorithmRegistry, MapSpecStore/LifecycleEngine (MapSpec itself = dict). Singletons: Capability/Algorithm registries (module-level `_registry`); ToolRegistry is a single injected instance (de-facto singleton, but constructible in tests/eval — `app/evaluation/runner.py:175`, `app/lib/gis/runtime_manifest.py:263`).

---

## (b) Extension mechanisms that already exist (how third-party/V4 capabilities attach WITHOUT touching frozen seams)

1. **Domain packs (the house pattern, ADR-0099 §34).** Three parallel aggregator modules, each a tuple/list of child modules; "new domain = new module + register in aggregator":
   - Algorithms: `app/lib/gis/algorithms/<domain>.py` exporting `ALGORITHMS` (+ optional `PARAMETER_CONTRACTS`); aggregated by `_ALL_MODULES` / `iter_domain_packs()` / `iter_contract_packs()` (`app/lib/gis/algorithms/__init__.py:21-48`). 13 packs today (aggregation, data_access, decision, density, geometry, interpolation, network, point_pattern, raster, remote_sensing, statistics, temporal, terrain).
   - Capabilities: `app/lib/gis/capabilities/<domain>.py` exporting `CAPABILITIES`; `iter_capability_packs()` (`app/lib/gis/capabilities/__init__.py:20-30`). 11 packs.
   - Recipes: `app/services/gis_harness/recipe_packs/<domain>.py` exporting `RECIPES: List[CartographyRecipe]`; `PACK_MODULES` tuple (24 entries) + `iter_recipe_packs()` (`app/services/gis_harness/recipe_packs/__init__.py:27-56`). Deterministic load order; duplicate ids resolved keep-first by seed registry; parity tests pin no-overlap.
2. **Tool modules.** New file `app/tools/<name>.py` with `register_<name>_tools(registry)` + one line in `_TOOL_MODULES` (`app/tools/__init__.py:25-56`); `init_tools` is failure-isolated (`__init__.py:64-71`). Rich descriptor kwargs (side_effect, cost, tier, security_tier, capabilities, produced_refs, …) declare semantics at registration; unknown kwargs fail loudly (`registry.py:486`).
3. **Skills.** Markdown skills in `app/skills/*.md` (frontmatter; 6 today: heatmap, site_selection, urban_planning, disaster_risk, local-geodata, local-stats) loaded by `app/tools/skills.py`; sandboxed `.py` skill loader with blocked imports/builtins/attrs (`skills.py:8-50`); hot refresh surface per ADR-0100 (`app/tools/skill_surface_refresh`).
4. **Pi extension.** `app/extensions/webgis-tools/index.{ts,mjs}` registers ONE proxy tool `webgis_execute` that HTTP-calls back into Python `/pi-tools/execute` (bridge-secret). Pi native surface is frozen at 7 tools; everything else attaches server-side (`.mjs` is the live copy, `.ts` is a dead source — `index.ts:3-6`).
5. **Data-source adapters.** `app/adapters/base.py:BaseDataAdapter` (`discover/quick_assess/fetch/parse/get_field_schema`); concrete `app/adapters/gov/gov_data_adapter.py`. Third-party data providers attach as new adapter subclasses, no registry edits.
6. **Controlled vocabularies / minor registries** (all additive): artifact categories via `vocabulary.register_category` (`app/lib/data/artifact_contract.py:218`); artifact types `app/lib/gis/artifacts.py:ArtifactTypeRegistry`; parameter contracts per domain pack; product/template/style registries (`app/services/gis_harness/product_templates.py`, `template_catalog.py`, `app/lib/cartography/model_library.py`); spatial-decision rule packs (`get_rule_pack_registry()`); model role profiles `app/services/chat/model_runtime/roles.py`.
7. **Cross-registry integrity + parity tests as the contract gate.** `app/services/gis_harness/registry_validation.py:validate_gis_library()` (Capability ↔ Algorithm ↔ Artifact ↔ MapModel ↔ Recipe ↔ ProductTemplate ↔ Style ↔ Tool existence); startup fail-fast via `compile_runtime_manifest` + `validate_runtime_manifest_strict` (`app/main.py:74-87`, escape `GIS_MANIFEST_STRICT=0`). Parity-test convention: `tests/unit/test_capability_registry_parity.py`, `test_component_catalog_parity.py`, `test_compiler_parity.py`, `test_protocol_parity.py`, etc.

---

## (c) Subagent infrastructure status vs Wave-6 needs

**What exists (all in-process; Pi does NOT host child agents — Pi hosts main turns only, subagents run as isolated ChatEngine instances inside the Python process):**

| Mechanism | Location | Status |
|---|---|---|
| Dispatcher (isolated engine, shared registry/refs/session) | `app/services/subagent.py:157` (`SubagentDispatcher`) | Done. Recursion depth ≤2 (`:188`); honest settle `:432-448`; refs diff returned `:439` |
| Tool allowlist (tier1 + domain tier2 + extra_tools; tier3 always excluded) | `select_tools_for_subagent` `app/services/subagent.py:85-151`; always-blacklist `spawn_subagent/propose_plan/execute_plan/get_plan_status` `:113` | Done. SEC-F2: extra_tools cannot override tier-3 exclusion `:130` |
| Mutation policy (fail-closed allow-list by descriptor `side_effect`) | `subagent.py:113-149` (`_READONLY_CLASSES`, allow_mutation=False → only pure/deterministic_compute/cacheable_read survive) | Done, but depends on descriptor side-effect enrichment coverage (see `docs/agent-runtime/tool-descriptor.md`) |
| Role definitions (frozen dataclass: name, title, model_role, allowed_domains, max_rounds, max_wall_time_s, max_tool_calls, max_heavy_tool_calls, allow_mutation, tool_blacklist) | `app/services/subagent_roles.py:43-56`; 6 built-ins `:60-123`: `data_researcher`, `gis_inspector`, `scientific_reviewer`, `cartography_reviewer`, `tool_result_verifier`, `cheap_summarizer` | Done as *policy class*; role∩caller-args intersection takes the stricter value (`subagent.py:214-221`) — matches "constraints may only NARROW caller permissions" |
| Budgets (tool calls / heavy calls / wall clock; `BudgetExceeded` is `BaseException` so broad `except Exception` can't swallow it) | `SubagentBudget` `subagent_roles.py:139-172`; dispatch wrapper `:175-197`; enforcement in `subagent.py:262-276,317-345,396-410` | Done per-run. Failure codes: `budget_exceeded:wall_time`, `budget_exceeded:tools`, `cancelled` |
| Cancellation parent→child | token link `subagent.py:283-293` (ADR-0100) | Done |
| Model role routing | `sub_engine.model_role` `subagent.py:252`; roles `subagent_worker`/`subagent_reviewer`/`structured_extraction` `app/services/chat/model_runtime/roles.py:80,84` | Done |
| Trace events | `EVENT_SUBAGENT_SPAWNED/COMPLETED` `app/lib/runtime/trace.py:42-43` | Done |
| LLM-facing spawn tool | `spawn_subagent` `app/tools/subagent.py:21-88` (tier=2, domains meta/what_if) | **Gap: no `role` parameter** — LLM can only spawn adhoc subagents; the 6 roles are reachable only from Python callers/tests |

**Gap table vs the 9 Wave-6 specialists:**

| Wave-6 specialist | Nearest existing role | Gap |
|---|---|---|
| Planner | — (none; `SessionPlan` progress is capability-based, planner is deterministic `app/services/gis_harness/planner_runtime.py`) | New role + expected-output schema; must stay read-only w.r.t. SessionPlan or go through intent tools |
| Data Inspector | `gis_inspector` (`subagent_roles.py:74`) | None material — reuse/rename |
| Spatial Scientist | `data_researcher` (`:62`) | Needs analysis-tool domains (spatial/statistics) with modest heavy budget; today researcher is lookup-oriented |
| Algorithm Reviewer | `scientific_reviewer` (`:84`) | Close; add method_references/conformance awareness (AlgorithmDescriptor VNext fields) |
| Cartography Reviewer | `cartography_reviewer` (`:94`) | 1:1 |
| Map Observer | — | New read-only role (render observation / verdict tools, e.g. `webgis_cartography_status`) |
| Result Verifier | `tool_result_verifier` (`:104`) | 1:1 |
| Cheap Corpus Worker | `cheap_summarizer` (`:114`) | Today max_tool_calls=0; corpus worker likely needs cheap read tools → new role variant |
| Documentation Cross-checker | — | New role (read docs/specs; zero heavy tools) |

**Cross-cutting gaps:**
1. **Role not exposed on the LLM surface** (`app/tools/subagent.py:55-61`) — Wave-6 delegation can't select specialists today.
2. **No expected-outputs / failure-behavior fields in `SubagentRole`** — failure behavior is uniform (`SubagentResult(success, summary, refs, error)`); output contracts must be added additively (new frozen-dataclass fields are additive since roles are values, not the frozen registries).
3. **No parallel subagent spawn** — docstring still lists it as future work (`subagent.py:22-25`); parallelism today exists only at the *tool-call wave* level (`app/services/chat/execution_engine.py:2165-2418`, `app/services/tool_dispatch_service.py:316-321` wave semaphore `TOOL_WAVE_CONCURRENCY` default 5, per-session fairness gate `:248-255`, heavy tool = 2 slots `:463-470`) and at the geocompute node level (`app/services/geocompute/budgets.py` max_concurrency).
4. **No budget roll-up** — `SubagentBudget` counts one run; there is no turn→agent→subagent aggregation (ADR-0101 §32 names the hierarchy; only the subagent level is implemented). Parent turn has tool-metrics accounting (`app/services/tool_metrics`) but not a shared quota with children.
5. **`SubagentResult` has no structured expected-output slot** (free-text summary + refs only).

---

## (d) Most relevant ADRs (titles in `docs/adr/`, 114 files, 0001→0103)

Full listing highlights: 0005 tiered-tool-catalog, 0006 unified-tool-dispatch, 0043 tool-execution-policy, 0051 harness-evaluation-v2, 0054-0058 mapspec desired/observed/CAS, 0068 all-tool-execution-through-dispatch-service, 0076 sessionplan, 0077 multi-pod/turn-ownership + wrap-vendored-pi, 0080 unified-gis-runtime-v3, 0081 map-product-completion, 0082 artifact-runtime, 0083 cost-aware-algorithm-resolution, 0088 autonomous-gis-product-runtime, 0092 reproducible-runtime, 0093 correctness-concurrency-v5, 0096 agent-product-plane-vnext + geocompute-data-plane-v3, 0099 map-product-lifecycle-v2 + spatial-science-geoai-vnext, 0100 pi-runtime-v6, 0101 agent-tool-model-runtime-v2 (+0101 template-library, geocompute-data-fabric-v4, geoworkflow-recipe-conformance), 0102 model-provider-runtime, 0103 cartographic-design-system-v4 + data-artifact-workspace-v3 + pi-gis-runtime-v3.

Five read in full (2-line summaries):

1. **ADR-0101 Agent Tooling & Model Runtime Foundation V2** (`docs/adr/0101-agent-tool-model-runtime-v2.md`): Pi stays the agent host and ToolRegistry stays execution truth; V2 adds projections/adapters (ToolDescriptor V2, fingerprints, alias tables, trace/replay) without moving any truth. Explicitly freezes the hard seams list (dispatch chokepoint, tier-3 ContextVar, frozen 7-tool Pi surface).
2. **ADR-0076 SessionPlan is the Pi-path plan truth** (`0076-sessionplan-is-pi-path-plan-truth.md`): SessionPlan is a Session-keyed SessionStore envelope whose GIS chapter embeds MapProductPlan; progress = capability completion, not tool-call sequences; ChatEngine's CanonicalPlan remains fallback-only.
3. **ADR-0082 GIS Artifact Runtime** (`0082-artifact-runtime.md`): adds an artifact facts layer (records/lineage/lifecycle) over the previously bare `bound_ref` pointers, without breaking the three binding surfaces; `ArtifactContract` is a projection of that facts layer, never a second truth.
4. **ADR-0099 Map Product Lifecycle V2** (`0099-map-product-lifecycle-v2.md`): ledger rows immutable; every lifecycle op is a new row + lineage edge (DAG evidence). Its §34 defines the domain-pack architecture the registries now use (seeds in per-domain modules, central registry only aggregates).
5. **ADR-0100 Pi Runtime V6** (`0100-pi-runtime-v6-cancellation-fairness.md`): unified cancellation (tracker↔bridge token join), per-session wave fairness, subagent cancel wiring, and truthful skill-surface refresh (registry-level hot refresh; native surface frozen at spawn).

---

## (e) Recommended module layout for V4 code (additive; zero edits to frozen seam logic)

Follow the existing conventions exactly:

```
app/lib/gis/capabilities/<v4_domain>.py      # new CAPABILITIES list → append to iter_capability_packs
app/lib/gis/algorithms/<v4_domain>.py        # new ALGORITHMS (+ PARAMETER_CONTRACTS) → append to _ALL_MODULES
app/tools/v4_<domain>.py                     # register_v4_<domain>_tools(registry) → one _TOOL_MODULES entry
                                             #   (declare side_effect/cost/tier/security_tier/capabilities at registration)
app/services/gis_harness/recipe_packs/<v4_domain>.py  # RECIPES → append to PACK_MODULES
app/services/subagent_roles.py               # ADD entries to SUBAGENT_ROLES (Planner, Map Observer, Spatial Scientist,
                                             #   Algorithm Reviewer, Cheap Corpus Worker, Doc Cross-checker) — additive dict
                                             #   entries; extend SubagentRole with expected_outputs/failure_behavior fields
                                             #   (frozen dataclass is value-level additive; defaults keep old roles valid)
app/tools/subagent.py                        # add `role` param to spawn_subagent (additive; validate via get_subagent_role)
app/adapters/<source>_adapter.py             # third-party data sources
app/skills/<v4_skill>.md                     # specialist prompts / procedure knowledge (no code changes)
tests/unit/test_<v4>_parity.py               # parity/contract tests (house convention)
tests/unit/gis_harness/test_registry_validation.py  # extend cross-checks for new ids
docs/adr/0104-<v4-name>.md                   # ADR justification for the additive seam extension
```

Rules of thumb derived from this audit:
- Never write MapSpec dicts directly; add an intent in `app/services/mapspec/lifecycle_engine.py` if a new mutation class is unavoidable (that itself warrants an ADR).
- Never construct `SessionPlan` outside `app/services/session_plan.py` helpers; extend `apply_tool_result` with a new tool branch instead.
- New artifact categories → `vocabulary.register_category`, not schema edits.
- Keep all V4 mutation-policy classifications in descriptor kwargs (side_effect/security_tier) so `select_tools_for_subagent`'s fail-closed allow-list works — unclassified tools are invisible to no-mutation roles by design.
- Budget roll-up for parallel waves should reuse `SubagentBudget` composition + geocompute `budgets.py` scope-tree patterns rather than inventing a third accounting system.

## Docs inventory (skimmed)

- `docs/gis-harness.md` — GIS domain-intelligence layer: Pi=agent runtime, Harness=intent/recipe/eligibility/fallback/plan/components/evidence; execution stays in ToolDispatchService, desired state stays in MapSpec.
- `docs/agent-runtime/` — README (V2/V3 platform index + module map), `context-runtime.md` (context budgets/bounded projections), `model-runtime.md` (descriptors/roles/routing/health/providers, ADR-0102), `security-policy.md` (tier-3 red line = registry ContextVar gate), `tool-descriptor.md` (descriptor lifecycle/fingerprints/normalization), `tool-surface.md` (surface projection/retrieval/schema compression), `trace-replay.md` (in-process trace events, replay, metrics).
- `docs/science/` — `architecture.md` (ADR-0099 platform overview), `CONTRACT_BACKBONE.md` (working reference for domain-pack implementers), `ALGORITHM_CATALOG.md` (generated from registry — do not edit), `FOUNDATION_V3.md` (spatial algorithm foundation v3 baseline).
- `docs/data-v3/` — `PR_BODY.md` + `audit/00-07` (data-model / ingest / artifact-flow / cache / workspace / scale audits).
- `docs/cartography/` — `design-system.md`, `design-system-v4.md`, `component-catalog.md`, `composition-template-catalog.md`, `layout-solver.md`, `map-model-catalog.md`, `renderer-parity-matrix.md`, `template-authoring-guide.md`, `theme-palette-catalog.md`.
- Related: `docs/data-plane/geocompute-v4.md` (ExecutionPlan/budget plane), `docs/agents/` (dev-process docs: dispatch-protocol/domain/issue-tracker/triage-labels — not runtime), `docs/research/pi-host-seams.md`, `pi_agent_harness_design.md` (Pi host seam design background).
