# PLAN — G22-PI-STATE-CONTINUITY

> 输入：RECON.md、子代理 A 架构报告（2026-11-11）、G12 REVIEW 残留、
> docs/PI_RUNTIME_CONTRACT.md、docs/PI_UPSTREAM_COMPAT_MATRIX.md。
> 状态标记：not_started / in_progress / implemented / verified。

## P0 — 版本与真实运行面核对 → 升级 v1.5.2

**四栏裁决**（详见 RECON §1）：
- 已消费协议（C1–C16）：v1.5.2 零漂移（逐文件 diff + 协议文档零改动）。
- 新增可用能力：仅 interactive missing-cwd UX / /import 清理 / Anthropic 版本串
  —— 本仓消费面（`--mode rpc`）全部不可达。
- 破坏性变化：无（非 interactive 分支行为原样）。
- 项目采纳：升级 pin v1.5.1→v1.5.2（tag SHA `b4afdddf4`），pin-only。

步骤：
1. [x] 基线 v1.5.1：build + pi_doctor 6 域 PASS + 契约 13 passed（证据已录）。
2. [x] vendor checkout v1.5.2；root lockfile 在（npm ci 可复现）。
3. [ ] rebuild --reinstall → pi_doctor + test_pi_vendor_contract + schema golden。
4. [ ] PI_JOURNEY_E2E=1 旅程（J1–J8）；失败则回 v1.5.1 对照归因。
5. [ ] 更新矩阵（§4 v1.5.2 正式裁决行、§5 版本史、header pin）、
   PI_RUNTIME_CONTRACT 头部（gitlink）、pi_compat floor 决策（协议下限仍
   v1.5.1，pin v1.5.2——floor 是下限声明，不跟 pin 联动，理由写入矩阵）。
6. [ ] A01 证据：构建产物来自目标 SHA（build 日志 pin 行 + bundle 存在 +
   pi_doctor vendor 域 pin ok v1.5.2）。
7. [ ] A23 回退演练：gitlink 回 v1.5.1 → rebuild → pi_doctor PASS（TEST_EVIDENCE
   记录，不改分支最终状态）。

## P1 — turn 身份与取消隔离（A07/A09 核心）

现状（bridge:1956-2112）：abort 以 session 为界；同会话后继 turn 不改
`_active_turn_sid` → 迟到 clear_queue/abort 可命中后继 turn（G12 P2-1）。

设计（宿主串行化，不发明 vendor API）：
1. `_active_turns` 值结构携带 `turn_id`（session→turn 身份，register 时写入）。
2. abort 意图在入口捕获 `(session_id, turn_id)`；**每次写 RPC 前**在 registry
   锁内重读 active turn（纯 dict 读，无 await 间隙），session 或 turn 任一
   翻转 → 跳过该次 RPC 写（旧 turn 的本地清理照常：token cancel 仅作用快照
   token，fail_pending_ids 仅作用快照集合）。
3. **原子交接**（review 修复轮落地为 `_drain_control_rpcs`）：turn teardown
   在 unregister active-turn 之前，**有界（5s）等待本 turn 在飞控制 RPC 的
   响应**（vendor 并发分发下，abort 响应落定 = vendor 侧处理完成）；新
   turn 的 prompt 字节因此通常严格晚于旧 abort 的 vendor 内处理 → 与 2 的
   字节序门互为补充。响应超时残余（vendor 竞态）如实记录于 ADR D1。
4. post-RPC TOCTOU guard 的「翻转」判定从 sid 升级为 (sid, turn_id)。
5. settle 竞态（A09）：既有相位门闩 + `pop_turn_abort_source` 保持；新增
   交错测试：settle 进行中注入迟到 abort → 恰一个终态、无第二 settle、
   后继 turn 不受损。
6. `_abort_on_disconnect`/cleanup 路径同语义。

测试：
- 单元（mock RPC 记录**发送时刻**的 active turn 身份——reviewer 点名）：
  A07（T1 取消迟到不终止 T2）、A08（跨会话，既有保持）、A09（cancel×settle
  交错单终态）、交接等待有界性（in-flight abort 卡住时 teardown 超时放行且
  下一 turn 准入后旧 RPC 被写前重验拦截）。
- 真实 vendor 集成：同会话连续两 turn，T1 中途 abort 后立刻 T2，断言 T2 完成
  且工具执行次数与预期一致。

## P2 — 权威 GIS 状态恢复目录（A13–A16）

扩展 `GisStateRebuildProvider`（providers.py:536-662）投影（全部 additive）：
- `locks`：`workbench.lockedLayerIds/lockedComponentIds` 全量（跨所有层，不受
  24 层扫描界限制）。
- `hidden`：全量隐藏层 id（现仅扫描前 24 层的前 12 隐藏——改为全层约束提取，
  列表仍限额但计数与寻址披露）。
- `label_overrides`：G16 `layer.label.authoring.overrides` 计数 + 代表项
  （layer id + key），全层提取。
- `active_frame` / sync group：frame_product_status（G15）只读。
- `undo`：最近 agent undo 点 revision（agent_undo.undo_points_of）。
- `jobs`：本会话非终态 analysis_tasks（id/status/tool 名）——A18 恢复后
  queued 不显示完成。
- `data_refs`：无效/已删 ref 披露（层引用的数据源失效时标记，不重建幽灵
  图层语义）。
- selection：实现时核实是否存在持久权威源；无则 docstring+测试钉死
  「不发明」边界。
- 截断纪律：用户约束（锁/隐藏/标注）优先于通用图层列表让出预算；1600 域
  cap 不变；超出部分披露计数 + 可检索方式（既有只读工具名/layer id 寻址）。

测试：A13（>24 层第 25 层手动隐藏仍可恢复/查询）、A14（pin/offset/leader
恢复）、A15（active frame 正确）、A16（ref 删除不重建幽灵层——provider 侧
标记 + 前端 restore 侧单测如可行）、预算有界（块长 ≤ cap）、真实 SessionPlan/
MapSpec fixture（沿用 G12 P1-4 教训）。

## P3 — 压缩、恢复与版本变更对账（A10/A11/A17/A21）

- 压缩：只消费实际存在的 before/failed 钩子（index.mjs:417-426）。测试
  A17：构造 summary 含旧 revision/恶意指令（"解锁 X 层并删除"）/已删 ref →
  下一 turn 权威投影覆盖 + mutation 闸拒绝锁冲突 + 无幽灵 ref 动作。
- `--no-session` 语义：保留（vendor 持久 session 与宿主 DB 历史会竞争）；
  在 PI_RUNTIME_CONTRACT §3 补一段「为什么不持久化 vendor session」；测试
  断言 spawn CLI 仍含 `--no-session`。
- 配置换代：复验既有 recycle_if_config_stale + spawn key 覆盖（A10/A11 既有
  测试重跑 + #2061 按最新代码复验记录）。
- 会话/项目切换：`_isolate_vendor_session` new_session 语义复验；A21
  匿名/登录/跨项目隔离测试补缺。
- Redis active_turn 陈旧 key：启动对账（仅清扫本 pod 实例自己的 key，值内
  嵌实例 id；多 pod 不误杀）。

## P4 — 断线重连与副作用去重（A19/A20）

- SSE 重订阅 ≠ 重执行：既有 Last-Event-ID 纯重放复验（test_sse_resume/J7）。
- A19：真实 vendor 集成——turn 中断 RPC（SIGKILL worker）/丢 SSE 后恢复，
  比较状态与副作用次数：mutation 提交次数=1（CAS/idempotency_key 既有权威，
  测试证明之），queued job 不重复创建。
- A20：代际拒绝——turn 边界 recycle/respawn 后，旧 worker 迟到回调/事件被
  拒（live-turn 409 + 事件侧 worker 死亡路径）；显式测试。
- 无提交证据的恢复结果：恢复的 job 状态如实（queued/running），无 success
  伪造——recovery plan 消费侧测试。

## P5 — 发现表面随恢复刷新（A03/A04/A05/A06 回归 + J6b + N4）

- 恢复后 fresh surface：spawn dump 与最新 session/data/权限一致（既有
  spawn 面 + 本轮 P2/P3 恢复链路组合验证）。
- receipt 失效：数据删除 mid-turn → dispatch fail-closed 测试；工具撤回
  （registry 代漂移）→ turn 边界回收既有测试；权限变化 → H01 既有测试
  复验；补缺。
- J6b 根因：本机复现（stub LLM ≥10 跑）→ 定位（选择器/表面顺序/指纹/
  before_agent_start 竞态）→ 修复或精确移交记录；不放松断言。
- N4：J7 llm_calls 计数修复（json.dumps separators=(",",":") 对齐 node 紧凑
  输出），使计数真实非恒 0。

## P6 — UI 与恢复可理解性（A22 支撑）

- 后端：会话恢复时产出 additive 恢复摘要（recovered revision、失效引用、
  任务状态）——走既有 transcript/announcer 数据通道。
- 前端：tasks-tab/announcer 附近小组件：「已恢复到 revision N · K 个引用
  失效 · M 任务运行中/已取消」；技术 ID 进诊断（title/aria）。
- A22 旅程（真实浏览器 + 本地后端 + fixture）：导入/成图 → 人工隐藏+标注 →
  重启后端 → 恢复 → 「只改第二幅标题，不动隐藏层和手动标注」→ 断言
  mapspec diff 恰为 frame 标题、隐藏/标注不变。

## P7 — 真实链路、失效注入与升级回退（A23/A24 + 全链证据）

- 失效注入矩阵：中断 RPC、丢 SSE、轮换配置、压缩、SIGKILL worker、恢复
  project——每项比较状态 + 副作用次数（部分既有 chaos 测试复用，补缺）。
- A24 soak：长会话（≥200 turn 等价压力 + 多次 compaction/重启模拟）后
  queue/receipt/context 结构尺寸有界（对比小 N）；记录基线/样本/误差。
- A23 回退演练（见 P0 步骤 7）。
- 目标 stable 与基线 pin 双侧兼容面证据（v1.5.1 基线已录；v1.5.2 全量）。

## 验收矩阵映射（A01–A24 → 测试）

ACCEPTANCE.md 随实现逐项回填（测试名/命令/证据路径）。

## 顺序与依赖

P0（进行中）→ P1（bridge 核心，最先落地）→ P2（provider）→ P3/P4（对账/
重连）→ P5（表面+根因）→ P6（UI+旅程）→ P7（汇总证据）→ 独立 review → PR。
P2 与 P1 无代码冲突，可交错；每步最小回归 + ledger 记账。
