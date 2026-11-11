# G27 TEST EVIDENCE — 可复跑命令与真实结果

## 0. 环境（实证）

| 项 | 值 |
|---|---|
| 宿主 | `Linux cachyos-3080 7.3.1-1-cachyos`，16 CPU，62 GiB RAM（可用 38 GiB），938G 磁盘（可用 543G） |
| 硬限制 | `ulimit -Hv = ulimit -Sv = 4194304 KB (4 GiB)`，`ulimit -v unlimited` → `Permission denied` |
| BASE_SHA | `8a9a79f76875eb95d31bd448baad971edb2e61d3`（`git fetch origin master` → `FETCH_OK`） |
| worktree | `/home/kevin/projects/webgis/webgis-wt-g27-scale-aware-20261111T144000Z`（干净起点） |
| 分支 | `zcode/g27-scale-aware-portrayal-20261111T144000Z` |
| 隔离 venv | `/home/kevin/.workbuddy/binaries/python/envs/g27-scale-aware`（pytest 9.2.0 / pydantic 2.14.3 / numpy 2.5.4 / pytest-asyncio） |
| 前端依赖 | 独立 `frontend/node_modules`（859M，gitignore）；`npm install --ignore-scripts --package-lock=false`，单进程 10m29s / 704 包 |
| 宿主重任务锁 | `flock -w … /home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock`（pnpm/npm 安装与重任务共用） |
| 编译并行 | 无原生编译；pytest 单 worker、BLAS/OMP/MKL/NumExpr=1；Vitest `--maxWorkers=1` |
| 峰值 RSS | 纯函数级探针 `tracemalloc` 峰值 18–34 KB（见 §4） |

## 1. 后端 deterministic unit/contract（全绿）

```
cd <WT>
TMPDIR=/home/kevin OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
/home/kevin/.workbuddy/binaries/python/envs/g27-scale-aware/bin/python -m pytest \
  tests/cartography/test_scale_portrayal_v1.py \
  tests/cartography/test_scale_portrayal_corpus_parity.py \
  tests/cartography/test_g27_scale_portrayal_wiring.py \
  tests/cartography/test_g27_publication_scale_context.py \
  tests/cartography/test_g27_scale_portrayal_service.py \
  tests/cartography/test_g27_component_portrayal.py \
  -q -p no:cacheprovider --confcutdir=tests/cartography -o addopts=
```
**结果：`72 passed, 2 warnings in 0.87s`**（R2 修复+主线整合后；历史：`45` → `63` → `68`（R1 reviewer 复跑）→ `70`，随 F3/F9/F11 契约用例增长）。

隔离参数理由（实证，非降低门禁）：根 `tests/conftest.py` 会拉起 `app.models.init_db`/faiss/celery/chat 全套链，最小 venv 下原命令 `Exit 137 SIGTERM`；`pytest.ini` 的 `addopts` 含 `--cov=app`（需未安装的 pytest-cov）。`--confcutdir` 是 pytest 官方隔离选项，被测契约与断言不变。完整 CI 车道仍以仓库原命令为准，本方向未改 `pytest.ini`。

子代理首次红→绿实证：`ImportError: cannot import name 'scale_portrayal'`（Exit 2）→ `40 passed`；主 Agent 复跑 `40 passed in 0.56s`。

## 2. 前端契约（等价执行，全绿）

`frontend/lib/carto-ir/scale-portrayal.test.ts` 与 `vitest.g27.config.ts` 已入库供 CI 运行。本地 CLI 被阻断（见 §5），改用**纯 JS tsc 转译 + Node 原生执行同一模块源码**：

```
# 复制 3 个 TS 源文件到 worktree 外目录，仅等价改写 import（@/ → 相对、补 .ts）
cp <WT>/frontend/lib/carto-ir/{scale-portrayal,visibility}.ts <CHECK>/
cp <WT>/frontend/lib/mapspec-runtime/label-layout.ts <CHECK>/
# tsc 5.9.3 纯 JS 转译（--noCheck 跳过类型检查，只做语法转译）
node node_modules/typescript/bin/tsc --target ES2022 --module commonjs \
  --moduleResolution node --skipLibCheck --noCheck --outDir out *.ts
node check.cjs
```
**结果：`G27 frontend contract (tsc transpile + node): 23 checks passed`**。消费同一 `scale_portrayal_cases.json`，逐 case 断言 `topRatio`/`sizeRatio` 边界、A05 全 zoom 恒定、A28、A24。前端 A15 已改为「同 zoom 幂等 + 跨 zoom 档位分化」的可证伪断言（DPI 不在前端解析面，不写恒真断言）；后端 A15 另补带 visibility gate 的 layer 用例覆盖 gated 裁决路径（R2 复审修正）。

前端全量类型检查：
```
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.json
```
**R1 结果**：`TOTAL_ERRORS 1 / G27_RELATED 0`，唯一错误 `toolSlice.ts(32,3) TS1109`（当时开放 PR #2085 在途修复项）。**R2（主线整合后）结果**：`TOTAL 1 / G27_RELATED 0`，唯一错误变为 `lib/map-kit/render-debouncer.ts(1,13) TS1192`（maplibre-gl 无 default export）—— 由 npm 单进程安装版本与 pnpm-lock 版本差异产生的本地基线，非 G27 引入；`toolSlice.ts` 已随 #2085 合入修复。本方向未触碰这两个文件、未降低门禁。

## 3. 生产调用图证据（P0/P2）

| 结论 | 证据 |
|---|---|
| `lod_for_zoom` 唯一既有生产调用 | `app/lib/cartography/representation.py:452` + `analysis_cartography_converter.py:915` |
| `build_label_zoom_bands` / `terrain_maxzoom_for_zoom` / `decimate_budget` 原**零生产消费者** | 全仓 Grep 仅定义与 `tests/unit/test_scene_lod.py` |
| 现在成为真实调用 | `app/lib/cartography/scale_portrayal.py:383-389`（`monkeypatch` 断言存在） |
| 落笔进既有 spec 字段 | `analysis_cartography_converter.py` G27 段 → `res_layer["label"]["zoomBands"]`；`MapSpecLabelZoomBand` 字段逐键匹配 |
| runtime 真实消费 | `frontend/lib/mapspec-runtime/runtime.ts:1811,1819,1884-1887,2157-2181`；`label-layout.ts:65-105,177-203,272` |
| zoom 手势不写 desired | ADR-0054/0057/0058；新增 `webgis:zoom` 为只读 CustomEvent |
| DPI 不进可见性门 | `publication_export.py` `dpi_scale` 仅几何/字号；`export_adjudicate_layer` 不接受 DPI（三种 DPI 裁决逐位相同） |
| MapSpec 版本 | `mapspec_schema.py:87 LATEST_VERSION="2.1"`（未按 ADR-0218 倒退 1.5） |

## 4. P6 性能 / 内存（可复跑）

```
TMPDIR=/home/kevin <venv>/python planning/G27-SCALE-AWARE-PORTRAYAL/perf_probe.py
```
R2 真实规模：**真实构造** 5000 labels / 20000 热力点 / 8000 线网要素，5 次重复，5 个 zoom（3/6/10/14/19）：

| 场景 | 真实要素数 | 冷缓存 ms | 热缓存均值±σ ms | 每档切换 ms | 峰值 traced KB | 状态更新/5 zoom | 持久化 mutation | feature_count 不变 |
|---|---|---|---|---|---|---|---|---|
| large_labels | 5000 | 2.661 | 2.356 ± 0.359 | 0.471 | 34 | 3 | 0 | ✅ |
| heatmap_points | 20000 | 2.391 | 2.230 ± 0.136 | 0.446 | 14 | 3 | 0 | ✅ |
| line_network | 8000 | 2.442 | 2.523 ± 0.602 | 0.505 | 14 | 3 | 0 | ✅ |

R1 F6 修正后（三次复跑每档切换落在 0.446–0.734 ms，宿主 7 路并行负载导致波动，量级一致）：三种规模**真实构造**合成 FeatureCollection（原为描述符数字空跑）；`feature_count_unchanged` 逐轮断言 `len(features)==n`，`mutation_module_loaded` 由 `sys.modules` 实测为 false；`persistent_mutation_calls` 是纯函数路径的推导定值（探针不引用任何持久化 mutation API），非独立计数器实测。字段证据只扫前 50 要素 / ≤32 键，解释面不携带要素载荷、不上传属性；**未靠删数据换基准**。真实浏览器帧时间因 §5 阻断未测。

## 5. 环境阻断（如实记录，未伪造通过）

| 阻断 | 实证 | 处置 |
|---|---|---|
| pnpm 10 多 worker | `Fatal process out of memory: Failed to reserve virtual memory for CodeRange`（3 次，含去 shim + `--network-concurrency=1 --child-concurrency=1`） | 改单进程 `npm install`（704 包成功） |
| Vitest / Vite | `RangeError: WebAssembly.instantiate(): Out of memory: Cannot allocate Wasm memory for new instance`（默认配置 + 最小 `vitest.g27.config.ts` 均复现） | 保留 vitest 用例供 CI；本地用 tsc+Node 等价执行 |
| Node 22 `--experimental-strip-types` | `node:internal/deps/amaro … WebAssembly.Instance(): Out of memory` | 同上 |
| `--poolOptions` 无效 | `CACError: Unknown option --poolOptions`（vitest 4.1.12） | 改用合法 `--maxWorkers=1 --pool=forks` |
| 真实 Pi 子进程 / 本地后端 / 浏览器 E2E | 后端服务栈依赖（`pydantic_settings` 等）未装；Pi vendor 依赖未装；wasm 工具链不可用 | **blocked**；工具/api/service 有隔离 fixture 测试，但不声称 RPC/浏览器全链实证 |
| 真实 `apply_gis_mutation` CAS/undo 引擎链 | `pydantic_settings` → `app.core.config` 未装，最小 venv 无法启动 GISWorldState 门面 | **blocked**（R1 F2）；A19/A20 已降为 partial，测试名不再含 `undo_chain`，不冒充引擎级实证 |
| `/goal` / goal-loop skill | `~/.workbuddy/commands`、`<repo>/.claude/commands` 不存在；无已安装可调用入口 | 按指令「缺失不阻断」，用本会话原生流程推进；未虚构参数 |
| Goal 配额 | 无已授权的真实配额读取/配置入口 | 如实记录有效预算=本会话上下文；未按 3.2 亿空转 |

## 6. 基线 / HEAD 同条件对照

- R1：BASE 与 HEAD 的 `tsc --noEmit` 唯一错误均为 `toolSlice.ts:32 TS1109`（#2085 在途），本方向 0 相关错误。R2：主线整合后该错误消失，剩余 `render-debouncer.ts(1,13) TS1192` 本地基线错误，本方向仍 0 相关错误（reviewer R2 自行复跑确认 `TOTAL 1 / G27_RELATED 0`）。
- 独立 review 两轮（R1 findings → 修复 → R2 复验）见 `REVIEW.md`；reviewer 自行复跑 `68 passed` / `23 checks`。
- 起始工作区 `M vendor/pi`、`.claude/skills/*`、`_tmp_47_*` 全程只读保留，未 checkout/reset/删除。
- 未改 master、未合并、未删他人分支/worktree、未等线上 CI、未改门禁。
