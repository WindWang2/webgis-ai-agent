# PR_OVERLAP_MATRIX — 去重矩阵

## 网络状态（开工时）

`git fetch` / `gh api` 对 github.com 全端点 TLS 失败（`SSL routines::unexpected eof`，≥5 次重试含退避）。本地 `origin/master` = `4b3629c90`（= 任务书锚点，当天观察）。**PR 创建前必须重试网络并二次核对 open PR 清单**（见 LOOP_STATE 待办）。

## 已合并 PR 审计（本地 git log，最近 30+）

以下已合并能力**明确不重复建设**（本任务只消费或零改动）：

| PR | 能力 | 本任务关系 |
|---|---|---|
| #1867 (g3dv2-c) | 三维科学分析→Harness→制图闭环 ADR-0276 | 无重叠（3D 面） |
| #1836 (g3dv2-b) | 3D 场景运行时/glTF/预算/context lost | 无重叠（3D 面） |
| #1835 (g3dv2-a) | DEM 摄入/Terrarium XYZ/quota | 无重叠（栅格面） |
| #1834/#1830/#1829 | scene 命令/编排（g3dv2 系） | 无重叠 |
| goal-02（Interaction IR V1，ADR-0227） | 13-kind 交互遥测、feature_selected 契约 | **只读消费**（selection→preview 目标）；不改词表 |
| goal-04（Pi Tool ABI V2） | pi_tool_spec/surface_receipt/result_abi | **只读消费**（新工具自动进面/receipt） |
| #1773/#1771 | （g3d 系先序） | 无重叠 |
| #1763/#1762/#1761/#1760/#1759/#1758/#1756/#1755 | g3d01-05（basemap DEM/scene engine/scientific/harness 3D/制图产品） | 无重叠（3D 面） |
| 更早：workbench undo/redo CAS、representation_binding taxonomy、mapspec lifecycle engine、artifact_revisions、data_quality、ref_lifecycle、session ownership | 基础设施 | 复用，最小 patch 清单见 PLAN D10 |

## 实质能力查重结论

「持久数据要素编辑」在 master 上 = **absent**（BASELINE.md 归档表）：
- 无要素级写入路由/工具/服务（layer.py 全只读；工具面无 edit_feature 族）
- 无稳定要素 ID 铸造（摄入不铸 fid）
- 无 PatchSet/journal（artifact `delta` 仅词表占位）
- 前端无 feature-level mutation intent / 提案卡 / 要素 diff 模型

相邻但明确不做（任务书边界）：Interaction IR V2、workbench undo 重写、图层显隐/组件 mutation、2D/3D 场景控制器、渲染器、数据接入界面重做。

## 潜在冲突面（本地可见 worktree）

- `webgis-wt-g3dv2-a-data` / `webgis-wt-g3dv2-b-scene`：分支 HEAD 均已被合并进 master（#1835/#1836），无在途冲突。
- 中心文件 patch 均为行级新增（见 PLAN D10）；与上述已合并方向无文件级竞争（g3dv2 系不触碰 mapspec mutation registry / feature 面）。
- 若网络恢复后发现 open PR 与本任务实质重叠 → 按 LOOP_STATE 预案缩窄到未覆盖子任务并重写本矩阵。
