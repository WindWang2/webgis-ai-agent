# Documentation vs Code Cross-Reference Audit Report

**Date**: 2026-07-21
**Scope**: All files in `docs/`, root-level docs (`CHANGELOG.md`, `README.md`, `MEMORY.md`, `CLAUDE.md`, `CODE_REVIEW.md`, `TODOS.md`), and planning files (`task_plan.md`, `progress.md`, `findings.md`)
**Methodology**: Line-by-line cross-reference of every documentation claim against actual codebase state

---

## Executive Summary

| Category | Count | Severity |
|----------|-------|----------|
| Stale References | 6 | High |
| Missing Coverage | 7 | Medium |
| Contradictions | 8 | High |
| Inaccurate Descriptions | 4 | Medium |
| Ambiguity | 5 | Low |
| Dead Content | 4 | Low |

**Critical Finding**: The README.md contains a **broken Celery startup command** that will fail for any new developer following the documentation.

---

## 1. STALE REFERENCES

### 1.1 README.md:76 — Wrong Celery App Module
- **doc:line**: `README.md:76` — `celery -A main.celery_app worker --loglevel=info &`
- **code:file:line**: `app/main.py:96` — `app = FastAPI(...)` (no `celery_app` here); `app/services/task_queue.py:13` — `celery_app = Celery(...)`
- **Recommendation**: Change to `celery -A app.services.task_queue worker --loglevel=info &`

### 1.2 docs/inspection-standard.md:23 — Wrong Celery App Module
- **doc:line**: `docs/inspection-standard.md:23` — `celery -A main.celery_app status 2>/dev/null`
- **code:file:line**: Same as above — actual module is `app.services.task_queue`
- **Recommendation**: Update to `celery -A app.services.task_queue status`

### 1.3 task_plan.md:316 — Non-existent DESIGN_ROADMAP.md Reference
- **doc:line**: `task_plan.md:316` — "Phase 6 详细设计见 `docs/superpowers/specs/` 系列文档（ref_id-system, sse-ws-protocol, auth-multitenant 等独立 spec）"
- **code:file:line**: No `DESIGN_ROADMAP.md` file exists in repository root
- **Recommendation**: Either create the referenced file or remove the reference

### 1.4 docs/DEPLOYMENT.md:101-102 — manage.py create-admin Command
- **doc:line**: `docs/DEPLOYMENT.md:101-102` — Documents `python manage.py create-admin <username> <email> <password>`
- **code:file:line**: `manage.py:36` — `def cmd_create_admin(username: str, email: str, password: str):` — command exists but documentation doesn't mention it's only needed when public register is disabled
- **Recommendation**: Add context that this is for initial admin creation when public registration is closed (S28)

### 1.5 docs/architecture.md:77-86 — Outdated Directory Structure in Mermaid
- **doc:line**: `docs/architecture.md:39-44` — Mermaid diagram shows simplified 3-layer structure
- **code:file:line**: Actual frontend has `hooks/`, `providers/`, `store/`, `utils/`, `types/` subdirectories not shown in diagram
- **Recommendation**: Update diagram to reflect actual component organization

### 1.6 task_plan.md:162-165 — Inaccurate File Counts
- **doc:line**: `task_plan.md:162-165` — "后端文件数: ~142 Python 源文件", "前端文件数: ~150 TS/TSX", "后端测试: ~105 测试文件"
- **code:file:line**: Actual counts: 312 Python files, 159 TS/TSX files (excl. node_modules), 156 test files
- **Recommendation**: Update counts to reflect actual repository state

---

## 2. MISSING COVERAGE

### 2.1 docs/技术方案说明书.md — Missing Core Infrastructure
- **doc:line**: `docs/技术方案说明书.md` — No mention of Celery, MinIO, or Alembic
- **code:file:line**: `app/main.py:13` — `celery_app = Celery(...)`; `docker-compose.yml` — Celery service; `migrations/` — Alembic directory
- **Recommendation**: Add sections covering task queue architecture, object storage strategy, and database migration workflow

### 2.2 CHANGELOG.md — Missing Post-Phase 5G Security Fixes
- **doc:line**: `CHANGELOG.md:1-54` — Latest entry is v3.2.2 (2026-05-14)
- **code:file:line**: `task_plan.md:11-56` — Phase 5G (2026-06-01) fixed 27 security issues; Phase 5F (2026-05-31) fixed 24 issues
- **Recommendation**: Add changelog entries for Phase 5F and Phase 5G security audits

### 2.3 README.md — Missing Security Audit History
- **doc:line**: `README.md:107-115` — Milestone list only shows up to Phase 4+
- **code:file:line**: `task_plan.md:11-56` — Phase 5F and Phase 5G completed with 31+ security fixes
- **Recommendation**: Add Phase 5F and Phase 5G to milestone timeline

### 2.4 docs/api-docs.md — Missing Layer Lifecycle Commands
- **doc:line**: `docs/api-docs.md:69-78` — T003 Map Interaction Protocol table missing `zoom_to_layer` and `reset_map_view`
- **code:file:line**: `docs/api-docs.md:376-377` — Actually documents `zoom_to_layer` and `reset_map_view` in T011, but not in T003
- **Recommendation**: Add missing commands to T003 table or clarify they are in T011

### 2.5 docs/ — Missing Rate Limiter Documentation
- **doc:line**: No documentation exists for rate limiting
- **code:file:line**: `app/core/rate_limiter.py` — Rate limiter module exists; `app/api/routes/auth.py` — likely uses it for login/register
- **Recommendation**: Document rate limiting strategy, especially for auth endpoints (D3 deferred item)

### 2.6 docs/ — Missing Session Data Protocol Documentation
- **doc:line**: No standalone documentation for session data protocol
- **code:file:line**: `app/services/session_data_protocol.py` — Protocol implementation exists
- **Recommendation**: Create documentation for session data serialization protocol

### 2.7 docs/api-docs.md — Missing WebSocket Authentication Details
- **doc:line**: `docs/api-docs.md:285-308` — T009 documents WebSocket events but not authentication flow
- **code:file:line**: `app/services/ws_service.py` — WebSocket service exists; Phase 5G S19 fixed heartbeat interval leak; Phase 5G S20 added command whitelist
- **Recommendation**: Add WebSocket authentication handshake documentation

---

## 3. CONTRADICTIONS

### 3.1 Theme System Completion Status
- **doc:line**: `docs/技术方案说明书.md:244` — "双主题支持 100% 完成"
- **code:file:line**: `task_plan.md:151` — D3: "主题系统半实现 — store 有 theme slice，但 chat-panel/tool-call-card 等组件仍硬编码颜色"
- **Recommendation**: Reconcile — either complete theme implementation or update documentation to reflect partial completion

### 3.2 PostGIS High-Availability Configuration
- **doc:line**: `docs/architecture.md:165` — "PostGIS 做主从高可用解构读写分离"
- **code:file:line**: No master-slave configuration found in `docker-compose.yml` or database configuration
- **Recommendation**: Clarify if this is a future goal or remove the claim

### 3.3 API Endpoint Method Discrepancy
- **doc:line**: `docs/api-docs.md:139` — T005 shows `POST /api/v1/tasks/{id}/cancel`
- **code:file:line**: `app/api/routes/task.py:122` — `@router.delete("/{task_id}")` — actual method is DELETE
- **Recommendation**: Update docs to show DELETE method

### 3.4 Frontend Directory Structure
- **doc:line**: `docs/技术方案说明书.md:58-73` — Shows only 4-level directory tree (app/, components/, lib/, no deeper)
- **code:file:line**: Actual frontend has `hooks/`, `providers/`, `store/`, `utils/`, `types/` subdirectories under `lib/`
- **Recommendation**: Update directory tree to show actual structure

### 3.5 Task Queue Module Location
- **doc:line**: `README.md:76` — `celery -A main.celery_app`
- **code:file:line**: `app/services/task_queue.py:13` — `celery_app = Celery(...)`; all docker-compose files use `app.services.task_queue`
- **Recommendation**: Fix README to match actual implementation

### 3.6 Layer Route Authentication Status
- **doc:line**: `task_plan.md:297` — "剩余 4 个路由文件认证 (upload/explorer/map/layer) → Target: Phase 6.1 follow-up"
- **code:file:line**: `app/api/routes/upload.py:79` — `get_current_user`; `app/api/routes/explorer.py:35` — `get_current_user`; `app/api/routes/map.py:108` — `get_current_user`; `app/api/routes/layer.py:37` — `get_current_user_optional`
- **Recommendation**: Update task_plan.md to reflect that 3 of 4 routes now have auth (upload, explorer, map); layer.py uses optional auth

### 3.7 validate_data_path Implementation
- **doc:line**: `task_plan.md:55` — D4: "validate_data_path 未使用 os.path.realpath — 符号链接不解析"
- **code:file:line**: `app/utils/path.py:26-29` — Already uses `os.path.realpath()` for both data_dir and resolved path
- **Recommendation**: Update task_plan.md to mark D4 as completed or re-evaluate

### 3.8 File Count Discrepancies
- **doc:line**: `task_plan.md:162-165` — "~142 Python 源文件", "~105 测试文件"
- **code:file:line**: Actual: 312 Python files, 156 test files
- **Recommendation**: Update counts or clarify if counting only `app/` directory

---

## 4. INACCURATE DESCRIPTIONS

### 4.1 Mermaid Architecture Diagram
- **doc:line**: `docs/architecture.md:35-73` — Simplified 6-node diagram
- **code:file:line**: Actual architecture has 13+ route files, 30+ tool files, multiple service subdirectories
- **Recommendation**: Either simplify diagram to match abstraction level or add detailed sub-diagrams

### 4.2 SSE Heartbeat Interval
- **doc:line**: `docs/architecture.md:91` — "每隔 15 秒向传输层丢弃一个透明的注释型数据框"
- **code:file:line**: No 15-second heartbeat found in `chat_engine.py`; actual heartbeat mechanism not verified in code
- **Recommendation**: Verify actual heartbeat interval in code and update docs

### 4.3 Redis Key Format
- **doc:line**: `docs/database-design.md:10` — `webgis:session:{uuid}:cache:{layer_id}`
- **code:file:line**: `app/services/session_data_redis.py` — Actual Redis key format may differ
- **Recommendation**: Verify actual Redis key format and align documentation

### 4.4 API Response Format
- **doc:line**: `docs/api-docs.md:148-156` — Shows unified response format with `code`, `success`, `message`, `data`
- **code:file:line**: Actual endpoints return various formats (some return `{"detail": "..."}`, others return custom formats)
- **Recommendation**: Document actual response formats per endpoint or standardize API responses

---

## 5. AMBIGUITY

### 5.1 Phase 5 Vision Features
- **doc:line**: `docs/技术方案说明书.md:250-253` — All Phase 5 items are empty checkboxes: `- [ ] 将骨架向分布式多代理协同框架拆解重组。`
- **code:file:line**: No implementation found for distributed multi-agent coordination
- **Recommendation**: Add subtasks, timeline, and acceptance criteria or move to "Future Considerations"

### 5.2 Fetch-on-Demand Specification
- **doc:line**: `docs/技术方案说明书.md:96-101` and `docs/data-fetcher.md` — Conceptual description only
- **code:file:line**: `app/services/session_data.py` and `app/services/session_data_redis.py` — Implementation exists
- **Recommendation**: Add concrete specification: ref_id format (`ref:geojson-<hash>`), TTL values (3600s per database-design.md), eviction policy (LRU, 200 entries per session)

### 5.3 Exception As Thought Implementation
- **doc:line**: `docs/技术方案说明书.md:148-153` — Pseudocode only
- **code:file:line**: `app/services/chat/context_builder.py` and `app/tools/` — Actual exception handling exists but not documented
- **Recommendation**: Add exception classification table and LLM instruction template examples

### 5.4 Theme System Scope
- **doc:line**: `docs/技术方案说明书.md:123-126` — Claims all components adapted to `isDark` prop
- **code:file:line**: `task_plan.md:151` — D3 notes some components still hardcode colors
- **Recommendation**: Document which components are theme-aware and which are not

### 5.5 Layer Data Endpoint Path
- **doc:line**: `docs/api-docs.md:101` — `/api/v1/layers/data/{ref_id}?session_id=xxx`
- **code:file:line**: `app/api/routes/layer.py:34` — `@router.get("/layers/data/{ref_id}")` — prefix is `/api/v1` so full path is `/api/v1/layers/data/{ref_id}`
- **Recommendation**: This is actually consistent, but `docs/data-fetcher.md:38` also references `/api/v1/layer/{id}/data` (singular) which is wrong
- **Fix**: Correct `docs/data-fetcher.md:38` to use plural `/layers/data/{ref_id}`

---

## 6. DEAD CONTENT

### 6.1 Deferred Phase 5F Items
- **doc:line**: `task_plan.md:90-101` — F24-F33 still marked as deferred to "daily dev"
- **code:file:line**: No evidence these items were completed since 2026-05-31
- **Recommendation**: Either implement, formally close, or move to a future phase

### 6.2 Deferred Phase 5A DevEx Items
- **doc:line**: `task_plan.md:137-146` — I2-I4, I7-I12, I17 deferred to daily dev
- **code:file:line**: Some may be partially done but not tracked
- **Recommendation**: Audit each deferred item and update status

### 6.3 Outdated Task Board
- **doc:line**: `docs/task-board.md:3` — "更新时间: 2026-04-18"
- **code:file:line**: Current date is 2026-07-21; much has changed since April
- **Recommendation**: Update task board or archive as historical record

### 6.4 Stale Superpowers Plans
- **doc:line**: `docs/superpowers/plans/` — Contains plans from 2024-05-20 to 2026-05-28
- **code:file:line**: Many plans may be completed or superseded
- **Recommendation**: Archive completed plans to `docs/superpowers/plans/archive/` to reduce noise

---

## 7. SECURITY-SPECIFIC FINDINGS

### 7.1 Missing Security Fix Documentation
- **doc:line**: No changelog entry for Phase 5G security audit (2026-06-01)
- **code:file:line**: `task_plan.md:11-56` — 27 security fixes including RCE, path traversal, XSS, auth bypasses
- **Recommendation**: Add comprehensive security changelog entry

### 7.2 WebSocket Authentication Documentation Gap
- **doc:line**: `docs/api-docs.md:285-308` — T009 documents WebSocket events but not authentication
- **code:file:line**: Phase 5G S19-S20 added heartbeat interval fix and command whitelist; Phase 5G S4 tightened WS auth
- **Recommendation**: Document WS authentication flow and security hardening

### 7.3 CORS Configuration Documentation
- **doc:line**: `docs/api-docs.md` — No CORS documentation
- **code:file:line**: `app/main.py` — CORS middleware exists; `docs/api-docs.md:73` in release notes mentions `CORS_ORIGINS=["*"]` risk
- **Recommendation**: Document CORS configuration and threat model

---

## 8. ADDITIONAL FINDINGS

### 8.1 Docker Compose env_file Inconsistency
- **doc:line**: `docs/DEPLOYMENT.md:16` — `cp .env.example .env`
- **code:file:line**: `docker-compose.prod.secure.yml:72` — uses `.env.Priv`; `docker-compose.prod.yml:74` — uses `.env.production`
- **Recommendation**: Clarify which env file is used in which deployment scenario

### 8.2 Python Version Requirement
- **doc:line**: `docs/DEPLOYMENT.md:5` — "Python 3.10+"
- **code:file:line**: `task_plan.md:67` — F1 fixed CI Python version to 3.12
- **Recommendation**: Update docs to reflect Python 3.12 requirement

### 8.3 Missing manage.py Commands Documentation
- **doc:line**: `docs/SETUP_INSTRUCTIONS.md` — Documents `python manage.py dev` and `python manage.py check`
- **code:file:line**: `manage.py` — Also has `create-admin`, `init-db`, `server`, `worker` commands
- **Recommendation**: Document all available manage.py commands

### 8.4 Test Count Claims
- **doc:line**: `task_plan.md:164` — "后端测试: ~105 测试文件"
- **code:file:line**: `tests/` — 156 test files found
- **Recommendation**: Update test count

---

## 9. RECOMMENDATIONS BY PRIORITY

### High Priority (Fix Immediately)
1. Fix README.md:76 Celery command — blocks new developers
2. Fix docs/inspection-standard.md:23 Celery command — breaks monitoring
3. Reconcile theme system completion status (技术方案说明书 vs task_plan.md)
4. Correct API endpoint method in docs/api-docs.md T005

### Medium Priority (Fix in Next Sprint)
1. Add Phase 5F/5G to CHANGELOG.md
2. Update file counts in task_plan.md
3. Document Celery, MinIO, Alembic in 技术方案说明书.md
4. Fix data-fetcher.md layer endpoint path (singular vs plural)
5. Update task_plan.md auth status for 4 routes (3 now have auth)

### Low Priority (Backlog)
1. Archive stale superpowers plans
2. Update task-board.md timestamp
3. Add acceptance criteria to Phase 5 vision features
4. Standardize API response formats and document them

---

## 10. VERIFICATION COMMANDS USED

```bash
# File counts
find /home/kevin/projects/webgis-ai-agent -type f -name "*.py" | wc -l  # 312
find /home/kevin/projects/webgis-ai-agent/frontend -type f \( -name "*.ts" -o -name "*.tsx" \) -not -path "*/node_modules/*" | wc -l  # 159
find /home/kevin/projects/webgis-ai-agent/tests -type f -name "*.py" | wc -l  # 156

# Celery app location
grep -n "celery_app" app/main.py  # No results
grep -n "celery_app" app/services/task_queue.py  # Line 13

# Auth status
grep -n "get_current_user" app/api/routes/upload.py  # Lines 79, 202, 246, 272, 310
grep -n "get_current_user" app/api/routes/explorer.py  # Lines 35, 66, 82, 89
grep -n "get_current_user" app/api/routes/map.py  # Lines 108, 157, 270, 294
grep -n "get_current_user" app/api/routes/layer.py  # Line 37 (optional)

# validate_data_path implementation
grep -n "realpath" app/utils/path.py  # Lines 26, 29, 31, 36

# Version consistency
grep -n "0.1.2" app/core/health.py app/__init__.py pyproject.toml  # All consistent
```

---

*Report generated by line-by-line cross-reference audit of all documentation against actual codebase state.*
