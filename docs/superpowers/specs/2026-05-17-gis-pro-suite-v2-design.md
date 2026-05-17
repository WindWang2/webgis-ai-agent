# Design Spec: GIS Pro Suite v2 (Raster-Vector Synergy & UI Intelligence)

**Date:** 2026-05-17  
**Status:** Approved  
**Topic:** Integration of raster statistical analysis and interactive frontend data intelligence.

## 1. Goal
Expand the `geo_analysis` and `map-kit` libraries to support industrial-grade Raster-Vector operations (Zonal Stats, Interpolation) and interactive UI capabilities (Feature Probing, Dynamic Filtering). This completes the transformation into a professional, standalone-ready GIS package.

## 2. Backend: Raster-Vector Synergy (geo_analysis)

### 2.1 Zonal Statistics (`zonal_statistics`)
- **Responsibility**: Compute descriptive statistics (mean, max, min, sum, std) of raster values within vector zones.
- **Engine**: Uses `rasterio` for raster data handling and `geopandas` for zonal iteration.
- **Workflow**: Auto-projects both layers to a shared metric CRS (UTM) before sampling to ensure pixel alignment.

### 2.2 Spatial Interpolation (`idw_interpolation`)
- **Responsibility**: Generate a continuous spatial surface from discrete point samples.
- **Method**: Inverse Distance Weighting (IDW).
- **Output**: Returns a high-resolution H3 hexagonal grid where each hex contains the estimated value. This ensures professional "smooth" rendering.

## 3. Frontend: UI Data Intelligence (map-kit)

### 3.1 Feature Identification (`queryFeaturesAt`)
- **Namespace**: `map-kit/state.ts`
- **Logic**: Wraps `map.queryRenderedFeatures`. Allows the Agent to "read" attribute data for specific coordinates.
- **Result**: Standardized `FeatureInfo` object including properties, layer metadata, and screen coordinates.

### 3.2 Dynamic Data Filtering (`setLayerFilter`)
- **Namespace**: `map-kit/renderer.ts`
- **Logic**: Implements MapLibre data-driven expressions.
- **Capability**: Supports comparison operators (`>`, `<`) and membership checks (`in`, `!in`) applied instantly on the client side.

### 3.3 Geodesic Measurement (`measure`)
- **Namespace**: `map-kit/navigation.ts`
- **Logic**: Uses `turf.js` or manual Haversine math to calculate distance and area, ensuring accuracy at different latitudes.

## 4. LLM Decision Logic
- **"Probing" Strategy**: SYSTEM_PROMPT is updated to allow the Agent to ask "What is here?" using `queryFeaturesAt` before recommending actions.
- **"Cross-Domain" Logic**: Agent is taught to use raster data (terrain, population) to contextualize vector data (POI locations) using Zonal Stats.

## 5. Success Criteria
- [ ] `zonal_statistics` returns accurate population/slope means for vector district boundaries.
- [ ] `idw_interpolation` transforms sparse sensor points into a smooth H3 surface.
- [ ] Agent can dynamically filter visible coffee shops by "rating" without a backend round-trip.
