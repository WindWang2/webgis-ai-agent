# Ticket: ADR 落笔——引入决策与集成架构

**Label**: `wayfinder:grilling` | **Type**: HITL | **Status**: closed
**Blocked by**: ticket-003-duckdb-deckgl-sidequest.md, ticket-006-tool-exposure-strategy.md, ticket-007-minimal-poc.md

## Question

汇总全部证据与决策，落笔 ADR：是否引入 Whitebox WASM 工具箱、以何种集成架构（数据路径/结果回流/暴露策略）、边界（与 SpatialAnalyzer 共存约束、agent 接线为后续 effort 的方向性描述）；附 DuckDB-WASM/deck.gl 一句话结论。ADR 编号顺延 `docs/adr/`。

## Resolution

用户确认 accepted。ADR-0078 已落笔：[docs/adr/0078-geolibre-wasm-browser-algorithm-engine.md](../../docs/adr/0078-geolibre-wasm-browser-algorithm-engine.md) —— 有条件引入 `geolibre-wasm` 为浏览器端补充算法引擎；白名单类目+manifest 动态注册、服务端中介字节直传、算在浏览器/状态在服务端的回流、客户端预检护栏；agent 接线为后续 effort（方向：ToolRegistry `client` 执行策略）；供应链风险以 pin 精确版本+显式评审升级缓解；DuckDB-WASM/deck.gl 一句话结论随附。地图到站：8/8 票关闭，无遗留 frontier。
