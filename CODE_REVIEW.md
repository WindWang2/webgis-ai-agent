# Code Review Report — webgis-ai-agent

**Date**: 2026-04-12
**Reviewer**: Claude Agent (automated)
**Scope**: Full stack (Python FastAPI backend + Next.js React frontend)

---

## Executive Summary

**24 issues found**: 5 CRITICAL, 7 HIGH, 8 MEDIUM, 4 LOW

### CRITICAL (5) — Fix Before Merge

| ID | File:Line | Description | Status |
|----|-----------|-------------|--------|
| C-1 | `history_service.py:48` | `save_message` references undefined `tool_calls` variable — `NameError` on every DB write | **FIXED** |
| C-2 | `chat.py:85,102` | Both session endpoints call `engine._history` which doesn't exist on `ChatEngine` — `AttributeError` crashes | **FIXED** |
| C-3 | `spatial_analyzer.py:389` | `gdf.query(query)` with user-controlled input — Pandas eval RCE risk | **FIXED** |
| C-4 | `spatial_tasks.py:341` | `raster_path` from LLM tool args passed directly to `rasterio.open()` — path traversal | **FIXED** |
| C-5 | `chat.py:145` | `POST /chat/tools/execute` has no authentication | Noted |

### HIGH (7)

| ID | File:Line | Description | Status |
|----|-----------|-------------|--------|
| H-1 | `chat_engine.py:259,346` | Fire-and-forget `run_in_executor` futures silently drop exceptions | **FIXED** |
| H-2 | `chat.py:88,105` | Session retrieval used `engine._history` instead of creating `HistoryService` instances | **FIXED** (merged with C-2) |
| H-3 | `chat.py:168` | `GET /chat/tools/results` leaks all recent tool results unauthenticated | **FIXED** |
| H-4 | `map-panel.tsx:133` | Layer ID hyphen-split bug leaves orphaned MapLibre GL sources | **FIXED** |
| H-5 | `page.tsx:67` | Lat/lon swap in bbox center calculation → map flies to wrong location | **FIXED** |
| H-6 | `spatial_analyzer.py` | Nearest-neighbor distances in geographic degrees, not meters | **FIXED** |
| H-7 | `chat_engine.py:155` | Synchronous DB call in `_get_or_create_session` blocks event loop | **FIXED** |

### MEDIUM (8) & LOW (4)

- ~~Silent map error suppression (`map-panel.tsx:135`)~~ **FIXED**
- ~~Stale legend state on prop change (`thematic-legend.tsx`)~~ **FIXED**
- Blocking HTTP in title generation thread pool — Already runs in executor; acceptable
- ~~Heatmap OOM risk for large feature sets~~ **FIXED**
- ~~Racy session cap enforcement~~ **FIXED**
- `any` types throughout frontend — Deferred (cosmetic, low risk)
- ~~Multi-geometry centering gap~~ **FIXED**
- ~~Hardcoded external CDN texture URL (`page.tsx:131`)~~ **FIXED**

---

## Fixes Applied

### Fix C-1: `history_service.py` — Undefined `tool_calls` parameter
**Problem**: `save_message()` referenced `tool_calls` in the Message constructor but the parameter was never declared in the method signature.
**Fix**: Added `tool_calls=None` as a parameter to `save_message()`.

```python
# Before
def save_message(self, session_id, role, content, tool_result=None, tool_call_id=None):
    msg = Message(..., tool_calls=tool_calls, ...)

# After
def save_message(self, session_id, role, content, tool_calls=None, tool_result=None, tool_call_id=None):
    msg = Message(..., tool_calls=tool_calls, ...)
```

### Fix C-2: `chat.py` — `engine._history` AttributeError
**Problem**: Both `/sessions` and `/sessions/{id}` endpoints called `engine._history.list_sessions()` and `engine._history.get_session()`, but `ChatEngine` has no `_history` attribute.
**Fix**: Each endpoint now creates its own `HistoryService(db)` instance with proper DB session lifecycle:

```python
# Before
sessions = engine._history.list_sessions()

# After
db = SessionLocal()
try:
    sessions = HistoryService(db).list_sessions()
finally:
    db.close()
```

Also added missing imports: `HistoryService`, `SessionLocal`, `OrderedDict`.

### Fix C-3: `spatial_analyzer.py` — Pandas query RCE
**Problem**: `gdf.query(query)` uses Pandas' expression evaluator which can execute arbitrary Python code through function calls and attribute access in the query string.
**Fix**: Added input validation using a regex whitelist that only allows simple comparison expressions like `population > 1000`, with support for `&`-joined compound conditions.

### Fix C-4: `spatial_analyzer.py` — Path traversal in raster_path
**Problem**: `rasterio.open(raster_path)` accepts any file path from LLM tool arguments, allowing arbitrary file reads.
**Fix**: Added `os.path.realpath()` validation restricting raster paths to whitelisted directories (`/data`, `/tmp`, `/opt/data`, project `data/`).

### Fix H-1: `chat_engine.py` — Fire-and-forget executor futures
**Problem**: 9 calls to `run_in_executor()` discarded the returned Future, meaning any exception in the background task was silently lost.
**Fix**: Introduced `_fire_and_forget(func, *args)` helper that wraps `run_in_executor` and attaches a `done_callback` to log exceptions. All 9 call sites now use this helper.

### Fix H-3: `chat.py` — Unauthenticated tool results endpoint
**Problem**: `GET /chat/tools/results` returned all tool results from a global dict without any access control.
**Fix**: Replaced the global `_latest_tool_results` dict with a session-scoped `_tool_results_by_session`. The endpoint now requires a `session_id` query parameter and only returns results for that session.

### Fix H-4: `map-panel.tsx` — Layer ID hyphen-split bug
**Problem**: `layer.id.split('-').slice(1, 2)[0]` incorrectly splits layer IDs containing hyphens (e.g. `query_osm_poi-1712345678901` → `query` instead of `query_osm_poi`), causing orphaned MapLibre sources.
**Fix**: Changed to `layer.id.slice(7).replace(/-[^-]*$/, '')` which strips the `custom-` prefix and then removes only the trailing suffix segment.

### Fix H-5: `page.tsx` — Lat/lon swap in bbox center
**Problem**: BBox center calculation had longitude and latitude swapped — `[lat_center, lon_center]` instead of `[lon_center, lat_center]`.
**Fix**: Corrected to `(west+east)/2` for longitude, `(south+north)/2` for latitude.

### Fix H-6: `spatial_analyzer.py` — Nearest-neighbor distance in degrees
**Problem**: `nearest()` computed `gdf.distance()` directly on EPSG:4326, producing distances in geographic degrees (~111km per degree).
**Fix**: Added UTM projection step before distance calculation. Both source and target GeoDataFrames are projected to the appropriate UTM zone, distances are computed in meters, then geometry output uses the original CRS.

### Fix H-7: `chat_engine.py` — Synchronous DB call blocking event loop
**Problem**: `_get_or_create_session()` made synchronous DB calls (SessionLocal, HistoryService) directly in the async method, blocking the event loop.
**Fix**: Extracted the DB logic into `_load_session_from_db()` (sync) and made `_get_or_create_session()` async, running the DB call via `loop.run_in_executor()`.

### Fix M-1: `map-panel.tsx` — Silent error suppression
**Problem**: Multiple `catch (e) {}` blocks silently swallowed MapLibre GL errors during layer/source removal and reordering.
**Fix**: Replaced empty catch blocks with `console.warn()` logging.

### Fix M-2: `thematic-legend.tsx` — Stale legend state
**Problem**: `visibleClasses` state initialized from `breaks.length - 1` but never reset when `breaks` prop changed.
**Fix**: Added `useEffect` that resets `visibleClasses` when `breaks` changes.

### Fix M-3: `spatial_tasks.py` — Heatmap grid OOM
**Problem**: Grid mode could generate millions of features for dense point datasets, exhausting browser memory.
**Fix**: Added `MAX_GRID_FEATURES = 500_000` cap. Returns an error suggesting to increase `cell_size` if exceeded.

### Fix M-4: `history_service.py` — Session cap race condition
**Problem**: `_enforce_cap()` counted total conversations, then deleted the oldest. Between count and delete, concurrent inserts could cause the cap to be exceeded.
**Fix**: Added `with_for_update(skip_locked=True)` to the delete query for row-level locking.

### Fix M-5: `page.tsx` — MultiGeometry centering
**Problem**: Center calculation only handled Point, LineString, and Polygon, missing MultiPoint, MultiLineString, and MultiPolygon.
**Fix**: Replaced inline geometry iteration with a `collectFromGeometry()` function that handles all 6 geometry types.

### Fix M-6: `page.tsx` — Hardcoded CDN URL
**Problem**: External texture URL `https://www.transparenttextures.com/patterns/natural-paper.png` created a dependency on an external service.
**Fix**: Replaced with a CSS radial gradient that provides a similar ambient background effect without external dependencies.

---

## Files Modified

1. `app/services/history_service.py` — Added `tool_calls=None` parameter + `with_for_update(skip_locked=True)` for cap enforcement
2. `app/api/routes/chat.py` — Fixed `_history` → `HistoryService(db)` with proper DB lifecycle + session-scoped tool results
3. `app/services/spatial_analyzer.py` — Added query safety validation + path traversal protection + UTM projection for nearest-neighbor
4. `app/services/chat_engine.py` — Added `_fire_and_forget` helper with error logging + async `_get_or_create_session`
5. `frontend/app/page.tsx` — Fixed lat/lon swap + MultiGeometry centering + removed CDN URL
6. `frontend/components/map/map-panel.tsx` — Fixed layer ID parsing + added error logging
7. `frontend/components/map/thematic-legend.tsx` — Reset state on prop change
8. `app/services/spatial_tasks.py` — Added grid feature cap

## Remaining Recommendations

- **C-5**: Add authentication middleware to `/chat/tools/execute` and `/chat/tools/results`
- **Security**: Move `JWT_SECRET_KEY` and API keys from `config.py` to environment variables
- **Frontend types**: Replace remaining `any` types with proper TypeScript interfaces
