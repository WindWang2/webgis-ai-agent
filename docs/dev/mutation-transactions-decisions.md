# 方向 8：Mutation Transactions — 决策日志

> 线：`harness/map-mutation-transactions-v1` · 基线 `580b33e9` · 持续更新

## D-01 落点：扩展现有门面，不新建事务系统

任务书 U1-U3 的「统一 envelope / precedence / server transaction」以**挂接**方式落在既有
`gis_world_state` 门面 + `MapSpecLifecycleEngine` 上；不新建平行的 mutation service/store。
理由：引擎已具备锁/CAS/单事务提交/回滚（recon §0），第二套系统即违反 Agent Host 原则与
防重复施工约束。

## D-02 producer 分类表（任务书阶梯 → 代码语义）

阶梯：`USER_PINNED > USER_EXPLICIT > AGENT_EXPLICIT > REPAIR_AUTOFILL > TEMPLATE > SYSTEM_DEFAULT`。

判定函数 `resolve_producer_class(origin, actor, intent, locked_targets)`（确定性、纯）：
- origin=user → USER_EXPLICIT；若 intent 写 workbench 锁集或目标在锁集 → USER_PINNED（锁是既有 durable pin 载体）；
- origin=agent 且 actor ∈ {runtime_repair, map_finalizer, finalize_display, presentation_sweep} → REPAIR_AUTOFILL；
- origin=agent 且 actor 以 `tool:` 前缀且 ∈ 模板/配方族（apply_template 等）→ TEMPLATE；
- origin=agent 其余（工具 authoring / 显式 agent 指令）→ AGENT_EXPLICIT；
- origin=system → SYSTEM_DEFAULT。
任务书的「初始思想」按上述代码事实调整：REPAIR 与 TEMPLATE 分层独立（任务书并作一层），
因模板「只填未声明」与修复「user pin guard」规则不同（U6 要求分开守卫）。

## D-03 幂等实现：mutation_id 锁内 dedup，map_state 有界索引

- 键：`_mutation_dedup`（map_state，dict，FIFO 上限 64），值 `{revision, ts, origin}`。
- 时机：engine 锁内、deleted 检查后、CAS 检查**前** —— 响应丢失后的重试（此刻 expected_revision
  已落后）命中 dedup 返回 committed 结果而非 409；这正是幂等的目的。
- 落地：经 `save_mapspec(..., extra_fields={...})` 并入 commit 单事务（Redis 单 MULTI；崩溃窗口消灭）。
- 边界（诚实披露）：索引存 cache 层，Redis 状态过期后 dedup 失效；此时 CAS 仍是正确性地板
  （revision 已推进 → superseded），仅「重启后同 id 重放」回退为 at-least-once。不把索引写进
  MapSpec 文件（不污染 spec 结构）。
- 响应：`MapSpecResult.duplicate=True` + 存证 revision + 当前权威 spec，`success=True`；
  provenance 不追加第二条（不双记）。

## D-04 引擎 API 演进：只加可选参数

`apply_mutation(..., *, mutation_id=None, actor=None, producer_class=None, reason=None)`、
`MapSpecResult` 加 `duplicate/mutation_id/producer_class` 字段（默认值保持旧行为）。
`to_dict()` 只在有值时输出新键 —— 14 个既有调用点与测试零破坏。

## D-05 ws_service 遗留 socket 直写：检测而非迁移

`ws_service.handle_layer_toggled/opacity/removed` 直写 runtime layers（无 CAS、不同步 spec）。
迁移需 socket 客户端携带 expected_revision（破坏性协议变更，超出本线边界）。本线：
reconciliation anomaly（`VISIBILITY_MISMATCH`/`ZOMBIE_RUNTIME_LAYER`）使其**可观测**，
handler 增加 provenance 记录（origin=user, actor="ws_legacy"）。完整迁移列为后续接口点。

## D-06 模板/autofill 语义：fill-undeclared

`precedence.resolve_field`：高阶类（≥TEMPLATE 之上）可覆盖任意字段；TEMPLATE 只允许写
`base` 中**未声明**（absent/None）的字段；SYSTEM_DEFAULT 同且最低。修复类（REPAIR_AUTOFILL）
受既有 user-presentation 守卫 + W15 锁 guard 约束（不改 repair 算法，只补 producer 归因）。

## D-07 前端幂等与 pending 身份

- 每笔用户 mutation 生成 `client_mutation_id`（uuid v4，`crypto.randomUUID` 不可用时退化
  随机串），随 POST body 上行；`duplicate` 响应按成功收敛处理（应用 spec、清 pending、不 toast）。
- pending presentation 条目加 `mutationId` + per-layer 单调 `gen`（关闭 ST-P3-3 半开放项）；
  clear-on-commit 语义不变（向后兼容：旧字段消费方读不到 gen 时行为不变）。
- 队列观测：`enqueueUserMutation` 记录深度峰值/等待时延/提交时延（有界 32 环，dev 日志），
  不新增持久化状态。

## D-08 与 #1277（kernel）的边界

kernel 管 harness 运行代（turn/step/checkpoint 的 SessionPlan v2）；本线管地图状态事务
（mutation envelope/dedup/precedence）。两者在「revision」上同名不同物：kernel 的 SessionPlan
revision 不参与 MapSpec CAS。本线不 import kernel、不占用其文件。

## D-09 测试驱动方式

Barrier/event 驱动（asyncio.Event + 显式 await 点），零 sleep。复用 `tests/cartography/`
既有 clean_session fixture 风格；前端 vitest 直测 user-mutation/session-cursor 模块单例。

## D-10 不修的 finding 及理由（review 后回填）

（review 阶段填充）
