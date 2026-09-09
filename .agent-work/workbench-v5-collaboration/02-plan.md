# 02 — Implementation Plan (12 waves)

每个 wave：契约/测试先行 → 最小闭环 → targeted tests → lint/typecheck → 小步 commit。

| Wave | 内容 | 主要文件 |
|---|---|---|
| W1 | WorkbenchDoc V5 模型：嵌套 group 树 reducer（纯函数）+ store 投影扩展 + V4 迁移 | `lib/workbench/doc.ts`、`workbenchSlice.ts`、`workspace-projection.ts` |
| W2 | Agent lock 强制：visibility-transaction + remove_layer 通道接 lock，typed LockConflict，用户 override | `visibility-transaction.ts`、`layerCommands.ts`、`layer-ops.ts` |
| W3 | Durable doc：后端 `patch_workbench_state` intent + engine 分支 + 路由；前端提交/恢复通道 + hydrate | `lifecycle_engine.py`、`mapspec_mutations.py`、`user-mutation.ts`、`map-state-restore.ts` |
| W4 | Undo/redo + op journal：command 模型 + 有界 history + 接活 opLog 写入点 | `lib/workbench/undo.ts`、`uiSlice.ts` |
| W5 | 多 tab 协同基座：BroadcastChannel 收敛 + 冲突确定性 + 多 tab 测试 | `lib/workbench/collab.ts` |
| W6 | True side-by-side + secondary parity（legend/filter/terrain/time/selection） | `comparison-view.tsx`、floating-legend |
| W7 | Viewport thinning：网格索引 + per-cell cap + stale 取消 | `utils/geo.ts`、`map-kit/renderer.ts` |
| W8 | 10k 虚拟化：窗口渲染 hook + 键盘 + 稳定选择 + 10k 压测 | `layers-tab.tsx`、`use-virtual-rows.ts` |
| W9 | Editing UX：拖放进组、批量样式、模式感知工具禁用、上下文操作 | `layers-tab.tsx`、`layer-ops.ts` |
| W10 | Artifact linkage：provenance badge + stale/updated 指示 + inspect 链接 | `layers-tab.tsx`、`lib/api`（既有 lineage API 消费） |
| W11 | 刷新恢复/重连：localStorage session 锚 + 自动恢复 + 在途 op 幂等重放 | `use-workspace-session.ts`、`lib/workbench/session-anchor.ts` |
| W12 | A11y：treegrid roles、focus 管理、reduced motion、键盘闭环 | 图层树/面板组件 |

顺序：W1→W2→W3→W4→W5→W11→W8→W6→W7→W9→W10→W12（恢复与协同优先于 UX 打磨）。

后端横切：W3 完成后跑 OpenAPI snapshot 再生成（API_SNAPSHOT_UPDATE=1）+ drift report；rebase 后复跑一次。

## 测试矩阵（04 详列）

- 纯函数：doc reducer 迁移/嵌套/剪枝；undo 反演；thinning 确定性。
- 行为：lock 拦截 agent 通道（visibility + remove）；undo async 不 corrupt；多 tab 收敛；重启恢复 doc；10k 渲染窗口；双视图 parity 状态。
- 后端：engine intent 单测（CAS/回滚/cap）；路由 409/422。
