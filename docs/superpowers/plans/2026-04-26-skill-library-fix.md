# Skill Library Full-Stack Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all broken tools and frontend gaps to get the WebGIS AI Agent to a solid, deployable baseline.

**Architecture:** Backend: each Celery-dependent tool function gets a try/except ImportError branch — if `spatial_tasks` can't import (no Celery), fall back to calling `SpatialAnalyzer` methods directly. Frontend: centralized API config, missing SSE handlers, sessionId prop, WebSocket reconnection, console.log cleanup.

**Tech Stack:** Python 3.x (FastAPI, Pydantic, shapely, geopandas), TypeScript (Next.js 14, React 18, Zustand, MapLibre GL)

---

## File Structure

### Backend
- **Modify** `app/tools/spatial.py` — Add ImportError fallback to SpatialAnalyzer for all 4 tools
- **Modify** `app/tools/advanced_spatial.py` — Add ImportError fallback to SpatialAnalyzer for all 6 tools
- **Modify** `app/tools/skills.py` — Add AST validation to `create_new_skill()`, implement `watch_skills()` file watcher

### Frontend
- **Create** `frontend/lib/api/config.ts` — Centralized API base URL + WS URL
- **Modify** `frontend/app/page.tsx` — Replace 8 hardcoded URLs, add sessionId prop, add SSE handlers
- **Modify** `frontend/app/story/page.tsx` — Replace 1 hardcoded URL
- **Modify** `frontend/components/map/map-action-handler.tsx` — Replace 2 hardcoded URLs
- **Modify** `frontend/components/panel/results-panel.tsx` — Replace 2 hardcoded URLs
- **Modify** `frontend/components/panel/asset-card.tsx` — Replace 1 hardcoded URL
- **Modify** `frontend/components/hud/settings-panel.tsx` — Replace 5 hardcoded URLs
- **Modify** `frontend/lib/store/useHudStore.ts` — Replace 2 hardcoded URLs
- **Modify** `frontend/lib/hooks/use-websocket.ts` — Import config, add reconnection with exponential backoff
- **Modify** `frontend/lib/contexts/task-context.tsx` — Remove 7 console.log
- **Modify** `frontend/lib/contexts/map-action-context.tsx` — Remove 2 console.log
- **Modify** `frontend/components/map/map-action-handler.tsx` — Remove 2 console.log
- **Modify** `frontend/app/page.tsx` — Remove 2 console.log
- **Modify** `frontend/lib/hooks/use-websocket.ts` — Remove 2 console.log

---

### Task 1: Fix spatial.py — Add Celery fallback (4 tools)

**Files:**
- Modify: `app/tools/spatial.py`

`spatial_tasks.py` imports `celery` at module level, so `from app.services.spatial_tasks import run_buffer_analysis` fails when Celery isn't installed. Each tool function needs a try/except ImportError that falls back to calling `SpatialAnalyzer` directly. `SpatialAnalyzer` methods return `AnalysisResult(success, data, error_message, stats)`.

- [ ] **Step 1: Add SpatialAnalyzer import and refactor buffer_analysis**

Add at top of file after line 7:
```python
from app.services.spatial_analyzer import SpatialAnalyzer
```

Replace the body of `buffer_analysis` (lines 61-76) with:
```python
    def buffer_analysis(geojson: Any, distance: float, unit: str = "m") -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", data) if isinstance(data, dict) else data

            try:
                from app.services.spatial_tasks import run_buffer_analysis
                task = run_buffer_analysis.apply_async(args=[features, distance, unit])
                result = task.get(timeout=120)
            except ImportError:
                r = SpatialAnalyzer.buffer(features, distance=distance, unit=unit)
                result = {"success": r.success, "data": r.data, "stats": r.stats}
                if not r.success:
                    result["error"] = r.error_message

            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Buffer analysis error: {e}")
            return {"error": str(e)}
```

- [ ] **Step 2: Refactor spatial_stats**

Replace the body of `spatial_stats` (lines 80-96) with:
```python
    def spatial_stats(geojson: Any) -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])

            try:
                from app.services.spatial_tasks import run_spatial_stats
                task = run_spatial_stats.apply_async(args=[features])
                result = task.get(timeout=60)
            except ImportError:
                r = SpatialAnalyzer.statistics(features, spatial_stats=True)
                result = {"success": r.success, "stats": r.stats}
                if not r.success:
                    result["error"] = r.error_message

            if result.get("success"):
                return {"stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"Spatial stats error: {e}")
            return {"error": str(e)}
```

- [ ] **Step 3: Refactor nearest_neighbor**

Replace the body of `nearest_neighbor` (lines 100-116) with:
```python
    def nearest_neighbor(geojson: Any) -> dict:
        try:
            data = _safe_parse_geojson(geojson)
            if not data:
                return {"error": "Invalid GeoJSON input"}
            features = data.get("features", [])

            try:
                from app.services.spatial_tasks import run_nearest_neighbor
                task = run_nearest_neighbor.apply_async(args=[features])
                result = task.get(timeout=60)
            except ImportError:
                r = SpatialAnalyzer.nearest(features)
                if r.success:
                    result = {"success": True, "data": r.data}
                else:
                    result = {"success": False, "error": r.error_message}

            if result.get("success"):
                return result.get("data")
            return {"error": result.get("error")}
        except Exception as e:
            logger.error(f"NN analysis error: {e}")
            return {"error": str(e)}
```

- [ ] **Step 4: Refactor heatmap_data**

Replace the Celery-dependent branch (lines 139-153) inside heatmap_data. Keep the native render branch unchanged (lines 129-137). The change is:

Replace lines 139-153:
```python
            from app.services.spatial_tasks import run_heatmap_generation
            task = run_heatmap_generation.apply_async(
                kwargs={"features": features, "cell_size": cell_size, "radius": radius, "render_type": render_type, "palette": palette}
            )
            result = task.get(timeout=120)
```

With:
```python
            try:
                from app.services.spatial_tasks import run_heatmap_generation
                task = run_heatmap_generation.apply_async(
                    kwargs={"features": features, "cell_size": cell_size, "radius": radius, "render_type": render_type, "palette": palette}
                )
                result = task.get(timeout=120)
            except ImportError:
                return {"error": "Heatmap generation requires Celery. Use render_type='native' for client-side rendering."}
```

Note: Heatmap generation has complex matplotlib logic with no direct `SpatialAnalyzer` equivalent, so we return an error suggesting the native mode instead. The native mode (lines 129-137) works without any backend processing.

- [ ] **Step 5: Verify imports work**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.spatial import register_spatial_tools; print('OK')"`

Expected: `OK` (no ImportError)

- [ ] **Step 6: Commit**

```bash
git add app/tools/spatial.py
git commit -m "fix: add Celery ImportError fallback for 4 spatial tools (buffer, stats, nearest, heatmap)"
```

---

### Task 2: Fix advanced_spatial.py — Add Celery fallback (6 tools)

**Files:**
- Modify: `app/tools/advanced_spatial.py`

Same pattern as Task 1. Each tool wraps the `spatial_tasks` import in try/except ImportError and falls back to `SpatialAnalyzer`.

- [ ] **Step 1: Add SpatialAnalyzer import**

Add after line 6:
```python
from app.services.spatial_analyzer import SpatialAnalyzer
```

- [ ] **Step 2: Refactor path_analysis**

Replace the body of `path_analysis` (lines 41-53) with:
```python
    def path_analysis(network_features: Any, start_point: List[float], end_point: List[float]) -> dict:
        try:
            features = network_features.get("features", network_features) if isinstance(network_features, dict) else network_features

            try:
                from app.services.spatial_tasks import run_path_analysis
                task = run_path_analysis.apply_async(args=[features, start_point, end_point])
                result = task.get(timeout=120)
            except ImportError:
                r = SpatialAnalyzer.path_analysis(features, start_point=start_point, end_point=end_point)
                result = {"success": r.success, "data": r.data, "stats": r.stats}
                if not r.success:
                    result["error"] = r.error_message

            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}
```

- [ ] **Step 3: Refactor zonal_stats**

Replace the body of `zonal_stats` (lines 58-71) with:
```python
    def zonal_stats(geojson: Any, raster_path: str) -> dict:
        try:
            features = geojson.get("features", geojson) if isinstance(geojson, dict) else geojson

            try:
                from app.services.spatial_tasks import run_zonal_stats
                task = run_zonal_stats.apply_async(args=[features, raster_path])
                result = task.get(timeout=120)
            except ImportError:
                r = SpatialAnalyzer.zonal_statistics(features, raster_path=raster_path)
                if r.success:
                    result = {"success": True, "data": r.data}
                else:
                    result = {"success": False, "error": r.error_message}

            if result.get("success"):
                return {"zonal_stats": result.get("data", {}).get("zonal_stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}
```

- [ ] **Step 4: Refactor overlay_analysis**

Replace the body of `overlay_analysis` (lines 76-91) with:
```python
    def overlay_analysis(layer_a: Any, layer_b: Any, how: str = "intersection") -> dict:
        try:
            features_a = layer_a.get("features", layer_a) if isinstance(layer_a, dict) else layer_a
            features_b = layer_b.get("features", layer_b) if isinstance(layer_b, dict) else layer_b

            try:
                from app.services.spatial_tasks import run_overlay_analysis
                task = run_overlay_analysis.apply_async(args=[features_a, features_b, how])
                result = task.get(timeout=120)
            except ImportError:
                r = SpatialAnalyzer.overlay(features_a, features_b, how=how)
                result = {"success": r.success, "data": r.data, "stats": r.stats}
                if not r.success:
                    result["error"] = r.error_message

            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}
```

- [ ] **Step 5: Refactor attribute_filter**

Replace the body of `attribute_filter` (lines 96-109) with:
```python
    def attribute_filter(geojson: Any, query: str) -> dict:
        try:
            features = geojson.get("features", geojson) if isinstance(geojson, dict) else geojson

            try:
                from app.services.spatial_tasks import run_attribute_filter
                task = run_attribute_filter.apply_async(args=[features, query])
                result = task.get(timeout=60)
            except ImportError:
                r = SpatialAnalyzer.attribute_filter(features, query=query)
                result = {"success": r.success, "data": r.data, "stats": r.stats}
                if not r.success:
                    result["error"] = r.error_message

            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}
```

- [ ] **Step 6: Refactor spatial_join**

Replace the body of `spatial_join` (lines 114-128) with:
```python
    def spatial_join(left_layer: Any, right_layer: Any, join_type: str = "inner", predicate: str = "intersects") -> dict:
        try:
            features_left = left_layer.get("features", left_layer) if isinstance(left_layer, dict) else left_layer
            features_right = right_layer.get("features", right_layer) if isinstance(right_layer, dict) else right_layer

            try:
                from app.services.spatial_tasks import run_spatial_join
                task = run_spatial_join.apply_async(args=[features_left, features_right, join_type, predicate])
                result = task.get(timeout=120)
            except ImportError:
                r = SpatialAnalyzer.spatial_join(features_left, features_right, join_type=join_type, predicate=predicate)
                result = {"success": r.success, "data": r.data, "stats": r.stats}
                if not r.success:
                    result["error"] = r.error_message

            if result.get("success"):
                return {"geojson": result.get("data"), "stats": result.get("stats")}
            return {"error": result.get("error")}
        except Exception as e:
            return {"error": str(e)}
```

- [ ] **Step 7: Verify imports work**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.advanced_spatial import register_advanced_spatial_tools; print('OK')"`

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add app/tools/advanced_spatial.py
git commit -m "fix: add Celery ImportError fallback for 6 advanced spatial tools (path, zonal, overlay, filter, join)"
```

---

### Task 3: Create centralized API config

**Files:**
- Create: `frontend/lib/api/config.ts`

- [ ] **Step 1: Create the config module**

```typescript
/**
 * Centralized API configuration
 * All API and WebSocket URLs should import from this module.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001';
export const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001';
```

- [ ] **Step 2: Update chat.ts to use config**

In `frontend/lib/api/chat.ts`, add import at top (after line 4):
```typescript
import { API_BASE } from './config';
```

Remove line 7 (`const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";`).

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/api/config.ts frontend/lib/api/chat.ts
git commit -m "feat: create centralized API config module"
```

---

### Task 4: Replace all hardcoded URLs in frontend

**Files:**
- Modify: `frontend/app/page.tsx` (8 URLs)
- Modify: `frontend/app/story/page.tsx` (1 URL)
- Modify: `frontend/components/map/map-action-handler.tsx` (2 URLs)
- Modify: `frontend/components/panel/results-panel.tsx` (2 URLs)
- Modify: `frontend/components/panel/asset-card.tsx` (1 URL)
- Modify: `frontend/components/hud/settings-panel.tsx` (5 URLs)
- Modify: `frontend/lib/store/useHudStore.ts` (2 URLs)
- Modify: `frontend/lib/hooks/use-websocket.ts` (1 URL)

- [ ] **Step 1: page.tsx — Add import and replace all 8 URLs**

Add import near top of file (after other imports, around line 17):
```typescript
import { API_BASE } from '@/lib/api/config';
```

Replace all 8 occurrences of `http://localhost:8001` with the value of `API_BASE`:
- Line 22: `fetch(\`http://localhost:8001/api/v1/chat/sessions/${sessionId}\`)` → `fetch(\`${API_BASE}/api/v1/chat/sessions/${sessionId}\`)`
- Line 73: `fetch("http://localhost:8001/api/v1/chat/sessions")` → `fetch(\`${API_BASE}/api/v1/chat/sessions\`)`
- Line 85: `fetch("http://localhost:8001/api/v1/chat/sessions")` → `fetch(\`${API_BASE}/api/v1/chat/sessions\`)`
- Line 97: `fetch(\`http://localhost:8001/api/v1/chat/sessions/${sid}\`)` → `fetch(\`${API_BASE}/api/v1/chat/sessions/${sid}\`)`
- Line 136: `fetch(\`http://localhost:8001/api/v1/chat/sessions/${sid}\`, { method: "DELETE" })` → `fetch(\`${API_BASE}/api/v1/chat/sessions/${sid}\`, { method: "DELETE" })`
- Line 152: `fetch(\`http://localhost:8001/api/v1/chat/sessions/${savedSessionId}\`)` → `fetch(\`${API_BASE}/api/v1/chat/sessions/${savedSessionId}\`)`
- Line 188: `fetch(\`http://localhost:8001/api/v1/layers/data/${result.geojson_ref}?session_id=${sid}\`)` → `fetch(\`${API_BASE}/api/v1/layers/data/${result.geojson_ref}?session_id=${sid}\`)`
- Line 331: `fetch("http://localhost:8001/api/v1/chat/completions",` → `fetch(\`${API_BASE}/api/v1/chat/completions\`,`

- [ ] **Step 2: story/page.tsx — Replace 1 URL**

Add import: `import { API_BASE } from '@/lib/api/config';`
Replace line 22: `http://localhost:8001` → `${API_BASE}`

- [ ] **Step 3: map-action-handler.tsx — Replace 2 URLs**

Add import: `import { API_BASE } from '@/lib/api/config';`
- Line 300: `http://localhost:8001/api/v1/export` → `${API_BASE}/api/v1/export`
- Line 311: `http://localhost:8001${url}` → `${API_BASE}${url}`

- [ ] **Step 4: results-panel.tsx — Replace 2 URLs**

Add import: `import { API_BASE } from '@/lib/api/config';`
- Line 181: `http://localhost:8001/api/v1/chat/tools/call?tool=manage_analysis_asset&asset_id=${id}&action=delete` → `${API_BASE}/api/v1/chat/tools/call?tool=manage_analysis_asset&asset_id=${id}&action=delete`
- Line 185: `http://localhost:8001/api/v1/chat/tools/call?tool=manage_analysis_asset&asset_id=${id}&action=rename&new_name=${encodeURIComponent(newName)}` → `${API_BASE}/api/v1/chat/tools/call?tool=manage_analysis_asset&asset_id=${id}&action=rename&new_name=${encodeURIComponent(newName)}`

- [ ] **Step 5: asset-card.tsx — Replace 1 URL**

Add import: `import { API_BASE } from '@/lib/api/config';`
- Line 99: `http://localhost:8001/api/v1/layers/data/${asset.id}?download=true` → `${API_BASE}/api/v1/layers/data/${asset.id}?download=true`

- [ ] **Step 6: settings-panel.tsx — Replace 5 URLs**

Add import: `import { API_BASE } from '@/lib/api/config';`
- Line 34: `http://localhost:8001/api/v1/config/llm` → `${API_BASE}/api/v1/config/llm`
- Line 35: `http://localhost:8001/api/v1/config/mcp` → `${API_BASE}/api/v1/config/mcp`
- Line 36: `http://localhost:8001/api/v1/config/skills` → `${API_BASE}/api/v1/config/skills`
- Line 61: `http://localhost:8001/api/v1/config/llm` → `${API_BASE}/api/v1/config/llm`
- Line 78: `http://localhost:8001/api/v1/config/mcp` → `${API_BASE}/api/v1/config/mcp`

- [ ] **Step 7: useHudStore.ts — Replace 2 URLs**

Add import: `import { API_BASE } from '../api/config';`
- Line 229: `http://localhost:8001/api/v1/chat/tools/call?tool=list_analysis_assets&session_id=${sessionId || ''}` → `${API_BASE}/api/v1/chat/tools/call?tool=list_analysis_assets&session_id=${sessionId || ''}`
- Line 234: `http://localhost:8001/api/v1/uploads?session_id=${sessionId || ''}` → `${API_BASE}/api/v1/uploads?session_id=${sessionId || ''}`

- [ ] **Step 8: use-websocket.ts — Replace WS URL**

Add import: `import { WS_BASE } from '../api/config';`
Replace line 4: `const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8001/api/v1/ws';` → `const WS_URL = \`${WS_BASE}/api/v1/ws\`;`

- [ ] **Step 9: Verify build compiles**

Run: `cd /home/kevin/projects/webgis-ai-agent/frontend && npx next build 2>&1 | tail -20`

Expected: Build completes (may have warnings but no errors related to the changed files)

- [ ] **Step 10: Commit**

```bash
git add frontend/
git commit -m "fix: replace 21 hardcoded localhost URLs with centralized API config"
```

---

### Task 5: Fix missing sessionId prop for DataHud

**Files:**
- Modify: `frontend/app/page.tsx`

- [ ] **Step 1: Pass sessionId to DataHud**

In page.tsx, the `DataHud` component at line 654. Change:

```tsx
        <DataHud
          layers={layers}
          onToggleLayer={toggleLayer}
          onRemoveLayer={removeLayer}
          onUpdateLayer={updateLayer}
          onReorderLayers={reorderLayers}
        />
```

To:

```tsx
        <DataHud
          layers={layers}
          sessionId={sessionId}
          onToggleLayer={toggleLayer}
          onRemoveLayer={removeLayer}
          onUpdateLayer={updateLayer}
          onReorderLayers={reorderLayers}
        />
```

- [ ] **Step 2: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "fix: pass sessionId prop to DataHud so assets tab loads data"
```

---

### Task 6: Add missing SSE event handlers

**Files:**
- Modify: `frontend/app/page.tsx`

The SSE event loop in `handleSend` (around lines 396-478) handles `session`, `task_start`, `step_start`, `step_result`, `step_error`, `task_complete`, `message`/`content`/`token`, `done`/`end`, `task_error`/`tool_error`. Missing: `tool_call`, `task_plan`, `task_cancelled`.

- [ ] **Step 1: Add tool_call handler**

After the `eventType === "task_error" || eventType === "tool_error"` block (around line 470), add:

```typescript
          } else if (eventType === "tool_call" && data?.tool) {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? { ...msg, content: assistantContent + `\n\n> 🔧 **执行工具**: ${data.tool}...` }
                  : msg
              )
            )
```

- [ ] **Step 2: Add task_plan handler**

After the tool_call handler:

```typescript
          } else if (eventType === "task_plan" && data?.steps) {
            const planSteps = (data.steps as string[]).map((s, i) => `${i + 1}. ${s}`).join("\n")
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? { ...msg, content: assistantContent + `\n\n📋 **任务计划**:\n${planSteps}` }
                  : msg
              )
            )
```

- [ ] **Step 3: Add task_cancelled handler**

After the task_plan handler:

```typescript
          } else if (eventType === "task_cancelled") {
            clearTask()
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === thinkingMessage.id
                  ? { ...msg, content: assistantContent + "\n\n> ⏹ 任务已取消" }
                  : msg
              )
            )
```

- [ ] **Step 4: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat: add SSE handlers for tool_call, task_plan, and task_cancelled events"
```

---

### Task 7: Add WebSocket reconnection with exponential backoff

**Files:**
- Modify: `frontend/lib/hooks/use-websocket.ts`

- [ ] **Step 1: Rewrite use-websocket.ts with reconnection logic**

Replace the entire file with:

```typescript
import { useEffect, useRef, useCallback, useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { WS_BASE } from '@/lib/api/config';

const WS_URL = `${WS_BASE}/api/v1/ws`;

export function useWebSocket(sessionId?: string) {
  const socketRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [connected, setConnected] = useState(false);
  const { addProcessLayer, removeProcessLayer } = useHudStore();

  const connect = useCallback(() => {
    if (!sessionId) return;

    const url = `${WS_URL}/${sessionId}`;
    const socket = new WebSocket(url);
    socketRef.current = socket;

    socket.onopen = () => {
      retryCountRef.current = 0;
      setConnected(true);
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        const { event: eventType, data } = payload;

        if (eventType === 'STEP_COMPLETED' || eventType === 'geojson_update') {
          if (data.step_id && data.geojson) {
            addProcessLayer(data.step_id, data.geojson);
          }
        } else if (eventType === 'STEP_REMOVED') {
          if (data.step_id) {
            removeProcessLayer(data.step_id);
          }
        }
      } catch {}
    };

    socket.onclose = () => {
      setConnected(false);
      socketRef.current = null;
      // Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
      retryCountRef.current += 1;
      timerRef.current = setTimeout(connect, delay);
    };

    socket.onerror = () => {
      socket.close();
    };
  }, [sessionId, addProcessLayer, removeProcessLayer]);

  useEffect(() => {
    connect();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (socketRef.current) {
        socketRef.current.onclose = null;
        socketRef.current.close();
        socketRef.current = null;
      }
      setConnected(false);
    };
  }, [connect]);

  const sendMessage = useCallback((message: any) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(message));
    }
  }, []);

  return { sendMessage, connected };
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/lib/hooks/use-websocket.ts
git commit -m "feat: add WebSocket reconnection with exponential backoff and connection state"
```

---

### Task 8: Add AST validation to Skill Creator

**Files:**
- Modify: `app/tools/skills.py`

- [ ] **Step 1: Add AST validation function**

Add after the existing imports (line 6):

```python
import ast

_BLOCKED_IMPORTS = {"subprocess", "multiprocessing", "ctypes", "socket", "http", "urllib", "ftplib", "smtplib", "telnetlib", "xmlrpc"}
_BLOCKED_BUILTINS = {"eval", "exec", "compile", "__import__", "open", "input"}
_BLOCKED_ATTRS = {"system", "popen", "call", "run", "Popen"}


def _validate_skill_code(code: str) -> list[str]:
    """Validate skill code for dangerous patterns. Returns list of errors."""
    errors = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    for node in ast.walk(tree):
        # Block dangerous imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".")[0]
                if root_mod in _BLOCKED_IMPORTS:
                    errors.append(f"Blocked import: {alias.name}")

        if isinstance(node, ast.ImportFrom):
            if node.module:
                root_mod = node.module.split(".")[0]
                if root_mod in _BLOCKED_IMPORTS:
                    errors.append(f"Blocked import: {node.module}")

        # Block dangerous builtins
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _BLOCKED_BUILTINS:
                errors.append(f"Blocked builtin: {func.id}")
            if isinstance(func, ast.Attribute) and func.attr in _BLOCKED_ATTRS:
                errors.append(f"Blocked attribute: {func.attr}")

    return errors
```

- [ ] **Step 2: Integrate validation into create_new_skill**

In the `create_new_skill` function (line 24), add validation before `skill_creator.create_skill()`:

Replace:
```python
async def create_new_skill(module_name: str, code: str, description: str) -> str:
    """Agent 调用的创建技能函数"""
    from app.services.skill_creator import skill_creator
    from app.api.routes.chat import registry
    
    result = skill_creator.create_skill(module_name, code, description)
```

With:
```python
async def create_new_skill(module_name: str, code: str, description: str) -> str:
    """Agent 调用的创建技能函数"""
    errors = _validate_skill_code(code)
    if errors:
        return f"Skill validation failed:\n" + "\n".join(f"- {e}" for e in errors) + "\nPlease revise your code to remove dangerous patterns."

    from app.services.skill_creator import skill_creator
    from app.api.routes.chat import registry

    result = skill_creator.create_skill(module_name, code, description)
```

- [ ] **Step 3: Verify import works**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.skills import _validate_skill_code; print(_validate_skill_code('import os'));"`

Expected: `[]` (os is allowed)

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.skills import _validate_skill_code; print(_validate_skill_code('import subprocess'));"`

Expected: `['Blocked import: subprocess']`

- [ ] **Step 4: Commit**

```bash
git add app/tools/skills.py
git commit -m "feat: add AST-based code validation to Skill Creator, block dangerous imports and builtins"
```

---

### Task 9: Implement watch_skills() file watcher

**Files:**
- Modify: `app/tools/skills.py`

- [ ] **Step 1: Replace the stub watch_skills()**

Replace the current `watch_skills` function (lines 71-76):

```python
def watch_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """
    TODO: Implement a file watcher (e.g., using watchdog) to hot-reload skills.
    For now, we just load them once at startup.
    """
    load_skills(registry, skills_dir)
```

With:

```python
def watch_skills(registry: ToolRegistry, skills_dir: str = "app/skills"):
    """Poll-based file watcher for hot-reloading skills.

    Tracks file modification times. Call periodically (e.g., every 5s)
    to detect new or changed skill files and reload them.
    """
    _mtimes: dict[str, float] = {}

    def _check():
        if not os.path.exists(skills_dir):
            return
        changed = False
        for filename in os.listdir(skills_dir):
            if not filename.endswith(".py") or filename.startswith("__"):
                continue
            filepath = os.path.join(skills_dir, filename)
            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                continue
            if filepath not in _mtimes or _mtimes[filepath] < mtime:
                _mtimes[filepath] = mtime
                changed = True
        if changed:
            load_skills(registry, skills_dir)

    _check()
    return _check
```

- [ ] **Step 2: Verify import works**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.skills import watch_skills; print(type(watch_skills))"`

Expected: `<class 'function'>`

- [ ] **Step 3: Commit**

```bash
git add app/tools/skills.py
git commit -m "feat: implement poll-based watch_skills() file watcher for hot-reloading"
```

---

### Task 10: Remove console.log remnants

**Files:**
- Modify: `frontend/lib/contexts/task-context.tsx` (7 occurrences)
- Modify: `frontend/lib/contexts/map-action-context.tsx` (2 occurrences)
- Modify: `frontend/components/map/map-action-handler.tsx` (2 occurrences)
- Modify: `frontend/app/page.tsx` (2 occurrences)

- [ ] **Step 1: Remove console.log from task-context.tsx**

Remove these 7 lines:
- Line 41: `console.log('[Task] Starting task:', taskId);`
- Line 50: `console.log('[Task] Step started:', taskId, stepId, stepIndex, tool);`
- Line 68: `console.log('[Task] Step result:', taskId, stepId, tool);`
- Line 85: `console.log('[Task] Step error:', taskId, stepId, error);`
- Line 99: `console.log('[Task] Task completed:', taskId, stepCount, summary);`
- Line 114: `console.log('[Task] Task error:', taskId, error);`
- Line 126: `console.log('[Task] Task cancelled:', taskId);`
- Line 137: `console.log('[Task] Clearing task');`

- [ ] **Step 2: Remove console.log from map-action-context.tsx**

Remove these 2 lines:
- Line 38: `console.log('[MapAction] Throttled redundant command:', newAction.command);`
- Line 57: `console.log('[MapAction] Dispatched to queue:', newAction.command, newAction.params);`

- [ ] **Step 3: Remove console.log from map-action-handler.tsx**

Remove these 2 lines:
- Line 75: `console.log('[MapActionHandler] Processing action:', action.command, 'on map:', map.getContainer().id);`
- Line 177: `console.log('[MapActionHandler] Directly setting base layer to:', MAP_STYLES[idx].name);`

- [ ] **Step 4: Remove console.log from page.tsx**

Remove these 2 lines:
- Line 412: `console.log('[Home] Direct command dispatch from tool result:', result.command)`
- Line 423: `console.log('[Home] Raster result detected, adding to map...')`

- [ ] **Step 5: Commit**

```bash
git add frontend/
git commit -m "chore: remove 13 console.log development remnants from frontend"
```

---

## Self-Review Checklist

- [x] **Spec coverage**: P0 (sync fallback + URL cleanup) → Tasks 1-4. P1 (SSE + sessionId + WS) → Tasks 5-7. P2 (AST validation + file watcher) → Tasks 8-9. P3 (console.log) → Task 10.
- [x] **Placeholder scan**: No TBD/TODO/fill-in-later. All code blocks are complete.
- [x] **Type consistency**: `API_BASE` string used consistently. `AnalysisResult` fields (success, data, stats, error_message) matched correctly in fallback branches. `sessionId` prop type matches DataHud expectation.
- [x] **No orphaned references**: All imports and function names referenced in later tasks are defined in earlier tasks.
