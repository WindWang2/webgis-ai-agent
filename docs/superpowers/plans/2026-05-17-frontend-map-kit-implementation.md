# Frontend Map Toolkit (map-kit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor frontend map operations into a professional, decoupled, and testable toolkit.

**Architecture:** A set of pure functional modules under `frontend/lib/map-kit/` that extend MapLibre GL JS capabilities.

**Tech Stack:** TypeScript, MapLibre GL JS, HTML5 Canvas, Vitest.

---

### Task 1: Foundation & Navigation Module

**Files:**
- Create: `frontend/lib/map-kit/navigation.ts`
- Create: `frontend/lib/map-kit/types.ts`
- Test: `frontend/lib/map-kit/navigation.test.ts`

- [ ] **Step 1: Define shared types**
Create `types.ts` with standard interfaces for MapState, ViewportParams, and LayerOptions.
- [ ] **Step 2: Implement smooth navigation logic**
Implement `flyTo`, `fitBounds`, and `jumpTo` in `navigation.ts`. Add coordinate validation helpers.
- [ ] **Step 3: Write tests for navigation**
Verify that params are correctly mapped to MapLibre calls using mocks.
- [ ] **Step 4: Commit**
`git add frontend/lib/map-kit/ && git commit -m "feat(map-kit): implement foundation and navigation module"`

### Task 2: Advanced Renderer Module

**Files:**
- Create: `frontend/lib/map-kit/renderer.ts`
- Test: `frontend/lib/map-kit/renderer.test.ts`

- [ ] **Step 1: Implement robust layer management**
Implement `addGeoJsonSource` and `addVectorLayer` with built-in existence checks and auto-cleanup.
- [ ] **Step 2: Implement native heatmap support**
Move the professional vector heatmap logic from `MapActionHandler.tsx` into `renderer.ts`.
- [ ] **Step 3: Implement style updates**
Add `updateLayerStyle` to handle dynamic property changes (color, opacity).
- [ ] **Step 4: Write tests for renderer**
Verify layer and source creation/removal logic.
- [ ] **Step 5: Commit**
`git add frontend/lib/map-kit/renderer.ts && git commit -m "feat(map-kit): implement advanced renderer module"`

### Task 3: Professional Exporter Module

**Files:**
- Create: `frontend/lib/map-kit/exporter.ts`
- Test: `frontend/lib/map-kit/exporter.test.ts`

- [ ] **Step 1: Implement snapshot capture**
Extract the high-DPI canvas capture logic. Support `once('render')` wrapping.
- [ ] **Step 2: Implement professional layout engine**
Implement `composeLayout` to draw title, scale, and compass overlays using HTML5 Canvas.
- [ ] **Step 3: Implement multi-format download**
Add support for PNG and blob generation.
- [ ] **Step 4: Write tests for exporter**
Verify canvas drawing math and DPI scaling.
- [ ] **Step 5: Commit**
`git add frontend/lib/map-kit/exporter.ts && git commit -m "feat(map-kit): implement professional exporter module"`

### Task 4: UI Integration & Refactoring

**Files:**
- Modify: `frontend/components/map/map-action-handler.tsx`
- Modify: `frontend/components/map/map-panel.tsx`

- [ ] **Step 1: Refactor MapActionHandler**
Replace the 27kb of logic with thin wrappers that call the `map-kit`.
- [ ] **Step 2: Standardize state sync**
Use `navigation.syncState` in `MapPanel` to ensure HUD and Map remain in sync.
- [ ] **Step 3: Final verification**
Run a manual smoke test on FlyTo, Heatmap, and Export.
- [ ] **Step 4: Commit**
`git add . && git commit -m "refactor: integrate map-kit and clean up UI components"`
