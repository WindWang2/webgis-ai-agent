# Changelog

## [Unreleased]

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
