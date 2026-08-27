# Ticket: DuckDB-WASM Spatial 与 deck.gl 顺带摸底

**Label**: `wayfinder:research` | **Type**: AFK | **Status**: closed
**Blocked by**: —

## Question

DuckDB-WASM Spatial（浏览器端空间 SQL）与 deck.gl（高性能图层）各自的集成成本面：包体、成熟度、与现有 maplibre-gl + MapSpec Compiler 前端的兼容性、GeoLibre 如何使用它们？各给一句话结论：值不值得后续单独开图评估。不进入本 ADR 决策。

## Resolution

- DuckDB-WASM Spatial：值得后续单独开图评估（若确定要做"浏览器端空间 SQL/多格式直读"）——集成面清晰（懒加载、自托管 CORS、Arrow 版本对齐三条成本线，GeoLibre 已验证 SQL Workspace 模式），硬成本是 core+扩展约 8 MB 压缩传输的懒加载资源。
- deck.gl：值得后续单独开图但低优先级——官方 MapboxOverlay 与 MapSpec→MapLibre style 管线互补不冲突（增量实测 ~190–260 KB gzip），仅当出现 MapLibre style 表达不了的高性能/聚合/三维可视化需求时立项。
- 详见：../../docs/research/geolibre-duckdb-deckgl-sidequest.md
