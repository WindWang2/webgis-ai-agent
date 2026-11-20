# TEST_MATRIX — G20 定向验证结果

执行环境：Windows / Python 3.13.9 / rasterio 1.5.0 / numpy 2.4.4 / Node 24.14.0 /
vitest 4.1.12 / pnpm 10.18.3（离线 store，hoisted node-linker）。数值库线程
`OMP/OPENBLAS/MKL_NUM_THREADS=1`；vitest `--maxWorkers=1`；pytest 无 xdist。

## 命令与结果（修复后复跑）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/unit/raster_authoring -q --no-cov -o timeout=900` | **44 passed**（8 warnings：rasterio `round_shape` 弃用，基线既有；含复审残留 P2 修复回归 2 项） |
| `python -m pytest <18 个基线 + 新增文件> -q --no-cov -o timeout=900`（含 G20 全套、栅格、schema、mutation parity、SVG 孪生、tool registry、mapspec_source、API/字段闸） | **352 passed, 1 skipped**（最终复跑） |
| `python -m pytest tests/test_adr_numbering_contract.py -q` | **3 passed, 1 failed**：唯一失败为基线 ADR 编号撞号（见下） |
| `python -m pytest tests/unit/test_mapspec_source.py -q` | **24 passed**（验证 store_data additive 分支不回归） |
| `python -m ruff check <本任务所有 Python 改动>` | **All checks passed** |
| `git diff --check` | 干净 |
| `pnpm exec tsc --noEmit` + `tsc -p tsconfig.test.json --noEmit` | **EXIT=0 / EXIT=0** |
| `pnpm exec eslint <本任务所有前端改动> --max-warnings 0` | **EXIT=0** |
| `vitest run <9 files> --maxWorkers=1` | **111 passed**（9 files） |
| `API_SNAPSHOT_UPDATE=1 …` + `API_CONTRACT_FIELDS_UPDATE=1 …` | 快照按仓库既定流程刷新；刷新后两个闸 **2 passed** |

## 复用兼容回归（全部绿）

`test_raster_spec.py`、`test_raster_tile_service.py`、`test_raster_runtime_v3.py`、
`test_raster_stretch_payload.py`、`test_ts_projection_contract.py`、
`test_mutation_registry_consistency.py`、`test_mutation_parity_corpus.py`、
`test_mapspec_schema_v14/v17/v18`、`test_mapspec_to_svg.py`、
`test_mapspec_to_svg_expression_parity.py`、`test_tool_registry.py`、
`test_tool_registry_errors.py`、`test_api_compatibility.py`、
`test_field_contract.py`、`compiler.test.ts`、`reconciler.test.ts`、
`runtime.test.ts`、`layer-style-panel.test.tsx`、`key-completeness.test.ts`。

## 新增用例与任务场景映射

| ID | 用例 | 断言 | 结果 |
|---|---|---|---|
| A1 | 2 波段默认 / 二元 RGB | `single [1]` + `BAND_COUNT_UNSUPPORTED` | ✅ `test_raster_expression_contract.py` |
| A2 | 非法 band/NaN/反向/未知色带/重采样/分类+插值 | 各具体 code，零写入 | ✅ 同上 + HTTP 旅程 |
| A3 | 4 波段 3/2/1→4/3/2 | 真实 tile PNG 像素不同（z=0/x=0/y=0 全球网格） | ✅ `test_band_combination_and_colormap_change_real_tile_pixels` + HTTP 旅程 |
| A4 | 单波段色带+范围 | 像素变化、图例端点同步、数据指纹不变 | ✅ 同上；`legend_swatches` 同源 LUT |
| A5 | 手动 stretch 跨瓦片/冻结 | 端点冻结进 style_id；重复渲染缓存命中 | ✅ `test_manual_stretch_is_frozen_and_cache_isolated` |
| A6 | 缓存隔离 / 同源双层 | 两层不同 style_id 且互不覆盖 | ✅ `test_commit_restores_style_and_two_layers_do_not_cross_color` + `compiler.numeric-raster.test.ts` |
| A7 | 提交/恢复/撤销语义 | **单笔** mutation、reload 后 style_id 一致、CAS 冲突零写入 | ✅ `test_single_mutation_first_mount…`、`test_concurrent_commit_rejection_writes_nothing`、service/HTTP 旅程 |
| A8 | stale_style / 数据修订 | 旧 style_id 409、跨会话 ref 404 | ✅ HTTP 旅程 |
| A9 | probe 源值 / CRS / scale / 单位 | (0,0)=100/400、scaled=205、单位 unknown | ✅ `test_probe_reads_source_pixel_with_crs_and_scale` |
| A10 | 点在外 / mask / 无 CRS | 三种可区分状态 | ✅ `test_probe_distinguishes_outside_mask_and_no_crs` |
| A11 | 0 有效 vs nodata=0 | 前者有效、后者 mask，统计一致 | ✅ 两个独立用例 |
| A12 | 全 nodata / 常值 | 全透明、无伪极值、图例 constant | ✅ `test_all_nodata_and_constant_have_no_pseudo_extremes` |
| A13 | 分类 nearest | 契约拒绝插值；快照按 spec.resampling | ✅ 契约测试 + `render_snapshot` |
| A14 | 展开 clip 域 | 重新读真实窗口（服务端拉伸端点改变即重读源）；uint8 载荷不参与检视 | ✅ 拉伸改变像素用例 + probe 走源像元 |
| A15 | 权限 | 跨会话 descriptor/probe/tile 失败且不泄漏路径 | ✅ HTTP 旅程 |
| A16 | 预算/失效/缓存 | 点数/波段/桶/窗口上限；`put_if_current`；源解析 5s TTL 缓存 + ≤16 波段有界读 | ✅ `test_source_resolution_is_cached_and_band_bounded` + HTTP 旅程 |
| A17 | 图例同源 | LUT 单源、RGB 无连续色标、别名解析 | ✅ `test_colormap_aliases_resolve_to_a_real_lut` + 导出 sidecar |
| A18 | 导出冻结 | PNG 可读 + 图例 style_id 一致 + 修订冲突 409 + 能力 URL | ✅ `test_export_is_real_png_and_frozen_to_one_revision` + `test_export_download_is_always_a_signed_capability` |
| A19 | SVG 离线 | base64 data URI，无 session_id | ✅ 导出测试 |
| A20 | Pi 工具旅程 | 4 工具真实注册/派发，UI/Pi 同错误码，回执有界 | ✅ `test_raster_expression_tools.py` |
| A21 | 旧 PNG/底图/旧 tile | 旧 imageRef/raster-opacity 与 style=None 回归 | ✅ `compiler.numeric-raster.test.ts` + 4 个旧栅格测试文件 |
| A22 | UI 契约 | 默认 auto 不伪造精确范围、逐通道 dirty 可提交、快速点击 A/B 抑制、四态 | ✅ 前端 `contracts.test.ts`/`state.test.ts`/`tile-url.test.ts` |
| A23 | 描述子截断/直方图披露 | 原始 vs 降采样边、`bands_truncated` | ✅ 评审修复回归 |
| A24 | 宽波段 probe 截断 / 失效缓存 | >16 波段 default probe 返回 16 个波段 + `truncated=True`；ref 失效清缓存 | ✅ `test_wide_band_probe_is_truncated_and_disclosed`、`test_source_cache_is_invalidated_with_ref_lifecycle` |
| A25 | 单笔事务与 CAS 零写入 | 首挂载 `mutations==1`、version 在场、CAS 拒绝后 MapSpec 逐字未变 | ✅ `test_single_mutation_first_mount…`、`test_concurrent_commit_rejection_writes_nothing` |
| A26 | 能力 URL 绑定 filename | 登录 principal 也带 exp/sig；签名输入含具体文件名 | ✅ `test_export_download_is_always_a_signed_capability` + 复审源码核对 |

## 固定测试数据（现场生成，不签入二进制）

`tests/unit/raster_authoring/helpers.py::write_tif`：
1/2/4 波段、nodata=0、零值有效、全 nodata、常值、非 WGS84（EPSG:3857 经
CRS 变换检视）、全球粗网格（64×32，z=0 瓦片可稳定采样）。波段 tag 显式声明
units/scale/offset；波段角色从不由 index 推断。

## 基线既有失败（隔离证据，非本任务引入）

1. `tests/test_adr_numbering_contract.py::test_adr_numbers_are_unique`：
   `{'0286': 2, '0287': 2}`。主工作区 BASE_SHA 的 `docs/adr/` 同样存在
   `0286-*.md`×2 / `0287-*.md`×2（前批并行 PR 各占同号）。本任务 ADR 文件
   `docs/adr/G20-RASTER-EXPRESSION.md` **不含数字编号**，不新增撞号。
2. `tests/unit/gis_tool_conformance/test_live_registry_audit.py::
   test_live_registry_completeness_ratchet`：`binding_unbacked 66 > ceiling 64`。
   主工作区基线 `collect_live_facts(bootstrap=True)` 实测 `tool_count=400,
   binding_unbacked=66`；本分支 `404 / 66`，四个新工具均为
   `no_capability_binding`（not_evaluated），未增加债务。
3. `test_live_registry_has_no_structural_fail` / `ref_offload_disclosed_set` /
   `STATUS_FAIL==0` 在本分支已恢复为绿（初版曾因 `capabilities` 悬空和缺
   `network`/`produced_refs` 引入 4 个 fail，已全部修复）。
4. OpenAPI 快照 master 过期（issue #2007 / #2035 的 `UndoAgentCommitBody`），
   本任务按仓库既定流程刷新，PR 明确说明。

## 未运行项目（如实披露）

- 线上 CI：按任务书不等待、不轮询。
- 真实浏览器 Playwright 地图点击旅程：本地未起完整前端服务；地图点击链以
  `publishMapClick` + `subscribeMapClick` 纯函数与 state reducer 单测覆盖，
  HTTP/瓦片/导出均通过 `httpx ASGITransport` 真实 FastAPI 路由验证。
- 付费 LLM：未调用；Pi 旅程使用 `ToolRegistry.dispatch` 的真实注册工具入口，
  未使用仓库 `PI_JOURNEY_E2E=1` 的 uvicorn+stub node 进程（环境无该跳板，
  以等价的工具 dispatch 断言替代并如实标注）。
- 完整 publication PDF 渲染链：明确拒绝而非静默丢层（sidecar 披露）。
