# 00 — Baseline（master @ 8a33e3a5，2026-09-10）

Worktree: `../webgis-ai-agent-workbench-v6`，branch `feat/workbench-v6-multiuser-collaboration`，基于 `origin/master` 8a33e3a5。

## 仓库态势

- 最新 open PRs（并行 Epic，无同域冲突）：#1178 science-v5、#1177 workflow-v5、#1176 lakehouse-v7、#1175 harness-v6、#1174 data-fabric-v7、#1173 cartographic-harness-v6。
- 直接前置：PR #1169 Workbench V5（merged）——组织态持久化/同浏览器多 tab/undo/10k 虚拟化；其 Known limitations 明言「多用户（跨浏览器）协同需要服务端事件总线」。
- 最新 issues：#1107-#1113 已关闭的安全/性能修复（SSRF、IDOR、锁泄漏、缓存幽灵）；无本域 open issue。
- ADR 编号：master 最高 `0118-*`；6 个并行 open PR 均宣称 ADR-0119（互斥）。本分支先用 `0119-workbench-v6-multiuser-collaboration`，rebase 时按最新编号重查。
- Alembic head：`0031_revision_indexes`（V5 未动 migration；本 Epic 目标同样零 schema 变更）。

## 已存在的基础（file:line 证据）

| 能力 | 位置 | 状态 |
|---|---|---|
| WorkbenchDocV5 模型/归一化/256KB 闸 | `frontend/lib/workbench/doc.ts:40,165,23` | native |
| 持久化（armed 门+800ms 防抖+409 回灌） | `frontend/lib/workbench/persistence.ts:88,152,268` | native |
| 同浏览器多 tab（BroadcastChannel `wb5:{sid}`，revision 门控采纳） | `frontend/lib/workbench/collab.ts:79-111` | native（V6 需保持兼容） |
| 客户端 undo/redo 命令栈（capture-before-execute，经 CAS 通道重放） | `frontend/lib/workbench/undo.ts:82,174,259` | native |
| 锁定护栏 lockedLayerIds（**仅前端** gate agent visibility/remove） | `frontend/lib/workbench/layer-lock.ts:26` | native（服务端无强制） |
| 会话恢复锚 | `frontend/lib/workbench/session-anchor.ts` | native |
| mutation 通道（13 intent discriminated union；409 superseded；503 busy） | `app/api/routes/mapspec_mutations.py:166,337,306` | native |
| 引擎 CAS（`_cartographic_mutation_revision` 单调持久）+ COW + checkpoint | `app/services/mapspec/lifecycle_engine.py:773-790,1673` | native |
| 每会话分布式锁（Redis+Lua token 释放/续期；进程内降级；有界 fallback 注册表） | `app/services/distributed_lock.py:1-80` | native |
| mutation 统一门面（user-wins 守卫 + pre_commit_check 锁内复检 + provenance） | `app/services/gis_world_state/mutation.py:192,238` | native |
| 已认证 WS 端点 `/ws/{session_id}`（JWT subprotocol、token_version、所有权、IP rate limit） | `app/api/routes/ws.py:30-155` | native（前端 useWebSocket 死代码；无 owner_token 匿名支持；无跨进程扇出） |
| 进程内连接管理器（per-session broadcast） | `app/services/ws_service.py:12-71` | native（单进程） |
| Redis pub/sub 先例（cache_broadcast：线程监听、有界消息、降级 no-op、退避自愈） | `app/services/cache_broadcast.py:27,82,138,168` | native |
| 会话所有权语义（user_id 匹配 / 匿名 owner_token hmac 对比 / legacy NULL 拒绝） | `app/core/auth.py:104-133` | native |
| SSE 携带 mapspec+revision（agent 回合内） | `frontend/lib/hooks/use-sse-stream.ts:528-534` | native |
| O(n) 工作台投影（Set 化）+ 窗口虚拟化 | `frontend/lib/layers/workspace-projection.ts:78-177`、`frontend/components/sidebar/layers-tab.tsx` | native |
| ref staleness 传播（后端权威） | `app/services/ref_lifecycle.py:127-172` | native（前端零消费） |
| lineage / artifact 注册表 / WorkflowRun 台账 | `app/services/lineage_service.py`、`app/models/project.py:229,150` | native（工作台 UI 零消费） |

## 已确认缺口（V6 必须闭合）

- **G1 跨浏览器事件分发缺失**：doc 变更只经 BroadcastChannel（同浏览器）与 SSE（仅 agent 回合）；浏览器 B 看不到浏览器 A 的组织态提交，只能靠自身下次提交时的 409 回灌被动收敛 —— 长时间陈旧 + 全量 doc LWW 互踩。
- **G2 WS 无匿名 owner_token 支持**：`ws.py` 仅 JWT（`app/api/routes/ws.py:82-99`）；匿名会话（owner_token 语义，auth.py:116-132）无法建立事件通道。
- **G3 无跨进程扇出**：ConnectionManager 进程内 dict（ws_service.py:14）；k8s replicas:2 下同会话两连接可能落不同 pod，无总线则互不可见。
- **G4 无 presence**（任何形式）。
- **G5 无租约锁**：lockedLayerIds 是持久意图锁（agent gate），无 TTL/租约/接管语义；无编辑协调。
- **G6 全量 doc LWW + 快照式 undo**：`patch_workbench_state` 整体替换（lifecycle_engine.py:359-368）；undo 反演 `applyDocSlices` 全量回写四个切片（undo.ts:190-196）→ 并发下他人在窗口期的组织态编辑被快照覆盖（§D「no whole-table rollback」违例）。
- **G7 >256KB 场景停摆**：membership 全量平铺 10k 键 ~250-300KB 触顶（doc.ts:23 注释）；无 delta patch。
- **G8 无 server journal 可见性**：opLog 是 per-tab store（hud-types OpLogEntry）；跨浏览器不可见。
- **G9 artifact/workflow 感知零消费**：staleness/lineage/WorkflowRun 后端完整，工作台 UI 无 stale 徽标/为何变化入口。
- **G10 服务端锁强制缺失**：agent 携带 locked 层的 presentation/remove 直接走 `apply_gis_mutation` 服务端不拒绝（守卫只覆盖 user presentation 反转，mutation.py:82-107）；前端 ack gate 可被非 UI 通道绕过。
- **G11 projection 局部 O(n²)**：`descendantGroupIds` 用 `frontier.includes`（doc.ts:112）+ `subtreeMaxDepth` 反复 groupDepth（doc.ts:141-148）——大树下 reparent 校验放大。
- **G12 焦点/键盘 parity**：嵌套树 aria/键盘导航不完整（V5 Known limitations：虚拟窗口次行可见性受限）。

## 资源/复杂度审计

- mutation 每次全量 JSON parse + COW 分支替换（已接受成本，batch 通道已存在）；
- presence/lease 若按每 cursor move 一次 Redis RTT 设计将放大 —— 必须 client 节流 + server 合并；
- event 载荷：doc delta 而非全量 doc；presence 载荷 ≤ 256B/人；journal 摘要 ≤ 256B；
- 100k 树：投影 O(n) 已达；瓶颈在 normalize/diff —— delta diff 必须 O(changed) 而非 O(n) stringify。

## 共享文件冲突面（与 9 个并行 worktree）

- `CHANGELOG.md`（最小追加）、`docs/adr/`（编号冲突高危，rebase 时重查）、`tests/quality/snapshots/openapi.json` + contract drift report（属主刷新）、`frontend/lib/workbench/*` 与 `layers-tab.tsx`（本 Epic 主战场，其他 Epic 不碰）、`app/services/gis_world_state/mutation.py`（quality-v2 刚动过，已合并，冲突面小）、`app/core/config.py`（additive 配置）。
- Alembic：零新增 migration（无 schema 变更）→ 无双 head 风险。
