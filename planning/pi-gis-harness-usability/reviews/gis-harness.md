# GIS harness production-path review

Branch: `zcode/pi-gis-harness-usability-20261106`  
HEAD: `a678d0005` (= `origin/master` at review time)  
Scope: production path from natural language to a verified map product. Library modules that exist but are not called are listed only when they block scenarios A–H. Deferred work called out by ADR/docs is a non-finding.

Reviewer did not re-implement #1972/#1973/#1974/#1975/#1976/#1965. No source files were modified except this review.

## Actual production call chain

Pi chat (`USE_NEW_AGENT=1`) does **not** run the legacy planner.

1. `app/api/routes/chat.py:1224` `pi_event_generator` — turn entry.  
   `chat.py:1244-1249` states the Pi path deliberately skips `classify_followup` / `should_plan` / `make_plan` / `ToolCatalog`. That skip is documented as roadmap (#726, `docs/gis-harness.md:202-211`), not an accidental dead import.
2. Tool calls enter `app/agent_pi_bridge.py:474` `dispatch_tool` → `app/services/tool_dispatch_service.py:277` `ToolDispatchService.dispatch`. Native names are classified in `app/services/chat/pi_native_surface.py:76` `resolve_pi_tool_call`. Frozen native set (`pi_native_surface.py:23-31`): `webgis_map_intent`, `webgis_map_product`, `webgis_component_update`, `webgis_cartography_status`, `query_local_poi`, `get_local_admin_boundary`, `list_available_tools`. Everything else goes through `webgis_execute` or the dynamic registry surface.
3. Intent: `app/services/gis_harness/tool_map_intent.py:18` `webgis_map_intent` → `intent.resolve_map_request_intent` (`intent.py:262`) → `planner.plan_from_intent` (`tool_map_intent.py:171-172`). Requirement IR is written at `tool_map_intent.py:394` `ensure_document`. Palette/title pins are applied at `tool_map_intent.py:413` `apply_pending_bindings_safe`, which **does not write layer paint** (`representation_binding/requirement_to_mapspec.py:262`, `:534-535`).
4. Data: `app/tools/local_stats.py:152` `query_local_poi` → `app/services/local_poi.py:754` `query_gd_poi`. Heatmap authoring is either `app/tools/spatial.py:475` `heatmap_data` or `tool_map_product.py:285-322` (re-authors a heatmap layer from `primary_ref`, no POI call).
5. Product: `app/services/gis_harness/tool_map_product.py:27` `webgis_map_product` binds caller-supplied `primary_ref` (`:134-138`, `:276-322`), commits layout via `mapspec_store.layout_set` (`:661`), then marks `poi_query` done if the ref has points (`:719-727`).
6. Style edits that stay on the component channel: `app/services/gis_harness/tools_component_update.py:142` `webgis_component_update` (title/legend position; docstring `:129-140` says it does not re-query). Layer paint batch: `app/tools/map_operations_tools.py` `webgis_map_operation` → `app/services/gis_harness/map_operations/service.py`. Paint whitelist is opacity-only (`map_operations/contracts.py:77-86`).
7. After each tool: `app/services/chat/pi_post_dispatch.py:222` `_finalize_after_tool` → `maybe_finalize_map_product` (`completion/pipeline.py:1075`). Turn end: `harness_kernel/settle.py:338` `run_settle_effects` → same finalizer with `final_gate=True` (`settle.py:369-371`). Block writer: `completion/pipeline.py:816` `map_product_block` (sets `task_complete` at `:925`, goal satisfaction at `:937`, goal verification at `:958`).
8. User-visible turn end: `app/services/chat/pi_event_mapper.py:290` `_handle_agent_settled` always emits SSE event name `task_complete` (`:325`). Nested `map_product.status` is copied when present (`:306-310`). The boolean `task_complete` used by the finalizer lives on `finalization_sse_payload` (`pipeline.py:1695-1698`) and on the stored block (`pipeline.py:1646`).

MapSpec authoring seam inside dispatch: `tool_dispatch_service.py` projects heatmap/raster results into MapSpec (heatmap branch around `:1212-1226` and `:1812+` `add_heatmap_raster`). Failures get an additive `recovery` annotation only (`tool_dispatch_service.py:1012-1060`); the policy does not execute the retry (`recovery_policy/policy.py:221-237`, ADR-0281 D5).

## Confirmed findings

### GH-01 — P1 — `task_complete` and goal `satisfied` without a real render observation

**Where**

- `app/services/gis_harness/render_observation.py:377-389` — no observation → `render_status=unknown` plus warning `render_unverified`. Explicitly not an error, so it does not block completion.
- `app/services/gis_harness/completion/pipeline.py:520-522` — zero error findings → `status=complete` (`"map product validated"`).
- `app/services/gis_harness/completion/map_verification.py:318-323` — `complete` + render not in `{verified, not_applicable}` → `final_map_status=verified_with_degradation` (constant at `completion/contracts.py:132`).
- `app/services/gis_harness/completion/contracts.py:407-410` — `complete` plus the unverified warning → product verdict `READY_WITH_WARNINGS`.
- `app/services/gis_harness/completion/pipeline.py:1646-1664` `_is_task_complete` — `READY_WITH_WARNINGS` **and** `verified_with_degradation` → `task_complete=True`.
- `app/services/gis_harness/goal_satisfaction/evaluator.py:192-211` — map requirement is `FULFILLED` whenever verdict is `READY*` and `final_map_status` is `verified` or `verified_with_degradation`, unless `render_status=="stale"`. `unknown` is not stale, so it fulfills.
- `evaluator.py:474-476` — all required requirements fulfilled → goal verdict `satisfied` / signal `complete` (`:491-492`).
- `app/lib/harness/goal_verification/reconcile.py:324-335` records a contradiction string. It does **not** clear `task_complete` or downgrade the goal verdict. Absent observation sets the render slot to `not_evaluated`, not `fail` (`reconcile.py:234-237`), so the “task_complete=true but failing slots” contradiction (`:326-329`) also does not fire.

**L5:** `goal_satisfaction/evidence.py:89-91` states L5 visual-judge evidence is not persisted on the finalize face and must not be invented. `reconcile.py:201-208` treats `quality_review_present is True` as “L4/L5 evaluated”. The caller passes `cartographic_review is not None` (`pipeline.py:1316-1319`). A deterministic `_cartographic_review` dict therefore counts as the quality review. That is not a screenshot/L5 pass, but the slot text says L5 was evaluated.

**Reproduction**

1. Session with a GIS chapter and planned result layers, MapSpec layers present, no render-observation POST (frontend offline, or observation write skipped).
2. Run `maybe_finalize_map_product` (happens automatically after `webgis_map_product` via `pi_post_dispatch.py:230`, and again at clean settle `settle.py:369`).
3. Read `gis_chapter["map_product"]`: `task_complete` is true, `goal_satisfaction.verdict` is `satisfied` if the only map requirement was the product, `render_status` is `unknown`, `final_map_status` is `verified_with_degradation`.

**User impact**

Scenario A can be reported finished (heatmap / district map “done”, turn SSE `task_complete`) when the browser never painted the layer. ADR-0281 D4 (“渲染观察缺失永不 pass”) is not enforced on the boolean the UI and evidence chain consume (`settle.py:483-487` copies that boolean into `USER_OUTPUT`).

**Root cause**

`unknown` render was designed as a non-blocking warning for old clients (`render_observation.py:377-381`, `contracts.py:127-132` includes unknown observation inside `verified_with_degradation`). Goal satisfaction then treats that degraded token as fulfillment. Verification only annotates the contradiction.

**Duplicate check**

Not a closed fix. Comments describe the loose rule as intentional compatibility. It contradicts ADR-0281 D4, which is the contract this review was asked to judge. No later gate flips the boolean.

**Minimal fix**

In `_is_task_complete` and `_eval_map_requirement`, require `render_status=="verified"` before `task_complete` / map `FULFILLED` when the chapter has planned result layers. Keep `unknown` as `READY_WITH_WARNINGS` disclosure, not as pass. Do not treat a deterministic cartographic-review dict as L5.

---

### GH-02 — P1 — “热力图改蓝色 / 图例右边 / 改标题” has no paint path, and Pi will not stop a re-query

**Where**

- Style tool the prompt points at: `tools_component_update.py:134-140` examples cover title and legend position and say the tool does not re-query. It mutates component fields only (`:190-197`). It cannot set heatmap ramp color.
- `map_operations/contracts.py:77-86` `PAINT_KEYS` is opacity-only (`heatmap-opacity` yes, `heatmap-color` no). A color effect is `paint_key_unsupported` (`:108`).
- Requirement pin path: `requirement_to_mapspec.py:262` comment “不强写 paint”; `:534-535` grammar-face pins are not mutations. `apply_pending_bindings_safe` (`tool_map_intent.py:413`) therefore does not recolor the live heatmap layer.
- The only no-query recolor in production is `webgis_map_product` re-authoring from an existing `primary_ref` when `palette` is passed (`tool_map_product.py:285-305`). Native snippet steers restyle to `webgis_component_update` (`pi_native_surface.py:51`), not to that palette argument.
- Pi does not classify style vs new analysis: `chat.py:1244-1249`. `classify_followup` (`planning/followup.py:64`, style keywords include 蓝色/图例 at `:13-16`) runs only on the legacy engine (`execution_engine.py:1067`). Nothing in `ToolDispatchService.dispatch` refuses `query_local_poi` on a style-only turn.

**Reproduction**

1. Finish a Chengdu school heatmap (scenario A).
2. User: `热力图改蓝色，图例右边，标题改成成都市小学空间分布`.
3. If the model follows the native snippet and calls `webgis_component_update` (title + legend position) and/or `webgis_map_operation` paint: title and legend move; heatmap ramp stays the old palette; both tools return success.
4. If the model calls `query_local_poi` again, dispatch executes it. There is no server refusal.

**User impact**

Scenario B either looks successful while the ramp is unchanged, or re-hits the POI store (and the 50-point online fallback in GH-06) for a pure cartographic edit.

**Root cause**

Heatmap color lives on layer paint. The production style channels that are actually registered for “改标题/图例” do not write that paint, and the Pi host has no follow-up gate.

**Duplicate check**

The missing Pi planner is explicitly deferred (`docs/gis-harness.md:202-211`, chat.py comment). This finding is the **wired** style surface, not a request to port CanonicalPlan. Do not treat the #726 deferral as covering the paint whitelist.

**Minimal fix**

Add a heatmap palette/paint mutation (or apply the already-parsed `representation.palette` pin onto the bound heatmap layer’s `paint`/`legend_spec`) inside `webgis_component_update` or `webgis_map_operation`, using the existing layer id. Do not call `query_local_poi`. Optionally reject `query_local_poi` when the turn text is style-only and a live heatmap layer exists — a small guard, not a second planner.

---

### GH-03 — P1 — city switch does not reject a Chengdu `primary_ref`

**Where**

- Scope change is detected: `intent_diff.py:39` `scope` is a data dimension; `diff_chapters` (`:218-220`) does not carry data rows when scope changes. `session_plan.py:181-185` `goal_key` includes scope name, so a new intent supersedes the chapter (`session_plan.py:1205-1246`).
- `北京市` is in the fast path (`intent_semantic.py:264`), so `换成北京市做同样的图` should parse a new scope and should **not** take the Chengdu memory fallback (`tool_map_intent.py:46-66` only when `intent.scope.name` is empty). That part is fine.
- What is not fine: `webgis_map_product` never compares `primary_ref` geography to `intent.scope`. It profiles whatever ref the caller passes (`tool_map_product.py:134-138`) and, if points exist, authors the heatmap/point layers from it (`:276-322`) and marks `poi_query` available (`:719-727`). Supersede does not delete or fence the old session ref.

**Reproduction**

1. Scenario A stores `ref:poi-chengdu` and a map.
2. User: `换成北京市做同样的图`.
3. Model calls `webgis_map_intent` (chapter correctly becomes 北京, Chengdu completion rows not carried) then `webgis_map_product(query="换成北京市…", primary_ref=<chengdu ref>)` — the natural reuse of the ref still in the session.
4. Product title/intent say 北京; layers are Chengdu points. `success: true`.

**User impact**

Scenario D can publish a Beijing-titled map of Chengdu schools. The plan ledger looks invalidated; the pixels are not.

**Root cause**

Intent diff only gates **plan row carry**. Product assembly trusts the caller ref. No bbox/adcode/place check against `intent.scope`.

**Duplicate check**

Not fixed by intent-diff (that code is the plan-side half only). No scope-ref guard found in `tool_map_product.py`.

**Minimal fix**

Before `layer_upsert` in `webgis_map_product`, if `intent.scope.name` is set, require the ref descriptor place/adcode/bbox to intersect that scope; otherwise return `success: false` with `scope_ref_mismatch` and do not mark `poi_query` done. Do not delete unrelated refs.

---

### GH-04 — P1 — “这些学校” distance analysis does not use the server ownership check

**Where**

- Ownership checker exists: `app/services/gis_situation/analysis_scope.py:235` `validate_analysis_scope` (session store get, layer/source match, feature-id survival).
- Production caller of that function in the tool surface is only `app/tools/interaction_scope_tools.py:179` inside `webgis_selection_scope`. It is not referenced from `tool_dispatch_service.py`.
- The tool docstring tells the model to pass `feature_ids` into analysis tools (`interaction_scope_tools.py:70-71`). `nearest_facility` (`app/tools/advanced_spatial.py:2858-2861`) has no `feature_ids` parameter and does not call `validate_analysis_scope`. It runs `nearest_neighbor_features` on the entire source and target collections (`app/lib/geo_analysis/network.py:368`).
- Feature-edit does bind a selection (`feature_edit/service.py:46-58`) but that path is edits, not distance analysis.

**Reproduction**

1. User box-selects a subset of schools (selection IR stored).
2. User: `分析这些学校到最近医院的距离`.
3. Model can call `nearest_facility(source_points=<full school ref>, target_points=<hospitals>)` and get `success: true` for every school in the ref, including ones outside the box. No ownership/selection check runs.
4. A cross-session `ref:` fails only because `session_data_manager.get` is session-keyed (store miss), not because analysis tools consult `validate_analysis_scope`. A ref that resolves in **this** session but is not the selected layer is accepted.

**User impact**

Scenario C returns distances for the wrong population and can be presented as “these schools”. The ownership module is unwired from the tool that actually computes the distance.

**Root cause**

Selection validation is an optional advisory tool. The distance tool’s signature cannot express a validated selection, and dispatch does not require one.

**Duplicate check**

`validate_analysis_scope` is implemented and tested; it is not on the `nearest_facility` / dispatch path. Not a duplicate of the situation-module work — that work stopped at the advisory tool.

**Minimal fix**

Give `nearest_facility` optional `feature_ids` / `selection_ref`. When the session has a live selection, require `validate_analysis_scope` status `usable` and filter to those ids before the KD-tree. `unusable` → typed error, do not analyze the full layer.

---

### GH-05 — P1 — Euclidean nearest-facility is registered as Dijkstra network analysis, and the result does not say so

**Where**

- `app/lib/gis/algorithms/network.py:127-137` `network.closest_facility` lists `tool_candidates=["network_closest_facility", "nearest_facility"]` and assumptions that say every pair comes from a Dijkstra tree (`:137`), `method_references=["dijkstra1959"]` (`:135`).
- `algorithm_registry.py:487-488` maps **every** candidate tool, including the second, onto capability `closest_facility`. `tool_map_product.py:702-705` uses `tool_to_capability()` to mark steps done from layer provenance.
- Implementation of `nearest_facility`: `advanced_spatial.py:2833-2861` (`domains=["network"]`, `network=False`) → `geo_analysis/network.py:368-472` `cKDTree` in a projected CRS. Summary (`:469-472`) is “Calculated nearest neighbors … Average distance” with `distance_m`. No `distance_method=euclidean`.
- The tool **description** is honest (`advanced_spatial.py:2840`: 当前是欧氏距离). The result payload and the algorithm card are not. `network_closest_facility` (`network_tools.py:546-584`) is the real network tool and requires a `network` argument; if that argument is missing the model can call `nearest_facility` and still satisfy `closest_facility`.

**Reproduction**

1. No road network loaded.
2. User asks for distance from selected schools to the nearest hospital (scenario C), or the planner resolves `closest_facility`.
3. `nearest_facility` returns `success: true` and `distance_m`.
4. Capability index records `closest_facility` / the Dijkstra descriptor. Nothing in the result says the network was absent.

**User impact**

Straight-line metres are presented through a network-analysis capability. Scenario C’s “到最近医院距离” is the wrong quantity if the user meant travel distance, and the system has no forced disclosure on the success payload.

**Root cause**

Euclidean fallback was added as a second `tool_candidate` of a Dijkstra algorithm instead of a separate capability with an explicit downgrade (the recipe packs do have `NETWORK_DISCONNECTED → euclidean_*` fallbacks, but this registry entry bypasses that disclosure).

**Duplicate check**

Description text at `advanced_spatial.py:2840` is the honest half. It does not fix the registry or the result stamp. Not a duplicate of the network solver tests, which cover `network_closest_facility`, not this alias.

**Minimal fix**

Remove `nearest_facility` from `network.closest_facility.tool_candidates`. Stamp `distance_method: "euclidean"` and a one-line disclosure on `GeoAnalysisResult.summary`. If a recipe fallback is used, keep the existing `NETWORK_DISCONNECTED` reason on the product; do not mark the network step `done`.

---

### GH-06 — P2 — missing local POI database becomes a successful 50-point “city” layer

**Where**

- `local_poi.py:778-781` — missing `gd_pois.gpkg` returns `error` containing `缺失`.
- `local_stats.py:178-241` — that substring triggers an online Amap fallback. `offset` is `min(limit, 50)` (`:200`, `:206`). A non-empty page returns a normal FeatureCollection (`:235-241`) with `source: amap_online_fallback` and a `note`, not an error.
- `webgis_map_product` then treats any point ref as `poi_query` available (`tool_map_product.py:719-727`) and can author a heatmap (`:285`).
- Slimming: if the payload exceeds 2500 chars, `llm_result_formatter.py:261-287` keeps non-feature keys (so `source`/`note` usually survive) but the geojson summary note talks only about feature count. `note` is not in `_PRESERVED_META_KEYS` (`llm_result_formatter.py:26-53`); a later budget clamp can drop it. Unconfirmed whether the clamp always keeps `source`.

**Reproduction**

1. Environment with no `gd_pois.gpkg` (or the tool error string contains `缺失`/`未生成`) and a valid `AMAP_API_KEY`.
2. `query_local_poi(district="成都市", subtype="小学")`.
3. Result is `type: FeatureCollection`, `count <= 50`, `success` path (no `error` key). Heatmap/product tools accept it.

**User impact**

Scenario A/E “empty / missing local data” looks like a finished Chengdu school map of the first online page. Recovery policy (`tool_dispatch_service.py:1012+`) never runs because the result is not an error.

**Root cause**

Fallback converts a typed data-missing error into a successful, silently truncated collection.

**Duplicate check**

The `note` field is an attempt at disclosure. It is not a failure code, and product completion does not read it. Not fixed by ADR-0281 (that layer only sees error results).

**Minimal fix**

Return `success: false` / `code: partial_online_fallback` unless the caller passed an explicit online flag. Do not let `webgis_map_product` mark `poi_query` done from `source=amap_online_fallback` without copying that code into `warnings` and blocking `READY`.

---

### GH-07 — P2 — a non-map turn re-emits the previous map’s completion

**Where**

- Clean settle always runs the final gate (`settle.py:360-371`). If the gate no-ops, `settle.py:453-459` loads the stored block via `read_stored_map_product` and returns it as this turn’s `map_product`.
- `pi_event_mapper.py:290-325` names every settled turn `task_complete` and, when that dict has `status`, nests `map_product.status` (`:306-310`).
- Frontend `use-sse-stream.ts:936-942` treats the event as “turn finished”, not as a cartography toast. A consumer that reads nested `map_product.status==complete` still sees a cartography success on this turn.
- Pi does not force spatial tools on non-map text (`chat.py:1244-1249`). That half of scenario H holds.

**Reproduction**

1. Complete scenario A so `gis_chapter.map_product.status` is `complete`.
2. Next user message: `今天天气怎么样` (no tool calls).
3. Clean settle still attaches the stored map block. SSE event is `task_complete` with nested `map_product.status=complete`.

**User impact**

Scenario H does not force a new POI query, but the turn receipt can still claim the previous map completed on this chat turn. Combined with GH-01, that claim may also be unverified.

**Root cause**

Idempotent disclosure of the stored block is not scoped to turns that touched the map.

**Duplicate check**

`settle.py:427-429` only skips disclosure when the finalizer **threw**. A no-op gate is treated as “still disclose”. Intentional for map turns; wrong for non-map turns.

**Minimal fix**

On settle, attach `map_product` only if this turn recorded a map mutation or an explicit cartography tool. Non-map turns emit turn-end without `map_product` / without `task_complete=true`.

## Capabilities present but not on the Pi/chat production path

These block A–H only as listed. Others were not promoted to findings.

| Capability | Called from | Blocks | Verdict |
| --- | --- | --- | --- |
| `classify_followup` (`planning/followup.py:64`) | Legacy engine only (`execution_engine.py:1067`) | B (no server refusal of re-query) | Deferred as a full planner port (#726). The paint gap is GH-02, not this table. |
| `solve_layout_v5` (`layout_solver.py:802`) | Tests and `semantic_checks_layout.py` type import only. No hit in `publication_export.py`, `mapspec_to_svg.py`, or `frontend/**`. | G layout-solver expectation | **Deferred by ADR-0283** (Proposed): MapSpec wiring and `quality_loop.py` explicitly left unwired. Not a false claim that the solver ran. |
| `validate_analysis_scope` | Only `webgis_selection_scope` | C | Finding GH-04. |
| `network_closest_facility` | Registered, requires a network argument | C if the model picks `nearest_facility` instead | Finding GH-05 (alias), not “tool missing”. |
| Cartographer `solve_layout_v4` (`agent_swarm/specialists/cartographer.py:385`) | Swarm specialist, not Pi export | G only if swarm is the export path — it is not | Not a Pi-path defect. |

Label collision **is** on the vector PDF compile path: `mapspec_to_svg.py:1821` `solve_export_labels(..., obstacles=glyph_obstacles)`. Publication PDF calls that compiler (`publication_export.py:46-48`). PNG/PDF via `export_thematic_map` (`app/tools/cartography.py:952`) only emits a frontend `export_map` command; the browser canvas exporter (`frontend/lib/map-kit/exporter.ts`) does not call `solve_layout_v5`.

## Non-findings

- **Intent diff on city change (plan rows).** Scope is a data gate (`intent_diff.py:218-220`). Chengdu completion rows are not carried into a Beijing chapter. The defect is ref reuse at product assembly (GH-03), not the diff function.
- **Memory scope fallback stealing 北京.** Fallback runs only when scope is empty (`tool_map_intent.py:46`). `北京市` is in `CITY_FAST_PATH` (`intent_semantic.py:264`). Unconfirmed for queries that omit the city name (“做同样的图” alone); that case is specified to reuse the previous place.
- **Vector PDF Chinese font embed.** Vendored face is injected when the system probe fails (`publication_export.py:373-404`). `url_fetcher` allows `data:font/ttf` (`:932-938`). `font_cjk` reports the **system probe**, not the embed (`:916`); unit test expects `False` in that case. Not a confirmed tofu bug. ADR-0283 explicitly deferred the separate WeasyPrint `data:` interception issue (#1910).
- **Vector PDF busy/freeze.** `render_pdf_exclusive` (`publication_export.py:635-643`) fails closed with `PublicationBusyError` instead of queueing. Frontend upload timeout is 90s (`exporter.ts:1365`). No confirmed infinite freeze on the code path reviewed.
- **Missing basemap tiles (frontend export).** `exporter.ts:2261` records `scene_basemap_unavailable`. Publication export documents raster omission (`publication_export.py:10-12`). Not a silent-success defect on those paths.
- **Heatmap sample guard.** `heatmap_data` rejects non-points and `< HEATMAP_MIN_POINTS` (`spatial.py:518-529`). Empty native heatmap is not a success. The hole is the POI fallback that never looks empty (GH-06).
- **Recovery annotation wiring.** Timeout/5xx/empty/CRS reasons are classified and attached (`tool_dispatch_service.py:1012-1060`, `recovery_policy/diagnose.py:203`). Auto-retry is a decision flag, not an unbounded dispatch loop (`policy.py:221-237`). ADR-0281 D5 says execution stays with the agent. Not a defect by itself.
- **Cancel / Last-Event-ID resume.** Resume POST does not send a new prompt (`chat.py:1214-1221`). Non-clean settle skips the final gate (`settle.py:376-379`). Mid-tool cancel leaving a stale `task_complete` from an earlier turn is the same stored-block behavior as GH-07; a fresh cancel does not mark the cancelled turn complete. Unconfirmed: refresh during an in-flight tool replay. Not promoted.
- **Scenario A recipe choice (heatmap vs choropleth).** `webgis_map_intent` docstring (`tool_map_intent.py:451-452`) says 「各区数量」should prefer admin aggregation, and `TASK_SEMANTIC_FAMILY_GUARDS` blocks analytical density delivered as visual heatmap (`contracts.py:105-107`). Whether the live parser hits that rule for the exact sentence was not executed here. Unconfirmed as a parser miss; not listed as a defect.
- **Non-map chat forcing `query_local_poi`.** No server force. Only GH-07’s stale completion receipt.

## Scenario scoreboard

| Scenario | Result |
| --- | --- |
| A 成都小学分布 / 密集 / 各区数量 | Can be marked complete without a render (GH-01). Missing local DB becomes a 50-point success (GH-06). Choropleth-vs-heatmap choice unconfirmed. |
| B 改蓝色 / 图例右边 / 改标题, no re-query | Title/legend can move without re-query. Blue ramp cannot (GH-02). Re-query is not refused. |
| C 框选学校 → 最近医院 | Selection ownership not applied (GH-04). Distance may be Euclidean labeled as network capability (GH-05). |
| D 换成北京 | Plan rows invalidate; product will still draw the old ref (GH-03). |
| E timeout / empty / bad CRS / missing field | Error results get recovery notes. Empty-via-missing-gpkg does not (GH-06). `nearest_facility` does not call `preflight_tool_input` (no CRS block on that tool). Other tools that do call it (`spatial.py:361`, `advanced_spatial.py:120`) are fine. |
| F cancel / refresh / resume | Resume does not re-execute. Cancel skips final gate. Stale completion receipt is GH-07. In-flight refresh unconfirmed. |
| G PNG/PDF fonts, layout solver, labels, tiles, freeze | Layout solver v5 is tests-only and explicitly deferred. Label collision is on vector SVG compile. Font embed exists for vector PDF. Tile omission and busy-reject exist. No confirmed export hang. |
| H non-map chat | Does not force spatial compute. Can still attach the previous map’s `complete` (GH-07). |

## Suggested fix order

1. GH-01 — stop unverified render from setting `task_complete` / goal `satisfied`.
2. GH-03 — reject scope/ref mismatch in `webgis_map_product`.
3. GH-05 — un-alias Euclidean facility from the Dijkstra capability and stamp the method.
4. GH-04 — require validated selection on `nearest_facility` when a selection exists.
5. GH-02 — one paint/palette mutation for the live heatmap; do not re-query.
6. GH-06 — missing local POI stays an error (or a typed partial that cannot finalize).
7. GH-07 — do not re-emit stored map completion on non-map turns.

Unconfirmed items above must not be fixed as if they were reproduced.
