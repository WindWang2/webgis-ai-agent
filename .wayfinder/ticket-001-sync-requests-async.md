# Ticket: Replace sync requests with async httpx in data fetcher

**Label**: `wayfinder:task` | **Type**: HITL

## Question

The `third_party_api_adapter.py` uses synchronous `requests.get()` inside an async codebase, blocking the event loop. Should we migrate to `httpx.AsyncClient` for all HTTP calls in the data fetcher adapters, and what is the migration path for the base class interface?

## Context

- File: `app/services/data_fetcher/adapters/third_party_api_adapter.py`, line 41
- Severity: P0-critical
- Blocks: All async data fetcher operations
