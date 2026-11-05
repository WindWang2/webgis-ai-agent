# PLAN — situated-agent-workbench-vnext（情境化人机协同 GIS 制图工作台）

> 分支：`zcode/goal-situated-agent-workbench-vnext-20261105`（worktree
> `/home/kevin/projects/webgis/webgis-wt-goal-situated-workbench`）
> 基准：origin/master `40cda057a`（2026-11-06）
> 目标书：05_HUMAN_AGENT — Agent 与用户共同维护一个可信地图情境的多轮制图与交互分析

## 0. 使命（一句话）

把已有的 GIS Situation / Interaction IR / GIS Memory / MapSpec / map_operation /
turn journal 资产**贯通**成「多轮、可选择、可恢复」的人机协同闭环：用户的地图
选择成为受控 Agent 输入、选择能约束真实 GIS 分析、多轮指代有据可解、刷新/
重连/取消/换会话不泄漏旧状态、工作台对执行/等待/失败/撤销状态诚实可见。

## 1. 现有资产盘点（结论先行，详见 SOURCE_INVENTORY.md）

| 资产 | 状态 | 对本方向的含义 |
|---|---|---|
| GIS Situation（ADR-0252/0180） | implemented：11 分区契约/编译器/diff/观察环/投影/查询/一致性（含死选中、user-wins） | E1 的底座；缺口在 selection→analysis scope 的服务端验证投影与多轮指代证据面 |
| Interaction IR（ADR-0227） | implemented：13 kind + 批量端点 + outbox/bus + 前端接线（含 goal03 的 selected_ids/brush 多选上行） | E2 的底座；缺口在图表刷选/表格定位/时间窗等事件是否已有真实钩子待核 |
| map_operations（ADR-0278） | implemented：组合操作/回执环/指代解析（focused_layer/last_result/clarification）/display-ack | E2 Agent 建议动作生命周期复用；指代解析需扩到 selection/chart 面 |
| feature editing（ADR-0277） | implemented：preview/confirm/undo + journal | 敏感编辑确认门复用 |
| turn journal（ADR-0268） | implemented：durable 事件账本 + advisory resume | E3 事件链的账本面 |
| recovery（ADR-0281） | implemented：诊断/处置词表/GoalVerification | E3 恢复语义对齐 |
| workbench undo（V5/W4） | implemented：user/agent 突变命令模型 + inverse replay + 会话切换清栈 | E3 undo 的既有面（MapSpec 突变域） |
| selection-store v4 + chart-map-link | implemented：map/chart/table/legend 四源选择 + 防回环 | E2 前端底座 |
| e2e journeys（ADR-0146 + #1968） | implemented：registry/helpers/mock+real 双档 | E5 验收载体 |

**核心判断：方向不是重建，是「贯通 + 补缺 + 验收」。**

## 2. 里程碑（R 轮次）

- **R0 事实审计**（本轮）：远端同步、PR/issue 矩阵、文档/源码深读、12 组多轮
  操作的事件流断点盘点（E0）。
- **R1 基线 + golden**：锁定既有行为基线（situation/IR/undo/selection 回归全绿
  记录）；E0 的 12 组操作固化为「事件流追踪测试」（characterization tests，
  只断言现状不断言期望——期望断言在后续轮引入）。
- **R2 契约/ADR**：缺口确认后的契约设计（IR additive kind、selection→scope
  验证投影、turn 链投影、三类 diff 词表）+ ADR 分配。
- **R3 最小纵切**：E1 selection→受控分析 scope（服务端 ref/revision/存在性
  验证 + situation 投影）+ 一条「框选→这一区域有多少学校」真实链路。
- **R4 主要能力**：E2 剩余（事件缺口补齐 + agent 建议 preview/apply/ACK/undo
  展示面）、E3 turn 链投影 + 三类可解释 diff + stale 防护回归。
- **R5 异常与恢复**：E3 全景（refresh/reconnect/cancel/session switch/stale
  observation）+ E4 工作台状态/无障碍/主题。
- **R6 性能**：交互响应延迟、重复请求、不必要 GIS 重算的量化门。
- **R7 journey 验收**：≥8 组多轮 journey（E5）mock 档全绿；real 档尽环境所及。
- **R8-R9 独立 review + 清偿**：P0/P1=0，P2 有处置。
- **R10 PR**。

## 3. 硬边界（红线，全文遵守）

1. 不建第二 orchestrator/ToolRegistry/MapSpec 真相/平行 harness。
2. MapSpec 唯一 desired；actual 来自可信 observation/ACK；LLM 自述≠成功。
3. IR 词表只收有真实前端钩子的 kind（ADR-0227 纪律）。
4. 不碰 #1972/#1965 占用文件（见 PR_OVERLAP_MATRIX.md）。
5. 本地 -j2；不等远端 CI；既有门禁不削弱。
6. 所有验证如实标注 PASSED/FAILED/NOT_RUN/BLOCKED。

## 4. 每轮记账格式

`master HEAD / changed files / baseline / implementation / regression tests /
failed checks / risk / next smallest change`（记于本文件 §6 与 MILESTONES.md）。
