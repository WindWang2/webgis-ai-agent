# F03 — Harness Lifecycle Convergence：Recon 与架构决策

基线：`origin/master = 9e1ad22907e99cd7b4721153294448ce6496c717`（2026-09-24，PR #1494 后）。
分支：`zcode/f03-harness-lifecycle-convergence-20260926-9e1ad229`。
Worktree：`/home/kevin/project/wt-webgis-f03-harness-lifecycle-convergence-20260926-9e1ad229`。

## 0. 基线状态（执行时实测，非 seed 快照）

- `git fetch origin --prune` 后 `origin/master = 9e1ad229`，与 seed snapshot SHA 一致；
  本地 `master`（d5315716）落后，不在其上开发。
- 最近功能性大波次 #1479–#1488 全部在基线内（含 #1485 canonical turn lifecycle、
  #1481 Pi↔Harness boundary）。
- Open PR（9）：#1489（dependabot docker）、#1497–#1504（F08–F15 并行方向，同基线）。
  与本方向的热区重叠评估见 §4。

## 1. #1485 / #1481 交付后的真实缺口（本方向的 Still Missing）

#1485（canonical TurnPhase + versioned event journal + ownership matrix）与
#1481（post-dispatch parity）的 Out of Scope 经逐项核实**全部仍然成立**：

| #1485/#1481 声明的未完成面 | 核实结果（file:line，基线 9e1ad229） |
|---|---|
| `agent_settled` 多触发结算未收敛（ADR-0180 K9） | 结算序列在 stream/non-stream 两个 finally 块重复（agent_pi_bridge.py :2041-2085 / :2629-2645）；error/cancel/timeout/process_died 路径不跑 `settle_turn_projections`（仅 clean agent_settled 两处调用 :1993-2005 / :2392-2397） |
| `refused`/`aborted` 无生产发射者 | 词表就绪（models.py :47-50 TERMINAL、:130 表边、:138 STATUS_TO_TRIGGER）但 `_hk_turn_status`（bridge :1198-1216）恒不产生二者；全仓无任何 `end_turn(status="refused"/"aborted")` 调用 |
| `map_mutated` 发射缝缺失 | 仅 RESERVED_EVENT_KINDS 占位（models.py :334-336）；`apply_gis_mutation` post-success 块有现成模式（mutation.py :597-612 notify_map_mutation）但无 kernel 事件 |
| StageState/GoalNodeStatus/RuntimePhase 投影收敛 | `phase_adapter.project_runtime_phase` 零生产消费者（仅 tests）；error 分支不推进 runtime_state_machine（pi_post_dispatch.py :170-206 显式保留的不对称） |
| error 分支无终验/检查点/证据链 | `settle_turn_projections` 只在 clean 路径调用；超时/死亡/取消 turn 留下未收口的 V7 投影、无 checkpoint、无链持久化 |

### 勘察新发现的 P1 级缺陷（本 PR 修复，标注 pre-existing）

1. **未分类异常 → kernel `completed` 假阳性**：non-stream generic `except Exception`
   （bridge :2010-2012）不置任何 settle flag → `_hk_turn_status` 返回 `completed`，
   而 `rt_ev` 已记 FAILED；stream 路径根本没有 generic except（:2281 try 直接进
   finally），任何异常同样结算成 `completed`。错误伪装成完成 —— 直接违反 DoD。
2. **用户/系统 abort 后 turn 结算成 `completed`**：`PiBridge.abort()`（:1540-1649）
   发 abort RPC + 点燃 turn token，但结算映射不读 token；vendor 按 abort 正常发
   `agent_settled` → 双路径 flags 全 False → kernel `completed` + tracker
   `complete_task`（tracker.cancel 的另一路真相与之矛盾）。session_cancellation
   与 no-progress watchdog（:1070-1090）是真实生产触发面。

## 2. 并行状态词表盘点（Ownership 视角，摘要）

| 词表 | 定义处 | 写者/驱动 | 与 canonical 的关系 |
|---|---|---|---|
| `TurnPhase`/`TurnStatus`（canonical） | harness_kernel/models.py :47-73 | GISSessionRuntime（kernel 唯一终态写者 end_turn） | **权威** |
| V7 `RuntimePhase` | gis_harness/runtime_state_machine.py :67-88 | 派生（derive_runtime_state :322-379；唯一命令式写点 commit_runtime_context :809） | 任务级投影；canonical→V7 映射已备（phase_adapter） |
| `StageState` | gis_harness/workflow_instance.py :84-93 | 派生（derive_workflow_instance :478；唯一写者 maybe_update_workflow_instance :872） | 投影 |
| `GoalNodeStatus` | gis_harness/goal_graph.py :63-71 | 图构造派生（无 turn 级写者） | 投影 |
| `RequirementState`/`HarnessSignal` | goal_satisfaction/contracts.py :38-63 | evaluator 纯函数 | 投影/信号 |
| Workflow `NodeState`/`InstanceStatus` | workflow_runtime/contracts.py :16-45 | driver/service/recovery/store（fencing :342/:623） | 独立执行域权威（不收敛，只对齐事件） |
| tracker task status | task_tracker.py :32-37 | bridge finally + execution_engine + session_cancellation | Pi host 结算投影（由单 seam 驱动） |
| Mission state | mission_runtime/contracts.py :26-43 | service（lease-fenced transition） | 独立任务域权威（不触碰） |
| Governor | 无 turn 态枚举 | admit/reject（dispatch_adapter :235-246）；cancel_session 无生产调用方 | 不触碰（F08 热区） |

**禁止双写规则**（写入 ADR）：turn 终态只能由 `GISSessionRuntime.end_turn` 写；
bridge/tracker/V7/StageState 全是投影；投影失真用 parity 观测暴露，不开第二写口。

## 3. 架构决策（F03）

- **D1 单结算 seam**：bridge 新增 `_settle_turn_outcome`，stream/non-stream 的
  finally 都只调它；`_hk_turn_status` 扩展 `error`/`abort_source` 维度；
  tracker 结算收敛进 seam。clean 路径的 in-stream/in-try 投影调用保留（SSE 事件
  序不变），seam 经 `projections_settled` 幂等去重。
- **D2 outcome-aware settle pipeline**：`TurnSettleOutcome`（frozen）+ `settle_turn_projections`
  的 reduced 分支——非 clean 结算跳过完成度终验与 map_product 披露（错误不伪装成
  completed），但补齐 WorkflowInstance/RuntimeState(turn_settled)/checkpoint/链
  USER_OUTPUT+persist（错误有可回放证据）。签名向后兼容（outcome=None ≡ 旧行为）。
- **D3 aborted 发射点**：`PiBridge.abort(session_id, *, source)` 记录来源
  （user/system/policy）到有界 turn 级台账；结算映射 abort_source 优先于失败族：
  user→cancelled、system/policy→aborted。真实触发面：session_cancellation（user）、
  session 删除路由（system）、no-progress watchdog（policy）。
- **D4 refused 发射点**：ADR-0208 语义「执行前结束、无所失物」的真实生产事实 =
  clean settle + 本 turn 零执行活动（零 tool_calls 且零步被本 turn 触碰）+
  chapter.intent.clarification 存在未解决问题（与 evaluator REQUEST_CLARIFICATION
  谓词同源，evaluator.py :495-499）。kernel 纯读 `turn_refusal_candidate`，
  单 seam 在 end_turn 前降级 completed→refused。
- **D5 map_mutated seam**：`apply_gis_mutation`/`_batch` post-success 块一次性发射
  （镜像 notify_map_mutation 纪律：never-raise、revision 关联）；kernel
  `record_map_mutation` 幂等（causal_id=mutation_id）；归因只信 `envelope.turn_id`
  （不猜活跃 turn —— 迟到回调不得污染 successor），原 turn 已终态时仍记原 turn
  名下（detail.late=true），绝不重开/推进 phase。
- **D6 投影 adapters + parity 观测**：phase_adapter 增 StageState/GoalNodeStatus
  渲染与 terminal parity 判定；settle 管线输出一条有界 parity 行（canonical vs V7
  实测），失真仅计数+日志（投影修复是后续方向，不在本 PR 强改 V7）。
- **D7 重放不变量**：终态冻结、seq 单调、重复回调/回调重投幂等、supersede 迁移
  event_seq、restart 中断相位终态化 —— 全部以 seeded generative（仓规不用
  hypothesis）+ 图 oracle + 多会话 chaos 测试钉住。

## 4. Overlap / Already Done / Still Missing / Must Not Touch / Integration Seams

- **Overlap（open PR 热区，绕开）**：F08 governor/workflow dispatch；F09 trace/replay
  （app/lib/harness/replay、gis_trace 只读不改动 emit 面）；F10/F11 cartography/
  template；F12 plan compiler（gis_harness/planner*）；F13 mapspec render runtime
  （mapspec_layer_pipeline/compile_coordinator/to_svg + gis_world_state render 路径）；
  F14 export；F15 visual observation（chat.py observation 路由）。
- **Already Done**：#1485 kernel 生命周期骨架、#1481 clean 路径 parity、#1471 fencing、
  #1407 late-callback 守卫 —— 全部复用不重做。
- **Still Missing**：§1 表格五项 + §1 两个 P1。
- **Must Not Touch**：Pi vendor loop 所有权；ToolDispatchService；Mission store；
  Governor admission；MapSpec 引擎事务面（只在 post-success 通知块加缝）。
- **Integration Seams**：pi_post_dispatch（settle 管线，方向 09 文件，F08–F15 无触碰）；
  bridge finally（自有结算序列）；mutation.py post-success 块（E1 通知块同位）；
  harness_kernel（本方向主域）。

## 5. master 已知失败基线

本分支验证跑：(1) 新套件；(2) harness_kernel/pi_post_dispatch/parity/kernel e2e 邻域；
(3) gis_harness 单测目录抽查。#1485 报告的 master 既有失败（workflow_guards manifest
漂移、capability_graph_v8 文件序 flake、multiturn 跨文件状态 flake）不阻塞本方向；
任何新失败先 stash 对照归因。
