# Task Plan — goal-gis-topology-authoring-20261125

## Goal
专业 WebGIS 拓扑约束编辑、空间要素生产与质量闭环（任务 02）。
让 Agent 在明确权限与约束下协助维护真实矢量地理数据：把既有单要素/结构编辑
（feature_edit, G14）与多源拓扑规则（topology_workbench, G31）连成面向生产的
跨要素约束编辑体验。

## Oracle（可验证完成条件）
1. 面共享边编辑后两面无重叠/缺口（基线违规须披露）；邻面随规则同步变化且原子提交。
2. 线网节点受控连接；不同层级交叉不误连；错误 CRS/歧义容差/超预算可靠阻断。
3. UI 与 Pi 走同一 plan→审批→CAS→质量复检路径；≥3 个用户旅程（面边界/线网/拒绝撤销）+ 负例证据。
4. 本域 pytest + 受影响邻域全绿；前端 lint/typecheck/test 绿；无已知新增高/中风险回归。
5. 独立冷审 R1→修复→R2 循环无未关闭 P0/P1/P2。
6. 分支已推、PR 已开：`feat(gis-edit): constraint-preserving spatial authoring and topology review`。

## WP 分解
- WP1 共享空间结构权威建模 + 编辑能力审计（拓扑索引、impact plan 契约）
- WP2 面状共享边编辑与面覆盖约束（共边顶点移动/插入/删除、邻面同步）
- WP3 线网约束与辅助数字化（端点连接、节点移动/拆分、noding、trace）
- WP4 原子提交、字段约束与回滚（跨要素事务、idempotency、undo/redo 同事务）
- WP5 用户与 Pi 同一作者链路（工具 inspect/preview/impact/propose/revalidate、UI 工作台）
- WP6 质量数据集、性能与可复现审查（对照测试、property-based、race 实测）

## 迭代纪律（goal-loop）
- 每轮：读 ledger → 评估差距 → 单一改动 → 运行验证 → 记账 → 判定
- 完成条件未满足不结束回合；完成必须附验证证据
- 最多 3 个活动 subagent；实现者不得兼任独立复核

## 状态
- [x] Phase 0 侦察（3 个只读 agent）+ baseline/ownership/避让矩阵
- [x] WP1–WP6 全部实现并本地验证
- [x] Playwright j50（mock 档 2 例通过）+ 前端组件/API 测试
- [x] R1 冷审 → P1×5/P2×2 修复（43e857477）
- [x] R2 独立复审 → N1–N5 修复（54ab9a2bc）
- [x] R3 聚焦复审 → 多 path 回归修复（7fb7ace33）+ 提案重放对齐（1e9373c80）
- [x] R4 最终聚焦复审（approve，无新 P0–P3）
- [x] 推送分支 + PR：[#2279](https://github.com/WindWang2/webgis-ai-agent/pull/2279)
