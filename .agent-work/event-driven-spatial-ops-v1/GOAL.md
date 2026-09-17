# GOAL — Event-Driven Spatial Operations & Mission Portfolio Control Plane

## 目标

把"用户 turn 驱动"的 Pi + GIS Harness 推进为**事件驱动、可持续运行的空间任务控制平面**：

外部/内部空间事件 → 有界、持久、可回放的 **SpatialEvent ledger** → **Situation 投影** →
**Watch/Trigger 求值** → **Mission create/revise/resume**（复用现有 CAS/lease/fencing）→
**受影响 ExecutionGraph/MapProduct 子图增量失效**。另建 Project/Mission **portfolio 只读视图**。

## 不变量（红线）

1. Pi 仍是唯一 Agent Host；不自建第二个通用 Agent loop。
2. 不再造第二套 Mission Runtime / ExecutionGraph / ArtifactRegistry / MapSpec / EvidenceGraph / Data Fabric。
3. 事件**先投影 Situation，再决定 Mission**——禁止事件直接绕过 Situation 修改 Agent 真相。
4. Big payload 只走 ref/ticket，绝不进 LLM context / 事件载荷（inline payload ≤ 2KB）。
5. 租户硬隔离：事件的 org_id 贯穿 ledger/watch/trigger/mission，A 租户事件永不触发 B 租户 mission。
6. 全链路 fail-open 于现有 turn-driven hot path：`GIS_SPATIAL_EVENT_RUNTIME=0` 时行为与 master 逐字节一致。
7. 所有事件侧副作用幂等 + durable cursor 恢复不丢不双跑。

## Oracle（可客观验证）

见 `.goal-loop-ledger.md` Oracle 节；覆盖：重复投递幂等、重启恢复、增量失效选择性、
1000+ burst 有界收敛、租户隔离、feature-off 兼容、targeted tests 全绿、review 无 P0/P1、
关键验证双跑一致、PR 创建（不 merge）。
