# ADR-0286: Pi Turn 相位状态机、事件幂等与运行时三态披露

- **状态**：Accepted（G01-PI-NATIVE-RUNTIME / PI-01、PI-04、PI-06）
- **日期**：2026-11-07
- **关联**：ADR-0282（Pi v1.5.0 vendor/Host）、ADR-0223（settle coordinator）、ADR-0259-f03（单结算 seam）
- **Supersedes**：无（扩展既有 seam，不推翻）

## 背景

PR #1973/#1989/#1993/#1998 之后，Pi 路径的归属守卫、取消链、迟到写拒绝已生产级，
但三处"已有但碎片化"（M0 审计 G1–G6，证据见 `planning/G01-PI-NATIVE-RUNTIME/BASELINE.md`）：

1. **G3 turn 状态机碎片化**：`agent_pi_bridge.py` 的 `prompt()`/`stream_prompt()` 各自维护
   6 个布尔旗标 + 2 个字符串（`cancelled/timed_out/send_failed/process_died/unclassified_error/
   timeout_reason/_projections_settled`），两条路径各复制一份初始化（历史上两处
   UnboundLocalError）。`tool_wait`/`settling` 中间相位不可表达不可观测。任务词表
   （created/starting/running/tool_wait/cancelling/settling/settled/failed）在代码中零命中。
2. **G1 SSE 事件幂等缺失**：Pi 重复回传同一 `tool_execution_end` 时，dispatch cache
   单次消费（读后即清）使第二份走 cache-miss 降级路径，仍向客户端再发一条 `step_result`；
   无 toolCallId 级去重，无测试。
3. **G6 运行时三态披露不统一**：native/fallback/unavailable 只有日志与 `/health` 的
   runtime 名；per-request 静默降级（`chat.py` 的 `_use_pi_bridge` 判定失败）客户端不可见。

## 决策

### D1 TurnPhase 显式状态机（PI-01）

新模块 `app/services/chat/pi_turn_state.py`：

```
CREATED → STARTING → RUNNING ⇄ TOOL_WAIT
             │          │        │
             │          ↓        ↓
             └────→ CANCELLING → SETTLING → SETTLED | FAILED（终态）
```

- **观测与防御面，不改控制流**：现有旗标与单结算 seam 的权威语义不变；相位机是
  叠加的显式表达。非法转移被拒绝并计数（`illegal_transitions`），绝不抛异常破坏 turn。
- **终态映射**：`SETTLED` 仅对应 kernel `completed`/`refused`；`cancelled/aborted/
  interrupted/failed` 及一切未知词表 → `FAILED`。细粒度终态（取消 vs 失败）仍由
  `TurnEvidence.OutcomeRecorder` first-wins 记录——**不建第二事实源**，两记录器互补。
- **settlement exactly-once 门闩**：`TurnPhaseTracker.begin_settlement()` 首次 True、
  之后一律 False；`_settle_turn_outcome` 借它在 bridge 边界直接拦截第二次结算
  （coordinator 幂等仍是兜底权威）。
- **观测出口**：`TurnEvidence.attach_phase()`（host-neutral，任何带 `to_dict()` 的宿主
  相位机可挂载）→ `to_summary()["turn_phase"]`（相位、转移轨迹有界、非法计数、
  settlement 尝试次数）。
- **未知 kernel status fail-closed**：seam 内 tracker 结算的 else 分支从"未知→complete"
  收紧为"仅 completed/refused→complete，未知→fail"（封闭词表防御）。

### D2 SSE 事件幂等去重（PI-04）

在 bridge 事件循环（单写者）加 **turn 内 toolCallId 终结事件去重**：

- 每个 `tool_execution_end` 按 `toolCallId` 首见放行、重复丢弃并计数
  （`pi_surface_metrics` 新计数器 + TurnEvidence warning），不再向客户端发第二条
  `step_result`；`agent_settled` 重复同样只结算一次（相位机门闩已保）。
- 纯函数 mapper 不动（保持零状态）；去重属宿主编排职责。
- 重放/重连契约不变：`Last-Event-ID` ring 重放依旧由路由层负责。

### D3 运行时三态披露（PI-06）

`pi_compat.PiVendorIdentity` 增加运行时模式判定 `runtime_mode()`：
`native`（真 Pi RPC 活跃）/ `fallback`（ChatEngine 承载，带原因码）/
`unavailable`（Pi 不可用且 ChatEngine 亦降级）。披露出口：

- `/health` 的 `agent_runtime` 段附 `mode` + `reason`（有界安全摘要，无内部路径/密钥）；
- `task_start` SSE 负载附 additive `runtime_mode`（客户端可见的诚实降级）；
- `pi_doctor` 输出同词表（CLI 与运行时共用一个定义点）。

## 备选方案与不采纳理由

- **把旗标重写为相位机驱动的控制流**：3,246 行核心文件 + 475 项存量测试的风险收益比
  恶劣；旗标驱动控制流保持，相位机做显式投影与防御。
- **mapper 内做去重**：破坏 mapper 纯函数契约（零状态），且去重需要 turn 级生命周期。
- **新建状态持久化表**：违反"唯一事实源"（kernel turn_journal 已是权威台账）。

## 后果

- 正向：PI-01 词表落地可测；exactly-once 在 bridge 边界直接可测（G4）；SSE 重复事件
  有界抑制（G1）；降级诚实可见（G6）。
- 成本：两条路径各 ~10 行钩子；每 turn 一个小对象（有界转移轨迹 ≤32 条）。
- 风险与缓解：非法转移计数若在生产攀升，说明相位机与真实控制流有偏差——计数本身
  就是回归信号（fail-open 观测，绝不阻断）。

## 验证（Oracle 映射）

见 `planning/G01-PI-NATIVE-RUNTIME/TEST_MATRIX.md` W1/W2/W3 节。
