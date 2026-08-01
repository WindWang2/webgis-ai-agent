# 34. Pure 5-Stage ExplorerPipeline & Celery Adapter Isolation

Date: 2026-08-02

## Status

Accepted

## Context

Prior to this decision, the 5-stage GIS exploration workflow (`discover`, `fetch`, `parse`, `geocode`, `validate`) had its core domain algorithms bound directly inside Celery `@celery_app.task` wrappers in `app/tasks/explorer/task_chain.py` (with the exception of `geocode_stage.py`).

This Celery coupling prevented the exploration pipeline from running in-process (e.g. for CLI tasks, unit tests, fast local development, or alternative task queues) and required Celery worker processes for testing.

## Decisions

1. **Pure Stage Modules**: Extracted pure async stage runners into individual modules under `app/services/explorer/`:
   - `discover_stage.py` -> `run_discover_stage(...)`
   - `fetch_stage.py` -> `run_fetch_stage(...)`
   - `parse_stage.py` -> `run_parse_stage(...)`
   - `validate_stage.py` -> `run_validate_stage(...)`
   Each stage accepts `ExplorerStageContext` and returns `StageResult` with dependency-injected storage seams (`load_ref`, `store_ref`).
2. **In-Process Pipeline Runner `ExplorerPipeline`**: Created `app/services/explorer/pipeline.py` providing `run_in_process(...)` for in-memory sequential stage execution.
3. **Thin Celery Adapters**: Reduced Celery tasks in `app/tasks/explorer/task_chain.py` to thin adapters that delegate to stage runner functions with `self.update_state` as the progress callback.
4. **Orchestrator Mode Switch**: Added `mode: str = "celery"` ("celery" or "in_process") to `ExplorerOrchestrator.start_exploration(...)`.

## Consequences

- **Leverage**: The 5-stage GIS exploration pipeline can run in Celery background queues, local async functions, API endpoints, or CLI tools.
- **Locality**: Parsing logic, quality pre-assessment, and field auto-mapping live inside dedicated stage modules rather than scattered inside Celery tasks.
- **Testability**: `tests/unit/test_explorer_pipeline.py` tests all 5 stages and the complete pipeline in-process without Celery worker processes.
