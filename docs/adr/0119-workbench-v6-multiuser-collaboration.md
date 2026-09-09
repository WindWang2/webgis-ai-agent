# ADR-0119: Workbench V6 —— 服务端多用户协作

日期：2026-09-10 ｜ 状态：Proposed ｜ 关联：ADR-0105（Workbench V5）、ADR-0058（CAS）、CONCURRENCY-V2（分布式锁）

## 背景

V5 已把工作台组织态（WorkbenchDocV5）持久化到 `mapspec.workbench`（同一 per-session
分布式锁 + `_cartographic_mutation_revision` CAS 串行链），同浏览器多 tab 经
BroadcastChannel 收敛。跨浏览器/跨进程没有任何事件通道：组织态只在自身下次提交的
409 回灌时被动收敛（长时间陈旧 + 全量 LWW 互踩），无 presence、无编辑协调、无
服务端操作留痕、agent 可经工具直连引擎绕过用户锁（V5 锁 gate 仅在前端）。

## 决策

### 1. 三层分离（不变式）

- **真相**：`mapspec.workbench` + `_cartographic_mutation_revision`（既有）。
  组织态的一切持久化仍走该通道，本 ADR 不引入第二事实源。
- **通知平面**：CollabEventBus（Redis pub/sub `webgis:collab:{sid}`；无 Redis =
  进程内降级）。**正确性绝不依赖总线**：任何客户端检测 revision 缺口 → 新鲜读
  `GET .../workbench/state`（绕过 L1）→ 重水合。
- **瞬态协调**：presence（TTL 30s，cap 32）与编辑租约（TTL 60s，Lua token 校验）。
  TTL 过期自愈，无永久孤儿。

### 2. Delta patch 而非全量 LWW

`patch_workbench_delta`（绝对值语义，重放幂等）：部分字段组更新、级联删除、
membershipSet/Clear（Set 优先）、locks 增删。服务端固定管线应用 + 结果级全量校验。
undo 反演 = **field 级 inverse delta**（`invertWorkbenchDelta`），不再整表快照回写
—— 并发下他人在窗口期的编辑存活（no whole-table rollback）。mode 不在 delta 域。

### 3. Workbench 级 CAS（R1-C2）

全量 doc 提交携带 `base_workbench_revision`；引擎在 workbench 分支盖 `_rev =
mutation_revision`，不匹配 → superseded（409 回灌）。堵「游标 revision 被无关
mutation 推进后，陈旧全量 doc 借新鲜 CAS 静默整表覆盖」的窗口。缺省 = V5 语义
（旧客户端兼容；风险：旧客户端保留原 LWW 缺口）。

### 4. 服务端 lock 守卫下沉引擎（R1-C1）

agent 锁定层守卫内建于 `engine.apply_mutation`（单发 + presentation batch 双路径），
而非 mutation 门面 —— agent 工具（cartography_tools → mapspec_store → engine）
直连引擎，守卫放门面会被结构性绕过。命中 `workbench.lockedLayerIds`（family 语义）
→ `error_code="layer_locked"`（MapSpecResult 新增 typed 字段，additive）。用户路径
不受限；租约是用户间 advisory 协调，**不**约束 agent（披露：单浏览器租约可短时
饿死 agent 编辑，属产品语义）。

### 5. 冲突模型：CAS + delta rebase + typed conflict（不上 CRDT）

组织态是低频粗粒度树操作；CRDT 收敛语义与组树心智模型错配且引入第二真相风险。
409 → 回灌服务端真相 → 在飞 delta 在服务端 doc 上重放**一次**（bounded）→
再冲突 = 显式冲突态（横幅 + toast），绝不静默 no-op。

### 6. 认证与安全

`/ws/collab/{sid}`：双前端认证（bearer JWT + token_version / session owner_token
hmac，语义 = `authorize_session_write`）；交叉使用（JWT 连匿名会话 / owner_token
连用户会话）4003；legacy NULL/NULL fail-closed（#1109）。独立 rate 桶
`ws_collab:{ip}`；入站白名单 + token bucket（20/s）+ 16KB 上限；出站每连接有界
队列（慢消费者断开）。事件无凭据；presence 隐私最小集（无坐标 cursor、label
自报且服务端裁剪）。

### 7. Artifact/workflow 感知 = 派生投影

`GET .../workbench/artifact-status` 投影 artifact_registry 台账（status/producer
节点/inputs 血缘；stale 优先 ≤200）。`ref_lifecycle` 失效路径向总线发 `artifact`
事件（单向通知，无环）。前端 `layer._refId == artifact_id` join → stale 徽标。
WorkflowRun 台账是 project 域真相，不在会话协作平面（披露为非目标）。

## 后果

- 正向：跨浏览器收敛从「被动 409」变为秒级事件 + 对账兜底；undo 并发安全；
  agent 锁服从成为服务端不变量；降级链（Redis→进程内→轮询）保证单用户零回归。
- 代价：新增一个 WS 端点与三个 Redis 键族（presence/lease/hash TTL 1h GC）；
  mutation 成功路径多一次 best-effort publish（锁外，失败静默）。
- 风险披露：Redis 多进程部署下跨 pod 依赖 bus 与新鲜读；`_rev` 无盖章的旧 doc
  全量提交退化为 V5 语义（base=-1 缺省省略）。

## 测试锚点

后端 `tests/test_collab_v6.py`（29 例：认证矩阵/守卫/CAS/delta/事件/扇出）；
前端 `lib/workbench/delta.test.ts`（11）、`lib/collab/collab-v6.test.ts`（11）、
`workbench-scale.test.ts`（5）、`layers-tab.workspace.test.tsx`（V6 块 3）。
