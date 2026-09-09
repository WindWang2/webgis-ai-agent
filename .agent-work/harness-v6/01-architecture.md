# Harness V6 Architecture（Semantic Retrieval · Durable Context · Long-Horizon Autonomy）

目标链路：`NL query → semantic retrieval → workflow evidence → tool dispatch → failure → remediation → resume/continue → rendered-state verified → final verdict`

全部决策 additive：不改 typed DAG 编译器、不动 GeoCompute scheduler、不换 Pi、不建第二 planner/状态源。

## D1 Hybrid Tool Retrieval V6（W2-W3）
新 `app/services/chat/semantic_retrieval.py`：
- **信号层（4 路，全部确定性、可单测）**：
  1. lexical（既有 `tool_retrieval.rank_tools`，baseline 恒在）；
  2. semantic-lexical：双语同义/近义扩展词表（GIS 域本体映射，如 克里金↔kriging、等时圈↔isochrone→service_area）→ 扩展查询词再打词法分（**零模型依赖**，默认生效）；
  3. capability graph：既有 algorithm_registry capability_tool_map 反查 + capability 别名（现有 active_capabilities 通道扩展为 query 内 capability 词命中）；
  4. methodology evidence：workflow_v4 MethodologyRegistry 的 12 方法族/44 候选（双语 label）→ query→family 命中 → 族内 method→tool 映射加成（**只作检索信号，不做计划**）。
- **融合**：归一化加权融合（每个信号通道独立归一到 [0,1]，权重常量有界+可解释，score_components 留痕）；embedding 检索器（faiss_store 同款 sentence-transformers，registry 指纹失效缓存）为可选第 5 路，env `TOOL_RETRIEVAL_SEMANTIC` 显式注入时叠加，失败降级。
- **接线**：`DynamicToolSurface.select()` 内启用 hybrid（kill switch `GIS_TOOL_RETRIEVAL_V6=0` → V5 逐位行为）；`pi_native_surface` 生产路径自动受益。
- 不建立第二 planner：输出仍只是 SurfaceSelection 投影。

## D2 Confidence / Abstention（W4）
- `SurfaceSelection` 增加 `confidence`（0-1）与 `abstained: bool`：置信度 = 校准函数(margin(top1,top2), 绝对分数水平, 证据通道覆盖数)；常数在校准集上测量后钉死。
- 低置信度 → `abstained=True` + 面上保留 list_available_tools 两跳通道 + selection_trace 披露 `abstain_reason`；生产 seam（pi_native_surface）在 abstain 时**不缩小**既有默认面、发低置信度 disclosure marker，并触发 deepen_profile/语义编译建议（不静默乱选）。
- 语料增加 `out_of_scope` 类（registry 无此能力）→ 期望 abstain；指标增加 abstention_correct_rate、calibration（10 桶 ECE）。

## D3 Retrieval Corpus V6（W2）
- `retrieval_eval_corpus.py` 扩到 **≥400 条人工金标**（保留 66，新增 direct/近重复对/hard_negative/ambiguous/out_of_scope/英文，覆盖全部算法域+cartography/map/data 管理）；查询为口语措辞，与 lexical 索引/能力反查不同源。
- 指标门：p@1/r@k/invalid/abstention/calibration 全部实测后钉保守线；**p@1 与 invalid 相对 V5 基线（0.6515/0.3333）必须有实质改善并钉线**。

## D4 Durable Context & Project Memory（W5）
新 `app/services/gis_harness/durable_context.py`：
- 三分层模型：`durable_facts`（user_goal/方法族选择/workflow position/dataset fingerprints/verdict 摘要/ref 清单）、`rebuildable`（tool surface/observations/plan 投影 —— 可由权威状态重建，不入 durable）、`forbidden`（LLM raw context、token/密钥、无界载荷）。
- 载体：**既有** `WorkflowResumeAnchor.anchor` JSON additive 键（`reasoning_digest`/`recovery_state`/`position`），零新表零 migration；写入点 = turn settle + anchor save。
- resume：`resume_from_anchor` 恢复后注入新 session（resumed_from 块扩展），保持 owner/project authz 语义（require_owned_session/user 一致校验不放宽）。

## D5 Persistent Recovery Ledger（W6）
新 `app/services/gis_harness/recovery_ledger.py`：
- (session, tool, failure_class) → attempts 的 **durable** 账本：session-plane JSON 文件 + flock（trace_store V5 同款模式）+ 进程内缓存；跨 worker 一致。
- 回写纪律：dispatch **成功 → reset 该 (session,tool) 的 class 计数**；失败 → +1；TTL 衰减保留（时间上远离的旧账归零）；turn 边界显式落盘。
- 与进程级 RemediationLedger 的关系：durable 账本为 authority，进程级作为无 session 上下文时的降级（诚实披露口径不变）。`classify_and_remediate` 优先消费 durable。
- 防恢复后无限重试：resume 时账本随 anchor/recovery state 重建预算余量。

## D6 Trace Store V6（W7）
- **分段**：`trace_chains.jsonl` 拆为分段文件 `trace_chains.<seg>.jsonl`（每段 ≤ SEG=128 条，段内单调 seq）+ `manifest.json`（段清单/min-max seq/行数/可选压缩标记）；append 只写当前段；roll 时新开段；trim 变为**整段淘汰**（保护记录所在段除外，段内仍保护）。
- **增量读**：`read_chains_since(session_id, after_seq)` 只开 seq 覆盖的段；`last_seq` 读 manifest O(1)。
- **压缩**：非当前段可 gzip（`.jsonl.gz`，读侧透明解压）；默认最近 1 段不压缩。
- **兼容**：旧单文件布局读取容忍（迁移读）；`read_chains` 行为不变；写侧仍 flock、单调 seq、幂等 settle、FINAL_VERDICT 保护。
- **跨进程汇聚接口**：`iter_session_chains(session_ids)` 有界迭代器 + GeoCompute trace bridge 消费点核实（只提供接口，不复制状态）。
- trace 仍**不是**第二 SessionPlan/Workflow 状态源：只读证据面。

## D7 Long-Horizon Continuation（W8）
新 `app/services/gis_harness/continuation.py`：
- `decide_continuation(evidence, ledger, observation)` → 纯函数裁决：continue / deepen_profile→requalify / remediate→retry→re-verify / replan / abort_with_disclosure。
- 两条生产回路显式化：①数据资格不足 → deepen_profile（D4/V5 既有）→ 重资格（data_qualification）→ 继续；②渲染失败 → classify（failure_taxonomy）→ remediation（bounded）→ 重试 → rendered-state 再验证（render_observation）。
- 预算耗尽 → abort_with_disclosure（复用 REMEDIATION_POLICY 上限）；裁决与状态进 recovery_state（D4 载体），resume 后不机械 replay —— 从 evidence 重新判定。
- 接线：runtime_repair/finalizer 既有入口调用裁决点（不新开主循环）。

## D8 Subagent Runtime 加固（W9）
- `SubagentBudgetClass`（light/standard/heavy/research）显式词表 → 每 role 映射默认 class（数字仍由既有 SubagentBudget 承载，class 只是可审计的预算档位 + 可按模型倍率缩放）。
- 加固点：子代理超时/取消/异常路径不污染主 turn 的 chaos 测试；lineage（parent_turn_id/depth/role）在结果与事件中全量携带（V5 已有，补齐缺掉的边角）；无递归派发（depth≤2 钉死测试已存在则复用）。
- 不重建 agent 框架：继续 Pi/Harness seam。

## D9 Rendered-State 语义阶梯（W10）
- `app/services/gis_harness/observation_states.py`：封闭词表 `mounted → loaded → rendered → data_present → semantically_correct`（每层/每 chart 一态 + `pending/unknown` 诚实态）；纯函数 `aggregate_observation_state(...)` 从既有 observation payload 派生（不新增采集面，先消费 V5 telemetry）。
- finalizer 消费：完成判定基于 `semantically_correct`（intent↔rendered 核对通过）而非「工具调用成功」；telemetry 缺席 → pending（不假通过）。
- workflow 消费接口：`to_workflow_health()` → ok/degraded/blocked/partial 词汇，供 WorkflowInstance runtime projection 消费（additive 键）。

## D10 Chaos / Recovery Corpus（W11-W12）
- `app/evaluation/recovery_ledger_corpus.py`（或扩 runtime_corpus）：断连/重复 turn/Pi cancellation/Redis 短故障/worker 重启/trace 写中断/resume dangling/telemetry 迟到 → deterministic 预期。
- 不变量测试：无全局锁泄漏（flock 泄漏检测）、无无限 retry（预算钉死）、durable 预算重启不丢、无跨 session 污染、trace 无撕裂读。

## D11 Docs（W14）
- ADR `0119-gis-harness-semantic-autonomy-v6.md`；CHANGELOG 一段；PR summary。

## 测试 oracle（每 D）
单元纯函数 + 真实 seam 接入测试（不 mock 事实源）+ 兼容/负路径 + chaos 不变量；retrieval 语料门 + 校准门；durable 账本重启/跨进程测试（多进程 flock）；trace 增量读等价全量读契约。

## 与并行 Epic 边界
- 不碰：workflow compiler 本体、GeoCompute scheduler、渲染器算法、extensions runtime。
- 共享面最小化：CHANGELOG 追加一段；无 CI 修改；migration 原则上零新增；ADR 独立编号。
