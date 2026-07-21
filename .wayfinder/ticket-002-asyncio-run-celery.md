# Ticket: Replace asyncio.run() with persistent event loop in Celery tasks

**Label**: `wayfinder:task` | **Type**: HITL

## Question

Seven `asyncio.run()` calls exist in Celery task files (`task_chain.py`). Each creates a new event loop per call, which is inefficient and breaks under gevent/eventlet pools. Should we create a shared event loop per worker process, and what is the safest pattern for Celery prefork compatibility?

## Context

- Files: `app/tasks/explorer/task_chain.py` (7 instances at lines 25, 32, 48, 53, 89, 148, 241)
- Severity: P0-critical
- Blocks: Explorer pipeline stability under load
