# ADR-0088: Cartographic Component Library 2.0

状态: 已实施（PR: feat/cartographic-component-library-v2）
日期: 2026-08-30
关联: ADR-0081（Export Parity）、ADR-0084（Cartographic Layout Engine）、
ADR-0085（Goal→Product Graph）、ADR-0086（Render Observation Runtime）

## Context

组件化制图 v1 已建立 MapSpec.layout.components 单一真相、descriptor
registry、composition 模板、resolver/composer、live/export 共享解析层
（resolveMapComponents）与渲染真值矩阵（component_renderers）。但存在
明确缺口：

1. `inset_map` 停留在 `runtime_status=planned`（schema/registry 在场、
   渲染器/导出器缺位）；
2. 图例族（legend / categorical_legend / continuous_colorbar）cardinality
   均为 single，且 colorbar↔legend 是 **type 级互斥** —— 多图层地图
   （heatmap 主层 + choropleth 参考层）无法同时拥有色条与分级图例；
3. annotation 仅支持静态文本卡（live 有、矩阵谎报导出缺失）；
4. chart_panel zero_or_one —— 多图表产品不可表达；
5. ProductGraph 只感知 statistics/chart/annotation 三类组件 facet。

本轮（Component Library 2.0）目标是在**不破坏单一真相不变量**的前提下
补齐以上能力。

## 不变量（全部保持）

- `MapSpec.layout.components` 仍是组件 desired-state 唯一真相；本轮未
  新建任何 spec/store（无 DashboardSpec / LegendSpecStore / InsetSpecStore）；
- ProductGraph / RenderObservation 仍是派生只读投影；
- live 与 export 共享同一解析层（resolveMapComponents）与布局求解器
  （resolveComponentLayout）；
- 真值矩阵（component_renderers.py）仍是 renderer/exporter 支持的单一
  权威，descriptor 撒谎在 registry.validate() 即爆。

## 架构决策

### D1 — 图例族 cardinality=multiple，冲突升级为 binding 级

type 级互斥（descriptor.conflicts）废除；`composition_validation.
validate_binding_conflicts` 按绑定语义判定：

- 同一 layerId 上离散图例（legend/categorical_legend）与连续色条并存
  → `binding_conflict`（同一层两种竞争语义）；
- 同一 layerId 上同型重复 → `binding_conflict`（无信息增益）；
- 不同层各自图例/色条 → 合法（Scenario A 的核心契约）；
- 未绑定 layerId 的实例不参与（HUD 发现语义）。

### D2 — 组合层 per-layer 图例展开

`ComponentSlot` 新增 `bind_scope: "primary" | "all_thematic"`。图例族
槽位声明 `bind_scope="all_thematic"` 时，`ComponentComposer` 按图层角色
逐一展开实例（`legend-{role}` / `colorbar-{role}`，primary 保留旧固定 id
`legend-main` / `colorbar-main` 向后兼容），并按**该层的 MapModel** 选型
（heatmap 层→colorbar、choropleth 层→legend；无兼容图例类型的层如实
跳过——不给纯点/边界参考层挂图例）。planner 传递全部启用图层的
role→layer_id 与 layer_id→model 映射。

### D3 — inset_map：轻量静态投影，不 mount 第二个 maplibre runtime

- live `inset-map.tsx`：纯 SVG —— 插图 bbox（必备，缺省自弃不虚构范围）
  + 可选边界折线（≤512 点，由 Agent/后端从既有 artifact 简化传入）
  + 主图范围指示框（显式 `mainBbox` > 真实 map bounds 自动确定）；
- 导出 `drawChromeInset`：同一 fit/投影语义（等比适配 + 居中 + y 翻转）；
- 真值翻转：`runtime_status=native`、矩阵 renderers/exporters 补真、
  catalog 豁免移除并重生成；resolver 以 `required_context=["inset_context"]`
  门控防空选（planner 对具名 scope 的报告产品供应该上下文；bbox 由
  Agent 经 `webgis_component_update` 填充）。

### D4 — 注记框架：一个类型三种形态

`annotation` 组件 variant 扩展为 `text | callout | group`：

- callout：`options.anchor=[lng,lat]` 地理锚定 + 引线 + 确定性象限避让
  （右半向左偏、上象限向下偏），live 与导出共用 `geo-anchor.ts` 投影
  （导出侧经 `boundsFromCenterZoom` 与导出经纬网同源推导 bounds）；
- group：`options.items`（≤12 条，单条文本 ≤200 字符）一条组件渲染一组，
  组内条目可各自带 anchor 成为组内 callout；
- bounds 缺席 → 降级为静态卡（不虚构位置）；payload 由
  `validate_annotation_payload` 把关（工具侧在**合并既有 options 之后**
  校验，分步突变不被卡）。

### D5 — chart_panel multiple 与多图表产品

cardinality zero_or_one→multiple；composition 图表槽 max_count=3；导出
panels 循环与 live 渲染器天然支持多实例（既有能力，本轮解除上游限制）。
chart facet 的 chartRef 在 ref descriptor 中查无证据 → 该 facet
`needs_repair`（仅该面板欠修，不把整图判死，Scenario F）。

### D6 — ProductGraph facet 扩展（仍是派生投影）

`legend` / `inset` 两个新 facet kind（`_STAT_KINDS` 机制复用）：

- legend facet key=组件 id、label=`id@layerId`（多图例可分辨）、
  metadata 带 layer_id；
- **信息性 facet**（`required=False`）：图例/插图是 conditional 槽位，
  在场即构成，缺席不欠账 —— 不虚构 required，不进 owed 统计。

### D7 — 布局与小视口（Scenario H）

- 求解器 meta 补 `inset_map`（top-right 族，priority 3 槽内堆叠）；
- `clampFloatingRect`：导出侧 floating 盒确定性夹取进画布（≥96px 可见
  窗口 + 8px 边距）—— 视口约束是渲染期 derived 语义，**不改 MapSpec 的
  user placement 真相**；live 不夹取（用户显式摆放优先，user > agent >
  auto 不变）。

### D8 — 产品原型（P7/P8）

`MapProductTemplate.archetype` 固定词表（distribution_overview /
regional_comparison / density_analysis / remote_sensing / simple_view /
proportional_symbol）+ regression guard 双锁：

- 既有结构重复 guard（同 recipe+composition+角色签名不得重复）—— 本轮
  实际拦截了新 `regional_comparison` 模板（与 administrative_statistics_map
  结构重复，对比语义由后者 + statistical_map 组合承载）；
- 新增 `test_seed_templates_declare_archetype`：非 deprecated 模板必须
  归属词表，垂直差异（school/hospital/earthquake）用 subject 参数表达，
  不再新增模板。

`remote_sensing_product` 接通 raster_distribution 配方（此前
composition.remote_sensing_map 不可达）。

## Parity 契约（live / PNG / PDF / SVG）

SVG 与 PDF 均复用 PNG 画布链（SVG=位图包装、PDF=画布嵌入），因此
语义 parity 的锚点是 `buildExportChrome` ↔ live `resolveMapComponents`：

| 维度 | live | export |
| --- | --- | --- |
| 图例族多实例 | 每实例一渲染器 | `legends[]/colorbars[]` 逐实例绘制，绑定各自 legend_spec |
| annotation text/callout/group | geo-anchor 投影 | drawChromeAnnotation 同一投影（boundsFromCenterZoom 与导出经纬网同源） |
| inset | inset-map.tsx SVG | drawChromeInset 同一 fit/投影 + 槽内堆叠 |
| placement | user floating 优先 | scaleFloatingRect 缩放 + clamp（页内不裁剪） |
| collapsed | 面板折叠条 | 折叠条（不展开用户折叠的面板） |

真值矩阵与 descriptor/catalog 三方对账由 `registry.validate()` 与
`test_component_catalog_parity` 锁定（本轮修正了 annotation 的历史谎报，
并随 inset 渲染器落地同步翻转）。

## 性能契约

- 校验/投影全部 O(C) 或 O(C log C)（C=组件数 ≤32）；碰撞披露 O(C²)
  仅在小常数矩阵上（既有行为）；
- inset 边界折线 ≤512 点、注记组 ≤12 条、组件观察 ≤32 条 —— 无
  O(features × components) 路径；
- inset 无第二 maplibre 实例（无额外 GPU 上下文/内存/生命周期负担）。

## Deferred（本轮刻意不做）

- **Panel/Dashboard 分组框架**（P5）：多实例 + 同槽堆叠 + floating 已
  覆盖实际需求；dock/tab 分组在出现真实用户场景前不引入第二布局框架。
- **inset 的 live maplibre basemap 模式**：静态投影 + 边界折线满足区位
  语义；若未来需要真实瓦片底图插图，需先解决嵌套地图的 destroy/resize/
  session-switch 生命周期与导出快照协议。
- **map-chart selection 联动**（P4 §7.3）：未建立 dashboard state engine；
  可在 chart_panel options 预留 `filterLayerId` 类协议后再评估。
- **inset bbox 自动推导**：服务端当前只做上下文门控，bbox 由 Agent
  填充；自动从 scope/行政区边界推导需要边界 artifact 协议（boundary
  字段已预留）。

## Follow-up（v2.1 hardening，同 PR 系列）

- **Scenario H fallback 规则补全**：共享求解器 `resolveComponentLayout`
  新增槽高预算容量裁决（画布高 × 0.7 预算，按 (priority, 声明序) 累积
  estHeight）——超预算最低优先级尾部单步侧让到 ANCHOR_FALLBACK 槽
  （fallbackFrom 记因），fallback 槽仍超限披露（`slot-capacity` 碰撞），
  绝不三层挪动；user 浮动组件永不参与。live 与 export 同一实现。
  至此 Scenario H 三规则（collapse / stack / fallback）全部确定性落地。
- **§13 十维 parity 矩阵测试**（`export-chrome.parity.test.ts`）：一份
  综合 MapSpec 逐维断言 live（resolveMapComponents）与 export
  （buildExportChrome）一致 —— presence / placement / variant / binding /
  collapsed / legend content / colorbar range / annotation text /
  inset extent / chart data。
- **collapsed chart parity 修复**：collapsed 的 chart_panel 此前导出仍
  展开（E-2 只覆盖 statistics）—— 现导出折叠标题条（text 携带标题，
  drawChromeChartPanel 同约定）。
- **§17 工具多实例契约**：`webgis_component_catalog` 暴露
  `cardinality` 与 `multi_instance_types`（图例族 + chart/annotation），
  summary 指引「新增实例须显式 component_id」；`webgis_component_update`
  description 补 annotation/inset payload 与多实例寻址规范。
