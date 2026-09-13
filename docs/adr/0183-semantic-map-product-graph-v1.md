# ADR-0183: Semantic Map Product Graph V1 — 语义产品层（MapProductSpec / 视图关系 / 产品编译器 / 语义完整性）

- 状态: Accepted
- 日期: 2026-09-14
- 线: cartography/semantic-map-product-graph-v1（方向 6）
- 关联: ADR-0076（SessionPlan 单一计划真相）、ADR-0085（ProductGraph 派生投影）、
  ADR-0092/0099（产品版本账本）、ADR-0120（MapSpec 文档 spine）、
  ADR-0150/0151/0161（意图/配方）、ADR-0152-0159（AC 能力波次）、ADR-0091（完成管线）、
  .audit CA-P1-3（组合抽象五源并存）

## 1. 背景与靶心

用户请求（如「成都小学分布情况，各区统计」）语义上是一张**地图产品**：主体分布视图 +
密度视图 + 行政区聚合对比 + 统计图表 + 标题/图例/比例尺/来源 + 交付要求，视图之间有
可验证的关系。当前生产把产品语义散落三处：SessionPlan 章节行（计划事实）、
composition/产品模板（静态库）、MapSpec layout.components（渲染态）。结果：

- turn 之间没有"这张产品是什么"的一等对象；
- 视图关系（同一数据集/派生统计/对比/概览-细节/图表联动/共享图例/共享范围/来源归属）
  只以 `options.layerId`、组件级 `component_links` 等局部字段隐式存在；
- completeness 是 binding-based（planner）+ 渲染/视口核验（completion/），
  没有"产品构成是否满足用户要求"的语义完整性；
- 产品级编辑（"去掉右边的统计图""改成柱状图""加主城区插图""只保留耕地""改 16:9"）
  只能落成组件级突变，没有"改产品语义 → 受影响视图重编译"的路径。

## 2. 谱系收敛（M0 裁定）

「地图产品」相关对象族与各自语义（全部保留，不合并本体）：

| 对象 | 真相种类 | 语义 |
|---|---|---|
| `SessionPlan.gis_chapter` | 计划真相（持久，Redis envelope，会话锁） | 数据需求/分析步骤/能力行状态 |
| `MapProductPlan` | 执行计划（draft→finalized） | 怎么执行：资格/降级/能力裁决证据 |
| `MapProductSpec`（**本 ADR 新增**） | **产品语义真相**（持久于 chapter 内 additive key） | 产品是什么：视图/关系/绑定/交付/覆盖 |
| `ProductGraph` / facets | 派生只读投影（ADR-0085 不变式不变） | 给 Pi 的 [GIS Plan] 披露 |
| `MapProductVersion` 账本 | 版本证据（append-only，DB） | 项目级版本/五维 diff/谱系 |
| Recipe / ProductTemplate / CompositionTemplate | 类型库（静态注册） | 引用目标，不是实例 |
| MapSpec v1.2 | 渲染真相 | 视图/组件的表达面 |

**唯一语义产品 owner = MapProductSpec**：intent 的下游、编译的上游、投影的上游、
语义完整性验证的对象。五源组合抽象（CA-P1-3）本批不合并本体；spec 以**引用**消费它们
（recipe_id / template_id / composition_template_id / 组件 id 全部来自既有注册表）。

## 3. 决策一：MapProductSpec v1（M1/M2）

- `spec_version="1.0"`；`spec_id`（query+recipe 摘要）；`revision`（每次编辑/重编译 +1）
  与 `digest`（canonical sha256，复用 provenance fingerprint 的 canonical_dumps）。
- `views[]`（≤12）：kind 词表 = {map, inset, overview, chart, stats_panel,
  narrative, comparison, time_panel}；每视图带 role、caption、binding
  （dataset_ref / analysis_ref / layer_hint / component_hint）、enabled、evidence。
- `relations[]`（≤24）：词表 = {same_dataset, derived_statistic, comparison,
  overview_detail, chart_linked_to_map, shared_legend, shared_extent,
  source_attribution}；端点必须是已登记视图 id（dangling = 校验错误）；
  `validate_relations()` 拒自环与环（comparison/shared_extent 等对称边以
  src<dst 归一化）。
- `delivery`：targets（interactive/png/pdf/…）、aspect（如 16:9）、audience；
  `overrides[]`：用户显式选择（编辑落点），编译优先级最高。
- 不收渲染细节：无 paint/legend_spec/placement/position（那是 MapSpec 与组合模板的
  职责）。fail-closed 校验：未知 kind / 端点越界 / 重复 id / 超界 → errors 非空即拒。

## 4. 决策二：产品类型库 = 原型 → 视图构成（M3）

`PRODUCT_ARCHETYPES`（6 值，product_templates.py）已经是防模板膨胀的词表；本批新增
`product_shapes.py` 给每个原型定义**视图构成缺省**（required views / relations /
delivery 画像），并给 `build_product_spec_from_plan()` 把既有 plan（intent + recipe +
template 裁决产物）确定性装配成 spec。不从零发明产品类型：shape 只引用既有词表
（archetype / intent.task / recipe.cartography）。

## 5. 决策三：产品编译器 = 纯函数，落图走既有通道（M4/M5）

`compile_product_spec(spec, *, plan, mapspec, composition_template) →
ProductCompileResult`：

- 确定性：同 (spec, plan, 模板/registry 状态) 同输出、同 `compile_digest`；
- **explicit choice 优先**：`spec.overrides` > intent 显式信号 > recipe/template 缺省；
- 组件选择消费 `component_registry` + composition 槽位（必需性裁决者仍是
  `validate_component_composition`），图表 kind 消费 `chart_kinds` 词表 —— 不建第三份
  组件目录/图表词表（CA-P1-3 不加重）；
- 产出 = 视图→组件指派、图表指派（含 chartRef 需求 refs —— 方向 5 ExecutionGraph
  未合并，只产需求不产 scheduler）、组合/标签/导出 intent refs、决策与降级披露
  （每条带 reason）、evidence 回填（M7）；
- **不直接写 MapSpec**：`webgis_map_product` 消费编译结果，仍经 mapspec_store 既有
  意图通道落图（CAS/revision/事务不变）。编译失败 → 工具回退既有组装路径并披露
  `product_compile_fallback`（可解释 fallback，不静默）。

## 6. 决策四：语义编辑（M6）

`webgis_product_edit`（tier2）+ `apply_product_edit(spec, op)`：编辑词表
（remove_view / replace_component / add_view / set_view_filter / set_delivery /
toggle_component / set_caption）在 spec 图上先验证后提交；返回 affected_view_ids；
未受影响视图的语义与 evidence 原样保留；`spec.revision`+1、digest 变更。
失败 → spec 不变。`webgis_component_update`（组件级、CAS 面向用户拖拽）保持不动，
两者互补。

## 7. 决策五：证据与语义完整性（M7/M8）

- M7：视图级 `ViewEvidence`（dataset_ref/analysis_ref/selection_reason/
  cartographic_decision/user_override/quality_ref），由编译器从 plan 既有证据
  （algorithm_selections / template_selection.sources / fallbacks / overrides）
  转录回填，有界（每视图 ≤4 条 decision）——只转录不推断，与
  `MapProductEvidence`（evidence.py）同哲学。
- M8：`product_completeness.py` 语义完整性验证器 —— 与 binding 完成度
  （planner.assess_completeness）和渲染/视口核验（completion validators）正交：
  必需视图在场（shape.required + 用户要求）、comparison 真是双视图对比、
  source note 组件在场、chart 视图与 map 视图同 dataset 绑定、shared_legend
  关系两端图例族一致、delivery 的 export 覆盖缺口如实披露（chart/statistics 导出
  LIVE-only 是已知缺口，披露不谎报）。必需性单一来源仍是
  `ProductFacetContract`（product_facets.py）。

## 8. 持久化与并发（决策 D2/D4）

- spec 以 `chapter["product_spec"]`（bounded dict）存于 SessionPlan envelope：
  复用会话锁、supersede 归档、resume 锚定；presence 语义合并（键在场即整体替换，
  缺席保持原值——与 merge_map_product_result 既有键语义一致）。spec 自带
  spec_version+revision+digest，未来可无损迁独立存储/进账本。
- `webgis_map_product` / `webgis_product_edit` 的 apply_tool_result 合并函数各司
  一个 additive 键；不新增状态机、不新增锁。

## 9. 风险与回滚

- 全部 additive：新模块、新工具、新结果键、chapter 新键；删除键/工具即回到 master 行为。
- `gis_harness/tools.py` 与在途 #1273/#1276 有文本冲突风险（合入序在后），语义正交。
- 编译器输出仅是"建议+披露"，最坏情况回退既有组装路径（工具内 fallback 保留）。

## 10. 明确不做

不重写 MapSpec runtime；不重做 adaptive symbology/label/layout/narrative/publication/
visual judge；不把 product schema 做成第二个 MapSpec；不合并五源组合抽象本体；
不写 ExecutionGraph scheduler；不复制 Goal Evaluator；不硬编码任何城市/主体专用逻辑。
