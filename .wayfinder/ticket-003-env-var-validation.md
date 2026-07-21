# Ticket: Add startup validation for required env vars

**Label**: `wayfinder:task` | **Type**: HITL

## Question

`LLM_API_KEY` has a placeholder default (`"your-api-key-here"`) that allows the app to start with an invalid key. Should we add a startup validator that raises `RuntimeError` if required env vars are missing or set to placeholder values in production mode?

## Context

- File: `app/core/config.py`, line 53
- Severity: P0-critical
- Affected vars: `LLM_API_KEY`, possibly `JWT_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`
