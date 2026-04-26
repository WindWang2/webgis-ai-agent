# Chinese Map API Integration Design

**Date:** 2026-04-27
**Status:** Approved

## Goal

Integrate Amap (高德) and Baidu Maps APIs as backend tools, providing POI search, geocoding, reverse geocoding, route planning, and district queries with automatic WGS84 ↔ GCJ-02 ↔ BD-09 coordinate transformation. All input/output is WGS84; the coordinate offset is invisible to the Agent and MapLibre.

## Architecture

```
Agent → chinese_maps tools → coord_transform (WGS84 ↔ GCJ-02/BD-09)
                                    ↓                ↓
                              Amap API (GCJ-02)   Baidu API (BD-09)
                                    ↓                ↓
                              coord_transform back to WGS84 → Agent → MapLibre
```

Single tool file `app/tools/chinese_maps.py` using the existing `ToolRegistry` pattern. Each tool accepts a `provider` parameter (`"amap"` default / `"baidu"`) with automatic fallback between providers.

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `app/utils/coord_transform.py` | Pure-math WGS84 ↔ GCJ-02 ↔ BD-09 conversion (~80 lines) |
| `app/tools/chinese_maps.py` | 5 tools registered via `register_chinese_map_tools(registry)` |

### Modified Files

| File | Change |
|------|--------|
| `app/core/config.py` | Add `AMAP_API_KEY` and `BAIDU_MAP_AK` fields |
| `.env.example` | Add placeholder entries for both keys |
| `app/api/routes/chat.py` | Import and call `register_chinese_map_tools` |
| `app/services/chat_engine.py` | Update SYSTEM_PROMPT to document new tools |

## Tools

### `search_poi`

POI search by keyword within a city/area.

- **Params:** `keyword` (str), `city` (str), `provider` ("amap"|"baidu", default "amap"), `limit` (int, default 20)
- **Amap API:** `https://restapi.amap.com/v3/place/text`
- **Baidu API:** `https://api.map.baidu.com/place/v2/search`
- **Output:** GeoJSON FeatureCollection with POI name, address, type, coordinates (WGS84)
- **Fallback:** If no key configured, falls back to `query_osm_poi`

### `geocode_cn`

Chinese address string → coordinates.

- **Params:** `address` (str), `city` (str, optional), `provider` ("amap"|"baidu", default "amap")
- **Amap API:** `https://restapi.amap.com/v3/geocode/geo`
- **Baidu API:** `https://api.map.baidu.com/geocoding/v3/`
- **Output:** `{ location: [lng, lat], formatted_address, province, city, district, adcode }` (WGS84)
- **Fallback:** Falls back to Nominatim

### `reverse_geocode_cn`

Coordinates → Chinese address and POI nearby.

- **Params:** `location` ([lng, lat]), `provider` ("amap"|"baidu", default "amap")
- **Amap API:** `https://restapi.amap.com/v3/geocode/regeo`
- **Baidu API:** `https://api.map.baidu.com/reverse_geocoding/v3/`
- **Output:** `{ formatted_address, province, city, district, street, street_number, nearby_pois: [...] }` (WGS84)
- **Fallback:** Falls back to Nominatim reverse geocode

### `plan_route`

Route planning for driving/walking/cycling/transit.

- **Params:** `origin` ([lng, lat]), `destination` ([lng, lat]), `mode` ("driving"|"walking"|"cycling"|"transit"), `city` (str, for transit), `provider` ("amap"|"baidu", default "amap")
- **Amap API:** `https://restapi.amap.com/v3/direction/{driving,walking,cycling,transit}/integrated`
- **Baidu API:** `https://api.map.baidu.com/directionlite/v1/{driving,walking,riding,transit}`
- **Output:** `{ distance_m, duration_s, polyline: [[lng,lat],...], steps: [{instruction, distance, duration}] }` (WGS84)
- **Fallback:** Returns error message asking user to configure API key

### `get_district`

Administrative district boundary query.

- **Params:** `keywords` (str), `level` ("country"|"province"|"city"|"district"), `provider` ("amap"|"baidu", default "amap")
- **Amap API:** `https://restapi.amap.com/v3/config/district`
- **Baidu API:** `https://api.map.baidu.com/api?v=2.0&ak=...` (Administrative division API)
- **Output:** GeoJSON FeatureCollection with district boundaries
- **Fallback:** Falls back to `query_osm_boundary`

## Coordinate Transformation

`app/utils/coord_transform.py` implements:

- `wgs84_to_gcj02(lng, lat) -> (lng, lat)` — input conversion for Amap
- `gcj02_to_wgs84(lng, lat) -> (lng, lat)` — output conversion for Amap
- `wgs84_to_bd09(lng, lat) -> (lng, lat)` — input conversion for Baidu
- `bd09_to_wgs84(lng, lat) -> (lng, lat)` — output conversion for Baidu
- `gcj02_to_bd09(lng, lat) -> (lng, lat)` — intermediate conversion
- `bd09_to_gcj09(lng, lat) -> (lng, lat)` — intermediate conversion

All algorithms are pure math based on the standard Krasovsky 1940 ellipsoid parameters and the national encryption constants. No third-party dependencies required.

Batch helpers for GeoJSON coordinate arrays:
- `transform_geojson_coords(geojson, from_system, to_system)`

## Configuration

`app/core/config.py` additions:

```python
AMAP_API_KEY: str = ""      # 高德 Web 服务 API Key
BAIDU_MAP_AK: str = ""       # 百度地图 AK
```

`.env.example` additions:

```env
# Chinese Map APIs (optional, enables POI search, CN geocoding, route planning)
AMAP_API_KEY=
BAIDU_MAP_AK=
```

When a key is empty, the corresponding provider is skipped. If both are empty, tools fall back to existing OSM/Nominatim services.

## Degradation Strategy

| Tool | Both keys empty | One key empty |
|------|----------------|---------------|
| `search_poi` | Fallback to `query_osm_poi` | Use available provider |
| `geocode_cn` | Fallback to Nominatim | Use available provider |
| `reverse_geocode_cn` | Fallback to Nominatim | Use available provider |
| `plan_route` | Return error with config instructions | Use available provider |
| `get_district` | Fallback to `query_osm_boundary` | Use available provider |

When the selected provider returns an error (rate limit, network), the tool automatically tries the other provider before failing.

## Error Handling

- API timeout: 10 seconds per request
- Rate limit: Return partial results with warning
- Invalid key: Return clear error message with key name
- Network error: Try other provider, then return error
- Coordinate transform: Input validation (lng [-180,180], lat [-90,90])
