# ADR-0090: GIS Product Runtime V3 与 Workspace 交互模型

- 状态：Accepted（2026-08-31）
- 关联：ADR-0085（Goal→ProductGraph）、ADR-0087（Facet Completion /
  Product Action Advisor）、ADR-0088（Autonomous GIS Product Runtime —— 本
  ADR 是其 Workspace/交互侧的延续）、ADR-0081（完成度终验）、ADR-0084
  （布局引擎）、ADR-0088（组件库 v2，另一编号空间）、ADR-0076（SessionPlan
  单一计划真相）、ADR-0082（Artifact Runtime）、ADR-0089（Raster v3）
- 分支：`feat/gis-product-runtime-v3`

## 1. Context

审计（`.audit/product-runtime-v3-audit.md`，5 路独立视角 + 主 agent 代码
实证）表明：产品运行时的**执行侧**（ProductGraph / facet completion /
GISActionIntent / runtime repair / lineage 最小重计算 / finalizer QA）在
master 已完整落地并有测试锁定。真实缺口集中在两处：

1. **产品语义的应然构成没有单一推导面**：facet 必需性是逐 kind 启发式
   （chart 信号甚至处于断线状态 —— planner 从未把 recipe.export_profile
   写进 template_selection，`chart:required` 合成只在测试手工注入时生效）；
2. **工作区（Workspace）交互层**：图层无状态词表、组件无管理入口、无
   dock 基座、map↔chart 无共享选择（ADR-0088 组件库明确 deferred 的项）。

## 2. Decision

### 2.1 ProductFacetContract（`product_facets.py`）—— 应然构成的单一推导面

```text
intent.task + recipe.export_profile + composition required 槽位
    → ProductFacetContract { required/optional 组件类型, chart_required,
                             legend_required, sources[] }
```

- **为什么不是第二 Plan**：契约是纯函数派生（同章节必同契约，复算即得），
  零持久化；SessionPlan 仍是唯一计划真相（ADR-0076）。它只回答「产品图
  视角下某类 facet 缺席是否欠账」，不编排执行。
- 消费面：`build_facet_completion` 的 `required` 字段、`chart:required` /
  `legend:required` 合成节点（后者带「已有主题层落 MapSpec」护栏 —— 无层
  不欠图例）、`map_completion.validate_semantics` 的契约驱动检查。
- 断线修复：planner finalize 现在把 recipe.export_profile 随组合证据写进
  template_selection（旧读面兼容保留）。

### 2.2 Recipe 不是第二 workflow engine

`task_optional_analysis`（task → 专属能力）是 recipe 的**声明性扩展**：
同一 recipe 服务多个 intent task（raster_distribution 兼任
change_detection）时只有命中 task 的计划并入专属能力，与
optional_analysis 的无条件并入语义不同。registry_validation 校验值域。
change_detection 任务由此真正计划 `raster_change_detection` 能力
（capability/tool 此前已存在但从未被该任务触达）。

### 2.3 ProductGraph 依赖语义

facet 级供给边（`ProductFacetCompletion.dependencies` /
`FacetLineageEntry.depends_on_facets`，均有界）：

```text
map_layer  ← analysis:<source_capability>
chart/statistics ← analysis:<表/聚合类产出能力>
```

- 语义：**「chart 欠账 ≠ 重查数据」的产品图表达** —— 上游 analysis facet
  完成且 artifact 存活时只补产物（`product_lineage.reusable_inputs` 已带
  ref 给 Pi）。
- **不是调度器**：执行优先级仍由 advisor/action_intent 的确定性序承载；
  边是披露与测试面。类型词表单源
  （`product_graph.CHART_INPUT_ARTIFACT_TYPES`，lineage 复用）。

### 2.4 语义级 QA（`map_completion.validate_semantics`）

组合路径被绕过（组件手工增删）时槽位校验看不见的四个检查：
`semantic_legend_mismatch`（legend_spec 判别值 ↔ 图例族类型；映射与
composer/渲染器同源，divergent 复用连续渲染器）、
`semantic_legend_missing`（契约必需 + 图例族在场但未覆盖某主题层；族整体
缺席仍归槽位级，不重复披露）、`title_missing_report_product`、
`crs_not_wgs84`（ArtifactRecord.crs 在场且非 WGS84 且 ref 供着 spec 层；
未知 ≠ 错，不虚构判定）。全部 warning 级 —— 换型/补标题是 agent 级决策，
finalizer 修复面不越界。

### 2.5 MapSpec / Selection 状态边界

```text
MapSpec   = desired product state（placement/enabled/collapsed/样式）
Selection = transient UI state（lib/selection/selection-store）
```

- 单一 SelectionContext（source/layer_id/artifact_ref/selected_ids≤50/
  selected_categories≤20/filter_field/bbox/≤8 标量属性），事件词表
  select|hover|brush|filter|clear_selection|extent_change；
- **chart→map**：类别选择经 `options.selectionField` 协议编译为 per-layer
  MapLibre 过滤，与图例过滤**同一** compose/reconcile 通道 —— 只重编译
  filter，source/layer 引用不变、零重拉（adapter 测试锁定数据面引用相等）；
  selectionField 缺席 → 仅状态高亮（诚实降级）；
- **map→chart**：feature 点击发布 select，chart 按 selectionField 从有界
  属性快照推导高亮类别；
- 任何选择变化不产生 user mutation / component patch / MapSpec 写入
  （测试锁定）；会话切换随 resetLiveState 连带清空。

### 2.6 Workspace dock 状态边界

```text
dock placement = 工作区 UI 状态（dockSlice，不持久化）
semantic component state = MapSpec placement/enabled/collapsed（唯一真相）
```

「图表数据」与「图表停靠位置」不是同一个对象：停靠/取消停靠只写
dockSlice；FloatingChrome 停靠态改静态流式渲染（同一渲染器双宿主），
折叠/隐藏仍走语义 patch 通道；MapSpecChrome 跳过停靠实例（不双渲染）。
导出不受影响（导出画面消费 MapSpec，不消费 dock 状态）。

### 2.7 Layer 状态词表（派生）

`loading|ready|rendering|hidden|stale|failed|expired` 从三个既有事实纯派
生：store 行（visible/_refId/_tileUrl）、committed revision（session-cursor）、
最新 RenderObservation 证据（`render-evidence` 有界 stash，≤64 id）。
hidden（期望关闭）≠ stale（期望在场、runtime 分歧 → runtime repair 域）
≠ expired（ref 确认驱逐 → 执行债域）。状态**永不回写** store/MapSpec。

### 2.8 视口折叠（Scenario H 补全）

`suggestViewportCollapses`（resolve-layout，纯函数）：小画布（<520px）
建议面板族折叠到标题条。user-pinned 浮动面板豁免；`placement.collapsed`
（用户所有）永不写入；导出画布天然不触发 —— 折叠是**视口派生语义**，
不是 desired state。

## 3. Performance 契约

- 全部新投影 O(layers+components) 或 O(1)，与 feature 数无关；
- 选择有界（50/20/8 标量，无几何）；证据 stash 有界（64 id，布尔+revision）；
- ref 缓存 LRU ≤24 条目（每条 ≤20k features —— 此前无界增长是长会话
  内存风险）；per-geometry bbox WeakMap 记忆化（viewport re-filter 100k
  features 从每次相机落定的全量坐标树降为 O(1) 查表）；
- user_action 事件体在系统上下文中截断 1200 字符（客户端可写面的有界
  披露）。

## 4. User-wins 重申

用户显隐（`_userPinned`/`presentation_owner=user`/durable presentation）、
用户拖放（floating placement）、用户折叠（placement.collapsed）在所有新
面（dock、折叠建议、选择过滤、状态词表）中优先或不被触碰。视口折叠建议
明确豁免 user-pinned；停靠取消不触碰语义状态。

## 5. Alternatives（拒绝）

- **facet 契约持久化进章节**：第二计划真相（ADR-0076），必然漂移；
- **SelectionContext 进 MapSpec**：把 transient 交互写进 desired product
  state —— 目标 §D5 明令禁止；
- **dock 状态进 MapSpec placement**：混淆「产品构成」与「工作区布局」，
  导出会被工作区状态污染；
- **选择过滤走 map-action 命令通道**：每类选择一次命令往返 + 可能的
  层重建；live-spec 复用通道零成本且已被图例过滤验证；
- **为 change_detection 新增产品模板**：违反 ADR-0088 D8 反垂直膨胀
  guard（与 remote_sensing 结构重复）—— 任务条件能力是正确粒度。

## 6. Compatibility

- SessionPlan / MapSpec / 组件 schema 零变化；`task_optional_analysis` 与
  `ProductFacetContract` 均为 additive（缺省行为不变）；
- `[Products]` 行格式不变（legend 欠账走既有 owed 尾巴机制）；
- 设置面板「图层」tab 折叠进工作区 Layers 标签（单一入口；旧持久化
  settingsTab 正常化回落）。

## 7. Deferred

- 表格视图接入 SelectionContext（table source 词表已预留）；
- brush / extent_change 事件的完整交互实现（词表与store 已就位）；
- 组件 remove 的专用 mutation intent（当前 hide 语义；多实例真移除需
  remove_component 意图 + CAS 设计）；
- chart 创建工具自动填充 `selectionField`（协议已就位，agent 可经
  component_update 填充；缺席时 chart→map 仅状态高亮）；
- 选择过滤的层级 recompile 优化：filter-only 变化目前走 reconciler 的
  recompile 路径（remove+add，与图例过滤同款既有行为 —— source 引用不
  变、零重拉，但可能有一帧闪烁）；`setFilter` 直通道是 reconciler 的
  后续演进；
- 错误 `selectionField` 可把图层过滤为空而状态词表仍显示 ready（过滤
  对可见性证据不可见）—— 需要「过滤命中数为零」的披露通道；
- SelectionContext 无 session 标记（会话切换经动态 import 微任务清空，
  同任务批内的迟到发布理论上可穿透 —— 概率极低）；
- 渲染证据 stash 上限 128 层（超出部分状态退化为无证据缺省）；
- user_action 的 json.dumps 在截断前仍付 O(payload) CPU（输出已有界）；
- slim_event_result 对无 bbox 内联 FC 的 O(features) 兜底（F4/F5，LOW）；
- dock 区记忆（跨会话工作区布局持久化）。
