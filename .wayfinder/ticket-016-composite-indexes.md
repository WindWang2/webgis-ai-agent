# Ticket: Add composite DB indexes for common query patterns

**Label**: `wayfinder:task` | **Type**: AFK

## Question

`Layer` and `AnalysisTask` have single-column indexes, but common queries filter by `(org_id, status)` or `(org_id, category, status)`. Should we add composite indexes, and generate the corresponding Alembic migration?

## Context

- File: `app/models/db_model.py`
- Severity: P2-medium
