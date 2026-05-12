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

## v3.2.1 — Perception & Hardening Patch

**Date**: 2026-05-12
**Status**: Patch release on top of V3.2

### Perception Pipeline Fixes

The Agent's `[环境感知]` system message could go stale or drop entirely when users panned the map on a fresh session, causing the LLM to fabricate viewport coordinates ("地图显示上海" while the map was over Africa). Three causes, three fixes:

- **Layer state no longer wiped on every chat turn.** `frontend/app/page.tsx` now serializes the actual `useHudStore.layers` into the chat request body (previously hardcoded `layers: []`).
- **Session-aware WebSocket reconnect.** The chat stream now carries `session_id` on the `task_start` event; the frontend captures it and reconnects WebSocket so subsequent pan/zoom/layer events reach the backend.
- **No more (0,0) coordinate fallback.** `_get_map_state_summary` in `app/services/chat_engine.py` now emits an explicit "视口: 未知" line when the frontend hasn't reported state, so the LLM stops mistaking missing data for the Gulf of Guinea.

A new `[ENV-INJECT]` debug log records the exact env block sent to the LLM each turn, gated behind `logger.debug`.

### LLM Reasoning Tokens

`token` events now carry an `is_reasoning` flag so the frontend can split thinking content from answer content. Supports MiniMax-M2.7 and DeepSeek-V3 reasoning streams.

The new `frontend/components/chat/collapsible-think.tsx` renders a collapsible "思考过程" panel above the answer when reasoning tokens are present.

### CORS Threat Model

`CORS_ORIGINS=["*"]` with `allow_credentials=True` is now documented inline in `app/main.py`. The pattern is intentional and accepted as risk under the assumption that the API is fronted by a trusted gateway or uses non-cookie credentials. Tighten before any public, cookie-authenticated deployment.

### Misc

- `dump.rdb` and `*.rdb` added to `.gitignore` so local Redis snapshots stop following commits around.
- `validate_data_path` docstring now spells out the symlink threat model (abspath-based, not realpath — fine for trusted deploys).

---
*WebGIS AI Agent Team*
