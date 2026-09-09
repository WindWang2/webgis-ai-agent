# 01 — Architecture (Workbench V5 & Collaboration)

## 目标态一句话

在**既有单 store + session-cursor CAS 单真相**之上，把 workbench 的组织态（嵌套分组树 / lock / mode）升级为可持久化、可恢复、可审计、可多 tab 收敛的 **Workbench Document V5**，并补齐 agent lock 强制、undo/redo、op journal、双视图 parity、视口 thinning、10k 虚拟化、刷新恢复与 a11y。

## 数据/状态所有权（单一事实源不变式）

| 域 | 真相 | V5 变化 |
|---|---|---|
| 图层 presentation（visible/opacity/style/order） | backend MapSpec + CAS revision（既有） | 不变；undo 经同一 CAS 通道反向提交 |
| workbench 组织态（groups/membership/locks/mode） | **backend MapSpec `workbench` 分支**（新增 intent `patch_workbench_state`，同一锁+CAS+provenance 链） | 新增；前端 store 只是投影 |
| transient UI（selection/isolate/search/panel/comparison position） | 前端 store，不持久化（既有纪律） | 不变 |
| op journal | 前端 journal（uiSlice opLog 接活写入）+ 后端 provenance 环（既有） | 前端补写入点 |

**禁止**：新 store、新后端表、localStorage 里的影子 project truth。workbench doc 走 mapspec 同一落盘（session 域），project 域打开由既有 WorkspaceSnapshot/MapProduct 承载（不扩）。

## 契约

```ts
// frontend/lib/workbench/doc.ts
interface WorkbenchDocV5 {
  version: 5;
  groups: WorkbenchGroupNode[];        // 嵌套树（root 序 = 创建序）
  membership: Record<string, string>;  // layerId -> leaf group id
  lockedLayerIds: string[];
  mode: WorkbenchMode;
}
interface WorkbenchGroupNode { id; name; collapsed; parentId: string | null }
```
- 后端：`SetWorkbenchStateIntent(doc: dict)` → `mapspec["workbench"]` 分支整体替换（64KB JSON cap，422 超限）；前端 `commitMapSpecMutation({intent:'patch_workbench_state', doc})`。
- 恢复：map-state restore 读 `mapspec.workbench` → hydrate store（V4 flat groups 迁移 = parentId 全 null，天然兼容）。
- agent lock：`LockConflict {layerIds, reason}` typed 结果 → map-action ack status `lock_conflict` + journal 审计；用户 override = 解锁（显式动作）。

## 关键机制

1. **Undo/Redo**：command 模型 `{do, undo payloads}`；执行=经既有提交通道正向/反向重放（async 收敛由 CAS 串行链保证）；有界 50；op 带 sessionId + generation，会话切换清空；async 完成不回写 history（payload 已定，无 corrupt state）。
2. **多 tab 协同基座**：BroadcastChannel `wb5:{sessionId}` 广播「已提交」事实（doc revision + presentation 收敛通知），接收方以 CAS revision 差异拉齐；冲突确定性 = 服务端 CAS last-writer-wins；无 presence/CRDT（不造第二真相）；权限边界 = 既有 session ownership。
3. **10k 虚拟化**：投影后扁平行数组 + 固定行高窗口渲染（自研 ~60 行 hook，不引依赖），overscan 8；selection/键盘走 id 不走 index（stable selection）。
4. **视口 thinning**：网格空间索引（cell = 视口/8），per-cell cap 抽稀，确定性（按面积排序），AbortSignal 随视口变更取消 stale 渲染请求；inline GeoJSON >2000 features 启用。
5. **刷新恢复**：localStorage 存 `{sessionId, projectId}`（wb5 专用键，非 project truth）→ mount 自动 selectSession 既有管线 + workbench doc hydrate；在途 op 本地 journal 重放（CAS 幂等）。

## 失败模式与对策

- 409 superseded → 既有收敛路径（toast + 回灌）；doc 同款。
- 广播丢失（tab 后开）→ 挂载时全量 fetch map-state 拉齐。
- doc 超限 → 422 + toast，本地不乐观生效。
- ghost layers → 复用 pendingRemoved 压制 + restore allowedIds 过滤（既有），广播只通知不直接改 layers。

## 与并行 Epic 边界

不动：GeoCompute、Data Control Plane、Harness planning、renderer 内核、OpenAPI 之外的 quality docs。共享文件仅：OpenAPI snapshot（本 intent 属主刷新）、contract-drift report（再生成）、CHANGELOG（1 条）。
