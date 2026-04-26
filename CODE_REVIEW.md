# Code Review & Integrity Standards (V3.2 Core)

**Date**: 2026-04-27
**Scope**: Full Stack (FastAPI + Next.js + Celery)

This document establishes the **V3.x Engineering Invariants**. All future Pull Requests MUST be audited against these rules to prevent system regression into the V1.0 bottleneck states.

---

## 🛑 V2.0 Architectural Invariants (MANDATORY AUDIT)

### 1. 计算隔离红线 (Strict Compute Isolation)
- **Rule**: 主 FastApi 路由（特别是 `chat_engine`）内部绝对禁止直接执行任何耗时大于 `500ms` 的空间运算（如 `gpd.sjoin`, `shapely.buffer` 对多图元集）。
- **Audit**: 审查所有 Tool 注册点。超限工具必须带有 `@celery_task` 装饰器，强制由 Worker 节点执行，通过 Redis 回传 `task_id` 或 `ref_id`。

### 2. 禁运大型 GeoJSON 红线 (Zero Big Data Context)
- **Rule**: 大语言模型 (Claude) 的上下文 `tool_result` 中，绝对禁止出现数组长度超过几十个特征点的全量 GeoJSON。
- **Audit**: 确保在向 `Message` 队列追加结果前，大型地理载荷必须已被剥离为 `ref_id` 形式（利用 `session_data.py` 缓存），仅抛出描述摘要。

### 3. SSE 心跳防护机制 (Keep-Alive Integrity)
- **Rule**: 在任何存在阻塞等待外部组件（如等 Celery 计算）的异步切片点，必须有周期性的 `yield` 操作向前端推送无害内容。
- **Audit**: 审查 `chat_engine.py` 的轮询 `await asyncio.sleep()`。必须同步执行 `yield "data: [HEARTBEAT]\n\n"`，从而封杀 `ERR_CONNECTION_RESET`。

### 4. 异常自理阻断 (No Naked Exceptions)
- **Rule**: Tool 执行抛出的逻辑异样（如地理拓扑越界、坐标系无法相交），严禁向上抛 `500 Internal Error`。
- **Audit**: 空间核心库最外层需实施 `try-except` 包裹，并在 `except` 中生成引导性自然语言建议反哺到大模型认知上下文中（"Exception As Thought"）。

### 5. 前端渲染并发锁 (Frontend isUpdatingRef)
- **Rule**: 任何引起 `MapLibre` 图层增删、显隐状态变化的 `useEffect` 或 `renderLayers`，必须受到互斥锁保护。
- **Audit**: 在 `map-panel.tsx` 中，`isUpdatingRef.current = true` 与 `try...finally` 配对使用验证。禁止重排引发 React 的渲染死循环。

### 6. SSE 事件格式统一 (SSE Event Format Consistency)
- **Rule**: 所有 SSE 事件必须通过 `_sse_event()` 辅助函数生成，禁止直接使用 `f"event: ...\ndata: ...\n\n"` 格式。
- **Audit**: `_sse_event()` 包含序列化安全保护（`_serialize_sse_data`），防止 JSON 编码失败导致流中断。

### 7. 双通道感知同步 (Dual-Channel Perception Sync)
- **Rule**: 前端必须同时通过 SSE `map_state` 参数和 WebSocket 感知通道上报地图状态。
- **Audit**: `page.tsx` 的 `handleSend` 必须传递 `mapState`；图层操作必须通过 `pushPerception()` 同步到后端。

---

## 📜 Historical Patch Records (V1.0)

*(The following are historical audits & vulnerability patches applied during transitioning from V1.0 to V1.1)*

### CRITICAL (5) — Fixed
| ID | File:Line | Description | Status |
|----|-----------|-------------|--------|
| C-1 | `history_service.py:48` | `save_message` references undefined `tool_calls` variable — `NameError` on every DB write | **FIXED** |
| C-2 | `chat.py:85,102` | Both session endpoints call `engine._history` which doesn't exist on `ChatEngine` — `AttributeError` crashes | **FIXED** |
| C-3 | `spatial_analyzer.py:389` | `gdf.query(query)` with user-controlled input — Pandas eval RCE risk | **FIXED** |
| C-4 | `spatial_tasks.py:341` | `raster_path` from LLM tool args passed directly to `rasterio.open()` — path traversal | **FIXED** |

### HIGH (7) — Fixed
| ID | File:Line | Description | Status |
|----|-----------|-------------|--------|
| H-1 | `chat_engine.py:259,346` | Fire-and-forget `run_in_executor` futures silently drop exceptions | **FIXED** |
| H-2 | `chat.py:88,105` | Session retrieval used `engine._history` instead of creating `HistoryService` instances | **FIXED** |
| H-3 | `chat.py:168` | `GET /chat/tools/results` leaks all recent tool results unauthenticated | **FIXED** |
| H-4 | `map-panel.tsx:133` | Layer ID hyphen-split bug leaves orphaned MapLibre GL sources | **FIXED** |
| H-5 | `page.tsx:67` | Lat/lon swap in bbox center calculation → map flies to wrong location | **FIXED** |
| H-6 | `spatial_analyzer.py` | Nearest-neighbor distances in geographic degrees, not meters | **FIXED** |
| H-7 | `chat_engine.py:155` | Synchronous DB call in `_get_or_create_session` blocks event loop | **FIXED** |

### MEDIUM / LOW — Fixed
- Silent map error suppression (`map-panel.tsx:135`)
- Stale legend state on prop change (`thematic-legend.tsx`)
- Heatmap OOM risk for large feature sets
- Multi-geometry centering gap
- Hardcoded external CDN texture URL (`page.tsx:131`)

*(Historical log ends here)*
