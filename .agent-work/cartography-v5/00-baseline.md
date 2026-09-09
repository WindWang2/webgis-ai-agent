# Cartography V5 — Baseline (Phase A)

- branch: `feat/cartography-v5-rendering-engine`
- worktree: `/home/kevin/projects/webgis/cartography-v5`
- base: `origin/master` = `445ad30ea026ffa2630234ff04f3dd222639c375`
- date: 2026-09-09

## 仓库平台现状（继承，不重建）
- GIS Harness V4 / Spatial Science V3 / Workbench V4 / Extension V1 / Quality V1 / Data Control V4 / GeoCompute V5 均已合并。
- 制图主链路: `app/lib/cartography/**`（label_engine, pdf_renderer, raster_render, layout_solver, component_renderers…）+ `app/services/mapspec/**`（pipeline/store/coordinator/lifecycle/composite）+ 前端 `frontend/lib/map-kit/**`（exporter, export-chrome）与 `frontend/lib/mapspec/**`。
- 权威 parity 矩阵: `app/lib/cartography/component_renderers.py`（docs/cartography/renderer-parity-matrix.md 为生成视图）。
- 已知诚实缺口（master 文档登记）: 小倍数/cartogram planned；python 孪生 `mapspec_to_svg` 不含 chrome marginalia；table_panel 仅 interactive。

## Epic 已知 gaps（待审计证实）
1. SVG export 200+ 字符 label 可能完整嵌入，无 truncation/overflow warning。
2. analysis-converter 路径 `SetLayoutIntent(legend={"visible": False})` 可能声明成功但 committed spec 仍 visible。
3. small_multiple / cartogram / before_after export runtime 不完整。
4. live/PNG/PDF/SVG 是否共享同一 MapSpec/scene truth 待证。
5. MapLibre native terrain/hillshade、vector PDF、text embedding 降级披露待审计。

## 审计任务分派
- Subagent A: 后端渲染链审计（mapspec 提交语义 / label engine / pdf+svg / export runtimes / terrain / diagnostics / xfail+known limitations）
- Subagent B: 前端渲染/导出链审计（live scene / exporter / export-chrome / label 渲染 / visual regression / legend visibility 链）
