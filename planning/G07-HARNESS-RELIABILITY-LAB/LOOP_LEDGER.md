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

## Loop 3 — M3 十二组黄金任务（完成）

- 输入：既有 72 个真实 dispatch 场景、golden benchmark、科学 oracle；跨城/选择/A4/指北针开放 Issue。
- 决定：用 `golden_tasks.json` 选择 12 能力组并引用 expectation ID；新增薄 CLI；仅对直接阻断输入可信性的 #2004 做 additive 修复。
- 代码差异：`lab/golden.py` manifest 校验、12 组引用、`harness_reliability_golden.py` 报告、selection omitted→projection→claimed/validated 全链 stale。
- 失败：manifest loader/CLI 缺席预期红；projection 丢 omitted 的集成测试先红；完整 CLI 初次 30/30 `LockDegradedError`。
- 修复：一行只读 passthrough；CLI 在 app import 前初始化离线环境；不重跑 Redis、不改锁。
- 本地验证：30/30 场景通过（报告 `reports/g07/golden.json`）；focused **255 passed**；selection 全链 **11 passed**；CLI 子进程契约 1 passed；Ruff 通过。
- 未验证：browser/live 均 not_run；#1999/#2001/#2003/#2005/#2006 保持开放，不作为本线修复成果。

## Loop 4 — M4 故障注入与终态一致性（完成）

- 输入：既有 10 类 replay faults、J7 settlement、#2009 锁修复、跨边界投影。
- 决定：不重写锁/完结门；在只读投影补 typed terminal reasons 与重复/迟到/动态卸载/渲染/导出关联负例。
- 代码差异：lock_lost/cancel/timeout、renderer_context_lost/tiles_missing、postgis_unavailable/export_failed、duplicate vs conflicting callback、dispatch_denials、late turn。
- 失败：lock loss 误报 pass；WebGL/缺 tiles/export ref 误报 pass；动态卸载和重复冲突未检出；均先红。
- 修复：终态原因先于 task_complete；render/export 独立裁决；相同重复=partial、冲突重复=fail；拒绝后不得完成。
- 本地验证：新/相关 journey **20 passed**；既有 fault/lock/dynamic/usability **89 passed**；Mypy/Ruff 通过。
- 限制：真实 Pi RPC 重连、真实 PostGIS/浏览器故障仍未运行；这些测试是录制证据与本地生产接缝的负例，不冒充线上 chaos。
