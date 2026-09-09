# 02 — Plan（实现 waves / commit 策略）

每个 wave：实现 → targeted tests → lint/type → progress 更新 → 独立 commit。

## 后端

- **W1 collab 总线**（`app/services/collab/__init__.py, bus.py`）：envelope/seq 语义、Redis pub/sub + 进程内降级、async listener（lifespan 启动）、本地扇出注册表（有界、慢消费者隔离）、发布预算（doc delta ≤64KB 等）。
- **W2 presence**（`collab/presence.py`）：Lua join（cap 32）、心跳 TTL 30s、快照、惰性清扫；进程内降级注册表。
- **W3 lease**（`collab/leases.py`）：Lua acquire/renew/release（token 校验）、TTL 60s、每 clientId ≤16、惰性过期。
- **W4 WS 端点**（`app/api/routes/ws_collab.py`）：双前端认证、所有权矩阵、`{event,data}` 信封、ping 捎带 revision（Redis 直读）、sync、消息白名单 + 预算、独立 rate bucket、finally 清理。
- **W5 mutation 事件挂钩 + 引擎锁守卫**：engine 内建 agent locked-layer 守卫（error_code=layer_locked）+ `_rev` 盖章；facade 成功后 publish `doc/delta/presentation/op` 事件；`MapSpecResult.error_code` additive。
- **W6 delta intent**：`patch_workbench_delta`（管线见 01-R1-M4）+ `base_workbench_revision`（全量）+ 校验 + OpenAPI snapshot 属主刷新。
- **W7 workbench state 端点**：`GET .../workbench/state`（新鲜读，`?meta=1`）。
- **W8 artifact-status 投影端点**：staleness + 近期 runs（≤10）派生投影，零新真相。

## 前端

- **W9 collab 客户端核**（`frontend/lib/collab/`）：WS 生命周期（退避+jitter 重连）、`{event,data}` 编解码、revision 门控、事件缓冲/replay、ping 心跳 + pong revision 对账、连接态（connected/degraded/offline）。
- **W10 delta 纯函数**：`workbench-delta.ts`（diffDoc→delta、applyDeltaToDoc、invertWorkbenchDelta、绝对值语义、上限）；单测含同节点异字段并发用例。
- **W11 persistence 接线**：delta 提交路径、`base_workbench_revision` 全量路径、409 单次 rebase（M5）、BC 保留（双通道幂等验证）、undo 反演换 inverse delta。
- **W12 远端采纳**：doc/delta/presentation 事件落 store（committed spec patch + revision 单调）、远端 op journal 合并、冲突/degraded/离线显式态。
- **W13 presence + lease UI**：头像排、编辑中徽标、lease acquire/renew/release、参与者上限降级文案；a11y（tree 语义/键盘/焦点）。
- **W14 artifact 感知**：stale/updated 徽标、为何变化 popover（lineage 摘要）、run 进度（recentRuns）、`artifact` 事件触发刷新。
- **W15 drag/drop reparent**：dnd-kit 组树、canReparentGroup 守卫、delta 提交、键盘拖拽等价操作。
- **W16 性能 + 文档**：100k 投影/虚拟化 work-count 基准、delta diff 基准、`descendantGroupIds` O(n²)→O(n) 修复（doc.ts:112）、ADR、CHANGELOG 最小追加。

## 测试矩阵（§15 映射）

| 必测项 | 落点 |
|---|---|
| 两浏览器并发 reorder | 后端集成（双 WS 客户端 + 双 mutation 提交）+ 前端 delta 并发单测 |
| user vs agent visibility | 既有 user-wins 守卫测试 + 新 lock 守卫测试 |
| stale revision / 409 | W6/W11（base_workbench_revision + delta CAS + 单次 rebase） |
| 重连补事件 / 事件重复 / 乱序 | W9（fake server）+ W4（sync 回放）|
| lock expiry / browser crash / server restart | W3（TTL）+ W4（finally + TTL 兜底）+ W9（重连 sync）|
| 未授权订阅 / 跨 owner 数据 | W4 认证矩阵 |
| undo 后他人无关变更存活 | W10 invert + W11 集成 |
| 100k projection work-count | W16 perf 测试 |
| BroadcastChannel 向后兼容 | W11 双通道测试 |
| 键盘操作 | W13 a11y 测试 |

## 资源纪律

后端重型测试串行；前端 vitest 单进程分批；无真实 Redis 依赖（进程内降级路径为主 oracle，Redis 路径用 fakeredis 或 skip-if-unavailable 标注）——检查 tests/conftest 既有 fixture 决定。
