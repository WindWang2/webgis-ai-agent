# Ticket: Replace print() with structured logger calls

**Label**: `wayfinder:task` | **Type**: AFK

## Question

`oss_adapter.py` uses `print()` for error output, bypassing the logging system. Are there other `print()` statements in production code, and should we replace all with `logger` calls?

## Context

- File: `app/services/data_fetcher/adapters/oss_adapter.py`, line 22
- Severity: P2-medium
