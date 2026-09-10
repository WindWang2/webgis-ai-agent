# Cartography V6 — Baseline & Phase A Audit（真实执行记录）

- branch: `feat/cartography-v6-publication-engine`
- worktree: `/home/kevin/projects/webgis/webgis-ai-agent-cartography-v6`
- base: `origin/master` = `8a33e3a53f0fa53873cace853758445192722dca`
- date: 2026-09-10

## 0. 并行方向盘点（冲突面）

- PR #1170 Cartography V5 **已 merged**（ADR-0118）——本 Epic 直接基线。
- PR #1173 Contextual Cartographic Harness V6 **OPEN**：触点 `app/services/mapspec/lifecycle_engine.py`、
  `app/services/cartography_runtime.py`、`app/lib/cartography/quality_loop.py`、`app/lib/cartography/runtime_repair.py`、
  `app/services/gis_world_state/mutation.py`、CHANGELOG。
  → **本 Epic 触碰面决策**：不改 lifecycle_engine / quality_loop / runtime_repair /
  cartography_runtime 的既有逻辑（只在 mapspec_to_svg、新模块、前端 export 链工作），
  把冲突面压到最小。ADR 编号取 0120（master 已有至 0118；#1173/#1175/#1176/#1177 均 claim 0119，避开）。
- Issue #1082：MapSpec mutation 热路径读放大 —— 本 Epic schema 层**只接冷路径**
  （导出/报告编译边界），禁止给 mutation 热路径加全量校验/解析。

## 1. 渲染/导出五条后端事实链（file:line 证据）

| 链 | 入口 | 消费 scene 语义 |
|---|---|---|
| live | `frontend/lib/mapspec/live-spec.ts` composeLiveMapSpec → `frontend/lib/mapspec-runtime/runtime.ts` → MapLibre；chrome=DOM（`frontend/components/map/map-spec-chrome.tsx`、`frontend/components/map/legends/`）| committed+pending 合成 spec |
| PNG 导出 | `frontend/lib/map-kit/exporter.ts` runExport → composeLayout（canvas）| export-chrome（committed only，V5 已修 pending 合并于 live，export 走 buildExportChrome）|
| 前端真矢量 SVG | `frontend/lib/mapspec-compiler/mapspec-to-svg.ts`（TS 孪生）+ `frontend/lib/map-kit/svg-marginalia.ts` → `frontend/lib/map-kit/vector-svg-export.ts` | 孪生编译 + marginalia 组装 |
| 后端孪生 SVG | `app/services/mapspec_to_svg.py`（948 行）← `app/services/report_service.py:24` 唯一生产调用 | **数据层 + label 截断**（:808 fit_label_text）+ thresholds 执行；**零 chrome/marginalia/legend**（grep legend/marginalia 零命中）|
| PDF | (a) `app/api/routes/map.py:285` /export/pdf → matplotlib 栅格壳（`app/lib/cartography/pdf_renderer.py`，PNG 嵌 A4）；(b) report_service：Jinja2 HTML + WeasyPrint 嵌孪生 SVG（矢量数据无 marginalia）；(c) 前端 jsPDF 栅格 | 三套版式互不一致 |

## 2. 事实源 / 第二事实源

- MapSpec desired-state 唯一事实源：`app/services/mapspec/lifecycle_engine.py`（锁+COW+CAS）+ `app/services/mapspec/store.py`（Redis+磁盘）。
- **平行手维护 schema**：`frontend/lib/mapspec-compiler/types.ts`（TS 全量手写镜像）。后端 dict 契约无 Pydantic 模型；
  version="1.0" 字面量（lifecycle_engine.py:830,857,1865），reconciler.ts:123 仅做相等比较。**无迁移机器**。
- 生成物通道已存在：`app/lib/cartography/export_component_catalog.py:27` → `frontend/lib/map-components/component-catalog.generated.json`（renderDiagnostics 段 + registry parity 测试锁定）。
- 诊断词表唯一权威：`app/lib/cartography/render_diagnostics.py`（17 码，死码门测试）。
- 语义 oracle：前端 `frontend/lib/map-kit/render-scene.ts` describeRenderScene（V5 W10）；后端无镜像。

## 3. Label Engine 现状

- `app/lib/cartography/label_engine.py`（565 行）：solve_labels（GridIndex 碰撞求解、点/线/面候选、角度规整、
  CJK 宽度 estimate_label_box:159）+ fit_label_text/wrap_label_text。
- **生产接线**：仅 fit_label_text 进孪生（mapspec_to_svg.py:808）。solve_labels **生产零接线**
  （render_diagnostics.py:65-67 注释自证）。前端靠 MapLibre live 碰撞；导出 bake 无碰撞。

## 4. Legend 推导散点（Must-have G 对象）

legend_spec 生产者：`app/tools/cartography_tools.py`、`app/tools/spatial_stats.py`、`app/lib/geo_analysis/density.py` 等工具层写入 layer.legend_spec。
消费/推导（≥6 处独立实现）：
1. `frontend/lib/mapspec-compiler/compiler.ts:289,474-493` extractLegendForLayer（compile legend）
2. live：`frontend/components/map/legends/`（legends.tsx slice(0,8) + thematic-legend.tsx）
3. export canvas：`frontend/lib/map-kit/export-chrome.ts` drawChromeLegend
4. SVG marginalia：`svg-marginalia.ts` renderSvgLegend（通用 items，语义由调用方决定）
5. oracle：`render-scene.ts:47-65` legendEntryCount（第三份类型收敛逻辑）
6. `vector-svg-export.ts` legendItemsOf
后端：**无 legend_spec → 图例条目推导**（孪生不画图例）。

## 5. 组件/高级制图现状（Must-have D）

- 组件类型已注册（TS types.ts:169-194 + 后端 `app/services/gis_harness/components.py`）：
  north_arrow、scale_bar、graticule、map_border、inset_map、attribution、title/subtitle、statistics_panel、chart_panel、table_panel、export_layout、methodology_note、uncertainty_panel、decision_panel。
- live DOM chrome + canvas export（export-chrome.ts 2683 行）已渲染大部分。
- **后端孪生/报告 PDF：零组件渲染** —— 出版物（WeasyPrint 报告）无图例/指北针/比例尺/图框。
- inset_map：live 静态投影 + drawChromeInset（V5 登记全链路），后端无。
- terrain/hillshade：`raster_render.py` + 词表 `terrain_3d_scale_caveat`；MapLibre native terrain live-only，导出无。
- cartogram：`cartogram_unsupported` 诚实降级（未实现，登记 planned）。

## 6. 多帧/atlas 现状（Must-have F）

- V5 W9：`frontend/lib/map-kit/frame-composer.ts` ExportRequest.frames（**请求级**，非 spec 级）；
  诊断 atlas_page_skipped / atlas_page_limit_truncated / small_multiple_panel_skipped 已有词表+发射器。
- MapSpec 契约核心无 frames 概念。

## 7. 验证体系现状（Must-have H）

- 后端：tests/cartography/ 33 文件 + golden_corpus/（corpus.py + goldens/）；孪生 parity 字节闸（TS↔Python fmtNum/escape 同链）。
- 前端：26 个 map-kit 测试 + describeRenderScene parity。
- 无像素 diff（视觉门禁依赖语义断言 —— 与 Non-goals 一致，保持）。
- 环境实测：venv Python 3.13.15（系统 python 3.14 无依赖，用 venv）、pydantic 2.13.4、matplotlib 3.11.1、
  **weasyprint 未预装（本次安装 70.0 成功并 smoke 通过；requirements 未登记）**、fontconfig CJK 字体 99 命中、
  node 26 + vitest 可用（worktree 缺 node_modules → symlink 主仓 node_modules）。
- 基线测试：tests/cartography/test_label_engine.py + test_render_diagnostics_contract.py = 35 passed（12.3s）。

## 8. P0/P1/P2 分级（Phase A 结论）

- **P1-A**：无 authoritative typed MapSpec；TS types.ts 平行手维护漂移风险；version 无验证/迁移。（Must-have A）
- **P1-B**：后端孪生/出版 PDF 零 chrome（图例/标题/指北针/比例尺/图框/经纬网）——"publication engine" 缺口核心。（Must-have B/C/D）
- **P1-C**：PDF 用户产物为栅格壳；真矢量 PDF（可选文本、CJK）技术可行（WeasyPrint 70 + 系统 Noto CJK）未实现。（Must-have C）
- **P1-D**：solve_labels 死代码；导出 bake 无标签碰撞。（Must-have E）
- **P2-E**：legend 推导 6 处散点（live slice(8) vs export 全集差异已有诊断，但推导逻辑无单一权威）。（Must-have G）
- **P2-F**：frames 仅请求级；spec 级 atlas 缺席。（Must-have F）
- **P2-G**：后端无 canonical scene 镜像（describeRenderScene 前端私有）——跨语言 parity 无后端 oracle。（Must-have B/H）
- **P3-H**：raster 层进矢量产物无诚实披露码；PDF 字体回退无披露码。（诊断词表缺口）
- planned 诚实登记：cartogram（保持 cartogram_unsupported，不做伪实现）；curved/line label 视架构允许度；
  terrain 导出（保持 terrain_3d_scale_caveat 口径）。

## 9. 资源复杂度与有界性结论

- 孪生编译已有 caps：DEFAULT_MAX_FEATURES=50000/层、timeoutMs 协作超时、MAX_DIAGNOSTICS_PER_EXPORT=64、
  MAX_SVG_LABEL_CHARS=60、INLINE_FEATURE_LIMIT=5000（mapspec_source.py:25）。
- 缺口：atlas 帧数上限在 frame-composer（前端）有 50 页；spec.frames 需同等上限；PDF 栅格嵌入需尺寸/字节上限；
  label 求解需标签数上限（防 20k 要素全标签 O(n log n) + box 检查膨胀）。
