# WebGIS AI Agent Release Notes - V3.6 (Iron Shield & Streaming Flow)

**Date**: 2026-05-18  
**Codename**: "Sentinel Stream"  
**Status**: Production Ready / Secure

## 🚀 Overview

V3.6 focuses on three core pillars: **System Robustness**, **Real-Time Visualization**, and **Hardened Security**. This release introduces a "Streaming Map" architecture that allows the frontend to render analysis results instantly, a refactored spatial task engine for 100% testability, and a comprehensive security sweep that purged sensitive credentials from the repository's history.

## 🛡️ Security & Integrity (Iron Shield)

### 1. Git History Scrubbing
Following a critical security alert, the entire Git history has been sanitized:
- **Credential Purge**: Used `git-filter-repo` to permanently remove `.env.prod`, `.env-2`, and SSL private keys from all historical commits.
- **Exposure Mitigation**: Identified and neutralized leaked AMap and Tianditu tokens.
- **Hardened Gitignore**: Updated `.gitignore` with recursive patterns (`**/.env*`, `*.key`) to prevent future accidental leaks in any subdirectory.

### 2. Robust Data Parsing
- **GeoJSON Auto-Repair**: Implemented a stack-based JSON repair mechanism in the core processor. The system can now gracefully handle truncated GeoJSON strings (common in high-latency LLM outputs), ensuring zero-failure rendering for interrupted streams.
- **Type-Safe Protocol**: Achieved 100% TypeScript consistency across the frontend, resolving all TSC and Lint errors in the `MapActionHandler` and real-time hooks.

## ⚡ Real-Time Intelligence (Streaming Flow)

### 1. "Streaming Map" Architecture
- **Parallel Rendering**: SSE streaming in `ChatEngine` delivers map layer updates the moment a spatial tool completes, allowing users to see data visualizations while the LLM is still generating its textual response. The separate WebSocket perception channel (`/ws/{session_id}`) handles bidirectional real-time map state sync.
- **Fetch-on-Demand (v2)**: Optimized large dataset transmission using a "Reference-then-Fetch" strategy. Large GeoJSON payloads are cached on the backend and retrieved by the frontend via authenticated REST calls, preventing WebSocket congestion.
- **Persistent Heartbeat**: Introduced a 30s WebSocket heartbeat mechanism to maintain stable connections through enterprise proxies and load balancers.

### 2. Precision Protocol v2 Enhancements
- **Local Admin Suite**: Added `get_local_admin_boundary` and `get_local_child_districts`. These tools bypass external APIs by querying local high-precision SHP libraries, offering sub-second response times for administrative analysis.
- **Hierarchical Intelligence**: The Agent can now navigate administrative hierarchies (Province -> City -> District) entirely offline.

## 🏗️ Engineering Excellence

### 1. Spatial Engine Refactoring
- **Logic/Task Decoupling**: Separated core GIS logic from Celery task boilerplate. This architectural change allows for direct unit testing of spatial operations, resulting in a **100% pass rate** for the regression test suite.
- **Error Transparency**: Refined error reporting for spatial statistics, providing clear feedback when data density is insufficient for advanced analysis (e.g., Moran's I).

### 2. Frontend Governance
- **Action Consistency**: Unified the `APPLY_LAYER_FILTER` command. The frontend now includes a human-friendly string parser (e.g., `"pop > 1000"`) that automatically converts to MapLibre GL expressions.
- **Code Health**: Resolved critical syntax corruption in the map components and standardized on `const` for immutable references.

---
*WebGIS AI Agent Team*
