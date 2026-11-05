# PLAN — 04_CARTOGRAPHY 出版级制图产品引擎（goal-04-carto-pub）

- 基准：`origin/master = 40cda057a`（2026-11-06 03:48 UTC+8 实测 fetch；任务书快照 b6ac970eb 已过期）
- branch：`zcode/goal-cartographic-publication-vnext-20261105`
- worktree：`/home/kevin/projects/webgis/webgis-wt-goal-carto-pub-vnext`
- 使命：在既有 MapSpec / 组件与模板库 / 分类与 L1–L5 校验之上，补齐出版级**约束布局、标注碰撞治理、中文排版、响应式版面与屏幕-PNG-PDF 输出一致性**；不建第二制图引擎。

## 1. 需求矩阵（D0 判定；依据 SOURCE_INVENTORY.md，全部经本 worktree HEAD 核对）

| # | 需求 | 判定 | 依据摘要 |
|---|---|---|---|
| D1a | 可序列化布局约束 DSL（must-fit/min-max/anchoring/zone/safe-area/overflow/折叠转栏/关键说明不截断） | **partial** | 求解器 V2–V4 有 zone/容量/width_units(1–4 离散)/collapsible/min_width_units；但约束是 solver 入参而非 spec 可序列化字段；无连续 min-max |
| D1b | 确定性 bounded solver（密集图例/长中文标题/多色带/窄版面/安全边距/导出版心；同输入同输出；不以减文字掩盖溢出） | **partial** | 单遍确定性算法已有 + golden 锁定；缺口在"关键说明不能截断"的 must-fit 语义与 safe-area→PublicationLayout 贯通 |
| D1c | user-wins / 不同容器重排不动语义 | **implemented** | composition_contract LOCK_REASON_USER_WINS、grammar_solver pins、composition-repair user-pinned 不动；本任务只补测试锁定 |
| D2a | label candidate 提取（字段真实存在+类型+优先级） | **implemented** | label_plan.py FieldStats/plan_label_strategy；缺"字段不存在不得生成标注"的显式失败码测试（核实后补） |
| D2b | 标注避让/尺度显隐/中英混排/图元冲突 | **partial** | 导出孪生避让+zoomBands+CJK 度量已有；图元冲突仅 LabelGrid 网格级；**导出链标签抑制披露未闭环**（export-chrome 移除了 label_suppressed_too_long 待回归） |
| D2c | presentation consistency（分类/色板/连续分级图例跨入口一致） | **partial** | legend 单源 derive_legend_items + 双侧对拍已有；补"同数据多入口同口径"一致性测试面 |
| D3a | ExportLayoutDescription 规范化（字体/dpi/视口/图例/比例尺/透明度/标签/离线瓦片/版权） | **implemented** | PublicationLayout v1 双侧镜像 + LAYOUT_IR v2 + golden；不动契约本体，只扩充消费面 |
| D3b | revision 锁定 + pending/degraded + 冻结快照 | **implemented** | expected_revision 乐观锁 / composeExportSpec pending 叠加 / 降级码 ~30；补边界测试 |
| D3c | 高 DPI（屏幕/A4/A3/可配置）+ 中文 PDF 可验证字形 | **implemented(前端)** | highdpi rerendered/degraded/skipped、NotoSansSC 双侧；#1910 是后端 WeasyPrint 缺陷（他人修），本任务做非重叠验收 |
| D3d | 缺瓦片披露 | **partial** | 仅聚合级降级码；无有界 per-tile/计数披露 |
| D3e | 导出异常可查 receipt、禁止静默降级 | **implemented** | export_lineage.py ADR-0211；补覆盖测试 |
| D4 | 版式/标签/可读性确定性发现词表 + 安全局部修复（user-wins/有限次数/可信 revision/not_evaluated） | **partial** | 语义检查 16+ 词表与 AUTO_SAFE 修复链已有；缺"版式/标签"专用发现词表与修复动作；**quality_loop.py 被 #1965 占用 → 增量落 selfheal_actions/semantic_checks/新模块** |
| D5 | 六类出版用例（点密度+区级统计/分级设色/连续栅格色条/长名称标签/双图对比/inset+图表研究图件）× 中英 × 16:9+A4/A3 | **partial** | 分级设色/连续色条✓；点密度族缺成套 fixture；长名称无端到端；双图对比后端 golden 缺；研究图件缺成套 |
| D0 | 不重做 #1870/#1880/#1875，不抢修 #1910 等 | 约束 | 见 PR_OVERLAP_MATRIX.md |

## 2. 交付范围（只做 missing/partial 的增量）

**所有权白名单**（写入面）：`app/lib/cartography/`（新模块 + layout_solver/layout_geometry/layout_description/label_*/semantic_checks*/selfheal_actions/render_diagnostics 等扩展）、`app/services/gis_harness/component_composer.py`（只做与 S14-003/013 非重叠的最小增量，或避让）、`app/services/gis_harness/components.py`、`frontend/lib/map-components/`、`frontend/components/map/map-components/`、`frontend/lib/export/`、`frontend/lib/cartography/`、`tests/cartography/`（新文件）、frontend 侧对应测试。

**只读/避让**（PR_OVERLAP_MATRIX.md 详）：`quality_loop.py`、`test_cartographic_quality_review.py`、`test_render_work_corpus_v1.py`、`golden/mutation_parity_corpus.golden.json`（#1965 改动中）；`publication_export.py`（#1910 他人）；`map-panel.tsx`、`chat.py`、`chat_schema.py`、`app/services/gis_harness/tools.py`、`tests/acceptance/gis_agent/*`（#1972/#1965/#1836）；`mapspec_schema.py` + `types.generated.ts` 仅允许最小 additive（跨域共享契约）。

### 增量工作包（WBS）

- **W1（D1）约束贯通**：
  - W1.1 `app/lib/cartography/layout_constraint_spec.py`（新）：`LayoutConstraintSpec v1` 可序列化约束（per-component：must_fit/min_size/max_size（px 或 page 比例）/anchor_zone/overflow_policy(keep|collapse|suppress_with_disclosure)/no_truncate:bool；page 级：safe_area、grid_columns、key_note_ids）——从 ComponentPlacement/描述符现有字段派生默认值，纯 additive。
  - W1.2 solver 扩展：`solve_layout_v5`（或 V4 参数化）消费 constraint spec：连续 min-max 仲裁、must-fit 保护（关键说明宁折叠不截断、不静默删）、溢出处置带披露码；同输入同输出（golden 锁定）。
  - W1.3 safe-area → PublicationLayout 贯通：layout_description.py / layout-description.ts 双侧把 safe_area 与版心（含出血 PUBLICATION_BLEED_MM）纳入 build_publication_layout 输入（additive 字段），对拍测试。
  - W1.4 前端消费：resolve-layout.ts 读 spec 约束（缺省 = 现行为，零破坏）；窄视口转栏建议与后端语义一致。
- **W2（D2）标注治理**：
  - W2.1 导出链标签抑制披露闭环：把 label-solver 抑制结果（too_long/suppressed/collision_dropped 计数+样本≤N）接入导出降级词表（词表权威 render_diagnostics.py），删除"待回归"注记。
  - W2.2 图元×标签冲突：导出求解器加有界 label-vs-feature AABB 检查（可选权重：让位/剔除+披露），不修改数据。
  - W2.3 字段缺失守卫：`label_field_missing`（或既有等价码核实）确定性失败码 + 不生成标注的负例测试；中英混排/长名称可靠退化测试补强。
  - W2.4 跨入口 presentation consistency 测试族：同数据经 ≥3 入口（thematic/heatmap/template）产出图例区间/单位/色标/nodata 一致（对拍 legend_spec 派生单源）。
- **W3（D3）导出一致性**：
  - W3.1 缺瓦片有界披露：导出聚合 tile 缺失计数+首 N 个样本（tile-zoom-plan 消费），进降级码/receipt；不静默。
  - W3.2 冻结快照边界测试：导出中 MapSpec 突变 → composeExportSpec 冻结语义回归（pending 叠加不混版本）。
  - W3.3 #1910 非重叠验收：前端 pdf-font 装载语义与后端回执一致性核对测试（不改 publication_export.py）。
  - W3.4 高 DPI/中文 PDF 证据：A4/A3/16:9 × dpi 档的 layout-description golden 对账 + 字形嵌入断言（前端 jsPDF 侧）。
- **W4（D4）质量回路**：
  - W4.1 版式/标签确定性发现词表：`layout.findings`（collision_unresolved/overflow_suppressed/key_note_truncated_risk/…）+ `label.findings`（collision_ratio 梯度/suppressed_overflow/field_missing），落 semantic_checks 新模块（不碰 quality_loop.py）。
  - W4.2 安全局部修复动作：change_anchor/shrink/collapse_to_overflow 已有 REPAIR_ACTIONS——接线到发现词表的 fix_hint 路由（quality_loop 现有消费面只读，通过 selfheal_actions.py 注册新动作映射）；上限/幂等/user-wins 复用现有闸。
  - W4.3 not_evaluated 纪律：视觉不可测路径显式 not_evaluated（复用 goal_satisfaction/provider 语义），测试锁定。
- **W5（D5）六类出版基线**：
  - fixtures：`tests/cartography/publication_cases/`（六类 MapSpec 输入 × zh/en × 16:9/A4/A3），生成器 + 结构 golden + 降级文案；点密度+区级统计（dot_density 族补 standards_cases/独立）、长名称行政区、双图对比（后端 golden 首例）、研究图件（inset+chart+annotation）。
  - 每用例：MapSpec→layout 求解→PublicationLayout→（可行时）渲染 harness 像素/结构检查 + PNG/PDF 对账 receipt 断言；Chromium 不可用时静态/单元证据 + NOT_RUN 披露。

## 3. 阶段计划（R0–R10；阶段可按风险反复）

R0 事实清点（本文件+三附件）→ R1 基线复跑与 before 缺陷 → R2 契约/ADR 定案（DECISIONS.md + ADR-0283 草案）→ R3 W1.1+W1.2 最小纵切 → R4 W1–W5 全量（薄切片 TDD，语义化 commit）→ R5 异常/恢复/边界 → R6 性能/预算 → R7 真实 journey（前端 vitest + 可行 E2E）→ R8 独立 review → R9 清偿复审 → R10 PR。

## 4. 验收（对齐任务书可验收终点）

1. 约束式布局：长标题/长图例/窄屏不出界且保留关键说明——六类用例 + 专测。✅判据：golden + 边界测试绿
2. 标注语义与可读性自动检查有效，缺字段/过密/混语言有明确可靠退化。✅判据：失败码 + 负例测试
3. 屏幕/PNG/PDF 关键语义与布局 golden 一致，高 DPI 与中文有证据。✅判据：双侧 golden 对账 + 字形断言
4. 修复 user-wins、无分析重算、有限次数、可信 revision；不可测=not_evaluated。✅判据：动作白名单测试
5. 不重做 #1870/#1880/#1875、不抢修 #1910。✅判据：diff 文件矩阵核查
