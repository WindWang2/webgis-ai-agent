# ADR-0183: Agent–User–Map Mutation Transaction System V1

- 状态：Proposed（本 PR）
- 日期：2026-09-14
- 线：`harness/map-mutation-transactions-v1`（方向 8）
- 基线：origin/master @ `580b33e9`

## 上下文

WebGIS 是共同编辑环境：agent 工具、用户 UI、自愈修复（V11 self-heal /
finalize_display / map_finalizer）、模板/配方、系统对账都会改同一张地图。
历史上这些写入面各自为政：

1. **无幂等键** —— 响应丢失后的客户端重试可能二次执行（remove+upsert 类
   非幂等操作尤其危险）；grep 全 mutation 路径无任何 idempotency key。
2. **无统一 precedence 阶梯** —— user-wins 语义散落四处（spec 的
   `presentation_owner` 印记、`classify_override`、W15 锁 guard、前端
   `_userPinned`/turn-focus），无单一确定性裁决规则可测可引用。
3. **pending 状态无代际**（audit ST-P3-3 半开放）—— 前端乐观 pending 是
   合并 map，无法回答"这个在途值是哪一笔操作留下的"。
4. **归因缺口** —— agent 工具经 `mapspec_store` 适配器直调引擎，绕过
   provenance / 协作 op 事件 / user-presentation 守卫环。

事务底座本身已存在且不重做（复用清单见 recon §4）：per-session 分布式锁
（fail-closed）、user 强制 CAS、单事务提交（spec+revision+指纹+layers）、
事务回滚、W15 锁 guard、`_preserve_durable_presentation`、
`enqueueUserMutation` 前端串行链。

## 决策

### D1 — MutationEnvelope：每笔突变一个可序列化身份（U1）

`app/services/gis_world_state/envelope.py`：

```python
MutationEnvelope(
  mutation_id,            # 幂等键；客户端不传则服务端铸造（uuid4 hex）
  origin,                 # agent | user | system（既有三值，不变）
  actor,                  # mapspec_route / tool:<name> / runtime_repair / …
  producer_class,         # 见 D2
  explicitness,           # explicit | derived
  client_optimistic_id,   # 前端乐观队列身份（= client_mutation_id）
  turn_id, reason, ts,
)
```

信封是元数据不是操作：操作语义仍是 MutationIntent；信封随既有
`apply_gis_mutation → engine.apply_mutation` 链路透传，不建第二套系统。
全字段 Optional/自动铸造 —— 调用方零改动即获得身份（additive）。

### D2 — 优先级阶梯落码（U2）

```
USER_PINNED > USER_EXPLICIT > AGENT_EXPLICIT > REPAIR_AUTOFILL > TEMPLATE > SYSTEM_DEFAULT
```

- `classify_producer(origin, actor, flags)`：确定性纯函数。origin=user →
  USER_EXPLICIT（目标在 workbench 锁集 → USER_PINNED，锁是既有 durable
  pin 载体）；origin=agent：修复/收口管线 actor（runtime_repair /
  map_finalizer / finalize_display）→ REPAIR_AUTOFILL，`tool:apply_template`
  / `tool:recipe_*` → TEMPLATE，其余 → AGENT_EXPLICIT；origin=system 或
  未知 origin → SYSTEM_DEFAULT（fail-low：未知来源绝不冒充高阶写入者）。
- 与任务书初始阶梯的差异：REPAIR 与 TEMPLATE 分层独立 —— 两者规则不同
  （repair 受 user-pin 守卫拒绝；template 只许 fill-undeclared），并层会
  丢失其中一条。
- `precedence.resolve_field / can_override / fill_undeclared`：字段面裁决
  （user 不被 agent 覆盖；同阶不覆盖走幂等；TEMPLATE 及以下只填空缺；
  SYSTEM_DEFAULT incumbent 视为无主默认）。执行仍由既有守卫承担，本层
  提供可测的单一裁决语义与新调用点的复用面。

### D3 — 服务端幂等：mutation_id 锁内去重（U3）

- 引擎锁内、deleted 检查后、CAS **之前**查 `_mutation_dedup`
  （map_state 键，FIFO ≤64）：命中 → 返回存证 revision + 当前权威 spec，
  不再执行、不再递增（`MapSpecResult.duplicate=True`）。
- 查重先于 CAS 是刻意的：响应丢失后的重试此刻 expected_revision 必然
  落后，幂等命中必须优先于 superseded，否则客户端被逼入假冲突。
- 存证随提交单事务落地（`save_mapspec(extra_fields=...)` 并入
  commit_mapspec_state 的单 MULTI）—— 不产生"已提交但同 id 可重放"的
  crash 窗口；回滚路径剥除存证（同 id 可重新执行）。
- 诚实边界：索引存 cache 层，Redis 状态过期后幂等失效，此时 CAS 仍是
  正确性地板（revision 已推进 → superseded）。不把索引写进 MapSpec 文件
  （不污染 spec 结构）。

### D4 — 生产接线：所有突变入口统一归因（U5/U6）

- `mapspec_store` 适配器 13 个 mutating 方法全部改经 `apply_gis_mutation`
  （此前直调引擎 = 无 provenance/无事件/无守卫环）；
- 工具 authoring 打 `actor="tool:<name>"`（apply_template 族自动归
  TEMPLATE）；repair/finalizer/finalize 的既有 actor 自动归
  REPAIR_AUTOFILL；style restore 走 origin=system；
- ws_service 遗留 socket 直写通道（ST-P3-4）：本线不迁移（迁移需客户端
  带 expected_revision = 破坏性协议变更），补 best-effort provenance 归因
  （actor=ws_legacy）+ reconciliation 观测（D5）。完整迁移列为后续接口点。

### D5 — Reconciliation：desired vs observed 的确定性对账（U7）

`reconciliation.reconcile_map_state(mapspec, runtime_layers, pending_removed)`
纯函数：对账 server MapSpec × runtime layer registry × pending 队列（前端
渲染面经 observation 通道间接可见，不入参），输出有界（≤32）确定性排序
anomaly：

```
USER_HIDDEN_BUT_VISIBLE > ZOMBIE_RUNTIME_LAYER
  > SPEC_LAYER_MISSING_RUNTIME > VISIBILITY_MISMATCH > STALE_PENDING_REMOVED
```

生产接线：`ws_collab` sync/replay 应答附带 `anomalies`（只读投影，失败
不阻断 sync）。这是 ws_service 遗留直写通道与一切 spec/runtime 漂移的
观测面。

### D6 — 前端：乐观队列的身份与观测（U4）

- 每笔用户 mutation 生成 `client_mutation_id`（uuid v4 + 非安全上下文
  退化），随 POST 上行；服务端以 `c:<id>` 去重，duplicate 响应按成功收敛
  （应用存证 revision + 权威 spec，无 toast 无回滚）。
- pending 身份记**旁路 meta 表**（`getPendingMutationMeta`：mutationId +
  模块级单调 gen）—— pending 值本体形状不变，compose/既有消费方零感知。
  关闭 ST-P3-3。
- durability 重试（visibility-transaction）同一逻辑操作共用同一 id
  （superseded 尝试不留服务端存证，committed/重放恰一次落账）。
- 队列观测：串行链深度/峰值/端到端时延（有界 32 样本环，诊断面，
  不写持久状态）。

## 替代方案（否决理由）

- **独立 transaction service / 全局锁**：与 per-session 锁+CAS 重复，
  违反 Agent Host 原则与防重复施工约束。
- **把幂等索引写进 MapSpec/spec 内嵌键**：污染 spec 结构（任务书边界
  「不重写 MapSpec」），且磁盘写放大。
- **field-level CAS（per-key revision）**：解决 false-positive 409（disjoint
  字段并发）但改动面=整个 spec 布局；本线以 envelope+前端串行链+幂等
  覆盖同一风险面，field-CAS 列为后续接口点。
- **迁移 ws_service 遗留 socket 到 CAS**：需要客户端协议升级，破坏性；
  本线以归因+对账观测覆盖（D4）。

## 影响

- **兼容性**：全部增量可选参数/字段；旧调用方零改动；`to_dict` 只在有值
  时输出新键；前端 `client_mutation_id` 缺席时服务端铸造。
- **可观测性**：provenance detail 增 producer_class/mutation_id；
  collab op/doc/delta/presentation 载荷增身份键；MutationApplyResponse 增
  duplicate/mutation_id/producer_class/client_mutation_id；sync 应答增
  anomalies。
- **回滚面**：revert 本 PR 即回到 master 行为（无 schema 迁移、无生成物
  变更、无 CI 修改）。
- **性能**：每笔提交多一个有界小 dict 字段写入（同一 MULTI，纳秒级序列
  化）；幂等查重为锁内 O(1) 内存查表；前端样本环有界 32。

## 验收证据

- `tests/cartography/test_mutation_transactions.py`：30 用例（分类表/
  裁决规则/幂等矩阵/竞态屏障/reconciliation/生产接线/review 修复回归）；
  显式声明：producer_class 目前**无运行时决策消费方** —— 阶梯是可测的
  裁决语义层 + 归因层，执行仍由既有守卫承担（D-06/A2）；
- 前端 `user-mutation.mtid` + `session-cursor.mtid`（10 用例）+ 既有
  mapspec 52 / map-commands 134 全绿；
- ledger：`docs/dev/mutation-transactions-ledger.md`。

## 后续接口点

1. ws_service 遗留 socket 通道的 CAS 化迁移（需 socket 协议 v2）；
2. field-level CAS（disjoint 字段并发免 409）；
3. reconciliation anomalies 接修复环（自动清理僵尸）与 prometheus 指标；
4. collab 事件 payload 的 mutation_id 客户端去重（跨浏览器幂等对账）。
