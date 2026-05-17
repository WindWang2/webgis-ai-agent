# Advanced Cartography & H3 Spatial Statistics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate H3 hex binning, LISA (Local Indicators of Spatial Association), and a high-performance data-driven Thematic Cartography engine.

**Architecture:** 
- **Backend:** Add `h3` and `esda` dependencies. Implement `h3_binning` and `h3_lisa`. Refactor `create_thematic_map` to return a `ThematicStyleDef` instead of mutating GeoJSON.
- **Frontend:** Update `map-kit/renderer.ts` to consume `ThematicStyleDef` for MapLibre data-driven styling. Update `map-kit/exporter.ts` to draw dynamic legends on canvas.

**Tech Stack:** Python, H3-py, PySAL (esda, libpysal), MapLibre GL JS, HTML5 Canvas.

---

### Task 1: Backend Dependencies & H3 Binning

**Files:**
- Modify: `requirements.txt`
- Modify: `app/lib/geo_analysis/aggregation.py`
- Test: `tests/unit/lib/test_geo_analysis.py`

- [ ] **Step 1: Add dependencies**
Add `h3>=3.7.6`, `esda>=2.5.1`, and `libpysal>=4.9.2` to `requirements.txt`.
- [ ] **Step 2: Implement H3 Binning**
Implement `h3_binning(geojson, resolution, stat_field, stat_method)` in `aggregation.py`. Use H3 to convert point coordinates to hex indices, group by index, calculate the statistic, and convert indices back to polygon boundaries. Return `GeoAnalysisResult`.
- [ ] **Step 3: Write tests for H3 Binning**
Verify that points are correctly assigned to hex bins and statistics are calculated.
- [ ] **Step 4: Commit**
`git add requirements.txt app/lib/geo_analysis/aggregation.py tests/unit/lib/test_geo_analysis.py && git commit -m "feat: add H3 dependencies and implement h3_binning"`

### Task 2: H3 LISA (Local Spatial Autocorrelation)

**Files:**
- Modify: `app/lib/geo_analysis/statistics.py`
- Test: `tests/unit/lib/test_geo_analysis.py`

- [ ] **Step 1: Implement H3 LISA**
Implement `h3_lisa(h3_geojson, value_field)` in `statistics.py`. Use `libpysal.weights.Queen.from_dataframe` to build weights for the H3 hexes. Use `esda.moran.Moran_Local` to calculate LISA. Assign `lisa_cluster` labels (HH, LL, HL, LH, NS).
- [ ] **Step 2: Add Narrative Summary**
The `GeoAnalysisResult.summary` should describe the dominant cluster type and count of significant hexes.
- [ ] **Step 3: Write tests for H3 LISA**
Verify that the `lisa_cluster` field is correctly populated.
- [ ] **Step 4: Commit**
`git add app/lib/geo_analysis/statistics.py tests/unit/lib/test_geo_analysis.py && git commit -m "feat: implement H3 LISA (Local Moran's I) with narrative summary"`

### Task 3: Backend Data-Driven Cartography Engine

**Files:**
- Modify: `app/tools/cartography.py`
- Modify: `app/services/cartography_service.py`

- [ ] **Step 1: Refactor `create_thematic_map`**
Update the tool in `app/tools/cartography.py` to return a `ThematicStyleDef` object alongside the original geojson. Stop modifying the raw GeoJSON properties.
- [ ] **Step 2: Update `CartographyService`**
Update `apply_choropleth` in `cartography_service.py` to calculate breaks and colors, and construct the `ThematicStyleDef` dictionary containing `type`, `field`, `breaks`, `colors`, and `legend_labels`.
- [ ] **Step 3: Add LISA Thematic Support**
Add a specific branch in `create_thematic_map` to handle `type="lisa"`, automatically assigning standard colors (Red for HH, Blue for LL, etc.).
- [ ] **Step 4: Commit**
`git add app/tools/cartography.py app/services/cartography_service.py && git commit -m "refactor: backend cartography engine now outputs data-driven ThematicStyleDef"`

### Task 4: Frontend Data-Driven Rendering

**Files:**
- Modify: `frontend/lib/map-kit/types.ts`
- Modify: `frontend/lib/map-kit/renderer.ts`
- Modify: `frontend/components/map/map-action-handler.tsx`

- [ ] **Step 1: Update Types**
Define `ThematicStyleDef` in `frontend/lib/map-kit/types.ts`.
- [ ] **Step 2: Implement `addThematicLayer`**
In `renderer.ts`, create a function that translates `ThematicStyleDef` into a MapLibre `['step', ...]` or `['match', ...]` expression for the `fill-color` paint property.
- [ ] **Step 3: Update `MapActionHandler`**
Handle the command from the backend that provides the `ThematicStyleDef`, calling the new `addThematicLayer`.
- [ ] **Step 4: Commit**
`git add frontend/lib/map-kit/ frontend/components/map/map-action-handler.tsx && git commit -m "feat(map-kit): implement high-performance data-driven thematic rendering"`

### Task 5: Frontend Dynamic Legend Export

**Files:**
- Modify: `frontend/lib/map-kit/exporter.ts`
- Modify: `frontend/components/map/map-panel.tsx` (if needed to pass legend state)

- [ ] **Step 1: Update `composeLayout`**
Modify `composeLayout` in `exporter.ts` to accept `ThematicStyleDef` (or a derived legend object).
- [ ] **Step 2: Draw Dynamic Legend**
Use Canvas 2D API to draw color swatches and text labels based on `legend_labels` in the lower corner of the exported image.
- [ ] **Step 3: Commit**
`git add frontend/lib/map-kit/exporter.ts && git commit -m "feat(map-kit): implement dynamic canvas-based legends for map export"`

### Task 6: System Integration & Review

**Files:**
- Modify: `app/services/chat_engine.py`

- [ ] **Step 1: Update SYSTEM_PROMPT**
Instruct the Agent to use `h3_binning` and `h3_lisa` for advanced spatial statistics. Ensure the Agent knows how to pass the output to `create_thematic_map` for data-driven styling.
- [ ] **Step 2: End-to-End Verification**
Run the backend tests (`pytest`) and frontend tests (`vitest`).
- [ ] **Step 3: Commit**
`git add app/services/chat_engine.py && git commit -m "feat: finalize integration of H3 statistics and data-driven cartography"`
