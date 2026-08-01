# 04 — Correct API docs endpoint paths and HTTP method contradictions

**What to build:**
Reconcile and fix documentation contradictions in `docs/api-docs.md` and `docs/data-fetcher.md` to align exact HTTP methods, parameter names, and endpoint paths with implementation code.

**Blocked by:** 02 — Document and enforce rate limiting strategy across Auth and WebSocket paths, 03 — Unify frontend component theme system behind CSS tokens.

**Status:** closed

- [x] Update `docs/api-docs.md` task cancellation method from `POST /api/v1/tasks/{id}/cancel` to `DELETE /api/v1/tasks/{id}`.
- [x] Correct singular `/api/v1/layer/{id}/data` path in `docs/data-fetcher.md` to plural `/api/v1/layers/data/{ref_id}`.
- [x] Update T003 protocol table to include missing `zoom_to_layer` and `reset_map_view` interaction commands.
- [x] Validate that all documented API endpoints match FastAPI route definitions in `app/api/routes/`.

