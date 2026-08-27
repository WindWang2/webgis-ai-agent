# Ticket: 最小 PoC——浏览器手动驱动跑通 1–2 个算子

**Label**: `wayfinder:prototype` | **Type**: HITL | **Status**: claimed (this session)
**Blocked by**: ticket-001-whitebox-wasm-consumption.md, ticket-004-data-path-decision.md, ticket-005-result-flow-decision.md

## Question

用最小 UI（不走 agent 链路）在浏览器里跑通 1 个矢量算子 + 1 个地形算子（如 viewshed，小 DEM），并按已定路线走完整回流：**数据进**（矢量 ref_id GeoJSON / DEM 上传件原样字节，见数据路径决策）→ WASM 执行 → **结果出**（矢量 `POST /upload` 回传→`upsert_layer` 挂层；栅格 COG 回传→raster-tiles by ref，见结果回流决策）→ 渲染到现有 MapLibre 地图。验证物理假设：包体可接受（单体 gzip 7.53 MiB + worker ~23 MB 懒加载）、数据序列化开销可控、`upsert_layer` 薄暴露可行。产出：可运行原型 + 实测数据（包体/耗时/回传体积），作为 ADR 证据。走 /prototype 技能。
