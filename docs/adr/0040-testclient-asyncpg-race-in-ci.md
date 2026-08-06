# 40. TestClient + asyncpg Race in CI

Date: 2026-08-06

## Status

Accepted

## Context

Routing that uses the async DB layer against real Postgres failed in CI with `asyncpg.exceptions...: another operation is in progress`. The trigger was the combination of httpx's synchronous `TestClient` (which drives the ASGI app from a threadpool) with asyncpg-backed connections: asyncpg connections are bound to the event loop that created them, and TestClient's threadpool re-enters the same connection across loops/requests, so concurrent operations on one connection race.

This did not reproduce locally: the local dev default is SQLite with a sync DB fallback (`app/core/database.py`), so the asyncpg loop-bound connection issue only manifested in CI against the real Postgres instance.

## Decisions

1. **Use `httpx.AsyncClient` for async DB routes**: tests exercise the app via `httpx.AsyncClient(transport=ASGITransport(app=app))` instead of the sync `TestClient`, keeping the request and the async DB operations on the same event loop and eliminating the threadpool/loop mismatch.

2. **Override the async DB dependency per test**: create a fresh async engine per test via `create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'templates.db'}")` and override `get_async_db` (from `app/core/database.py`) through `app.dependency_overrides[get_async_db] = override_get_async_db`. The engine must be **file-based, not `:memory:`** — SQLite `:memory:` is per-connection, so a write in one session would be invisible to a later read in another session within the same test; a per-test file in `tmp_path` makes all sessions in one test see the same data.

3. **Reference implementation**: the pattern was established in `tests/test_critical_auth_hardening.py` (isolated sqlite + real router registration) and extended in `tests/unit/test_templates_api.py` (per-test `setup_db` fixture that builds the engine, creates tables via `Base.metadata.create_all`, registers the override, and clears `app.dependency_overrides` + disposes the engine on teardown).

## Consequences

- **CI-stable async route tests**: the asyncpg loop-bound race is gone because the client and the DB share one event loop, and tests no longer touch real Postgres at all (isolated sqlite per test).
- **Real cross-module wiring is still exercised**: tests register the real FastAPI app and routers (`ASGITransport(app=app)`), so routing, middleware, and dependency wiring are covered even though the DB is swapped.
- **Limitation — direct-DB paths are not covered by the override**: code paths that query the database directly rather than through the `get_async_db` dependency cannot see the sqlite-overridden data (e.g., the AI-tool `list_templates` path in `tests/unit/test_templates_api.py`). Such paths are covered separately against the real DB (e.g., `test_tools_templates.py`).
- **Fixture hygiene**: `app.dependency_overrides.clear()` in fixture teardown prevents the sqlite override from leaking into other tests in the session.
