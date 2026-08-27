# ADR-0078: 引入 GeoLibre Whitebox WASM 工具箱作为浏览器端补充算法引擎

日期: 2026-08-28
状态: accepted

## 背景

现有算法栈全部在服务端 Python（`SpatialAnalyzer` + `app/lib/geo_analysis`、`geo_processor`、`network`、`rs`，156+ 已注册工具），水文、地形（viewshed）、LiDAR、kriging 与厚栅格处理是能力空白；G-1~G-9 属统计严谨性问题，与本决策无关（另开 effort）。wayfinder 图（`.wayfinder/geolibre/`，8 票全关）完成了证据链：npm 包 `geolibre-wasm` v1.5.2（MIT，733 上游 + ~330 自研 = 1063 算子）可直接消费、无需 vendor；`runTool`/`listManifests()` API 完整，manifest 为参数权威源；浏览器物理 PoC（`poc/`，commit 5e8ae3b）实证：单体 wasm 23.8 MB / CDN gzip ≈7.53 MiB 懒加载+编译 <0.4 s，viewshed 251 ms @300×300 DEM，dissolve 19.2 s @400 面（矢量重拓扑慢的负面发现），结果回传体积 2–27 KiB。

## 决策

**有条件引入**：`geolibre-wasm` 作为浏览器端补充算法引擎，纯补充、不替代任何服务端路径（"all geo math goes through SpatialAnalyzer" 的服务端不变量不动）。

1. **白名单类目**（暴露策略票）：水文(~100)/地形(~99)/LiDAR(~65)/插值家族+厚栅格精选，约 260 个算子经 `listManifests()` **动态生成注册**——`wb_` 命名前缀、执行策略枚举新增 `client`、类目→tier/domain/cost 缺省映射表；矢量基础类目不开（与现有工具重复），矢量重拓扑不进白名单（PoC 实测过慢）。经典 Whitebox 文档参数名不可靠（`--input` 非 `--dem`、`stations` 是矢量文件），一切以 manifest 为准。
2. **数据路径**（数据路径票）：服务端中介字节直传——矢量走 ref_id GeoJSON，栅格/点云走上传件单文件原样字节喂 wasm 内存虚拟 FS（`/work`）；CSV/SHP/PMTiles 服务端先转 GeoJSON；字节范围窗口提取（`extract_cog_subset`）与上传直通浏览器均延后/出局。
3. **结果回流**（结果回流票）：算在浏览器、状态在服务端——矢量结果 `POST /upload` 回传得 ref，经 `/sessions/{id}/mapspec/mutations` 薄暴露 `upsert_layer` intent（复用 `lifecycle_engine.UpsertLayerIntent`）挂层；栅格 COG 回传走现成 `raster-tiles by ref` 服务端切片回显；MapSpec 保持服务端单一写入边界（锁/checkpoint/fingerprint 机制不动）。
4. **体量护栏**：客户端喂 `/work` 前预检（矢量 ~10⁵ 要素 / 栅格 ~4000×4000），超限结构化错误经 self-healing hints 提示 agent 回落服务端 Celery 路径。

## 边界与后续

- **Agent 接线是后续 effort**：方向已定——ToolRegistry `client` 执行策略为锚点，agent 工具调用经 dispatch 下发浏览器执行、结果按本 ADR 回流；SSE/WS 委托-等待回路与 tier 授权交互不在本图。手动 UI 驱动先于 agent 接线。
- **供应链风险**：上游 whitebox_next_gen 单人主导、无 GitHub release/tag、仓库根缺 LICENSE 文件（代码内 crate 声明 MIT OR Apache-2.0，无 GPL）——引入时 pin 精确版本，升级走显式评审。
- **顺带结论**：DuckDB-WASM Spatial（brotli ~8.1 MB）值得单独开图，仅当确有浏览器端空间 SQL 需求；deck.gl（187–257 KB gzip，兼容 MapLibre v3+，对 MapSpec 管线是补充）值得但低优先级。
- **实现清单入口**：`.wayfinder/geolibre/map.md` Decisions so far 8 条 + `docs/research/geolibre-*.md` 三篇 + `poc/README.md` 实测数据。
