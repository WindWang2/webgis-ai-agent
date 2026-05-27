# Changelog

## [Unreleased]

### Added

- **`display_layer` AI tool**: lets the agent explicitly show a hidden data
  layer on the map with a meaningful name. All GeoJSON tool results are now
  loaded as hidden layers by default (layer ID = `ref_id`); the agent must
  call `display_layer(ref_id, name)` to surface the final result layer.
  Intermediate layers (boundary queries, raw POI searches, buffer helpers)
  remain hidden, keeping the map clean.
- **`LAYER_VISIBILITY_UPDATE` command extended**: now accepts optional `name`
  (renames the layer in the panel) and `color` (overrides the fill/stroke
  color) params alongside the existing `visible` and `opacity`.

### Fixed

- **Vertex circles on polygon/line vector layers** removed. Overpass API was
  returning untagged topology nodes (polygon boundary vertices with no
  attributes) as Point features; these are now skipped at parse time
  (`_overpass_to_geojson` requires `el.get("tags")` for node elements).
  Frontend cleanup: stale `*-point` MapLibre sublayers are explicitly hidden
  when a layer has no point features, and the circle sublayer carries an
  explicit `['==', '$type', 'Point']` filter.
- **Think content now collapsed** in the UI. The `is_reasoning` flag was
  being stripped from the `token` SSE event before reaching the frontend;
  it is now forwarded so reasoning tokens route to `CollapsibleThink` instead
  of the main message body.

### Changed

- Default UI theme is now **light** (was dark).
- Agent `max_rounds` raised from 30 to 60, reducing "达到最大轮数" aborts
  on complex multi-step analyses.

### Performance

- Tool-layer result cache (`@cached_tool`) opt-in via decorator, Redis-keyed,
  with graceful fallback when Redis is unreachable.
- Automatic per-dispatch timing in `ToolRegistry.dispatch` — every tool call
  writes one JSONL row to `logs/tool_metrics.jsonl` and contributes to an
  in-process aggregator that emits a `TOOL_METRICS_DIGEST` line every 100
  calls and at FastAPI shutdown.
- `trim_features` helper for payload reduction (caps FeatureCollection at
  5000 features, rounds coordinates to 6 decimals).
- `buffer_analysis`, `heatmap_data`, `h3_binning`, `kde_contours` opted in.
