# PR_OVERLAP_MATRIX — 避让矩阵（2026-11-06，基线 40cda057a，勘察 subagent gh 实测）

## A. 开放 PR 交集

### PR #1972（awf-v2 分析封套，head zcode/goal-gis-analytical-foundation-vnext-20261105，+12903/−8411）
- 43 文件：analysis_workflows/、gis_harness/tools.py(+40)、workflow_schema.py、app/tools/（advanced_spatial/analysis_workflow_tools/network_tools）、app/lib/geo_analysis/、tests/unit/gis_harness/、tests/acceptance/gis_agent/（scenarios.json 巨型再生）、tests/benchmarks/、docs/adr/0282
- **与任务目录交集：零**。避让：`app/services/gis_harness/tools.py`、`tests/acceptance/gis_agent/*`

### PR #1965（g3dv2 评审清偿，head zcode/review-g3dv2-fixes，+928/−287）
- **直接交集（本任务只读）**：
  - `app/lib/cartography/quality_loop.py`（+6）
  - `tests/cartography/test_cartographic_quality_review.py`（+31）
  - `tests/cartography/test_render_work_corpus_v1.py`（+4）
  - `tests/cartography/golden/mutation_parity_corpus.golden.json`（±111 再生，双写必冲突）
- 相邻共享（避开或纯增量）：`frontend/components/map/map-panel.tsx`（+49/−12）、`app/schemas/chat_schema.py`

## B. 已合并 PR 能力域（禁止重做清单）

| PR | 能力 | 关键文件 |
|---|---|---|
| #1870 | 表达模型层设计候选/决策 trace/user-wins 选择/语义检查三码 | design_candidates.py、design_decision_trace.py、semantic_checks_maplevel.py、design-candidates.tsx |
| #1871 | 图册 durable job（页绑定/指纹缓存/重试/QA 门/provenance） | atlas_batch/**、atlas_jobs.py、atlas_job_spec.py、publication_export.py（+244/−132）、atlas_export_tools.py |
| #1875 | 联动视图/分页表格/视口聚合 + label/SVG 导出保真 | label_typography.py、mapspec_to_svg.py（双侧）、label-geometry.ts、paged-table-data.ts、viewport-aggregate.ts、resolve-components/layout.ts |
| #1880 | 多尺度（ScaleRepresentationProfile.v1、MapSpec v1.9、语义取舍） | cartography/multiscale/**、mapspec_schema.py(+25/−5)、render_diagnostics.py、scale_profile/**、compiler.ts、carto-ir/* |
| #1836 | 3D 场景运行时 | scene3d/**、render_work_envelope.py、render_work_corpus/** |
| #1835 | DEM 供给 | terrain_ingest.py、basemap_profiles.py（−450 重构） |

## C. 开放 issue 与本任务关系（只读依据，不抢修）

- **#1910 [High] PDF deny-all url_fetcher 拦截 data: CJK 字体（WeasyPrint 回执谎报 pdf_cjk_font_embedded）**：修复落 `app/services/publication_export.py`（#1871 大改 + #1953 S99-011 认领）→ 本任务对该文件只读；仅做前端 pdf-font 语义一致性验收测试（W3.3）
- **#1966 [Med] S14-001..005**：component_abi enum×seed 默认值矛盾、abi 前 32 键绕过、**component_composer.py:276-294 _rehome top-center 独占容量误用（S14-003）**、Jenks 极值、conformance 误报 → component_composer.py 只做非重叠改动（最好避让）
- **#1967 [Low] S14-009..017**：含 **component_composer.py:109 priority 非数字 ValueError（S14-013）** → 同上
- #1826（前端 map-kit 27 条：exporter 注入/atlas 缺字体加载/比例尺）、#1827（components/map 6 条：floating-chrome zIndex/chart-panel bbox）→ 前端相邻域，新文件安全；改动被点名文件前核对不抢修
- #1864 S5b-006（pdf_renderer.py:149 非法颜色致 PDF 全败）、#1951（atlas_layout page_title 冲突等）、#1953 S99-011（publication_export feature_offset 空页）→ 他人认领域
- 不相关批次：#1841-1847、#1890-1908、#1911-1949（data_fabric/适配器/lakehouse/extensions/chat 等）

## D. 同域分支检查

`git branch -r --no-merged origin/master` = 仅 2 支（= 两开放 PR）。7 支历史制图分支全部已合并（#1763/#1755/#1870/#1871/#1875/#1880/#1830）。**当前窗口干净。**

## E. 写入白名单（结论）

可写：`frontend/lib/export/`、`frontend/lib/cartography/`、`frontend/lib/map-components/`、`frontend/components/map/map-components/`（新文件自由；改 table-panel/chart-panel/floating-chrome 前核对 #1827）、`app/lib/cartography/`（除下表）、`app/services/gis_harness/component_composer.py`（非重叠最小增量）、`components.py`、`tests/cartography/` 新文件。

只读/最小：quality_loop.py、test_cartographic_quality_review.py、test_render_work_corpus_v1.py、golden/mutation_parity_corpus.golden.json、publication_export.py、map-panel.tsx、chat.py、chat_schema.py、gis_harness/tools.py、tests/acceptance/gis_agent/*、pdf-font.ts（实现不动，只加验收测试）、mapspec_schema.py + types.generated.ts（最小 additive）。
