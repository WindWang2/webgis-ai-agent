# Design Spec: Frontend Map Toolkit (map-kit)

**Date:** 2026-05-17  
**Status:** Approved  
**Topic:** Decoupling frontend map operations into a professional functional library.

## 1. Goal
Extract and standardize all frontend map-related logic (navigation, rendering, and export) into a decoupled, stateless functional library located at `frontend/lib/map-kit/`. This improves testability, maintainability, and reusability.

## 2. Architecture
The toolkit is built as a set of pure functional namespaces.

### 2.1 navigation.ts
- **Responsibility:** Smooth viewport control and spatial orientation.
- **Functions:**
  - `flyTo(map, params)`: Smoothed transition to a center/zoom.
  - `fitBounds(map, bbox, padding)`: Automatically adjust view to a bounding box.
  - `jumpTo(map, params)`: Instant viewport change.
  - `syncState(map, store)`: Sync map center/zoom back to HUD store.

### 2.2 renderer.ts
- **Responsibility:** Layer management, source handling, and vector styling.
- **Functions:**
  - `addGeoJsonSource(map, id, data)`: Robust source addition with error recovery.
  - `addVectorLayer(map, options)`: Support for circle, line, and fill layers.
  - `addNativeHeatmap(map, options)`: Professional MapLibre vector heatmap integration.
  - `removeLayerStack(map, layerId)`: Cleanly remove a layer and its associated source.

### 2.3 exporter.ts
- **Responsibility:** High-fidelity map snapshotting and professional layout composition.
- **Functions:**
  - `captureSnapshot(map, options)`: Extract high-DPI image from the map canvas.
  - `composeLayout(canvas, elements)`: Overlay title, scale, and compass using HTML5 Canvas.
  - `downloadResult(blob, filename)`: Browser-safe file download trigger.

## 3. Design Principles
- **Statelessness:** Functions do not maintain their own map state; they operate directly on the provided `map` instance.
- **Standardized Coords:** All functions strictly enforce `[lng, lat]` order and WGS84 coordinates.
- **Promise-Based:** Wraps asynchronous MapLibre events (like `idle` or `load`) in async/await patterns.

## 4. Integration
`MapActionHandler.tsx` will be refactored to act as a pure command dispatcher that delegates all heavy lifting to the `map-kit`.
