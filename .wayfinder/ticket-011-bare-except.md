# Ticket: Replace bare except Exception clauses with specific exceptions

**Label**: **`wayfinder:task`** | **Type**: AFK

## Question

11 files contain bare `except Exception:` clauses that swallow all exceptions without logging type or re-raising. This makes debugging impossible. Should we replace each with specific exception types, or at minimum log the exception type before re-raising?

## Context

- Files: `tianditu.py:95`, `registry.py:134,148`, `_utils.py:72,96`, `spatial_stats.py:296`, `spatial.py:313,361`, `core.py:141`, `chat_engine.py:498,535`, `subagent.py:156,182`
- Severity: P2-medium
