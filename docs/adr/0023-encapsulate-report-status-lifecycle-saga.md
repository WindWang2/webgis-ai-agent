# Encapsulate report status-lifecycle saga in ReportService

**Status:** accepted

We will encapsulate the report status-lifecycle saga into `ReportService.create_and_generate(...)`, making the `create_report` route handler a thin wrapper that validates requests and maps `ReportSagaResult` to `ApiResponse`.

## Context

Architecture-review Candidate #4 identified that `app/api/routes/report.py:create_report` (~115 LOC) was performing a complex 2-phase transactional saga directly inside the API route handler:
1. Validate request format & session ownership.
2. Query `Conversation` & `Message` entities using the request's `AsyncSession`.
3. Create a `Report` DB record with status `"generating"` and commit.
4. Execute `db.expunge(report)` to detach the `Report` ORM instance from the primary session (preventing DB connection pool exhaustion during long-running PDF/HTML rendering).
5. Render the report via `ReportService.generate_report`.
6. Open a *second* DB session via `AsyncSessionLocal()` to update `Report` to terminal status (`"completed"` or `"failed"`).
7. Re-sync the detached ORM object and serialize the HTTP response.

Placing this database lifecycle & crash recovery logic in the route bloats the route handler and makes unit-testing status transitions impossible without setting up FastAPI dependencies or full HTTP mocks.

## Decision

1. **Deepen `ReportService`**: `ReportService` (`app/services/report_service.py`) becomes the sole owner of report generation and its status-lifecycle saga via a new method `create_and_generate(...)`.
2. **Two-Phase Session Management**: `create_and_generate` receives the active `db: AsyncSession` for Phase 1 (fetching messages, creating `generating` record, `expunge`) and `session_factory` (`AsyncSessionLocal`) for Phase 2 (updating terminal status to `"completed"` or `"failed"` after rendering).
3. **Structured Result Contract**: `create_and_generate` returns a `ReportSagaResult` dataclass (`success: bool`, `report: dict | None`, `message: str`, `err_code: ErrCode | None`).
4. **Thin Route Handler**: `create_report` in `app/api/routes/report.py` delegates directly to `ReportService.create_and_generate(...)` and maps the result to `ApiResponse.ok()` or `ApiResponse.fail()`.

## Consequences

- **Locality**: Report rendering, DB status lifecycle, and crash recovery are concentrated in `ReportService`.
- **Testability**: Status transitions (e.g. `generating` -> `completed` / `failed`, empty session handling) can be unit-tested directly against `ReportService` without HTTP routing overhead.
- **Connection Safety**: The 2-phase session detachment pattern (`db.expunge` + 2nd session update) remains intact, ensuring long rendering tasks (Jinja2/WeasyPrint) do not hold DB connections open.
