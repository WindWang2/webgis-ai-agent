# GeoLibre Whitebox WASM 工具箱的可消费形态与包体调研

> 调研日期：2026-08-27。数据来源：npm registry API、GitHub API/raw 源码、release 资产实测下载。
> 服务于 wayfinder 票据 [ticket-001](../../.wayfinder/geolibre/ticket-001-whitebox-wasm-consumption.md)。

## 结论速览

| 问题 | 结论 |
|---|---|
| 有无独立 npm 包 | **有，且有两个**：`whitebox-wasm`（733 个 Whitebox 上游工具）和 `geolibre-wasm`（同 733 工具 + ~323 个 GeoLibre 自研工具 + 参数 schema API）。无需从源码 vendor。 |
| JS API 形态 | `import { runTool, listTools } from "geolibre-wasm/tools"`；Promise 接口，实为 WASI 二进制 + 内存文件系统 shim；`listManifests()` 返回带参数 schema 的 JSON manifest。GeoLibre 适配层在 `packages/processing/src/wasm-client.ts`（+ worker 池 `wasm-tool-runner.ts`/`wasm-tool.worker.ts`），该包为 `private` 未发布，第三方需自写薄适配层（或复制其 MIT 源码）。 |
| 包体 | 完整工具箱为**单体 wasm，无按类目分包**：`geolibre-cli.wasm` 22.72 MiB（gzip 7.53 MiB 实测）+ 库 wasm `geolibre_wasm_bg.wasm` 6.14 MiB（gzip 1.84 MiB）。支持**整体懒加载**（动态 `import()`，首次用 Processing 才下载+编译，之后复用）。上游 issue #34 正在提案按工具族 feature-gate 裁剪（未合并）。 |
| 许可证 | 全链路无 GPL：GeoLibre/geolibre-rust/geolibre-wasm = MIT；whitebox-wasm = `MIT OR Apache-2.0`；上游 `whitebox_next_gen` 所有 crate 在 Cargo.toml 声明 `MIT OR Apache-2.0`（但**仓库根没有 LICENSE 文件**，见 4.3 注意事项）。Pro 付费工具在 OSS 仓库与 wasm 产物之外。 |
| 维护健康度 | 三层都很活跃：GeoLibre ~每周一个 release（v1.5.0→v2.8.0，2026-06-20→08-27），geolibre-rust 9 周 32 个 npm 版本，whitebox_next_gen 持续提交+crates.io 分批发布。风险：上游核心为个人/单一公司主导，whitebox_next_gen 无正式 release/tag。 |

---

## 1. npm 包形态

### 1.1 两个相关包（均已确认在 npm registry 上）

**[`geolibre-wasm`](https://registry.npmjs.org/geolibre-wasm)**（opengeos/geolibre-rust 的产物）——推荐消费入口：

- 最新 `1.5.2`（发布于 2026-08-19），自 2026-06-17 首发以来 **32 个版本**。
- License: `MIT`；unpacked 30,494,879 B（≈29.1 MiB）；唯一运行时依赖 `@bjorn3/browser_wasi_shim ^0.4.2`。
- 包内容（来自 package.json `files`）：`geolibre_wasm.js/.d.ts`（wasm-bindgen 加载器）、`geolibre_wasm_bg.wasm`（6.14 MiB 浏览器库）、`geolibre-cli.wasm`（22.72 MiB，完整工具箱 WASI 二进制）、`tools.mjs`（68.9 KB，工具执行器）、`tools.d.ts`。
- 双入口：`"."`（GeoTIFF/COG/矢量/LiDAR I/O 库）与 `"./tools"`（工具箱）。

**[`whitebox-wasm`](https://registry.npmjs.org/whitebox-wasm)**（opengeos/whitebox-wasm，"the WASM-ready fork of whitebox_next_gen"）：

- 最新 `0.5.1`（2026-07-28），自 2026-06-16 以来 7 个版本；License `MIT OR Apache-2.0`；unpacked 22,168,591 B。
- 同样双入口：`"."` 是 I/O 库（`whitebox_wasm_bg.wasm` 4.19 MiB），`"./tools"` 是 733 个上游工具的 WASI 运行器（`whitebox-cli.wasm` 16.85 MiB，`tools.mjs` 仅 3.5 KB）。
- 注意：其 GitHub README 有一句"the npm `.wasm` does not include them（工具）"，指工具不在**浏览器库 wasm 模块**里；实测 0.5.1 tarball 中 `whitebox-cli.wasm` 确实随包分发，仅在 import `"./tools"` 子路径时才加载。README 该句表述与实际打包已不一致，以 tarball 实测为准。
- `whitebox-wasm` 的 `./tools` **没有** `listManifests()`——参数 schema 只能靠 `help <tool>` 的 stdout 文本。这是它与 `geolibre-wasm` 的关键差距。

两者关系（[wasm-client.ts 源码注释](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-client.ts)）：`geolibre-wasm/tools` 是 `whitebox-wasm/tools` 的**超集**——同一个 `wbtools_oss` 引擎 + GeoLibre 自研工具，接口字节级兼容，且多了 `listManifests()`。

### 1.2 已排除的包名与渠道

- `whitebox-tools`、`whitebox-tools-wasm`：registry 直接查询 **404 Not Found**。
- `@whiteboxtools/*` scope：**404**，不存在。
- npm 全站搜索 `whitebox`（85 条结果）与 `whitebox-tools`：其余命中均为无关产品（`whitebox-pro-*` 营销自动化套件、`whitebox-vue` UI 库、`whitebox` 0.1.61 "Liveserver Client" 等），GIS 相关的只有上述两个包。
- 白盒上游（John Lindsay / Whitebox Geospatial Inc.）从未自行发布过 npm 包；npm 侧的供货方一直是 opengeos（Qiusheng Wu）。

### 1.3 GeoLibre 平台自身的 npm 包（参考）

[`@geolibre/core`](https://registry.npmjs.org/@geolibre%2fcore)、`@geolibre/map`、`@geolibre/embed` 已发布（2.8.0，MIT）。但工具箱适配层 **`@geolibre/processing` 在 monorepo 中为 `"private": true`，未发布**（[packages/processing/package.json](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/package.json)）——它依赖 `geolibre-wasm: ^1.5.2` 以及大量 `@turf/*`。第三方项目引入工具箱 = npm 装 `geolibre-wasm` + 自写适配层（GeoLibre 的 processing 源码 MIT，可作参考实现）。

---

## 2. JS API 形态与 GeoLibre 适配层位置

### 2.1 执行模型：WASI 二进制 + 内存文件系统

工具不是 wasm-bindgen 导出的函数，而是编译成 **WASI 命令行二进制**（`geolibre-cli.wasm`），JS 侧通过 `@bjorn3/browser_wasi_shim` 提供内存文件系统 `/work` 来跑它（[`npm/tools.mjs`](https://github.com/opengeos/geolibre-rust/blob/main/npm/tools.mjs)，与 `whitebox-wasm` 的 tools.mjs 同构）：

```js
import { runTool, listTools, listManifests } from "geolibre-wasm/tools";

// 列工具 id（执行 CLI 的 `list` 子命令）
const ids: string[] = await listTools();

// 参数 schema：执行 CLI 的 `manifests` 子命令，返回 JSON（仅 geolibre-wasm 有）
const manifests = await listManifests();

// 执行一个工具：input 里的字节被放进内存 /work，stdout 逐行捕获，
// 工具新写出的文件（如 --output=.../out.tif）按文件名返回
const { exitCode, stdout, files } = await runTool("slope", {
  args: ["--input=/work/dem.tif", "--output=/work/slope.tif", "--units=degrees"],
  input: { "dem.tif": demBytes },   // Uint8Array
});
const slopeCog = files["slope.tif"];  // Uint8Array，栅格输出为 COG
```

- CLI 契约（[geolibre-rust README "CLI contract"](https://github.com/opengeos/geolibre-rust)）：`geolibre list` / `geolibre manifests` / `geolibre manifest <id>` / `geolibre version` / `geolibre <tool> --k=v`。参数值类型自动推断（true/false→bool，数字→number，其余→string）。
- **每次 `runTool` 都是全新的 WASI 实例 + 全新 `/work`**，无跨调用状态。
- `wasi.start()` 是单次同步调用、无 yield 点：一旦开跑会阻塞所在线程直到结束——因此必须放进 Web Worker。
- 限制：wasm32 线性内存 ~4 GiB 上限；单线程（rayon 不可用）；大体栅格需用库 wasm 的 `CogStream` 按瓦片流式读，不能整幅解码（[whitebox-wasm README "Limits"](https://github.com/opengeos/whitebox-wasm)）。

### 2.2 工具清单与参数 schema

- **工具清单**：`listTools()` → `Promise<string[]>`；`whitebox_next_gen` OSS 套件共 **733 个工具**，外加 GeoLibre 自研 ~323 个（geolibre-rust README 自研工具表逐行计数），合计 ≈1056，与 GeoLibre 宣传的 "1,000+" 一致（其分类表 Vector 313 / Raster 256 / RS 154 / Hydrology 100 / Terrain 99 / LiDAR 65 / Conversion 49 / Network 26 / Projection 4，合计 1066）。
- **参数 schema**：`listManifests()` 每工具返回一个 manifest（[wasm-client.ts 的 `ToolManifest` 接口](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-client.ts)）：

  ```ts
  {
    id: string;                 // "fill_depressions"
    display_name?: string;      // "Fill Depressions"
    summary?: string;
    category?: string;          // "Hydrology" / "Raster" / ...
    license_tier?: string;      // "Open"
    source?: string;            // "geolibre" | "whitebox"
    defaults?: Record<string, unknown>;    // 参数默认值/示例值
    params?: Array<{
      name: string; description?: string; required?: boolean;
      io_role?: string;         // 输入/输出数据角色（raster_in / vector_out ...）
      data_kind?: string;
      schema?: { kind?: string; /* scalar | enum | input */
                 options?: Array<{ value?: unknown }>; /* enum 选项 */ };
    }>;
  }
  ```

  schema 由二进制内建（Rust 侧 `tool_param_schemas`），**完全离线可用**，不依赖 Python sidecar——这是 geolibre-wasm 相对 whitebox-wasm 的核心增值。另有 5 个网络类工具（`extract_cog_subset`/`extract_wms_subset`/`extract_xyz_tile_subset`/`pmtiles_extract`/`download_osm_vector`）由 JS 拦截实现（用库 wasm 的 CogStream 做字节范围请求），manifest 由 tools.mjs 在 JS 侧补齐。

### 2.3 GeoLibre 适配层位置（供我方参考/借鉴）

| 文件 | 职责 |
|---|---|
| `packages/processing/src/wasm-client.ts`（915 行） | 工具箱主适配层：懒加载 `import("geolibre-wasm/tools")`、`listWhiteboxWasmTools()`、`listWasmToolManifests()`、`manifestToWhiteboxTool()`（manifest→UI 工具卡，含 enum→下拉、默认值预填）、`mergeWasmToolManifests()`（与目录快照对账，wasm manifest 为参数权威源） |
| `packages/processing/src/wasm-tool-runner.ts`（184 行） | Worker 池：按需 spawn、闲置复用（`MAX_IDLE_WORKERS = 1`）、ack 心跳判活、`releaseIdleWasmToolWorkers()` 释放内存 |
| `packages/processing/src/wasm-tool.worker.ts`（58 行） | Web Worker 本体：worker 模块作用域内编译一次 ~23 MB wasm，跨 run 复用编译产物 |
| `packages/processing/src/wasm-convert.ts` | 格式转换（瓦片化等）走同一 runner |
| `packages/processing/src/sidecar-client.ts` | `WhiteboxTool`/`WhiteboxToolParameter` 等共享类型（sidecar 与 wasm 两种后端同构） |
| `apps/geolibre-desktop/src/lib/whitebox-*.ts`（menu-catalog、param-kind、field-params、extent、distance-params、layer-inputs、tool-url） | UI 表单生成（param kind 映射、地图范围参数、"Use map extent" 等） |
| `apps/geolibre-desktop/public/whitebox-catalog-snapshot.json`（827,846 B）+ `scripts/gen-whitebox-menu-catalog.mjs` | 工具目录快照（展示名/分类），运行时与 wasm manifest 对账 |
| `backend/geolibre_server/.../whitebox.py` | Python sidecar 后端的同套工具（wasm 是其浏览器替代，接口对齐） |

依赖声明见 [`packages/processing/package.json`](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/package.json)：`geolibre-wasm: ^1.5.2`；Vite 需把它加进 `optimizeDeps.exclude`（wasm 用 `new URL("./*.wasm", import.meta.url)` 引用）。

---

## 3. WASM 产物包体与加载成本

### 3.1 实测体积

| 文件 | 原始 | gzip -9（实测） | 来源 |
|---|---:|---:|---|
| `geolibre-cli.wasm`（全工具箱，733+~323 工具） | **22.72 MiB** | **7.53 MiB** | [release v1.5.2 资产](https://github.com/opengeos/geolibre-rust/releases/tag/v1.5.2)下载实测 |
| `geolibre_wasm_bg.wasm`（I/O 库） | 6.14 MiB | 1.84 MiB | 同上 |
| `whitebox-cli.wasm`（733 上游工具） | 16.85 MiB | 4.75 MiB | npm 0.5.1 tarball 解包实测 |
| `whitebox_wasm_bg.wasm`（I/O 库） | 4.19 MiB | 1.09 MiB | 同上 |
| `tools.mjs`（执行器 JS） | 68.9 KB（geolibre）/ 3.5 KB（whitebox） | — | 同上 |
| npm 包 unpacked | geolibre-wasm 29.1 MiB / whitebox-wasm 21.1 MiB | — | registry `dist.unpackedSize` |

注：GeoLibre 源码注释称运行时为 "~5 MB (gzipped)"（wasm-client.ts:106），与实测 gzip 7.53 MiB 有出入，疑为注释过时或按 brotli/更早版本估算；以实测为准。

### 3.2 分包能力：**当前无按类目/按工具分包**

- 工具箱是**单一 WASI 二进制**，733+ 工具编死在一个 `wbtools_oss` 里，两个 npm 包的 wasm 均为单体文件；无官方分片、无按类目子包。
- 实际的"分包"边界只有两条：① I/O 库 wasm（`.`）与工具箱 wasm（`./tools`）是两个文件，只用 I/O 不用工具箱时不必下载 22.72 MiB；② 5 个网络类工具在 JS 侧实现，不在 CLI wasm 内。
- **上游动向**：[whitebox_next_gen issue #34](https://github.com/jblindsay/whitebox_next_gen/issues/34)（2026-08-26，open，0 评论）提案给 `wbtools_oss` 加 Cargo feature gates（`hydrology`/`raster`/`gis`/`lidar`/`remote_sensing`，default 仍为 `all`）。提案者本地数据：hydrology-only 构建 rlib 133.58 MiB → 6.87 MiB（**-95%**），构建时间 -73%（注意这是构建产物而非最终 wasm 体积，但证明按族裁剪可行）。若合并，下游可自行编译按需子集——对"只想要地形/水文算子"的场景是明确的减包路径。截至调研日未合并。

### 3.3 首次加载 vs 按需成本（GeoLibre 的做法）

- **整体懒加载**：`wasm-client.ts` 用 `toolsModulePromise ??= import("geolibre-wasm/tools")` 动态导入——**只有用户首次打开 Processing/调用工具时才下载**，不用工具箱的用户零成本。失败时重置 promise 以便重试。
- **首次成本** = 下载 ~7.5 MiB（gzip）+ 在 worker 模块作用域 `WebAssembly.compileStreaming` 编译 22.72 MiB 模块（一次性）。编译产物随 worker 复用：闲置 worker 被 park（最多 1 个），后续 run 免编译；每个 worker 各自编译一份（主线程与 worker 不共享编译缓存），所以 GeoLibre 限制池大小为 1 并提供 `releaseIdleWasmToolWorkers()` 回收内存。
- **按需成本** = 每次 `runTool` 新建 WASI 实例（轻），长任务期间独占该 worker 线程；并发工具可 spawn 额外 worker。

---

## 4. 许可证

### 4.1 逐层结论（全链路无 GPL/传染性条款）

| 层 | 许可证 | 依据 |
|---|---|---|
| GeoLibre（app） | **MIT** | [GitHub repo License](https://github.com/opengeos/GeoLibre)（GitHub API spdx_id: MIT） |
| geolibre-rust（工具箱源） | **MIT** | [LICENSE](https://github.com/opengeos/geolibre-rust/blob/main/LICENSE)："Copyright (c) 2026 Qiusheng Wu" |
| npm `geolibre-wasm` | **MIT** | registry metadata |
| opengeos/whitebox-wasm（含 npm 包） | **`MIT OR Apache-2.0`** | npm `license` 字段、README License 节（vendored `wbgeotiff`/`wbprojection` © John Lindsay, Whitebox Geospatial Inc.，同双许可） |
| **上游 jblindsay/whitebox_next_gen** | **`MIT OR Apache-2.0`（Cargo.toml 声明）** | workspace 根 [Cargo.toml](https://github.com/jblindsay/whitebox_next_gen/blob/main/Cargo.toml) `license = "MIT OR Apache-2.0"`；逐一核实 `wbgeotiff`、`wbprojection`、`wbraster`、`wbvector`、`wbtopology`、`wblidar`、`wbcore`、`wbtools_oss` 全部为同一双许可 |
| 旧版 WhiteboxTools（jblindsay/whitebox-tools，1.x 时代） | MIT | GitHub API license 字段 |

### 4.2 open-core 边界（付费部分不会混入）

whitebox_next_gen 官方声明 open-core 模式：后端引擎 crate 与 `crates/wbtools_oss`（开源工具主体）在本仓库；付费扩展 `wbtools_pro` 在 OSS 工作区**之外**（Cargo 关系图中为 optional/external），通过 `wblicense_core`（开源的授权校验逻辑）对接。GeoLibre 的 wasm 产物只含 `wbtools_oss` + GeoLibre 自研工具；manifest 中 `license_tier: "Open"` 与此对应。这延续了旧版 "Open Core + 付费 Whitebox Toolset Extension" 的商业模式（[whiteboxgeo.com/extension-pricing](https://www.whiteboxgeo.com/extension-pricing/)）。

### 4.3 注意事项（合规卫生）

- **whitebox_next_gen 仓库根没有任何 LICENSE/COPYING 文件**（LICENSE/LICENSE.md/LICENSE.txt/COPYRIGHT/COPYING 均 404，GitHub API license 字段为 None）。许可证文本目前只存在于各 crate 的 `Cargo.toml` 声明与 crates.io 元数据（crates.io 发布强制校验 SPDX 表达式，`wbgeotiff` 已发布 0.1.2）。对引入方建议：a) 依赖 crates.io/npm 发布产物的许可证元数据；b) 有法务顾虑时向上游提 issue 请其补 LICENSE 文件。**不构成 GPL 风险，但属于仓库卫生瑕疵。**
- `wbtools_oss` 为 `publish = false`（不上 crates.io），源码随仓库分发——使用它只能经 opengeos 的 wasm 产物（其已含完整许可声明）或自行 vendor（MIT/Apache 双许可允许）。

---

## 5. 维护健康度（截至 2026-08-27，GitHub API/npm registry 实测）

| 仓库 | 创建 | 最近 push | stars | open issues | 发布节奏 |
|---|---|---|---:|---:|---|
| [opengeos/GeoLibre](https://github.com/opengeos/GeoLibre) | 2026-05-27 | **2026-08-27（当天）** | 6,786 | 19 | GitHub release 约每周一个：v1.5.0（06-20）→ v2.8.0（08-27）共 15 个 |
| [opengeos/geolibre-rust](https://github.com/opengeos/geolibre-rust) | 2026-06-17 | 2026-08-26 | 274 | 1 | npm 9 周 32 版；release v1.4.2（07-30）→ v1.5.2（08-19） |
| [opengeos/whitebox-wasm](https://github.com/opengeos/whitebox-wasm) | 2026-06-16 | 2026-08-26 | 18 | 0 | npm 7 版（06-16 → 07-28） |
| [jblindsay/whitebox_next_gen](https://github.com/jblindsay/whitebox_next_gen) | 2026-03-31 | 2026-08-04 | 88 | 11 | **无 GitHub release/tag**；crates.io 分批发布：wbgeotiff 0.1.2（05-07），wbraster/wbvector/wbtopology/wblidar（07-30），wbprojection 0.3.3（08-04） |

- **wasm 相关 open issue**：whitebox_next_gen 搜索 `wasm`（state:open）命中 **0**；GeoLibre 仅 1 条且与工具箱无关（#961，DuckDB-WASM 读 GeoParquet 的上游问题）。上游 open issue 质量高且处理活跃：#34（feature-gates 提案）、#32（TIFF predictor 读取 bug，08-20 提出）、#33（矢量河网分析挂起，08-24）。
- **风险评估**：三层中真正"供货"给第三方的是 opengeos 的 `geolibre-wasm`/`whitebox-wasm`（Qiusheng Wu，多项目持续高产维护者），活跃度无虞；根上游 whitebox_next_gen 为 John Lindsay/Whitebox Geospatial Inc. 主导、无正式版本发布流程，但 geolibre-rust 通过 vendored-crate 补丁集 + `MAINTAINING.md` 显式管理上游同步（whitebox-wasm README "Releasing"/"Credits" 节），上游变动不会直接冲击 npm 消费方。
- whitebox_next_gen 明确采用"human–AI 协作开发"模式，迭代速度极快（纯 Rust、无 GDAL/PROJ 依赖、全栈自研 I/O），这也意味着**算法行为可能有快速变更**，建议锁版本消费。

---

## 未查实事项与已排除渠道

- **旧版 "whitebox-geospatial-analysis-tools"（Whitebox GAT / Open Core 二进制仓库）的现行许可证**：GitHub API 对 `WhiteboxGeomatics` 组织及同名仓库查询均 404（组织/仓库疑似已改名或删除）；已排除渠道：GitHub REST API（org 列表 + 单仓库）、WebSearch。该遗产链与 GeoLibre 现行 wasm 工具箱**无代码关系**（现行上游为 whitebox_next_gen），不影响引入决策。
- **geolibre.app 线上站点实际下发 wasm 的 CDN/压缩配置**（brotli? 缓存策略）：未查实（属运行时基础设施，无法从仓库确认）；按 gzip 实测保守估算即可。
- **whitebox_next_gen 是否会有官方 GitHub release/tag**：无公开承诺，未见 milestone。

## 来源清单

1. [opengeos/GeoLibre](https://github.com/opengeos/GeoLibre) — README（1,000+ 工具、分类计数、技术栈、MIT）
2. [registry.npmjs.org/whitebox-wasm](https://registry.npmjs.org/whitebox-wasm) — 版本/license/unpackedSize/时间线
3. [registry.npmjs.org/geolibre-wasm](https://registry.npmjs.org/geolibre-wasm) — 同上（32 版本）
4. npm registry 搜索：[`text=whitebox`](https://registry.npmjs.org/-/v1/search?text=whitebox)、`whitebox-tools`、`geolibre`（排除无关同名包；`whitebox-tools`、`@whiteboxtools/*` 404）
5. [opengeos/whitebox-wasm](https://github.com/opengeos/whitebox-wasm) — README（安装/用法/limits/WASI CLI/license/credits）
6. [opengeos/geolibre-rust](https://github.com/opengeos/geolibre-rust) — README（Architecture、CLI contract、Use from JavaScript、GeoLibre integration、License）及其 [LICENSE](https://github.com/opengeos/geolibre-rust/blob/main/LICENSE)
7. [geolibre-rust npm/tools.mjs](https://github.com/opengeos/geolibre-rust/blob/main/npm/tools.mjs) — `runTool`/`listTools`/`listManifests` 实现与 JS 拦截工具 manifest
8. [geolibre-rust release v1.5.2](https://github.com/opengeos/geolibre-rust/releases/tag/v1.5.2) — wasm 资产实测（22.72 MiB / 6.14 MiB）
9. [jblindsay/whitebox_next_gen](https://github.com/jblindsay/whitebox_next_gen) — README（workspace crates、open-core 模式、发布状态）
10. [whitebox_next_gen 根 Cargo.toml](https://github.com/jblindsay/whitebox_next_gen/blob/main/Cargo.toml) 及各 crate Cargo.toml（raw，逐一核实 `license = "MIT OR Apache-2.0"`）
11. [crates.io: wbgeotiff](https://crates.io/crates/wbgeotiff)（API）— 0.1.2，2026-05-07；其余 wb* crate 同 API 查询
12. [whitebox_next_gen issue #34](https://github.com/jblindsay/whitebox_next_gen/issues/34) — Cargo feature gates 分包提案及体积数据
13. [GeoLibre packages/processing/src/wasm-client.ts](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-client.ts) — 适配层核心（懒加载、ToolManifest、schema 映射、catalog 对账）
14. [wasm-tool-runner.ts](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-tool-runner.ts) / [wasm-tool.worker.ts](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/src/wasm-tool.worker.ts) — worker 池与一次性编译
15. [packages/processing/package.json](https://github.com/opengeos/GeoLibre/blob/main/packages/processing/package.json) — `geolibre-wasm: ^1.5.2`、`"private": true`
16. GitHub API repos 元数据：[opengeos/GeoLibre](https://api.github.com/repos/opengeos/GeoLibre)、[opengeos/whitebox-wasm](https://api.github.com/repos/opengeos/whitebox-wasm)、[opengeos/geolibre-rust](https://api.github.com/repos/opengeos/geolibre-rust)、[jblindsay/whitebox_next_gen](https://api.github.com/repos/jblindsay/whitebox_next_gen)（license/stars/issues/pushed_at）
17. GitHub issues API：whitebox_next_gen open issues 全列 + 双方 `wasm` 关键词搜索
18. [jblindsay/whitebox-tools](https://github.com/jblindsay/whitebox-tools)（旧版，MIT）；[whiteboxgeo.com/extension-pricing](https://www.whiteboxgeo.com/extension-pricing/)（Open Core + WTE 商业模式）
19. [geolibre.app/getting-started](https://geolibre.app/getting-started/)（npm workspaces、浏览器构建无 sidecar）、[opengeos.org/geolibre-rust](https://opengeos.org/geolibre-rust/)（在线 demo）
