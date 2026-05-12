# WebGIS AI Agent Release Notes - V3.2 (CNS Hardening)

**Date**: 2026-05-12  
**Codename**: "Sentinel Shield"  
**Status**: Stable / Production Ready

## 🚀 Overview

V3.2 is a major stability and security release focusing on the **Agent CNS (Central Nervous System)**. This release transitions the project from a feature-prototype state to a production-hardened framework by enforcing architectural invariants, closing security vulnerabilities, and dramatically improving the developer experience.

## 🛡️ Security & Stability

### 1. Path Traversal Shield (Vulnerability Fixed)
- **Problem**: Tools were vulnerable to directory traversal attacks, allowing potentially malicious LLM prompts to access system files.
- **Solution**: Implemented `app/utils/path.py` with `validate_data_path()`. All file-based operations (NDVI, Zonal Stats, etc.) are now strictly confined to `./data` and `./tmp`.

### 2. SSE Stream Integrity
- **Problem**: Inconsistent SSE event generation and lack of serialization safety led to stream corruption.
- **Solution**: Standardized all SSE communication via a centralized `sse_event` utility. This ensures mandatory JSON serialization and safe handling of Pydantic models.

### 3. Dependency Conflict Resolution
- **Problem**: `starlette 1.0.0` was incompatible with FastAPI's `APIRouter`, causing startup failures.
- **Solution**: Pinned `starlette<0.39.0,>=0.37.2` in `requirements.txt`.

## 💻 Developer Experience (DevEx 2.0)

### 1. Unified Management CLI
- **`python manage.py dev`**: One-command orchestration of the full stack (FastAPI + Celery + Next.js).
- **`python manage.py check`**: Deep infrastructure diagnostic probe (Redis, DB, Worker, LLM connectivity).

### 2. Modern Tooling
- Integrated `rich` for professional, high-signal CLI output.
- Updated `docs/SETUP_INSTRUCTIONS.md` with the new streamlined workflow.

## ✅ Quality Assurance

- **Total Tests**: 307
- **Pass Rate**: 100%
- **Coverage Highlights**: 100% coverage on core security and communication utilities.
- **Key Fixes**: Resolved logic regressions in the Explorer geocoding task chain and fixed "Smart Quote" syntax errors in spatial services.

## 🗺️ Roadmap Update

- [x] Phase 4: 主控中枢与算子 MCP 化 (V3.2)
- [x] Phase 4+: V2 UI 重新设计与稳定化
- [ ] Phase 5: "超我"演进与星辰大海 (Next: Distributed Multi-Agent Coordination)

---
*WebGIS AI Agent Team*
