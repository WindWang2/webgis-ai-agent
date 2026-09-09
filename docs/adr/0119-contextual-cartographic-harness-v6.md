# ADR-0119: Contextual Cartographic Harness V6

- 状态：Proposed
- 日期：2026-09-09
- 关联：ADR-0118（Semantic Workflow Compiler V4）、
  ADR-0104（GIS Harness Autonomous Runtime V4）、ADR-0103（Cartographic Design System V4）、
  ADR-0099（空间科学平台）

## 背景

Compiler V4（ADR-0118）交付了 15+8 阶段确定性编译器、12 方法族与 typed DAG，
但 Phase-0 审计（`.agent-work/contextual-cartographic-harness-v6/00-baseline.md`，
HEAD 8a33e3a5）证实多系统平行、回路未闭环：

1. **编译器生产不可见**：V4 DAG 只读咨询，生产走 plan_orchestrator 两段式
   planner；compiler 证据（ontology 匹配/资格裁决/候选 trace/回退层）
   生产不可见（审计缺口⑦：LLM 路径与 harness 合成路径不对称）。
2. **runtime 双轨**：workflow 运行态归 workflow_instance（会话章），
   MapSpec/artifact/图层各有血缘，无统一运行态投影；变更无分类、
   无受影响子图、无局部重算。
3. **观测与诊断缺位**：视觉观测缺位；finalizer/render/runtime 三域
   findings 各说各话，完成裁决无单一 verdict；修复无规划、无防循环。
4. **上下文与恢复未收敛**：工具检索无生产级语义路径；context 无预算；
   resume 恢复后假设 artifact 可用而不验证；锁 guard 只有前端单点。

收敛目标：compiler / runtime / lineage / recompute / observation /
findings / verdict / repair / retrieval / context / resume / locks
收敛为唯一状态驱动、可观察、可诊断、可恢复、可局部重算的
Contextual Cartographic Harness——V6。

## 决策

1. **W1+W2 Compiler→Runtime bridge**（`13af748f`）：新增
   `app/services/gis_harness/runtime_bridge.py`，`derive_runtime_block`
   纯投影（确定性、有界、诚实降级）→ `gis_chapter[workflow_runtime_v6]`
   单键；typed node_id 成共享命名空间，节点状态 = plan_graph 同一派生源
   在 typed DAG 上的投影（StageState 词汇复用，零平行枚举）；证据漂移 →
   typed 边下游闭包 stale，无关分支零触碰；`maybe_update_runtime_projection`
   接入 3 触发点（agent_pi_bridge 成功/失败、chat observation 路由），
   LLM make_plan 路径补齐 V4 证据。测试：12 新例，gis_harness 域 998 全绿。
2. **W3 Artifact lineage 双向索引**（`35c7bbbb`）：运行态块新增
   `artifact_index`（ref → producer_node/consumer_nodes/layer_ids/
   component_ids，有界 ≤96）与节点 `inputs` 输入血缘；`_cap_all_refs`
   全行登记防过渡态失明；查询 API `artifact_lineage`/`node_lineage`
   （缺席 → None，不虚构）；服务入口接 `mapspec_store.get_mapspec`
   权威读取。测试：15 新例，gis_harness 域 1001 全绿。
3. **W4 Semantic Diff→Affected Subgraph 接线**（`81686755`）：桥内字段级
   变更分类 algorithm/parameter/data（RECOMPUTE_DIMENSIONS + CHANGE_TARGETS
   单一词表，新增 node 目标类）；`compute_affected_subgraph` 成唯一闭包引擎；
   `changes`+`recompute_plan` 进运行态块；`[GIS Recompute]` 单行进
   SessionPlan 投影（调度面可见，bridge 不翻行状态）；修复 port 后缀断链
   缺陷 + 回归锁。测试：bridge 21 + semantics 20 全绿，域 1032 全绿。
4. **W5 Partial Recompute + Reuse Validation**（`81686755`，同 commit）：
   `records` 快照（`list_artifacts` ≤128）进 derive，证据指纹/artifact
   health/package 稳定性三校验 → safe/unknown/unsafe；证据不足 → unknown
   （不假设）；unsafe 翻 stale（`reuse_unsafe:*`）强制重算；style-only
   结构性免疫科学重算；`reuse_validation` 进 state_fingerprint
   （gen1→gen2 一次性 +1 后稳定）。测试：见对应 commit。
5. **W6 Unified Findings adapter**（`cdf8ebce`）：新增
   `completion/unified_findings.py`，UnifiedFinding 12 字段投影（domain
   词表原地保留只投影，不迁移不新造码）；三投影器——harness_finalizer
   34 码、render_diagnostic 18 码（全域 degradation_only 永不阻断）、
   workflow_runtime（stale=warning 但保守阻断）；`blocks_completion`
   单点推导，`collect_unified_findings` 确定性序 + 有界 ≤24。测试：
   9 新例，gis_harness 域 1017 全绿。
6. **W7 Completion Verdict 单一化**（`cdf8ebce`，同 commit）：
   `evaluate_completion_contract` analysis 维纳入 runtime stale 硬输入
   （工具执行成功不再充分）；`derive_product_verdict` READY* 遇 stale
   压 NEEDS_REPAIR；无运行态块旧章节 parity 零漂移专测；VERDICT_* 五值
   冻结不变。测试：见对应 commit。
7. **W8 Deterministic Cartographic Observation**（`529e869f`）：
   `derive_component_layout_findings`——floating 组件实测像素 rect 重叠/
   完全越出画布 → layout_conflict warning 进主校验链（transient 不判
   error，user-wins）；`derive_component_lifecycle` 七阶段统一投影
   （requested→…→diagnostics，非 chart 族 rendered/data_bound=None 不虚构）；
   前端 observation 增 canvas 容器像素遥测 + DTO 白名单（additive）。
   测试：11 新例，域 1028 全绿，前端 mapspec-runtime 140 全绿 + tsc 净。
8. **W9 Visual Observation seam**（`529e869f`，同 commit）：新增
   `visual_evaluator.py`，触发白名单 5 种（finalization/major_layout_change/
   map_model_change/visual_repair/user_request；pan/zoom 不触发）；
   `GIS_VISUAL_EVALUATOR="module:callable"` hook 默认关闭；输出白名单校验
   （mutation 意图结构性判废，强制 domain=visual + degradation_only +
   不硬阻断）；评估器不接触 MapSpec。测试：见对应 commit。
9. **W10 Repair Planner**（`3662a07f`）：新增 `repair_planner.py`，
   UnifiedFinding → 16 修复类 × 5 安全级表驱动分类（code 精确→scope
   兜底→reobserve 保底）；锁/override → not_allowed（user-wins 硬约束）；
   visual 软发现一律 requires_user_approval；degradation 面不产生自动动作；
   executor 只列既有通道（不建第三修复通道）；`plan_repairs_for_chapter`
   接入 maybe_finalize_map_product，repair_plan 快照进 map_product 块。
   测试：7 新例，gis_harness 域 1035 全绿。
10. **W11 Repair Loop 防循环**（`3662a07f`，同 commit）：finding 指纹
    （domain+code+entity）+ 状态 epoch（runtime_rev:mapspec_rev）+ 尝试计数
    账本（`map_state[_repair_loop_v6]`，≤32 条）；同 finding 同 epoch →
    no_progress；≥3 次 → repair_exhausted → abort_with_disclosure 披露。
    测试：见对应 commit。
11. **W12 Tool Retrieval V6**（`7a94c4e3`）：新增 `ToolSemanticIndex`
   （复用 RAG 模型约定 paraphrase-multilingual-MiniLM-L12-v2；余弦下限
    0.15、量纲 6.0、上界 512）；`TOOL_RETRIEVAL_SEMANTIC` 显式注入，
    默认仍 lexical，失败降级词法；paraphrase 语料 66→306 条
    （PINNED_* 未动，p@1 0.6242/r@5 0.8282/r@10 0.8775/invalid 0.2917）。
    测试：33 passed。
12. **W13 Contextual Context Assembly**（`0f31a466`）：新增
    `v6_context_blocks` 三层投影（node-local 2048B / workflow-global
    4096B / map-situation 2048B，字节 hard cap + 截断留痕 + byte_cost 进
    budget_report）；经既有块通道注入；SessionPlan 投影加 additive 进度行。
    测试：19 新 + 相关 142 + 上下游 159 passed。
13. **W14 Resume VNext**（`1e25a308`）：新增 `resume_verify` 三裁决
    live/stale/unknown（ref 存活 + content_hash + data 在场 + mapspec 依赖
    + workflow 指纹；satisfied-but-unverified 翻 stale 经 W5 闭包引擎）；
    anchor schema v2（ref_evidence/workflow_fingerprint 快照，旧锚判
    unknown）；授权路径零改动。测试：7 新 + 回归 51 passed。
14. **W15 Human-Agent 状态收敛**（`8f567075`）：`lifecycle_engine` 新增统一
    `guard_locked_partitions`（locked/unlocked 分区 + 机器可读披露，
    code=layer_locked；user 源唯一旁路）；planner/runtime_repair/
    quality_loop 改走统一 guard（not_allowed 语义不变）；workbench 增
    lockedComponentIds；override 三分类 + 来源记录；transient 键提交边界
    剥离。测试：15 新 + 回归 1065 passed。
15. **W16 Closed-loop Corpus + E2E**（`3a8bfa6f`）：17 制图类型 × 12 故障注入
    = 204 条闭环语料，六段式期望全部命中既有词汇表零自创；§57 S1–S10
    一文件十测全绿（S8/S9/S10 薄封装 W15/W14/W11 既有口），确定性无 LLM。
    测试：17 新 + 关联 82 passed。
16. **W17 Performance/Security**（`a8da6d6e`，未动生产代码）：perf 结构契约
    7 项（@pytest.mark.perf，零 wall-clock：500/1k/10k layers 收口与 N 无关、
    lineage 有界、200 节点 DAG 精确、10 层产消链、long chat 三块 cap、
    大 registry 索引一次构建、50 findings 截断）；安全门 5 项（tier-3 双检索面
    不可见含恶意注入、跨 owner resume 拒绝、锁后端权威、披露无敏感载荷）。
    测试：12 项全绿。
17. **W18 Documentation**（本 wave，只写文档）：ADR-0119、
    CHANGELOG harness-v6 条目、11-pr-summary DoD 34 项核对、
    09-progress Waves 行更新到 W18。

## 红线

- **单一事实源**：节点状态 = plan_graph 同一派生源（StageState 词汇复用，
  零平行枚举）；血缘全部投影既有事实（行 bound_ref / spec source ref /
  组件 chartRef），不发明第二血缘；domain 词表原地保留只投影。
- **无第二 Registry**：methodology/artifact/tool 索引键复用既有实现
  （`ToolRetrievalIndex._index_key`）；executor 只列既有修复通道；
  `compute_affected_subgraph` 为唯一闭包引擎。
- **LLM 不碰状态**：compile_workflow_semantics 保持 advisory 零执行；
  visual 评估器不得改图（mutation 意图结构性判废）；transient 键提交边界
  剥离；user-wins——锁/override 命中任何路径不得绕过。
- **确定性**：全链 <2s 护栏内纯函数投影；缺席 → None/空/unknown，
  绝不虚构；失败 → 诚实降级（deterministic 层不受影响）；
  VERDICT_* 五值、15 阶段 COMPILER_STAGES 契约冻结。

## 后果

- 正向：意图→方法→typed DAG→运行态→观测→诊断→修复→验证全链确定性
  可审计；变更可精确计算受影响子图并局部重算；无效方法/不安全复用按
  事实拒绝；修复有规划、有预算（账本 ≤32、尝试 ≥3 熔断）、无无限循环。
- 代价：运行态块单键 + artifact_index ≤96 + records ≤128 有界增长；
  context 三块共 8KiB hard cap；repair 账本 ≤32 条；语料随方法论演进维护。
- kill-switch 清单（全部默认开/默认语义，关停即回退既有行为，均已
  grep 确认真实存在）：
  - `GIS_WORKFLOW_RUNTIME_V6=0` → 运行态投影整体关停
   （`app/services/gis_harness/runtime_bridge.py:49`）；
  - `GIS_TOOL_RETRIEVAL_V4=0` → 检索恢复 V3 行为
    （`app/services/chat/tool_retrieval.py:68`）；
  - `GIS_TOOL_SEMANTIC=0` 或 `TOOL_RETRIEVAL_SEMANTIC=off`/空 →
    语义路径整体缺席，静默降级词法
    （`app/services/chat/tool_surface_v3.py:246`、
    `app/services/chat/tool_semantic_retrieval.py:18`）；
  - `GIS_VISUAL_EVALUATOR` 未配置 → visual 评估缺席，deterministic 层
    不受影响（`app/services/gis_harness/visual_evaluator.py:38`）。

## 验收（本 Epic 内落地）

- W1–W11：gis_harness 域 1035 全绿；cartography 门 708 全绿；ruff 净
- W12：语义语料 306 条（MIN 300），PINNED_* 全绿；33 passed
- W13：19 新 + 相关 142 + 上下游 159 passed
- W14：7 新 + 回归 51 passed；W15：15 新 + 回归 1065 passed
- W16：closed-loop corpus 204 条；§57 S1–S10 全绿（17 新 + 关联 82）
- W17：perf 7 + 安全 5，共 12 项全绿，未动生产代码
- 两轮独立 review 无未修 BLOCKER/CRITICAL/MAJOR（待办，见 10-review-findings.md）
