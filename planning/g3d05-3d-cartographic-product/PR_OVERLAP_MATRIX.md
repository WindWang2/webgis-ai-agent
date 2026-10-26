# G3D05 PR 重叠矩阵（对照 2026-10-27 开放 PR 与近期合并）

## 开放 PR 对照（10 个）

| PR | 方向 | 与 G3D05 文件/功能重叠 | 处置 |
| --- | --- | --- | --- |
| #1773 G3D03 三维量测契约 | 兄弟 | 后端 `app/lib/gis/algorithms/terrain.py`、`app/tools/terrain_analysis.py` 等 38 文件。**功能互补**：其提供剖面计算，G3D05 提供剖面**产品组件**。零文件交集（本方向不动 algorithms/terrain 工具）。 | 只读消费其未合并契约形状（`{distance,elevation}` 序列），以 fixture 并行演进；不 cherry-pick。 |
| #1771 G3D01 底图/DEM 治理 | 兄弟 | 其改 `map-panel.tsx`、`map-kit/renderer.ts`、`exporter.ts`、`export-chrome.ts`、`mapspec-runtime/runtime.ts`、`constants.ts`、`providers.ts`。G3D05 也需窄改 `map-panel.tsx`（挂载场景控制器）与 `export-chrome.ts`/`exporter.ts`（披露印章）。 | 改动均为**新增段落/新增函数**（不同 hunk），冲突面小；不依赖其新增文件（terrain-governance 等 master 不存在，不 import）。 |
| #1772/#1770/#1769/#1768/#1767/#1766/#1765/#1764 dependabot | 依赖 | 无（npm/pip/docker 基线升级） | 无动作。 |

## 近期合并（已复用而非重实现）

| PR | 内容 | G3D05 复用方式 |
| --- | --- | --- |
| #1763 模板资产生命周期 | `asset.*` 模板命名空间 upsert/退役/恢复 | 3D pack 走系统 pack 路径（非 asset.*）；资产模板可继续承载用户变体，无需改动。 |
| #1762 任务级验收 / #1761 工具契约对账 | 21 场景 dispatch 语料、8 面对账 | 不动；新工具面（用户 set_scene mutation）不属于 agent 工具注册表。 |
| #1760 科学方法契约 | 三段科学守门 | 不触碰科学算法核心（边界 A）。 |
| #1759 数据集资格 / #1758 issue 清偿 | 数据语义身份 | 不动。#1694 批量审计明确不在授权范围。 |
| #1755 product conformance & export parity | manifest、语义语料、降级码 | **直接扩展**：manifest 增 scene 溯源块；RENDER_DIAGNOSTICS 增 3D 披露码；语义语料自动纳入新组件（真值矩阵翻转即生效）。 |
| #1622 RenderWork / #1623 Interaction IR / #1621 项目恢复 / #1617 语义缩放 / #1756 资格判据 | 已合入基础 | 全部经既有类型与通道消费，不造第二套。 |

## 结论

无任何开放 PR 承担"3D 制图组合模板 + 场景控制器 UI + 透视导出诚实性 + StoryMap 场景章节"这一工作面。G3D05 与兄弟方向的最大公约数为：**新文件为主，共享文件（map-panel.tsx、export-chrome.ts、exporter.ts）仅新增 hunk，不重写他人已改逻辑**。
