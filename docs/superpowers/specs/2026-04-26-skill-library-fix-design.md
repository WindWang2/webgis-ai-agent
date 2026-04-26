# Skill Library Full-Stack Fix Design

**Date**: 2026-04-26
**Status**: Approved
**Scope**: Fix broken tools, fill frontend gaps, solidify the "Agent is Everything" foundation before building multi-agent orchestration.

## Problem

34% of backend tools (10/29) are broken due to missing Celery dependency. Frontend has multiple functional gaps: missing SSE event handlers, hardcoded URLs that break non-local deployment, a missing sessionId prop that prevents asset loading, and a stub layer editor. The skill library is not solid enough to build higher-level agent features on top of.

## Approach

Full-stack fix with no new infrastructure dependencies. Use sync fallback for broken Celery-dependent tools, fix all frontend gaps, and harden the skill creator.

---

## P0: Backend — Sync Fallback for 10 Broken Spatial Tools

**Files**: `app/tools/registry.py`, `app/tools/spatial.py`, `app/tools/advanced_spatial.py`

**Current state**: `spatial.py` (4 tools) and `advanced_spatial.py` (6 tools) call Celery tasks (`run_buffer_analysis.apply_async()`, etc.). Celery is not installed, so these 10 tools fail with ImportError.

**Fix**: Add a `safe_execute()` function in `registry.py` that detects Celery availability. When Celery is unavailable, route calls to the existing synchronous implementations in `app/services/spatial_analyzer.py`. The tool definitions themselves don't change — only the dispatch path in the registry.

```python
# registry.py — dispatch logic
try:
    from celery import Celery
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False

def safe_execute(task_func, *args, **kwargs):
    if CELERY_AVAILABLE:
        return task_func.apply_async(args=args, kwargs=kwargs).get()
    else:
        # Fall back to synchronous execution
        return task_func(*args, **kwargs)
```

`spatial.py` and `advanced_spatial.py` import and call `safe_execute()` instead of `.apply_async()` directly.

## P0: Frontend — Hardcoded URL Cleanup

**Files**: `page.tsx`, `useHudStore.ts`, `results-panel.tsx`, `settings-panel.tsx`, `chat.ts`, `layer.ts`

21 occurrences of `http://localhost:8001` hardcoded across these frontend files. `NEXT_PUBLIC_API_URL` is already defined in `chat.ts` but unused elsewhere.

**Fix**: Create a centralized `lib/api/config.ts` module:

```typescript
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8001';
```

Replace all hardcoded URLs with imports from this module.

## P1: Frontend — SSE Event Handlers

**Files**: `frontend/app/page.tsx`, `frontend/lib/store/useHudStore.ts`

**Current state**: SSE stream parser handles `message`, `error`, `tool_result`, `tool_error`. Missing handlers for `tool_call`, `task_plan`, `task_cancelled`.

**Fix**: Add handlers in the SSE parsing loop:

- `tool_call` → Dispatch to store: show agent badge "Running {tool_name}..."
- `task_plan` → Update TaskTimeline with step list from payload
- `task_cancelled` → Clear task state in store

## P1: Frontend — Missing sessionId Prop

**File**: `frontend/app/page.tsx`

**Current state**: `DataHud` component expects `sessionId` prop to fetch analysis assets, but `page.tsx` doesn't pass it.

**Fix**: One-line change — pass `sessionId` from existing session state:

```tsx
<DataHud layers={layers} sessionId={sessionId} ... />
```

## P1: Frontend — WebSocket Reconnection

**File**: `frontend/lib/hooks/use-websocket.ts`

**Current state**: No error handling, no reconnection logic, no heartbeat. Connection silently drops.

**Fix**: Add exponential backoff reconnection (1s → 2s → 4s → max 30s). Add a connection state indicator in HUD (subtle green/red dot on panel border).

## P2: Frontend — Layer Edit Panel

**Files**: `frontend/components/map/MapPanel.tsx`, new `frontend/components/map/LayerEditPanel.tsx`

**Current state**: `onEditLayer={() => {}}` — button exists but does nothing.

**Fix**: Create `LayerEditPanel` component with controls for name, color, opacity, visibility. Wire `onEditLayer` to open the panel with the selected layer's current state. Call existing `/layers/data/{ref_id}` API for persistence.

## P2: Backend — Skill Creator AST Validation

**File**: `app/tools/skills.py`

**Current state**: Dynamic skill loading works but skill creator has no code validation. AI-generated Python code runs without safety checks.

**Fix**: Add AST-based validation before skill deployment:

- Parse the skill code with `ast.parse()`
- Block dangerous imports: `os.system`, `subprocess`, `eval`, `exec`, `__import__`
- Block file access patterns outside `app/skills/` directory
- Reject code that fails to parse

## P2: Backend — watch_skills() File Watcher

**File**: `app/tools/skills.py`

**Current state**: `watch_skills()` is a stub with a TODO comment.

**Fix**: Implement a simple file watcher for `app/skills/` directory using `pathlib` polling (check mtime every 5 seconds). When a new or modified `.py` file is detected, reload it into the tool registry. Keep it simple — no watchdog dependency.

## P3: Frontend — console.log Cleanup

**Files**: ~10 frontend files

Remove 30+ `console.log` development remnants across components.

---

## What Doesn't Change

- No database schema changes
- No new infrastructure (Redis, Celery, new services)
- No API contract changes — existing endpoints keep their signatures
- No new dependencies — all fixes use existing libraries

## Success Criteria

- All 29 backend tools return valid results (currently 19/29)
- Frontend loads assets correctly in the DataHud panel
- SSE events `tool_call`, `task_plan`, `task_cancelled` render in the UI
- App works when deployed to non-localhost URL
- WebSocket reconnects automatically after disconnect
- AI-created skills are validated before execution
