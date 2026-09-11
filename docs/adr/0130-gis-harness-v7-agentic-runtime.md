# ADR-0130: GIS Harness V7 — Long-Horizon Contextual GIS Agent Runtime

- 状态：Accepted（本地验证；随 PR `feat/harness-v7-agentic-runtime` 交付）
- 日期：2026-09-10
- 关联：ADR-0104（Workflow V4 typed DAG）、ADR-0118（Harness V5 可恢复
  runtime）、ADR-0119（Harness V6 semantic autonomy）

## 背景

V4-V6 交付了 typed DAG 编译、可恢复 runtime（trace flock/seq、remediation
ledger、rendered evidence、resume anchor）与语义自治（hybrid 检索、置信度/
弃权、durable context、durable recovery ledger、分段 trace、continuation
裁决、observation 状态阶梯）。但生产编排事实散落在触发点（agent_pi_bridge
的 tool_result / turn_settled 分别驱动 finalizer / WorkflowInstance /
runtime projection），没有单一状态机回答「任务处于认知闭环的哪一步」；
replan 有裁决词无生产驱动点；finalizer 不直接消费 decide_continuation；
intent_verified=(status==complete) 循环论证；harness 对 12 个已注册子代理
角色零程序化委派；检索缺结构化前置/后置条件与可靠性反馈；金标语料 598 条
硬编码行。

## 决策（D1-D7；全部 additive，不建第二事实源）

### D1 HarnessRuntime 任务级状态机（`runtime_state_machine.py`）

- RuntimePhase 12 态封闭词表（idle→intent_resolved→plan_ready→executing
  →[recomputing|observing|critiquing|repairing|replanning]→finalizing
  →committed / aborted）+ 封闭触发词表 + 合法转移表（文档 + 测试 oracle +
  命令式校验 fail-closed）。
- **阶段 = 章节权威事实的确定性派生**（同 WorkflowInstance 纪律）：
  priority 序 committed > finalizing > aborted > replanning > repairing >
  observing > critiquing > recomputing > executing > plan_ready。
- 持久化 `gis_chapter["runtime_state"]` additive 单键；gate 指纹幂等 +
  锁内 goal/rows/块漂移守卫；RuntimeTransition 环形 ≤16，表外组合记
  `DERIVED_OUTSIDE_TABLE`（可观测不静默）。
- `suspended` 覆盖旗标（turn 收尾未终态挂起，可经锚点恢复；committed/
  aborted/finalizing 不挂起）。
- 触发点 = 既有生产事件（tool_result / turn_settled / render observation
  / verdict_ready / replan_committed），零新事件源。

### D2 PlanRuntime（`plan_runtime.py`）

- `compute_plan_fingerprint`（goal + rows(V2) + contract 核心）→ 版本
  单调推进；history ≤8 环形 + rollback_points ≤4（首次创建不写历史）。
- **replan 预算与生产驱动点同一 commit**（V6 R1 M4 红线）：
  LOOP_BUDGETS 增 `replan:1`；驱动点 = `request_replan`（finalizer 出口）
  —— 置 `plan_runtime.replan_pending` + durable 记账；计划事实一变
  （版本推进）即消费（清 pending）。预算耗尽 → 诚实 abort 披露。
- `seed_recompute_from_failures`（纯函数）：failed 行 → 该 capability +
  PlanGraph 下游闭包 = 最小重算清单；非污染面 satisfied = reuse。

### D3 ContextLayers（`context_layers.py`）

- 九域封闭词表（turn/session/project/workspace/map/data/workflow/artifact/
  capability）；每域 = 权威事实的确定性投影（rebuildable —— 载荷永不进
  锚点，只有域指纹/压缩态摘要 `context_digest`，DURABLE_FACT_KEYS 增键）。
- durable 侧预算：域字节上限压缩 → 总预算按 DOMAIN_PRUNE_ORDER 确定性
  淘汰；violations 留痕。
- checkpoint：map_state `_context_layers` 单键原子写 + revision 单调 +
  content_fingerprint 校验（读侧不符即重建）；turn 边界自动 checkpoint；
  `build_anchor` 携带九域摘要（恢复侧由权威状态重建）。

### D4 CapabilityDescriptors + ScenarioCorpus

- `capability_descriptors.py`：跨 registry 只读投影（capability/algorithm/
  template/component 统一描述符）—— preconditions（几何/CRS 类/最小要素/
  必需字段/科学前提）、postconditions（输出 artifact/不确定性/参数契约）、
  cost/latency profile、fallback 链。
- `select_capabilities`：preconditions 硬过滤（冲突剔除+原因）+ 词法种子
  （CJK 二元组切分）+ cost 偏好（显式要求时）+ 可靠性罚分（durable
  ledger 聚合投影注入，中性缺省）+ 确定性 tie-break。
- tool_surface_v3 select() 增 6.7 描述符信号：小幅加成既有候选、不新增
  候选；kill switch `GIS_CAPABILITY_RETRIEVAL_V7=0` 逐位回退 V6。
- `scenario_corpus.py`：域包×参数槽×确定性展开的生成式金标结构（14 族 ×
  2316 场景 ≥2000 门）；expected 白名单锚定真实 registry（构建期校验，
  registry_missing=[]）；覆盖率门暴露 registry 新族缺语料；确定性 stride
  抽样评测（p@1 下限钉线）。

### D5 MapCritique（`map_critique.py`）

确定性检查词表（消费既有组件/阈值词表，不建第三词表）：blank_map_risk
（全层 rendered 且计数全明确报 0 —— 任一层缺证据即不猜）、
invalid_result_bounds、export_component_missing（title/north_arrow/scale_bar，
warning + family 修复路由）、label_collision（遥测比率 vs CARTO_LABEL_*，
缺席诚实降级）、planned_observed_mismatch。纯函数聚合（≤12 findings，
MapCompletionFinding 同 schema）；finalizer `_validate_all` 增值并轨。

### D6 Finalization Hook 增强

- `intent_acceptance.py`：意图独立三面核对（verdict READY* / desired spec
  层在场且可见 / observed 渲染证据 mounted+visible）。observation 缺席 →
  intent_verified=False（诚实收紧，semantically_correct 不再无证据自证
  晋级）。摘要入 `map_product["intent_acceptance"]`。
- finalizer 出口直连 `decide_continuation`（V6 follow-up 兑现）：
  needs_repair/failed → 裁决 repair/replan/abort；修复预算尽 →
  request_replan 生产驱动点。裁决入 `map_product["continuation"]`。
- READY → 上下文提交（checkpoint_context_layers + commit_runtime_context
  标记 + 阶段推进 verdict_ready）。
- `display_confirmation.py`：最终显示确认钩子 —— 默认 auto（True，零行为
  变化）；`GIS_FINAL_DISPLAY_CONFIRM=required` 等待显式 ack（render_seq
  代次比对）；record_display_ack 为 human confirmation 写半边。

### D7 Delegation（`delegation.py`）

- DelegationSpec handoff schema；role fail-closed 校验；预算/深度/lineage
  全由 SubagentDispatcher 既有语义承载。
- 父侧台账 `gis_chapter["delegations"]`（环形 ≤8）；状态词表封闭。
- 失败回收：首败 + repair 预算有余重试一次；再败/预算尽 → 诚实披露。
- 生产驱动点 `delegate_cartography_qa`：env `GIS_HARNESS_DELEGATION=1`
  显式开启（默认关 —— LLM 依赖不进终验热路径）；同成品 revision 幂等。

## 兼容性

- 零新表、零 migration。全部状态为 gis_chapter/map_state additive 键
  （runtime_state / plan_runtime / delegations / _context_layers /
  _final_display_ack）或既有锚点/成品块 additive JSON 键（context_digest /
  intent_acceptance / continuation / display_confirmed）。旧读者忽略。
- `GIS_RUNTIME_STATE_MACHINE=0` / `GIS_CAPABILITY_RETRIEVAL_V7=0` /
  `GIS_HARNESS_DELEGATION`（默认 0）逐位回退。
- intent_verified 语义收紧（observation 缺席不再自证）是有意的诚实修正，
  测试钉死；observation_health 的 unknown/blocked 投影自然承接。

## 本地验证

- changed-scope 全量回归：tests/unit/gis_harness/（76+ 文件，含 V7 新增
  7 个套件 78 项）+ session_plan/context_assembly/semantic_retrieval/
  tool_surface —— 除 9 项已知 Windows 环境缺失（fcntl/h3，master 基线
  相同）外全绿。
- ruff 全绿（app/services/gis_harness + 相关 chat/evaluation/tests）。
- V6 检索评测门（test_retrieval_eval_v6 / semantic_retrieval_v6 /
  tool_surface_v3 / pi_native_surface）复测通过 —— V7 信号未劣化。

## 风险与回滚

- 新增投影全部廉价门控（gate fingerprint），失败仅少披露不阻断。
- 回滚：env kill switch 逐位回退；additive 键可被旧代码安全忽略。
- 与并行 Epic 边界：不改 typed DAG 编译器本体、不碰 GeoCompute scheduler
  /渲染器/extensions；chat 侧仅 tool_surface_v3 一个 gated additive 信号。
