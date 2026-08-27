# Ticket: WASM 结果回流——MapSpec 写入与栅格渲染决策

**Label**: `wayfinder:grilling` | **Type**: HITL | **Status**: closed
**Blocked by**: ticket-002-wasm-io-formats.md, ticket-004-data-path-decision.md

## Question

浏览器端算子的结果如何成为地图图层并进入会话状态：谁写 MapSpec（客户端直写 vs 经服务端 Intent）？矢量结果要不要回传 SessionStore/ref_id（LLM 后续轮次可见性）？栅格输出没有现成的客户端渲染路径（现状 `raster_tile_service` 服务端切片）——浏览器端如何渲染 WASM 栅格产物？

## Resolution

**算在浏览器，状态在服务端。** 矢量结果：浏览器算完 → GeoJSON `POST /upload` 回传 → 得 ref → 经 `/sessions/{id}/mapspec/mutations` 薄暴露 `upsert_layer` intent（复用 `lifecycle_engine.py` 已有的 `UpsertLayerIntent`，属薄改动）挂层——LLM 后续轮次可见、会话恢复完整、Fetch-on-Demand 不动。栅格结果：COG bytes 回传 upload → 图层引用现成 `GET /layers/data/{ref_id}/raster-tiles/{z}/{x}/{y}.png`（`raster_tile_service` 服务端切片），前端渲染管线零改动。

出局/落雾：客户端内联直挂（Inline Carrier）不作持久图层（LLM 不可见、恢复即丢、绕开单一写入边界），至多为执行中的临时预览态；浏览器内自切瓦片（`write_pmtiles`/`raster_to_tiles`）落雾为 v2 优化。MapSpec 写入保持服务端单一边界（SessionLockRegistry/checkpoint/fingerprint 机制全保留）。
