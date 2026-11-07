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

## Loop 5 — M5 受控恢复实验（完成）

- 输入：既有 GoalVerification、RecoveryPolicy、scope guard、真实 TDS。
- 决定：不写新恢复引擎；以四组公开生产 seam 实验链证明 diagnosis→safe action proposal→re-observation/terminal。
- 代码差异：仅新增四组实验测试，无生产策略变化。
- 失败：测试 fixture 初次 SyntaxError，修正字面量后 4/4 通过；无生产缺陷被发现。
- 本地验证：缺观察 reobserve 后 pass；503 retry_with_backoff 后真实 dispatch 成功；跨城需新数据；持久编辑无幂等不得自动 replay。恢复域合计 **199 passed**，Ruff 通过。
- 限制：跨城实验使用 eligibility/scope 既有语义，不伪造自动修复；未运行付费模型或外部服务。

## Loop 6 — M7 证据接入与真实 Pi 模式（进行中）

- 输入：既有 ratchet/metrics store、quality manifest、Pi v1.5.0 固定 pin、浏览器车道文档。
- 决定：新指标只 record-only；不修改 #2007 生成物；单独交付 G07 质量文档与 failure taxonomy。
- 代码差异：journey_overall/render_observed/journey_findings/cross_session_violations 接入 replay metrics；补 artifact ref 持久化完整性；新增两个只读 CLI 和质量/运行文档。
- 失败：新指标三例先红；ref 无 artifact 的正向误报 pass，补独立负例后修正；两个正向 fixture 阶段顺序错误按规范修正，未放宽断言。
- 本地验证：journey 相关 **26 passed**；metrics/ratchet/schema **27 passed**；固定 Pi `pi_doctor --json` 全 PASS；显式 `PI_JOURNEY_E2E=1` 真实 Pi + stub 旅程 **1 passed, 32.17s**。
- 限制：默认 Pi 旅程先显式 skip，已记录 opt-in 原因；未运行浏览器/live services；不把 stub 当真实模型。

## Loop 7 — M6 实证性能（完成）

- 输入：C 的基线定位：UTF-8 字符误计、resume 每场景全量重写 O(N²)、deepcopy/sha256 不值得优化。
- 决定：canonical_json UTF-8 计量；resume 默认每 16 条 flush + 收尾强制 flush，interval=1 保留旧行为；不改预算阈值。
- 代码差异：`bench.py` 单生产改动、独立非 ASCII 测试、flush 次数/恢复语义测试、可复现 benchmark 脚本与原始 JSON。
- 失败：UTF-8 固定样例旧口径红（52≠61）；flush 参数不存在红；均最小实现后转绿。
- 本地验证：全量 Replay/Lab **268 passed**；focused perf **6 passed**；C 的 focused 测试另双跑通过；标准 Mypy/Ruff 通过。
- 实证：最新原始报告 resume before p50 468.510ms / after 265.073ms（约 1.77×），small/medium/large 140/140 green；canonical_json 计量未提速（0.154→0.262ms/call），deepcopy/sha256 明确不优化；T2 疑似 O(N²) 未验证（无 Redis）。

## Loop 8 — M7 文档与交付准备（完成）

- `docs/quality/GIS_HARNESS_RELIABILITY.md`、failure taxonomy、benchmark 报告和原始 JSON 已落盘；record-only 指标接入完成。
- 真实 Pi doctor 与 opt-in stub 旅程均通过，browser/live 未运行，未验证维度明确。
- 开始 M8 独立冷审，不提前宣称复审通过。
