# Semantic Map Product Graph — 里程碑台账（ledger）

- 分支：`cartography/semantic-map-product-graph-v1`（worktree `webgis-mspg-v1`）
- 基线：`origin/master` = `580b33e9`
- 状态标记：☐ 计划 / ◐ 进行中 / ☑ 完成（含证据指针）

## Phase 0 — 勘察与基线 ☑

- 目标：最新 master 勘察、防重复对账、生产调用链 before、重叠矩阵。
- 产出：`map-product-graph-recon.md`、`map-product-graph-decisions.md`（本文件随里程碑追加）。
- 证据：origin/master SHA `580b33e9`；open PR #1270/#1273-#1279 冲突面已核（见 recon §2）；
  ADR 在途最大 0182 → 本方向取 0183。

## M0 — 概念谱系收敛 ☑

- 目标：确认唯一语义产品 owner（D1）；谱系文档化。
- 改动：`docs/adr/0183-semantic-map-product-graph-v1.md`（新增）；谱系表入 ADR §2。
- 契约：无代码契约变更。
- 测试：无（文档里程碑）。
- 兼容：零代码改动，零回滚面。

## M1 — MapProductSpec v1 ☑

- 目标：versioned、可序列化、有界的语义产品 schema（视图/绑定/关系/交付/证据引用/覆盖）。
- 改动：`app/services/gis_harness/product_spec.py`（新增）。
- 契约：`MapProductSpec`（spec_version="1.0"）、`ProductView`、`ProductViewBinding`、
  `ProductRelation`、`ProductDeliveryIntent`、`ProductOverride`、`ViewEvidence`；
  `spec_digest()`（canonical sha256，复用 provenance.fingerprint.canonical_dumps）；
  `validate_product_spec()`（fail-closed：未知 view kind/关系端点/重复 id → errors）。
- 测试：`tests/gis_harness/test_product_spec.py`（schema 往返/有界/校验/digest 稳定）。
- 兼容：纯新增；旧会话无 spec。

## M2 — Product Graph（视图关系显式化）☑

- 目标：视图关系一等化 + dangling/cycle 验证。
- 改动：`product_spec.py` 内 `view_graph_edges()` / `validate_relations()`
  （悬空端点、自环/环、same_dataset 一致性）；关系词表 8 类（same_dataset /
  derived_statistic / comparison / overview_detail / chart_linked_to_map /
  shared_legend / shared_extent / source_attribution）。
- 测试：dangling / cycle / 合法图 用例（并入 test_product_spec.py）。
- 兼容：纯新增。

## M3 — Product type library（原型 → 视图构成）☑

- 目标：基于既有 PRODUCT_ARCHETYPES/recipes/templates 的产品类型库（不是新发明）。
- 改动：`app/services/gis_harness/product_shapes.py`（新增）：6 原型 × 视图构成
  （shape 决定 required views/relations/delivery 缺省）；`shape_for_intent()`
  确定性推导；`build_product_spec_from_plan()`（plan/模板/意图 → MapProductSpec v1）。
- 测试：`tests/gis_harness/test_product_shapes.py`（每原型 shape、意图推导确定性、
  与 recipe/模板词表一致性）。
- 兼容：只引用既有词表（archetype/task/cartography），不复制模板本体。

## M4 — Product compiler ☑

- 目标：`MapProductSpec → ProductCompileResult`（确定性；explicit choice 优先；
  fallback 可解释；零重复制图逻辑）。
- 改动：`app/services/gis_harness/product_compiler.py`（新增）：编译为
  视图→组件槽位满足（消费 composition_templates/component_registry）、图表指派
  （消费 chart_kinds）、图层角色对齐（消费 plan.map_layers，不重建角色映射）、
  组合/标签/导出 intent refs、决策与降级披露、`compile_digest()`。
- 测试：`tests/gis_harness/test_product_compiler.py`（确定性同 digest、explicit 优先、
  缺数据降级披露、MapSpec 兼容出口键）。
- 兼容：`webgis_map_product` 消费编译结果（M4b 接线），组装路径 fallback 保留。

## M5 — Component modularity（registry 单一真相消费）☑

- 目标：组件选择/替换按产品语义经 component_registry + composition 槽位，不硬编码第三份。
- 改动：compiler 内 `resolve_components_for_view()`（registry `recommend/compatible`
  + 槽位语义裁决者仍是 `validate_component_composition`）；无新组件目录。
- 测试：并入 test_product_compiler.py（registry 不可用降级、槽位必需性传递）。
- 兼容：零新词表；组件 id 全部来自 registry。

## M6 — Product edits ☑

- 目标：语义编辑 → spec 图局部修改 → 受影响视图重编译。
- 改动：`product_spec.py` 内 `apply_product_edit()`（编辑词表 + 先验证后提交 +
  affected_view_ids）；`webgis_product_edit`（tier2，tools.py 注册）；chapter 合并
  `merge_product_edit_result`（session_plan.py）。
- 测试：`tests/gis_harness/test_product_edits.py`（编辑词表全用例、无效编辑 fail-closed、
  未受影响视图语义保持）。
- 兼容：新工具 additive；编辑失败 spec 不变。

## M7 — Product evidence ☑

- 目标：每视图/组件可追溯（数据集/分析产物/选择原因/制图决策/用户覆盖/质量结果）。
- 改动：spec 的 `ViewEvidence`（编译器回填：来自 plan.algorithm_selections、
  template_selection.sources、fallbacks、overrides；质量结果留 ref 挂钩不推断）；
  webgis_map_product 结果携带 `product_spec` + `product_evidence` 有界转录。
- 测试：compiler 用例断言 evidence 回填与有界性。
- 兼容：evidence 是 spec 内字段（有界），无新真相源。

## M8 — Completeness validator ☑

- 目标：语义完整性（要求 vs 构成），与渲染/视口核验（completion/）、binding 完成度
  （planner）正交。
- 改动：`app/services/gis_harness/product_completeness.py`（新增）：
  `validate_product_completeness(spec, requirements)` → 报告（required views 在场、
  comparison 真的是对比、source note 在场、chart-map 同数据绑定、shared legend 一致、
  export 覆盖披露）；消费 ProductFacetContract（单一必需性源）。
- 测试：`tests/gis_harness/test_product_completeness.py`。
- 兼容：纯新增；报告进 webgis_map_product 结果（additive 键）与 chapter。

## M9 — Golden product corpus（≥50）☑

- 目标：50+ 产品语义 fixture（zh/en、缺数据、fallback、不同输出）回归 schema/compiler/
  binding/digest/MapSpec 兼容。
- 改动：`tests/fixtures/product_corpus/product_cases.json`（合成，54 案例）+
  `tests/gis_harness/test_product_corpus.py`。
- 测试：语料全量回归（确定性 digest 锁定）。
- 兼容：fixture 合成，不依赖真实大数。

## 生产接线 ☑

- `webgis_map_product`：意图→spec 构建（首调 build_product_spec_from_plan）→ 编译 →
  组装消费编译结果 → evidence/完整性回填 → 结果携带 `product_spec`/`product_completeness`。
- `webgis_product_edit`：语义编辑 → 受影响重编译 → 落图 → 结果合并 chapter。
- `session_plan.merge_map_product_result` + `merge_product_edit_result`：presence 语义
  合并 `product_spec`。
- `product_graph.py`：`build_product_graph`/`build_facet_completion` 接受
  `product_spec` 输入（视图 facet 从 spec 投影；缺省回退旧行为）。
- 证据：tests/gis_harness/test_product_wiring.py（意图→组装→编辑→投影全链，纯 stub store）。

## 最终 review（Subagent B）☐

## 交付 — PR ☐

- PR body 模板要求项见任务书；创建后不 merge、不 auto-merge。

## 里程碑证据汇总（2026-09-14）

- 提交链：acf16e15（Phase0+M0）→ 79900fbb（M1+M2）→ 8aa85e8c（M3）→
  06e216d1（M4+M5+M6编辑语义）→ 5d49ca3c（M6/M7/M8 接线）→ 57fdb9c7（M9）。
- 测试面：product_spec 13 / product_shapes 8 / product_compiler 9 /
  product_completeness 9 / product_wiring 9 / product_corpus 54 = 102 新测试全绿；
  存量回归：tests/unit/gis_harness 全目录 1406 passed / 4 skipped；
  -m cartography 门禁 933 passed / 4 skipped；
  session-plan/tool-meta 套件 34 passed。
- master 预存失败对照（干净 master=580b33e9 主工作树复跑同样失败，失败模式逐字一致）：
  - test_benchmark_harness.py::test_golden_cases_no_semantic_regression
    （semantic regressions: ['G4']）；
  - test_component_lifecycle.py::TestUserRemoveWinsOverRepair::test_user_removed_title_not_resurrected[trio]。
  二者均与本分支改动无交集（benchmark golden 语料 / 组件生命周期 trio 参数）。
- 编辑存活语义（本方向核心正确性）：webgis_map_product 重放合并
  （merge_spec_with_replay）不覆盖用户 overrides/移除视图/关闭组件族；
  apply_tool_result 的 webgis_product_edit 分支只动 chapter["product_spec"]。
