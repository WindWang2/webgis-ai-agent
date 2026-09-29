# ADR-0217: Unified GIS Action IR（统一执行语义中间表示）

- 状态：Accepted
- 日期：2026-09-29
- 方向：H10（Unified GIS Action IR）
- 基线：master `77d2678d`
- 编号说明：0216 已被同波次 H01/H04/H07 占用（open PR #1574/#1573/#1570），本 ADR 顺延为 0217。

## 背景与问题

仓库已有三层意图/计划结构：`MapRequestIntent`（理解，ADR-0150/0215）→ `MapProductPlan`（任务编排）→ `MapPlanIR`（制图表达面，ADR-0214）→ `compile_plan`（最小 MapSpec mutations）。执行入口也已统一（`ToolDispatchService` 单一调度点）。但仍缺**执行语义层**：

1. LLM 手工拼工具序列直接进 registry（`webgis_layer_upsert` 等 tier1 直写引擎）——无 typed plan、无 contract validation、无 diff、无补偿语义；
2. 分析/取数/聚合/导出等行为不在任何 IR 内——不可 diff、不可 replay 到动作粒度；
3. **classification 参数变化不触发派生重算**：`PlanAmendment.classification`（{k,method,palette}）经编译器路径只产出 `legend_spec={"classification":…}` 裸 token（compiler.py `_merged_layer_dict`），全仓无消费者展开——"把分级数改成 7"只改了数字字段；
4. legacy direct 路径无收敛指标，无法度量"工具直拼 → 计划路由"的迁移进度。

## 决策

引入第四层——**执行语义层 `GISActionPlan`（schema `action_ir.v1`，`app/lib/gis/action_ir.py`）**：

- **封闭词表**：`ActionKind`（data_acquire/inspect/transform/analyze/cartograph/mutate_presentation/export/observe）、`SideEffectClass`（pure/derived/session_state/external_io）、`Idempotency`（idempotent/duplicate_safe/non_idempotent）、`FailureStrategy`（fail_closed/best_effort/compensate）。演进只准 additive。
- **typed 动作**：inputs/outputs（IODescriptor：name/ref/semantic_type/fingerprint——refs-only，数据本体永不入 IR）、preconditions（data_ref_alive/capability_eligible/layer_present/layer_absent/revision_match）、params（≤16 键/2048B 调参 token，超限构造期拒绝）、compensation（remove_layer/restore_layer_style 等 reverse 声明）。
- **编译器（纯函数）**：`compile_actions(plan, resolver)` → contract validation（tool 解析 capability-first、副作用声明 vs 事实一致性、弃用闸给 superseded_by）+ Kahn 拓扑（声明序 tie-break，cycle → blocked）+ resource admission **hint**（governor 仍是准入唯一权威）+ 确定性 `compile_digest`/`client_action_id`。产物 = dry-run 视图。
- **plan diff**：`diff_plans` 回答新增/删除/参数变化（键级）/副作用变化/工具变化/顺序变化。
- **legacy adapter + 收敛指标**：descriptor 事实 → Action 投影（kind/side_effect/idempotency 全部来自 registry 元数据，不从工具名猜语义）；进程内有界计数器（direct_routed vs plan_routed）即迁移收敛指标。
- **dispatch 投影闸**：`bind_action_ir` 在 ToolDispatchService 调度前投影+编译（kill switch `GIS_ACTION_IR_BIND` 默认 ON；fail-open）。默认模式 blocking findings 只降级为证据（`ToolDispatchResult.action_evidence`，additive 字段）+ telemetry——生产行为逐位不变；`GIS_ACTION_IR_STRICT=1` 时 blocking → typed 拒绝（决策溯源 riding 既有 TOOL_CALLS 阶段，决策词表新增 `action_plan_compile`）。
- **classification 投影期物化**：`derive.materialize_classification` 在 `MapPlanCompilerService.compile_for_session` 编译前，把 classification 参数物化为完整 legend_spec（breaks/labels/palette_colors）——**compiler 保持纯函数**，物化只发生在 services 投影层，与工具路径共用 `build_graduated_spec` 单一实现（parity 测试锁定）。失败 → typed 记录 + 回落 token 行为（`GIS_ACTION_DERIVE` 默认 ON）。
- **执行器**：`ActionPlanExecutor` 消费已编译产物——逐步 precondition 校验、逐 action 失败策略、逆序补偿（合成不出如实记 UNCOMPENSATED）、step 级 receipt（digest 级，结果本体不入 receipt）。
- **只读工具面**：`webgis_action_plan`（dry_run 编译校验 / usage 收敛指标），零执行。

## 边界（不做）

- 不重造 ToolRegistry；执行仍只经 `ToolDispatchService` → registry.dispatch。
- 不让 compiler 持久化 runtime state；不让 LLM 决定 capability 资格（权威仍在 capability bind）。
- 不改 MapPlanIR/MapProductPlan 语义（它们是上游投影源；产品意图 ≠ 执行语义）。
- 不实现 H04 durable journal（只挂既有 decision_record 词表）；不与 #1573 抢 seam。
- 不做全部 369 工具的逐族迁移（adapter 词表已覆盖投影；逐族 parity 为后续波次）。

## 后果

- 正面：每条 GIS 行为先有 typed IR（可 diff/可验证/可回放）；核心制图旅程的分级参数变化产生确定性派生重算；legacy 收敛有度量；编译/投影零 I/O 全部可 golden。
- 负面/成本：dispatch 每次 +1 次轻量投影编译（纯内存 pydantic 构造，微秒级；fail-open + kill switch 兜底）；`_cartographic_mutation_revision` 物化在 legend 缺 breaks 时多一次会话 ref 读取（仅 classification 参数路径）。
- 迁移：全部 additive——`ToolDispatchResult.action_evidence`、decision kind `action_plan_compile`、新包 `app/services/gis_action/`、新工具 `webgis_action_plan`；既有消费端零改动。
