# Ticket: Whitebox WASM 工具箱的可消费形态与包体调研

**Label**: `wayfinder:research` | **Type**: AFK | **Status**: closed
**Blocked by**: —

## Question

GeoLibre 的 Whitebox WASM 工具箱（Whitebox Next Gen + 自研 WASM 工具，1000+ 算子）以什么形态可被第三方项目消费？是否有独立 npm 包？JS API 长什么样（工具清单、参数 schema、执行接口）？WASM 包体多大、能否懒加载/分包？Whitebox 上游与 GeoLibre 的许可证（确认 MIT）与维护健康度（版本节奏、issue 活跃度）如何？

## Resolution

有现成 npm 包，无需 vendor：`geolibre-wasm`（v1.5.2，MIT；733 个 Whitebox 上游工具 + ~323 个 GeoLibre 自研工具）及其上游精简版 `whitebox-wasm`（v0.5.1，MIT OR Apache-2.0；仅 733 上游工具、无 schema API）——推荐 `geolibre-wasm`，其 `listManifests()` 可离线返回每个工具的参数 schema（含 io_role/data_kind/enum options/默认值）。JS 接口为 Promise 形态的 WASI 执行器：`runTool(tool, {args, input})` 在内存 `/work` 文件系统上跑工具并返回 `{exitCode, stdout, files}`；因 `wasi.start()` 同步阻塞，必须放 Web Worker（GeoLibre 参考实现在 `packages/processing/src/wasm-client.ts` + `wasm-tool-runner.ts`/`wasm-tool.worker.ts` 的 worker 池）。包体为单体 wasm、暂无按类目分包：完整工具箱 `geolibre-cli.wasm` 22.72 MiB（实测 gzip 7.53 MiB）+ I/O 库 wasm 6.14 MiB；支持整体懒加载（动态 `import()`，首次使用才下载+编译，之后复用编译产物），按工具族裁剪已有上游提案（whitebox_next_gen#34，hydrology-only rlib -95%）。许可全链路无 GPL（GeoLibre/geolibre-rust/geolibre-wasm 均 MIT；whitebox_next_gen 各 crate 声明 `MIT OR Apache-2.0`，但注意其仓库根缺 LICENSE 文件），维护活跃（GeoLibre 约每周一版，geolibre-wasm 9 周 32 版）。详见 [调研报告](../../docs/research/geolibre-wasm-toolbox-consumption.md)。
