# ADR-0204: Pi Agent Runtime ↔ GIS Harness Ownership Boundary（方向 9 边界收敛）

- 状态：Accepted
- 日期：2026-09-20
- 关联：方向 9（Pi ↔ GIS Harness Boundary Convergence）；#1395 / #1407（决策链前置批次）；ADR-0022（bridge 抽取纪律）；ADR-0081 / ADR-0103 / ADR-0104 / ADR-0134（披露面既有裁决）

## Context

`agent_pi_bridge.py` 在 master（5a4d4632）达 3294 行，其中 `_dispatch_tool_bound`
后置披露段（~340 行）与 `stream_prompt` 的 agent_settled 收口段（~120 行）是无形化的
GIS 披露管线：ok/error 双分支逐行重复（含锁竞争重试 ×2）、「增值披露绝不阻断」纪律靠
10+ 处复制粘贴 try/except 维持。后果实测（见 `docs/dev/pi-harness-boundary-recon.md`）：

1. **stream / non-stream 漂移成灾**：非流式 `prompt()` 在 process_died 时把 tracker 任务
   结算成 `completed`（同函数 kernel 映射却是 `failed`）；turn-settle 的完成度终验/
   WorkflowInstance/RuntimeState 推进/上下文 checkpoint/证据链 USER_OUTPUT + 持久化
   **只在流式存在**，非流式靠 chat 路由手工补一个 finalization 调用；超时 failure_class
   单独使用私有 `drain_timeout`（流式为 `pi_stall`/`pi_turn_budget`）。
2. **hotpath flag 无单一真相**：22 个 `GIS_*` 行为 flag 散布 14+ 文件咨询，无 registry、
   无一致性测试 —— flag 矩阵漂移 CI 不可见。

## Decision

### D1 Ownership 表（本 ADR 的规范裁决）

| 关注点 | 权威拥有者 |
|---|---|
| 通用 agent loop / turn 生命周期 / RPC 编排 | Pi vendor + `PiBridge`（不建第二套 loop） |
| 调度正确性（dedup / ref / 自愈 / 广播） | `ToolDispatchService`（不搬进 Harness） |
| 调度资格（surface / tier / capability bind / input gate） | `dispatch_tool` 前置闸（#1395 契约不变） |
| **dispatch 后置披露（证据→计划→投影→终验→链）** | `app/services/chat/pi_post_dispatch.py`（typed pipeline） |
| **turn 结算披露（stream agent_settled ≡ non-stream 清洁收口）** | 同模块 `settle_turn_projections`（单实现双路径） |
| MapSpec desired state | MapSpec store（不新增第二份地图真相） |
| hotpath flag 盘点真相 | `hotpath_convergence/flag_registry.REGISTRY` |

### D2 typed 披露契约

`DispatchDisclosure`（frozen 输入，含 verifiedTurnId/activeTurnId/late_for_plan）→
`DisclosureOutcome`（plan_sse / finalization_payload / stale_generation 标记）。
pipeline 零依赖 bridge 类型；bridge 保持 ADR-0022 rendezvous（cache + PiToolResponse
翻译 + 测试 patch 面：cartography 访问器调用时经 bridge 命名空间惰性解析）。

### D3 stream / non-stream parity 修复（行为变更，其余逐字节保持）

| 项 | 修复 |
|---|---|
| D1 process_died tracker 结算 | 非流式 `complete_task` → `fail_task`（与流式及同函数 kernel 映射对齐） |
| D2 turn-settle 投影/终验 | 非流式清洁收口调用共享 `settle_turn_projections`（幂等门兜底；chat 路由既有补丁保留，无害） |
| D3 证据链 USER_OUTPUT + persist | 同上，双路径都入链 |
| D4 checkpoint_context_layers | 同上（受 `GIS_RUNTIME_STATE_MACHINE` kill switch） |
| D5 mark_first_event | 非流式 drain 补 TTFT 代理标记 |
| D6 超时 failure_class | `drain_timeout` → `pi_stall` / `pi_turn_budget`（同 taxonomy；raise 文案不变） |
| D7 maybe_record_turn | 非流式携带 map_product |

### D4 显式保留的既有不对称（follow-up，不在本批更改语义）

- error 分支不推进 `runtime_state_machine`、不做完成度终验（ok 分支做）——收敛后该不对称
  在 `_advance_runtime_projections` 单点可见，是否让 error 也推进交给独立批次评审。
- 证据链 latency 从「trace 时刻重测」改为「dispatch 实测耗时」（更准确，量级不变）。

### D5 flag registry（盘点收口，不改默认值）

26 个热路径 flag 全量登记（stable / opt_in / mode 三类）；`GIS_MISSION_HOTPATH` 保持
opt-in（创建持久 Mission 是重副作用入口）。双向一致性测试：热路径源码字面量
（`gis_harness/`、`chat/`、`session_plan.py`、`tool_dispatch_service.py`、
`agent_pi_bridge.py`）⊆ registry（防增殖），registry ⊆ registry 模块之外的真实咨询点
（防死条目）。**不移除任何 kill switch**——此为保守收敛：先可见、后裁决，移除稳定
flag 留给 flag 独立退役批次。`workflow_runtime/`、`session_data.py` 等资源调参旋钮
（`GIS_WORKFLOW_DISPATCH`、`GIS_REF_SPILL*` 等）非 turn 行为 flag，明确不在盘点范围。

## Consequences

- bridge 3294 → 2956 行；`_dispatch_tool_bound` 的 GIS 披露职责清零（仅剩前置闸、调度、
  tracker、响应组装）。
- 「增值披露绝不阻断」从复制粘贴纪律变为 pipeline 单点实现；新增披露点只改一处。
- 风险与对策：pipeline 与 bridge 的调用时惰性 import 保持与原内联代码相同的 import 图；
  stale 短路/迟到回调守卫/#1407 语义逐字保留并由 13 个新 pipeline 单测钉住。
- 平行分支热区：`agent_pi_bridge.py` 的 dispatch 前置闸与 `hotpath_convergence/`（#1395
  批次）未动；本 PR 全部改动在 dispatch 后置段与 turn 收尾段，冲突面可控。

## Verification

- 新增：`tests/unit/test_pi_post_dispatch_pipeline.py`（13）、
  `tests/unit/test_pi_stream_nonstream_parity.py`（7）、
  `tests/unit/test_hotpath_flag_registry.py`（4）。
- 回归：bridge lock / dispatch adapters / concurrency / leak / respawn / session-plan
  host / execution stability / harness interaction / cartography injection /
  observability / streaming lifecycle / kernel e2e / pi e2e / v5 acceptance / chaos
  （pi + resume）/ multipod / turn-id / task api / input gate / audit-1395 —— 全绿。
- 已知既有失败（与本 PR 无关，master 基线复现）：
  `test_pi_integration.py::test_stream_prompt_emits_heartbeats_during_silence`
  （本地事件循环时序敏感：0.07s 静默期内 heartbeat 竞态）。

## Independent Review（Subagent B，FIX-THEN-SHIP → 已闭合）

- P1 死条目检查空转（grep 命中 registry 自身/.pyc）→ 改为纯 Python 扫描、
  排除 registry/flags 模块，死条目可被 CI 捕捉。
- P1 ok 分支终验/投影顺序被调换（基线：finalize 先落 `chapter["map_product"]`，
  投影随后读取）→ 恢复基线顺序 + 序贯事件日志钉死测试（ok/error 两张顺序表）。
- P2 已吸收：flag 计数勘误（26）、扫描范围补 `session_plan.py` /
  `tool_dispatch_service.py`（新增登记 `GIS_ANALYSIS_REUSE`）、bridge 遗留死常量
  `_RECORD_ARGS_BOUND` 删除。
- P2 记录在案不再改：证据链 latency 改用 dispatch 实测耗时（ADR D4）；锁重试
  不再重解析 active turn（pipeline docstring，#1407 语义更正确）；非流式清洁
  收口响应阻塞于 settle 管线（与流式同义，幂等有界）。
