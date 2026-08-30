# ADR-0088: Autonomous GIS Product Runtime（统一动作 / 确定性 Runtime 修复 / 血缘最小重计算）

- 状态：Accepted（2026-08-30）
- 关联：ADR-0087（Facet Completion / Product Action Advisor —— 本 ADR 落地其
  Future work「advisor 与 plan_graph 并轨」）、ADR-0086（Render Observation
  Runtime —— 本 ADR 落地其 Future work「observation 驱动的确定性 runtime
  修复」）、ADR-0085（Goal → Product Graph）、ADR-0082（Artifact Runtime）、
  ADR-0083（Cost-aware Resolution）、ADR-0076（SessionPlan 单一计划真相）
- 分支：`feat/gis-autonomous-product-runtime`

## 1. Context

ADR-0086/0087 之后，系统能够「发现地图没完成」：finalizer 用渲染观察把
完成度校验推到 runtime 级，advisor 能点名欠账 facet。但发现之后仍是断头路：

- `render_layer_missing` 等 runtime 发散**无自动修复动作**（ADR-0086 §3.3
  显式不做，留给下一轮）；MapSpec 正确而 MapLibre 没挂上时，系统只能反复
  报 `needs_repair` 等人（或运气）自愈；
- advisor（产品侧 `ProductActionAdvisor`）与 plan graph（执行侧
  `PlanGraph.recommended_next`）各说各话 —— ADR-0087 Future work 点名的并轨；
- 「chart 欠着但 statistics artifact 还活着」这类场景，advisor 只能说
  `produce_chart`，不带可复用输入 —— 最小重计算无从谈起；
- artifact 过期（`artifact_expired`）与渲染缺席（`render_layer_missing`）
  在动作层无法区分 —— 前者要重跑分析，后者只需重挂载。

## 2. Problem

把「发现欠账」升级为「确定性还账」：Goal → Facets → PlanGraph → Capability
→ Algorithm → Tool → Artifact → MapSpec → Renderer → RenderObservation →
Facet Validation → Product Debt → **Deterministic Repair / Next Execution**
→ Re-observation → Completion。同时不得建立第二 agent loop、第二计划真相、
第二地图真相，不得对抗用户决策，不得无限循环。

## 3. Decision

### 3.1 GISActionIntent —— 执行侧/产品侧建议并轨（`action_intent.py`）

纯函数 / 只读 / 零 LLM / 零 IO 的派生投影，**不持久化**（SessionPlan 仍是
唯一计划真相）。单一入口 `resolve_next_gis_action(chapter, facets, *,
graph, lineage)` 按确定性优先级合并三个欠账来源：

```text
执行债（plan graph failed → retry_capability；ready → run_capability，
        带 node.input_refs 作为 artifact_inputs）
  → 产品债（复用 ProductActionAdvisor 优先级 —— 单一计算源，不重推）
  → 观察债（map_product complete + render:stale → reobserve）
```

动作词表（有限集合）：`run_capability / retry_capability / produce_layer /
produce_chart / produce_statistics / repair_runtime_layer /
reassert_mapspec / reobserve / finalize_product`；每个 intent 携带
`facet_id / kind / reason / capability / artifact_inputs / action_class /
execution_mode`，其中 `execution_mode ∈ {capability, runtime_repair,
observation, finalization}`（动作由哪条**既有**通道执行），`action_class`
区分执行债 / 产物债 / runtime 修复债 / 观察债 / 终验债。

[GIS Plan] 投影行改为由 `action_intent_projection` 产出（session_plan）：
capability 在场时格式与旧 advisor 零漂移；非 capability 通道附加
`<mode>:` 前缀（`[Next GIS Action] runtime_repair:map_layer:repair_runtime_layer`）。

** Scenario B 升级规则**：render 缺席 facet 的血缘若显示 source artifact
已确认过期，`repair_runtime_layer` 升级为 `retry_capability`（重跑上游）——
死 artifact 没有「重挂载」的语义（§3.4）。

### 3.2 Deterministic Runtime Repair Engine（`gis_harness/runtime_repair.py`）

纯确定性分类器 + 有界执行器，回答「desired state 正确而 runtime 偏离」：

```text
render_layer_missing + spec 层在场 + source ref 存活/未知
    → reassert_spec_layer（UpsertLayerIntent 内容保持重提交；
      revision 前进 → spec 提交前端 → reconcile 重挂载）
render_layer_missing + source ref 确认过期
    → 执行债披露（绝不 remount 死 artifact）
mounted 但 observed 不可见 + spec 期望可见 + 非 user-owned
    → restore_expected_visibility（PatchLayerPresentationIntent）
required 组件 spec 启用而观察未挂载
    → reassert_component（patch_component 重提交）
user-owned 隐藏 / source 收敛中（transient）/ 期望态本身缺失
    → no-op / 交还 finalizer（不越界）
```

全部修复走**既有突变通道**（`apply_gis_mutation` / `apply_gis_mutation_batch`
/ `mapspec_store.patch_component`）——不建第三通道；user-wins 守卫
（spec owner 印记 + provenance ring）在锁内逐 intent 复检。

**CAS**：所有修复突变携带 `expected_revision = 观察盖章 revision` ——
快照与提交之间 spec 被并发推进时整批 superseded，旧内容绝不覆盖新编辑。

**闭环**：观察 POST → finalizer（render issues 且本轮无 desired-state 修复）
→ 分类 → 有界执行 → 响应携带 `runtime_repair {applied, exhausted, mapspec,
mutation_revision}` → 前端 `commitMapSpecDocument`（旧代次保护）→
reconcile 重跑（重新挂载）→ settle 后自动再观察 → 重验。修复推进 revision
后旧观察自动变 stale（ADR-0086 revision 门）——stale 证据不可能再次触发
修复。

**边界**：`render_source_missing`（source 收敛中）是瞬态 warning，不修复；
`runtime_errors`（瓦片错误）不判失败；style 级质量修复仍归
`cartography/runtime_repair.py` 的 AUTO_SAFE 通道（ACK 闭环），本模块不动
style。

### 3.3 Facet ↔ Artifact ↔ MapSpec Lineage（`product_lineage.py`）

纯函数血缘投影（增强既有 ArtifactRegistry/ArtifactGraph，不建第二套
artifact 系统）：facet → artifact refs（output = source ref / bound_ref /
chartRef；input = registry artifact 类型交集推断的上游行）→ liveness
三态（alive / dead / unknown —— 只读调用方快照，不虚构）。回答：

- 某 facet 由哪些 artifact 支撑；某 layer 来源于哪个 analysis result；
- chart 欠账时可复用哪些**存活**的表/聚合类上游 artifact（`reusable_inputs`
  —— 随 GISActionIntent.artifact_inputs 带给 Pi：只补产物，不重跑分析链）；
- 哪些 output ref 已死（`dead_outputs` —— 执行债证据）。

### 3.4 User Wins（不变量重申 + 新守卫点）

`user decision > agent decision > auto repair`。runtime repair 不对抗用户：

- user-owned 隐藏层 → 分类器 no-op（披露 `user_owned`，不产生修复突变）；
- 即使分类器漏判，突变层守卫（`_check_user_presentation_guard` 锁内复检 +
  ring）拒绝反转用户显隐；
- 整层 upsert 重提交继承 durable presentation（`_preserve_durable_
  presentation`）：用户隐藏的层被 reassert 后仍隐藏；
- floating 组件位置、presentation preference 从不进入修复面。

### 3.5 有界修复循环（P5）

`MAX_RUNTIME_REPAIR_PASSES = 2`。ledger（`_runtime_repair_state`，session
ephemeral）按 **observation fingerprint 分代**：spec 内容变化（用户/agent
编辑）即重置预算；同一发散代次内总执行轮数达上限、或同一计划重复出现即
`exhausted: true` 披露（交回 Pi），**绝不自循环**。失败的尝试同样入账
（无重试上限的失败重放是无限循环的种子）。

### 3.6 Big Dataset Contract（P6）

`cost_model.resolve_runtime_strategy()`：规模/artifact 语义 → 运行通道
词表（`frontend_native / preaggregated / server_vector / server_raster /
vector_tile / raster_tile`，全部有真实代码参照，不虚构通道）。阈值与
`infer_execution_policy` 同源（FETCH_CAP / DATA_FABRIC 单一契约）。
前后端 magic number parity 由测试锁定（ref-source-resolver 的
`FETCH_FEATURE_CAP` 必须等于后端常量 —— 漂移即测试红）。

### 3.7 GIS Runtime Trace（P7）

`gis_harness/trace.py`：进程内、纯内存、有界（session LRU ≤256、每会话
事件环 ≤64、计数器键有限集合）。记录 finalization / runtime repair /
action intent / observation 拒绝的**聚合视图**（决策链），供测试与诊断；
不进 LLM context、不持久化、不网络。write 全部 best-effort —— trace 故障
不影响业务。

## 4. Rationale（问题清单逐条）

- **Why this is not a second agent loop**：`resolve_next_gis_action` 与
  `run_runtime_repair` 都是无自主推进的确定性函数 —— 前者只产出建议行
  （执行仍归 Pi + harness），后者只在新鲜观察到达时执行至多一轮预算内
  修复，没有"持续运转的目标"。修复后的收敛验证仍走既有观察触发点
  （reconcile settle → POST → finalizer），不是新调度器。
- **Why ProductAction remains derived**：GISActionIntent 若持久化即第二
  计划真相（ADR-0076），且必然与行状态/spec 漂移；复算即得（毫秒级纯
  内存），持久化只有成本没有收益。
- **Why runtime repair may mutate but ProductGraph may not**：修复突变走
  MapSpec 既有突变 API（GISMutationBatch / lifecycle engine），写的是
  **唯一地图真相**（且内容保持/CAS/守卫三重约束）；ProductGraph / facet
  completion / lineage 是只读投影，写它们 = 第二状态机。修复的落点是
  desired state 本身，不是投影。
- **Why user interaction wins**：见 §3.4。自动修复的正确性边界是
  "desired state 正确而 runtime 偏离"；用户决策改写 desired state 本身
  （presentation_owner= user），此时 runtime 与新 desired state 一致，
  自动修复若强行拉回旧期望就是在对抗权威。
- **Why expired artifacts trigger execution rather than remount**：ref
  descriptor 被驱逐意味着**数据事实不存在**；重提交 spec 层只会让前端
  再挂一个空源（假修复）。执行债交还 capability → resolver → tool 重跑
  才恢复数据事实。存储抖动（探测 unknown）不算过期 —— 不把瞬态错误判成
  执行债（与 gather_completion_inputs 的三态语义一致）。
- **Why repair passes are bounded**：修复的每轮都是一次 spec 提交 +
  前端 reconcile + 观察往返；无界循环 = 无限 toast/revision 膨胀。两轮
  不收敛说明发散原因不在 reassert 能修复的面上（如前端 bug / 持久
  错误）——继续自动对抗只会掩盖问题，exhausted 披露交回 Pi/用户才是
  诚实语义。

## 5. Alternatives

- **advisor 输出直接绑 tool id**：拒绝 —— 绕开 Capability → Algorithm
  Resolver → Tool 分层（P8 成果）；
- **runtime repair 走 map-action 命令通道**（cartographic_runtime_repair
  同款）：visibility 修复曾考虑；选择 spec 通道（PatchLayerPresentationIntent）
  是因为它继承 durable presentation 继承与 user-wins 守卫、且与 finalizer
  的 show_layer 修复同通道 —— 不为 runtime 修复另开前端命令契约；
- **把 render findings 塞进 cartography 质量环**（复用
  `_advance_runtime_cartographic_repair` 的 ledger）：拒绝 —— 质量环的
  ledger 由 `evaluate_cartographic_session` 持有，混入产品级 render 修复
  会让两个触发源竞争同一状态键；
- **新建 repair 计划持久化**（RepairPlanStore）：拒绝 —— 第二计划真相；
  ledger 是会话级 ephemeral 账本（与 `_cartographic_repair_state` 同级），
  不是计划。

## 6. Compatibility

- `[Next GIS Action]` 行：capability 情形格式零漂移；新增 mode 前缀只在
  非 capability 通道出现（新增信息，旧消费方按行文本容忍）；
- 观察响应新增 `runtime_repair` 键（additive）；旧前端忽略未知字段；
  旧后端 + 新前端：响应无该键 → 不提交（惰性降级）；
- `map_product` 块 / SessionPlan schema 零变化；无迁移。

## 7. Performance

- 分类 O(layers + sources + components)，与 feature 数无关（Scenario H
  测试锁定：150k features 与 2 features 同计划）；
- 修复执行只在渲染发散路径触发（终验 render_status == issues 且本轮无
  desired-state 修复）；每轮 ≤8 层 upsert + ≤8 显隐 + ≤4 组件，CAS 保护；
- lineage 投影 O(rows × 上游类型交集)（章节行数十量级 → 亚毫秒）；
- trace 全有界，write 侧 try/except 包裹。

## 8. Failure semantics

- 观察 rejected（fingerprint/generation 门）→ 不终验不修复；
- 修复突变 superseded（CAS）→ 不算 applied，尝试入账；
- 预算耗尽 → `exhausted: true` 披露，不再产生修复与 spec 提交（前端回路
  自然停止）；
- 修复通道异常 → 单项跳过/整体披露，下一触发点重试或 exhausted。

## 9. Migration

无 schema 迁移。`_runtime_repair_state` 为新 map_state 键（session 级
ephemeral）。

## 10. Future work

- finalizer 渲染证据接入导出 parity 的运行时判定（ADR-0086 deferred）；
- facet 级导出 parity（per-facet export 支持矩阵，ADR-0087 deferred）；
- reassert 对 raster 通道（`ref:raster/*`，磁盘态）的存活探测目前交给
  artifact_lifecycle 巡检 —— 修复分类器对 raster 源一律 reassert，
  exhausted 披露兜底；
- trace 聚合视图暴露到管理面（当前仅测试/日志消费）。
