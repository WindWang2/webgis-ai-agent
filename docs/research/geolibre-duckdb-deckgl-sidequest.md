# DuckDB-WASM Spatial 与 deck.gl 顺带摸底

> Wayfinder ticket-003 的调研产出（2026-08-27）。本报告只做事实摸底，不进入 GeoLibre/Whitebox WASM 的 ADR 决策。体积数据均为当日直接测量（curl / jsdelivr HEAD / esbuild 本地打包），标注了方法。

## 1. DuckDB-WASM Spatial（`@duckdb/duckdb-wasm` + spatial 扩展）

**包体（实测，Brotli/gzip 为浏览器实际传输量级）**

| 资源 | 未压缩 | 线上传输（压缩） |
| --- | --- | --- |
| 核心 `duckdb-eh.wasm`（stable 1.32.0） | ~35 MB（dev57 版实测 content-length 35.9 MB） | **6.76 MB Brotli**（jsdelivr 实测） |
| spatial 扩展 v1.3.0（wasm_eh） | 7.1 MB（实测） | **1.37 MB Brotli**（实测，`content-encoding: br`） |
| spatial 扩展 v1.1.3（wasm_eh） | 18.1 MB（实测） | 未测 |

- spatial 扩展自 DuckDB 1.3 起体积大幅瘦身（18.1 MB → 7.1 MB）。官方确认 wasm 扩展以 Brotli 预压缩下发（[extensions 文档](https://duckdb.org/docs/current/clients/wasm/extensions)）。
- JS 侧硬依赖 `apache-arrow@^17`（[npm registry](https://registry.npmjs.org/@duckdb/duckdb-wasm)、[Discussion #2145](https://github.com/duckdb/duckdb-wasm/discussions/2145)）；npm 包 unpacked ~149 MB，但那是全部 bundle 变体之和，不代表浏览器传输量。
- 版本疑点：[官方 wasm 文档](https://duckdb.org/docs/current/clients/wasm/overview)称"最新稳定版 1.5.4"，但 npm 上无此版本（最高非 dev 版本为 1.32.0，`latest` tag 指向 `1.33.1-dev57.0` dev 构建）；1.32.0 内置 DuckDB core 的对应版本**未查实**。

**成熟度**：可用但迭代慢——`latest` tag 仍是 dev 构建，官方长期只发 dev tag；wasm 限制为默认单线程、wasm 内存上限 4 GB（[官方文档](https://duckdb.org/docs/current/clients/wasm/overview)）。spatial 扩展 2023-12 起官方支持 wasm（[发布公告](https://duckdb.org/2023/12/18/duckdb-extensions-in-wasm.html)）。

**GeoLibre 用它做什么**：两个角色。其一，SQL Workspace（Processing → SQL Workspace）的默认引擎：已加载矢量图层暴露为可查询表，支持远程 URL（自动匹配 reader、HTTP range 流式读取）与 `s3://`→HTTPS 公共桶改写，结果可加回地图或导出 CSV/GeoParquet；另有 PGlite+PostGIS（首次加载 ~19 MB）与 Sedona/CereusDB 两个备选引擎（[SQL Workspace 文档](https://geolibre.app/user-guide/sql-workspace/)、[docs/index.md](https://github.com/opengeos/geolibre/blob/main/docs/index.md)）。其二，浏览器端格式驱动：`INSTALL/LOAD spatial` 后 GeoParquet 走 `read_parquet`、GDAL/OGR 格式走 `ST_Read`，检测几何列与 CRS 并 `ST_Transform`；zip shapefile 先经 `shpjs`、KML 走自研解析器兜底（[源码深读](https://juejin.cn/post/7668156693868118070)、[评测](https://juejin.cn/post/7667583763075137571)）。

**对"浏览器端空间 SQL 查询用户数据"的集成成本面**：本项目 `frontend/package.json` 目前无 duckdb 依赖，属全新引入。成本集中在三条线——(1) 懒加载策略：core+扩展约 8 MB 压缩传输（worker 方式），必须按需加载且 pin 版本；(2) 自托管/CDN 的 CORS 与缓存（extensions.duckdb.org 可直连但生产建议自托管）；(3) `apache-arrow@^17` 版本对齐（未来若叠加 geoarrow/deck.gl 亦需对齐）。后端无需改造，GeoLibre 已验证"图层暴露为表 + 结果回图"的完整模式。

**结论：值得后续单独开图评估**——若产品确定要"浏览器端空间 SQL/多格式直读"，集成面清晰可控（懒加载、自托管 CORS、Arrow 对齐三条成本线，GeoLibre 提供了可直接借鉴的实现蓝本），但需接受数十 MB 级懒加载资源这一硬成本；若该需求尚不确定，则不值得现在立项。

## 2. deck.gl（与 MapLibre GL JS 协同）

**集成成熟度**：官方路径是 `@deck.gl/mapbox` 的 `MapboxOverlay`——MapLibre 为根元素、deck.gl 作子层，相机自动同步，可挂为 IControl；兼容表明确 maplibre-gl v3+ 支持 `interleaved`（共享 WebGL 上下文插入 style 图层栈，`beforeId` 控制插入位），v3 之前仅 overlaid；MapLibre globe 投影完整支持，terrain 部分支持（[官方文档](https://deck.gl/docs/api-reference/mapbox/overview)）。当前 deck.gl 9.3.10；MapLibre v6 的显式验证仍是 open issue（[#10501](https://github.com/visgl/deck.gl/issues/10501) 要求显式支持 v4.5.1–v6），本项目 `maplibre-gl ^5.23.0` 落在 v3+ 支持区间内，社区大量 v5 + interleaved 实践（如 [maplibre-gl-wind](https://github.com/geoql/maplibre-gl-wind)）。

**包体（esbuild 本地打包实测，minify + gzip，2026-08-27）**

| 引入组合 | 体积 |
| --- | --- |
| `MapboxOverlay` + `ScatterplotLayer` | ~187 KB gzip（640 KB min） |
| 再加 GeoJson/Arc/Path + Heatmap/ScreenGrid + MVTLayer | ~257 KB gzip（890 KB min） |

按需引入的实际增量在 **190–260 KB gzip** 量级，tree-shaking 有效（对比本项目已有 `maplibre-gl` 本体约一倍出头，可控）。

**与 MapSpec Compiler 管线的关系**：`mapspec-compiler` 的输出是 MapLibre-compatible style（`frontend/lib/mapspec-compiler/index.ts`、`compiler.ts` 直接生成 `maplibreLayer.paint` 等字段），而 MapboxOverlay 不替换 style——overlaid 模式整体叠加在 style 之上，interleaved 模式按 `beforeId` 插入编译器产出的图层栈，MapLibre 仍是相机与样式的唯一真源。因此是**补充而非冲突**。需要注意的接缝：interleaved 的插入位置需与编译器图层排序协调、deck.gl 图层交互事件是 Map 代理的子集、terrain 下 z=0 数据不贴地、Popup 等 DOM 控件层级需处理（[文档 Limitations](https://deck.gl/docs/api-reference/mapbox/overview)）。

**结论：值得后续单独开图，但低优先级**——集成成本低（官方 MapboxOverlay 层 + ~190–260 KB gzip 增量，与 MapSpec→style 管线互补不冲突），但仅在出现 MapLibre style 表达不了的需求（大数据量散点/聚合热力/三维可视化）时才值得立项，当前没有 must-have 场景。

## 附：测量方法与来源

- duckdb 核心与扩展体积：`curl` 直接 GET/HEAD jsdelivr 与 extensions.duckdb.org，读 `content-length` 与 `content-encoding`（2026-08-27）。
- deck.gl 包体：在临时目录以 esbuild `--bundle --minify` 打包两个入口（最小组合/典型组合）后 gzip 实测，deck.gl 9.3.10。
- GeoLibre 用法：官方 README、[SQL Workspace 文档](https://geolibre.app/user-guide/sql-workspace/)、仓库 docs 与社区源码深读文章（链接见正文）。
- 本项目事实：`.worktrees/geolibre-wayfinder/frontend/package.json`（仅 `maplibre-gl ^5.23.0`，无 deck.gl/duckdb 依赖）、`frontend/lib/mapspec-compiler/`。
