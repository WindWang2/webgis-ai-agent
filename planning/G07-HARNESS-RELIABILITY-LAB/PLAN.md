# G07-HARNESS-RELIABILITY-LAB — 执行计划

## 目标与停止条件

在既有 Pi/GIS/MapSpec/渲染/出版与 Replay/Lab/Ratchet/Recovery 资产上，补齐可审计证据投影、真实执行模式的黄金任务、故障诊断、安全恢复和实证性能基线。完成条件是 REL-O01–O13 有本地证据、独立冷审 P0/P1 清零并复审、关键验收连续两次通过、推送并创建一个真实 PR；不等待 CI，不合并。

## 固定边界

- 单一事实源：ToolRegistry/ToolDispatchService、SessionStore、MapSpec、现有 TurnEvidence/GisTraceRegistry/GoalVerification/L1–L5。
- 不重建 Pi 会话/锁、Planner/TaskGraph、数据摄入、MapSpec/渲染器、前端交互 UI、registry 或 trace store。
- 新评测默认离线、固定 seed、资源有界；新指标先 record-only，不降低既有阈值，不把 `not_evaluated` 当 pass。
- 测试公共 seam：Replay/Lab 公共数据契约、真实 ToolDispatchService、RecoveryPolicy/repair_planner 既有纯决策接口、现有 ratchet/metrics store。浏览器与 live 服务分别 opt-in，绝不混称生产验证。

## 阶段

| 阶段 | 状态 | 输出与退出证据 |
|---|---|---|
| M0 | complete | 实时 SHA、50 个合并 PR、开放 PR/Reviews/Issues、分支与冲突矩阵，写入 BASELINE |
| M1 | complete | 既有资产/模式映射与 239 项基线通过；缺口锁定为关联投影、模式披露和实证，不重建框架 |
| M2 | complete | additive 八阶段证据投影、关联/时序/跨会话检查、诊断 CLI 与 Explain 接入；10 项 focused 通过 |
| M3 | in_progress | 至少 12 组黄金任务，明确四种执行模式、成功/失败 Oracle、科学/守恒/量纲性质 |
| M4 | pending | cancel/late/disconnect/lock loss/timeout 等故障注入，跨 session 与终态一致性 |
| M5 | pending | 至少 4 组恢复对照，权限与副作用边界、真实 replay+dispatch 复验 |
| M6 | pending | 固定环境 benchmark 原始汇总；至少 2 个热点实测改善或“不改善”结论 |
| M7 | pending | 既有 ratchet/metrics store 接入、分层车道、趋势输出、完整质量与本地运行文档 |
| M8 | pending | 独立 reviewer 冷读全 diff；P0/P1 修复并二次复审，P2 明示 |
| M9 | pending | 重取 master、必要集成、双次关键验收、语义化 commits、push 与唯一 PR |

## Oracle 对账

| ID | 验证方式 | 当前状态 |
|---|---|---|
| REL-O01 | 完成声明逐级取证；缺 renderer 观察必须 `not_evaluated`/非完成 | pending |
| REL-O02 | 12 组任务 manifest + 成功/失败双 Oracle + 模式披露 | pending |
| REL-O03 | session/turn/goal/operation/correlation 关联与跨会话负例 | pending |
| REL-O04 | cancel、late、lock loss、timeout、disconnect 故障矩阵 | pending |
| REL-O05 | 诊断报告阶段/缺失证据/重试资格 + secrets/owner/bytes 负例 | pending |
| REL-O06 | offline/live dispatch/browser/live services 覆盖矩阵 | pending |
| REL-O07 | 4 组受控恢复对照及权限、动作、终态 | pending |
| REL-O08 | 压力场景的 wall/CPU/RSS/UTF-8 bytes/events/artifact bytes | pending |
| REL-O09 | 两个热点的 before/after 或负结果实验记录 | pending |
| REL-O10 | 数值、单位、地理范围、会话归属既有回归不降级 | pending |
| REL-O11 | 原 ratchet/store 保留；阈值差异审计；not_evaluated 非 pass | pending |
| REL-O12 | 关键命令双跑 + 独立 Review 证据 | pending |
| REL-O13 | 干净 worktree 可复制命令、分层车道与资源上限文档 | pending |

## 验证纪律

每个垂直切片遵循 characterization → failing public-seam test → minimal implementation → focused test → relevant regression。记录实际命令、通过/失败数、运行模式、未验证边界。浏览器、真实 Pi RPC、付费模型、公网服务不可用时明确标为未运行，不以 fixture 替代结论。
