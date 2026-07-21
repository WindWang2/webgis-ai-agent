# Ticket: Replace naive datetime.now() with timezone-aware UTC

**Label**: `wayfinder:task` | **Type**: HITL

## Question

12+ files use `datetime.now()` instead of `datetime.now(timezone.utc)`. This causes `TypeError` when comparing naive and aware datetimes, and incorrect timezone handling. Should we replace all instances, and should we add a linter rule to prevent regressions?

## Context

- Files: `monitoring_report.py`, `session_data.py`, `session_data_redis.py`, `decision_log.py`, `context_builder.py`, `session_overview.py`, `explorer/quality_engine.py`, `report_service.py`, and more
- Severity: P1-high
