# G3D02 Baseline — 三维场景渲染内核 / 3D Tiles / glTF

- 基线 SHA: `1115d683a5c8343e44d8a9777e4ce05e073c29e5` (origin/master, 2026-10-26)
- Worktree: `../webgis-wt-g3d02-scene-engine-tiles` @ branch `zcode/g3d02-scene-engine-tiles-v1`
- 邻居 worktree（只读，不触碰）: `webgis-wt-g3d01-open-basemap-dem` (zcode/g3d01-open-basemap-dem-v1)

## 开放 PR（fetch 于 2026-10-26）
- 无开放 PR（`gh pr list --state open` 返回空）。

## 开放 issue
- 仅 #1694（Swarm 深度审查 P2/P3 批量聚合）——工作单 §0.6 明确排除在授权范围外。

## 近期合并（与本方向相关）
- #1763 模板资产生命周期、#1762 任务级验收、#1761 工具契约对账、#1760 科学适用性、#1759 数据集资格、#1758 issue 清偿、#1755 导出一致性、#1756 Pi/资格、#1622 RenderWork、#1623 Interaction IR、#1621 项目恢复、#1617 语义缩放——均需复用不重实现。

## 现状核查（file:line 证据见 PLAN.md）
- MapLibre GL JS ^6.10.0，无 three/任何 3D 依赖（frontend/package.json）。
- 全库 rg `3dtiles|gltf|glb|three|maplibre-gl-three|CustomLayerInterface` 无生产命中——3D Tiles/glTF 为空白，本方向无重叠。
- scene 域 v1.4（MapSceneConfig 2d/2.5d/3d + terrain 指针 + 相机）为 additive 扩展先例。
