# Fix Frontend Type Errors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix specific TypeScript errors and missing imports in the frontend to improve type safety and build stability.

**Architecture:** Surgical updates to `MapActionPayload` interface and `useWebSocket` hook.

**Tech Stack:** TypeScript, Next.js, Zustand.

---

### Task 1: Update MapActionPayload in `frontend/lib/types.ts`

**Files:**
- Modify: `frontend/lib/types.ts`

- [x] **Step 1: Update `command` union type and `params` interface**

```typescript
<<<<
  command: 'add_layer' | 'remove_layer' | 'fly_to' | 'add_heatmap_raster' | 'add_raster_layer' | 'export_map' | 'BASE_LAYER_CHANGE' | 'LAYER_VISIBILITY_UPDATE' | 'LAYER_STYLE_UPDATE' | 'REMOVE_LAYER';
  params: {
    id?: string;
    layerId?: string;
    layer_id?: string; // Support for snake_case from backend
    name?: string;     // For base layer change
    type?: 'fill' | 'line' | 'circle' | 'symbol';
    geojson?: GeoJSONFeatureCollection;
    style?: Record<string, unknown>;
    flyTo?: boolean;
====
  command: 'add_layer' | 'remove_layer' | 'fly_to' | 'add_heatmap_raster' | 'add_raster_layer' | 'add_native_heatmap' | 'APPLY_LAYER_FILTER' | 'export_map' | 'BASE_LAYER_CHANGE' | 'LAYER_VISIBILITY_UPDATE' | 'LAYER_STYLE_UPDATE' | 'REMOVE_LAYER';
  params: {
    id?: string;
    layerId?: string;
    layer_id?: string; // Support for snake_case from backend
    name?: string;     // For base layer change
    type?: 'fill' | 'line' | 'circle' | 'symbol';
    geojson?: GeoJSONFeatureCollection;
    filter?: any;      // Filter for APPLY_LAYER_FILTER
    palette?: string;  // Palette for add_native_heatmap
    radius?: number;   // Radius for add_native_heatmap
    style?: Record<string, unknown>;
    flyTo?: boolean;
>>>>
```

### Task 2: Fix missing import in `frontend/lib/hooks/use-websocket.ts`

**Files:**
- Modify: `frontend/lib/hooks/use-websocket.ts`

- [x] **Step 1: Add `API_BASE` to imports from `@/lib/api/config`**

```typescript
<<<<
import { useHudStore, type HudState } from '@/lib/store/useHudStore';
import { WS_BASE } from '@/lib/api/config';
====
import { useHudStore, type HudState } from '@/lib/store/useHudStore';
import { WS_BASE, API_BASE } from '@/lib/api/config';
>>>>
```

### Task 3: Verification

- [x] **Step 1: Run TypeScript compiler to verify fixes**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors related to `MapActionPayload` missing commands/params or `API_BASE` being undefined.
