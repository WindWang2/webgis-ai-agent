# ADR-003: Hybrid Async Compute Model

## Context

Spatial analysis operations (GeoPandas, rasterio, raster reprojection) are CPU-intensive and
will block the async event loop for seconds to minutes. The system needs to keep the SSE
gateway responsive while allowing heavy computation.

## Decision

Agent tool calls run in-process (asyncio event loop). Heavy spatial operations are dispatched
to Celery workers. This creates three parallel task-tracking systems:
1. `TaskTracker` (in-memory): agent tool-execution chain per user message.
2. `AnalysisTask` (DB): Celery-backed spatial ops with persistence across restarts.
3. `TaskQueueService._task_owners` (in-memory dict): ownership checks for Celery tasks.

## Consequences

**Positive:**
- SSE gateway stays responsive during CPU-bound GIS work.
- Celery workers can be scaled independently.
- `TaskTracker` gives sub-second visibility for agent steps; `AnalysisTask` gives persistence
  for long-running jobs.

**Negative:**
- Three tracking systems are not unified; `/tasks` and `/tasks/status/{id}` show different
  task lists.
- Cancellation is cooperative (polling `_cancelled` flag), not preemptive. Long-running tools
  may continue for 30–60s after user clicks cancel.
- Ownership checks must be duplicated across both systems.
