# BASELINE — 多尺度制图综合（GOAL 04）

工作树：`../webgis-wt-goal-multiscale-carto`，分支 `zcode/goal-multiscale-cartography-v1`，
基线 = origin/master `6c5e71c2c`（2026-10-31，PR #1875 merge）。
调查方法：主 Agent 精读核心文件 + 2 个只读 Explore subagent 全链追踪（报告存于本目录
`/baseline-reports/` 无 —— 直接并入本文；结论均可由 file:line 复核）。

## 0. 环境事实

- origin/master = `6c5e71c2c`；唯一 open PR = #1876（GOAL 01 spatial-semantics，无文件交集）。
- #1868/#1869/#1870/#1871/#1873/#1874/#1875 全部 MERGED（本任务边界即以它们为底座）。
- 相关 open issue（audit 家族，非本任务引入、不主动认领）：**#1809**（headless 编译器不输出
  layer.filter）、**#1810**（mapspec_to_svg 忽略 layer.filter）、**#1811**（runtime $type 语法
  混用）。经代码核实**均未修复**（compiler.ts:621-630 主层无 filter 键；mapspec_to_svg.py
  全文零处 filter 求值；git log 无相关提交）。⇒ 本任务的多尺度表达**绝不依赖运行时改写
  layer.filter**——要素取舍走预计算派生工件（新 source），绕开该缺陷族。

## 1. 逐子功能基线（已有实现 | 入口 | 调用链 | 测试 | 缺口 | 允许改动范围）

### 1.1 语义缩放表达协议 v1.6（本任务的直接底座）
- 已有：`app/lib/cartography/representation.py` —— `representation_at_scale()`（:310，
  几何族×数量×视口 → windows 计划；user_pin user-wins；artifact_modes capability seam）、
  `normalize_windows` :192、`project_mapspec_representation` :622（平凡 raw 不落笔）、
  `export_adjudicate_layer` :576 / `export_zoom_of_mapspec` :608。
- 词表：`REPRESENTATION_MODES` = raw/point_cluster/visual_heatmap/aggregate_grid/**generalized**/
  hidden；`ARTIFACT_MODES=(aggregate_grid, generalized)`（:81「声明即调用方确认工件已可用」）。
- 调用链：生产唯一落笔 = `app/services/analysis_cartography_converter.py:883-930`
  （**未传 artifact_modes** ⇒ generalized/aggregate_grid 在生产 spec 中从未出现）；
  消费 = `semantic_checks_representation.py:16`、`quality_loop.py:578`、
  `mapspec_to_svg.py:122,1382,1572`、前端 `carto-ir/representation.ts` + compiler/runtime。
- 测试：tests/cartography/test_representation_{protocol,projection,binding,corpus}_v1.py、
  test_export_representation_parity_v1.py；前端 representation.test.ts、
  representation-parity.golden.test.ts、runtime-representation.test.ts。
- **缺口（本任务 M3/M6 落点）**：generalized/aggregate_grid 无工件生产链、无校验链；
  窗口克隆用主层 source（compiler.ts:1150-1160 注释「artifact 源由后端在声明可用时
  另行装配」——装配机制不存在）。
- 允许改动：representation.py 原则上**只读复用**；MapSpec 窗口结构 additive 扩展
  （`window.source` 源覆盖）+ schema v1.8。

### 1.2 尺度带与比例尺换算
- 已有：`app/lib/cartography/scale_rules.py` —— `SCALE_TIERS` 四带
  world[0,8)/province[8,11)/city[11,14)/street[14,24)（与 label_plan.DEFAULT_ZOOM_BANDS
  契约锁定 :94-101）；`boundary_detail` 四档（generalized/standard/detailed/full）
  **全仓无渲染端消费者**（悬空建议）；`meters_per_pixel` :179（**256px 瓦片基准**）、
  `scale_denominator` :191（1:N，DPI-aware）。前端 `map-kit/meters-per-pixel.ts` 是
  **512px MapLibre worldSize 基准**——同 zoom 数值差 2 倍（双源分裂，历史事实）。
- 测试：test_scale_rules_v1.py、test_scale_matrix.py。
- **缺口（M1 落点）**：无「目标比例尺 1:N ↔ zoom」的 profile 输入面；显示尺度与
  计算尺度未显式分口径；boundary_detail 无消费者。

### 1.3 MVT 通道（禁改语义）
- 已有：`app/services/mvt.py` `_simplify_and_project` :860-902（z<14 时 lon/lat 空间
  shapely `simplify(2**-(z+1), preserve_topology=True)` 半像素容差；简化致无效则回退
  原几何）；缓存 `SpatialIndexCache`/`TileLRUCache`；ETag `sha256(gzip)[:16]` +
  If-None-Match 304（app/api/routes/layer.py:641-699）。
- 测试：tests/unit/test_mvt_{encoder,prepared_geometry,cache_memory_pressure,epoch_race_p11}.py。
- **边界**：本任务不改 mvt.py 的 simplify/缓存/ETag；派生综合是**另一通道**
  （预计算 geojson 工件 ref），与瓦片内抽稀正交。

### 1.4 符号律（禁重写）
- 已有：`frontend/lib/map-kit/symbol-law.ts`（401 行纯函数：densitySignal :155、
  circleRadius/lineWidth/heatmapRadius 表达式、resolveDensityPresentation :316
  阈值 5000/20000、evidence ring）。v1.6 后仅在层**无** representation 声明时介入。
- 测试：symbol-law.test.ts。允许改动：**只读 import**（multiscale 前端模块复用
  densitySignal/阈值常量），不改其行为。

### 1.5 标签/图例/导出 parity（#1875，禁平行设计）
- 已有：Python `label_engine.py`/`label_typography.py` + TS 孪生 `label-geometry.ts`/
  `label-solver.ts`（逐常量一致）；golden corpus parity
  （tests/cartography/golden_corpus/{label_collision,legend_model}/ 双侧消费）；
  导出：前端 `mapspec-compiler/mapspec-to-svg.ts`（真矢量 SVG）、后端孪生
  `app/services/mapspec_to_svg.py`（compile_mapspec_to_svg_detailed :1348，导出 zoom
  裁决 :1382/:1572-1590）；矢量 PDF `publication_export.assemble_pages_pdf` :207
  （WeasyPrint）；栅格 PDF/PNG = MapLibre canvas 快照（exporter.ts，WYSIWYG）。
- LabelFeature 有 `min_zoom` 门控与数值 priority，**无语义重要性映射**（M5 接缝）。
- 测试：见 §1.1 + exporter/pdf/vector-svg 系列。允许改动：mapspec_to_svg.py
  **薄适配**（窗口 source 覆盖 + scale 披露注记），不动其排版/标签实现。

### 1.6 数据 schema / 语义绑定（M2 的输入真相）
- 已有：ref 写入 `session_data_manager.store` → `compute_descriptor`
  （app/schemas/ref_descriptor.py:323，field_schema = 字段→{type,null_count,min,max,
  sampleValues 有界}）；读 O(1) `get_ref_descriptor`（session_data.py:874）；
  值分布 = `webgis_layer_aggregate`（#1875 layer_browse_tools.py:222）；角色词表 =
  `app/services/data_preparation/vocabulary.py`（category 中文词表含「等级」，:129；
  ADMIN_NAME_TERMS :152）。MapSpec source 持久画像 = mapspec_store.py:236-299。
- **缺口（M2 落点）**：无单点 API 返回「图层类别字段 + 有序值域」；
  descriptor.field_schema 无基数/值域（只有 sampleValues 采样）⇒ 规则引擎必须
  **组合 descriptor 类型 + aggregate 值分布 + 词表**，且缺证据时宁不选择。

### 1.7 MapSpec mutation / 事务 / provenance（M7 落笔通道）
- 已有：门面 `apply_gis_mutation`/`_batch`（app/services/gis_world_state/mutation.py:376/:551，
  user-wins 守卫 :102）；CAS 引擎 `MapSpecLifecycleEngine.apply_mutation`
  （lifecycle_engine.py:266，per-session 锁 + expected_revision + 回滚）；provenance
  `append_provenance`（gis_world_state/provenance.py:71，map_state._gis_provenance
  ≤64 FIFO）；范本事务 = map_operations（service.py:634 commit：幂等重放→敏感确认→
  batch 单事务→receipt 环）。intents 见 app/services/mapspec/intents.py
  （UpsertSourceIntent/UpsertLayerIntent 等；无 LOD/scale 类 intent）。
- 允许改动：**新增 intent 不必要**——UpsertSource/UpsertLayer/PatchLayerStyle 组合
  足以落 scale_profile（新 source + layer patch）；必要时加薄 intent。

### 1.8 工具注册 / dispatch / 观测（M7 通道）
- 已有：`ToolRegistry`（app/tools/registry.py:475）+ `@tool` 装饰器（:496，
  kwargs 白名单 :530-542）；装配 `app/tools/__init__.py:11-107` `_TOOL_MODULES`；
  dispatch = ToolDispatchService（app/services/tool_dispatch_service.py:308）→
  registry.dispatch（:1142，tier-3 确认闸）；范本工具 =
  `webgis_layer_features_page/aggregate/feature_detail`（layer_browse_tools.py:87-383）
  与 `webgis_map_operation`（map_operations_tools.py:52-145）。
- 观测回流：POST /sessions/{sid}/cartographic-observation（chat.py:1769）→
  render_apply_ack 校验 + mapspec_revision 盖章 → map_critique findings
  （check_label_collision :242 / check_export_label_diagnostics :293）。
- **缺口**：无 generalization/scale 类工具；observation 无 per-zoom 层可见性证据。

### 1.9 #1870 design candidates（M2 相容边界）
- 已有：`generate_design_candidates`（design_candidates.py:534，确定性）、
  `build_design_decision_trace`（design_decision_trace.py:58）、user pin 固化
  `webgis_map_design_select` → Requirement IR TurnPatch（tools.py:1745-1900）。
- **边界**：本任务**不改**候选生成/决策主题；scale_profile 的 importance 证据
  可作为 DesignTaskContext 的**只读输入**（未来接缝），不重新决策制图表达模型。

### 1.10 QA
- 已有：`review_cartography`（quality_loop.py:650）含 representation_at_scale_report
  :578（view.zoom 单点）；诊断目录 render_diagnostics（layer_gated_by_zoom/
  layer_representation_hidden 等）；semantic_checks_representation.py `_check_scale_representation`。
- **缺口（M8 落点）**：无逐带扫描 QA（跨 [0,24) 连续性/统计口径检查）；
  无 1:N 比例尺驱动检查。

## 2. 端到端场景追踪（3 个，NL → IR → Harness → Tools → ref/MapSpec → 渲染/导出 → evidence）

1. **「成都市小学分布」**（点族）：NL → `webgis_map_intent`（tools.py:297 →
   MapRequestIntent intent.py:132）→ planner（planner.py:814）→ 数据工具产 ref →
   `webgis_map_product`（tools.py:746）→ converter（analysis_cartography_converter.py:883
   representation_at_scale：点族按带 dense→point_dense_candidates）→ MapSpec
   layer.representation windows + source ref → 前端 injectResolvedRefSources 水合 →
   compiler 窗口编译（cluster 三子层/heatmap 子层）→ cartographic-observation 回流。
   **本任务增量**：profile 化（显示/原始计数披露 + user pin「放大后显示所有学校点」=
   apply 工具的 street 带 raw 窗口）。
2. **「省域道路图 1:500000→1:25000」**（线族+语义选择）：现状线族超载只能
   raw+REPR.OVERLOADED 码（representation.py:385-419）或 hidden 门；
   **本任务增量**：importance RuleEngine 读 ref field_schema 的 class 字段 →
   generalize 派生工件（主干保留+几何综合）→ generalized 窗 + window.source 覆盖 →
   导出同裁决。
3. **「同一行政分级专题图 PDF 与交互一致」**（面族 choropleth）：分类 breaks 由
   classify.py 从 source profile 计算 → legend_spec → 前后端图例孪生；
   **本任务增量**：profile 声明 classification_basis=original_source，多尺度出图
   共享 breaks；导出 metadata 带 scale contract。

## 3. 允许改动面汇总（对任务书所有权矩阵的落地）

| 文件 | 改动性质 |
|---|---|
| app/lib/cartography/multiscale/（新包） | 全部新增（profile/importance/generalize/disclosure） |
| frontend/lib/map-kit/multiscale/（新目录） | 全部新增 |
| app/lib/cartography/mapspec_schema.py | additive v1.8（window.source + layer.scale_profile 开放声明） |
| app/lib/cartography/representation.py | 只读复用（不改既有函数签名/行为） |
| frontend/lib/mapspec-compiler/compiler.ts | 窗口 source 覆盖最小消费（artifact 窗克隆时换 source） |
| frontend/lib/mapspec-runtime/runtime.ts | 同上最小消费 |
| app/services/mapspec_to_svg.py | 薄适配：artifact 窗按 window.source 解析数据 + scale 披露注记 |
| app/services/mvt.py / symbol-law.ts / label-geometry.ts | **不改**（只读 import） |
| app/tools/scale_profile_tools.py（新）+ __init__.py 注册 | 新增 |
| app/services/gis_harness/scale_profile/（新） | 新增（inspect/preview/apply 服务） |
| tests/cartography/multiscale/（新）+ tests/unit/… | 新增专项测试 |
