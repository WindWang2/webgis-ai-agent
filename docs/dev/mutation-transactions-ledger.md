# 方向 8：Mutation Transactions — 交付台账

> 线：`harness/map-mutation-transactions-v1` · 基线 `580b33e9` · 状态：☑ 全部里程碑完成

## M1 — 契约：envelope + precedence（U0/U1/U2） 状态：☑

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| U0 mutation inventory | `docs/dev/mutation-transactions-recon.md` §1 调用链 + §3 审计复核 | — | 9 入口枚举；历史审计 (a)-(g) 逐项复核（(a)-(e) 已修不重做，(g) 本线关闭，ws_service ST-P3-4 观测化） |
| U1 envelope | `app/services/gis_world_state/envelope.py`（新，174 行） | `tests/cartography/test_mutation_transactions.py::TestProducerClassification` | 6 类名册/未知 origin fail-low/未知名册降级/可序列化投影 |
| U2 precedence | `app/services/gis_world_state/precedence.py`（新，130 行） | 同上 `test_field_resolution_rules`/`test_can_override_and_fill_undeclared`/`test_ladder_total_order` | 阶梯全序；user 不可被 agent 覆盖；同阶不覆盖；TEMPLATE 只填空缺 |

## M2 — 服务端幂等（U3） 状态：☑

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| engine 锁内 dedup | `lifecycle_engine.py`（`_dedup_*` helpers + apply_mutation/apply_presentation_batch 加 mutation_id 参） | `TestMutationIdempotency` 5 用例 | 同 id 重放不推进；**幂等先于 CAS**（lost-response 重试返回 duplicate 而非 superseded）；屏障并发 4 同 id 恰 1 执行；失败不留存证可重执行；FIFO ≤64 |
| commit 单事务落地 | `store.py` save_mapspec `extra_fields` 参（并入单 MULTI；三处 no-op 短路加 `not _has_extra`） | 同上 | 存证与 spec 同代原子落地 |
| 路由/契约 | `schemas/mapspec_mutation_schema.py`（ClientMutationIdMixin ×14 + Response 4 字段）；`api/routes/mapspec_mutations.py`（`c:<id>` 命名空间 + 回声） | schema 冒烟 + API 套件 | additive；缺席 = 旧行为 |

## M3 — 门面/调用点接线（U5/U6） 状态：☑

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 适配器经门面 | `mapspec_store.py`（`_apply` helper ×13 调用点；方法加 origin/actor/mutation_id kwargs） | `test_adapter_route_produces_provenance`；既有 `test_mapspec_store`/`test_cartography_closed_loop` 等 103+ 用例 | 工具 authoring 路径出现 provenance 条目（此前完全绕过） |
| 工具归因 | `tool_dispatch_service.py`（2 处 `actor=f"tool:{tool_name}"`） | `test_issue_735_auto_mount_mapspec`（双打断言 actor 到达） | apply_template 族 → TEMPLATE 分类 |
| repair/restore 归因 | `runtime_repair.py`（patch_component actor）、`map_product_service.py`（style restore 经门面 origin=system）、ws_service（legacy provenance，actor=ws_legacy） | `test_repair_actor_classified_as_repair_autofill`；runtime_repair 既有套件 | REPAIR_AUTOFILL/SYSTEM_DEFAULT 分类落账 |
| 门面透传 | `gis_world_state/mutation.py`（信封铸造/回声/provenance detail/collab 事件载荷） | `test_facade_records_envelope_in_provenance`；collab v6 既有套件 | provenance detail 含 producer_class/mutation_id |

## M4 — 前端队列 envelope（U4） 状态：☑

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| client_mutation_id 上行 + duplicate 收敛 | `frontend/lib/mapspec/user-mutation.ts`（5 个 POST 点 + `newMutationId` uuid/退化） | `user-mutation.mtid.test.ts`（5 用例） | 在途 pending 与服务端键同票；duplicate 按成功收敛（游标推进+spec 采纳+零回滚） |
| pending per-op 身份 | `frontend/lib/mapspec/session-cursor.ts`（旁路 `pendingMeta` 表 + `getPendingMutationMeta`；gen 模块级单调） | `session-cursor.mtid.test.ts`（5 用例） | ST-P3-3 关闭；pending 值本体形状不变（既有 toEqual 消费方零破坏） |
| durability 稳定幂等键 | `frontend/lib/map-commands/visibility-transaction.ts`（重试共用一票） | 既有 map-commands 134 用例 | superseded 尝试不留存证，committed/重放恰一次 |
| 队列观测 | `enqueueUserMutation` 深度/峰值/时延样本环（有界 32） | `tracks depth peak and bounded settle samples` | 纯诊断面，无持久状态 |

## M5/M6 — Reconciliation + 竞态矩阵（U7/U8/U9） 状态：☑

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| anomaly 检测纯函数 | `app/services/gis_world_state/reconciliation.py`（新，150 行） | `TestReconciliation` 7 用例 | 5 类 code 确定性排序（USER_HIDDEN_BUT_VISIBLE 最高）；层族别名不误报；≤32 有界；一致态零 anomaly |
| 生产接线（sync 投影） | `app/api/routes/ws_collab.py`（`_reconciliation_anomalies` → sync/doc 应答；失败不阻断） | `test_ws_collab_sync_projection_carries_anomalies` | 僵尸态 sync 投影产出 ZOMBIE_RUNTIME_LAYER |
| 竞态矩阵（barrier/event 驱动零 sleep） | `tests/cartography/test_mutation_transactions.py`（25 用例） | `TestDeterministicRaces` | remove-vs-upsert 无僵尸（spec/runtime 一致）；user-vs-agent 同秒 revision 单调；user pin 拒绝 template（layer_locked）；409→retry 幂等收口 |

## M7 — 文档 / 门禁 / 回归 状态：☑

| 任务 | 证据 |
|---|---|
| ADR | `docs/adr/0183-mutation-transactions-v1.md`（0183 安全号：0180-0182 被 #1274/#1275/#1277/#1276/#1278/#1279 占用） |
| Cartography 门禁（release-blocking marker） | `pytest -m cartography` → **958 passed, 4 skipped**（135s） |
| 定向回归（改动面） | mapspec 52 / map-commands 134 / collab v6 / concurrency / user-presentation / gis_world_state / finalize_display / adapter 套件全绿；`tsc --noEmit` 对改动文件零错误 |
| master 预存失败归因 | `tests/unit/test_mapspec_store.py::test_validate_and_compile` 在干净 master 工作树**同样失败**（WinError 2 Node CLI 解析，#1270 修复域）——非本线回归。新 worktree 首跑 `test_workspace_v4` 失败为环境差异（fresh `data/webgis.db` 缺 schema；`Base.metadata.create_all` 后 46/46 全绿，master 工作树同文件 27 passed 一致） |
| 测试双打更新 | `test_issue_735`（断言 actor 到达）、`test_execution_offload`（stub 收下 facade kwargs）——适配器签名的诚实演进，非断言弱化 |

## 未解决项 / 后续接口点

1. ws_service 遗留 socket 通道 CAS 化（需 socket 协议 v2，客户端破坏性）—— 已归因 + 可观测；
2. field-level CAS（disjoint 字段并发免 409）—— 本线以幂等+串行链覆盖同一风险面；
3. reconciliation anomalies → 修复环自动清理 + prometheus 指标；
4. collab 事件 mutation_id 的跨浏览器客户端去重。
