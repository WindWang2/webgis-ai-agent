# 07 — Context / State 设计（Waves 13–15）

## GIS Situation ≠ LLM Context（§25）

GIS Situation 由有界事实组成：Conversation state、Workflow state、Workflow DAG、Dataset profiles、Artifact lineage、MapSpec、Workbench state、Runtime observations、Findings、Repair state、Trace、Resume anchor。LLM 每轮只拿必要投影。

## W13：Contextual Context Assembly

三层投影（落 `context_assembler.py` / `pi_turn_context.py` 现有块机制，不新建通道）：
1. **Node-local Context**：active workflow node（W1 投影取当前 running/failed/repairing 节点）→ 其 ports/params/obligations/failure/artifact refs 的有界摘要
2. **Workflow-global Context**：DAG 阶段进度、stale 计数、blocked 原因（复用 `format_session_plan_projection` 投影面扩展）
3. **Map Situation Summary**：mapspec revision、verdict、render_status、未决 findings 数、组件异常（复用现有 map_state/verdict 块）
- 依据：active node、current failure、current repair、user latest action、current map state（§26）
- 预算：接入现有 `history_compression`/`context_policy` 预算体系；新增块有 hard cap 并度量（schema_byte_cost 类指标）

## W14：Resume VNext

现有 anchor 已做 ref 重水合 + missing/dangling 披露（resume_anchor.py）。深化：
1. 恢复后**验证而非假设**（§28）：verify refs（liveness）、artifact revision（content_revision/data_fingerprint）、data existence、mapspec dependency、workflow fingerprint（package 版本 + rows_fingerprint）
2. 无证据 → `STALE/UNKNOWN`，不得假装 SUCCESS；stale 节点进入 recompute 流程（接 W5）
3. 真实场景验证（§27）：执行 40% → 中断 → resume → 恢复 graph/artifact mapping → 验证 live refs → 标 stale/dangling → 继续剩余 DAG（E2E Scenario 9）
4. 恢复授权保持现有 owner/session 隔离（§41）

## W15：Human-Agent 状态收敛

1. **状态三分类边界**（§32）：
   - semantic state（进 MapSpec/workflow，持久）
   - presentation state（可见性/透明度/色板/图例位置，durable presentation 继承已有 :490-539）
   - transient interaction state（pan/zoom/hover/selection，不持久）
2. **锁下沉**（§33，堵 ✅ 验证过的缺口）：
   - `lockedLayerIds` 检查下沉到 `lifecycle_engine` 引擎层：任何来源（tool/command/mapspec mutation/batch/repair planner）的 mutation 经统一 guard 函数分区 locked/unlocked，locked 部分拒绝 + 机器可读披露（对齐前端 `failed/layer_locked` 词）
   - component 级锁：workbench doc 增 `lockedComponentIds`（version 兼容：缺席=空），同一 guard
   - repair planner / runtime_repair / quality_loop suppressed_repairs 全部改走统一 guard
   - 前端命令层保留乐观提示，但权威在后端
3. **User override 三分类**：semantic override（改 spec 语义→change 分类）/ presentation override（仅呈现→不污染科学）/ temporary UI override（不进状态）；override 记录来源（user/agent）以支持 user-wins 判定（现有 :266/:800 模式推广）

## 验收

- E2E Scenario 8：用户锁图层 → agent repair 任何路径不得修改（tool 直调 + repair planner + batch 三路径专测）
- E2E Scenario 9：中断恢复全链路绿；stale 标记正确进 recompute
- Context 块预算测试：node-local 块字节数 hard cap 生效
