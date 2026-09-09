# 00 — Baseline (master @ 445ad30e, 2026-09-09)

Worktree: `../workbench-v5`, branch `feat/workbench-v5-collaboration` from `origin/master` 445ad30e.
Evidence source: 2 个只读 Explore subagent 全仓审计 + 主 agent 直接读码（workbenchSlice / workspace-projection / user-mutation / mapspec_mutations.py / lifecycle_engine.py）。

## 平台现状（不得重复建设）

GIS Harness V4（autonomous runtime）、Spatial Science V3、Workbench V4（Explore/Analyze/Compose + 图层面板 + swipe compare）、Extension Platform V1、Quality/Reliability V1、Data Control Plane V4 / GeoCompute V5 均已在 master。

## 已验证事实（路径:行号）

### State 架构
- Zustand v5 单 store `frontend/lib/store/useHudStore.ts`，7 slices（layers/results/task/settings/ui/dock/workbench）。
- 「UI projection 不是地图真相」：权威在 backend MapSpec（`frontend/lib/mapspec/session-cursor.ts:7-13` sessionId/revision/ownerToken + CAS revision；`user-mutation.ts:90-97` 单 promise 串行链）。
- workbenchSlice（`frontend/lib/store/slices/workbenchSlice.ts`）：mode、selectedLayerIds、lockedLayerIds（string[]，无持有人/scope）、isolate、**单层** layerGroups + membership、comparison。全不持久化（:14-16 自述 + partialize 不含）。

### Layer 状态机
- `frontend/lib/layers/layer-status.ts:81-118` `deriveLayerStatus` 七态封闭词表（expired>failed>loading>hidden>rendering>stale>ready），纯派生不落 store。
- manual（`user-mutation.ts:99-170`）与 agent（`visibility-transaction.ts:201-296`）走**同一** POST `/mapspec/mutations` CAS 通道 —— 无 manual/agent 双 cursor 分裂。

### 已确认缺口（V5 必做）
1. **agent 绕过 lock**：`layerCommands.ts:292-440` remove_layer 全程无 lock 检查；`visibility-transaction.ts:201-296` 同；无 typed lock_conflict。`layers-tab.tsx:491-493` 自认「后续接线」。
2. **group tree 单层 + 易失**：`LayerGroupEntity{id,name,collapsed}`（workbenchSlice:42-46），无嵌套、无持久化、会话切换 resetLayerGroups 清空。
3. **无 undo/redo**：唯一 historyStack 是死代码 EmbodiedHudEngine（`useHudStore.ts:148-236`，仅 3 个 UI 字段，零生产调用点）。
4. **opLog 断供**：`uiSlice.ts:177-184` 有 store 无写入点（`embodied-hud.tsx:323` 自述调用点在零挂载文件）。
5. **协同为零**：grep 全仓 BroadcastChannel/WebSocket/EventSource 生产代码零命中；后端 WS 前端消费者已删（`ws.py:8-10` 死代码）。
6. **side-by-side 诚实下线**：`comparison-view.tsx:15-18` 词表保留但恒按 swipe 0.5 裁剪（:429-434）；副图 is3D 恒 false（:170）、activeFilters 恒空（:166-171）、无 legend。
7. **无 viewport thinning**：仅 MVT>5000 通道（`layer-data.ts:25-32`）+ bbox 预过滤（`utils/geo.ts:30-77`）；无空间索引/渐进/视口取消。
8. **无虚拟化**：`layers-tab.tsx:995-1023` 全量渲染；防线仅 500 层（`test/workbench-stress-500.test.tsx`）。
9. **刷新即丢**：sessionId 不落 localStorage（`use-workspace-session.ts:321-322`），恢复唯一入口是 History 手动 selectSession。
10. **artifact stale 无前端指示**：后端 lineage/staleness 完整（artifact_registry valid→superseded→stale），前端零消费。

### 后端可复用底座（不新造）
- MapSpec lifecycle engine 意图管线：`app/services/mapspec/lifecycle_engine.py:581` apply_mutation = per-session 分布式锁 + CAS + 事务回滚 + checkpoint；意图为 discriminated dataclass；COW 分支 copy 模式可扩展。
- 用户 mutation 路由：`app/api/routes/mapspec_mutations.py:176`（12 intents，409 superseded / 503 session_busy 映射）。
- provenance 环：`app/services/gis_world_state/provenance.py`（64 条/会话）。
- WorkspaceSnapshot V4 / MapProduct 版本账本 / lineage（Artifact.layer_id FK）已齐；契约闸 = OpenAPI 字节快照（`tests/quality/test_api_compatibility.py`）+ drift report 双闸。
- Alembic head `0031_revision_indexes`。

## Scope 冻结

本 Epic = workbench 前端纵向深化 + 一个最小后端意图扩展（workbench doc 持久化），**不新造** registry/store/runtime，不建第二事实源。cartographic renderer / GeoCompute scheduler / Harness planning 不触碰。
