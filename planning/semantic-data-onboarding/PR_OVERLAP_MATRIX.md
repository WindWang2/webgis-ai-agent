# PR_OVERLAP_MATRIX — 去重矩阵

## 状态（2026-10-29，网络中断下的本地证据 + 待网络恢复复核）

本地证据：`git branch -r --no-merged origin/master` 为空（最后成功 fetch = 锚点 4b3629c9）；所有远端分支（feat/g01–g05、h01–h02、g3d01–05、g3dv2-a/b/c、fix/*、zcode/swarm）均已在 master 合并。

| 已合并 PR | 与本任务实质重叠 | 裁决 |
|---|---|---|
| #1759 G01 数据资格/多角色绑定/descriptor | 数据集级角色绑定、preflight、provenance 均其成果 | **复用为权威，不重建**；本任务仅补字段级确认回路 + 准备面 |
| #1760 G02 GIS 科学契约 | 契约纪律 | 遵守，不重叠 |
| #1761 G03 工具一致性 | 工具注册纪律 | 遵守（tier/domains/typed error） |
| #1762 G04 制图模板资产 | 无重叠 | — |
| #1763 G05 任务验收 | 无重叠 | — |
| #1755/#1758 swarm 修复 | 无重叠 | — |
| #1771/#1835 g3d01/DEM 供给 | DEM 管线 | 明确不做 |
| #1834/#1836 g3d02/b 场景运行时 | 3D | 明确不做 |
| #1773/#1829/#1867 g3d03/04/c 分析闭环 | Requirement IR/harness | 仅消费（DataRequirement/preflight），不改 |
| #1830 g3d05 3D 制图产品 | 无重叠 | — |
| #1549/#1551/#1598/#1679/#1680 修复类 | 无重叠 | — |
| dependabot #1764–#1768, #1770, #1772, #1831–#1833 | 依赖 | 不碰 |

## 开放 PR / issues（待网络恢复后必须补查）

- `gh pr list --state open`：挂起（TLS 断）。锚点后新开 PR 风险低但非零。
- issues #1842–#1866（审查类）：挂起。若其中存在与本方向重叠的安全 issue，按任务纪律只防本任务新引入风险，不抢修。
- 复核时点：① push 前；② gh pr create 前。两次都记录到 EVIDENCE.md。
