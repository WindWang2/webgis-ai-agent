# GeoLibre Whitebox WASM 算子输入输出格式与数据体量调研

**Ticket**: [.wayfinder/geolibre/ticket-002-wasm-io-formats.md](../../.wayfinder/geolibre/ticket-002-wasm-io-formats.md)
**日期**: 2026-08-28
**调研对象版本**: `geolibre-wasm@1.5.2`（npm 实测安装包）、`opengeos/geolibre-rust` main、`opengeos/whitebox-wasm` main、`opengeos/GeoLibre` main（均为浅克隆/官方文档站）

---

## 结论速览

| # | 问题 | 结论（一句话） |
|---|------|----------------|
| 1 | 算子接受哪些输入/输出格式 | 工具层（WASI）：矢量按扩展名读写 GeoJSON/Shapefile/FlatGeobuf/GeoPackage/KML/GPX/TopoJSON/GML/GeoParquet；栅格按扩展名读写 GeoTIFF/BigTIFF/COG 为主（另有 ASCII Grid/JPEG2000/PNG+world file 等 16+ 格式）；LiDAR 为 LAS/LAZ。**无** Whitebox 传统 `.dep`/`.tas`/`.taudem` 格式（next-gen 重写后已不存在） |
| 2 | 数据如何物理进入 WASM | 纯内存虚拟文件系统：JS 把 `Record<文件名, Uint8Array>` 写进 `@bjorn3/browser_wasi_shim` 的 `PreopenDirectory("/work")`，argv 传 `--input=/work/xxx`，工具经 `std::fs` 读写 /work，结束后 JS 遍历 /work 收回新文件。运行在专用 Web Worker 内，每次 run 全新 /work |
| 3 | 代表算子体量 | DEM 地形/水文工具在 2048² 为 1-4 s/工具、4096² slope 15 s，viewshed 2048² 26 s（本机实测）；矢量 buffer 10k 面 ~20 s，但 dissolve 与稠密矢量切瓦片是瓶颈（10k dissolve >9 min、vector_to_pmtiles 稠密面 OOM）。WASM 4 GiB 线性内存是硬上限，官方明示「全国尺度十亿像素栅格不能在浏览器整幅解码」 |
| 4 | OPFS / Service Worker | **工具数据不落盘**：无 OPFS；Service Worker 仅缓存底图瓦片（离线区域）。所有工具 I/O 驻留内存、随 run 结束丢弃 |
| 5 | 对照本项目数据路径 | 最短路径是 **ref_id GeoJSON（零转换）**；其次是 **S3 COG 经 `extract_cog_subset` 字节范围窗口提取（不整文件下载）** 与 **服务端 GeoTIFF 原样字节直喂**；GeoParquet/GPKG/FGB 单文件可直喂；SHP 需解包多文件、CSV/MVT-PMTiles 必须先转 GeoJSON。栅格结果回显是缺口：COG 字节需 `write_pmtiles`/`raster_to_tiles` 浏览器内转瓦片，或回传服务端 raster_tile_service |

---

## 1. 算子接受哪些输入/输出格式

先厘清「Whitebox WASM」的构成：npm 包 `geolibre-wasm`（源仓库 [opengeos/geolibre-rust](https://github.com/opengeos/geolibre-rust)）包含两层（README「Architecture」节）：

- **库层** `geolibre-wasm`（`.` 导出，wasm-bindgen，`wasm32-unknown-unknown`）：内存字节 API，不是工具。
- **工具层** `geolibre-wasm/tools`（`./tools` 导出，WASI，`wasm32-wasip1`）：`geolibre-cli.wasm`，内含 **733 个 whitebox 工具**（[whitebox-wasm README](https://github.com/opengeos/whitebox-wasm)：「The full `wbtools_oss` algorithm suite (**733 tools**)」）+ GeoLibre 自研工具。**实测 `listTools()` 返回 1063 个工具 id**（geolibre-wasm@1.5.2，Node 运行）。GeoLibre 官网目录分类合计 1000+（Vector 313 / Raster 256 / 遥感 154 / 水文 100 / 地形 99 / LiDAR 65 / Conversion 49 / Network 26 / 投影 4，见 [GeoLibre README](https://github.com/opengeos/GeoLibre)）。

### 1.1 矢量（工具层，WASI，`std::fs` 按扩展名分发）

格式检测在 `wbvector::Layer::detect`/`read`/`write`，按扩展名 + 轻量嗅探（[whitebox-wasm `crates/wbvector/src/lib.rs`](https://github.com/opengeos/whitebox-wasm/blob/main/crates/wbvector/src/lib.rs)，第 182-330 行附近的扩展名分发）：

| 格式 | 扩展名 | 读 | 写 | 备注 |
|---|---|---|---|---|
| GeoJSON | `.geojson` | ✓ | ✓ | 工具输出默认格式；带 CRS member 时写出会重投影 |
| Shapefile | `.shp` | ✓ | ✓ | 需 `.shp`+`.dbf` 同目录（`.prj` 可选，只读）；`read` 接受带或不带扩展名的基础路径（`shapefile/mod.rs:47-53`）。GeoLibre UI 把 `.shp/.shx/.dbf/.prj/.cpg` 打包回 zip 给用户（`wasm-client.ts:43-57`） |
| FlatGeobuf | `.fgb` | ✓ | ✓ | |
| GeoPackage | `.gpkg` | ✓ | ✓ | 单文件，天然适合 memfs |
| GeoParquet | `.parquet` | ✓ | ✓ | 见下方特性说明 |
| KML/GPX/TopoJSON/GML/KMZ | `.kml/.gpx/.json/.gml/.kmz` | ✓ | ✓ | KMZ 依赖 `kmz` feature |
| OSM PBF | `.pbf` | ✓ | — | `osmpbf` feature（库层启用；工具输入不常用） |

**GeoParquet 特性细节**：`wbvector` 的 `geoparquet` 是非默认 feature（[wbvector/Cargo.toml:16-20](https://github.com/opengeos/whitebox-wasm/blob/main/crates/wbvector/Cargo.toml)）。`wbtools_oss` 依赖 wbvector 时未开该 feature，但 `geolibre-tools`（编入同一 `geolibre-cli.wasm`）开启了 `features = ["geoparquet"]`（[geolibre-tools/Cargo.toml](https://github.com/opengeos/geolibre-rust/blob/main/crates/geolibre-tools/Cargo.toml)，注释明确「GeoParquet read/write lives behind wbvector's `geoparquet` feature」）。按 Cargo 特性统一规则，同一构建图内 feature 合并生效，因此工具层可以按 `.parquet` 扩展名读写 GeoParquet。旁证：GeoLibre 生产代码把 `geoparquet` 列为 WASM 矢量输出格式（`GeoLibre/packages/processing/src/wasm-client.ts:26-30` 的 `VECTOR_OUTPUT_EXTENSION`）。此条为**源码推断**，未做独立运行验证。

### 1.2 栅格（工具层）

工具经 `Raster::read(path)` 打开输入（如 [wbtools_oss `hydrology/mod.rs:359`](https://github.com/opengeos/whitebox-wasm/blob/main/crates/wbtools_oss/src/tools/hydrology/mod.rs)），`RasterFormat` 按扩展名分发（[wbraster/src/lib.rs:10-25 格式表](https://github.com/opengeos/whitebox-wasm/blob/main/crates/wbraster/src/lib.rs)）：

- **主格式：GeoTIFF / BigTIFF / COG**（`.tif/.tiff`），读+写。**栅格输出是云优化 GeoTIFF**（分块、Deflate、带金字塔与 GDAL ghost metadata；[whitebox-wasm README](https://github.com/opengeos/whitebox-wasm)「Raster outputs are Cloud Optimized GeoTIFFs」；`npm/README.md:161-163`）。GeoLibre 拿到结果后还会再走一次 `convertGeoTiffToCog` 归一化（`wasm-client.ts:646-648`）。
- 其余按扩展名：Esri ASCII Grid `.asc/.grd`、Esri Binary Grid `.adf`（工作区多文件）、Esri Float Grid `.flt+.hdr`、GRASS ASCII、Surfer `.grd`、PCRaster `.map`、SAGA `.sdat/.sgrd`、Idrisi `.rst/.rdc`、ER Mapper `.ers`、ERDAS HFA `.img`（只读）、ENVI `.hdr` 系列、GeoPackage 栅格 `.gpkg`、JPEG2000 `.jp2`、PNG/JPEG + world file。
- **Whitebox 传统自有格式 `.dep`/`.tas`/`.taudem`：不存在**。在整个 whitebox-wasm 仓库 `crates/` 下全文检索 `.dep`/`.tas`/`.taudem` 无任何命中（2026-08-28，浅克隆 main 分支）；whitebox_next_gen 用上述格式表替代了旧 WhiteboxTools 的原生格式。本项目若有历史 `.dep` 数据需先在服务端转 GeoTIFF。

### 1.3 LiDAR（工具层）

LAS/LAZ（及 PLY）点云：GeoLibre 的输入校验强制「LASF」魔数（`wasm-client.ts:563-566, 788-792`），错误信息明确「Load LAS/LAZ files, or use the sidecar」。

### 1.4 库层（wasm-bindgen，内存字节 API，供对照）

与工具层不同，库层从**字节缓冲**读（[geolibre-rust `crates/geolibre-wasm/src/vector.rs:4-6`](https://github.com/opengeos/geolibre-rust/blob/main/crates/geolibre-wasm/src/vector.rs)）：「In-memory formats: GeoJSON, TopoJSON, GML, GPX, KML (text); FlatGeobuf, GeoPackage, GeoParquet, KMZ (binary). **Shapefile / MapInfo / OSM-PBF are file-oriented in the engine and not yet exposed here.**」。栅格侧 `GeoTiffReader`（GeoTIFF/BigTIFF/COG 读）、`CogBuilder`（COG 写）、`CogStream`（HTTP 字节范围流式读 COG 瓦片，[whitebox-wasm README](https://github.com/opengeos/whitebox-wasm)）；矢量可输出 GeoJSON 字符串、deck.gl 二进制属性（`vector_to_binary`）或 GeoArrow IPC（`vector_to_arrow_ipc`）。

### 1.5 每个工具的参数 schema

`listManifests()` 返回全部工具的 manifest，参数带 `io_role`（input/output）、`data_kind`（raster/vector/lidar/file/number/bool/…）与枚举选项——可直接驱动表单与文件类型校验（实测 `viewshed`/`d8_flow_accum`/`buffer_vector` 等均有完整 schema；[geolibre-cli `main.rs:9-26` 的调用契约](https://github.com/opengeos/geolibre-rust/blob/main/crates/geolibre-cli/src/main.rs)）。

---

## 2. 数据如何物理进入 WASM 运行时

**结论：内存虚拟文件系统（memfs），不是 OPFS，也不是 ArrayBuffer 直传给算法。**

### 2.1 调用契约与代码路径

JS 侧入口 `runTool(tool, { args, input })`（[geolibre-rust `npm/tools.mjs`](https://github.com/opengeos/geolibre-rust/blob/main/npm/tools.mjs)，`exec()` 在第 145-187 行）：

```js
// 1) 输入：Record<文件名, Uint8Array>（或 http(s) URL 字符串，JS 先 fetch 全量）
const contents = new Map(Object.entries(inputFiles).map(
  async ([k, v]) => [k, new File(await materializeInput(v))]));
// 2) 内存文件系统作为 WASI preopen
const work = new PreopenDirectory("/work", contents);   // @bjorn3/browser_wasi_shim
// 3) argv 传工具名 + --input=/work/xxx
const wasi = new WASI(["geolibre", ...argv], [], [stdin, stdout, stderr, work], {...});
// 4) 运行后遍历 /work，收回所有新写入文件（含子目录）
walk(work.dir, "");   // => files: Record<相对路径, Uint8Array>
```

Rust 侧（[geolibre-cli `main.rs`](https://github.com/opengeos/geolibre-rust/blob/main/crates/geolibre-cli/src/main.rs)）把 `--k=v` 解析成 `ToolArgs`（JSON map），`ToolRegistry::run` 执行，工具经 `std::fs`（即 WASI fd 映射到 memfs）读写 `/work`。`main.rs` 的测试直接用一个 point GeoJSON 作为矢量 fixture、`examples/sample.tif`（64×48，3.7 KB）作为 DEM fixture，证实了这条路径。

### 2.2 关键性质

- **无网络**：WASM 模块沙箱内不做任何网络 I/O；HTTP 都在 JS 侧完成（[whitebox-wasm README「Reading from an HTTP URL」](https://github.com/opengeos/whitebox-wasm)）。
- **整文件进入内存**：`input` 里的 URL 会被 `materializeInput` 整个 `fetch().arrayBuffer()`（`tools.mjs:124-134`）。GeoLibre 应用层喂栅格也是整文件 `fetch(url)`（[GeoLibre `apps/geolibre-desktop/src/lib/whitebox-layer-inputs.ts:63-84`](https://github.com/opengeos/GeoLibre/blob/main/apps/geolibre-desktop/src/lib/whitebox-layer-inputs.ts)）——工具运行前栅格必须完整可取。窗口化读取只存在于少数 JS 拦截的提取工具（见 2.4）。
- **内存峰值 ≈ 输入字节 + memfs 副本 + 工具内部展开**（栅格工具内部普遍按 f64 展开：`w*h*8` 字节）。
- **4 GiB 硬上限**：官方文档明示「WebAssembly is 32-bit, so linear memory is capped at ~4 GiB … a national billion-pixel raster cannot be fully decoded in one piece in-browser」（[whitebox-wasm README「Limits」](https://github.com/opengeos/whitebox-wasm)；`npm/README.md:195-196` 亦重复此约束）。
- **运行在 Web Worker**：GeoLibre 把 `runTool` 放进专用 Worker（[GeoLibre `packages/processing/src/wasm-tool.worker.ts`](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-tool.worker.ts)）：`wasi.start()` 是一次同步调用无让出点，主线程跑会冻结 UI。Worker 内编译一次 **~23 MB 的 `geolibre-cli.wasm`** 并常驻复用（idle 池 `MAX_IDLE_WORKERS = 1`，[wasm-tool-runner.ts](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-tool-runner.ts)）；每次 run 得到全新 WASI 实例与全新 /work，**运行之间无状态残留**。
- **懒加载**：工具运行时只在首次使用时下载（`wasm-client.ts:106` 注释：`~5 MB (gzipped)`；@1.5.2 实测 gzip 后 7.9 MB，见 §3.3）。

### 2.3 GeoLibre 应用层的喂入方式（`runWhiteboxToolWasm`）

[GeoLibre `packages/processing/src/wasm-client.ts:656-915`](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-client.ts) 是最完整的参考实现：

- `vector_in`：地图层的内存 GeoJSON FeatureCollection 直接 `TextEncoder.encode(JSON.stringify(fc))` 写入 `/work/<参数名>.geojson`（行 741-751）；若调用方给的是带扩展名的 URL/字节则按原扩展名写入。
- `raster_in`：必须通过 TIFF 魔数嗅探（`isTiff`，II/MM + 42/43，含 BigTIFF），否则报错「not a readable GeoTIFF in the browser. Load the rasters as COG/GeoTIFF, or use the sidecar」（行 783-787）。
- `lidar_in`：必须 LAS/LAZ（`LASF` 魔数）。
- `vector_out`：默认 `.geojson`（JSON.parse 回 FeatureCollection 直接成图）；可选 `.parquet` / `.fgb` / `.shp`（shp 结果把 sidecar 打包成 zip 下载）（行 26-30, 800-816, 862-876）。
- `raster_out`：`.tif`，随后 `ensureWhiteboxRasterCog` 统一转 COG（行 817-833, 878-882）。
- `buffer_vector`/`multiple_ring_buffer` 的坐标预处理：GeoJSON 先在 JS 里投影到 EPSG:3857 再进工具（平面缓冲），结果由 GeoJSON writer 按 CRS member 投回 WGS84（行 469-541）。

### 2.4 例外：JS 拦截的字节范围提取工具

`extract_cog_subset` / `extract_wms_subset` / `extract_xyz_tile_subset` / `pmtiles_extract` / `download_osm_vector` 不走整文件 WASI 读：`tools.mjs` 在 JS 侧编排 HTTP Range 请求（`runTool` 分发行 250-258）。HTTP COG 用 `CogStream` 先读文件头（256 KB 起步、按需加倍至 8 MB 上限），再只取 bbox 覆盖的瓦片字节（`extractCogSubset`，行 1380-1499）；`pmtiles_extract` 用 `PmtilesExtractor` 逐轮取所需字节区间（行 566-633）。**这意味着 S3 上的 COG/PMTiles 可以只取窗口而不下载全量**——这是本项目 data_fabric 路径的关键接口。

### 2.5 最小集成样例

`geolibre-rust/demo/index.html`：`<input type=file>` → `el.files[0].arrayBuffer()` → `runTool(id, { args, input })` → 渲染 stdout/files/下载链接（第 95、464-510 行）；`npm/README.md:88-93` 有同构的代码示例。

---

## 3. 代表性算子的典型体量

### 3.1 官方/源码中可查的数字

| 数字 | 场景 | 来源 |
|---|---|---|
| 256×256 窗口 ≈ 下载 5.7 MiB 文件的 **~13%** | `CogStream` 窗口读取示例 | [whitebox-wasm `examples/cog-stream.mjs` + README](https://github.com/opengeos/whitebox-wasm) |
| 「全国尺度十亿像素栅格不能在浏览器整幅解码」 | 硬限制 | [whitebox-wasm README「Limits」](https://github.com/opengeos/whitebox-wasm) |
| **290 个多边形 dissolve ≈ 60 s**；国家尺度矢量切瓦片到 z14 要「数分钟」 | 真实 issue（GeoLibre#1977）驱动 Worker 化 | [GeoLibre `wasm-tool.worker.ts:5-9`](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-tool.worker.ts)、`wasm-tool-runner.ts:2-8` |
| 地形 viewshed 有专用轻量路径：解码 Terrarium 瓦片成高程网格在自研 worker 里算 | GeoLibre 认为「需要严谨结果且手头有 DEM 时」才用 Whitebox `viewshed` | [GeoLibre `terrain-viewshed.ts:1-20`](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/terrain-viewshed.ts) |
| 官方无系统性 benchmark 页面 | 已查：geolibre.app 文档、两个仓库 README/docs、issues | — |

### 3.2 本机实测（geolibre-wasm@1.5.2，Node v26.8.1，单线程 WASM）

方法：库层 `CogBuilder` 生成合成 DEM（f64 高熵地形，EPSG:32610，30 m 分辨率，Deflate COG）→ `runTool` 跑工具，记录输入/输出字节数与墙钟时间；矢量用规则网格 GeoJSON。**环境为 Node 而非浏览器，时间是量级参考。**

**栅格工具**（输入 = 同一个 DEM COG；输出字节为工具写出的 COG）：

| DEM | 单元数 | 输入 COG | 工具 | 耗时 | 输出 |
|---|---|---|---|---|---|
| 512×512 | 0.26 M | 1.85 MB | slope | 0.36 s | 0.88 MB |
| | | | hillshade | 0.20 s | 0.58 MB |
| | | | fill_depressions | 0.26 s | 1.22 MB |
| | | | d8_pointer | 0.08 s | 0.02 MB |
| | | | d8_flow_accum | 0.13 s | 0.21 MB |
| 2048×2048 | 4.2 M | 37.5 MB | slope | 3.9 s | 17.9 MB（f32） |
| | | | hillshade | 2.9 s | 12.2 MB |
| | | | fill_depressions | 4.2 s | 24.5 MB |
| | | | d8_pointer | 1.2 s | 0.54 MB |
| | | | d8_flow_accum | 2.4 s | 4.84 MB |
| | | | viewshed | **26.3 s** | 0.06 MB（0/1 掩膜） |
| 4096×4096 | 16.8 M | 150 MB | slope | 14.9 s | 71.4 MB |

**矢量工具**：

| 输入 | 输入大小 | 工具 | 耗时 | 输出 |
|---|---|---|---|---|
| 10,000 个正方形多边形 | GeoJSON 1.57 MB | buffer_vector d=20 | ~18-20 s | GeoJSON 14.27 MB（≈9× 膨胀） |
| 同上 | — | buffer_vector + `--dissolve=true` | **>9 min 未完成**（实测进程在 600 s 上限处被终止） | — |

**显示链路（结果 → 瓦片）**：

| 步骤 | 结果 |
|---|---|
| write_pmtiles（2048² DEM，z0-12，viridis 色带 → Web Mercator PNG 金字塔 PMTiles） | **3.25 s → 5.01 MB 单文件 PMTiles** |
| raster_to_tiles（2048² DEM，z0-12 → XYZ PNG 瓦片树） | **2.52 s → `{z}/{x}/{y}.png` 目录树**（单瓦片 ~0.02-0.08 MB） |
| vector_to_pmtiles（10k 缓冲面 GeoJSON 14.27 MB，z0-14） | **WASM 内存 OOM 崩溃**（Rust 侧单次 96 MB 分配失败，`unreachable`）；稀疏矢量可用，稠密矢量需降 max_zoom/开 simplify/drop_rate 或分块 |

实测注记：
- 合成 DEM 的 f64 地形是高熵数据，Deflate 压不动（2048² COG 37.5 MB > raw f64 32 MB）；**真实整米编码的 DEM 通常有 1.5-3× 压缩比**，输入文件字节会更小。输出侧 slope/d8 等自动用紧凑 dtype（f32/uint8/log 值），故输出 COG 普遍小于输入展开。
- viewshed 是实测最慢的地形工具（逐格视线计算），2048² 需 26 s；DEM 水文链（fill_depressions → d8_pointer → d8_flow_accum）在 2048² 合计 ~8 s。
- 10k 多边形 buffer 一次 18-20 s，但其 **dissolve（union）超过 9 分钟未完成**，**vector_to_pmtiles 对 10k 缓冲面（圆角膨胀后的稠密顶点）直接 WASM OOM**——矢量叠加/联合/稠密切瓦片才是 WASM 引擎的真正瓶颈（与 GeoLibre 自身 290 多边形 dissolve ~60 s 的案例同量级外推）。
- 栅格结果的显示路径在本机验证畅通：2048² DEM → `write_pmtiles` 3.25 s 得 5 MB PMTiles，或 `raster_to_tiles` 2.5 s 得 XYZ PNG 树，两者 MapLibre 都能直接消费。
- 内存换算：跑 2048² 工具时至少同时持有「JS 输入字节 + memfs 副本 + 工具内部 f64 展开（32 MB）+ 输出缓冲」；4096² 时 f64 展开已达 128 MB。OOM 是真实的失败模式（见上 vector_to_pmtiles 案例），不只是理论值。

### 3.3 运行时与包体（实测）

| 产物 | 原始 | gzip |
|---|---|---|
| `geolibre-cli.wasm`（WASI 工具运行器，1063 工具） | 23.8 MB | 7.9 MB |
| `geolibre_wasm_bg.wasm`（wasm-bindgen 库层） | 6.4 MB | 1.9 MB |

（@1.5.2 npm 包实测；与 `geolibre-wasm/Cargo.toml` 注释「~4.5 MB → ~6.4 MB / 1.2 → 1.9 MB gzipped」及 GeoLibre 源码注释「~23 MB」「~5 MB (gzipped)」一致。）

### 3.4 体量结论

- 输入端三重账：**网络传输的文件字节 + memfs 副本 + 工具内部 f64 展开**。f64 展开后 2048² = 32 MB、4096² = 128 MB；Deflate COG 对真实整米 DEM 一般还有 1.5-3× 压缩（合成高熵 f64 数据例外，实测反而更大）。
- 实测修正：单遍滤镜（slope/hillshade）与水文链（fill_depressions/d8_pointer/d8_flow_accum）在 2048² 上同为秒级（1-4 s/工具，水文链合计 ~8 s），并不比滤镜贵一个量级；真正贵的是 viewshed（26 s @2048²）与矢量 union/稠密切瓦片（10k dissolve >9 min、10k 稠密面 vector_to_pmtiles OOM）。
- 经验阈值（实测 + 外推）：浏览器端宜控制在**输入栅格 ≤ ~4096×4096、矢量 ≤ ~10⁴ 要素或缓冲/叠加前的稀疏几何**；超过即走服务端路径（GeoLibre 自己也是这个产品逻辑：大数据回落 Python sidecar，`wasm-client.ts:779-781` 的错误信息明示这一分工）。

---

## 4. OPFS / Service Worker 的使用情况

**结论：工具数据不落盘。**

- WASI 工具的 /work 是 `@bjorn3/browser_wasi_shim` 的纯内存目录（JS `File` 对象包 `Uint8Array`），每次 run 全新（`wasm-tool.worker.ts:14-15`：「Each run still gets a fresh WASI instance and a fresh /work from `runTool`, so nothing carries over between them」）。
- 在 GeoLibre 全仓库（apps/packages/workers，排除 node_modules）检索 `navigator.storage.getDirectory` / OPFS：**无工具数据路径使用**。命中的只有：`offline-regions.ts` 用 `navigator.storage.estimate()` 报告配额；`zarr-directory-picker.ts` / `tauri-io.ts` 用的是 **File System Access API**（用户选目录/文件句柄，非 OPFS），且属桌面端文件选取。
- Service Worker 仅一处：`offline-tiles.ts` + Workbox 缓存（cache 名 `geolibre-basemaps`），**只缓存底图瓦片**供离线区域使用，与工具 I/O 无关（`offline-regions.ts:1-11`）。
- 大数据策略因此是「整文件进内存，太大就换引擎」（sidecar/服务端），而非「落盘再算」。

对本项目的含义：不要指望 OPFS 让浏览器端算子处理超大文件；体量约束是真实的内存约束。反过来，若我们要做「结果持久化」，必须自己把结果字节送到服务端或对象存储。

---

## 5. 对照本项目三类数据源的接入路径

本项目现状（票面给定，未重复调研）：上传件在服务端（GeoJSON/SHP/GPKG/CSV/KML/GeoTIFF）；大对象经 ref_id 由前端取 GeoJSON；data_fabric 有 GeoParquet/PMTiles/S3 适配器；栅格结果由服务端 raster_tile_service 切 XYZ PNG 下发。

### 5.1 各数据源 → WASM 工具的转换链

| 数据源 | 转换链 | 评价 |
|---|---|---|
| **ref_id GeoJSON** | 前端已取到的 FC → `JSON.stringify` → `input{"layer.geojson": bytes}` | **最短路径，零格式转换**（与 GeoLibre 自身 `vector_in` 完全同构） |
| **服务端上传件 GeoJSON/KML/GPX** | 直接 `fetch(上传件URL)` → bytes → 按扩展名写入 /work | 短；注意 URL 需带扩展名或改文件名（工具按扩展名分发；GeoLibre 对无扩展名 URL 有显式报错逻辑，`wasm-client.ts:717-726`） |
| **服务端上传件 GeoTIFF** | `fetch` → `isTiff` 校验 → `/work/dem.tif` | 短；原样字节直喂，是最省事的栅格路径 |
| **服务端上传件 GPKG / FGB** | 同上按 `.gpkg`/`.fgb` 直喂 | 短（单文件格式）；注意 GeoPackage 里多图层时工具取法 |
| **服务端上传件 SHP** | 上传件是 zip → 前端解包 → `.shp+.dbf(+.prj)` 三件作为**三个 input 键**放入 /work | 中；多文件格式是 memfs 下唯一别扭的矢量格式（GeoLibre 结果方向也因多文件选择 zip 打包） |
| **服务端上传件 CSV** | 无直接支持：先服务端转 GeoJSON（现有 ref_id 机制即可）或 DuckDB-WASM 转 | 长；建议不走 WASM 工具直接吃 CSV |
| **data_fabric S3 COG（栅格）** | `runTool("extract_cog_subset", {args:["--url=<s3 cog>", "--bbox=…", "--bbox_crs=…"]})` 字节范围窗口提取 → 窗口 COG → 再喂工具；或直接把 `--url` 传给该工具后接力 | **大数据下的最优栅格路径**（不整文件下载）；前置条件：对象存储 CORS 允许跨域 Range（`tools.mjs` 注释明确要求） |
| **data_fabric GeoParquet** | 直喂：`/work/x.parquet`（§1.1 特性统一推断）；或库层 `vector_to_binary`/`vector_to_arrow_ipc` 读出后转 GeoJSON 再喂 | 中；建议 POC 时先验证 `.parquet` 工具输入，失败则走库层中转 |
| **data_fabric PMTiles（矢量 MVT）** | **不能直喂**：工具读不了 MVT/PMTiles。需 JS 先解码（或经 DuckDB-WASM）成 GeoJSON；`pmtiles_extract` 只做 archive→archive 子集，不产出可喂工具的矢量 | 长；PMTiles 目前只在**输出方向**有价值（见 5.2） |
| **data_fabric S3 上的普通 GeoTIFF（非 COG）** | 只能整文件 fetch（`extract_cog_subset` 对普通 GeoTIFF 也要求能定位 IFD；whitebox-wasm README 提供了尾目录普通 TIFF 的 `CogStream.from_windows` 前缀解析，但那是库层代码） | 中；建议入 fabric 时统一转 COG |

### 5.2 结果回显（输出端）

- 矢量结果：默认 GeoJSON FC → 直接挂 MapLibre GeoJSON source，与现状一致；也可让工具直出 `.pmtiles`（`vector_to_pmtiles`，MVT 金字塔，可设 simplify/drop_rate、max_zoom 默认 14，[geolibre-tools `vector_to_pmtiles.rs`](https://github.com/opengeos/geolibre-rust/blob/main/crates/geolibre-tools/src/vector_to_pmtiles.rs)）。
- 栅格结果：COG 字节（f64/uint8），**不能直接当 XYZ PNG 瓦片用**。三个选项：(a) 浏览器内 `write_pmtiles`（栅格经色带渲染成 PMTiles PNG 金字塔）或 `raster_to_tiles`（写 `{z}/{x}/{y}.png` 树）——**本机实测 2048² DEM 分别 3.25 s→5.01 MB PMTiles、2.52 s→XYZ PNG 树**；(b) 库层 `CogStream` 客户端直读渲染（GeoLibre 的做法）；(c) 回传服务端走现有 raster_tile_service。**(a) 是引入 WASM 引擎后保持「瓦片下发」体验的最短自洽路径**。
- 纯数值输出（`file_out`）：csv/html/json 按 `--output` 扩展名给出（`wasm-client.ts:409-456`）。

### 5.3 结论

- **最短路径：ref_id GeoJSON → vector_in（零转换）**；栅格侧最短是**服务端 GeoTIFF 原样字节**，但**体量最优**的是 **S3 COG + extract_cog_subset 字节范围窗口**。
- 需要补齐的转换件只有三个：SHP zip 解包（前端 unzip）、CSV→GeoJSON（服务端已有）、MVT-PMTiles→GeoJSON（仅当要算 fabric 的 PMTiles 源）。
- 引擎分工建议与 GeoLibre 一致：**浏览器 WASM 做中小数据的交互式分析（阈值见 §3.4），超阈值回落现有服务端流水线**；两条路径共用同一工具清单（manifests 可离线枚举），降低双实现漂移。

---

## 来源清单

**官方文档**
- GeoLibre 官网 Processing 指南（双工具箱、WASM 默认引擎、COG 输出、sidecar 分工）: <https://geolibre.app/user-guide/processing/>
- GeoLibre 主仓库 README（工具分类计数、技术栈、MIT）: <https://github.com/opengeos/GeoLibre>

**源码（geolibre-rust，npm 包 geolibre-wasm 的源仓库）**
- README（双层架构、runTool 示例、COG/WMS/XYZ/PMTiles 提取、~4 GiB 限制）: <https://github.com/opengeos/geolibre-rust>
- `npm/tools.mjs`（`exec()` memfs、`materializeInput`、JS 拦截工具、Range 请求）: <https://github.com/opengeos/geolibre-rust/blob/main/npm/tools.mjs>
- `npm/README.md`（工具层 API 契约、输出格式约定、限制声明）: <https://github.com/opengeos/geolibre-rust/blob/main/npm/README.md>
- `crates/geolibre-cli/src/main.rs`（argv→ToolArgs 契约、manifest schema、GeoJSON fixture 测试）: <https://github.com/opengeos/geolibre-rust/blob/main/crates/geolibre-cli/src/main.rs>
- `crates/geolibre-wasm/src/vector.rs`（库层内存矢量格式清单；Shapefile 未暴露）: <https://github.com/opengeos/geolibre-rust/blob/main/crates/geolibre-wasm/src/vector.rs>
- `crates/geolibre-tools/Cargo.toml`（wbvector `geoparquet` feature 开启）与 `crates/geolibre-tools/src/vector_to_pmtiles.rs`（矢量→PMTiles 工具）: <https://github.com/opengeos/geolibre-rust/tree/main/crates>
- `demo/index.html`（文件选择器→arrayBuffer→runTool 最小样例）: <https://github.com/opengeos/geolibre-rust/blob/main/demo/index.html>

**源码（whitebox-wasm，whitebox_next_gen 的 WASM fork；733 工具）**
- README（733 工具、格式支持、CogStream、Limits、WASI CLI 示例）: <https://github.com/opengeos/whitebox-wasm>
- `crates/wbvector/src/lib.rs`（扩展名读写分发）、`crates/wbvector/src/shapefile/mod.rs`（.shp/.dbf/.prj）、`crates/wbvector/Cargo.toml`（feature 定义）: <https://github.com/opengeos/whitebox-wasm/tree/main/crates/wbvector>
- `crates/wbraster/src/lib.rs`（栅格格式表）、`crates/wbtools_oss/Cargo.toml`（工具层依赖与特性）、`crates/wbhdf/`（HDF4，非 .dep/.tas）: <https://github.com/opengeos/whitebox-wasm/tree/main/crates>

**源码（GeoLibre 主应用，Tauri v2 + React；浏览器喂入与运行时编排）**
- `packages/processing/src/wasm-client.ts`（runWhiteboxToolWasm：格式校验、GeoJSON 序列化、输出打包、buffer 预投影）: <https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-client.ts>
- `packages/processing/src/wasm-tool.worker.ts`、`wasm-tool-runner.ts`（Worker 化、~23 MB 编译、fresh /work、60 s dissolve 案例）: <https://github.com/opengeos/GeoLibre/tree/main/packages/processing/src>
- `apps/geolibre-desktop/src/lib/whitebox-layer-inputs.ts`（整文件 fetch 喂栅格）: <https://github.com/opengeos/GeoLibre/blob/main/apps/geolibre-desktop/src/lib/whitebox-layer-inputs.ts>
- `packages/processing/src/terrain-viewshed.ts`（交互式 viewshed 走 Terrarium 瓦片自研路径）: <https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/terrain-viewshed.ts>
- `apps/geolibre-desktop/src/lib/offline-tiles.ts`、`offline-regions.ts`（Service Worker 仅底图瓦片缓存）: <https://github.com/opengeos/GeoLibre/tree/main/apps/geolibre-desktop/src/lib>

**实测（本机，2026-08-28）**
- `npm install geolibre-wasm@1.5.2` 于 Node v26.8.1：产物字节数（`ls -l` + `gzip -k`）、`listTools()` 计数 1063、§3.2 基准脚本（项目内 `.scratch/geolibre-bench/`：`bench2.mjs` 栅格/矢量、`viewshed2.mjs`、`bench3.mjs` 显示链路，日志 `bench2.log`/`bench3.log`）。
