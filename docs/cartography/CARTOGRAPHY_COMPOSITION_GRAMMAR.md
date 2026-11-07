# CARTOGRAPHY_COMPOSITION_GRAMMAR — 制图组合语法（G04-CARTOGRAPHY-COMPOSER）

> 状态:**与代码同步**(2026-11-08,branch `zcode/goal-intelligent-carto-composer-20261107`)。
> 本文描述的是**已实现并经测试锁定**的组合语法,不是愿景。每节给出权威模块与锁定测试。

## 0. 语法总览

一次制图组合 = **判选(selection) → 模板/契约(template/contract) → 实例化(instantiation) → 版面(layout) → 导出(export)** 五段文法。每段都有单一事实源;跨段的引用是 fail-closed 的(注册表互账)。

```
数据/要求(intent facts + profile)
   │ ① 唯一投影器 project_selection_facts        [app/lib/cartography/selection_context.py]
   ▼
SelectionFacts(类目/几何/测量层级/统计/产物/点位计数/时相/用户pin)
   │ ②a 组合候选 select_composition_alternatives  [composition_selection.py]  → ≤3 候选
   │ ②b 表达候选 generate_design_candidates      [design_candidates.py]      → 2–4 候选(R1–R6 门控)
   ▼
CompositionPlan(spec_id/槽位/绑定)  [template_intelligence.py plan_composition]
   │ ③ 实例化(双通道,见 §3)                       [component_resolver→composer | composition_contract.apply_contract]
   ▼
MapSpec layout.components(实例 id 惯例 = component_abi.INSTANCE_ID_BY_TYPE)
   │ ④ 版面 solve_layout_v5(单求解器;print profile) [layout_solver.py + publication_constraint_layout.py]
   ▼
导出 PNG/SVG/PDF/Atlas(能力矩阵 component_renderers._SUPPORT_MATRIX)
```

## 1. 判选输入语法(①):结构化事实,不是关键词

**输入**(全部结构化,无 query 字符串):
- `intent_task`(17 键审定词表 `TASK_CATEGORY_BY_INTENT` → 组合类目;表外兜底 `thematic_cartography`)
- `geometry_types`(原始几何类型列表)→ `collapse_geometry_types`(计数最大类)→ geometry_kind(point/line/polygon/raster)
- `variable_kind`(测量层级:none/categorical/ordinal/continuous/rate):profile `dataKind` 显式优先;粗判口径 = **数值字段在场 → continuous,仅文本/类别 → categorical,无字段 → continuous**(name+count 数据不因伴生文本降级)
- `statistic`:density ⟸ measure∈{density,rate,ratio} 或 task∈{analytical_density,concentration_analysis};否则 count
- `artifact_types`:profile 显式声明(≤12)优先;否则 `GEOMETRY_KIND_ARTIFACTS` 几何派生;overlay 面证据在场追加 `admin_boundary_set`
- 增量证据:overlay 次几何、分母字段(DENOMINATOR_FIELD_HINTS 命中,宁缺毋滥)、时相双期(声明+字段双证据)、用户 pin(grammar_face_pins / bare_density_visual)

**单一投影器**:`project_selection_facts(intent_task, profile, geometry_expectation, measure, scale_hint, overlay_geometry_kinds, user_requested_models, temporal_periods)` → `SelectionFacts`。
- planner 侧(`_composition_alternatives_evidence`)与 tool 侧(`webgis_map_product`)**消费同一产物**——同一会话不再投出两套口径(此前漂移:design 侧 variable_kind 恒缺省、statistic 两套)。
- 锁定:`tests/cartography/test_selection_context_v1.py`(双侧字段一致、几何坍缩与 recipes 等价、产物 id 闭合于 ArtifactType)。

**硬编码禁令**:判选链本体无 per-query 关键词匹配;审定静态表(类目亲和/变量-分类适配/率语义模型)是**知识表**,与语料负例锁定(tests/cartography/representation_corpus)。判选不允许"学校=热力图"式捷径——热力必须过 R3 资格门(<HEATMAP_MIN_POINTS BLOCKED)。

## 2. 候选生成语法(②):资格先行,typed block 不静默

- **组合层**(`select_composition_alternatives`):geometry∩artifact 硬过滤(仅 native runtime)→ 6 因素加权(数据契合 .30/类目亲和 .25/变量契合 .15/输出 .10/组件可用性 .10/统计-比例尺 .10)→ 类目轮转保多样性 → ≤3 候选。
- **表达层**(`generate_design_candidates`,2–4 候选):借道组合池 + 亲和族补齐 + 率语义分母门控对。资格门:
  - R1 率语义无分母 → **BLOCKED**(typed,可见不静默)
  - R2 点位过密(≥250)→ CONDITIONAL(建议降级链)
  - R3 热力点位不足 → BLOCKED
  - R4 双期在场 → 补 change 类目
  - R5 聚合质心谬误守卫
  - R6 分类裁决装配(choose_classification:skew≥0.5→head_tail;k∈[3,7])
- 用户显式表达要求:pin → honored;不可行 → typed_block(GEOMETRY_INCOMPATIBLE / DATA_REQUIREMENT_UNMET),双通道诚实。
- Agent 给 2–3 候选必须带依据(reasons)与不误导边界(risks/conditions);用户选择优先(`webgis_map_design_select` 固化 user-wins pin)。

## 3. 模板与组件契约语法(③):五件套 + 实例寻址

**契约五件套**(fail-closed 互账,`component_registry.validate` 与 ABI/渲染矩阵/taxonomy 交叉对账):
1. `MapComponentDescriptor`(类型级:placement/positions/conflicts/dependencies/variants/size_range/accessibility)
2. `component_abi`(版本化 props schema + **实例 id 惯例单一事实**:`INSTANCE_ID_BY_TYPE`,如 legend→`legend-main`、chart_panel→`chart-panel`,多实例 `-2` 后缀)
3. `ComponentTemplate`(原子变体:variant + default_options/default_style)
4. `MapCompositionTemplate`(槽位:cardinality required/recommended/optional/conditional/**forbidden**+max_count=0、allowed_component_types、preferred_templates、position_zone/fallback_zones、bind_role/bind_scope primary|all_thematic)
5. `CompositionContractV1`(versioned 契约 + lock-aware replay:`apply_contract` 只填空槽、锁实例零触碰 LOCK_REASON_USER_WINS、links 幂等、写 `layout.composition` 身份块)

**实例化双通道**(语义分工,非重复):
- composer 主链(resolver→composer):首次组装,含 `bind_scope=all_thematic` 的 per-layer 图例族展开(`legend-main`/`legend-{role}`/`colorbar-{role}`)
- apply_contract 契约链:后续重放/应用,已在场实例零触碰;per-layer 展开目前只建 primary 单实例并**披露**该差异(composition_contract.py,已知边界)

**Agent 精确寻址**:`webgis_component_update(component_id=...)` 以实例 id(如 `chart-panel-2`)定位单个组件,"只改命中的单个组件,其余组件与所有图层不动";`webgis_component_catalog` 列在场实例;组件图边(`layout.component_links`)以实例 id 为端点。**绕过组件锁与用户偏好的修改即为失败**(锁体系见 §5)。

**模板资产**(前 G04 wave 已落):`asset.*` 命名空间、单调 revision upsert、钉版本 apply、发布门(published 才可 apply;draft 拒绝)。

## 4. 统计图与地图语义绑定(CM-03 语法)

- 图表面板(chart_panel)数据通道:`options.chart` inline(经 adaptChartData 校验)或 `options.chartRef`(`ref:chart-*` 会话按需拉取);**不新增第二图表事实源**。
- 联动:类别选择经 selection-store 发布(shared selection);linked extent 视口过滤(bbox 相交 + 有界值集,`chart-panel.tsx geometryIntersectsBbox/visibleCategoryValues`);图表 kind 变体限 data-shape 家族内改写(防 xy 数据硬转 bar)。
- 门禁:可点选 kind = donut/grouped_bar/stacked_bar/histogram/rose 等;line/scatter/area 诚实不联。
- 锁定:`frontend/components/map/map-components/chart-linkage.wave5.test.tsx`、`chart-panel-agg.test.tsx`。

## 5. 版面语法(④):单求解器 + 用户锁优先

- **`solve_layout_v5` 是唯一求解器**(V5=V4+LayoutConstraintSpec v1)。profile 词表:viewport / a4_portrait / a4_landscape / **a3_portrait / a3_landscape**(G04 补,与 PublicationIR 纸张几何对齐)/ presentation_16x9 / academic_figure。屏幕档走 v4 viewport,不冒充约束求解。
- 生产消费方(全仓仅两处):`publication_constraint_layout`(矢量 PDF 链 + 制图专家)与 **export 工具链(任务创建时求解,报告随任务下发)**。
- **组件族锁**:锁 `legend` 保护 `legend__1`(`__` 双向族语义,`app/contracts/workbench_locks.is_entity_locked`);版面求解对锁实例零触碰(zone 不改写、不抑制),`user_lock_kept` 如实披露族命中。
- **求解结果不落第二事实源**:求解报告不写进持久 MapSpec(此前 #2000 的污染路径已清偿);组件 position/anchor 是一等事实;渲染期应用只碰导出副本。
- 用户手动挪动的组件(非历史锚点)导出遵循其锚点(user-wins)。

## 6. 导出语法(⑤):能力矩阵 + typed 披露

- 支持矩阵真值:`component_renderers._SUPPORT_MATRIX`(live/canvas-exporters/publication 三通道)+ `renderer_capability.v1` fingerprint(Python/TS parity);**契约撒谎 = validate 失败**。
- 不支持能力 → `unsupported`/`EXPORT_PARITY_EXEMPT_TYPES` 明示,不虚构出版级成功。
- 关键 parity 语义:指北针 `rotate(-bearing)` 三链同语义(live/canvas/SVG);缺省导出字节稳定;constraint_layout 状态词 evaluated/not_evaluated + reason 诚实披露(含 `no_session_mapspec`)。
- 回执链:画布链 `constraint_layout_evaluated` 诊断码(随上传 diagnostics 持久化 sidecar)→ product manifest(`.manifest.json`)→ export lineage/receipts。

## 7. 增量编辑语法(CM-08)

用户增量指令("只改色带/换指北针/移图例/切 A4 横向/导出矢量 PDF")必须编译为**精确组件/样式变更事务**:
- 组件面:`webgis_component_update`(单实例局部突变)
- 组合面:`webgis_apply_composition`(conformance 预检→锁预检→apply_contract→layout_set 单一写入通道)
- 修改无关组件/数据 = 失败(测试断言 digest/revision 边界)
- 无数据要求 → unknown/partial 披露,不生成装饰性假地图(empty-state 测试锁定)

## 8. 语法边界(不做什么)

- 不造第二 MapSpec/求解器/渲染器/工具注册中心/图表事实源
- 判选不做 NL 关键词匹配;亲和表是审定知识表
- 求解输出永不持久为标记;锁语义只有 `locked/unlocked` + 族命中(无第三态)
