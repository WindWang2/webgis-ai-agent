# Research & Architectural Design: Token Budget Guard, Parallel Asyncio Dispatch & Worker Diffing Architecture (Ticket #250)

## 1. Executive Summary

This document specifies the architectural design for performance optimizations and pipeline enhancements in Ticket #250, addressing three critical system bottlenecks:
1. **Token Budget Guard (`app/services/llm_result_formatter.py`)**: Preventing context window token exhaustion and reducing LLM inference latency when handling large spatial query payloads.
2. **Parallel Asyncio Dispatch (`app/services/chat/execution_engine.py`)**: Replacing sequential tool invocation loops with `asyncio.gather()` concurrent execution for multi-tool LLM turns.
3. **Worker Diffing Architecture (`frontend/lib/mapspec-compiler/reconciler.ts`)**: Offloading MapSpec AST diffing off the main UI thread via Web Worker RPC with graceful SSR/Node fallbacks.

---

## 2. Component 1: GeoJSON Property Slimming & Token Budget Guard

### 2.1 Problem Analysis
When spatial tools (e.g. `query_spatial_db`, `fetch_poi_radius`, `buffer_analysis`) return thousands of GeoJSON features, sending raw coordinates or uncompressed property dictionaries to the LLM context window causes:
- Context token overflow (>32k tokens), triggering model context errors or prompt truncation.
- Extremely high API latency and cost per turn.
- LLM confusion from verbose property payloads.

### 2.2 Property Slimming & Cursor Reference Injection Rules
To balance LLM context economy with analytical usefulness:
1. **Sample Feature Ceiling**: Limit raw feature samples to **max 5 sample features** (`SAMPLE_FEATURES = 5`).
2. **Property Key & Value Truncation**:
   - Limit sample property keys to top 15 most informative keys (`PROPERTY_KEYS_MAX = 15`).
   - Truncate individual property string values to 80 characters (`VALUE_MAX_CHARS = 80`).
3. **Cursor Reference Injection (`ref:<ref_id>`)**:
   - Raw GeoJSON geometries are stored directly in `session_data_manager` and assigned a unique `ref_id` (e.g. `ref:geojson_a1b2c3d4`).
   - The LLM context receives only the metadata summary + explicit instruction:
     `"如需进一步空间分析，请调用工具并将 geojson 参数设为 \"ref:<ref_id>\"。"`
   - Subsequent spatial tools resolve `ref:<ref_id>` from server session memory without routing massive payloads through the LLM.

---

## 3. Component 2: Parallel Asyncio Dispatch for Independent Tool Calls

### 3.1 Sequential Bottleneck vs. Concurrent Execution
When an LLM returns multiple tool calls in a single turn (e.g. `[query_poi, fetch_weather, get_traffic]`), executing them sequentially takes $T = \sum t_i$. By employing `asyncio.gather(*tasks, return_exceptions=True)`, execution time drops to $T = \max(t_i)$, delivering up to 60-75% latency reduction for multi-tool rounds.

### 3.2 Concurrent Tool Execution Architecture & SSE Heartbeats
- Independent tool calls are packed into `asyncio.create_task()` execution handles.
- `asyncio.gather(*tasks, return_exceptions=True)` executes all tool calls concurrently.
- Real-time SSE events (`step_start`, `step_result`, `step_error`) are multiplexed safely via an async queue.

---

## 4. Component 3: Worker Diffing Architecture (`reconciler.worker.ts`)

### 4.1 Architecture Overview
To keep MapSpec AST diffing (`diffSpecs`) from blocking the main UI thread during map updates:
1. `frontend/lib/mapspec-compiler/reconciler.worker.ts` handles AST diffing off-thread.
2. `WorkerReconcilerBridge` manages communication and RPC promises.
3. Fallback mechanism uses synchronous `diffSpecs` on main thread if `typeof Worker === 'undefined'` (e.g. Next.js SSR / Node test runner).
