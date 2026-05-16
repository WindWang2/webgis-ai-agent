# TODOS — Base Framework Review

Generated from /plan-eng-review on 2026-05-09.

## Architecture (D2–D7)
- [x] D2 — Enable Redis by default for session storage, with graceful fallback to in-memory
- [x] D3 — Require JWT_SECRET_KEY in production, fail startup if missing
- [x] D4 — Replace in-memory rate limiter with Redis-backed implementation
- [x] D5 — Move tool imports to lazy init function called from lifespan startup
- [ ] D6 — Refactor standard_response to use generic T model for type safety
- [ ] D7 — Add systematic logging for tool entry/exit with execution time

## Database & Refactoring (D8–D11)
- [ ] D8 — Migrate all API routes to Depends(get_db), services to db_session() context manager
- [x] D9 — Audit and narrow exception types in tool functions
- [ ] D10 — Implement soft-delete logic for layers and assets
- [x] D11 — Standardize error response format to match global exception handler

## Future (TBD)
- [ ] Implement robust retry logic for 3rd party APIs (Overpass, Nominatim)
- [ ] Add rate-limit monitoring and alerts

---

### Legend
- [ ] To Do
- [/] In Progress
- [x] Done
- [!] Blocked / Problematic

---
Note: This file is used by Gemini CLI to track framework hardening progress.
Only modify architecture-related tasks here. Domain features belong in `docs/task-board.md`.
