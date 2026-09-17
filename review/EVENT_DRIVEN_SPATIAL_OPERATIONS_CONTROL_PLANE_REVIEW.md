# EVENT-DRIVEN SPATIAL OPERATIONS CONTROL PLANE — 独立对抗审查记录

- 分支：`harness/event-driven-spatial-ops-v1`（baseline `origin/master` = `faa453a8`）
- 审查方式：独立 reviewer subagent 全量通读 `git diff faa453a8..HEAD` + 关键路径运行时实证；
  主 agent 复核后逐条修复。审查维度：Correctness/fail-open、Concurrency/idempotency、
  Security/tenancy、Performance/memory、Backward compatibility、Observability honesty、
  Contract drift。

## P1 findings（全部：红测 → 修复 → 回归）

| # | finding | 证据（修复前） | 红测 | 修复 |
|---|---|---|---|---|
| P1-1 | E3/E4 同步 hook 返回未 await 的 coroutine：事件**永不入账**且调用方拿到 truthy"成功" | `_dispatch_sync` 调用 async `_dispatch` 返回 coroutine；实证 `notify_job_finished_sync()` 返回 `<class 'coroutine'>` | `TestSyncDispatchPaths`（assert isinstance bool 且 ledger 有行） | `_dispatch_sync` 直接调同步 `ingest_sync`；补 sync 路径测试 ×3 |
| P1-2 | 失效桥结构性 no-op：`PendingChange(target_kind=subject_type)` 永不命中 `_seed_nodes` 词表 → 无节点被标 STALE | reviewer 对 workflow_v4/recompute.py:70-92 的 seed 词表分析；单测用 FakeWorkflowSvc 自伪响应掩盖 | `test_invalidation_e2e.py` ×4（真 InstanceStore+ChangeApplier+PackageRow） | 事件 ref ↔ 节点 `bound_ref`/`output_ref` 精确匹配 → `target_kind="node"` 种子；无 ref/无匹配节点诚实 skipped；端到端断言 descendants STALE |
| P1-3 | deferred 重试被 cooldown 吞掉：fire 时写 `last_fired_at`，重试同事件求值 `(0 < cooldown)` → fired=False → 动作永久丢失且事件标 processed | 全部测试 `_setup_watch` 用 `cooldown_s=0` 系统性掩盖 | `TestDeferredRetryWithCooldown`（cooldown_s=60 + DenyOnceGate） | `_process_event` 检测已持久 pending/deferred fire → 绕过求值直接重执行动作 |
| P1-4 | watch upsert 无 org 归属校验：org-b 可用同 `watch_id` upsert **劫持** org-a 触发器 | `ledger.upsert_watch` 按主键覆盖并改写 org_id | `test_upsert_rejects_cross_tenant_hijack` | 存在他 org 同 id 行 → `LedgerWatchConflict`（API 400） |
| P1-5 | webhook 单一部署级密钥 + body 自报 org_id：持密者可向**任意租户**注入事件（经 bridge 消耗其配额/建 mission） | HMAC 覆盖 body 但密钥不绑定租户 | `test_org_binds_tenant`（部署密钥伪造 org-b → 503） | per-org 密钥 `GIS_SPATIAL_EVENT_WEBHOOK_SECRET__ORG_<ORG>`；部署密钥仅对 `..._DEFAULT_ORG` 单一 org 生效 |

## P2 findings（已修）

- **事件循环上的同步 DB 写**（E1 挂 mutation 热路径）：`_dispatch` 改 `asyncio.to_thread`。
- **feature-off 非零成本**：E1 每次 mutation 做 org 解析查询——全部 `notify_*` 顶部 flag 前置零开销返回。
- **watch 状态 RMW 竞态**（多副本丢更新）：本版以 fire 行 `executing` 认领（`claimed_at` 列 + 条件更新 + stale 复位清扫）收紧 mission 动作双写窗口；watch 求值状态仍为 last-writer-wins（单副本 drain 串行精确；多副本下 consecutive/窗口计数为 advisory，已在本文件"已知边界"披露）。
- **fire 'pending' 重试双执行**：`claim_fire` 条件更新（pending/deferred→executing），失败方得 duplicate。
- **coalesce 盲合并**：分组键加入 `dedupe_key`；幸存者取组内最高 priority；`collapsed_count` 改 SQL 原子累加；扫描加 `_COALESCE_SCAN_CAP=1000` 上界。
- **watch 求值 N+1**：drain 级 watch 定义缓存（同批只读一次）。
- **SSE 生成器在 loop 上做同步 DB**：查询 `to_thread` 卸载；心跳对齐 D11 = 15s。
- **portfolio `recent_watch_fires` 未按 project 过滤**：经事件 (org, project) 归属过滤。
- **POST /drain 全局跨租户**：改 `require_admin`（drain 是全局消费动作，不得暴露给租户用户）。

## P3（备忘，未在本分支处理）

1. `mark_processed/mark_failed` 不校验 `claimed_by`——stale 重认领窗口内旧 worker 可再结算（副作用幂等吸收，at-least-once 窗口比宣称宽）。
2. durable cursor 当前是"只写元数据"（除 /stats 外无消费者）；注释宣称的"重启恢复据此对账"实际由 status CAS 恢复承担——注释已按实现口径修正（见 ledger docstring 语义说明）。
3. cooldown 基于 `occurred_at`：乱序/迟到事件的时间差已取绝对值（|Δt|），负 diff 绕过冷却已修；基于处理时间的冷却留待后续。
4. `window_min_events` 契约上限 1000 vs 状态环 128：>128 的配置永不触发且无告警。
5. fire 行按 (watch,event) 单行：watch 配多动作时 outcome 相互覆盖。
6. `/spatial-events/health` 无鉴权暴露 flag 状态（与 mission health 同口径，有意）；`/stats` 暴露 org 域计数与全局 cursor 水位（≈最大行 id，低敏感）。
7. 速率限制 >256 org 时驱逐字典序最前桶（近似最旧；非精确 LRU）。
8. `find_affected_instances` 只查 `status='running'`（与注释一致化了；含 suspended 语义的实例失效留待后续）。
9. CheckConstraint 中 'duplicate' 状态词保留未用（去重发生在 append 层；词表留给未来行级 dedupe 标记）。

## 误报/降级

- "claim_batch ORDER BY CASE 无法用索引"：属实但候选集已 `LIMIT`，且事件面吞吐目标（burst 1000 收敛）实测无压力；记录为性能备忘而非缺陷。
- "cursor 未实现恢复语义"：部分误报——恢复确实不依赖 cursor（status CAS 承担），cursor 是对账/观测水位；已改注释与文档口径，非功能缺陷。

## 与 master / open PR 的交叉

- #1335（`fix/harness-claim-mission-failclosed`）：其热区 `mission_runtime/{service,store}.py`、`hotpath_convergence/*`、`evidence_claim/{census,grounding,verify}.py` 本分支**零修改**（只调用公开 API）。若 #1335 先合入，本分支 rebase 后无文本冲突面。
- #1336（geoai）：热区 modelops/geoai/frontend 无交集；本分支对 `app/main.py`（import+include+lifespan+worker）、`tests/conftest.py`（env baseline 追加）为不同区域加法触碰；若并行合入由 rebase 消解。
- 结论：**不需要 integration PR**；但 PR 合入顺序上本分支应在 #1335 之后 rebase 一次以吃进 mission store 的 fail-closed 修复（bridge 依赖其 org 断言语义）。

## 复审结论

5 个 P1 全部红测→修复→回归闭环；11 个 P2 修复 9 个（余 2 个降级为备忘：watch 状态 advisory 语义、claim CASE 索引）。reviewer 的"骨架大体正确、P1 集中在 flag 开启后的生产路径且被测试矩阵掩盖"结论成立——本分支修复后补充的测试专门覆盖了这些被掩盖路径（cooldown=60、真实 ChangeApplier、sync hook、跨租户 upsert/webhook）。
