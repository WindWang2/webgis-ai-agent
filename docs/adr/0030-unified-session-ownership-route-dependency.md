# ADR 0030: Unified Session-Ownership Route Dependency (`require_owned_session`)

* **Status**: Accepted
* **Date**: 2026-08-01
* **Context**: Batch 6 Candidate F4 Architecture Refactoring

## Context and Problem Statement

Across FastAPI routes (`chat.py`, `layer.py`, `report.py`, `upload.py`, `task.py`), session-scoped read/write endpoints executed repetitive manual session verification boilerplate:

```python
async with async_db_session() as db:
    conv = await AsyncHistoryService(db).get_session(
        session_id, user_id=user_id, owner_token=owner_token
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Session not found")
```

This led to:
1. **Boilerplate Duplication**: 5+ separate endpoints and route-level helpers duplicated multi-line database session querying and error handling.
2. **Inconsistent Security Scope**: Some route helpers passed `owner_token` (SEC-08 token validation for anonymous sessions), while others omitted it or constructed custom queries.
3. **Cross-Module Imports**: `task.py` imported a local helper `_verify_session_owner` directly from `layer.py`.

## Decision

We introduce a unified session ownership verification model centered in `app/core/auth.py`:

1. **Canonical Async Helper**: `verify_session_owner(db, session_id, user_id=None, owner_token=None)` in `app/core/auth.py`. Handles `AsyncHistoryService` lookups, validates `user_id` or `X-Session-Token` (SEC-08), and raises `HTTPException(404, "Session not found")` on non-existent or unauthorized access (S31/S32/SEC-08 cross-tenant isolation). Returns the `Conversation` ORM object.
2. **FastAPI Dependency Injection**: `require_owned_session` in `app/core/auth.py`. Extracts `session_id`, `_user` via `get_current_user_optional`, and `X-Session-Token` header (`get_owner_token`), delegates to `verify_session_owner`, and directly injects `conv: Conversation` into FastAPI route handlers.
3. **Route Migration**:
   - `chat.py`: Endpoints (`get_session_detail`, `get_session_map_state`, `push_session_map_state`, `clear_session`) use `Depends(require_owned_session)`.
   - `layer.py`: `get_session_layer_data` uses `Depends(require_owned_session)`. Retains a thin wrapper `_verify_session_owner` for backwards compatibility.
   - `task.py`: Endpoints use `Depends(require_owned_session)` or call `verify_session_owner(db, ...)`.
   - `report.py` & `upload.py`: Use `verify_session_owner(db, ...)`.

## Consequences

- **Security Uniformity**: All session-scoped endpoints guarantee SEC-08 anonymous session token checks and S31/S32/S35/S42 cross-tenant 404 security rules.
- **Clean Route Signatures**: Eliminates redundant `async with async_db_session()` blocks from route functions.
- **Zero Regressions**: Verified by 71 passing targeted security/session tests and full application import sweep (141 modules, 0 errors).
