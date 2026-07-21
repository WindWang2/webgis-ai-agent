# Ticket: Fix CORS defaults and add explicit configuration

**Label**: `wayfinder:task` | **Type**: HITL

## Question

CORS defaults to `["*"]` with `allow_credentials=True`, which is a dangerous combination. Should we change the default to `[]` or a specific localhost origin, and add explicit `allow_methods` and `allow_headers` configuration?

## Context

- Files: `app/core/config.py`, line 90; `app/main.py`, lines 148-159
- Severity: P2-medium
