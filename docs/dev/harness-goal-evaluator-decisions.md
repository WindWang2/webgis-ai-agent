# Goal Satisfaction Evaluator — Decision Log

规则：每个决策记「背景 → 选项 → 决定 → 理由 → 回滚面」。执行时事实优先于任务书文字；任务书假设与代码不符时记「任务书假设 → 实际 → 调整」。

## D-001 ADR 编号

- 背景：master ADR 至 0179；open PR #1274/#1275/#1277 声明 ADR-0180，#1276 声明 ADR-0181，#1278 声明 ADR-0182。
- 决定：本任务使用 **ADR-0183**（`docs/adr/0183-harness-goal-satisfaction-evaluator.md`）。
- 回滚面：无；若 #1273-#1279 先合且占用 0183，rebase 时顺延重编号（纯文档移动）。

## D-002 不新增第二「L5 / verdict」词表

- 任务书假设：需要建立 goal 评估层级命名（Execution evidence → … → User-goal satisfaction）。
- 实际：master 已有 7 维完成契约 + Product Verdict + final_map + L5 visual（recon §1）。
- 决定：Goal evaluator 的层级轴直接复用既有词表并新增**任务语义维**（user_goal satisfaction = goal requirements 逐项裁决），输出为 additive 块 `goal_satisfaction`；不重定义 READY/verified 词表，不重复计算颜色/布局/标注。
- 回滚面：additive 块删除即回滚。

## D-003 评估器形态：deterministic 纯函数 + 可选 LLM 语义辅助（默认关）

- 决定：`evaluate_goal_satisfaction(chapter, *, user_goal, plan)` 是零 IO 纯函数；LLM/VLM 只允许作为 `assisted` 证据源注入（evidence 带 `evidence_class: "assisted"`），**assisted 证据只能把 not_evaluated 抬为 partial/fulfilled 且必须留痕，永远不能把 missing required evidence 判 PASS**（fail-closed）。
- 理由：G3 红线 + anti-cheating（G6）。
- 回滚面：无状态纯函数；调用点单点。

## D-004 需求面来源（G1）

- 任务书假设：scope/subject/time/analysis/comparison/map/chart/delivery/must/must-not/pinned。
- 实际：intent 产物（`intent.py` 的 export_intents、任务族）、planner 产物（plan.exports、map_layers 角色、required_components）、chapter 行（data_requirements/analysis_steps）已是结构化事实。
- 决定：`GoalRequirement` 从这些结构化事实**确定性派生**（不 raw-text regex）：每个 `analysis_steps` 行、每个 export intent、每个 must/must-not（来自 intent 显式约束字段，缺席为空）、comparison 族能力行 → requirement。schema 版本 `goal_requirement.v1`，序列化为 pydantic 模型。
- 回滚面：派生函数纯函数，调用方可旁路。

## D-005 证据注册（G2）

- 决定：`EvidenceRegistry` 以 chapter + map_product + render_observation 快照为输入构建（只读投影，零持久化，同 goal_graph 纪律）；每条 evidence 带 `{source, revision, confidence, evidence_class}`；上界有界（≤64 条）。
- 回滚面：纯投影可随时重建。

## D-006 子目标状态词表（G4）

- 决定：`fulfilled / partial / blocked / not_evaluated / failed`（任务书词表，master 无冲突）；全局 verdict = `satisfied / partial / blocked / failed / not_evaluated`；**fulfilled 要求全部 required requirement = fulfilled**；任一 required failed/blocked → 全局降级；optional 缺席 → partial 披露不阻断（user pinned 除外）。
- anti-cheat 锚：`false_pass` 计数器进验收指标（G9，最高优先级指标）。

## D-007 停止/续行信号（G5）

- 决定：evaluator 输出 `harness_signal ∈ {complete, continue, repair_cartography, replan, request_clarification, blocked_by_data}`；接线点 = `read_stored_map_product` 载荷 additive 键 + `map_product_block` additive 键；`request_clarification` 只在 intent 明确携带歧义且无 pinned 决策时产生（产品运行时面，开发期不依赖）。
- 生产消费（默认 Pi path）：`task_complete` 折叠**保持既有语义不变**（零回归），goal_satisfaction 作为 additive 披露与 runtime phase 的 advisory 输入；`repair_cartography`/`replan` 复用 `_finalizer_continuation` 既有路由词，不造新循环。
- 理由：验收要求「evaluator 结果能驱动 Harness stop/replan」且「default Pi path 实际消费」——采取「信号进既有 continuation/route 词表 + SSE additive 键」双通道，均有确定性测试锁定。

## D-008 语料与测试（G6/G9）

- 决定：新建 `app/evaluation/goal_satisfaction_corpus.py`（≥100 structured cases，zh/en，single/multi-goal，覆盖矩阵见 ledger）+ `tests/` focused runner；`false_pass` 指标 = 期望 FAIL/NOT-PASS 的案例被判 fulfilled 的比率，必须为 0；语料走 anti_claim.py 同款「数据即案例」模式，零 LLM、全离线。
- 回滚面：纯测试资产。

## D-009 与 open PR 的冲突面控制

- 决定：只新增文件 + 最小 additive 接线点（`completion/pipeline.py` 的 `map_product_block`/`read_stored_map_product`/`finalization_sse_payload`、`session_plan.format_session_plan_projection` 的 `[GIS Goal]` 行、`pi_event_mapper` task_complete 载荷键）。【执行时调整（review P2-6）】：`runtime_state_machine.py` advisory 读取未做——任务语义披露经 map_product 块/task_complete 面已可达，避免与 #1273 在该文件正面冲突；本条与代码对齐。
- 回滚面：每个接线点 <20 行。

## D-010 任务书假设核销记录

- 「derive_goal_satisfaction 或最新等价」→ 实际 = visual_evaluator.py L5 视觉推导（cartography-only）；本任务**不重写它**，把它降级为证据源。
- 「quality tiers/verdict」→ 实际 = completion/contracts（READY 族）+ final_map 词表；复用。
- 「task tracker」→ 实际 = SessionPlan CapabilityProgress + CanonicalPlan（planning/）；作为证据源。
- 「completion/finalize display」→ 实际 = finalization_sse_payload + task_complete SSE；G5 消费点。
