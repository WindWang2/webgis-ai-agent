# Keep the AsyncHistoryService DB fallback — do not force session injection

**Status:** accepted

We will **not** remove the `if self.db is None: async with async_db_session()` fallback from
`AsyncHistoryService`, nor force DB-session injection at the service boundary or via route
middleware. The fallback serves a legitimate no-request-context caller; the proposed fix
would break it.

## Context

Architecture-review Candidate #5 (rated "Worth exploring", 2026-08-01 report) claimed
`AsyncHistoryService` methods contain inline `if self.db is None: async with
async_db_session()` fallbacks, "embedding session creation inside every service method." The
proposed fix: require DB session injection at the service boundary or manage sessions via
outer route middleware, removing the hidden fallbacks.

A code investigation found the claim overstated, the proposed fix infeasible for the one real
caller, and a narrower (cosmetic) sub-problem worth recording.

## What the investigation found

### 1. The fallback exists in 2 of ~10 methods, not "every method"

`AsyncHistoryService.__init__` takes an optional session
(`db: Optional[AsyncSession] = None`, `history_service_async.py:68`). Two methods branch on it:

```python
# load_context, L79-84
if self.db is not None:
    conv = await self.get_or_create_conversation(session_id, user_id=user_id)
else:
    async with async_db_session() as db:
        svc = AsyncHistoryService(db)
        conv = await svc.get_or_create_conversation(session_id, user_id=user_id)
```

The same shape repeats at L116-121 for `commit_interaction`. Every other method assumes a
non-None `db` (the routes always pass one). The "every service method" framing is inaccurate.

### 2. One fallback serves a legitimate caller; the other is dead code

- `load_context`'s fallback has exactly **one** app caller: `chat_engine.py:190`, which
  instantiates `AsyncHistoryService()` with **no db**. ChatEngine is a long-lived service, not
  a FastAPI request handler — it has no request to middleware a session onto, and no session
  to inject at construction. The fallback is the correct accommodation for this no-context
  caller.
- `commit_interaction`'s fallback is **dead code** — zero callers in `app/` or `tests/`.

### 3. The proposed fix breaks the real caller

"Manage sessions via outer route middleware" is infeasible for ChatEngine: it is not a request
handler and has no request. "Require injection at the service boundary" breaks
`AsyncHistoryService()` unless paired with a larger ChatEngine refactor that gives it a session
factory — out of scope for a "Worth exploring" candidate.

### 4. The genuine (minor) finding is route-layer inconsistency

The history routes open their own session inline:
```python
# chat.py:176, 205, 242, 272
async with async_db_session() as db:
    sessions = await AsyncHistoryService(db).list_sessions(...)
```
Meanwhile `report.py:110` already uses the FastAPI dependency idiom
(`db: AsyncSession = Depends(get_async_db)`). So the route layer is **inconsistent** — some
routes use `Depends(get_async_db)`, history routes reinvent it inline. This is a real cosmetic
smell, but it is NOT "the route has a session and fails to pass it": each route correctly
passes the session it opens; it just opens it the long way. This does not affect the service's
fallback, which is driven by ChatEngine's no-db construction, not by the routes.

## Decision

Keep the `AsyncHistoryService` DB fallback. Do not force session injection or route middleware.
The fallback correctly serves ChatEngine's no-request-context construction.

## Recorded sub-problem (optional cleanup, not blocking)

The route layer is inconsistent: history routes use inline
`async with async_db_session() as db` while other routes use `Depends(get_async_db)`. Aligning
the history routes to `Depends(get_async_db)` is a safe cosmetic cleanup that does NOT touch
the service fallback (the routes already pass a session). Recorded here as low-priority, not
acted on now.

Additionally, `commit_interaction` (and its dead fallback) has zero callers and is a deletion
candidate — recorded here, not acted on now (protocol method deletion needs a contract check).

## What we are not doing

- No removal of the `if self.db is None` fallback.
- No forced session injection / route middleware on `AsyncHistoryService`.
- No ChatEngine session-factory refactor to enable injection.

## Trigger to revisit

Reopen only if **ChatEngine is itself refactored to hold a session factory** (e.g. constructed
with an `async_sessionmaker`), at which point the no-db construction path disappears and the
fallback becomes removable. A re-suggestion to "force injection" does not meet this bar unless
it first provides the ChatEngine caller a session source. Separately, the route-inconsistency
cleanup and `commit_interaction` deletion can proceed anytime as independent low-risk work.
