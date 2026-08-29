# ADR-0080: Unified GIS Runtime v3 — 单一运行时真相、Planner Runtime 与依赖感知规划

- 状态：Accepted（2026-08-29）
- 关联：ADR-0076（SessionPlan 是 Pi-path 计划真相）、ADR-0079（Runtime v2 原子状态/编译清单）、ADR-0069（项目记忆排序）
- 分支：`feat/gis-unified-runtime-v3`
- 审计基线：PR #1085（组件化模板 phase-2）+ #1086（Runtime v2）合并后的 post-merge 集成审计

## Context（背景）

Runtime v2 与组件化模板 phase-2 合并后（master `7ab44b1`），系统组件齐全，但
合并树里存在四类真实集成缺陷（本轮审计 A1–A4 全部复现并修复）：

1. **A1 — manifest stale 披露 fail-open**：`tools._manifest_stale` 调用不存在的
   `manifest.stale(recorded)`（真实 API 是 `is_stale_plan`），AttributeError 被
   broad except 洗成 `False`——`webgis_map_product` 结果里的 `manifest_stale`
   证据**恒假**，旧 registry 世代的计划从不被披露为 stale。
2. **A2 — planner 生命周期形同虚设**：`webgis_map_intent` /
   `webgis_map_product` / `plan_orchestrator` 各自 `MapProductPlanner()` 临时
   建实例，`_plan_memo` 只活在一次调用里；intent→product 链（同 query 2–3 次
   规划）跨调用零复用。
3. **A3 — 潜在 eviction TypeError**：`_plan_memo` 是 plain `dict` 却用
   `popitem(last=False)` 驱逐——缓存一旦超过 64 条即 TypeError；该 bug 被 A2
   掩盖（每调用新实例，缓存从不满）。
4. **A4 — 命令式覆盖层双事实源**：`lib/map-kit/custom-overlay-registry.ts`
   （source/layer 定义账本，生产重放路径，**无界**）与
   `lib/map-commands/custom-overlay-registry.ts`（重挂闭包 LRU 64，其
   `remountCustomOverlays` **无任何生产调用方**——闭包被 remember/forget
   维护却永不重放）。审计同时发现：`addNativeHeatmap` 不经
   `recordCustomOverlayLayer` 缝——native heatmap 的 layer 定义从未入账，
   basemap 切换后 source 重挂而**图层永久消失**。

规划面上，`data_requirements` / `analysis_steps` 是同一 capability 列表的两个
平行扁平投影：无边、无 `depends_on`、无 artifact 关联；执行顺序只存在于
recipe 声明顺序里，且没有任何东西强制它。producer→consumer 语义只存在于
registry descriptor（`input/output_artifact_types`），plan 从不携带。

## Problem

- stale/version mismatch 是 correctness signal，不允许被 API 漂移静默洗掉；
- 同一进程内对同一 query 的规划必须可复用（Planner 是纯函数，无理由重复算）；
- 命令式运行时图层（custom overlay / native heatmap / raster overlay）只能有
  一个挂载账本，且 setStyle 后必须完整、幂等地恢复；
- Harness 必须能向 Pi 披露"什么必须先执行、什么依赖什么、什么可跳过、什么
  不可用、fallback 是什么"——但**不能**让 LLM 维护 DAG 状态，也不能建立第二
  套计划真相。

## Decision

### 1. Runtime Layer Truth — `frontend/lib/map-kit/runtime-layer-registry.ts`

**唯一的命令式运行时图层挂载账本**（canonical store）。两个旧模块降级为
facade（导出面不变，全部委托同一存储；adapter ≠ second storage）：

- 描述符：`runtimeLayerId` / `sourceId`（身份）、`sourceDef` / `layerDef` /
  `remount`（重放：定义优先，闭包兜底）、`family`（vector/raster/heatmap/
  annotation/custom，从 layer type 派生）、`ownership=command` /
  `mountMode=imperative` / `persistence=session`（契约边界）、`zGroup` /
  `seq`（插入世代，重放序 = 首挂序）。
- **spec 层不进账本**（契约边界，非能力缺口）：spec 承载层的挂载真相在
  MapSpec + `MapSpecRuntime.reconcile`（declarative）；把 spec 层塞进本账本
  会制造第二份 spec 真相，违反 §19。
- 生命周期：`unregisterRuntimeLayer`（层族前缀 `id-` / `id__` 清扫，source
  记在层账目内一并移除）、`clearRuntimeLayerRegistry`（会话切换，
  session-cursor 触发）、`remountRuntimeLayers`（style reload 后：先
  sources 后 layers、幂等、`onLayerAdded` 补记 z 序账本）。
- **有界 256 条 FIFO**（v2 定义账本无界 / 闭包账本 64 的统一界）+ O(1)
  source 吸附索引（`sourceIndex`：登记 N 层不再是 O(N²) 扫描）。
- `addNativeHeatmap` 补记 layer def（A4 伴生缺陷修复）：native heatmap 在
  basemap 切换后 source + layer 完整恢复。

### 2. Planner Runtime — `app/services/gis_harness/planner_runtime.py`

进程级共享规划服务（`get_planner_runtime()` / `reset_planner_runtime()`）：

- planner 是 immutable service：无 session mutable state；会话态继续在
  SessionPlan / GISWorldState / MapSpec（ADR-0076 边界不变）。
- `_plan_memo`：`OrderedDict` FIFO（修复 A3 的 plain dict TypeError），读写
  持 `RLock`，命中与存入均深拷贝；有界 64。
- **memo 键 = 确定性裁决结果**：recipe/template 选择（廉价排序）前置到 memo
  查询之前，键用解析后的 id——intent 阶段（无显式参数）与 product 阶段
  （显式回放同一 recipe/template 的 plan 连续性参数）命中同一条目；
  `project_verified` 只在改变裁决时才分键（裁决相同 ⇒ 输出相同，命中合法）。
- registry 身份守卫：`reset_recipe_registry` 等替换 registry 单例时自动重建
  planner（旧引用与旧 memo 键不存活）。
- memo 键含 manifest 指纹：registry 内容变化自动失效。指纹对
  **algorithms 的 tool_candidates 顺序**（解析优先级）敏感；recipes 投影
  只含 capability 集与 task（集合语义、无序）——recipe 内容重排的安全性
  由裁决前置保证：排序变化若改变选择 → 解析后的 recipe/template id 变
  → 分键；不变 → 输出不变，命中合法。

### 3. DataRequirement Graph / Analysis DAG — `app/services/gis_harness/plan_graph.py`

**扁平行仍是唯一持久事实源**：`DataRequirement` / `AnalysisStep` 新增
additive 字段 `depends_on`（capability id 列表）与 `optional`（recipe
optional_analysis 成员），由 planner 编制时经 `infer_dependency_edges`
（registry artifact 类型推断：`A.output ∩ B.input ⇒ A→B`）填充并随
SessionPlan chapter 持久化。`PlanGraph` 是这些行的**纯派生投影**——
legacy projection = graph projection，单一计算源：

- 旧持久计划（无 `depends_on` 字段）读取侧重放推断，行为等价；
- 节点状态 = `merge(requirement.status, step.status)`（available/done →
  complete；unavailable → unavailable；否则 pending），`kind` 从 capability
  category 派生（data_access → requirement，否则 analysis），`cost_class`
  从已裁决算法的 complexity 取档，`input_refs`/`output_ref` 从依赖与自身的
  `bound_ref` 派生，`fallback_to` 从 resolver 的
  `capability_fallback_available:<cap>` 裁决证据提取；
- **ready**：pending 且依赖全满足（complete/skipped/fallback-unlocked/
  optional-failed-absorbed）；
- **failed（Phase E 收尾，端到端接线）**：数据/分析工具 dispatch error 时，
  Pi bridge 以 ``success=False`` 调 ``apply_tool_result``，命中的能力行
  （``capabilities_hit_by_tool``）标 failed——progress 行、两类扁平行、
  SSE progress 事件与 ``open=`` 投影一致；DAG 中 mandatory failed 阻塞
  下游（``blocked_by`` 披露、等待重试），optional failed **不阻塞**
  mandatory 图（节点保留 failed 披露，下游按"该增强缺席"继续）；重试成功
  行翻 complete，下游经 blocked-recovery 解锁。规划入口工具
  （``webgis_*``）失败不映射（无确定受害能力行）。合并优先序
  complete > failed > unavailable > pending；
- **unavailable 传播**：mandatory 依赖缺失 → 本节点 unavailable 并记录
  `blocked_by`；optional 依赖缺失 → 本节点（若 optional）skipped 级联，
  **不阻塞 mandatory 图**；
- **fallback unlock**：preferred 节点 unavailable 但其 fallback capability
  完成 → 该依赖视为满足，下游解锁（真实声明如 grid_binning →
  density_surface）；
- **环**：registry 推断按计划声明序增量插边、跳过成环边（确定性，记录在
  `dropped_cycle_edges`）；`strict=True` 对手工构造的环/悬空依赖抛
  `PlanGraphError`（对抗校验入口）。

### 4. SessionPlan Integration（有界投影）

`format_session_plan_projection` 首行契约不变；其后追加**有界** `[GIS Plan]`
块（≤10 行：nodes / Ready / Waiting（含 `<- deps`）/ Completed /
Unavailable（含 fallback）/ Skipped / Recommended next）。graph 从持久
chapter 派生评估，**不写回**任何持久状态——节点状态仍由 SessionPlan
`_mark_progress` 的行写入推进（tool result binding），投影每次读取重算。
`webgis_map_intent` 的 `guidance` 追加一行有界依赖序摘要（无依赖边时零噪声）。

### 5. Pi Boundary（不变，显式重申）

Pi 仍是 Agent Host：每轮只见有界投影（一行 SessionPlan + [GIS Plan] 块 +
cartography verdict），决定下一次工具调用；GIS 执行语义（依赖序 / ready /
fallback 裁决 / artifact 绑定）全部在 Harness 侧确定性推进。不 fork
vendor/pi、不注册 100+ GIS 工具为 native Pi tool、无第二 agent loop、无第二
SessionPlan。

## Bugs Fixed（本轮真实修复）

| 缺陷 | 现象 | 修复 |
|---|---|---|
| A1 | `manifest_stale` 证据恒 False（API 漂移被 broad except 洗白） | 调用真实 `is_stale_plan`；移除 broad except（correctness signal 显式暴露）；回归测试锁定漂移必须 raise |
| A2 | planner memo 跨调用零复用 | PlannerRuntime 共享 + memo 键用裁决结果（intent→product 链真命中；ToolRegistry dispatch 层测试锁定） |
| A3 | `dict.popitem(last=False)` TypeError（被 A2 掩盖的潜伏 crash） | OrderedDict FIFO + RLock |
| A4 | 双覆盖层账本（closure 账本永不重放）；native heatmap layer 定义缺账 | 单一 canonical registry + 双 facade；`addNativeHeatmap` 补记 layer def |
| Phase H | 导出成品的 subtitle 不读 spec（title/subtitle 行为分叉） | 同一事实源链：请求参数 > spec 组件 > 内置空串 |

## Review Findings（独立评审后修复）

五个独立 reviewer（架构 / 正确性 / 前端运行时 / 性能 / 测试对抗）对分支
的阻断性与建议性发现，已修复项：

- **fallback 解析对真实 resolver 输出失效**（正确性 review P1）：
  resolver 的 reason 形如 `capability_fallback_available:<cap>; <理由>`，
  解析截到首个 `;`；测试 fixture 改用真实后缀格式。
- **optional 依赖不可用阻塞 mandatory 下游**（正确性/架构 review P1）：
  `_evaluate` 重排——optional-unavailable 先翻 skipped（每轮最先），
  只有 mandatory 依赖不可用才阻塞；complete 是行事实永不覆盖；blocked
  节点在 blocker 转满足后恢复。
- **同一 source 的第二个层会吸附删除第一个层的账目**（三 review 共同
  P1/P2）：source 吸附仅限**尚无 layerDef** 的记账；多层层共存 + 重放 +
  反注册不误伤有回归测试。
- **session-cursor 同 id 重设清空统一账本**（架构/正确性 review P2）：
  账本清理只在会话 id 变化分支（`clearCustomOverlayRegistry`）；
  `resetLiveState` 不再触碰（v2 清的是独立闭包账本，统一后清会违反
  "同 id 重设不清"契约）。
- **memo 命中带回错误出处的 template_selection 证据**（正确性 review
  P2）：选择器 dump 是证据基线，显式 template_id 只在改写裁决时覆盖——
  两路径对同一裁决产出 byte-identical 证据。
- **死测试断言 / 缺 dispatch 层 A2 锁定 / FIFO 未钉死 / 条件断言空洞**
  （测试 review P1/P2）：artifact-binding 测试补全全部依赖后无条件断言；
  新增 ToolRegistry dispatch 层共享 memo 测试；FIFO 驱逐钉死边界 key；
  guidance 投影的不变量先行断言。
- **显式空 `depends_on` 被当作缺失重推断 + 推断不惰性**（架构/性能
  review P2）：`is not None` 判别字段存在性，推断惰性计算。
- **性能测试盲区**（性能 review P2）：级联 unavailable 与宽扇入传播
  fixture 补齐；命名与实际断言对齐（同内容重编译 ≠ 失效）。

## Compatibility

- `webgis_map_intent` / `webgis_map_product` / `webgis_component_update` /
  `webgis_component_catalog`：result 形状不变（plan dump 新增 additive 字段、
  guidance 多一行）；contract_version 不动（无破坏性变更）；
- SessionPlan SSE 事件名与首行投影格式不变；
- 前端：两个旧 registry 模块导出面不变（facade）；`remountCustomOverlays`
  语义超集（返回计数）；
- 唯一行为变更：map-commands 闭包账本的界从 64 并入统一 256（v2 定义账本
  无界 → 收敛为有界）；对应测试已更新。

## Performance

确定性 call-count 契约（`tests/benchmarks/test_planner_runtime_perf.py`）+
规模契约（`frontend/lib/map-kit/runtime-layer-registry.test.ts`）：

- 同输入第二次规划 **0 次** resolver 解析；intent→product 链恰解析一次
  （= 单次 `use_memo=False`）；
- manifest 同内容重编译指纹稳定（不误失效）；registry 内容变化全量失效；
- 满容量（80+ 唯一 intent）下 100 次规划中位数毫秒级；
- registry：100/300 层登记 + 全量重放；重复 style reload 幂等；会话切换
  零泄漏；O(1) 吸附（无 O(N²) 扫描）；
- `build_plan_graph`：真实 plan 毫秒级；60 节点链式图 <200ms 且 ready 推进
  正确（逐级解锁）。

## Failure Semantics

- stale/version mismatch：**fail-loud**（`_manifest_stale` 无兜底；投影路径
  保留容错但 API 已对齐——异常只会在 manifest 编译真故障时出现）；
- graph 投影：增值信号，异常不阻断 turn 上下文（首行仍在）；
- memo：异常退直算（正确性优先，缓存只是加速）；
- registry 重放：单条目失败不阻断其余（下轮重放再试）。

## Rejected Alternatives

- **spec 层入统一 registry**——制造第二份 spec 真相；spec 层恢复已有
  reconcile 全量重放，账本只服务 imperative 层。
- **PlanGraph 作为独立持久模型**——第二计划事实源；改为扁平行 additive 字段
  + 纯投影，旧数据零迁移。
- **闭包账本删除**——导出面有生产调用方（remember/forget/reset）；
  降级为 canonical 的兜底 provider（定义缺失时才触发重放）。
- **planner 全局可变单例 + 锁内重建**——registry 身份守卫（指针比较）更
  廉价且天然正确。
- **LLM 维护 DAG 状态**——违反 Pi/Harness 边界；状态由 tool result binding
  确定性推进。

## Follow-ups（记录，不在本轮扩大）

1. **导出器 placement 保真**：live FloatingChrome 尊重 persisted placement
   （anchor/floating 坐标），canvas exporter 用固定布局槽——placement 进
   导出需要真正的布局引擎（§13 明确排除的 export engine 重写量级）。
2. statistics_panel / chart_panel 仅 live 渲染；attribution 并入导出
   watermark/metadata 行——组件族导出覆盖需同上布局引擎。
3. full inset_map / graticule / map_border 组件渲染器（§13 排除项）。
4. source GC：统一 registry 已能判断 source 独占性（entry 内聚合），但删除
   语义需按 scenario/ADR 审定后另行落地。
5. ~~`running`/`failed` 预留~~ —— `failed` 已端到端接线（见上）；
   `running` **有意不持久化**：Pi turn 严格串行（bridge `self._lock`），
   投影只在 turn 开始时绑定（`bind_turn_prompt`）——turn 内设置的 running
   标记在下一次可观测读之前必然已被 complete/failed 覆写，唯一存活场景
   是 crash 中断的 turn，而那需要 stale-running 恢复逻辑才诚实。每工具
   调用一次额外 store 写换取零可观测收益，违反"无消费字段不引入"原则；
   in-flight 可见性属于 dispatch 层（bridge 的 SSE 流与 toolCallId 结果
   缓存），不属于计划真相。
