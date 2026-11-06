# Baseline — Pi × GIS Harness usability

Date: 2026-11-06
Branch: `zcode/pi-gis-harness-usability-20261106`
Base: `origin/master` `a678d0005429541a17804f0829f482638dd3cf64`
Subject: Merge pull request #1981 (`fix(ci): keep source registry lint independent of raster runtime`)
Vendor pin: `vendor/pi` `f9a26365568127461c47a07a37dcb42c140a3ac5` (v1.5.0)
Worktree: `/home/kevin/projects/webgis/webgis-wt-pi-gis-harness-usability`
Master was not modified.

## Already merged — do not reimplement

| PR | What it already shipped |
|---|---|
| #1973 | Pi v1.5.0 pin, pi-schema coercion golden, extension two-phase load |
| #1974 | Situated selection / turn chain / interaction IR |
| #1975 | Publication layout, label governance, multi-format export |
| #1976 | Reviewed issue integration |
| #1972 | Typed analysis envelope v2 and mixed method chains |
| #1965 | scene3d render domain, observation chain, terrain supply |
| #1981 | Registry lint independent of raster runtime (current HEAD) |

Open PRs at baseline are dependabot only (#1978–#1980, #1982–#1986). Open issues: none. This branch does not touch those dependency files.

## Production call chain (as of a678d0005)

```text
user text / map event
  → chat.py pi_event_generator (skips legacy planner)
  → PiBridge.stream_prompt
  → vendored prompt (one in-memory AgentSession per worker)
  → extension tool execute → POST /pi-tools/execute
  → resolve_pi_tool_call → ToolDispatchService.dispatch
  → GIS result ref
  → webgis_map_intent / webgis_map_product / webgis_component_update
  → MapSpec (desired)
  → frontend reconcile + cartographic observation
  → maybe_finalize_map_product → task_complete SSE
```

Invariants that already hold and were not reopened:

- ToolRegistry is the capability registry. ToolDispatchService is the execution door.
- MapSpec is desired cartography. Frontend observation is runtime fact.
- Streaming fallback does not badge itself as Pi.
- Frozen natives (`webgis_map_intent`, `webgis_map_product`, `webgis_component_update`, `webgis_cartography_status`, `query_local_poi`, `get_local_admin_boundary`, `list_available_tools`) are registered and wrap-rejected on the Python classifier.
- `solve_layout_v5` stays test-only; ADR-0283 deferred MapSpec wiring. Not treated as a defect.
- SSE id dedup already prevents reconnect layer duplication. Not confirmed as a bug.

## Gaps this pass confirmed

See `REVIEW_FINDINGS.md`. Highest user impact before the fix:

1. One Pi worker transcript was shared across GIS sessions (default pool size 1).
2. The extension told the model to call `heatmap_data` via `webgis_execute`, then rejected that wrap.
3. A cancelled turn that had already entered dispatch could still `layer_upsert`.
4. `task_complete` / goal `satisfied` could be true with `render_status=unknown`.
5. Heatmap color had no no-requery paint path.
6. A Beijing intent could still draw a Chengdu `primary_ref`.
7. `nearest_facility` is Euclidean but was a Dijkstra `closest_facility` candidate, and the result did not say so.
8. Box selection was not on the next turn body. Cancel did not abort layer fetches.

## Tests that already existed

- `tests/integration/test_pi_vendor_contract.py` — vendor RPC, not two GIS sessions.
- `tests/unit/test_pi_session_plan_host.py` — Chengdu chain via in-process `dispatch_tool`, does not load `index.mjs`.
- `tests/unit/gis_harness/test_observation_completion_v4.py` — task_complete fold. Updated so degraded-without-observation is not complete.
- Cartography / goal satisfaction suites. Re-run after the render gate; 61 related tests passed.

## Conflict matrix

| Area | Open PR overlap | This change |
|---|---|---|
| `requirements*.txt`, frontend lockfiles | dependabot #1982–#1986, #1978–#1980 | none |
| `vendor/pi` | none | not edited |
| MapSpec schema | none open | checkpoint before apply; heatmap paint rewrite uses existing upsert |
| ToolRegistry / dispatch | none open | `nearest_facility` gained optional `feature_ids`; meter fields are `float` |
| Publication solver | none open | not rewired |
