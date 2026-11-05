# SOURCE_INVENTORY — 制图域 D0 清点（勘察 subagent + 主 agent 核对，基准 40cda057a）

> 本清点以本 worktree HEAD（origin/master 40cda057a）为准核对过行数/存在性；
> 标注四件套、layout_solver V2–V4、publication_ir、export_lineage 等行数均已实测一致。

## A. 资产清单（"绝不能重做"）

| 资产 | 位置 | 要点 |
|---|---|---|
| MapSpec schema | `app/lib/cartography/mapspec_schema.py`（v1.9 = KNOWN 1.0–1.9） | layout.components(:827)/frames/labels/component_links/composition；MapSpecLayerLabel(:443 field/mode all\|top_n\|hover_only/zoomBands)；ComponentPlacement(anchor/floating)；MapSpecFrame；MapLabelConfig(collision="deterministic") |
| layout solver | `app/lib/cartography/layout_solver.py`（795 行） | V2 solve_component_layout(:117)；V3 solve_layout_v3(:309, width_units 1–4/collision_group/avoid_zones/compact)；V4 solve_layout_v4(:558, collapsible+min_width_units/RepairStep/healed/collapsed_ids/hidden_ids) |
| layout 几何 | `app/lib/cartography/layout_geometry.py`（432 行） | Rect/CanvasSpec/SafeArea(:104)/safe_area_for(:180)/zone_rects/check_component_bounds(:362)/layout_geometry_report |
| 旧约束检测 | `app/lib/cartography/layout_constraints.py`（92 行） | ZONE_CAPACITY/EXCLUSIVE_ZONES/detect_collisions/detect_orphan_components（QA 报警，行为冻结） |
| 版面合成决策 | `app/lib/cartography/component_composer.py`（300 行） | SINGLETON/COLLAPSIBLE_TYPES、REPAIR_ACTIONS(change_anchor\|shrink\|collapse_to_overflow\|hide_lowest_priority\|keep_required)、plan_layout_repairs |
| 组件工厂 | `app/services/gis_harness/components.py`（949 行） | 全组件工厂/build_default_components(:543)/mutate/remove/duplicate |
| 组件契约 | `app/contracts/cartography_components.py:124` | CartographyComponent + MULTI_INSTANCE_TYPES/normalize_placement |
| 组件注册/模板/ABI | component_registry.py（1165 行）/ component_templates.py（706）/ composition_templates.py（412）/ component_abi.py（495, ADR-0214） | 前端镜像 component-catalog.generated.json（schemaVersion 5） |
| 标注四件套 | label_engine.py（391）/ label_collision.py（169）/ label_plan.py（662）/ label_typography.py（374） | LabelCandidate 8 方位/沿线/面内；solve_export_labels；FieldStats+ZoomBand+LabelStrategy；is_cjk/estimate_label_box(CJK 1.0em·其他 0.6em)/fit_label_text/wrap_label_multilingual/keep_upright_export_twin |
| TS 标注孪生 | frontend/lib/label-geometry.ts + mapspec-compiler/label-solver.ts（MAX_LABELS_PER_EXPORT=400）+ mapspec-runtime/label-grid.ts + label-layout.ts（WARN .10/FAIL .25/SEVERE .50/EXTREME 1.50 阈值梯） | 跨树共享 golden：label-solver.test.ts 直接读 tests/cartography/golden_corpus/label_collision/ |
| legend 单源 | render_scene.py:217 derive_legend_items ↔ frontend/lib/map-kit/legend-model.ts | 对拍测试 test_legend_model_parity.py + legend-model.parity.test.ts |
| 出版版面 | `frontend/lib/export/layout-description.ts`（PublicationLayout v1 纯函数）+ `app/lib/cartography/layout_description.py`（镜像 :291 build_publication_layout；LAYOUT_IR_VERSION=2；export_bounds_for_frame；compute_nice_scale） | 双侧 golden：test_layout_description_golden.py / layout-description.golden.test.ts |
| 出版 IR | publication_ir.py（717 行）AtlasPolicy/PagePaper/PageLayoutIR/PublicationIR(content-addressed)/plan_publication_pages/publication_preflight；atlas_layout.py | #1871 图册线消费 |
| PDF 字体 | frontend/lib/export/pdf-font.ts（NotoSansSC fetch→b64→jsPDF VFS）+ frontend/public/fonts/NotoSansSC-Regular-subset.ttf + app/lib/cartography/fonts/（同字体）+ pdf_renderer.py `_register_vendored_cjk_font` | 降级码 pdf_cjk_font_embedded/pdf_text_rasterized_cjk |
| 高 DPI | frontend/lib/export/highdpi.ts（rerendered/degraded-native/skipped + watchdog）+ map-kit/oversample.ts + tile-zoom-plan.ts | |
| 导出主管线 | frontend/lib/map-kit/exporter.ts（~1900 行 runExport/composeExportSpec pending 冻结叠加/hasNonWinAnsiChars）+ export-chrome.ts（ExportDegradationCode ~30 码，词表权威 render_diagnostics.py）+ publication-export.ts/vector-svg-export.ts/frame-composer.ts | |
| 导出 receipt | app/services/export_lineage.py（ADR-0211，gis_chapter["export_receipts"] 唯一生产方，MAX_RECEIPTS=8） | |
| 质量回路 | quality_loop.py（864 行 MAX_REPAIR_ITERATIONS=2/cartographic_fingerprint:269/review_cartography:651）；selfheal_actions.py；runtime_repair.py；scene_selfheal.py | **#1965 改动中 → 本任务只读** |
| 语义检查 | semantic_checks.py（14 码）+ semantic_checks_maplevel.py + semantic_checks_representation.py | CartographyFinding(severity/evidence_class/repairability/suggested_fix) |
| 视觉观察 | app/services/gis_harness/visual_observation/（provider.py rules_only/vlm/hybrid 墙钟预算 20s/60s、auto_repair.py BUDGET_KEY、taxonomy） | L1–L5 权威：docs/cartographic-closed-loop.md |
| L1–L5/harness | pi_agent_harness.py:1807 _success_levels；visual_evaluator.py derive_goal_satisfaction(:629)；lifecycle_engine.py:202（expected_revision 乐观锁/_visual_heal_ledger/反幻觉网关） | goal_satisfaction/contracts.py NOT_EVALUATED |
| golden 体系 | tests/cartography/golden_corpus/{goldens,label_collision(6),legend_model(6),layout_description,layout_ir,publication_ir,render_scene,raster_stretch,composition_selection,context_matrix,h02_scenarios}；fixtures/standards_cases 230 例 ×6 profile；carto_ir_corpus v15 | pytest marker `cartography` = release-blocking |
| 多尺度/设计候选/图册（v1.8/v1.9） | app/lib/cartography/multiscale/、design_candidates.py、atlas_job_spec.py、atlas_batch fixtures（#1876–#1880 已合并） | 禁重做基线 |

## B. D1–D5 判定（详见 PLAN.md 需求矩阵）

D1 partial（离散 width_units、无 spec 级约束 DSL、safe-area 未贯通导出）；D2 partial（导出抑制披露未闭环、图元冲突仅网格级）；D3 implemented 为主（缺口 per-tile 披露 + 六场景对账证据）；D4 partial（缺版式/标签发现词表；quality_loop 被占用须绕开）；D5 partial（点密度/长名称/对比/研究图件四族缺成套 fixture）。

## C. 跨入口一致性机制

1. legend 派生单源 + 双侧对拍；2. 组件目录生成物流（exportedFrom=component_registry.py）；3. layout-description 双实现镜像 + golden；4. 共享 golden corpus 跨树消费；5. 降级词表权威在后端 render_diagnostics.py；6. 工具产出统一漏斗 ToolDispatchService→MapSpec→legend_spec 标记；7. layout.composition 指纹（template_id/contract_fingerprint/ABI 版本/applied_revision）入 cartographic_fingerprint。

## D. 测试命令（本机实测口径）

- Python（主检出 .venv 复用，worktree 源码）：`pytest -m cartography --no-cov --timeout=120 --timeout-method=thread -q`（全 lane）；定向 `pytest tests/cartography/test_<x>.py -q`；lint `ruff check app/ tests/ scripts/ main.py manage.py`；mypy `python -m mypy`（app/core 棘轮）
- 前端（frontend/，pnpm 唯一）：`pnpm test`（vitest run）/ `pnpm typecheck` / `pnpm lint`（--max-warnings 0）/ `pnpm build`
- 本机并行约束：-j2（CMAKE_BUILD_PARALLEL_LEVEL=2/MAKEFLAGS=-j2/RAYON_NUM_THREADS=2/OMP_NUM_THREADS=2/OPENBLAS_NUM_THREADS=2/MKL_NUM_THREADS=2）；不同时多 subagent 跑全量测试/前端构建
