# 01 — Architecture（Workbench V6 Server-Side Multi-User Collaboration）

> R1：已并入 Subagent-A 架构挑战的修订（C1/C2 CRITICAL、M1-M5 MAJOR、m1-m6 MINOR），修订处标注 [R1-n]。

## 0. 事实源与通道分层（不变式）

```text
真相（持久，唯一）：
  mapspec.workbench（组织态 doc / delta 应用后结果）+ _cartographic_mutation_revision CAS
  mapspec layers presentation + provenance（presentation 真相）
  ref store / artifact registry / WorkflowRun（artifact 与 run 真相）
通知平面（易失，可丢，正确性绝不依赖）：
  CollabEventBus（Redis pub/sub `webgis:collab:{sid}`；无 Redis → 进程内直连扇出 = degraded）
  会话事件通道 /ws/collab/{sid}（认证 + 所有权 + 有界消息预算）
瞬态协调（TTL，易失）：
  presence（Redis hash + TTL）、lease locks（Redis + Lua + TTL）
禁止：localStorage/IndexedDB 第二真相；客户端权威 state；bus 载荷携带凭据。
```

正确性恢复路径不依赖总线：任何客户端检测 revision 缺口（事件 revision > 本地+1 或心跳 gap）→ 单飞 refetch 权威 doc（复用既有 map-state GET）→ 重新武装基线。bus 丢失 = 退化为 V5 行为（自身提交 CAS 收敛 + 周期性 revision 对账），永不错误。

## 1. Collaboration Event Bus（backend `app/services/collab/`）

- `bus.py`：`publish(session_id, event)` + 每进程一个 asyncio listener task（app lifespan 启动；参考 cache_broadcast 的降级/退避语义但改 async）。
- event envelope：`{v:1, kind, sid, seq, ts}`；kind ∈ `doc|delta|presentation|presence|lock|op|artifact`。`seq`：
  - doc/delta/presentation/op（源自 mutation 提交）：`seq = mutation_revision`（天然单调、与 CAS 同源 —— replay cursor 免费）；
  - presence/lock（非 mutation）：`seq = 单调毫秒时间戳`（瞬态事件不参与 replay，只参与「新者胜」）。
- payload 上限：doc delta ≤ 64KB、presentation ≤ 512B、presence ≤ 512B/人、op ≤ 512B；超限拒发并降级为 `sync` 提示事件（接收方 refetch）。**绝不全量 100k doc 进 bus**（>64KB 的 doc 变更只发 `{kind:'doc', truncated:true, revision}` → 接收方 refetch）。
- 多进程：Redis PUBLISH → 各进程 listener 扇出给本地 WS 连接。degraded（无 Redis）：进程内 direct dispatch；跨进程不可达 = 诚实降级（前端显示 degraded 徽标 + 低频 revision 对账轮询，见 §8）。

## 2. WS collab 端点（`app/api/routes/ws_collab.py`）

`/ws/collab/{session_id}`，复用 ws.py 认证骨架并扩展：

- 认证：subprotocol `["bearer", <jwt>]`（认证会话）或 `["session", <owner_token>]`（匿名会话，hmac.compare_digest 对比 Conversation.owner_token，语义 = authorize_session_write auth.py:104-133）。token_version 校验同 ws.py:101-120。legacy NULL/NULL → 4003 fail-closed。
- 所有权：`get_session_meta(session_id, user_id)`（认证）或 owner_token 匹配（匿名）；跨 owner 一律 4003。
- rate limit：per-IP 连接数（复用 limiter）+ 每连接消息预算（token bucket：默认 20 msg/s，超发 4408 close；presence 类消息服务端合并节流）。
- 连接生命周期：connect → 发 `hello{revision, participants snapshot, leases snapshot, degraded}` → 订阅本地扇出。收到 `sync{known_revision}`：服务端读 mapspec 权威 revision；不一致 → 回 `doc` 事件（权威 doc + revision，带 `replay:true`）；一致 → 回 `ok`。
- 接收消息白名单（其余丢弃计数）：`ping|presence|lease_acquire|lease_renew|lease_release|sync`。
- 断连：presence/lease 由 finally 清理 + Redis TTL 兜底（进程崩溃无 finally 时 TTL 过期自愈 —— no permanent orphan lock）。

## 3. Presence（`collab/presence.py`）

- 记录：`clientId → {userLabel, color, kind:user|agent, viewport:{zoom?,bbox?}, selectionIds≤50, editingLayerId?, editingGroupId?, ts}`；**无坐标级 cursor**（隐私最小集；optional 字段缺省不发）。
- 存储：Redis hash `collab:presence:{sid}`（field=clientId，PX per-field via 独立 key `collab:presence:{sid}:{clientId}` TTL 30s 更稳 —— 选 per-client key + zset 索引 `collab:presence-index:{sid}` score=expireAt）。心跳 10s；TTL 30s；读到过期成员惰性清除 + zset `ZREMRANGEBYSCORE` 周期清扫。
- 有界：每会话参与者 ≤ 32（超出拒绝 join，4408 + 理由）。
- 节流：客户端 presence 变更 coalesce 250ms；viewport 变化 500ms；服务端 per-connection presence 消息 ≥ 200ms 间隔（合并最后态）。
- 通知：变更 PUBLISH `presence` 事件（join/update/leave，载荷 ≤512B）；快照只随 hello/sync 回给单连接，不广播。
- 隐私：userLabel = 认证用户的显示名脱敏（首字符+掩码）或匿名会话自报 clientLabel；绝不传 email/token/user_id 原文。

## 4. Shared Workbench State + Delta Patch

- 新 intent `patch_workbench_delta`（mapspec_mutations discriminated union 增量成员）：

```text
WorkbenchDelta = {
  setGroups?: GroupNode[]            // upsert-by-id（新增/改名字/折叠/reparent）
  removeGroupIds?: string[]          // 级联：子孙组一并移除；成员降为未分组
  membershipSet?: {layerId,groupId}[]// ≤2000/patch
  membershipClear?: layerId[]        // ≤2000/patch
  locksAdd?: layerId[] / locksRemove?: layerId[]   // ≤2000/patch
  mode?: explore|analyze|compose
}
```

- 服务端语义：锁内 load 当前 doc → 应用 delta → `_workbench_doc_error` 同款结构/体积校验 → COW 写回 + revision+1。非法/超限 4xx 整体拒绝（无半更新）。delta 本身 ≤64KB。
- `patch_workbench_state`（全量）保留：恢复/hydrate/小 doc 场景不变（backward compat）。
- 前端 persistence：armed 后组织态变更 → 与 lastCommittedJson 基线做**结构化 diff**（O(changed)，非 stringify 全量）→ 有 delta 走 delta intent；diff 失败/超限回退全量。409 回灌逻辑不变（delta 也可能 superseded）。
- **undo 反演改为 inverse delta**：`docCommand` 捕获 before/after 后计算 field 级 inverse delta（membership 键级回退、组 upsert/remove 反演、锁增删反演）；undo = 经同一 CAS 通道提交 inverse delta → 并发下只回退本命令触碰的字段，**不再整表快照回写**（G6 闭合）。inverse 无法表达时（理论不发生：delta 域闭包）journal-only 并披露。

## 5. Conflict Handling（明确不上 CRDT 的理由）

- 组织态是低频、粗粒度、树形结构操作；CRDT 收敛语义（RGA/RRArray）与「用户可见的组树」心智模型错配，且会引入第二事实源风险。选型：**optimistic CAS + delta rebase + typed conflict**。
- CAS 失败（409 superseded）：回灌服务端真相（既有）；本地未提交编辑保留为 dirty → 自动 re-diff → 重新提交（一次自动 rebase 尝试）；仍冲突 → 显式冲突态（toast + 面板 degraded 徽标），**绝不静默 no-op**。
- presentation/视图：per-layer CAS 既有通道 + revision 单调采纳；LWW with evidence（provenance 已记录 origin/actor/revision）。
- lock-required 操作：锁定层上的他人/agent 变更服务端拒绝（G10）。
- agent vs user 优先级：user-wins 守卫（既有，mutation.py）不动；新增服务端 lock 守卫同向（agent 遵从用户锁）。

## 6. Locks（双轨，边界清晰）

- **持久意图锁**（既有 lockedLayerIds，doc 内）：用户显式「锁定」，服务端强制 gate **agent** intents（G10 闭合）：`apply_gis_mutation` pre_commit_check 扩展 —— origin=agent 且 intent 目标层（含 family 语义，同 presentation guard 的 `_should_match_layer_family`）∈ prior spec `workbench.lockedLayerIds` → `is_error + error:'layer_locked'`（tool 契约一致）。用户路径不受限（自己的项目）。
- **瞬态编辑租约**（新，`collab/leases.py`）：`lockKey ∈ layer:{layerId}|group:{groupId}`；Redis hash `collab:lease:{sid}` + Lua（owner token 校验的 acquire/renew/release，模式同 distributed_lock.py:39-51）；TTL 60s（配置 `COLLAB_LEASE_TTL_S`），续期 20s；每 clientId 租约 ≤ 16（有界）；惰性过期清扫 + 断连释放 + TTL 兜底。
- 租约是**advisory**：UI 显示「X 正在编辑」+ 其他用户写入时提示确认（软协调）；agent 同时遵从意图锁（硬）与租约（作为 layer_locked 参考 —— agent 的编辑通道在租约非己有时同样拒绝，与意图锁同词表）。
- 无永久孤儿：TTL 过期即失效；Redis 丢失 = 租约消失（advisory 语义安全降级）。

## 7. Server-backed Undo/Redo & Journal

- 命令栈仍在客户端（会话绑定、50 上限）；**执行通道 = 服务端 CAS delta**（§4）→ 天然跨浏览器安全。
- journal：每次成功 mutation（facade 层）PUBLISH `op` 事件 `{seq:revision, kind, actor:user|agent, label≤80, target, reversibleHint}`；前端 opLog 面板合并本地+远端条目（有界 200，远端条目不可本地 undo——只显示「由 {label} 执行」，本端 undo 只作用于本端栈）。
- 语义披露：remove_layer / 数据类操作 reversible:false（现状保持，journal 标注）；undo 他人在先的无关变更 = delta 只回退自身字段，无整表回滚；并发 undo 冲突由 CAS+rebase 收敛。

## 8. Reconnect/Resync 与 degraded

- 客户端 WS：指数退避重连（0.5s→8s cap，jitter）；重连成功发 `sync{known_revision}`；服务端按 §2 回 doc/ok。
- revision 对账兜底：总线 degraded 或页面 visibilitychange→visible 时，单飞轻量 revision 探测（既有 map-state GET 的 revision 字段；15s 最小间隔）；缺口 → refetch doc。
- 服务进程重启：Redis 键存活（presence TTL 自清）；进程内 backlog 丢失无害（正确性走 revision sync）。

## 9. Artifact/Workflow Awareness

- 新投影端点 `GET /chat/sessions/{sid}/workbench/artifact-status`（require_owned_session；**派生投影，零新真相**）：`{staleRefIds≤200, layers:{layerId→{status: current|stale|missing, refId}}, recentRuns≤10:{id,workflow,status,created_at}}`，来源 = ref store staleness + mapspec layer ref 绑定 + WorkflowRun 近期行。
- 前端：图层行 stale/updated 徽标；「为何变化」popover = lineage 摘要（既有 lineage_service 数据）；run 进度条读 recentRuns；`artifact` bus 事件触发刷新（无事件时 30s 静默对账）。

## 10. UI Quality

- presence 头像排 + 租约徽标 + 冲突/degraded 显式态（layers-tab header）；
- drag/drop reparent：@dnd-kit（既有依赖）挂组树，复用 `canReparentGroup` 守卫 + delta 提交；
- a11y：树 role=tree/grid 语义、aria-level/setsize/posinset、方向键导航、Enter 重命名、Space 折叠、删除确认焦点管理；虚拟窗口固定行高下焦点跟随滚动（existing 虚拟化钩子扩展）；
- 键盘 parity：面板按钮已有 undo/redo；补组树全套键盘操作。

## 11. 性能预算（work-count 口径）

| 路径 | 预算 |
|---|---|
| delta diff | O(changed)；10k membership 局部改名 < 5ms（无全量 stringify） |
| delta 服务端应用 | O(patch + doc 校验)；校验复用既有单趟结构检查 |
| presence 更新 | client coalesce 250ms；server ≤5 RTT/s/连接；无 cursor 全量 doc stringify |
| 投影/虚拟化 | projectWorkspace O(n) 既有；新增 inverted index 组操作 O(subtree)；100k 行投影 work-count 基准 + <60 DOM 窗口 |
| bus 载荷 | doc delta ≤64KB；其余 ≤512B；broadcast fan-out per-connection send 有异常隔离（慢消费者断开不阻塞他人） |

## 12. 安全边界

- 订阅 = 所有权强制（4003 矩阵：跨用户 JWT、错 owner_token、legacy NULL、不存在会话）；消息无凭据；日志不落 token（subprotocol 与 ws.py 同法）；
- 消息预算/白名单/JSON 深度防护（payload ≤16KB except sync 应答由服务端构造）；DoS：per-IP 连接 ≤5/60s（既有）+ 每连接 rate bucket + 参与者/租约上限；
- presence 隐私最小集；跨 session 载荷字段一律服务端生成（clientId 服务端分配）。

## 13. 兼容与 rollout

- BroadcastChannel 通道保留（同浏览器快路径，双通道去重：revision 门控使重复采纳幂等）——§15 必测向后兼容；
- `patch_workbench_state` 全量语义不变；delta 为纯增量 intent；旧前端不发送 delta（行为不变）；
- WS collab 端点为新增；旧 /ws 不动；无 migration；OpenAPI snapshot 属主刷新；
- 降级链：Redis 失效 → degraded 总线（进程内）→ 客户端轮询对账；任一层失效不阻塞既有单用户工作台。

## 14. Test oracle

双客户端收敛语义：给定初始 doc D0 与操作序列（A: move L1→G1; B: rename G2; A: undo），oracle = 服务端最终 doc 与所有活跃客户端投影一致，且每步 revision 单调、无整表覆盖（B 的 rename 在 A undo 后存活）。以「两 WS 客户端 + 共享 Redis（或进程内总线）」的集成测试实现 §15 必测矩阵。

---

# R1 修订（Subagent-A 架构挑战后冻结）

## [R1-C1] 锁守卫下沉引擎层

agent 工具直连引擎绕过门面（`app/tools/cartography_tools.py:649` → `mapspec_store.layer_remove:231` → `engine.apply_mutation`，无 pre_commit_check）。lock 守卫改为 **engine.apply_mutation 内建默认守卫**：origin="agent" 且 intent 目标层（RemoveLayerIntent / UpsertLayerIntent / PatchLayerPresentationIntent / PatchLayerStyleIntent）命中 prior spec `workbench.lockedLayerIds`（family 语义同 `_should_match_layer_family`）→ `MapSpecResult(is_error, error_code="layer_locked")`。`MapSpecResult` 增加可选 `error_code` 字段（additive；HTTP 400 载荷与 tool 结果透出）。用户路径不受限；无 workbench 分支 = 空 locked 集 = 零行为变化（全部既有测试不受影响）。

## [R1-C2] 全量 doc 的 workbench 级 CAS（base_workbench_revision）

- 引擎在每次 workbench 分支落盘（全量或 delta）时在 `mapspec["workbench"]` 内盖 `_rev = mutation_revision`（服务端保留键；前端 normalize 只取已知字段，天然忽略）。
- `SetWorkbenchStateBody` 增加可选 `base_workbench_revision`：提供且 ≠ 存储 `_rev` → superseded 409（回灌含当前 doc，既有通道）；缺省 = 旧行为（backward compat，风险披露于 PR）。前端始终携带（来源：自身上次 workbench 提交返回 revision / hydrate 时 spec.workbench._rev / BC doc 消息 revision）。
- delta intent `patch_workbench_delta` 走严格 `expected_revision` CAS（任何无关 mutation 推进 revision → 409 → 回灌 → re-diff → 单次自动重试）。

## [R1-M1] 心跳捎带权威 revision

WS collab 协议：客户端每 10s `ping`；服务端 `pong` 携带 `revision`（Redis 直读 `_cartographic_mutation_revision`，不经 L1 缓存）。mismatch → 客户端 sync 流程。只读参与者陈旧问题由此闭合。

## [R1-M2] 时序与新鲜读

- 连接序固定：**先订阅本地扇出 → 再读 revision → 再发 hello**（杜绝订阅前窗口漏事件）。
- refetch 期间到达的事件缓冲，refetch 落地后按 revision 门重放（delta 绝对值语义 → 重放幂等）；落地后 Redis 直读复核 revision，仍变 → 再 fetch 一次（上限 2）→ 转 degraded 对账。
- 新增专用端点 `GET /chat/sessions/{session_id}/workbench/state`（`?meta=1` → `{revision}`；完整 → `{doc, revision}`）：**绕过 L1 的新鲜读**（引擎/存储的权威读路径），singleflight 不需要（读无写放大）。revision 探测与 doc refetch 都走它（替代「复用 map-state GET」——后者返回全量 map_state 且有 2s L1 陈旧，m1 闭合）。

## [R1-M3] 组操作部分字段语义 + mode 出域

- delta `setGroups` 条目 = `{id, name?, collapsed?, parentId?}`（缺省键不动）；id 不存在 → create（要求 name 在场），存在 → patch。
- inverse delta 按**字段**构造（改名只回 name；建组 undo = removeGroupIds+membership 复原；删组 undo = setGroups 全节点+membership 复原——由 forward delta 捕获的 prior 态构造 `invertWorkbenchDelta(delta, beforeDoc)`）。
- **mode 移出 delta 域**（与 V5 undo R1-M2 决策一致：mode 不参与组织态撤销）；mode 变更走全量 doc 通道（自然随下次组织态提交收敛）。

## [R1-M4] delta 应用管线（服务端，固定次序）

1. setGroups：逐条 create/patch（parentId 必须引用当前已存在组；非法 400）；
2. removeGroupIds：级联移除子孙组 + 清空指向被移除组的 membership；与 setGroups 同 id → 400；
3. membershipSet（目标组必须在此刻存在，否则 400）→ membershipClear；
4. locksAdd / locksRemove；
5. 结果 doc 重跑 `_workbench_doc_error` 全量校验 + 盖 `_rev`。
membershipSet/Clear 同键：Clear 后 Set（Set 优先——声明序无关，固定语义 Set wins）。patch 自身 ≤64KB； membershipSet≤2000 / clear≤2000 / locks≤2000。

## [R1-M5] 409 rebase 语义（bounded 单次）

superseded 时：hydrate 服务端 doc 为基线 → 用纯函数 `applyDeltaToDoc(serverDoc, inflightDelta)` 重放本地在飞 delta → 结果写回 store → 自动重提交一次；第二次 409 → 停止，显式冲突态（toast + 面板徽标）。本地在飞期间的新编辑由订阅触发新一轮 diff（不叠加无限重试）。

## [R1-m2/m3/m4/m5/m6/NIT 采纳]

- WS 认证双前端：subprotocol `["bearer",<jwt>]`（认证）或 `["session",<owner_token>]`（匿名）；交叉使用（JWT 连匿名会话 / owner_token 连用户会话）→ 4003；token_version 仅认证路径。独立 rate bucket `ws_collab:{ip}` 5/60s。
- collab WS 信封沿用既有 `{event, data}` 形态（与 realtime contract 快照一致性最大化）；新增契约快照属主刷新（openapi + realtime-contract 如涉及）。
- 事件单扇出路径：发布者不本地直发，仅经总线（进程内降级 = 直发，二选一，无双计）。
- artifact-status 路径参数命名 `session_id`。
- presence join 用 Lua 原子 check-cap-写；ephemeral seq 用服务端接收时刻 `time.time_ns()`（不信客户端时钟）。
- 租约对 agent 不设硬闸（agent 遵从更强持久意图锁 + user-wins 守卫）；租约是用户间编辑协调（advisory）——披露于文档。agent 不持有 clientId，不参与租约。
