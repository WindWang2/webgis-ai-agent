# 04 — Tool Retrieval & Tool Platform Audit (GIS Harness V4 baseline, HEAD 16d1c70)

Read-only audit. All paths relative to repo root. Line numbers from `git show 16d1c70` working tree.

---

## (a) Current retrieval / selection pipeline

### A.1 Registration (single source of truth)

- Canonical registry: `ToolRegistry` in `app/tools/registry.py:387`. No second registry exists; `app/tools/descriptor.py:1-6` codifies "ToolRegistry is the only execution truth; ToolDescriptor is a derived read-only projection".
- Module registration: `app/tools/__init__.py:11-57` lists 43 `register_*_tools` modules; `init_tools` (`app/tools/__init__.py:61-90`) imports each, then `load_skills` (`app/tools/skills.py:439`) hot-loads `app/skills/*.py`, then `refresh_list_available_tools_args` (`app/tools/meta_tools.py:125-138`) rebuilds the discovery tool's domain enum from the final registry.
- Registration APIs: decorator `ToolRegistry.tool()` (`app/tools/registry.py:408`), module-level `@tool(registry, name=..., ...)` (`app/tools/registry.py:1817`), `register()` (`registry.py:459`). Grep counts: **256 `@tool(` registration sites** across `app/tools/*.py` + `app/services/gis_harness/tools.py` (one tool per site; exact runtime count printed by `tests/benchmarks/bench_planning_v3.py:444`).
- Per-tool data stored at register: `_metadata[name]` (`registry.py:631-672`) — tier/domains/execution_policy/timeout/version/contract_version/cost/status/summary/deprecation_of/side_effect (tier≥3 force-coerced to destructive, `registry.py:629-630`)/requires_credentials/capabilities/algorithms/provider_dependencies/tags/output_semantic_type/produced_refs/accepts_ref_types/network/deterministic/result_size_policy/required_fields + ADR-0103 V3 fields (input_artifacts, required_context, map_mutations, data_mutations, latency_class, memory_class, scale_class, crs_semantics, unit_semantics, idempotent, security_tier, required_permission, examples, anti_examples, failure_modes, fallback_tool). Registration-time validation: `validate_descriptor_fields` (`app/tools/descriptor.py:405-530`); unknown kwargs fail loudly (`registry.py:486-491`).
- Derived projection: `ToolRegistry.descriptor(name)` (`registry.py:870-960`) builds cached `ToolDescriptor` (`descriptor.py:155-356`) incl. derived properties `destructive_level / requires_confirmation / model_visible / retry_safe / cacheable / replay_safe / effective_security_tier / effective_idempotent`, and `capability_source` backfill from AlgorithmRegistry (`registry.py:53-73, 896-904`).

### A.2 Per-turn tool-list selection — three live layers

**Layer 1 (legacy engine, default live path):**
`app/services/chat/execution_engine.py:479-481` calls `compile_tool_surface()` → `app/services/gis_harness/tool_surface.py:169-232` (phase compiler: SessionPlan/chapter/next_action → one of 5 phases `planning|data|analysis|assembly|final`, static tables `_PHASE_PREFERRED:59-73`, `_PHASE_DOMAINS:76-82`, `_ACTION_PHASE:87-95`). Phase output feeds `ToolCatalog.select_schemas` (`app/services/tool_catalog.py:176-237`):
- tier=1 always; tier=2 if `tool.domains ∩ active_domains` (keyword `DOMAIN_KEYWORDS` `tool_catalog.py:41-124` ∪ plan `declared_domains` ∪ sticky ∪ `surface.allowed_domains`); **tier=3 never auto-included** (`tool_catalog.py:223`).
- sticky domains: TTL 3 user-turns (`:127`), cap 4 active sticky domains (`:132`), per-session map with LRU cap 500 (`:169-172`); `reset_sticky` on new goal (`execution_engine.py:1085`); `decay_sticky_domain` is a dead branch in production (`app/services/chat/plan_orchestrator.py:21,768`).
- tier-2 byte budget: `_TIER2_SCHEMA_BUDGET_BYTES = 24KB` env `TOOL_SCHEMA_TIER2_BUDGET_KB` (`tool_catalog.py:138-140`); truncation priority fresh→declared→sticky domains, `webgis_*` front-door exempt via `_rank` (`:279-290`).
- local-OSM suppression of amap/baidu POI tools (`:143-148, 224-226`).

**Layer 2 (V2 projection, additive post-process):** `execution_engine.py:518-527` wraps catalog output in `ToolSurfaceProjector.augment` (`app/services/tool_surface_v2.py:120-278`): lifecycle filter (hidden/planned/external_unavailable, `:146-151`), tier≥3 drop **also on the no-catalog fallback path** (`:155-157`), then lexical retrieval fill-up via `rank_tools` (max 6, inherits 24KB budget, tier-3/external never revived — safety note `:206-217`), optional `compress_schema` (default off, `:45`), per-projection fingerprint (`:264-269`).

**Layer 3 (V3 dynamic surface, Pi path):** `app/services/chat/pi_turn_context.py:230-245` → `compute_turn_active_tools` (`app/services/chat/pi_native_surface.py:355-411`) → `DynamicToolSurface.select` (`app/services/chat/tool_surface_v3.py:188-304`):
1. core front-door tools (`CORE_TOOL_NAMES:47-52`, always);
2. capability→tool candidates from `AlgorithmRegistry.capability_tool_map()` (`:207-218, 256-259`, boost 9.0/cap);
3. composite query = user_message + task_type + workflow_stage + active_capabilities + data_profile_domains + map_state_summary (clamped 4096 chars, `:150-163`);
4. lexical retrieval `rank_tools` (top_k = 3×k_max) + optional injected semantic retriever (env `TOOL_RETRIEVAL_SEMANTIC="module:attr"`, `:40, 125-137`; failure degrades to lexical);
5. data-profile domain boost (+4.0, `:262-273`);
6. contract filter `_contract_filter:168-183` — lifecycle, tier≥3 / effective_security_tier≥3, role→side-effect allowlists `ROLE_SIDE_EFFECT_POLICY:63-85`;
7. deterministic sort `(-score, name)` (`:285`), k∈[10,30] (`:43-44`); k_min is advisory, never pads (`:294-296`).
`project()` (`:309-353`) assembles schemas from registry with `compress_schema` + byte budget + fingerprint. Result becomes the `[ACTIVE_TOOLS_MARKER:...]` extension for the Pi agent (`pi_turn_context.py:239-245`); final safety double-check drops tier≥3 again (`pi_native_surface.py:397-401`).

**Tier-3 two-hop channel:** `list_available_tools` (tier=1, always visible; `app/tools/meta_tools.py:54-122`) lists a domain's tools with name/description/tier; **tier≥3 are excluded with a count disclosure** (`meta_tools.py:104-121`) — i.e. the agent cannot discover tier-3 names through the channel.

**Lexical retriever internals:** `app/services/chat/tool_retrieval.py` — weights name-exact 12 / prefix 6 / substr 4 / tag 5 / domain 4 / capability 3.5 / description 2 / bigram 1, match cap 3, `min_score=4.0` (`:33-41,160`); tokenizer ASCII words + CJK unigram/bigram (`:44-76`); per-tool `ToolLexicon` corpus = name + tags + domains + capabilities+algorithms + summary+description (`:91-108`); index cached on `registry.registry_fingerprint()` (`:130-152`); deterministic tie-break by name (`:201`).

### A.3 Gating (retrieval can never bypass)

1. **Dispatch chokepoint**: `registry._dispatch_impl` refuses tier≥3 unless `confirm_tier3()` ContextVar is set — `app/tools/registry.py:1213-1218` (error `TIER3_CONFIRMATION_REQUIRED`); ContextVar + contextmanager `registry.py:81-127`; PLANNED tools also refused (`:1200-1206`).
2. **Route gate**: `/chat/tools/execute` requires `confirm_destructive=true` for tier≥3, then wraps dispatch in `confirm_tier3()` — `app/api/routes/chat.py:1980-2011`.
3. **Pi bridge hard reject**: tier≥3 never dispatched via Pi extension — `app/agent_pi_bridge.py:496-512`.
4. **Surface gates**: catalog `tool_catalog.py:223`; V2 `tool_surface_v2.py:155-157, 206-217`; V3 `tool_surface_v3.py:178-179`; Pi final check `pi_native_surface.py:397-401`; retrieval index itself skips non-`model_visible` tools (`tool_retrieval.py:147-150`).
5. **Role policy**: `ROLE_SIDE_EFFECT_POLICY` (`tool_surface_v3.py:63-85`) restricts side-effect classes for corpus_worker / doc_crosscheck / descriptor_enrichment / static_analysis roles.
6. Descriptor honesty: `requires_confirmation` also true for destructive side_effect at any tier (`descriptor.py:242-245`), but the execution gate keys on tier only (`registry.py:1213`) — low-tier destructive tools rely on route/bridge checks (see gaps).

---

## (b) Tool metadata availability vs. ranking signals

Declared-count = grep of declaration lines in `app/tools/*.py` + `app/services/gis_harness/tools.py` (≈256 registration sites; a line may declare several fields — approximate coverage).

| Wave-4 ranking signal | Descriptor field(s) | Declared coverage | In retrieval corpus today? | Used in ranking today? |
|---|---|---|---|---|
| Lexical match | name/description/summary | 100% / 100% / summary≈31 sites | YES (`ToolLexicon` `tool_retrieval.py:93-107`) | YES (weights `:33-41`) |
| Tool tags | `tags` | ≈218 | YES (`_W_TAG=5`) | YES |
| Domains | `domains` | ≈233 | YES (`_W_DOMAIN=4`) | YES (catalog activation + V3 boost) |
| Capability match | `capabilities` | 8 declared + derived from AlgorithmRegistry (171 algo descriptors with `tool_candidates`, `app/lib/gis/algorithms/*.py`; map `app/lib/gis/algorithm_registry.py:256-315`) | YES (`_W_CAPABILITY=3.5` + V3 exact-hit boost 9.0) | YES |
| Algorithm match | `algorithms` | 2 declared + derived | tokenized **into the same capability corpus** (`tool_retrieval.py:97`) — no distinct weight | Partial (flattened) |
| Workflow phase | — (derived, `tool_surface.py:43-95`) | n/a | workflow_stage string only appended to query (`tool_surface_v3.py:154-156`) | Partial (preferred_tools/domains via catalog; no per-phase weights) |
| Data/artifact semantic type | `output_semantic_type` (≈215), `input_artifacts` (2), `accepts_ref_types` (2), `produced_refs` (3) | output yes; I/O contract near-zero | NO — not in `ToolLexicon` | NO (only domain-string intersection with data_profile, `tool_surface_v3.py:262-273`) |
| CRS semantics | `crs_semantics` | ≈103 | NO | NO |
| Unit semantics | `unit_semantics` | ≈28 | NO | NO |
| Scale class | `scale_class` | ≈212 | NO | NO |
| Side effect | `side_effect` | ≈207 | NO | Filter only (role policy, tier3), never a score |
| Latency/memory class | `latency_class`/`memory_class` (≈212 each), `cost` (43 declared; default light `registry.py:140-141`) | declared | NO | NO (`cost` used for concurrency/waves, not ranking) |
| Deterministic flag | `deterministic` (≈213), `idempotent` (4) | declared | NO | NO (used in replay/cache semantics elsewhere) |
| Prior failure / no-progress | `failure_modes` (≈199), `fallback_tool` (6) | declared | NO | **NO — no live failure feedback into selection** (see A.4) |
| Sticky / continuation | — | n/a | n/a | Domain-level sticky only (`tool_catalog.py:126-148`); no tool-level or plan-step continuation boost |
| Examples / anti-examples | `examples` (37), `anti_examples` (14) | sparse | NO | NO (anti_examples = negative-retrieval evidence, unused) |
| Required context | `required_context` | ≈54 | NO | NO (session-state preconditions unmatched) |
| Security | `security_tier` (3), `required_permission` (3) | near-zero | NO | Filter via `effective_security_tier` fallback to tier |
| Schema byte size | computed | 100% (`registry.schema_size` `registry.py:810-826`, cached #1062) | n/a | YES (budget gates V1/V2/V3) |

**Fingerprint plumbing:** `schema_fingerprint` (name+parameters only, `descriptor.py:372-380`), `descriptor_fingerprint` (full contract payload, `:383-387`), `registry_fingerprint` = manifest over `(name, schema_fingerprint)` (`registry.py:994-1005`, invalidated on register/update_args_model `:694, 804`). Consumed by: lexical index cache key (`tool_retrieval.py:137-152`), projection fingerprints (`tool_surface_v2.py:264`, `tool_surface_v3.py:341-344`), eval/replay.

---

## (c) Corpus inventory

| Corpus | Location | Size | Format | Metrics / assertions |
|---|---|---|---|---|
| **Golden + Matrix ("306 corpus", ADR-0092)** | `app/evaluation/golden_cases.py:13-478` (G1–G33) + `app/evaluation/case_matrix.py:590-613` `build_matrix_cases()` | **33 + 273 = 306** (273 = 131 family `case_matrix.py:84-218` + 15 negative + 10 form + 50 scope (10×5 cities `:322-360`) + 17 decision + 30 compound + 20 style) | Python `GISBenchmarkCase` dataclass (`app/evaluation/case.py`): query, expected_task/recipe(s)/capabilities/optional_capabilities/allowed+forbidden_algorithms/methodology warnings, ScriptSteps, numeric assertions | Runner `app/evaluation/runner.py`: `capability_precision`/`capability_recall` (`:239-244`), `ontology_top1_correct`, `recipe_selection_correct`, `no_false_professional_analysis`, `unnecessary_tool_count`, `qualification_states_correct`, `fallback_tier_correct`, `planning_deterministic` (`:310-341, 358-415`) |
| **Conformance corpus ("3240 → 20088")** | `app/evaluation/conformance.py:104-613` (59 `ConformanceFamily`s, 186 seed phrases) × 12 `SCOPE_VARIANTS:26-39` × 9 `UTTERANCE_VARIANTS:45-55` | **20,088** deterministic cases (was 3,240 pre-V3 — `docs/workflows/semantic-workflow-v3.md:180`; `docs/workflows/conformance-corpus.md:15` relates it to the 306) | Generated `GISBenchmarkCase`s, stable ids `CF-{family}-{lang}{i}-{scope}-{utter}`; family table = human-audited artifact | Gate `tests/unit/gis_harness/test_conformance_corpus.py:29-36` (≥20000, full-run green, id determinism); default CI lane = deterministic stratified sample 543 + domain slices; pack-domain coverage (`:87-104`) |
| Tool-selection golden seed (Wave 9 seed, not full corpus) | `tests/unit/test_tool_surface_v2.py:287-309` | 4 cases | (query, required tools, forbidden tools) triples through `ToolSurfaceProjector` | recall ≥ 0.99, leakage == 0; tier-3 never via retrieval (`:312-339`) |
| Retrieval metrics (offline, no LLM) | `app/evaluation/runtime_metrics.py:71-107` `retrieval_metrics()` | runs over any `GISBenchmarkCase` sequence | V3 `DynamicToolSurface.select` per case | `recall@k`, `precision@k`, `irrelevant_rate`, cases/skipped (relevance = expected_capabilities ∩ tool capabilities) |
| Surface A/B + replay invariants | `app/evaluation/replay.py:296-328` `ab_compare_tool_surface`; `:36,148-159` `CallPatternTracker` | n/a | Selection diff (`only_in_a/b`, retriever labels); scripted agent-loop replay | Deterministic diff counts; replay enforces tier-3 refusal, alias folding, no-progress detection |
| Tool-choice accuracy (recorded runs) | `app/lib/harness/pi_agent_harness.py:1867, 2070` | metric over executed sessions | harness run summary | `ToolChoiceAccuracy` (excludes error/suspicious calls — `tests/unit/test_pi_harness.py:308-325`) |
| Schema-budget / selection perf bench | `tests/benchmarks/bench_planning_v3.py:340-360, 444` | n/a | Builds real registry via `init_tools`, prints tool count, per-turn selection counts/bytes | tokens≈chars/4; "selection logic identical master vs v3" report |
| Cartography golden corpus (adjacent, not tool selection) | `tests/cartography/golden_corpus/` (`corpus.py`, `goldens/`) | 503 golden JSONs | composition × profile × export variants | recipe/composition golden assertions |
| Science oracles (adjacent) | `tests/science_oracles/data/*.json` | 12 datasets | numeric regression/crs_units/edge_cases | numeric oracle replay (`scripts/gen_science_oracles.py`) |

**Formats note:** all corpora are deterministic in-repo Python generators (dataclass tables × expansion), not JSONL/JSON fixture files — a tool-selection corpus should follow the same family-table pattern.

---

## (d) Gaps, ranked

1. **Ranking ignores most of the descriptor (highest leverage).** `ToolLexicon` (`tool_retrieval.py:91-108`) uses only name/tags/domains/capability+algorithm ids/summary+description. Unrepresented despite declared data: `output_semantic_type` (~215), `crs_semantics` (~103), `unit_semantics` (28), `scale_class`/`latency_class`/`memory_class` (~212 each), `deterministic` (~213), `side_effect` (~207), `examples`/`anti_examples`/`failure_modes`/`fallback_tool`, `required_context` (~54). Wave-4 evidence axes mostly have zero wire-in today.
2. **No artifact-aware matching.** V3's only data signal is a domain-string intersect (`tool_surface_v3.py:262-273`). Nothing matches a candidate tool's `input_artifacts`/`accepts_ref_types`/`output_semantic_type` against the live session's artifact ledger / ref semantic types (`input_artifacts` declared by only 2 tools — the consumption side is essentially unpopulated and needs both plumbing + enrichment).
3. **No prior-failure / no-progress feedback into selection.** `CallPatternTracker` + `canonical_call_signature` exist (`app/services/chat/no_progress.py`) but are wired **only into the replay harness** (`app/evaluation/replay.py:36,148-159`). The live engine counts no-progress/suspicious streaks to fail the turn (`execution_engine.py:1373, 1639-1653`; `tool_pipeline.py:320`) and `tool_metrics` rows carry `failure_class`/`recovery_action` (`registry.py:1109-1160`), but selection never sees any of it — a tool that failed 3 turns running is re-surfaced identically. Same for `fallback_tool` (6 declarations, unused).
4. **Sticky/continuation is domain-level only.** TTL-3, cap-4 sticky domains (`tool_catalog.py:126-148`) plus static phase `preferred_tools`; no tool-level stickiness, no "continue the in-flight plan step's tool family" boost, and the decay branch is dead code (`plan_orchestrator.py:21,768`).
5. **Workflow phase signal is thin.** 5 closed phases with static tables (`gis_harness/tool_surface.py:59-95`); the phase reaches V3 only as extra query text (`tool_surface_v3.py:154-156`) — no per-phase weight vectors, phase-conditioned k budgets, or phase×capability priors.
6. **Algorithm match is flattened** into the capability corpus with a single weight (`tool_retrieval.py:97,188-189`); method-level disambiguation (kriging vs GWR vs IDW) has no distinct evidence channel, though AlgorithmRegistry holds `algorithms_for_capability` with priority order (`algorithm_registry.py:235-253`).
7. **Side-effect/cost/determinism never score.** Used purely as filters; no prefer-read-only boost for exploratory queries, no heavy-cost demotion under tight byte/turn budgets.
8. **Semantic retrieval is injection-only.** `TOOL_RETRIEVAL_SEMANTIC` read once at import (`tool_surface_v3.py:40`), unset in production → lexical-only. Lexical `min_score=4.0` + CJK bigrams is decent but has no embedding fallback and no per-domain vocabulary expansion (e.g. "泰森多边形"→voronoi works only because it's in DOMAIN_KEYWORDS, `tool_catalog.py:105-124`).
9. **Index staleness edge:** `registry_fingerprint` covers `(name, schema_fingerprint)` only (`registry.py:994-1005`; `schema_fingerprint` excludes description `descriptor.py:372-380`). A re-register that changes **description/tags/summary but not the parameter schema** yields an identical fingerprint → `ToolRetrievalIndex.build_if_stale` (`tool_retrieval.py:137-152`) keeps the stale lexicon. Descriptor fingerprints exist but are not part of the index key.
10. **Corpus gaps.** The tool-selection eval is a **4-case seed** (`test_tool_surface_v2.py:287-309`); `retrieval_metrics()` (`runtime_metrics.py:71`) is generic but not committed as a gate over the 306/20088 corpora; no negative-retrieval corpus despite `anti_examples` vocabulary; conformance corpus asserts task/recipe routing, never "was the right tool on the surface". No benchmark computes recall@k for the surface stack in CI.
11. Minor: `_DERIVED_CAPABILITY_CACHE` is process-global, cleared only at 4096 entries (`registry.py:50-73`) — safe while AlgorithmRegistry seed is immutable, but it is not fingerprint-keyed. Low-tier destructive tools: execution gate keys on tier only (`registry.py:1213`) while `requires_confirmation` can be true at tier<3 (`descriptor.py:242-245`) — retrieval filters on tier/effective_security_tier, so a future low-tier destructive tool would be surface-visible; today the confirm gate still catches execution.

---

## (e) Recommended additive integration seam

Keep `ToolRegistry` the only truth; grow the **retrieval layer above it** (V3 `DynamicToolSurface` + `ToolRetrievalIndex`), not a parallel registry:

1. **Enrich `ToolLexicon`** (`tool_retrieval.py:79-108`) with the already-declared fields: `output_semantic_type`, `crs_semantics`, `unit_semantics`, `scale_class`, `input_artifacts`, `examples` (+ `anti_examples` as negative evidence). All come from `ToolDescriptor` — zero new truth. Add bounded weights beside `_W_TAG/_W_DOMAIN`.
2. **Add a scorer stage between `rank()` and V3 step 7** (`tool_surface_v3.py:275-292`): a pure `rerank(hits, context) -> hits` that applies context boosts (phase table, active capabilities already exist, add artifact-type match, sticky-tool boost, prior-failure demotion via `fallback_tool`/failure_class). Deterministic, tie-break by name, every boost appended to `reasons` (existing explainability surface).
3. **Extend `ToolSelectionContext`** (`tool_surface_v3.py:88-102`) with three additive optional fields: `session_artifact_types: Tuple[str,...]` (from session artifact ledger/ref table), `recent_tool_outcomes: Dict[str,str]` (tool → last failure_class / no-progress reason; sources already recorded: `tool_metrics.record_tool_call` at `registry.py:1140-1160`, `CallPatternTracker`), and `continuation_tools: Tuple[str,...]` (current plan step's bound tool / last turn's selected names). Callers: `pi_turn_context.py:230-245` and the legacy `_select_tools` path.
4. **Never let ranking grant visibility:** keep `_contract_filter` (tier-3 / lifecycle / role policy) applied **after** re-ranking, exactly as today (`tool_surface_v3.py:275-284`), and keep the lexical index skipping non-`model_visible` tools. Retrieval may only reorder/produce candidates; the gates subtract. Destructive confirmation stays solely at `registry._dispatch_impl` + route/Pi bridges (A.3) — retrieval changes touch none of them.
5. **Fix index invalidation cheaply:** include per-tool `descriptor_fingerprint` in the fingerprint that `ToolRetrievalIndex.build_if_stale` keys on (or fold tags/summary/description into the manifest payload) so metadata-only edits rebuild the lexicon. `ToolRegistry.fingerprints()` (`registry.py:1007-1012`) already provides it.
6. **Committed offline benchmark:** instantiate `retrieval_metrics()` over (a) the 306 golden+matrix corpus and (b) the 543-case stratified conformance sample; assert recall@k, precision@k, tier-3 leak == 0, role-policy leak == 0; add per-signal ablation via existing `ab_compare_tool_surface` (`replay.py:296`). Grow the 4-case seed into a family-table tool-selection corpus (query → required/forbidden tool sets) following the `conformance.py` generator pattern, so it scales the same way (3240→20088 precedent).
