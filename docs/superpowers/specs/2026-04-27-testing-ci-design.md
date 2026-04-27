---
name: testing-ci-hardening
description: Fix existing tests, add unit + integration tests, harden CI pipeline
date: 2026-04-27
status: approved
---

# Testing Coverage + CI/CD Hardening Design

## Current State

- 24 test files exist, 4 async tests fail due to `pytest.mark.asyncio` configuration
- CI pipeline already has lint + test-backend + test-frontend + build + deploy stages
- `pytest.ini` has `asyncio_mode = auto` but tests use `@pytest.mark.asyncio` which conflicts
- No `pytest-cov` installed locally, no coverage threshold enforced
- CI only triggers on push to main, not on PRs
- Missing test coverage for: skill registry (.md), session data, chat engine streaming, skill API endpoints

## Design

### A. Fix Existing Tests + Configure Coverage

- Fix 4 failing async tests in `test_tool_registry.py` — remove explicit `@pytest.mark.asyncio` since `asyncio_mode = auto` handles it
- Add `pytest-cov` to requirements.txt (dev deps section)
- Add coverage config to `pytest.ini`: `--cov=app`, baseline threshold `fail-under=40`
- Add `.coveragerc` to exclude `__pycache__`, tests, migrations

### B. New Unit Tests (Core Modules)

**`tests/unit/test_skill_registry.py`** — Tests for `app/tools/skills.py`:
- `_parse_md_frontmatter`: valid frontmatter, missing frontmatter, invalid YAML, empty body
- `_load_md_skill`: loads .md file correctly, skips files without name
- `list_md_skills`: returns list after loading
- `get_md_skill`: returns skill data, returns None for unknown
- `load_skills`: handles both .py and .md files in skills dir

**`tests/unit/test_session_data.py`** — Tests for `app/services/session_data.py` (in-memory):
- `store` + `get`: roundtrip with cursor ref
- `set_alias` + `get` by alias
- `list_refs`: shows refs with aliases
- LRU eviction at capacity
- `set_map_state` + `get_map_state`
- `append_event` + `get_event_log` with MAX_EVENTS cap
- `clear_session`

**`tests/unit/test_coord_transform.py`** — Already exists, verify it passes

### C. New Integration Tests (API Level)

Use FastAPI `TestClient` for HTTP-level tests.

**`tests/integration/test_skill_api.py`**:
- `GET /api/v1/chat/skills` returns skill list
- `POST /api/v1/chat/stream` with `skill_name` returns SSE events

**`tests/integration/test_session_api.py`**:
- `GET /api/v1/chat/sessions/{id}/map-state` returns map state
- Session list and detail endpoints

### D. CI Pipeline Hardening

**Update `.github/workflows/ci.yml`:**
- Add `pull_request` trigger (all branches)
- Add coverage threshold: `pytest --cov-fail-under=40`
- Upload coverage report as artifact
- Add test summary job that gates merge

## File Structure

| File | Action |
|------|--------|
| `requirements.txt` | Add `pytest-cov` to testing section |
| `pytest.ini` | Add cov config, asyncio markers |
| `.coveragerc` | New: coverage exclusions |
| `tests/test_tool_registry.py` | Fix 4 async tests |
| `tests/unit/test_skill_registry.py` | New: skill registry unit tests |
| `tests/unit/test_session_data.py` | New: session data unit tests |
| `tests/integration/__init__.py` | New: package marker |
| `tests/integration/test_skill_api.py` | New: skill API integration tests |
| `tests/integration/test_session_api.py` | New: session API integration tests |
| `.github/workflows/ci.yml` | Add PR trigger, coverage threshold |

## What This Does NOT Include

- Frontend tests (separate effort)
- Load/stress testing
- Redis-specific tests (would need Redis instance)
- Database migration tests
- E2E browser tests
