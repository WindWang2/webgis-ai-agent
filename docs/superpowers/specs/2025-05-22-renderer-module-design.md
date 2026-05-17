# Design Spec: Advanced Renderer Module

## Goal
Implement a professional layer and style management module in `frontend/lib/map-kit/renderer.ts` to refactor map rendering logic out of components and into a reusable library.

## Architecture

The `renderer` module will provide a set of utility functions for interacting with a MapLibre map instance. These functions will handle source and layer lifecycle, ensuring that sources and layers are correctly added, updated, or removed without duplication or errors.

### Functions

#### 1. `addGeoJsonSource(map, id, data)`
- **Purpose**: Safely add or update a GeoJSON source.
- **Parameters**:
  - `map: maplibregl.Map`
  - `id: string`
  - `data: GeoJSON.FeatureCollection | GeoJSON.Feature`
- **Logic**:
  - Check if source `id` exists.
  - If yes, call `source.setData(data)`.
  - If no, call `map.addSource(id, { type: 'geojson', data })`.

#### 2. `addVectorLayer(map, options)`
- **Purpose**: Add a vector layer (circle, line, or fill).
- **Parameters**:
  - `map: maplibregl.Map`
  - `options: VectorLayerOptions`
- **`VectorLayerOptions`**:
  - `id: string`
  - `source: string`
  - `type: 'circle' | 'line' | 'fill'`
  - `paint?: any`
  - `layout?: any`
  - `beforeId?: string`
- **Logic**:
  - If layer `id` exists, remove it first (to allow type changes).
  - Call `map.addLayer`.

#### 3. `addNativeHeatmap(map, options)`
- **Purpose**: Add a native MapLibre heatmap layer.
- **Parameters**:
  - `map: maplibregl.Map`
  - `options: HeatmapLayerOptions`
- **`HeatmapLayerOptions`**:
  - `id: string`
  - `source: string`
  - `palette?: 'classic' | 'magma' | 'viridis' | 'thermal'`
  - `radius?: number`
  - `weight?: number | any[]`
  - `intensity?: number | any[]`
  - `opacity?: number`
  - `beforeId?: string`
- **Logic**:
  - Define internal `colorRamps` for the supported palettes.
  - Construct the heatmap layer specification.
  - If layer `id` exists, remove it.
  - Call `map.addLayer`.

#### 4. `removeLayerStack(map, id)`
- **Purpose**: Safely remove a layer and its corresponding source.
- **Parameters**:
  - `map: maplibregl.Map`
  - `id: string`
- **Logic**:
  - Check if layer `id` exists, if so `map.removeLayer(id)`.
  - Check if source `id` exists, if so `map.removeSource(id)`.

#### 5. `updateLayerStyle(map, id, style)`
- **Purpose**: Update layer visibility and opacity.
- **Parameters**:
  - `map: maplibregl.Map`
  - `id: string`
  - `style: { visible?: boolean; opacity?: number }`
- **Logic**:
  - Check if layer `id` exists.
  - If `visible` is provided, `map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none')`.
  - If `opacity` is provided, determine the correct paint property based on layer type (e.g., `fill-opacity`, `line-opacity`, `circle-opacity`, `heatmap-opacity`) and call `map.setPaintProperty`.

## Testing Strategy
- Use `vitest` for unit tests.
- Mock `maplibregl.Map` instance.
- Verify that MapLibre methods (`addSource`, `addLayer`, `getSource`, `getLayer`, `removeLayer`, `removeSource`, `setLayoutProperty`, `setPaintProperty`) are called with the expected arguments.
- Test edge cases: adding to existing source, removing non-existent layer, updating style of non-existent layer.

## Dependencies
- `maplibre-gl`
