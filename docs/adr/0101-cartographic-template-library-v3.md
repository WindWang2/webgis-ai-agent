# ADR 0101 — Cartographic Template & Component Library V3

日期：2026-09-06
状态：Proposed（随 feat/cartographic-template-library-v3 分支交付）
前置：ADR 0088（Cartographic Component Library v2）、ADR 0092（professional
GIS runtime）、ADR 0099（Map Product lifecycle v2）

## 背景

ADR-0088 之后系统已具备：Component Registry / Taxonomy / Templates、8 个
Composition Templates、14 个 Map Models、机器真值 renderer 支持矩阵、
GIS Harness resolver/composer、interactive 与 canvas-export 双 renderer。
但库的规模与专业覆盖仍是种子级：模型不能覆盖 point/line/polygon/raster/
decision 的主流表达谱系；组合模板没有领域包；variant 只有零散词表；
theme/palette 没有统一描述层；后端 layout collision 求解是 stub；
disclosure 族组件没有导出消费方；没有系统性 golden corpus。

## 目标

把「种子库」发展为「专业制图设计系统」：Map Model Library 大扩容、
Composition Template domain packs、Component Variant Library 一等公民化、
Theme/Palette 描述层、Layout Solver V2、renderer parity 补齐、multi-layer
binding 强化、结构化 golden corpus。核心是**建库与统一契约**，不是推翻
现有抽象。

## 非目标

- 后端 ComponentType union 不新增成员；前端 MapSpecComponent.type union
  通过**补齐 3 个后端既有成员**（methodology_note/uncertainty_panel/
  decision_panel —— 渲染器早已注册，仅类型面漂移）实现对齐，不引入
  新 union 语义。表达能力经 variant 与 template 扩展。
- 不改 vendor/pi 核心、不改 `app/services/gis_harness/planner.py` /
  `recipes.py` 的主流程（resolver 仅加 template runtime_status 门控）。
- 不实现 SVG 矢量孪生的 marginalia（报告 vector SVG 无 chrome 是已知
  限制，诚实记录，不用豁免掩盖）。
- 不做像素级 screenshot golden（结构化 golden 为准）。

## 决策

### D1 — Pack 化扩容，seed 保持不变

- `app/lib/cartography/model_packs/`：按域组织 MapModel 种子
  （point_line / polygon_statistical / raster_remote_sensing /
  decision_analysis），`MapModelRegistry.load_builtins()` 先载入原
  SEED_MAP_MODELS，再按确定性顺序载入 packs。
- `app/lib/cartography/composition_packs/`：按域组织
  MapCompositionTemplate，`CompositionTemplateRegistry.load_builtins()`
  同法。
- 既有 14 models / 8 templates / 35 templates 的 id、字段、fallback
  链全部保持不变（向后兼容由测试锁定）。

### D2 — 模型诚实性分级

native 的充分条件：表达机制完全落在现有运行时事实上——
`maplibre_layer_type` ∈ 前端编译器 union 支持的图层族
（fill/line/circle/symbol/heatmap/raster/fill-extrusion）+ legend_spec /
paint 投影（thematic_spec）+ `classify_values` 分级。需要新运行时能力
（hillshade/color-relief 图层、cluster source、双变量色阵、点密度
生成、SAR/趋势 artifact、中心性计算等）的模型一律
`runtime_status="planned"`，登记 pitfalls 与前置条件，不伪装可用。
planned 模型不得被 planner 选为最终产品（既有 gate 不变）。

### D3 — Variant 是 registry 事实，不是前端条件

- `MapComponentDescriptor.variants` 只列**渲染器真实支持**的变体；
- 每个变体有对应 `ComponentTemplate`（id 命名 `type/variant`）；
- 前瞻变体以 `ComponentTemplate(runtime_status="planned")` 入目录，
  resolver 增加 template 级 runtime_status 门控（唯一 harness 兼容性
  修改），杜绝 planned variant 进入最终产品；
- catalog 导出（schemaVersion 3，additive）携带 variants/themes/palettes。

### D4 — Theme 层描述而不复制颜色事实

- 十六进制真值不搬家：thematic 色带唯一真值仍是 `palettes.py`；UI chrome
  颜色唯一真值仍是前端 design tokens（globals.css / lib/theme.ts）。
- `themes.py` 提供 `PaletteDescriptor`（引用 palette id，colorblind
  承接 PALETTE_KINDS，print-safe 由灰度 ΔE 推导计算）与
  `CartographicThemeDescriptor`（profile、typography 结构、对前端语义
  token 名的引用、按 color_scheme_kind 的 palette 推荐）。
- WCAG 对比度基础检查：前端 vitest 从 globals.css 解析 token 实测
  AA（正文对 ≥4.5:1）；后端校验 palette 灰度可分级性（复用
  palettes.py CIEDE2000/灰度工具）。

### D5 — Layout Solver V2：确定性单遍

`layout_solver.py`：slot priority → 主 zone 容量 → slot fallback zones →
邻接 fallback → overflow 处置（optional 确定性抑制 + required 保留并
warning）。无循环、无随机、同输入同输出；page profile（viewport /
a4_portrait / a4_landscape / presentation_16x9 / academic_figure）只影响
容量与堆叠预算表。既有 `detect_collisions` QA 路径不变；
solver 以独立纯函数交付（`validate_component_composition` 保持零改动），
golden corpus 直接消费 `solve_component_layout`。

### D6 — Renderer parity 补齐而非豁免

- disclosure 族（uncertainty_panel / methodology_note / decision_panel）
  落地 canvas 导出绘制（export-chrome.ts），矩阵 / descriptor / catalog
  三方同 move（validate_against_descriptors 锁定）；
- table_panel 维持 interactive-only（Runtime V4 产品决策，非豁免掩盖，
  矩阵 note 说明）；
- 报告 vector SVG（python 孪生）无 chrome 为已知限制，记录于 parity
  文档，不在矩阵中虚报。

### D7 — Binding 语义强化

- per-layer 图例族展开已有（ADR-0088 D2）；V3 增加**确定性实例 id 契约**
  （同输入千次 compose id 集合不变）、multi-colorbar（heatmap 主层 +
  raster 次层）、flow + admin context、comparison 双主层用例的测试锁定；
- composer 对重复实例 id 确定性去重（保序保先）。

### D8 — Golden corpus：结构化而非像素

`tests/cartography/golden_corpus/`：builder 生成
native model × compatible composition × output target × locale × page
profile 的用例矩阵（数百级），每用例固化
resolve → compose → validate → layout 的结构化 digest（committed JSON）。
属性测试：双跑一致、无 planned 泄漏、required 槽满足或显式 fallback、
binding 零冲突。特殊用例：中英长标题、极端图例标签、缺失可选 context、
fallback template 触发。

## 后果

- 库规模：models 14→40+，composition templates 8→25+，component
  templates 35→55+，variants 词表显著扩容且三方对账；
- 前端类型面零破坏（无新 ComponentType）；catalog JSON schemaVersion
  2→3 仅 additive；
- 已知限制：SAR/趋势/双变量/hillshade 等模型为 planned；报告 SVG 无
  chrome；table_panel 不导出（产品决策）。
