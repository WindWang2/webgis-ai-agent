# Wayfinding Map: GeoLibre WASM 算法库引入决策

## Destination

一份带证据的 ADR：决定是否将 GeoLibre 的 Whitebox WASM 工具箱引入为**浏览器端补充算法引擎**（补足水文/地形/LiDAR/viewshed/kriging/厚栅格等现有 Python 栈缺失的类目），并锁定集成架构与边界——与服务端 SpatialAnalyzer 栈纯共存，agent 编排为目标架构。证据 = 最小 PoC（浏览器手动驱动跑通 1–2 个算子并渲染到地图）。实现集成与 agent 接线本身不在终点内。

## Notes

- 域：webgis-ai-agent（FastAPI + Celery + Next.js/MapLibre；无 PostGIS，默认 SQLite）
- 本图票据均为 `.wayfinder/geolibre/` 下的文件；claim = 把票据头部 `Status: open` 改为 `Status: claimed`；blocking 用票据内 `Blocked by:` 行（本地 markdown tracker 无原生依赖关系）
- HITL 票走 /grilling + /domain-modeling；prototype 票走 /prototype
- 调研产物写入 `docs/research/geolibre-*.md`，票据 Resolution 链接之
- 工作发生在 worktree `.worktrees/geolibre-wayfinder`（分支 `research/geolibre-algorithms`）
- 图定期约束（chart 时已锁，勿重开）：
  - 只取 Whitebox WASM 工具箱；DuckDB-WASM Spatial 与 deck.gl 仅顺带摸底（ticket-003），不进本 ADR 决策
  - 纯补充：既有 156+ 工具与 SpatialAnalyzer 服务端路径一行不动
  - 动机 = 算法类目广度（水文/地形/LiDAR/viewshed/kriging）；G-1~G-9 统计严谨性问题不是本图的药方
  - PoC 用最小 UI 手动驱动，agent 接线是后续 effort

## Decisions so far

- [Whitebox WASM 工具箱的可消费形态与包体调研](ticket-001-whitebox-wasm-consumption.md) — 无需 vendor：npm `geolibre-wasm`（MIT，1063 算子，`runTool` API + `listManifests` 参数 schema）可直接消费；单体 wasm 22.72 MiB / gzip 7.53 MiB，仅支持整体懒加载（上游 #34 按族分包提案未合并）；上游单人维护、仓库缺 LICENSE 文件为主要风险；全链路无 GPL
- [WASM 算子输入输出格式与数据体量约束调研](ticket-002-wasm-io-formats.md) — 扩展名驱动的内存虚拟 FS（`Uint8Array` → `/work`，专用 Web Worker，~4 GiB 内存上限，不用 OPFS）；中小规模可行（栅格 ≤~4000×4000、矢量 ≤~10⁵ 要素）；**ref_id GeoJSON 是零转换最短路径**，COG 支持字节范围窗口提取（`extract_cog_subset`/`pmtiles_extract`），栅格输出一律 COG、回显需浏览器内转瓦片或回传 `raster_tile_service`
- [DuckDB-WASM Spatial 与 deck.gl 顺带摸底](ticket-003-duckdb-deckgl-sidequest.md) — DuckDB-WASM（brotli 6.76+1.37 MB，单线程默认）值得单独开图——仅当确有浏览器端空间 SQL 需求；deck.gl（187–257 KB gzip，兼容 MapLibre v3+，对 MapSpec 管线是补充非冲突）值得但低优先级
- [v1 数据进入浏览器的路径决策](ticket-004-data-path-decision.md) — v1 = 服务端中介字节直传：矢量 ref_id GeoJSON、栅格/点云上传件原样单文件字节；CSV/SHP/PMTiles 服务端先转 GeoJSON；超护栏体量回落服务端 Celery；range 直取落雾，上传直通出局
- [WASM 结果回流——MapSpec 写入与栅格渲染决策](ticket-005-result-flow-decision.md) — **算在浏览器、状态在服务端**：矢量 `POST /upload` 回传→ref→`/mapspec/mutations` 薄暴露 `upsert_layer`（复用现有 `UpsertLayerIntent`）；栅格 COG 回传→现成 raster-tiles by ref 回显，前端零改动；MapSpec 保持服务端单一写入边界
- [算子子集暴露与 ToolRegistry 分类策略](ticket-006-tool-exposure-strategy.md) — 类目白名单（水文/地形/LiDAR/插值+厚栅格精选，~260 个，矢量基础不开）+ `listManifests()` 动态生成注册（`wb_` 前缀、新增 `client` 执行策略、类目→tier/domain/cost 映射表）+ 客户端预检护栏（超限结构化错误→self-healing hints→回落服务端 Celery）
- [最小 PoC——浏览器手动驱动跑通 1–2 个算子](ticket-007-minimal-poc.md) — **物理路线成立**（poc/，commit 5e8ae3b）：viewshed 251 ms@300² DEM、dissolve 19.2 s@400 面（矢量重拓扑慢→白名单维持栅格/地形类为主）、wasm 编译 <0.4 s、回传体积 2–27 KiB、manifest 参数权威源实证
- [ADR 落笔——引入决策与集成架构](ticket-008-adr-decision.md) — 用户确认 accepted：[ADR-0078](../../docs/adr/0078-geolibre-wasm-browser-algorithm-engine.md) 有条件引入 `geolibre-wasm` 为浏览器端补充算法引擎，集成架构四要点（白名单动态注册/字节直传/回流/护栏）+ agent 接线后续 effort + 供应链风险缓解。**地图到站。**

## Not yet specified

- COG/GeoParquet 字节范围窗口提取直取浏览器（`extract_cog_subset`/`pmtiles_extract`）——v2 优化，需在 data_fabric 新开面向浏览器的提取通道（v1 决策已将其延后）
- 浏览器内瓦片自渲染（`write_pmtiles`/`raster_to_tiles`）——v2 优化，仅当栅格回传成本被证明不可接受时毕业（结果回流决策已将 v1 定为回传-服务端切片）
- WASM 算子结果质量对照验证（与 QGIS/服务端 Python 结果比对）——仅当 ADR 落笔票对 PoC 证据有争议时才需要（PoC 中 viewshed 可见像元 835/90000 未对照参考实现）

## Out of scope

- 嵌入整个 GeoLibre 应用 / 替换 Next.js 前端（chart 时已拒）
- 服务端引入 WhiteboxTools 引擎（chart 时已拒）
- 替代/收编现有 Python 分析路径（chart 时已锁纯补充）
- G-1~G-9 统计严谨性与数据质量修复（另开 effort，见 `GIS_ALGORITHM_REVIEW.md`）
- DuckDB-WASM Spatial / deck.gl 的正式引入决策（只摸底，产出一句话结论）
- 上传文件直通浏览器、不经服务端（数据路径决策：与 session 所有权/ref_id/权限链冲突）
- Agent 编排接线的实现与详细设计（SSE/WS 委托-等待回路、tier 授权与 `client` 执行策略的落地）——方向性结论已在暴露策略票 Resolution 中给出，归后续 effort
