# Ticket: 最小 PoC——浏览器手动驱动跑通 1–2 个算子

**Label**: `wayfinder:prototype` | **Type**: HITL | **Status**: closed
**Blocked by**: ticket-001-whitebox-wasm-consumption.md, ticket-004-data-path-decision.md, ticket-005-result-flow-decision.md

## Question

用最小 UI（不走 agent 链路）在浏览器里跑通 1 个矢量算子 + 1 个地形算子（如 viewshed，小 DEM），并按已定路线走完整回流：**数据进**（矢量 ref_id GeoJSON / DEM 上传件原样字节，见数据路径决策）→ WASM 执行 → **结果出**（矢量 `POST /upload` 回传→`upsert_layer` 挂层；栅格 COG 回传→raster-tiles by ref，见结果回流决策）→ 渲染到现有 MapLibre 地图。验证物理假设：包体可接受（单体 gzip 7.53 MiB + worker ~23 MB 懒加载）、数据序列化开销可控、`upsert_layer` 薄暴露可行。产出：可运行原型 + 实测数据（包体/耗时/回传体积），作为 ADR 证据。走 /prototype 技能。

## Resolution

**物理路线成立。** 可运行原型与实测数据见 [poc/](../../../poc/)（含 README、截图、样本与构建脚本；commit 5e8ae3b，分支 research/geolibre-algorithms）。

关键实测（headless Chromium，2026-08-28）：
- wasm 单体 23.8 MB（localhost 无压缩；CDN gzip ≈7.53 MiB）懒加载 + 编译 371–380 ms；1063 工具；`listManifests()` 离线参数 schema 完整
- **viewshed（地形）251 ms** @300×300 DEM，输出 COG 2.0 KiB，回传体积无障碍，overlay 画布实画验证
- **dissolve（矢量拓扑）19.2 s** @400 面（冷启动；GeoLibre 自家案例同量级）→ 矢量重拓扑不适合客户端白名单（为暴露策略决策补注脚）
- manifest 即参数权威源（经典 Whitebox 文档参数名不可靠：`--input` 非 `--dem`、`stations` 是矢量文件）
- `upsert_layer` 薄暴露：静态核实（`UpsertLayerIntent` 已存在，mutations API 只差 action 分支）；动态端到端留待实现 effort

未被本票推翻的假设：包体预算（雾项「包体预算与加载策略」已被本票吸收，出雾）。
