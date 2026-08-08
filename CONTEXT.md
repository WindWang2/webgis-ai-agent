# Domain Context

## Glossary

### Session / Conversation
A **Conversation** is the persistent DB record (`conversations` table). A **session** is the
in-memory runtime state (map_state, refs, LRU caches, TaskTracker entries) keyed by a
`session_id` UUID. The two terms are used interchangeably in the codebase; the `session_id`
acts as a capability token for data access.

### ref_id (Cursor)
An opaque 16-hex string (e.g., `ref:geojson-abc123...`) pointing to large data objects stored
in `SessionStore`, not in the DB. This is the core of the Fetch-on-Demand pattern: the
LLM never sees the full payload; the frontend fetches it separately via `get_ref_data`.

### SessionStore
The deep state storage module managing runtime session data across 16 core operations
(`get`, `store`, `overwrite`, `set_alias`, `resolve_alias`, `list_refs`, `get_map_state`,
`set_map_state`, `update_layer_in_state`, `remove_layer_from_state`, `get_event_log`, `append_event`,
`get_started_at`, `get_session_metadata`, `clear_session`, `cleanup_idle_sessions`).
Implemented by two adapters: `RedisSessionStore` (`app/services/session_data_redis.py`) and
`MemorySessionStore` (`app/services/session_data.py`), with `get_session_store()` as active provider.

### KnowledgeEngine
The deep RAG knowledge retrieval and vector indexing engine (`app/services/rag/engine.py`).
Encapsulates document chunking, embedding generation, multi-tenant security isolation (`TenantContext`),
and automatic FAISS vector index compaction (`compact_index`).

### Project Workspace
The root domain workspace (`Project`) managing datasets, persistent workflows, execution runs, spatial artifacts, map revisions, and reports for a given tenant/user. Decouples long-term GIS workspace management from transient `Session`/`Conversation` runtime states.

### ProjectDataset
Logical dataset attachment inside a `Project`, pointing to source layers or upload records without duplicating raw payloads. Tracks schema profile, CRS, quality status, and dataset version fingerprints.

### Workflow & WorkflowRun
A `Workflow` is a persistent, versioned DAG recipe composed of tool steps, parameter templates, input bindings, dependency edges, and execution policies. Can be created from a successful `Plan`. A `WorkflowRun` is an immutable execution record producing versioned output artifacts and metrics.

### Artifact & ArtifactLineage
`Artifact` is the unified logical representation for datasets, raster analysis, maps, and reports produced in a Project. `ArtifactLineage` tracks the DAG execution provenance graph (`parents -> artifact -> consumers`), recording producing tool, parameters, CRS, quality state, and execution run IDs.

### SpatialQualityEngine
The deep spatial data validation engine (`app/services/spatial_quality_service.py`) performing multi-dimensional quality auditing across Geometry, Topology, CRS, Attributes, and Spatial Sanity. Emits structured `SpatialQualityReport`s with explicit severity levels (`info`, `warning`, `error`, `blocking`).

### SpatialRepairPipeline
The safe spatial data remediation engine (`app/services/spatial_repair_pipeline.py`) providing non-destructive repairs (`make_valid`, `remove_empty`, `normalize_geometry_type`, `deduplicate`, `snap_within_tolerance`, `crs_transform`, `attribute_type_normalization`) to produce clean `Derived Artifacts` with explicit provenance.

### ChatContextAssembler
The deep prompt context composition engine (`app/services/chat/context_assembler.py`).
Encapsulates map state ambient summaries, history token budget truncation, XML security fencing,
and execution plan blocks behind a single `assemble(session_id, messages)` interface method.
Returns a structured `ContextAssemblyResult` value object.

### ChatExecutionEngine
The deep agent chat loop execution engine (`app/services/chat/execution_engine.py`).
Encapsulates streaming token emission, tool loop retries, session lock lifecycle management,
and SSE event payload formatting behind `chat()` and `chat_stream()` interfaces.

### AgentPlanOrchestrator
The deep plan state orchestration engine (`app/services/chat/plan_orchestrator.py`).
Encapsulates structured plan creation (`Plan`), heuristic gating, LRU plan memory caching,
and `ToolCatalog` domain decay synchronization behind a unified execution seam.

### SpectralRasterEngine
The deep remote sensing raster computation engine (`app/services/rs/spectral_engine.py`).
Encapsulates NumPy band math (NDVI, NDWI, EVI, NBR), Horn terrain derivatives (slope, aspect, hillshade),
and STAC cloud-optimized GeoTIFF reads behind a degraded `RasterAnalysisResult` value object.

### MapSpecLifecycleEngine
The deep cartographic lifecycle engine (`app/services/mapspec/lifecycle_engine.py`).
Encapsulates discriminated intent mutations (`InitProject`, `SetView`, `UpsertLayer`, `RemoveLayer`),
spatial auto-profiling, pre-compile validation, disk/Redis dual-write, and checkpoint snapshot storage.

### PdfRenderer
The deep PDF cartography rendering engine (`app/lib/cartography/pdf_renderer.py`).
Encapsulates A4 landscape figure geometry, thread-safe CJK font resolution (`FontProperties`),
title/subtitle/footer layout math, and metadata embedding into pure `bytes`.

### GeocodeProviderStrategy
The deep multi-provider geocoding failover engine (`app/services/geocode_strategy.py`).
Encapsulates provider rotation loops, failure rate threshold evaluation (>30%), and coordinate shape
normalization (`loc` array, `lat`/`lon`, `location` dict) behind `GeocodeAddressResult` value objects.

### LLMResultFormatter
The deep LLM payload trimming and result formatting engine (`app/services/llm_result_formatter.py`).
Encapsulates GeoJSON feature property sampling, error self-healing wrapping, and event log result slimming.

### SpatialOperator
The deep spatial operator runner decorator (`app/services/spatial_operator.py`).
Encapsulates single/multi-layer GeoJSON input feature normalization (`to_feature_collection`), progress callback firing,
and standardized exception safety wrapping behind `@spatial_operator(name, progress_pct, feature_keys)`.


### ExplorerPipeline
The 5-stage GIS exploration pipeline (`discover`, `fetch`, `parse`, `geocode`, `validate`).
Extracted into pure async stage modules (`discover_stage.py`, `fetch_stage.py`, `parse_stage.py`,
`geocode_stage.py`, `validate_stage.py`) behind an `ExplorerPipeline.run_in_process(...)` engine
(`app/services/explorer/pipeline.py`). Celery tasks in `task_chain.py` serve as thin adapters.


### Tool
A Python callable registered in `ToolRegistry` with an OpenAI-compatible JSON schema, tier
(1/2/3), and domain tags. Tools are the only interface to spatial operations.

### Tool dispatch
The single act of executing one tool call end-to-end: validate → tier-authorize → run via the
registry → store large GeoJSON to a `ref_id` cursor (Fetch-on-Demand) → shape an LLM payload
and a slim frontend event (BBox, property truncation, sample feature generation) → intercept
repeated calls → wrap errors as self-healing hints.
"Dispatch" names this whole chain, not just the `registry.dispatch()` call inside it. Both the
legacy ChatEngine path and the Pi bridge path perform agent-loop tool dispatch via `ToolDispatchService`.
Non-agent tool execution (`plan_mode` batch runner, `/tools/execute` admin endpoint) executes directly
via `ToolRegistry.dispatch()`, as they do not require agent-loop cross-cutting concerns (ADR-0014).

### SpatialAnalyzer
The concrete Python class (`app/services/spatial_analyzer.py`) providing pure spatial calculations
(buffer, clip, overlay, statistics, cluster, aggregate, etc.). Dynamic string-based operator dispatch
(`execute`, `OPERATOR_MAP`) was a dead seam (ADR-0013) and was deleted; spatial tools call concrete
methods directly. The operator surface includes the 5 density/geometry algorithms extracted from the
tool adapter (ADR-0029: `kde_surface`, `kde_contours`, `voronoi_polygons`, `convex_hull`,
`multi_ring_buffer`) plus `hotspot` and `lisa` - all routed through SpatialAnalyzer so the invariant
"all geo math goes through SpatialAnalyzer" holds uniformly for the 7 stats tools. The orphaned
`path_analysis` operator was deleted (vaporware - `shortest_path` never existed, live `ImportError`).


### Tier
Tool priority classification. Tier 1 is always-on; Tier 2 activates on keyword/sticky match;
Tier 3 is only visible to explicit `list_available_tools` calls.

### Domain
A thematic category (e.g., `raster`, `network`, `osm`, `statistics`) used by `ToolCatalog` to
dynamically subset tools per LLM round.

### Plan
An optional structured plan (`intent`, `domains`, `steps`) generated by a planner LLM call.
Stored in-memory per session with sticky TTL decay. Drives `ToolCatalog` domain selection and
provides frontend progress UI.

### TaskInfo / TaskStep
In-memory agent execution chain tracking a single user message's tool calls and steps.
Contrast with `AnalysisTask` (DB-backed Celery task) and Celery `task_id`.

### AnalysisTask
DB-backed legacy spatial analysis task (buffer, overlay, etc.) dispatched to Celery. Largely
superseded by agent-driven tool calls, but still used for heavy CPU-bound operations.

### Explorer
A 5-stage Celery pipeline (`discover → fetch → parse → geocode → validate`) for autonomous
external data discovery.

### UploadRecord
Catch-all asset table storing both user uploads *and* analysis results (e.g., NDVI GeoTIFF
outputs). The `geometry_type` field carries implicit semantics like `"raster_analysis"`.

### Report & Report Generation Saga
A **Report** is a DB-backed artifact (`reports` table) generated from a session's conversation history into PDF, HTML, or Markdown format. The report creation lifecycle follows a 2-phase transactional **status-lifecycle saga** (`ReportService.create_and_generate`): initial `Report` creation in `"generating"` status with immediate detachment (`db.expunge`), async Jinja2/WeasyPrint rendering without holding the primary DB session, and a separate DB session update to terminal status (`"completed"` or `"failed"`).

### Tool Execution Pipeline
A dedicated deep module (`ToolExecutionPipeline` in `app/services/chat/tool_pipeline.py`) responsible for executing AI tool calls during chat loops. It encapsulates tool parameter JSON parsing, sentinel duplicate-loop detection, `TaskTracker` step lifecycle management (`start_step` / `complete_step` / `fail_step`), `ToolDispatchService` invocation, and payload slimming into a structured `ToolExecutionResult`.

### SpatialAnalyzer Domain Engine
A unified domain service (`SpatialAnalyzer` in `app/services/spatial_analyzer.py`) providing deep spatial and raster analysis operations (`buffer`, `clip`, `overlay`, `spatial_join`, `zonal_stats`, `raster_reclassify`, `raster_calculator`, `raster_resample`, `isochrone_network`, `statistics`, `cluster`, `central_feature`). Encapsulates `validate_data_path` security checks and returns standardized `GeoAnalysisResult` objects. The 4 raster methods share a single `rasterio_env()` GDAL context (in `geo_analysis/raster_math.py`, ADR-0037) rather than inlining `rasterio.Env` per method. This is the de-facto execution engine; the 32 tool facades in `tools/*.py` are intentionally shallow (one schema each) — ADR-0013 rejected a name-dispatch "engine" wrapper over them, and ADR-0037 reaffirmed that as cleanup-only.

### Tracked Provider HTTP Request
An automated HTTP execution seam (`tracked_provider_get` in `app/services/provider_health.py`) that binds `ProviderHealthTracker` directly to `get_shared_client()`. Automatically handles rate limiting (`record_attempt`), proxy/SSL context injection, HTTP/JSON decoding, provider-specific business status validation (`check_amap_status`, `check_baidu_status`, `check_tianditu_status`), and circuit breaker state tracking (`record_success` / `record_error`).

### Chinese Maps Provider (capability matrix)
The deep provider module (`app/tools/chinese_maps/`) for Amap / Baidu / Tianditu. A `ChineseMapsProvider`
Protocol defines the **9 shared capabilities** with identical signatures across providers
(`search_poi`, `search_poi_around`, `search_poi_polygon`, `geocode`, `reverse_geocode`, `route`,
`input_tips`, `district`, `distance_matrix`); `AmapProvider` / `BaiduProvider` / `TiandituProvider`
implement it. Each class encapsulates endpoint paths, request-param building, response-unwrap keys, and
**both sides of coordinate transformation** (input WGS84→provider CRS via a private `_to_src`, output
normalization via provider-private `_shape_*` helpers; POI outputs still route through `_shaping.py`).
The three Amap-only features (`isochrone_analysis`, `search_transit_route`, `get_traffic_status`) are
non-Protocol methods on `AmapProvider`. The capability matrix is the Protocol's membership plus three
singleton declarations — not scattered `exclude=` sets. A `get: Callable[[str, dict], Awaitable[dict]]`
injected into each provider's `__init__` (defaulting to the real `_amap_get` / `_baidu_get` /
`_tianditu_get`) is the seam between provider logic and the Tracked Provider HTTP Request; it doubles as
the fake-GET test seam. `@tool` wrappers in `__init__.py` retain per-capability LLM-facing input
validation (Chinese error strings, returns to the LLM) and delegate transport fallback to a shared
`with_fallback(provider, call, exclude)` helper.


### Exception As Thought
Architectural philosophy where tool errors are returned as structured error dicts with
`correction_hint` rather than raised as HTTP 500s. The LLM sees these and decides whether to
retry, change parameters, or abort.

### Organization
Multi-tenant root entity. All users, layers, and documents belong to an organization via
`org_id`. The `slug` field is used for URL-level tenancy.

### HistoryStore & HistoryContext
Deepened conversation persistence seam. `HistoryContext` consolidates Conversation ORM metadata, owner token validation (SEC-08), and role-converted LLM messages (`llm_messages`). `HistoryStoreProtocol` defines 4 intent operations: `load_context`, `commit_interaction`, `delete_history`, and `summarize_session_title`.

### CartographicStyle (Thematic Style Module)
The canonical deep module (`app/services/cartographic_style.py`) for thematic cartographic classification
(Fisher-Jenks, quantiles, equal interval, LISA) and palette color generation. Solves the dual-pipeline
friction between live map overlay state (`legend_spec`) and MapSpec compiler paint specifications
(`StyleMethod`) by offering a unified model with twin adapters (`.to_legend_spec()` and `.to_style_method()`).

### MapSpec (Cartographic Intent)
The declarative, high-level cartographic specification: view, layers with high-level style
methods (`constant` / `interpolate` / `step` / `match` / `field`), layout (legend, controls,
margins), and runtime thresholds. Co-exists with Redis `map_state` under a **dual-write**
contract — see *Cartographic Intent vs. Runtime State* below. Lives as a versioned document
in `.webgis-agent/` (the doc), **not** a replacement for `map_state`.

### MapSpec Source (geojson source entry)
A per-key entry under a MapSpec document's `sources` map. Carries `type` plus **exactly one** data
key. Two source types now exist:
- **`type:"geojson"`** — exactly one of `inlineData` (the dict payload, travelling inside the doc),
  `url` (a string: a real HTTP/local URL, or — as a known overload — an opaque `ref:xxx` cursor), or
  `dataPath` (a ref path; **read-only in Python today**, only written by the TS side or carried by
  existing checkpoints).
- **`type:"raster"`** — a single-resolution georeferenced PNG (ADR-0011). Carries `imageRef` (an
  opaque `ref:`-style cursor into the session raster store), `bounds` ([w,s,e,n] WGS84), and
  `imageSize` ([w,h] px). Compiled to a MapLibre `image` source; colormap is baked into the PNG at
  render time, not data-driven.

This shape knowledge is owned by the `app/services/mapspec_source.py` pure-function module
(ADR-0008); producers call it rather than re-deriving "which key holds the data" inline.

The style-method discriminant field is **`method`** (e.g. `{"method": "interpolate", "field":
"mag", "stops": [...]}`), per the originating spec doc. This is distinct from a layer's
MapLibre `type` (`circle`/`line`/`fill`); a style-method object carries `method` to avoid the
two-`type` collision. The TS compiler (`frontend/lib/mapspec-compiler/`) is the sole authority
for compiling MapSpec → MapLibre style.

### Cartographic Intent vs. Runtime State
Two sources with distinct responsibilities — **currently connected headless-only, not live**:
- **Runtime State** = `map_state` in `SessionStore` (Redis). What the running map *actually
  renders*: live layer refs, viewport, and inline `layer.style` + `layer.legend_spec` (the
  live-map overlay path). The frontend `map-kit` renders from this.
- **Cartographic Intent** = `MapSpec`. What the map *should be*: high-level style/layout/legend.
- The **MapSpec Compiler** turns Intent → MapLibre Style (`style.json`/`index.html`). **This
  output does NOT feed the live map today** — it drives only headless consumers: the Playwright
  runtime validator, eval-evidence scoring, and the static exporter. The function that would
  apply compiled MapSpec to the live map (`applyMapSpecToMap`) has no live callers. Making the
  live map render from compiled MapSpec is an unrealized spec aspiration (the "dual-write
  tension"), not the current architecture.
- **MapSpec is authoritative for intent and headless acceptance.** Frontend live-UI edits
  (drag, opacity slider, paint tweaks) write `map_state` directly and are **transient** — they
  are *not* back-synced to MapSpec. To persist a manual edit, the user invokes
  `webgis_layer_upsert` (intended to surface as a "保留 (Keep)" button when a live edit
  diverges from MapSpec — that diff detector is itself an open item, #202).
- Layers in MapSpec reference data by `ref_id` at runtime (Fetch-on-Demand preserved); only at
  **checkpoint** is the payload behind the ref materialized (snapshot copy), so a checkpoint is
  self-contained and replayable.

### MapSpecLifecycleEngine
The deep cartographic intent engine (`app/services/mapspec/lifecycle_engine.py`).
Consolidates MapSpec document mutations (`InitProjectIntent`, `SetViewIntent`, `UpsertLayerIntent`, `RemoveLayerIntent`), source profiling, compiler coordination, and checkpoint generation behind an atomic `apply_mutation(session_id, intent)` seam.

### MapSpec Compiler
Deterministic, framework-agnostic TS module (`frontend/lib/mapspec-compiler/`) that turns a
MapSpec into MapLibre `style.json` + `index.html`. **Consumed by headless consumers** — the
Playwright runtime validator, eval-evidence capture, and the static map exporter — so it is
the single source of truth for "what this MapSpec renders to *headlessly*". The live `map-kit`
map reconciles against a derived MapSpec via the **MapSpecRuntime** (ADR-0036); see below.
Supports **GeoJSON sources only** in this refactor; PMTiles/OGC/Cesium/OpenLayers are deferred
to "后续 Adapter".

### MapSpecRuntime (Live Reconciliation Engine)
The deep module (`frontend/lib/mapspec-runtime/`, ADR-0036) that bridges the two previously-
disconnected worlds of *Cartographic Intent* and *Runtime State* on the live map. A pure
`hudStateToMapSpec` adapter projects the HUD store's `Layer[]` (+ processLayers +
activeFilters + is3D) into a flat `MapSpec`; the `MapSpecRuntime` class reconciles that spec
against the live MapLibre instance via `diffSpecs` (pure, in the compiler package) → minimal
source/layer/view patch → side-effectful application reusing `map-kit/renderer`'s cache-aware
helpers. Replaces the orphaned `applyMapSpecToMap` (zero callers) and the ~225-line imperative
render loop + 6 stale-closure refs that previously lived in `map-panel.tsx`. View changes stay
imperative (`flyTo`); only sources/layers/paint are reconciled.

### Tool Catalog (webgis_*)
The 11 `webgis_*` tools (`webgis_project_init`, `webgis_state_get`, `webgis_source_profile`,
`webgis_view_set`, `webgis_layer_upsert`, `webgis_layout_set`, `webgis_validate`,
`webgis_compile_maplibre`, `webgis_runtime_validate`, `webgis_checkpoint`, …) are the canonical
tool names, **hard-migrated** from the legacy `add_layer` / `set_view` / etc. via a central
`old→new` alias table at the `ToolRegistry.dispatch()` entry, so Pi bridge, history replay, and
tests all cross one normalization boundary. Legacy names carry no alias; stored history is
translated through the table on replay.

### Runtime Validator
Headless Playwright over a **static** `index.html`+`style.json` produced by the MapSpec Compiler
(not the live Next.js app). Self-contained and replayable: serves its own read-only static
server, drives Chromium, emits PNG/trace/`report.json`. Verifies `mapLoaded`, `mapIdle`,
console/page/network errors, canvas-blank rate, and control overflow/collision. Live-Next.js
regression testing is out of scope.

### Eval Evidence
Per-run artifacts auto-captured: MapSpec revisions, Spatial Meta Profiles, `style.json`,
`index.html`, PNG, Playwright trace, `report.json`, cost stats. Scored on 5 computable
dimensions (spatial/data correctness 25%, task completion 20%, browser runtime 15%,
traceability/safety/reproducibility 10%, tool-call efficiency/cost 10%). Cartographic quality
(20%) is **deferred** pending the future `webgis-visual-judge`; reported scores are normalized
to an 80% max until then.

### Spatial Meta Profile
The statistical/metadata summary of a source (GeoJSON only in this refactor): BBOX, suggested
view, CRS, feature count, geometry types, field names/types/sample-values, numeric field
min/max/mean/histogram. Calculated via the canonical `geojson_bbox` walker (`app/utils/geojson.py`),
which structurally traverses Feature / Geometry / Collection elements. Empty sources return `None`
for BBOX (rather than `[0,0,0,0]`), suppressing `suggestedView` to prevent Null Island (`[0,0]`) view
injection. **Auto-injected**: the *first* dissectable layer auto-writes `view.center`/`view.zoom` on
its first upsert (only when the view has not been explicitly set); the Agent can override via
`webgis_view_set`. Prevents blind-guessing viewport and breaks. PMTiles metadata is deferred to
the "后续 Adapter" queue.

### Checkpoint
A self-contained snapshot of a MapSpec at a point in time: the intent doc *plus* the materialized
payload of every `ref_id` it references (copied into the snapshot dir). Enables rollback, diff,
and replay without the live Redis store.

### Analysis Result
The canonical shape returned by a spatial-analysis algorithm (`GeoAnalysisResult.to_llm_response()`):
a `{success, summary, data, error_type?, correction_hint?}` dict where `data` is a GeoJSON
FeatureCollection (for geometry-emitting algorithms) or a `{stats, ...}` dict (for pure-statistic
algorithms). The dataclass **never carries `legend_spec`** — `to_llm_response()` emits only the
fields above. Thematic markers (`legend_spec`, `algorithm`, `source_ref`) are attached **after**
flattening, by inline emitters at the tool layer (`h3_binning`, `heatmap_data`, `kde_contours`,
`create_thematic_map`, `apply_template`), and **only one** of those (`h3_binning`) routes through
the dataclass — the rest attach `legend_spec` to a raw dict that bypasses `GeoAnalysisResult`
entirely. See ADR-0009: analysis-result identity is *not* the divergent concern; the `legend_spec`
attachment location is.

### legend_spec
The classification contract an analysis algorithm emits to describe how its output *should be
colored*. A discriminated object with a `type` of `graduated` (class breaks + a palette of hex
colors, one per class), `continuous` (a min/max range + palette ramp), or `categorical` (a list of
`{key, color, label}` entries). Produced by `CartographyService.build_legend_spec` and five inline
emitters (`h3_binning`, `kde_contours`, `heatmap_data`, `create_thematic_map`, `apply_template`).
This is a **distinct, parallel contract**
from a MapSpec `StyleMethod`: `legend_spec` feeds the live-map `<ThematicLegend>` overlay path,
whereas a MapSpec layer's `paint.color` (a `step`/`interpolate`/`match` `StyleMethod`) is what the
MapSpec Compiler auto-derives its own legend from. The two legend pipelines do not share a type.

### Thematic vs. Geometry-only Analysis
Two classes of spatial-analysis output, distinguished by whether a `legend_spec` is attached.
**Thematic** algorithms (hotspot, h3-binning, kde-contours, heatmap, choropleth/thematic-map) carry
data-driven classification → they style a MapSpec layer with a `step`/`interpolate`/`match`
`StyleMethod` derived from their `legend_spec`. **Geometry-only** algorithms (buffer, clip, dissolve,
overlay, voronoi, isochrone, fishnet, nearest-neighbor) emit pure geometry with no classification →
they style a MapSpec layer with a `constant` paint and sensible per-type defaults. Both flow through
the same ingestion path; the difference is only how rich the resulting paint is.

### Derived Layer (Analysis-backed)
A MapSpec layer whose source data is the output of a spatial-analysis algorithm, rather than a
user-provided or fetched dataset. Persisted like any other layer — its GeoJSON travels inline
(checkpoint-replayable) — but additionally carries **provenance** metadata recording which algorithm
produced it, from which source, with which parameters, and at what time. Provenance is audit
lineage; it is opaque to the MapSpec Compiler (which renders from the materialized GeoJSON + paint)
and surfaces best-effort `warnings` (e.g. `mixed_geometries`) rather than blocking the layer.

### Raster Layer (Analysis-backed, single-resolution)
A MapSpec layer backed by a computed raster array (NDVI, slope, etc.), distinct from the
geometry-only vector Derived Layer. The `rs_service`-computed array (previously discarded — see
ADR-0011) is now rendered to a single-resolution georeferenced PNG by
`raster_cartography_converter.py`, stored under `.webgis-agent/<sid>/raster/`, and referenced by a
`type:"raster"` MapSpec source (image source + bounds). The colormap is baked into the PNG at render
time (not data-driven); a parallel `legend_spec` (continuous: min/max + palette) carries the
"what these colors mean" for the live-map overlay path — the same two-pipeline split as vector
(ADR-0007). Multi-resolution zoom (XYZ/COG) and the upload-raster `UploadRecord` path remain out of
scope.

### NetworkGraphEngine
The deep topological graph construction, caching, snapping, and network routing engine (`app/services/network/engine.py`).
Encapsulates NetworkDataset parsing (LineString/MultiLineString topology, intersection splitting, endpoint snapping, one-way direction, cost attributes), TravelProfile impedances (walking, driving, cycling, custom), point snapping with tolerance confidence, shortest path routing, OD cost matrix, closest facility V2, service area / isochrone polygons, network accessibility, location-allocation optimization, and route optimization (VRP).

### TravelProfile
A structured value object (`app/services/network/models.py`) defining network traversal rules including mode (`walking`, `driving`, `cycling`, `custom`), default speed ($km/h$), impedance field name (`length_m`, `travel_time_s`, `cost`), allowed edge types, direction constraints (`one_way`), and turn/intersection penalties.

### NetworkDataset
A topological network data structure containing Nodes, Edges, Junctions, CRS metadata, spatial index, and TravelProfile graph caches built from vector road networks or OSM fetches. Encapsulates fingerprint-based LRU graph caching (`network_fingerprint + profile_hash + params_hash`).

### PointSnappingResult
The result of snapping a point feature (facility or demand) onto the nearest valid network edge/node, returning the snapped WGS84 coordinate, edge ID, fractional position Along-Edge ($[0.0, 1.0]$), perpendicular distance ($meters$), and snapping confidence score ($[0.0, 1.0]$) with structured correction hints on tolerance breach.

### AccessibilityResult
A data-grounded accessibility evaluation result (`app/services/network/models.py`) calculating 15-minute or target-break coverage of demand/population layers over facilities via network distance. Contains served population, unserved population, coverage percentage, average network travel cost, zone metrics, and derived MapSpec spatial layer.

### TemporalDatasetProfile
An automated temporal profiling value object (`app/services/temporal/models.py`) analyzing vector/raster datasets to identify temporal timestamp/datetime fields, time types (`instant`, `interval`), temporal extents ($[T_{\text{min}}, T_{\text{max}}]$), resolution (`hour`, `day`, `month`, `year`), record counts, timezone metadata, temporal gaps, and profiling confidence score ($[0.0, 1.0]$).

### TemporalEngine
The deep temporal GIS computation runtime (`app/services/temporal/engine.py`).
Encapsulates `temporal_filter` (instant, range, relative window), `temporal_aggregate` (hourly, daily, monthly, yearly stats), `temporal_change` ($T_1$ vs $T_2$ count/attribute/geometry deltas), `temporal_trend` (Sen's slope, moving averages, anomalies), `spatiotemporal_hotspot` (space-time window clustering), and windowed `temporal_raster` time series analysis without reading entire rasters into memory.

### MapSpec Time Dimension
The declarative time extension section in MapSpec schema (`app/services/mapspec/models.py`), adding optional `time` parameters (`enabled`, `field`, `type`, `extent`, `current`, `window`, `playback`, `step`, `speed`). Preserves 100% backward compatibility for non-temporal MapSpec documents while powering interactive timeline UI sliders and zero-reconstruct feature/raster filtering in the map runtime.

## Key Relationships

```
Organization 1 ── * User
Organization 1 ── * Layer
User 0..1 ── * Conversation   (nullable: anonymous sessions)
User 1 ── * LayerPermission
Layer 1 ── * LayerPermission
Conversation 1 ── * Message
Conversation 1 ── * Report
Conversation 1 ── 1 SessionData (in-memory/Redis)
Conversation 1 ── * TaskInfo
Layer (result_layer_id) ◄── AnalysisTask
UploadRecord (session_id) ──── Conversation
Document 1 ── * Chunk
NetworkDataset 1 ── * Node / Edge (Spatial & Graph Topology)
NetworkGraphEngine ── 1 LRU GraphCache (Fingerprint + Profile)
TemporalDatasetProfile ◄── TemporalEngine (Time Analysis & Raster Sequences)
MapSpec 1 ── 0..1 MapSpec Time Dimension (Timeline UI & Temporal Rendering)
```
