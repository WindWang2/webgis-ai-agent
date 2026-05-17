# Design Spec: Advanced Cartography & H3 Spatial Statistics

**Date:** 2026-05-17  
**Status:** Approved  
**Topic:** Integration of H3 hex binning, LISA (Local Indicators of Spatial Association), and a data-driven Thematic Cartography engine.

## 1. Goal
Elevate the analytical depth of the Agent by introducing Uber's H3 grid system for mathematically robust spatial statistics (LISA). Simultaneously, overhaul the cartography engine to use high-performance data-driven styling and dynamic legend generation for professional exports.

## 2. Architecture: H3 Spatial Statistics (Backend)

### 2.1 `app/lib/geo_analysis/aggregation.py`
- **`h3_binning(geojson, resolution, stat_field, stat_method)`**:
  - Replaces or augments the simple fishnet grid.
  - Groups points into H3 hexagons at the specified resolution.
  - Supports `count`, `sum`, `mean` aggregation.

### 2.2 `app/lib/geo_analysis/statistics.py`
- **`h3_lisa(h3_geojson, value_field)`**:
  - Computes Local Moran's I on the H3 hex grid.
  - Classifies each hex into: `HH` (High-High cluster), `LL` (Low-Low cluster), `HL` (High-Low outlier), `LH` (Low-High outlier), or `NS` (Not Significant).
  - Outputs a `GeoAnalysisResult` with a narrative summary of the dominant clustering patterns.

## 3. Architecture: Data-Driven Cartography (Full Stack)

### 3.1 Backend: `app/tools/cartography.py`
- **Refactor `create_thematic_map`**:
  - **Old Behavior**: Loops through thousands of GeoJSON features and injects `fill_color` into each `properties` dict.
  - **New Behavior**: Calculates the classification breaks (e.g., Fisher-Jenks Natural Breaks) and returns a lightweight `ThematicStyleDef` object alongside the unmodified GeoJSON.
  - The `ThematicStyleDef` includes:
    - `type`: "choropleth" | "lisa"
    - `field`: The property field to symbolize.
    - `breaks`: Array of cutoff values.
    - `colors`: Array of hex color strings.
    - `legend_labels`: Human-readable labels for the legend (e.g., "100 - 500").

### 3.2 Frontend: `frontend/lib/map-kit/renderer.ts`
- **Implement `addThematicLayer(map, id, data, styleDef)`**:
  - Translates the backend's `ThematicStyleDef` into a MapLibre **Data-Driven Expression**.
  - Example: `['step', ['get', field], colors[0], breaks[0], colors[1], breaks[1]...]`.
  - Dramatically improves rendering performance for large H3 hex datasets.

### 3.3 Frontend: `frontend/lib/map-kit/exporter.ts`
- **Dynamic Legends**:
  - The `composeLayout` function will now accept the `ThematicStyleDef`.
  - When drawing the canvas for PDF/PNG export, it will automatically iterate over the `colors` and `legend_labels` to draw a professional, categorized legend box.

## 4. Dependencies
- Backend: Add `h3` (H3-py) to `requirements.txt`.
- Frontend: No new dependencies; purely leveraging MapLibre GL JS expressions and Canvas API.

## 5. Success Criteria
- [ ] Agent can aggregate 10,000 points into an H3 grid and perform LISA analysis.
- [ ] MapLibre renders the H3 grid using data-driven styling without freezing the browser.
- [ ] The exported map (PNG/PDF) includes a beautifully formatted legend corresponding exactly to the Jenks breaks.
