# Geo Processor Design Spec

**Goal:** Provide a robust, high-level GIS library for common geoprocessing tasks with automatic CRS management.

## 1. Architecture

The `geo_processor` library is designed as a modular package within `app/lib`. It abstracts low-level GeoPandas and Shapely operations into "smart" functions that handle projection and data validation automatically.

### Modules:
- `core.py`: Coordinate transformations, GeoJSON parsing, and UTM projection utilities.
- `geometry.py`: Geometric operations (buffer, clip, dissolve).
- `overlay.py`: Spatial overlays (intersection, union, difference).

## 2. Component Details

### 2.1 Core Module (`core.py`)
- `safe_parse(geojson: Any) -> dict`:
    - Input: string or dict.
    - Output: dict (GeoJSON).
    - Logic: Robust parsing with error handling.
- `to_utm_gdf(geojson: dict | str) -> tuple[gpd.GeoDataFrame, str]`:
    - Upgraded from `_geojson_utils.py`.
    - Handles automatic UTM zone detection based on centroid.
    - Returns the projected GeoDataFrame and the EPSG string.
- `Coordinate Transformers`:
    - `wgs84_to_gcj02`, `gcj02_to_wgs84`, `gcj02_to_bd09`, `bd09_to_gcj02`.
    - Migrated from `app/utils/coord_transform.py`.

### 2.2 Geometry Module (`geometry.py`)
- `buffer_smart(geojson: Any, distance: float, unit: str = "meters") -> dict`:
    - Automatically converts to UTM for accurate metric buffering.
    - Returns result as GeoJSON in WGS84.
- `clip_smart(target_layer: Any, mask_layer: Any) -> dict`:
    - Ensures both layers are in the same CRS.
    - Performs spatial clip.
- `dissolve_smart(geojson: Any, field: str = None) -> dict`:
    - Standard dissolve operation.

### 2.3 Overlay Module (`overlay.py`)
- `intersection(layer_a: Any, layer_b: Any) -> dict`
- `union(layer_a: Any, layer_b: Any) -> dict`
- `difference(layer_a: Any, layer_b: Any) -> dict`
- All overlay functions will:
    - Parse inputs using `safe_parse`.
    - Align CRS if they differ.
    - Return GeoJSON Features.

## 3. Data Flow

1. Input (GeoJSON string/dict)
2. `safe_parse` -> dict
3. Operation-specific logic:
    - If distance-based: `to_utm_gdf` -> Operate in UTM -> `to_crs("EPSG:4326")`
    - If overlap-based: Align CRS -> Operate -> `to_crs("EPSG:4326")`
4. Output (GeoJSON dict)

## 4. Testing Strategy

- **Unit Tests**: Located in `tests/unit/lib/test_geo_processor.py`.
- **Methodology**: TDD. Every function must have at least one success case and one edge case (empty input, invalid geometry).
- **Fixtures**: Use small, well-defined GeoJSON features (Points, Polygons).

## 5. Migration Plan
- Move logic from `app/tools/_geojson_utils.py` and `app/utils/coord_transform.py`.
- Update `app/tools/_geojson_utils.py` to use `app/lib/geo_processor` once the library is stable (future phase).
