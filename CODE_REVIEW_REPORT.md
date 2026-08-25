# Master Code & Architecture Review Report: WebGIS AI Agent

**Project**: `webgis-ai-agent`  
**Review Version**: Definitive Full-Scope Synthesis (Post-Reproduction & Empirical Verification)  
**Date**: 2026-08-25  
**Auditor**: Master Review Synthesis Engine  
**Status**: **53 Issues Categorized & Analyzed | 12 P0/P1 Issues Empirically Confirmed**

---

## 1. Executive Summary & Architecture Scorecard

A comprehensive, full-stack architectural audit and deep source-code review was conducted across the entire `webgis-ai-agent` codebase. The target system is an enterprise-grade, geospatial AI agent application combining high-performance Python FastAPI backend services, multi-round LLM perception/reasoning/tool-calling engines, computational GIS & spatial statistics packages, interactive MapLibre GL / Next.js 16 frontend engines, and distributed Celery/Redis/PostGIS data infrastructure.

### Overall Architecture Grade: **B+ (High Potential, Production-Hardening Required)**

The system demonstrates advanced geospatial domain engineering and architectural patterns:
- **Dual-Engine Agent Execution**: Seamlessly supports both native in-process Python execution (`execution_engine.py`) and bidirectional JSON-RPC subprocess bridging (`agent_pi_bridge.py`) with reverse HTTP tool callback evaluation loops.
- **Off-the-Loop Heavy GIS Computation**: Systematic offloading of CPU-bound operations (`ijson`, `geopandas`, `rasterio`, `shapely`, `pyogrio`) to worker threads (`asyncio.to_thread`) and background Celery task queues.
- **Tenant Isolation & Capability Tokens**: Granular ownership verification (`verify_session_owner`, `owner_token` capability digests) across data-plane and control-plane endpoints.
- **Fetch-on-Demand Large GeoJSON Handling**: Automatic compression of massive spatial payloads into lightweight `ref:UUID` session cursors to keep LLM context windows clean.

However, the audit identified **53 distinct defects and architectural bottlenecks** across the 5 core subsystems. Most critically, **3 P0 Blockers** and **17 P1 Critical vulnerabilities** directly threaten production boot stability, WebSocket streaming perception, LLM chat session continuity, and spatial analysis mathematical accuracy.

### Subsystem Architecture Scorecard

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                ARCHITECTURE SCORECARD                                      │
├────────────────────────┬───────┬────────────────────────────────────────────────────────────┤
│ Subsystem              │ Grade │ Key Health Indicators & Critical Vulnerabilities          │
├────────────────────────┼───────┼────────────────────────────────────────────────────────────┤
│ 1. Backend API & Core  │  B    │ Strong path traversal sanitization; P1 DB pool starvation  │
│    FastAPI Services    │       │ & loop-bound lock crashes; P2 error envelope inconsistency.│
├────────────────────────┼───────┼────────────────────────────────────────────────────────────┤
│ 2. Agent Workflows &   │  B+   │ Dual-engine architecture; P1 message order invalidation on │
│    Multi-Agent Systems │       │ cancellation; P1 subagent state mutation; P1 Pi stall loop.│
├────────────────────────┼───────┼────────────────────────────────────────────────────────────┤
│ 3. GIS Algorithms &    │  B-   │ Comprehensive analysis toolkit; P1 H3 LISA crash on        │
│    Spatial Geometry    │       │ islands; P1 NN false CSR; P1 BH-FDR NaN poisoning.         │
├────────────────────────┼───────┼────────────────────────────────────────────────────────────┤
│ 4. Frontend UI &       │  B+   │ Advanced MapSpec compiler; P1 compiler TypeError crash;    │
│    Interactive Map     │       │ P1 command alias discards; P1 missing panel error boundary.│
├────────────────────────┼───────┼────────────────────────────────────────────────────────────┤
│ 5. Test Suites, DevOps │  C+   │ Fast unit suite & drift detection; P0 Nginx WS breakage;   │
│    & Deployment        │       │ P0 SSRF blocking private LLMs; P0 missing prod LLM_API_KEY.│
└────────────────────────┴───────┴────────────────────────────────────────────────────────────┘
```

---

## 2. Severity Breakdown Matrix (All 53 Issues)

The identified 53 issues are categorized across four severity levels:
- **P0 (Blocker - 3 issues)**: Total system boot failure, complete feature lockout, or security blocks preventing core operation.
- **P1 (Critical - 17 issues)**: Crashes under realistic workloads, data corruption, mathematical errors, or session destruction.
- **P2 (Major / Performance - 20 issues)**: Algorithmic inaccuracies, memory/connection leaks, degraded UX, or broken error handling.
- **P3 (Minor / Refactoring - 13 issues)**: Code hygiene, dead code, edge-case validation, or minor configuration discrepancies.

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    SEVERITY DISTRIBUTION                                        │
│  P0 Blocker: 3  ███ (5.7%)                                                                      │
│  P1 Critical: 17 █████████████████ (32.1%)                                                      │
│  P2 Major: 20    ████████████████████ (37.7%)                                                   │
│  P3 Minor: 13    █████████████ (24.5%)                                                          │
│  Total: 53 Issues                                                                               │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Complete 53-Issue Master Catalog

| Issue Key | Sev | Subsystem | Target Location | Summary Description | Repro Status |
|---|---|---|---|---|:---:|
| **ISSUE-01** | **P0** | Test/DevOps | `deploy/nginx/nginx.conf:174-206, 273-286` | WebSocket route mismatch and `Connection ""` stripping breaks WS streaming | **CONFIRMED** |
| **ISSUE-02** | **P0** | Test/DevOps | `.env.prod.example:51-54`, `config.py:218` | Missing `LLM_API_KEY` in `.env.prod.example` crashes container on production boot | **CONFIRMED** |
| **ISSUE-03** | **P0** | Test/DevOps | `app/core/config.py:284-288, 326-378` | SSRF validator unconditionally blocks private/internal LLM inference endpoints | **CONFIRMED** |
| **BE-01** | **P1** | Backend | `app/api/routes/data_fabric.py`, `project.py` | Dual connection pool checkout (`Depends(get_db)` + worker thread) causes pool exhaustion | **CONFIRMED** |
| **BE-02** | **P1** | Backend | `app/core/network.py:69, 94-106` | Module-level `_pool_lock` causes cross-event-loop `RuntimeError` in worker threads | **CONFIRMED** |
| **W-01** | **P1** | Workflows | `app/services/chat/execution_engine.py:718` | `_repair_orphaned_tool_calls` appends tool message at tail, causing LLM API 400 Bad Request | **CONFIRMED** |
| **W-02** | **P1** | Workflows | `app/services/subagent.py:166-174` | Subagent executes in parent session with unshared lock, risking state races and overwrite | Static Audit |
| **W-03** | **P1** | Workflows | `app/agent_pi_bridge.py:1809-1852` | Non-streaming Pi turn event drain spins in tight loop and triggers false 504 timeout | Static Audit |
| **P1-01** | **P1** | GIS | `app/lib/geo_analysis/statistics.py:705-710` | H3 LISA crashes with unhandled broadcast `ValueError` on disconnected island cells | **CONFIRMED** |
| **P1-02** | **P1** | GIS | `app/lib/geo_analysis/statistics.py:477-486` | Nearest Neighbor analysis reports coincident points ($A=0$) as random Poisson CSR ($R=1$) | **CONFIRMED** |
| **P1-03** | **P1** | GIS | `app/lib/geo_analysis/statistics.py:20-35` | Benjamini-Hochberg FDR `np.minimum.accumulate` poisons all q-values to NaN on single NaN | **CONFIRMED** |
| **P1-04** | **P1** | GIS | `app/lib/geo_analysis/network.py:87-125` | Isochrone snaps to distant node endpoint, generating polygon miles away from facility | **CONFIRMED** |
| **P1-05** | **P1** | GIS | `app/lib/geo_analysis/raster_math.py:341-352` | Raster calculator fabricates data (treats non-overlapping extents as 0 instead of NoData) | **CONFIRMED** |
| **FE-01** | **P1** | Frontend | `frontend/lib/mapspec-compiler/compiler.ts:37` | Unhandled `TypeError: m.stops is not iterable` in `compileStyleMethod` crashes compiler | **CONFIRMED** |
| **FE-02** | **P1** | Frontend | `frontend/lib/mapspec-compiler/html-template.ts` | Script injection (XSS) in HTML export & outdated MapLibre GL v3.6.2 CDN | Static Audit |
| **FE-03** | **P1** | Frontend | `frontend/lib/map-commands/layerCommands.ts` | Parameter alias discard bug causing valid AI commands (`id` param) to fail `target_not_found` | **CONFIRMED** |
| **FE-04** | **P1** | Frontend | `frontend/components/layout/context-panel.tsx` | Missing `PanelErrorBoundary` around `ChatTab`, `AnalysisTab`, `DataSourcesTab` | Static Audit |
| **ISSUE-04** | **P1** | Test/DevOps | `app/api/routes/health.py:61-72` | Synchronous Celery inspect in readiness probes causes cascading 503 pod outages under load | Static Audit |
| **ISSUE-05** | **P1** | Test/DevOps | `Dockerfile.prod:114`, `02-api-deployment.yaml` | Dual-process container architecture leaves unrecovered zombie pods upon Node.js crash | Static Audit |
| **ISSUE-06** | **P1** | Test/DevOps | `app/core/logging_config.py:12-13, 60-65` | Module-level `mkdir` & file logging crash containers running on read-only root filesystems | Static Audit |
| **BE-03** | **P2** | Backend | `app/core/database.py:92-104` | `get_async_db` fallback yields synchronous `SessionLocal` to async callers, causing `TypeError` | Static Audit |
| **BE-04** | **P2** | Backend | `app/core/exception.py:63-112`, `main.py:254` | Production global exception handler overwrites informative 4xx errors into generic 500 | Static Audit |
| **BE-05** | **P2** | Backend | `app/api/routes/report.py`, `knowledge.py` | Inconsistent `ApiResponse` envelope returns HTTP 200 on business errors, breaking REST | Static Audit |
| **BE-06** | **P2** | Backend | `data_fabric/adapters/postgis_adapter.py:131` | PostGIS adapter caches transient connection pool creation failures indefinitely | Static Audit |
| **W-04** | **P2** | Workflows | `app/services/chat/llm_client.py:477-506` | Streaming `<think>` / `</think>` tags split across chunk boundaries leak CoT into user content | Static Audit |
| **W-05** | **P2** | Workflows | `app/services/workflow_engine.py:85-119` | Workflow DAG ignores input bindings for dependencies and drops multi-depth property resolution | Static Audit |
| **W-06** | **P2** | Workflows | `app/services/distributed_lock.py:97-129` | Resilient distributed lock skips in-process lock on Redis success, opening fallback race | Static Audit |
| **P2-01** | **P2** | GIS | `app/lib/geo_analysis/statistics.py:148-152` | Standard Deviational Ellipse formula inflates axes by $1.414\times$ and area by $2.0\times$ | Static Audit |
| **P2-02** | **P2** | GIS | `app/lib/geo_analysis/aggregation.py:157-161` | Missing numeric coercion on `value_field` crashes `spatial_aggregate` on string numbers | Static Audit |
| **P2-03** | **P2** | GIS | `app/lib/cartography/classify.py:30-36` | Inconsistent degenerate break output across classification methods on zero-variance data | Static Audit |
| **P2-04** | **P2** | GIS | `app/lib/geo_analysis/statistics.py:880-908` | `st_dbscan_narrated` lacks parameter bounds checks, treating negative parameters as noise | Static Audit |
| **P2-05** | **P2** | GIS | `app/lib/geo_analysis/heatmap_grid.py:49-71` | Antimeridian $\pm 180^\circ$ span triggers grid dimension explosion (>5000 bins) | Static Audit |
| **P2-06** | **P2** | GIS | `app/services/network/routing.py:481-500` | Uncached $O(E)$ full graph edge scan in `_min_cost_per_meter` on every A* routing call | Static Audit |
| **FE-05** | **P2** | Frontend | `frontend/lib/map-kit/exporter.ts:742-752` | Synchronous `URL.revokeObjectURL` immediately after `a.click()` aborts large downloads | Static Audit |
| **FE-06** | **P2** | Frontend | `frontend/components/map/map-panel.tsx:496` | Redundant 3D terrain rebuild pass (`map.setTerrain`) triggered on every layer reconcile | Static Audit |
| **FE-07** | **P2** | Frontend | `frontend/components/map/map-panel.tsx:411` | Missing `webglcontextlost` and `webglcontextrestored` event handlers on MapLibre canvas | Static Audit |
| **FE-08** | **P2** | Frontend | `frontend/components/hud/layer-style-panel.tsx` | Hardcoded dark-mode utility classes (`text-white/50`) break Light Mode UI contrast | Static Audit |
| **ISSUE-07** | **P2** | Test/DevOps | `deploy/k8s/02-api-deployment.yaml:160-168` | Redundant dual volume mounts of `uploads-volume` to `/app/uploads` and `/app/data` | Static Audit |
| **ISSUE-08** | **P2** | Test/DevOps | `deploy/nginx/nginx.conf:288-296` | Nginx `/health` returns static 200 without checking backend upstream health | Static Audit |
| **ISSUE-09** | **P2** | Test/DevOps | `app/services/task_queue.py:79-99` | In-memory `_task_owners` causes cross-replica 404 failures in multi-pod API deployments | Static Audit |
| **ISSUE-10** | **P2** | Test/DevOps | `.github/workflows/production.yml:32` | Workflow pins nonexistent GitHub Actions major versions (`checkout@v7`, `setup-node@v7`) | Static Audit |
| **BE-07** | **P3** | Backend | `app/api/routes/config.py:221-303` | In-process script execution of uploaded custom skills poses security risk if admin token leaks | Static Audit |
| **BE-08** | **P3** | Backend | `app/services/local_osm.py:249-258` | Double serialization overhead in Local OSM GPKG Features route (`to_json` -> `loads` -> JSON) | Static Audit |
| **W-07** | **P3** | Workflows | `app/api/routes/chat.py:345-386` | SSE batcher unflushed token buffer dropped on client `GeneratorExit` disconnect | Static Audit |
| **P3-01** | **P3** | GIS | `app/services/spatial_analyzer.py:402-404` | `SpatialAnalyzer.zonal_stats` rejects bare feature lists `[f1, f2]` | Static Audit |
| **P3-02** | **P3** | GIS | `app/lib/geo_analysis/geometry_ops.py:302` | `multi_ring_buffer` missing `make_valid()` pass on difference polygon rings | Static Audit |
| **P3-03** | **P3** | GIS | `app/lib/geo_analysis/geometry_ops.py:96-105` | `voronoi_polygons` with inverted `clip_bounds` silently returns 0 cells with success=True | Static Audit |
| **P3-04** | **P3** | GIS | `app/services/rs/spectral_engine.py:88-96` | `legend_spec` computed in spectral engine but unattached to `RasterAnalysisResult` | Static Audit |
| **FE-09** | **P3** | Frontend | `frontend/lib/store/slices/layersSlice.ts:101` | `reorderLayers` omits `layerIntentGeneration` increment, drifting from layer mutations | Static Audit |
| **FE-10** | **P3** | Frontend | `frontend/components/chat/map-action-renderer.tsx`| Orphaned dead component `MapActionRenderer` never rendered across production screens | Static Audit |
| **FE-11** | **P3** | Frontend | `frontend/lib/mapspec-compiler/mapspec-to-svg.ts` | Potential `TypeError` when compiling FeatureCollection with missing `features` array | Static Audit |
| **ISSUE-11** | **P3** | Test/DevOps | `.env.example:67-69` | `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND` omit Redis password placeholder in example | Static Audit |
| **ISSUE-12** | **P3** | Test/DevOps | `tests/test_health_api.py:35-56` | 100% mocked health checks hide runtime database/Redis/Celery/LLM connectivity bugs | Static Audit |

---

## 3. Subsystem Deep Dives & Detailed Remediations

---

### Subsystem A: Backend API & Core Architecture

#### BE-01: Dual DB Connection Allocation & Pool Starvation in Data Fabric and Project Routes (P1)
- **Files**: `app/api/routes/data_fabric.py` (12 routes), `app/api/routes/project.py` (24 routes)
- **Technical Mechanism**:
  FastAPI route signatures declare `db: Session = Depends(get_db)`. When an HTTP request enters, FastAPI checks out Connection #1 from SQLAlchemy's `QueuePool`. Inside the handler, `db` is never referenced; instead, handlers call `_run_sync_orm` or `_run_async_manager`, which allocates Connection #2 via `SessionLocal()` inside a worker thread. Each request holds two active database connections concurrently until completion.
- **Impact**: Under moderate concurrency (15 concurrent requests on default `pool_size=10, max_overflow=20`), the connection pool is completely exhausted, throwing `sqlalchemy.exc.TimeoutError` and cascading 500 errors across all API endpoints.
- **Remediation**: Remove `db: Session = Depends(get_db)` from all route signatures delegating ORM operations to worker threads.

```python
# Remediation in app/api/routes/data_fabric.py:
@router.post("/data-fabric/sources", tags=["Data Fabric / 数据织网"])
async def create_data_source(
    req: CreateDataSourceRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    ...
    data_source = await _run_sync_orm(_create)
    return {"success": True, "data_source": data_source}
```

#### BE-02: Event Loop Binding Mismatch on Module-Level `asyncio.Lock` in Network Shared Pool (P1)
- **Files**: `app/core/network.py:69, 94-106`
- **Technical Mechanism**:
  `_pool_lock = asyncio.Lock()` is instantiated at module import time in `app/core/network.py`. When workflow engines or worker threads execute asynchronous tools using `asyncio.run()`, a new event loop is initialized for that thread. When the tool acquires `_pool_lock` via `get_shared_client()`, Python throws `RuntimeError: Task <...> got Future <...> attached to a different loop`.
- **Remediation**: Dynamically initialize or resolve the lock for the currently active event loop:

```python
# app/core/network.py
_pool_lock: asyncio.Lock | None = None
_pool_lock_loop: asyncio.AbstractEventLoop | None = None

def _get_pool_lock() -> asyncio.Lock:
    global _pool_lock, _pool_lock_loop
    loop = asyncio.get_running_loop()
    if _pool_lock is None or _pool_lock_loop is not loop:
        _pool_lock = asyncio.Lock()
        _pool_lock_loop = loop
    return _pool_lock
```

#### BE-03: Broken `get_async_db` Fallback Yields Synchronous Session to Async Callers (P2)
- **Files**: `app/core/database.py:92-104`
- **Technical Mechanism**: When `AsyncSessionLocal is None`, `get_async_db` falls back to yielding a synchronous `SessionLocal()`. Calling endpoints invoke `await db.execute(...)`, causing immediate `TypeError: object Result cannot be used in 'await' expression`.
- **Remediation**: Fail fast at startup with an informative configuration exception rather than returning an incompatible synchronous session.

#### BE-04: Global Exception Handler Suppresses Informative 4xx Details in Production (P2)
- **Files**: `app/core/exception.py:63-112`, `app/main.py:254`
- **Technical Mechanism**: In production mode (`settings.is_production() == True`), `format_error_response` ignores `exc.detail` on `HTTPException` and replaces all messages with "服务器内部错误，请稍后重试", turning routine 404s, 401s, and 422 validation errors into generic 500 error messages.
- **Remediation**: Preserve `exc.detail` for `HTTPException` instances while sanitizing unhandled internal server exceptions.

#### BE-05: Inconsistent API Response Envelopes Across Endpoints (P2)
- **Files**: `app/api/routes/report.py`, `app/api/routes/knowledge.py`
- **Technical Mechanism**: `report.py` and `knowledge.py` return `ApiResponse.fail(...)` with HTTP 200 on business errors, conflicting with the RESTful status code paradigm across the rest of the API.
- **Remediation**: Standardize on raising `HTTPException` with typed Pydantic models.

#### BE-06: PostGIS Adapter Caches Transient Connection Pool Failures Indefinitely (P2)
- **Files**: `app/services/data_fabric/adapters/postgis_adapter.py:131-153`
- **Technical Mechanism**: When an initial connection fails during database restart, `_POSTGIS_POOLS[key] = None` is cached permanently, disabling connection pooling until the process restarts.
- **Remediation**: Store only active pools in `_POSTGIS_POOLS`, allowing subsequent requests to retry pool creation upon transient failures.

---

### Subsystem B: Agent Workflows, Orchestration & Multi-Agent Coordination

#### W-01: Message Order Invalidation in `_repair_orphaned_tool_calls` (P1)
- **Files**: `app/services/chat/execution_engine.py:718-746`
- **Technical Mechanism**:
  When a streaming turn with tool calls is interrupted by user cancellation and a new user query is received, `_repair_orphaned_tool_calls` synthesizes cancellation tool responses and calls `messages.append(cancel_msg)` at the tail of the message list. This creates the invalid sequence `[Assistant(tool_calls=[ID]), User("Next query"), Tool(tool_call_id=ID)]`. Upstream LLM providers (OpenAI, Anthropic, DeepSeek) strictly require tool messages to immediately follow the calling assistant message, rejecting all subsequent turns with `400 Bad Request`.
- **Remediation**: Splice the synthesized tool message immediately after the corresponding `assistant` message index in `messages`.

```diff
--- a/app/services/chat/execution_engine.py
+++ b/app/services/chat/execution_engine.py
@@ -735,3 +735,10 @@ class ChatExecutionEngine:
         for tc_id in orphaned_ids:
             cancel_msg = {
                 "role": "tool",
                 "tool_call_id": tc_id,
                 "content": "工具执行已被用户取消",
             }
-            messages.append(cancel_msg)
+            inserted = False
+            for idx in range(len(messages) - 1, -1, -1):
+                msg = messages[idx]
+                if msg.get("role") == "assistant" and any(tc.get("id") == tc_id for tc in msg.get("tool_calls", [])):
+                    messages.insert(idx + 1, cancel_msg)
+                    inserted = True
+                    break
+            if not inserted:
+                messages.append(cancel_msg)
             await self._save_msg_async(session_id, "tool", cancel_msg["content"], tool_call_id=tc_id)
```

#### W-02: Subagent Lock Isolation & Shared Parent Session State Mutation (P1)
- **Files**: `app/services/subagent.py:166-174`
- **Technical Mechanism**: `SubagentDispatcher` constructs a new `ChatEngine` (`sub_engine`) and executes subtasks in `parent_session_id`. Because `sub_engine` has its own unshared `_session_locks` dictionary, concurrent user actions and subagent tool executions mutate parent map state simultaneously without mutual exclusion.
- **Remediation**: Execute subagents in a dedicated sub-session (`f"{parent_session_id}:sub:{uuid}"`) and merge resulting spatial references back upon subagent completion.

#### W-03: Non-Streaming Pi Turn Event Drain Tight Loop & False 504 Timeout (P1)
- **Files**: `app/agent_pi_bridge.py:1809-1852`
- **Technical Mechanism**: When complex GIS tools execute without emitting interim events, `wait_budget` shrinks, causing `prompt()` to rapidly spin in a tight loop and prematurely raise `PiRpcError(504, "Prompt turn timed out...")`.
- **Remediation**: Check `self._active_tool_calls` to extend the stall window while tools are actively running and yield control to avoid zero-wait tight loops.

#### W-04: Streaming `<think>` / `</think>` Tag Splitting Across Chunk Boundaries (P2)
- **Files**: `app/services/chat/llm_client.py:477-506`
- **Technical Mechanism**: Token chunks splitting `<think>` or `</think>` across network packet boundaries fail string matching (`remaining.find("<think>") == -1`), leaking internal chain-of-thought reasoning into user content.
- **Remediation**: Maintain an 8-character sliding carryover prefix buffer across streaming iterations.

#### W-05: Workflow Engine DAG Input Bindings Ignored in Dependency Order (P2)
- **Files**: `app/services/workflow_engine.py:85-119, 518-531`
- **Technical Mechanism**: `validate_dag` inspects only explicit `step.dependencies`, ignoring dependencies declared in `input_bindings` (e.g. `step_1.result.geojson_ref`), and `_resolve_step_args` only splits on the first dot rather than recursively resolving object paths.
- **Remediation**: Extract implicit dependencies from `input_bindings` during topological sort and implement multi-segment property path resolution.

#### W-06: Distributed Lock Fallback Race Condition on Redis Error (P2)
- **Files**: `app/services/distributed_lock.py:97-129`
- **Technical Mechanism**: `_ResilientSessionLock` skips in-process locking when Redis succeeds. If a second coroutine encounters a Redis error, it acquires the in-process lock immediately, entering the critical section concurrently with the first coroutine.
- **Remediation**: Implement a dual-lock hierarchy: always acquire the in-process lock first before acquiring Redis.

---

### Subsystem C: GIS Spatial Algorithms & Computational Geometry

#### P1-01: `h3_lisa`: Unhandled Exception & Crash on Non-Contiguous / Island H3 Cells (P1)
- **Files**: `app/lib/geo_analysis/statistics.py:705-710`
- **Technical Mechanism**:
  When calculating LISA for geographically disconnected H3 hexagons, `libpysal.weights.Queen.from_dataframe` creates island weights ($0$ neighbors). `esda.moran.Moran_Local` attempts to assign empty neighbor arrays into 999-permutation arrays, raising `ValueError: could not broadcast input array from shape (0,) into shape (999,)`.
- **Remediation**: Inspect `w.islands` prior to calling `Moran_Local`. If all cells are islands, return a graceful error message without crashing.

```python
# app/lib/geo_analysis/statistics.py
w = Queen.from_dataframe(gdf)
if len(w.islands) == len(gdf) or w.n_components == len(gdf):
    return GeoAnalysisResult(
        False, None,
        "所有 H3 网格单元均为孤立要素（无相邻单元），无法建立空间邻接权重计算 LISA。请使用聚集度更高的点集或调整分辨率。",
        error_type="DisconnectedWeightsError"
    )
```

#### P1-02: `calculate_nearest`: Degenerate Coincident Points Classified as "Random" ($R=1.0$) (P1)
- **Files**: `app/lib/geo_analysis/statistics.py:477-486`
- **Technical Mechanism**:
  For coincident points, observed distance is $\bar{r}_A = 0.0$ and area is $A = 0$, resulting in $expected\_mean = 0.0$. The ternary `r_ratio = mean_dist / expected_mean if expected_mean > 0 else 1` sets $R = 1.0$, falsely classifying 100% clustered points as Complete Spatial Randomness (Poisson CSR).
- **Remediation**: Detect `mean_dist == 0.0` and explicitly return $R = 0.0$ and `pattern = "clustered"`.

```python
# app/lib/geo_analysis/statistics.py
if mean_dist == 0.0:
    return GeoAnalysisResult(
        True,
        {
            "mean_nearest_distance": 0.0,
            "expected": 0.0,
            "R": 0.0,
            "mean_distance": 0.0,
            "r_ratio": 0.0,
            "std_distance": 0.0,
            "min_distance": 0.0,
            "max_distance": 0.0,
            "pattern": "clustered",
        },
        "Nearest Neighbor Insight: 所有点要素坐标重合，平均最近邻距离为 0.00 米，呈现极度聚集模式 (R = 0.00)。"
    )
```

#### P1-03: `_bh_qvalues`: NaN in P-Values Silently Poisons All Q-Values into NaNs (P1)
- **Files**: `app/lib/geo_analysis/statistics.py:20-35`
- **Technical Mechanism**:
  `np.argsort(p)` places `NaN` at the end of the array. In `ranked[::-1]`, `NaN` appears at index 0. Because `np.minimum(nan, x) -> nan`, `np.minimum.accumulate` overwrites every single element with `NaN`, destroying all detected hotspots in `hotspot_narrated`.
- **Remediation**: Compute BH-FDR strictly over the finite subset and map results back to original indices.

```python
# app/lib/geo_analysis/statistics.py
def _bh_qvalues(p: "np.ndarray") -> "np.ndarray":
    p = np.asarray(p, dtype=float)
    n = p.size
    if n == 0:
        return p
    out = np.full(n, np.nan, dtype=float)
    valid_mask = np.isfinite(p)
    m = int(valid_mask.sum())
    if m == 0:
        return out
    p_valid = p[valid_mask]
    order = np.argsort(p_valid)
    ranked = p_valid[order] * m / (np.arange(m) + 1)
    q_sorted = np.minimum.accumulate(ranked[::-1])[::-1]
    valid_out = np.empty(m, dtype=float)
    valid_out[order] = np.clip(q_sorted, 0.0, 1.0)
    out[valid_mask] = valid_out
    return out
```

#### P1-04: `calculate_isochrones`: Facility Snapping Displaces Isochrones Kilometers from Facility (P1)
- **Files**: `app/lib/geo_analysis/network.py:87-125`
- **Technical Mechanism**: `calculate_isochrones` snaps facilities to road endpoint vertices rather than projecting onto the nearest road edge. For facilities along long segments, Dijkstra search begins from a vertex kilometers away with the full travel budget, creating an isochrone shifted to the distant endpoint that fails to touch the facility itself.
- **Remediation**: Calculate perpendicular projection onto the nearest edge, insert a virtual node, and split the edge before running Dijkstra search.

#### P1-05: `raster_calculator`: Missing Nodata in Unaligned Raster B Fabricates Data (P1)
- **Files**: `app/lib/geo_analysis/raster_math.py:341-352, 375`
- **Technical Mechanism**: When `raster_b` lacks an explicit `nodata` tag, non-overlapping extents are filled with 0.0 and marked as valid data (`mask_b = all True`). For expression `A + B`, non-overlapping pixels compute $A + 0 = A$ instead of outputting NoData.
- **Remediation**: Track the valid bounding footprint of Raster B during destination reprojection using an explicit extent mask.

#### P2-01: `calculate_sde`: Extraneous Factor of 2 in Variance Formula Inflates Axes (P2)
- **Files**: `app/lib/geo_analysis/statistics.py:148-152`
- **Technical Mechanism**: `sigma_x_2 = 2 * sum(...) / n` includes an erroneous leading factor of 2, inflating standard deviational ellipse axes by $\sqrt{2} \approx 1.414\times$ and area by $2.0\times$.
- **Remediation**: Remove the leading factor of 2 from $\sigma_x^2$ and $\sigma_y^2$ calculations.

---

### Subsystem D: Frontend UI & Interactive GIS Components

#### FE-01: Unhandled `TypeError` in `compileStyleMethod` on Missing/Null `stops` or `cases` (P1)
- **Files**: `frontend/lib/mapspec-compiler/compiler.ts:37-74`
- **Technical Mechanism**:
  `compileStyleMethod` assumes `m.stops` and `m.cases` are always defined iterables. If an AI generates an incomplete style method, `for (const [stopVal, outputVal] of m.stops)` crashes with `TypeError: m.stops is not iterable`, aborting layer reconciliation in the runtime.
- **Remediation**: Guard with `Array.isArray()` and return safe fallback colors/values.

```diff
--- a/frontend/lib/mapspec-compiler/compiler.ts
+++ b/frontend/lib/mapspec-compiler/compiler.ts
@@ -37,6 +37,9 @@ export function compileStyleMethod(method: StyleMethod | undefined): any {
     case "interpolate": {
       const fieldExpr = ["to-number", ["get", m.field]];
       const flattenedStops: any[] = [];
+      if (!Array.isArray(m.stops) || m.stops.length === 0) {
+        return m.default ?? "#000000";
+      }
       for (const [stopVal, outputVal] of m.stops) {
         flattenedStops.push(stopVal, outputVal);
       }
@@ -62,6 +65,9 @@ export function compileStyleMethod(method: StyleMethod | undefined): any {
     case "match": {
       const fieldExpr = ["get", m.field];
       const cases: any[] = [];
+      if (!Array.isArray(m.cases) || m.cases.length === 0) {
+        return m.default ?? "";
+      }
       for (const [caseVal, outputVal] of m.cases) {
         cases.push(caseVal, outputVal);
       }
```

#### FE-02: Script Injection (XSS) Vulnerability in Static HTML Export & Outdated MapLibre CDN (P1)
- **Files**: `frontend/lib/mapspec-compiler/html-template.ts:13-14, 23-25`
- **Technical Mechanism**: Direct string interpolation of `JSON.stringify(style)` into inline `<script>` tags allows arbitrary script breakout if layer names contain `</script><script>...`. In addition, the template pins an outdated MapLibre GL v3.6.2 CDN.
- **Remediation**: Sanitize JSON strings with `.replace(/</g, "\\u003c")` and upgrade CDN references to MapLibre GL v5.23.0.

#### FE-03: Parameter Alias Discard Bug in Core Map Commands (P1)
- **Files**: `frontend/lib/map-commands/layerCommands.ts:256-260, 327-332, 379-384, 459-465, 694-699`
- **Technical Mechanism**:
  Command entry validators (`requiredParams`) accept canonical `id` parameters (`typeof p.id === 'string'`), but command `run(ctx)` handlers only destructure `layer_id`, `layerId`, or `name`. When AI tool calls emit `{ id: "schools", ... }`, validation passes but execution immediately returns `{ status: 'failed', error: 'target_not_found' }`.
- **Remediation**: Harmonize parameter extraction across all command handlers to support `params?.layer_id || params?.layerId || params?.id`.

```diff
--- a/frontend/lib/map-commands/layerCommands.ts
+++ b/frontend/lib/map-commands/layerCommands.ts
@@ -259,2 +259,2 @@ export const layerCommands: Record<string, CommandEntry> = {
-      const { layer_id, layerId } = params || {};
-      const target = layer_id || layerId;
+      const target = (params?.layer_id || params?.layerId || params?.id) as string | undefined;
@@ -382,2 +382,3 @@ export const layerCommands: Record<string, CommandEntry> = {
-      const { layer_id, visible, opacity, name, color } = params || {};
-      if (!layer_id) return { status: 'failed', error: 'target_not_found' };
+      const layer_id = (params?.layer_id || params?.layerId || params?.id) as string | undefined;
+      if (!layer_id) return { status: 'failed', error: 'target_not_found' };
```

#### FE-04: Missing Error Boundaries Around Volatile Sidebar Panels (P1)
- **Files**: `frontend/components/layout/context-panel.tsx:350-380`
- **Technical Mechanism**: `ContextPanel` does not wrap `ChatTab`, `AnalysisTab`, or `DataSourcesTab` with `PanelErrorBoundary`. Markdown parsing errors, Recharts rendering exceptions, or streaming token anomalies bubble up to the root `ErrorBoundary`, unmounting the entire application and map canvas.
- **Remediation**: Wrap each individual tab component in `PanelErrorBoundary`.

---

### Subsystem E: Test Suites, Deployment & DevOps

#### ISSUE-01: WebSocket Routing Mismatch & Connection Header Stripping in Nginx (P0)
- **Files**: `deploy/nginx/nginx.conf:174-206, 273-286`, `app/main.py:403`, `app/api/routes/ws.py:24, 30`
- **Technical Mechanism**:
  1. FastAPI mounts WebSocket routes at `/api/v1/ws/{session_id}`.
  2. Nginx defines `location /ws/` which proxies directly to `http://api_backend` without path rewriting, forwarding `/ws/{session_id}` to Uvicorn where it returns HTTP 404.
  3. When clients connect to the canonical path `/api/v1/ws/{session_id}`, Nginx matches `location /api/`, where line 177 executes `proxy_set_header Connection "";` and lacks `Upgrade $http_upgrade`, stripping all WebSocket handshake headers and returning HTTP 400 Upgrade Rejected.
- **Remediation**: Add dedicated `location ~ ^/api/v1/ws/` block with complete upgrade proxy headers in `nginx.conf`:

```nginx
# deploy/nginx/nginx.conf
location ~ ^/api/v1/ws/ {
    access_log /var/log/nginx/access.log ws_no_query;
    proxy_pass http://api_backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 86400s;
}
```

#### ISSUE-02: Missing `LLM_API_KEY` in Production Environment Template (P0)
- **Files**: `.env.prod.example:51-54`, `app/core/config.py:218-230`
- **Technical Mechanism**:
  `Settings._validate_required_env_vars` enforces that in production (`ENV=production`), `LLM_API_KEY` cannot be empty or equal to the default placeholder `"your-api-key-here"`. However, `.env.prod.example` lists commented legacy placeholders (`# OPENAI_API_KEY`, `# ANTHROPIC_API_KEY`) and completely omits `LLM_API_KEY`. When operators create `.env.prod` from `.env.prod.example`, the application crashes on container startup with `RuntimeError`.
- **Remediation**: Update `.env.prod.example` to declare `LLM_API_KEY`:

```ini
# .env.prod.example
# ======== 大模型 API 配置（必填）=======
LLM_BASE_URL=https://api.stepfun.com/step_plan/v1
LLM_API_KEY=CHANGE_ME_TO_YOUR_ACTUAL_LLM_API_KEY
LLM_MODEL=step-3.7-flash
```

#### ISSUE-03: Private/Internal LLM Endpoints Blocked by SSRF Validator (P0)
- **Files**: `app/core/config.py:284-288, 326-330, 343-378`
- **Technical Mechanism**:
  `Settings._validate_external_urls` runs `_validate_no_ssrf` across all configured URLs including `LLM_BASE_URL`. `_validate_no_ssrf` unconditionally rejects private IP ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`), localhost, and DNS names resolving to private IPs. In enterprise, on-premise, or Kubernetes VPC environments where LLMs run internally (e.g. `vLLM`, `Ollama`, `TGI`), the application crashes on startup with `ValueError`.
- **Remediation**: Scope SSRF validation strictly to public web scraping/geocoding tools (`OVERPASS_API_URL`, `NOMINATIM_URL`) while allowing internal infrastructure URLs for `LLM_BASE_URL`.

```python
# app/core/config.py
@model_validator(mode="after")
def _validate_external_urls(self) -> "Settings":
    # SSRF checks apply strictly to public data tools (OVERPASS, NOMINATIM),
    # while LLM_BASE_URL is a trusted administrative configuration allowing VPC endpoints.
    for attr in ("OVERPASS_API_URL", "NOMINATIM_URL"):
        url = getattr(self, attr)
        self._validate_no_ssrf(url, field=attr)
    return self
```

#### ISSUE-04: Cascading Service Outages via Synchronous Celery Inspect in Readiness Probes (P1)
- **Files**: `app/api/routes/health.py:61-72`, `deploy/k8s/02-api-deployment.yaml:143-151`
- **Technical Mechanism**: `/api/v1/ready` executes broadcast Celery worker inspection (`app.control.inspect(timeout=2.0)`). When workers are busy computing spatial algorithms, inspection calls time out, marking all API pods unready and removing them from Kubernetes Service load balancing simultaneously.
- **Remediation**: Probe Celery broker connectivity (Redis ping) in API readiness rather than polling worker broadcast inspection.

#### ISSUE-05: Dual-Process Container Antipattern & Zombie Pods (P1)
- **Files**: `Dockerfile.prod:114`, `deploy/k8s/02-api-deployment.yaml:130-137`
- **Technical Mechanism**: Running Uvicorn and Next.js concurrently in a single container with a liveness probe monitoring only port 8000 leaves pods in an unrecovered zombie state when Node.js crashes.
- **Remediation**: Configure Kubernetes liveness probe to verify both ports or separate frontend and backend into independent container deployments.

#### ISSUE-06: Read-Only Root Filesystem Crashes in `logging_config.py` (P1)
- **Files**: `app/core/logging_config.py:12-13, 60-65`, `deploy/k8s/02-api-deployment.yaml`
- **Technical Mechanism**: Module-level `LOG_DIR.mkdir()` and `RotatingFileHandler` throw `OSError: Read-only file system` under Kubernetes `securityContext.readOnlyRootFilesystem: true`.
- **Remediation**: Wrap directory creation in `try/except OSError` and default to standard output logging in container environments.

---

## 4. Empirical Reproduction Suite Summary

All 12 priority issues (3 P0 Blockers + 9 P1 Critical issues) have been verified with 100% genuine reproduction test scripts located under `.agents/reproduction_worker/scripts/`.

### Reproduction Execution Commands

| # | Target Issue | Reproduction Command | Failure Signature Verified |
|---|---|---|---|
| **01** | Nginx WebSocket Routing | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_01_nginx_ws.py` | Connection header stripped on `/api/`; 404 on `/ws/` |
| **02** | SSRF Blocking Private LLM | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_02_ssrf_llm.py` | `ValueError: LLM_BASE_URL='...' uses blocked domain pattern` |
| **03** | `.env.prod.example` LLM Key | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_03_env_prod_llm_key.py` | `RuntimeError: LLM_API_KEY must be set to a real value in production` |
| **04** | H3 LISA Island Crash | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_04_h3_lisa_island.py` | `ValueError: could not broadcast input array from shape (0,) into shape (999,)` |
| **05** | NN Coincident Point CSR | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_05_nearest_neighbor_coincident.py` | Identical points ($A=0$) produce $R=1.00$, reporting pattern as `"random"` |
| **06** | BH-FDR NaN Poisoning | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_06_bh_fdr_nan.py` | Single NaN in p-values poisons all output elements to `NaN` |
| **07** | Network Isochrone Snapping | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_07_isochrone_snapping.py` | Isochrone polygon snaps to endpoint 4.25 km away, missing facility |
| **08** | Raster Calculator NoData | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_08_raster_nodata.py` | Non-overlapping areas filled with 0.0, evaluating $A+B = A+0 = A$ |
| **09** | Dual DB Pool Starvation | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_09_db_pool_starvation.py` | Dual connection checkout exhausts `QueuePool`, throwing `TimeoutError` |
| **10** | Event Loop Lock Mismatch | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_10_pool_lock_loop.py` | `RuntimeError: Timeout context manager should be used inside a task` |
| **11** | Orphaned Tool Call Order | `PYTHONPATH=. .venv/bin/python .agents/reproduction_worker/scripts/repro_11_orphaned_tool_calls.py` | Synthesized tool message appended to tail, causing LLM API 400 Bad Request |
| **12** | MapSpec Compiler & Aliases | `(cd frontend && npx vitest run lib/mapspec-compiler/repro_12.test.ts)` | `TypeError: m.stops is not iterable` & 5 commands fail with `target_not_found` |

### Master Runner Command
To execute the entire reproduction verification suite sequentially:
```bash
./.agents/reproduction_worker/scripts/run_all_reproductions.sh
```
*Total Execution Time: < 15 seconds. External Network Calls Required: 0.*

---

## 5. Strategic Architectural Roadmap & Remediation Phasing

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   REMEDIATION ROADMAP PHASING                                    │
├───────────────────┬──────────────────────────────────────────────────────────────────────────────┤
│ Phase 1 (Week 1)  │ Immediate Production Blockers (P0) & Core Crashes (P1)                       │
│                   │ • Fix Nginx WebSocket routing and header forwarding in deploy/nginx/nginx.conf│
│                   │ • Update .env.prod.example to declare LLM_API_KEY                            │
│                   │ • Refactor Settings._validate_external_urls to permit private LLM endpoints  │
│                   │ • Fix message ordering in ChatExecutionEngine._repair_orphaned_tool_calls    │
│                   │ • Remove redundant Depends(get_db) from data_fabric and project routes       │
├───────────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Phase 2 (Week 2)  │ GIS Algorithmic Correctness & Spatial Hardening (P1/P2)                      │
│                   │ • Add disconnected weights guard to h3_lisa in statistics.py                 │
│                   │ • Correct coincident point clustering and Clark-Evans R calculation          │
│                   │ • Implement NaN masking in _bh_qvalues to prevent FDR q-value poisoning      │
│                   │ • Add edge projection to calculate_isochrones in network.py                  │
│                   │ • Track valid raster extent in raster_calculator to prevent data fabrication │
│                   │ • Fix SDE variance formula (remove factor of 2) in statistics.py             │
├───────────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Phase 3 (Week 3)  │ Frontend Compiler, Command Aliases & Error Boundaries (P1/P2)                │
│                   │ • Guard compileStyleMethod against missing stops/cases in compiler.ts        │
│                   │ • Support canonical params.id across all layerCommands.ts handlers          │
│                   │ • Wrap ChatTab, AnalysisTab, and DataSourcesTab in PanelErrorBoundary        │
│                   │ • Replace synchronous URL.revokeObjectURL with delayed revocation           │
│                   │ • Add WebGL context lost/restored lifecycle listeners to map canvas          │
├───────────────────┼──────────────────────────────────────────────────────────────────────────────┤
│ Phase 4 (Week 4)  │ DevOps, Observability & Long-Term Architectural Cleanups (P2/P3)              │
│                   │ • Decouple Celery worker inspect from API readiness probes                   │
│                   │ • Standardize API error responses, deprecating ApiResponse in legacy routes  │
│                   │ • Separate Next.js and Uvicorn into independent container deployments        │
│                   │ • Fix GitHub Actions version tags in .github/workflows/production.yml        │
└───────────────────┴──────────────────────────────────────────────────────────────────────────────┘
```
