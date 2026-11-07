# Oracle 循环账本

每条记录遵循：输入事实 → 决定 → 代码差异 → 失败 → 修复 → 本地验证。未完成事项不得写成完成。

## Loop 0 — M0 实时审计

- 输入：实时 fetch 的 master、8 个开放 PR、50 个合并 PR、指定 PR files/reviews、50 个 Issues、远端/本地分支。
- 决定：建立指定隔离 worktree；避开 Pi/锁/Planner/UI/MapSpec 所有权；从既有 T1–T4/Recovery 资产寻找真实缺口。
- 代码差异：仅新增本规划/账本文件，尚无产品代码变化。
- 失败：无。
- 修复：不适用。
- 本地验证：branch/worktree 创建成功，HEAD `585812ee4e2d75612f2b124e6fe6587af3deca5c`。

## Loop 1 — M0/M1 资产映射

状态：进行中。下一步读取 replay/lab/result_contracts、recovery、现有测试与行内 review comments。

## Loop 2 — M2 证据投影与诊断（完成）

- 输入：ReplayTrace/TurnEvidence 只有 request/session/turn/run；chain 规范阶段序；既有 explain/ratchet 不可跨层回答最后有效阶段。
- 决定：新增 `replay/journey.py` 纯投影和 `scripts/harness_journey_diagnose.py`，只读既有事实；报告按 session/turn scope，静默拒绝跨 owner；真实 UTF-8 bytes 限制；不接生产热路径，默认零开销。
- 代码差异：additive TypedDict、8 阶段/8 完成声明、跨 session/悬空/乱序检查、typed findings、last_valid_stage/runtime/mode、恢复资格、Explain 投影；TDS 仅补已确证 tool_call/capability/turn 归因。
- 失败：模块缺席预期红；TypedDict 属性误用 1 次；跨 session/悬空/乱序/完整正向/诊断/CLI 分别先红；Mypy 初次 12 个可空收窄错误。
- 修复：逐切片最小实现；类型守卫统一；正向 fixture 的 artifact 顺序错误修正而非放宽乱序断言。
- 本地验证：journey/diagnostic/explain **10 passed**；recovery **195 passed**；Mypy 两个目标文件零问题；Ruff 与 `git diff --check` 通过。
- 未验证：该投影尚不证明真实浏览器或 live services；`correlation_id/goal_id/operation_id` 缺席仍如实显示，不伪造全链覆盖。
